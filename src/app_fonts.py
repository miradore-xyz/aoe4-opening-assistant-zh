"""软件内置字体：Sen承接拉丁字母与数字，思源黑体承接中文。

字体只登记到当前进程，不修改Windows（微软桌面系统）的系统字体目录。
"""
from pathlib import Path
import sys

from PyQt5.QtGui import QFont, QFontDatabase, QGuiApplication


FONT_FAMILIES = ('Sen', 'Source Han Sans SC', 'Microsoft YaHei UI')
FONT_CSS = "'Sen','Source Han Sans SC','Microsoft YaHei UI'"
_registered = None


def font_root():
    """源码和独立程序都读取随附的字体，不依赖用户预先安装。"""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / 'assets/design/字体'
    return Path(__file__).resolve().parent / '界面素材/字体'


def register_ui_fonts():
    """在图形应用创建后登记一次；返回成功登记的字体文件，便于实际验收。"""
    global _registered
    if _registered is not None:
        return _registered
    if QGuiApplication.instance() is None:
        return ()
    root = font_root()
    registered = []
    for subfolder, names in (
        ('Sen', ('Sen-Regular.ttf', 'Sen-Medium.ttf', 'Sen-SemiBold.ttf', 'Sen-Bold.ttf')),
        ('思源黑体', ('SourceHanSansSC-Regular.otf', 'SourceHanSansSC-Medium.otf', 'SourceHanSansSC-Bold.otf')),
    ):
        for name in names:
            path = root / subfolder / name
            identity = QFontDatabase.addApplicationFont(str(path))
            if identity >= 0:
                registered.append(str(path))
    QFont.insertSubstitutions('Sen', list(FONT_FAMILIES[1:]))
    _registered = tuple(registered)
    return _registered


def make_ui_font(size=14, weight=QFont.Normal):
    """明确的中英文字体链；字重由随附的真实字体文件匹配。"""
    register_ui_fonts()
    font = QFont()
    font.setFamilies(list(FONT_FAMILIES))
    font.setPixelSize(size)
    font.setWeight(weight)
    return font
