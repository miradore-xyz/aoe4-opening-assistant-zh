"""开发中占位组件：以同一份矢量素材居中绘制，不显示说明文字。"""
from pathlib import Path
import sys

from PyQt5.QtCore import Qt, QRectF, QSize
from PyQt5.QtGui import QPainter
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QWidget, QSizePolicy


class DevelopmentPlaceholder(QWidget):
    """DevelopmentPlaceholder（开发中占位）：随可用空间居中，图标最大宽180像素。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DevelopmentPlaceholder")
        self.setAccessibleName("正在开发中")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        root = (Path(sys._MEIPASS) / "assets" / "design" if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parent / "界面素材")
        self.renderer = QSvgRenderer(str(root / "施工中.svg"), self)

    def sizeHint(self):
        """仅提供首选尺寸，不设置最小高度，以免空页面撑大主窗口。"""
        return QSize(240, 220)

    def icon_rect(self):
        """返回以内容区域中心为基准的等比例绘制区域。"""
        width = max(0.0, min(180.0, self.width() - 24.0, (self.height() - 24.0) * 180.0 / 160.0))
        height = width * 160.0 / 180.0
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        self.renderer.render(painter, self.icon_rect())
        painter.end()
