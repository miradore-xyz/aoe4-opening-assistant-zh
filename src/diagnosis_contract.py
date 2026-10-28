"""复盘诊断公共数据约定：事实、推断、可信度和影响分别保存。"""
from __future__ import annotations

import hashlib
import json
import math

SCHEMA_VERSION = 2
CAPABILITY_LABELS = {"A": "直接记录", "B": "可靠计算", "C": "模型估算", "D": "当前无法判定"}
DIAGNOSIS_KINDS = {"confirmed_issue", "review_segment", "observation", "unavailable"}


def stable_id(game_id, rule_id, evidence_keys):
    """稳定身份使用对局、规则和原证据键；不随展示排序或玩家筛选变化。"""
    ordered = sorted({json.dumps(key, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                      for key in evidence_keys})
    raw = json.dumps([game_id, rule_id, ordered], ensure_ascii=False, separators=(",", ":"))
    return rule_id + "-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def capability(identity, grade, *, available, inputs=(), reason="", unit=None):
    """A至D为数据能力，不是玩家成绩，也不是问题的严重程度。"""
    if grade not in CAPABILITY_LABELS:
        raise ValueError("数据能力等级应为A、B、C或D。")
    if not available:
        grade = "D"
    return {"id": identity, "grade": grade, "label": CAPABILITY_LABELS[grade],
            "available": bool(available), "inputs": list(inputs), "reason": str(reason), "unit": unit}


def diagnostic_record(identity, rule_id, title, start, end, *, players=(), teams=(), entities=(),
                      region=None, facts=(), reference=None, gap=None, evidence=(),
                      confidence=None, severity=None, impacts=(), recommendations=(),
                      training_goal=None, limitations=(), kind="review_segment", rule_version="1",
                      metrics=(), series=()):
    """构建可直接序列化的诊断；来源不足时禁止填成已确认错误。"""
    if kind not in DIAGNOSIS_KINDS:
        raise ValueError("未知诊断状态。")
    if any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t)
           for t in (start, end)) or start < 0 or end < start:
        raise ValueError("诊断时间必须为同一原始时钟的有效秒数区间。")
    facts, evidence = list(facts), list(evidence)
    if kind != "unavailable" and (not facts or not evidence):
        raise ValueError("可显示诊断需要事实和证据引用。")
    confidence = dict(confidence or {"level": "unknown", "reason": "尚未完成证据核验。"})
    severity = dict(severity or {"level": "review", "reason": "用于排序待检查片段，未认定操作错误。"})
    if confidence.get("level") not in {"high", "medium", "low", "unknown"}:
        raise ValueError("可信度等级无效。")
    if severity.get("level") not in {"high", "medium", "low", "review", "unknown"}:
        raise ValueError("严重程度等级无效。")
    if kind == "confirmed_issue" and (not reference or gap is None or confidence["level"] != "high"):
        raise ValueError("已确认问题需要明确参考、差距和高可信证据。")
    return {"schema_version": SCHEMA_VERSION, "id": str(identity), "type": str(rule_id),
            "rule_id": str(rule_id), "rule_version": str(rule_version), "kind": kind,
            "players": list(players), "teams": list(teams), "entities": list(entities),
            "time_range": [start, end], "region": region, "title": str(title),
            "facts": facts, "reference": reference, "gap": gap, "evidence": evidence,
            "confidence": confidence, "severity": severity, "impacts": list(impacts),
            "recommendations": list(recommendations), "training_goal": training_goal,
            "limitations": list(limitations), "metrics": list(metrics), "series": list(series),
            "causal_claim": False}


def filter_diagnostics(findings, player_id=None, time_range=None):
    """共享筛选按区间相交，不改事件时刻，不把未给玩家的结果混入单人结果。"""
    output = []
    for row in findings:
        if player_id is not None and player_id not in row.get("players", []):
            continue
        start, end = row.get("time_range", [None, None])
        if start is None or end is None:
            continue
        if time_range is not None and (end < time_range[0] or start > time_range[1]):
            continue
        output.append(row)
    return output
