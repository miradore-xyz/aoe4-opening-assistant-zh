"""软件内置得意黑：中文、英文字母与数字使用同一套字形。

字体只登记到当前进程，不修改Windows（微软桌面系统）的系统字体目录。
"""
from pathlib import Path
import sys

from PyQt5.QtGui import QFont, QFontDatabase, QGuiApplication


FONT_FAMILY = 'Smiley Sans'
FONT_FAMILIES = (FONT_FAMILY,)
FONT_CSS = "'Smiley Sans'"
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
    path = root / '得意黑' / 'SmileySans-Oblique.ttf'
    identity = QFontDatabase.addApplicationFont(str(path))
    if identity >= 0:
        registered.append(str(path))
    _registered = tuple(registered)
    return _registered


def make_ui_font(size=14, weight=QFont.Normal):
    """全界面共用得意黑；官方只有一个字重，加粗由Qt合成，不伪造另一字重文件。"""
    register_ui_fonts()
    font = QFont()
    font.setFamilies(list(FONT_FAMILIES))
    font.setPixelSize(size)
    font.setWeight(weight)
    return font
