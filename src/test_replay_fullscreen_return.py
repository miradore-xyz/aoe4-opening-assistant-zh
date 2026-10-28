"""F11只在嵌入页面和专用全屏间往返，不留下普通地图浮窗。"""
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import sip
from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from replay_page import ReplayPage, _ReplayMapDialog
from test_replay_fullscreen_state import fullscreen_report
from visual import STYLE


class ReplayFullscreenReturnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        cls.application.setStyleSheet(STYLE)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.page = ReplayPage(self.directory.name)
        self.page.resize(1100, 780)
        self.page.show()
        self.page.activateWindow()
        QTest.qWait(10)

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()
        self.directory.cleanup()

    def visible_dialogs(self):
        return [widget for widget in self.application.topLevelWidgets()
                if isinstance(widget, _ReplayMapDialog) and widget.isVisible()]

    def open_by_f11(self, *, settle=True):
        self.page.activateWindow()
        self.page.expand_map_button.setFocus()
        self.application.processEvents()
        QTest.keyClick(self.page.expand_map_button, Qt.Key_F11)
        dialog = self.page._map_dialog
        self.assertIsNotNone(dialog)
        if settle:
            self.application.processEvents()
        return dialog

    def test_fast_f11_pairs_empty_loaded_and_playing_leave_no_floating_window(self):
        for condition in ("empty", "loaded", "playing"):
            with self.subTest(condition=condition):
                if condition != "empty":
                    self.page._show_report(fullscreen_report())
                    view = self.page.map_view
                    view.view_mode.setCurrentIndex(view.view_mode.findData("records"))
                    view.player_filter.setCurrentIndex(view.player_filter.findData(202))
                    selected = next(event for event in view.events if event["player_id"] == 202)
                    view.select_event(selected)
                    view.seek(38)
                    view.speed.setCurrentIndex(view.speed.findData(4))
                    if condition == "playing":
                        view.toggle_playback()
                main = self.page.map_view
                for _ in range(2):
                    dialog = self.open_by_f11()
                    self.assertTrue(dialog.isFullScreen())
                    self.assertEqual(self.visible_dialogs(), [dialog])
                    self.assertFalse(main.timer.isActive())
                    self.assertEqual(dialog.map_view.timer.isActive(), condition == "playing")
                    expected = dialog.map_view.capture_view_state()
                    # 不插入人为等待；第二次真实按键直接交还当前状态。
                    QTest.keyClick(dialog, Qt.Key_F11)
                    self.assertIsNone(self.page._map_dialog)
                    self.assertIs(self.page.map_view, main)
                    self.assertFalse(dialog.isVisible())
                    self.assertTrue(dialog.map_view._cleaned)
                    self.assertFalse(dialog.map_view.timer.isActive())
                    self.assertFalse(dialog._restore_timer.isActive())
                    self.assertEqual(self.visible_dialogs(), [])
                    actual = main.capture_view_state()
                    for key in ("time", "speed", "player_id", "selected_event_id", "playing", "view_mode"):
                        self.assertEqual(actual[key], expected[key], key)
                    self.application.sendPostedEvents(None, QEvent.DeferredDelete)
                    self.assertTrue(sip.isdeleted(dialog), "退出后旧宿主应销毁，不能成为悬空浮窗")

    def test_second_f11_before_deferred_restore_returns_pending_state(self):
        self.page._show_report(fullscreen_report())
        main = self.page.map_view
        main.seek(38)
        expected = main.capture_view_state()
        dialog = self.open_by_f11(settle=False)
        self.assertTrue(dialog._restore_timer.isActive())
        QTest.keyClick(dialog, Qt.Key_F11)
        self.assertIsNone(self.page._map_dialog)
        self.assertFalse(dialog._restore_timer.isActive())
        self.assertEqual(main.current_time, expected["time"])
        QTest.qWait(15)
        self.assertEqual(self.visible_dialogs(), [])

    def test_auto_repeated_f11_and_escape_are_ignored(self):
        def repeat(target, key):
            for kind in (QEvent.KeyPress, QEvent.KeyRelease):
                self.application.sendEvent(target, QKeyEvent(kind, key, Qt.NoModifier, "", True, 3))
        self.assertFalse(self.page._fullscreen_shortcut.autoRepeat())
        repeat(self.page.expand_map_button, Qt.Key_F11)
        self.assertIsNone(self.page._map_dialog)
        dialog = self.open_by_f11()
        self.assertFalse(dialog._fullscreen_shortcut.autoRepeat())
        self.assertFalse(dialog._return_shortcut.autoRepeat())
        for key in (Qt.Key_F11, Qt.Key_Escape):
            repeat(dialog, key)
            self.assertIs(self.page._map_dialog, dialog)
            self.assertTrue(dialog.isFullScreen())
        QTest.keyClick(dialog, Qt.Key_Escape)
        self.assertIsNone(self.page._map_dialog)
        repeat(self.page.expand_map_button, Qt.Key_F11)
        self.assertIsNone(self.page._map_dialog)

    def test_no_initial_caption_row_but_loading_and_error_states_still_show(self):
        label = self.page.analysis_placeholder
        self.assertEqual(label.text(), "")
        self.assertTrue(label.isHidden())
        self.assertTrue(self.page.analysis_card.layout().itemAt(0).isEmpty())
        self.page._preview_public_context(101, {"map": "King of the Hill"})
        self.assertIn("正在读取", label.text())
        self.assertFalse(label.isHidden())
        self.page._job_error("本场公开回放暂不可用。")
        self.assertIn("暂不可用", label.text())
        self.assertFalse(label.isHidden())
        expected = self.page.map_view.capture_view_state()
        dialog = self.open_by_f11()
        self.assertFalse(dialog.testAttribute(Qt.WA_TranslucentBackground))
        self.assertFalse(dialog.testAttribute(Qt.WA_NoSystemBackground))
        self.assertIsNone(dialog.map_view.official_map)
        canvas = dialog.map_view.canvas
        self.assertIs(dialog.map_view.map_stack.currentWidget(), canvas)
        self.assertEqual(canvas.backgroundBrush().color().alpha(), 255)
        self.assertAlmostEqual(canvas.transform().m11(), canvas.transform().m22())
        QTest.keyClick(dialog, Qt.Key_F11)
        self.assertIsNone(self.page._map_dialog)
        returned = self.page.map_view.capture_view_state()
        self.assertEqual(returned['time'], expected['time'])
        self.assertAlmostEqual(returned['camera']['zoom'], expected['camera']['zoom'])


if __name__ == "__main__":
    unittest.main()
