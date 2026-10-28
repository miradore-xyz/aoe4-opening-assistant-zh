"""把录像发令坐标转换为二维地图数据；不模拟单位运动或游戏世界。

build_spatial_model（建立空间模型）接收解析结果或已归档的本地报告。
目标点表示玩家发令时指定的位置，不表示涉及单位当时所在的位置。
目前使用 X/Z 平面、保留 Y 高度；轴向和比例尚待已知地标校准。
"""
from __future__ import annotations

from collections import Counter
import math

from replay_analysis import SYSTEM_TYPE_IDS, SYSTEM_TYPES, TYPE_LABELS, resolve_report_players


_ALIASES = {
    "queueunitcommand": "queue_unit", "movecommand": "move", "cancelcommand": "cancel",
    "buildcommand": "build", "attackmovecommand": "attack_move", "unitabilitycommand": "ability",
    "rallypointcommand": "rally", "patrolcommand": "patrol", "unitstancecommand": "stance",
    "seeksheltercommand": "shelter", "pre_command": "precommand", "post_command": "postcommand",
}
_LIMITATIONS = [
    "点位是指令目标，不是单位实际位置；不会连接成单位行军轨迹。",
    "当前坐标采用 X/Z 平面，方向、比例尚未通过游戏内地标校准。",
    "显示范围仅由已记录目标点确定，不代表真实地图边界；尚未取得地形和资源分布。",
    "只有目标实体编号的指令无法确定位置，不会把其他时刻的目标点冒充其位置。",
    "此模型未取得带坐标的出生、死亡和完整单位状态；不会把生产排队当作出生。",
]


def _number(value, nonnegative=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (nonnegative and value < 0)):
        return None
    return value


def _integer(value):
    number = _number(value, nonnegative=True)
    return int(number) if number is not None and int(number) == number else None


