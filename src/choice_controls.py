"""下拉选择控件：避免浏览设置时被滚轮意外改选。"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QComboBox


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
