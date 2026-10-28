"""输入控件的中文标准编辑菜单，不依赖外部 Qt 翻译文件。"""

import re

from app_fonts import make_ui_font, register_ui_fonts
from PyQt5.QtCore import QEvent, QObject, QTranslator
from PyQt5.QtWidgets import QMenu


MENU_STYLE = """
QMenu {background:#1c1c20;color:#ededf0;border:1px solid #42424a;
       border-radius:8px;padding:6px;font-family:'Smiley Sans';font-size:13px;}
QMenu::item {background:transparent;border:1px solid transparent;border-radius:5px;
             min-height:24px;padding:5px 18px 5px 12px;}
QMenu::item:selected {background:#34343C;border:1px solid #64646E;color:#ffffff;}
QMenu::item:pressed {background:#42424A;color:#ffffff;}
QMenu::item:disabled {color:#74747e;background:transparent;border-color:transparent;}
QMenu::separator {height:1px;background:#38383f;margin:5px 8px;}
"""


def style_edit_menu(menu):
    """菜单使用清晰悬停与键盘选中状态；仅去掉文本中的英文助记字母。"""
    menu.setStyleSheet(MENU_STYLE)
    for action in menu.actions():
        action.setText(re.sub(r"[（(]&?[A-Za-z][）)]", "", action.text()))
    return menu


class _MenuAppearance(QObject):
    """在显示时统一标准右键菜单，不拦截动作、快捷键或鼠标事件。"""
    def eventFilter(self, watched, event):
        if isinstance(watched, QMenu) and event.type() == QEvent.Show:
            style_edit_menu(watched)
        return False


class ChineseEditMenuTranslator(QTranslator):
    """只翻译 Qt 标准编辑菜单，保留原有动作、快捷键、启禁与只读行为。"""

    _EDIT_CONTEXTS = frozenset(('QLineEdit', 'QWidgetTextControl'))
    _EDIT_CAPTIONS = {
        '&Undo': '撤销',
        '&Redo': '重做',
        'Cu&t': '剪切',
        '&Copy': '复制',
        '&Paste': '粘贴',
        'Delete': '删除',
        'Select All': '全选',
        'Copy &Link Location': '复制链接地址',
    }
    _SPIN_CAPTIONS = {
        '&Step up': '增加',
        'Step &up': '增加',
        '&Step down': '减少',
        'Step &down': '减少',
        '&Select All': '全选',
    }

    def isEmpty(self):
        # 此翻译器的词条写在程序中；向 Qt 声明非空，才能注册到应用。
        return False

    def translate(self, context, source_text, disambiguation=None, n=-1):
        if context in self._EDIT_CONTEXTS:
            return self._EDIT_CAPTIONS.get(source_text)
        if context == 'QAbstractSpinBox':
            return self._SPIN_CAPTIONS.get(source_text)
        # None 对应 Qt 的空值字符串，让未匹配的文本继续查找其他翻译或保留原文。
        # 不可返回 ''，否则会把快捷键里的 Ctrl、普通按钮等无关文本替换为空白。
        return None


def install_chinese_edit_menus(application):
    """在 QApplication 建立后安装；重复调用复用同一个翻译器。"""
    translator = getattr(application, '_chinese_edit_menu_translator', None)
    if translator is None:
        translator = ChineseEditMenuTranslator(application)
        application.installTranslator(translator)
        # 保存 Python 引用，确保翻译器与应用同时存活；用户自定义菜单无需修改。
        application._chinese_edit_menu_translator = translator
    appearance = getattr(application, '_chinese_edit_menu_appearance', None)
    if appearance is None:
        appearance = _MenuAppearance(application)
        application.installEventFilter(appearance)
        application._chinese_edit_menu_appearance = appearance
    return translator
