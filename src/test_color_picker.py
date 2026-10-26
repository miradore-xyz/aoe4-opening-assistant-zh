"""真实Qt选色交互：取消不提交、色值与拖动一致、无系统标题栏。"""
import os
import re
import unittest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import QPoint, QTimer, Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QPushButton, QStyle, QStyleOptionButton
from color_picker import ColorPickerDialog
from visual import STYLE


class ColorPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.quit_policy = cls.application.quitOnLastWindowClosed()
        cls.application.setQuitOnLastWindowClosed(False)
        # 整套测试共用 QApplication（应用对象）；先消化上一窗口留下的退出事件，
        # 避免它抢先结束本测试的模态事件循环，使计划中的点击落到下一个窗口。
        QTimer.singleShot(0, cls.application.quit)
        cls.application.exec_()

    @classmethod
    def tearDownClass(cls):
        cls.application.setQuitOnLastWindowClosed(cls.quit_policy)

    def test_hex_and_plane_update_same_selected_color(self):
        dialog = ColorPickerDialog(QColor("#1b1b1e"))
        try:
            dialog.show(); self.application.processEvents()
            dialog.hex_input.setText("#ed5082")
            self.assertEqual(dialog._color.name(), "#ed5082")
            self.assertTrue(dialog.accept_button.isEnabled())
            dialog.hex_input.setText("#xyz")
            self.assertFalse(dialog.accept_button.isEnabled())
            QTest.mouseClick(dialog.plane, Qt.LeftButton, pos=QPoint(dialog.plane.width()-2, 2))
            self.assertTrue(dialog.accept_button.isEnabled())
            self.assertGreater(dialog._color.valueF(), .97)
            self.assertGreater(dialog._color.hsvSaturationF(), .97)
            self.assertTrue(dialog.windowFlags() & Qt.FramelessWindowHint)
            self.assertEqual(dialog.cancel_button.text(), "取消")
        finally:
            dialog.reject(); dialog.deleteLater(); self.application.processEvents()

    def test_modal_cancel_preserves_original_and_confirm_returns_selection(self):
        interactions = []
        def cancel():
            dialog = self.application.activeModalWidget()
            dialog.hex_input.setText("#126aa5")
            interactions.append("cancel")
            dialog.reject()
        original = QColor("#1b1b1e")
        QTimer.singleShot(0, cancel)
        self.assertFalse(ColorPickerDialog.getColor(original).isValid())
        self.assertEqual(interactions, ["cancel"], "应当实际执行取消，而非被旧退出事件关闭")
        self.assertEqual(original.name(), "#1b1b1e")
        def confirm():
            dialog = self.application.activeModalWidget()
            dialog.hex_input.setText("126aa5")
            interactions.append("confirm")
            dialog.accept_button.click()
        QTimer.singleShot(0, confirm)
        self.assertEqual(ColorPickerDialog.getColor(original).name(), "#126aa5")
        self.assertEqual(interactions, ["cancel", "confirm"])

    def test_minimum_dialog_keeps_scaled_text_inside_controls(self):
        """18像素界面字号时，当前色按钮的文字区随字体扩展而不被固定尺寸挤压。"""
        previous_style = self.application.styleSheet()
        previous_font = self.application.font()
        scaled = re.sub(r'font-size:\s*([0-9.]+)px',
                        lambda m: f'font-size:{max(11, round(float(m[1]) * 18 / 14))}px', STYLE)
        self.application.setStyleSheet(scaled)
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(18)
        self.application.setFont(font)
        dialog = ColorPickerDialog(QColor("#1b1b1e"))
        try:
            dialog.resize(400, 470)
            dialog.show()
            self.application.processEvents()
            current = next(button for button in dialog.findChildren(QPushButton)
                           if button.accessibleName() == "恢复当前颜色")
            option = QStyleOptionButton()
            option.initFrom(current)
            option.text = current.text()
            area = current.style().subElementRect(QStyle.SE_PushButtonContents, option, current)
            self.assertGreaterEqual(area.width(), current.fontMetrics().horizontalAdvance(current.text()))
            self.assertGreaterEqual(area.height(), current.fontMetrics().height())
            self.assertLessEqual(dialog.hex_input.geometry().right(), dialog.hex_input.parentWidget().width())
            self.assertLessEqual(dialog.accept_button.geometry().bottom(), dialog.accept_button.parentWidget().height())
        finally:
            dialog.reject()
            dialog.deleteLater()
            self.application.processEvents()
            self.application.setStyleSheet(previous_style)
            self.application.setFont(previous_font)


if __name__ == "__main__":
    unittest.main()
