"""网格全屏时间条：真实Qt拖动、独立时间同步、缺时长、宽窄布局与状态返回。"""
import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication, QStyle, QStyleOptionSlider

from replay_map_view import ReplayMapView
from visual import STYLE


def model(duration=180):
    return {'duration': duration, 'map_name': '占山为王',
            'players': [{'id': 1, 'name': '玩家甲'}, {'id': 2, 'name': '玩家乙'}],
            'events': [{'id': 1, 'time': 10, 'player_id': 1, 'x': 12, 'y': 6,
                        'kind': 'command', 'label': '移动目标'},
                       {'id': 2, 'time': 80, 'player_id': 2, 'x': -20, 'y': 30,
                        'kind': 'command', 'label': '攻击目标'}]}


class GridFullscreenTimelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.views = []

    def tearDown(self):
        for view in self.views:
            view.cleanup()
            view.close()
            view.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def view(self, data=None, width=1200, fullscreen=True):
        view = ReplayMapView(model() if data is None else data)
        self.views.append(view)
        view.set_fullscreen_mode(fullscreen)
        view.resize(width, 700)
        view.show()
        self.app.processEvents()
        return view

    def test_default_grid_does_not_import_or_read_official_map(self):
        """正式默认模式可在旧地图模块完全不可导入时创建。"""
        with patch.dict(sys.modules, {'full_map_view': None, 'map_pack_location': None}):
            view = self.view()
            self.assertIsNone(view.official_map)
            self.assertEqual(view.map_stack.count(), 1)
            self.assertIs(view.map_stack.currentWidget(), view.canvas)
            self.assertEqual(view.view_mode.count(), 1)
            self.assertEqual(view.view_mode.currentData(), 'records')
            self.assertTrue(view.view_mode.isHidden())
            self.assertTrue(view.map_pack_button.isHidden())
            self.assertFalse(view.use_map_pack('unused-map.aoe4maps'))

    def test_actual_drag_pauses_and_synchronizes_once(self):
        """拖动真实滑块暂停播放，主时间条同步且不产生递归跳转。"""
        view = self.view()
        view.toggle_playback()
        self.assertTrue(view.timer.isActive())
        slider = view.fullscreen_slider
        option = QStyleOptionSlider()
        slider.initStyleOption(option)
        center = slider.style().subControlRect(QStyle.CC_Slider, option,
                                              QStyle.SC_SliderHandle, slider).center()
        target = QPoint(round(slider.width()*.70), center.y())
        QTest.mousePress(slider, Qt.LeftButton, pos=center)
        self.assertTrue(slider.isSliderDown())
        # 离屏平台的QTest.mouseMove不带按住按钮状态，补完整拖动事件而非改slider值。
        QApplication.sendEvent(slider, QMouseEvent(QEvent.MouseMove, QPointF(target),
                                                  Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        QTest.mouseRelease(slider, Qt.LeftButton, pos=target)
        self.app.processEvents()
        self.assertFalse(view.timer.isActive())
        self.assertGreater(view.current_time, 80)
        self.assertLess(view.current_time, view.duration)
        self.assertEqual(view.slider.value(), slider.value())
        self.assertAlmostEqual(view.current_time, slider.value()/10)
        self.assertEqual(view.clock_label.text(), view.fullscreen_clock.text())
        spy = QSignalSpy(view.timeSelected)
        view.seek(42.7)
        self.assertEqual(len(spy), 1)
        self.assertEqual(slider.value(), 427)
        self.assertEqual(view.fullscreen_clock.text(), '00:42 / 03:00')

    def test_playback_speed_and_clock_update_same_timeline(self):
        """原倍速面板与新增时间条共用播放时钟，不再建第二个定时器。"""
        view = self.view()
        view.seek(20)
        view.set_playback_speed(8)
        self.assertEqual(view.speed.currentData(), 8)
        self.assertTrue(view.fullscreen_speeds[8].isChecked())
        class QuarterSecond:
            def restart(self):
                return 250
        view.elapsed = QuarterSecond()
        view.timer.start()
        view._advance()
        view.pause()
        self.assertEqual(view.current_time, 22)
        self.assertEqual(view.fullscreen_slider.value(), 220)
        self.assertEqual(view.fullscreen_clock.text(), view.clock_label.text())

    def test_missing_real_duration_never_enables_fullscreen_slider(self):
        """只有较晚操作点不能伪造完整录像时长。"""
        for duration in (None, 0, float('nan')):
            with self.subTest(duration=duration):
                view = self.view(model(duration))
                self.assertFalse(view._valid_timeline())
                self.assertFalse(view.fullscreen_slider.isEnabled())
                self.assertTrue(view.fullscreen_timeline.isHidden())
                self.assertTrue(view.fullscreen_panel.isHidden())
                view.fullscreen_slider.setValue(200)
                self.assertEqual(view.current_time, 0)

    def test_wide_and_narrow_controls_keep_fixed_gaps(self):
        """常规宽度置左下，极窄窗口自动上移，外边距24且中缝16。"""
        for width in (1200, 760, 420):
            with self.subTest(width=width):
                view = self.view(width=width)
                bar, panel = view.fullscreen_timeline.geometry(), view.fullscreen_panel.geometry()
                self.assertEqual(bar.left(), 24)
                self.assertFalse(bar.intersects(panel))
                self.assertLessEqual(bar.right(), view.width()-24)
                if width >= 760:
                    self.assertEqual(bar.top(), panel.top())
                    self.assertEqual(panel.left()-(bar.left()+bar.width()), 16)
                else:
                    self.assertEqual(panel.top()-(bar.top()+bar.height()), 16)
                self.assertGreater(view.fullscreen_slider.width(), 64)

    def test_return_to_embedded_and_legacy_state_keeps_time_speed(self):
        """全屏状态交接回嵌入画布时保留时刻/倍速；旧reference状态被忽略。"""
        full = self.view()
        full.seek(64.3)
        full.set_playback_speed(16)
        state = full.capture_view_state()
        state['view_mode'] = 'reference'
        embedded = self.view(width=760, fullscreen=False)
        embedded.set_dashboard_mode()
        self.assertTrue(embedded.restore_view_state(state))
        self.assertEqual(embedded.current_time, 64.3)
        self.assertEqual(embedded.slider.value(), 643)
        self.assertEqual(embedded.speed.currentData(), 16)
        self.assertEqual(embedded.view_mode.currentData(), 'records')
        self.assertEqual(embedded.capture_view_state()['official_map'], None)
        self.assertTrue(embedded.fullscreen_timeline.isHidden())
        full.set_fullscreen_mode(False)
        self.assertTrue(full.fullscreen_timeline.isHidden())
        self.assertTrue(full.fullscreen_panel.isHidden())
        self.assertFalse(full.slider.isHidden())
        self.assertTrue(full.view_mode.isHidden())
        self.assertEqual(full.current_time, 64.3)


if __name__ == '__main__':
    unittest.main()
