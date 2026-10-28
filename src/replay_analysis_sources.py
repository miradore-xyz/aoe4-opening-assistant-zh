"""复盘补源：同场公开统计摘要、严格字段解码与来源核对。

STLS（出现/损失统计）只给事件瞬间坐标；STPD（玩家统计）给采样库存、
来源每分速率和账号。默认只读本地，allow_network（允许联网）须明确启用，
并在界面后台调用。不会启动游戏、读取进程或覆盖原回放/既有详情缓存。
"""
from __future__ import annotations

from collections import Counter, defaultdict
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import zlib
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from match_detail import normalize_summary
from replay_analysis import resolve_report_players
from replay_parser import ReplayError, _Reader, _chunk_file
from version import APP_VERSION

SCHEMA_VERSION = 1
MAX_BYTES = 16 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024
_METADATA_HOST = 'aoe-api.worldsedgelink.com'
_DOWNLOAD_HOST = 'rl0aoelivemk2blob.blob.core.windows.net'
_FORMAT_SOURCE = 'AoE4World/replays-api efc391296451da352c3660daf814403e37e787e8 Models/DataSTLS.cs,DataSTPD.cs,DataSTLC.cs'
GAPS = [
    '统计摘要不提供完整单位连续位置、移动路径或每名村民工作状态。',
    '未取得本场视野/战争迷雾、镜头轨迹、选择切换或控制组完整记录。',
    '生产排队指令不是队列实际状态，出现事件不能单独证明开始排队或建筑空闲。',
    'STLS不完整记录起始单位和中立资源实例，不能据此补造树矿动物分布。',
    '损失中攻击方为来源原字段，不代表完整伤害归属或死亡原因。',
    '资源采样的统计窗口未由游戏原生实现独立验证；库存变化不是战损。',
]


class AnalysisSourceError(ValueError):
    """来源不匹配、结构未支持或公开服务不可用。"""


def _id(value):
    return int(str(value)) if not isinstance(value, bool) and re.fullmatch(r'[1-9][0-9]{0,11}', str(value)) else None


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _int(reader):
    return reader.unpack('i')[0]


def _float(reader):
    value = reader.unpack('f')[0]
    if not math.isfinite(value):
        raise AnalysisSourceError('统计摘要含非有限数值。')
    return value


def _count(reader):
    value = _int(reader)
    if value < 0 or value > min(1_000_000, reader.remaining + 1):
        raise AnalysisSourceError('统计摘要数组长度无效。')
    return value


def _resources(reader):
    size = _int(reader)
    keys = ['action', 'command', 'food', 'gold']
    if size == 9:
        keys.append('merc_byz')
    elif size != 8:
        raise AnalysisSourceError('统计摘要资源字典版本尚未确认。')
    keys += ['militia_hre', 'popcap', 'stone', 'wood']
    result = {}
    for expected in keys:
        key = reader.string()
        if key != expected:
            raise AnalysisSourceError('统计摘要资源键或顺序不匹配。')
        result[key] = _float(reader)
    return result


def _finish(reader):
    if reader.remaining:
        raise AnalysisSourceError('统计摘要存在未识别尾字段，未采用该块。')


