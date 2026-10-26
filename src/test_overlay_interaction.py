"""可调背景与锁定穿透：保留旧透明度、主面板解锁和快捷键恢复。"""
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeySequence
from PyQt5.QtTest import QTest
from unittest.mock import patch
from core import DEFAULTS, atomic_json, load_settings
import test_brand_name as brand_tests
from test_app import timed_example


class OverlayInteractionSettingsTests(unittest.TestCase):
    def test_new_default_is_readable_and_legacy_opacity_is_preserved_without_rewriting_source(self):
        self.assertEqual(DEFAULTS["opacity"], 65)
        self.assertFalse(DEFAULTS["passthrough"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "设置.json"
            new, warning = load_settings(path)
            self.assertEqual(new["opacity"], 65)
            self.assertFalse(new["passthrough"])
            self.assertFalse(warning)
            atomic_json(path, {"opacity": 92, "passthrough": False, "timed_playback_policy": 1})
            original = path.read_bytes()
            migrated, warning = load_settings(path)
            self.assertEqual(migrated["opacity"], 92)
            self.assertFalse(migrated["passthrough"])
            self.assertEqual(migrated["overlay_interaction_policy"], 2)
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(warning)

    def test_user_opacity_and_unlocked_preference_survive_reopen_after_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "设置.json"
            settings, _ = load_settings(path)
            settings.update(opacity=38, passthrough=False)
            atomic_json(path, settings)
            reopened, _ = load_settings(path)
            self.assertEqual(reopened["opacity"], 38)
            self.assertFalse(reopened["passthrough"])
            reopened["opacity"] = 0
            atomic_json(path, reopened)
            self.assertEqual(load_settings(path)[0]["opacity"], 0)


class OverlayInteractionManagerTests(unittest.TestCase):
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
        self.manager.updates._startup_timer.stop()
        self.manager.order = timed_example()
        self.manager.ids = [0]
        self.manager.show()
        self.manager.open_session()
        QTest.qWait(20)

    def tearDown(self):
        self.case.tearDown()

    def test_main_panel_lock_button_and_shortcut_can_unlock_the_passthrough_window(self):
        manager = self.manager
        manager.change_passthrough(True)
        control = manager.passthrough_check
        self.assertTrue(control.isChecked())
        self.assertTrue(control.isVisible())
        self.assertFalse(control.icon().isNull())
        self.assertIn("解锁悬浮窗", control.accessibleName())
        self.assertEqual(manager.settings["hotkeys"]["passthrough"], "")
        self.assertNotIn("Ctrl+Alt+F10", control.toolTip())
        self.assertNotIn("也可按", control.toolTip())
        # 默认不预占全局按键；只有用户主动填写并保存后才显示该快捷键。
        manager.key_inputs["passthrough"].setKeySequence(QKeySequence("Ctrl+Alt+F10"))
        with patch.object(manager, "prepare_voice"):
            manager.apply_settings()
        self.assertEqual(manager.settings["hotkeys"]["passthrough"], "Ctrl+Alt+F10")
        self.assertIn("Ctrl+Alt+F10", control.toolTip())
        self.assertTrue(manager.overlay.windowFlags() & Qt.WindowTransparentForInput)
        QTest.mouseClick(control, Qt.LeftButton)
        self.assertFalse(manager.settings["passthrough"])
        self.assertFalse(manager.overlay.windowFlags() & Qt.WindowTransparentForInput)
        self.assertIn("锁定悬浮窗", control.accessibleName())
        manager.on_hotkey("passthrough")
        self.assertTrue(manager.settings["passthrough"])
        self.assertTrue(control.isChecked())
        self.assertTrue(manager.overlay.windowFlags() & Qt.WindowTransparentForInput)
        manager.on_hotkey("passthrough")
        self.assertFalse(manager.settings["passthrough"])
        self.assertFalse(control.isChecked())
        settings, warning = load_settings(manager.settings_path)
        self.assertFalse(settings["passthrough"])
        self.assertEqual(settings["hotkeys"]["passthrough"], "Ctrl+Alt+F10")
        self.assertFalse(warning)

    def test_zero_background_opacity_preserves_visible_text_and_full_window_opacity(self):
        manager = self.manager
        self.assertEqual((manager.opacity_input.minimum(), manager.opacity_input.maximum()), (0, 100))
        manager.opacity_input.setValue(0)
        manager.apply_settings()
        self.assertEqual(manager.opacity_input.value(), 0)
        manager.overlay.render()
        manager.overlay.show()
        QTest.qWait(20)
        self.assertEqual(manager.overlay.windowOpacity(), 1.0)
        self.assertIn("第一步", manager.overlay.notes.text())
        image = manager.overlay.grab().toImage()
        opaque_text = sum(1 for y in range(image.height()) for x in range(image.width())
                          if image.pixelColor(x, y).alpha() > 220
                          and min(image.pixelColor(x, y).getRgb()[:3]) > 180)
        self.assertGreater(opaque_text, 20)

    def test_start_locks_and_independent_handle_unlocks_without_pausing(self):
        manager = self.manager
        self.assertFalse(manager.settings["passthrough"])
        manager.overlay.show()
        manager.overlay.render()
        QTest.qWait(20)
        self.assertTrue(manager.overlay.start_button.isVisible())
        QTest.mouseClick(manager.overlay.start_button, Qt.LeftButton)
        self.assertTrue(manager.clock.running)
        self.assertTrue(manager.settings["passthrough"])
        self.assertTrue(manager.overlay.start_button.isHidden())
        self.assertTrue(manager.overlay.windowFlags() & Qt.WindowTransparentForInput)
        handle = manager.overlay.lock_handle
        QTest.qWait(20)
        self.assertTrue(handle.isVisible())
        self.assertFalse(handle.windowFlags() & Qt.WindowTransparentForInput)
        self.assertIn("解锁", handle.accessibleName())
        QTest.mouseClick(handle, Qt.LeftButton)
        self.assertFalse(manager.settings["passthrough"])
        self.assertTrue(manager.clock.running, "解锁控制不能暂停演练")

    def test_unready_voice_does_not_lock_or_start_a_waiting_preview(self):
        manager = self.manager
        with patch.object(manager, "voice_ready", return_value=False), patch.object(manager, "prepare_voice"):
            manager.toggle_clock()
        self.assertFalse(manager.clock.running)
        self.assertFalse(manager.settings["passthrough"])


if __name__ == "__main__":
    unittest.main()
