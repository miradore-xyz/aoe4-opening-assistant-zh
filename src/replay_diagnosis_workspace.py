"""统一复盘工作区：先显示有证据的待回看片段，再按需展开原始记录。

workspace=复盘工作区；focusRequested=用户明确要求定位证据；后台只交付纯字典。
这里不解析录像、不联网、不生成单位运动，也不把候选交战命名为已确认错误。
"""
from __future__ import annotations

import math
import os
from functools import lru_cache
from pathlib import Path
from threading import Thread

from PyQt5.QtCore import QObject, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontDatabase, QPainter, QValidator
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                            QSizePolicy, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from app_fonts import make_ui_font
from choice_controls import ClickComboBox
from player_labels import player_labels
from replay_diagnosis import build_replay_diagnosis
from visual import line_icon


_STYLE = """
QWidget#DiagnosisWorkspace {background:#0F0F11;color:#e7e7ed;}
QWidget#DiagnosisWorkspace QLabel, QWidget#DiagnosisWorkspace QPushButton,
QWidget#DiagnosisWorkspace QComboBox, QWidget#DiagnosisWorkspace QSpinBox {
font-family:'Microsoft YaHei UI','Segoe UI';font-size:13px;font-style:normal;}
QWidget#DiagnosisWorkspace QLabel#WorkspaceHeading {font-family:'Smiley Sans';font-size:20px;color:#f1f1f5;}
QWidget#DiagnosisWorkspace QLabel#DiagnosisTitle {font-family:'Smiley Sans';font-size:16px;color:#f0f0f4;}
QWidget#DiagnosisWorkspace QLabel#DiagnosisMuted {color:#999ba8;font-size:12px;}
QWidget#DiagnosisWorkspace QLabel#DiagnosisBadge {color:#c3c9d4;font-size:11px;}
QFrame#DiagnosisCard {background:#161619;border:1px solid #303038;border-radius:12px;}
QFrame#DiagnosisCard[current="true"] {border-color:#7c8f9d;}
QFrame#DiagnosisCard[evidence_selected="true"] {border-color:#c5d7e5;}
QPushButton#DiagnosisAction {background:#25252d;border:1px solid #44444f;border-radius:7px;padding:6px 10px;color:#e1e3e9;}
QPushButton#DiagnosisAction:hover {background:#30313a;}
QPushButton#DiagnosisEvidence {background:transparent;border:1px solid #34343d;border-radius:7px;padding:6px 9px;color:#b2b7c3;}
QPushButton:focus {border-color:#a3a8b8;}
QSpinBox {background:#1c1c22;border:1px solid #363640;border-radius:6px;padding:5px 7px;}
QTabWidget::pane {border:0;background:transparent;}
QTabBar::tab {background:transparent;color:#9799a5;padding:10px 7px;border-bottom:2px solid transparent;font-family:'Microsoft YaHei UI';font-size:12px;}
QTabBar::tab:selected {color:#eeeeF3;border-bottom-color:#adb7c6;}
QScrollArea {border:0;background:transparent;}
QScrollBar:vertical {background:transparent;width:6px;}
QScrollBar::handle:vertical {background:#41414b;min-height:26px;border-radius:3px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
QSplitter::handle {background:#25252c;width:1px;}
"""


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _clock(value):
    value = max(0, int(value)) if _finite(value) else 0
    return f"{value // 60:02d}:{value % 60:02d}"


def _number(value):
    return f"{value:,.0f}" if _finite(value) else "未提供"


@lru_cache(maxsize=1)
def _body_font():
    """正文用系统正体；离屏插件不枚举系统字体时只在当前进程登记已有微软雅黑。"""
    if "Microsoft YaHei UI" not in QFontDatabase().families():
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
        if font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(13)
    font.setItalic(False)
    return font


def _text(value, role=None, wrap=True):
    label = QLabel(str(value or ""))
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(wrap)
    label.setMinimumWidth(0)
    label.setFont(_body_font())
    if role:
        label.setObjectName(role)
    if not wrap:
        label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
    return label


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().hide()
            item.widget().deleteLater()


def _range(row):
    values = row.get("time_range") or []
    return values if len(values) == 2 and all(_finite(value) for value in values) else [0, 0]


