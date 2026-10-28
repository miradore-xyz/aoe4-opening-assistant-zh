"""全屏地图的高级分析浮层：独立入口栏与内容卡，不覆盖空白地图的鼠标区域。

Dock（浮层控制器）只在数据或玩家筛选变化时建立分析；播放仅移动图表时标。
图表表示已有记录，缺少真实视野、轨迹或因果证据时，保留对应的数据缺口。
"""
from __future__ import annotations

import math
from bisect import bisect_right
from queue import Empty, SimpleQueue
from threading import Thread

from PyQt5.QtCore import QEvent, QObject, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (QDoubleSpinBox, QFrame, QHBoxLayout,
                            QLabel, QPushButton, QScrollArea, QSizePolicy,
                            QVBoxLayout, QWidget)

from advanced_analysis import ANALYSIS_SPECS, build_advanced_analysis
from app_fonts import make_ui_font
from choice_controls import ClickComboBox
from player_labels import player_labels
from visual import line_icon


_COLORS = ("#74cbb3", "#81b7e2", "#d7ad79", "#b39bd3", "#d48eaa", "#c7c47d", "#8dbdc7", "#bdc2cc")
_STATUS = {"ready": "已有数据", "partial": "部分可分析", "missing": "待补充数据", "loading": "正在分析…"}
_STYLE = """
QFrame#AdvancedRail, QFrame#AdvancedPanel {background:#161619;border:1px solid #2c2c32;border-radius:14px;}
QWidget {color:#ededf0;font-family:'Smiley Sans';font-size:13px;background:transparent;}
QLabel {border:0;background:transparent;}
QLabel#AnalysisMuted {color:#9999a3;font-size:12px;}
QLabel#AnalysisGroup {color:#8f8f9a;font-size:11px;padding:8px 6px 4px;}
QLabel#AnalysisStatus {color:#c5c5ce;font-size:11px;}
QLabel#AnalysisSection {color:#c3c3cc;font-size:12px;}
QPushButton {background:transparent;border:1px solid transparent;border-radius:7px;color:#b5b5bf;padding:0;}
QPushButton:hover {background:#25252b;color:#f4f4f5;}
QPushButton:pressed {background:#303037;}
QPushButton:focus {border:1px solid #8e8e99;}
QPushButton#AnalysisNav {text-align:left;padding:0 8px;font-size:13px;min-height:32px;max-height:32px;}
QPushButton#AnalysisNav:checked {background:#303037;border-color:#484852;color:#f5f5f6;}
QPushButton#AnalysisAction {background:#24242a;border:1px solid #3c3c45;padding:7px 10px;}
QPushButton#AnalysisAction:hover {background:#303037;}
QPushButton:disabled {color:#666671;border-color:#25252b;}
QDoubleSpinBox {background:#222228;color:#e6e6ed;border:1px solid #44444f;border-radius:7px;padding:6px 8px;min-height:20px;}
QDoubleSpinBox:focus {border-color:#9999a6;}
QScrollArea {background:transparent;border:0;}
QScrollBar:vertical {background:transparent;border:0;width:6px;margin:2px 0;}
QScrollBar::handle:vertical {background:#454550;border-radius:3px;min-height:25px;}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {height:0;}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {background:transparent;}
QScrollBar:horizontal {background:transparent;border:0;height:6px;}
QScrollBar::handle:horizontal {background:#454550;border-radius:3px;min-width:25px;}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {width:0;}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {background:transparent;}
"""


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _clock(value):
    seconds = max(0, int(value)) if _finite(value) else 0
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _number(value):
    if value is None:
        return "未提供"
    if _finite(value):
        return f"{value:,.2f}".rstrip("0").rstrip(".") if not float(value).is_integer() else f"{int(value):,}"
    return str(value)


def _label(value, role=None, wrap=True):
    widget = QLabel(str(value or ""))
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(wrap)
    widget.setFont(make_ui_font(13))
    widget.setMinimumWidth(0)
    if role:
        widget.setObjectName(role)
    return widget


def _axis_number(value):
    """仅压缩坐标刻度；表格和指标仍保留实际值。"""
    return f"{value / 10000:.1f}万" if abs(value) >= 10000 else f"{value:.0f}"


