"""离线录像报告：把发令记录转成可核对的多玩家时间线。

command（指令）不是物理按键，也不代表执行成功。本模块只消费已解析数据，
不读取游戏进程、网络包、键盘或鼠标。归档仅保存白名单报告字段。
"""
from __future__ import annotations

import hashlib
import json
import math
import ntpath
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 1
CATEGORY_LABELS = {
    "production": "生产排队", "movement": "移动与集结", "combat": "攻击与巡逻",
    "construction": "建造", "cancel": "取消", "ability": "能力与姿态",
    "other": "其他指令", "unknown": "未识别指令",
}
TYPE_LABELS = {
    "queue_unit": "单位排队指令", "queue": "排队指令", "produce": "生产指令",
    "research": "研究指令", "move": "移动指令", "rally": "集结点指令",
    "attack": "攻击指令", "attack_move": "攻击移动指令", "patrol": "巡逻指令",
    "build": "建造指令", "cancel": "取消指令", "ability": "能力指令",
    "stance": "姿态指令", "shelter": "避难指令", "stop": "停止指令",
    "select": "选择指令", "unknown": "未识别指令",
}
TYPE_CATEGORIES = {
    "queue_unit": "production", "queue": "production", "produce": "production",
    "research": "production", "move": "movement", "rally": "movement",
    "attack": "combat", "attack_move": "combat", "patrol": "combat",
    "build": "construction", "cancel": "cancel", "ability": "ability",
    "stance": "ability", "shelter": "ability", "stop": "other", "select": "other",
}
SYSTEM_TYPES = {"control", "periodic", "sync", "heartbeat", "precommand", "postcommand"}
SYSTEM_TYPE_IDS = {146, 148, 152, 153}
RELATION_LABELS = {"self": "关注玩家", "ally": "同队", "opponent": "其他队伍", "unknown": "关系未确认"}


class ReplayAnalysisError(ValueError):
    """可以直接显示的本地报告错误。"""


def _text(value, length=200):
    return str(value).strip()[:length] if value is not None else ""


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        return None
    return value if value >= 0 else None


def _integer(value):
    number = _number(value)
    return int(number) if number is not None and int(number) == number else None