def _players(row):
    """兼容旧候选时只沿明确原事件取参与玩家，不按队伍或同名猜身份。"""
    values = row.get("players") or []
    explicit = {value.get("player_id", value.get("id")) if isinstance(value, dict) else value for value in values}
    for event in row.get("events") or row.get("evidence") or []:
        for key in ("player_id", "target_player_id", "attacker_player_id"):
            if type(event.get(key)) is int:
                explicit.add(event[key])
    return sorted(value for value in explicit if type(value) is int)


def _position(row):
    region = row.get("region")
    candidates = [row.get("position"), region.get("center") if isinstance(region, dict) else None]
    for candidate in candidates:
        if isinstance(candidate, dict) and _finite(candidate.get("x")) and _finite(candidate.get("y")):
            return dict(candidate)
    return None


def _impact_text(item):
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and item.get("kind") == "recorded_losses":
        return (f"此候选双方记录 {_number(item.get('count'))} 个目标损失，其中军事单位 {_number(item.get('military'))}、"
                f"工人 {_number(item.get('workers'))}；可避免程度尚未判定。")
    return item.get("description") or item.get("label") if isinstance(item, dict) else None


def _reference_text(reference):
    if not isinstance(reference, dict):
        return str(reference) if reference else None
    conditions = reference.get("conditions") or {}
    if conditions:
        return (f"分组标准：固定首点半径 {_number(conditions.get('radius'))} 原始坐标单位，"
                f"相邻损失间隔不超过 {_number(conditions.get('gap_seconds'))} 秒，"
                f"片段跨度不超过 {_number(conditions.get('max_span_seconds'))} 秒；不是操作错误判据。")
    return reference.get("description") or reference.get("label")


def _record_for_player(record, battle, selected):
    """只取明确属于当前玩家的训练项；保留全局候选双方事实及稳定身份。"""
    if selected is None or record.get("rule_id", record.get("type")) != "battle_candidates":
        return record
    output = dict(record)
    source = battle or record
    recommendations = source.get("recommendations_by_player") or {}
    goals = source.get("training_goals_by_player") or {}
    personal = recommendations.get(selected, recommendations.get(str(selected)))
    goal = goals.get(selected, goals.get(str(selected)))
    own_losses = sum(row.get("count") or 0 for side in source.get("losses") or []
                     for row in side.get("by_player") or [] if row.get("player_id") == selected)
    output["recommendations"] = personal or ["该玩家本候选未记损失，未生成个人训练项。" if own_losses == 0
                                             else "该玩家的个人训练建议尚未生成。"]
    output["training_goal"] = output["train_goal"] = goal or {}
    return output


class _SecondsSpinBox(QSpinBox):
    """以分:秒显示原始秒数，不压缩或拉伸回放时钟。"""
    def textFromValue(self, value):
        return _clock(value)

    def valueFromText(self, text):
        try:
            parts = text.strip().split(":")
            return int(parts[0]) * 60 + int(parts[1]) if len(parts) == 2 else int(text)
        except ValueError:
            return self.value()

    def validate(self, text, position):
        if text == "" or all(character.isdigit() or character == ":" for character in text) and text.count(":") <= 1:
            return QValidator.Acceptable, text, position
        return QValidator.Invalid, text, position


class _DiagnosisBridge(QObject):
    completed = pyqtSignal(object)


