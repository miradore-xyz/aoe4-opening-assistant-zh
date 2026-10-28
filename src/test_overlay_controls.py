"""精简悬浮窗：开始后锁定、独立解锁、关闭与正文分组的离屏验证。"""
from __future__ import annotations

import copy
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import unittest
from unittest.mock import patch

from PyQt5.QtCore import Qt, QPoint, QEvent, QRect
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtTest import QTest, QSignalSpy
from PyQt5.QtWidgets import QApplication, QWidget
from app import ASSETS, Overlay, STYLE, spin
from core import Clock, DEFAULTS, NoteRenderer, read_order, timed_ids


class Panel(QWidget):
    """保留真实计时和文本渲染的隔离面板，不注册热键、不启动声音或联网。"""

    def __init__(self):
        super().__init__()
        self.settings = copy.deepcopy(DEFAULTS)
        self.settings.update(width=280, height=220, speech=False, auto=True, passthrough=False)
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
            self.stop_voice()
        else:
            if not self.voice_ready():
                return
            self.clock.start()
            self.change_passthrough(True)
        self.update_display()

    def change_passthrough(self, enabled):
        self.settings["passthrough"] = bool(enabled)
        self.overlay.apply_settings()
        self.overlay.render()
        self.save_settings()

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
        self.overlay.lock_handle.cleanup()
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

    def test_smallest_header_keeps_title_clock_lock_slot_and_close_separate(self):
        self.overlay.resize(280, 220)
        self.panel.clock.seek(65 * 60 + 12)
        self.overlay.render()
        self.application.processEvents()
        title, clock, close = self.overlay.title, self.overlay.clock_label, self.overlay.close_button
        lock_slot = self.overlay.lock_anchor
        self.assertEqual(close.size().width(), 28)
        self.assertEqual(close.size().height(), 28)
        self.assertGreater(title.width(), 20)
        title_right = title.mapTo(self.overlay, QPoint(title.width(), 0)).x()
        clock_left = clock.mapTo(self.overlay, QPoint()).x()
        close_left = close.mapTo(self.overlay, QPoint()).x()
        self.assertLessEqual(title_right, clock_left)
        lock_left = lock_slot.mapTo(self.overlay, QPoint()).x()
        self.assertLessEqual(clock.mapTo(self.overlay, QPoint(clock.width(), 0)).x(), lock_left)
        self.assertLessEqual(lock_slot.mapTo(self.overlay, QPoint(lock_slot.width(), 0)).x(), close_left)
        self.assertGreaterEqual(clock.width(), clock.fontMetrics().horizontalAdvance(clock.text()))
        self.assertTrue(self.overlay.rect().contains(close.geometry()))
        self.overlay.lock_handle.sync_position()
        slot = lock_slot.rect().translated(lock_slot.mapToGlobal(QPoint()))
        self.assertTrue(slot.contains(self.overlay.lock_handle.geometry()))

    def test_group_range_keeps_condition_notes_without_extra_caption(self):
        self.panel.ids = [0, 1]
        with patch("app.timer_steps", return_value=[{"time": "0:00"}, {"time": "4:00"}]):
            self.overlay.render()
        self.assertEqual(self.overlay.current_stage_ids, [0, 1])
        self.assertNotIn("条件提示", self.overlay.notes.text())
        self.assertIn("达到条件后再分配村民", self.overlay.notes.text())
        self.assertFalse(self.overlay.resources.isVisible())
        self.panel.ids = [2]
        self.overlay.render()
        self.assertEqual(self.overlay.current_stage_ids, [2])
        self.assertEqual(self.overlay.previous_stage_ids, [0, 1])
        current_html = self.overlay.notes.text().split('<div data-stage="previous">')[0]
        self.assertNotIn("条件提示", current_html)
        self.assertNotIn("条件提示", self.overlay.notes.text())
        self.assertIn("达到条件后再分配村民", self.overlay.notes.text(), "上一阶段的附属条件正文仍应保留")

    def test_start_is_compact_locks_only_after_success_and_removes_legacy_controls(self):
        self.assertEqual((self.overlay.start_button.width(), self.overlay.start_button.height()), (28, 28))
        self.assertEqual(self.overlay.start_button.text(), "")
        self.assertFalse(self.overlay.start_button.icon().isNull())
        self.assertIn("开始计时", self.overlay.start_button.accessibleName())
        self.assertEqual(self.overlay.start_button.geometry().center().y(), self.overlay.lock_anchor.geometry().center().y())
        self.assertTrue(self.overlay.start_button.isVisible())
        self.assertFalse(self.panel.settings["passthrough"])
        for name in ("pause", "previous", "next", "status", "progress", "step_count", "lock_button"):
            self.assertFalse(hasattr(self.overlay, name), name)
        with patch.object(self.panel, "voice_ready", return_value=False):
            self.overlay.render()
            self.assertFalse(self.overlay.start_button.isEnabled())
            self.overlay.start_session()
            self.assertFalse(self.panel.clock.running)
            self.assertFalse(self.panel.settings["passthrough"])
        self.overlay.render()
        lock_slot_before = self.overlay.lock_anchor.geometry()
        close_before = self.overlay.close_button.geometry()
        QTest.mouseClick(self.overlay.start_button, Qt.LeftButton)
        self.application.processEvents()
        self.assertTrue(self.panel.clock.running)
        self.assertTrue(self.panel.settings["passthrough"])
        self.assertTrue(self.overlay.windowFlags() & Qt.WindowTransparentForInput)
        self.assertFalse(self.overlay.start_button.isVisible())
        self.assertEqual(self.overlay.lock_anchor.geometry(), lock_slot_before,
                         "隐藏播放图形后保留槽位，锁图标不能突然换位置")
        self.assertEqual(self.overlay.close_button.geometry(), close_before)
        QTest.mouseClick(self.overlay.lock_handle, Qt.LeftButton)
        self.assertFalse(self.panel.settings["passthrough"])
        self.assertTrue(self.panel.clock.running, "解锁只恢复面板操作，不改变正在运行的计时")
        self.assertTrue(self.overlay.start_button.isVisible())
        self.assertIn("暂停", self.overlay.start_button.accessibleName())
        QTest.mouseClick(self.overlay.start_button, Qt.LeftButton)
        self.assertFalse(self.panel.clock.running)
        self.assertFalse(self.panel.settings["passthrough"])
        QTest.mouseClick(self.overlay.start_button, Qt.LeftButton)
        self.assertTrue(self.panel.clock.running)
        self.assertTrue(self.panel.settings["passthrough"])

    def test_short_body_shrinks_below_old_height_without_persisting_auto_size(self):
        self.panel.order["build_order"][0]["notes"] = ["持续造村民。"]
        self.panel.order["build_order"][1]["time"] = "0:10"
        self.panel.ids = [0]
        self.overlay.render()
        self.application.processEvents()
        self.assertLess(self.overlay.height(), 160)
        self.assertEqual(self.panel.settings["height"], 220)
        self.assertEqual(self.overlay.scroll.verticalScrollBar().maximum(), 0)
        self.assertFalse(self.overlay.resize_save.isActive())
        self.assertLess(self.overlay.start_button.geometry().bottom(), self.overlay.scroll.geometry().top())

    def test_default_text_and_history_wrap_without_individual_background_strips(self):
        from PyQt5.QtGui import QTextDocument
        self.panel.settings["font_size"] = 16
        self.panel.ids = [2]
        self.overlay.render()
        body = self.overlay.notes.text()
        self.assertIn("font-size:16px", body)
        self.assertIn("font-size:14px", body)
        self.assertIn("#dbe2d7", body)
        document = QTextDocument()
        document.setHtml(body)
        document.setTextWidth(120)
        document.size()
        fragments, wrapped = 0, False
        block = document.begin()
        while block.isValid():
            wrapped |= block.layout().lineCount() > 1
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and fragment.text().strip():
                    fragments += 1
                    self.assertEqual(fragment.charFormat().background().style(), Qt.NoBrush,
                                     "文字与图标共用整块渐隐暗衬，换行后不能再出现逐行黑条")
                iterator += 1
            block = block.next()
        self.assertGreater(fragments, 3)
        self.assertNotIn("background-color", body)
        self.assertTrue(wrapped, "验证Qt实际解析与换行，不只检查HTML字符串")

    def test_real_bundled_mongol_three_stages_remain_complete_and_last_line_is_accessible(self):
        class AvailableScreen:
            def __init__(self):
                self.native = QApplication.primaryScreen()

            def availableGeometry(self):
                return QRect(0, 0, 1600, 900)

            def __getattr__(self, name):
                return getattr(self.native, name)

        screen = AvailableScreen()
        self.panel.order = read_order(Path(__file__).parent / "文明资料/修正开局/蒙古-怯薛封建压制.json")
        self.panel.ids = timed_ids(self.panel.order, 300, starting=True)
        self.panel.settings.update(width=400, font_size=16, height=220)
        with patch.object(QApplication, "screens", return_value=[screen]), \
             patch.object(QApplication, "primaryScreen", return_value=screen), \
             patch.object(QApplication, "screenAt", return_value=screen):
            self.overlay.apply_settings()
            self.overlay.render()
            self.application.processEvents()
            self.assertEqual(self.overlay.previous_previous_stage_ids, [8])
            # 完整历史使用最多75%可用屏高；额外18像素只留给正文外的渐隐尾部。
            self.assertLessEqual(self.overlay.height(), 675 + 18)
            self.assertLessEqual(self.overlay.scroll.geometry().bottom(), 675)
            self.assertGreaterEqual(self.overlay.height() - self.overlay.scroll.geometry().bottom(), 18)
            full_previous = self.overlay.stage_html(self.panel.order, [8], timed=True, history_level=2)
            self.assertIn(full_previous, self.overlay.notes.text(), "增大行距不能靠删掉最早阶段来容纳")
            scrollbar = self.overlay.scroll.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
            self.application.processEvents()
            self.assertEqual(scrollbar.value(), scrollbar.maximum())
            last_line_bottom = self.overlay.notes.mapTo(self.overlay.scroll.viewport(),
                                                        QPoint(0, self.overlay.notes.height())).y()
            self.assertLessEqual(last_line_bottom, self.overlay.scroll.viewport().height(),
                                 "超过屏高的正文必须能解锁滚到最后一行")

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
