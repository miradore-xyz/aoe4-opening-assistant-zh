"""真实Qt外观设置验收：即时显示、保存、演练连续性及实际金色按钮。"""
from __future__ import annotations

import copy
import json
import os
from unittest.mock import patch
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, QRect, Qt
from PyQt5.QtGui import QColor, QIcon, QTextDocument
from PyQt5.QtWidgets import QPushButton
import test_brand_name as brand_fixture
from test_app import timed_example
from core import load_settings


def gold_pixels(image):
    """统计真实绘制的金色前景，不把暗底或灰色图标误算作金色。"""
    return sum(image.pixelColor(x, y).alpha() > 160
               and image.pixelColor(x, y).red() > 130
               and image.pixelColor(x, y).green() > 100
               and image.pixelColor(x, y).red() > image.pixelColor(x, y).blue() + 30
               for y in range(image.height()) for x in range(image.width()))


class OverlayAppearanceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        brand_fixture.BrandNameTests.setUpClass()
        cls.application = brand_fixture.BrandNameTests.application

    @classmethod
    def tearDownClass(cls):
        brand_fixture.BrandNameTests.tearDownClass()

    def setUp(self):
        self.freeze = patch("core.time.monotonic", return_value=100.0)
        self.freeze.start()
        self.fixture = brand_fixture.BrandNameTests("test_product_name_and_license_credit_in_actual_widgets")
        self.fixture.setUp()
        self.manager = self.fixture.manager
        self.manager.updates._startup_timer.stop()
        self.manager.order = timed_example()
        self.manager.ids = [0]
        self.manager.settings.update(speech=False, auto=True, timing="starting")
        self.manager.show()
        self.overlay = self.manager.overlay
        self.overlay.show()
        self.overlay.move(50, 40)
        self.manager.update_display()
        self.application.processEvents()

    def tearDown(self):
        try:
            self.fixture.tearDown()
        finally:
            self.freeze.stop()

    def saved(self):
        return json.loads(self.manager.settings_path.read_text("utf-8"))

    def test_text_color_immediately_renders_in_all_styles_and_survives_stage_changes(self):
        manager, overlay = self.manager, self.overlay
        manager.clock.start()
        manager.clock.seek(7)
        manager.change_passthrough(True)
        position = overlay.pos()
        ids = list(manager.ids)
        original_icons = [button.icon().pixmap(18, 18).toImage()
                          for button in (overlay.start_button, overlay.close_button)]
        with patch("app.ColorPickerDialog.getColor", return_value=QColor("#ef46b0")):
            manager.overlay_text_color_input.click()
        self.assertEqual(self.saved()["overlay_text_color"], "#ef46b0")
        self.assertEqual(load_settings(manager.settings_path)[0]["overlay_text_color"], "#ef46b0")
        self.assertEqual(manager.ids, ids)
        self.assertTrue(manager.clock.running)
        self.assertTrue(manager.settings["passthrough"])
        for style in ("a", "c", "b"):
            manager.overlay_style_input.setCurrentIndex(manager.overlay_style_input.findData(style))
            manager.ids = [1]
            manager.update_display()
            self.application.processEvents()
            with self.subTest(style=style):
                self.assertEqual(overlay.pos(), position)
                self.assertEqual(manager.clock.seconds, 7)
                self.assertIn("#ef46b0", overlay.title.styleSheet())
                self.assertIn("#ef46b0", overlay.clock_label.styleSheet())
                document = QTextDocument()
                document.setHtml(overlay.notes.text())
                block = document.begin()
                text_fragments = 0
                while block.isValid():
                    cursor = block.begin()
                    while not cursor.atEnd():
                        fragment = cursor.fragment()
                        if fragment.isValid() and fragment.text().strip() and not fragment.charFormat().isImageFormat():
                            self.assertEqual(fragment.charFormat().foreground().color().name(), "#ef46b0")
                            text_fragments += 1
                        cursor += 1
                    block = block.next()
                self.assertGreater(text_fragments, 2, "当前及历史正文都应使用所选文字色")
                picture = overlay.notes.grab().toImage()
                pink = sum(picture.pixelColor(x, y).alpha() > 200
                           and picture.pixelColor(x, y).red() > 180
                           and picture.pixelColor(x, y).green() < 100
                           and picture.pixelColor(x, y).blue() > 100
                           for y in range(picture.height()) for x in range(picture.width()))
                self.assertGreater(pink, 20, "实际正文须绘制所选颜色，而非仅保存配置")
                for index, button in enumerate((overlay.start_button, overlay.close_button)):
                    self.assertEqual(button.icon().pixmap(18, 18).toImage(), original_icons[index])
                for _icon, value in overlay.goals.cells.values():
                    self.assertIn("#ef46b0", value.styleSheet())

    def test_text_color_cancel_and_default_reset_preserve_other_appearance(self):
        manager = self.manager
        before = dict(manager.settings)
        with patch("app.ColorPickerDialog.getColor", return_value=QColor()), \
             patch.object(manager, "save_settings") as save:
            manager.overlay_text_color_input.click()
            save.assert_not_called()
        self.assertEqual(manager.settings, before)
        with patch("app.ColorPickerDialog.getColor", return_value=QColor("#24384a")):
            manager.overlay_text_color_input.click()
        manager.default_text_color_button.click()
        self.assertEqual(manager.settings, before)
        self.assertEqual(self.saved()["overlay_text_color"], "")
        self.assertIn("默认", manager.overlay_text_color_input.text())
        self.assertIn("#f2f4e9", manager.overlay.notes.text())

    def test_restore_all_appearance_also_restores_default_text_palette(self):
        with patch("app.ColorPickerDialog.getColor", return_value=QColor("#24384a")):
            self.manager.overlay_text_color_input.click()
        self.manager.restore_appearance_button.click()
        self.assertEqual(self.saved()["overlay_text_color"], "")
        self.assertIn("#f2f4e9", self.overlay.notes.text())

    def test_live_font_color_opacity_and_shadow_preserve_session_and_geometry(self):
        manager, overlay = self.manager, self.overlay
        manager.clock.start()
        manager.clock.seek(7)
        manager.change_passthrough(True)
        overlay.move(50, 40)
        position, width = overlay.pos(), overlay.width()
        order, ids = copy.deepcopy(manager.order), list(manager.ids)
        manager.font_input.setValue(15)
        manager.opacity_input.setValue(38)
        manager.text_shadow_strength_input.setValue(48)
        manager.text_shadow_check.setChecked(False)
        with patch("app.ColorPickerDialog.getColor", return_value=QColor("#325a77")):
            manager.overlay_color_input.click()
        self.application.processEvents()
        saved = self.saved()
        for key, expected in {"font_size": 15, "opacity": 38, "overlay_color": "#325a77",
                              "text_shadow": False, "text_shadow_strength": 48}.items():
            self.assertEqual(saved[key], expected)
        self.assertEqual(saved["width"], width)
        self.assertEqual(overlay.width(), width)
        self.assertEqual(overlay.pos(), position)
        self.assertEqual(manager.clock.seconds, 7)
        self.assertTrue(manager.clock.running)
        self.assertTrue(manager.settings["passthrough"])
        self.assertTrue(overlay.windowFlags() & Qt.WindowTransparentForInput)
        self.assertEqual(manager.order, order)
        self.assertEqual(manager.ids, ids)
        self.assertEqual(overlay.notes.font().pixelSize(), 15)
        self.assertEqual(overlay.windowOpacity(), 1.0)
        self.assertEqual(overlay.goals._font_size, 15)

    def test_zero_opacity_removes_background_for_all_styles_and_custom_color_is_used(self):
        manager, overlay = self.manager, self.overlay
        with patch("app.ColorPickerDialog.getColor", return_value=QColor("#325a77")):
            manager.overlay_color_input.click()
        for style in ("a", "c", "b"):
            with self.subTest(style=style):
                manager.overlay_style_input.setCurrentIndex(manager.overlay_style_input.findData(style))
                manager.opacity_input.setValue(0)
                image = overlay.content_backdrop(260, 140).toImage()
                self.assertEqual(max(image.pixelColor(x, y).alpha() for y in range(image.height())
                                     for x in range(image.width())), 0)
                manager.opacity_input.setValue(50)
                half = overlay.content_backdrop(260, 140).toImage().pixelColor(130, 70)
                manager.opacity_input.setValue(100)
                full = overlay.content_backdrop(260, 140).toImage().pixelColor(130, 70)
                self.assertGreater(full.alpha(), half.alpha())
                self.assertAlmostEqual(full.red(), 0x32, delta=2)
                self.assertAlmostEqual(full.green(), 0x5a, delta=2)
                self.assertAlmostEqual(full.blue(), 0x77, delta=2)
                self.assertEqual(overlay.windowOpacity(), 1.0)

    def test_color_dialog_cancel_does_not_commit_or_change_settings(self):
        before = dict(self.manager.settings)
        with patch("app.ColorPickerDialog.getColor", return_value=QColor()), \
             patch.object(self.manager, "save_settings") as save:
            self.manager.choose_overlay_color()
            save.assert_not_called()
        self.assertEqual(self.manager.settings, before)

    def test_black_text_shadow_switch_and_strength_affect_only_text_effects(self):
        manager, overlay = self.manager, self.overlay
        manager.text_shadow_check.setChecked(True)
        manager.text_shadow_strength_input.setValue(30)
        effect = overlay.notes.graphicsEffect()
        self.assertTrue(effect.isEnabled())
        self.assertEqual((effect.color().red(), effect.color().green(), effect.color().blue()), (0, 0, 0))
        weak = effect.color().alpha()
        manager.text_shadow_strength_input.setValue(90)
        self.assertIs(overlay.notes.graphicsEffect(), effect)
        self.assertGreater(effect.color().alpha(), weak)
        manager.text_shadow_check.setChecked(False)
        self.assertFalse(effect.isEnabled())
        self.assertIsNone(overlay.graphicsEffect())
        self.assertIsNone(overlay.start_button.graphicsEffect())
        self.assertIsNone(overlay.close_button.graphicsEffect())

    def test_restore_defaults_is_explicit_and_keeps_style_position_width_and_clock(self):
        manager, overlay = self.manager, self.overlay
        manager.overlay_style_input.setCurrentIndex(manager.overlay_style_input.findData("c"))
        manager.font_input.setValue(21)
        manager.opacity_input.setValue(0)
        manager.text_shadow_check.setChecked(False)
        manager.text_shadow_strength_input.setValue(10)
        with patch("app.ColorPickerDialog.getColor", return_value=QColor("#873454")):
            manager.overlay_color_input.click()
        overlay.move(50, 40)
        manager.clock.start()
        manager.clock.seek(8)
        position, width = overlay.pos(), overlay.width()
        manager.restore_appearance_button.click()
        saved = self.saved()
        for key, expected in {"font_size": 14, "opacity": 65, "overlay_color": "#1b1b1e",
                              "text_shadow": True, "text_shadow_strength": 75}.items():
            self.assertEqual(saved[key], expected)
        self.assertEqual(saved["overlay_style"], "c")
        self.assertEqual(overlay.pos(), position)
        self.assertEqual(overlay.width(), width)
        self.assertTrue(manager.clock.running)
        self.assertEqual(manager.clock.seconds, 8)

    def test_real_main_and_overlay_icons_remain_gold_in_disabled_and_pause_states(self):
        manager, overlay = self.manager, self.overlay
        self.assertIs(type(manager.pause_button), QPushButton, "主动作不能由纹样按钮再次重染图标")
        for running in (False, True):
            if running:
                manager.clock.start()
            manager.update_display()
            for button in (manager.pause_button, overlay.start_button, overlay.close_button):
                for enabled in (True, False):
                    with self.subTest(running=running, button=button.objectName(), enabled=enabled):
                        button.setEnabled(enabled)
                        icon = button.icon().pixmap(18, 18, QIcon.Normal if enabled else QIcon.Disabled).toImage()
                        self.assertGreater(gold_pixels(icon), 8)
                        rect = QRect((button.width() - 18) // 2, (button.height() - 18) // 2, 18, 18)
                        image = button.grab().toImage().copy(rect)
                        self.assertGreater(gold_pixels(image), 8, "实际按钮中心应仍绘制金色图标")


if __name__ == "__main__":
    unittest.main()
