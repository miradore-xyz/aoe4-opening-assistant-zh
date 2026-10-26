"""无需录像的官方图鉴入口：真实全屏、空报告边界与反复退出清理。"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import sip
from PyQt5.QtCore import QEvent, QTimer, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QPushButton

from official_maps import catalog_entries
from replay_page import ReplayPage
from visual import STYLE


class OfficialMapBrowseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        cls.application.setStyleSheet(STYLE)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.page = ReplayPage(self.directory.name)
        self.page.resize(760, 520)
        self.page.show()
        QTest.qWait(10)
        self.browse = next(button for button in self.page.findChildren(QPushButton)
                           if button.text() == "浏览官方地图")

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.drain_deletion()
        self.directory.cleanup()

    def drain_deletion(self):
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()

    def test_empty_page_opens_real_fullscreen_reference_without_reading_or_creating_match(self):
        with patch("replay_page.parse_replay") as parse, \
                patch("replay_page.PublicReplayClient.fetch") as download, \
                patch("replay_page.MatchDetailClient.fetch") as fetch_detail, \
                patch("replay_page.ReplayLibrary.save") as save:
            QTest.mouseClick(self.browse, Qt.LeftButton)
            QTest.qWait(10)
            dialog = self.page._map_dialog
            self.assertIsNotNone(dialog)
            self.assertTrue(dialog.isFullScreen())
            view = dialog.map_view
            self.assertEqual(view.view_mode.currentData(), "reference")
            self.assertTrue(view.official_map.isVisible())
            self.assertEqual(len(catalog_entries()), 74, "当前版本应包含74项官方自动匹配配置")
            self.assertEqual(view.official_map.map_selector.count(), 74)
            self.assertIsNone(view.official_map.entry, "无对局不擅自选一张地图充当本场")
            self.assertTrue(view.official_map.source_label.isVisible())
            self.assertEqual(view.official_map.source_label.text(), "官方生成参考实例 · 非本场实战")
            self.assertEqual(view.model["events"], [])
            self.assertEqual(view.model["players"], [])
            self.assertFalse(view.play_button.isEnabled())
            self.assertFalse(view.slider.isEnabled())
            self.assertIsNone(self.page.report)
            self.assertIsNone(self.page.report_id)
            self.assertIsNone(self.page.map_view)
            self.assertFalse(self.page.busy)
            self.assertEqual(self.page.archive.count(), 0)
            self.assertFalse((Path(self.directory.name) / "本地复盘").exists())
            for operation in (parse, download, fetch_detail, save):
                operation.assert_not_called()

    def test_return_reopen_and_page_hide_delete_dialog_timers_without_orphans(self):
        closed = []
        for index in range(3):
            self.browse.click()
            QTest.qWait(5)
            dialog = self.page._map_dialog
            self.assertTrue(dialog.isFullScreen())
            self.assertEqual(dialog.map_view.view_mode.currentData(), "reference")
            timers = dialog.findChildren(QTimer)
            self.assertGreaterEqual(len(timers), 2)
            self.assertFalse(any(timer.isActive() for timer in timers))
            if index == 0:
                # 回归已发现的入口边界：现有宿主被切到记录和窗口化后，
                # “浏览官方地图”仍应明确回到参考模式与真正全屏。
                dialog.map_view.view_mode.setCurrentIndex(dialog.map_view.view_mode.findData("records"))
                dialog.activateWindow()
                dialog.map_view.canvas.setFocus()
                QTest.keyClick(dialog.map_view.canvas, Qt.Key_F11)
                QTest.qWait(5)
                self.assertFalse(dialog.isFullScreen())
                self.assertEqual(dialog.map_view.view_mode.currentData(), "records")
            self.browse.click()
            QTest.qWait(5)
            self.assertIs(self.page._map_dialog, dialog, "重复打开复用同一个宿主")
            self.assertTrue(dialog.isFullScreen())
            self.assertEqual(dialog.map_view.view_mode.currentData(), "reference")
            dialog.return_button.click()
            self.assertIsNone(self.page._map_dialog)
            self.assertTrue(dialog.map_view._cleaned)
            self.assertFalse(any(timer.isActive() for timer in timers))
            self.drain_deletion()
            self.assertTrue(sip.isdeleted(dialog))
            self.assertTrue(all(sip.isdeleted(timer) for timer in timers))
            closed.append(dialog)  # 保留Python引用，证明清理不依赖随机垃圾回收。
        self.browse.click()
        dialog = self.page._map_dialog
        timers = dialog.findChildren(QTimer)
        self.page.hide()
        self.assertIsNone(self.page._map_dialog)
        self.assertFalse(any(timer.isActive() for timer in timers))
        self.drain_deletion()
        self.assertTrue(sip.isdeleted(dialog))
        self.assertFalse(any(type(window).__name__ == "_ReplayMapDialog"
                             for window in self.application.topLevelWidgets()))
        self.assertIsNone(self.page.report)
        self.assertEqual(self.page.archive.count(), 0)


if __name__ == "__main__":
    unittest.main()
