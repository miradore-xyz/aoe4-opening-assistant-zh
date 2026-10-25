"""数值与滑杆禁用滚轮、标题对齐和隐藏滚动条的实际控件验证。"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PyQt5.QtCore import Qt, QPoint, QPointF, QEvent
from PyQt5.QtGui import QFontDatabase, QWheelEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel, QSlider, QSpinBox
from app import Manager, STYLE
from choice_controls import ClickSlider, ClickSpinBox
from core import atomic_json


def wheel(widget, delta=-120):
    """发送实际滚轮事件，同时检查控件是否把事件交给父滚动区。"""
    local = widget.rect().center()
    event = QWheelEvent(QPointF(local), QPointF(widget.mapToGlobal(local)), QPoint(), QPoint(0, delta),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)
    QApplication.processEvents()


class SettingsInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            path = Path("C:/Windows/Fonts") / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        folder = Path(self.temporary.name)
        atomic_json(folder / "设置.json", {"voice_engine": "system", "hotkeys": {}, "speech": False})
        self.patches = [patch.object(Manager, "seed"), patch("app.Speech.start"),
                        patch("app.Manager.register_hotkeys"), patch("update_ui.UpdateController.check")]
        for replacement in self.patches:
            replacement.start()
        self.application.setStyleSheet(STYLE)
        self.manager = Manager(self.application, folder)
        self.manager.show()
        QTest.qWait(30)

    def tearDown(self):
        self.manager.tick_timer.stop()
        self.manager.hotkeys.close()
        self.manager.speech.close()
        self.manager.online.close()
        self.manager.overlay.hide()
        self.manager.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        for replacement in reversed(self.patches):
            replacement.stop()
        self.temporary.cleanup()

    def test_all_setting_numbers_and_volume_slider_ignore_wheel_even_with_focus(self):
        self.manager.navigate(1)
        numbers = self.manager.tabs.widget(1).findChildren(QSpinBox)
        sliders = self.manager.tabs.widget(1).findChildren(QSlider)
        self.assertEqual(len(numbers), 10)
        self.assertEqual(len(sliders), 1)
        for widget in numbers + sliders:
            self.assertIsInstance(widget, (ClickSpinBox, ClickSlider))
            before = widget.value()
            for focused in (False, True):
                if focused:
                    widget.setFocus()
                else:
                    widget.clearFocus()
                wheel(widget, 120)
                wheel(widget, -120)
                self.assertEqual(widget.value(), before)
        self.manager.volume_input.lineEdit().selectAll()
        QTest.keyClicks(self.manager.volume_input.lineEdit(), "73")
        self.assertEqual(self.manager.volume_input.value(), 73)
        slider = sliders[0]
        old = slider.value()
        QTest.mouseClick(slider, Qt.LeftButton, pos=QPoint(slider.width() - 4, slider.height() // 2))
        self.assertNotEqual(slider.value(), old)
        self.assertEqual(slider.value(), self.manager.volume_input.value())

    def test_ignored_wheel_scrolls_settings_and_hidden_bar_does_not_clip_content(self):
        self.manager.resize(760, 520)
        self.manager.navigate(1)
        QTest.qWait(30)
        scroll = self.manager.settings_scroll
        bar = scroll.verticalScrollBar()
        self.assertEqual(scroll.verticalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(0)
        slider = scroll.findChild(ClickSlider)
        wheel(slider)
        self.assertGreater(bar.value(), 0, "滚轮应滚动外层设置页")
        bar.setValue(bar.maximum())
        self.assertFalse(bar.isVisible())
        self.assertGreater(scroll.widget().height(), scroll.viewport().height())

    def test_primary_page_title_origins_match_library_at_both_window_sizes(self):
        for width, height in ((1000, 680), (760, 520)):
            self.manager.resize(width, height)
            self.application.processEvents()
            positions = []
            for index, title in ((0, "我的开局"), (3, "战绩分析"), (1, "设置"), (2, "使用帮助")):
                self.manager.navigate(index)
                self.application.processEvents()
                candidates = [item for item in self.manager.findChildren(QLabel)
                              if item.text() == title and item.isVisible()
                              and item.objectName() in ("PageTitle", "LibraryHeading")]
                self.assertEqual(len(candidates), 1, title)
                positions.append(candidates[0].mapTo(self.manager, QPoint(0, 0)))
            self.assertEqual(len({point.x() for point in positions}), 1, positions)
            self.assertLessEqual(max(point.y() for point in positions) - min(point.y() for point in positions), 1, positions)

    def test_successful_save_is_quiet_and_failed_save_still_shows_error(self):
        self.manager.apply_settings()
        self.assertEqual(self.manager.status_bar.text(), "")
        with patch("app.atomic_json", side_effect=OSError("测试无写入权限")):
            self.manager.apply_settings()
        self.assertIn("设置未能写入", self.manager.status_bar.text())


if __name__ == "__main__":
    unittest.main()
