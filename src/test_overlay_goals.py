"""顶部目标条：当前阶段已知值、穿透、跨屏居中、窄屏换行与清理。"""
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QRect, Qt
from PyQt5.QtGui import QColor, QFontDatabase, QImage
from PyQt5.QtWidgets import QApplication

from overlay_goals import GoalHUD


class Screen:
    def __init__(self, geometry):
        self.area = QRect(*geometry)

    def availableGeometry(self):
        return QRect(self.area)


class Renderer:
    def __init__(self, path):
        self.path = path
        self.paths = []

    def image_path(self, token):
        self.paths.append(token)
        return self.path


class GoalHudTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))
        cls.directory = tempfile.TemporaryDirectory()
        cls.icon_path = str(Path(cls.directory.name) / "resource.png")
        picture = QImage(24, 24, QImage.Format_ARGB32)
        picture.fill(QColor("#cbb267"))
        picture.save(cls.icon_path)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.renderer = Renderer(self.icon_path)
        self.hud = GoalHUD(self.renderer)
        self.screen = Screen((0, 0, 1920, 1080))

    def tearDown(self):
        self.hud.cleanup()
        self.hud.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)

    @staticmethod
    def full_step():
        return {"resources": {"food": 20, "wood": 10, "gold": 6, "stone": 0, "builder": 1},
                "villager_count": 37, "population_count": 50, "age": 3}

    def test_shows_only_known_current_targets_and_keeps_zero(self):
        self.assertFalse(self.hud.set_step({"resources": {"food": -1}}))
        self.hud.show_for_screen(self.screen)
        self.assertFalse(self.hud.isVisible())
        self.assertTrue(self.hud.set_step(self.full_step(), 12))
        self.assertFalse(self.hud.isVisible(), "更新值不私自打开已隐藏的宿主目标条")
        self.hud.show_for_screen(self.screen)
        self.assertTrue(self.hud.isVisible())
        self.assertEqual(self.hud.values["stone"], 0)
        self.assertEqual(self.hud.cells["age"][1].text(), "")
        self.assertTrue(self.hud.cells["age"][1].isHidden())
        self.assertIn("age/age_3.webp", self.renderer.paths)
        self.assertIn("当前阶段目标", self.hud.accessibleName())
        self.assertFalse(hasattr(self.hud, "prefix"), "目标含义留在无障碍说明，不额外占一段文字")
        self.hud.set_step({"resources": {"wood": 4}, "age": -1})
        self.assertEqual(self.hud.values, {"wood": 4}, "前一阶段目标不会累积进来")
        self.assertTrue(self.hud.cells["food"][0].isHidden())
        self.hud.set_step({})
        self.assertFalse(self.hud.isVisible())

    def test_remains_mouse_passthrough_without_focus_or_opaque_window(self):
        for flag in (Qt.Tool, Qt.FramelessWindowHint, Qt.WindowStaysOnTopHint,
                     Qt.WindowTransparentForInput, Qt.WindowDoesNotAcceptFocus):
            self.assertTrue(self.hud.windowFlags() & flag)
        self.assertEqual(self.hud.focusPolicy(), Qt.NoFocus)
        self.assertTrue(self.hud.testAttribute(Qt.WA_TransparentForMouseEvents))
        self.hud.set_step(self.full_step())
        self.hud.show_for_screen(self.screen)
        self.application.processEvents()
        image = self.hud.grab().toImage()
        self.assertEqual(self.hud.windowOpacity(), 1.0)
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        # 新目标条是一条连贯窄衬，项目之间也应连续；四边仍透明，不能变为不透明底板。
        ratio = image.devicePixelRatioF()
        def color_at(x, y):
            return image.pixelColor(round(x * ratio), round(y * ratio))
        self.assertEqual(color_at(self.hud.width() // 2, 1).alpha(), 0)
        gaps = []
        for first, second in zip(self.hud.rows[0], self.hud.rows[0][1:]):
            first_icon, first_value = self.hud.cells[first]
            last = first_icon.geometry().right() if first_value.isHidden() else first_value.geometry().right()
            following = self.hud.cells[second][0].geometry().left()
            gaps.append(color_at((last + following) // 2, self.hud.height() // 2))
        self.assertGreaterEqual(len(gaps), 3)
        for color in gaps:
            self.assertGreater(color.alpha(), 0, "各项之间连续渐隐，椭圆暗衬不要求处处同浓度")
            self.assertLess(color.alpha(), 190, "A字幕样式保持轻暗衬，数字和图标独立清晰")
            self.assertLess(max(color.getRgb()[:3]), 60)

    def test_screen_center_top_wrap_and_repeat_sync_use_logical_pixels(self):
        for size, geometry in ((8, (0, 0, 1920, 1080)), (12, (-1280, -200, 1280, 720)),
                               (18, (200, 0, 280, 300))):
            with self.subTest(font=size, geometry=geometry):
                screen = Screen(geometry)
                self.hud.set_step(self.full_step(), size)
                self.hud.show_for_screen(screen)
                self.application.processEvents()
                first = self.hud.geometry()
                self.assertTrue(screen.area.contains(first))
                self.assertLessEqual(abs(first.center().x() - screen.area.center().x()), 1)
                expected_top = screen.area.top() + max(72, round(screen.area.height() * .12))
                self.assertEqual(first.top(), min(expected_top, screen.area.bottom() - first.height() + 1))
                for _ in range(5):
                    self.hud.sync_screen(screen)
                self.assertEqual(self.hud.geometry(), first, "反复同步不得再次乘显示倍率")
                for key in self.hud.values:
                    for child in self.hud.cells[key]:
                        if not child.isHidden():
                            self.assertTrue(self.hud.rect().contains(child.geometry()))
                if geometry[2] == 280:
                    self.assertGreater(len(self.hud.rows), 1)

    def test_cleanup_is_idempotent_and_hidden_sync_does_not_reopen(self):
        self.hud.set_step(self.full_step())
        self.hud.show_for_screen(self.screen)
        self.hud.hide()
        self.hud.sync_screen(self.screen)
        self.assertFalse(self.hud.isVisible())
        self.hud.cleanup()
        self.hud.cleanup()
        self.hud.show_for_screen(self.screen)
        self.assertFalse(self.hud.isVisible())
        self.assertFalse(self.hud.set_step(self.full_step()))

    def test_missing_age_picture_keeps_a_readable_roman_fallback(self):
        self.renderer.path = None
        self.hud.set_step({"age": 2})
        self.hud.show_for_screen(self.screen)
        self.assertEqual(self.hud.cells["age"][1].text(), "II")
        self.assertFalse(self.hud.cells["age"][1].isHidden())


if __name__ == "__main__":
    unittest.main()
