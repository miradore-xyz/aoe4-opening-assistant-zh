"""开发中占位组件：在可用空间中央显示同一条中性灰色文字。"""
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtWidgets import QLabel, QSizePolicy


class DevelopmentPlaceholder(QLabel):
    """DevelopmentPlaceholder（开发中占位）：只显示状态，不提供尚未实现的操作。"""

    def __init__(self, parent=None):
        super().__init__("开发中", parent)
        self.setObjectName("DevelopmentPlaceholder")
        self.setAccessibleName("开发中")
        self.setAlignment(Qt.AlignCenter)
        self.setTextFormat(Qt.PlainText)
        self.setStyleSheet("font-size:20px;font-weight:400;color:#929299;background:transparent;border:0;")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def sizeHint(self):
        """仅提供首选尺寸，不设置最小高度，以免空页面撑大主窗口。"""
        return QSize(240, 220)

