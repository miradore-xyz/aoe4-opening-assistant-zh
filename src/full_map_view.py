"""完整官方生成参考实例的原生 Qt 视图，不接收本场操作坐标。

tile=图块，level=金字塔层级，cache=缓存，source=来源。仅加载当前视野
所需图块和当前地图实体；仅显示自然高程地表，保存坐标不受压缩影响。
"""
from __future__ import annotations

from collections import OrderedDict
import json
import math

from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen, QPicture, QPixmap, QPolygonF
from PyQt5.QtWidgets import (QComboBox, QCompleter, QFrame, QGraphicsScene,
                            QGraphicsView, QHBoxLayout, QLabel, QPlainTextEdit,
                            QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from choice_controls import ClickComboBox
from app_fonts import make_ui_font
from windowing import DialogBar, FramelessDialog


ENTITY_LABELS = {"tree": "树木", "gold": "金矿", "stone": "石矿", "berries": "浆果",
                 "berry": "浆果", "food": "食物资源", "animal": "动物", "fish": "鱼群",
                 "spawn_marker": "玩家出生标记", "sacred_site": "圣地", "relic": "圣物",
                 "building": "中立建筑", "unknown": "其他实体"}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _color(value):
    """网页原画法八位色是RRGGBBAA，显式转成Qt颜色，避免误读成ARGB。"""
    if len(value) == 9:
        return QColor(int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16), int(value[7:9], 16))
    return QColor(value)


def entity_role(entity):
    if entity.get("blueprint_reference_raw") == "starting_position_shared_territory":
        return "spawn_marker"
    if entity.get("category") == "food" and "forage_bush" in (entity.get("category_evidence") or {}).get("common_type_tags", []):
        return "berries"
    return entity.get("category") or "unknown"


def _variation(identity):
    """FNV（稳定散列）只计算一次，镜头移动时不重复扫描实体名称。"""
    value = 2166136261
    for char in identity:
        value = ((value ^ ord(char)) * 16777619) & 0xffffffff
    return value / 4294967295


def build_entity_index(data, render):
    """只映射已校准且与地形同源的真实保存实体；不生成或移动资源。"""
    expected = render.get("source_sha256") or (render.get("source") or {}).get("source_sha256")
    source = (data.get("source_scenario") or {}).get("sha256") if isinstance(data, dict) else None
    coordinates = data.get("coordinates") or {} if isinstance(data, dict) else {}
    mapping = coordinates.get("world_to_grid") or {}
    entities = data.get("entities") if isinstance(data, dict) else None
    grid = render.get("source_grid") or {}
    extent = render.get("valid_content_size") or [render.get("width"), render.get("height")]
    keys = ("origin_x", "origin_z", "column_x", "column_z", "row_x", "row_z")
    if (not source or source != expected or data.get("kind") != "official_generated_entities"
            or data.get("schema_version") != 1 or coordinates.get("calibrated") is not True
            or coordinates.get("source_sha256") != source or not isinstance(entities, list)
            or (data.get("counts") or {}).get("entities") != len(entities)
            or not all(_finite(mapping.get(k)) for k in keys)
            or abs(mapping["column_x"] * mapping["row_z"] - mapping["column_z"] * mapping["row_x"]) < 1e-12
            or not all(_finite(grid.get(k)) and grid[k] > 1 for k in ("width", "height"))
            or len(extent) != 2 or not all(_finite(v) and v > 0 for v in extent)):
        return None
    points, buckets, seen = [], {}, set()
    for entity in entities:
        if not isinstance(entity, dict):
            return None
        position, saved = entity.get("position") or {}, entity.get("grid_position") or {}
        identity = entity.get("id")
        if (not isinstance(identity, str) or identity in seen
                or not all(_finite(position.get(k)) for k in ("x", "y", "z"))):
            return None
        seen.add(identity)
        dx, dz = position["x"] - mapping["origin_x"], position["z"] - mapping["origin_z"]
        column = dx * mapping["column_x"] + dz * mapping["column_z"]
        row = dx * mapping["row_x"] + dz * mapping["row_z"]
        if (not all(_finite(saved.get(k)) for k in ("column", "row"))
                or abs(column - saved["column"]) > 1e-3 or abs(row - saved["row"]) > 1e-3):
            return None
        point = {"entity": entity, "px": column * extent[0] / (grid["width"] - 1),
                 "py": row * extent[1] / (grid["height"] - 1), "role": entity_role(entity),
                 "variation": _variation(identity)}
        if point["role"] in {"unknown", "resource_spawner"}:
            continue
        points.append(point)
        key = (math.floor(point["px"] / 256), math.floor(point["py"] / 256))
        buckets.setdefault(key, []).append(point)
    bounds = ((min(k[0] for k in buckets), min(k[1] for k in buckets),
               max(k[0] for k in buckets), max(k[1] for k in buckets)) if buckets else None)
    tree_buckets = {}
    unit = render.get("pixels_per_source_unit", 12)
    for point in points:
        if point["role"] != "tree":
            continue
        entity_scale = point["entity"].get("scale")
        entity_scale = entity_scale if _finite(entity_scale) else 1
        point["tree_radius"] = unit * (1.45 + point["variation"] * .45) * max(.5, min(2, entity_scale))
        key = (math.floor(point["px"] / 256), math.floor(point["py"] / 256))
        tree_buckets.setdefault(key, []).append(point)
    return {"points": points, "buckets": buckets, "tree_buckets": tree_buckets,
            "bounds": bounds, "source_sha256": source}


