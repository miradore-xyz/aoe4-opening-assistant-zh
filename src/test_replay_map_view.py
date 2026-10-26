"""二维主视图真实Qt交互：证据时窗、点选、平移缩放、播放与生命周期。"""
import copy
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QFontDatabase, QMouseEvent, QWheelEvent
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication, QGraphicsPathItem

from replay_map_view import ReplayMapView
from visual import STYLE


def spatial_model():
    rows = []
    for identity, time, player, x, z, kind, action in (
        (1, 0, 1, 0, 0, "birth", "单位出现"),
        (2, 5, 1, 40, 20, "command", "移动目标"),
        (3, 30, 2, -20, 30, "command", "攻击移动目标"),
        (4, 35, 2, -10, 40, "death", "单位阵亡"),
        (5, 45, 1, 70, -10, "command", "集结目标"),
    ):
        rows.append({"id": identity, "time": time, "player_id": player, "x": x, "y": z,
                     "target": {"x": x, "y": 3, "z": z}, "kind": kind, "label": action,
                     "units": [10, 11], "target_entity_id": None, "confidence": "command_target"})
    return {"duration": 60, "bounds": None, "events": rows,
            "players": [{"id": 1, "name": "玩家甲"}, {"id": 2, "name": "玩家乙"}]}


class ReplayMapViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))
        cls.application.setStyleSheet(STYLE)

    def setUp(self):
        self.model = spatial_model()
        self.view = ReplayMapView(self.model)
        self.view.resize(760, 540)
        self.view.show()
        self.application.processEvents()

    def tearDown(self):
        self.view.cleanup()
        self.view.close()
        self.view.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_time_window_has_only_real_past_events_and_distinct_evidence_kinds(self):
        unchanged = copy.deepcopy(self.model)
        self.view.seek(35)
        self.assertEqual([item["id"] for item in self.view.layer.recent_events], [3, 4])
        self.assertEqual([item["id"] for item in self.view.layer.history_events], [1, 2])
        self.assertNotIn(5, [item["id"] for item in self.view.layer.recent_events])
        self.assertIn("地形/完整单位位置尚未提供", self.view.notice.text())
        self.assertIn("网格仅为坐标参照", self.view.scope.text())
        self.assertIn("菱形为出现点", self.view.scope.text())
        self.assertIn("叉号为阵亡点", self.view.scope.text())
        self.assertFalse(any(isinstance(item, QGraphicsPathItem) for item in self.view.canvas.scene().items()))
        self.assertEqual(self.model, unchanged)
        self.assertGreaterEqual(self.view.canvas.height(), 280)
        self.assertLessEqual(self.view.slider.mapTo(self.view, QPoint()).y() + self.view.slider.height(), self.view.height())

    def test_player_filter_drops_other_players_and_stale_detail(self):
        self.view.select_event(self.view.events[1])
        self.assertIn("玩家甲", self.view.detail_heading.text())
        self.view.seek(35)
        self.view.player_filter.setCurrentIndex(2)
        self.assertEqual([item["id"] for item in self.view.layer.recent_events], [3, 4])
        self.assertEqual(self.view.layer.history_events, [])
        self.assertIsNone(self.view.layer.selected)
        self.assertIn("点选操作点", self.view.detail_heading.text())
        self.view.seek(20)
        self.assertEqual(self.view.layer.recent_events, [])

    def test_click_real_marker_emits_event_and_time_with_ids_and_coordinates(self):
        self.view.seek(10)
        event_signal, time_signal = QSignalSpy(self.view.eventSelected), QSignalSpy(self.view.timeSelected)
        point = self.view.canvas.mapFromScene(QPointF(40, -20))
        QTest.mouseClick(self.view.canvas.viewport(), Qt.LeftButton, pos=point)
        self.assertEqual(event_signal[-1][0]["id"], 2)
        self.assertEqual(time_signal[-1][0], 5)
        self.assertEqual(self.view.current_time, 5)
        self.assertIn("玩家甲", self.view.detail_heading.text())
        self.assertIn("移动目标", self.view.detail_heading.text())
        self.assertIn("10、11", self.view.detail_coordinates.text())
        self.assertIn("横向X 40", self.view.detail_coordinates.text())
        self.assertIn("纵向Z 20", self.view.detail_coordinates.text())
        self.view.seek(0)
        self.assertIsNone(self.view.layer.selected)

    def test_slider_seek_five_seconds_speed_play_pause_and_end(self):
        self.view.slider.setValue(250)
        self.assertEqual(self.view.current_time, 25)
        self.view.back_button.click()
        self.assertEqual(self.view.current_time, 20)
        self.view.forward_button.click()
        self.assertEqual(self.view.current_time, 25)
        self.view.speed.setCurrentIndex(self.view.speed.findData(8))
        self.view.play_button.click()
        QTest.qWait(160)
        self.assertGreater(self.view.current_time, 25.5)
        self.view.play_button.click()
        stopped = self.view.current_time
        QTest.qWait(60)
        self.assertEqual(self.view.current_time, stopped)
        self.view.seek(1000)
        self.assertEqual(self.view.current_time, 60)
        self.assertFalse(self.view.timer.isActive())
        self.view.reset_button.click()
        self.assertEqual(self.view.current_time, 0)
        self.assertEqual(self.view.zoom_label.text(), "100%")

    def test_wheel_keeps_mouse_anchor_and_drag_pans_without_selecting(self):
        canvas = self.view.canvas
        position = QPoint(240, 160)
        before = canvas.mapToScene(position)
        scale = canvas.transform().m11()
        wheel = QWheelEvent(QPointF(position), QPointF(canvas.viewport().mapToGlobal(position)),
                            QPoint(), QPoint(0, 120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        self.application.sendEvent(canvas.viewport(), wheel)
        self.assertGreater(canvas.transform().m11(), scale)
        after = canvas.mapToScene(position)
        self.assertLess((after - before).manhattanLength(), 2 / canvas.transform().m11())
        center = canvas.viewport().rect().center()
        center_before = canvas.mapToScene(center)
        selected = QSignalSpy(self.view.eventSelected)
        end = center + QPoint(35, 20)
        for kind, local, button, buttons in ((QEvent.MouseButtonPress, center, Qt.LeftButton, Qt.LeftButton),
                                            (QEvent.MouseMove, end, Qt.NoButton, Qt.LeftButton),
                                            (QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton)):
            mouse = QMouseEvent(kind, QPointF(local), QPointF(canvas.viewport().mapToGlobal(local)), button, buttons, Qt.NoModifier)
            self.application.sendEvent(canvas.viewport(), mouse)
        self.assertNotEqual(canvas.mapToScene(center), center_before)
        self.assertEqual(len(selected), 0)
        self.view.fit_button.click()
        self.assertEqual(self.view.zoom_label.text(), "100%")

    def test_empty_model_hide_and_cleanup_stop_timers(self):
        self.view.play_button.click()
        self.assertTrue(self.view.timer.isActive())
        self.view.hide()
        self.assertFalse(self.view.timer.isActive())
        self.view.set_model({"events": [{"time": 3, "x": float("nan"), "y": 2}], "duration": 0})
        self.assertEqual(self.view.events, [])
        self.view.toggle_playback()
        self.assertFalse(self.view.timer.isActive())
        self.view.cleanup()
        self.view.cleanup()
        self.view.toggle_playback()
        self.assertFalse(self.view.timer.isActive())

    def test_late_evidence_extends_reachable_time_but_preserves_original_duration(self):
        model = spatial_model()
        model["duration"] = 12
        model["events"] = model["events"][:2]
        model["events"][-1]["time"] = 20
        self.view.set_model(model)
        self.assertEqual(self.view.recorded_duration, 12)
        self.assertEqual(self.view.duration, 20)
        self.assertIn("录像 00:12", self.view.notice.text())
        self.assertIn("时间轴至 00:20", self.view.notice.text())
        self.view.seek(20)
        self.assertEqual(self.view.current_time, 20)
        self.assertEqual(self.view.layer.recent_events[-1]["id"], 2)
        self.assertEqual(model["events"][-1]["time"], 20, "不拉伸原始操作时钟")


if __name__ == "__main__":
    unittest.main()