class _LossComparison(QWidget):
    """候选内同尺度的已记录目标数；兵种分段只取后端明确的分玩家角色计数。"""
    def __init__(self, losses, labels, parent=None):
        super().__init__(parent)
        self.setFixedHeight(56)
        self.setMinimumWidth(0)
        self.setAccessibleName("双方已记录损失目标对比；不是参战总兵力")
        self.rows = []
        for loss in losses:
            players = loss.get("by_player") or []
            names = [labels.get(row.get("player_id"), row.get("name") or "") for row in players]
            names = [name for name in names if name]
            roles = {}
            for player in players:
                for role, count in (player.get("roles") or {}).items():
                    if _finite(count) and count >= 0:
                        roles[role] = roles.get(role, 0) + count
            count = sum(player.get("count") or 0 for player in players) if players else loss.get("recorded_loss_count")
            military, workers = roles.get("military", 0), roles.get("worker", 0)
            self.rows.append({"name": "、".join(names) or f"队伍 {loss.get('team')}",
                              "count": count, "military": military, "workers": workers,
                              "other": max(0, (count or 0)-military-workers), "cost": loss.get("resource_loss")})
        self.maximum = max([1] + [row["count"] for row in self.rows if _finite(row["count"])])
        self.setAccessibleDescription("；".join(f"{row['name']}，已记录{row['count']}个目标，军事{row['military']}，工人{row['workers']}"
                                               for row in self.rows))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        font = QFont(_body_font())
        font.setPixelSize(11)
        painter.setFont(font)
        palette = (("军事", "#8daabf"), ("工人", "#d2b385"), ("未分类", "#7e818c"))
        for index, (label, color) in enumerate(palette):
            x = index*60
            painter.fillRect(QRectF(x, 1, 8, 8), QColor(color))
            painter.setPen(QColor("#9da5b3"))
            painter.drawText(QRectF(x+12, -3, 46, 16), Qt.AlignVCenter, label)
        painter.drawText(QRectF(self.width()-76, -3, 76, 16), Qt.AlignRight | Qt.AlignVCenter, "名义资源")
        left, right = 78, max(112, self.width()-102)
        width = max(1, right-left)
        for index, row in enumerate(self.rows):
            y = 16 + index*20
            painter.setPen(QColor("#bec5d0"))
            name = painter.fontMetrics().elidedText(row["name"], Qt.ElideRight, left-7)
            painter.drawText(QRectF(0, y, left-6, 18), Qt.AlignVCenter, name)
            painter.fillRect(QRectF(left, y+5, width, 7), QColor("#282b32"))
            x = float(left)
            for key, (_, color) in zip(("military", "workers", "other"), palette):
                span = width * row[key] / self.maximum
                if span:
                    painter.fillRect(QRectF(x, y+5, span, 7), QColor(color))
                    x += span
            painter.setPen(QColor("#dae1e9"))
            painter.drawText(QRectF(right+5, y, 28, 18), Qt.AlignRight | Qt.AlignVCenter,
                             _number(row["count"]) if _finite(row["count"]) else "—")
            painter.setPen(QColor("#a5adba"))
            painter.drawText(QRectF(self.width()-67, y, 67, 18), Qt.AlignRight | Qt.AlignVCenter,
                             _number(row["cost"]) if _finite(row["cost"]) else "未齐")
        painter.end()