def _read_stls(reader, version):
    if version != 2003:
        raise AnalysisSourceError('出现/损失统计版本尚未支持。')
    header = dict(zip(('unknown1', 'unknown2', 'unknown3', 'game_length', 'unknown5'),
                      (reader.u8(), _int(reader), _int(reader), _int(reader), _int(reader))))
    if (header['unknown1'], header['unknown2'], header['unknown5']) != (0, 1, 0):
        raise AnalysisSourceError('出现/损失统计头部不匹配。')
    created = []
    for _ in range(_count(reader)):
        start = reader.pos
        row = {'time': _float(reader), 'player_id': _int(reader), 'entity_id': _int(reader),
               'marker': reader.u8(), 'entity_type': reader.string(),
               'unknown4': reader.unpack('h')[0], 'category': reader.unpack('h')[0],
               'unknown_resources_1': _resources(reader), 'unknown_resources_2': _resources(reader),
               'x': _float(reader), 'y': _float(reader), 'offset': start, 'source': 'STLS.createdEntities'}
        if row['marker'] != 1 or row['unknown4'] != 0 or row['category'] not in (19, 46, 52) or row['time'] < 0:
            raise AnalysisSourceError('出现统计字段超出已核实结构。')
        created.append(row)
    lost = []
    for _ in range(_count(reader)):
        start = reader.pos
        row = {'time': _float(reader), 'target_player_id': _int(reader), 'target_entity_id': _int(reader),
               'has_target': reader.u8(), 'target_type': reader.string(), 'unknown3': reader.unpack('h')[0],
               'unknown4': reader.unpack('h')[0], 'attacker_player_id': _int(reader),
               'attacker_entity_id': _int(reader), 'has_attacker': reader.u8(), 'attacker_type': reader.string(),
               'offset': start, 'source': 'STLS.lostEntities'}
        if row['has_target'] != 1 or row['has_attacker'] not in (0, 1) or row['time'] < 0:
            raise AnalysisSourceError('损失统计字段超出已核实结构。')
        if row['has_attacker']:
            row['unknown5'], row['unknown6'] = reader.unpack('hh')
            row['weapon_type'] = reader.string()
        row.update(zip(('target_x', 'target_y', 'attacker_x', 'attacker_y'), (_float(reader) for _ in range(4))))
        lost.append(row)
    if _int(reader) != 0:
        raise AnalysisSourceError('损失统计尾部版本尚未支持。')
    _finish(reader)
    return {'version': version, 'header': header, 'created': created, 'lost': lost,
            'position_semantics': '事件瞬时坐标，不是连续轨迹；坐标到场景米未校准。'}


def _read_stpd(reader, version):
    # 本次真实两场均2034；旧版有未升版本结构差异，未用猜测路径接受。
    if version != 2034:
        raise AnalysisSourceError('玩家统计仅支持已实证的2034版本。')
    row = {'player_id': _int(reader), 'name': reader.string(wide=True), 'version': version}
    prefix = [_int(reader) for _ in range(28)]
    row['outcome'], row['timestamp_eliminated'] = prefix[0], prefix[2]
    row['reported_units_produced'], row['reported_largest_army'] = prefix[6], prefix[16]
    row['unknown_resources_1'] = _resources(reader)
    losses = [_int(reader) for _ in range(12)]
    row['reported_buildings_lost'], row['reported_units_lost'], row['reported_units_lost_value'] = losses[0], losses[2], losses[3]
    row['reported_tech_researched'] = losses[10]
    row['unknown_resources_2'] = _resources(reader)
    row['total_resources_spent_on_upgrades'] = _resources(reader)
    row['unknown_resources_3'], row['unknown_resources_4'] = _resources(reader), _resources(reader)
    kills = [_int(reader) for _ in range(11)]
    row['reported_units_killed'], row['reported_units_killed_value'], row['reported_buildings_razed'] = kills[0], kills[1], kills[4]
    row['total_resources_gathered'], row['total_resources_spent'] = _resources(reader), _resources(reader)
    row['unknown_resource_totals'] = [_resources(reader) for _ in range(4)]
    reader.skip(4 * 18)
    row['unknown_resources_5'] = _resources(reader)
    reader.skip(4 * 4 + 1)
    row['civilization'] = reader.string()
    reader.skip(8)
    row['profile_id'] = _int(reader)
    row['unknown14d'] = _int(reader)
    timeline = []
    for _ in range(_count(reader)):
        timeline.append({'time': _int(reader), 'current': _resources(reader),
                         'cumulative_unverified': _resources(reader), 'per_minute': _resources(reader),
                         'unit_value': _resources(reader), 'unknown1': _int(reader)})
    row['resource_timeline'] = timeline
    row['score_timeline'] = [dict(zip(('time', 'economy', 'military', 'society', 'technology', 'total'),
                            (_int(reader), *(_float(reader) for _ in range(5))))) for _ in range(_count(reader))]
    row['unknown15'] = [reader.unpack('iiii') for _ in range(_count(reader))]
    row['unknown16'] = reader.unpack('iiii')
    unit_timeline = []
    for _ in range(_count(reader)):
        event = {'id_type': reader.u8()}
        if event['id_type'] == 1:
            event['pbgid'] = _int(reader)
        elif event['id_type'] == 2:
            event['mod_id'] = reader.take(20).hex()
        else:
            raise AnalysisSourceError('单位统计蓝图标记尚未支持。')
        event['unknown2'], event['unknown3'] = reader.unpack('hh')
        event['time_raw'] = _int(reader)
        event['icon'], event['label'] = reader.string(), reader.string(wide=True)
        event['unknown4'] = reader.string(wide=True)
        event['unknown_resources'] = _resources(reader)
        event['unknown5'], event['unknown6'] = reader.string(wide=True), _int(reader)
        unit_timeline.append(event)
    row['unit_timeline_raw'] = unit_timeline
    row['unknown17'] = [reader.unpack('iiif') for _ in range(_count(reader))]
    # 剩余字段按公开结构的固定长度完整消费；未证语义不为其命名。
    reader.skip(8 * 4)
    row['reported_relics_captured'] = _float(reader)
    reader.skip(4 + 6 * 4 + 1 + 4 + 4 + 7 * 4 + 13 * 4 + 4 * 4)
    row['player_color'] = _int(reader)
    reader.skip(3 * 4 + 16 * 4)
    _finish(reader)
    for timeline_name in ('resource_timeline', 'score_timeline'):
        times = [point['time'] for point in row[timeline_name]]
        if any(t < 0 for t in times) or any(b <= a for a, b in zip(times, times[1:])):
            raise AnalysisSourceError('玩家统计采样时间轴异常。')
    return row


