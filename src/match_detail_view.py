"""赛后经济与事件复盘：中文图表、可追溯证据和明确区分的情景估算。

QPainter（Qt 原生绘图）绘制曲线，不增加网页、QtCharts 或绘图库依赖。
所有时间均为游戏经过时间；缺失采样断开显示，不填零或猜测玩家行为。
"""
from __future__ import annotations

import math
from bisect import bisect_left
from statistics import median

from app_fonts import make_ui_font, register_ui_fonts
from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem,
    QStyledItemDelegate, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from choice_controls import ClickComboBox, ClickSpinBox
from match_analysis import civilization_name, map_name
from match_detail import compare_players, estimate_worker_loss, time_label
from player_labels import player_labels


COLORS = ("#62d9b5", "#f2ae72")
EVENT_LABELS = {
    "initial": "初始拥有", "production": "单位完成/出现", "loss": "损失",
    "building": "建筑完成", "upgrade": "科技完成", "age": "时代变化",
    "transformed": "单位转化", "packed": "打包", "unpacked": "展开",
}
METRICS = (
    ("bank_total", "四资源库存"), ("income_total", "四资源采集速率"),
    ("villagers_alive", "村民数量 · 事件推算"),
    ("villagers_lost", "累计村民损失"),
    ("bank_food", "食物库存"), ("bank_wood", "木材库存"),
    ("bank_gold", "黄金库存"), ("bank_stone", "石料库存"),
    ("income_food", "食物采集速率"), ("income_wood", "木材采集速率"),
    ("income_gold", "黄金采集速率"), ("income_stone", "石料采集速率"),
    ("score_economy", "经济评分"), ("score_military", "军事评分"),
)


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _number(value):
    if not _finite(value):
        return "无采样"
    return f"{value:,.0f}" if value == int(value) else f"{value:,.1f}"


def _label(text="", role=None):
    label = QLabel(str(text))
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    if role:
        label.setObjectName(role)
    return label


class _AccountNameDelegate(QStyledItemDelegate):
    """玩家列中间省略昵称，保留末尾账号编号；完整名称仍在提示中。"""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.textElideMode = Qt.ElideMiddle


