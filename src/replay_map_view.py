"""二维操作坐标主视图；只展示回放证据点，不重建地形或单位真实运动轨迹。"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right

from app_fonts import make_ui_font, register_ui_fonts
from PyQt5.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import (QComboBox, QFileDialog, QFrame, QGraphicsItem, QGraphicsScene, QGraphicsView,
                            QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider,
                            QStackedWidget, QVBoxLayout, QWidget)

from choice_controls import ClickComboBox
from full_map_view import FullMapView
from map_pack_location import load_map_pack_location, save_map_pack_location
from player_labels import player_labels


PLAYER_COLORS = ("#62d9b5", "#f2ae72", "#78b8f1", "#c6a0ed", "#e388a3", "#d5cd79", "#91c7d0", "#c1c8d2")
RECENT_SECONDS = 20.0


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _clock(seconds):
    value = max(0, int(seconds))
    return f"{value // 60:02d}:{value % 60:02d}"


def _number(value):
    return f"{value:.1f}".rstrip("0").rstrip(".") if _finite(value) else "未提供"


def _point(event):
    return QPointF(float(event["x"]), -float(event["y"]))


def _kind(event):
    kind = event.get("kind")
    return "birth" if kind in ("birth", "spawn", "created", "initial") else "death" if kind in ("death", "loss", "destroyed") else "command"


def _legend_caption(metrics, name, available_width):
    """窄图例优先保留账号或局内编号，只截短昵称，完整标签仍在提示中。"""
    nickname, separator, identity = name.rpartition(" · ")
    if separator and identity.startswith(("账号", "局内", "席位")):
        suffix = separator + identity
        remaining = available_width - metrics.horizontalAdvance(suffix)
        if remaining > 0:
            return metrics.elidedText(nickname, Qt.ElideRight, remaining) + suffix
        return metrics.elidedText(name, Qt.ElideLeft, available_width)
    return metrics.elidedText(name, Qt.ElideRight, available_width)


class _EventLayer(QGraphicsItem):
    """历史点批量绘制，近期点单独强调；不创建连线或持续单位位置。"""

    def __init__(self):
        super().__init__()
        self.events, self.filtered_events, self.recent_events, self.history_events = [], [], [], []
        self.colors = {}
        self.selected = None
        self._rect = QRectF(-100, -100, 200, 200)
        self._times = []

    def boundingRect(self):
        return QRectF(self._rect)

    def set_events(self, events, bounds, colors):
        self.prepareGeometryChange()
        self.events, self.colors = events, colors
        self._rect = bounds.adjusted(-bounds.width() * .3, -bounds.height() * .3, bounds.width() * .3, bounds.height() * .3)
        self.filter_player(None)

    def filter_player(self, player):
        self.filtered_events = [event for event in self.events if player is None or event.get("player_id") == player]
        self._times = [event["time"] for event in self.filtered_events]

    def seek(self, seconds):
        stop = bisect_right(self._times, seconds + 1e-8)
        recent = bisect_left(self._times, max(0, seconds - RECENT_SECONDS))
        self.history_events = self.filtered_events[:min(recent, stop)]
        self.recent_events = self.filtered_events[min(recent, stop):stop]
        self.update()

    def pick(self, position, scene_radius):
        best, distance = None, scene_radius * scene_radius
        # 新点优先，重叠坐标不误选到更早的同位置操作。
        for event in reversed(self.history_events + self.recent_events):
            point = _point(event)
            candidate = (point.x() - position.x()) ** 2 + (point.y() - position.y()) ** 2
            if candidate < distance:
                best, distance = event, candidate
        return best

    @staticmethod
    def _glyph(painter, point, radius, color, kind):
        pen = QPen(color, 1.5)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if kind == "birth":
            painter.setBrush(color)
            painter.drawPolygon(QPolygonF([point + QPointF(0, -radius), point + QPointF(radius, 0),
                                           point + QPointF(0, radius), point + QPointF(-radius, 0)]))
        elif kind == "death":
            painter.drawLine(point + QPointF(-radius, -radius), point + QPointF(radius, radius))
            painter.drawLine(point + QPointF(-radius, radius), point + QPointF(radius, -radius))
        else:
            painter.drawEllipse(point, radius, radius)

    def paint(self, painter, option, widget=None):
        scale = max(.000001, abs(painter.worldTransform().m11()))
        grouped = {}
        for event in self.history_events:
            color = QColor(self.colors.get(event.get("player_id"), PLAYER_COLORS[0]))
            color.setAlpha(48)
            if _kind(event) == "command":
                grouped.setdefault(event.get("player_id"), []).append(_point(event))
            else:
                self._glyph(painter, _point(event), 2.5 / scale, color, _kind(event))
        for player, points in grouped.items():
            color = QColor(self.colors.get(player, PLAYER_COLORS[0]))
            color.setAlpha(48)
            pen = QPen(color, 2.2, Qt.SolidLine, Qt.RoundCap)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawPoints(QPolygonF(points))
        for event in self.recent_events:
            color = QColor(self.colors.get(event.get("player_id"), PLAYER_COLORS[0]))
            self._glyph(painter, _point(event), 4.2 / scale, color, _kind(event))
        if self.selected is not None:
            pen = QPen(QColor("#f7f1d8"), 1.6)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(_point(self.selected), 8 / scale, 8 / scale)


class _CoordinateCanvas(QGraphicsView):
    pointSelected = pyqtSignal(object)
    zoomChanged = pyqtSignal(float)

    def __init__(self, layer, parent=None):
        scene = QGraphicsScene(parent)
        super().__init__(scene, parent)
        self.layer = layer
        scene.addItem(layer)
        self.setFrameShape(QFrame.NoFrame)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setBackgroundBrush(QColor("#0f0f11"))
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setAccessibleName("战场操作坐标画布；滚轮缩放，按住左键拖动")
        self.extent = QRectF(-100, -100, 200, 200)
        self.legend = []
        self.user_camera = False
        self._fit_scale = 1.0
        self._press = None

    def set_extent(self, extent):
        self.extent = QRectF(extent)
        # 这是可平移的坐标画布范围，不绘制为游戏地图边界。
        self.setSceneRect(extent.adjusted(-extent.width() * 50, -extent.height() * 50,
                                         extent.width() * 50, extent.height() * 50))
        self.fit_all()

    def fit_all(self):
        self.resetTransform()
        self.fitInView(self.extent, Qt.KeepAspectRatio)
        self._fit_scale = max(.000001, self.transform().m11())
        self.user_camera = False
        self.zoomChanged.emit(1.0)

    def zoom_at(self, position, factor):
        before = self.mapToScene(position)
        relative = self.transform().m11() / self._fit_scale
        target = min(60.0, max(.15, relative * factor))
        if abs(target - relative) < 1e-9:
            return
        self.scale(target / relative, target / relative)
        after = self.mapToScene(position)
        self.translate(after.x() - before.x(), after.y() - before.y())
        self.user_camera = True
        self.zoomChanged.emit(target)

    def wheelEvent(self, event):
        if event.angleDelta().y():
            self.zoom_at(event.pos(), 1.2 ** (event.angleDelta().y() / 120))
            event.accept()
        else:
            super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.user_camera:
            self.fit_all()

    def mousePressEvent(self, event):
        self._press = event.pos() if event.button() == Qt.LeftButton else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is not None and (event.pos() - self._press).manhattanLength() > 4:
            self.user_camera = True
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        click = self._press is not None and (event.pos() - self._press).manhattanLength() <= 4
        super().mouseReleaseEvent(event)
        self._press = None
        if click:
            event_point = self.layer.pick(self.mapToScene(event.pos()), 8 / max(.000001, self.transform().m11()))
            if event_point is not None:
                self.pointSelected.emit(event_point)

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        scale = max(.000001, self.transform().m11())
        raw_step = 75 / scale
        power = 10 ** math.floor(math.log10(max(raw_step, .000001)))
        step = next(value * power for value in (1, 2, 5, 10) if value * power >= raw_step)
        pen = QPen(QColor("#252529"), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for index in range(math.floor(rect.left() / step), math.ceil(rect.right() / step) + 1):
            x = index * step
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        for index in range(math.floor(rect.top() / step), math.ceil(rect.bottom() / step) + 1):
            y = index * step
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        self._grid_step = step

    def drawForeground(self, painter, rect):
        painter.save()
        painter.resetTransform()
        font = make_ui_font()
        font.setPixelSize(10)
        painter.setFont(font)
        step = getattr(self, "_grid_step", 100)
        painter.setPen(QColor("#73737d"))
        for index in range(math.floor(rect.left() / step), math.ceil(rect.right() / step) + 1):
            point = self.mapFromScene(QPointF(index * step, rect.top()))
            painter.drawText(point.x() + 3, self.viewport().height() - 6, _number(index * step))
        for index in range(math.floor(rect.top() / step), math.ceil(rect.bottom() / step) + 1):
            point = self.mapFromScene(QPointF(rect.left(), index * step))
            painter.drawText(4, point.y() - 4, _number(-index * step))
        left, top = 12, 19
        for name, color in self.legend:
            name = _legend_caption(painter.fontMetrics(), name, min(230, max(40, self.viewport().width() - 49)))
            width = painter.fontMetrics().horizontalAdvance(name) + 25
            if left + width > self.viewport().width() - 12:
                left, top = 12, top + 18
            painter.fillRect(QRectF(left - 4, top - 13, width, 18), QColor(13, 23, 32, 230))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(color))
            painter.drawEllipse(QPointF(left + 2, top - 4), 3, 3)
            painter.setPen(QColor("#cbd9df"))
            painter.drawText(left + 11, top, name)
            left += width + 5
        if not self.layer.filtered_events:
            painter.setPen(QColor("#9cafb8"))
            painter.drawText(self.viewport().rect(), Qt.AlignCenter, "这份资料尚无可定位的操作坐标")
        elif not self.layer.history_events and not self.layer.recent_events:
            painter.setPen(QColor("#9cafb8"))
            painter.drawText(self.viewport().rect(), Qt.AlignCenter, "当前时刻尚无已记录操作点，播放或拖动时间轴查看")
        painter.restore()


class ReplayMapView(QWidget):
    """二维主视图；seek（定位时间）和两个信号供父页面同步统计与事件详情。"""

    timeSelected = pyqtSignal(float)
    eventSelected = pyqtSignal(dict)

    def __init__(self, model=None, parent=None, *, map_data_dir=None):
        super().__init__(parent)
        self.map_data_dir = map_data_dir
        self.setObjectName("ReplayMapView")
        self.setMinimumWidth(0)
        self.setMinimumHeight(320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.model, self.events, self.players = {}, [], []
        self.duration, self.current_time = 0.0, 0.0
        self._selected = None
        self._cleaned = False
        self.layer = _EventLayer()
        self.canvas = _CoordinateCanvas(self.layer, self)
        self.canvas.pointSelected.connect(self.select_event)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.title = QLabel("战场坐标")
        self.title.setStyleSheet("font-size:14px;font-weight:600;color:#f4f4f5;")
        toolbar.addWidget(self.title)
        self.view_mode = ClickComboBox()
        self.view_mode.setAccessibleName("地图视图")
        self.view_mode.addItem("对局记录", "records")
        self.view_mode.addItem("官方地图参考", "reference")
        self.view_mode.setFixedWidth(132)
        toolbar.addWidget(self.view_mode)
        self.map_pack_button = self._button("地图资料", self.choose_map_pack)
        self.map_pack_button.setToolTip("内置地图资料缺失时，可选择已有地图资料包。")
        toolbar.addWidget(self.map_pack_button)
        self.player_filter = QComboBox()
        self.player_filter.setMinimumWidth(120)
        self.player_filter.setMaximumWidth(240)
        self.player_filter.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.player_filter.setMinimumContentsLength(8)
        self.player_filter.setAccessibleName("显示的玩家")
        self.player_filter.currentIndexChanged.connect(self._filter_changed)
        toolbar.addWidget(self.player_filter)
        toolbar.addStretch()
        self.zoom_label = QLabel("100%")
        self.zoom_label.setFixedWidth(42)
        self.canvas.zoomChanged.connect(self._update_zoom_label)
        toolbar.addWidget(self.zoom_label)
        self.fit_button = self._button("适应全部", self.fit_all)
        self.reset_button = self._button("复位", self.reset)
        self.reset_button.setToolTip("暂停并回到00:00，同时恢复完整坐标视野")
        toolbar.addWidget(self.fit_button)
        toolbar.addWidget(self.reset_button)
        layout.addLayout(toolbar)
        self.notice = QLabel("操作坐标视图 · 地形/完整单位位置尚未提供")
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet("color:#a4a4ad;font-size:11px;")
        layout.addWidget(self.notice)
        self.map_stack = QStackedWidget()
        # 隐藏参考层的工具栏不能挤高对局画布；两种视图只分配当前模式所需空间。
        self.map_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.map_stack.setMinimumHeight(160)
        self.map_stack.addWidget(self.canvas)
        self.official_map = FullMapView(self)
        saved_pack = load_map_pack_location(self.map_data_dir)
        # 升级后优先使用内置紧凑高程图，旧版保存的大资料包仅作缺失时的后备。
        if saved_pack is not None and self.official_map.catalog.data_status().get("state") != "ready":
            self.official_map.catalog.set_root(saved_pack)
        self.official_map.zoomChanged.connect(self._update_zoom_label)
        self.map_stack.addWidget(self.official_map)
        layout.addWidget(self.map_stack, 1)
        timebar = QHBoxLayout()
        timebar.setSpacing(6)
        self.back_button = self._button("−5秒", lambda: self.seek(self.current_time - 5))
        self.play_button = self._button("播放", self.toggle_playback)
        self.forward_button = self._button("+5秒", lambda: self.seek(self.current_time + 5))
        for button in (self.back_button, self.play_button, self.forward_button):
            timebar.addWidget(button)
        self.speed = QComboBox()
        self.speed.setAccessibleName("播放速度")
        for rate in (.5, 1, 2, 4, 8):
            self.speed.addItem(f"{rate:g}×", rate)
        self.speed.setCurrentIndex(1)
        self.speed.setFixedWidth(62)
        self.speed.currentIndexChanged.connect(self._speed_changed)
        timebar.addWidget(self.speed)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimumWidth(80)
        self.slider.setAccessibleName("回放时间")
        self.slider.sliderPressed.connect(self.pause)
        self.slider.valueChanged.connect(lambda value: self.seek(value / 10))
        timebar.addWidget(self.slider, 1)
        self.clock_label = QLabel("00:00 / 00:00")
        self.clock_label.setStyleSheet("font-family:'Sen','Source Han Sans SC','Microsoft YaHei UI';font-size:12px;color:#e4e4e7;")
        self.clock_label.setFixedWidth(100)
        timebar.addWidget(self.clock_label)
        layout.addLayout(timebar)
        self.scope = QLabel("网格仅为坐标参照 · 高亮最近20秒，早前操作淡显")
        self.scope.setStyleSheet("font-size:10px;color:#929299;")
        layout.addWidget(self.scope)
        self.detail_heading = QLabel("点选操作点，查看时间、玩家与动作")
        self.detail_coordinates = QLabel("涉及实体与目标坐标将在这里显示")
        for label in (self.detail_heading, self.detail_coordinates):
            label.setTextFormat(Qt.PlainText)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            label.setStyleSheet("font-size:11px;color:#c4c4cb;")
            layout.addWidget(label)
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._advance)
        self.elapsed = QElapsedTimer()
        self.setStyleSheet("""
            QWidget#ReplayMapView QPushButton, QWidget#ReplayMapView QComboBox {
                min-height:0;max-height:28px;padding:4px 7px;border:1px solid #303035;
                border-radius:5px;background:#202023;color:#d6d6db;font-size:11px;
            }
            QWidget#ReplayMapView QPushButton:hover {background:#2a2a30;border-color:#777781;}
            QWidget#ReplayMapView QPushButton:focus {border-color:#c4c4cb;}
            QWidget#ReplayMapView QPushButton:disabled, QWidget#ReplayMapView QComboBox:disabled {
                color:#73737d;background:#19191c;border-color:#303035;
            }
            QWidget#ReplayMapView QSlider::groove:horizontal {height:4px;background:#3e3e44;border-radius:2px;}
            QWidget#ReplayMapView QSlider::sub-page:horizontal {background:#b8bac2;border-radius:2px;}
            QWidget#ReplayMapView QSlider::handle:horizontal {width:10px;margin:-4px 0;background:#e4e4e7;border-radius:4px;}
            QWidget#ReplayMapView QSlider::sub-page:horizontal:disabled {background:#55555f;}
            QWidget#ReplayMapView QSlider::handle:horizontal:disabled {background:#73737d;}
        """)
        self.view_mode.currentIndexChanged.connect(self._view_mode_changed)
        self.set_model(model or {})

    @staticmethod
    def _button(text, callback):
        button = QPushButton(text)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedHeight(28)
        button.clicked.connect(callback)
        return button

    def set_model(self, model):
        self.pause()
        self.model = model or {}
        self.events = [dict(event) for event in self.model.get("events", [])
                       if all(_finite(event.get(key)) for key in ("x", "y", "time")) and event["time"] >= 0]
        self.events.sort(key=lambda event: event["time"])
        self.players = [dict(player) for player in self.model.get("players", [])]
        self.colors = {player.get("id"): PLAYER_COLORS[index % len(PLAYER_COLORS)] for index, player in enumerate(self.players)}
        self.names = player_labels(self.players)
        self.recorded_duration = max(0.0, float(self.model.get("duration"))) if _finite(self.model.get("duration")) else None
        last_event_time = max((event["time"] for event in self.events), default=0.0)
        self.duration = max(self.recorded_duration or 0.0, last_event_time)
        timing = f"录像 {_clock(self.recorded_duration)}" if self.recorded_duration is not None else f"操作记录至 {_clock(last_event_time)}"
        if self.recorded_duration is not None and last_event_time > self.recorded_duration:
            timing += f"（含较晚操作，时间轴至 {_clock(self.duration)}）"
        detail_duration = self.model.get("detail_duration")
        if _finite(detail_duration) and abs(detail_duration - (self.recorded_duration if self.recorded_duration is not None else self.duration)) > 1:
            timing += f" / 公开 {_clock(detail_duration)}（按录像时钟播放）"
        notice = f"操作坐标视图 · 地形/完整单位位置尚未提供 · 已定位{len(self.events):,}个点 · {timing}"
        self.notice.setText(notice)
        self.notice.setAccessibleName(notice)
        coverage = self.model.get("coverage") or {}
        explanation = (notice + f"\n原始记录：{coverage.get('commands_total', '未提供')}；"
                       f"未定位玩家指令：{coverage.get('unlocated', '未提供')}。"
                       "\n只展示记录中的真实目标坐标；网格并非真实地图边界，不把操作目标连成单位轨迹。")
        self.notice.setToolTip(explanation)
        self.notice.setAccessibleDescription(explanation)
        self.clock_label.setToolTip(timing + "；不拉伸操作时间来套用公开对局时长。")
        self.player_filter.blockSignals(True)
        self.player_filter.clear()
        self.player_filter.addItem("全部玩家", None)
        for player in self.players:
            self.player_filter.addItem(self.names[player.get("id")], player.get("id"))
            self.player_filter.setItemData(self.player_filter.count() - 1, self.names[player.get("id")], Qt.ToolTipRole)
        self.player_filter.blockSignals(False)
        self.slider.setRange(0, math.ceil(self.duration * 10))
        self._selected = self.layer.selected = None
        self.detail_heading.setText("点选操作点，查看时间、玩家与动作")
        self.detail_coordinates.setText("涉及实体与目标坐标将在这里显示")
        bounds = self._extent(self.events)
        self.layer.set_events(self.events, bounds, self.colors)
        self.canvas.legend = [(self.names[player.get("id")], self.colors[player.get("id")]) for player in self.players]
        self._update_legend_description()
        self.canvas.set_extent(bounds)
        self.official_map.set_match_map(self.model.get("map_name"))
        self.view_mode.setCurrentIndex(0)
        self._view_mode_changed()
        self.seek(0, emit=False)

    def _reference_active(self):
        return self.view_mode.currentData() == "reference"

    def _view_mode_changed(self):
        reference = self._reference_active()
        if reference:
            self.pause()
        self.title.setText("地图图鉴" if reference else "战场坐标")
        self.player_filter.setVisible(not reference)
        self.map_pack_button.setVisible(reference and self.official_map.catalog.data_status().get("state") != "ready")
        # 完整地图工具栏已有适应与缩放；参考模式不重复摆放外层同类按钮。
        self.fit_button.setVisible(not reference)
        self.reset_button.setVisible(not reference)
        for widget in (self.notice, self.scope, self.detail_heading, self.detail_coordinates):
            widget.setVisible(not reference)
        for widget in (self.back_button, self.play_button, self.forward_button, self.speed, self.slider):
            widget.setEnabled(not reference)
            widget.setToolTip("官方地图参考不按本场时间播放；切回对局记录后可使用时间轴。" if reference else "")
        self.fit_button.setText("适应全图" if reference else "适应全部")
        self.reset_button.setToolTip("恢复参考图的完整视野，不改变对局时间。" if reference else
                                    "暂停并回到00:00，同时恢复完整坐标视野")
        self.layout().activate()
        self.map_stack.setCurrentWidget(self.official_map if reference else self.canvas)
        self._update_zoom_label()

    def choose_map_pack(self):
        """选择独立地图包，成功后记住位置；不改变对局、时间和镜头。"""
        dialog = QFileDialog(self, "选择地图资料包")
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        dialog.setFileMode(QFileDialog.ExistingFile)
        dialog.setNameFilter("地图资料包 (*.aoe4maps)")
        dialog.setLabelText(QFileDialog.Accept, "选择")
        dialog.setLabelText(QFileDialog.Reject, "取消")
        if not dialog.exec_() or not dialog.selectedFiles():
            return
        self.use_map_pack(dialog.selectedFiles()[0])

    def use_map_pack(self, path):
        """资料包接入与文件选择分开，便于验证真实目录和错误状态。"""
        catalog = self.official_map.catalog
        previous = catalog.root
        catalog.set_root(path)
        status = catalog.data_status()
        if status.get('state') != 'ready':
            catalog.set_root(previous)
            self.official_map.status_label.setText(status.get('message_zh') or '地图资料包无法读取。')
            return False
        self.official_map.refresh_tiles()
        try:
            save_map_pack_location(self.map_data_dir, path)
        except OSError:
            self.official_map.status_label.setText('地图已载入，资料位置未能保存；下次启动需重新选择。')
        self._view_mode_changed()
        return True

    def _update_zoom_label(self, *_args):
        if self._reference_active():
            ratio = self.official_map.canvas.zoom_ratio()
        else:
            ratio = self.canvas.transform().m11() / max(.000001, self.canvas._fit_scale)
        self.zoom_label.setText(f"{ratio * 100:.0f}%")

    @staticmethod
    def _extent(events):
        if not events:
            return QRectF(-100, -100, 200, 200)
        xs, ys = [event["x"] for event in events], [-event["y"] for event in events]
        width, height = max(20, max(xs) - min(xs)), max(20, max(ys) - min(ys))
        return QRectF((min(xs) + max(xs)) / 2 - width / 2, (min(ys) + max(ys)) / 2 - height / 2, width, height).adjusted(-width * .08, -height * .08, width * .08, height * .08)

    def _filter_changed(self):
        player = self.player_filter.currentData()
        self.player_filter.setToolTip(self.player_filter.currentText())
        self.layer.filter_player(player)
        self.canvas.legend = [(self.names[key], color) for key, color in self.colors.items() if player is None or player == key]
        self._update_legend_description()
        if self._selected is not None and player is not None and self._selected.get("player_id") != player:
            self._clear_selection()
        self.layer.seek(self.current_time)
        self._update_scope()
        self.canvas.viewport().update()

    def _update_legend_description(self):
        """完整身份说明不受图例宽度裁切影响，无障碍与悬停提示保持一致。"""
        names = "\n".join(name for name, _color in self.canvas.legend)
        self.canvas.setToolTip(names)
        self.canvas.setAccessibleDescription(names)

    def _update_scope(self):
        kinds = {_kind(event) for event in self.layer.filtered_events}
        legend = "圆环为操作目标" + ("，菱形为出现点" if "birth" in kinds else "") + ("，叉号为阵亡点" if "death" in kinds else "")
        self.scope.setText(f"网格仅为坐标参照 · 最近20秒 {len(self.layer.recent_events)} 个点，历史 {len(self.layer.history_events)} 个点 · {legend}")

    def _clear_selection(self):
        self._selected = self.layer.selected = None
        self.detail_heading.setText("点选操作点，查看时间、玩家与动作")
        self.detail_coordinates.setText("涉及实体与目标坐标将在这里显示")
        self.detail_heading.setToolTip("")
        self.detail_coordinates.setToolTip("")

    def seek(self, seconds, emit=True):
        if self._cleaned or not _finite(seconds):
            return
        value = min(self.duration, max(0.0, float(seconds)))
        changed = abs(value - self.current_time) > 1e-8
        self.current_time = value
        self.slider.blockSignals(True)
        self.slider.setValue(round(value * 10))
        self.slider.blockSignals(False)
        self.clock_label.setText(f"{_clock(value)} / {_clock(self.duration)}")
        if self._selected is not None and self._selected["time"] > value:
            self._clear_selection()
        self.layer.seek(value)
        self._update_scope()
        if value >= self.duration:
            self.pause()
        if changed and emit:
            self.timeSelected.emit(value)

    def select_event(self, event, *, seek=True, emit=True):
        if seek:
            self.seek(event["time"])
        self._selected = self.layer.selected = event
        self.layer.update()
        name = self.names.get(event.get("player_id"), f"玩家 {event.get('player_id')}")
        action = str(event.get("label") or "操作目标")
        self.detail_heading.setText(f"{_clock(event['time'])}　{name}　{action}")
        units = event.get("units") or []
        units = units if isinstance(units, (list, tuple)) else [units]
        identities = "、".join(str(value) for value in units[:5]) or "未提供"
        if len(units) > 5:
            identities += f"…共{len(units)}个"
        target = event.get("target") or {}
        target = target if isinstance(target, dict) else {}
        position = f"横向X {_number(event['x'])}，纵向Z {_number(event['y'])}"
        text = f"涉及实体ID：{identities}　目标坐标：{position}"
        if event.get("target_entity_id") is not None:
            text += f"　目标实体：{event['target_entity_id']}"
        self.detail_coordinates.setText(text)
        self.detail_heading.setToolTip(self.detail_heading.text())
        self.detail_coordinates.setToolTip(text + "\n完整实体ID：" + ("、".join(map(str, units)) or "未提供")
                                           + f"\n高度Y：{_number(target.get('y'))}")
        if emit:
            self.eventSelected.emit(dict(event))

    def capture_view_state(self):
        """复制观看状态，不复制或改写对局模型；编号始终使用原始局内编号。"""
        center = self.canvas.mapToScene(self.canvas.viewport().rect().center())
        extent = self.canvas.extent
        return {"time": self.current_time, "player_id": self.player_filter.currentData(),
                "speed": self.speed.currentData(), "playing": self.timer.isActive(),
                "view_mode": self.view_mode.currentData(),
                "official_map": self.official_map.capture_view_state(),
                "selected_event_id": self._selected.get("id") if self._selected is not None else None,
                "camera": {"extent": (extent.x(), extent.y(), extent.width(), extent.height()),
                           "center": (center.x(), center.y()),
                           "zoom": self.canvas.transform().m11() / max(.000001, self.canvas._fit_scale),
                           "user_camera": self.canvas.user_camera}}

    def restore_view_state(self, state):
        """在另一尺寸的地图恢复时间、身份和镜头；恢复选点不把时间退回选点时刻。"""
        if self._cleaned or not isinstance(state, dict):
            return False
        self.pause()
        self._clear_selection()
        player_index = self.player_filter.findData(state.get("player_id"))
        self.player_filter.setCurrentIndex(max(0, player_index))
        self._filter_changed()
        speed_index = self.speed.findData(state.get("speed"))
        if speed_index >= 0:
            self.speed.setCurrentIndex(speed_index)
        self.seek(state.get("time", 0), emit=False)
        selected_id = state.get("selected_event_id")
        if selected_id is not None:
            selected = next((event for event in self.layer.filtered_events
                             if event.get("id") == selected_id and event["time"] <= self.current_time), None)
            if selected is not None:
                self.select_event(selected, seek=False, emit=False)
        camera = state.get("camera")
        if isinstance(camera, dict):
            extent = camera.get("extent")
            if (isinstance(extent, (list, tuple)) and len(extent) == 4
                    and all(_finite(value) for value in extent) and extent[2] > 0 and extent[3] > 0):
                self.canvas.set_extent(QRectF(*extent))
            else:
                self.canvas.fit_all()
            zoom = camera.get("zoom")
            if _finite(zoom):
                zoom = max(.15, min(60.0, zoom))
                self.canvas.scale(zoom, zoom)
                self.canvas.zoomChanged.emit(zoom)
            center = camera.get("center")
            if isinstance(center, (list, tuple)) and len(center) == 2 and all(_finite(value) for value in center):
                self.canvas.centerOn(QPointF(*center))
            self.canvas.user_camera = bool(camera.get("user_camera"))
        saved_pack = load_map_pack_location(self.map_data_dir)
        if (saved_pack is not None and saved_pack != self.official_map.catalog.root
                and self.official_map.catalog.data_status().get("state") != "ready"):
            self.official_map.catalog.set_root(saved_pack)
        self.official_map.restore_view_state(state.get("official_map"))
        mode_index = self.view_mode.findData(state.get("view_mode", "records"))
        self.view_mode.setCurrentIndex(max(0, mode_index))
        self._view_mode_changed()
        if state.get("playing") is True and self.current_time < self.duration and not self._reference_active():
            self.toggle_playback()
        return True

    def fit_all(self):
        if self._reference_active():
            self.official_map.fit_all()
        else:
            self.canvas.set_extent(self._extent(self.layer.filtered_events))

    def reset(self):
        if self._reference_active():
            self.official_map.fit_all()
            return
        self.pause()
        self.seek(0)
        self.fit_all()

    def _speed_changed(self):
        if self.timer.isActive():
            self.elapsed.restart()

    def toggle_playback(self):
        if self._reference_active():
            return
        if self.timer.isActive():
            self.pause()
        elif self.events and self.duration > 0 and not self._cleaned:
            if self.current_time >= self.duration:
                self.seek(0)
            self.elapsed.start()
            self.timer.start()
            self.play_button.setText("暂停")

    def _advance(self):
        if self.timer.isActive():
            elapsed = self.elapsed.restart() / 1000
            self.seek(self.current_time + elapsed * float(self.speed.currentData() or 1))

    def pause(self):
        if hasattr(self, "timer"):
            self.timer.stop()
        if hasattr(self, "play_button"):
            self.play_button.setText("播放")

    def cleanup(self):
        if not self._cleaned:
            self.pause()
            self.official_map.cleanup()
            self._cleaned = True

    def hideEvent(self, event):
        self.pause()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.pause()
        super().closeEvent(event)