def parse_statistics_summary(raw):
    """解码原始type1（统计摘要）字节，不自行赋予对局编号。"""
    if len(raw) > MAX_BYTES:
        raise AnalysisSourceError('统计摘要超过16MB。')
    if raw.startswith(b'\x1f\x8b'):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                data = stream.read(MAX_EXPANDED + 1)
        except (OSError, EOFError, zlib.error) as error:
            raise AnalysisSourceError('统计摘要压缩数据损坏或不完整。') from error
    else:
        data = raw
    if len(data) > MAX_EXPANDED:
        raise AnalysisSourceError('统计摘要解压后超过64MB。')
    reader, chunks = _Reader(data), []
    _chunk_file(reader, chunks)
    _finish(reader)
    stls_chunks = [chunk for chunk in chunks if chunk[:2] == (b'DATA', b'STLS')]
    if len(stls_chunks) != 1:
        raise AnalysisSourceError('统计摘要缺少唯一出现/损失块。')
    _, _, version, start, end = stls_chunks[0]
    stls = _read_stls(_Reader(data, start, end), version)
    players, current = [], None
    for kind, ident, version, start, end in chunks:
        if kind != b'DATA':
            continue
        block = _Reader(data, start, end)
        if ident == b'STPD':
            current = _read_stpd(block, version)
            current['conversions'] = []
            players.append(current)
        elif ident == b'STLC':
            if current is None or version != 1:
                raise AnalysisSourceError('转化统计玩家边界或版本无效。')
            current['conversions'] = [dict(zip(('pbgid', 'lost', 'gained'), block.unpack('iii')))
                                      for _ in range(_count(block))]
            _finish(block)
    ids = [row['player_id'] for row in players]
    profiles = [row['profile_id'] for row in players]
    if not 1 <= len(players) <= 8 or len(ids) != len(set(ids)) or len(profiles) != len(set(profiles)) or any(not _id(i) for i in profiles):
        raise AnalysisSourceError('统计摘要玩家编号重复或无效。')
    for event in stls['created']:
        if event['player_id'] not in ids:
            raise AnalysisSourceError('出现记录玩家不在本场统计中。')
    return {'stls': stls, 'players': players, 'sha256': _sha(raw), 'expanded_sha256': _sha(data),
            'compressed_bytes': len(raw), 'expanded_bytes': len(data), 'format_source': _FORMAT_SOURCE,
            'chunk_inventory': dict(Counter(f'{a.decode()}/{b.decode()}/{v}' for a, b, v, _, _ in chunks))}


