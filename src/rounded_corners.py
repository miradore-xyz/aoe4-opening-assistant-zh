"""铺满子控件的卡片共用平滑圆角，避免整数多边形遮罩的斜切与锯齿。"""
from functools import lru_cache
import math

from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QColor, QImage, QPainter, QPainterPath
from PyQt5.QtWidgets import QWidget

CARD_RADIUS = 12  # 同级卡片统一使用逻辑像素半径。


@lru_cache(maxsize=32)
def corner_image(scale, right, bottom, background="#0F0F11", radius=CARD_RADIUS):
    """只缓存一个小角；镜像同一像素图使四角在分数缩放下也对称。"""
    edge = math.ceil(radius * scale)
    image = QImage(edge, edge, QImage.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(scale)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    square, circle = QPainterPath(), QPainterPath()
    square.addRect(QRectF(0, 0, radius, radius))
    circle.addEllipse(QRectF(0, 0, radius * 2, radius * 2))
    painter.fillPath(square.subtracted(circle), QColor(background))
    painter.end()
    return image.mirrored(right, bottom)


class CornerCover(QWidget):
    """只覆盖卡片角落外缘，不复制正文/地图，不拦截鼠标。"""
    def __init__(self, right, bottom, parent, background="#0F0F11", radius=CARD_RADIUS):
        super().__init__(parent)
        self.right, self.bottom = right, bottom
        self.background, self.radius = background, radius
        self.setFixedSize(radius, radius)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAutoFillBackground(False)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawImage(0, 0, corner_image(self.devicePixelRatioF(), self.right, self.bottom,
                                           self.background, self.radius))


def make_corner_covers(parent, background="#0F0F11"):
    return [CornerCover(right, bottom, parent, background)
            for right, bottom in ((False, False), (True, False), (False, True), (True, True))]


def position_corner_covers(parent, corners):
    for corner in corners:
        corner.move(parent.width() - corner.radius if corner.right else 0,
                    parent.height() - corner.radius if corner.bottom else 0)
        corner.raise_()