class _TileCache:
    """LRU（最近使用优先）缓存，图块数与估计解码内存同时受限。"""
    def __init__(self, max_tiles=96, max_bytes=128 * 1024 * 1024):
        self.max_tiles, self.max_bytes = max_tiles, max_bytes
        self.items, self.bytes, self.loads = OrderedDict(), 0, 0

    def clear(self):
        self.items.clear()
        self.bytes = 0

    def get(self, key, load_bytes):
        if key in self.items:
            picture, cost = self.items.pop(key)
            self.items[key] = (picture, cost)
            return picture
        picture = QPixmap()
        try:
            payload = load_bytes()
            if payload:
                picture.loadFromData(payload)
        except (OSError, ValueError, KeyError):
            pass  # 当前块缺失时使用同源低层底图，不读其他地图替代。
        self.loads += 1
        cost = picture.width() * picture.height() * 4 if not picture.isNull() else 0
        if cost <= self.max_bytes:
            self.items[key] = (picture, cost)
            self.bytes += cost
            while len(self.items) > self.max_tiles or self.bytes > self.max_bytes:
                _, (_, old_cost) = self.items.popitem(last=False)
                self.bytes -= old_cost
        return picture


class _TreeLayerCache:
    """静态树冠分区缓存32MiB；常用缩放复用位图，近看复用矢量命令。"""
    def __init__(self, max_bytes=32 * 1024 * 1024, max_items=4096):
        self.max_bytes, self.max_items = max_bytes, max_items
        self.items, self.bytes, self.builds = OrderedDict(), 0, 0

    def clear(self):
        self.items.clear()
        self.bytes = 0

    def get(self, key, build):
        if key in self.items:
            layer, cost = self.items.pop(key)
            self.items[key] = (layer, cost)
            return layer
        layer, cost = build()
        self.builds += 1
        if cost <= self.max_bytes:
            self.items[key] = (layer, cost)
            self.bytes += cost
            while self.bytes > self.max_bytes or len(self.items) > self.max_items:
                _, (_, old_cost) = self.items.popitem(last=False)
                self.bytes -= old_cost
        return layer