def _request(url, maximum, opener):
    request = Request(url, headers={'User-Agent': f'AOE4HELP/{APP_VERSION} (user initiated single match analysis sources)', 'Accept': 'application/json, application/gzip'})
    with opener(request, timeout=25) as response:
        final = urlparse(response.geturl()) if hasattr(response, 'geturl') else urlparse(url)
        if final.hostname != urlparse(url).hostname or final.scheme != 'https':
            raise AnalysisSourceError('公开来源跳转到未确认地址。')
        raw = response.read(maximum + 1)
    if len(raw) > maximum:
        raise AnalysisSourceError('公开来源响应过大。')
    return raw


def _report_identity(report):
    source = report.get('source') or {}
    game = _id(source.get('game_id'))
    focus = _id(source.get('profile_id')) or _id(report.get('focus_profile_id'))
    digest = source.get('sha256', '')
    if not game or not focus or not re.fullmatch('[0-9a-f]{64}', str(digest)):
        raise AnalysisSourceError('本地报告没有可核对的公开对局、账号和录像摘要。')
    return game, focus, digest


def _bind_statistics(report, statistics, manifest):
    game, focus, digest = _report_identity(report)
    if (not isinstance(manifest, dict) or manifest.get('schema_version') != SCHEMA_VERSION or manifest.get('game_id') != game
            or manifest.get('report_sha256') != digest or manifest.get('summary_sha256') != statistics['sha256']):
        raise AnalysisSourceError('补源对局编号或文件摘要与当前录像不一致。')
    build = (report.get('metadata') or {}).get('build_number')
    if build is not None and manifest.get('build_number') != build:
        raise AnalysisSourceError('补源游戏构建版本与当前录像不一致。')
    player_ids = {row['profile_id'] for row in statistics['players']}
    public_profiles = set(manifest.get('available_replay_profile_ids') or [])
    download_profile = _id(manifest.get('download_profile_id'))
    if (player_ids != set(manifest.get('participant_profile_ids') or []) or focus not in player_ids
            or download_profile not in player_ids or download_profile not in public_profiles
            or not public_profiles.issubset(player_ids)):
        raise AnalysisSourceError('补源参与者或可用公开录像账号不一致。')
    known = {row['id']: row for row in resolve_report_players(report)}
    links = []
    for row in statistics['players']:
        original = known.get(row['player_id'])
        if original is None:
            raise AnalysisSourceError('统计摘要局内玩家不在当前录像中。')
        if original.get('profile_id') not in (None, 0, row['profile_id']):
            raise AnalysisSourceError('当前录像账号与统计账号冲突。')
        if original.get('civilization') and original['civilization'] != row['civilization']:
            raise AnalysisSourceError('当前录像文明与统计文明冲突。')
        # 名称只做矛盾检查，主绑定来自同局原二进制player_id，而非名字匹配。
        if original.get('name') and not re.fullmatch(r'玩家\s*\d+', original['name']) and original['name'] != row['name']:
            raise AnalysisSourceError('当前录像玩家名称与统计源矛盾。')
        links.append({'player_id': row['player_id'], 'profile_id': row['profile_id'],
                      'source': '同局STPD.playerId/playerProfileId', 'verified': True})
    if set(known) != {row['player_id'] for row in statistics['players']}:
        raise AnalysisSourceError('当前录像与摘要的完整玩家集合不同。')
    return links