def _combo(name):
    combo = ClickComboBox()
    combo.setAccessibleName(name)
    combo.setMinimumWidth(0)
    combo.setSizeAdjustPolicy(ClickComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(4)
    return combo


def _button(text, callback):
    button = QPushButton(text)
    button.setCursor(Qt.PointingHandCursor)
    button.setMinimumHeight(28)
    button.clicked.connect(callback)
    return button


def _card(parent=None):
    frame = QFrame(parent)
    frame.setObjectName("DetailCard")
    frame.setMinimumWidth(0)
    return frame


def _event_label(event):
    """兼容旧报告文字；finished（完成/出现）不是严格的付费生产事件。"""
    label = str(event.get("label") or EVENT_LABELS.get(event.get("kind"), "事件"))
    if event.get("kind") == "production" and label.startswith("单位完成 "):
        label = "单位完成/出现 " + label[len("单位完成 "):]
    return label


def line_segments(points):
    """将缺失点分开；没有采样的区间不会出现看似实测的连续线。"""
    segments, current = [], []
    for point in points or []:
        if len(point) >= 2 and _finite(point[0]) and _finite(point[1]):
            current.append((float(point[0]), float(point[1])))
        elif current:
            segments.append(current)
            current = []
    if current:
        segments.append(current)
    return segments


def build_review_windows(primary, other, limit=3):
    """从完整共同采样中选非重叠窗口；缺点和采样间断不能跨过去。

    正差表示对比玩家的来源四资源速率更高；差值扩大只是复核线索，
    不表示这个时段决定胜负。先选增长最大的窗口，另补首个持续正差时段。
    """
    mappings, cadences = [], []
    for player in (primary, other):
        points = player.get("series", {}).get("income_total", {}).get("points", [])
        mapping = {float(point[0]): point[1] for point in points
                   if len(point) >= 2 and _finite(point[0])}
        mappings.append(mapping)
        times = sorted(mapping)
        intervals = [right - left for left, right in zip(times, times[1:]) if right > left]
        if intervals:
            cadences.append(median(intervals))
    if len(cadences) < 2 or not all(mappings):
        return []
    cadence = min(cadences)
    all_times = sorted(set(mappings[0]) | set(mappings[1]))
    valid = {time for time in all_times if all(_finite(mapping.get(time)) for mapping in mappings)}
    gaps = {time: mappings[1][time] - mappings[0][time] for time in valid}
    candidates = []
    for start in sorted(valid):
        end = start + 60
        if end not in valid:
            continue
        times = [time for time in all_times if start <= time <= end]
        if (not all(time in valid for time in times)
                or any(right - left > cadence * 1.01 for left, right in zip(times, times[1:]))):
            continue
        candidates.append({"start": start, "end": end, "start_label": time_label(start),
                           "end_label": time_label(end), "gap_start": gaps[start],
                           "gap_end": gaps[end], "gap_change": gaps[end] - gaps[start],
                           "unit": "资源/分", "kind": "swing", "times": times})
    chosen = []
    for window in sorted(candidates, key=lambda item: (-item["gap_change"], item["start"])):
        if window["gap_change"] <= 0:
            continue
        if all(window["end"] <= previous["start"] or window["start"] >= previous["end"] for previous in chosen):
            chosen.append(window)
        if len(chosen) >= limit:
            break
    first = next((window for window in candidates if all(gaps[time] > 0 for time in window["times"])), None)
    if first and not any(window["start"] == first["start"] and window["end"] == first["end"] for window in chosen):
        first = dict(first, kind="first_gap")
        first.pop("gap_change")
        chosen.append(first)
    return [{key: value for key, value in window.items() if key != "times"} for window in chosen]


def resource_gap_changes(primary, other, window):
    """拆分窗口两端的速率差变化；任何一端缺失都保留未知，不补零。"""
    result = {}
    start, end = window.get("start"), window.get("end")
    for resource in ("food", "wood", "gold", "stone"):
        values = []
        for player in (primary, other):
            series = player.get("series", {}).get("income_" + resource, {})
            points = dict(series.get("points", [])) if series.get("kind") != "unverified" else {}
            values.extend((points.get(start), points.get(end)))
        result[resource] = (values[3] - values[1]) - (values[2] - values[0]) if all(_finite(value) for value in values) else None
    return result


class DetailChart(QWidget):
    """可悬停、点击和键盘定位的折线/每分钟事件柱状图。"""

    timeSelected = pyqtSignal(float)

    def __init__(self, parent=None, *, height=236):
        super().__init__(parent)
        self.lines = []
        self.duration = 1.0
        self.unit = ""
        self.mode = "line"
        self.selected_time = None
        self.hover_time = None
        self.hover_details = []
        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("对局数据图表，可用左右方向键选择采样时刻")

    def set_data(self, lines, duration, unit="", mode="line"):
        self.lines = list(lines or [])
        self.duration = max(1.0, float(duration)) if _finite(duration) else 1.0
        self.unit, self.mode = str(unit), mode
        self.hover_time, self.hover_details = None, []
        self.update()

    def plot_rect(self):
        return QRectF(54, 38, max(1, self.width() - 72), max(1, self.height() - 72))

    def set_selected_time(self, seconds):
        self.selected_time = min(self.duration, max(0, float(seconds))) if _finite(seconds) else None
        self.update()

    def _times(self):
        return sorted({float(point[0]) for line in self.lines for point in line.get("points", [])
                       if len(point) >= 2 and _finite(point[0])})

    def _nearest_time(self, seconds):
        times = self._times()
        if not times:
            return seconds
        if self.mode == "bars":
            return float(int(seconds // 60) * 60)
        index = bisect_left(times, seconds)
        return min(times[max(0, index - 1):index + 1], key=lambda time: abs(time - seconds))

    def samples_at(self, seconds):
        """实测采样须同刻；事件阶梯只延续最近一条已记录状态，不跨越缺失点。"""
        result = []
        for line in self.lines:
            points = line.get("points", [])
            if line.get("kind") == "step":
                before = [point for point in points if len(point) >= 2 and _finite(point[0]) and point[0] <= seconds]
                value = before[-1][1] if before else None
            else:
                value = next((point[1] for point in points
                              if len(point) >= 2 and _finite(point[0]) and abs(point[0] - seconds) < .001), None)
            result.append({"label": str(line.get("label", "玩家")), "value": value,
                           "profile_id": line.get("profile_id"),
                           "color": line.get("color", COLORS[len(result) % 2])})
        return result

    def _read_position(self, point):
        rect = self.plot_rect()
        return max(0, min(self.duration, (point.x() - rect.left()) / rect.width() * self.duration))

    def mouseMoveEvent(self, event):
        if self.plot_rect().contains(event.pos()):
            self.hover_time = self._nearest_time(self._read_position(event.pos()))
            self.hover_details = self.samples_at(self.hover_time)
        else:
            self.hover_time, self.hover_details = None, []
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.hover_time, self.hover_details = None, []
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.plot_rect().contains(event.pos()):
            self.setFocus(Qt.MouseFocusReason)
            self.set_selected_time(self._nearest_time(self._read_position(event.pos())))
            self.timeSelected.emit(self.selected_time)
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Left, Qt.Key_Right):
            times = self._times()
            if times:
                current = self.selected_time if self.selected_time is not None else 0
                index = bisect_left(times, current)
                if event.key() == Qt.Key_Left:
                    index -= 1
                elif index < len(times) and times[index] <= current:
                    index += 1
                self.set_selected_time(times[max(0, min(len(times) - 1, index))])
                self.timeSelected.emit(self.selected_time)
                event.accept()
                return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(make_ui_font(12))
        painter.setPen(QPen(QColor("#B8B8C0" if self.hasFocus() else "#27272C"), 1))
        painter.setBrush(QColor("#161619"))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 10, 10)
        rect = self.plot_rect()
        values = [point[1] for line in self.lines for point in line.get("points", [])
                  if len(point) >= 2 and _finite(point[0]) and _finite(point[1])]
        legend_x = 16
        for index, line in enumerate(self.lines[:2]):
            color = QColor(line.get("color", COLORS[index]))
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(legend_x + 4, 19), 3.5, 3.5)
            painter.setPen(QColor("#e5e9ee"))
            available = max(75, int((self.width() - 48) / max(1, len(self.lines[:2])) - 30))
            # 中间省略保留同名账号的编号后缀，避免窄图例又只剩相同昵称。
            name = painter.fontMetrics().elidedText(str(line.get("label", "玩家")), Qt.ElideMiddle, available)
            painter.drawText(QRectF(legend_x + 15, 8, available, 22), Qt.AlignVCenter, name)
            legend_x += available + 30
        if not values:
            painter.setPen(QColor("#929299"))
            painter.drawText(rect, Qt.AlignCenter, "该指标暂无可绘制样本")
            return
        low = min(0.0, min(values))
        high = max(1.0, max(values))
        span = high - low
        magnitude = 10 ** math.floor(math.log10(max(span / 4, .001)))
        step = next((n * magnitude for n in (1, 2, 5, 10) if n * magnitude >= span / 4), magnitude * 10)
        low = math.floor(low / step) * step
        high = max(low + step, math.ceil(high / step) * step)
        def position(t, value):
            return QPointF(rect.left() + max(0, min(self.duration, t)) / self.duration * rect.width(),
                           rect.bottom() - (value - low) / (high - low) * rect.height())
        painter.setFont(make_ui_font(11))
        for i in range(5):
            value = low + (high - low) * i / 4
            y = position(0, value).y()
            painter.setPen(QPen(QColor("#303035"), 1, Qt.DashLine))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QColor("#929299"))
            label = f"{value / 1000:g}千" if abs(value) >= 1000 else f"{value:g}"
            painter.drawText(QRectF(4, y - 8, 43, 16), Qt.AlignRight | Qt.AlignVCenter, label)
            t = self.duration * i / 4
            x = position(t, 0).x()
            painter.drawText(QRectF(x - 24, rect.bottom() + 8, 48, 18), Qt.AlignCenter, time_label(t))
        painter.save()
        painter.setClipRect(rect.adjusted(-2, -2, 2, 2))
        for index, line in enumerate(self.lines):
            color = QColor(line.get("color", COLORS[index % 2]))
            painter.setPen(QPen(color, 2))
            painter.setBrush(Qt.NoBrush)
            if self.mode == "bars":
                groups = max(1, len(self.lines))
                width = max(2, min(25, 60 / self.duration * rect.width() * .70 / groups))
                painter.setPen(Qt.NoPen)
                painter.setBrush(color)
                for t, value in (point for point in line.get("points", []) if len(point) >= 2 and _finite(point[1])):
                    p = position(t, value)
                    x = p.x() + index * width + 1
                    baseline = position(t, 0).y()
                    painter.drawRoundedRect(QRectF(x, min(p.y(), baseline), width - 1,
                                                  max(1, abs(baseline - p.y()))), 2, 2)
            else:
                for segment in line_segments(line.get("points", [])):
                    path = QPainterPath(position(*segment[0]))
                    previous = segment[0]
                    for point in segment[1:]:
                        if line.get("kind") in ("event_count", "event_estimate", "step", "scenario"):
                            path.lineTo(position(point[0], previous[1]))
                        path.lineTo(position(*point))
                        previous = point
                    painter.drawPath(path)
                    if len(segment) == 1:
                        painter.setBrush(color)
                        painter.drawEllipse(position(*segment[0]), 2, 2)
                        painter.setBrush(Qt.NoBrush)
        active = self.hover_time if self.hover_time is not None else self.selected_time
        if active is not None:
            x = position(active, low).x()
            painter.setPen(QPen(QColor("#e4e9f0"), 1, Qt.DashLine))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            for sample in self.samples_at(active):
                if _finite(sample["value"]):
                    painter.setPen(QPen(QColor("#161619"), 2))
                    painter.setBrush(QColor(sample["color"]))
                    painter.drawEllipse(position(active, sample["value"]), 4, 4)
        painter.restore()
        if self.hover_time is not None:
            title = time_label(self.hover_time)
            if self.mode == "bars":
                title += " – " + time_label(min(self.duration, self.hover_time + 59))
            entries = [title] + [f"{item['label']}  {_number(item['value'])} {self.unit}" for item in self.hover_details]
            painter.setFont(make_ui_font(11))
            width = min(self.width() - 24, max(145, max(painter.fontMetrics().horizontalAdvance(text) for text in entries) + 24))
            box = QRectF(max(8, min(position(self.hover_time, high).x() + 12, self.width() - width - 8)),
                         38, width, 18 * len(entries) + 18)
            painter.setPen(QPen(QColor("#50505A"), 1))
            painter.setBrush(QColor("#242429"))
            painter.drawRoundedRect(box, 7, 7)
            for index, text in enumerate(entries):
                painter.setPen(QColor("#f1f4f8") if index == 0 else QColor(self.hover_details[index - 1]["color"]))
                painter.drawText(box.adjusted(12, 8 + index * 18, -10, 0), Qt.AlignTop, text)