class _DiagnosisCard(QFrame):
    focusRequested = pyqtSignal(object)

    def __init__(self, record, labels, context_key, *, battle=None, parent=None):
        super().__init__(parent)
        self.setObjectName("DiagnosisCard")
        self.record = record
        self.battle = battle or record
        self.identity = record.get("id") or self.battle.get("id")
        self.time_range = _range(record if record.get("time_range") else self.battle)
        self.setProperty("current", False)
        self._active = False
        box = QVBoxLayout(self)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(8)
        heading = _text(record.get("title") or "优先回看片段", "DiagnosisTitle")
        box.addWidget(heading)
        names = [labels.get(identity, f"玩家 {identity}") for identity in _players(self.battle)]
        line = f"{_clock(self.time_range[0])}–{_clock(self.time_range[1])}"
        box.addWidget(_text(line + (" · " + "、".join(names[:2]) + (f" 等{len(names)}人" if len(names)>2 else "") if names else ""), "DiagnosisMuted"))
        losses = self.battle.get("losses") or []
        facts = record.get("facts") or self.battle.get("facts") or []
        if losses:
            recorded = sum(loss.get("recorded_loss_count", loss.get("count")) or 0 for loss in losses)
            box.addWidget(_text(f"该区域记录到 {_number(recorded)} 个明确敌方来源的损失目标。"))
        elif facts:
            box.addWidget(_text(facts[0]))
        self.loss_comparison = None
        if losses:
            self.loss_comparison = _LossComparison(losses, labels)
            box.addWidget(self.loss_comparison)
        if losses:
            unknown = sum(row.get("nearby_unattributed_loss_count") or 0 for row in losses)
            box.addWidget(_text(f"附近另有 {_number(unknown)} 个归属未明目标，未计入上述数量。" if unknown else
                                "仅含已核敌方来源损失；名义成本不等于净损失。", "DiagnosisMuted"))
        confidence = record.get("confidence") or {}
        severity = record.get("severity") or {}
        confidence_level = confidence.get("level") if isinstance(confidence, dict) else None
        severity_level = severity.get("level") if isinstance(severity, dict) else None
        confidence_name = {"high":"高", "medium":"中", "low":"低", "unknown":"未判定"}.get(confidence_level, "未判定")
        severity_name = {"high":"高", "medium":"中", "low":"低", "review":"待回看", "unknown":"未判定"}.get(severity_level, "待回看")
        box.addWidget(_text(f"证据可信度：{confidence_name}　影响程度：{severity_name}", "DiagnosisBadge"))
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.focus_button = QPushButton("定位片段起点")
        self.focus_button.setObjectName("DiagnosisAction")
        self.focus_button.setIcon(line_icon("search", "#d7dce7"))
        self.focus_button.setAccessibleName(f"定位{line}片段起点并暂停")
        payload = {"id": self.identity, "context_key": context_key, "time_range": list(self.time_range),
                   "position": _position(self.battle), "region": self.battle.get("region"),
                   "evidence": record.get("evidence") or self.battle.get("evidence") or [],
                   "events": self.battle.get("events") or [],
                   "players": _players(self.battle), "teams": self.battle.get("teams") or record.get("teams") or []}
        self.focus_button.clicked.connect(lambda checked=False: self.focusRequested.emit(dict(payload)))
        actions.addWidget(self.focus_button)
        self.expand_button = QPushButton("证据与建议")
        self.expand_button.setObjectName("DiagnosisEvidence")
        self.expand_button.setCheckable(True)
        self.expand_button.setAccessibleName("展开该片段的事实、推断、缺口与训练建议")
        self.expand_button.toggled.connect(self._toggle_evidence)
        actions.addWidget(self.expand_button)
        actions.addStretch()
        box.addLayout(actions)
        self.detail = QWidget()
        self.detail_layout = QVBoxLayout(self.detail)
        self.detail_layout.setContentsMargins(0, 3, 0, 0)
        self.detail_layout.setSpacing(8)
        self._detail_built = False
        self._labels = labels
        box.addWidget(self.detail)
        self.detail.hide()

    def _toggle_evidence(self, opened):
        if opened and not self._detail_built:
            self._detail_built = True
            row = self.record
            def section(title, values):
                values = list(dict.fromkeys(str(value) for value in values if value))
                if values:
                    self.detail_layout.addWidget(_text(title, "DiagnosisBadge"))
                    self.detail_layout.addWidget(_text("\n".join(values), "DiagnosisMuted"))
            section("已记录事实", row.get("facts") or self.battle.get("facts") or [])
            section("推断 · 仍需核对", [row.get("inference") or self.battle.get("inference")])
            confidence = row.get("confidence") or {}
            severity = row.get("severity") or {}
            section("可信度与影响依据", [confidence.get("reason") if isinstance(confidence, dict) else None,
                                      severity.get("reason") if isinstance(severity, dict) else None,
                                      *[_impact_text(item) for item in row.get("impacts") or []]])
            reference = row.get("reference")
            if reference:
                section("参考条件", [_reference_text(reference)])
            section("还不能判断", row.get("limitations") or row.get("unknowns") or self.battle.get("unknowns") or [])
            recommendations = row.get("recommendations") or []
            goal = row.get("training_goal") or row.get("train_goal") or {}
            action = goal.get("action") if isinstance(goal, dict) else goal
            section("下一局建议", [*recommendations, action])
            if isinstance(goal, dict):
                section("训练复测", [goal.get("condition"), goal.get("target") or goal.get("measurement")])
            evidence = row.get("evidence") or self.battle.get("evidence") or []
            self.detail_layout.addWidget(_text(f"原始证据 · {len(evidence)}条", "DiagnosisBadge"))
            self._evidence = evidence
            self._evidence_shown = 0
            self._evidence_widgets = []
            self._more_evidence = QPushButton("继续查看证据")
            self._more_evidence.setObjectName("DiagnosisEvidence")
            self._more_evidence.clicked.connect(self._append_evidence)
            self.detail_layout.addWidget(self._more_evidence)
            self._append_evidence()
        self.detail.setVisible(opened)

    def _append_evidence(self):
        stop = min(len(self._evidence), self._evidence_shown + 12)
        for item in self._evidence[self._evidence_shown:stop]:
            caption = (f"{_clock(item.get('time'))} · {self._labels.get(item.get('player_id'), '玩家未提供')} · "
                       f"实体 {item.get('entity_id', item.get('target_entity_id', '未提供'))}")
            label = _text(caption, "DiagnosisMuted")
            self._evidence_widgets.append((item, label))
            self.detail_layout.insertWidget(self.detail_layout.indexOf(self._more_evidence), label)
        self._evidence_shown = stop
        self._more_evidence.setText(f"继续查看证据 · {stop}/{len(self._evidence)}")
        self._more_evidence.setVisible(stop < len(self._evidence))

    def select_evidence(self, event):
        self.setProperty("evidence_selected", True)
        self.style().unpolish(self)
        self.style().polish(self)
        self.expand_button.setChecked(True)
        def same(item):
            # 诊断ID已限定所属片段，再按来源/原偏移/时刻匹配该片段证据，绝不匹配command表。
            return (item.get("source") == event.get("source") and item.get("offset") == event.get("offset")
                    and item.get("time") == event.get("time"))
        index = next((index for index, item in enumerate(self._evidence) if same(item)), None)
        if index is not None:
            while self._evidence_shown <= index:
                self._append_evidence()
            self._evidence_widgets[index][1].setStyleSheet("color:#e0e9f2;background:#28313b;border-radius:4px;padding:4px;")
            return self._evidence_widgets[index][1]
        return self

    def set_time(self, seconds):
        active = self.time_range[0] <= seconds <= self.time_range[1]
        if active != self._active:
            self._active = active
            self.setProperty("current", active)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()


