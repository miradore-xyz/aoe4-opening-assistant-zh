"""验证字体真实装载与中英文字形来源，避免只设置名称却继续使用系统替代字体。"""
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtGui import QFont, QFontInfo, QTextLayout
from PyQt5.QtWidgets import QApplication

from app_fonts import make_ui_font, register_ui_fonts


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
        self.assertEqual(regular.styleName(), 'Regular')
        self.assertEqual(bold.styleName(), 'Bold')

    def test_chinese_uses_bundled_source_han_with_real_glyphs(self):
        layout = QTextLayout('玩家查询对局分析开局库设置 Recent Games 1234', make_ui_font(14))
        layout.beginLayout()
        line = layout.createLine()
        line.setLineWidth(1000)
        layout.endLayout()
        runs = layout.glyphRuns()
        self.assertTrue(any(run.rawFont().familyName() == 'Source Han Sans SC' for run in runs))
        self.assertTrue(any(run.rawFont().familyName().startswith('Sen') for run in runs))
        self.assertTrue(all(index != 0 for run in runs for index in run.glyphIndexes()))


if __name__ == '__main__':
    unittest.main()
