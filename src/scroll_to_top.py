"""查询页返回顶部：按钮固定在可见区域右下角，不跟随列表正文滚动。"""
from PyQt5.QtCore import QEvent, QRect, QRectF, Qt
from PyQt5.QtGui import QColor, QPainter, QPen, QRegion
from PyQt5.QtWidgets import QToolButton

from visual import line_icon


class ScrollToTopButton(QToolButton):
    """ScrollToTopButton（返回顶部按钮）仅在内容已向下滚动时出现。"""

    def __init__(self, scroll):
        super().__init__(scroll.viewport())
        self.scroll = scroll
        self.setObjectName('ScrollToTop')
        self.setFixedSize(44, 44)
        self.setMask(QRegion(self.rect(), QRegion.Ellipse))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setToolTip('返回顶部')
        self.setAccessibleName('返回顶部')
        self._arrow_icon = line_icon('arrow-up', '#3F3F46')
        self.setStyleSheet('QToolButton#ScrollToTop{background:transparent;border:0;padding:0;}')
        self.clicked.connect(self.return_to_top)
        scroll.viewport().installEventFilter(self)
        bar = scroll.verticalScrollBar()
        bar.valueChanged.connect(self.sync)
        bar.rangeChanged.connect(self.sync)
        self.sync()

    def return_to_top(self):
        """直接改变现有滚动位置，不重建列表、不改变查询或选中对局。"""
        self.scroll.verticalScrollBar().setValue(0)

    def sync(self, *args):
        viewport = self.scroll.viewport()
        self.move(max(0, viewport.width() - self.width() - 18),
                  max(0, viewport.height() - self.height() - 18))
        self.setVisible(self.scroll.verticalScrollBar().value() > 0)
        if not self.isHidden():
            self.raise_()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            self.sync()
        return False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#D9D9DF' if self.isDown() else '#FFFFFF' if self.underMouse() else '#F4F4F5'))
        painter.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        # 保留圆形点击区，内部使用统一的Lucide向上箭头与深灰色。
        self._arrow_icon.paint(painter, QRect(12, 12, 20, 20))
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor('#6C6C76'), 1.5, Qt.DotLine))
            painter.drawEllipse(QRectF(4, 4, 36, 36))
        painter.end()


def install_scroll_to_top(scroll):
    """为现有QScrollArea（滚动区域）安装按钮并返回强引用。"""
    return ScrollToTopButton(scroll)
