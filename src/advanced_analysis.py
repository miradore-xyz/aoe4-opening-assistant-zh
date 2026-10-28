"""十八类复盘观测的只读证据模型。

ready（可计算）只表示相应测量的输入齐备；partial（部分证据）不等于诊断成立。
发令、实体出现、评分和玩家视野是不同事实，不把代理指标改名为实战结论。
本模块无网络、文件写入或界面依赖，只在导入与筛选变化时预计算。
"""
from __future__ import annotations

import math
from bisect import bisect_left
from collections import Counter, defaultdict

from replay_analysis import CATEGORY_LABELS, resolve_report_players


_SPEC_ROWS = (
    ("scouting", "侦察效率分析", "信息与操作", "核对情报获取与有效覆盖，区分玩家视野和观察者全图。", ["玩家视野时间序列", "侦察单位身份与连续位置", "敌方情报首次可见事件"]),
    ("threat_response", "威胁反应速度", "信息与操作", "分别定位信息已知、条件具备、开始执行与产生效果。", ["威胁首次被该玩家看见的时刻", "已验证的应对关联", "资源与生产条件状态"]),
    ("attention", "操作类型与节奏", "信息与操作", "观察指令类别的数量、比例和时间分布，不推断认知注意力。", ["带时间与玩家的指令"]),
    ("ineffective_actions", "重复指令与操作模式", "信息与操作", "比较已完整解码、原身份已核的重复发令候选，不判为无效操作。", ["完整解码的命令参数", "已核实的原实体引用"]),
    ("resource_opportunity", "库存与采集速率", "经济与执行", "按玩家与资源分别显示库存和来源采集速率，不将库存持续量称为损失。", ["库存时间序列", "来源采集速率时间序列"]),
    ("production_bottleneck", "生产完成记录", "经济与执行", "查看生产出现与完成节点；不由事件数量推断实际队列。", ["实体出现与建成事件"]),
    ("strategy_deviation", "参考计划节点对照", "经济与执行", "把实际节点和明确选定的计划按相同蓝图及发生次序对照。", ["明确选定的参考计划", "真实建成、科技或时代节点", "玩家与蓝图身份"]),
    ("technology_return", "科技完成时间轴", "经济与执行", "显示科技名称和真实完成时刻；没有使用结果时不计算实际回报。", ["科技完成事件"]),
    ("army_utilization", "单位出现与损失观测", "战斗与空间", "分别查看单位出现、损失、攻击发令和稀疏位置，不计算使用率。", ["完整单位身份与存活区间", "军事单位分类"]),
    ("combat_efficiency", "局部损失记录", "战斗与空间", "核对损失事件、来源记载攻击方和瞬时位置；候选交战由诊断规则另行分组。", ["损失时刻与位置"]),
    ("attack_defense_timing", "军事评分变化对照", "战斗与空间", "比较同来源军事评分，明确其不是即时兵力或当前战斗力。", ["同期军事评分"]),
    ("base_vulnerability", "实体位置事件", "战斗与空间", "查看已记录的出现与损失坐标，不将这些坐标解释为基地风险。", ["真实实体位置事件"]),
    ("tactical_adaptation", "单位构成变化", "决策与协同", "按明确账号查看单位出现构成，不推断玩家已知情报或应对意图。", ["单位出现时间线"]),
    ("defeat_trace", "经济军事变化回看", "决策与协同", "分开回看评分、采集速率和来源结果，不把同期变化解释为败因。", ["同局结果与时间序列"]),
    ("team_coordination", "团队攻击发令时序", "决策与协同", "比较同队攻击发令的原时间，实际支援需另外的接敌与位置证据。", ["明确同局队伍关系", "带玩家和时间的攻击指令"]),
    ("resource_contest", "目标实体引用观察", "决策与协同", "查看命令的原实体目标，不将未绑定资源的编号称为资源控制。", ["已核实的目标实体引用"]),
    ("error_patterns", "跨局观测对照", "长期训练", "同账号同定义的跨局观测矩阵，不自动生成错误标签或聚类结论。", ["同一账号的多局样本", "一致的指标定义"]),
    ("training_attribution", "训练前后观测对照", "长期训练", "比较同账号训练分组前后的观测值，不作训练因果归因。", ["训练记录与前后样本", "一致指标与同一账号"]),
)
ANALYSIS_SPECS = [dict(id=key, title=title, group=group, description=description, required=required)
                  for key, title, group, description, required in _SPEC_ROWS]
RESOURCE_LABELS = {"food": "食物", "wood": "木材", "gold": "黄金", "stone": "石料"}
_ATTACK = {"attack", "attack_move"}
_COMMAND_PARAMETER_FIELDS = ("type", "type_id", "unit_type_id", "research_type_id", "building_type_id", "target",
    "target_entity_id", "queued", "queued_candidate", "command_flags_raw", "queue_count_raw",
    "queue_player_id", "queue_trailing_flags_raw", "argument_frame_type_id", "argument_frame_raw",
    "target_frame_type_id", "target_frame_prefix_raw", "target_attribute_type_id", "target_frame_tail_raw",
    "target_extension_values_raw", "placement_points", "placement_flags_raw", "placement_unknown_value_raw")


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


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _clock(seconds):
    value = int(seconds)
    return f"{value // 60:02d}:{value % 60:02d}"


def _metric(label, value, unit=""):
    return {"label": label, "value": value, "unit": unit}


def _series(label, points, unit="次", **extra):
    return dict(label=label, points=points, unit=unit, **extra)


def _result(spec):
    return dict(spec, status="missing", summary="当前输入不足以计算该项诊断。", metrics=[], series=[],
                columns=[], rows=[], missing=list(spec["required"]), evidence=[], caveat="", chart_type="line")


def _set(result, summary, *, metrics=(), series=(), columns=(), rows=(), missing=(), evidence=(), caveat="", status=None):
    readable_evidence = []
    for item in list(evidence)[:80]:
        if isinstance(item, dict):
            item = dict(item)
            if not item.get("label"):
                source = str(item.get("source") or "")
                item["label"] = ("实体出现/损失原始记录" if source.startswith("STLS") else
                                 "同局公开摘要事件" if "detail" in source or "build_order" in source else
                                 "同局玩家库存采样" if "bank_" in source else "本地录像发令记录")
        readable_evidence.append(item)
    result.update(summary=summary, metrics=list(metrics), series=list(series), columns=list(columns),
                  rows=list(rows), missing=list(missing), evidence=readable_evidence, caveat=caveat)
    result["status"] = status or ("partial" if missing else "ready")


