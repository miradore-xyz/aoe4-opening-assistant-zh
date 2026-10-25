"""下拉选择控件：避免浏览设置时被滚轮意外改选。"""

from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtWidgets import QApplication, QAbstractScrollArea, QComboBox, QSlider, QSpinBox


def _scroll_parent(widget, event):
    """明确把数值控件上的滚轮交给最近的外层滚动区，包括隐藏滚动条时。"""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            viewport = parent.viewport()
            local = QPointF(viewport.mapFromGlobal(event.globalPos()))
            forwarded = QWheelEvent(local, event.globalPosF(), event.pixelDelta(), event.angleDelta(),
                                    event.buttons(), event.modifiers(), event.phase(), event.inverted(), event.source())
            QApplication.sendEvent(viewport, forwarded)
            event.setAccepted(forwarded.isAccepted())
            return
        parent = parent.parentWidget()
    event.ignore()


class ClickSpinBox(QSpinBox):
    """数字输入保留点击和键盘编辑；滚轮交给外层页面滚动。"""

    def wheelEvent(self, event):
        _scroll_parent(self, event)


class ClickSlider(QSlider):
    """滑杆保留点击、拖动与键盘调节，滚轮不改变当前数值。"""

    def wheelEvent(self, event):
        _scroll_parent(self, event)


class ClickComboBox(QComboBox):
    """点击展开选择，并保留 Tab、方向键等键盘操作。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # StrongFocus（键盘与点击焦点）避免仅经过滚轮就抢走输入焦点。
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event):
        # 忽略选框本体的滚轮事件，让外层设置页继续滚动。
        # 展开后的列表由独立视图处理滚轮，仍可正常滚动浏览选项。
        event.ignore()
