"""验证内置得意黑真实字形：中文、英文与数字不能悄悄换回其它字体。"""
import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtGui import QFont, QFontInfo, QRawFont, QTextLayout
from PyQt5.QtWidgets import QApplication
from app_fonts import FONT_FAMILY, font_root, make_ui_font, register_ui_fonts


class AppFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def test_single_official_font_loads_without_system_installation(self):
        self.assertEqual(len(register_ui_fonts()), 1)
        self.assertEqual(QFontInfo(make_ui_font(14)).family(), FONT_FAMILY)
        self.assertEqual({p.name for p in font_root().iterdir() if p.is_dir()}, {'得意黑'})
        expected = QRawFont(str(font_root() / '得意黑' / 'SmileySans-Oblique.ttf'), 14)
        self.assertTrue(expected.isValid())
        # 官方只有400字重；粗体可由Qt合成，但底层字形表必须仍为同一份原字体。
        for weight in (QFont.Normal, QFont.Bold):
            actual = QRawFont.fromFont(make_ui_font(14, weight))
            self.assertTrue(actual.isValid())
            for table in ('OS/2', 'glyf', 'hmtx'):
                self.assertEqual(bytes(actual.fontTable(table)), bytes(expected.fontTable(table)))

    def test_chinese_latin_and_digits_all_use_the_same_bundled_glyphs(self):
        content = '玩家查询对局分析开局库设置 排名未提供 无段位 每分钟操作次数 Recent Games ELO APM 0123456789 −×→↗'
        layout = QTextLayout(content, make_ui_font(14))
        layout.beginLayout()
        layout.createLine().setLineWidth(1600)
        layout.endLayout()
        runs = layout.glyphRuns()
        self.assertTrue(runs)
        expected = QRawFont(str(font_root() / '得意黑' / 'SmileySans-Oblique.ttf'), 14)
        for run in runs:
            self.assertTrue(all(index != 0 for index in run.glyphIndexes()))
            # Windows可能报告Helvetica别名；逐表核对才能确定实际字形来源。
            for table in ('OS/2', 'cmap', 'glyf', 'hmtx'):
                self.assertEqual(bytes(run.rawFont().fontTable(table)), bytes(expected.fontTable(table)))


if __name__ == '__main__':
    unittest.main()
