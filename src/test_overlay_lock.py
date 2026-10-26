"""独立锁柄只接收小锁槽的输入；主体穿透、跟随位置和退出生命周期不互相干扰。"""
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import sip
from PyQt5.QtCore import QEvent, QPoint, QRect, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import QApplication, QWidget
from overlay_lock import OverlayLockHandle


class OverlayLockHandleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        for name in ("msyh.ttc", "msyhbd.ttc"):
            path = Path("C:/Windows/Fonts") / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))

    def setUp(self):
        self.overlay = QWidget()
        self.overlay.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint |
                                    Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput)
        self.overlay.setGeometry(180, 120, 360, 180)
        self.anchor = QWidget(self.overlay)
        self.anchor.setGeometry(280, 10, 70, 26)
        self.activations = 0
        self.handle = OverlayLockHandle(self.overlay, self.anchor, self.activated)
        self.overlay.show()
        QTest.qWait(20)

    def tearDown(self):
        self.handle.cleanup()
        if not sip.isdeleted(self.overlay):
            self.overlay.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()

    def activated(self):
        self.activations += 1

    def slot_rect(self):
        return QRect(self.anchor.mapToGlobal(QPoint()), self.anchor.size())

    def test_only_the_lock_slot_accepts_input_and_body_flags_and_position_stay_unchanged(self):
        handle = self.handle
        self.assertTrue(handle.isWindow())
        self.assertTrue(handle.windowFlags() & Qt.FramelessWindowHint)
        self.assertTrue(handle.windowFlags() & Qt.WindowStaysOnTopHint)
        self.assertTrue(handle.testAttribute(Qt.WA_ShowWithoutActivating))
        self.assertFalse(handle.windowFlags() & Qt.WindowTransparentForInput)
        self.assertFalse(handle.testAttribute(Qt.WA_TransparentForMouseEvents))
        self.assertTrue(self.overlay.windowFlags() & Qt.WindowTransparentForInput)
        self.assertTrue(self.slot_rect().contains(handle.geometry()))
        self.assertEqual((handle.width(), handle.height()), (28, 26))
        position = self.overlay.pos()
        QTest.mouseClick(handle, Qt.LeftButton)
        self.assertEqual(self.activations, 1)
        self.assertEqual(self.overlay.pos(), position)
        self.assertTrue(self.overlay.windowFlags() & Qt.WindowTransparentForInput)

    def test_host_and_anchor_moves_resize_the_handle_without_moving_the_body(self):
        self.overlay.move(310, 210)
        QTest.qWait(20)
        self.assertTrue(self.slot_rect().contains(self.handle.geometry()))
        body = self.overlay.geometry()
        self.anchor.setGeometry(120, 40, 40, 24)
        QTest.qWait(20)
        self.assertEqual(self.overlay.geometry(), body)
        self.assertEqual((self.handle.width(), self.handle.height()), (28, 24))
        self.assertTrue(self.slot_rect().contains(self.handle.geometry()))
        self.overlay.resize(480, 220)
        QTest.qWait(20)
        self.assertTrue(self.slot_rect().contains(self.handle.geometry()))

    def test_hide_close_and_show_follow_both_the_overlay_and_anchor(self):
        self.assertTrue(self.handle.isVisible())
        self.overlay.hide()
        self.assertFalse(self.handle.isVisible())
        self.overlay.show()
        QTest.qWait(20)
        self.assertTrue(self.handle.isVisible())
        self.anchor.hide()
        self.assertFalse(self.handle.isVisible())
        self.anchor.show()
        QTest.qWait(20)
        self.assertTrue(self.handle.isVisible())
        self.overlay.close()
        self.assertFalse(self.handle.isVisible())
        self.overlay.show()
        QTest.qWait(20)
        self.assertTrue(self.handle.isVisible())

    def test_current_state_icon_action_text_and_keyboard_activation_are_explicit(self):
        handle = self.handle
        handle.set_locked(True)
        locked = handle.grab().toImage()
        self.assertEqual(handle.text(), "")
        self.assertIn("当前已锁定", handle.toolTip())
        self.assertIn("解锁悬浮窗", handle.accessibleName())
        handle.set_locked(False)
        unlocked = handle.grab().toImage()
        self.assertNotEqual(locked, unlocked)
        self.assertEqual(handle.text(), "")
        self.assertIn("当前已解锁", handle.toolTip())
        self.assertEqual(handle.focusPolicy(), Qt.StrongFocus)
        QTest.keyClick(handle, Qt.Key_Space)
        QTest.keyClick(handle, Qt.Key_Return)
        self.assertEqual(self.activations, 2)

    def test_hover_and_press_animate_the_compact_icon_without_a_permanent_capsule(self):
        handle = self.handle
        handle.clearFocus()
        self.application.sendEvent(handle, QEvent(QEvent.Leave))
        QTest.qWait(140)
        normal = handle.grab().toImage()
        self.assertGreater(normal.pixelColor(2, 2).alpha(), 0,
                           "小锁空心及周围保持非零透明度命中区域，避免原生点击穿到游戏")
        self.assertLessEqual(normal.pixelColor(2, 2).alpha(), 1)
        self.application.sendEvent(handle, QEvent(QEvent.Enter))
        QTest.qWait(140)
        hovered = handle.grab().toImage()
        self.assertEqual(handle._animation.duration(), 120)
        self.assertGreater(handle._activity, .9)
        self.assertNotEqual(normal, hovered)
        QTest.mousePress(handle, Qt.LeftButton)
        QTest.qWait(140)
        pressed = handle.grab().toImage()
        self.assertGreater(handle._activity, 1.9)
        self.assertNotEqual(hovered, pressed)
        QTest.mouseRelease(handle, Qt.LeftButton)

    def test_gold_icons_render_visible_and_distinct_shapes_in_the_compact_slot(self):
        pictures = []
        for locked in (True, False):
            with self.subTest(locked=locked):
                self.handle.set_locked(locked)
                self.handle.clearFocus()
                self.application.sendEvent(self.handle, QEvent(QEvent.Leave))
                QTest.qWait(140)
                image = self.handle.grab().toImage()
                # 线稿含抗锯齿，按金色像素的覆盖率计量，不能套用原实心图的不透明面积。
                gold = sum(image.pixelColor(x, y).alpha() / 255
                           for y in range(image.height()) for x in range(image.width())
                           if image.pixelColor(x, y).red() > 150
                           and image.pixelColor(x, y).green() > 100
                           and image.pixelColor(x, y).red() > image.pixelColor(x, y).blue() + 30)
                pictures.append(image)
                self.assertGreater(gold, 30, "实际按钮必须绘出可辨认金色图形，不能只有透明命中框")
        self.assertNotEqual(*pictures, "开锁与闭锁必须以不同图形表达当前状态")

    def test_near_transparent_corner_still_belongs_to_the_lock_click_area(self):
        self.handle.clearFocus()
        self.application.sendEvent(self.handle, QEvent(QEvent.Leave))
        QTest.qWait(140)
        image = self.handle.grab().toImage()
        self.assertEqual(image.pixelColor(2, 2).alpha(), 1,
                         "未绘制图标的边角保留alpha=1，原生锁窗口不能出现鼠标命中空洞")
        self.assertFalse(self.handle.windowFlags() & Qt.WindowTransparentForInput)
        QTest.mouseClick(self.handle, Qt.LeftButton, pos=QPoint(2, 2))
        self.assertEqual(self.activations, 1)
        self.assertTrue(self.overlay.windowFlags() & Qt.WindowTransparentForInput)

    def test_cleanup_is_idempotent_and_late_host_events_cannot_leave_an_orphan(self):
        self.handle.cleanup()
        self.handle.cleanup()
        self.assertFalse(self.handle.isVisible())
        self.assertFalse(self.handle._sync_timer.isActive())
        self.overlay.move(500, 300)
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertTrue(sip.isdeleted(self.handle))
        self.handle.cleanup()

    def test_deleting_the_anchor_destroys_the_independent_handle(self):
        self.anchor.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertTrue(sip.isdeleted(self.handle))


if __name__ == "__main__":
    unittest.main()
