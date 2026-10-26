"""产品显示名称与品牌标记：可缩放的原创几何龙首。"""
from __future__ import annotations

import math

from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPixmap


# APP_NAME 是产品显示名；数据目录、安装标识和更新地址继续沿用原值。
APP_NAME = "AoE4 HELP"


def brand_mark(size: int = 26) -> QPixmap:
    """返回指定逻辑像素大小的品牌图标；高分屏按设备倍率绘制。

    brand_mark 为品牌标记，size 为逻辑边长，QPixmap 为 Qt 位图。
    最低使用两倍像素密度，小图标缩放时仍保留清楚的轮廓。
    """
    edge = max(1, int(size))
    active_window = QGuiApplication.focusWindow()
    screen = active_window.screen() if active_window is not None else QGuiApplication.primaryScreen()
    ratio = max(2.0, float(screen.devicePixelRatio()) if screen is not None else 2.0)
    physical_edge = math.ceil(edge * ratio)
    # 使用实际像素边长计算倍率，确保任意缩放比例下逻辑尺寸仍准确。
    ratio = physical_edge / edge
    mark = QPixmap(physical_edge, physical_edge)
    mark.setDevicePixelRatio(ratio)
    mark.fill(Qt.transparent)

    painter = QPainter(mark)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.scale(edge / 32.0, edge / 32.0)
    painter.setPen(Qt.NoPen)

    # 浅灰底与近黑龙首延续页面的中性色，不给顶栏增加额外金色装饰。
    painter.setBrush(QColor("#DEDEE3"))
    painter.drawRoundedRect(QRectF(0.0, 0.0, 32.0, 32.0), 8.0, 8.0)

    # 龙首采用大块剪影：向后的双角、前伸下颌和弯曲颈部在 24 像素仍可分辨。
    dragon = QPainterPath()
    dragon.moveTo(7.5, 25.0)
    dragon.cubicTo(11.8, 23.5, 12.8, 20.5, 10.9, 17.7)
    dragon.lineTo(7.2, 13.4)
    dragon.lineTo(11.7, 14.4)
    dragon.lineTo(9.4, 7.0)
    dragon.lineTo(15.1, 10.8)
    dragon.lineTo(18.4, 6.9)
    dragon.lineTo(18.0, 12.0)
    dragon.cubicTo(20.7, 12.2, 22.7, 13.7, 23.6, 16.0)
    dragon.lineTo(26.0, 18.4)
    dragon.lineTo(23.9, 20.5)
    dragon.lineTo(20.5, 19.6)
    dragon.lineTo(18.8, 21.0)
    dragon.lineTo(20.9, 23.2)
    dragon.cubicTo(17.6, 27.5, 11.7, 28.0, 7.5, 25.0)
    dragon.closeSubpath()
    painter.setBrush(QColor("#18181B"))
    painter.drawPath(dragon)

    # 眼部仅保留一个切面；没有描边与细小纹理，避免退化为模糊线团。
    eye = QPainterPath()
    eye.moveTo(19.0, 14.5)
    eye.lineTo(22.0, 16.0)
    eye.lineTo(19.7, 16.4)
    eye.closeSubpath()
    painter.setBrush(QColor("#DEDEE3"))
    painter.drawPath(eye)
    painter.end()
    return mark