def _statistics_detail(statistics, report, game, focus):
    """已解码采样与有明确转换依据的兼容事件；未知累计值不当采集量。"""
    teams = {row['id']: row.get('team') for row in resolve_report_players(report)}
    # 旧分析报告把0队误作未知；完整回放metadata（元数据）的0队是有效原值。
    for row in (report.get('metadata') or {}).get('players', []):
        if isinstance(row, dict) and type(row.get('team')) is int and row['team'] >= 0:
            teams[row.get('id')] = row['team']
    players = []
    for row in statistics['players']:
        timeline = row['resource_timeline']
        scores = {point['time']: point for point in row['score_timeline']}
        resources = {'timestamps': [point['time'] for point in timeline]}
        for resource in ('food', 'wood', 'gold', 'stone'):
            resources[resource] = [point['current'][resource] for point in timeline]
            resources[resource + '_per_min'] = [point['per_minute'][resource] for point in timeline]
        for score in ('total', 'economy', 'military', 'technology', 'society'):
            resources[score] = [scores.get(point['time'], {}).get(score) for point in timeline]
        player = {'profile_id': row['profile_id'], 'name': row['name'], 'civilization': row['civilization'],
                  'team': teams.get(row['player_id']), 'resources': resources,
                  'total_resources_gathered': row['total_resources_gathered'],
                  'total_resources_spent': row['total_resources_spent'],
                  'build_order': _compatibility_build_order(row['unit_timeline_raw'])}
        for field in ('total_resources_gathered', 'total_resources_spent'):
            player[field] = dict(player[field])
            player[field]['total'] = sum(player[field][key] for key in ('food', 'wood', 'gold', 'stone', 'merc_byz') if key in player[field])
        players.append(player)
    detail = normalize_summary({'game_id': game, 'players': players,
                                'duration': statistics['stls']['header']['game_length']}, focus, game)
    detail['coverage']['source'] = '同场官方type1统计摘要/STPD2034'
    detail['coverage']['event_semantics_source'] = 'AoE4World/replays-api CompatConverter.cs MapBuildOrder；保留其兼容事件语义，不当完整原生队列。'
    return detail


def _compatibility_build_order(timeline):
    """按明确公开转换代码复现兼容事件，未知标记保留不分类。

    对2034当前样本，与已有公开JSON归一化事件独立逐条对照。该转换源没有
    本场初始单位实例ID；这里也不推断它们、施工状态或生产建筑的实际队列。
    """
    entries = {}
    for event in timeline:
        icon = event['icon'].replace('\\', '/')
        if not icon:
            continue
        identity = ('pbgid', event['pbgid']) if 'pbgid' in event else ('mod', event.get('mod_id', icon))
        if '/buildings/' in icon:
            kind = 'Building'
        elif '/units/' in icon:
            kind = 'Unit'
        elif '/upgrades/' in icon or '/uprades/' in icon:
            kind = 'Upgrade'
        elif '/animals/' in icon:
            kind = 'Animal'
        elif '/age/' in icon:
            kind = 'Age'
        else:
            kind = 'Unknown'
        if identity not in entries:
            entries[identity] = {'pbgid': event.get('pbgid'), 'modid': event.get('mod_id'), 'type': kind,
                                 'finished': [], 'destroyed': [], 'constructed': [],
                                 'packed': [], 'unpacked': [], 'transformed': [], 'unknown': {}}
        entry = entries[identity]
        timestamp, flag = event['time_raw'], event['unknown6']
        if timestamp < 0:
            raise AnalysisSourceError('兼容实体时间线含负时间。')
        if flag in (1, 8, 12) or (flag == 3 and kind != 'Building'):
            field = 'finished'
        elif flag in (2, 4) or (flag == 6 and kind == 'Building'):
            field = 'destroyed'
        elif flag == 3:
            field = 'packed'
        elif flag == 5:
            unpacking = (entry['packed'] and
                         (not entry['constructed'] or entry['packed'][-1] > entry['constructed'][-1]) and
                         (not entry['unpacked'] or entry['packed'][-1] > entry['unpacked'][-1]))
            field = 'unpacked' if unpacking else 'constructed'
        else:
            entry['unknown'].setdefault(str(flag), []).append(timestamp)
            continue
        entry[field].append(timestamp)
    return list(entries.values())


