"""官方规则粗布局的正交地形参考；纹理和光照仅为阅读美术。

tt_none（待引擎填充的区域）始终保持中性灰，不能变成确定草地。
树林、金石、圣地及贸易标记表示规则区域；出生点是示例席位，均非实战实体。
不读取游戏、不模拟地形高度，不将该参考与回放坐标对齐。仅依赖已有Qt。
"""
from __future__ import annotations

import math
import random
import re
import sys
from collections import defaultdict, deque
from functools import lru_cache
from pathlib import Path

from app_fonts import make_ui_font, register_ui_fonts
from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QBrush, QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen


REFERENCE_LEGEND = "灰色为待定区域；树林、矿物为规则区域示意；圆形席位为示例出生区，非实战单位。"
_PALETTE = {
    "unresolved": "#686d6b", "grass": "#848064", "shore": "#aaa389",
    "upland": "#8a8573", "mountain": "#777c77", "forest": "#3b5342",
    "water": "#345969", "deep_water": "#294956", "marsh": "#647a6a",
}
_PLAYER_COLORS = ("#73afd8", "#d77f73", "#d8ba6b", "#90b786", "#ba9ccc", "#7dbbb8", "#d6a080", "#c0c6cf")
_ICONS = {"gold": "resource/resource_gold.webp", "stone": "resource/resource_stone.webp",
          "holy": "resource/sacred_sites.webp", "trade": "building_economy/market.webp",
          "player": "building_economy/town-center.webp"}


def _surface(terrain):
    """仅识别类型中明确的地形词，未知类型保持待定，不把地图名当作地形。"""
    text = terrain.lower()
    if text == "tt_none" or not text.startswith("tt_"):
        return "unresolved"
    # lake_side（湖畔）也用于地图专属地标名称，不能因此把地标画进水里。
    local = text.replace("tt_lake_side_", "tt_").replace("tt_sacred_mountains_", "tt_")
    if "player_start" in local or _marker_kinds(local):
        # four_lakes/westlake在这些类型中是地图限定词，不是出生区/圣地的水体声明。
        if any(word in local for word in ("hill", "plateau")):
            return "upland"
        if any(word in local for word in ("plains", "valley", "clearing")):
            return "grass"
        return "unresolved"
    if any(word in local for word in ("forest", "trees", "stealth_natural")) and "clearing" not in local:
        return "forest"
    if "shore" in local or "beach" in local:
        return "shore"
    if "ocean" in local:
        return "deep_water" if "deep" in local else "water"
    if "lake" in local or "pond" in local or ("river" in local and "dry_river" not in local):
        return "water"
    if "swamp" in local:
        return "marsh"
    if "mountain" in local:
        return "mountain"
    if any(word in local for word in ("hill", "plateau", "cliff", "ridge")):
        return "upland"
    if any(word in local for word in ("plains", "flatland", "clearing", "valley")):
        return "grass"
    return "unresolved"


def _marker_kinds(terrain):
    """只给明确的资源/目标区域加图符，golden（黄金色）不等于金矿。"""
    text = terrain.lower()
    result = []
    if re.search(r"(?:^|_)gold(?:_|$)|_wgold(?:_|$)", text):
        result.append("gold")
    if re.search(r"(?:^|_)stone(?:_|$)", text):
        result.append("stone")
    if re.search(r"(?:^|_)holy(?:_|$)", text) or "sacred_site" in text:
        result.append("holy")
    if "settlement" in text:
        result.append("trade")
    return result