def _valid_points(series):
    """时间轴错误整体拒绝；缺值保留，不能排序后错配原值。"""
    points = series.get("points") if isinstance(series, dict) else None
    if not isinstance(points, list):
        return []
    output = []
    for row in points:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            return []
        time = _number(row[0], True)
        if time is None or (output and time <= output[-1][0]):
            return []
        output.append([time, _number(row[1])])
    return output


def integrate_excess(points, reserve=0):
    """旧归档兼容计算工具；当前产品入口不再生成库存积分指标。

    对相邻采样间线性库存做正部积分，单位资源×分。

    阈值交叉的区间只积三角形面积；缺点两侧均不补线，不外推到比赛终点。
    返回累计曲线、积分和实际覆盖秒数，不能换算为损失的军队或胜率。
    """
    threshold = _number(reserve, True)
    if threshold is None:
        raise ValueError("库存阈值必须是非负有限数值。")
    checked = _valid_points({"points": points})
    total, covered, cumulative = 0.0, 0.0, []
    previous = None
    for time, value in checked:
        if value is None or value < 0:
            cumulative.append([time, None])
            previous = None
            continue
        if previous is not None:
            last_time, last_value = previous
            delta = time - last_time
            a, b = last_value - threshold, value - threshold
            if a >= 0 and b >= 0:
                area = (a + b) * delta / 2
            elif a <= 0 and b <= 0:
                area = 0
            else:
                # 只有一端高于阈值：交点由线性插值确定。
                height = max(a, b)
                area = delta * height * height / (2 * abs(b - a))
            total += area / 60
            covered += delta
        cumulative.append([time, total])
        previous = time, value
    return {"points": cumulative, "integral": total, "covered_seconds": covered, "reserve": threshold}


