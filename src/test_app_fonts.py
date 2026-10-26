"""验证字体真实装载与中英文字形来源，避免只设置名称却继续使用系统替代字体。"""
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtGui import QFont, QFontInfo, QRawFont, QTextLayout
from PyQt5.QtWidgets import QApplication

from app_fonts import font_root, make_ui_font, register_ui_fonts


class AppFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def test_packaged_sen_regular_and_bold_load_without_system_installation(self):
        self.assertEqual(len(register_ui_fonts()), 7)
        regular, bold = QFontInfo(make_ui_font(14)), QFontInfo(make_ui_font(14, QFont.Bold))
        self.assertEqual(regular.family(), 'Sen')
        self.assertEqual(bold.family(), 'Sen')
        # Windows插件的styleName（样式名称）可能为空；核对实际字重与随附字形表，避免合成加粗。
        for weight, filename in ((QFont.Normal, 'Sen-Regular.ttf'), (QFont.Bold, 'Sen-Bold.ttf')):
            actual = QRawFont.fromFont(make_ui_font(14, weight))
            expected = QRawFont(str(font_root() / 'Sen' / filename), 14)
            self.assertTrue(actual.isValid())
            self.assertEqual(actual.weight(), weight)
            for table in ('OS/2', 'glyf', 'hmtx'):
                data = bytes(expected.fontTable(table))
                self.assertTrue(data, table)
                self.assertEqual(bytes(actual.fontTable(table)), data, (filename, table))

    def test_chinese_uses_bundled_source_han_with_real_glyphs(self):
        layout = QTextLayout('玩家查询对局分析开局库设置 Recent Games 1234', make_ui_font(14))
        layout.beginLayout()
        line = layout.createLine()
        line.setLineWidth(1000)
        layout.endLayout()
        runs = layout.glyphRuns()
        # Windows按系统语言返回家族名；中文别名仍须匹配随附思源黑体的真实字体表。
        chinese = [run.rawFont() for run in runs
                   if run.rawFont().familyName() in {'Source Han Sans SC', '思源黑体'}]
        self.assertTrue(chinese)
        expected = QRawFont(str(font_root() / '思源黑体' / 'SourceHanSansSC-Regular.otf'), 14)
        for actual in chinese:
            for table in ('OS/2', 'cmap', 'hmtx'):
                data = bytes(expected.fontTable(table))
                self.assertTrue(data, table)
                self.assertEqual(bytes(actual.fontTable(table)), data, table)
        self.assertTrue(any(run.rawFont().familyName().startswith('Sen') for run in runs))
        self.assertTrue(all(index != 0 for run in runs for index in run.glyphIndexes()))


if __name__ == '__main__':
    unittest.main()