@lru_cache(maxsize=12)
def _texture(kind):
    """稳定的低对比细纹理，代表阅读材质，不代表逐棵树或真实地表细节。"""
    edge = 160
    image = QImage(edge, edge, QImage.Format_RGB32)
    base = QColor(_PALETTE[kind])
    generator = random.Random("official-reference-material:" + kind)
    for y in range(edge):
        for x in range(edge):
            grain = generator.randrange(-4, 5)
            variation = math.sin(x * .061 + math.sin(y * .044) * 2) * 2
            if kind == "forest":
                variation += math.sin(x * .12 + math.sin(y * .091) * 2) * 4 + math.sin(y * .17 + x * .07) * 3
            shade = round(grain + variation)
            image.setPixel(x, y, QColor(max(0, min(255, base.red() + shade)),
                                       max(0, min(255, base.green() + shade)),
                                       max(0, min(255, base.blue() + shade))).rgb())
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    if kind in ("water", "deep_water"):
        painter.setPen(QPen(QColor(183, 204, 207, 15), .8))
        for y in range(9, edge, 13):
            path = QPainterPath(QPointF(-12, y))
            for x in range(0, edge + 24, 24):
                path.quadTo(QPointF(x, y - 2), QPointF(x + 12, y))
            painter.drawPath(path)
    elif kind == "forest":
        # 交叠短笔触形成连贯林冠材质，不以圆球/散点假装单棵树。
        for _ in range(330):
            x, y = generator.randrange(edge), generator.randrange(edge)
            painter.setPen(QPen(QColor(16, 34, 24, generator.randrange(15, 34)), 1.2))
            painter.drawLine(QPointF(x - 3, y + 2), QPointF(x + 3, y - 3))
            painter.setPen(QPen(QColor(153, 169, 128, 22), .7))
            painter.drawLine(QPointF(x - 2, y + 1), QPointF(x + 1, y - 2))
    elif kind in ("mountain", "upland"):
        for _ in range(75):
            x, y = generator.randrange(edge), generator.randrange(edge)
            path = QPainterPath(QPointF(x - 6, y + 4))
            path.lineTo(x, y - 3)
            path.lineTo(x + 5, y)
            painter.setPen(QPen(QColor(211, 211, 187, 20), 1))
            painter.drawPath(path)
    elif kind in ("grass", "marsh"):
        for _ in range(120):
            x, y = generator.randrange(edge), generator.randrange(edge)
            painter.setPen(QPen(QColor(48, 64, 36, 23), .8))
            painter.drawLine(QPointF(x, y), QPointF(x + 2, y - 3))
    painter.end()
    return image


def _region_path(cells, cell_width, cell_height, bounds):
    """合并相邻粗格后柔化外缘；不绘制坐标网格或暗示精确岸线。"""
    squares = QPainterPath()
    squares.setFillRule(Qt.WindingFill)
    for row, column in cells:
        squares.addRect(bounds.left() + column * cell_width, bounds.top() + row * cell_height,
                        cell_width, cell_height)
    result = QPainterPath()
    result.setFillRule(Qt.WindingFill)
    radius = min(cell_width, cell_height) * .19
    for polygon in squares.simplified().toSubpathPolygons():
        vertices = list(polygon)
        if len(vertices) > 1 and vertices[0] == vertices[-1]:
            vertices.pop()
        if len(vertices) < 3:
            continue
        cuts = []
        for index, vertex in enumerate(vertices):
            previous, following = vertices[index - 1], vertices[(index + 1) % len(vertices)]
            def toward(other):
                length = math.hypot(other.x() - vertex.x(), other.y() - vertex.y())
                amount = min(.4, radius / length) if length else 0
                return vertex + (other - vertex) * amount
            cuts.append((toward(previous), vertex, toward(following)))
        result.moveTo(cuts[0][0])
        for before, vertex, after in cuts:
            result.lineTo(before)
            result.quadTo(vertex, after)
        result.closeSubpath()
    return result


def _components(cells):
    """一个相连类型区域仅放一个标记，数量不是矿点或建筑数量。"""
    remaining = set(cells)
    while remaining:
        first = min(remaining)
        remaining.remove(first)
        queue, group = deque([first]), [first]
        while queue:
            row, column = queue.popleft()
            for point in ((row - 1, column), (row + 1, column), (row, column - 1), (row, column + 1)):
                if point in remaining:
                    remaining.remove(point)
                    group.append(point)
                    queue.append(point)
        yield group


@lru_cache(maxsize=6)
def _icon(kind):
    root = Path(sys._MEIPASS) / "assets" if hasattr(sys, "_MEIPASS") else Path(__file__).resolve().parent / "upstream/docs/assets"
    return QImage(str(root / "aoe4" / _ICONS[kind]))