def _identifier(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    value = _text(value, 80)
    return int(value) if value.isdecimal() else (value or None)


def _team(value):
    """零、负数和未知标记不作为可靠队伍关系。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if math.isfinite(value) and value > 0 and int(value) == value else None
    value = _text(value, 80)
    if value.lower() in {"", "0", "-1", "unknown", "none", "未知"}:
        return None
    return int(value) if value.isdecimal() else value


def _source(parsed):
    source = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
    digest = _text(source.get("sha256"), 128).lower()
    result = {
        "name": ntpath.basename(_text(source.get("name"), 500)) or "未命名录像",
        "sha256": digest if re.fullmatch(r"[0-9a-f]{64}", digest) else "",
        "format": _text(source.get("format"), 80) or "unknown",
        "size": _integer(source.get("size")),
    }
    member = ntpath.basename(_text(source.get("archive_member"), 500))
    if member:
        result["archive_member"] = member
    # 公开回放的对局来源用于归档核对；不保存下载令牌或用户私人路径。
    if source.get("origin") == "aoe4world-public-replay":
        game_id, profile_id = _integer(source.get("game_id")), _integer(source.get("profile_id"))
        if game_id and game_id > 0 and profile_id and profile_id > 0:
            result.update(origin="aoe4world-public-replay", game_id=game_id, profile_id=profile_id)
            # 回放可取自官网标记公开的同场玩家；分析关注账号仍为原查询账号。
            download_profile_id = _integer(source.get("download_profile_id"))
            if download_profile_id and download_profile_id > 0:
                result["download_profile_id"] = download_profile_id
    return result


def _player(raw):
    player_id = _identifier(raw.get("id"))
    return {
        "id": player_id, "profile_id": _identifier(raw.get("profile_id")),
        "name": _text(raw.get("name")) or f"玩家 {player_id}",
        "civilization": _text(raw.get("civilization"), 100) or None,
        "team": _team(raw.get("team")), "command_count": 0,
        "counts": {key: 0 for key in CATEGORY_LABELS}, "per_minute": [], "relation": "unknown",
    }


def _command_type(raw):
    value = _text(raw.get("type"), 80).lower().replace("-", "_")
    aliases = {
        "queueunitcommand": "queue_unit", "movecommand": "move", "cancelcommand": "cancel",
        "buildcommand": "build", "attackmovecommand": "attack_move", "unitabilitycommand": "ability",
        "rallypointcommand": "rally", "patrolcommand": "patrol", "unitstancecommand": "stance",
        "seeksheltercommand": "shelter", "pre_command": "precommand", "post_command": "postcommand",
    }
    return aliases.get(value, value or "unknown")


def _is_system(raw, kind):
    return (kind in SYSTEM_TYPES or kind.startswith("periodiccommand")
            or _integer(raw.get("type_id")) in SYSTEM_TYPE_IDS or raw.get("is_system") is True
            or raw.get("category") == "system" or _identifier(raw.get("player_id")) == 0)


def _details(raw, display=False):
    parts = []
    units = raw.get("units")
    if isinstance(units, list):
        ids = [_identifier(value) for value in units]
        ids = [value for value in ids if value is not None]
        if ids:
            parts.append(f"涉及 {len(ids)} 个单位" if display else f"涉及 {len(ids)} 个单位；编号 "
                         + "、".join(map(str, ids[:6])) + ("…" if len(ids) > 6 else ""))
    unit_type = _identifier(raw.get("unit_type_id"))
    if unit_type is not None and not display:
        parts.append(f"对象类型编号 {unit_type}")
    target = raw.get("target")
    if isinstance(target, dict):
        coords = [(key, target.get(key)) for key in ("x", "y", "z")
                  if isinstance(target.get(key), (int, float)) and not isinstance(target.get(key), bool)
                  and math.isfinite(target[key])]
        if coords:
            parts.append("已记录目标位置" if display else "目标坐标 " + " / ".join(f"{value:g}" for _, value in coords))
        entity = _identifier(target.get("entity_id", target.get("id")))
        if entity is not None:
            parts.append("已记录目标对象" if display else f"目标实体编号 {entity}")
    elif _identifier(target) is not None:
        parts.append("已记录目标对象" if display else f"目标实体编号 {_identifier(target)}")
    if _identifier(raw.get("target_entity_id")) is not None:
        parts.append("已记录目标对象" if display else f"目标实体编号 {_identifier(raw['target_entity_id'])}")
    if isinstance(raw.get("queued"), bool):
        parts.append("追加到指令队列" if raw["queued"] else "立即发令")
    return "；".join(parts)


def _target(value):
    if not isinstance(value, dict):
        return _identifier(value)
    result = {key: value[key] for key in ("x", "y", "z") if isinstance(value.get(key), (float, int))
              and not isinstance(value[key], bool) and math.isfinite(value[key])}
    for key in ("entity_id", "id"):
        if _identifier(value.get(key)) is not None:
            result[key] = _identifier(value[key])
    return result or None


def _evidence(item):
    return {key: item.get(key) for key in ("time", "player_id", "label", "offset")}


def _findings(players, timeline):
    """生成带证据的问题卡，不根据发令次数或空档推断操作失误。"""
    findings = []
    for player in players:
        rows = [row for row in timeline if row["player_id"] == player["id"]]
        known = [row for row in rows if row["known"]]
        candidates = []
        for category in ("construction", "production", "combat"):
            first = next((row for row in known if row["category"] == category and row["time"] is not None), None)
            if first:
                candidates.append(first)
        if candidates:
            findings.append({
                "player_id": player["id"], "title": "核对关键发令顺序", "confidence": "recorded",
                "body": f"已定位 {player['name']} 的关键发令时刻。这些记录说明发出了指令，不代表建筑、单位或进攻已经完成。",
                "evidence": [_evidence(row) for row in candidates],
                "action": "对照所选开局和录像画面，确认当时的安排与后续结果是否符合计划。",
            })
        cancelled = [row for row in known if row["category"] == "cancel"]
        if cancelled:
            findings.append({
                "player_id": player["id"], "title": "回看取消指令的目的", "confidence": "question",
                "body": f"记录到 {len(cancelled)} 条取消指令。取消可能是主动调整，现有记录不能判断是否造成损失。",
                "evidence": [_evidence(row) for row in cancelled[:3]],
                "action": "回看对应时刻，区分计划调整、受压应对和误操作，再决定是否需要专项练习。",
            })
        if not candidates and not cancelled and rows:
            findings.append({
                "player_id": player["id"], "title": "先核对指令与画面", "confidence": "question",
                "body": "当前可识别记录不足以定位开局节点或训练问题。发令空档不能直接解释为闲置或失误。",
                "evidence": [_evidence(rows[0])], "action": "结合录像确认操作对象和实际结果，再补充训练目标。",
            })
    return findings


def focus_report(report, player_id=None):
    """为内存中或已归档报告切换关注玩家；不改变证据或猜测队伍。"""
    # 时间线和证据只读共享；切换玩家不复制数万条记录，避免界面卡顿。
    result = dict(report)
    result["players"] = [dict(player) for player in report.get("players", [])]
    requested = _identifier(player_id)
    focus = next((player for player in result.get("players", []) if player.get("id") == requested), None)
    result["focus_player_id"] = focus["id"] if focus else None
    focus_team = _team(focus.get("team")) if focus else None
    for player in result.get("players", []):
        team = _team(player.get("team"))
        relation = "unknown"
        if focus and player["id"] == focus["id"]:
            relation = "self"
        elif focus_team is not None and team is not None:
            relation = "ally" if team == focus_team else "opponent"
        player["relation"] = relation
    return result


def analyze_replay(parsed, focus_player_id=None):
    """生成纯数据报告；时间单位为秒，offset（偏移）用于定位原录像证据。"""
    if not isinstance(parsed, dict):
        raise ReplayAnalysisError("录像解析结果格式不正确。")
    raw_meta = parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {}
    player_index = {}
    for raw in raw_meta.get("players") or []:
        if isinstance(raw, dict) and _identifier(raw.get("id")) not in (None, 0):
            player = _player(raw)
            player_index.setdefault(player["id"], player)
    raw_coverage = parsed.get("coverage") if isinstance(parsed.get("coverage"), dict) else {}
    raw_warnings = raw_coverage.get("warnings")
    warnings = [_text(item, 1000) for item in raw_warnings if isinstance(item, str)] if isinstance(raw_warnings, list) else []
    ticks_per_second = _number(raw_coverage.get("ticks_per_second"))
    timeline, per_minute = [], defaultdict(Counter)
    systems = missing_player = invalid_records = 0
    commands = parsed.get("commands") if isinstance(parsed.get("commands"), list) else []
    for raw in commands:
        if not isinstance(raw, dict):
            invalid_records += 1
            continue
        kind = _command_type(raw)
        if _is_system(raw, kind):
            systems += 1
            continue
        player_id = _identifier(raw.get("player_id"))
        if player_id is None:
            missing_player += 1
            continue
        if player_id not in player_index:
            player_index[player_id] = _player({"id": player_id})
        player = player_index[player_id]
        known = raw.get("known") is not False and kind in TYPE_CATEGORIES
        category = TYPE_CATEGORIES.get(kind, "unknown") if known else "unknown"
        time = _number(raw.get("time"))
        tick = _integer(raw.get("tick"))
        if time is None and tick is not None and ticks_per_second:
            time = tick / ticks_per_second
        type_id = _integer(raw.get("type_id"))
        label = TYPE_LABELS.get(kind, "未识别指令") if known else "未识别指令"
        timeline.append({
            "time": time, "tick": tick, "player_id": player_id, "player_name": player["name"],
            "category": category, "label": label, "detail": _details(raw), "display_detail": _details(raw, display=True),
            "offset": _integer(raw.get("offset")), "size": _integer(raw.get("size")),
            "type": kind, "type_id": type_id, "known": known,
            "units": [_identifier(value) for value in raw.get("units", []) if _identifier(value) is not None]
                     if isinstance(raw.get("units"), list) else [],
            "unit_type_id": _identifier(raw.get("unit_type_id")), "target": _target(raw.get("target")),
            "target_entity_id": _identifier(raw.get("target_entity_id")),
            "producer_entity_id": _identifier(raw.get("producer_entity_id")),
            "queued": raw["queued"] if isinstance(raw.get("queued"), bool) else None,
            "payload_decoded": raw["payload_decoded"] if isinstance(raw.get("payload_decoded"), bool) else None,
            "payload_bytes_remaining": _integer(raw.get("payload_bytes_remaining")),
            "target_bytes_remaining": _integer(raw.get("target_bytes_remaining")),
        })
        player["command_count"] += 1
        player["counts"][category] += 1
        if time is not None:
            per_minute[player_id][int(time // 60)] += 1
    timeline.sort(key=lambda row: (row["time"] is None, row["time"] or 0, row["offset"] or 0))
    players = list(player_index.values())
    for player in players:
        player["per_minute"] = [{"minute": minute, "count": count}
                                for minute, count in sorted(per_minute[player["id"]].items())]
    if missing_player:
        warnings.append(f"{missing_player} 条非系统记录未提供玩家编号，未计入玩家统计。")
    if invalid_records:
        warnings.append(f"{invalid_records} 条格式不正确的记录未计入统计。")
    metadata = {
        "game_version": _text(raw_meta.get("game_version"), 100) or None,
        "build_number": _integer(raw_meta.get("build_number")),
        "map": _text(raw_meta.get("map", raw_meta.get("map_name"))) or None,
        "duration": _number(raw_meta.get("duration")),
        "duration_source": _text(raw_meta.get("duration_source"), 100) or None,
        "players": [{key: player[key] for key in ("id", "profile_id", "name", "civilization", "team")} for player in players],
    }
    unknown_types = raw_coverage.get("unknown_types")
    if isinstance(unknown_types, dict):
        unknown_types = {_text(key, 80): _integer(value) for key, value in unknown_types.items()}
    elif isinstance(unknown_types, list):
        unknown_types = [_identifier(value) for value in unknown_types if _identifier(value) is not None]
    else:
        unknown_types = sorted({row["type_id"] for row in timeline if not row["known"] and row["type_id"] is not None})
    report = {
        "schema_version": SCHEMA_VERSION, "source": _source(parsed), "metadata": metadata,
        "players": players, "timeline": timeline, "findings": _findings(players, timeline),
        "coverage": {
            "commands_total": _integer(raw_coverage.get("commands_total")) if _integer(raw_coverage.get("commands_total")) is not None else len(commands),
            "commands_decoded": _integer(raw_coverage.get("commands_decoded")),
            "commands_identified": _integer(raw_coverage.get("commands_identified")),
            "decoded_definition": _text(raw_coverage.get("decoded_definition"), 500),
            "commands_analyzed": len(timeline), "commands_known": sum(row["known"] for row in timeline),
            "system_commands": systems, "unassigned_commands": missing_player,
            "unknown_types": unknown_types, "warnings": list(dict.fromkeys(warnings)),
            "ticks_per_second": ticks_per_second,
            "ticks_total": _integer(raw_coverage.get("ticks_total")),
            "stream_complete": raw_coverage["stream_complete"] if isinstance(raw_coverage.get("stream_complete"), bool) else None,
            "metadata_version": _integer(raw_coverage.get("metadata_version")),
            "payload_errors": _integer(raw_coverage.get("payload_errors")),
            "chat_records_skipped": _integer(raw_coverage.get("chat_records_skipped")),
            "expanded_size": _integer(raw_coverage.get("expanded_size")),
            "events_available": bool(parsed.get("events")), "resources_available": bool(parsed.get("resources")),
            "limitations": ["发令记录不是键鼠次数，也不证明执行成功。", "周期和控制记录已排除，不将发令空档判为失误。",
                            "尚未验证生产队列、人口上限、单位状态和视野，不能据此判定断村、卡人口或闲军。"],
        },
    }
    return focus_report(report, focus_player_id)


def filter_timeline(report, player_id=None, category=None, query="", offset=0, limit=None):
    """按玩家、类别和显示文本筛选；分页只改变视图，不改变完整报告。"""
    player_id = _identifier(player_id)
    query = _text(query, 200).casefold()
    rows = [row for row in report.get("timeline", []) if
            (player_id is None or row.get("player_id") == player_id)
            and (not category or row.get("category") == category)
            and (not query or query in " ".join(str(row.get(key, "")) for key in ("player_name", "label", "detail")).casefold())]
    start = max(0, _integer(offset) or 0)
    return rows[start:] if limit is None else rows[start:start + max(0, _integer(limit) or 0)]


def _archive_report(report):
    """白名单化生成报告，聊天、原始字节和完整文件路径均不归档。"""
    if not isinstance(report, dict) or report.get("schema_version") != SCHEMA_VERSION:
        raise ReplayAnalysisError("不支持的本地复盘报告版本。")
    # 重建输入，只取本模块生成报告所需的字段，避免归档额外敏感内容。
    commands = []
    for row in report.get("timeline", []):
        commands.append({key: row.get(key) for key in ("time", "tick", "player_id", "type", "type_id", "known", "offset", "size",
                                                       "units", "unit_type_id", "target", "target_entity_id", "producer_entity_id",
                                                       "queued", "payload_decoded", "payload_bytes_remaining", "target_bytes_remaining")})
    clean = analyze_replay({"source": report.get("source", {}), "metadata": report.get("metadata", {}),
                            "commands": commands, "coverage": report.get("coverage", {})}, report.get("focus_player_id"))
    # detail（详情）重新由数值字段生成，避免任意外部正文混入归档。
    original_coverage = report.get("coverage", {})
    for key in ("system_commands", "unassigned_commands", "events_available", "resources_available"):
        value = original_coverage.get(key)
        clean["coverage"][key] = bool(value) if key.endswith("_available") else (_integer(value) or 0)
    return clean


class ReplayLibrary:
    """本地复盘库：按源文件摘要去重；删除只影响报告，不碰原录像。"""
    def __init__(self, data_dir):
        self.directory = Path(data_dir) / "本地复盘"
        self.index_directory = self.directory / "索引"

    def _path(self, report_id):
        if not isinstance(report_id, str) or not re.fullmatch(r"[0-9a-f]{64}", report_id):
            raise ReplayAnalysisError("本地报告编号不正确。")
        return self.directory / f"{report_id}.json"

    @staticmethod
    def _summary(report, report_id, created_at, updated_at=None):
        source, metadata, coverage = report.get("source", {}), report.get("metadata", {}), report.get("coverage", {})
        return {"id": report_id, "name": ntpath.basename(_text(source.get("name"), 500)) or "未命名录像",
                "created_at": _text(created_at, 80),
                "updated_at": _text(updated_at or created_at, 80),
                "player_names": [_text(player.get("name")) for player in report.get("players", []) if isinstance(player, dict)],
                "map": _text(metadata.get("map")) or None, "duration": _number(metadata.get("duration")),
                "command_count": _integer(coverage.get("commands_analyzed")) or 0,
                "decoded_count": _integer(coverage.get("commands_decoded")),
                "status": "partial" if coverage.get("warnings") or coverage.get("unknown_types") else "parsed"}

    def _write_summary(self, summary):
        """小型独立索引让列表无需解析每份完整时间线。"""
        summary = dict(summary)
        source_stat = self._path(summary["id"]).stat()
        summary["_report_size"] = source_stat.st_size
        summary["_report_mtime_ns"] = source_stat.st_mtime_ns
        self.index_directory.mkdir(parents=True, exist_ok=True)
        path = self.index_directory / f"{summary['id']}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(summary, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temporary.replace(path)

    def save(self, report):
        report = _archive_report(report)
        body = json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False)
        report_id = report["source"]["sha256"] or hashlib.sha256(body.encode("utf-8")).hexdigest()
        path = self._path(report_id)
        duplicate = path.is_file()
        now = datetime.now(timezone.utc).isoformat()
        created_at = now
        if duplicate:
            # 用户重新导入代表重新分析，同一录像也可能得到更完整的解析结果。
            # 保留条目与首次导入时间，原子替换报告；旧缓存不覆盖新结果。
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
                if previous.get("id") == report_id:
                    created_at = _text(previous.get("created_at"), 80) or now
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {"id": report_id, "created_at": created_at, "updated_at": now, "report": report}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
        self._write_summary(self._summary(report, report_id, created_at, now))
        return {"id": report_id, "path": str(path), "duplicate": duplicate, "updated": duplicate, "report": report}

    def load(self, report_id):
        try:
            payload = json.loads(self._path(report_id).read_text(encoding="utf-8"))
            if payload.get("id") != report_id:
                raise ReplayAnalysisError("本地报告编号不匹配。")
            return _archive_report(payload["report"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ReplayAnalysisError("本地复盘报告无法读取，请重新导入录像。") from exc

    def list(self):
        items = []
        for path in self.directory.glob("*.json"):
            if not re.fullmatch(r"[0-9a-f]{64}", path.stem):
                continue
            try:
                index_path = self.index_directory / path.name
                summary = None
                if index_path.is_file():
                    summary = json.loads(index_path.read_text(encoding="utf-8"))
                    if summary.get("id") != path.stem:
                        continue
                    source_stat = path.stat()
                    if summary.get("_report_size") != source_stat.st_size or summary.get("_report_mtime_ns") != source_stat.st_mtime_ns:
                        summary = None
                if summary is not None:
                    # 索引同样只返回已知显示字段，不透传额外内容。
                    summary = {"id": path.stem, "name": ntpath.basename(_text(summary.get("name"), 500)),
                               "created_at": _text(summary.get("created_at"), 80),
                               "updated_at": _text(summary.get("updated_at") or summary.get("created_at"), 80),
                               "player_names": [_text(name) for name in summary.get("player_names", []) if isinstance(name, str)],
                               "map": _text(summary.get("map")) or None, "duration": _number(summary.get("duration")),
                               "command_count": _integer(summary.get("command_count")) or 0,
                               "decoded_count": _integer(summary.get("decoded_count")),
                               "status": "partial" if summary.get("status") == "partial" else "parsed"}
                else:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    report = payload["report"]
                    if payload.get("id") != path.stem or report.get("schema_version") != SCHEMA_VERSION:
                        continue
                    summary = self._summary(report, path.stem, payload.get("created_at"), payload.get("updated_at"))
                    self._write_summary(summary)
            except (ReplayAnalysisError, OSError, ValueError, TypeError, KeyError, AttributeError):
                continue
            items.append(summary)
        return sorted(items, key=lambda item: item["updated_at"], reverse=True)

    def delete(self, report_id):
        path = self._path(report_id)
        (self.index_directory / path.name).unlink(missing_ok=True)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
