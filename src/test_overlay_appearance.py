"""悬浮外观的实际像素回归：背景颜色／透明度独立于文字及黑色柔影。"""
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QRect, Qt
from PyQt5.QtGui import QColor, QFontDatabase
from PyQt5.QtWidgets import QApplication, QLabel, QWidget

from overlay_effects import configure_hud_shadow, style_backdrop
from overlay_goals import GoalHUD


class _Screen:
    """固定逻辑屏幕，避免测试触碰用户现有窗口。"""

    def availableGeometry(self):
        return QRect(0, 0, 1600, 900)


class _Renderer:
    """缺失图标时使用现有中文回退文字，不访问真实资源目录。"""

    def image_path(self, _token):
        return None


class OverlayAppearanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))

    def setUp(self):
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            if isinstance(window, GoalHUD):
                window.cleanup()
            window.close()
            window.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()

    @staticmethod
    def pixels(image):
        """遍历物理像素，覆盖高显示缩放而非只取逻辑坐标。"""
        return (image.pixelColor(x, y) for y in range(image.height()) for x in range(image.width()))

    @staticmethod
    def color_at(image, x, y):
        ratio = image.devicePixelRatioF()
        return image.pixelColor(round(x * ratio), round(y * ratio))

    def snapshot(self, widget):
        self.application.processEvents()
        return widget.grab().toImage()

    def make_goal(self):
        hud = GoalHUD(_Renderer())
        self.windows.append(hud)
        hud.set_step({"resources": {"food": 24, "wood": 12, "gold": 6}})
        hud.show_for_screen(_Screen())
        return hud

    def goal_gap(self, hud, image):
        """取第一项与第二项之间的空白，排除字形和其柔影。"""
        first, second = hud.rows[0][:2]
        last = hud.cells[first][1].geometry().right()
        following = hud.cells[second][0].geometry().left()
        return self.color_at(image, (last + following) // 2, hud.height() // 2)

    def test_zero_opacity_clears_every_background_pixel_in_all_styles(self):
        for style in ("a", "c", "b"):
            for goal in (False, True):
                for ratio in (1, 1.5):
                    with self.subTest(style=style, goal=goal, ratio=ratio):
                        image = style_backdrop(180, 70, ratio, style, goal=goal,
                                               background_color="#914d34", opacity=0).toImage()
                        self.assertEqual(image.devicePixelRatioF(), ratio)
                        self.assertFalse(any(color.alpha() for color in self.pixels(image)),
                                         "零不透明度必须连同圆角边线完全清空")

    def test_selected_color_and_percentage_change_rendered_material(self):
        target = QColor("#285578")
        for style in ("a", "c", "b"):
            with self.subTest(style=style):
                low = style_backdrop(180, 70, style=style, background_color=target, opacity=25).toImage()
                high = style_backdrop(180, 70, style=style, background_color=target, opacity=100).toImage()
                first, second = self.color_at(low, 90, 35), self.color_at(high, 90, 35)
                self.assertGreater(first.alpha(), 0)
                self.assertAlmostEqual(second.alpha(), first.alpha() * 4, delta=4,
                                       msg="百分比应真实改变背景通道而非整窗不透明度")
                for actual in (first, second):
                    for channel, expected in zip(actual.getRgb()[:3], target.getRgb()[:3]):
                        self.assertAlmostEqual(channel, expected, delta=2)
                self.assertEqual(high.pixelColor(0, 0).alpha(), 0, "保留原有边缘形状")
                if style != "b":
                    self.assertEqual(high.pixelColor(1, 35).alpha(), 0)
                    self.assertLess(high.pixelColor(3, 35).alpha(), second.alpha(), "字幕仍保留横向渐隐")
                else:
                    self.assertEqual(second.alpha(), 255, "完整面板的100%背景可真实不透明")

    def test_no_argument_legacy_material_remains_compatible(self):
        expected = {"a": 170, "b": 237}
        for style, alpha in expected.items():
            image = style_backdrop(180, 70, style=style).toImage()
            self.assertEqual(self.color_at(image, 90, 35).alpha(), alpha)
        image = style_backdrop(180, 70, style="c").toImage()
        self.assertGreater(self.color_at(image, 90, 35).alpha(), 170)

    def test_subtitle_glow_is_elliptical_and_distinct_from_the_side_rail_style(self):
        image = style_backdrop(400, 240, style="a", background_color="#101b17", opacity=100).toImage()
        center = self.color_at(image, 240, 84).alpha()
        self.assertGreater(center, 250, "100%表示光晕中心真实不透明")
        self.assertLess(self.color_at(image, 40, 200).alpha(), center * .35,
                        "左下角随椭圆距离渐隐，不残留整块矩形底衬")
        self.assertLess(self.color_at(image, 240, 200).alpha(), center * .8)
        self.assertEqual(image.pixelColor(1, 120).alpha(), 0)
        rail = style_backdrop(400, 240, style="c", background_color="#101b17", opacity=100).toImage()
        self.assertNotEqual(image, rail, "字幕光晕与时间侧轨保留不同材质")

    def test_shadow_strength_changes_real_black_pixels_and_can_be_disabled(self):
        canvas = QWidget()
        self.windows.append(canvas)
        canvas.setAttribute(Qt.WA_TranslucentBackground)
        canvas.setFixedSize(260, 70)
        text = QLabel("田 回 明 中　24 12 6", canvas)
        text.setGeometry(15, 15, 230, 45)
        text.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        text.setStyleSheet("background:transparent;color:#ffffff;font-family:'Microsoft YaHei UI';font-size:24px;")
        canvas.show()
        effect = configure_hud_shadow(text, False, 100)
        plain = self.snapshot(canvas)
        self.assertIs(effect, configure_hud_shadow(text, True, 20))
        faint = self.snapshot(canvas)
        self.assertIs(effect, configure_hud_shadow(text, True, 100))
        strong = self.snapshot(canvas)
        self.assertIs(effect, configure_hud_shadow(text, False, 100))
        disabled = self.snapshot(canvas)
        configure_hud_shadow(text, True, 0)
        zero = self.snapshot(canvas)
        # 只比较原本全透明的区域，白字抗锯齿本身不计为柔影。
        def darkness(image):
            total = 0
            for y in range(plain.height()):
                for x in range(plain.width()):
                    if plain.pixelColor(x, y).alpha() == 0:
                        pixel = image.pixelColor(x, y)
                        if pixel.alpha():
                            self.assertEqual(pixel.getRgb()[:3], (0, 0, 0), "新增柔影是纯黑色")
                            total += pixel.alpha()
            return total
        self.assertGreater(darkness(faint), 0)
        self.assertGreater(darkness(strong), darkness(faint) * 2)
        self.assertEqual(darkness(disabled), 0)
        self.assertEqual(darkness(zero), 0)
        self.assertEqual(plain, disabled)
        self.assertEqual(plain, zero)
        # Qt在150%缩放下会改变特效纹理的光栅相位，因此允许1物理像素的笔画偏移，
        # 仍要求每个原始亮笔画附近有不透明的明亮前景，而非把字形一同淡化。
        for y in range(1, plain.height() - 1):
            for x in range(1, plain.width() - 1):
                pixel = plain.pixelColor(x, y)
                if pixel.alpha() >= 180 and min(pixel.getRgb()[:3]) >= 240:
                    self.assertTrue(any(strong.pixelColor(x + dx, y + dy).alpha() >= 180
                                        and min(strong.pixelColor(x + dx, y + dy).getRgb()[:3]) >= 180
                                        for dx in (-1, 0, 1) for dy in (-1, 0, 1)))
        self.assertEqual(canvas.windowOpacity(), 1.0)

    def test_goal_appearance_updates_pixels_without_changing_targets_or_input(self):
        hud = self.make_goal()
        self.assertEqual(hud._font_size, 14)
        for style in ("a", "c", "b"):
            with self.subTest(style=style):
                hud.set_style(style)
                before_geometry, before_values = hud.geometry(), dict(hud.values)
                before_flags = hud.windowFlags()
                hud.set_appearance({"overlay_color": "#285578", "opacity": 65, "text_shadow": False})
                blue = self.snapshot(hud)
                blue_gap = self.goal_gap(hud, blue)
                self.assertGreater(blue_gap.alpha(), 70)
                self.assertGreater(blue_gap.blue(), blue_gap.red())
                hud.set_appearance({"overlay_color": "#923b2b", "opacity": 25, "text_shadow": False})
                red = self.snapshot(hud)
                red_gap = self.goal_gap(hud, red)
                self.assertLess(red_gap.alpha(), blue_gap.alpha())
                self.assertGreater(red_gap.red(), red_gap.blue() * 2)
                hud.set_appearance({"overlay_color": "#923b2b", "opacity": 0, "text_shadow": False})
                clear = self.snapshot(hud)
                self.assertEqual(self.goal_gap(hud, clear).alpha(), 0,
                                 "零背景也移除时间栏款式中的金色分隔线")
                self.assertGreater(sum(c.alpha() > 180 and min(c.getRgb()[:3]) >= 180
                                       for c in self.pixels(clear)), 20,
                                   "背景全透明时，目标数字及图标回退文字仍清晰")
                self.assertEqual(hud.geometry(), before_geometry)
                self.assertEqual(hud.values, before_values)
                self.assertEqual(hud.windowFlags(), before_flags)
                self.assertTrue(hud.windowFlags() & Qt.WindowTransparentForInput)
                self.assertTrue(hud.isVisible())
                self.assertEqual(hud.windowOpacity(), 1.0)

    def test_goal_dynamic_shadow_reuses_effect_and_repaints(self):
        hud = self.make_goal()
        values = {"opacity": 0, "text_shadow": False, "text_shadow_strength": 100}
        hud.set_appearance(values)
        clear = self.snapshot(hud)
        effect = hud.cells["food"][1].graphicsEffect()
        hud.set_appearance(dict(values, text_shadow=True))
        shaded = self.snapshot(hud)
        self.assertIs(hud.cells["food"][1].graphicsEffect(), effect)
        added_black = sum(before.alpha() == 0 and after.alpha() > 0 and max(after.getRgb()[:3]) == 0
                          for before, after in zip(self.pixels(clear), self.pixels(shaded)))
        self.assertGreater(added_black, 20, "目标栏阴影设置须改变实际字形周围的像素")
        hud.set_appearance(dict(values, text_shadow=True, text_shadow_strength=0))
        self.assertEqual(self.snapshot(hud), clear)
        self.assertIs(hud.cells["food"][1].graphicsEffect(), effect)
        self.assertFalse(effect.isEnabled())


if __name__ == "__main__":
    unittest.main()