def _draw_marker(painter, center, radius, kind, player=None):
    color = QColor(_PLAYER_COLORS[player % len(_PLAYER_COLORS)]) if player is not None else QColor("#c5ba91")
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(8, 17, 13, 65))
    painter.drawEllipse(center + QPointF(0, 2), radius + 3, radius + 3)
    painter.setPen(QPen(color, 2.1 if player is not None else 1.5))
    painter.setBrush(QColor(30, 38, 33, 235))
    painter.drawEllipse(center, radius, radius)
    icon = _icon(kind)
    if not icon.isNull():
        area = QRectF(center.x() - radius * .70, center.y() - radius * .70, radius * 1.4, radius * 1.4)
        painter.save()
        clip = QPainterPath()
        clip.addEllipse(area)
        painter.setClipPath(clip, Qt.IntersectClip)
        painter.drawImage(area, icon)
        painter.restore()
    else:
        # 素材不可用时用明确几何符号，保持圆形区域含义，不伪造游戏实体。
        painter.setPen(QPen(color, 1.6))
        half = radius * .40
        if kind == "holy":
            painter.drawLine(center + QPointF(-half, 0), center + QPointF(half, 0))
            painter.drawLine(center + QPointF(0, -half), center + QPointF(0, half))
        else:
            path = QPainterPath(center + QPointF(0, -half))
            for dx, dy in ((half, 0), (0, half), (-half, 0), (0, -half)):
                path.lineTo(center + QPointF(dx, dy))
            painter.drawPath(path)
    if player is not None:
        tag = center + QPointF(radius * .72, radius * .72)
        painter.setBrush(color)
        painter.setPen(QPen(QColor("#26352c"), 1))
        painter.drawEllipse(tag, radius * .40, radius * .40)
        font = make_ui_font()
        font.setPixelSize(max(10, round(radius * .58)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#14241b"))
        painter.drawText(QRectF(tag.x() - radius, tag.y() - radius, radius * 2, radius * 2), Qt.AlignCenter, str(player + 1))


@lru_cache(maxsize=3)
def _render(grid, edge):
    rows, columns = len(grid), len(grid[0])
    image = QImage(edge, edge, QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor("#242e29"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    bounds = QRectF(14, 14, edge - 28, edge - 28)
    width, height = bounds.width() / columns, bounds.height() / rows
    painter.fillRect(bounds, QBrush(_texture("unresolved")))
    surfaces, markers, players = defaultdict(list), defaultdict(list), defaultdict(list)
    for row, cells in enumerate(grid):
        for column, (terrain, player) in enumerate(cells):
            surfaces[_surface(terrain)].append((row, column))
            for kind in _marker_kinds(terrain):
                markers[kind].append((row, column))
            if player is not None:
                players[player].append((row, column))
    painter.save()
    painter.setClipRect(bounds)
    for kind in ("grass", "shore", "upland", "mountain", "forest", "marsh", "water", "deep_water"):
        if not surfaces[kind]:
            continue
        path = _region_path(surfaces[kind], width, height, bounds)
        if kind in ("water", "deep_water", "forest", "mountain"):
            painter.setPen(QPen(QColor(18, 31, 25, 48), max(2, edge / 310)))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        painter.fillPath(path, QBrush(_texture(kind)))
        # 宽幅柔光是阅读材质，绝不声称记录了实际高度或日照。
        light = QLinearGradient(bounds.topLeft(), bounds.bottomRight())
        light.setColorAt(0, QColor(243, 229, 187, 10))
        light.setColorAt(1, QColor(10, 27, 20, 18))
        painter.fillPath(path, light)
    radius = max(17, min(23, min(width, height) * .24))
    def anchor(group):
        average = (sum(point[0] for point in group) / len(group), sum(point[1] for point in group) / len(group))
        row, column = min(group, key=lambda point: (point[0] - average[0]) ** 2 + (point[1] - average[1]) ** 2)
        return QPointF(bounds.left() + (column + .5) * width, bounds.top() + (row + .5) * height)
    for kind, cells in markers.items():
        for group in _components(cells):
            _draw_marker(painter, anchor(group), radius, kind)
    for player, group in sorted(players.items()):
        _draw_marker(painter, anchor(group), radius * 1.13, "player", player)
    painter.restore()
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(168, 179, 160, 85), 1))
    painter.drawRect(bounds)
    painter.end()
    image.setText("kind", "official_rule_reference")
    image.setText("not_match_reconstruction", "true")
    image.setText("legend", REFERENCE_LEGEND)
    return image


def render_layout_reference(layout, size=1536):
    """将受支持的官方规则参考渲染为QImage（Qt图像）；不接受实战地图声明。

    尺寸限制在1024–2048像素。缓存至多三张，返回写时复制图像以免调用方污染缓存。
    """
    if not isinstance(layout, dict) or layout.get("kind") != "official_rule_reference" or layout.get("not_match_reconstruction") is not True:
        raise ValueError("只接受明确标注为非实战重建的官方规则布局。")
    if layout.get("status") != "supported":
        raise ValueError("该地图没有可渲染的规则布局。")
    source = layout.get("grid")
    if not isinstance(source, list) or not source or not all(isinstance(row, list) and row for row in source):
        raise ValueError("规则布局缺少有效粗网格。")
    if len({len(row) for row in source}) != 1:
        raise ValueError("规则布局各行宽度不一致。")
    grid = []
    for row in source:
        cells = []
        for cell in row:
            terrain = cell.get("terrain", "tt_none") if isinstance(cell, dict) else "tt_none"
            terrain = terrain if isinstance(terrain, str) else "tt_none"
            player = cell.get("player_index") if isinstance(cell, dict) else None
            player = player if type(player) is int and player >= 0 else None
            cells.append((terrain, player))
        grid.append(tuple(cells))
    return QImage(_render(tuple(grid), max(1024, min(2048, int(size)))))
