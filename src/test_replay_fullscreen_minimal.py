"""精简全屏的实际Qt回归：空态、无旧控件占位、真实时钟与浮动播放板。

fullscreen=独立全屏显示；网格只展示原记录目标，不补造地形或单位轨迹。
"""
import os
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel, QPushButton

from full_map_catalog import FullMapCatalog
from replay_map_view import ReplayMapView
from replay_page import ReplayPage
from test_replay_fullscreen_state import fullscreen_report
from visual import STYLE


class ReplayFullscreenMinimalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        cls.application.setStyleSheet(STYLE)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.page = ReplayPage(self.directory.name)
        self.page.resize(1100, 780)
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

    def expand(self, *, report=True):
        if report:
            self.page._show_report(fullscreen_report())
        self.page._expand_map()
        QTest.qWait(20)
        return self.page._map_dialog

    def escape(self, dialog):
        dialog.activateWindow()
        QTest.keyClick(dialog, Qt.Key_Escape)

    def test_unselected_empty_is_centered_pure_background_without_map_or_playback(self):
        with patch.object(FullMapCatalog, "load_entities") as load:
            dialog = self.expand(report=False)
            load.assert_not_called()
        view = dialog.map_view
        self.assertTrue(dialog.isFullScreen())
        self.assertFalse(dialog.testAttribute(Qt.WA_TranslucentBackground),
                         "全屏地图不能继承通用弹窗的透明分层合成")
        self.assertFalse(dialog.testAttribute(Qt.WA_NoSystemBackground))
        self.assertTrue(view.fullscreen_empty.isVisible())
        self.assertEqual(view.fullscreen_empty.text(), "未选择对局数据")
        self.assertEqual(view.fullscreen_empty.alignment() & Qt.AlignCenter, Qt.AlignCenter)
        self.assertTrue(view.fullscreen_panel.isHidden())
        self.assertIsNone(view.official_map)
        self.assertFalse(view.timer.isActive())
        self.assertTrue(dialog.return_button.isHidden())
        self.assertTrue(dialog.fullscreen_button.isHidden())
        pixels = view.grab().toImage()
        self.assertEqual(pixels.pixelColor(0, 0).name(), "#0f0f11")
        self.assertEqual(pixels.pixelColor(pixels.width() - 1, pixels.height() - 1).name(), "#0f0f11")
        dialog_pixels = dialog.grab().toImage()
        self.assertEqual(dialog_pixels.pixelColor(0, 0).getRgb(), (15, 15, 17, 255))
        self.assertEqual(dialog_pixels.pixelColor(dialog.width() - 1, dialog.height() - 1).getRgb(), (15, 15, 17, 255))
        self.escape(dialog)
        self.assertIsNone(self.page._map_dialog)

    def test_selected_context_keeps_grid_and_map_name_without_fake_playback(self):
        self.page._preview_public_context(101, {"map": "King of the Hill", "mode": "rm_4v4", "duration": 120})
        dialog = self.expand(report=False)
        view = dialog.map_view
        self.assertIsNone(view.official_map)
        self.assertEqual(view.model['map_name'], 'King of the Hill')
        self.assertIs(view.map_stack.currentWidget(), view.canvas)
        self.assertFalse(view.events, '仅有公开地图名不能补造坐标记录')
        self.assertFalse(view.fullscreen_empty.isVisible())
        self.assertTrue(view.fullscreen_panel.isHidden())
        self.assertFalse(view.timer.isActive())
        view.toggle_playback()
        self.assertFalse(view.timer.isActive(), "公开摘要时长不能变成假回放时钟")
        self.assertIn(view.layer, view.canvas.scene().items())

    def test_real_report_has_only_overlay_controls_and_zero_old_chrome_space(self):
        dialog = self.expand()
        view = dialog.map_view
        self.assertEqual(dialog.layout().contentsMargins().left(), 0)
        self.assertEqual(dialog.layout().contentsMargins().top(), 0)
        self.assertEqual(view.map_stack.geometry().topLeft(), QPoint(0, 0))
        self.assertEqual(view.map_stack.size(), view.size())
        old = [*view._toolbar_widgets, *view._timebar_widgets, view.notice, view.scope,
               view.detail_heading, view.detail_coordinates]
        self.assertTrue(all(widget.isHidden() for widget in old))
        self.assertTrue(dialog.return_button.isHidden())
        self.assertTrue(dialog.fullscreen_button.isHidden())
        dock = view.advanced_analysis
        self.assertIsNotNone(dock)
        self.assertTrue(dock.rail.isVisible())
        self.assertEqual(len(dock.buttons), 18)
        self.assertTrue(dock.panel.isHidden())
        expected = {view.fullscreen_play, *view.fullscreen_speeds.values(), *dock.buttons.values()}
        visible = {button for button in view.findChildren(QPushButton) if button.isVisible()}
        self.assertEqual(visible, expected)
        self.assertEqual(set(view.fullscreen_speeds), {1, 4, 8, 16})
        self.assertEqual(view.fullscreen_play.text(), "")
        self.assertFalse(view.fullscreen_play.icon().isNull())
        self.assertEqual(view.fullscreen_play.toolTip(), "")
        self.assertEqual(view.fullscreen_play.accessibleName(), "播放")
        self.assertTrue(view.fullscreen_timeline.isVisible())
        self.assertTrue(view.fullscreen_clock.isVisible())
        self.assertFalse(view.fullscreen_empty.isVisible())

    def test_overlay_follows_bottom_right_when_window_and_fullscreen_sizes_change(self):
        dialog = self.expand()
        dialog.showNormal()
        last = None
        for width, height in ((760, 520), (1100, 780), (1500, 940)):
            with self.subTest(width=width):
                dialog.resize(width, height)
                QTest.qWait(15)
                view, panel = dialog.map_view, dialog.map_view.fullscreen_panel
                self.assertTrue(panel.isVisible())
                self.assertEqual(view.size(), dialog.size())
                self.assertLessEqual(panel.geometry().right(), view.width() - 1)
                self.assertLessEqual(panel.geometry().bottom(), view.height() - 1)
                self.assertGreaterEqual(panel.x(), 0)
                self.assertGreaterEqual(panel.y(), 0)
                if last is not None:
                    self.assertEqual(view.width() - panel.geometry().right(), last[0])
                    self.assertEqual(view.height() - panel.geometry().bottom(), last[1])
                last = (view.width() - panel.geometry().right(), view.height() - panel.geometry().bottom())
                self.assertEqual(view.map_stack.geometry(), view.rect())
        # 仅为离屏多尺寸布局测试重新进入全屏；用户F11的返回语义另有真实按键回归。
        dialog.showFullScreen()
        QTest.qWait(15)
        self.assertTrue(dialog.isFullScreen())
        self.assertLessEqual(dialog.map_view.fullscreen_panel.geometry().bottom(), dialog.map_view.height() - 1)

    def test_four_speed_buttons_advance_actual_grid_clock_and_update_active_state(self):
        dialog = self.expand()
        view = dialog.map_view
        self.assertEqual(view.view_mode.currentData(), "records")
        deltas = []
        for rate in (1, 4, 8, 16):
            with self.subTest(rate=rate):
                view.pause()
                view.seek(20, emit=False)
                view.fullscreen_speeds[rate].click()
                self.assertEqual(view.speed.currentData(), rate)
                self.assertTrue(view.fullscreen_speeds[rate].isChecked())
                self.assertEqual([key for key, button in view.fullscreen_speeds.items() if button.isChecked()], [rate])
                started = time.monotonic()
                view.fullscreen_play.click()
                self.assertTrue(view.timer.isActive())
                self.assertFalse(self.page.map_view.timer.isActive())
                QTest.qWait(260)
                view.fullscreen_play.click()
                elapsed = time.monotonic() - started
                delta = view.current_time - 20
                self.assertFalse(view.timer.isActive())
                self.assertGreater(delta, rate * .12)
                self.assertAlmostEqual(delta, elapsed * rate, delta=rate * .08 + .04)
                deltas.append(delta)
        self.assertGreater(deltas[1], deltas[0] * 2.5)
        self.assertGreater(deltas[2], deltas[1] * 1.5)
        self.assertGreater(deltas[3], deltas[2] * 1.5)

    def test_grid_time_reveals_recorded_actions_without_moving_camera_or_targets(self):
        dialog = self.expand()
        view = dialog.map_view
        self.assertIsNone(view.official_map)
        before = view.capture_view_state()['camera']
        targets = [(event['time'], event['x'], event['y']) for event in view.events]
        initial_count = len(view.layer.history_events) + len(view.layer.recent_events)
        view.seek(80)
        view.fullscreen_speeds[16].click()
        view.fullscreen_play.click()
        QTest.qWait(100)
        view.pause()
        self.assertGreater(view.current_time, 80)
        self.assertEqual(view.capture_view_state()['camera'], before)
        self.assertEqual([(event['time'], event['x'], event['y']) for event in view.events], targets)
        self.assertIn(view.layer, view.canvas.scene().items())
        visible = len(view.layer.history_events) + len(view.layer.recent_events)
        self.assertGreater(visible, initial_count)
        self.assertEqual(visible, sum(event['time'] <= view.current_time for event in view.events))

    def test_grid_playback_f11_returns_to_one_original_clock_with_state_intact(self):
        dialog = self.expand()
        view = dialog.map_view
        view.player_filter.setCurrentIndex(view.player_filter.findData(202))
        selected = next(event for event in view.events if event["player_id"] == 202)
        view.select_event(selected)
        view.seek(35)
        canvas = view.canvas
        canvas.zoom_at(canvas.viewport().rect().center(), 2.2)
        canvas.centerOn(canvas.extent.center() + QPointF(180, -120))
        self.assertIs(view.layer.selected, selected)
        view.fullscreen_speeds[16].click()
        view.fullscreen_play.click()
        QTest.qWait(80)
        expected = view.capture_view_state()
        dialog.activateWindow()
        QTest.keyClick(dialog, Qt.Key_F11)
        main = self.page.map_view
        self.assertIsNone(self.page._map_dialog)
        self.assertFalse(view.timer.isActive())
        self.assertTrue(view._cleaned)
        self.assertTrue(main.timer.isActive())
        actual = main.capture_view_state()
        for key in ("time", "speed", "player_id", "selected_event_id", "view_mode"):
            self.assertEqual(actual[key], expected[key], key)
        self.assertIsNone(actual['official_map'])
        camera, saved = actual['camera'], expected['camera']
        self.assertAlmostEqual(camera["zoom"], saved["zoom"], places=6)
        tolerance = 2 / main.canvas.transform().m11()
        for value, target in zip(camera["center"], saved["center"]):
            self.assertAlmostEqual(value, target, delta=tolerance)
        returned = main.current_time
        QTest.qWait(90)
        self.assertGreater(main.current_time, returned)
        self.assertIn(main.layer, main.canvas.scene().items())

    def test_missing_valid_duration_or_players_never_creates_playback_panel(self):
        for model in ({"map_name": "Dry Arabia", "duration": 120, "players": []},
                      {"map_name": "Dry Arabia", "duration": float("nan"), "players": [{"id": 1}]},
                      {"map_name": "Dry Arabia", "duration": 0, "players": [{"id": 1}]}):
            with self.subTest(model=model):
                view = ReplayMapView(model)
                self.extra_views.append(view)
                view.set_fullscreen_mode(True)
                view.resize(760, 520)
                view.show()
                self.application.processEvents()
                self.assertTrue(view.fullscreen_panel.isHidden())
                view.toggle_playback()
                self.assertFalse(view.timer.isActive())


if __name__ == "__main__":
    unittest.main()
