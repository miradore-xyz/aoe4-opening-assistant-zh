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
from PyQt5.QtWidgets import QApplication, QLabel, QPushButton, QSlider, QSpinBox
from app import Manager, STYLE
from choice_controls import ClickSlider, ClickSpinBox
from core import atomic_json
from test_app import destroy_test_manager


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
        atomic_json(folder / "设置.json", {"voice_engine": "system", "hotkeys": {}, "speech": False,
                                             "timed_playback_policy": 1})
        self.patches = [patch.object(Manager, "seed"), patch("app.Speech.start"),
                        patch("app.Manager.register_hotkeys"), patch("update_ui.UpdateController.check")]
        for replacement in self.patches:
            replacement.start()
        self.application.setStyleSheet(STYLE)
        self.manager = Manager(self.application, folder)
        self.manager.show()
        QTest.qWait(30)

    def tearDown(self):
        destroy_test_manager(self.application, self.manager)
        for replacement in reversed(self.patches):
            replacement.stop()
        self.temporary.cleanup()

    def test_all_setting_numbers_and_volume_slider_ignore_wheel_even_with_focus(self):
        self.manager.navigate(1)
        numbers = self.manager.tabs.widget(1).findChildren(QSpinBox)
        sliders = self.manager.tabs.widget(1).findChildren(QSlider)
        self.assertEqual(len(numbers), 7, "移除尺寸数值框，语速改为三档下拉，其他数值仍不可被滚轮误改")
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
            # 当前五个主导航页共用标题原点；已移除的个人页与独立开发中占位不冒充主导航。
            for index, title in ((0, "我的开局"), (3, "玩家查询"), (4, "对局分析"), (1, "设置"), (2, "关于")):
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
        self.assertFalse(self.manager.status_bar.isVisible(), "底部不能出现状态文字或文字占位")
        with patch("app.atomic_json", side_effect=OSError("测试无写入权限")):
            self.manager.apply_settings()
        self.assertIn("设置未能写入", self.manager.status_bar.text())
        self.assertFalse(self.manager.status_bar.isVisible())
        self.assertTrue(self.manager._problem_dialog.isVisible(), "保存失败仍需在对应弹窗中可见")

    def test_hidden_settings_reflows_when_returning_from_narrow_to_wide_window(self):
        """隐藏期间换列后，语音卡不能沿用窄窗分配的空白高度。"""
        for width in (760, 1100):
            self.manager.navigate(3)
            self.manager.resize(width, 820)
            QTest.qWait(20)
            self.manager.navigate(1)
            QTest.qWait(20)
            item = self.manager.settings_scroll.widget().layout().itemAt(0)
            self.assertLessEqual(item.geometry().height(), item.sizeHint().height() + 4)

    def test_software_data_actions_have_one_separate_responsive_category(self):
        manager = self.manager
        manager.navigate(1)
        group = manager.software_data_group
        self.assertEqual(group.title(), "软件与数据")
        self.assertIs(group.parentWidget(), manager.settings_right_column)
        self.assertIs(manager.hotkeys_group.parentWidget(), manager.settings_right_column)
        self.assertIs(manager.check_updates_button.parentWidget(), group)
        self.assertIs(manager.data_folder_button.parentWidget(), group)
        for caption in ("检查软件更新", "打开助手数据目录"):
            self.assertEqual(sum(item.text() == caption for item in manager.findChildren(QPushButton)), 1)
        for width in (1100, 760):
            with self.subTest(width=width):
                manager.resize(width, 900)
                self.application.processEvents()
                hotkeys = manager.hotkeys_group.mapTo(manager, QPoint())
                software = group.mapTo(manager, QPoint())
                appearance = manager.appearance_group.mapTo(manager, QPoint())
                self.assertEqual(software.x(), hotkeys.x())
                self.assertGreaterEqual(software.y(), hotkeys.y() + manager.hotkeys_group.height() + 12)
                if width == 1100:
                    self.assertGreater(hotkeys.x(), appearance.x())
                    self.assertEqual(hotkeys.y(), appearance.y())
                else:
                    self.assertEqual(hotkeys.x(), appearance.x())
                    self.assertGreater(hotkeys.y(), appearance.y() + manager.appearance_group.height())
        with patch.object(manager.updates, "check") as check:
            manager.check_updates_button.click()
        check.assert_called_once_with(manual=True)
        with patch("app.os.startfile") as open_folder:
            manager.data_folder_button.click()
        open_folder.assert_called_once_with(str(manager.data_dir))


if __name__ == "__main__":
    unittest.main()