def _bins(records, duration, predicate=None, seconds=30):
    counts, end, observed = Counter(), duration or 0, False
    for row in records:
        if predicate is not None and not predicate(row):
            continue
        time = _number(row.get("time"), True)
        if time is not None:
            counts[int(time // seconds)] += 1
            end = max(end, time)
            observed = True
    # 只用于已观察时轴，零表示该区间没有此类已记录事件。
    return [[i * seconds, counts[i]] for i in range(int(end // seconds) + 1)] if observed else []


def _evidence(row, source="report.timeline"):
    return {key: row.get(key) for key in ("time", "player_id", "profile_id", "offset", "label", "source", "pbgid")
            if row.get(key) is not None} | {"source": row.get("source") or source}


def _reference_signature(value):
    if not isinstance(value, dict):
        return None
    kind, original, packed = _id(value.get("kind")), _id(value.get("original_id")), _id(value.get("packed_raw"))
    if (kind not in (0, 1, 2, 3) or original in (None, 0) or packed is None or packed > 0xffffffff
            or value.get("encoding_build") != 11308):
        return None
    return kind, original, packed, 11308


def _freeze_parameters(value):
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze_parameters(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_parameters(item) for item in value)
    return value


def _command_signature(row):
    """比较已保留的完整参数及已验证原引用，拒绝只凭旧紧凑码判断同对象。"""
    if row.get("known") is not True or row.get("payload_decoded") is not True:
        return None
    if row.get("payload_bytes_remaining") not in (None, 0) or row.get("target_bytes_remaining") not in (None, 0):
        return None
    flags = _id(row.get("command_flags_raw"))
    if (flags is None or flags > 255 or not isinstance(row.get("queued_candidate"), bool)
            or row["queued_candidate"] != bool(flags & 128)):
        return None
    if row.get("selected_refs_verified") is not True:
        return None
    references = row.get("selected_refs") or []
    units = [_reference_signature(value) for value in references] if isinstance(references, list) else []
    producer = _reference_signature(row.get("producer_ref"))
    if (not units and producer is None) or any(value is None for value in units):
        return None
    if row.get("producer_ref") is not None and producer is None:
        return None
    return tuple(sorted(units)), producer, tuple((field, _freeze_parameters(value))
        for field in _COMMAND_PARAMETER_FIELDS if (value := row.get(field)) is not None)


def _detail_context(report, detail, sources, players, selected):
    """只有可核对同场编号才融合摘要；不能用名字或队伍顺序猜账号。"""
    game_id = _id((report.get("source") or {}).get("game_id"))
    if game_id is None:
        game_id = _id(report.get("game_id"))
    source_ok = bool(game_id is not None and _id(sources.get("game_id")) == game_id)
    candidate = sources.get("detail") if source_ok and isinstance(sources.get("detail"), dict) else detail
    candidate = candidate if isinstance(candidate, dict) else {}
    reason = ""
    if candidate and (game_id is None or _id(candidate.get("game_id")) != game_id):
        candidate, reason = {}, "摘要和录像缺少相同的明确对局编号，未混合两来源。"
    # 官方统计回退只带采样而没有建造数组；同场、完整账号集合一致时保留已有事件。
    if (candidate and isinstance(detail, dict) and _id(detail.get("game_id")) == game_id
            and candidate is not detail):
        incoming = {_id(p.get("profile_id")): p for p in _rows(detail.get("players"))}
        existing = _rows(candidate.get("players"))
        if set(incoming) == {_id(p.get("profile_id")) for p in existing} and None not in incoming:
            candidate = dict(candidate)
            merged = []
            for player in existing:
                player = dict(player)
                previous = incoming[_id(player.get("profile_id"))]
                if not player.get("events") and previous.get("events"):
                    player["events"] = previous["events"]
                if not player.get("result") and previous.get("result"):
                    player["result"] = previous["result"]
                merged.append(player)
            candidate["players"] = merged
    links = _rows(sources.get("identity_links")) if source_ok else []
    # 确认单向、反向都唯一；有冲突则该局内身份整项拒绝。
    link_ids, reverse = defaultdict(set), defaultdict(set)
    for row in links:
        pid, profile = _id(row.get("player_id")), _id(row.get("profile_id"))
        if row.get("verified") is True and pid is not None and profile is not None:
            link_ids[pid].add(profile)
            reverse[profile].add(pid)
    for player in players:
        values = link_ids[player["id"]]
        if len(values) == 1:
            profile = next(iter(values))
            existing = _id(player.get("profile_id"))
            if len(reverse[profile]) == 1 and existing in (None, profile):
                player["profile_id"] = profile
    detail_players = _rows(candidate.get("players"))
    if selected is not None:
        player = next((p for p in players if p["id"] == selected), {})
        profile = _id(player.get("profile_id"))
        matches = [p for p in detail_players if profile is not None and _id(p.get("profile_id")) == profile]
        local_matches = [p for p in players if profile is not None and _id(p.get("profile_id")) == profile]
        if len(matches) != 1 or len(local_matches) != 1:
            detail_players = []
            if candidate:
                reason = "所选局内玩家尚无唯一且明确的摘要账号绑定，未按昵称或席位猜配。"
        else:
            detail_players = matches
    return candidate, detail_players, source_ok, reason


def _event_rows(players):
    output = []
    for player in players:
        profile = _id(player.get("profile_id"))
        if profile is None:
            continue
        for event in _rows(player.get("events")):
            if (_number(event.get("time"), True) is not None
                    and _id(event.get("profile_id")) in (None, profile)):
                output.append(dict(event, profile_id=profile, player_name=player.get("name") or f"账号 {profile}"))
    return sorted(output, key=lambda row: row["time"])


def _unit_observations(sources, players, selected):
    """只接受同局已验证账号桥下的STLS单位观测，不使用命令紧凑单位号。"""
    by_player, by_profile = defaultdict(set), defaultdict(set)
    for link in _rows(sources.get("identity_links")):
        pid, profile = _id(link.get("player_id")), _id(link.get("profile_id"))
        if link.get("verified") is True and pid is not None and profile is not None:
            by_player[pid].add(profile)
            by_profile[profile].add(pid)
    known = {p["id"]: _id(p.get("profile_id")) for p in players}
    groups = _rows(sources.get("entity_observations"))
    pairs = Counter((_id(row.get("player_id")), _id(row.get("entity_id"))) for row in groups)
    source_kinds = {"birth": "STLS.createdEntities", "loss": "STLS.lostEntities.target",
                    "attack": "STLS.lostEntities.attacker"}
    output = []
    for group in groups:
        pid, profile, entity = _id(group.get("player_id")), _id(group.get("profile_id")), _id(group.get("entity_id"))
        if (pid not in known or (selected is not None and pid != selected) or entity in (None, 0)
                or group.get("identity_ambiguous") is not False or pairs[(pid, entity)] != 1
                or by_player[pid] != {profile} or by_profile[profile] != {pid} or known[pid] != profile):
            continue
        observations, positions = [], defaultdict(set)
        for row in _rows(group.get("observations")):
            position = row.get("position") if isinstance(row.get("position"), dict) else {}
            time, x, y = _number(row.get("time"), True), _number(position.get("x")), _number(position.get("y"))
            if (time is None or x is None or y is None or row.get("kind") not in source_kinds
                    or row.get("source") != source_kinds[row["kind"]]):
                continue
            observations.append(dict(row, position={"x": x, "y": y}))
            positions[time].add((x, y))
        if (not observations or sum(row["kind"] == "birth" for row in observations) > 1
                or any(len(values) > 1 for values in positions.values())):
            continue
        observations.sort(key=lambda row: (row["time"], row["kind"], row.get("offset") or 0))
        distinct = set().union(*positions.values())
        output.append(dict(group, player_id=pid, profile_id=profile, entity_id=entity, observations=observations,
                           position_changed_observed=len(positions) > 1 and len(distinct) > 1,
                           world_meter_scale=None, continuous_path=False))
    return output


def _reference_key(row):
    """明确时代节点可按时代号对照；普通建造/科技必须有真实蓝图编号。"""
    if row.get("kind") == "age" and row.get("new_age") in (2, 3, 4):
        return "age", row["new_age"]
    blueprint = _id(row.get("pbgid"))
    return (row.get("kind"), blueprint) if blueprint is not None else None


def _score_gap_series(detail, selected_detail, key):
    """同一时间戳的队伍平均评分差；缺值断开，不把不同采样时刻硬接。"""
    all_players = _rows(detail.get("players"))
    output = []
    selected_ids = {_id(p.get("profile_id")) for p in selected_detail}
    teams = {p.get("team") for p in all_players if p.get("team") is not None}
    for team in sorted(teams, key=str):
        own = [p for p in all_players if p.get("team") == team]
        enemies = [p for p in all_players if p.get("team") is not None and p.get("team") != team]
        if not enemies or not any(_id(p.get("profile_id")) in selected_ids for p in own):
            continue
        maps = [dict(_valid_points((p.get("series") or {}).get(key))) for p in own + enemies]
        if not maps or any(not values for values in maps):
            continue
        timestamps = sorted(set().union(*(set(values) for values in maps)))
        points = []
        for time in timestamps:
            values = [mapping.get(time) for mapping in maps]
            gap = None if any(value is None for value in values) else sum(values[:len(own)]) / len(own) - sum(values[len(own):]) / len(enemies)
            points.append([time, gap])
        others = sorted({p.get("team") for p in enemies}, key=str)
        label = "来源军事评分差（非现有兵力）" if key == "score_military" else "来源采集速率差"
        direction = f"队伍{team}均值减队伍{others[0]}均值" if len(others) == 1 else f"队伍{team}均值减其他队玩家均值"
        output.append(_series(f"{label} · {direction}", points,
                              "分" if key == "score_military" else "资源/分", team=team,
                              comparison={"left_team": team, "right_teams": others, "aggregation": "每玩家均值"},
                              measure="military_score_gap" if key == "score_military" else "income_rate_gap",
                              chart_group="score" if key == "score_military" else "income_rate"))
        # 两队的反向差值完全镜像：全体范围只保留按队号稳定选择的一条。
        if len(teams) == 2:
            break
    return output


def _apply_history(by_id, history, allowed_profiles=None):
    """跨局只接受明确记录的同账号、同定义观测，不从一局生成错误标签。"""
    games = _rows(history.get("games")) if isinstance(history, dict) else []
    valid = [row for row in games if _id(row.get("profile_id")) is not None and _id(row.get("game_id")) is not None
             and isinstance(row.get("features"), dict) and row.get("metric_definition")
             and (allowed_profiles is None or _id(row.get("profile_id")) in allowed_profiles)]
    # 同账号、同局、同定义的冲突观测整组拒绝；相同重复记录只计一次。
    by_game = defaultdict(list)
    for row in valid:
        by_game[(_id(row["profile_id"]), _id(row["game_id"]), str(row["metric_definition"]))].append(row)
    valid = []
    for duplicates in by_game.values():
        first = duplicates[0]
        if all(row["features"] == first["features"] for row in duplicates[1:]):
            valid.append(first)
    groups = defaultdict(list)
    for row in valid:
        groups[(_id(row["profile_id"]), str(row["metric_definition"]))].append(row)
    groups = [rows for rows in groups.values() if len({_id(row["game_id"]) for row in rows}) >= 2]
    result = by_id["error_patterns"]
    if groups:
        rows, feature_count = [], set()
        for samples in groups:
            seen = set()
            for row in samples:
                identity = _id(row["game_id"])
                if identity in seen:
                    continue
                seen.add(identity)
                for feature, value in row["features"].items():
                    if _number(value) is not None:
                        rows.append([row["profile_id"], identity, str(feature), value, str(row["metric_definition"])])
                        feature_count.add(str(feature))
        _set(result, "已形成同账号、同定义的跨局观测矩阵；尚未验证错误标签或聚类质量。",
             metrics=[_metric("同定义样本组", len(groups), "组"), _metric("观测维度", len(feature_count), "项")],
             columns=["账号", "对局", "观测指标", "观测值", "指标定义"], rows=rows,
             missing=["可靠的错误标签与适用条件", "样本数量和聚类稳定性验证"],
             caveat="观测相似性不是错误归属；不能从重复低分直接诊断固定弱点。")
    training = _rows(history.get("training")) if isinstance(history, dict) else []
    if training:
        rows = []
        for experiment in training:
            profile = _id(experiment.get("profile_id"))
            before = {_id(value) for value in experiment.get("before_game_ids") or [] if _id(value) is not None}
            after = {_id(value) for value in experiment.get("after_game_ids") or [] if _id(value) is not None}
            feature, definition = experiment.get("feature"), experiment.get("metric_definition")
            if (profile is None or not feature or not definition or before & after
                    or (allowed_profiles is not None and profile not in allowed_profiles)):
                continue
            # 同一对局重复记录或同局两个冲突数值都不进入前后均值。
            samples = defaultdict(list)
            for game in valid:
                if _id(game["profile_id"]) == profile and game["metric_definition"] == definition:
                    value = _number(game["features"].get(feature))
                    if value is not None:
                        samples[_id(game["game_id"])].append(value)
            pre = [values[0] for identity, values in samples.items() if identity in before and len(values) == 1]
            post = [values[0] for identity, values in samples.items() if identity in after and len(values) == 1]
            if pre and post:
                a, b = sum(pre) / len(pre), sum(post) / len(post)
                rows.append([profile, str(feature), len(pre), len(post), a, b, b - a])
        if rows:
            _set(by_id["training_attribution"], "已比较明确训练分组的前后观测均值；变化尚不能归因为训练。",
                 columns=["账号", "训练指标", "训练前局数", "训练后局数", "前均值", "后均值", "观测变化"], rows=rows,
                 missing=["对手强度、地图、版本及训练外因素控制", "足量样本及不确定性估计"],
                 caveat="此处只报告样本均值差，没有生成因果效果或置信区间。")


def build_advanced_analysis(report=None, spatial=None, detail=None, reference=None, history=None,
                            selected_player_id=None, reserve=0.0, repeat_window=1.0, sources=None):
    """生成全部十八项结果。返回普通字典；播放器更新光标时无需重新调用。

    selected_player_id（所选局内编号）为空表示全体。sources（补充来源）必须带相同
    game_id（对局编号），identity_links（身份关联）必须明确 verified=True（已验证）。
    reserve仅保留旧调用兼容，当前不再生成库存积分；库存与采集速率保持原采样值。
    reference.events（参考事件）必须由调用者明确提供。
    """
    report = report if isinstance(report, dict) else {}
    if isinstance(report.get("report"), dict):
        report = report["report"]
    spatial = spatial if isinstance(spatial, dict) else {}
    sources = sources if isinstance(sources, dict) else {}
    selected = _id(selected_player_id)
    players = [dict(player) for player in resolve_report_players(report)]
    if not players:
        players = [dict(player) for player in _rows(spatial.get("players"))]
    selected_valid = selected_player_id is None or (selected is not None and any(player.get("id") == selected for player in players))
    candidate, detail_players, source_ok, identity_reason = _detail_context(report, detail, sources, players, selected)
    if not selected_valid:
        detail_players = []
        identity_reason = "所选局内玩家编号不在当前录像中。"
    raw_records = report.get("timeline") if isinstance(report.get("timeline"), list) else report.get("commands")
    records = [row for row in _rows(raw_records) if (selected is None or _id(row.get("player_id")) == selected)
               and _number(row.get("time"), True) is not None and row.get("category") != "system"
               and row.get("is_system") is not True and row.get("type") not in {"control", "periodic", "sync", "heartbeat", "precommand", "postcommand"}
               and _id(row.get("player_id")) not in (None, 0)] if selected_valid else []
    records = sorted(records, key=lambda row: (row["time"], row.get("offset") or 0))
    # 各分析共享分类索引和命令签名，避免每个指标重复解读数万条命令。
    category_records, signatures = defaultdict(list), {}
    for row in records:
        category_records[row.get("category", "unknown")].append(row)
        signatures[id(row)] = _command_signature(row)
    all_records = [row for row in _rows(raw_records) if _number(row.get("time"), True) is not None
                   and row.get("type") in _ATTACK and row.get("known") is True]
    metadata = report.get("metadata") or {}
    duration = _number(metadata.get("duration"), True)
    if duration is None:
        duration = _number(spatial.get("duration"), True)
    if duration is None:
        duration = max([0] + [row["time"] for row in records])
    results = [_result(spec) for spec in ANALYSIS_SPECS]
    by_id = {row["id"]: row for row in results}
    events = _event_rows(detail_players)
    missing_identity = [identity_reason] if identity_reason else []
    stls = sources.get("stls") if source_ok and isinstance(sources.get("stls"), dict) else {}
    created = [row for row in _rows(stls.get("created")) if selected is None or _id(row.get("player_id")) == selected]
    lost = [row for row in _rows(stls.get("lost")) if selected is None or _id(row.get("target_player_id")) == selected]

    # 1、2：缺少真实视野和首次可见事件时保持缺项，不用移动或避难发令替代。
    # 原始指令仍完整保存在report（录像报告）中；这里只停止生成不回答该问题的代理图表。
    by_id["scouting"]["summary"] = "缺少玩家视野记录，暂时无法计算侦察效率。"
    by_id["scouting"]["caveat"] = "移动目标和巡逻发令不能证明玩家探索了哪些区域，或获得了哪些敌方情报。"
    by_id["threat_response"]["summary"] = "缺少威胁首次可见记录，暂时无法计算反应时间。"
    by_id["threat_response"]["caveat"] = "需要该玩家首次看见具体威胁的时刻及可靠的应对关联；攻击或避难发令数量不能替代。"

    # 3：指令类别热矩阵是可观察输入分配，不能等同于认知注意力。
    if records:
        series = [_series(label, _bins(category_records[key], duration), "条")
                  for key, label in CATEGORY_LABELS.items()]
        totals = Counter(row.get("category", "unknown") for row in records)
        rows = [[_clock(time), *[dict(item["points"]).get(time, 0) for item in series]]
                for time, _ in _bins(records, duration)]
        _set(by_id["attention"], "已按30秒区间统计指令类别分布；这衡量已记录的输入类别，不是实际注意力。",
             metrics=[_metric("带时间的玩家指令", len(records), "条"), _metric("未识别指令", totals["unknown"], "条")],
             series=series, columns=["区间起点"] + list(CATEGORY_LABELS.values()), rows=rows,
             missing=["选择与控制对象变化", "经济生产与交战的完整状态"], evidence=[_evidence(row) for row in records[:8]],
             caveat="发令空档不能判为闲置；移动、生产与攻击类别不能直接解释玩家正在关注的画面。")
        by_id["attention"]["chart_type"] = "heat"

    # 4：同一玩家的相邻、已核原对象及已保留参数相同的输入才成为候选。
    previous, repeats = {}, []
    window = _number(repeat_window, True)
    if window is None:
        raise ValueError("重复候选窗口必须是非负有限秒数。")
    for row in records:
        pid = _id(row.get("player_id"))
        last = previous.get(pid)
        signature = signatures[id(row)]
        if signature is not None and last and signature == signatures[id(last)] and 0 <= row["time"] - last["time"] <= window:
            repeats.append(dict(row, previous_time=last["time"], previous_offset=last.get("offset"), delta=row["time"] - last["time"]))
        previous[pid] = row
    if records:
        _set(by_id["ineffective_actions"], f"在{window:g}秒窗口找到{len(repeats)}条相邻相同发令候选；尚不能判断无效。",
             metrics=[_metric("相邻重复候选", len(repeats), "条"), _metric("取消指令", sum(row.get("category") == "cancel" for row in records), "条"),
                      _metric("完整参数可比较指令", sum(value is not None for value in signatures.values()), "条")],
             series=[_series("相邻重复发令候选", _bins(repeats, duration), "条")],
             columns=["时刻", "局内玩家", "指令", "距上一条秒数", "原偏移", "上一原偏移"],
             rows=[[_clock(row["time"]), row["player_id"], row.get("label") or "已解码指令", row["delta"], row.get("offset"), row["previous_offset"]] for row in repeats],
             missing=["执行结果或状态变化", "完整选择、快捷键与鼠标输入记录"], evidence=[_evidence(row) for row in repeats[:80]],
             caveat="只比较已完整解码、原引用已核的已归档参数；旧紧凑码不参与身份比较。"
                    "未归档的原始头标记不能推定相同。重复可用于追击、路径修正或确认操作；候选数不是无效操作率。")
        by_id["ineffective_actions"]["chart_type"] = "bar"

    # 5：库存为状态量、采集为速率；逐玩家/资源保留原采样，两种单位不得共用数值轴。
    inventory_rows, inventory_series, inventory_evidence = [], [], []
    chart_groups = {}
    profile_ids = defaultdict(list)
    for player in players:
        profile_ids[_id(player.get("profile_id"))].append(player["id"])
    profile_players = {profile: ids[0] for profile, ids in profile_ids.items() if profile is not None and len(ids) == 1}
    for player in detail_players:
        profile = _id(player.get("profile_id"))
        display = f"{player.get('name') or '玩家'}（账号{profile}）"
        for key, label in RESOURCE_LABELS.items():
            samples = {}
            for prefix, measure, title, unit in (("bank_", "stock", "库存", "资源"),
                                                  ("income_", "income_rate", "来源采集速率", "资源/分")):
                points = [[time, _number(value, True)] for time, value in
                          _valid_points((player.get("series") or {}).get(prefix + key))]
                samples[measure] = dict(points)
                if not points:
                    continue
                group = f"{key}:{measure}"
                series_id = f"{profile}:{group}"
                inventory_series.append(_series(f"{display} · {label}{title}", points, unit,
                                                 id=series_id, resource=key, measure=measure, profile_id=profile,
                                                 player_id=profile_players.get(profile), chart_group=group,
                                                 source="players[].series." + prefix + key))
                chart_groups.setdefault(group, {"id": group, "title": label + title, "unit": unit, "series_ids": []})["series_ids"].append(series_id)
                inventory_evidence.append({"source": "players[].series." + prefix + key,
                                           "label": f"同局{label}{title}采样", "game_id": candidate.get("game_id"),
                                           "profile_id": profile, "samples": len(points)})
            for time in sorted(set(samples["stock"]) | set(samples["income_rate"])):
                inventory_rows.append([time, display, profile, label, samples["stock"].get(time),
                                       samples["income_rate"].get(time)])
    if inventory_series:
        _set(by_id["resource_opportunity"], "按账号与四种资源分别保留真实库存及来源采集速率；两种单位分开对照。",
             metrics=[_metric("原始采样序列", len(inventory_series), "条")], series=inventory_series,
             columns=["原始秒数", "玩家", "账号", "资源", "库存（资源）", "来源采集速率（资源/分）"], rows=inventory_rows,
             missing=["与战略相符的储备计划及可确认的生产需求", "完整支出、交易、返还及转化明细"],
             evidence=inventory_evidence,
             caveat="只在原采样时刻有观测值；缺值不补零，不插值计算支出或收益。"
                    "库存和采集速率使用不同数值轴，线段仅供观察采样趋势。来源速率的原生统计窗口仍未独立验证。")
        by_id["resource_opportunity"]["chart_groups"] = list(chart_groups.values())
    else:
        by_id["resource_opportunity"]["missing"] += missing_identity

    # 6、7、8：保留完成/出现与开始发令的语义边界。
    key_events = [row for row in events if row.get("kind") in {"building", "upgrade", "age", "production"}]
    if key_events:
        _set(by_id["production_bottleneck"], "已并列真实建成、科技、时代和单位出现节点；尚无逐建筑队列可定位生产瓶颈。",
             metrics=[_metric("已记录节点", len(key_events), "组"), _metric("单位出现数量", sum(row.get("quantity", 1) for row in key_events if row["kind"] == "production"), "个")],
             series=[_series("单位完成/出现事件组", _bins(key_events, duration, lambda row: row["kind"] == "production"), "组")],
             columns=["时刻", "玩家", "已记录节点", "数量", "来源"],
             rows=[[_clock(row["time"]), row["player_name"], row.get("label"), row.get("quantity", 1), row.get("source")] for row in key_events],
             missing=["逐建筑生产队列、开始、取消及闲置区间", "资源及依赖条件的完整状态", "经过验证的传播机制"],
             evidence=[_evidence(row, "detail.events") for row in key_events[:80]],
             caveat="相邻出现事件的间隔可能来自多建筑、转化、调兵或计划调整，不能直接叫断村或生产闲置。")
        by_id["production_bottleneck"]["event_records"] = key_events
    strategic = [row for row in key_events if row["kind"] in {"building", "upgrade", "age"}]
    if strategic:
        actual_rows = [[_clock(row["time"]), row["player_name"], row.get("label"), row.get("pbgid"), "待选参考"] for row in strategic]
        _set(by_id["strategy_deviation"], "已整理实际建成、科技和时代节点；需明确选定计划才能测量执行偏差。",
             columns=["实际时刻", "玩家", "节点", "蓝图编号", "参考差值"], rows=actual_rows,
             missing=["明确选定且可比的参考计划"], evidence=[_evidence(row, "detail.events") for row in strategic[:80]],
             caveat="未将对手或系统默认开局自动当成玩家意图。")
        if isinstance(reference, dict) and _rows(reference.get("events")) and len(detail_players) == 1:
            expected = [row for row in _rows(reference.get("events")) if _number(row.get("time"), True) is not None]
            expected.sort(key=lambda row: row["time"])
            queues = defaultdict(list)
            for row in strategic:
                key = _reference_key(row)
                if key is not None:
                    queues[key].append(row)
            rows, delays = [], []
            for planned in expected:
                key = _reference_key(planned)
                same = queues[key] if key is not None else []
                actual = same.pop(0) if same else None
                delay = actual["time"] - planned["time"] if actual else None
                if delay is not None:
                    delays.append(delay)
                rows.append([planned.get("label") or "计划节点", _clock(planned["time"]), _clock(actual["time"]) if actual else "未匹配", delay])
            _set(by_id["strategy_deviation"], "已按节点种类、蓝图、时代及发生次序逐项对照所给计划。",
                 metrics=[_metric("已匹配计划节点", len(delays), "项")],
                 columns=["计划节点", "计划时刻", "实际时刻", "实际减计划（秒）"], rows=rows,
                 missing=[] if len(delays) == len(expected) else ["部分参考节点未匹配到实际事件"],
                 caveat="偏差只表示对所给计划的时间差，不评价计划是否适合当时战局。")
    upgrades = [row for row in events if row.get("kind") == "upgrade"]
    if upgrades:
        completions = defaultdict(Counter)
        upgrade_names = {}
        for row in upgrades:
            profile = _id(row.get("profile_id"))
            completions[profile][row["time"]] += row.get("quantity", 1)
            upgrade_names[profile] = row["player_name"]
        _set(by_id["technology_return"], "已定位科技完成记录；没有用后续评分变化替代科技收益。",
             metrics=[_metric("科技完成事件", len(upgrades), "组")],
             columns=["完成时刻", "玩家", "科技", "蓝图编号", "数量"],
             rows=[[_clock(row["time"]), row["player_name"], row.get("entity_name"), row.get("pbgid"), row.get("quantity", 1)] for row in upgrades],
             series=[_series(f"{upgrade_names[profile]}（账号{profile}）· 科技完成时刻", sorted(points.items()), "项",
                              profile_id=profile, measure="completion_count", chart_group="technology_completions",
                              time_semantics="完成事件原秒数，不分桶") for profile, points in completions.items()],
             missing=["同版本科技成本、受益单位及效果", "实际受益行为和可比反事实"], evidence=[_evidence(row, "detail.events") for row in upgrades[:80]],
             caveat="完成时刻不是开始投资时刻；相关的评分变化不能证明该项科技回本。")
        by_id["technology_return"]["event_records"] = upgrades
        by_id["technology_return"]["chart_type"] = "bar"

    # 9：非村民单位仍可能包含经济或辅助单位，不能统称军队。
    unit_events = [row for row in events if row.get("entity_type") == "Unit" and row.get("is_villager") is False]
    if unit_events:
        observed = [row for row in unit_events if row.get("kind") in {"production", "loss"}]
        _set(by_id["army_utilization"], "已并列非村民单位的出现、损失与攻击发令数量；无法还原持续作战或闲置时间。",
             metrics=[_metric("非村民单位出现", sum(row.get("quantity", 1) for row in observed if row["kind"] == "production"), "个"),
                      _metric("非村民单位损失", sum(row.get("quantity", 1) for row in observed if row["kind"] == "loss"), "个")],
             series=[_series("非村民单位出现事件组", _bins(observed, duration, lambda row: row["kind"] == "production"), "组"),
                     _series("非村民单位损失事件组", _bins(observed, duration, lambda row: row["kind"] == "loss"), "组"),
                     _series("攻击发令", _bins(records, duration, lambda row: row.get("type") in _ATTACK), "条")],
             columns=["时刻", "玩家", "单位事件", "数量"],
             rows=[[_clock(row["time"]), row["player_name"], row.get("label"), row.get("quantity", 1)] for row in observed],
             missing=["逐实例军事分类和完整存活状态", "连续位置、任务与接敌区间"],
             caveat="非村民单位不全部是军队；出现次数与发令次数均不能作为兵力使用率的分子或分母。")
        by_id["army_utilization"]["event_records"] = observed
    army_result = by_id["army_utilization"]
    unit_observations = _unit_observations(sources, players, selected) if source_ok and selected_valid else []
    army_result["unit_observations"] = unit_observations
    if unit_observations:
        if army_result["status"] == "missing":
            _set(army_result, "已取得同一单位在不同事件时刻的位置观测；尚不足以计算兵力使用率。",
                 missing=["逐实例军事分类和完整存活状态", "连续位置、任务与接敌区间"])
        else:
            army_result["summary"] += " 已加入同一单位的出现、来源记载攻击及损失位置观测。"
        army_result["metrics"] += [
            _metric("有位置观测的单位", len(unit_observations), "个"),
            _metric("原始位置观测点", sum(len(row["observations"]) for row in unit_observations), "个"),
            _metric("观测到位置变化的单位", sum(row["position_changed_observed"] for row in unit_observations), "个")]
        army_result["caveat"] = (army_result["caveat"] + " 观测包含经济与辅助单位；同一STLS玩家/实体编号的两个时刻"
                                 "坐标不同，只证明这两个时刻位置变化。点间没有实际路线、速度或动画证据；不插值，原坐标未校准为米。"
                                 "实际原UID命令绑定属于独立状态索引；这里不将任何发令目标当成实际位置。").strip()

    # 10：原始STLS带攻击方时保留归属；归属事件也不等于局部资源交换。
    losses = [row for row in events if row.get("kind") == "loss"]
    if lost:
        known_player_ids = {p["id"] for p in players}
        attributed_ids = {id(row) for row in lost if row.get("has_attacker") == 1
                          and _id(row.get("attacker_player_id")) in known_player_ids}
        _set(by_id["combat_efficiency"], "已保留损失事件的原始攻击方字段与瞬时位置；尚未分组为完整局部交战或资源交换。",
             metrics=[_metric("原始损失事件", len(lost), "条"), _metric("来源记载攻击方玩家", len(attributed_ids), "条")],
             series=[_series("损失事件", _bins(lost, duration), "条")],
             columns=["时刻", "损失方局内编号", "目标实体", "攻击方局内编号", "攻击实体", "损失位置原坐标"],
             rows=[[_clock(row["time"]), row.get("target_player_id"), row.get("target_entity_id"),
                    row.get("attacker_player_id") if id(row) in attributed_ids else "未明确", row.get("attacker_entity_id"),
                    f"{row.get('target_x')} / {row.get('target_y')}" if _number(row.get("target_x")) is not None and _number(row.get("target_y")) is not None else "未提供"]
                   for row in lost if _number(row.get("time"), True) is not None],
             missing=["同版本单位价值及变形/转化/中立损失排除", "完整交战分组和交战边界"],
             evidence=[dict(_evidence(row, "STLS.lost"), target_entity_id=row.get("target_entity_id"), attacker_player_id=row.get("attacker_player_id")) for row in lost[:80]],
             caveat="攻击方是原字段报告的归属，不能据此认定全部损失属于同一交战；瞬时损失位置不是连续轨迹。")
    elif losses:
        _set(by_id["combat_efficiency"], "已定位同期损失记录；缺少明确击杀归属和交战边界，不能计算局部交换比。",
             metrics=[_metric("已记录损失数量", sum(row.get("quantity", 1) for row in losses), "个")],
             series=[_series("同期损失事件组", _bins(losses, duration), "组")],
             columns=["时刻", "损失方", "实体", "数量"],
             rows=[[_clock(row["time"]), row["player_name"], row.get("entity_name"), row.get("quantity", 1)] for row in losses],
             missing=["明确的攻击方玩家与交战位置", "同版本资源价值及完整交战分组"],
             caveat="时间接近不能证明相互交战；未知击杀方不补成对手。")

    # 11、14：评分和采集速率是同来源观测，不称作战力或败因。
    military_gaps = _score_gap_series(candidate, detail_players, "score_military")
    if military_gaps:
        _set(by_id["attack_defense_timing"], "已按同一采样时刻计算队均军事评分差；尚不能判断应当进攻还是防守。",
             series=military_gaps, columns=["队伍曲线", "采样时刻", "队均军事评分差"],
             rows=[[series["label"], _clock(time), value] for series in military_gaps for time, value in series["points"]],
             missing=["即时兵力、位置、血量、克制及地形", "玩家当时已知信息和交战事件"],
             caveat="来源为players[].resources.military的统计评分，具体累计公式尚未独立验证；"
                    "不等于现存军队价值或实际战力。这里计算每玩家队均差，不是整队总值；两队全体只留一个方向。")
    income_gaps = _score_gap_series(candidate, detail_players, "income_total")
    if military_gaps or income_gaps:
        rows = []
        for player in detail_players:
            rows.append([player.get("name"), player.get("profile_id"), player.get("result") or "未提供", player.get("team")])
        _set(by_id["defeat_trace"], "已按时间追踪队均评分和来源采集速率差，并列来源记录的对局结果。",
             series=military_gaps + income_gaps, columns=["玩家", "账号", "来源结果", "来源队伍"], rows=rows,
             missing=["经过验证的失误机制及反事实", "完整兵力和经济状态"],
             caveat="差距形成区间只是观察线索，不能称为败因、转折点或某项操作造成的连锁后果。")

    # 12：只展现真实位置事件，不把建筑放置指令当成已建成基地。
    positioned = [dict(row, position_kind="出现") for row in created if _number(row.get("x")) is not None and _number(row.get("y")) is not None]
    positioned += [dict(row, player_id=row.get("target_player_id"), x=row.get("target_x"), y=row.get("target_y"),
                        entity_id=row.get("target_entity_id"), entity_type=row.get("target_type"), position_kind="损失")
                   for row in lost if _number(row.get("target_x")) is not None and _number(row.get("target_y")) is not None]
    if positioned:
        _set(by_id["base_vulnerability"], "已列出真实出现/损失的瞬时原坐标；尚不足以计算基地风险覆盖。",
             metrics=[_metric("有坐标的状态事件", len(positioned), "条")],
             columns=["时刻", "局内玩家", "事件", "实体", "类型原标识", "原横坐标", "原纵坐标"],
             rows=[[_clock(row["time"]), row.get("player_id"), row["position_kind"], row.get("entity_id"), row.get("entity_type"), row["x"], row["y"]]
                   for row in positioned if _number(row.get("time"), True) is not None],
             missing=["经济价值、防御覆盖与寻路地形", "敌方连续位置及玩家视野"],
             caveat="出现/损失位置不是持续位置；本模型未将STLS坐标与指令目标坐标拼成轨迹，也未冒称米制风险半径。")

    # 13：仅比较两分钟内的实际单位出现构成，不推断对手情报或决策原因。
    productions = [row for row in events if row.get("kind") == "production" and row.get("entity_type") == "Unit"]
    if productions:
        distribution = Counter()
        for row in productions:
            quantity = _number(row.get("quantity", 1), True)
            if quantity is not None and quantity > 0 and int(quantity) == quantity:
                distribution[(int(row["time"] // 120) * 120, _id(row.get("profile_id")),
                              _id(row.get("pbgid")), row.get("entity_name") or "未识别单位")] += int(quantity)
        profile_names = {_id(player.get("profile_id")): player.get("name") for player in detail_players}
        _set(by_id["tactical_adaptation"], "已整理每两分钟的单位出现构成；尚不能判断玩家是否根据已知敌情调整。",
             columns=["阶段起点", "玩家", "账号", "蓝图编号", "单位", "出现数量"],
             rows=[[_clock(time), profile_names.get(profile) or "玩家", profile, blueprint, unit, count]
                   for (time, profile, blueprint, unit), count in sorted(distribution.items(), key=lambda item: str(item[0]))],
             missing=["当时已获知的敌情", "版本对应的应对规则与执行约束"],
             caveat="出现构成变化只是一项观测，不能证明针对性转型、预测成功或适应迟缓。")

    # 15：队伍由明确账号关联后的摘要优先；不可用0/1与1/2的编号巧合跨源拼队。
    detail_index = {_id(p.get("profile_id")): p for p in _rows(candidate.get("players"))}
    teams = {}
    for player in players:
        linked = detail_index.get(_id(player.get("profile_id")))
        if linked is not None and linked.get("team") is not None:
            teams[player["id"]] = ("摘要队伍", linked["team"])
        elif player.get("team") is not None:
            teams[player["id"]] = ("录像队伍", player["team"])
    names = {p["id"]: p.get("name") or f"局内 {p['id']}" for p in players}
    attack_rows = [row for row in records if row.get("type") in _ATTACK and row.get("known") is True]
    if attack_rows:
        player_attacks = defaultdict(list)
        for row in all_records:
            player_attacks[_id(row.get("player_id"))].append(row["time"])
        for times in player_attacks.values():
            times.sort()
        rows = []
        for row in attack_rows:
            pid, team, nearest = _id(row.get("player_id")), teams.get(_id(row.get("player_id"))), None
            if team is not None:
                for ally, ally_team in teams.items():
                    if ally == pid or ally_team != team:
                        continue
                    times = player_attacks[ally]
                    index = bisect_left(times, row["time"])
                    for offset in (index - 1, index):
                        if 0 <= offset < len(times):
                            difference = times[offset] - row["time"]
                            if nearest is None or abs(difference) < abs(nearest[0]):
                                nearest = difference, ally
            rows.append([_clock(row["time"]), names.get(pid), team[1] if team else "未明确", names.get(nearest[1]) if nearest else "未匹配", nearest[0] if nearest else None])
        track_ids = sorted({_id(row.get("player_id")) for row in attack_rows})
        _set(by_id["team_coordination"], "已列出攻击发令多轨及时间上最近的同队发令；尚不能把它解释成实际进攻或支援同步。",
             metrics=[_metric("攻击发令", len(attack_rows), "条"), _metric("有同队邻近发令可对照", sum(row[-1] is not None for row in rows), "条")],
             series=[_series(f"{names.get(pid)} · 攻击发令", _bins(attack_rows, duration, lambda row, pid=pid: _id(row.get("player_id")) == pid), "条") for pid in track_ids],
             columns=["发令时刻", "玩家", "明确队伍", "最近同队发令者", "同队发令减本次（秒）"], rows=rows,
             missing=["真实接敌、目标与支援位置", "战术意图或配合目标"],
             caveat="最近仅指时间接近；可能发生在不同战区，且较远的输入也会列出实际差值，不设置虚假的成功阈值。")
        by_id["team_coordination"]["chart_type"] = "tracks"
        by_id["team_coordination"]["event_records"] = attack_rows

    # 16：目标编号引用有证据，但没有资源实例绑定就不叫控制资源。
    references = []
    for row in records:
        target = row.get("target") if isinstance(row.get("target"), dict) else {}
        target_id = _id(row.get("target_entity_id"))
        if target_id is None:
            target_id = _id(target.get("entity_id", target.get("id")))
        if target_id not in (None, 0):
            references.append(dict(row, observed_target=target_id))
    if references:
        _set(by_id["resource_contest"], "已列出原始目标实体引用；尚未建立这些编号与本场资源实例的可靠对应。",
             metrics=[_metric("实体目标引用", len(references), "条"), _metric("唯一目标编号", len({row["observed_target"] for row in references}), "个")],
             columns=["时刻", "局内玩家", "指令", "原目标实体编号"],
             rows=[[_clock(row["time"]), row.get("player_id"), row.get("label") or "指令", row["observed_target"]] for row in references],
             missing=["本场中立资源实例身份及坐标绑定", "实际采集、控制与玩家视野"], evidence=[_evidence(row) for row in references[:80]],
             caveat="目标引用可能是军队、建筑或其他对象；不能把它自动认作矿、树或动物，也不能据此算资源控制率。")

    if selected is not None:
        selected_player = next((player for player in players if player["id"] == selected), {})
        profile = _id(selected_player.get("profile_id"))
        unique = sum(_id(player.get("profile_id")) == profile for player in players) == 1
        history_profiles = {profile} if selected_valid and profile is not None and unique else set()
    elif selected_player_id is not None:
        history_profiles = set()
    else:
        history_profiles = {_id(player.get("profile_id")) for player in players if _id(player.get("profile_id")) is not None} if players else None
    _apply_history(by_id, history, history_profiles)
    if identity_reason:
        for result in results:
            if result["status"] == "missing" and identity_reason not in result["missing"]:
                result["missing"].append(identity_reason)
    detail_duration = _number(candidate.get("duration"), True)
    clock_warning = ""
    clock = sources.get("clock") if source_ok and isinstance(sources.get("clock"), dict) else {}
    original_duration = _number(clock.get("detail_reported_duration_seconds"), True)
    if (clock.get("isolated_duration_header_corrected") is True and original_duration is not None
            and detail_duration is not None and original_duration != detail_duration):
        clock_warning = (f"旧摘要孤立时长标头{original_duration:g}秒已按同局原始统计修为{detail_duration:g}秒，"
                         f"与录像末时刻{duration:g}秒相符；仅修正时长标头，事件和采样的原始秒数未改动或拉伸。")
    elif detail_duration is not None and duration is not None and abs(detail_duration - duration) > 1:
        statistics_duration = _number((stls.get("header") or {}).get("game_length"), True)
        if statistics_duration is not None and abs(statistics_duration - duration) <= 1:
            clock_warning = (f"旧摘要时长标头为{detail_duration:g}秒，与录像末时刻{duration:g}秒不一致；"
                             f"同局原始统计终时{statistics_duration:g}秒与录像相符。保留原事件秒数，"
                             "不把孤立时长标头差异当作全部事件错位，也不拉伸曲线。")
        else:
            clock_warning = (f"录像声明时长{duration:g}秒，摘要声明时长{detail_duration:g}秒，两者不一致；"
                             "保留各来源原事件秒数，不单凭时长标头差异认定事件时钟偏移或拉伸曲线。")
    if clock_warning:
        for result in results:
            if result["status"] != "missing" and result["id"] in {
                    "resource_opportunity", "production_bottleneck", "strategy_deviation", "technology_return",
                    "army_utilization", "combat_efficiency", "attack_defense_timing", "base_vulnerability",
                    "tactical_adaptation", "defeat_trace", "team_coordination"}:
                result["caveat"] = (result["caveat"] + " " + clock_warning).strip()
    # 这些统计明确来自_bins的30秒区间，供图表标注时间粒度，避免把次数当速率或累计。
    for identity in ("attention", "ineffective_actions", "production_bottleneck",
                     "army_utilization", "combat_efficiency", "team_coordination"):
        if by_id[identity].get("series"):
            by_id[identity]["bin_seconds"] = 30
            for series in by_id[identity]["series"]:
                series["measure"] = "event_count"
                series["interval_seconds"] = 30
            if identity in {"production_bottleneck", "army_utilization", "combat_efficiency"}:
                by_id[identity]["chart_type"] = "bar"
    for result in results:
        result["analysis_kind"] = "unavailable" if result["status"] == "missing" else "observation"
        result["diagnostic_claim"] = False
    return {"schema_version": 1, "selected_player_id": selected, "players": players, "duration": duration,
            "results": results, "by_id": by_id,
            "coverage": {"commands": len(records), "detail_players": len(detail_players), "detail_events": len(events),
                         "state_created": len(created), "state_lost": len(lost), "same_game_sources": source_ok,
                         "identity_warning": identity_reason, "clock_warning": clock_warning,
                         "status_counts": dict(Counter(row["status"] for row in results)),
                         "note": "部分证据表示可核对观测，不代表高级推断已经成立。"}}
