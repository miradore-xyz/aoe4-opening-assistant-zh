"""历史排位结果展示：rank=段位，rating=排位积分，change=该局积分变化。

AoE4 World 对局中的 rating 是赛前积分，rating_diff 是该局变化，
二者相加得到赛后积分。这里绝不使用 mmr（匹配分）或玩家当前总段位。
现代积分分段及原生徽章来源记录在 界面素材/段位/来源说明.txt。
"""
from __future__ import annotations

import math
import sys
from functools import lru_cache
from pathlib import Path


WIN = "#51c283"
LOSS = "#e3958b"
MUTED = "#9da6b0"

# AoE4 World 公开排位分布图：每个段位的下界；I/II/III 保留游戏标记。
RANK_THRESHOLDS = (
    (0, "bronze_1", "青铜 I"), (400, "bronze_2", "青铜 II"),
    (450, "bronze_3", "青铜 III"), (500, "silver_1", "白银 I"),
    (600, "silver_2", "白银 II"), (650, "silver_3", "白银 III"),
    (700, "gold_1", "黄金 I"), (800, "gold_2", "黄金 II"),
    (900, "gold_3", "黄金 III"), (1000, "platinum_1", "白金 I"),
    (1100, "platinum_2", "白金 II"), (1150, "platinum_3", "白金 III"),
    (1200, "diamond_1", "钻石 I"), (1300, "diamond_2", "钻石 II"),
    (1350, "diamond_3", "钻石 III"), (1400, "conqueror_1", "征服者 I"),
    (1500, "conqueror_2", "征服者 II"), (1600, "conqueror_3", "征服者 III"),
)
_RANKS = {tier: label for _, tier, label in RANK_THRESHOLDS}
_RANKED_MODES = {"rm_solo", "rm_team", "rm_1v1", "rm_2v2", "rm_3v3", "rm_4v4"}


def _integer(value):
    """缺失、布尔值、非有限数和小数都不冒充积分。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or int(value) != value:
        return None
    return int(value)


def _base_mode(value):
    mode = str(value or "")
    return mode[:-8] if mode.endswith("_console") else mode


def rank_for_points(points):
    """按现代排位积分返回 (tier 段位代码, label 中文名称)。"""
    value = _integer(points)
    if value is None or value < 0:
        return None, None
    for minimum, tier, label in reversed(RANK_THRESHOLDS):
        if value >= minimum:
            return tier, label
    return None, None


def ranked_result(row):
    """返回单场赛后段位资料；缺失时用破折号，不把未知积分补成零。

    row（对局字典）使用 mode/kind、leaderboard、season、rating、rating_diff。
    也接受明确标为赛后的 rating_after、rank_level_after；未标时不猜 rank_level。
    """
    mode = _base_mode(row.get("mode") or row.get("kind"))
    leaderboard = _base_mode(row.get("leaderboard"))
    ranked = mode in _RANKED_MODES and (not leaderboard or leaderboard in _RANKED_MODES)
    output = {"ranked": ranked, "tier": None, "label": "—", "points": None,
              "change": None, "change_text": "", "text": "—", "tooltip": "快速或非排位对局，没有排位段位。",
              "icon_name": None, "color": MUTED}
    if not ranked:
        return output
    output["tooltip"] = "该场公开记录没有提供完整的排位积分变化，无法确认赛后段位。"
    before = _integer(row.get("rating"))
    change = _integer(row.get("rating_diff"))
    after = _integer(row.get("rating_after"))
    if after is None and before is not None and before >= 0 and change is not None:
        after = before + change
    if after is None or after < 0:
        return output
    season = _integer(row.get("season"))
    explicit_rank = row.get("rank_level_after")
    # 前两个历史赛季不直接套用当前分段；只有明确赛后段位可作为依据。
    tier, label = (explicit_rank, _RANKS[explicit_rank]) if explicit_rank in _RANKS else (
        rank_for_points(after) if season is None or season >= 3 else (None, "积分"))
    if explicit_rank == "unranked":
        tier, label = None, "定位中"
    family = "solo" if mode in {"rm_solo", "rm_1v1"} else "team"
    change_text = f"{change:+d}" if change is not None else ""
    detail = f"{after} 分" + (f" · {change_text}" if change_text else "")
    tooltip = f"该局赛后：{label} · {after} 分"
    if change is not None:
        tooltip += f"\n该局积分变化：{change_text}"
    if before is not None and change is not None:
        tooltip += f"\n赛前 {before} → 赛后 {after}"
    tooltip += "\n依据该场公开排位积分，不使用匹配分或当前总段位。"
    output.update(tier=tier, label=label, points=after, change=change, change_text=change_text,
                  text=f"{label}\n{detail}", tooltip=tooltip,
                  icon_name=f"{family}_{tier}.png" if tier else None,
                  color=WIN if change is not None and change > 0 else LOSS if change is not None and change < 0 else MUTED)
    return output


def badge_path(icon_name):
    """只从随程序携带的徽章读取，不在绘制时联网。"""
    if icon_name not in {f"{family}_{tier}.png" for family in ("solo", "team") for tier in _RANKS}:
        return None
    base = Path(sys._MEIPASS) / "assets/design" if getattr(sys, "frozen", False) else Path(__file__).resolve().parent / "界面素材"
    path = base / "段位" / icon_name
    return path if path.is_file() else None


@lru_cache(maxsize=36)
def rank_badge(icon_name):
    """徽章缓存；QPixmap（图片）必须在 Qt 应用创建后调用。"""
    from PyQt5.QtGui import QPixmap
    path = badge_path(icon_name)
    return QPixmap(str(path)) if path is not None else QPixmap()
