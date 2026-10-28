"""二维操作坐标主视图；只展示回放证据点，不重建地形或单位真实运动轨迹。"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right

from app_fonts import make_ui_font, register_ui_fonts
from PyQt5.QtCore import QElapsedTimer, QEvent, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPolygonF, QWheelEvent
from PyQt5.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QGraphicsItem, QGraphicsScene, QGraphicsView,
                            QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider,
                            QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from choice_controls import ClickComboBox
from player_labels import player_labels
from replay_placement_markers import PlacementMarkerLayer, building_metadata, placement_description
from replay_selection_card import SelectionDetailsCard
from visual import line_icon


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
    """窄图例优先保留账号或局内编号，只截短昵称；完整标签供无障碍读取。"""
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
        self.kinds = set()
        self._drawables = []
        self._visible_indices = None
        self._history_stop = 0
        self._history_groups, self._history_glyphs = {}, []
        self._history_pens, self._history_colors, self._recent_colors = {}, {}, {}
        self._recent_drawables = []

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
        # 只在数据/玩家筛选变化时转换Qt坐标与颜色；拖动镜头不再逐点重复构造。
        self._drawables = [(_point(event), event.get("player_id"), _kind(event))
                           for event in self.filtered_events]
        self.kinds = {kind for _point_value, _player, kind in self._drawables}
        self._history_pens, self._history_colors, self._recent_colors = {}, {}, {}
        for _point_value, identity, _kind_value in self._drawables:
            if identity in self._recent_colors:
                continue
            color = QColor(self.colors.get(identity, PLAYER_COLORS[0]))
            self._recent_colors[identity] = color
            history_color = QColor(color)
            history_color.setAlpha(48)
            self._history_colors[identity] = history_color
            pen = QPen(history_color, 2.2, Qt.SolidLine, Qt.RoundCap)
            pen.setCosmetic(True)
            self._history_pens[identity] = pen
        self._visible_indices = None
        self._history_stop = 0
        self._history_groups, self._history_glyphs = {}, []
        self._recent_drawables = []

    def seek(self, seconds):
        stop = bisect_right(self._times, seconds + 1e-8)
        recent = bisect_left(self._times, max(0, seconds - RECENT_SECONDS))
        history_stop = min(recent, stop)
        indices = (history_stop, stop)
        if indices == self._visible_indices:
            return False
        self.history_events = self.filtered_events[:history_stop]
        self.recent_events = self.filtered_events[history_stop:stop]
        # 正向播放只追加新进入历史区的点；倒退时按原顺序重建，保持重叠点语义。
        if history_stop < self._history_stop:
            self._history_stop = 0
            self._history_groups, self._history_glyphs = {}, []
        for point, identity, kind in self._drawables[self._history_stop:history_stop]:
            if kind == "command":
                points = self._history_groups.get(identity)
                if points is None:
                    points = self._history_groups[identity] = QPolygonF()
                points.append(point)
            else:
                self._history_glyphs.append((point, identity, kind))
        self._history_stop = history_stop
        self._recent_drawables = self._drawables[history_stop:stop]
        self._visible_indices = indices
        self.update()
        return True

    def pick(self, position, scene_radius):
        if not self.isVisible():
            return None
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
        for point, identity, kind in self._history_glyphs:
            self._glyph(painter, point, 2.5 / scale, self._history_colors[identity], kind)
        for identity, points in self._history_groups.items():
            painter.setPen(self._history_pens[identity])
            painter.drawPoints(points)
        for point, identity, kind in self._recent_drawables:
            self._glyph(painter, point, 4.2 / scale, self._recent_colors[identity], kind)
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
        self.placement_layer = None
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
        self.minimal_chrome = False
        self.user_camera = False
        self._fit_scale = 1.0
        self.fit_bottom_inset = 0
        self._press = None
        self.battlefield_rect = None
        self.boundary_confirmed = False
        self.diagnosis_points = []
        self.diagnosis_region = None
        self.diagnosis_caption = ""

    def set_battlefield(self, bounds=None, *, confirmed=False):
        """边框保存在场景坐标中；操作范围与有来源的地图边界分别标示。"""
        self.battlefield_rect = QRectF(bounds) if bounds is not None else None
        self.boundary_confirmed = bool(confirmed and bounds is not None)
        self.setAccessibleDescription("对局模型提供的地图边界" if self.boundary_confirmed else
                                      "操作范围：已记录目标点的包围范围，真实地图边界尚未提供")
        self.viewport().update()

    def set_extent(self, extent):
        self.extent = QRectF(extent)
        # 这是可平移的坐标画布范围，不绘制为游戏地图边界。
        self.setSceneRect(extent.adjusted(-extent.width() * 50, -extent.height() * 50,
                                         extent.width() * 50, extent.height() * 50))
        self.fit_all()

    def fit_all(self):
        self.resetTransform()
        # 全屏的浮动时间条也占显示空间；仅自动取景留位，不改变世界范围或横纵比例。
        width = max(1, self.viewport().width() - 4)
        height = max(1, self.viewport().height() - self.fit_bottom_inset - 4)
        scale = min(width / self.extent.width(), height / self.extent.height())
        self.scale(scale, scale)
        self.centerOn(self.extent.center() + QPointF(0, self.fit_bottom_inset / (2 * scale)))
        self._fit_scale = max(.000001, self.transform().m11())
        self.user_camera = False
        self.zoomChanged.emit(1.0)

    def set_fit_bottom_inset(self, inset):
        inset = max(0, int(inset))
        if self.fit_bottom_inset != inset:
            self.fit_bottom_inset = inset
            if not self.user_camera:
                self.fit_all()

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
        # 浮层子控件在滚动边界可能忽略事件；到达地图前按浮层命中区域收口。
        guard = getattr(self, "wheel_guard", None)
        if callable(guard) and guard(event):
            return
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
            position = self.mapToScene(event.pos())
            scale = max(.000001, self.transform().m11())
            # 诊断层是来源明确的损失证据，独立于指令偏移和历史注记。
            event_point = next((row for row in reversed(self.diagnosis_points)
                                if (_point(row) - position).manhattanLength() <= 10 / scale), None)
            if event_point is None:
                event_point = self.placement_layer.pick(position, scale, scale/max(.000001, self._fit_scale)) if self.placement_layer is not None else None
            if event_point is None:
                event_point = self.layer.pick(position, 8 / scale)
            if event_point is not None:
                self.pointSelected.emit(event_point)

    def drawBackground(self, painter, rect):
        # 每次先清理重绘区域，避免平移后旧网格或边框留在范围之外。
        painter.fillRect(rect, QColor("#0f0f11"))
        frame = self.battlefield_rect
        area = rect.intersected(frame) if frame is not None else rect
        if area.isEmpty():
            return
        painter.save()
        painter.setClipRect(area)
        if frame is not None:
            painter.fillRect(area, QColor("#141417"))
        scale = max(.000001, self.transform().m11())
        raw_step = 75 / scale
        power = 10 ** math.floor(math.log10(max(raw_step, .000001)))
        major = next(value * power for value in (1, 2, 5, 10) if value * power >= raw_step)
        # 原网格每边细分五格，主线保留参照；不修改录像坐标、素材尺寸或镜头比例。
        minor = major / 5
        for step, color in ((minor, "#222227"), (major, "#303037")):
            pen = QPen(QColor(color), 1)
            pen.setCosmetic(True)
            painter.setPen(pen)
            for index in range(math.floor(area.left() / step), math.ceil(area.right() / step) + 1):
                x = index * step
                painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
            for index in range(math.floor(area.top() / step), math.ceil(area.bottom() / step) + 1):
                y = index * step
                painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
        self._grid_step = major
        self._minor_grid_step = minor
        painter.restore()

    def drawForeground(self, painter, rect):
        frame = self.battlefield_rect
        if frame is not None:
            painter.save()
            pen = QPen(QColor("#797985" if self.boundary_confirmed else "#62626e"), 1.4)
            pen.setCosmetic(True)
            pen.setStyle(Qt.SolidLine if self.boundary_confirmed else Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(frame)
            # 界面名称统一为地图范围，来源可信度仍由虚实线及模型字段单独保留。
            corner = self.mapFromScene(frame.topLeft())
            painter.resetTransform()
            font = make_ui_font()
            font.setPixelSize(11)
            painter.setFont(font)
            caption = "地图范围"
            width = painter.fontMetrics().horizontalAdvance(caption) + 14
            label = QRectF(corner.x() + 8, corner.y() + 8, width, 21)
            painter.fillRect(label, QColor("#141417"))
            painter.setPen(QColor("#a4a4ad"))
            painter.drawText(label, Qt.AlignCenter, caption)
            painter.restore()
        if self.placement_layer is not None:
            self.placement_layer.paint(painter, None, relative_zoom=self.transform().m11()/max(.000001, self._fit_scale))
        if self.diagnosis_region is not None:
            painter.save()
            pen = QPen(QColor("#d5b6f2"), 1.5, Qt.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(QColor(174, 143, 204, 15))
            painter.drawRect(self.diagnosis_region)
            painter.setBrush(Qt.NoBrush)
            radius = 5 / max(.000001, self.transform().m11())
            for row in self.diagnosis_points:
                center = _point(row)
                if not rect.adjusted(-radius, -radius, radius, radius).contains(center):
                    continue
                pen.setColor(QColor(row.get("color") or "#e9bec0"))
                pen.setStyle(Qt.SolidLine)
                painter.setPen(pen)
                painter.drawLine(center + QPointF(-radius, -radius), center + QPointF(radius, radius))
                painter.drawLine(center + QPointF(-radius, radius), center + QPointF(radius, -radius))
            corner = self.mapFromScene(self.diagnosis_region.topLeft())
            painter.resetTransform()
            font = make_ui_font()
            font.setPixelSize(12)
            font.setItalic(False)
            painter.setFont(font)
            width = painter.fontMetrics().horizontalAdvance(self.diagnosis_caption) + 16
            caption_box = QRectF(corner.x() + 4, corner.y() + 4, width, 24)
            painter.fillRect(caption_box, QColor("#22212a"))
            painter.setPen(QColor("#decdee"))
            painter.drawText(caption_box, Qt.AlignCenter, self.diagnosis_caption)
            painter.restore()
        painter.save()
        painter.resetTransform()
        font = make_ui_font()
        font.setPixelSize(10)
        painter.setFont(font)
        step = getattr(self, "_grid_step", 100)
        painter.setPen(QColor("#73737d"))
        # 简洁模式只隐藏坐标刻度，玩家身份和当前时刻的空态仍须可见。
        if not self.minimal_chrome:
            for index in range(math.floor(rect.left() / step), math.ceil(rect.right() / step) + 1):
                point = self.mapFromScene(QPointF(index * step, rect.top()))
                painter.drawText(point.x() + 3, self.viewport().height() - 6, _number(index * step))
            for index in range(math.floor(rect.top() / step), math.ceil(rect.bottom() / step) + 1):
                point = self.mapFromScene(QPointF(rect.left(), index * step))
                painter.drawText(4, point.y() - 4, _number(-index * step))
        # 全屏左侧仍为原有分析类别栏，图例从其右侧起排，避免玩家名称被遮住。
        origin = 12 + getattr(self, "legend_left_inset", 0)
        left, top = origin, 19
        for name, color in self.legend:
            name = _legend_caption(painter.fontMetrics(), name, min(230, max(40, self.viewport().width() - origin - 37)))
            width = painter.fontMetrics().horizontalAdvance(name) + 25
            if left + width > self.viewport().width() - 12:
                left, top = origin, top + 18
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
    analysisDataRequested = pyqtSignal()
    diagnosisEvidenceSelected = pyqtSignal(object)

    def __init__(self, model=None, parent=None, *, map_data_dir=None, grid_only=True):
        super().__init__(parent)
        self.map_data_dir = map_data_dir
        self._grid_only = bool(grid_only)
        self.setObjectName("ReplayMapView")
        self.setMinimumWidth(0)
        self.setMinimumHeight(320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.model, self.events, self.players = {}, [], []
        self._dashboard_mode = False
        self._fullscreen_mode = False
        self._reference_clock_enabled = False
        self.duration, self.current_time = 0.0, 0.0
        self._selected = None
        self._cleaned = False
        # 高级分析仅在全屏时创建；嵌入地图不增加常驻图表或播放计时器。
        self.advanced_analysis = None
        self._analysis_context = {}
        self._advanced_analysis_state = {}
        self._advanced_exploration_enabled = True
        self._diagnosis_focus = None
        self.layer = _EventLayer()
        self.canvas = _CoordinateCanvas(self.layer, self)
        self.selection_card = SelectionDetailsCard(self.canvas.viewport())
        self.selection_card.closed.connect(self._clear_selection)
        self.canvas.wheel_guard = self._route_analysis_wheel
        self._routing_analysis_wheel = False
        self.placement_markers_visible = True
        self.operation_points_visible = True
        self.placement_layer = PlacementMarkerLayer(self.canvas.viewport().update)
        self.canvas.placement_layer = self.placement_layer
        self.canvas.pointSelected.connect(self._select_canvas_event)
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
        if not self._grid_only:
            self.view_mode.addItem("官方地图参考", "reference")
        self.view_mode.setFixedWidth(132)
        toolbar.addWidget(self.view_mode)
        self.map_pack_button = self._button("地图资料", self.choose_map_pack)
        self.map_pack_button.setToolTip("内置地图资料缺失时，可选择已有地图资料包。")
        toolbar.addWidget(self.map_pack_button)
        self.player_filter = ClickComboBox()
        # 弹层与本页11像素选框同字号，避免按小字计算宽度却显示全局大字。
        self.player_filter.view().setStyleSheet(self.player_filter.view().styleSheet() +
                                               "QListView#ChoicePopupList {font-size:11px;}")
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
        self._toolbar_widgets = [self.title, self.view_mode, self.map_pack_button, self.player_filter,
                                 self.zoom_label, self.fit_button, self.reset_button]
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
        self.official_map = None
        if not self._grid_only:
            # 历史参考模式只按明确调用延迟创建；正式网格视图不导入或读取地图资料。
            from full_map_view import FullMapView
            from map_pack_location import load_map_pack_location
            self.official_map = FullMapView(self)
            saved_pack = load_map_pack_location(self.map_data_dir)
            if saved_pack is not None and self.official_map.catalog.data_status().get("state") != "ready":
                self.official_map.catalog.set_root(saved_pack)
            self.official_map.zoomChanged.connect(self._update_zoom_label)
            self.map_stack.addWidget(self.official_map)
        for canvas in self._canvases():
            canvas.viewport().installEventFilter(self)
        layout.addWidget(self.map_stack, 1)
        timebar = QHBoxLayout()
        timebar.setSpacing(6)
        self.back_button = self._button("−5秒", lambda: self.seek(self.current_time - 5))
        self.play_button = self._button("播放", self.toggle_playback)
        self.forward_button = self._button("+5秒", lambda: self.seek(self.current_time + 5))
        for button in (self.back_button, self.play_button, self.forward_button):
            timebar.addWidget(button)
        self.speed = ClickComboBox()
        self.speed.view().setStyleSheet(self.speed.view().styleSheet() +
                                       "QListView#ChoicePopupList {font-size:11px;}")
        self.speed.setAccessibleName("播放速度")
        for rate in (.5, 1, 2, 4, 8, 16):
            self.speed.addItem(f"{rate:g}×", rate)
        self.speed.setCurrentIndex(1)
        # 紧凑时间条仍为倍速文字与Lucide下拉箭头留足独立位置。
        self.speed.setFixedWidth(80)
        self.speed.currentIndexChanged.connect(self._speed_changed)
        timebar.addWidget(self.speed)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimumWidth(80)
        self.slider.setAccessibleName("回放时间")
        self.slider.sliderPressed.connect(self.pause)
        self.slider.valueChanged.connect(lambda value: self.seek(value / 10))
        timebar.addWidget(self.slider, 1)
        self.clock_label = QLabel("00:00 / 00:00")
        self.clock_label.setStyleSheet("font-family:'Smiley Sans';font-size:12px;color:#e4e4e7;")
        self.clock_label.setFixedWidth(100)
        timebar.addWidget(self.clock_label)
        self._timebar_widgets = [self.back_button, self.play_button, self.forward_button,
                                 self.speed, self.slider, self.clock_label]
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
        self._create_fullscreen_controls()
        self.set_model(model or {})

    @staticmethod
    def _button(text, callback):
        button = QPushButton(text)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedHeight(28)
        button.clicked.connect(callback)
        return button

    def set_model(self, model):
        self._diagnosis_focus = None
        self.canvas.diagnosis_points = []
        self.canvas.diagnosis_region = None
        self.canvas.diagnosis_caption = ""
        self.pause()
        self.selection_card.set_event(None)
        self._reference_clock_enabled = False
        self.model = model or {}
        self._analysis_context = {"spatial": self.model}
        self._advanced_analysis_state = {}
        if self.advanced_analysis is not None:
            self.advanced_analysis.close_panel()
            self.advanced_analysis.set_context(spatial=self.model)
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
        self.placement_layer.set_events(self.events, bounds, self.colors)
        self.canvas.legend = [(self.names[player.get("id")], self.colors[player.get("id")]) for player in self.players]
        actual_bounds = self._map_bounds(self.model.get("bounds"))
        battlefield = actual_bounds if actual_bounds is not None else (bounds if self.events else None)
        self.canvas.set_battlefield(battlefield, confirmed=actual_bounds is not None)
        self._update_legend_description()
        # 首次适应给边框留出少量余白；边框本身不随播放时间或玩家筛选变化。
        view_bounds = battlefield if battlefield is not None else bounds
        margin = max(view_bounds.width(), view_bounds.height()) * .06
        self.canvas.set_extent(view_bounds.adjusted(-margin, -margin, margin, margin))
        if self.official_map is not None:
            self.official_map.set_match_map(self.model.get("map_name"))
        self.view_mode.setCurrentIndex(0)
        self._view_mode_changed()
        self.seek(0, emit=False)

    def _reference_active(self):
        return self.official_map is not None and self.view_mode.currentData() == "reference"

    def _canvases(self):
        """网格是默认唯一画布；旧参考层仅显式启用后存在。"""
        return (self.canvas,) if self.official_map is None else (self.canvas, self.official_map.canvas)

    def _view_mode_changed(self):
        reference = self._reference_active()
        self.selection_card.set_enabled(not reference)
        if reference and not self._reference_playback_allowed():
            self.pause()
        self.title.setText("地图图鉴" if reference else "战场坐标")
        self.view_mode.setVisible(not self._grid_only)
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
        self._apply_dashboard_presentation()
        self._apply_fullscreen_presentation()

    def _valid_timeline(self):
        """只有录像给出的时长与局内玩家均存在时，才提供全屏时间播放。"""
        duration = getattr(self, "recorded_duration", None)
        return bool(duration is not None and duration > 0
                    and any(player.get("id") is not None for player in self.players))

    def _reference_playback_allowed(self):
        return self._valid_timeline() and (self._fullscreen_mode or self._reference_clock_enabled)

    def _create_fullscreen_controls(self):
        self.fullscreen_empty = QLabel("未选择对局数据", self)
        self.fullscreen_empty.setAlignment(Qt.AlignCenter)
        self.fullscreen_empty.setStyleSheet("background:transparent;color:#929299;font-size:18px;")
        self.fullscreen_empty.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.fullscreen_empty.hide()
        self.fullscreen_panel = QFrame(self)
        self.fullscreen_panel.setObjectName("FullscreenPlayback")
        self.fullscreen_panel.setStyleSheet("QFrame#FullscreenPlayback{background:#161619;border:1px solid #303036;border-radius:10px;}"
            "QFrame#FullscreenPlayback QPushButton{min-height:36px;max-height:36px;padding:0;border:0;border-radius:6px;background:transparent;color:#b8b8c0;}"
            "QFrame#FullscreenPlayback QPushButton:hover{background:#303036;color:#f4f4f5;}"
            "QFrame#FullscreenPlayback QPushButton:checked{background:#e4e4e7;color:#18181b;}"
            "QFrame#FullscreenPlayback QPushButton:focus{border:1px solid #929299;}")
        row = QHBoxLayout(self.fullscreen_panel)
        row.setContentsMargins(8, 8, 8, 8)
        row.setSpacing(6)
        self.fullscreen_play = QPushButton(self.fullscreen_panel)
        self.fullscreen_play.setFixedSize(36, 36)
        self.fullscreen_play.setIconSize(QSize(18, 18))
        self.fullscreen_play.setCursor(Qt.PointingHandCursor)
        self.fullscreen_play.clicked.connect(self.toggle_playback)
        row.addWidget(self.fullscreen_play)
        self.fullscreen_speeds = {}
        for rate in (1, 4, 8, 16):
            button = QPushButton(f"{rate}×", self.fullscreen_panel)
            button.setFixedSize(44, 36)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setAccessibleName(f"{rate}倍播放")
            button.clicked.connect(lambda checked=False, value=rate: self.set_playback_speed(value))
            row.addWidget(button)
            self.fullscreen_speeds[rate] = button
        self.fullscreen_panel.hide()
        self.fullscreen_timeline = QFrame(self)
        self.fullscreen_timeline.setObjectName("FullscreenTimeline")
        self.fullscreen_timeline.setStyleSheet(
            "QFrame#FullscreenTimeline{background:#161619;border:1px solid #303036;border-radius:10px;}"
            "QLabel{background:transparent;color:#e4e4e7;font-size:12px;border:0;}")
        timeline = QHBoxLayout(self.fullscreen_timeline)
        timeline.setContentsMargins(16, 8, 16, 8)
        timeline.setSpacing(16)
        self.fullscreen_slider = QSlider(Qt.Horizontal, self.fullscreen_timeline)
        self.fullscreen_slider.setMinimumWidth(64)
        self.fullscreen_slider.setAccessibleName("全屏回放时间")
        self.fullscreen_slider.setToolTip("")
        self.fullscreen_slider.sliderPressed.connect(self.pause)
        self.fullscreen_slider.valueChanged.connect(self._seek_fullscreen_time)
        timeline.addWidget(self.fullscreen_slider, 1)
        self.fullscreen_clock = QLabel("00:00 / 00:00", self.fullscreen_timeline)
        self.fullscreen_clock.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.fullscreen_clock.setMinimumWidth(96)
        timeline.addWidget(self.fullscreen_clock)
        self.fullscreen_timeline.hide()
        self._sync_fullscreen_controls()

    def _seek_fullscreen_time(self, value):
        if self._valid_timeline():
            self.pause()
            self.seek(value / 10)

    def _sync_fullscreen_timeline(self):
        if not hasattr(self, "fullscreen_slider"):
            return
        blocked = self.fullscreen_slider.blockSignals(True)
        self.fullscreen_slider.setRange(0, self.slider.maximum())
        self.fullscreen_slider.setValue(self.slider.value())
        self.fullscreen_slider.blockSignals(blocked)
        self.fullscreen_slider.setEnabled(self._valid_timeline())
        self.fullscreen_clock.setText(self.clock_label.text())

    def set_playback_speed(self, value):
        index = self.speed.findData(value)
        if index >= 0:
            self.speed.setCurrentIndex(index)
            self._sync_fullscreen_controls()

    def _sync_fullscreen_controls(self):
        if not hasattr(self, "fullscreen_play"):
            return
        playing = self.timer.isActive()
        self.fullscreen_play.setIcon(line_icon("pause" if playing else "play", "#f4f4f5"))
        self.fullscreen_play.setAccessibleName("暂停" if playing else "播放")
        self.fullscreen_play.setToolTip("")
        for rate, button in self.fullscreen_speeds.items():
            button.setChecked(self.speed.currentData() == rate)
        self._sync_fullscreen_timeline()

    def set_fullscreen_mode(self, enabled=True):
        """独立大图只保留地图与浮动播放板；主页面与普通图鉴不采用此显示模式。"""
        self._fullscreen_mode = bool(enabled)
        if enabled:
            if self._advanced_exploration_enabled:
                self._ensure_advanced_analysis()
            self.setMinimumHeight(0)
            self.setAttribute(Qt.WA_StyledBackground, True)
            self.layout().setSpacing(0)
            if self.official_map is not None:
                self.official_map.layout().setSpacing(0)
                self.official_map.canvas.set_initial_fit("cover")
            self.canvas.minimal_chrome = True
            self.setStyleSheet(self.styleSheet() + "QWidget#ReplayMapView{background:#0f0f11;}")
        else:
            self.canvas.minimal_chrome = False
            for widget in self._toolbar_widgets + self._timebar_widgets:
                widget.show()
            self._view_mode_changed()
        self._apply_fullscreen_presentation()

    def _ensure_advanced_analysis(self):
        if self.advanced_analysis is None and not self._cleaned:
            from advanced_analysis_panel import AdvancedAnalysisDock
            self.advanced_analysis = AdvancedAnalysisDock(self.canvas.viewport())
            self.advanced_analysis.requestMoreData.connect(self.analysisDataRequested)
            self.advanced_analysis.set_context(**self._analysis_context)
            self.advanced_analysis.restore_state(self._advanced_analysis_state)
            self.advanced_analysis.set_time(self.current_time, self.player_filter.currentData())

    def set_advanced_exploration_enabled(self, enabled):
        """保留原有全屏分析入口；仅由明确的观看状态控制显示。"""
        self._advanced_exploration_enabled = bool(enabled)
        if enabled and self._fullscreen_mode:
            self._ensure_advanced_analysis()
        if self.advanced_analysis is not None:
            self.advanced_analysis.set_enabled(bool(enabled and self._fullscreen_mode))

    def focus_diagnosis(self, payload, *, focus_camera=True):
        """定位诊断的原损失证据；不猜连续位置、不调整原时间，也不发起播放。"""
        if self._cleaned or not isinstance(payload, dict):
            return False
        points = []
        identity = payload.get("id") or payload.get("diagnostic_id") or payload.get("battle_id")
        events = payload.get("events") or payload.get("evidence") or []
        for index, row in enumerate(events):
            if not isinstance(row, dict):
                continue
            pos = row.get("position") or {}
            x, y = row.get("target_x", pos.get("x")), row.get("target_y", pos.get("y"))
            if not _finite(x) or not _finite(y) or not _finite(row.get("time")):
                continue
            player = row.get("target_player_id", row.get("player_id"))
            points.append(dict(row, x=x, y=y, player_id=player, kind="death", action="unit_loss",
                               id=f"{identity}-loss-{index}", diagnostic_id=identity,
                               label="损失 · " + str(row.get("target_name_zh") or row.get("name_zh") or row.get("target_type") or "单位"),
                               color=self.colors.get(player, "#e9bec0"), diagnosis_evidence=True))
        pos = payload.get("position") or payload.get("region") or {}
        if points:
            xs, ys = [row["x"] for row in points], [row["y"] for row in points]
            width, height = max(20, max(xs) - min(xs)), max(20, max(ys) - min(ys))
            bounds = QRectF((min(xs) + max(xs)) / 2 - width / 2,
                            -(min(ys) + max(ys)) / 2 - height / 2, width, height)
        elif isinstance(pos, dict) and _finite(pos.get("x")) and _finite(pos.get("y")):
            bounds = QRectF(pos["x"] - 15, -pos["y"] - 15, 30, 30)
        else:
            return False
        self._diagnosis_focus = dict(payload)
        self.canvas.diagnosis_points = points
        self.canvas.diagnosis_region = bounds.adjusted(-8, -8, 8, 8)
        times = payload.get("time_range") or []
        self.canvas.diagnosis_caption = "片段损失位置" + (
            f" · {_clock(times[0])}–{_clock(times[1])}" if len(times) == 2 and all(_finite(t) for t in times) else "")
        if focus_camera:
            frame = bounds.adjusted(-20, -20, 20, 20)
            width = max(1, self.canvas.viewport().width() - 64)
            height = max(1, self.canvas.viewport().height() - self.canvas.fit_bottom_inset - 64)
            factor = min(width / frame.width(), height / frame.height())
            factor = min(self.canvas._fit_scale * 60, max(self.canvas._fit_scale * .15, factor))
            self.canvas.resetTransform()
            self.canvas.scale(factor, factor)
            self.canvas.centerOn(frame.center() + QPointF(0, self.canvas.fit_bottom_inset / (2 * factor)))
            self.canvas.user_camera = True
            self.canvas.zoomChanged.emit(factor / max(.000001, self.canvas._fit_scale))
        self.canvas.viewport().update()
        return True

    def set_analysis_context(self, report=None, detail=None, *, sources=None, reference=None, history=None):
        """补充分析来源不重建地图，保留当前播放时刻、选点和镜头。"""
        self._analysis_context = dict(report=report, spatial=self.model, detail=detail,
                                      sources=sources, reference=reference, history=history)
        if self.advanced_analysis is not None:
            self.advanced_analysis.set_context(**self._analysis_context)
            self.advanced_analysis.set_time(self.current_time, self.player_filter.currentData())

    def _route_analysis_wheel(self, event):
        """浮层内滚轮只交给对应内容区，边界也不泄漏为底图缩放。"""
        if not self._cleaned and self.selection_card.handle_wheel(event):
            return True
        dock = self.advanced_analysis
        if not self._fullscreen_mode or dock is None or self._cleaned:
            return False
        for frame, scroll in ((dock.panel, dock.content), (dock.rail, dock.navigation)):
            if frame.isVisible() and frame.rect().contains(frame.mapFromGlobal(event.globalPos())):
                if not self._routing_analysis_wheel:
                    self._routing_analysis_wheel = True
                    try:
                        target = scroll.viewport()
                        forwarded = QWheelEvent(
                            QPointF(target.mapFromGlobal(event.globalPos())), event.globalPosF(),
                            event.pixelDelta(), event.angleDelta(), event.buttons(), event.modifiers(),
                            event.phase(), event.inverted(), event.source())
                        QApplication.sendEvent(target, forwarded)
                    finally:
                        self._routing_analysis_wheel = False
                # 转交后的内容区可能仍在边界；递归到此立即消费，不能继续缩放。
                event.accept()
                return True
        return False

    def _apply_fullscreen_presentation(self):
        if not self._fullscreen_mode:
            self.fullscreen_panel.hide()
            self.fullscreen_timeline.hide()
            if self.advanced_analysis is not None:
                self.advanced_analysis.set_enabled(False)
            return
        for widget in self._toolbar_widgets + self._timebar_widgets:
            widget.hide()
        for widget in (self.notice, self.scope, self.detail_heading, self.detail_coordinates):
            widget.hide()
        if self.official_map is not None:
            tools = self.official_map.layout().itemAt(0).layout()
            for index in range(tools.count()):
                widget = tools.itemAt(index).widget()
                if widget is not None:
                    widget.hide()
            self.official_map.source_label.hide()
            self.official_map.status_label.hide()
            self.official_map.setToolTip("")
            self.official_map.canvas.setToolTip("")
            self.official_map.canvas.setAccessibleDescription("官方生成参考 · 非本场随机布局")
            self.official_map.canvas.set_reference_grid("#0f0f11", "#1d1d22")
        for canvas in self._canvases():
            canvas.setBackgroundBrush(QColor("#0f0f11"))
        selected = bool(self.model.get("map_name") or self.players or self.events)
        if selected and self.official_map is not None and self.official_map.entry is None:
            self.official_map.canvas.empty_text = "暂无可匹配的参考地图"
        self.map_stack.setVisible(selected)
        self.fullscreen_empty.setVisible(not selected)
        self.fullscreen_panel.setVisible(self._valid_timeline())
        self.fullscreen_timeline.setVisible(self._valid_timeline())
        if self.advanced_analysis is not None:
            self.advanced_analysis.set_enabled(selected and self._advanced_exploration_enabled)
        self._sync_fullscreen_timeline()
        self._position_fullscreen_controls()

    def _position_fullscreen_controls(self):
        if not self._fullscreen_mode:
            self.canvas.set_fit_bottom_inset(0)
            self.canvas.legend_left_inset = 0
            self.selection_card.reposition(self.canvas.viewport().rect())
            return
        self.fullscreen_empty.setGeometry(self.rect())
        self.fullscreen_panel.adjustSize()
        self.fullscreen_panel.move(max(0, self.width() - self.fullscreen_panel.width() - 24),
                                   max(0, self.height() - self.fullscreen_panel.height() - 24))
        # 宽屏在播放面板左侧，窄窗不足时移至其上方；间距始终16，外边距24。
        side_width = self.fullscreen_panel.x() - 16 - 24
        height = self.fullscreen_panel.height()
        if side_width >= 280:
            self.fullscreen_timeline.setGeometry(24, self.fullscreen_panel.y(), side_width, height)
        else:
            self.fullscreen_timeline.setGeometry(24, max(0, self.fullscreen_panel.y() - 16 - height),
                                                 max(0, self.width() - 48), height)
        # 控件可在窄屏上下叠排，按实际最上沿留位，避免地图下框落入时间条。
        inset = 0
        if self._valid_timeline():
            controls_top = min(self.fullscreen_timeline.y(), self.fullscreen_panel.y())
            # 全屏画布填满本组件；父resize早于子布局，不能读取尚未更新的viewport高度。
            inset = self.height() - controls_top + 16
        self.canvas.set_fit_bottom_inset(inset)
        if self.advanced_analysis is not None:
            self.advanced_analysis.reposition(self.canvas.viewport().rect(), bottom_reserved=inset)
        self.canvas.legend_left_inset = (self.advanced_analysis.rail.geometry().right() + 8
                                        if self.advanced_analysis is not None
                                        and self.advanced_analysis._enabled else 0)
        self.selection_card.reposition(self.canvas.viewport().rect(), bottom_reserved=inset)
        self.fullscreen_empty.raise_()
        self.fullscreen_timeline.raise_()
        self.fullscreen_panel.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_fullscreen_controls()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._fullscreen_mode:
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor("#0f0f11"))

    def eventFilter(self, watched, event):
        if watched is self.canvas.viewport():
            if event.type() == QEvent.Resize:
                self._position_fullscreen_controls()
            elif event.type() == QEvent.Wheel and self.selection_card.handle_wheel(event):
                return True
        if (event.type() == QEvent.Wheel and self._dashboard_mode and not self._fullscreen_mode
                and not event.modifiers() & Qt.ControlModifier
                and watched in tuple(canvas.viewport() for canvas in self._canvases())):
            # 嵌入地图让普通滚轮继续浏览页面；只在Ctrl组合手势下缩放。
            # 转交原始像素/角度增量，保留触控板细滚动、方向和滚动阶段。
            ancestor = self.parentWidget()
            while ancestor is not None and not isinstance(ancestor, QScrollArea):
                ancestor = ancestor.parentWidget()
            if ancestor is not None:
                target = ancestor.viewport()
                forwarded = QWheelEvent(
                    QPointF(target.mapFromGlobal(event.globalPos())), event.globalPosF(),
                    event.pixelDelta(), event.angleDelta(), event.buttons(), event.modifiers(),
                    event.phase(), event.inverted(), event.source())
                QApplication.sendEvent(target, forwarded)
            # 页面到达边缘也不退回地图缩放，避免同一手势突然改变含义。
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def set_dashboard_mode(self, enabled=True):
        """嵌入首屏只保留地图与浮动全屏入口；独立全屏另有精简显示模式。"""
        self._dashboard_mode = bool(enabled)
        self.setMinimumHeight(180 if enabled else 320)
        if enabled:
            self.layout().setSpacing(0)
            if self.official_map is not None:
                self.official_map.layout().setSpacing(0)
                self.official_map.canvas.set_initial_fit("cover")
        self._apply_dashboard_presentation()

    def _apply_dashboard_presentation(self):
        if not self._dashboard_mode:
            return
        reference = self._reference_active()
        for widget in self._toolbar_widgets:
            widget.hide()
        for widget in self._timebar_widgets:
            widget.setVisible(not reference)
        for widget in (self.notice, self.scope, self.detail_heading, self.detail_coordinates):
            widget.hide()
        # 参考画布仅保留身份说明，重复工具已放到右侧卡片；不改完整地图类的行为。
        for canvas in self._canvases():
            canvas.setBackgroundBrush(QColor("#161619"))
        if self.official_map is None:
            return
        tools = self.official_map.layout().itemAt(0).layout()
        for index in range(tools.count()):
            widget = tools.itemAt(index).widget()
            if widget is not None:
                widget.hide()
        self.official_map.status_label.hide()
        entry = self.official_map.entry or {}
        reference_name = entry.get("name_zh") or entry.get("name_en") or entry.get("key")
        caption = "官方生成参考 · 非本场随机布局"
        self.official_map.source_label.setText(f"{reference_name} · {caption}" if reference_name else "尚未选择参考地图")
        # 参考身份只保留在无障碍说明中，不弹出覆盖地图的悬停文字。
        description = self.official_map.source_label.text()
        self.official_map.source_label.hide()
        self.official_map.setToolTip("")
        self.official_map.canvas.setToolTip("")
        self.official_map.canvas.setAccessibleDescription(description)
        self.official_map.setAccessibleDescription(description)
        # 留白与卡片同色，地图仍以原比例适应全图，不靠裁切消除黑边。
        for canvas in (self.canvas, self.official_map.canvas):
            canvas.setBackgroundBrush(QColor("#161619"))
        self.official_map.canvas.set_reference_grid("#161619", "#26262c")

    def zoom_by(self, factor):
        """缩放当前可见地图；参考地形与真实操作坐标保持独立。"""
        canvas = self.official_map.canvas if self._reference_active() else self.canvas
        canvas.zoom_at(canvas.viewport().rect().center(), factor)

    def choose_map_pack(self):
        """选择独立地图包，成功后记住位置；不改变对局、时间和镜头。"""
        if self.official_map is None:
            return False
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
        if self.official_map is None:
            return False
        from map_pack_location import save_map_pack_location
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
    def _map_bounds(value):
        """只接受模型明确提供的有效边界，不按玩家数、地图名或操作点猜真实大小。"""
        keys = ("x_min", "x_max", "y_min", "y_max")
        if not isinstance(value, dict) or not all(_finite(value.get(key)) for key in keys):
            return None
        if value["x_max"] <= value["x_min"] or value["y_max"] <= value["y_min"]:
            return None
        return QRectF(value["x_min"], -value["y_max"],
                      value["x_max"] - value["x_min"], value["y_max"] - value["y_min"])

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
        self.placement_layer.filter_player(player)
        self.canvas.legend = [(self.names[key], color) for key, color in self.colors.items() if player is None or player == key]
        self._update_legend_description()
        if self._selected is not None and player is not None and self._selected.get("player_id") != player:
            self._clear_selection()
        self.layer.seek(self.current_time)
        self.placement_layer.seek(self.current_time)
        self._update_scope()
        if self.advanced_analysis is not None:
            self.advanced_analysis.set_time(self.current_time, player)
        self.canvas.viewport().update()

    def _update_legend_description(self):
        """完整身份供无障碍读取，画布不弹出遮挡地图的名单提示。"""
        names = "\n".join(name for name, _color in self.canvas.legend)
        self.canvas.setToolTip("")
        source = ("对局模型提供的地图边界" if self.canvas.boundary_confirmed else
                  "已记录目标点范围，真实地图边界尚未提供")
        self.canvas.setAccessibleDescription(source + ("\n" + names if names else ""))

    def _update_scope(self):
        kinds = self.layer.kinds
        legend = "圆环为操作目标" + ("，菱形为出现点" if "birth" in kinds else "") + ("，叉号为阵亡点" if "death" in kinds else "")
        self.scope.setText(f"网格仅为坐标参照 · 最近20秒 {len(self.layer.recent_events)} 个点，历史 {len(self.layer.history_events)} 个点 · {legend}")

    def _clear_selection(self):
        self.selection_card.set_event(None)
        self._selected = self.layer.selected = None
        self.placement_layer.set_selected(None)
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
        self._sync_fullscreen_timeline()
        if self.advanced_analysis is not None:
            self.advanced_analysis.set_time(value, self.player_filter.currentData())
        if self._selected is not None and self._selected["time"] > value:
            self._clear_selection()
        if self.layer.seek(value):
            self._update_scope()
        self.placement_layer.seek(value)
        if value >= self.duration:
            self.pause()
        if changed and emit:
            self.timeSelected.emit(value)

    def _select_canvas_event(self, event):
        """画布单击只查看详情；正在观看的时刻和播放状态保持不变。"""
        if event.get("diagnosis_evidence"):
            self.diagnosisEvidenceSelected.emit(event)
        self.select_event(event, seek=False)

    def select_event(self, event, *, seek=True, emit=True):
        if seek:
            self.seek(event["time"])
        self._selected = self.layer.selected = event
        metadata = building_metadata(event.get("building_type_id")) if event.get("action") == "place_building" else None
        self.selection_card.set_event(event, self.names, metadata)
        self.selection_card.set_enabled(not self._reference_active())
        self.selection_card.reposition(self.canvas.viewport().rect(),
                                       self.canvas.fit_bottom_inset if self._fullscreen_mode else 0)
        self.placement_layer.set_selected(event.get('id'))
        self.layer.update()
        name = self.names.get(event.get("player_id"), f"玩家 {event.get('player_id')}")
        action = str(event.get("label") or "操作目标")
        if event.get('action') == 'place_building':
            action = placement_description(event)
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
        if event.get('action') == 'place_building':
            text += f"　原始坐标点 {len(event.get('placement_points') or [])} 个"
        if event.get("target_entity_id") is not None:
            text += f"　目标实体：{event['target_entity_id']}"
        self.detail_coordinates.setText(text)
        self.detail_heading.setToolTip('' if event.get('action') == 'place_building' else self.detail_heading.text())
        self.detail_coordinates.setToolTip('' if event.get('action') == 'place_building' else
                                         text + "\n完整实体ID：" + ("、".join(map(str, units)) or "未提供")
                                         + f"\n高度Y：{_number(target.get('y'))}")
        if emit:
            self.eventSelected.emit(dict(event))

    def capture_view_state(self):
        """复制观看状态，不复制或改写对局模型；编号始终使用原始局内编号。"""
        # 使用浮点几何中心，与centerOn（镜头居中）一致，避免全屏往返累积左上偏移。
        inverse, _ = self.canvas.viewportTransform().inverted()
        center = inverse.map(QPointF(self.canvas.viewport().width()/2, self.canvas.viewport().height()/2))
        extent = self.canvas.extent
        return {"time": self.current_time, "player_id": self.player_filter.currentData(),
                "speed": self.speed.currentData(), "playing": self.timer.isActive(),
                "view_mode": self.view_mode.currentData(),
                "placement_markers_visible": self.placement_markers_visible,
                "operation_points_visible": self.operation_points_visible,
                "advanced_exploration_enabled": self._advanced_exploration_enabled,
                "diagnostic_focus": self._diagnosis_focus,
                "advanced_analysis": (self.advanced_analysis.capture_state() if self.advanced_analysis is not None
                                      else dict(self._advanced_analysis_state)),
                "reference_clock": self._reference_playback_allowed(),
                "automatic_cover": ((self._dashboard_mode or self._fullscreen_mode)
                                    and self.official_map is not None and not self.official_map.canvas.user_camera),
                "official_map": self.official_map.capture_view_state() if self.official_map is not None else None,
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
        if isinstance(state.get('placement_markers_visible'), bool):
            self.set_placement_markers_visible(state['placement_markers_visible'])
        if isinstance(state.get('operation_points_visible'), bool):
            self.set_operation_points_visible(state['operation_points_visible'])
        # 只有显式的大图状态交接才能让主页面继续真实报告时钟，普通参考图仍禁止启播。
        self._reference_clock_enabled = state.get("reference_clock") is True and self._valid_timeline()
        self._clear_selection()
        self.set_advanced_exploration_enabled(state.get("advanced_exploration_enabled", True) is True)
        self._diagnosis_focus = None
        self.canvas.diagnosis_points = []
        self.canvas.diagnosis_region = None
        self.canvas.diagnosis_caption = ""
        if isinstance(state.get("diagnostic_focus"), dict):
            self.focus_diagnosis(state["diagnostic_focus"], focus_camera=False)
        player_index = self.player_filter.findData(state.get("player_id"))
        self.player_filter.setCurrentIndex(max(0, player_index))
        self._filter_changed()
        speed_index = self.speed.findData(state.get("speed"))
        if speed_index >= 0:
            self.speed.setCurrentIndex(speed_index)
        self.seek(state.get("time", 0), emit=False)
        self._advanced_analysis_state = dict(state.get("advanced_analysis") or {})
        if self.advanced_analysis is not None:
            self.advanced_analysis.restore_state(self._advanced_analysis_state)
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
            if camera.get("user_camera"):
                zoom = camera.get("zoom")
                if _finite(zoom):
                    zoom = max(.15, min(60.0, zoom))
                    self.canvas.scale(zoom, zoom)
                    self.canvas.zoomChanged.emit(zoom)
                center = camera.get("center")
                if isinstance(center, (list, tuple)) and len(center) == 2 and all(_finite(value) for value in center):
                    self.canvas.centerOn(QPointF(*center))
                self.canvas.user_camera = True
        if self.official_map is not None:
            from map_pack_location import load_map_pack_location
            saved_pack = load_map_pack_location(self.map_data_dir)
            if (saved_pack is not None and saved_pack != self.official_map.catalog.root
                    and self.official_map.catalog.data_status().get("state") != "ready"):
                self.official_map.catalog.set_root(saved_pack)
            self.official_map.restore_view_state(state.get("official_map"))
        mode_index = self.view_mode.findData(state.get("view_mode", "records"))
        self.view_mode.setCurrentIndex(max(0, mode_index))
        self._view_mode_changed()
        official_state = state.get("official_map") or {}
        camera_state = official_state.get("camera") or {}
        if (self.official_map is not None and (self._fullscreen_mode or self._dashboard_mode) and state.get("automatic_cover") is True
                and not camera_state.get("user_camera")):
            # 自动取景按目标窗口宽高比铺满；手动镜头仍沿用完整存档。
            self.official_map.canvas.fit_all()
            self.official_map.canvas.set_initial_fit("cover")
        if (state.get("playing") is True and self.current_time < self.duration
                and (not self._reference_active() or self._reference_playback_allowed())):
            self.toggle_playback()
        return True

    def set_placement_markers_visible(self, visible):
        """只开关建筑放置记录的小图标注记，与操作点独立。"""
        self.placement_markers_visible = bool(visible)
        self.placement_layer.setVisible(self.placement_markers_visible)

    def set_operation_points_visible(self, visible):
        """隐藏原操作点和选中圆环，同时停止命中；数据、筛选和时钟不变。"""
        self.operation_points_visible = bool(visible)
        self.layer.setVisible(self.operation_points_visible)

    def fit_all(self):
        if self._reference_active():
            self.official_map.fit_all()
        else:
            bounds = self.canvas.battlefield_rect
            if bounds is None:
                bounds = self._extent(self.events)
            margin = max(bounds.width(), bounds.height()) * .06
            self.canvas.set_extent(bounds.adjusted(-margin, -margin, margin, margin))

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
        self._sync_fullscreen_controls()

    def toggle_playback(self):
        if self._reference_active() and not self._reference_playback_allowed():
            return
        if self.timer.isActive():
            self.pause()
        elif ((self.events or self._reference_playback_allowed() or (self._fullscreen_mode and self._valid_timeline()))
              and self.duration > 0 and not self._cleaned):
            if self.current_time >= self.duration:
                self.seek(0)
            self.elapsed.start()
            self.timer.start()
            self.play_button.setText("暂停")
            self._sync_fullscreen_controls()

    def _advance(self):
        if self.timer.isActive():
            elapsed = self.elapsed.restart() / 1000
            self.seek(self.current_time + elapsed * float(self.speed.currentData() or 1))

    def pause(self):
        if hasattr(self, "timer"):
            self.timer.stop()
        if hasattr(self, "play_button"):
            self.play_button.setText("播放")
        self._sync_fullscreen_controls()

    def cleanup(self):
        if not self._cleaned:
            self.pause()
            if self.official_map is not None:
                self.official_map.cleanup()
            if self.advanced_analysis is not None:
                self.advanced_analysis.cleanup()
            self.selection_card.cleanup()
            self._cleaned = True

    def hideEvent(self, event):
        self.pause()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.pause()
        super().closeEvent(event)
