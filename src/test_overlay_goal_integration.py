"""中央目标和右上正文同步，但不重复、不截获游戏点击。"""
import os
import unittest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from test_overlay_controls import Panel


class OverlayGoalIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.panel = Panel()
        self.overlay = self.panel.overlay
        self.overlay.render()
        self.overlay.show()
        QTest.qWait(20)

    def tearDown(self):
        self.overlay.resize_save.stop()
        self.overlay.lock_handle.cleanup()
        self.overlay.goals.cleanup()
        self.overlay.hide()
        self.overlay.deleteLater()
        self.panel.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_current_target_updates_in_center_and_is_absent_from_text(self):
        goals = self.overlay.goals
        self.assertTrue(goals.isVisible())
        self.assertEqual(goals.values["food"], 6)
        self.assertTrue(goals.windowFlags() & Qt.WindowTransparentForInput)
        self.assertTrue(goals.windowFlags() & Qt.WindowDoesNotAcceptFocus)
        self.panel.ids = [2]
        self.overlay.render()
        self.assertEqual(goals.values["food"], 10)
        self.assertEqual(goals.values["gold"], 2)
        self.assertEqual(goals.values["age"], 2)
        self.assertNotIn("data-stage-resources", self.overlay.notes.text())
        area = QApplication.screenAt(self.overlay.frameGeometry().center()).availableGeometry()
        self.assertLessEqual(abs(goals.geometry().center().x() - area.center().x()), 1)
        self.assertTrue(area.contains(goals.geometry()))

    def test_hiding_and_restoring_text_panel_controls_goal_visibility(self):
        self.overlay.hide()
        self.assertTrue(self.overlay.goals.isHidden())
        self.overlay.show()
        QTest.qWait(10)
        self.assertTrue(self.overlay.goals.isVisible())
        self.overlay.goals.cleanup()
        self.overlay.render()
        self.assertFalse(self.overlay.goals.isVisible())


if __name__ == "__main__":
    unittest.main()
