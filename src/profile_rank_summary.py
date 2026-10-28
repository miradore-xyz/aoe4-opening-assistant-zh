"""当前排位摘要，读取真实模式资料，不推算隐藏分。

profile=玩家资料，modes=模式字典，rating=排位积分，rank_level=段位代码。
仅使用当前 rm_solo（单排）/rm_team（组排）；rank（排行榜名次）独立显示，
不参与段位或积分推算；不读取previous_seasons（历史赛季）、mmr/_elo（匹配分）。
"""
from __future__ import annotations

import math

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from rank_display import RANK_THRESHOLDS, rank_badge
from visual import CARD_GAP


_RANK_LABELS = {tier: label for _, tier, label in RANK_THRESHOLDS}
_MODES = (("rm_solo", "单排", "solo"), ("rm_team", "组排", "team"))


def _integer(value):
    """保留明确的零；布尔、负数、非有限数、小数不冒充积分或赛季。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0 or int(value) != value:
        return None
    return int(value)


def profile_rank_rows(profile):
    """将当前玩家资料转成固定顺序两行；不修改资料、不发网络请求。

    段位必须由公开资料明确提供 rank_level；有积分却没有该字段时仍显示
    原积分及“无段位”，不从积分阈值、名次、匹配分或历史最高段位猜补。
    """
    modes = profile.get("modes") if isinstance(profile, dict) else None
    modes = modes if isinstance(modes, dict) else {}
    rows = []
    for mode, name, family in _MODES:
        values = modes.get(mode)
        values = values if isinstance(values, dict) else {}
        tier = values.get("rank_level")
        tier = tier if isinstance(tier, str) and tier in _RANK_LABELS else None
        points, season = _integer(values.get("rating")), _integer(values.get("season"))
        placement = _integer(values.get("rank"))
        placement = placement if placement is not None and placement>0 else None
        placement_text = f"排名 {placement}" if placement is not None else "排名未提供"
        label = _RANK_LABELS[tier] if tier else "无段位"
        points_text = f"{points} 分" if points is not None else "—"
        scope = f"第{season}赛季" if season else "当前模式资料"
        tooltip = f"{name} · {scope}\n{label}"
        tooltip += f" · {points_text}" if points is not None else " · 未提供排位积分"
        tooltip += f"\n{placement_text}"
        tooltip += "\n依据公开玩家资料的段位与排位积分；不使用匹配分或历史赛季。"
        rows.append({"mode": mode, "name": name, "tier": tier, "label": label,
                     "points": points, "points_text": points_text, "season": season,
                     "icon_name": f"{family}_{tier}.png" if tier else None,
                     "leaderboard_rank":placement,"leaderboard_text":placement_text,
                     "available": tier is not None or points is not None, "tooltip": tooltip})
    return rows


def _label(text, *, size, color, weight=QFont.Normal):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    font = label.font()
    font.setPixelSize(size)
    font.setWeight(weight)
    label.setFont(font)
    label.setStyleSheet(f"background:transparent;border:0;padding:0;color:{color};font-size:{size}px;"
                       f"font-weight:{600 if weight >= QFont.DemiBold else 400};")
    return label


class ProfileRankSummary(QWidget):
    """当前段位组件；expanded（放大）用于走势图旁，缺失项仍显示明确状态。"""

    def __init__(self, profile=None, parent=None, *, expanded=False):
        super().__init__(parent)
        self.expanded = expanded
        self.badge_size = 52 if expanded else 28
        self.setObjectName("ProfileRankSummary")
        if not expanded:
            self.setMaximumWidth(244)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding if expanded else QSizePolicy.Maximum, QSizePolicy.Preferred)
        self.setStyleSheet("QWidget#ProfileRankSummary{background:transparent;border:0;}")
        self.rows = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.empty_label = _label("无段位", size=12, color="#9fa2ac")
        self.empty_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if not expanded:
            outer.addWidget(self.empty_label)
        else:
            self.empty_label.hide()
        self.details = QWidget(self)
        self.details.setObjectName("RankSummaryDetails")
        self.details.setStyleSheet("QWidget#RankSummaryDetails{background:transparent;border:0;}")
        self.rank_cards = []
        if expanded:
            # 两个独立卡片，中缝露出页面底色；外层不再画同色大卡或重复标题。
            stack = QVBoxLayout(self.details)
            stack.setContentsMargins(0, 0, 0, 0)
            stack.setSpacing(CARD_GAP)
        else:
            grid = QGridLayout(self.details)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(6)
            grid.setVerticalSpacing(2)
            grid.setColumnStretch(2, 1)
        self.badge_labels, self.mode_labels, self.rank_labels, self.points_labels = [], [], [], []
        self.leaderboard_labels = []
        for row_index, (_, name, _) in enumerate(_MODES):
            badge = QLabel(self.details)
            badge.setFixedSize(self.badge_size, self.badge_size)
            badge.setAlignment(Qt.AlignCenter)
            badge.setStyleSheet("background:transparent;border:0;padding:0;")
            mode = _label(name, size=12, color="#9fa2ac")
            rank = _label("无段位", size=16 if expanded else 12, color="#9fa2ac")
            points = _label("—", size=20 if expanded else 13, color="#e5e6eb", weight=QFont.DemiBold)
            placement = _label("排名未提供",size=11,color="#9fa2ac")
            points.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if expanded:
                panel = QFrame(self.details)
                panel.setObjectName("SoloRankCard" if row_index==0 else "TeamRankCard")
                panel.setStyleSheet(f"QFrame#{panel.objectName()}{{background:#161619;border:0;border-radius:12px;}}")
                panel.setMinimumWidth(0)
                panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                line = QHBoxLayout(panel)
                line.setContentsMargins(16, 12, 16, 12)
                line.setSpacing(12)
                text = QWidget(panel)
                text.setMinimumWidth(0)
                text.setStyleSheet("background:transparent;border:0;")
                labels = QVBoxLayout(text)
                labels.setContentsMargins(0, 0, 0, 0)
                labels.setSpacing(4)
                labels.addWidget(mode)
                labels.addWidget(rank)
                labels.addWidget(placement)
                rank.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                line.addWidget(badge)
                line.addWidget(text, 1)
                line.addWidget(points)
                stack.addWidget(panel, 1)
                self.rank_cards.append(panel)
            else:
                placement.hide()  # 紧凑旧API不增加标题行高度，右侧放大卡片才显示名次。
                for column, widget in enumerate((badge, mode, rank, points)):
                    grid.addWidget(widget, row_index, column)
            self.badge_labels.append(badge)
            self.mode_labels.append(mode)
            self.rank_labels.append(rank)
            self.points_labels.append(points)
            self.leaderboard_labels.append(placement)
        outer.addWidget(self.details, 1 if expanded else 0)
        # 无障碍描述承接来源说明；静态控件不再弹出悬停框或继承父级提示。
        for widget in [self, *self.findChildren(QWidget)]:
            widget.setToolTip("")
            widget.installEventFilter(self)
        self.set_profile(profile)

    def eventFilter(self, watched, event):
        if event.type()==QEvent.ToolTip:
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def set_profile(self, profile):
        """接收 AnalysisPage 已按精确玩家编号合并的原始 profile（玩家字典）。"""
        self.rows = profile_rank_rows(profile)
        available = any(row["available"] for row in self.rows)
        self.details.setVisible(available or self.expanded)
        self.empty_label.setVisible(not available and not self.expanded)
        self.empty_label.setAccessibleDescription("公开玩家资料未提供当前单排或组排段位。")
        names = []
        for index, row in enumerate(self.rows):
            badge = self.badge_labels[index]
            badge.clear()
            if row["icon_name"]:
                image = rank_badge(row["icon_name"])
                if not image.isNull():
                    badge.setPixmap(image.scaled(self.badge_size, self.badge_size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            if self.expanded:
                badge.setVisible(badge.pixmap() is not None and not badge.pixmap().isNull())
            self.rank_labels[index].setText(row["label"])
            self.rank_labels[index].setStyleSheet(
                f"background:transparent;border:0;padding:0;font-size:{16 if self.expanded else 12}px;"
                "font-weight:400;color:" + ("#e5e6eb;" if row["tier"] else "#9fa2ac;"))
            self.points_labels[index].setText(row["points_text"])
            self.leaderboard_labels[index].setText(row['leaderboard_text'])
            description = f"{row['name']}，{row['label']}，{row['points_text']}，{row['leaderboard_text']}"
            for widget in (badge, self.mode_labels[index], self.rank_labels[index], self.points_labels[index], self.leaderboard_labels[index]):
                widget.setToolTip("")
                widget.setAccessibleDescription(row["tooltip"])
                widget.setAccessibleName(description)
            if self.expanded:
                self.rank_cards[index].setAccessibleName(description)
                self.rank_cards[index].setAccessibleDescription(row["tooltip"])
                self.rank_cards[index].setToolTip("")
            names.append(description)
        self.setToolTip("")
        self.setAccessibleName("；".join(names) if available or self.expanded else "无段位")
        self.setAccessibleDescription("\n".join(row["tooltip"]for row in self.rows))
        self.updateGeometry()

    def set_overview(self, rows):
        """兼容 profile_overview 返回的总览行，只接真实排位积分。

        既有总览不保留 rank_level，因此可能只显示积分及“无段位”；
        标题接线应优先 set_profile，以保留公开资料中的明确段位和徽章。
        """
        modes = {}
        for row in rows if isinstance(rows, (list, tuple)) else ():
            if not isinstance(row, dict) or row.get("mode") not in {"rm_solo", "rm_team"}:
                continue
            if row.get("rating_label") != "排位积分":
                continue
            modes[row["mode"]] = {key: row.get(key) for key in ("rating", "rank_level", "season", "rank")}
        self.set_profile({"modes": modes})
