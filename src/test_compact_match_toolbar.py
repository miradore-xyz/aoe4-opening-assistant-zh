"""查询工具栏的真实 Qt 几何验证：图标操作并排、输入底边与操作空间。"""
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from analysis_page import AnalysisPage
from test_match_view_alignment import snapshot
from visual import STYLE


class CompactMatchToolbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        for path in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"):
            QFontDatabase.addApplicationFont(path)
        cls.application.setStyleSheet(STYLE)

    def test_icon_toolbar_fits_supported_window_widths_without_clipping(self):
        """常用宽度保持一行；文字和实际输入面积均完整保留。"""
        with tempfile.TemporaryDirectory() as folder:
            page = AnalysisPage(folder)
            page._add_history("Miradore", 5497834, "Miradore")
            page._show_snapshot(snapshot())
            page.show()
            try:
                for width in (760, 1100, 1500):
                    with self.subTest(width=width):
                        page.resize(width, 640)
                        QTest.qWait(30)
                        fields = (page.query, page.query_button, page.refresh_button,
                                  page.history_combo, page.delete_history)
                        for control in fields:
                            position = control.mapTo(page, QPoint())
                            self.assertGreaterEqual(position.x(), 0)
                            self.assertLessEqual(position.x() + control.width(), page.width())
                            self.assertGreaterEqual(control.height(), control.fontMetrics().height() + 6)
                            self.assertLessEqual(control.height(), 36)
                        expected_single_row = True
                        self.assertEqual(page.query_toolbar.property("singleRow"), expected_single_row)
                        self.assertLessEqual(page.query_toolbar.height(), 30 if expected_single_row else 62)
                        self.assertEqual(page.query.mapTo(page, QPoint()).y() ==
                                         page.history_combo.mapTo(page, QPoint()).y(), expected_single_row)
                        self.assertEqual(page.history_combo.width(), page.history_combo.height())
                        # 验证实际控件边界的统一节奏，不依赖嵌套布局内部如何实现。
                        ordered = (*fields, page.scope_label)
                        positions = [control.mapTo(page, QPoint()) for control in ordered]
                        self.assertEqual(len({position.y() for position in positions}), 1)
                        gaps = [positions[index + 1].x() - positions[index].x() - ordered[index].width()
                                for index in range(len(ordered) - 1)]
                        self.assertEqual(gaps, [8, 12, 8, 8, 8])
                        self.assertEqual(len({control.height() for control in ordered}), 1)
                        self.assertEqual(page.delete_history.text(), "")
                        self.assertFalse(page.delete_history.icon().isNull())
                        QTest.mouseClick(page.history_combo, Qt.LeftButton, pos=page.history_combo.rect().center())
                        self.assertTrue(page.history_combo.view().isVisible())
                        QTest.keyClick(page.history_combo.view(), Qt.Key_Escape)
                        self.assertFalse(page.history_combo.view().isVisible())
                        self.assertFalse(hasattr(page, "mode"), "模式筛选入口已移除，不能留隐藏限制")
                        self.assertEqual(page.scope_label.mapTo(page, QPoint()).y(),
                                         page.history_combo.mapTo(page, QPoint()).y())
                        self.assertFalse(page.scope_label.wordWrap())
                        self.assertEqual(page.query_button.text(), "")
                        self.assertEqual(page.query_button.accessibleName(), "查询玩家")
                        page._set_busy(True)
                        self.assertEqual(page.query_button.text(), "")
                        page._set_busy(False)
                        self.assertGreaterEqual(page.scope_label.width(),
                                                page.scope_label.fontMetrics().horizontalAdvance(page.scope_label.text()))
                        self.assertIn("非赛季累计", page.scope_label.toolTip())
                        self.assertEqual(page.scroll.verticalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
                        self.assertEqual(page.scroll.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
            finally:
                page.close()
                page.deleteLater()
                # 真实弹层由Qt单独管理；本夹具结束前完成所属页面及弹层的延迟销毁。
                self.application.sendPostedEvents(None, QEvent.DeferredDelete)
                self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