class ReplayDiagnosisWorkspace(QWidget):
    """同窗诊断/证据与地图；时间筛选只筛固定候选，不重新分组或改候选ID。"""
    focusRequested = pyqtSignal(object)
    filtersChanged = pyqtSignal(object)
    advancedRequested = pyqtSignal()
    analysisReady = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DiagnosisWorkspace")
        self.setStyleSheet(_STYLE)
        self._context_key = None
        self._generation = 0
        self._cleaned = False
        self._report = None
        self._sources = None
        self._result = {}
        self._ready = False
        self._cards = []
        self._limits = {"economy": 6, "military": 6}
        self._time = 0
        self._shown_second = None
        self._orientation = None
        self._bridge = _DiagnosisBridge(self)
        self._bridge.completed.connect(self._completed)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 0)
        outer.setSpacing(12)
        heading = QHBoxLayout()
        heading.addWidget(_text("对局复盘", "WorkspaceHeading"), 1)
        self.quality = _text("打开真实对局后核验数据", "DiagnosisMuted")
        heading.addWidget(self.quality)
        self.clock = _text("当前 00:00", "DiagnosisMuted")
        heading.addWidget(self.clock)
        outer.addLayout(heading)
        controls = QHBoxLayout()
        controls.setSpacing(9)
        controls.addWidget(_text("玩家", "DiagnosisMuted", False))
        self.player_filter = ClickComboBox()
        self.player_filter.setMinimumWidth(0)
        self.player_filter.setMaximumWidth(220)
        self.player_filter.addItem("全部玩家", None)
        self.player_filter.setAccessibleName("统一复盘玩家筛选")
        self.player_filter.currentIndexChanged.connect(self._filter_changed)
        controls.addWidget(self.player_filter, 1)
        controls.addWidget(_text("时段", "DiagnosisMuted", False))
        self.range_start, self.range_end = _SecondsSpinBox(), _SecondsSpinBox()
        for widget, name in ((self.range_start, "复盘时段起点，分秒"), (self.range_end, "复盘时段终点，分秒")):
            widget.setRange(0, 1)
            widget.setFixedWidth(86)
            widget.setKeyboardTracking(False)
            widget.setAccessibleName(name)
            widget.valueChanged.connect(self._filter_changed)
        controls.addWidget(self.range_start)
        controls.addWidget(_text("–", "DiagnosisMuted", False))
        controls.addWidget(self.range_end)
        self.reset_range = QPushButton("整场")
        self.reset_range.setObjectName("DiagnosisEvidence")
        self.reset_range.setFixedWidth(52)
        self.reset_range.clicked.connect(self._reset_range)
        controls.addWidget(self.reset_range)
        controls.addStretch(1)
        outer.addLayout(controls)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(330)
        self.tabs.setAccessibleName("对局复盘工作区分类")
        self._layouts = {}
        for key, title in (("overview", "诊断总览"), ("economy", "经济与生产"),
                           ("military", "军事与交战"), ("advanced", "高级探索")):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            body = QWidget()
            layout = QVBoxLayout(body)
            layout.setContentsMargins(0, 12, 10, 4)
            layout.setSpacing(12)
            layout.setAlignment(Qt.AlignTop)
            scroll.setWidget(body)
            self.tabs.addTab(scroll, title)
            self._layouts[key] = layout
        self.splitter.addWidget(self.tabs)
        self.map_host = QWidget()
        self.map_layout = QVBoxLayout(self.map_host)
        self.map_layout.setContentsMargins(0, 0, 0, 0)
        self.map_layout.setSpacing(0)
        self.map_host.setMinimumWidth(320)
        self.splitter.addWidget(self.map_host)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([370, 850])
        outer.addWidget(self.splitter, 1)
        self.exploration_button = QPushButton("打开观察性统计")
        self.exploration_button.setObjectName("DiagnosisEvidence")
        self.exploration_button.setAccessibleName("打开原有观察性统计，不能作为正式诊断")
        self.exploration_button.clicked.connect(self.advancedRequested)
        self._layouts["advanced"].addWidget(_text("原始记录与观察性统计用于人工核查；不等于已识别操作错误。", "DiagnosisMuted"))
        self._layouts["advanced"].addWidget(self.exploration_button)
        self.capability_button = QPushButton("本局数据能力与限制")
        self.capability_button.setObjectName("DiagnosisEvidence")
        self.capability_button.setCheckable(True)
        self.capability_button.toggled.connect(self._show_capabilities)
        self._layouts["advanced"].addWidget(self.capability_button)
        self.capability_body = QWidget()
        self.capability_layout = QVBoxLayout(self.capability_body)
        self.capability_layout.setContentsMargins(0, 0, 0, 0)
        self.capability_layout.setSpacing(10)
        self.capability_body.hide()
        self._capability_generation = None
        self._layouts["advanced"].addWidget(self.capability_body)
        self.tabs.currentChanged.connect(lambda index: self._render())
        self._render()

    def attach_map(self, widget):
        self.map_layout.addWidget(widget, 1)

    def attach_exploration(self, widget):
        self._layouts["advanced"].addWidget(widget)

    def set_context(self, report=None, sources=None):
        key = (id(report), id(sources))
        if key == self._context_key or self._cleaned:
            return
        same_report = report is self._report
        old_start, old_end = self.range_start.value(), self.range_end.value()
        self._context_key = key
        self._generation += 1
        self._report, self._sources = report, sources
        self._result, self._ready = {}, False
        self.player_filter.blockSignals(True)
        selected = self.player_filter.currentData() if same_report else None
        self.player_filter.clear()
        self.player_filter.addItem("全部玩家", None)
        labels = player_labels((report or {}).get("players") or [])
        for player, name in labels.items():
            self.player_filter.addItem(name, player)
        self.player_filter.setCurrentIndex(max(0, self.player_filter.findData(selected)))
        self.player_filter.blockSignals(False)
        duration = max(1, math.ceil(((report or {}).get("metadata") or {}).get("duration") or 1))
        for widget in (self.range_start, self.range_end):
            widget.blockSignals(True)
            widget.setRange(0, duration)
        self.range_start.setValue(min(old_start, duration) if same_report else 0)
        self.range_end.setValue(min(old_end, duration) if same_report else duration)
        for widget in (self.range_start, self.range_end):
            widget.blockSignals(False)
        self._render()
        if not isinstance(report, dict) or not isinstance(sources, dict):
            return
        generation, bridge = self._generation, self._bridge
        def run():
            try:
                result = build_replay_diagnosis(report, sources, context=sources.get("official_unit_context"))
                payload = (generation, result, None)
            except Exception as error:
                payload = (generation, {}, str(error))
            try:
                bridge.completed.emit(payload)
            except RuntimeError:
                pass
        Thread(target=run, daemon=True, name="ReplayDiagnosis").start()

    def _completed(self, payload):
        generation, result, error = payload
        if self._cleaned or generation != self._generation:
            return
        self._result = result
        self._ready = True
        if error:
            self._result = {"quality": {"status": "blocked", "gaps": ["诊断计算未完成：" + error]}}
        self._render()
        self.analysisReady.emit()

    def is_ready(self):
        return self._ready

    def _reset_range(self):
        self.range_start.blockSignals(True)
        self.range_end.blockSignals(True)
        self.range_start.setValue(0)
        self.range_end.setValue(self.range_end.maximum())
        self.range_start.blockSignals(False)
        self.range_end.blockSignals(False)
        self._filter_changed()

    def _filter_changed(self, *args):
        if self.range_end.value() < self.range_start.value():
            self.range_end.blockSignals(True)
            self.range_end.setValue(self.range_start.value())
            self.range_end.blockSignals(False)
        self._limits = {"economy": 6, "military": 6}
        self._render()
        self.filtersChanged.emit({"player_id": self.player_filter.currentData(),
                                  "time_range": [self.range_start.value(), self.range_end.value()]})

    def _visible(self, rows):
        player = self.player_filter.currentData()
        start, end = self.range_start.value(), self.range_end.value()
        return [row for row in rows if (player is None or player in _players(row))
                and _range(row)[1] >= start and _range(row)[0] <= end]

    def _render(self):
        self._cards = []
        for key in ("overview", "economy", "military"):
            _clear(self._layouts[key])
        quality = self._result.get("quality") or {}
        status = quality.get("status")
        self.quality.setText("数据核验：部分可用" if status == "partial" else "数据核验：已通过" if status == "ready"
                             else "数据不足，当前无法判定" if status == "blocked" else
                             "正在核验本局记录…" if self._report is not None else "打开真实对局后核验数据")
        inventory = self._result.get("capability_inventory") or self._result.get("capabilities") or []
        grouping = next((row for row in inventory if row.get("id") == "battle_grouping"), None)
        if grouping is not None:
            # 能力等级直接影响主页面状态；C类只显示候选，D类不包装成已完成诊断。
            self.quality.setText(self.quality.text() + " · 候选分组：" + str(grouping.get("label") or "当前无法判定"))
        labels = player_labels((self._report or {}).get("players") or [])
        findings = self._visible(self._result.get("findings") or [])
        by_id = {row.get("id"): row for row in findings}
        battles = self._visible(self._result.get("battles") or [])
        confirmed = [row for row in findings if row.get("kind") == "confirmed_issue"]
        if not self._ready:
            self._layouts["overview"].addWidget(_text("正在等待同局来源并核验可回看的片段。" if self._report else "请先打开一场真实对局。", "DiagnosisMuted"))
        else:
            summary = "数据不足，当前无法判定问题。" if status == "blocked" else (
                "本局已有可确认的问题。" if confirmed else "目前未发现可确认的问题。")
            self._layouts["overview"].addWidget(_text(summary, "DiagnosisTitle"))
            self._layouts["overview"].addWidget(_text("以下是优先回看的真实片段；候选交战不代表已认定操作错误。" if battles else
                                                      "当前筛选下没有可定位的交战候选。", "DiagnosisMuted"))
        def add_card(key, record, battle=None):
            if key != ("overview", "economy", "military", "advanced")[self.tabs.currentIndex()]:
                return  # 隐藏分类不创建卡片，更不预先展开全部证据。
            selected = self.player_filter.currentData()
            record = _record_for_player(record, battle, selected)
            card = _DiagnosisCard(record, labels, self._context_key, battle=battle)
            card.tab_key = key
            card.focusRequested.connect(self.focusRequested)
            self._layouts[key].addWidget(card)
            self._cards.append(card)
        for battle in battles[:3]:
            add_card("overview", by_id.get(battle.get("id"), battle), battle)
        if self._ready and not battles:
            gaps = quality.get("gaps") or []
            if gaps:
                self._layouts["overview"].addWidget(_text(gaps[0], "DiagnosisMuted"))
        economy = self._visible(self._result.get("observations") or [])
        if not economy:
            economy = [row for row in findings if row.get("rule_id", row.get("type")) != "battle_candidates"]
        self._layouts["economy"].addWidget(_text("经济与生产的待核片段", "DiagnosisTitle"))
        for row in economy[:self._limits["economy"]]:
            add_card("economy", row)
        if not economy:
            self._layouts["economy"].addWidget(_text("当前筛选下没有足够证据生成经济或生产诊断；原始采样保留在高级探索。", "DiagnosisMuted"))
        self._layouts["military"].addWidget(_text("军事与交战", "DiagnosisTitle"))
        self._layouts["military"].addWidget(_text(f"当前范围有 {len(battles)} 个候选片段，按原规则边界保留。", "DiagnosisMuted"))
        if len(economy) > self._limits["economy"]:
            more = QPushButton(f"继续查看 · {self._limits['economy']}/{len(economy)}")
            more.setObjectName("DiagnosisEvidence")
            more.clicked.connect(lambda: self._show_more("economy"))
            self._layouts["economy"].addWidget(more)
        for battle in battles[:self._limits["military"]]:
            add_card("military", by_id.get(battle.get("id"), battle), battle)
        if len(battles) > self._limits["military"]:
            more = QPushButton(f"继续查看 · {self._limits['military']}/{len(battles)}")
            more.setObjectName("DiagnosisEvidence")
            more.clicked.connect(lambda: self._show_more("military"))
            self._layouts["military"].addWidget(more)
        self.set_time(self._time)
        if self.capability_button.isChecked():
            self._show_capabilities(True)

    def _show_capabilities(self, opened):
        if opened and self._capability_generation != self._generation:
            _clear(self.capability_layout)
            for row in self._result.get("capability_inventory") or self._result.get("capabilities") or []:
                self.capability_layout.addWidget(_text(f"{row.get('title', '数据能力')} · {row.get('label', '当前无法判定')}", "DiagnosisBadge"))
                self.capability_layout.addWidget(_text(row.get("reason") or "当前缺少可靠数据。", "DiagnosisMuted"))
            self._capability_generation = self._generation
        self.capability_body.setVisible(opened)

    def _show_more(self, key):
        self._limits[key] += 6
        self._render()

    def set_time(self, seconds):
        if not _finite(seconds) or self._cleaned:
            return
        self._time = max(0, seconds)
        shown = int(seconds)
        if shown != self._shown_second:
            self._shown_second = shown
            self.clock.setText("当前 " + _clock(seconds))
        for card in self._cards:
            card.set_time(seconds)

    def select_evidence(self, event):
        """地图反选只高亮同一诊断及其证据，不再定位或改变时钟。"""
        if not isinstance(event, dict) or not event.get("diagnostic_id"):
            return False
        identity = event["diagnostic_id"]
        card = next((card for card in self._cards if card.identity == identity), None)
        if card is None:
            self.tabs.setCurrentIndex(2)
            card = next((card for card in self._cards if card.identity == identity), None)
        if card is None:
            return False
        target = card.select_evidence(event)
        self.tabs.currentWidget().ensureWidgetVisible(target, 0, 8)
        return True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        orientation = Qt.Horizontal if self.width() >= 840 else Qt.Vertical
        if orientation != self._orientation:
            self._orientation = orientation
            self.splitter.setOrientation(orientation)
            self.splitter.setSizes([370, max(400, self.width()-390)] if orientation == Qt.Horizontal else [420, 420])

    def cleanup(self):
        self._cleaned = True
        self._generation += 1
