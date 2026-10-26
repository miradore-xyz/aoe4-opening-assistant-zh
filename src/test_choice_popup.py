"""真实主窗口中的深色下拉弹层：滚动、键盘和大字号均可完整使用。"""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QHelpEvent, QMouseEvent, QWheelEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QListView, QStyle, QToolTip, QWidget

import test_brand_name as brand_tests
from test_match_view_alignment import snapshot


class ChoicePopupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        brand_tests.BrandNameTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        brand_tests.BrandNameTests.tearDownClass()

    def setUp(self):
        self.case = brand_tests.BrandNameTests("test_product_name_and_license_credit_in_actual_widgets")
        self.case.setUp()
        self.manager = self.case.manager
        self.page = self.manager.analysis_page
        self.manager.navigate(3)
        self.page._show_snapshot(snapshot())
        for number in range(20):
            self.page._add_history(f"玩家 {number}", 10000 + number,
                                   f"玩家 {number} 的很长中文昵称，用于验证完整提示文字")
        self.manager.resize(760, 640)
        self.manager.show()
        self.case.application.sendPostedEvents(None, QEvent.DeferredDelete)
        QTest.qWait(30)

    def tearDown(self):
        self.page.history_combo.hidePopup()
        QToolTip.hideText()
        self.case.tearDown()

    def test_manager_history_has_no_native_white_scrollers(self):
        """11、14、20 像素字号均使用深色列表，弹层没有白色原生滚动箭头带。"""
        for font_size in (11, 14, 20):
            self.manager.settings["ui_font_size"] = font_size
            self.manager.apply_ui_font()
            QTest.qWait(20)
            for combo in (self.page.history_combo,):
                with self.subTest(font=font_size, control=combo.accessibleName()):
                    combo.showPopup()
                    QTest.qWait(30)
                    view = combo.view()
                    self.assertIsInstance(view, QListView)
                    self.assertEqual(combo.style().styleHint(QStyle.SH_ComboBox_Popup, None, combo), 0)
                    self.assertTrue(view.isVisible())
                    self.assertEqual(view.verticalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
                    self.assertEqual(view.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
                    # 私有滚动器类型不属于公开 Python 类，按 Qt 类名检查是否可见。
                    self.assertFalse(any(child.isVisible() for child in view.window().findChildren(QWidget)
                                         if child.metaObject().className() == "QComboBoxPrivateScroller"))
                    image = view.window().grab().toImage()
                    for y in (1, 2, image.height() - 3, image.height() - 2):
                        bright = sum(1 for x in range(6, image.width() - 6)
                                     if min(image.pixelColor(x, y).getRgb()[:3]) > 180)
                        self.assertLess(bright, max(1, image.width() // 4))
                    self.assertGreaterEqual(view.sizeHintForRow(0), combo.fontMetrics().height() + 8)
                    self.assertGreaterEqual(view.viewport().width(), view.sizeHintForColumn(0))
                    last_visible = min(combo.maxVisibleItems(), combo.count()) - 1
                    last_rect = view.visualRect(view.model().index(last_visible, 0))
                    self.assertLessEqual(last_rect.bottom(), view.viewport().rect().bottom())
                    combo.hidePopup()

    def test_open_history_scrolls_to_all_items_and_supports_keyboard_selection(self):
        """选框本体滚轮不改值，展开的二十条历史可滚动且 End、回车可选择末项。"""
        combo = self.page.history_combo
        combo.setCurrentIndex(0)
        original = combo.currentIndex()
        wheel = QWheelEvent(QPointF(8, 8), QPointF(combo.mapToGlobal(QPoint(8, 8))),
                            QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                            Qt.NoScrollPhase, False)
        QApplication.sendEvent(combo, wheel)
        self.assertEqual(combo.currentIndex(), original)
        combo.showPopup()
        QTest.qWait(30)
        view = combo.view()
        self.assertGreater(view.verticalScrollBar().maximum(), 0)
        old_scroll = view.verticalScrollBar().value()
        viewport = view.viewport()
        wheel = QWheelEvent(QPointF(8, 8), QPointF(viewport.mapToGlobal(QPoint(8, 8))),
                            QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                            Qt.NoScrollPhase, False)
        QApplication.sendEvent(viewport, wheel)
        self.assertGreater(view.verticalScrollBar().value(), old_scroll)
        # 历史已绑定编号时走_job，而非lookup；精确替身防止真实HTTP线程越过夹具寿命。
        with patch.object(self.page, "_job") as job, patch.object(self.page.client, "fetch_profile") as fetch:
            QTest.keyClick(view, Qt.Key_End)
            QTest.keyClick(view, Qt.Key_Return)
            job.assert_called_once()
            job.call_args.args[0]()
            fetch.assert_called_once_with(self.page.history[combo.currentIndex()]["profile_id"])
        self.assertEqual(combo.currentIndex(), combo.count() - 1)
        self.assertFalse(view.isVisible())

    def test_long_item_hover_has_full_text_tooltip(self):
        combo = self.page.history_combo
        combo.showPopup()
        QTest.qWait(30)
        view = combo.view()
        index = view.model().index(0, 0)
        position = view.visualRect(index).center()
        event = QHelpEvent(QEvent.ToolTip, position, view.viewport().mapToGlobal(position))
        QApplication.sendEvent(view.viewport(), event)
        self.assertEqual(QToolTip.text(), combo.itemText(0))

    def test_pointer_hover_changes_the_visible_highlight(self):
        """悬停到其他选项时明确移动高亮，菜单尚未确认前不改动选框本体。"""
        combo = self.page.history_combo
        combo.setCurrentIndex(0)
        combo.showPopup()
        QTest.qWait(30)
        view = combo.view()
        position = view.visualRect(view.model().index(1, 0)).center()
        move = QMouseEvent(QEvent.MouseMove, QPointF(position),
                           Qt.NoButton, Qt.NoButton, Qt.NoModifier)
        QApplication.sendEvent(view.viewport(), move)
        QTest.qWait(20)
        self.assertEqual(view.currentIndex().row(), 1)
        self.assertEqual(combo.currentIndex(), 0)
        image = view.viewport().grab().toImage()
        selected = view.visualRect(view.model().index(1, 0))
        plain = view.visualRect(view.model().index(0, 0))
        self.assertNotEqual(image.pixelColor(5, selected.center().y()),
                            image.pixelColor(5, plain.center().y()))


if __name__ == "__main__":
    unittest.main()
