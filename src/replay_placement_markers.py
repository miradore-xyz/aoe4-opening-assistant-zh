"""官方图标显示实体放置记录：12至32屏幕像素，不表示竣工或米制地基。

placement（放置）坐标只定位原记录；多点字段不擅自解释成多栋建筑或墙形。
"""
from __future__ import annotations

from bisect import bisect_right
from functools import lru_cache
import json
import math
from pathlib import Path
import sys

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF

from catalog import CATALOG_ROOT, _document, resolve_icon
from match_detail import load_entity_catalog

def marker_size(relative_zoom=1):
    """观看倍率平方根控制注记大小，和世界米制建筑占地无关。"""
    return min(32.0, max(12.0, 16*math.sqrt(max(.000001, float(relative_zoom)))))


def _identifier(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdecimal() and int(value) > 0:
        return int(value)
    return None


def _asset_root():
    root = Path(sys._MEIPASS)/'assets/design' if hasattr(sys, '_MEIPASS') else Path(__file__).resolve().parent/'界面素材'
    return root/'tactical'


@lru_cache(maxsize=1)
def official_icon_registry():
    """读取原实体身份及精确官方图标补充映射，不再加载3D顶视素材。"""
    path = _asset_root()/'official_icon_registry.json'
    if not path.is_file():
        return {'icons': {}, 'identity_overrides': {}}
    return json.loads(path.read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def _catalog_icons():
    result = {}
    for item in _document().get('items', []):
        if item.get('kind') in ('building', 'unit') and item.get('id'):
            result.setdefault(str(item['id']), []).append(item)
    return result


@lru_cache(maxsize=1024)
def building_metadata(type_id):
    """按精确蓝图编号解析名称；未知编号不借用相似名字或另一文明图片。"""
    identity = _identifier(type_id)
    registry = official_icon_registry()
    entity = dict(load_entity_catalog().get(str(identity), {})) if identity is not None else {}
    # 原EBP（实体蓝图）编号覆盖优先，不能与同号SBP（小队蓝图）混用。
    entity.update(registry.get('identity_overrides', {}).get(str(identity), {}))
    exact = registry.get('icons', {}).get(str(identity))
    icon = str(_asset_root()/exact['file']) if exact is not None else None
    if icon is None:
        candidates = [row for row in _catalog_icons().get(str(entity.get('id')), [])
                      if row.get('kind') == entity.get('kind') and
                      set(row.get('civs') or []) & set(entity.get('civs') or [])]
        paths = {str(resolve_icon(row)) for row in candidates}
        # 本地目录只含远程来源标记时，使用同一精确编号的随包离线PNG；不发下载请求。
        if not paths and entity.get('kind') in ('building', 'unit'):
            source_icon = str(entity.get('icon') or '')
            category = 'buildings' if entity['kind'] == 'building' else 'units'
            prefix = f'https://data.aoe4world.com/images/{category}/'
            filename = source_icon[len(prefix):] if source_icon.startswith(prefix) else ''
            if filename and '/' not in filename and '\\' not in filename:
                paths.add(str(CATALOG_ROOT/'icons'/category/filename))
        icon = next(iter(paths)) if len(paths) == 1 else None
    if icon is not None and not Path(icon).is_file():
        icon = None
    kind = entity.get('kind')
    known = kind in ('building', 'unit')
    return {'type_id': identity, 'kind': kind,
            'name': str(entity.get('name') or
                        (f'对象编号 {identity}' if identity is not None else '对象编号未提供')),
            'known': known, 'icon': icon, 'render_mode': 'official_icons',
            'wall': bool(entity.get('wall')) or any('wall' in str(value) or 'palisade' in str(value)
                        for value in [entity.get('base_id', ''), *(entity.get('classes') or [])])}


def building_name(type_id):
    """供地图和页面详情共用的精确中文名称。"""
    return building_metadata(type_id)['name']


def placement_description(event):
    metadata = building_metadata(event.get('building_type_id'))
    label = {'building': '建筑放置记录', 'unit': '器械放置记录'}.get(metadata['kind'], '实体放置记录')
    missing = ' · 官方图标待补' if metadata['icon'] is None else ''
    return f"{label} · {metadata['name']}{missing} · 完成状态未知"


@lru_cache(maxsize=1024)
def _stamp(type_id):
    """小图只生成一次；paint（绘制）只拷贝缓存，不重复缩放或读图。"""
    metadata = building_metadata(type_id)
    # 32逻辑像素母图、两倍密度；不画卡片底、地基框或重复文字。
    result = QPixmap(64, 64)
    result.setDevicePixelRatio(2)
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    picture = QPixmap(metadata['icon']) if metadata['icon'] is not None else QPixmap()
    if not picture.isNull():
        # 官方图案作为固定屏幕注记居中显示；图标外框不代表游戏地基占地。
        scaled = picture.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        target = QRectF((64-scaled.width())/4, (64-scaled.height())/4,
                        scaled.width()/2, scaled.height()/2)
        painter.drawPixmap(target, scaled, QRectF(scaled.rect()))
    else:
        # 缺少精确图标时只给小型中性定位符，不借另一种对象的图标或画问号。
        painter.setPen(QColor('#d8d8de'))
        painter.setBrush(QColor('#34343c'))
        painter.drawPolygon(QPolygonF([QPointF(16, 12), QPointF(20, 16),
                                      QPointF(16, 20), QPointF(12, 16)]))
    painter.end()
    return result


class PlacementMarkerLayer:
    """可定位原record（记录）的注记层；不使用朝向、米制尺寸或完成/摧毁推测。"""

    def __init__(self, changed=None):
        # 由画布前景绘制，不给固定屏幕尺寸注记伪造世界包围盒。
        self._changed, self._visible = changed, True
        self.events, self.filtered_events, self.visible_records = [], [], []
        self.colors = {}
        self._player_colors = {}
        self._anchor_pen = QPen(QColor('#0f0f11'), .75)
        self.selected_event_id = None
        self._times, self._latest, self._drawables = [], {}, []
        self._stop = -1
        self._paint_geometry, self._geometry_key = None, None
        self._geometry_builds = 0

    def update(self):
        if self._changed is not None:
            self._changed()

    def setVisible(self, visible):
        visible = bool(visible)
        if visible != self._visible:
            self._visible = visible
            self.update()

    def isVisible(self):
        return self._visible

    def set_selected(self, event_id):
        if event_id != self.selected_event_id:
            self.selected_event_id = event_id
            self.update()

    def set_events(self, events, bounds, colors):
        self.events = [event for event in events if event.get('action') == 'place_building']
        self.colors = colors
        self._player_colors = {identity: QColor(color) for identity, color in colors.items()}
        self._unknown_color = QColor('#9ca0ab')
        self.filter_player(None)

    def filter_player(self, player):
        self.filtered_events = [event for event in self.events if player is None or event.get('player_id') == player]
        self._times = [event['time'] for event in self.filtered_events]
        self._stop = -1
        self._latest, self._drawables, self.visible_records = {}, [], []
        self._paint_geometry, self._geometry_key = None, None

    @staticmethod
    def _key(event):
        return (event.get('player_id'), event.get('building_type_id'), event['x'], event['y'])

    def seek(self, seconds):
        stop = bisect_right(self._times, seconds+1e-8)
        if stop == self._stop:
            return False
        start = max(0, self._stop)
        if stop < self._stop:
            start, self._latest = 0, {}
        for index in range(start, stop):
            event = self.filtered_events[index]
            self._latest[self._key(event)] = (index, event)
        # 后来的注记盖在较早的上方；同位置同类只留最新记录，不表示取消或摧毁。
        self.visible_records = [event for _index, event in sorted(self._latest.values())]
        self._drawables = [(QPointF(event['x'], -event['y']),
                            _stamp(event.get('building_type_id')),
                            self._player_colors.get(event.get('player_id'), self._unknown_color),
                            event) for event in self.visible_records]
        self._stop = stop
        self._paint_geometry, self._geometry_key = None, None
        self.update()
        return True

    def paint(self, painter, option, widget=None, *, relative_zoom=1):
        if not self._visible:
            return
        transform = painter.worldTransform()
        size = marker_size(relative_zoom)
        # 几何缓存不含平移：拖动不再逐条变换场景点或重建源区域。
        # 不缓存二次栅格图，保留原图的分像素采样、锚点笔刷和选中轮廓原有层序。
        linear = (transform.m11(), transform.m12(), transform.m21(), transform.m22())
        key = (linear, size)
        if self._paint_geometry is None or self._geometry_key != key:
            from PyQt5.QtGui import QTransform
            linear_transform = QTransform(*linear, 0, 0)
            geometry = []
            for point, stamp, color, event in self._drawables:
                screen = linear_transform.map(point)
                geometry.append((screen.x(), screen.y(), stamp, QRectF(stamp.rect()), color, event.get('id')))
            self._paint_geometry, self._geometry_key = geometry, key
            self._geometry_builds += 1
        painter.save()
        painter.resetTransform()
        dx, dy = transform.dx(), transform.dy()
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setPen(self._anchor_pen)
        last_color = None
        for x, y, stamp, source_rect, color, event_id in self._paint_geometry:
            # 先加平移再构造目标框，和原路径运算顺序一致，避免高分屏边缘像素偏差。
            target = QRectF(x+dx-size/2, y+dy-size/2, size, size)
            painter.drawPixmap(target, stamp, source_rect)
            if color is not last_color:
                painter.setBrush(color)
                last_color = color
            painter.drawEllipse(QPointF(target.right()-2, target.bottom()-2), 2, 2)
            if event_id == self.selected_event_id:
                painter.setPen(QPen(QColor('#d4d4db'), 1))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(target.adjusted(-1, -1, 1, 1))
                painter.setPen(self._anchor_pen)
                last_color = None
        painter.restore()

    def pick(self, position, scale, relative_zoom=1):
        if not self.isVisible():
            return None
        scale = max(.000001, abs(scale))
        radius = marker_size(relative_zoom)/2
        for point, _stamp_value, _color, event in reversed(self._drawables):
            if abs(point.x()-position.x())*scale <= radius and abs(point.y()-position.y())*scale <= radius:
                return event
        return None
