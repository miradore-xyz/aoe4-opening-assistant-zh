"""只读复盘诊断：先核输入，再产生可回看的规则线索。

confirmed_fact（已记录事实）与needs_review（需要回看）分开；规则阈值是观察标准，
不是错误判决。无视野、真实队列、连续轨迹或已验证成本时不补造这些事实。
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

from replay_analysis import resolve_report_players
from diagnosis_contract import diagnostic_record, stable_id, capability


SCHEMA_VERSION = 2
DEFINITION_VERSION = "diagnosis-loss-ledger-v2"  # 原损失台账与候选分组口径。
DEFAULT_SETTINGS = {"battle_gap_seconds": 20, "battle_max_seconds": 60,
                    "battle_radius": 30, "battle_min_losses": 2,
                    "worker_gap_seconds": 60, "military_gap_seconds": 90,
                    "reserve_minutes": 2, "reserve_span_seconds": 60,
                    "max_sample_gap_seconds": 45, "max_findings": 3}
RESOURCE_NAMES = {"food": "食物", "wood": "木材", "gold": "黄金", "stone": "石料"}
METRIC_DEFINITIONS = {
    "battle_candidates": {
        "label": "局部交战候选", "inputs": ["同局STLS损失时空点", "来源攻击方玩家", "明确敌我队伍", "唯一非零目标原UID"],
        "formula": "全场原UID先去重/隔离冲突，再将同敌对队伍损失点按固定首点半径、相邻间隔和首末跨度分组；攻击方零编号不丢目标，未知归属单列。",
        "unit": "候选片段", "time_granularity": "原始损失事件秒数", "applicability": "只涵盖有明确敌我和坐标的损失记录",
        "confidence": "规则分组待回看；不代表完整战斗边界", "not_equivalent_to": "真实交战范围、全部伤害归属或胜负原因"},
    "worker_completion_gaps": {
        "label": "工人完成长间隔", "inputs": ["明确is_villager=True的单位完成/出现事件", "至少两个有效完成时刻"],
        "formula": "同玩家工人完成事件的相邻不同秒数之差达到设定观察标准。",
        "unit": "秒", "time_granularity": "完成事件原始秒数", "applicability": "不包含开局初始拥有，也不外推最后一次完成后的间隔",
        "confidence": "完成间隔事实；是否停产须回看", "not_equivalent_to": "城镇中心实际空闲、断村或可避免错误"},
    "military_completion_gaps": {
        "label": "军事单位完成长间隔", "inputs": ["同版本已验证军事分类", "至少两个真实单位完成/出现时刻"],
        "formula": "同玩家明确军事单位完成事件的相邻不同秒数之差达到设定观察标准。",
        "unit": "秒", "time_granularity": "完成事件原始秒数", "applicability": "is_villager=False本身不足以证明军事分类",
        "confidence": "完成间隔事实；是否停产须回看", "not_equivalent_to": "单一兵营空闲、完整军队产量或战力不足原因"},
    "resource_reserve_minutes": {
        "label": "来源采集速率归一的库存", "inputs": ["同玩家同资源库存采样", "相同采样时刻的正来源采集速率"],
        "formula": "储备分钟数=库存资源数÷来源每分钟采集速率；速率为零、负值或缺失时不计算。",
        "unit": "来源采集分钟", "time_granularity": "原始库存/速率采样时刻", "applicability": "按资源分别计算，不跨缺值或过大采样间隔连接",
        "confidence": "依来源采样口径的观察量", "not_equivalent_to": "合理战略储备、资源浪费或真实机会成本"},
    "resource_high_reserve_spans": {
        "label": "高储备连续采样段", "inputs": ["有效储备分钟数采样", "观察分钟标准", "最小首末采样跨度"],
        "formula": "连续有效采样均达到观察标准，且相邻间隔未超过上限，记录首末采样跨度。",
        "unit": "秒", "time_granularity": "连续采样首末点", "applicability": "仅证明这些采样点偏高；不能证明采样之间从未下降",
        "confidence": "规则线索待回看", "not_equivalent_to": "已证资源闲置、经济错误或损失的军队数量"},
    "reference_resource_loss": {
        "label": "已验证参考成本合计", "inputs": ["每个损失目标的同版本validated单位成本", "完整四资源成本", "明确成本来源"],
        "formula": "全部目标均有同版本已验证参考成本时，按队合计四资源账面成本；任一目标未知则该队为空。",
        "unit": "参考资源", "time_granularity": "候选片段", "applicability": "不处理未经验证的折扣、返还或动态成本",
        "confidence": "给定成本口径的账面合计", "not_equivalent_to": "真实净经济损失、战斗收益或实际资源交换效率"},
}


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _number(value, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value if not nonnegative or value >= 0 else None


def _id(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    return int(value) if isinstance(value, str) and value.isdecimal() else None


def _settings(settings):
    values = dict(DEFAULT_SETTINGS)
    if isinstance(settings, dict):
        for key in values:
            if key in settings:
                value = _number(settings[key])
                if value is None or value <= 0:
                    raise ValueError("诊断观察标准必须是正的有限数值：" + key)
                values[key] = value
    for key in ("battle_min_losses", "max_findings"):
        if int(values[key]) != values[key]:
            raise ValueError("候选数量设置必须为正整数：" + key)
        values[key] = int(values[key])
    return values


def _metric(label, value, unit):
    return {"label": label, "value": value, "unit": unit}


def _clock_text(value, upper=False):
    seconds = math.ceil(value) if upper else math.floor(value)
    return f'{seconds // 60}:{seconds % 60:02d}'


def _fact_evidence(source, **fields):
    return dict(source=source, **{key: value for key, value in fields.items() if value is not None})


def _quality(report, sources, selected):
    source = report.get("source") or {}
    game = _id(source.get("game_id"))
    stls = sources.get("stls") if isinstance(sources.get("stls"), dict) else {}
    same_game = game is not None and game == _id(sources.get("game_id")) == _id(stls.get("game_id"))
    provenance = _rows(sources.get("provenance"))
    digest = source.get("sha256")
    summary_digest = stls.get("summary_sha256")
    digest_ok = bool(isinstance(digest, str) and len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)
        and isinstance(summary_digest, str) and len(summary_digest) == 64 and all(char in "0123456789abcdef" for char in summary_digest) and any(
        row.get("kind") == "official_statistics" and row.get("report_sha256") == digest
        and _id(row.get("game_id")) == game and row.get("summary_sha256") == stls.get("summary_sha256")
        for row in provenance))
    players = {p["id"]: dict(p) for p in resolve_report_players(report)}
    forward, reverse = defaultdict(set), defaultdict(set)
    for link in _rows(sources.get("identity_links")):
        pid, profile = _id(link.get("player_id")), _id(link.get("profile_id"))
        if link.get("verified") is True and pid is not None and profile is not None:
            forward[pid].add(profile)
            reverse[profile].add(pid)
    linked = {}
    setup_teams, setup_conflicts = {}, []
    for row in _rows((report.get('metadata') or {}).get('players')) + _rows(report.get('players')):
        proof = row.get('team_evidence') or {}
        value = proof.get('value')
        if (proof.get('source') == 'replay_setup' and type(value) is int and 0 <= value <= 63
                and proof.get('setup_version') in (56, 58, 60) and _id(proof.get('offset')) is not None):
            pid = _id(row.get('id'))
            if pid in setup_teams and setup_teams[pid] != value:
                setup_conflicts.append([pid, pid])
            setup_teams[pid] = value
    detail = sources.get("detail") if isinstance(sources.get("detail"), dict) else {}
    detail_players = _rows(detail.get("players")) if _id(detail.get("game_id")) == game else []
    detail_index = defaultdict(list)
    for player in detail_players:
        detail_index[_id(player.get("profile_id"))].append(player)
    for pid, player in players.items():
        profiles = forward[pid]
        if len(profiles) != 1:
            continue
        profile = next(iter(profiles))
        if reverse[profile] != {pid} or _id(player.get("profile_id")) not in (None, 0, profile):
            continue
        matches = detail_index[profile]
        if len(matches) != 1:
            continue
        detail_team = _id(matches[0].get('team'))
        linked[pid] = dict(player, profile_id=profile, team=setup_teams.get(pid, detail_team),
                           detail_team=detail_team,
                           team_source='replay_setup' if pid in setup_teams else 'same_game_detail', detail=matches[0])
    # 摘要的队号可为1/2而原录像为0/1；核对的是同队分区，不能直接比标签数字。
    team_conflicts = list(setup_conflicts)
    checked = [p for p in linked if p in setup_teams and linked[p]['detail_team'] is not None]
    for index, left in enumerate(checked):
        for right in checked[index + 1:]:
            if ((setup_teams[left] == setup_teams[right]) !=
                    (linked[left]['detail_team'] == linked[right]['detail_team'])):
                team_conflicts.append([left, right])
    if any(pid in setup_teams for pid in linked):
        public_to_setup = defaultdict(set)
        for pid in checked:
            public_to_setup[linked[pid]['detail_team']].add(setup_teams[pid])
        for pid, player in linked.items():
            if pid in setup_teams:
                continue
            candidates = public_to_setup[player['detail_team']]
            player['team'] = next(iter(candidates)) if len(candidates) == 1 else None
            player['team_source'] = 'public_partition_mapped_to_replay_setup' if len(candidates) == 1 else 'unmapped_public_partition'
    identity = bool(linked) and (selected is None or selected in linked)
    duration = _number((report.get("metadata") or {}).get("duration"), True)
    stats_duration = _number((stls.get("header") or {}).get("game_length"), True)
    clock_ok = duration is not None and stats_duration is not None and abs(duration - stats_duration) <= 1
    timeline = _rows(report.get("timeline"))
    eligible_commands = [r for r in timeline if _id(r.get("player_id")) not in (None, 0)
                         and (selected is None or _id(r.get("player_id")) == selected)]
    unknown = sum(row.get("known") is not True for row in eligible_commands)
    unknown_rate = unknown / len(eligible_commands) if eligible_commands else None
    gaps = ["未取得真实视野、逐建筑队列实际状态、连续单位轨迹或完整初始中立资源表。",
            "损失的来源攻击方不是完整伤害归属；资源采集速率的原生统计窗口尚未独立验证。"]
    gates = {"same_game": same_game, "source_digest": digest_ok, "identity": identity, "clock": clock_ok}
    if team_conflicts:
        gates['team_partition'] = False
    reasons = {"same_game": "录像与补源没有相同且明确的对局编号。", "source_digest": "补源摘要未通过本录像摘要绑定。",
               "identity": "所选范围缺少唯一且明确的局内编号/账号桥。", "clock": "录像终时与原始统计终时缺失或相差超过1秒。"}
    gaps.extend(reason for key, reason in reasons.items() if not gates[key])
    if team_conflicts:
        gaps.append('录像原队伍与同局摘要的同队分区冲突，已停止交战判断。')
    if len(linked) != len(players):
        gaps.append("部分玩家缺少唯一同局账号关联，相关事件未纳入诊断。")
    return {"status": "partial" if all(gates.values()) else "blocked", "gates": gates,
            "unknown_command_rate": unknown_rate,
            "clock": {"report_seconds": duration, "statistics_seconds": stats_duration,
                      "header_delta_seconds": duration - stats_duration if duration is not None and stats_duration is not None else None,
                      "isolated_header_corrected": (sources.get("clock") or {}).get("isolated_duration_header_corrected") is True,
                      "scope": "这里只校验终时声明；保留各源原秒数，不能由终时一致推断全部事件流均已完整校准。"},
            "team_partition_conflicts": team_conflicts,
            "team_sources": {str(pid): row['team_source'] for pid, row in linked.items()},
            "counts": {"report_players": len(players), "linked_players": len(linked), "player_commands": len(eligible_commands),
                       "unknown_commands": unknown, "stls_created": len(_rows(stls.get("created"))), "stls_lost": len(_rows(stls.get("lost")))},
            "gaps": gaps, "provenance": provenance,
            "metrics": {key: {"status": "available" if all(gates.values()) else "blocked", "definition_version": DEFINITION_VERSION,
                               "gates": list(gates), "scope": definition["confidence"]} for key, definition in METRIC_DEFINITIONS.items()}}, linked


def _definition_baseline(report, player, metric, value, unit, **extra):
    metadata = report.get("metadata") or {}
    return dict(player_id=player["id"], profile_id=player["profile_id"], metric_id=metric, value=value, unit=unit,
                definition_version=DEFINITION_VERSION,
                conditions={"game_id": (report.get("source") or {}).get("game_id"), "build_number": metadata.get("build_number"),
                            "map": metadata.get("map"), "civilization": player.get("civilization")}, **extra)


def _finding(identity, rule, title, start, end, facts, inference, unknowns, goal, evidence, metrics=(), series=(), priority=0):
    players = sorted({row['player_id'] for row in evidence if _id(row.get('player_id')) is not None})
    row = diagnostic_record(identity, rule, title, start, end, players=players, facts=facts,
        reference={'kind': 'observation_rule', 'not_error_benchmark': True}, gap=None, evidence=evidence,
        confidence={'level': 'medium', 'reason': '原事件或采样可核对，实际原因尚未确认。'},
        severity={'level': 'review', 'reason': '观察性线索，不认定玩家错误。'},
        recommendations=[goal['action']], training_goal=goal, limitations=unknowns, kind='observation',
        rule_version=DEFINITION_VERSION, metrics=metrics, series=series)
    row.update(grade='needs_review', fact_grade='confirmed_fact', inference=inference, unknowns=unknowns,
               train_goal=goal, priority=priority)
    return row


def _reference_cost(entity_type, context, build):
    """适配两种已验证资料结构；只合计可唯一使用的基础四资源名义成本。"""
    row = (context.get('unit_costs') or {}).get(entity_type)
    empty = {'available': False, 'resources': None, 'total': None, 'reason': '缺少同版本明确成本', 'source': None}
    if not isinstance(row, dict) or build is None or not row.get('source'):
        return empty
    legacy = row.get('validated') is True and row.get('build_number') == build
    official = (row.get('game_build') == build and context.get('game_build', build) == build
                and row.get('nominal_only') is True and row.get('namespace') == 'ebps')
    raw = row.get('resources') if legacy else row if official else {}
    values = {key: _number(raw.get(key), True) for key in RESOURCE_NAMES}
    if not (legacy or official) or any(value is None for value in values.values()):
        return empty
    result = dict(empty, source=row['source'], base_definition_resources=values,
                  semantics='同构建官方基础名义成本；未计算折扣、返还、动态科技或净经济损失。')
    if official and row.get('stls_label_namespace') == 'sbps':
        if not row.get('squad_loadout_source'):
            return dict(result, reason='小队名称缺少原loadout绑定证据')
        result['label_binding'] = {key: row.get(key) for key in ('stls_label_namespace', 'stls_label',
            'entity_internal_name', 'squad_pbgid', 'squad_loadout_source')}
    if official and (row.get('validated_for_scalar_comparison') is not True
                     or row.get('cost_variant_invariant') is not True or row.get('merc_byz') != 0):
        result['reason'] = '原成本存在变体差异或额外货币，不能作为唯一四资源标量'
        return result
    result.update(available=True, resources=values, total=sum(values.values()), reason=None)
    return result


def _validated_cost(entity_type, context, build):
    return _reference_cost(entity_type, context, build)['total']


def _loss_ledger(report, sources, linked, context):
    """全场先核单位生命周期，再分组；不以攻击方零编号丢弃明确目标损失。"""
    build = (report.get('metadata') or {}).get('build_number')
    game = (report.get('source') or {}).get('game_id')
    summary = (sources.get('stls') or {}).get('summary_sha256')
    duration = _number((report.get('metadata') or {}).get('duration'), True)
    ambiguous = {(_id(g.get('player_id')), _id(g.get('entity_id'))) for g in _rows(sources.get('entity_observations'))
                 if g.get('identity_ambiguous') is True}
    definitions = {}
    state = sources.get('unit_state') or {}
    if (state.get('report_sha256') == (report.get('source') or {}).get('sha256')
            and state.get('game_build') == build and str(state.get('game_id')) == str(game)):
        for unit in _rows(state.get('units')):
            key = (_id(unit.get('player_id')), _id(unit.get('original_id')))
            if unit.get('identity_ambiguous') is True:
                ambiguous.add(key)
            definitions[key] = unit.get('definitions') or {}
    counters, unidentified, quarantined, invalid_uids = Counter(), [], [], set()
    by_uid = defaultdict(list)
    for raw in _rows((sources.get('stls') or {}).get('lost')):
        counters['raw_loss_records'] += 1
        pid, uid = _id(raw.get('target_player_id')), _id(raw.get('target_entity_id'))
        t, x, y = _number(raw.get('time'), True), _number(raw.get('target_x')), _number(raw.get('target_y'))
        if (raw.get('has_target') != 1 or pid not in linked or t is None or x is None or y is None
                or duration is not None and t > duration + 0.125):
            counters['invalid_target_time_position_or_identity'] += 1
            if uid not in (None, 0):
                invalid_uids.add(uid)
            continue
        attacker = _id(raw.get('attacker_player_id'))
        auid = _id(raw.get('attacker_entity_id'))
        has_attacker = raw.get('has_attacker') == 1
        own_team = linked[pid].get('team')
        enemy_team = linked.get(attacker, {}).get('team') if has_attacker else None
        if not has_attacker:
            attribution = 'unattributed'
        elif attacker not in linked:
            attribution = 'unknown_attacker_player'
        elif attacker == pid:
            attribution = 'self'
        elif own_team is None or enemy_team is None:
            attribution = 'unknown_team'
        elif own_team == enemy_team:
            attribution = 'same_team'
        else:
            attribution = 'enemy'
        definition = definitions.get((pid, uid), {}).get(raw.get('target_type')) or {}
        classes = definition.get('classes') or []
        role = ('logic_dummy' if 'spawn_ignored_on_timeline' in classes else
                'military' if definition.get('is_military') is True else 'worker' if 'villager' in classes else 'unknown')
        ax, ay = _number(raw.get('attacker_x')), _number(raw.get('attacker_y'))
        attacker_definition = definitions.get((attacker, auid), {}).get(raw.get('attacker_type')) or {}
        event = {'time': t, 'target_player_id': pid, 'target_entity_id': uid, 'target_type': raw.get('target_type'),
                 'target_name_zh': definition.get('name_zh'), 'target_x': x, 'target_y': y, 'has_target': 1,
                 'attacker_player_id': attacker if has_attacker else None,
                 'attacker_entity_id': auid if has_attacker else None, 'attacker_type': raw.get('attacker_type') if has_attacker else None,
                 'attacker_name_zh': attacker_definition.get('name_zh') if has_attacker else None,
                 'attacker_x': ax, 'attacker_y': ay, 'has_attacker': int(has_attacker),
                 'weapon_type': raw.get('weapon_type') if has_attacker else None,
                 'target_team': own_team, 'attacker_team': enemy_team, 'attribution': attribution,
                 'attacker_entity_resolved': bool(has_attacker and auid not in (None, 0) and (attacker, auid) not in ambiguous),
                 'role': role, 'position': {'x': x, 'y': y},
                 'attacker_position': {'x': ax, 'y': ay} if ax is not None and ay is not None else None,
                 'source': 'STLS.lostEntities', 'summary_sha256': summary, 'offset': _id(raw.get('offset')),
                 'source_offsets': [_id(raw.get('offset'))] if _id(raw.get('offset')) is not None else [],
                 'reference_cost': _reference_cost(raw.get('target_type'), context, build)}
        event['evidence_key'] = [summary, 'STLS.lostEntities', event['offset']] if event['offset'] is not None else [
            summary, pid, uid, t, x, y, raw.get('target_type'), attacker, auid]
        if uid in (None, 0):
            # 多段墙/建筑可同玩家同秒且UID均0；不能按(player,0,time)合成一件。
            unidentified.append(event)
            counters['target_UID_zero_or_missing'] += 1
            continue
        if role == 'logic_dummy':
            counters['logic_dummy_excluded'] += 1
            continue
        by_uid[uid].append(event)
    canonical = []
    signature_keys = ('time', 'target_player_id', 'target_type', 'attacker_player_id', 'attacker_entity_id',
                      'attacker_type', 'weapon_type', 'position', 'attacker_position', 'attribution')
    for uid, events in by_uid.items():
        first = events[0]
        conflict = (uid in invalid_uids or any((e['target_player_id'], uid) in ambiguous for e in events)
                    or any(any(e.get(k) != first.get(k) for k in signature_keys) for e in events[1:]))
        if conflict:
            counters['conflicting_lifecycle_records'] += len(events)
            quarantined.append({'original_id': uid, 'reason': '同一原UID的玩家/时刻/类型/位置或生命周期证据冲突',
                                'events': events})
            continue
        if len(events) > 1:
            counters['duplicate_loss_record'] += len(events) - 1
            first = min(events, key=lambda e: e['offset'] if e['offset'] is not None else math.inf)
            first['source_offsets'] = sorted({o for e in events for o in e['source_offsets']})
        canonical.append(first)
        counters['canonical_unit_losses'] += 1
        counters['attribution_' + first['attribution']] += 1
        if first['attribution'] == 'enemy' and not first['attacker_entity_resolved']:
            counters['enemy_loss_with_unresolved_attacker_entity'] += 1
    canonical.sort(key=lambda e: (e['time'], e['offset'] if e['offset'] is not None else math.inf,
                                   e['target_player_id'], e['target_entity_id']))
    return {'events': canonical, 'unidentified_target_records': unidentified, 'quarantined_lifecycles': quarantined,
            'counts': dict(counters), 'semantics': '原UID在全场损失流先去重/隔离冲突；未知归属与零编号目标单列，不虚填完整交战总量。'}


def _side_losses(team, events, nearby):
    own = [e for e in events if e['target_team'] == team]
    unknown = [e for e in nearby if e['target_team'] == team]
    known = [e for e in own if e['reference_cost']['available']]
    known_resources = {r: sum(e['reference_cost']['resources'][r] for e in known) for r in RESOURCE_NAMES}
    complete_cost = bool(own) and len(known) == len(own)
    names = {e['target_type']: e.get('target_name_zh') for e in own}
    by_player = []
    for pid in sorted({e['target_player_id'] for e in own}):
        subset = [e for e in own if e['target_player_id'] == pid]
        complete = all(e['reference_cost']['available'] for e in subset)
        by_player.append({'player_id': pid, 'count': len(subset), 'roles': dict(Counter(e['role'] for e in subset)),
                          'resource_loss': sum(e['reference_cost']['total'] for e in subset) if complete else None,
                          'unit_types': [{'internal_name': name, 'name_zh': names.get(name), 'count': count}
                                         for name, count in Counter(e['target_type'] for e in subset).most_common()]})
    return {'team': team, 'count': len(own), 'recorded_loss_count': len(own), 'total_battle_loss_count': None,
            'by_player': by_player,
            'roles': dict(Counter(e['role'] for e in own)),
            'unit_types': [{'internal_name': name, 'name_zh': names.get(name), 'count': count} for name, count in Counter(e['target_type'] for e in own).most_common()],
            'resource_loss': sum(known_resources.values()) if complete_cost else None,
            'reference_resources': known_resources if complete_cost else None,
            'known_reference_resources': known_resources if known else None,
            'priced_loss_count': len(known), 'unpriced_loss_count': len(own) - len(known),
            'unpriced_losses': [{'player_id': e['target_player_id'], 'original_id': e['target_entity_id'],
                                'internal_name': e['target_type'], 'reason': e['reference_cost']['reason']} for e in own if not e['reference_cost']['available']],
            'nearby_unattributed_loss_count': len(unknown), 'nearby_unattributed_losses': unknown,
            'value_scope': 'explicit_enemy_attributed_records_only',
            'cost_semantics': '同版本基础四资源名义成本；未归属/未定价目标不补0，不代表实际净损失。',
            'entity_keys': [[e['target_player_id'], e['target_entity_id']] for e in own]}


def _battles(report, sources, linked, selected, settings, context):
    ledger = _loss_ledger(report, sources, linked, context)
    seeds = [e for e in ledger['events'] if e['attribution'] == 'enemy']
    clusters, active = [], []
    for event in seeds:
        time = event['time']
        pair = tuple(sorted((event['target_team'], event['attacker_team']), key=str))
        active = [g for g in active if time - g['end'] <= settings['battle_gap_seconds']
                  and time - g['start'] <= settings['battle_max_seconds']]
        choices = []
        for group in active:
            distance = math.hypot(event['position']['x'] - group['anchor'][0], event['position']['y'] - group['anchor'][1])
            if group['teams'] == pair and distance <= settings['battle_radius']:
                choices.append((distance, group['start'], group))
        if choices:
            group = min(choices, key=lambda c: (c[0], c[1]))[2]
        else:
            group = {'start': time, 'end': time, 'teams': pair,
                     'anchor': (event['position']['x'], event['position']['y']), 'events': [], 'nearby': []}
            clusters.append(group); active.append(group)
        group['events'].append(event); group['end'] = time
    # 无归属损失只在已成立候选的原时窗内补充上下文，不扩张边界，也不当敌方击杀。
    for event in ledger['events']:
        if event['attribution'] not in ('unattributed', 'unknown_attacker_player', 'unknown_team'):
            continue
        choices = []
        for group in clusters:
            if (not group['start'] <= event['time'] <= group['end']
                    or event['target_team'] is not None and event['target_team'] not in group['teams']):
                continue
            distance = math.hypot(event['position']['x'] - group['anchor'][0], event['position']['y'] - group['anchor'][1])
            if distance <= settings['battle_radius']:
                choices.append((distance, group['start'], group))
        if choices:
            min(choices, key=lambda c: (c[0], c[1]))[2]['nearby'].append(event)
    output = []
    game = (report.get('source') or {}).get('game_id')
    for group in clusters:
        events, nearby = group['events'], group['nearby']
        if len(events) < settings['battle_min_losses']:
            continue
        players = sorted({e['target_player_id'] for e in events + nearby} |
                         {e['attacker_player_id'] for e in events if e['attacker_player_id'] in linked})
        losses = [_side_losses(team, events, nearby) for team in group['teams']]
        for side in losses:
            for row in side['by_player']:
                row['name'] = linked[row['player_id']].get('name')
        start, end = group['start'], group['end']
        identity = stable_id(game, 'battle_candidates', [e['evidence_key'] for e in events + nearby])
        region = {'x': group['anchor'][0], 'y': group['anchor'][1], 'radius': settings['battle_radius'],
                  'unit': '原始坐标单位', 'min_x': min(e['position']['x'] for e in events),
                  'max_x': max(e['position']['x'] for e in events), 'min_y': min(e['position']['y'] for e in events),
                  'max_y': max(e['position']['y'] for e in events)}
        facts = [f'{start:g}～{end:g}秒内，固定区域记录到{len(events)}个明确敌方来源的不同原UID损失目标。',
                 '；'.join(f"队伍{s['team']}记录损失{s['count']}个目标" for s in losses) + '。']
        if nearby:
            facts.append(f'同区同时窗另有{len(nearby)}个来源不明的单位损失，单独列示。')
        unknowns = ['这是损失时空候选，真实接敌/脱离边界、全部参战单位和完整伤害归属未知。',
                    '记录零损失的一方不等于实际无损；未由损失数判断兵力、科技、操作或撤退是否不足。']
        if any(s['unpriced_loss_count'] for s in losses):
            unknowns.append('部分基础参考成本未唯一验证，只列已定价部分，未补全总价值或计算交换比。')
        evidence = [_fact_evidence('STLS.lostEntities', game_id=game, summary_sha256=e['summary_sha256'],
            time=e['time'], offset=e['offset'], source_offsets=e['source_offsets'], player_id=e['target_player_id'],
            entity_id=e['target_entity_id'], entity_type=e['target_type'], attacker_player_id=e['attacker_player_id'],
            attacker_entity_id=e['attacker_entity_id'], attribution=e['attribution'], position=e['position']) for e in events + nearby]
        military = sum(s['roles'].get('military', 0) for s in losses)
        worker = sum(s['roles'].get('worker', 0) for s in losses)
        title = f'优先回看{int(start) // 60}:{int(start) % 60:02d}片段'
        review_targets = []
        for side in losses:
            for person in side['by_player']:
                labels = '、'.join(f"{u.get('name_zh') or '未定名单位'}{u['count']}个" for u in person['unit_types'][:3])
                focus = person.get('name') or str(person['player_id'])
                workers = person['roles'].get('worker', 0)
                unknown_roles = person['roles'].get('unknown', 0)
                facts.append(f"{focus}记录损失{person['count']}个目标，已核工人{workers}个；主要为{labels}。")
                location = f"原坐标({region['x']:.1f}, {region['y']:.1f})附近"
                main = person['unit_types'][0]
                main_name = main.get('name_zh')
                worker_events = [e for e in events if e['target_player_id'] == person['player_id'] and e['role'] == 'worker']
                advice_start = min(e['time'] for e in worker_events) if worker_events else start
                advice_end = max(e['time'] for e in worker_events) if worker_events else end
                if workers:
                    condition = '若回看确认这是常规采集点，且存在可行的安全撤离方向'
                    training_action = '下一局将外出工人单独编组，遇袭时优先撤往已确认安全的位置。'
                    measurement = '同类片段复测工人损失，并人工区分可避免损失；不把原死亡时刻当首次受攻击或已看见时刻。'
                elif main_name:
                    condition = f'若回看确认这批{main_name}有可行的撤退通道，且损失并非有意交换目标'
                    training_action = f'下一局只练一项：决定脱离时，用{main_name}控制组统一后撤到预设集结点。'
                    measurement = f'复盘记录同类片段中已人工确认可避免的{main_name}损失数，并核对撤退命令是否选中了该组。'
                else:
                    condition = '单位类型或战术条件尚未确认'
                    training_action = '先从这个已定位片段逐个确认损失对象，再确定一项训练动作。'
                    measurement = '没有确认可避免问题之前，不设错误数量或反应时间目标。'
                if workers:
                    advice = (f"{focus}在{_clock_text(advice_start)}～{_clock_text(advice_end, True)}、"
                              f"{location}记录{workers}名村民遇袭损失。先核对这处采集点的保护与撤离。训练检查方向：{training_action}")
                else:
                    advice = (f"{focus}在{_clock_text(start)}～{_clock_text(end, True)}、"
                              f"{location}损失的主要对象为{labels}。训练检查方向：{condition}，{training_action}")
                review_targets.append({'player_id': person['player_id'], 'recorded_losses': person['count'],
                    'unit_types': person['unit_types'], 'known_worker_losses': workers,
                    'worker_losses': workers if not unknown_roles else None,
                    'time_range': [advice_start, advice_end],
                    'condition': condition, 'action': advice, 'measurement': measurement,
                    'reaction_time_measured': False})
        review_targets.sort(key=lambda p: (-p['recorded_losses'], p['player_id']))
        recommendations_by_player = {str(t['player_id']): [t['action']] for t in review_targets}
        goals_by_player = {str(t['player_id']): {'action': t['action'], 'metric_id': 'battle_candidates',
            'player_id': t['player_id'], 'condition': t['condition'], 'target': t['measurement'],
            'time_range': t['time_range'], 'reaction_time_measured': False} for t in review_targets}
        chosen = next((t for t in review_targets if t['player_id'] == selected), None) if selected is not None else review_targets[0]
        goal = goals_by_player[str(chosen['player_id'])] if chosen else None
        recommendations = recommendations_by_player.get(str(selected), []) if selected is not None else [t['action'] for t in review_targets]
        unknowns.extend(t['condition'] + '；这是训练检查方向，不是已确认失误。' for t in review_targets)
        known_difference = (losses[0]['resource_loss'] - losses[1]['resource_loss']
                            if all(s['resource_loss'] is not None for s in losses) else None)
        battle = diagnostic_record(identity, 'battle_candidates', title, start, end, players=players, teams=group['teams'],
            entities=[{'player_id': e['target_player_id'], 'original_id': e['target_entity_id'], 'namespace': 'recorded_unit_uid'} for e in events],
            region=region, facts=facts, reference={'kind': 'same_candidate_recorded_sides',
                'label': '同候选双方记录损失与基础名义成本', 'teams': list(group['teams']),
                'conditions': {'radius': settings['battle_radius'], 'gap_seconds': settings['battle_gap_seconds'],
                               'max_span_seconds': settings['battle_max_seconds']}, 'not_error_benchmark': True},
            gap={'kind': 'recorded_reference_cost_difference', 'value': known_difference, 'unit': '基础四资源名义值'},
            evidence=evidence, confidence={'level': 'medium', 'facts': 'high', 'boundary': 'rule_candidate',
                'reason': '损失身份/时刻有原记录；候选边界与未归属损失尚待回看。'},
            severity={'level': 'review', 'reason': '记录损失规模用于回看排序，不据此认定错误严重性。'},
            impacts=[{'kind': 'recorded_losses', 'count': len(events), 'military': military, 'workers': worker,
                      'reference_cost_by_team': [{'team': s['team'], 'value': s['resource_loss']} for s in losses]}],
            recommendations=recommendations, training_goal=goal, limitations=unknowns, kind='review_segment',
            rule_version=DEFINITION_VERSION, metrics=[_metric('已核目标损失', len(events), '个'), _metric('候选跨度', end - start, '秒')])
        battle.update(position={'x': region['x'], 'y': region['y'], 'unit': region['unit']}, losses=losses, events=events,
            battle_id=identity, diagnostic_id=identity,
            review_targets=review_targets,
            recommendations_by_player=recommendations_by_player, training_goals_by_player=goals_by_player,
            nearby_unattributed_losses=nearby, inference='这些损失构成可定位的局部交战候选；原因需要结合原操作与地图回看。',
            nearby_unassigned_team_losses=[e for e in nearby if e['target_team'] is None],
            unknowns=unknowns, train_goal=goal, grade='needs_review', fact_grade='confirmed_fact', boundary_verified=False,
            grouping={'anchor': '首个目标固定原坐标', 'radius': settings['battle_radius'], 'radius_unit': '原始坐标单位，非米',
                      'max_span_seconds': settings['battle_max_seconds'], 'gap_seconds': settings['battle_gap_seconds'],
                      'max_diameter_bound': 2 * settings['battle_radius']},
            review_order_basis={'recorded_target_losses': len(events), 'not_severity_score': True})
        output.append(battle)
    output.sort(key=lambda b: (-len(b['events']), b['time_range'][0], b['id']))
    for index, battle in enumerate(output):
        battle['review_rank'] = index + 1
    if selected is not None:
        output = [b for b in output if selected in b['players']]
    return output, list(output), ledger


def _military_event(event, context, build):
    # 单凭“不是村民”不能识别军事单位；分类必须显式验证、同版本且有来源。
    classification = (context.get("unit_classifications") or {}).get(str(event.get("pbgid")))
    if classification is None:
        classification = (context.get("unit_classifications") or {}).get(event.get("pbgid"))
    return (isinstance(classification, dict) and classification.get("role") == "military"
            and classification.get("validated") is True and classification.get("build_number") == build
            and bool(classification.get("source")))


def _production(report, linked, selected, settings, context):
    findings, baselines, counts = [], [], Counter()
    build = (report.get("metadata") or {}).get("build_number")
    for player in linked.values():
        if selected is not None and player["id"] != selected:
            continue
        completed = [event for event in _rows(player["detail"].get("events"))
                     if event.get("kind") == "production" and event.get("entity_type") == "Unit"
                     and _id(event.get("profile_id")) == player["profile_id"]
                     and _number(event.get("time"), True) is not None and event["time"] > 0]
        for role, label, threshold in (("worker", "工人", settings["worker_gap_seconds"]),
                                       ("military", "军事单位", settings["military_gap_seconds"])):
            events = [event for event in completed if (event.get("is_villager") is True if role == "worker"
                                                       else _military_event(event, context, build))]
            by_time = defaultdict(list)
            for event in events:
                by_time[event["time"]].append(event)
            times = sorted(by_time)
            rule = role + "_completion_gaps"
            counts[role + "_completion_times"] += len(times)
            if len(times) < 2:
                continue
            counts[role + "_eligible_players"] += 1
            gaps = [(a, b) for a, b in zip(times, times[1:]) if b - a >= threshold]
            baselines.append(_definition_baseline(report, player, rule, len(gaps), "待核实间隔",
                                                 max_gap_seconds=max(b - a for a, b in zip(times, times[1:])), threshold_seconds=threshold))
            for start, end in gaps:
                evidence = [_fact_evidence(event.get("source") or "detail.events", time=event["time"],
                                          profile_id=player["profile_id"], player_id=player["id"], pbgid=event.get("pbgid"),
                                          quantity=event.get("quantity")) for event in by_time[start] + by_time[end]]
                identity = f"{rule}-{player['id']}-{start:g}-{end:g}"
                findings.append(_finding(identity, rule, f"核对{label}完成长间隔", start, end,
                    [f"{player.get('name') or player['id']}的相邻两次{label}完成/出现记录相隔{end-start:g}秒，达到{threshold:g}秒观察标准。",
                     "此结论仅针对已记录的同类完成时刻，不包含开始排队和建筑实际工作状态。"],
                    "这段完成间隔值得回看，不能直接判为停产或操作失误。",
                    ["生产建筑身份、实际队列、取消、人口限制与资源条件未知。", "可能存在主动停造、转化或来源事件缺口；多个建筑的完成记录不能定位单建筑闲置。"],
                    {"action": "回看间隔内的生产安排；若确认不是主动停造，下局在交战与扩张后检查对应生产队列。",
                     "metric_id": rule, "target": "同版本、同开局条件下复测待核实长间隔数量和最长跨度；先核实意图再决定是否缩短。"},
                    evidence, [_metric("相邻完成间隔", end-start, "秒"), _metric("观察标准", threshold, "秒")],
                    [{"label": f"{label}完成时刻", "points": [[time, 1] for time in times if start <= time <= end], "unit": "完成事件时刻"}],
                    priority=(end-start) / threshold))
    return findings, baselines, dict(counts)


def _point_map(series):
    points = series.get("points") if isinstance(series, dict) else None
    if not isinstance(points, list):
        return None
    values, previous = {}, None
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        time = _number(point[0], True)
        if time is None or (previous is not None and time <= previous):
            return None
        values[time], previous = _number(point[1], True), time
    return values


def _capability_inventory(report, sources, linked, quality, ledger=None, battles=()):
    """来源事实A、确定公式B、候选模型C与当前缺失D各自门控。"""
    ledger = ledger or {'events': [], 'counts': {}}
    ready = bool(quality.get('gates')) and all(quality['gates'].values())
    events = ledger['events']
    priced = sum(e['reference_cost']['available'] for e in events)
    stock_points, income_points, completed = 0, 0, 0
    for player in linked.values():
        for event in _rows(player['detail'].get('events')):
            if (event.get('kind') == 'production' and event.get('entity_type') == 'Unit'
                    and _id(event.get('profile_id')) == player['profile_id'] and _number(event.get('time'), True) is not None):
                completed += 1
        for key, series in (player['detail'].get('series') or {}).items():
            if not key.startswith(('bank_', 'income_')):
                continue
            points = _point_map(series)
            count = sum(v is not None for v in points.values()) if points is not None else 0
            if key.startswith('bank_'):
                stock_points += count
            else:
                income_points += count
    completed += sum(e.get('category') == 46 for e in _rows((sources.get('stls') or {}).get('created')))
    specifications = [
        ('loss_events', '损失原记录', 'A', ledger['counts'].get('raw_loss_records', 0) > ledger['counts'].get('invalid_target_time_position_or_identity', 0), ['同局STLS原损失时刻/目标/来源字段'],
         '原记录包含零编号目标与未知来源，后续计算分别列示。', '损失记录'),
        ('canonical_unit_losses', '可核单位损失', 'B', bool(events), ['原UID全场去重', '冲突生命周期隔离'],
         '零编号目标不冒充不同单位，跨玩家或多时刻冲突不重复计数。', '不同原UID损失'),
        ('nominal_loss_cost', '基础名义损失值', 'B', bool(priced), ['同build官方成本', '完整四资源', '变体/额外货币门控'],
         f'已定价{priced}/{len(events)}个可核目标；各候选成本未全覆盖时总值为空。', '四资源基础名义值'),
        ('battle_grouping', '候选交战分组', 'C', bool(battles), ['明确敌我队伍', '固定首点半径', '相邻间隔/总跨度'],
         '规则得到可回看片段，未确认完整交战边界。', '候选片段'),
        ('production_completions', '单位出现与完成记录', 'A', bool(completed), ['STLS出现记录或同玩家完成时间线'],
         '提供出现/完成时刻，不等于建筑队列进度。', '记录'),
        ('queue_state', '实际生产队列', 'D', False, ['逐建筑实时队列/进度'], '研究/生产指令不能证明队列已执行。', None),
        ('resource_bank', '资源库存采样', 'A', bool(stock_points), ['同玩家原库存采样'], '保留原采样秒数与资源单位。', '资源数'),
        ('resource_income', '来源采集速率', 'A', bool(income_points), ['同玩家来源速率采样'], '统计窗口未独立验证，不当实际支出。', '来源每分钟资源'),
        ('scouting', '实际侦察', 'D', False, ['视野观测序列'], '指令目标不证明已侦察。', None),
        ('first_visible', '首次看见敌情', 'D', False, ['玩家逐时刻可见对象'], '当前没有真实首次可见记录。', None)]
    output = []
    for identity, title, grade, available, inputs, reason, unit in specifications:
        row = capability(identity, grade, available=ready and available, inputs=inputs,
                         reason=reason if ready else '同局、来源、身份或时钟核验未通过。', unit=unit)
        row['title'] = title
        row['gates'] = dict(quality.get('gates') or {})
        if identity == 'nominal_loss_cost':
            row['gates'].update(same_build_validated_cost=bool(priced), has_priced_coverage=bool(priced))
            row['coverage'] = {'priced': priced, 'eligible': len(events), 'complete': bool(events) and priced == len(events),
                               'same_build_required': True, 'build_number': (report.get('metadata') or {}).get('build_number')}
        if identity == 'battle_grouping':
            row['gates']['explicit_enemy_partition'] = bool(ledger['counts'].get('attribution_enemy'))
        if identity in ('queue_state', 'scouting', 'first_visible'):
            row['gates']['required_state_sequence'] = False
        output.append(row)
    return output


def _resources(report, linked, selected, settings):
    findings, baselines, invalid = [], [], []
    for player in linked.values():
        if selected is not None and player["id"] != selected:
            continue
        series = player["detail"].get("series") or {}
        for resource, label in RESOURCE_NAMES.items():
            bank, rate = _point_map(series.get("bank_" + resource)), _point_map(series.get("income_" + resource))
            if bank is None or rate is None:
                invalid.append({"player_id": player["id"], "resource": resource, "reason": "缺少有效库存/速率时间序列"})
                continue
            valid_count = sum(value is not None and rate.get(time) is not None and rate[time] > 0 for time, value in bank.items())
            if valid_count < 2:
                invalid.append({"player_id": player["id"], "resource": resource, "reason": "不足两个同期有效库存与正来源速率采样"})
                continue
            segments, segment = [], []
            for time in sorted(set(bank) | set(rate)):
                stock, income = bank.get(time), rate.get(time)
                value = stock / income if stock is not None and income is not None and income > 0 else None
                high = value is not None and value >= settings["reserve_minutes"]
                if (not high or (segment and time - segment[-1][0] > settings["max_sample_gap_seconds"])):
                    if segment:
                        segments.append(segment)
                    segment = []
                if high:
                    segment.append([time, value])
            if segment:
                segments.append(segment)
            segments = [points for points in segments if len(points) >= 2 and points[-1][0] - points[0][0] >= settings["reserve_span_seconds"]]
            total_span = sum(points[-1][0] - points[0][0] for points in segments)
            baselines.append(_definition_baseline(report, player, "resource_high_reserve_spans", total_span, "首末采样跨度秒",
                                                 resource=resource, segment_count=len(segments), reserve_minutes=settings["reserve_minutes"],
                                                 min_span_seconds=settings["reserve_span_seconds"], max_sample_gap_seconds=settings["max_sample_gap_seconds"]))
            for points in segments:
                start, end = points[0][0], points[-1][0]
                evidence = [_fact_evidence("players[].series.bank_" + resource + "+income_" + resource,
                                          time=time, player_id=player["id"], profile_id=player["profile_id"],
                                          resource=resource, bank=bank[time], rate=rate[time], reserve_minutes=value) for time, value in points]
                findings.append(_finding(f"reserve-{player['id']}-{resource}-{start:g}-{end:g}", "resource_high_reserve_spans",
                    f"核对{label}高储备采样段", start, end,
                    [f"{player.get('name') or player['id']}的{label}在连续{len(points)}个有效采样点，库存相当于至少{settings['reserve_minutes']:g}分钟来源采集量。",
                     f"首末采样跨度{end-start:g}秒，最大储备值{max(value for _, value in points):.2f}来源采集分钟。"],
                    "这是需要核对投入计划的库存线索，不能据此认定资源浪费。",
                    ["战略储备计划、可用生产建筑、人口和真实队列条件未知。", "来源采集速率的统计窗口未独立验证；采样之间可能短暂下降，也可能因低速率使比值放大。"],
                    {"action": f"回看这段{label}的预定用途；下局为大额储备注明计划，并在计划时刻检查资源是否已经投入。",
                     "metric_id": "resource_high_reserve_spans", "target": "按相同资源、观察分钟数和采样跨度标准复测；区分有计划储备与已核实的非计划积压。"},
                    evidence, [_metric("首末采样跨度", end-start, "秒"), _metric("观察储备标准", settings["reserve_minutes"], "来源采集分钟")],
                    [{"label": label + "库存/来源采集速率", "points": points, "unit": "来源采集分钟"}],
                    priority=(end-start) / settings["reserve_span_seconds"]))
    return findings, baselines, invalid


def build_replay_diagnosis(report=None, sources=None, selected_player_id=None, settings=None, context=None):
    """构建可追溯诊断，不做网络、文件、进程或界面操作。

    兼容原validated成本结构与official_unit_context的同build基础成本结构；变体、
    额外货币或字段缺失保留未定价。单位角色只用同录像unit_state的官方classes。
    不执行网络、文件、进程、游戏或界面操作。
    """
    report = report if isinstance(report, dict) else {}
    sources = sources if isinstance(sources, dict) else {}
    context = context if isinstance(context, dict) else {}
    values = _settings(settings)
    selected = _id(selected_player_id)
    if not context:
        context = sources.get('official_unit_context') if isinstance(sources.get('official_unit_context'), dict) else {}
    quality, linked = _quality(report, sources, selected)
    if selected_player_id is not None and selected is None:
        quality["gates"]["identity"] = False
        quality["status"] = "blocked"
        quality["gaps"].append("所选局内玩家编号无效。")
        for metric in quality["metrics"].values():
            metric["status"] = "blocked"
    result = {"schema_version": SCHEMA_VERSION, "definition_version": DEFINITION_VERSION,
              "game_id": (report.get("source") or {}).get("game_id"), "selected_player_id": selected,
              "quality": quality, "findings": [], "battles": [], "observations": [], "training_baselines": [],
              "metric_definitions": {key: dict(value, definition_version=DEFINITION_VERSION) for key, value in METRIC_DEFINITIONS.items()},
              "settings": values, "scope": "只生成观测事实与待回看的规则线索，不确认操作错误、因果或完整战斗边界。"}
    result['capability_inventory'] = _capability_inventory(report, sources, linked, quality)
    result['capabilities'] = result['capability_inventory']
    if quality["status"] == "blocked":
        return result
    battles, battle_findings, ledger = _battles(report, sources, linked, selected, values, context)
    production, production_baselines, production_counts = _production(report, linked, selected, values, context)
    resources, resource_baselines, resource_gaps = _resources(report, linked, selected, values)
    all_findings = battle_findings + production + resources
    # 总览先给可定位的损失片段；普通完成间隔/高库存留在观察区，不能挤成三个“错误”。
    result["findings"] = battle_findings[:values["max_findings"]]
    result["battles"] = battles
    result['observations'] = production + resources
    result['loss_ledger'] = ledger
    result['capability_inventory'] = _capability_inventory(report, sources, linked, quality, ledger, battles)
    result['capabilities'] = result['capability_inventory']
    result["training_baselines"] = production_baselines + resource_baselines
    quality["counts"].update(production_counts, battle_candidates=len(battles), rule_candidates=len(all_findings),
                             shown_findings=len(result["findings"]), resource_series_gaps=len(resource_gaps))
    quality["battle_exclusions"] = ledger['counts']
    quality['battle_coverage'] = {'raw_loss_records': ledger['counts'].get('raw_loss_records', 0),
        'canonical_unit_losses': len(ledger['events']), 'unidentified_target_records': len(ledger['unidentified_target_records']),
        'quarantined_lifecycles': len(ledger['quarantined_lifecycles']),
        'enemy_attributed_losses': ledger['counts'].get('attribution_enemy', 0),
        'unattributed_losses': ledger['counts'].get('attribution_unattributed', 0),
        'unknown_team_losses': ledger['counts'].get('attribution_unknown_team', 0),
        'cost_priced_losses': sum(e['reference_cost']['available'] for e in ledger['events'])}
    quality["resource_series_gaps"] = resource_gaps
    for role in ("worker", "military"):
        if not production_counts.get(role + "_eligible_players"):
            quality["metrics"][role + "_completion_gaps"]["status"] = "missing"
            quality["metrics"][role + "_completion_gaps"]["reason"] = "没有明确分类且至少两个有效完成时刻的玩家；未推断队列空闲。"
    if not resource_baselines:
        for metric in ("resource_reserve_minutes", "resource_high_reserve_spans"):
            quality["metrics"][metric]["status"] = "missing"
            quality["metrics"][metric]["reason"] = "没有两个同期有效库存与正来源速率采样。"
    if not any(loss["resource_loss"] is not None for battle in battles for loss in battle["losses"]):
        quality["metrics"]["reference_resource_loss"]["status"] = "missing"
        quality["metrics"]["reference_resource_loss"]["reason"] = "没有覆盖损失目标的同版本已验证参考单位成本。"
    return result
