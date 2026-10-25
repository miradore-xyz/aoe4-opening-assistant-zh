"""悬浮窗关闭按钮、最窄标题布局及条件提示分组的离屏验证。"""
from __future__ import annotations

import copy
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import unittest
from unittest.mock import patch

from PyQt5.QtCore import Qt, QPoint, QEvent
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtTest import QTest, QSignalSpy
from PyQt5.QtWidgets import QApplication, QWidget
from app import ASSETS, Overlay, STYLE, spin
from core import Clock, DEFAULTS, NoteRenderer


class Panel(QWidget):
    """保留真实计时和文本渲染的隔离面板，不注册热键、不启动声音或联网。"""

    def __init__(self):
        super().__init__()
        self.settings = copy.deepcopy(DEFAULTS)
        self.settings.update(width=280, height=220, speech=False, reminders=False, auto=True)
        self.clock = Clock()
        self.ids = [0]
        self.order = {
            "name": "英格兰 · 长弓压制转双城镇中心的较长标题用于窄窗测试", "civilization": "English",
            "build_order": [
                {"population_count": -1, "villager_count": 6, "age": 1, "time": "0:00",
                 "resources": {"food": 6, "wood": -1, "gold": -1, "stone": -1}, "notes": ["持续造村民。"]},
                {"population_count": -1, "villager_count": -1, "age": -1, "time": "",
                 "resources": {"food": -1, "wood": -1, "gold": -1, "stone": -1}, "notes": ["达到条件后再分配村民。"]},
                {"population_count": -1, "villager_count": 15, "age": 2, "time": "4:00",
                 "resources": {"food": 10, "wood": 3, "gold": 2, "stone": -1}, "notes": ["训练长弓手。"]},
            ],
        }
        self.renderer = NoteRenderer(ASSETS)
        self.step_alert = ""
        self.reminder_text = ""
        self.voice_stops = 0
        self.display_updates = 0
        self.width_input = spin(280, 900, 280)
        self.height_input = spin(160, 1400, 220)
        self.overlay = Overlay(self)

    def step(self, change):
        self.ids = [max(0, min(2, self.ids[-1] + change))]

    def toggle_clock(self):
        if self.clock.running:
            self.clock.pause()
        else:
            self.clock.start()

    def voice_ready(self):
        return True

    def stop_voice(self):
        self.voice_stops += 1

    def update_display(self):
        self.display_updates += 1
        self.overlay.render()

    def save_settings(self):
        pass

    def position_fields(self):
        pass


class OverlayControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            path = Path("C:/Windows/Fonts") / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))

    def setUp(self):
        self.application.setStyleSheet(STYLE)
        self.panel = Panel()
        self.panel.show()
        self.overlay = self.panel.overlay
        self.overlay.show()
        self.application.processEvents()
        self.overlay.render()

    def tearDown(self):
        self.overlay.resize_save.stop()
        self.overlay.hide()
        self.overlay.deleteLater()
        self.panel.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_click_close_pauses_and_stops_voice_without_exiting_main_panel(self):
        self.panel.clock.start()
        quitting = QSignalSpy(self.application.aboutToQuit)
        position = self.overlay.pos()
        QTest.mouseClick(self.overlay.close_button, Qt.LeftButton)
        self.assertFalse(self.overlay.isVisible())
        self.assertFalse(self.panel.clock.running)
        self.assertEqual(self.panel.voice_stops, 1)
        self.assertGreater(self.panel.display_updates, 0)
        self.assertTrue(self.panel.isVisible())
        self.assertEqual(len(quitting), 0)
        self.assertEqual(self.overlay.pos(), position)
        self.assertIsNone(self.overlay.drag)
        self.overlay.show()
        self.assertTrue(self.overlay.isVisible(), "关闭按钮不销毁悬浮窗，仍能恢复")

    def test_paused_close_stops_manual_speech_and_supports_keyboard(self):
        self.assertFalse(self.panel.clock.running)
        self.assertEqual(self.overlay.close_button.focusPolicy(), Qt.StrongFocus)
        self.assertIn("关闭悬浮窗", self.overlay.close_button.accessibleName())
        self.assertFalse(self.overlay.close_button.icon().isNull())
        self.overlay.close_button.setFocus()
        QTest.keyClick(self.overlay.close_button, Qt.Key_Space)
        self.assertFalse(self.overlay.isVisible())
        self.assertEqual(self.panel.voice_stops, 1)

    def test_smallest_header_keeps_close_clock_and_title_separate(self):
        self.overlay.resize(280, 220)
        self.panel.clock.seek(65 * 60 + 12)
        self.overlay.render()
        self.application.processEvents()
        title, clock, close = self.overlay.title, self.overlay.clock_label, self.overlay.close_button
        self.assertEqual(close.size().width(), 24)
        self.assertEqual(close.size().height(), 24)
        self.assertGreater(title.width(), 20)
        title_right = title.mapTo(self.overlay, QPoint(title.width(), 0)).x()
        clock_left = clock.mapTo(self.overlay, QPoint()).x()
        close_left = close.mapTo(self.overlay, QPoint()).x()
        self.assertLessEqual(title_right, clock_left)
        self.assertLessEqual(clock.mapTo(self.overlay, QPoint(clock.width(), 0)).x(), close_left)
        self.assertGreaterEqual(clock.width(), clock.fontMetrics().horizontalAdvance(clock.text()))
        self.assertTrue(self.overlay.rect().contains(close.geometry()))

    def test_group_range_and_small_condition_caption_keep_original_notes(self):
        self.panel.ids = [0, 1]
        with patch("app.timer_steps", return_value=[{"time": "0:00"}, {"time": "4:00"}]):
            self.overlay.render()
        self.assertEqual(self.overlay.step_count.text(), "01–02 / 03")
        self.assertIn("条件提示", self.overlay.notes.text())
        self.assertIn("达到条件后再分配村民", self.overlay.notes.text())
        self.assertEqual(self.overlay.resources.cells["food"][2].text(), "6")
        self.panel.ids = [2]
        self.overlay.render()
        self.assertEqual(self.overlay.step_count.text(), "03 / 03")
        self.assertNotIn("条件提示", self.overlay.notes.text())

    def test_input_passthrough_flag_survives_header_control_and_can_be_recovered(self):
        self.panel.settings["passthrough"] = True
        self.overlay.apply_settings()
        self.assertTrue(self.overlay.windowFlags() & Qt.WindowTransparentForInput)
        self.panel.settings["passthrough"] = False
        self.overlay.apply_settings()
        self.assertFalse(self.overlay.windowFlags() & Qt.WindowTransparentForInput)
        self.overlay.show()
        QTest.mouseClick(self.overlay.close_button, Qt.LeftButton)
        self.assertFalse(self.overlay.isVisible())


if __name__ == "__main__":
    unittest.main()
