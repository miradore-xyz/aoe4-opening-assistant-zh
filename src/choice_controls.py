"""下拉选择控件：避免浏览设置时被滚轮意外改选。"""

from PyQt5.QtCore import QEvent, Qt, QPointF
from PyQt5.QtGui import QPalette, QColor, QWheelEvent
from PyQt5.QtWidgets import (
    QApplication, QAbstractItemView, QAbstractScrollArea, QComboBox, QListView,
    QSlider, QSpinBox, QToolTip,
)


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


class _ChoiceListView(QListView):
    """列表弹层保留原始完整文字，较长项目通过悬停提示阅读。"""

    def viewportEvent(self, event):
        if event.type() == QEvent.ToolTip:
            index = self.indexAt(event.pos())
            if index.isValid():
                text = index.data(Qt.ToolTipRole) or index.data(Qt.DisplayRole)
                if text:
                    QToolTip.showText(event.globalPos(), str(text), self.viewport(),
                                      self.visualRect(index))
                    return True
            QToolTip.hideText()
        return super().viewportEvent(event)


class ClickComboBox(QComboBox):
    """点击展开选择，并保留 Tab、方向键等键盘操作。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # StrongFocus（键盘与点击焦点）避免仅经过滚轮就抢走输入焦点。
        self.setFocusPolicy(Qt.StrongFocus)
        # 使用常规列表弹层，避免系统菜单风格插入白色的上下滚动箭头区域。
        # combobox-popup 是 Qt 样式提示；仅改变弹层模式，不覆盖本体的页面样式。
        self.setStyleSheet("QComboBox {combobox-popup:0;}")
        view = _ChoiceListView(self)
        view.setObjectName("ChoicePopupList")
        view.setSelectionMode(QAbstractItemView.SingleSelection)
        view.setSelectionBehavior(QAbstractItemView.SelectRows)
        view.setUniformItemSizes(True)
        view.setMouseTracking(True)
        view.setTextElideMode(Qt.ElideRight)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view.setVerticalScrollMode(QAbstractItemView.ScrollPerItem)
        view.setStyleSheet("""
            QListView#ChoicePopupList {
                background:#202023; color:#f4f4f5; border:0; outline:0;
                padding:3px; selection-background-color:#3A3A42;
                selection-color:#f4f4f5;
            }
            QListView#ChoicePopupList::item {
                padding:5px 9px; border:0; border-radius:4px;
            }
            QListView#ChoicePopupList::item:hover {background:#2c2c32;}
            QListView#ChoicePopupList::item:selected {background:#3A3A42; color:#f4f4f5;}
            QListView#ChoicePopupList::item:disabled {color:#777780;}
        """)
        palette = view.palette()
        for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
            palette.setColor(group, QPalette.Base, QColor("#202023"))
            palette.setColor(group, QPalette.Window, QColor("#202023"))
            palette.setColor(group, QPalette.Highlight, QColor("#3A3A42"))
            palette.setColor(group, QPalette.HighlightedText, QColor("#f4f4f5"))
        view.setPalette(palette)
        view.viewport().setPalette(palette)
        view.viewport().setAutoFillBackground(True)
        self.setView(view)
        self.setMaxVisibleItems(9)

    def showPopup(self):
        # 弹层容器独立于页面背景；在显示前明确其边框和底色，避免继承透明背景。
        container = self.view().parentWidget()
        container.setObjectName("ChoicePopupFrame")
        container.setStyleSheet("QFrame#ChoicePopupFrame {background:#202023;"
                               "border:1px solid #4c4c55;border-radius:6px;}")
        # 列表可以比收起后的紧凑选择框宽；长昵称超过屏幕宽度时仍可悬停阅读。
        metrics = self.fontMetrics()
        text_width = max((metrics.horizontalAdvance(self.itemText(index))
                          for index in range(self.count())), default=0)
        screen_width = self.screen().availableGeometry().width()
        container.setMinimumWidth(min(max(self.width(), text_width + 40),
                                      max(1, screen_width - 16)))
        super().showPopup()
        # Qt 在弹层布局中仍可能保留菜单上下的空白占位；去掉后每一行完整显示。
        layout = container.layout()
        if layout is not None:
            for index in range(layout.count()):
                spacer = layout.itemAt(index).spacerItem()
                if spacer is not None:
                    spacer.changeSize(0, 0)
            layout.invalidate()
            layout.activate()

    def wheelEvent(self, event):
        # 忽略选框本体的滚轮事件，让外层设置页继续滚动。
        # 展开后的列表由独立视图处理滚轮，仍可正常滚动浏览选项。
        event.ignore()