class FullMapCanvas(QGraphicsView):
    """原尺寸图块按需绘制；没有异步旧图覆盖，也不解码整张高分辨率地图。"""
    zoomChanged = pyqtSignal(float)
    entitySelected = pyqtSignal(object)

    def __init__(self, catalog, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.catalog, self.entry, self.render = catalog, None, None
        self.extent = QRectF(0, 0, 1, 1)
        self.style, self.index, self.selected = "natural", None, None
        self.entities_enabled, self.user_camera = True, False
        self._fit_scale, self._press = 1.0, None
        self.tile_cache = _TileCache()
        self.tree_cache = _TreeLayerCache()
        self.tree_geometry_cache = _TreeLayerCache(max_bytes=16 * 1024 * 1024)
        self.last_tiles, self.last_level = [], None
        self.empty_text = "搜索并选择地图，浏览完整参考实例"
        self.setFrameShape(QFrame.NoFrame)
        self.setBackgroundBrush(QColor("#0f0f11"))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAccessibleName("官方生成参考地图；滚轮缩放、左键拖动、点击资源查看保存坐标；非本场实战")

    def set_entry(self, entry, entities=None):
        self.entry, self.render = entry, entry.get("render") if entry else None
        self.index = build_entity_index(entities, self.render) if isinstance(entities, dict) and self.render else None
        self.selected = None
        self.tile_cache.clear()
        self.tree_cache.clear()
        self.tree_geometry_cache.clear()
        self.last_tiles, self.last_level = [], None
        self.extent = QRectF(0, 0, self.render["width"], self.render["height"]) if self.render else QRectF(0, 0, 1, 1)
        self.setSceneRect(self.extent.adjusted(-self.extent.width() * 3, -self.extent.height() * 3,
                                              self.extent.width() * 3, self.extent.height() * 3))
        self.fit_all()
        self.viewport().update()

    def fit_all(self):
        self.resetTransform()
        self.fitInView(self.extent, Qt.KeepAspectRatio)
        self._fit_scale = max(1e-6, self.transform().m11())
        self.user_camera = False
        self.zoomChanged.emit(1.0)

    def zoom_ratio(self):
        return self.transform().m11() / max(1e-6, self._fit_scale)

    def zoom_at(self, position, factor):
        if not self.render or not _finite(factor) or factor <= 0:
            return
        before = self.mapToScene(position)
        current = self.zoom_ratio()
        target = min(80.0, max(.15, current * factor))
        self.scale(target / current, target / current)
        after = self.mapToScene(position)
        self.translate(after.x() - before.x(), after.y() - before.y())
        self.user_camera = True
        self.zoomChanged.emit(target)

    def capture_camera(self):
        if not self.render:
            return None
        center = self.mapToScene(self.viewport().rect().center())
        return {"zoom": self.zoom_ratio(), "center": [center.x(), center.y()], "user_camera": self.user_camera}

    def restore_camera(self, state):
        if not self.render or not isinstance(state, dict):
            return
        self.fit_all()
        if _finite(state.get("zoom")):
            zoom = min(80.0, max(.15, state["zoom"]))
            self.scale(zoom, zoom)
        center = state.get("center")
        if isinstance(center, (list, tuple)) and len(center) == 2 and all(_finite(v) for v in center):
            self.centerOn(QPointF(*center))
        self.user_camera = bool(state.get("user_camera"))
        self.zoomChanged.emit(self.zoom_ratio())

    @staticmethod
    def _factors(level, render):
        factor = level.get("image_pixel_scale")
        if _finite(factor) and factor > 0:
            return factor, factor
        return render["width"] / level["width"], render["height"] / level["height"]

    def visible_tiles(self, level, rect, margin=0):
        rect = rect.intersected(self.extent)
        if rect.isEmpty():
            return []
        fx, fy = self._factors(level, self.render)
        size = level.get("tile_size") or self.render.get("tile_size", 512)
        left = max(0, math.floor(rect.left() / fx / size) - margin)
        top = max(0, math.floor(rect.top() / fy / size) - margin)
        right = min(level["cols"] - 1, math.floor((rect.right() - 1e-7) / fx / size) + margin)
        bottom = min(level["rows"] - 1, math.floor((rect.bottom() - 1e-7) / fy / size) + margin)
        return [(x, y) for y in range(top, bottom + 1) for x in range(left, right + 1)]

    def _level(self, levels, rect):
        wanted = self.render["width"] * abs(self.transform().m11()) * min(2.0, self.devicePixelRatioF())
        choice = next((i for i, level in enumerate(levels) if level["width"] >= wanted), len(levels) - 1)
        base_tiles = len(self.visible_tiles(levels[0], rect))
        while choice > 0 and len(self.visible_tiles(levels[choice], rect, 1)) + base_tiles > 96:
            choice -= 1
        return levels[choice]

    def _draw_level(self, painter, level, rect):
        fx, fy = self._factors(level, self.render)
        size = level.get("tile_size") or self.render.get("tile_size", 512)
        for x, y in self.visible_tiles(level, rect):
            level_id = level["level"]
            key = (self.entry["key"], self.style, level_id, x, y)
            picture = self.tile_cache.get(key, lambda: self.catalog.tile_bytes(self.entry["key"], self.style, level_id, x, y))
            if picture.isNull():
                continue
            width, height = min(size, level["width"] - x * size, picture.width()), min(size, level["height"] - y * size, picture.height())
            target = QRectF(x * size * fx, y * size * fy, width * fx, height * fy)
            painter.drawPixmap(target, picture, QRectF(0, 0, width, height))

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        levels = ((self.render or {}).get("styles", {}).get(self.style) or {}).get("levels") or []
        if not levels:
            return
        painter.save()
        painter.setClipRect(self.extent)
        level = self._level(levels, rect)
        # 低层提供当前视野的底图，目标块缺失时仍保留同一实例的地形。
        self._draw_level(painter, levels[0], rect)
        if level is not levels[0]:
            self._draw_level(painter, level, rect)
        self.last_level, self.last_tiles = level["level"], self.visible_tiles(level, rect)
        painter.restore()

    def visible_entities(self, rect):
        if not self.index or not self.index["bounds"]:
            return []
        bounds = self.index["bounds"]
        found = []
        for y in range(max(bounds[1], math.floor(rect.top() / 256)), min(bounds[3], math.floor(rect.bottom() / 256)) + 1):
            for x in range(max(bounds[0], math.floor(rect.left() / 256)), min(bounds[2], math.floor(rect.right() / 256)) + 1):
                found.extend(p for p in self.index["buckets"].get((x, y), ()) if rect.contains(p["px"], p["py"]))
        return found

    def _tree_picture(self, points, scale):
        """记录原有五个树冠椭圆；半径和颜色沿用保存实体，不压缩为位图。"""
        picture = QPicture()
        painter = QPainter(picture)
        painter.setPen(Qt.NoPen)
        palette = [QColor(color) for color in ("#42643f", "#5b7d4b", "#78975d")]
        dead_palette = [QColor(color) for color in ("#6d6752", "#8a8063", "#a29673")]
        shade = _color("#17281888")
        for point in points:
            x, y, variation = point["px"], point["py"], point["variation"]
            radius = max(.7 / scale, point["tree_radius"])
            painter.setBrush(shade)
            painter.drawEllipse(QPointF(x + radius * .2, y + radius * .3), radius, radius)
            colors = dead_palette if "_dead_" in str(point["entity"].get("blueprint_reference_raw")) else palette
            for i in range(4):
                angle = i * 2.399 + variation * 5
                painter.setBrush(colors[i % 3])
                r = radius * (.46 + (i % 2) * .08)
                painter.drawEllipse(QPointF(x + math.cos(angle) * radius * .4,
                                           y + math.sin(angle) * radius * .35), r, r)
        painter.end()
        return picture

    def _tree_layer(self, points, scale, raster_scale, picture_key):
        """树冠以保存坐标绘制；位图只缓存显示结果，不参与资源定位。"""
        def build_picture():
            picture = self._tree_picture(points, scale)
            return picture, picture.size()
        # 新缩放档只重新栅格化，不再从Python重复生成数万次绘图命令。
        picture = self.tree_geometry_cache.get(picture_key, build_picture)
        if raster_scale is None:
            return (picture, None), picture.size()
        # 分区外的树冠与阴影也计入边界，避免拖动时出现拼接缺口。
        pad = max(max(.7 / scale, point["tree_radius"]) for point in points) * 1.5 + 2 / raster_scale
        bounds = QRectF(min(point["px"] for point in points) - pad,
                        min(point["py"] for point in points) - pad,
                        max(point["px"] for point in points) - min(point["px"] for point in points) + pad * 2,
                        max(point["py"] for point in points) - min(point["py"] for point in points) + pad * 2)
        width, height = math.ceil(bounds.width() * raster_scale), math.ceil(bounds.height() * raster_scale)
        image = QPixmap(width, height)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(raster_scale, raster_scale)
        painter.translate(-bounds.left(), -bounds.top())
        painter.drawPicture(QPointF(0, 0), picture)
        painter.end()
        # 整数像素取整后保持映射比例，不能把剩余像素压回原边界。
        target = QRectF(bounds.left(), bounds.top(), width / raster_scale, height / raster_scale)
        return (image, target), width * height * 4

    def _draw_trees(self, painter, rect, scale):
        """缩放档向上取整保清晰，移动复用静态图层；近看树木少时保留矢量。"""
        if not self.index:
            return
        buckets = self.index["tree_buckets"]
        bounds = self.index["bounds"]
        if not bounds:
            return
        # 全图与常用缩放缓存树冠成图；高倍率使用Qt矢量，避免大块位图占用内存。
        effective = scale * self.devicePixelRatioF()
        raster_scale = 2 ** math.ceil(math.log2(effective)) if effective < 1 else None
        left, right = max(bounds[0], math.floor(rect.left() / 256)), min(bounds[2], math.floor(rect.right() / 256))
        top, bottom = max(bounds[1], math.floor(rect.top() / 256)), min(bounds[3], math.floor(rect.bottom() / 256))
        for y in range(top, bottom + 1):
            for x in range(left, right + 1):
                points = buckets.get((x, y))
                if not points:
                    continue
                minimum = min(point["tree_radius"] for point in points)
                # 极远视图的0.7px最小树冠随实际缩放变化，不能复用尺寸不同的图层。
                minimum_key = 0 if minimum * scale >= .7 else scale
                key = (x, y, raster_scale, minimum_key)
                layer, target = self.tree_cache.get(key, lambda: self._tree_layer(points, scale, raster_scale, (x, y, minimum_key)))
                if target is None:
                    painter.drawPicture(QPointF(0, 0), layer)
                else:
                    painter.drawPixmap(target, layer, QRectF(layer.rect()))

    def _draw_entities(self, painter, rect):
        if not self.entities_enabled or not self.index:
            return
        scale = max(1e-6, abs(self.transform().m11()))
        unit = self.render.get("pixels_per_source_unit", 12) * scale
        points = self.visible_entities(rect.adjusted(-80 / scale, -80 / scale, 80 / scale, 80 / scale))
        transform, ink = self.viewportTransform(), False
        painter.save()
        painter.setClipRect(self.extent)
        self._draw_trees(painter, rect.adjusted(-80 / scale, -80 / scale, 80 / scale, 80 / scale), scale)
        painter.restore()
        painter.save()
        painter.resetTransform()
        painter.setClipRect(transform.mapRect(self.extent))

        def dot(x, y, radius, color, outline=None, width=.45):
            painter.setPen(QPen(_color(outline), width) if outline else Qt.NoPen)
            painter.setBrush(_color(color))
            painter.drawEllipse(QPointF(x, y), radius, radius)

        for point in points:
            entity, kind = point["entity"], point["role"]
            if kind == "tree":
                continue
            center = transform.map(QPointF(point["px"], point["py"]))
            x, y, variation = center.x(), center.y(), point["variation"]
            entity_scale = entity.get("scale") if _finite(entity.get("scale")) else 1
            radius = max(.7, unit * (1.45 + variation * .45) * max(.5, min(2, entity_scale)))
            if kind in {"gold", "stone"}:
                r = max(2, unit * 1.7)
                color = ("#aa8140" if ink else "#d6b953") if kind == "gold" else ("#857c68" if ink else "#9ba6a4")
                painter.setBrush(_color(color))
                painter.setPen(QPen(_color("#554a36" if ink else "#273333"), max(.7, min(1.6, unit * .1))))
                painter.drawPolygon(QPolygonF([QPointF(x + math.cos(i * math.pi / 3) * r, y + math.sin(i * math.pi / 3) * r * .75) for i in range(6)]))
            elif kind in {"berry", "berries", "food"}:
                r = max(1, unit)
                dot(x, y, r, "#817451" if ink else "#536d3d", None if ink else "#26382999", .4)
                dot(x - r * .25, y - r * .1, max(.5, r * .2), "#8f5b46" if ink else "#b84e55")
            elif kind in {"spawn_marker", "sacred_site"}:
                r = max(3.5, unit * 1.3)
                painter.setPen(QPen(_color("#6b4736" if ink else "#f0d390"), 1.3))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(center, r, r)
                painter.drawLine(QPointF(x - r * 1.3, y), QPointF(x + r * 1.3, y))
                painter.drawLine(QPointF(x, y - r * 1.3), QPointF(x, y + r * 1.3))
            elif kind == "relic":
                r = max(2, unit * .65)
                dot(x, y, r, "#ae8242" if ink else "#f1d27e")
                painter.setPen(QPen(_color("#58432a" if ink else "#5a482b"), .7))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(QRectF(x - r * .5, y - r * .8, r, r * 1.6))
            elif kind == "building":
                r = max(2.3, unit * 2)
                painter.setBrush(_color("#8a6a4b" if ink else "#bd9570"))
                painter.setPen(QPen(_color("#55422e" if ink else "#624c38"), 1))
                painter.drawRect(QRectF(x - r, y - r * .65, r * 2, r * 1.3))
                painter.drawLine(QPointF(x - r, y), QPointF(x + r, y))
            elif kind in {"animal", "fish"}:
                dot(x, y, max(1.1, unit * .55), "#776348" if ink else "#91c7c4" if kind == "fish" else "#e5d6ae",
                    "#354034b8" if not ink and kind == "animal" else None, .5)
        if self.selected:
            center = transform.map(QPointF(self.selected["px"], self.selected["py"]))
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#fff2bd"), 2))
            painter.drawEllipse(center, max(8, unit * 2.5), max(8, unit * 2.5))
        painter.restore()

    def drawForeground(self, painter, rect):
        if self.render:
            self._draw_entities(painter, rect)
        else:
            painter.save()
            painter.resetTransform()
            painter.setPen(QColor("#aaaab3"))
            painter.drawText(self.viewport().rect().adjusted(20, 20, -20, -20), Qt.AlignCenter | Qt.TextWordWrap, self.empty_text)
            painter.restore()

    def pick_entity(self, position, radius=12):
        if not self.render or not self.entities_enabled:
            return None
        point = self.mapToScene(position)
        span = radius / max(1e-6, self.transform().m11())
        candidates = self.visible_entities(QRectF(point.x() - span, point.y() - span, span * 2, span * 2))
        best, distance = None, radius * radius
        for candidate in candidates:
            screen = self.viewportTransform().map(QPointF(candidate["px"], candidate["py"]))
            error = (screen.x() - position.x()) ** 2 + (screen.y() - position.y()) ** 2
            if error <= distance:
                best, distance = candidate, error
        return best

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
            self.selected = self.pick_entity(event.pos())
            self.viewport().update()
            self.entitySelected.emit(self.selected["entity"] if self.selected else None)


class FullMapView(QWidget):
    """74张完整代表实例的独立参考层；与旧官方参考视图接口兼容。"""
    zoomChanged = pyqtSignal(float)
    entitySelected = pyqtSignal(object)
    sourceRequested = pyqtSignal(dict)

    def __init__(self, parent=None, *, catalog=None):
        super().__init__(parent)
        if catalog is None:
            from full_map_catalog import FullMapCatalog
            catalog = FullMapCatalog()
        self.catalog = catalog
        self.setFont(make_ui_font(12))
        self.entries = {entry["key"]: entry for entry in catalog.entries()}
        self.entry, self._source_dialog, self._pending_camera = None, None, None
        self.setObjectName("FullMapView")
        self.setMinimumWidth(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.map_selector = ClickComboBox()
        self.map_selector.setEditable(True)
        self.map_selector.setInsertPolicy(QComboBox.NoInsert)
        self.map_selector.setMinimumWidth(0)
        self.map_selector.setMinimumContentsLength(8)
        self.map_selector.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.map_selector.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.map_selector.setAccessibleName("搜索官方完整参考实例")
        for entry in self.entries.values():
            label = str(entry.get("name_zh") or entry.get("name_en") or entry["key"])
            self.map_selector.addItem(label, entry["key"])
            self.map_selector.setItemData(self.map_selector.count() - 1, label, Qt.ToolTipRole)
        self.map_selector.setCurrentIndex(-1)
        self.map_selector.lineEdit().setPlaceholderText(f"搜索{len(self.entries)}张完整地图")
        self.map_selector.completer().setCaseSensitivity(Qt.CaseInsensitive)
        self.map_selector.completer().setFilterMode(Qt.MatchContains)
        self.map_selector.completer().setCompletionMode(QCompleter.PopupCompletion)
        # 可编辑选择框会新建补全列表，两个列表都沿用无可见滚动条的界面规则。
        completion_popup = self.map_selector.completer().popup()
        for popup in (self.map_selector.view(), completion_popup):
            popup.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            popup.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        completion_popup.setStyleSheet("background:#202023;color:#f4f4f5;selection-background-color:#303035;border:1px solid #4c4c55;")
        self.map_selector.activated[int].connect(lambda index: self.select_map(self.map_selector.itemData(index)))
        toolbar.addWidget(self.map_selector, 1)
        for text, callback in (("−", lambda: self.zoom_out()), ("+", lambda: self.zoom_in()), ("适应全图", self.fit_all)):
            button = QPushButton(text)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(28)
            button.setAccessibleName({"−": "缩小地图", "+": "放大地图"}.get(text, text))
            if len(text) == 1:
                button.setFixedWidth(28)
            button.clicked.connect(callback)
            toolbar.addWidget(button)
        self.source_button = QPushButton("来源")
        self.source_button.setFixedHeight(28)
        self.source_button.setCursor(Qt.PointingHandCursor)
        self.source_button.clicked.connect(self._show_source)
        toolbar.addWidget(self.source_button)
        layout.addLayout(toolbar)
        self.canvas = FullMapCanvas(catalog, self)
        self.canvas.zoomChanged.connect(self.zoomChanged)
        self.canvas.entitySelected.connect(self._select_entity)
        layout.addWidget(self.canvas, 1)
        self.source_label = QLabel("官方生成参考实例 · 非本场实战")
        self.source_label.setTextFormat(Qt.PlainText)
        self.source_label.setFont(make_ui_font(12))
        self.source_label.setStyleSheet("font-size:12px;color:#c7b590;padding:0;")
        self.source_label.setMinimumHeight(20)
        layout.addWidget(self.source_label)
        self.status_label = QLabel("选择地图查看完整高程、材质和保存资源位置。")
        self.status_label.setTextFormat(Qt.PlainText)
        self.status_label.setFont(make_ui_font(12))
        self.status_label.setStyleSheet("font-size:12px;color:#aaaab3;padding:0;")
        self.status_label.setMinimumHeight(20)
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        layout.addWidget(self.status_label)
        self.setStyleSheet("""
            QWidget#FullMapView QPushButton, QWidget#FullMapView QComboBox {
                min-height:0;max-height:28px;padding:3px 6px;border:1px solid #303036;
                border-radius:6px;background:#202023;color:#dddde3;font-size:11px;
            }
            QWidget#FullMapView QPushButton:hover {background:#2a2a30;border-color:#55555f;}
            QWidget#FullMapView QPushButton:focus {border-color:#b2b2bb;}
        """)
        self.source_button.setEnabled(False)

    def set_match_map(self, value):
        """只用已核对别名选择参考实例，不把该实例当作本场随机地图。"""
        return self.select_map(value)

    def set_map(self, key):
        return self.select_map(key)

    def select_map(self, key, *, load=True):
        self.entry = self.catalog.get(key) if key else None
        self._pending_camera = None
        self.map_selector.blockSignals(True)
        self.map_selector.setCurrentIndex(self.map_selector.findData(self.entry["key"]) if self.entry else -1)
        if not self.entry:
            self.map_selector.setEditText("")
        self.map_selector.blockSignals(False)
        self.source_button.setEnabled(bool(self.entry))
        entities = None
        entity_error = False
        availability = self.catalog.data_status(self.entry["key"]) if self.entry and hasattr(self.catalog, "data_status") else {"state": "ready"}
        available = availability.get("state") == "ready"
        if self.entry and available:
            try:
                entities = self.catalog.load_entities(self.entry["key"])
            except (OSError, ValueError, KeyError):
                entity_error = True
        self.canvas.empty_text = (availability.get("message_zh") or "地图资料包未安装，请先选择资料包。") if not available else "未匹配完整实例，请搜索并选择地图。"
        self.canvas.set_entry(self.entry if available else None, entities)
        if not self.entry:
            self.status_label.setText("未匹配完整实例，请搜索并选择地图。")
        elif not available:
            self.status_label.setText(availability.get("message_zh") or "地图资料包未安装，当前仅有地图目录与来源。")
        else:
            name = self.entry.get("name_zh") or self.entry["key"]
            count = len(self.canvas.index["points"]) if self.canvas.index else 0
            self.status_label.setText(f"{name} · {self.canvas.extent.width():g} × {self.canvas.extent.height():g} · {count:,} 项可见类目资源")
            if entity_error or self.canvas.index is None:
                self.status_label.setText(f"{name} · 参考地形可浏览，实体资料暂不可用。")
        self.status_label.setToolTip(self.status_label.text())
        self.source_label.setAccessibleName(self.source_label.text())
        return self.entry is not None

    def fit_all(self):
        self.canvas.fit_all()

    fit_map = fit_all

    def zoom_in(self):
        self.canvas.zoom_at(self.canvas.viewport().rect().center(), 1.3)

    def zoom_out(self):
        self.canvas.zoom_at(self.canvas.viewport().rect().center(), 1 / 1.3)

    def _select_entity(self, entity):
        if entity is None:
            self.status_label.setText("点选资源可查看本参考实例中的保存坐标。")
        else:
            position, kind = entity["position"], entity_role(entity)
            self.status_label.setText(f"{ENTITY_LABELS.get(kind, '其他实体')} · 横向 X {position['x']:.2f} · 高度 Y {position['y']:.2f} · 纵向 Z {position['z']:.2f}")
            note = "\n这是出生标记，不表示已生成村民或城镇中心。" if kind == "spawn_marker" else ""
            self.status_label.setToolTip(self.status_label.text() + note + "\n实体来源名称：" + str(entity.get("blueprint_reference_raw") or "未提供"))
        self.entitySelected.emit(entity)

    def _show_source(self):
        if not self.entry:
            return
        self.sourceRequested.emit(dict(self.entry))
        if self._source_dialog is not None:
            self._source_dialog.raise_()
            self._source_dialog.activateWindow()
            return
        dialog = FramelessDialog(self)
        dialog.setWindowTitle("地图来源")
        dialog.setFont(make_ui_font(13))
        dialog.resize(610, 260)
        dialog.setMinimumWidth(420)
        box = QVBoxLayout(dialog)
        box.setContentsMargins(12, 6, 12, 12)
        box.setSpacing(10)
        box.addWidget(DialogBar(dialog, "地图来源"))
        dialog.description = QLabel("官方生成并保存的固定参考实例\n含真实高程、地表材质与静态资源位置；不代表当前对局。", dialog)
        dialog.description.setWordWrap(True)
        dialog.description.setTextFormat(Qt.PlainText)
        dialog.description.setStyleSheet("background:transparent;color:#dddde3;")
        box.addWidget(dialog.description)
        explanation = QLabel("出生标记不表示已生成村民或城镇中心；世界坐标不标米或北向。", dialog)
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color:#aaaab3;")
        box.addWidget(explanation)
        dialog.details = QPlainTextEdit(dialog)
        dialog.details.setReadOnly(True)
        dialog.details.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        dialog.details.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        dialog.details.setFont(make_ui_font(11))
        dialog.details.setStyleSheet("background:#161619;color:#bdbdc6;border:1px solid #303036;border-radius:6px;padding:6px;")
        dialog.details.setPlainText(json.dumps(self.entry, ensure_ascii=False, indent=2))
        dialog.details.setAccessibleName("原始来源字段；source_sha256为源文件校验值，render为图块规格，coordinates为坐标校准")
        dialog.details.setMinimumHeight(210)
        dialog.details.hide()
        box.addWidget(dialog.details, 1)
        controls = QHBoxLayout()
        details_button = QPushButton("详细来源", dialog)
        details_button.setCheckable(True)
        details_button.toggled.connect(dialog.details.setVisible)
        controls.addWidget(details_button)
        controls.addStretch()
        close_button = QPushButton("关闭", dialog)
        close_button.clicked.connect(dialog.reject)
        controls.addWidget(close_button)
        box.addLayout(controls)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.finished.connect(lambda _result: setattr(self, "_source_dialog", None))
        self._source_dialog = dialog
        dialog.open()

    def capture_view_state(self):
        return {"map_key": self.entry["key"] if self.entry else None,
                "representation": "natural", "camera": self._pending_camera or self.canvas.capture_camera(),
                "selected_entity_id": self.canvas.selected["entity"]["id"] if self.canvas.selected else None}

    def restore_view_state(self, state):
        if not isinstance(state, dict):
            return
        self.select_map(state.get("map_key"))
        # 旧存档中的手绘状态只迁移为高程地表，镜头和资源点选保持原样。
        if self.isVisible():
            self.canvas.restore_camera(state.get("camera"))
        else:
            self._pending_camera = state.get("camera")
        selected = state.get("selected_entity_id")
        if selected and self.canvas.index:
            self.canvas.selected = next((p for p in self.canvas.index["points"] if p["entity"]["id"] == selected), None)
            if self.canvas.selected:
                self._select_entity(self.canvas.selected["entity"])

    def showEvent(self, event):
        super().showEvent(event)
        self.layout().activate()
        if self._pending_camera is not None:
            state, self._pending_camera = self._pending_camera, None
            self.canvas.restore_camera(state)

    def cleanup(self):
        if self._source_dialog is not None:
            self._source_dialog.close()
        self.canvas.tile_cache.clear()
        self.canvas.tree_cache.clear()
        self.canvas.tree_geometry_cache.clear()
        self.canvas.index = None
        if hasattr(self.catalog, "close"):
            self.catalog.close()

    def refresh_tiles(self):
        """宿主切换资料包后刷新当前地图，保留镜头与可重选实体。"""
        state = self.capture_view_state()
        self.restore_view_state(state)
        self.canvas.viewport().update()

