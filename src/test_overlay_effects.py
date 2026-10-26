"""悬浮小字暗影：原字形不移位、不变暗，暗影不封闭字腔。"""
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import QApplication, QLabel, QWidget

from overlay_effects import HudOutlineEffect, install_hud_outline


class OverlayTextEffectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))

    def sample(self, font_size, effect):
        canvas = QWidget()
        canvas.setAttribute(Qt.WA_TranslucentBackground)
        canvas.setFixedSize(300, 70)
        label = QLabel("口 日 田 回 明 中　00:00　吃肉村民", canvas)
        label.setGeometry(20, 20, 270, 40)
        label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        label.setStyleSheet(f"background:transparent;color:#ffffff;font-family:'Microsoft YaHei UI';font-size:{font_size}px;")
        if effect:
            install_hud_outline(label)
        canvas.show()
        self.application.processEvents()
        image = canvas.grab().toImage()
        canvas.hide()
        canvas.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        return image

    def test_install_is_idempotent_and_shadow_stays_local(self):
        label = QLabel("村民")
        install_hud_outline(label)
        first = label.graphicsEffect()
        install_hud_outline(label)
        self.assertIs(first, label.graphicsEffect())
        self.assertIsInstance(first, HudOutlineEffect)
        self.assertLessEqual(first.blurRadius(), 3)
        self.assertLess(first.color().alpha(), 200)
        self.assertEqual(first.xOffset(), 0)
        self.assertLessEqual(first.yOffset(), 1)
        label.deleteLater()

    def test_bright_foreground_keeps_exact_position_at_small_fonts(self):
        for size in (8, 12, 18):
            with self.subTest(font_size=size):
                original, shadowed = self.sample(size, False), self.sample(size, True)
                original_bright, shadowed_bright = set(), set()
                for y in range(original.height()):
                    for x in range(original.width()):
                        before, after = original.pixelColor(x, y), shadowed.pixelColor(x, y)
                        if before.alpha() >= 180 and min(before.getRgb()[:3]) >= 250:
                            original_bright.add((x, y))
                        if after.alpha() >= 180 and min(after.getRgb()[:3]) >= 180:
                            shadowed_bright.add((x, y))
                self.assertGreater(len(original_bright), 5)
                # 150% 显示缩放时，原生文字和特效离屏纹理的光栅相位可差 1 个物理像素。
                # 检查每个明亮笔画附近仍有明亮前景，且没有超出这个范围的第二份浅色字形。
                for x, y in original_bright:
                    self.assertTrue(any((x + dx, y + dy) in shadowed_bright for dx in (-1, 0, 1) for dy in (-1, 0, 1)))
                for x, y in shadowed_bright:
                    self.assertTrue(any(original.pixelColor(x + dx, y + dy).alpha() > 0 for dx in (-1, 0, 1) for dy in (-1, 0, 1)))

    def test_soft_shadow_does_not_fill_clear_centers_with_opaque_black(self):
        original, shadowed = self.sample(12, False), self.sample(12, True)
        blank_points, opaque_added = 0, 0
        for y in range(1, original.height() - 1):
            for x in range(1, original.width() - 1):
                if original.pixelColor(x, y).alpha() == 0:
                    blank_points += 1
                    after = shadowed.pixelColor(x, y)
                    opaque_added += after.alpha() >= 180 and max(after.getRgb()[:3]) < 40
        self.assertGreater(blank_points, 100)
        self.assertEqual(opaque_added, 0, "无字区域只允许轻暗影，不能铺出厚黑边")
        self.assertEqual(shadowed.pixelColor(2, 2).alpha(), 0, "不引入整窗底色")


if __name__ == "__main__":
    unittest.main()
