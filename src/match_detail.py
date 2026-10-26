"""公开对局摘要转为图表与事件证据，不把指令意图当作实际发生。

summary（摘要）中的库存、每分速率和评分分别保存。单位出现、建成及损失
按原始事件秒数排列；没有选中、移动轨迹、击杀归属或村民工作分配时保持缺失。
本模块不发网络请求，不读取游戏进程。所有经济结论都能回到采样点或事件。
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path


SCHEMA_VERSION = 1
RESOURCES = {"food": "食物", "wood": "木材", "gold": "黄金", "stone": "石料"}
SCORES = {"total": "总分", "economy": "经济评分", "military": "军事评分",
          "technology": "科技评分", "society": "社会评分"}
GAPS = ["未提供选中目标和鼠标点击记录", "未提供单位逐帧位置或移动轨迹",
        "未提供每名村民的采集、步行、建造和空闲状态", "损失事件未提供击杀方或死亡原因",
        "单位完成/出现事件未提供开始排队或生产建筑空闲时间", "兼容摘要未保留完整转化得失，村民事件计数不是工作状态或停产证明",
        "来源采集速率的统计窗口未明确说明"]


class MatchDetailError(ValueError):
    """可直接呈现给用户的数据错误。"""


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _integer(value):
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    number = _number(value)
    return int(number) if number is not None and number >= 0 and number == int(number) else None


def _text(value, limit=180):
    return str(value).strip()[:limit] if value is not None else ""


def time_label(seconds):
    """游戏经过秒数转为 分:秒；不是墙上时钟时间。"""
    value = max(0, int(_number(seconds) or 0))
    return f"{value // 60:02d}:{value % 60:02d}"


@lru_cache(maxsize=1)
def load_entity_catalog():
    """包内精确 pbgid（实体蓝图编号）目录；不靠图标文件名猜单位。"""
    root = Path(sys._MEIPASS) / "assets" / "catalog" if hasattr(sys, "_MEIPASS") else Path(__file__).resolve().parent / "文明资料"
    try:
        document = json.loads((root / "match_entities.json").read_text(encoding="utf-8"))
        return document.get("entities", {})
    except (OSError, ValueError, TypeError):
        return {}


def _relation(team, other_team, same_player=False):
    # 公开摘要明确使用从 0 开始的队伍编号；0 是有效队伍。
    if same_player:
        return "self"
    if team is None or other_team is None:
        return "unknown"
    return "ally" if team == other_team else "opponent"


def _series(label, unit, kind, source, timestamps, values, confidence="reported"):
    points = []
    if isinstance(values, list):
        for index, timestamp in enumerate(timestamps):
            value = _number(values[index]) if index < len(values) else None
            # 缺点保留 None，图表应断开，不能补零或补成另一时间的值。
            points.append([timestamp, value])
    return {"label": label, "unit": unit, "kind": kind, "source": source,
            "points": points, "confidence": confidence}


def _sum_series(series, keys, label, unit, kind):
    if not all(key in series for key in keys):
        return None
    sources = [series[key] for key in keys]
    points = []
    for index, (timestamp, _) in enumerate(sources[0]["points"]):
        values = [row["points"][index][1] if index < len(row["points"]) and row["points"][index][0] == timestamp else None for row in sources]
        points.append([timestamp, sum(values) if all(value is not None for value in values) else None])
    return {"label": label, "unit": unit, "kind": kind, "source": "+".join(keys),
            "points": points, "confidence": "derived"}


def _player_series(raw, warnings):
    resources = raw.get("resources") if isinstance(raw.get("resources"), dict) else {}
    raw_times = resources.get("timestamps", [])
    if not isinstance(raw_times, list):
        return {}
    # 不能排序时间轴后仍用旧数组位置，异常时间轴直接不绘制。
    timestamps = [_number(value) for value in raw_times]
    if any(value is None or value < 0 for value in timestamps) or any(b <= a for a, b in zip(timestamps, timestamps[1:])):
        warnings.append("资源时间轴异常，已跳过该玩家曲线。")
        return {}
    series = {}
    for resource, label in RESOURCES.items():
        for prefix, field, suffix, unit, kind in (
                ("bank", resource, "库存", "资源", "bank"),
                ("income", resource + "_per_min", "来源采集速率", "资源/分", "rate"),
                ("reported_usage", resource + "_gathered", "原始累计值（语义待核实）", "资源", "unverified")):
            if isinstance(resources.get(field), list):
                if len(resources[field]) != len(timestamps):
                    warnings.append(f"{label}{suffix}存在缺失采样，缺值保留为空。")
                series[f"{prefix}_{resource}"] = _series(label + suffix, unit, kind,
                    "players[].resources." + field, timestamps, resources[field])
    for prefix, label, unit, kind in (("bank", "四资源库存", "资源", "bank"), ("income", "四资源采集速率", "资源/分", "rate")):
        total = _sum_series(series, [f"{prefix}_{resource}" for resource in RESOURCES], label, unit, kind)
        if total:
            series[prefix + "_total"] = total
    for field, label in SCORES.items():
        if isinstance(resources.get(field), list):
            series["score_" + field] = _series(label, "分", "score", "players[].resources." + field, timestamps, resources[field])
    if any(key.startswith("reported_usage_") for key in series):
        warnings.append("源字段 *_gathered 在已核对样本中与支出统计相符；尚未用它冒充累计采集量或计算经济差距。")
    return series


def _entity_events(raw, catalog, warnings):
    profile_id = _integer(raw.get("profile_id"))
    entries = raw.get("build_order") if isinstance(raw.get("build_order"), list) else []
    events, known, unknown = [], 0, 0
    villager_changes = defaultdict(lambda: Counter(born=0, lost=0, initial=0))
    villager_known, villager_ambiguous = False, False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        pbgid = _integer(entry.get("pbgid"))
        # 模组可能重用蓝图编号；原版目录不能替它确认名称或村民类别。
        entity = catalog.get(str(pbgid), catalog.get(pbgid, {})) if entry.get("modid") in (None, "") else {}
        if entry.get("modid") not in (None, ""):
            warnings.append("部分实体来自模组，未使用原版蓝图目录猜测其名称和类别。")
        entity_type = _text(entry.get("type"), 30)
        if entity:
            known += 1
        else:
            unknown += 1
        name = _text(entity.get("name")) or {"Unit": "未识别单位", "Building": "未识别建筑", "Upgrade": "未识别科技", "Age": "时代"}.get(entity_type, "未识别实体")
        is_villager = bool(entity_type == "Unit" and "villager" in entity.get("classes", []))
        villager_known |= is_villager
        if is_villager and any(entry.get(field) for field in ("packed", "unpacked", "transformed")):
            villager_ambiguous = True
        for source_field in ("finished", "constructed", "destroyed", "packed", "unpacked", "transformed"):
            values = entry.get(source_field)
            if not isinstance(values, list):
                continue
            counts = Counter(value for value in (_number(value) for value in values) if value is not None and value >= 0)
            for timestamp, quantity in counts.items():
                if source_field == "destroyed":
                    kind, verb = "loss", "损失"
                elif source_field == "constructed":
                    kind, verb = "building", "建成"
                elif source_field == "finished":
                    kind, verb = {"Unit": ("production", "单位完成/出现"), "Upgrade": ("upgrade", "科技完成"), "Age": ("age", "进入时代")}.get(entity_type, ("completed", "完成"))
                else:
                    kind, verb = source_field, {"packed": "打包", "unpacked": "展开", "transformed": "变形"}[source_field]
                if timestamp == 0 and source_field in {"finished", "constructed"}:
                    kind, verb = "initial", "初始拥有"
                if entity_type == "Age":
                    # 时代实体不冒充普通单位产量。
                    name = "时代节点"
                events.append({"time": timestamp, "time_label": time_label(timestamp), "profile_id": profile_id,
                    "kind": kind, "label": f"{verb} {name}" + (f" ×{quantity}" if quantity != 1 else ""),
                    "entity_name": name, "entity_type": entity_type, "pbgid": pbgid,
                    "icon": _text(entity.get("icon"), 500), "quantity": quantity,
                    "is_villager": is_villager, "name_known": bool(entity), "source": "build_order[]." + source_field,
                    "confidence": "reported_event", "killer_profile_id": None})
                if is_villager:
                    if source_field == "finished":
                        villager_changes[timestamp]["initial" if timestamp == 0 else "born"] += quantity
                    elif source_field == "destroyed":
                        villager_changes[timestamp]["lost"] += quantity
    # 地标分析中时代字段是明确值，可精确补充中文时代标签。
    analysis = raw.get("analysis") if isinstance(raw.get("analysis"), dict) else {}
    for landmark in analysis.get("landmarks", []) if isinstance(analysis.get("landmarks"), list) else []:
        if not isinstance(landmark, dict):
            continue
        age, timestamp = _integer(landmark.get("new_age")), _number(landmark.get("game_time"))
        if age not in (2, 3, 4) or timestamp is None or timestamp < 0:
            continue
        pbgid = _integer(landmark.get("pbgid"))
        entity = catalog.get(str(pbgid), catalog.get(pbgid, {}))
        name = _text(entity.get("name")) or _text(landmark.get("name"))
        events.append({"time": timestamp, "time_label": time_label(timestamp), "profile_id": profile_id, "kind": "age",
            "label": "进入" + {2: "封建时代", 3: "城堡时代", 4: "帝王时代"}[age], "entity_name": name,
            "entity_type": "Age", "pbgid": pbgid, "icon": _text(entity.get("icon"), 500), "quantity": 1,
            "is_villager": False, "name_known": bool(entity), "source": "analysis.landmarks[].new_age",
            "confidence": "reported_event", "new_age": age, "killer_profile_id": None})
    # 若同一时刻已有明确地标时代信息，去掉含混的 Age 蓝图占位节点。
    age_times = {event["time"] for event in events if "new_age" in event}
    events = [event for event in events if not (event["entity_type"] == "Age" and event["time"] in age_times and "new_age" not in event)]
    events.sort(key=lambda event: (event["time"], event["kind"], event["pbgid"] or 0))
    worker_series = {}
    if villager_known:
        alive, produced, lost = 0, 0, 0
        values = {"alive": [], "produced": [], "lost": []}
        for timestamp, changes in sorted(villager_changes.items()):
            produced += changes["born"]
            lost += changes["lost"]
            alive += changes["initial"] + changes["born"] - changes["lost"]
            if alive < 0:
                villager_ambiguous = True
                warnings.append("村民损失大于已记录出现数，存活曲线未强行归零。")
            values["alive"].append([timestamp, alive if alive >= 0 else None])
            values["produced"].append([timestamp, produced])
            values["lost"].append([timestamp, lost])
        for key, label in (("alive", "事件计数村民存量"), ("produced", "新出现村民"), ("lost", "已记录村民损失")):
            worker_series["villagers_" + key] = {"label": label, "unit": "人", "kind": "step",
                "source": "build_order[].finished/destroyed + exact villager class", "points": values[key],
                "confidence": "partial" if villager_ambiguous else "event_count"}
    if villager_ambiguous:
        warnings.append("存在村民变形或不完整事件，村民存量仅供核对，不代表完整单位状态。")
    return events, worker_series, {"known": known, "unknown": unknown, "total": known + unknown}


def _totals(raw, key):
    value = raw.get(key) if isinstance(raw.get(key), dict) else {}
    return {resource: _number(value.get(resource)) for resource in (*RESOURCES, "oliveoil", "total")}


def _gap(own, other):
    """只比较双方都有的同一时刻采样，正数表示对方更高。"""
    left = dict(own.get("points", []))
    return [[timestamp, value - left[timestamp]] for timestamp, value in other.get("points", [])
            if timestamp in left and value is not None and left[timestamp] is not None]


def _divergence(points, seconds=60):
    """找到首个连续可见的速率落后区间，不声称该分钟决定胜负。"""
    start = previous = None
    first_gap = None
    for timestamp, gap in points:
        # 采样大间断不能视作连续落后。
        if gap <= 0 or (previous is not None and timestamp - previous > 40):
            start, first_gap = None, None
        if gap > 0:
            if start is None:
                start, first_gap = timestamp, gap
            if timestamp - start >= seconds:
                return {"start": start, "end": timestamp, "start_label": time_label(start), "end_label": time_label(timestamp),
                    "gap_start": first_gap, "gap_end": gap, "unit": "资源/分", "confidence": "sampled_observation"}
        previous = timestamp
    return None


def compare_players(focus, other):
    """经济对比只陈述差距和同一时间窗内的可查事件，不生成因果断言。"""
    result = {"profile_id": other["profile_id"], "name": other["name"],
              "relation": _relation(focus.get("team"), other.get("team"), focus["profile_id"] == other["profile_id"]),
              "income_gap": [], "economy_score_gap": [], "first_sustained_income_gap": None,
              "largest_income_gap": None, "strongest_income_swing": None, "evidence": [], "findings": []}
    own_series, other_series = focus["series"], other["series"]
    if "income_total" in own_series and "income_total" in other_series:
        points = _gap(own_series["income_total"], other_series["income_total"])
        result["income_gap"] = points
        result["first_sustained_income_gap"] = interval = _divergence(points)
        if points:
            timestamp, value = max(points, key=lambda point: point[1])
            result["largest_income_gap"] = {"time": timestamp, "time_label": time_label(timestamp), "value": value, "unit": "资源/分"}
            lookup = dict(points)
            swings = [(timestamp - 60, timestamp, gap - lookup[timestamp - 60]) for timestamp, gap in points if timestamp - 60 in lookup]
            if swings:
                start, end, change = max(swings, key=lambda row: row[2])
                result["strongest_income_swing"] = {"start": start, "end": end, "start_label": time_label(start),
                    "end_label": time_label(end), "gap_change": change, "gap_start": lookup[start],
                    "gap_end": lookup[end], "unit": "资源/分", "confidence": "sampled_observation"}
        if interval:
            result["findings"].append(f"{interval['start_label']}–{interval['end_label']} 的连续样本中，对方来源四资源采集速率高于你，区间末差 {interval['gap_end']:g} 资源/分。")
            for player in (focus, other):
                for event in player["events"]:
                    if interval["start"] - 60 <= event["time"] <= interval["end"] and (event["kind"] in {"upgrade", "age"} or event["is_villager"]):
                        result["evidence"].append({key: event[key] for key in ("time", "time_label", "profile_id", "kind", "label", "pbgid", "quantity", "source")})
            result["findings"].append("可结合区间前后的村民完成、损失和经济科技事件复核；尚无村民工作分配、空闲和受袭轨迹，不能据此断定差距原因。")
    if "score_economy" in own_series and "score_economy" in other_series:
        result["economy_score_gap"] = _gap(own_series["score_economy"], other_series["score_economy"])
    own_total, other_total = focus["totals"]["gathered"].get("total"), other["totals"]["gathered"].get("total")
    result["final_gathered_gap"] = other_total - own_total if own_total is not None and other_total is not None else None
    result["caution"] = "这是单名玩家对比。文明机制、团队分工和资源转移不同，不能只用村民数或总分判断操作优劣。"
    return result


def normalize_summary(summary, focus_profile_id=None, expected_game_id=None, entity_catalog=None):
    """把一个已获取的公开对局摘要转成无原始下载地址的轻量报告。"""
    if not isinstance(summary, dict):
        raise MatchDetailError("对局摘要格式无效。")
    game_id = _integer(summary.get("game_id"))
    expected = _integer(expected_game_id)
    if not game_id or (expected is not None and game_id != expected):
        raise MatchDetailError("对局摘要编号与所选对局不一致。")
    raw_players = summary.get("players")
    if not isinstance(raw_players, list) or not raw_players:
        raise MatchDetailError("摘要未提供玩家资料。")
    ids = [_integer(player.get("profile_id")) if isinstance(player, dict) else None for player in raw_players]
    if any(not identity for identity in ids) or len(ids) != len(set(ids)):
        raise MatchDetailError("摘要中的玩家编号缺失或重复，无法可靠匹配。")
    focus_id = _integer(focus_profile_id) if focus_profile_id is not None else ids[0]
    if focus_id not in ids:
        raise MatchDetailError("所选玩家不在这场对局中。")
    focus_raw = raw_players[ids.index(focus_id)]
    focus_team = _integer(focus_raw.get("team"))
    catalog = load_entity_catalog() if entity_catalog is None else entity_catalog
    catalog = catalog if isinstance(catalog, dict) else {}
    players, warnings, events = [], [], []
    for raw in raw_players:
        profile_id = _integer(raw.get("profile_id"))
        player_warnings = []
        player_events, worker_series, counts = _entity_events(raw, catalog, player_warnings)
        series = _player_series(raw, player_warnings)
        series.update(worker_series)
        player = {"profile_id": profile_id, "name": _text(raw.get("name")) or f"玩家 {profile_id}",
            "civilization": _text(raw.get("civilization")), "team": _integer(raw.get("team")),
            "relation": _relation(focus_team, _integer(raw.get("team")), profile_id == focus_id),
            "result": _text(raw.get("result"), 30), "apm": _number(raw.get("apm")),
            "totals": {"gathered": _totals(raw, "total_resources_gathered"), "spent": _totals(raw, "total_resources_spent")},
            "scores": {key: _number(raw.get("scores", {}).get(key)) for key in SCORES} if isinstance(raw.get("scores"), dict) else {key: None for key in SCORES},
            "series": series, "events": player_events, "entity_counts": counts,
            "warnings": list(dict.fromkeys(player_warnings)), "worker_jobs": None, "unit_positions": None,
            "selection_events": None, "kill_attribution": None}
        players.append(player)
        events.extend(player_events)
        warnings.extend(player_warnings)
    focus = next(player for player in players if player["profile_id"] == focus_id)
    report = {"schema_version": SCHEMA_VERSION, "kind": "aoe4world-match-detail", "game_id": game_id,
        "duration": _number(summary.get("duration")), "started_at": _integer(summary.get("started_at")),
        "map_id": _integer(summary.get("map_id")), "map_name": _text(summary.get("map_name")),
        "leaderboard": _text(summary.get("leaderboard")), "focus_profile_id": focus_id,
        "players": players, "events": sorted(events, key=lambda event: (event["time"], event["profile_id"], event["kind"])),
        "comparisons": [compare_players(focus, player) for player in players if player["relation"] == "opponent"],
        "coverage": {"source": "公开对局摘要", "source_summary_version": _integer(summary.get("summary_version")),
            "gaps": list(GAPS), "warnings": list(dict.fromkeys(warnings)),
            "has_resource_series": any("bank_total" in player["series"] for player in players),
            "has_event_timeline": bool(events), "has_worker_jobs": False, "has_selection": False,
            "has_unit_paths": False, "has_kill_attribution": False,
            "causal_status": "只定位可观察的差距形成区间，不把相关事件宣称为原因或胜负决定点"}}
    return report


def estimate_worker_loss(events, profile_id, rate_per_worker=40, recovery_seconds=None, until=None, sample_seconds=20):
    """对已记录村民损失做反事实情景估算，不冒充实际少采集量。

    recovery_seconds（补回延迟）为 None 表示假设未补回；60 表示每名损失
    村民恰在 60 秒后恢复同等产能。后续正常生产不会自动当作补人。rate 是
    用户输入的每人每分有效产量，未对任何文明硬套倍率。
    """
    identity, rate = _integer(profile_id), _number(rate_per_worker)
    recovery = _number(recovery_seconds) if recovery_seconds is not None else None
    until_value, sample = _number(until), _number(sample_seconds)
    if not identity or rate is None or rate < 0 or (recovery_seconds is not None and (recovery is None or recovery < 0)) or sample is None or sample <= 0 or (until is not None and until_value is None):
        raise MatchDetailError("情景估算的玩家、产量或补回延迟无效。")
    losses = []
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict) or event.get("profile_id") != identity or event.get("kind") != "loss" or event.get("is_villager") is not True:
            continue
        timestamp, quantity = _number(event.get("time")), _integer(event.get("quantity"))
        if timestamp is not None and timestamp >= 0 and quantity and (until_value is None or timestamp <= until_value):
            losses.append({"time": timestamp, "time_label": time_label(timestamp), "quantity": quantity,
                           "pbgid": _integer(event.get("pbgid")), "source": _text(event.get("source"))})
    losses.sort(key=lambda item: item["time"])
    if until_value is None:
        until_value = max((item["time"] for item in losses), default=0)
    if until_value < 0:
        raise MatchDetailError("情景估算的结束时间无效。")
    timestamps = {0, until_value}
    timestamps.update(item["time"] for item in losses)
    if recovery is not None:
        timestamps.update(item["time"] + recovery for item in losses if item["time"] + recovery <= until_value)
    # 采样仅用于绘图；公式始终按实际经过秒数计算，不累计取整误差。
    timestamps.update(index * sample for index in range(int(until_value // sample) + 1))
    points = []
    for timestamp in sorted(timestamps):
        total = sum(min(max(0, timestamp - item["time"]), recovery if recovery is not None else until_value) * item["quantity"] * rate / 60 for item in losses)
        points.append([timestamp, round(total, 4)])
    return {"kind": "worker-loss-scenario", "confidence": "scenario", "profile_id": identity,
        "label": "村民损失产能情景估算", "unit": "资源（估算）", "rate_per_worker": rate,
        "recovery_seconds": recovery, "until": until_value, "losses": losses, "points": points,
        "estimated_loss": points[-1][1] if points else 0,
        "assumptions": [f"假设每名村民每分有效采集 {rate:g} 资源", "假设未恢复损失产能" if recovery is None else f"假设每名损失村民在 {recovery:g} 秒后恢复同等产能",
            "不把后续正常生产自动当作补人；未包含步行、空闲、文明加成、科技及转移采集影响"],
        "caution": "这是所选参数下的反事实情景，不是录像直接测得的真实少采集量；损失来源也未给出击杀方。"}
