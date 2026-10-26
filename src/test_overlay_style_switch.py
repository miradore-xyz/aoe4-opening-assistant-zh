"""三款悬浮样式即时保存、两窗口同步、暂停继续与一次性自动完成隐藏。"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtTest import QTest

# 只导入夹具模块，避免把其他模块的测试类重复收集进来。
import test_brand_name as brand_fixture
from core import overlay_end_deadline
from test_app import manual_example, timed_example


class OverlayStyleSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        brand_fixture.BrandNameTests.setUpClass()
        cls.application = brand_fixture.BrandNameTests.application

    @classmethod
    def tearDownClass(cls):
        brand_fixture.BrandNameTests.tearDownClass()

    def setUp(self):
        self.clock_patch = patch("core.time.monotonic", return_value=100.0)
        self.clock_patch.start()
        self.fixture = brand_fixture.BrandNameTests("test_product_name_and_license_credit_in_actual_widgets")
        self.fixture.setUp()
        self.manager = self.fixture.manager
        self.manager.updates._startup_timer.stop()
        self.manager.order = timed_example()
        self.manager.ids = [0]
        self.manager.settings.update(auto=True, speech=False, timing="starting")
        self.manager.show()
        self.manager.overlay.show()
        self.manager.update_display()
        self.application.processEvents()
        self.manager.overlay.lock_handle.sync_position()

    def tearDown(self):
        try:
            self.fixture.tearDown()
        finally:
            self.clock_patch.stop()

    def assert_all_surfaces_visible(self, visible):
        """正文、目标条和独立锁柄必须一起显隐，不留下孤立悬浮控件。"""
        overlay = self.manager.overlay
        # 锁柄依靠零延时同步任务跟随顶层窗口；允许Qt完成下一轮显隐和布局事件。
        QTest.qWait(20)
        self.assertEqual(overlay.isVisible(), visible)
        self.assertEqual(overlay.goals.isVisible(), visible)
        self.assertEqual(overlay.lock_handle.isVisible(), visible)

    def start(self):
        QTest.mouseClick(self.manager.overlay.start_button, Qt.LeftButton)
        self.application.processEvents()
        self.assertTrue(self.manager.clock.running)
        self.assertTrue(self.manager.settings["passthrough"])

    def complete(self):
        self.start()
        deadline = overlay_end_deadline(self.manager.order)
        self.assertEqual(deadline, 140)
        self.manager.clock.seek(deadline)
        self.assertTrue(self.manager.check_overlay_completion())
        self.assert_all_surfaces_visible(False)

    def test_styles_save_immediately_and_update_both_surfaces_without_resetting_session(self):
        manager, overlay = self.manager, self.manager.overlay
        self.assertEqual([manager.overlay_style_input.itemData(index)
                          for index in range(manager.overlay_style_input.count())], ["a", "c", "b"])
        self.start()
        manager.clock.seek(123)
        order_identity = manager.order
        order_copy = copy.deepcopy(manager.order)
        ids = list(manager.ids)
        surfaces = {}
        for style in ("c", "b", "a"):
            with self.subTest(style=style):
                manager.overlay_style_input.setCurrentIndex(manager.overlay_style_input.findData(style))
                self.application.processEvents()
                saved = json.loads(manager.settings_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["overlay_style"], style)
                self.assertEqual(overlay.visual_style, style)
                self.assertEqual(overlay.goals.visual_style, style)
                self.assertIs(manager.order, order_identity)
                self.assertEqual(manager.order, order_copy)
                self.assertEqual(manager.ids, ids)
                self.assertEqual(manager.clock.seconds, 123)
                self.assertTrue(overlay.clock_label.isVisible(), "计划节点时刻不能替代实际计时")
                self.assertEqual(overlay.clock_label.text(), "02:03")
                self.assertTrue(manager.clock.running)
                self.assertTrue(manager.settings["passthrough"])
                self.assertTrue(overlay.windowFlags() & Qt.WindowTransparentForInput)
                self.assertTrue(overlay.goals.windowFlags() & Qt.WindowTransparentForInput)
                self.assertFalse(overlay.lock_handle.windowFlags() & Qt.WindowTransparentForInput)
                self.assert_all_surfaces_visible(True)
                surfaces[style] = (overlay.grab().toImage(), overlay.goals.grab().toImage())
        for index in (0, 1):
            self.assertNotEqual(surfaces["a"][index], surfaces["c"][index], "正文与目标条均须实际切换视觉")
            self.assertNotEqual(surfaces["a"][index], surfaces["b"][index])

    def test_style_switch_does_not_commit_other_unapplied_setting_inputs(self):
        manager = self.manager
        width, font_size, ui_size = manager.settings["width"], manager.settings["font_size"], manager.settings["ui_font_size"]
        manager.ui_font_input.setValue(ui_size + 1)
        manager.font_input.setValue(font_size + 1)
        manager.overlay_style_input.setCurrentIndex(manager.overlay_style_input.findData("c"))
        saved = json.loads(manager.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["overlay_style"], "c")
        self.assertEqual(saved["width"], width)
        self.assertEqual(saved["font_size"], font_size + 1, "悬浮字号是即时应用项")
        self.assertEqual(saved["ui_font_size"], ui_size, "尚未应用的主界面字号不随样式切换提交")

    def test_unlock_running_exposes_pause_then_continue_locks_again(self):
        manager, overlay = self.manager, self.manager.overlay
        self.start()
        manager.clock.seek(8)
        QTest.mouseClick(overlay.lock_handle, Qt.LeftButton)
        self.application.processEvents()
        self.assertTrue(manager.clock.running)
        self.assertFalse(manager.settings["passthrough"])
        self.assertTrue(overlay.start_button.isVisible())
        self.assertTrue(overlay.close_button.isVisible())
        self.assertIn("暂停", overlay.start_button.accessibleName())
        QTest.mouseClick(overlay.start_button, Qt.LeftButton)
        self.assertFalse(manager.clock.running)
        self.assertEqual(manager.clock.seconds, 8)
        self.assertFalse(manager.settings["passthrough"])
        self.assertIn("继续", overlay.start_button.accessibleName())
        QTest.mouseClick(overlay.start_button, Qt.LeftButton)
        self.application.processEvents()
        self.assertTrue(manager.clock.running)
        self.assertEqual(manager.clock.seconds, 8)
        self.assertTrue(manager.settings["passthrough"])
        self.assertFalse(overlay.start_button.isVisible())
        self.assertFalse(overlay.close_button.isVisible())

    def test_play_pause_lock_and_close_use_the_same_visible_gold_family(self):
        overlay = self.manager.overlay
        for running in (False, True):
            if running:
                self.start()
                QTest.mouseClick(overlay.lock_handle, Qt.LeftButton)
                self.application.processEvents()
            images = {"开始或暂停": overlay.start_button.icon().pixmap(18, 18).toImage(),
                      "关闭": overlay.close_button.icon().pixmap(18, 18).toImage(),
                      "开锁": overlay.lock_handle.grab().toImage()}
            for name, image in images.items():
                with self.subTest(running=running, icon=name):
                    gold = sum(image.pixelColor(x, y).alpha() > 180
                               and image.pixelColor(x, y).red() > 130
                               and image.pixelColor(x, y).green() > 100
                               and image.pixelColor(x, y).red() > image.pixelColor(x, y).blue() + 30
                               for y in range(image.height()) for x in range(image.width()))
                    self.assertGreater(gold, 8, "不能混入白色旧图标或空图")

    def test_last_author_time_plus_two_minutes_hides_all_surfaces_once(self):
        manager = self.manager
        self.start()
        manager.clock.seek(139.999)
        self.assertFalse(manager.check_overlay_completion())
        self.assert_all_surfaces_visible(True)
        manager.clock.seek(140)
        self.assertTrue(manager.check_overlay_completion())
        self.assert_all_surfaces_visible(False)
        self.assertTrue(manager.clock.running, "自动隐藏不会擅自暂停玩家的计时")
        self.assertTrue(manager._overlay_completion_handled)
        self.assertFalse(manager.check_overlay_completion())
        manager.toggle_overlay()
        self.assert_all_surfaces_visible(True)
        manager.clock.seek(160)
        manager.tick()
        self.assert_all_surfaces_visible(True)

    def test_reset_restores_only_a_window_hidden_by_automatic_completion(self):
        manager = self.manager
        self.complete()
        manager.reset_clock()
        self.assertEqual(manager.clock.seconds, 0)
        self.assertFalse(manager.clock.running)
        self.assertFalse(manager._overlay_completion_handled)
        self.assert_all_surfaces_visible(True)
        manager.toggle_overlay()
        self.assert_all_surfaces_visible(False)
        manager.reset_clock()
        self.assert_all_surfaces_visible(False)

    def test_manual_order_and_paused_clock_have_no_automatic_hide(self):
        manager = self.manager
        self.start()
        manager.clock.pause()
        manager.clock.seek(200)
        self.assertFalse(manager.check_overlay_completion())
        self.assert_all_surfaces_visible(True)
        manager.order = manual_example()
        manager.ids = [0]
        manager.clock.start()
        manager.clock.seek(10000)
        manager.update_display()
        self.assertIsNone(overlay_end_deadline(manager.order))
        self.assertFalse(manager.check_overlay_completion())
        self.assert_all_surfaces_visible(True)


if __name__ == "__main__":
    unittest.main()
