"""透明 HUD（游戏提示层）：轻暗影、独立解锁、三阶段正文和多宽度字号。"""
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QFontDatabase, QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from app import STYLE
from core import timed_ids
from overlay_effects import HudOutlineEffect
from test_overlay_controls import Panel
from test_overlay_stages import grouped_order


class TransparentOverlayHudTests(unittest.TestCase):
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
        self.panel.order = grouped_order(self.panel.order["build_order"][0])
        self.panel.settings.update(width=400, height=220, font_size=16, opacity=0, passthrough=False)
        self.panel.ids = timed_ids(self.panel.order, 60, starting=True)
        self.overlay = self.panel.overlay
        self.overlay.apply_settings()
        self.overlay.show()
        self.overlay.render()
        self.application.processEvents()

    def tearDown(self):
        self.overlay.resize_save.stop()
        self.overlay.lock_handle.cleanup()
        self.overlay.hide()
        self.overlay.deleteLater()
        self.panel.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_independent_lock_handle_can_unlock_the_body_while_the_body_stays_click_through(self):
        self.assertFalse(self.overlay.windowFlags() & Qt.WindowTransparentForInput)
        handle = self.overlay.lock_handle
        self.assertEqual(handle.focusPolicy(), Qt.StrongFocus)
        QTest.mouseClick(handle, Qt.LeftButton)
        self.assertTrue(self.panel.settings["passthrough"])
        self.assertTrue(self.overlay.windowFlags() & Qt.WindowTransparentForInput)
        self.assertTrue(self.overlay.windowFlags() & Qt.WindowDoesNotAcceptFocus)
        self.assertFalse(handle.windowFlags() & Qt.WindowTransparentForInput)
        self.assertEqual(handle.text(), "")
        self.assertIn("已锁定", handle.accessibleName())
        QTest.mouseClick(handle, Qt.LeftButton)
        self.assertFalse(self.panel.settings["passthrough"])
        self.assertFalse(self.overlay.windowFlags() & Qt.WindowTransparentForInput)
        self.assertFalse(self.overlay.windowFlags() & Qt.WindowDoesNotAcceptFocus)
        self.assertEqual(handle.text(), "")

    def test_unlocked_title_drags_and_locked_title_does_not_start_drag(self):
        title = self.overlay.title
        old = self.overlay.pos()
        press = QMouseEvent(QEvent.MouseButtonPress, QPointF(4, 4), QPointF(title.mapToGlobal(QPoint(4, 4))), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        self.application.sendEvent(title, press)
        self.assertIsNotNone(self.overlay.drag)
        move = QMouseEvent(QEvent.MouseMove, QPointF(14, 14), QPointF(title.mapToGlobal(QPoint(14, 14))), Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        self.application.sendEvent(title, move)
        self.assertNotEqual(self.overlay.pos(), old)
        self.panel.change_passthrough(True)
        self.assertIsNone(self.overlay.drag)
        self.application.sendEvent(title, press)
        self.assertIsNone(self.overlay.drag)

    def test_configured_opacity_keeps_one_faded_backdrop_and_opaque_text(self):
        self.panel.settings["opacity"] = 65
        self.overlay.apply_settings()
        self.overlay.render()
        self.application.processEvents()
        self.assertEqual(self.overlay.windowOpacity(), 1.0)
        image = self.overlay.grab().toImage()
        self.assertEqual(image.pixelColor(1, 1).alpha(), 0)
        bright = 0
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                bright += color.alpha() > 220 and min(color.getRgb()[:3]) > 180
        self.assertGreater(bright, 30)
        # 单一暗衬可覆盖正文之间的空隙；透明边缘与柔和尾部仍须真实存在。
        backdrop = self.overlay.content_backdrop(self.overlay.width(), self.overlay.height()).toImage()
        ratio = backdrop.devicePixelRatioF()
        def alpha_at(x, y):
            return backdrop.pixelColor(round(x * ratio), round(y * ratio)).alpha()
        middle = self.overlay.width() // 2
        center_alpha = alpha_at(middle, self.overlay.height() // 2)
        self.assertGreater(center_alpha, 40)
        self.assertLessEqual(center_alpha, 166)
        self.assertEqual(alpha_at(1, 1), 0)
        self.assertEqual(alpha_at(self.overlay.width() - 2, self.overlay.height() // 2), 0)
        amounts = [alpha_at(middle, y) for y in range(20, self.overlay.height() - 20)]
        self.assertLess(max(abs(a - b) for a, b in zip(amounts, amounts[1:])), 8,
                        "椭圆暗衬连续渐隐，不按每行文字反复明暗交替")
        tail_alpha = alpha_at(middle, self.overlay.height() - 6)
        self.assertGreater(tail_alpha, 0)
        self.assertLess(tail_alpha, center_alpha)
        self.assertFalse(self.overlay.resources.isVisible())
        body = self.overlay.notes.text()
        self.assertNotIn("background-color", body)
        self.assertFalse("data-stage-resources" in body, "资源目标交给独立顶部目标栏，正文不重复占空间")
        for role in ("current", "previous", "previous_previous"):
            self.assertIn(f'data-stage="{role}"', body)
        current, previous = body.split('<div data-stage="previous">', 1)
        self.assertIn("<img", current, "当前指令中的图标仍应直接渲染")
        self.assertIn("<img", previous, "历史说明中的图标仍应直接渲染")

    def test_light_shadow_preserves_readable_text_without_shadowing_the_gold_lock(self):
        for widget in (self.overlay.title, self.overlay.clock_label, self.overlay.notes):
            self.assertIsInstance(widget.graphicsEffect(), HudOutlineEffect)
            self.assertLessEqual(widget.graphicsEffect().blurRadius(), 3)
            self.assertLessEqual(abs(widget.graphicsEffect().offset().y()), 1)
        self.assertIsNone(self.overlay.lock_handle.graphicsEffect())
        self.assertIsNone(self.overlay.start_button.graphicsEffect())
        self.assertNotIn("text-shadow", self.overlay.notes.styleSheet())

    def test_logical_width_and_font_values_stay_bounded_without_persisting_auto_height(self):
        for width, font in ((280, 8), (330, 12), (500, 14), (330, 18)):
            with self.subTest(width=width, font=font):
                self.panel.settings.update(width=width, height=220, font_size=font)
                self.overlay.apply_settings()
                self.overlay.render()
                self.application.processEvents()
                area = QApplication.primaryScreen().availableGeometry()
                self.assertLessEqual(self.overlay.width(), area.width())
                self.assertLessEqual(self.overlay.height(), area.height())
                self.assertIn(f"font-size:{font}px", self.overlay.notes.text())
                self.assertEqual(self.panel.settings["height"], 220)
                self.assertFalse(self.overlay.resize_save.isActive())
                self.assertLessEqual(self.overlay.clock_label.mapTo(self.overlay, QPoint(self.overlay.clock_label.width(), 0)).x(), self.overlay.lock_anchor.mapTo(self.overlay, QPoint()).x())


if __name__ == "__main__":
    unittest.main()
