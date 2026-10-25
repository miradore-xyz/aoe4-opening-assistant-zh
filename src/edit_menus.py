"""输入控件的中文标准编辑菜单，不依赖外部 Qt 翻译文件。"""

from PyQt5.QtCore import QTranslator


class ChineseEditMenuTranslator(QTranslator):
    """只翻译 Qt 标准编辑菜单，保留原有动作、快捷键、启禁与只读行为。"""

    _EDIT_CONTEXTS = frozenset(('QLineEdit', 'QWidgetTextControl'))
    _EDIT_CAPTIONS = {
        '&Undo': '撤销(&U)',
        '&Redo': '重做(&R)',
        'Cu&t': '剪切(&T)',
        '&Copy': '复制(&C)',
        '&Paste': '粘贴(&P)',
        'Delete': '删除(&D)',
        'Select All': '全选(&A)',
        'Copy &Link Location': '复制链接地址(&L)',
    }
    _SPIN_CAPTIONS = {
        '&Step up': '增加(&U)',
        'Step &up': '增加(&U)',
        '&Step down': '减少(&D)',
        'Step &down': '减少(&D)',
        '&Select All': '全选(&A)',
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
    return translator
