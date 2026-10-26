"""悬浮 HUD（游戏提示层）的轻暗影，不采样屏幕、不改变文字不透明度。"""
import math

from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPixmap, QPen, QRadialGradient, QTransform
from PyQt5.QtWidgets import QGraphicsDropShadowEffect


class HudOutlineEffect(QGraphicsDropShadowEffect):
    """保留原接口名，改用 Qt 的单次轻暗影，避免八方向剪影叠成厚黑边。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Qt 自行处理显示缩放与源图坐标；只柔化暗影，前景始终原样绘制。
        self.setBlurRadius(2.2)
        self.setOffset(0, .65)
        self.setColor(QColor(0, 0, 0, 191))


def _percentage(value, default):
    """外观百分比只接受有限数值；布尔值不冒充强度或不透明度。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return default
    return max(0, min(100, value))


def configure_hud_shadow(widget, enabled=True, strength=75):
    """即时调整纯黑柔影；复用已有特效，开关不会改变前景或整窗不透明度。"""
    effect = widget.graphicsEffect()
    if not isinstance(effect, HudOutlineEffect):
        effect = HudOutlineEffect(widget)
        widget.setGraphicsEffect(effect)
    amount = _percentage(strength, 75)
    effect.setColor(QColor(0, 0, 0, round(255 * amount / 100)))
    effect.setEnabled(bool(enabled) and amount > 0)
    widget.update()
    return effect


def install_hud_outline(widget):
    """每个可读前景单独处理，不给整个悬浮矩形增加深色背景。"""
    return configure_hud_shadow(widget)


def overlay_style(value):
    """三种已选设计的稳定配置值；非法旧值回退自由字幕。"""
    return value if value in ("a", "c", "b") else "a"


def style_backdrop(width, height, ratio=1.0, style="a", *, goal=False,
                   background_color=None, opacity=None):
    """同源材质；显式opacity为0–100百分比，只改变背景，不混入文字。

    不传新参数时沿用旧版各款默认材质；传入0会连同边线完全透明。
    """
    style = overlay_style(style)
    picture = QPixmap(max(1, round(width * ratio)), max(1, round(height * ratio)))
    picture.setDevicePixelRatio(ratio)
    picture.fill(Qt.transparent)
    custom = background_color is not None or opacity is not None
    color = QColor(background_color) if background_color is not None else QColor("#1b1b1e")
    if not color.isValid():
        color = QColor("#1b1b1e")
    legacy_alpha = 237 if style == "b" else 198 if style == "c" else 170
    alpha = round(255 * _percentage(opacity, 65) / 100) if opacity is not None else legacy_alpha
    if alpha == 0:
        return picture
    painter = QPainter(picture)
    painter.setRenderHint(QPainter.Antialiasing)
    bounds = QRectF(1, 1, max(0, width - 2), max(0, height - 2))
    if style == "b":
        # 完整表面负责复杂地图上的稳定阅读；边界只保留低对比细线。
        border = color.lighter(145) if custom else QColor(235, 241, 226)
        border.setAlpha(round(alpha * 30 / 237) if custom else 30)
        painter.setPen(QPen(border, 1))
        fill = QColor(color)
        fill.setAlpha(alpha)
        painter.setBrush(fill)
        painter.drawRoundedRect(bounds, 9 if goal else 12, 9 if goal else 12)
    else:
        painter.setClipRect(QRectF(2, 2, max(0, width - 4), max(0, height - 4)))
        edge = min(7 if goal else 17, width * .3)
        if style == "a" and custom:
            # 自由字幕用偏右上方的椭圆暗衬，色阶对应提案的柔和光晕。
            # 直接一次绘制渐变，不采样游戏画面，也不引入逐帧模糊开销。
            radial = QRadialGradient(0, 0, 1)
            for stop, factor in ((0, 1), (.46, 166 / 212), (.65, 85 / 212), (.84, 0), (1, 0)):
                fill = QColor(color)
                fill.setAlpha(round(alpha * factor))
                radial.setColorAt(stop, fill)
            brush = QBrush(radial)
            transform = QTransform()
            transform.translate(width * .6, height * .35)
            transform.scale(max(1, width * .9), max(1, height * .95))
            brush.setTransform(transform)
            painter.fillRect(bounds, brush)
            # 光晕延伸出字幕范围的部分继续柔化，窗口四周不出现硬切边。
            painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
            horizontal = QLinearGradient(0, 0, width, 0)
            for stop, amount in ((0, 0), (edge / max(1, width), 255),
                                 (1 - edge / max(1, width), 255), (1, 0)):
                horizontal.setColorAt(stop, QColor(0, 0, 0, amount))
            painter.fillRect(bounds, horizontal)
        else:
            horizontal = QLinearGradient(0, 0, width, 0)
            right_alpha = alpha if style == "a" else round(alpha * 158 / 198)
            for stop, stop_alpha in ((0, 0), (edge / max(1, width), alpha),
                                     (1 - edge / max(1, width), right_alpha), (1, 0)):
                fill = QColor(color)
                fill.setAlpha(stop_alpha)
                horizontal.setColorAt(stop, fill)
            painter.fillRect(bounds, horizontal)
        painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        vertical = QLinearGradient(0, 0, 0, height)
        top, bottom = (3, 3) if goal else (8, 20)
        for stop, alpha in ((0, 0), (min(.49, top / max(1, height)), 255),
                            (max(.51, 1 - bottom / max(1, height)), 255), (1, 0)):
            vertical.setColorAt(stop, QColor(0, 0, 0, alpha))
        painter.fillRect(bounds, vertical)
    painter.end()
    return picture