def _identifier(value):
    """保留录像实例编号，不与账号编号、对象类型编号相互替代。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if not isinstance(value, str):
        return None
    value = value.strip()
    return int(value) if value.isdecimal() else (value or None)


def _text(value):
    return str(value).strip() if value is not None else ""


def _kind(raw):
    kind = _text(raw.get("type")).lower().replace("-", "_")
    return _ALIASES.get(kind, kind or "unknown")


def _is_system(raw, kind):
    return (kind in SYSTEM_TYPES or kind.startswith("periodiccommand")
            or _integer(raw.get("type_id")) in SYSTEM_TYPE_IDS
            or raw.get("is_system") is True
            or raw.get("category") in {"system", "control", "periodic"}
            or _identifier(raw.get("player_id")) == 0)


def _player(raw):
    player_id = _identifier(raw.get("id"))
    profile = _integer(raw.get("profile_id"))
    return {
        "id": player_id, "profile_id": profile if profile else None,
        "name": _text(raw.get("name")) or f"玩家 {player_id}",
        "civilization": _text(raw.get("civilization")) or None,
        "team": raw.get("team") if isinstance(raw.get("team"), (int, str))
        and not isinstance(raw.get("team"), bool) else None,
    }


def build_spatial_model(report, detail=None):
    """生成地图和时间轴模型；只消费传入数据，不读写文件或发起网络请求。

    report（报告）可为 parse_replay 的 commands（原始指令）、
    analyze_replay 的 timeline（已整理指令），或本地库的外层归档。
    detail（详细统计）只提供独立的对局时长，绝不用于拉伸录像时钟。
    bounds（真实地图边界）保持未知；observed_bounds（目标点范围）仅供镜头适配。
    """
    if not isinstance(report, dict):
        raise ValueError("二维地图需要本地录像报告。")
    if isinstance(report.get("report"), dict):
        report = report["report"]
    metadata = report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
    source = report.get("source") if isinstance(report.get("source"), dict) else {}
    coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
    if isinstance(report.get("commands"), list):
        records, record_source = report["commands"], "replay.commands"
    else:
        records = report.get("timeline") if isinstance(report.get("timeline"), list) else []
        record_source = "report.timeline"
    raw_players = resolve_report_players(report)
    players = {}
    for raw in raw_players if isinstance(raw_players, list) else []:
        if isinstance(raw, dict):
            player = _player(raw)
            if player["id"] not in (None, 0):
                players[player["id"]] = player

    digest = _text(source.get("sha256")).lower()
    # 无源摘要时仍提供可重复的局部编号；它不宣称跨不同录像的全局唯一性。
    prefix = digest if len(digest) == 64 and all(char in "0123456789abcdef" for char in digest) else "local"
    ticks_per_second = _number(coverage.get("ticks_per_second"), nonnegative=True)
    counts = Counter()
    unknown_types = Counter()
    actions = Counter()
    identifiers = Counter()
    events = []
    last_time = None
    duration = _number(metadata.get("duration"), nonnegative=True)
    for index, raw in enumerate(records):
        if not isinstance(raw, dict):
            counts["invalid_records"] += 1
            continue
        action = _kind(raw)
        if _is_system(raw, action):
            counts["processed_system_commands"] += 1
            continue
        player_id = _identifier(raw.get("player_id"))
        if player_id is None:
            counts["unassigned_commands"] += 1
            continue
        counts["player_commands"] += 1
        if player_id not in players:
            players[player_id] = _player({"id": player_id, "name": raw.get("player_name")})
        known = raw.get("known") is not False and action in TYPE_LABELS and action != "unknown"
        if not known:
            counts["unknown_commands"] += 1
            type_id = _integer(raw.get("type_id"))
            unknown_types[str(type_id) if type_id is not None else action] += 1
        time = _number(raw.get("time"), nonnegative=True)
        tick = _integer(raw.get("tick"))
        if time is None and tick is not None and ticks_per_second:
            time = tick / ticks_per_second
        if time is not None:
            last_time = time if last_time is None else max(last_time, time)
        target = raw.get("target")
        x = _number(target.get("x")) if isinstance(target, dict) else None
        z = _number(target.get("z")) if isinstance(target, dict) else None
        if x is None or z is None:
            counts["unlocated"] += 1
            if isinstance(target, dict) and any(key in target for key in ("x", "y", "z")):
                counts["invalid_or_incomplete_coordinates"] += 1
            continue
        if time is None:
            counts["untimed_coordinates"] += 1
            continue
        offset = _integer(raw.get("offset"))
        base_id = f"{prefix}:command:{offset}" if offset is not None else f"{prefix}:record:{index}"
        occurrence = identifiers[base_id]
        identifiers[base_id] += 1
        event_id = base_id if not occurrence else f"{base_id}:{occurrence}"
        target_entity_id = _identifier(raw.get("target_entity_id"))
        if target_entity_id is None and isinstance(target, dict):
            target_entity_id = _identifier(target.get("entity_id", target.get("id")))
        units = raw.get("units") if isinstance(raw.get("units"), list) else []
        event = {
            "id": event_id, "time": time, "tick": tick,
            "player_id": player_id, "profile_id": players[player_id]["profile_id"],
            "player_name": players[player_id]["name"],
            "kind": "command", "action": action,
            "label": TYPE_LABELS.get(action, "未识别指令") if known else "未识别指令",
            "units": [value for unit in units if (value := _identifier(unit)) is not None],
            "target": {"x": x, "y": _number(target.get("y")), "z": z},
            "x": x, "y": z,
            "target_entity_id": target_entity_id,
            "unit_type_id": _identifier(raw.get("unit_type_id")),
            "building_type_id": _identifier(raw.get("building_type_id")),
            "placement_points": [dict(point) for point in raw.get("placement_points", [])
                                 if isinstance(point, dict)] if isinstance(raw.get("placement_points"), list) else [],
            "placement_outcome": "unknown" if action == "place_building" else None,
            "producer_entity_id": _identifier(raw.get("producer_entity_id")),
            "queued": raw.get("queued") if isinstance(raw.get("queued"), bool) else None,
            "known": known, "payload_decoded": raw.get("payload_decoded")
            if isinstance(raw.get("payload_decoded"), bool) else None,
            "type_id": _integer(raw.get("type_id")), "offset": offset,
            "source": record_source, "source_index": index, "confidence": "command_target",
        }
        events.append(event)
        actions[action] += 1
        if duration is not None and time > duration:
            counts["beyond_reported_duration"] += 1
    events.sort(key=lambda event: (event["time"], event["source_index"]))
    observed_bounds = None
    if events:
        observed_bounds = {"x_min": min(event["x"] for event in events),
                           "x_max": max(event["x"] for event in events),
                           "y_min": min(event["y"] for event in events),
                           "y_max": max(event["y"] for event in events)}
    duration_source = _text(metadata.get("duration_source")) or ("metadata.duration" if duration is not None else None)
    if duration is None and last_time is not None:
        duration, duration_source = last_time, "last_command_time"
    detail = detail if isinstance(detail, dict) else {}
    detail_duration = _number(detail.get("duration"), nonnegative=True)
    warnings = []
    if detail_duration is not None and duration is not None and detail_duration != duration:
        warnings.append("录像时钟与详细统计时长不同；地图保留录像时钟，不拉伸目标点时间。")
    if counts["beyond_reported_duration"]:
        warnings.append("部分指令时间晚于所给录像时长，已保留原时间供核对。")
    if not events:
        warnings.append("当前报告没有同时包含时间与 X/Z 目标坐标的玩家指令。")
    # 归档时间线已剔除系统行，不能把本轮观察到的零行误写成源录像没有系统记录。
    total = _integer(coverage.get("commands_total"))
    source_systems = _integer(coverage.get("system_commands"))
    model_coverage = {
        "commands_total": total if total is not None else len(records),
        "records_received": len(records), "player_commands": counts["player_commands"],
        "plottable": len(events), "unlocated": counts["unlocated"],
        "untimed_coordinates": counts["untimed_coordinates"],
        "invalid_or_incomplete_coordinates": counts["invalid_or_incomplete_coordinates"],
        "invalid_records": counts["invalid_records"], "unassigned_commands": counts["unassigned_commands"],
        "processed_system_commands": counts["processed_system_commands"],
        "source_system_commands": source_systems if source_systems is not None else counts["processed_system_commands"],
        "unknown_commands": counts["unknown_commands"], "unknown_types": dict(unknown_types),
        "plottable_by_action": dict(actions), "beyond_reported_duration": counts["beyond_reported_duration"],
        "births_available": False, "deaths_available": False,
        "building_placements_available": bool(actions.get("place_building")),
        "unit_positions_available": False, "terrain_available": False,
    }
    return {
        "schema_version": 1, "kind": "command-target-map", "game_id": _integer(source.get("game_id")),
        "source_sha256": digest if prefix != "local" else None, "map_name": metadata.get("map"),
        "players": list(players.values()), "duration": duration, "duration_source": duration_source,
        "detail_duration": detail_duration, "events": events,
        "bounds": None, "observed_bounds": observed_bounds,
        "coordinate_system": {"axes": ["x", "z"], "vertical_axis": "y", "calibrated": False,
                              "label": "指令目标坐标参照", "unit": "录像原始坐标单位，未校准"},
        "coverage": model_coverage, "warnings": warnings, "limitations": list(_LIMITATIONS),
    }
