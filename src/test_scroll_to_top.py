"""实际滚动区域的返回顶部交互：位置、显隐、点击与键盘，不访问网络。"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtWidgets import QApplication, QScrollArea, QWidget
from PyQt5.QtTest import QTest
from scroll_to_top import install_scroll_to_top


class ScrollToTopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.scroll = QScrollArea()
        self.scroll.resize(420, 280)
        content = QWidget()
        content.resize(380, 1600)
        self.scroll.setWidget(content)
        self.button = install_scroll_to_top(self.scroll)
        self.scroll.show()
        self.application.processEvents()

    def tearDown(self):
        self.scroll.close()
        self.scroll.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_only_scrolled_content_shows_fixed_corner_button_and_click_returns_top(self):
        bar = self.scroll.verticalScrollBar()
        self.assertTrue(self.button.isHidden())
        bar.setValue(600)
        self.application.processEvents()
        self.assertFalse(self.button.isHidden())
        position = self.button.pos()
        bar.setValue(900)
        self.assertEqual(self.button.pos(), position)
        self.assertEqual(self.button.parentWidget(), self.scroll.viewport())
        QTest.mouseClick(self.button, Qt.LeftButton)
        self.assertEqual(bar.value(), 0)
        self.assertTrue(self.button.isHidden())

    def test_resize_and_space_activation_keep_button_inside_viewport(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(600)
        self.scroll.resize(320, 220)
        self.application.processEvents()
        self.assertLessEqual(self.button.geometry().right(), self.scroll.viewport().width() - 18)
        self.assertLessEqual(self.button.geometry().bottom(), self.scroll.viewport().height() - 18)
        self.button.setFocus()
        QTest.keyClick(self.button, Qt.Key_Space)
        self.assertEqual(bar.value(), 0)
        self.assertEqual(self.button.accessibleName(), '返回顶部')

    def test_content_without_overflow_never_shows_button(self):
        self.scroll.widget().resize(200, 80)
        self.application.processEvents()
        self.assertEqual(self.scroll.verticalScrollBar().maximum(), 0)
        self.assertTrue(self.button.isHidden())


if __name__ == '__main__':
    unittest.main()