def build_entity_observations(stls, identity_links=()):
    """同一STLS原实体编号的出现/攻击方/损失瞬时位置；绝不插值为移动。

    只收明确单位类别或unit_原类型；建筑0编号及紧凑命令units编号不参与。
    同玩家同实体ID若重复出现，保留观测并标注身份歧义，不称唯一生命周期。
    """
    profiles = {row['player_id']: row['profile_id'] for row in identity_links if row.get('verified') is True}
    groups = defaultdict(list)

    def add(player, entity, entity_type, time, x, y, kind, offset, source):
        if player not in profiles or not isinstance(entity, int) or entity <= 0:
            return
        groups[(player, entity)].append({'time': time, 'kind': kind, 'entity_type': entity_type,
                                        'position': {'x': x, 'y': y}, 'offset': offset, 'source': source})

    for row in stls.get('created', []):
        if row.get('category') == 46:
            add(row['player_id'], row['entity_id'], row['entity_type'], row['time'], row['x'], row['y'],
                'birth', row.get('offset'), 'STLS.createdEntities')
    for row in stls.get('lost', []):
        if row.get('has_target') == 1 and row.get('target_type', '').startswith('unit_'):
            add(row['target_player_id'], row['target_entity_id'], row['target_type'], row['time'],
                row['target_x'], row['target_y'], 'loss', row.get('offset'), 'STLS.lostEntities.target')
        if row.get('has_attacker') == 1 and row.get('attacker_type', '').startswith('unit_'):
            add(row['attacker_player_id'], row['attacker_entity_id'], row['attacker_type'], row['time'],
                row['attacker_x'], row['attacker_y'], 'attack', row.get('offset'), 'STLS.lostEntities.attacker')
    entities = []
    for (player, entity), observations in sorted(groups.items()):
        observations.sort(key=lambda row: (row['time'], row['kind'], row.get('offset') or 0))
        birth_count = sum(row['kind'] == 'birth' for row in observations)
        positions_at_time = defaultdict(set)
        for row in observations:
            positions_at_time[row['time']].add((row['position']['x'], row['position']['y']))
        ambiguous = birth_count > 1 or any(len(points) > 1 for points in positions_at_time.values())
        distinct = {tuple(row['position'].values()) for row in observations}
        changes = len(positions_at_time) > 1 and len(distinct) > 1
        entities.append({'player_id': player, 'profile_id': profiles[player], 'entity_id': entity,
                         'entity_types': sorted({row['entity_type'] for row in observations}),
                         'observations': observations, 'identity_ambiguous': ambiguous,
                         'position_changed_observed': changes and not ambiguous,
                         'coordinate_system': 'STLS.xy原统计平面；到场景米比例未校准',
                         'world_meter_scale': None, 'continuous_path': False,
                         'semantics': '相同时刻损失事件中的攻击方位置也只是观测点；点间无路径、速度或动画证据。'})
    return entities


def _select_statistics_file(rows, focus):
    """优先关注账号；缺少时取同场清单中一个账号唯一的可用统计摘要。"""
    candidates = [row for row in rows if row.get('datatype') == 1 and _id(row.get('profile_id'))
                  and row.get('public') is not False]
    own = [row for row in candidates if row['profile_id'] == focus]
    if len(own) > 1:
        raise AnalysisSourceError('官方为当前账号返回了冲突的多个统计摘要。')
    if own:
        return own[0], 'focus_public_summary'
    counts = Counter(row['profile_id'] for row in candidates)
    for row in candidates:
        if counts[row['profile_id']] == 1:
            return row, 'same_match_available_summary'
    raise AnalysisSourceError('官方未提供本场可唯一核对的公开统计摘要。')


