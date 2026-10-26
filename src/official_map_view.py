"""官方地图图鉴：独立参考图，不接收或叠加任何本场玩家操作坐标。"""
from __future__ import annotations

import math

from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPixmap
from PyQt5.QtWidgets import (QComboBox, QCompleter, QFrame, QGraphicsPixmapItem,
                            QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel,
                            QSizePolicy, QVBoxLayout, QWidget)

from choice_controls import ClickComboBox
from official_maps import catalog_entries, find_official_map, map_layout_reference, map_preview_path
from official_grid_renderer import render_layout_reference


def _finite(value):
    """镜头状态仅接受有限数值。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class _ReferenceCanvas(QGraphicsView):
    """只含一张官方参考图片的画布；没有实战事件图层或坐标投影接口。"""

    zoomChanged = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(QGraphicsScene(parent), parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setBackgroundBrush(QColor("#101b20"))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAccessibleName("官方地图参考图；滚轮缩放，按住左键拖动；非本场实战地形")
        self.picture = None
        self.empty_text = "搜索并选择官方地图"
        self.extent = QRectF(0, 0, 1, 1)
        self.user_camera = False
        self._fit_scale = 1.0
        self._press = None

    def set_image(self, picture, empty_text="此地图尚未提供参考图"):
        self.scene().clear()
        self.picture = None
        self.empty_text = empty_text
        if picture is not None and not picture.isNull():
            self.picture = QGraphicsPixmapItem(picture)
            self.picture.setAcceptedMouseButtons(Qt.NoButton)
            self.picture.setTransformationMode(Qt.SmoothTransformation)
            self.scene().addItem(self.picture)
            self.extent = self.picture.boundingRect()
        else:
            self.extent = QRectF(0, 0, 1, 1)
        # 扩大可拖动空间，不把此范围当作真实对局边界。
        self.setSceneRect(self.extent.adjusted(-self.extent.width() * 4, -self.extent.height() * 4,
                                              self.extent.width() * 4, self.extent.height() * 4))
        self.fit_all()
        self.viewport().update()

    def fit_all(self):
        self.resetTransform()
        self.fitInView(self.extent, Qt.KeepAspectRatio)
        self._fit_scale = max(.000001, self.transform().m11())
        self.user_camera = False
        self.zoomChanged.emit(1.0)

    def zoom_ratio(self):
        return self.transform().m11() / max(.000001, self._fit_scale)

    def zoom_at(self, position, factor):
        if self.picture is None:
            return
        before = self.mapToScene(position)
        current = self.zoom_ratio()
        target = max(.15, min(60.0, current * factor))
        if abs(target - current) < 1e-9:
            return
        self.scale(target / current, target / current)
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
        super().mouseReleaseEvent(event)
        self._press = None

    def drawForeground(self, painter, _rect):
        if self.picture is None:
            painter.save()
            painter.resetTransform()
            painter.setPen(QColor("#b7c7ce"))
            painter.drawText(self.viewport().rect().adjusted(24, 24, -24, -24),
                             Qt.AlignCenter | Qt.TextWordWrap, self.empty_text)
            painter.restore()

    def capture_camera(self):
        if self.picture is None:
            return None
        center = self.mapToScene(self.viewport().rect().center())
        return {"zoom": self.zoom_ratio(), "center": (center.x(), center.y()),
                "user_camera": self.user_camera}

    def restore_camera(self, state):
        if self.picture is None or not isinstance(state, dict):
            return
        self.fit_all()
        zoom = state.get("zoom")
        if _finite(zoom):
            zoom = max(.15, min(60.0, zoom))
            self.scale(zoom, zoom)
            self.zoomChanged.emit(zoom)
        center = state.get("center")
        if isinstance(center, (tuple, list)) and len(center) == 2 and all(_finite(value) for value in center):
            self.centerOn(QPointF(*center))
        self.user_camera = bool(state.get("user_camera"))


class OfficialMapView(QWidget):
    """可搜索的官方地图参考层；显示模式与镜头可随全屏宿主往返恢复。"""

    zoomChanged = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.entries = {entry["key"]: dict(entry) for entry in catalog_entries()}
        self.entry = None
        self.actual_representation = None
        self._loaded_key = None
        self._image_cache = {}
        self._pending_camera = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.map_selector = ClickComboBox()
        self.map_selector.setEditable(True)
        self.map_selector.setInsertPolicy(QComboBox.NoInsert)
        self.map_selector.setMinimumWidth(0)
        self.map_selector.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.map_selector.setMinimumContentsLength(10)
        self.map_selector.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.map_selector.setAccessibleName("搜索并选择官方地图")
        self.map_selector.lineEdit().setPlaceholderText(f"搜索{len(self.entries)}张官方地图")
        for entry in self.entries.values():
            name = str(entry.get("name_zh") or entry.get("name_en") or entry["key"])
            english = str(entry.get("name_en") or "")
            label = f"{name} · {english}" if english and english != name else name
            self.map_selector.addItem(label, entry["key"])
            self.map_selector.setItemData(self.map_selector.count() - 1, label, Qt.ToolTipRole)
        self.map_selector.setCurrentIndex(-1)
        # 可编辑模式会重设Qt列表策略；显式恢复无可见滚动条的图鉴选择列表。
        self.map_selector.view().setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.map_selector.view().setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        completer = self.map_selector.completer()
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        completer.popup().setStyleSheet("background:#1b2a31;color:#dce5e8;selection-background-color:#334d4b;border:1px solid #49605e;")
        completer.popup().setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        completer.popup().setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.map_selector.activated[int].connect(self._map_activated)
        toolbar.addWidget(self.map_selector, 1)
        self.representation = ClickComboBox()
        self.representation.setAccessibleName("地图参考图类型")
        self.representation.addItem("二维布局参考", "layout")
        self.representation.addItem("官方示意", "preview")
        self.representation.setFixedWidth(126)
        self.representation.currentIndexChanged.connect(self._representation_changed)
        toolbar.addWidget(self.representation)
        layout.addLayout(toolbar)
        self.canvas = _ReferenceCanvas(self)
        self.canvas.zoomChanged.connect(self.zoomChanged)
        layout.addWidget(self.canvas, 1)
        self.source_label = QLabel("官方地图参考 · 非本场实战地形")
        self.source_label.setTextFormat(Qt.PlainText)
        self.source_label.setStyleSheet("font-size:11px;color:#d8bf84;")
        self.source_label.setAccessibleName(self.source_label.text())
        layout.addWidget(self.source_label)
        self.status_label = QLabel("未匹配本场地图，请搜索并选择。")
        self.status_label.setTextFormat(Qt.PlainText)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-size:10px;color:#9dafb6;")
        self.status_label.setMinimumWidth(0)
        layout.addWidget(self.status_label)

    def set_match_map(self, value):
        """仅精确匹配官方别名；未知地图保持未选择，不用其他地图代替。"""
        match = find_official_map(value)
        self.select_map(match.get("key") if match else None)

    def select_map(self, key, *, load=True):
        self.entry = self.entries.get(key)
        self._loaded_key = None
        self._pending_camera = None
        self.actual_representation = None
        self.map_selector.blockSignals(True)
        index = self.map_selector.findData(key) if self.entry else -1
        self.map_selector.setCurrentIndex(index)
        if index < 0:
            self.map_selector.setEditText("")
        self.map_selector.blockSignals(False)
        self.map_selector.setToolTip(self.map_selector.currentText() + f"\n官方图鉴共{len(self.entries)}项，不代表当前排位图池。")
        self.canvas.set_image(None, "未匹配本场地图\n搜索并选择官方地图，浏览其参考布局。" if not self.entry else "正在准备官方地图参考…")
        if not self.entry:
            self.status_label.setText("未匹配本场地图，请搜索并选择。")
            self.status_label.setToolTip("")
        elif load and self.isVisible():
            self._ensure_image()

    def _map_activated(self, index):
        if index >= 0:
            self.select_map(self.map_selector.itemData(index))

    def _representation_changed(self):
        self._loaded_key = None
        self._pending_camera = None
        if self.isVisible():
            self._ensure_image()

    def _picture(self, representation):
        key = (self.entry["key"], representation)
        if key in self._image_cache:
            return self._image_cache[key]
        picture = QPixmap()
        try:
            if representation == "layout":
                reference = map_layout_reference(self.entry)
                if isinstance(reference, dict):
                    image = render_layout_reference(reference, size=1536)
                    if image is not None and not image.isNull():
                        picture = QPixmap.fromImage(image)
            else:
                path = map_preview_path(self.entry)
                if path:
                    picture = QPixmap(path)
        except (OSError, ValueError, TypeError, RuntimeError):
            # 单张参考资料不可用不影响对局时间线或其他官方地图浏览。
            picture = QPixmap()
        if not picture.isNull():
            if len(self._image_cache) >= 4:
                self._image_cache.pop(next(iter(self._image_cache)))
            self._image_cache[key] = picture
        return picture

    def _ensure_image(self):
        if self.entry is None:
            return
        requested = self.representation.currentData() or "layout"
        cache_key = (self.entry["key"], requested)
        if self._loaded_key == cache_key:
            return
        picture = self._picture(requested)
        actual = requested
        fallback = False
        if picture.isNull() and requested == "layout":
            picture = self._picture("preview")
            actual, fallback = "preview", not picture.isNull()
        if fallback:
            self.representation.blockSignals(True)
            self.representation.setCurrentIndex(self.representation.findData("preview"))
            self.representation.blockSignals(False)
        self._loaded_key = (self.entry["key"], self.representation.currentData() or requested)
        self.actual_representation = actual if not picture.isNull() else None
        missing_text = ("此地图尚未提供官方示意图\n可切换二维布局参考，或浏览其他官方地图。" if requested == "preview" else
                        "此地图尚未提供可用的参考图\n可继续浏览其他官方地图，或返回对局记录。")
        self.canvas.set_image(picture, missing_text)
        name = str(self.entry.get("name_zh") or self.entry.get("name_en") or self.entry["key"])
        if picture.isNull():
            status = (f"{name}：官方示意暂缺，可尝试切换二维布局参考。" if requested == "preview" else
                      f"{name}：参考资料缺失，未绘制替代地形。")
        elif actual == "layout":
            status = f"{name} · 树林、矿物为规则区域示意，并非本场真实矿点。"
        else:
            status = f"{name} · 官方示意图，不代表本场随机生成的地形。"
            if fallback:
                status = "布局参考暂缺，显示" + status
        self.status_label.setText(status)
        self.status_label.setToolTip(f"{status}\n官方配置：{self.entry.get('config_path') or '未提供'}")
        if self._pending_camera is not None:
            state, self._pending_camera = self._pending_camera, None
            self.canvas.restore_camera(state)

    def showEvent(self, event):
        super().showEvent(event)
        self.layout().activate()
        self._ensure_image()

    def fit_all(self):
        self.canvas.fit_all()

    def capture_view_state(self):
        return {"map_key": self.entry["key"] if self.entry else None,
                "representation": self.representation.currentData(),
                "camera": self.canvas.capture_camera() or self._pending_camera}

    def restore_view_state(self, state):
        if not isinstance(state, dict):
            return
        self.select_map(state.get("map_key"), load=False)
        index = self.representation.findData(state.get("representation"))
        if index >= 0:
            self.representation.blockSignals(True)
            self.representation.setCurrentIndex(index)
            self.representation.blockSignals(False)
        self._loaded_key = None
        self._pending_camera = state.get("camera")
        if self.isVisible():
            self._ensure_image()