class _ElidedLabel(QLabel):
    """长玩家名仅截短可见文字，完整名字保留在无障碍名称中。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.setTextFormat(Qt.PlainText)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def set_full_text(self, text):
        self._full_text = str(text)
        self.setAccessibleName(self._full_text)
        self._elide()

    def resizeEvent(self, event):
        self._elide()
        super().resizeEvent(event)

    def _elide(self):
        self.setText(self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, max(0, self.width())))


class AnalysisChart(QWidget):
    """原生轻量时序图；缓存几何，播放时只更新细时标覆盖的区域。"""

    def __init__(self, result, duration=0, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(222)
        self.setFont(make_ui_font(11))
        self.setAccessibleName(f"{result.get('title', '分析')}图表；横轴为对局时间")
        self.kind = result.get("chart_type", "line")
        self.bin_seconds = result.get("bin_seconds")
        self.series = []
        for index, row in enumerate(result.get("series") or []):
            points = [(float(point[0]), float(point[1]) if _finite(point[1]) else None)
                      for point in row.get("points", [])
                      if isinstance(point, (list, tuple)) and len(point) > 1 and _finite(point[0])]
            if points:
                color = QColor(row.get("color") or _COLORS[index % len(_COLORS)])
                self.series.append({"label": str(row.get("label") or "已有记录"), "points": points,
                                    "unit": str(row.get("unit") or ""), "color": color})
        greatest_time = max((point[0] for row in self.series for point in row["points"]), default=0)
        self.duration = max(1.0, float(duration or 0), greatest_time)
        self.cursor = 0.0
        self._cursor_pixel = None
        self._geometry_key = None
        self._paths = []
        self._values = [point[1] for row in self.series for point in row["points"] if point[1] is not None]
        self._low = min(0, min(self._values, default=0))
        self._high = max(self._low + 1, max(self._values, default=1))
        self._units = list(dict.fromkeys(row["unit"] for row in self.series if row["unit"]))
        self.setAccessibleDescription("；".join(row["label"] for row in self.series))

    def _plot(self):
        if self.kind in ("heat", "tracks"):
            metrics = QFontMetrics(make_ui_font(11))
            natural = max((metrics.horizontalAdvance(row["label"]) for row in self.series), default=30)
            left = max(48, min(natural + 20, self.width() - 156))
            top = 24
        else:
            left, top = 48, 46 if len(self.series) > 2 else 28
        return QRectF(left, top, max(1, self.width() - left - 16), max(1, self.height() - top - 28))

    def _x(self, seconds):
        plot = self._plot()
        return plot.left() + plot.width() * max(0, min(1, seconds / self.duration))

    def set_cursor(self, seconds):
        self.cursor = max(0, min(self.duration, float(seconds))) if _finite(seconds) else 0
        pixel = int(self._x(self.cursor))
        if pixel == self._cursor_pixel:
            return
        if self._cursor_pixel is not None:
            self.update(QRect(self._cursor_pixel - 2, 36, 5, max(1, self.height() - 55)))
        self._cursor_pixel = pixel
        self.update(QRect(pixel - 2, 36, 5, max(1, self.height() - 55)))

    def _cache_paths(self, plot):
        key = (self.width(), self.height())
        if key == self._geometry_key:
            return
        self._geometry_key = key
        self._paths = []
        for row in self.series:
            path, continuing = QPainterPath(), False
            for seconds, value in row["points"]:
                if value is None:
                    continuing = False
                    continue
                point = QPointF(self._x(seconds), plot.bottom() - (value - self._low) / (self._high - self._low) * plot.height())
                if continuing:
                    path.lineTo(point)
                else:
                    path.moveTo(point)
                    continuing = True
            self._paths.append(path)

    def _legend(self, painter):
        if self.kind in ("heat", "tracks"):
            return
        columns = max(1, min(2, len(self.series)))
        slot = max(1, (self.width() - 30) // columns)
        for index, row in enumerate(self.series):
            x, y = 16 + (index % columns) * slot, 14 + (index // columns) * 18
            painter.setPen(QPen(row["color"], 2))
            painter.drawLine(x, y, x + 10, y)
            painter.setPen(QColor("#bfc0ca"))
            caption = row["label"].split("（")[0]
            caption = painter.fontMetrics().elidedText(caption, Qt.ElideRight, max(1, slot - 23))
            painter.drawText(QRectF(x + 16, y - 10, slot - 20, 20), Qt.AlignVCenter, caption)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(make_ui_font(11))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#111114"))
        painter.drawRoundedRect(QRectF(self.rect()), 10, 10)
        self._legend(painter)
        plot = self._plot()
        painter.setPen(QPen(QColor("#29292f"), 1))
        for ratio in (0, .5, 1):
            y = plot.bottom() - plot.height() * ratio
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            if self.kind not in ("heat", "tracks"):
                painter.setPen(QColor("#858592"))
                value = self._low + (self._high - self._low) * ratio
                painter.drawText(QRectF(1, y - 8, 40, 16), Qt.AlignRight | Qt.AlignVCenter, _axis_number(value))
                painter.setPen(QPen(QColor("#29292f"), 1))
        painter.setPen(QColor("#858592"))
        for ratio, align in ((0, Qt.AlignLeft), (.5, Qt.AlignHCenter), (1, Qt.AlignRight)):
            x = plot.left() + ratio * plot.width()
            painter.drawText(QRectF(x - (40 if ratio else 0), plot.bottom() + 6, 80 if ratio == .5 else 40, 17),
                             align | Qt.AlignVCenter, _clock(self.duration * ratio))
        if self.kind == "heat":
            self._paint_rows(painter, plot, heat=True)
        elif self.kind == "tracks":
            self._paint_rows(painter, plot, heat=False)
        elif self.kind == "bar":
            self._paint_bars(painter, plot)
        else:
            self._cache_paths(plot)
            painter.save()
            painter.setClipRect(plot.adjusted(-1, -2, 1, 2))
            for row, path in zip(self.series, self._paths):
                painter.setPen(QPen(row["color"], 1.7))
                painter.setBrush(Qt.NoBrush)
                painter.drawPath(path)
                # 单条观测仍可见；没有用零值连接缺失采样。
                if len(row["points"]) == 1 and row["points"][0][1] is not None:
                    seconds, value = row["points"][0]
                    y = plot.bottom() - (value - self._low) / (self._high - self._low) * plot.height()
                    painter.drawEllipse(QPointF(self._x(seconds), y), 2.5, 2.5)
            painter.restore()
        painter.setPen(QPen(QColor("#e9e9ee"), 1, Qt.DashLine))
        painter.drawLine(QPointF(self._x(self.cursor), plot.top()), QPointF(self._x(self.cursor), plot.bottom()))
        painter.end()

    def _paint_bars(self, painter, plot):
        for index, row in enumerate(self.series):
            values = row["points"]
            for position, (seconds, value) in enumerate(values):
                if value is None:
                    continue
                right = values[position + 1][0] if position + 1 < len(values) else min(self.duration, seconds + 60)
                available = max(2, self._x(right) - self._x(seconds))
                width = max(1, (available - 2) / max(1, len(self.series)))
                x = self._x(seconds) + index * width
                baseline = plot.bottom() - (0 - self._low) / (self._high - self._low) * plot.height()
                y = plot.bottom() - (value - self._low) / (self._high - self._low) * plot.height()
                painter.fillRect(QRectF(x, min(y, baseline), width, max(1, abs(baseline - y))), row["color"])

    def _paint_rows(self, painter, plot, heat):
        if not self.series:
            return
        height = plot.height() / len(self.series)
        for index, row in enumerate(self.series):
            y = plot.top() + index * height
            painter.setPen(QColor("#aaaab5"))
            label_width = max(1, int(plot.left()) - 18)
            caption = row["label"]
            if painter.fontMetrics().horizontalAdvance(caption) > label_width:
                name, separator, category = caption.rpartition(" · ")
                if separator:
                    suffix = separator + category
                    caption = painter.fontMetrics().elidedText(name, Qt.ElideRight,
                              max(8, label_width - painter.fontMetrics().horizontalAdvance(suffix))) + suffix
            painter.drawText(QRectF(4, y, label_width, height), Qt.AlignRight | Qt.AlignVCenter, caption)
            painter.fillRect(QRectF(plot.left(), y + 2, plot.width(), max(1, height - 4)), QColor("#202025"))
            for position, (seconds, value) in enumerate(row["points"]):
                if value is None:
                    continue
                ratio = max(0, min(1, (value - self._low) / (self._high - self._low)))
                x = self._x(seconds)
                if heat:
                    right = row["points"][position + 1][0] if position + 1 < len(row["points"]) else min(self.duration, seconds + 60)
                    color = QColor(row["color"])
                    color.setAlphaF(.12 + ratio * .88)
                    painter.fillRect(QRectF(x, y + 2, max(1, self._x(right) - x - 1), max(1, height - 4)), color)
                elif value > 0:
                    painter.setPen(QPen(row["color"], 2))
                    painter.drawLine(QPointF(x, y + height - 4), QPointF(x, y + height - 4 - (height - 8) * ratio))


class ObservationChart(QWidget):
    """一个实体的真实位置采样散点，不连接采样点、不补中间运动。"""

    _KINDS = {"birth": ("出现", "#74cbb3"), "attack": ("攻击观测", "#d7ad79"), "loss": ("损失", "#d48eaa")}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(230)
        self.setMinimumWidth(0)
        self.setAccessibleName("单位位置观测散点图，非完整移动轨迹")
        self.points, self._times = [], []
        self._current = -1
        self._seconds = 0.0
        self._bounds = (0, 1, 0, 1)

    def set_group(self, group):
        self.points = sorted([dict(row) for row in group.get("observations", [])
                              if row.get("kind") in self._KINDS and _finite(row.get("time"))
                              and isinstance(row.get("position"), dict)
                              and _finite(row["position"].get("x")) and _finite(row["position"].get("y"))],
                             key=lambda row: row["time"])
        self._times = [row["time"] for row in self.points]
        if self.points:
            xs = [row["position"]["x"] for row in self.points]
            ys = [row["position"]["y"] for row in self.points]
            xpad, ypad = max(.5, (max(xs)-min(xs))*.12), max(.5, (max(ys)-min(ys))*.12)
            self._bounds = (min(xs)-xpad, max(xs)+xpad, min(ys)-ypad, max(ys)+ypad)
        self._current = -2
        self.set_cursor(self._seconds)
        self.update()

    def set_cursor(self, seconds):
        self._seconds = float(seconds) if _finite(seconds) else 0.0
        current = bisect_right(self._times, self._seconds + 1e-8)-1
        if current != self._current:
            self._current = current
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(make_ui_font(11))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#111114"))
        painter.drawRoundedRect(QRectF(self.rect()), 10, 10)
        painter.setPen(QColor("#aaaab5"))
        painter.drawText(QRectF(14, 10, self.width()-28, 18), Qt.AlignLeft, "○ 出现　△ 攻击观测　× 损失")
        plot = QRectF(48, 40, max(1, self.width()-70), 136)
        painter.setPen(QPen(QColor("#29292f"), 1))
        for ratio in (0, .5, 1):
            painter.drawLine(QPointF(plot.left(), plot.top()+ratio*plot.height()), QPointF(plot.right(), plot.top()+ratio*plot.height()))
            painter.drawLine(QPointF(plot.left()+ratio*plot.width(), plot.top()), QPointF(plot.left()+ratio*plot.width(), plot.bottom()))
        xmin,xmax,ymin,ymax = self._bounds
        def point(row):
            position = row["position"]
            return QPointF(plot.left()+(position["x"]-xmin)/(xmax-xmin)*plot.width(),
                           plot.bottom()-(position["y"]-ymin)/(ymax-ymin)*plot.height())
        for index, row in enumerate(self.points):
            location = point(row)
            color = QColor(self._KINDS[row["kind"]][1])
            color.setAlpha(240 if index <= self._current else 105)
            painter.setPen(QPen(color, 1.7))
            painter.setBrush(Qt.NoBrush)
            if row["kind"] == "birth":
                painter.drawEllipse(location, 4, 4)
            elif row["kind"] == "loss":
                painter.drawLine(location+QPointF(-4,-4), location+QPointF(4,4))
                painter.drawLine(location+QPointF(-4,4), location+QPointF(4,-4))
            else:
                shape = QPainterPath(location+QPointF(0,-5))
                shape.lineTo(location+QPointF(5,4))
                shape.lineTo(location+QPointF(-5,4))
                shape.closeSubpath()
                painter.drawPath(shape)
            if index == self._current:
                painter.setPen(QPen(QColor("#eeeef2"), 1.2))
                painter.drawEllipse(location, 8, 8)
        painter.setPen(QColor("#858592"))
        painter.drawText(QRectF(2, 33, 40, 16), Qt.AlignRight, _axis_number(ymax))
        painter.drawText(QRectF(2, 163, 40, 16), Qt.AlignRight, _axis_number(ymin))
        painter.drawText(QRectF(48, 181, plot.width(), 16), Qt.AlignLeft, f"原始X {_number(xmin)}")
        painter.drawText(QRectF(48, 181, plot.width(), 16), Qt.AlignRight, _number(xmax))
        if self._current >= 0:
            row = self.points[self._current]
            caption = f"{_clock(row['time'])} · {self._KINDS[row['kind']][0]} · X {_number(row['position']['x'])} / Y {_number(row['position']['y'])}"
        else:
            caption = "当前时刻之前暂无位置采样；淡色为此单位后续已有记录。"
        painter.drawText(QRectF(14, 206, self.width()-28, 18), Qt.AlignLeft,
                         painter.fontMetrics().elidedText(caption, Qt.ElideRight, self.width()-28))
        painter.end()


class AdvancedAnalysisDock(QObject):
    """地图视口中的两块独立子控件；空白地图继续直接接收拖动与滚轮。"""

    requestMoreData = pyqtSignal()
    analysisSelected = pyqtSignal(str)
    analysisReady = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._viewport = parent
        self._enabled = False
        self._context = {}
        self._models = {}
        self._model = {}
        self._player_id = None
        self._reserve = 0.0
        self._repeat_window = 1.0
        self._generation = 0
        self._workers = set()
        self._pending_key = None
        # 后台只持纯Python队列，避免父视口销毁Qt桥对象时与线程emit发生原生竞态。
        self._results = SimpleQueue()
        self._delivery_timer = QTimer(self)
        self._delivery_timer.setInterval(20)
        self._delivery_timer.timeout.connect(self._deliver_results)
        self._selected_id = None
        self._seconds = 0.0
        self._shown_second = None
        self._chart = None
        self._observation_chart = None
        self._observation_key = None
        self._series_choice = {}
        self._rendered_result = None
        self._available_rect = QRect()
        self._bottom_reserved = 0
        self._cleaned = False
        self._loading = False
        self._pending_scroll_restore = None
        self._rendering_result = False
        self._content_body = None
        self._content_viewport = None
        self._anchoring = False
        self._anchors = {}
        self.buttons = {}
        self.rail = QFrame(parent)
        self.rail.setObjectName("AdvancedRail")
        self.rail.setStyleSheet(_STYLE)
        self.rail.setAccessibleName("高级分析类别入口")
        self.rail.setFont(make_ui_font(13))
        self.rail.installEventFilter(self)
        rail_layout = QVBoxLayout(self.rail)
        rail_layout.setContentsMargins(10, 14, 10, 10)
        rail_layout.setSpacing(8)
        heading = _label("高级分析", wrap=False)
        heading.setStyleSheet("font-size:16px;color:#f1f1f4;padding-left:6px;")
        rail_layout.addWidget(heading)
        self.navigation = QScrollArea(self.rail)
        self.navigation.setWidgetResizable(True)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.navigation.setFrameShape(QFrame.NoFrame)
        self.navigation.viewport().setStyleSheet("background:#161619;")
        self.navigation.viewport().setAutoFillBackground(True)
        navigation_body = QWidget()
        navigation_body.setStyleSheet("background:#161619;")
        navigation_body.setAutoFillBackground(True)
        navigation_layout = QVBoxLayout(navigation_body)
        navigation_layout.setContentsMargins(0, 0, 2, 0)
        navigation_layout.setSpacing(4)
        previous_group = None
        for index, spec in enumerate(ANALYSIS_SPECS):
            # 数据层可细分主题；浮层固定三个大组，避免窄栏被过多组标题切碎。
            group = "信息与操作" if index < 4 else "经济与执行" if index < 8 else "战术与成长"
            if group != previous_group:
                navigation_layout.addWidget(_label(group, "AnalysisGroup", False))
                previous_group = group
            button = QPushButton(spec["title"])
            button.setObjectName("AnalysisNav")
            button.setCheckable(True)
            button.setFixedHeight(34)
            button.setMinimumWidth(0)
            button.setCursor(Qt.PointingHandCursor)
            button.setAccessibleName(f"{spec['title']}，第{index + 1}项，共18项")
            button.setAccessibleDescription(spec.get("description", ""))
            button.setToolTip("")
            button.clicked.connect(lambda checked=False, identity=spec["id"]: self.select_analysis(identity))
            navigation_layout.addWidget(button)
            self.buttons[spec["id"]] = button
        navigation_layout.addStretch()
        self.navigation.setWidget(navigation_body)
        rail_layout.addWidget(self.navigation, 1)
        self.panel = QFrame(parent)
        self.panel.setObjectName("AdvancedPanel")
        self.panel.setStyleSheet(_STYLE)
        self.panel.setAccessibleName("高级分析内容")
        self.panel.setFont(make_ui_font(13))
        self.panel.installEventFilter(self)
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(20, 16, 20, 16)
        panel_layout.setSpacing(12)
        header = QHBoxLayout()
        header.setSpacing(12)
        self.title = _ElidedLabel()
        self.title.setStyleSheet("font-size:20px;color:#f2f2f5;")
        self.close_button = QPushButton()
        self.close_button.setIcon(line_icon("x", "#bfbfc9"))
        self.close_button.setIconSize(QSize(18, 18))
        self.close_button.setFixedSize(30, 30)
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setAccessibleName("关闭高级分析内容，保留类别入口")
        self.close_button.setToolTip("")
        self.close_button.clicked.connect(self.close_panel)
        header.addWidget(self.title, 1)
        header.addWidget(self.close_button)
        panel_layout.addLayout(header)
        metadata = QHBoxLayout()
        metadata.setSpacing(10)
        self.status = _label("", "AnalysisStatus", False)
        self.player = _ElidedLabel()
        self.player.setObjectName("AnalysisMuted")
        self.current_time = _label("当前 00:00", "AnalysisMuted", False)
        metadata.addWidget(self.status)
        metadata.addWidget(self.player, 1)
        metadata.addWidget(self.current_time)
        panel_layout.addLayout(metadata)
        self.content = QScrollArea(self.panel)
        self.content.setWidgetResizable(True)
        self.content.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content.setFrameShape(QFrame.NoFrame)
        self.content.setAccessibleName("分析图表，可上下滚动")
        self.content.verticalScrollBar().rangeChanged.connect(self._try_restore_scroll)
        self._content_viewport = self.content.viewport()
        self._content_viewport.installEventFilter(self)
        panel_layout.addWidget(self.content, 1)
        self.rail.hide()
        self.panel.hide()

    def set_context(self, report=None, spatial=None, detail=None, reference=None, history=None, sources=None):
        """只在上下文改变时失效缓存；同一数据的25赫兹播放不调用此方法。"""
        self._context = dict(report=report, spatial=spatial, detail=detail, reference=reference, history=history)
        if sources is not None:
            self._context["sources"] = sources
        self._models.clear()
        self._generation += 1
        self._rendered_result = None
        self._rebuild_model()

    def _rebuild_model(self):
        """分析在后台执行；上下文代次与玩家/阈值共同决定缓存身份。"""
        if self._cleaned:
            return
        self._pending_scroll_restore = None
        key = (self._generation, self._player_id, self._reserve, self._repeat_window)
        self._pending_key = key
        if key in self._models:
            self._apply_model(self._models[key])
            return
        # 清掉旧玩家/旧对局内容，而不是把旧结果留在“正在分析”的标题下。
        self._model = {}
        self._rendered_result = None
        self._sync_player_caption()
        self.status.setText("正在分析…")
        if self._selected_id:
            self._render_result()
        if key in self._workers:
            return
        self._workers.add(key)
        results, context, builder = self._results, dict(self._context), build_advanced_analysis

        def work():
            try:
                model = builder(**context, selected_player_id=key[1], reserve=key[2], repeat_window=key[3])
                payload = {"key": key, "model": model}
            except Exception as error:
                payload = {"key": key, "error": str(error)}
            results.put(payload)

        self._delivery_timer.start()
        Thread(target=work, name="advanced-analysis", daemon=True).start()

    def _deliver_results(self):
        """只在有后台任务时由GUI线程取结果；空闲与关闭后停止交付计时器。"""
        while not self._cleaned:
            try:
                payload = self._results.get_nowait()
            except Empty:
                break
            self._model_completed(payload)
        if not self._cleaned and not self._workers:
            self._delivery_timer.stop()

    def _model_completed(self, payload):
        key = payload["key"]
        self._workers.discard(key)
        if self._cleaned or key[0] != self._generation:
            return
        model = payload.get("model")
        if model is None:
            results = [dict(spec, status="missing", summary="本次分析未完成。", metrics=[], series=[],
                            columns=[], rows=[], missing=["分析错误：" + str(payload.get("error") or "原因未知")],
                            evidence=[], caveat="可重新补充数据后再试。") for spec in ANALYSIS_SPECS]
            model = {"players": [], "duration": 0, "by_id": {row["id"]: row for row in results}}
        self._models[key] = model
        if key == self._pending_key:
            self._apply_model(model)

    def _sync_player_caption(self):
        players = self._model.get("players")
        if not players:
            spatial = self._context.get("spatial") or {}
            report = self._context.get("report") or {}
            players = spatial.get("players") or report.get("players") or []
        labels = player_labels(players)
        caption = "全部玩家" if self._player_id is None else labels.get(self._player_id, f"局内玩家 {self._player_id}")
        self.player.set_full_text(caption)

    def _apply_model(self, model):
        self._model = model
        self._rendered_result = None
        self._sync_player_caption()
        if self._selected_id:
            self._render_result()
            self.reposition(self._available_rect, self._bottom_reserved)
        self.analysisReady.emit()

    def is_ready(self):
        """供实际验收等待当前上下文结果，不把旧后台任务当成已完成。"""
        return self._pending_key in self._models and not self._cleaned

    def set_time(self, seconds, player_id=None):
        """更新播放器时标；筛选未改变时不扫描事件、不重新建立控件或图表。"""
        if self._cleaned:
            return
        self._seconds = max(0, float(seconds)) if _finite(seconds) else 0.0
        if player_id != self._player_id:
            self._player_id = player_id
            self._rebuild_model()
        shown_second = int(self._seconds)
        if shown_second != self._shown_second:
            self._shown_second = shown_second
            self.current_time.setText(f"当前 {_clock(self._seconds)}")
        if self._chart is not None and self.panel.isVisible():
            self._chart.set_cursor(self._seconds)
        if self._observation_chart is not None and self.panel.isVisible():
            self._observation_chart.set_cursor(self._seconds)

    def set_enabled(self, enabled):
        self._enabled = bool(enabled) and not self._cleaned
        self.rail.setVisible(self._enabled)
        self.panel.setVisible(self._enabled and self._selected_id is not None)
        if self._enabled:
            self.reposition(self._available_rect, self._bottom_reserved)

    def select_analysis(self, identity):
        if identity not in self.buttons:
            return
        self._pending_scroll_restore = None
        if self._selected_id == identity:
            self.close_panel()
            return
        self._selected_id = identity
        for key, button in self.buttons.items():
            button.setChecked(key == identity)
        self.navigation.ensureWidgetVisible(self.buttons[identity], 0, 12)
        self._render_result()
        self.panel.setVisible(self._enabled)
        self.reposition(self._available_rect, self._bottom_reserved)
        self.analysisSelected.emit(identity)

    def close_panel(self):
        self._pending_scroll_restore = None
        self._selected_id = None
        for button in self.buttons.values():
            button.setChecked(False)
        self.panel.hide()

    def _render_result(self):
        result = (self._model.get("by_id") or {}).get(self._selected_id)
        if result is None:
            result = next((spec for spec in ANALYSIS_SPECS if spec["id"] == self._selected_id), {})
            if self._pending_key is not None and not self.is_ready():
                result = dict(result, status="loading", summary="正在分析当前对局的已有记录…")
        key = (id(self._model), self._selected_id)
        if key == self._rendered_result:
            return
        self._rendered_result = key
        self._rendering_result = True
        self.title.set_full_text(result.get("title", "高级分析"))
        self.status.setText(_STATUS.get(result.get("status"), "待补充数据"))
        old_body = self.content.takeWidget()
        if old_body is not None:
            old_body.deleteLater()
        self._chart = None
        self._observation_chart = None
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 3, 4)
        layout.setSpacing(16)
        series = [row for row in result.get("series", []) if any(isinstance(point, (list, tuple)) and len(point) > 1
                  and _finite(point[0]) and _finite(point[1]) for point in row.get("points", []))]
        if self._selected_id == "resource_opportunity" and series:
            threshold_row = QHBoxLayout()
            threshold_row.setSpacing(12)
            threshold_row.addWidget(_label("比较阈值 · 四资源统一", "AnalysisMuted"), 1)
            reserve_input = QDoubleSpinBox()
            reserve_input.setRange(0, 1000000)
            reserve_input.setDecimals(0)
            reserve_input.setSingleStep(100)
            reserve_input.setValue(self._reserve)
            reserve_input.setSuffix(" 资源")
            reserve_input.setFixedWidth(126)
            reserve_input.setKeyboardTracking(False)
            reserve_input.setFont(make_ui_font(13))
            reserve_input.setAccessibleName("四类资源统一库存阈值；零仅为比较基线")
            reserve_input.editingFinished.connect(lambda widget=reserve_input: self.set_reserve(widget.value()))
            threshold_row.addWidget(reserve_input)
            layout.addLayout(threshold_row)
        if series:
            units = list(dict.fromkeys(str(row.get("unit") or "") for row in series))
            needs_selection = result.get("chart_type") not in ("heat", "tracks") and (len(series) > 4 or len(units) > 1)
            selected_series = min(self._series_choice.get(self._selected_id, 0), len(series)-1)
            if needs_selection:
                curve_selector = ClickComboBox()
                curve_selector.setMinimumWidth(0)
                curve_selector.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                curve_selector.setFont(make_ui_font(13))
                curve_selector.setMaxVisibleItems(8)
                curve_selector.setAccessibleName("选择图表曲线")
                for row in series:
                    curve_selector.addItem(str(row.get("label") or "已有记录"))
                curve_selector.setCurrentIndex(selected_series)
                layout.addWidget(curve_selector)
            shown = [series[selected_series]] if needs_selection else series
            shown_result = dict(result, series=shown)
            self._chart = AnalysisChart(shown_result, self._model.get("duration") or 0)
            layout.addWidget(self._chart)
            self._chart.set_cursor(self._seconds)
            unit_label = _label("", "AnalysisMuted", False)
            def set_unit(rows):
                unit = "、".join(dict.fromkeys(str(row.get("unit") or "") for row in rows))
                seconds = result.get("bin_seconds")
                unit_label.setText(f"每{_number(seconds)}秒 · {unit}" if _finite(seconds) and seconds > 0 else unit)
            set_unit(shown)
            layout.addWidget(unit_label)
            if needs_selection:
                identity = self._selected_id
                def choose_curve(index):
                    if not 0 <= index < len(series):
                        return
                    self._series_choice[identity] = index
                    old_chart = self._chart
                    position = layout.indexOf(old_chart)
                    new_chart = AnalysisChart(dict(result, series=[series[index]]), self._model.get("duration") or 0)
                    new_chart.setFixedHeight(old_chart.height())
                    layout.removeWidget(old_chart)
                    old_chart.deleteLater()
                    self._chart = new_chart
                    layout.insertWidget(position, new_chart)
                    new_chart.set_cursor(self._seconds)
                    set_unit([series[index]])
                curve_selector.currentIndexChanged.connect(choose_curve)
        observations = result.get("unit_observations") or []
        if observations:
            layout.addWidget(_label("单位位置观测", "AnalysisSection"))
            selector = ClickComboBox()
            selector.setMinimumWidth(0)
            selector.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            selector.setFont(make_ui_font(13))
            selector.setMaxVisibleItems(8)
            selector.setAccessibleName("选择单位的位置观测；按玩家和实体编号识别")
            labels = player_labels(self._model.get("players") or [])
            selected = 0
            for index, group in enumerate(observations):
                name = labels.get(group.get("player_id"), "身份未提供")
                selector.addItem(f"{name} · 实体 {group.get('entity_id', '未提供')}", index)
                if (group.get("player_id"), group.get("entity_id")) == self._observation_key:
                    selected = index
            selector.setCurrentIndex(selected)
            layout.addWidget(selector)
            observation_chart = ObservationChart()
            self._observation_chart = observation_chart
            def choose(index):
                if 0 <= index < len(observations):
                    group = observations[index]
                    self._observation_key = (group.get("player_id"), group.get("entity_id"))
                    observation_chart.set_group(group)
                    observation_chart.set_cursor(self._seconds)
                    selector.setAccessibleDescription(selector.itemText(index))
            selector.currentIndexChanged.connect(choose)
            choose(selected)
            layout.addWidget(observation_chart)
            layout.addWidget(_label("离散位置观测 · 原始坐标（未标定）", "AnalysisMuted"))
        if not series and not observations:
            if result.get("status") == "loading":
                empty_text = "正在分析…"
            elif self._selected_id in ("scouting", "threat_response") and result.get("summary"):
                empty_text = result["summary"]
            else:
                missing = result.get("missing") or []
                empty_text = ("缺少" + str(missing[0]).removeprefix("缺少").rstrip("。") + "，暂无可靠图表。") if missing else "当前数据不足，暂无可靠图表。"
            layout.addWidget(_label(empty_text, "AnalysisMuted"))
        layout.addStretch()
        self.content.setWidget(body)
        self._content_body = body
        body.installEventFilter(self)
        self.content.verticalScrollBar().setValue(0)
        self._rendering_result = False
        self._try_restore_scroll()

    def _try_restore_scroll(self, *_args):
        """等待当前异步正文有真实滚动范围后恢复，不给加载短文夹成零。"""
        pending = self._pending_scroll_restore
        if pending is None or self._cleaned or self._rendering_result:
            return
        key, identity, value = pending
        if key != self._pending_key or identity != self._selected_id:
            self._pending_scroll_restore = None
            return
        if not self.is_ready():
            return
        scrollbar = self.content.verticalScrollBar()
        if scrollbar.maximum() > 0 or value == 0:
            self._pending_scroll_restore = None
            scrollbar.setValue(min(scrollbar.maximum(), value))

    def eventFilter(self, watched, event):
        if self._cleaned:
            return False
        # QGraphicsView平移会调用viewport.scroll，连同其子控件一起移动。
        # 两块浮层保持视口锚点；几何回正会正常使旧区域失效，不随世界坐标漂移。
        if watched in self._anchors and event.type() == QEvent.Move and not self._anchoring:
            expected = self._anchors[watched]
            if watched.geometry() != expected:
                self._anchoring = True
                watched.setGeometry(expected)
                self._anchoring = False
        if watched is self._content_viewport and event.type() in (QEvent.Wheel, QEvent.MouseButtonPress):
            self._pending_scroll_restore = None
        elif watched is self._content_body and event.type() == QEvent.LayoutRequest:
            self._try_restore_scroll()
        return super().eventFilter(watched, event)

    def reposition(self, available_rect=None, bottom_reserved=0):
        """尺寸均为逻辑像素；避开底部播放条，不创建全视口透明遮挡层。"""
        if self._cleaned:
            return
        rect = QRect(available_rect) if available_rect is not None else QRect()
        if not rect.isValid() and self._viewport is not None:
            rect = self._viewport.rect()
        self._available_rect = rect
        self._bottom_reserved = max(0, int(bottom_reserved))
        if not rect.isValid():
            return
        compact = rect.width() < 800 or rect.height() < 520
        panel_layout = self.panel.layout()
        panel_layout.setContentsMargins(16, 12, 16, 12) if compact else panel_layout.setContentsMargins(20, 16, 20, 16)
        panel_layout.setSpacing(8 if compact else 12)
        margin = 12 if compact else 24
        top = rect.top() + (16 if compact else 40)
        height = max(100, min(760, rect.bottom() + 1 - top - self._bottom_reserved - margin))
        rail_width = 164 if compact else 180
        rail_rect = QRect(rect.left() + margin, top, rail_width, height)
        self._anchors[self.rail] = rail_rect
        self._anchoring = True
        self.rail.setGeometry(rail_rect)
        gap = 12 if compact else 20
        panel_x = self.rail.geometry().right() + 1 + gap
        panel_width = min(720, rect.right() + 1 - panel_x - margin)
        if panel_width < 270:
            # 极窄视口允许内容卡盖住入口的一部分；关闭按钮始终留在可见范围。
            panel_x = rect.left() + margin + 42
            panel_width = max(120, rect.right() + 1 - panel_x - margin)
        result = (self._model.get("by_id") or {}).get(self._selected_id, {})
        preferred_height = (580 if result.get("unit_observations") else 480 if self._selected_id == "resource_opportunity"
                            and result.get("series") else 400 if result.get("series") else 180)
        panel_height = min(preferred_height, height)
        panel_rect = QRect(panel_x, top, panel_width, panel_height)
        self._anchors[self.panel] = panel_rect
        self.panel.setGeometry(panel_rect)
        if self._chart is not None:
            self._chart.setFixedHeight(max(160, min(222, panel_height - 124)))
        self._anchoring = False
        self.rail.raise_()
        if self._enabled and self._selected_id:
            self.panel.raise_()

    def capture_state(self):
        return {"selected_id": self._selected_id, "navigation_scroll": self.navigation.verticalScrollBar().value(),
                "content_scroll": self.content.verticalScrollBar().value(), "player_id": self._player_id,
                "seconds": self._seconds, "reserve": self._reserve, "observation_key": self._observation_key}

    def set_reserve(self, value):
        """库存阈值由用户输入；仅在结束编辑且数值改变时重新计算。"""
        if not _finite(value) or float(value) == self._reserve or self._cleaned:
            return
        self._reserve = max(0.0, float(value))
        self._rendered_result = None
        self._rebuild_model()

    def set_loading(self, loading):
        """后台补源只更新按钮状态，不重建模型或打断地图播放。"""
        if self._cleaned:
            return
        self._loading = bool(loading)
        for button in self.panel.findChildren(QPushButton):
            if button.property("analysis_more_data"):
                button.setEnabled(not self._loading)
                button.setText("正在补充…" if self._loading else "补充对局数据")
                button.setAccessibleName("正在补充当前对局数据" if self._loading else "补充当前对局的可用数据")

    def restore_state(self, state):
        if not isinstance(state, dict) or self._cleaned:
            return
        observation_key = state.get("observation_key")
        if isinstance(observation_key, (list, tuple)) and len(observation_key) == 2:
            self._observation_key = tuple(observation_key)
        self.set_reserve(state.get("reserve", self._reserve))
        self.set_time(state.get("seconds", self._seconds), state.get("player_id", self._player_id))
        identity = state.get("selected_id")
        if identity in self.buttons:
            if self._selected_id != identity:
                self.select_analysis(identity)
        else:
            self.close_panel()
        self.navigation.verticalScrollBar().setValue(int(state.get("navigation_scroll") or 0))
        if identity in self.buttons:
            self._pending_scroll_restore = (self._pending_key, identity, max(0, int(state.get("content_scroll") or 0)))
            self._try_restore_scroll()

    def cleanup(self):
        if self._cleaned:
            return
        self._cleaned = True
        self._delivery_timer.stop()
        self._workers.clear()
        self._pending_key = None
        self._results = None  # 已启动任务只保留自己的纯Python队列，晚到结果可自然释放。
        self._generation += 1
        self._enabled = False
        self._models.clear()
        self._context.clear()
        self._chart = None
        self.rail.hide()
        self.panel.hide()
        self.rail.deleteLater()
        self.panel.deleteLater()
