"""悬浮外观设置：保留已有选择，颜色格式与阴影强度可安全往返。"""
import tempfile
from pathlib import Path
import unittest

from core import DEFAULTS, atomic_json, load_settings


class OverlayAppearanceSettingsTests(unittest.TestCase):
    def test_text_color_round_trips_and_missing_or_invalid_values_keep_default_palette(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "设置.json"
            self.assertEqual(load_settings(path)[0]["overlay_text_color"], "")
            for value, expected in (("#E04AB2", "#e04ab2"), ("#000000", "#000000"),
                                    ("", ""), ("red", ""), ("#123", ""), (12, ""),
                                    ("#000000; font-size:90px", "")):
                with self.subTest(value=value):
                    atomic_json(path, {"overlay_text_color": value, "font_size": 14, "font_size_unit": "px"})
                    loaded, warning = load_settings(path)
                    self.assertFalse(warning)
                    self.assertEqual(loaded["overlay_text_color"], expected)
                    self.assertEqual(loaded["font_size"], 14)

    def test_defaults_and_saved_custom_appearance_round_trip_without_font_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "设置.json"
            settings, _ = load_settings(path)
            self.assertEqual(settings["font_size"], 14)
            self.assertEqual(settings["overlay_color"], "#1b1b1e")
            self.assertTrue(settings["text_shadow"])
            settings.update(font_size=12, overlay_color="#A05E20", opacity=0,
                            text_shadow=False, text_shadow_strength=31)
            atomic_json(path, settings)
            for dpi in (96, 144, 192):
                actual, warning = load_settings(path, logical_dpi=dpi)
                self.assertFalse(warning)
                self.assertEqual(actual["font_size"], 12)
                self.assertEqual(actual["overlay_color"], "#a05e20")
                self.assertEqual(actual["opacity"], 0)
                self.assertFalse(actual["text_shadow"])
                self.assertEqual(actual["text_shadow_strength"], 31)

    def test_invalid_colors_fall_back_and_strength_is_clamped(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "设置.json"
            for color in ("red", "#abc", "#00112233", "#00zz11", "", 8, None):
                with self.subTest(color=color):
                    atomic_json(path, {"overlay_color": color, "text_shadow_strength": 120})
                    actual, _ = load_settings(path)
                    self.assertEqual(actual["overlay_color"], DEFAULTS["overlay_color"])
                    self.assertEqual(actual["text_shadow_strength"], 100)
            atomic_json(path, {"text_shadow_strength": -4, "text_shadow": "false"})
            actual, _ = load_settings(path)
            self.assertEqual(actual["text_shadow_strength"], 0)
            self.assertTrue(actual["text_shadow"], "错误类型不能代替真实开关")


if __name__ == "__main__":
    unittest.main()
