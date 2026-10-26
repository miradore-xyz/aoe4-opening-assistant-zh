"""字号实际使用像素；已有磅数设置只换算一次并尽量保持显示大小。"""
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import DEFAULTS, atomic_json, load_settings
import test_brand_name as brand_tests
from test_app import timed_example


class FontSettingsMigrationTests(unittest.TestCase):
    def test_new_settings_use_pixels_without_converting_the_default_again(self):
        with tempfile.TemporaryDirectory() as folder:
            settings, warning = load_settings(Path(folder) / "设置.json", logical_dpi=144)
            self.assertEqual(settings["font_size"], 14)
            self.assertEqual(settings["font_size_unit"], "px")
            self.assertEqual(DEFAULTS["font_size_unit"], "px")
            self.assertEqual(settings["width"], 400)
            self.assertFalse(warning)

    def test_legacy_points_convert_at_the_active_screen_logical_dpi(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "设置.json"
            for points, dpi, pixels in ((6, 96, 8), (7, 96, 9), (8, 96, 11),
                                        (17, 96, 23), (28, 96, 37),
                                        (7, 144, 14), (28, 144, 56)):
                with self.subTest(points=points, dpi=dpi):
                    atomic_json(path, {"font_size": points, "ui_font_size": 15})
                    original = path.read_bytes()
                    settings, warning = load_settings(path, logical_dpi=dpi)
                    self.assertEqual(settings["font_size"], pixels)
                    self.assertEqual(settings["font_size_unit"], "px")
                    self.assertEqual(settings["ui_font_size"], 15)
                    self.assertEqual(path.read_bytes(), original)
                    self.assertFalse(warning)

    def test_saved_pixel_sizes_survive_style_upgrades_without_enlargement(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "设置.json"
            for old, expected in ((8, 8), (12, 12), (13, 13), (14, 14), (16, 16), (18, 18)):
                with self.subTest(old=old):
                    atomic_json(path, {"font_size": old, "font_size_unit": "px"})
                    settings, _ = load_settings(path)
                    self.assertEqual(settings["font_size"], expected)
                    self.assertEqual(settings["overlay_readability_policy"], 2)
            settings.update(font_size=8)
            atomic_json(path, settings)
            self.assertEqual(load_settings(path)[0]["font_size"], 8,
                             "升级后主动选择的小字号不能被下一次启动再次重置")

    def test_only_old_default_width_is_widened_and_later_custom_width_is_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "设置.json"
            for width, expected in ((280, 280), (330, 400), (380, 380), (500, 500)):
                with self.subTest(width=width):
                    atomic_json(path, {"width": width, "font_size": 14, "font_size_unit": "px"})
                    self.assertEqual(load_settings(path)[0]["width"], expected)
            atomic_json(path, {"width": 330, "overlay_readability_policy": 1})
            self.assertEqual(load_settings(path)[0]["width"], 330,
                             "升级后用户主动选回330不再自动扩大")

    def test_saved_pixel_value_is_not_converted_on_reopen_or_a_different_screen(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "设置.json"
            atomic_json(path, {"font_size": 17})
            converted, _ = load_settings(path, logical_dpi=96)
            atomic_json(path, converted)
            for dpi in (96, 144, 192):
                with self.subTest(dpi=dpi):
                    reopened, warning = load_settings(path, logical_dpi=dpi)
                    self.assertEqual(reopened["font_size"], 23)
                    self.assertEqual(reopened["font_size_unit"], "px")
                    self.assertFalse(warning)


class FontPixelsIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        brand_tests.BrandNameTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        brand_tests.BrandNameTests.tearDownClass()

    def test_real_settings_and_overlay_paragraphs_use_pixels(self):
        case = brand_tests.BrandNameTests("test_product_name_and_license_credit_in_actual_widgets")
        case.setUp()
        try:
            manager = case.manager
            self.assertEqual(manager.font_input.suffix(), " 像素")
            self.assertEqual(manager.ui_font_input.suffix(), " 像素")
            self.assertEqual((manager.font_input.minimum(), manager.font_input.maximum()), (8, 80))
            manager.order = timed_example()
            manager.ids = [0]
            manager.font_input.setValue(17)
            manager.apply_settings()
            manager.overlay.render()
            text = manager.overlay.notes.text()
            self.assertIn("font-size:17px", text)
            self.assertIn("font-size:13px", text, "17像素正文对应13像素阶段标题，均使用像素")
            history = manager.overlay.stage_html(manager.order, [0], previous=True, timed=True)
            self.assertIn("font-size:15px", history, "历史正文比当前正文小两像素")
            self.assertNotIn("pt;", text)
            settings, warning = load_settings(manager.settings_path)
            self.assertEqual(settings["font_size"], 17)
            self.assertEqual(settings["font_size_unit"], "px")
            self.assertFalse(warning)
        finally:
            case.tearDown()


if __name__ == "__main__":
    unittest.main()