def collect_analysis_sources(report, data_dir=None, allow_network=False, *, opener=None):
    """返回detail（详情）、stls（真实事件）、identity_links（账号绑定）与缺口。

    本地扩展缓存固定在数据目录/复盘扩展来源/对局-编号；其清单把摘要SHA、
    原录像SHA、官方元数据参与者与构建号绑在一起。网络开启时只请求当前场次。
    """
    result = {'schema_version': SCHEMA_VERSION, 'game_id': None, 'detail': None, 'raw_summary': None,
              'stls': None, 'statistics_players': [], 'entity_observations': [], 'identity_links': [], 'capabilities': {},
              'provenance': [], 'gaps': list(GAPS), 'errors': [], 'network_requests': 0}
    try:
        game, focus, digest = _report_identity(report)
    except AnalysisSourceError as error:
        result['errors'].append(str(error))
        return result
    result['game_id'] = game
    opener = opener or urlopen
    base = Path(data_dir) if data_dir is not None else None
    target = base / '复盘扩展来源' / f'对局-{game}' if base is not None else None
    if base is not None:
        detail_path = base / '对局详情' / f'玩家-{focus}-对局-{game}.json'
        try:
            raw_detail = detail_path.read_bytes()
            if len(raw_detail) <= MAX_BYTES:
                detail = json.loads(raw_detail)
                ids = [p.get('profile_id') for p in detail.get('players', [])]
                if detail.get('game_id') == game and detail.get('focus_profile_id') == focus and focus in ids and len(ids) == len(set(ids)):
                    result['detail'] = detail
                    result['provenance'].append({'kind': 'normalized_detail', 'sha256': _sha(raw_detail), 'game_id': game, 'source': '既有单场详情缓存'})
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    statistics, manifest = None, None
    if target is not None:
        try:
            manifest_raw = (target / '来源核对.json').read_bytes()
            if len(manifest_raw) > 128 * 1024:
                raise AnalysisSourceError('扩展来源清单过大。')
            manifest = json.loads(manifest_raw)
            statistics = parse_statistics_summary((target / 'statistics.gz').read_bytes())
            result['identity_links'] = _bind_statistics(report, statistics, manifest)
        except (OSError, ValueError, TypeError, ReplayError, EOFError) as error:
            if not isinstance(error, FileNotFoundError):
                result['errors'].append('本地补源未采用：' + str(error))
            statistics, manifest = None, None
    if statistics is None and allow_network:
        try:
            url = 'https://' + _METADATA_HOST + '/community/leaderboard/getReplayFiles?' + urlencode({'title': 'age4', 'matchIDs': f'[{game}]'})
            result['network_requests'] += 1
            metadata_raw = _request(url, 2 * 1024 * 1024, opener)
            metadata = json.loads(metadata_raw)
            if not isinstance(metadata, dict):
                raise AnalysisSourceError('官方单场下载元数据格式无效。')
            rows = metadata.get('replayFiles', [])
            if (not isinstance(metadata.get('result'), dict) or metadata['result'].get('code') != 0
                    or not isinstance(rows, list) or not rows
                    or any(not isinstance(row, dict) or row.get('matchhistory_id') != game for row in rows)):
                raise AnalysisSourceError('官方单场下载元数据未通过核对。')
            selected, selection_basis = _select_statistics_file(rows, focus)
            download_profile = _id(selected['profile_id'])
            download = urlparse(selected.get('url', ''))
            if (download.scheme != 'https' or download.hostname != _DOWNLOAD_HOST or download.port not in (None, 443)
                    or download.username or download.password or download.fragment
                    or not re.search(rf'/([0-9]+)/M_{game}_[0-9a-f]{{64}}\.gz$', download.path)):
                raise AnalysisSourceError('官方统计下载地址不匹配本场。')
            build = int(re.search(r'/([0-9]+)/M_', download.path).group(1))
            if type(selected.get('size')) is not int or not 0 < selected['size'] <= MAX_BYTES:
                raise AnalysisSourceError('官方统计摘要大小无效。')
            result['network_requests'] += 1
            raw = _request(selected['url'], selected['size'], opener)
            if len(raw) != selected['size']:
                raise AnalysisSourceError('官方统计摘要下载不完整。')
            statistics = parse_statistics_summary(raw)
            manifest = {'schema_version': SCHEMA_VERSION, 'game_id': game, 'report_sha256': digest,
                        'summary_sha256': _sha(raw), 'metadata_sha256': _sha(metadata_raw),
                        # 下载清单只列当前可用录像，可能少于完整参与者，不能误作全员名单。
                        'available_replay_profile_ids': sorted({_id(row.get('profile_id')) for row in rows}),
                        'participant_profile_ids': sorted(row['profile_id'] for row in statistics['players']),
                        'participant_source': 'STPD明确账号；完整局内ID/文明/名称与原录像矛盾检查',
                        'build_number': build, 'datatype': 1, 'download_profile_id': download_profile,
                        'selection_basis': selection_basis,
                        'metadata_source': url, 'download_origin': download._replace(query='').geturl()}
            result['identity_links'] = _bind_statistics(report, statistics, manifest)
            if target is not None:
                target.mkdir(parents=True, exist_ok=True)
                (target / 'statistics.gz').write_bytes(raw)
                (target / '来源核对.json').write_text(json.dumps(manifest, ensure_ascii=False, allow_nan=False), 'utf-8')
        except (OSError, ValueError, TypeError, KeyError, ReplayError, EOFError) as error:
            result['errors'].append('公开补源未采用：' + str(error))
            statistics, manifest = None, None
    if statistics is not None:
        if result['detail'] is not None:
            expected_profiles = {row['profile_id'] for row in statistics['players']}
            actual_profiles = {row.get('profile_id') for row in result['detail']['players']}
            if actual_profiles != expected_profiles:
                result['errors'].append('既有详情参与者与核对后的官方摘要不一致，未采用旧详情。')
                result['detail'] = None
        result['stls'] = dict(statistics['stls'], game_id=game, summary_sha256=statistics['sha256'])
        result['statistics_players'] = statistics['players']
        result['entity_observations'] = build_entity_observations(result['stls'], result['identity_links'])
        result['provenance'].append(dict(manifest, kind='official_statistics', format_source=_FORMAT_SOURCE))
        # 已有详情包含完整建成/出现/损失数组；保留它，STLS供另行精确扩展。
        if result['detail'] is None:
            result['detail'] = _statistics_detail(statistics, report, game, focus)
        original_duration = result['detail'].get('duration')
        game_length = statistics['stls']['header']['game_length']
        replay_length = (report.get('metadata') or {}).get('duration')
        duration_corrected = (isinstance(original_duration, (int, float))
                              and isinstance(replay_length, (int, float))
                              and abs(replay_length - game_length) <= 1
                              and abs(original_duration - game_length) > 1)
        if duration_corrected:
            # 只修单独时长标头；原事件/采样秒数一律保留，不做比例拉伸。
            result['detail']['duration'] = game_length
            result['detail'].setdefault('coverage', {})['original_reported_duration'] = original_duration
            result['detail']['coverage']['duration_source'] = 'STLS.gameLength，与原录像最后逻辑刻相差不超过一秒'
            result['provenance'].append({'kind': 'duration_header_correction', 'game_id': game,
                                         'original_reported_duration': original_duration,
                                         'used_duration': game_length, 'report_last_tick': replay_length,
                                         'event_and_sample_times_changed': False})
        result['capabilities'].update(identity_links=True, instantaneous_entity_events=True,
                                      reported_attacker_fields=True, resource_bank=True,
                                      source_resource_rate=True, resource_unit_value=True, conversion_totals=True,
                                      entity_position_observations=True)
        result['clock'] = {'statistics_game_length_seconds': game_length,
                           'report_last_tick_seconds': replay_length,
                           'detail_reported_duration_seconds': original_duration,
                           'detail_used_duration_seconds': result['detail'].get('duration'),
                           'isolated_duration_header_corrected': duration_corrected,
                           'semantics': '保留各源原秒数；不比例拉伸，不把单独duration标头差值当事件时钟偏移。'}
        # 原生解码后的kind/UID才能绑定单位；在既有后台任务建立索引，播放时不重算。
        from replay_unit_state import build_unit_state_index
        from unit_combat_catalog import CombatCatalogError, load_catalog, validated_context
        catalog = None
        try:
            catalog = load_catalog()
            result['official_unit_context'] = validated_context(report, catalog=catalog)
        except (OSError, ValueError, CombatCatalogError) as error:
            result['errors'].append('官方单位目录未采用：' + str(error))
        result['unit_state'] = build_unit_state_index(report, result, catalog=catalog)
        result['capabilities']['canonical_unit_command_identity'] = result['unit_state']['capabilities']['canonical_command_identity']
    result['capabilities'].update(continuous_entity_positions=False, fog_of_war=False, camera=False,
                                  selection_history=False, control_groups=False, actual_queue_state=False,
                                  initial_neutral_resource_table=False)
    return result