class MatchDetailView(QWidget):
    """嵌入既有复盘滚动容器；不发请求、不修改游戏或原始摘要。"""

    timeSelected = pyqtSignal(float)
    PAGE_SIZE = 12

    def __init__(self, detail, parent=None):
        super().__init__(parent)
        self.detail = {}
        self.players = []
        self._player_names = {}
        self._primary_profile_id = None
        self.filtered_events = []
        self.page_index = 0
        self.scenario = {}
        self.setObjectName("MatchDetailView")
        self.setMinimumWidth(0)
        self.setStyleSheet("""
            QWidget#MatchDetailView {color:#F4F4F5; background:transparent; font-family:'Sen','Source Han Sans SC','Microsoft YaHei UI'; font-size:12px;}
            #MatchDetailView QLabel {background:transparent; border:0; color:#D6D6DB; font-size:12px;}
            #MatchDetailView QLabel#DetailMuted {color:#929299; font-size:11px;}
            #MatchDetailView QLabel#DetailTitle {font-size:14px; font-weight:600;}
            #MatchDetailView QFrame#DetailCard {background:#161619; border:0; border-radius:14px;}
            #MatchDetailView QComboBox, #MatchDetailView QLineEdit, #MatchDetailView QSpinBox {
                background:#1E1E21; color:#F4F4F5; border:1px solid #303035; border-radius:7px;
                padding:6px 10px; min-height:21px; font-size:12px; selection-background-color:#45454D;
            }
            #MatchDetailView QComboBox:focus, #MatchDetailView QLineEdit:focus, #MatchDetailView QSpinBox:focus {border-color:#B8B8C0;}
            #MatchDetailView QComboBox::drop-down {border:0; width:18px;}
            #MatchDetailView QPushButton, #MatchDetailView QToolButton {
                background:#202023; color:#D6D6DB; border:1px solid #303035; border-radius:7px; padding:6px 10px; font-size:12px;
            }
            #MatchDetailView QPushButton:hover, #MatchDetailView QToolButton:hover {background:#303035; border-color:#60606A;}
            #MatchDetailView QPushButton:disabled {color:#62626B; border-color:#29292F;}
            #MatchDetailView QTabWidget::pane {border:0; padding-top:8px; background:transparent;}
            #MatchDetailView QTabBar::tab {background:#161619; color:#929299; border:0; border-radius:7px; padding:8px 14px; margin-right:6px;}
            #MatchDetailView QTabBar::tab:selected {color:#F4F4F5; background:#303035;}
            #MatchDetailView QTabBar::tab:hover {background:#242429; color:#F4F4F5;}
            #MatchDetailView QTableWidget {background:#161619; alternate-background-color:#161619; border:0; border-radius:12px; gridline-color:#27272C; color:#D6D6DB; selection-background-color:#303037; selection-color:#F4F4F5;}
            #MatchDetailView QHeaderView::section {background:#161619; color:#929299; border:0; padding:10px; font-size:11px;}
            #MatchDetailView QTableWidget::item {padding:7px 10px; border-bottom:1px solid #27272C;}
            #MatchDetailView QTableWidget::item:selected {background:#303037; color:#F4F4F5;}
            #MatchDetailView QScrollBar {width:0; height:0;}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        selectors = QHBoxLayout()
        selectors.setSpacing(8)
        selectors.addWidget(_label("主玩家"))
        self.player_combo = _combo("复盘主玩家")
        selectors.addWidget(self.player_combo, 1)
        selectors.addWidget(_label("对比"))
        self.opponent_combo = _combo("对比玩家")
        selectors.addWidget(self.opponent_combo, 1)
        root.addLayout(selectors)
        self.player_summary = _label("", "DetailMuted")
        root.addWidget(self.player_summary)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.setMinimumWidth(0)
        root.addWidget(self.tabs)
        self._build_charts()
        self._build_insights()
        self._build_timeline()
        self.player_combo.currentIndexChanged.connect(self._primary_changed)
        self.opponent_combo.currentIndexChanged.connect(self._refresh_comparison)
        self.set_detail(detail)

    def _build_charts(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        heading.addWidget(_label("经济走势", "DetailTitle"))
        heading.addStretch()
        self.metric_combo = _combo("图表指标")
        for key, name in METRICS:
            self.metric_combo.addItem(name, key)
        self.metric_combo.setCurrentIndex(1)
        self.metric_combo.setMinimumWidth(188)
        self.metric_combo.setMaximumWidth(230)
        heading.addWidget(self.metric_combo)
        layout.addLayout(heading)
        self.chart = DetailChart(height=238)
        self.chart.timeSelected.connect(self._chart_selected)
        layout.addWidget(self.chart)
        self.chart_note = _label("", "DetailMuted")
        layout.addWidget(self.chart_note)
        bars_heading = QHBoxLayout()
        bars_heading.addWidget(_label("每分钟已记录事件", "DetailTitle"))
        bars_heading.addStretch()
        self.event_metric = _combo("每分钟事件类型")
        for name, kind in (("单位完成/出现", "production"), ("建筑完成", "building"), ("单位损失", "loss")):
            self.event_metric.addItem(name, kind)
        self.event_metric.setMinimumWidth(120)
        self.event_metric.setMaximumWidth(170)
        bars_heading.addWidget(self.event_metric)
        layout.addLayout(bars_heading)
        self.event_chart = DetailChart(height=178)
        self.event_chart.timeSelected.connect(self._chart_selected)
        layout.addWidget(self.event_chart)
        self.selected_label = _label("悬停查看数值，点击图表或事件时刻定位。", "DetailMuted")
        layout.addWidget(self.selected_label)
        layout.addStretch()
        self.tabs.addTab(page, "经济走势")
        self.metric_combo.currentIndexChanged.connect(self._refresh_charts)
        self.event_metric.currentIndexChanged.connect(self._refresh_bars)

    def _build_insights(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        card = _card()
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(8)
        top = QHBoxLayout()
        self.gap_title = _label("差距从哪里开始", "DetailTitle")
        top.addWidget(self.gap_title, 1)
        self.gap_jump = _button("定位时段", self._jump_gap)
        top.addWidget(self.gap_jump)
        box.addLayout(top)
        window_row = QHBoxLayout()
        window_row.setSpacing(8)
        window_row.addWidget(_label("回看时段", "DetailMuted"))
        self.review_window_combo = _combo("选择整局的经济差距回看时段")
        self.review_window_combo.currentIndexChanged.connect(self._show_window_evidence)
        window_row.addWidget(self.review_window_combo, 1)
        box.addLayout(window_row)
        self.fact_labels = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        for index, field in enumerate(("事实", "可能原因", "建议", "下局验证")):
            key = _label(field, "DetailMuted")
            key.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            value = _label()
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(key, index, 0)
            grid.addWidget(value, index, 1)
            self.fact_labels[field] = value
        grid.setColumnStretch(1, 1)
        box.addLayout(grid)
        self.comparison_caution = _label("", "DetailMuted")
        box.addWidget(self.comparison_caution)
        layout.addWidget(card)
        scenario_card = _card()
        scenario_layout = QVBoxLayout(scenario_card)
        scenario_layout.setContentsMargins(14, 12, 14, 12)
        scenario_layout.setSpacing(8)
        scenario_layout.addWidget(_label("村民损失 · 情景估算", "DetailTitle"))
        scenario_controls = QGridLayout()
        scenario_controls.setHorizontalSpacing(7)
        self.scenario_rate = ClickSpinBox()
        self.scenario_rate.setAccessibleName("假设每名村民每分钟资源产能")
        self.scenario_rate.setRange(1, 300)
        self.scenario_rate.setValue(40)
        self.scenario_rate.setSuffix(" 资源/人/分")
        self.scenario_rate.setButtonSymbols(QSpinBox.NoButtons)
        self.scenario_rate.setMaximumWidth(160)
        self.scenario_delay = _combo("假设每名损失村民补回所需时间")
        for name, value in (("未补回", None), ("60 秒", 60), ("120 秒", 120), ("自定义", "custom")):
            self.scenario_delay.addItem(name, value)
        self.scenario_seconds = ClickSpinBox()
        self.scenario_seconds.setAccessibleName("自定义补回延迟秒数")
        self.scenario_seconds.setRange(0, 3600)
        self.scenario_seconds.setValue(90)
        self.scenario_seconds.setSuffix(" 秒")
        self.scenario_seconds.setButtonSymbols(QSpinBox.NoButtons)
        self.scenario_seconds.setMaximumWidth(95)
        scenario_controls.addWidget(_label("产能"), 0, 0)
        scenario_controls.addWidget(self.scenario_rate, 0, 1)
        scenario_controls.addWidget(_label("损失后补回"), 0, 2)
        scenario_controls.addWidget(self.scenario_delay, 0, 3)
        scenario_controls.addWidget(self.scenario_seconds, 0, 4)
        scenario_controls.setColumnStretch(5, 1)
        scenario_layout.addLayout(scenario_controls)
        self.scenario_summary = _label("", "DetailMuted")
        scenario_layout.addWidget(self.scenario_summary)
        self.scenario_chart = DetailChart(height=166)
        self.scenario_chart.timeSelected.connect(self._chart_selected)
        scenario_layout.addWidget(self.scenario_chart)
        self.scenario_note = _label("这是改变假设后的产能情景，不是实测损失；后续新出现的村民不会自动算作补回。", "DetailMuted")
        scenario_layout.addWidget(self.scenario_note)
        layout.addWidget(scenario_card)
        self.coverage_toggle = QToolButton()
        self.coverage_toggle.setText("数据范围与缺失项")
        self.coverage_toggle.setCheckable(True)
        self.coverage_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.coverage_toggle.setArrowType(Qt.RightArrow)
        self.coverage_toggle.toggled.connect(self._toggle_coverage)
        layout.addWidget(self.coverage_toggle, 0, Qt.AlignLeft)
        self.coverage_label = _label("", "DetailMuted")
        self.coverage_label.hide()
        layout.addWidget(self.coverage_label)
        layout.addStretch()
        self.tabs.addTab(page, "差距与改进")
        self.scenario_rate.valueChanged.connect(self._refresh_scenario)
        self.scenario_delay.currentIndexChanged.connect(self._refresh_scenario)
        self.scenario_seconds.valueChanged.connect(self._refresh_scenario)

    def _build_timeline(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        tools = QHBoxLayout()
        tools.setSpacing(7)
        self.event_scope = _combo("事件玩家范围")
        for name, value in (("双方事件", "both"), ("主玩家", "primary"), ("对比玩家", "other")):
            self.event_scope.addItem(name, value)
        self.event_scope.setMaximumWidth(120)
        tools.addWidget(self.event_scope)
        self.event_filter = _combo("事件类型筛选")
        self.event_filter.addItem("关键事件", "key_events")
        self.event_filter.addItem("全部事件", None)
        for key, name in EVENT_LABELS.items():
            self.event_filter.addItem(name, key)
        self.event_filter.setMaximumWidth(140)
        tools.addWidget(self.event_filter)
        self.event_search = QLineEdit()
        self.event_search.setAccessibleName("查找单位建筑或科技")
        self.event_search.setPlaceholderText("查找单位、建筑或科技")
        tools.addWidget(self.event_search, 1)
        layout.addLayout(tools)
        self.timeline_table = QTableWidget(0, 3)
        self.timeline_table.setHorizontalHeaderLabels(["时刻", "玩家", "完成与损失事件"])
        self.timeline_table.verticalHeader().hide()
        self.timeline_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.timeline_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.timeline_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.timeline_table.setAlternatingRowColors(True)
        self.timeline_table.setShowGrid(False)
        self.timeline_table.setWordWrap(False)
        self.timeline_table.setItemDelegateForColumn(1, _AccountNameDelegate(self.timeline_table))
        self.timeline_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.timeline_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.timeline_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.timeline_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.timeline_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.timeline_table.setColumnWidth(0, 66)
        self.timeline_table.setColumnWidth(1, 125)
        self.timeline_table.verticalHeader().setDefaultSectionSize(33)
        self.timeline_table.cellClicked.connect(self._timeline_clicked)
        self.timeline_table.setFixedHeight(33 * self.PAGE_SIZE + 34)
        layout.addWidget(self.timeline_table)
        footer = QHBoxLayout()
        self.event_count = _label("", "DetailMuted")
        footer.addWidget(self.event_count, 1)
        self.previous_button = _button("上一页", lambda: self._change_page(-1))
        self.next_button = _button("下一页", lambda: self._change_page(1))
        footer.addWidget(self.previous_button)
        footer.addWidget(self.next_button)
        layout.addLayout(footer)
        layout.addWidget(_label("事件显示已报告的完成/出现、建成或损失；出现不等于付费生产，转化得失及具体工作状态尚不完整。", "DetailMuted"))
        layout.addStretch()
        self.tabs.addTab(page, "事件时间线")
        self.event_scope.currentIndexChanged.connect(self._filter_events)
        self.event_filter.currentIndexChanged.connect(self._filter_events)
        self.event_search.textChanged.connect(self._filter_events)

    def set_detail(self, detail):
        self.detail = detail if isinstance(detail, dict) else {}
        self.players = [player for player in self.detail.get("players", []) if isinstance(player, dict)]
        self._player_names = player_labels(self.players, id_key="profile_id")
        self._primary_profile_id = None
        for chart in (self.chart, self.event_chart, self.scenario_chart):
            chart.set_selected_time(None)
        self.selected_label.setText("悬停查看数值，点击图表或事件时刻定位。")
        self.player_combo.blockSignals(True)
        self.player_combo.clear()
        focus = self.detail.get("focus_profile_id")
        for player in self.players:
            name = self._player_label(player)
            self.player_combo.addItem(name, player.get("profile_id"))
            self.player_combo.setItemData(self.player_combo.count() - 1, name, Qt.ToolTipRole)
        index = self.player_combo.findData(focus)
        self.player_combo.setCurrentIndex(max(0, index))
        self.player_combo.blockSignals(False)
        self._primary_changed()

    def _player_label(self, player):
        """昵称只负责展示；同名后缀与跨视图一致，数据始终关联账号编号。"""
        return self._player_names.get(player.get("profile_id"), str(player.get("name") or "玩家"))

    def primary_player(self):
        wanted = self.player_combo.currentData()
        return next((player for player in self.players if player.get("profile_id") == wanted), {})

    def other_player(self):
        wanted = self.opponent_combo.currentData()
        return next((player for player in self.players if player.get("profile_id") == wanted), {})

    def _primary_changed(self, *_args):
        primary = self.primary_player()
        previous = self.opponent_combo.currentData()
        former_primary = self._primary_profile_id
        self._primary_profile_id = primary.get("profile_id")
        if previous == self._primary_profile_id and former_primary is not None:
            # 将原对比玩家切成主玩家时交换双方，不暗中换成第三名同局玩家。
            previous = former_primary
        self.opponent_combo.blockSignals(True)
        self.opponent_combo.clear()
        candidates = [player for player in self.players if player.get("profile_id") != primary.get("profile_id")]
        candidates.sort(key=lambda player: (0 if primary.get("team") is not None and player.get("team") is not None
                                             and player.get("team") != primary.get("team") else 1))
        for player in candidates:
            name = self._player_label(player)
            relation = "对手" if primary.get("team") is not None and player.get("team") is not None and primary.get("team") != player.get("team") else "队友" if primary.get("team") is not None and player.get("team") == primary.get("team") else "关系未知"
            self.opponent_combo.addItem(f"{name} · {relation}", player.get("profile_id"))
            self.opponent_combo.setItemData(self.opponent_combo.count() - 1, name + " · " + relation, Qt.ToolTipRole)
        index = self.opponent_combo.findData(previous)
        self.opponent_combo.setCurrentIndex(max(0, index))
        self.opponent_combo.blockSignals(False)
        self._refresh_comparison()

    def _refresh_comparison(self, *_args):
        primary = self.primary_player()
        result = {"win": "胜利", "loss": "失利"}.get(primary.get("result"), "结果未提供")
        name = map_name(self.detail.get("map_name"))
        duration = self.detail.get("duration")
        apm = primary.get("apm")
        values = [name, time_label(duration), civilization_name(primary.get("civilization")), result]
        if _finite(apm):
            values.append(f"APM（每分钟操作） {_number(apm)}")
        self.player_summary.setText(" · ".join(values))
        self._refresh_charts()
        self._refresh_bars()
        self._refresh_insights()
        self._refresh_scenario()
        self._filter_events()

    def _line(self, player, key, index):
        series = player.get("series", {}).get(key) or {}
        # 语义尚未核实的 reported_usage_*（原始统计列）不进入事实图表。
        if series.get("kind") == "unverified":
            series = {}
        points = [list(point) for point in series.get("points", [])]
        duration = self.detail.get("duration")
        if series.get("kind") == "step" and points and _finite(duration) and points[-1][0] < duration:
            # 事件计数在下一事件前保持不变，不将最后记录点误画成数量归零。
            points.append([duration, points[-1][1]])
        return {"label": self._player_label(player), "profile_id": player.get("profile_id"), "points": points,
                "color": COLORS[index], "kind": series.get("kind"), "source": series.get("source")}

    def _refresh_charts(self, *_args):
        primary, other = self.primary_player(), self.other_player()
        key = self.metric_combo.currentData() or "bank_total"
        series = primary.get("series", {}).get(key) or other.get("series", {}).get(key) or {}
        lines = [self._line(player, key, index) for index, player in enumerate((primary, other)) if player]
        self.chart.set_data(lines, self.detail.get("duration"), series.get("unit", ""))
        if key.startswith("villagers_"):
            note = "按已报告的初始、完成和损失事件推算；不同文明单个村民产能不同，人数不能直接等同经济强弱。"
        elif key.startswith("income_"):
            note = "来源报告的每分钟采集速率；采样间隔内的变化、村民工作与空闲状态未提供。"
        elif key.startswith("score_"):
            note = "游戏评分，不能换算成实际资源或单独解释胜负。"
        else:
            note = "库存反映当时剩余资源，不能当作累计采集量；四资源不含特殊文明资源。"
        self.chart_note.setText(note)
        self.chart.setToolTip(str(series.get("source") or "该指标未提供来源样本。"))

    def _refresh_bars(self, *_args):
        kind = self.event_metric.currentData() or "production"
        duration = self.detail.get("duration") or 0
        bins = max(1, int(duration // 60) + 1)
        lines = []
        for index, player in enumerate((self.primary_player(), self.other_player())):
            if not player:
                continue
            values = [0] * bins
            for event in player.get("events", []):
                if event.get("kind") != kind or not _finite(event.get("time")):
                    continue
                if kind == "loss" and str(event.get("entity_type", "")).casefold() != "unit":
                    continue
                minute = int(event["time"] // 60)
                if 0 <= minute < bins:
                    values[minute] += event.get("quantity", 1) if _finite(event.get("quantity", 1)) else 1
            lines.append({"label": self._player_label(player), "profile_id": player.get("profile_id"), "color": COLORS[index],
                          "points": [[minute * 60, value] for minute, value in enumerate(values)], "kind": "bins"})
        self.event_chart.set_data(lines, duration, "次" if kind == "building" else "个", "bars")

    def _refresh_insights(self):
        primary, other = self.primary_player(), self.other_player()
        self.comparison = compare_players(primary, other) if primary and other else {}
        self.review_windows = build_review_windows(primary, other)
        self.review_window_combo.blockSignals(True)
        self.review_window_combo.clear()
        for index, window in enumerate(self.review_windows):
            if window.get("kind") == "first_gap":
                text = f"首次持续正差 · {time_label(window['start'])} — {time_label(window['end'])}"
            else:
                text = f"变化时段 {index + 1} · {time_label(window['start'])} — {time_label(window['end'])} · +{_number(window['gap_change'])} 资源/分"
            self.review_window_combo.addItem(text, index)
            self.review_window_combo.setItemData(index, text, Qt.ToolTipRole)
        if not self.review_windows:
            self.review_window_combo.addItem("暂无完整的差距时段", None)
        self.review_window_combo.setEnabled(bool(self.review_windows))
        self.review_window_combo.blockSignals(False)
        self._show_window_evidence()

    def _show_window_evidence(self, *_args):
        primary, other = self.primary_player(), self.other_player()
        index = self.review_window_combo.currentData()
        gap = self.review_windows[index] if isinstance(index, int) and 0 <= index < len(self.review_windows) else None
        self.gap_window = gap
        largest = self.comparison.get("largest_income_gap") or {}
        own_name = self._player_label(primary)
        other_name = self._player_label(other)
        if gap:
            start, end = gap.get("start", 0), gap.get("end", 0)
            self.gap_title.setText(f"重点回看 {time_label(start)} — {time_label(end)}")
            if gap.get("gap_change", 0) > 0:
                fact = f"这 60 秒内，{other_name} 相对 {own_name} 的四资源采集速率差增加 {_number(gap['gap_change'])} 资源/分。"
            elif "gap_change" in gap:
                fact = f"这 60 秒内，双方来源四资源采集速率差未继续向{other_name}扩大，变化为 {_number(gap.get('gap_change'))} 资源/分。"
            else:
                fact = f"该时段末，{other_name}的来源四资源采集速率比{own_name}高 {_number(gap.get('gap_end'))} 资源/分。"
            if largest and _finite(largest.get("value")) and largest["value"] > 0:
                fact += f" 本局最大正差出现在 {time_label(largest.get('time', 0))}：{_number(largest['value'])} 资源/分。"
            events = [event for event in primary.get("events", []) if _finite(event.get("time")) and start <= event["time"] <= end]
            other_events = [event for event in other.get("events", []) if _finite(event.get("time")) and start <= event["time"] <= end]
            losses = sum(event.get("quantity", 1) for event in events if event.get("kind") == "loss" and event.get("is_villager"))
            produced = sum(event.get("quantity", 1) for event in events if event.get("kind") == "production" and event.get("is_villager"))
            other_losses = sum(event.get("quantity", 1) for event in other_events if event.get("kind") == "loss" and event.get("is_villager"))
            other_produced = sum(event.get("quantity", 1) for event in other_events if event.get("kind") == "production" and event.get("is_villager"))
            fact += f" 同段村民出现/损失：主玩家 {produced}/{losses}，对比玩家 {other_produced}/{other_losses}。"
            deltas = resource_gap_changes(primary, other, gap)
            resource_text = []
            for key, name in (("food", "食物"), ("wood", "木材"), ("gold", "黄金"), ("stone", "石料")):
                value = deltas[key]
                text = ("+" if value > 0 else "") + _number(value) if value is not None else "未提供"
                resource_text.append(name + " " + text)
            fact += "\n速率差变化：" + " · ".join(resource_text) + "（资源/分）。"
            if losses:
                reason = f"同一时段记录到 {losses} 名村民损失，可能与产能下降有关；摘要不含村民工作和受袭轨迹，不能据此确认全部原因。"
                advice = "先定位这段村民损失，再查看同期双方经济科技和村民出现事件，确定防护与恢复的先后顺序。"
                verify = "下一局回看相同阶段，比较村民损失数量及采集速率差是否缩小；不要只比较最后总分。"
            else:
                upgrades = [event.get("entity_name") for event in events if event.get("kind") == "upgrade" and event.get("entity_name")]
                reason = ("该时段完成“" + "、".join(upgrades[:2]) + "”，可作为复核节点；" if upgrades else "此时段未记录主玩家村民损失；") + "科技、文明机制、分工或资源转移仍可能影响差距，现有数据不能直接判定原因。"
                advice = "从这个时间窗对照双方村民出现、损失和经济科技事件，再在游戏回放中核对当时的局势与采集分工。"
                verify = "下一局只调整一个可验证环节，再比较同阶段速率、村民损失和库存变化，避免把单局差异当固定结论。"
            self.gap_jump.setEnabled(True)
        else:
            self.gap_title.setText("当前对比缺少连续采样证据")
            fact = "没有足够的双方采集速率样本，暂不能确定经济差距开始的时段。"
            reason = "单看村民数、库存或游戏评分，都不足以确认胜负原因。"
            advice = "先看已有完成和损失事件，再结合可播放的游戏回放核对。"
            verify = "取得相同指标的完整采样后，再比较同一阶段。"
            self.gap_jump.setEnabled(False)
        for key, value in zip(("事实", "可能原因", "建议", "下局验证"), (fact, reason, advice, verify)):
            self.fact_labels[key].setText(value)
        self.comparison_caution.setText(self.comparison.get("caution") or "按单名玩家对比；团队分工和文明机制会影响资源曲线，不能据此判定文明选择错误。")
        gaps = self.detail.get("coverage", {}).get("gaps") or []
        if not isinstance(gaps, list):
            gaps = [str(gaps)]
        self.coverage_label.setText("；".join(map(str, gaps)) or "未提供逐帧位置、选中目标、村民具体工作和损失归属；此处只展示摘要已报告的采样与事件。")

    def _refresh_scenario(self, *_args):
        delay = self.scenario_delay.currentData()
        custom = delay == "custom"
        self.scenario_seconds.setVisible(custom)
        delay = self.scenario_seconds.value() if custom else delay
        primary = self.primary_player()
        self.scenario = (estimate_worker_loss(self.detail.get("events", []), primary.get("profile_id"),
                                              rate_per_worker=self.scenario_rate.value(), recovery_seconds=delay,
                                              until=self.detail.get("duration"))
                         if primary.get("profile_id") else {"estimated_loss": 0, "losses": [], "points": []})
        total = self.scenario.get("estimated_loss")
        losses = sum(loss.get("quantity", 0) for loss in self.scenario.get("losses", []))
        self.scenario_summary.setText(f"已记录 {losses} 名村民损失 · 按当前假设，截至 {time_label(self.detail.get('duration'))} 情景累计少产约 {_number(total)} 资源。")
        self.scenario_chart.set_data([{"label": "累计少产 · 假设情景", "color": COLORS[1],
                                       "points": self.scenario.get("points", []), "kind": "line"}],
                                     self.detail.get("duration"), "资源")
        self.scenario_chart.setToolTip(str(self.scenario.get("caution") or "情景估算，不是实测经济损失。"))

    def _filter_events(self, *_args):
        primary_id = self.primary_player().get("profile_id")
        other_id = self.other_player().get("profile_id")
        scope = self.event_scope.currentData()
        wanted = {primary_id} if scope == "primary" else {other_id} if scope == "other" else {primary_id, other_id}
        kind = self.event_filter.currentData()
        query = self.event_search.text().strip().casefold()
        self.filtered_events = sorted((event for event in self.detail.get("events", [])
                                       if event.get("profile_id") in wanted
                                       and (kind is None or event.get("kind") == kind
                                            or kind == "key_events" and event.get("kind") in {"production", "loss", "building", "upgrade", "age"})
                                       and (not query or query in str(event.get("label", "")).casefold()
                                            or query in str(event.get("entity_name", "")).casefold())),
                                      key=lambda event: event.get("time", 0))
        self.page_index = 0
        self._show_event_page()

    def _change_page(self, delta):
        pages = max(1, math.ceil(len(self.filtered_events) / self.PAGE_SIZE))
        self.page_index = max(0, min(pages - 1, self.page_index + delta))
        self._show_event_page()

    def _show_event_page(self):
        start = self.page_index * self.PAGE_SIZE
        self.visible_events = self.filtered_events[start:start + self.PAGE_SIZE]
        self.timeline_table.setRowCount(len(self.visible_events))
        names = self._player_names
        # 原固定125像素会把两个同名账号都截成“昵称 · …”，消歧后按实际字宽扩列。
        disambiguated = any(names.get(player.get("profile_id")) != str(player.get("name") or "玩家")
                            for player in self.players)
        ideal_width = max((self.timeline_table.fontMetrics().horizontalAdvance(name) + 24
                           for name in names.values()), default=125)
        cap = max(125, min(240, self.timeline_table.viewport().width() // 3))
        self.timeline_table.setColumnWidth(1, min(cap, max(125, ideal_width)) if disambiguated else 125)
        for row, event in enumerate(self.visible_events):
            label = _event_label(event)
            for column, text in enumerate((time_label(event.get("time")), names.get(event.get("profile_id"), "玩家"), label)):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, event.get("time"))
                item.setToolTip(text + ("\n来源：" + str(event.get("source")) if column == 2 else "\n点击定位到经济图表"))
                if column == 0:
                    item.setForeground(QColor("#F4F4F5"))
                self.timeline_table.setItem(row, column, item)
        pages = max(1, math.ceil(len(self.filtered_events) / self.PAGE_SIZE))
        self.event_count.setText(f"{len(self.filtered_events)} 条事件 · 第 {self.page_index + 1}/{pages} 页")
        self.previous_button.setEnabled(self.page_index > 0)
        self.next_button.setEnabled(self.page_index + 1 < pages)

    def _timeline_clicked(self, row, _column):
        if 0 <= row < len(self.visible_events):
            self.focus_time(self.visible_events[row].get("time"), show_chart=True)

    def _jump_gap(self):
        if self.gap_window:
            self.focus_time(self.gap_window.get("start", 0), show_chart=True)

    def _chart_selected(self, seconds):
        self.focus_time(seconds)

    def focus_time(self, seconds, *, show_chart=False):
        if not _finite(seconds):
            return
        seconds = max(0, min(float(self.detail.get("duration") or 0), float(seconds)))
        for chart in (self.chart, self.event_chart, self.scenario_chart):
            chart.set_selected_time(seconds)
        self.selected_label.setText(f"已定位 {time_label(seconds)} · 可切换指标，查看同一时刻的变化。")
        if show_chart:
            self.tabs.setCurrentIndex(0)
        self.timeSelected.emit(seconds)

    def _toggle_coverage(self, checked):
        self.coverage_label.setVisible(checked)
        self.coverage_toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
