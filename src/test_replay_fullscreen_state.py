"""大图宿主真实Qt交互：全屏切换、Esc返回、状态交接和无旧工具占位。"""
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication

from replay_analysis import analyze_replay
from replay_map_view import ReplayMapView
from replay_page import ReplayPage
from visual import STYLE


def fullscreen_report():
    """只用合成的真实结构指令，未加入地形或推测单位位置。"""
    return analyze_replay({
        "source": {"name": "全屏交互验证.rec", "sha256": "d" * 64},
        "metadata": {"duration": 120, "map": "Dry Arabia", "players": [
            {"id": 101, "profile_id": 880001, "name": "同名玩家"},
            {"id": 202, "profile_id": 990002, "name": "同名玩家"}]},
        "commands": [{"time": index * 5, "tick": index * 40, "offset": index + 100,
                      "player_id": 101 if index % 2 == 0 else 202, "type": "move", "known": True,
                      "target": {"x": index * 12, "y": 2, "z": index * 6}, "entity_ids": [index + 20]}
                     for index in range(20)],
        "coverage": {"ticks_per_second": 8, "commands_total": 20}})


class ReplayFullscreenStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        cls.application.setStyleSheet(STYLE)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.page = ReplayPage(self.directory.name)
        self.page.resize(1100, 780)
        self.report = fullscreen_report()
        self.page._show_report(self.report)
        # 首屏默认参考地图；以下既有9项交接验证显式选择真实操作坐标模式。
        self.page.map_view.view_mode.setCurrentIndex(self.page.map_view.view_mode.findData("records"))
        self.page.show()
        self.page.activateWindow()
        QTest.qWait(10)
        self.extra_views = []

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        for view in self.extra_views:
            view.cleanup()
            view.close()
            view.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()
        self.directory.cleanup()

    def configure_view(self, view, *, player=202, seconds=35, speed=4, zoom=2.4):
        view.pause()
        view.player_filter.setCurrentIndex(view.player_filter.findData(player))
        view.speed.setCurrentIndex(view.speed.findData(speed))
        event = next(event for event in reversed(view.events) if event["player_id"] == player and event["time"] <= seconds)
        view.select_event(event)
        view.seek(seconds, emit=False)
        view.canvas.zoom_at(view.canvas.viewport().rect().center(), zoom)
        view.canvas.centerOn(QPointF(70, -35))
        return view.capture_view_state()

    def close_large(self, dialog):
        """按产品实际入口Esc返回，不依赖已移出界面的旧文字按钮。"""
        dialog.activateWindow()
        QTest.keyClick(dialog, Qt.Key_Escape)

    def assert_state(self, view, expected):
        actual = view.capture_view_state()
        for key in ("time", "player_id", "speed", "playing", "selected_event_id"):
            self.assertEqual(actual[key], expected[key], key)
        self.assertEqual(actual["camera"]["extent"], expected["camera"]["extent"])
        self.assertAlmostEqual(actual["camera"]["zoom"], expected["camera"]["zoom"], places=6)
        # Qt滚动条以整像素定位中心，跨尺寸允许最多两个物理像素的量化误差。
        tolerance = 2 / max(.000001, view.canvas.transform().m11()) + 1e-9
        for actual_center, expected_center in zip(actual["camera"]["center"], expected["camera"]["center"]):
            self.assertAlmostEqual(actual_center, expected_center, delta=tolerance)
        self.assertEqual(actual["camera"]["user_camera"], expected["camera"]["user_camera"])

    def test_capture_restore_keeps_past_selection_without_rewinding_time_or_emitting_actions(self):
        expected = self.configure_view(self.page.map_view, seconds=38)
        target = ReplayMapView(self.page.spatial_model)
        self.extra_views.append(target)
        target.resize(1600, 900)
        target.show()
        self.application.processEvents()
        event_spy, time_spy = QSignalSpy(target.eventSelected), QSignalSpy(target.timeSelected)
        self.assertTrue(target.restore_view_state(expected))
        self.assert_state(target, expected)
        self.assertEqual(len(event_spy), 0)
        self.assertEqual(len(time_spy), 0)
        self.assertLess(target.layer.selected["time"], target.current_time)

    def test_real_fullscreen_and_escape_restore_all_view_state(self):
        expected = self.configure_view(self.page.map_view)
        self.page.expand_map_button.click()
        QTest.qWait(15)
        dialog = self.page._map_dialog
        self.assertTrue(dialog.isFullScreen())
        self.assertTrue(dialog.return_button.isHidden())
        self.assertTrue(dialog.fullscreen_button.isHidden())
        self.assertTrue(dialog.map_view.slider.isHidden())
        self.assert_state(dialog.map_view, expected)
        changed = self.configure_view(dialog.map_view, player=101, seconds=60, speed=8, zoom=1.5)
        self.close_large(dialog)
        self.assertIsNone(self.page._map_dialog)
        self.assertFalse(dialog.map_view.timer.isActive())
        self.assert_state(self.page.map_view, changed)
        self.assertFalse(self.page.map_view._cleaned)

    def test_f11_opens_then_returns_to_embedded_page_and_escape_is_equivalent(self):
        expected = self.configure_view(self.page.map_view, seconds=38)
        for return_key in (Qt.Key_F11, Qt.Key_Escape):
            with self.subTest(return_key=return_key):
                self.page.activateWindow()
                self.page.expand_map_button.setFocus()
                self.application.processEvents()
                QTest.keyClick(self.page.expand_map_button, Qt.Key_F11)
                QTest.qWait(15)
                dialog = self.page._map_dialog
                self.assertIsNotNone(dialog)
                self.assertTrue(dialog.isFullScreen())
                self.assert_state(dialog.map_view, expected)
                changed = self.configure_view(dialog.map_view, player=101, seconds=60, speed=8, zoom=1.5)
                dialog.activateWindow()
                QTest.keyClick(dialog.map_view.canvas, return_key)
                self.assertIsNone(self.page._map_dialog)
                self.assertFalse(dialog.isVisible(), "退出不能留下无标题栏普通地图浮窗")
                self.assertFalse(dialog.map_view.timer.isActive())
                self.assertTrue(dialog.map_view._cleaned)
                self.assert_state(self.page.map_view, changed)
                self.assertFalse(self.page.map_view._cleaned)
                expected = changed

    def test_repeated_fullscreen_returns_preserve_new_camera_and_do_not_leave_dialogs(self):
        expected = self.configure_view(self.page.map_view, seconds=38)
        for cycle in range(3):
            self.page._expand_map()
            QTest.qWait(10)
            dialog = self.page._map_dialog
            self.assertTrue(dialog.isFullScreen())
            self.assert_state(dialog.map_view, expected)
            # 用户主动缩放、平移的新状态必须取代旧镜头，交回原页面。
            self.configure_view(dialog.map_view, player=101, seconds=50 + cycle * 5, speed=8, zoom=1.5)
            dialog.map_view.canvas.centerOn(QPointF(90 + cycle * 5, -45))
            changed = dialog.map_view.capture_view_state()
            dialog.toggle_fullscreen()
            self.assertIsNone(self.page._map_dialog)
            self.assertFalse(dialog.isVisible())
            self.assertFalse(dialog._restore_timer.isActive())
            self.assertFalse(dialog.map_view.timer.isActive())
            self.assert_state(self.page.map_view, changed)
            # 下轮以原页面的可表示镜头为基准，不累加跨不同像素栅格的量化误差。
            expected = self.page.map_view.capture_view_state()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(any(widget.isVisible() and widget.windowTitle() == "二维复盘地图"
                             for widget in self.application.topLevelWidgets()))

    def test_fullscreen_return_keeps_original_page_maximized_and_normal_geometry(self):
        normal = QRect(38, 44, 720, 500)
        self.page.setGeometry(normal)
        self.page.showMaximized()
        QTest.qWait(15)
        expected = self.configure_view(self.page.map_view, seconds=38)
        self.page._expand_map()
        QTest.qWait(15)
        dialog = self.page._map_dialog
        self.assertTrue(dialog.isFullScreen())
        dialog.toggle_fullscreen()
        self.assertIsNone(self.page._map_dialog)
        self.assertTrue(self.page.isMaximized(), "返回应保持原页面状态，而不是还原独立地图窗口")
        self.assert_state(self.page.map_view, expected)
        self.page.showNormal()
        QTest.qWait(15)
        self.assertEqual(self.page.geometry(), normal)

    def test_playback_continues_in_one_view_then_resumes_in_original_view(self):
        main = self.page.map_view
        main.seek(30)
        main.speed.setCurrentIndex(main.speed.findData(2))
        main.toggle_playback()
        self.page._expand_map()
        QTest.qWait(20)
        dialog = self.page._map_dialog
        self.assertFalse(main.timer.isActive())
        self.assertTrue(dialog.map_view.timer.isActive())
        # 等到实际播放计时器触发，避免把首次全屏布局耗时算作播放失败。
        for _ in range(25):
            if dialog.map_view.current_time > 30:
                break
            QTest.qWait(20)
        self.assertGreater(dialog.map_view.current_time, 30)
        returned_time = dialog.map_view.current_time
        self.close_large(dialog)
        self.assertTrue(main.timer.isActive())
        self.assertEqual(main.current_time, returned_time)
        QTest.qWait(100)
        self.assertGreater(main.current_time, returned_time)

    def test_immediate_close_before_deferred_restore_has_no_orphan_or_state_reset(self):
        expected = self.configure_view(self.page.map_view, seconds=38)
        self.page._expand_map()
        dialog = self.page._map_dialog
        dialog.close()
        self.assertIsNone(self.page._map_dialog)
        self.assertFalse(dialog._restore_timer.isActive())
        self.assertFalse(dialog.map_view.timer.isActive())
        self.assert_state(self.page.map_view, expected)
        QTest.qWait(20)
        self.assertIsNone(self.page._map_dialog)

    def test_hiding_page_or_replacing_report_does_not_restart_invisible_playback(self):
        self.page.map_view.toggle_playback()
        self.page._expand_map()
        QTest.qWait(10)
        self.page.hide()
        self.assertIsNone(self.page._map_dialog)
        self.assertFalse(self.page.map_view.timer.isActive())
        self.page.show()
        self.page._expand_map()
        dialog = self.page._map_dialog
        self.page._show_report(self.report)
        self.assertIsNone(self.page._map_dialog)
        self.assertTrue(dialog.map_view._cleaned)
        self.assertFalse(dialog._restore_timer.isActive())
        self.assertEqual(self.page.map_view.current_time, 0)

    def test_embedded_time_axis_and_fullscreen_overlay_fit_both_sizes_without_old_chrome(self):
        for width, height in ((760, 520), (1600, 900)):
            with self.subTest(width=width, height=height):
                self.page.resize(width, height)
                QTest.qWait(15)
                view = self.page.map_view
                viewport = self.page.scroll.viewport()
                bottom = view.slider.mapTo(viewport, QPoint(0, view.slider.height()))
                self.assertLessEqual(bottom.y(), viewport.height())
                self.assertGreaterEqual(view.canvas.height(), 160)
                self.page._expand_map()
                QTest.qWait(10)
                dialog = self.page._map_dialog
                dialog.showNormal()
                dialog.resize(width, height)
                QTest.qWait(10)
                large = dialog.map_view
                self.assertTrue(large.slider.isHidden())
                self.assertTrue(large.speed.isHidden())
                self.assertTrue(dialog.return_button.isHidden())
                self.assertTrue(dialog.fullscreen_button.isHidden())
                self.assertEqual(large.map_stack.geometry().topLeft(), QPoint(0, 0))
                self.assertEqual(large.map_stack.size(), large.size())
                self.assertGreaterEqual(large.map_stack.currentWidget().height(), 200)
                panel = large.fullscreen_panel
                self.assertTrue(panel.isVisible())
                self.assertLessEqual(panel.geometry().right(), large.width() - 1)
                self.assertLessEqual(panel.geometry().bottom(), large.height() - 1)
                self.close_large(dialog)


if __name__ == "__main__":
    unittest.main()
