"""目标栏自定义文字色：实际像素、阶段切换、默认层级及不染色的资源图片。"""
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QRect, Qt
from PyQt5.QtGui import QColor, QFontDatabase, QImage, QPalette
from PyQt5.QtWidgets import QApplication

from overlay_goals import GoalHUD


class _Screen:
    """固定逻辑屏幕，测试不触碰游戏窗口。"""

    def availableGeometry(self):
        return QRect(0, 0, 1600, 900)


class _Renderer:
    """临时资源图片或中文回退文字，完全离线。"""

    def __init__(self, picture=None):
        self.picture = picture

    def image_path(self, _token):
        return self.picture


class GoalTextColorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))

    def setUp(self):
        self.hud = GoalHUD(_Renderer())
        self.settings = {"opacity": 0, "text_shadow": False, "overlay_text_color": "#3bd9a7"}

    def tearDown(self):
        self.hud.cleanup()
        self.hud.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()

    def render(self):
        self.application.processEvents()
        return self.hud.grab().toImage()

    def assert_label_color(self, label, color):
        self.application.processEvents()
        self.assertEqual(label.palette().color(QPalette.WindowText), QColor(color))

    def assert_glyph_pixels(self, image, label, color):
        """只取控件实际矩形内的高覆盖像素，排除抗锯齿边缘与透明背景。"""
        target = QColor(color)
        ratio = image.devicePixelRatioF()
        rect = label.geometry()
        count = 0
        for y in range(round(rect.top() * ratio), round((rect.bottom() + 1) * ratio)):
            for x in range(round(rect.left() * ratio), round((rect.right() + 1) * ratio)):
                pixel = image.pixelColor(x, y)
                if pixel.alpha() >= 230 and all(abs(a - b) <= 2 for a, b in zip(pixel.getRgb()[:3], target.getRgb()[:3])):
                    count += 1
        self.assertGreater(count, 5, f"{label.text()}的实际像素应使用{color}")

    def test_color_set_before_targets_renders_numbers_and_fallback_text(self):
        self.hud.set_appearance(self.settings)
        self.assertFalse(self.hud.has_targets)
        self.hud.set_step({"resources": {"food": 24}, "age": 2}, 24)
        self.hud.show_for_screen(_Screen())
        image = self.render()
        for key in ("food", "age"):
            for label in self.hud.cells[key]:
                self.assert_label_color(label, "#3bd9a7")
                self.assert_glyph_pixels(image, label, "#3bd9a7")
        self.assertEqual(self.hud.cells["age"][1].text(), "II")

    def test_immediate_change_preserves_geometry_targets_visibility_and_input(self):
        self.hud.set_step({"resources": {"food": 24, "wood": 12}}, 24)
        self.hud.show_for_screen(_Screen())
        self.render()
        before = (self.hud.geometry(), dict(self.hud.values), self.hud.windowFlags())
        self.hud.set_appearance(self.settings)
        image = self.render()
        self.assertEqual((self.hud.geometry(), self.hud.values, self.hud.windowFlags()), before)
        self.assertTrue(self.hud.isVisible())
        self.assertTrue(self.hud.windowFlags() & Qt.WindowTransparentForInput)
        self.assertEqual(self.hud.windowOpacity(), 1.0)
        for key in ("food", "wood"):
            for label in self.hud.cells[key]:
                self.assert_label_color(label, "#3bd9a7")
                self.assert_glyph_pixels(image, label, "#3bd9a7")

    def test_stage_style_and_empty_target_transitions_keep_custom_color(self):
        self.hud.set_appearance(self.settings)
        for style in ("a", "c", "b"):
            with self.subTest(style=style):
                self.hud.set_style(style)
                self.hud.set_step({})
                self.assertFalse(self.hud.has_targets)
                self.hud.set_step({"resources": {"gold": 16}}, 24)
                self.hud.show_for_screen(_Screen())
                first = self.render()
                self.hud.set_step({"resources": {"gold": 16}}, 24)
                second = self.render()
                self.assertEqual(first, second, "内容缓存命中不能丢失文字颜色")
                for label in self.hud.cells["gold"]:
                    self.assert_label_color(label, "#3bd9a7")
                    self.assert_glyph_pixels(second, label, "#3bd9a7")

    def test_empty_or_invalid_color_restores_original_layered_colors(self):
        self.hud.set_step({"resources": {"food": 24}}, 24)
        self.hud.show_for_screen(_Screen())
        for value in ("", "red", "#123", "#ff000080", None):
            with self.subTest(value=value):
                self.hud.set_appearance(self.settings)
                self.hud.set_appearance(dict(self.settings, overlay_text_color=value))
                image = self.render()
                icon, number = self.hud.cells["food"]
                for label, color in ((icon, "#e7d4a2"), (number, "#f4f0e4")):
                    self.assert_label_color(label, color)
                    self.assert_glyph_pixels(image, label, color)

    def test_picture_pixels_stay_original_while_number_changes_color(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "resource.png")
            picture = QImage(24, 24, QImage.Format_ARGB32)
            picture.fill(QColor("#3e8bb6"))
            self.assertTrue(picture.save(path))
            self.hud.renderer.picture = path
            self.hud.set_appearance(self.settings)
            self.hud.set_step({"resources": {"food": 24}, "age": 3}, 24)
            self.hud.show_for_screen(_Screen())
            image = self.render()
            for key in ("food", "age"):
                icon, number = self.hud.cells[key]
                self.assert_glyph_pixels(image, icon, "#3e8bb6")
                self.assertFalse(icon.pixmap().isNull())
                self.assertEqual(icon.text(), "")
                if key == "food":
                    self.assert_glyph_pixels(image, number, "#3bd9a7")
                else:
                    self.assertTrue(number.isHidden(), "时代图片不额外复制数字")
            self.hud.set_appearance(dict(self.settings, overlay_text_color="#d954a9"))
            image = self.render()
            self.assert_glyph_pixels(image, self.hud.cells["food"][0], "#3e8bb6")
            self.assert_glyph_pixels(image, self.hud.cells["food"][1], "#d954a9")

    def test_custom_text_does_not_recolor_the_gold_side_rail_decoration(self):
        self.hud.set_style("c")
        self.hud.set_step({"resources": {"food": 24, "wood": 12}}, 24)
        self.hud.show_for_screen(_Screen())
        settings = dict(self.settings, opacity=65, overlay_text_color="")
        self.hud.set_appearance(settings)
        original = self.render()
        icon, value = self.hud.cells["food"]
        x, y = value.geometry().right() + 9, icon.y() + icon.height() // 2
        ratio = original.devicePixelRatioF()
        baseline = original.pixelColor(round(x * ratio), round(y * ratio))
        self.assertGreater(baseline.red(), baseline.green())
        self.assertGreater(baseline.green(), baseline.blue())
        self.hud.set_appearance(dict(settings, overlay_text_color="#3bd9a7"))
        changed = self.render()
        self.assertEqual(changed.pixelColor(round(x * ratio), round(y * ratio)), baseline,
                         "自定义文字不改变C款的金色分隔装饰")


if __name__ == "__main__":
    unittest.main()
