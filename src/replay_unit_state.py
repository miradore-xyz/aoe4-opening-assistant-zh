"""回放原编号到单位出现、损失、实际发令的索引。

原引用kind保留原值。当前kind2与同局STLS单位组UID精确对应；不把编队编号
冒称物理实体编号，不将指令目标当单位实际位置，不在观测点之间猜测路线。
构建在已有后台分析任务进行，查询仅二分时间索引，不重复扫描整场发令。
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
import math


def _integer(value):
    return value if type(value) is int and value >= 0 else None


def _seconds(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _position(value):
    if not isinstance(value, dict):
        return None
    x, y = value.get('x'), value.get('y')
    if (type(x) not in (int, float) or type(y) not in (int, float)
            or not math.isfinite(x) or not math.isfinite(y)
            or abs(x) > 1e7 or abs(y) > 1e7):
        return None
    return {'x': x, 'y': y}


def build_unit_state_index(report, sources, *, catalog=None):
    """仅按同玩家、原UID绑定，保留初始单位和转换缺口，不推测身份或队列执行。"""
    metadata = report.get('metadata') or {}
    build = metadata.get('build_number')
    game = (report.get('source') or {}).get('game_id')
    source_game = sources.get('game_id')
    if game is not None and source_game is not None and str(game) != str(source_game):
        raise ValueError('单位状态索引拒绝合并其他对局。')
    profiles = {row['player_id']: row.get('profile_id') for row in _rows(sources.get('identity_links'))
                if row.get('verified') is True and _integer(row.get('player_id')) is not None}
    players = {row['id']: row for row in _rows(report.get('players')) if _integer(row.get('id')) is not None}
    units = {}

    def unit_for(player, identity):
        if player not in profiles or _integer(identity) is None or identity <= 0:
            return None
        key = (player, identity)
        if key not in units:
            units[key] = {'player_id': player, 'profile_id': profiles[player], 'reference_kind': 2,
                          'original_id': identity, 'observations': [], 'command_indices': [],
                          'identity_ambiguous': False}
        return units[key]

    for group in _rows(sources.get('entity_observations')):
        unit = unit_for(group.get('player_id'), group.get('entity_id'))
        if unit is None:
            continue
        unit['identity_ambiguous'] = bool(group.get('identity_ambiguous'))
        for row in _rows(group.get('observations')):
            time = _seconds(row.get('time'))
            if time is None or row.get('kind') not in ('birth', 'loss', 'attack'):
                continue
            unit['observations'].append({'time': time, 'kind': row['kind'],
                                         'entity_type': row.get('entity_type'),
                                         'position': _position(row.get('position')),
                                         'source': row.get('source'), 'offset': row.get('offset')})
    linked_commands = 0
    linked_references = 0
    decoded_unit_references = 0
    rejected_references = 0
    first_commands = {}
    timeline = report.get('timeline') or []
    for index, command in enumerate(timeline):
        if not isinstance(command, dict):
            continue
        time = _seconds(command.get('time'))
        player = command.get('player_id')
        if time is None or player not in profiles:
            continue
        seen = set()
        for ref in _rows(command.get('selected_refs')):
            # encoding_build来自已经原生读写核验的解码器；旧紧凑units值不得加偏移猜绑。
            if ref.get('kind') != 2:
                continue
            if (command.get('selected_refs_verified') is not True or ref.get('encoding_build') != build
                    or _integer(ref.get('original_id')) is None):
                rejected_references += 1
                continue
            key = (player, ref['original_id'])
            if key in seen:
                continue
            seen.add(key)
            decoded_unit_references += 1
            existing = units.get(key)
            if existing is not None and existing['observations']:
                linked_references += 1
            unit = unit_for(*key)
            if unit is not None:
                unit['command_indices'].append(index)
                first_commands[key] = min(time, first_commands.get(key, time))
        if seen and any(units.get(key, {}).get('observations') for key in seen):
            linked_commands += 1
    records = []
    definitions = {}
    for key, unit in sorted(units.items()):
        observations = sorted(unit['observations'], key=lambda row: (row['time'], row['kind']))
        unit['observations'] = observations
        if not observations and key not in first_commands:
            continue
        birth = [row['time'] for row in observations if row['kind'] == 'birth']
        loss = [row['time'] for row in observations if row['kind'] == 'loss']
        first = min([row['time'] for row in observations] + ([first_commands[key]] if key in first_commands else []))
        unit.update(first_seen=first, birth_time=birth[0] if len(birth) == 1 else None,
                    loss_time=loss[0] if len(loss) == 1 else None,
                    identity_ambiguous=unit['identity_ambiguous'] or len(birth) > 1 or len(loss) > 1)
        # 器械在建时可预下命令，选择集也可遗留已损失编号。发令不产生存活/执行事实。
        unit['commands_before_appearance'] = sum(timeline[i]['time'] < unit['birth_time']
                                                for i in unit['command_indices']) if unit['birth_time'] is not None else 0
        unit['commands_at_or_after_loss'] = sum(timeline[i]['time'] >= unit['loss_time']
                                               for i in unit['command_indices']) if unit['loss_time'] is not None else 0
        unit['definitions'] = {}
        if catalog is not None and getattr(catalog, 'game_build', None) == build:
            for entity_type in sorted({row.get('entity_type') for row in observations if row.get('entity_type')}):
                if entity_type not in definitions:
                    resolver = getattr(catalog, 'resolve_stls_unit', None)
                    if callable(resolver):
                        definition = resolver(entity_type, build_number=build)
                    else:
                        from unit_combat_catalog import resolve_stls_unit
                        definition = resolve_stls_unit(entity_type, build_number=build, catalog=catalog)
                    definitions[entity_type] = {
                        'identity': definition['identity'], 'name_zh': definition.get('name_zh'),
                        'is_military': definition.get('is_military'), 'classes': definition.get('classes'),
                        'base_stats': definition.get('base_stats'), 'base_cost': definition.get('base_cost'),
                        'official_icon_key': definition.get('official_icon_key'),
                        'upgrades_applied': False} if definition is not None else None
                if definitions[entity_type] is not None:
                    unit['definitions'][entity_type] = definitions[entity_type]
        records.append(unit)
    return {'schema_version': 1, 'game_id': game, 'game_build': build,
            'report_sha256': (report.get('source') or {}).get('sha256'),
            'players': [{'player_id': player, 'profile_id': profile,
                         'name': players.get(player, {}).get('name')} for player, profile in sorted(profiles.items())],
            'units': records,
            'coverage': {'unit_groups': len(records), 'with_recorded_type': sum(bool(x['observations']) for x in records),
                         'with_birth': sum(x['birth_time'] is not None for x in records),
                         'with_loss': sum(x['loss_time'] is not None for x in records),
                         'decoded_unit_references': decoded_unit_references, 'linked_unit_references': linked_references,
                         'linked_commands': linked_commands, 'rejected_references': rejected_references},
            'capabilities': {'canonical_command_identity': bool(decoded_unit_references),
                             'recorded_lifecycles': True, 'continuous_positions': False,
                             'complete_initial_population': False, 'actual_production_queue': False},
            'command_time_checks': {'before_appearance': sum(u['commands_before_appearance'] for u in records),
                                    'at_or_after_loss': sum(u['commands_at_or_after_loss'] for u in records)},
            'semantics': '发令只绑定原单位组，不改变真实位置或证明执行。出现记录不强称绝对出生时刻；无出现记录的单位从第一次明确观察起入索引。'}


class UnitStateIndex:
    """同场时间查询；存活观测与当前位置分别返回，禁止把最后观测点伪装成当前点。"""

    def __init__(self, document, report):
        digest = (report.get('source') or {}).get('sha256')
        if document.get('schema_version') != 1 or document.get('report_sha256') != digest:
            raise ValueError('单位状态索引与当前录像不一致。')
        self.document = document
        self.timeline = report.get('timeline') or []
        self.units = {}
        for unit in _rows(document.get('units')):
            row = dict(unit)
            row['_times'] = [value['time'] for value in row['observations']]
            row['_command_times'] = [self.timeline[index]['time'] for index in row['command_indices']]
            self.units[(row['player_id'], row['original_id'])] = row

    def query_unit(self, player_id, original_id, seconds):
        time = _seconds(seconds)
        unit = self.units.get((player_id, original_id))
        if time is None or unit is None or unit['identity_ambiguous'] or time < unit['first_seen']:
            return None
        if unit['loss_time'] is not None and time >= unit['loss_time']:
            return None
        if unit['birth_time'] is not None and time < unit['birth_time']:
            return None
        stop = bisect_right(unit['_times'], time)
        observations = unit['observations'][:stop]
        latest = observations[-1] if observations else None
        # 未来损失处记载的兵种不能提前冒充早期兵种（例如升级或转化）。
        entity_type = latest.get('entity_type') if latest else None
        position_row = next((row for row in reversed(observations) if row.get('position') is not None), None)
        command_stop = bisect_right(unit['_command_times'], time)
        last_command = self.timeline[unit['command_indices'][command_stop - 1]] if command_stop else None
        exact_position = position_row['position'] if position_row and position_row['time'] == time else None
        definition = unit['definitions'].get(entity_type)
        excluded = bool(definition and 'spawn_ignored_on_timeline' in (definition.get('classes') or []))
        return {'player_id': player_id, 'original_id': original_id, 'reference_kind': unit['reference_kind'],
                'time': time, 'recorded_type': entity_type, 'definition': definition,
                'excluded_from_unit_counts': excluded,
                'birth_time': unit['birth_time'], 'last_position_observation': position_row,
                'position': exact_position, 'position_is_current': exact_position is not None,
                'last_command': ({key: last_command.get(key) for key in ('time', 'type', 'type_id', 'queued', 'target')}
                                 if last_command else None),
                'existence_basis': 'recorded_birth' if unit['birth_time'] is not None else 'first_explicit_observation'}

    def snapshot(self, seconds, player_id=None):
        units = [value for (player, identity) in self.units
                 if (player_id is None or player == player_id)
                 and (value := self.query_unit(player, identity, seconds)) is not None]
        # 官方原classes明确排除的dummy（逻辑占位）不能计作真实兵力。
        counts = Counter((row['player_id'], row['recorded_type']) for row in units
                         if not row['excluded_from_unit_counts'])
        return {'time': seconds, 'units': units,
                'counts': [{'player_id': player, 'recorded_type': entity_type, 'count': count}
                           for (player, entity_type), count in sorted(counts.items(), key=lambda value: str(value[0]))],
                'complete_initial_population': False, 'continuous_positions': False}
