"""自建窗口的Qt输入回归：标题拖动只移动，边缘缩放和最大化仍可用。"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt, QEvent, QPoint, QPointF
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication, QVBoxLayout

import test_brand_name as manager_fixture
from windowing import FramelessWindow, WindowBar


def send_mouse(widget, kind, local, global_position, button, buttons):
    """只向本测试创建的控件发事件，不移动系统鼠标或操作其他窗口。"""
    event = QMouseEvent(kind, QPointF(local), QPointF(global_position),
                        button, buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, event)


def drag_title(bar, delta):
    """一次完整的按下、拖动、松开，以同一初始全局坐标计算。"""
    local = QPoint(20, 26)
    origin = bar.mapToGlobal(local)
    send_mouse(bar, QEvent.MouseButtonPress, local, origin, Qt.LeftButton, Qt.LeftButton)
    send_mouse(bar, QEvent.MouseMove, local + delta, origin + delta, Qt.NoButton, Qt.LeftButton)
    send_mouse(bar, QEvent.MouseButtonRelease, local + delta, origin + delta,
               Qt.LeftButton, Qt.NoButton)
    QApplication.processEvents()


class WindowDragManagerTests(unittest.TestCase):
    """使用隔离数据目录的真实Manager，复用已有网络/语音替身与销毁流程。"""

    @classmethod
    def setUpClass(cls):
        manager_fixture.BrandNameTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        manager_fixture.BrandNameTests.tearDownClass.__func__(cls)

    def setUp(self):
        manager_fixture.BrandNameTests.setUp(self)

    def tearDown(self):
        manager_fixture.BrandNameTests.tearDown(self)

    def test_repeated_title_drag_keeps_actual_manager_size_on_three_pages(self):
        """个人、在线开局库、设置页连拖，尺寸及布局最小值均不累加。"""
        window = self.manager
        window.show()
        handle_type = type(window.windowHandle())
        with patch("windowing.sys.platform", "win32"), \
                patch.object(handle_type, "startSystemMove", return_value=True) as native_move:
            for width, height in ((1000, 680), (760, 520)):
                window.resize(width, height)
                self.application.processEvents()
                for page in (5, 6, 1):
                    window.navigate(page)
                    self.application.processEvents()
                    size, minimum = window.size(), window.minimumSize()
                    for delta in (QPoint(20, 12), QPoint(-13, -17), QPoint(9, 6)) * 3:
                        position = window.pos()
                        drag_title(window.window_bar, delta)
                        self.assertEqual(window.pos(), position + delta)
                        self.assertEqual(window.size(), size)
                        self.assertEqual(window.minimumSize(), minimum)
                        self.assertIsNone(window.window_bar._drag_state)
            native_move.assert_not_called()

    def test_edge_resize_after_drag_still_changes_size_and_respects_minimum(self):
        """标题连拖不固定窗口大小，真实边缘鼠标路径仍能拉伸与收缩。"""
        window = self.manager
        window.show()
        self.application.processEvents()
        with patch("windowing.sys.platform", "win32"), \
                patch.object(window, "_try_system_resize", return_value=False):
            drag_title(window.window_bar, QPoint(20, 12))
            initial = window.size()
            local = QPoint(window.width() - 2, window.height() - 2)
            origin = window.mapToGlobal(local)
            send_mouse(window, QEvent.MouseButtonPress, local, origin, Qt.LeftButton, Qt.LeftButton)
            self.assertIsNotNone(window._resize_state)
            delta = QPoint(70, 50)
            send_mouse(window, QEvent.MouseMove, local + delta, origin + delta, Qt.NoButton, Qt.LeftButton)
            send_mouse(window, QEvent.MouseButtonRelease, local + delta, origin + delta,
                       Qt.LeftButton, Qt.NoButton)
            self.application.processEvents()
            self.assertEqual(window.width(), initial.width() + 70)
            self.assertEqual(window.height(), initial.height() + 50)
            self.assertIsNone(window._resize_state)
            window.resize(760, 520)
            self.application.processEvents()
            self.assertEqual(window.size(), window.minimumSize())

    def test_maximize_restore_and_title_drag_restore_keep_normal_size(self):
        """按钮还原和从最大化拖回均保留普通窗口尺寸，之后拖动不长高。"""
        window = self.manager
        window.show()
        self.application.processEvents()
        normal = window.size()
        bar = window.window_bar
        bar.toggle_maximized()
        self.application.processEvents()
        self.assertTrue(window.isMaximized())
        bar.toggle_maximized()
        self.application.processEvents()
        self.assertFalse(window.isMaximized())
        self.assertEqual(window.size(), normal)
        bar.toggle_maximized()
        self.application.processEvents()
        with patch("windowing.sys.platform", "win32"):
            drag_title(bar, QPoint(40, 20))
            self.assertFalse(window.isMaximized())
            self.assertEqual(window.size(), normal)
            for _ in range(3):
                drag_title(bar, QPoint(10, 10))
                self.assertEqual(window.size(), normal)


class WindowSystemMoveBoundaryTests(unittest.TestCase):
    """修正范围严格限于Windows透明无边框窗体。"""

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_non_windows_and_non_translucent_windows_keep_system_move(self):
        for platform, translucent in (("linux", True), ("win32", False)):
            with self.subTest(platform=platform, translucent=translucent):
                window = FramelessWindow()
                window.setAttribute(Qt.WA_TranslucentBackground, translucent)
                window.resize(860, 560)
                layout = QVBoxLayout(window)
                bar = WindowBar(window)
                layout.addWidget(bar)
                layout.addStretch()
                window.show()
                self.application.processEvents()
                try:
                    with patch("windowing.sys.platform", platform), \
                            patch.object(type(window.windowHandle()), "startSystemMove", return_value=True) as native:
                        point = QPoint(20, 26)
                        send_mouse(bar, QEvent.MouseButtonPress, point, bar.mapToGlobal(point),
                                   Qt.LeftButton, Qt.LeftButton)
                        native.assert_called_once_with()
                        self.assertIsNone(bar._drag_state)
                finally:
                    window.close()
                    window.deleteLater()
                    self.application.sendPostedEvents(None, QEvent.DeferredDelete)
                    self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
