"""诊断定位必须保留来源命名空间、原秒数与跨全屏镜头。"""
import copy
import os
import unittest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import QPointF, Qt
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication
from replay_map_view import ReplayMapView
from test_replay_map_view import spatial_model


class DiagnosisMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = ReplayMapView(spatial_model())
        self.view.resize(800, 600)
        self.view.show()
        self.app.processEvents()
        self.payload = {"id": "battle-A", "time_range": [12, 18], "position": {"x": 30, "y": 40},
                        "events": [{"source": "STLS.lostEntities", "offset": 123, "time": 14,
                                    "target_player_id": 1, "target_entity_id": 50001,
                                    "target_type": "spearman", "target_x": 30, "target_y": 40}]}

    def tearDown(self):
        self.view.cleanup()
        self.view.close()

    def test_focus_does_not_seek_or_play_and_click_preserves_clock(self):
        self.view.seek(20)
        unchanged = copy.deepcopy(self.payload)
        self.assertTrue(self.view.focus_diagnosis(self.payload))
        self.assertEqual(self.payload, unchanged)
        self.assertEqual(self.view.current_time, 20)
        self.assertFalse(self.view.timer.isActive())
        spy = QSignalSpy(self.view.diagnosisEvidenceSelected)
        position = self.view.canvas.mapFromScene(QPointF(30, -40))
        QTest.mouseClick(self.view.canvas.viewport(), Qt.LeftButton, pos=position)
        self.assertEqual(len(spy), 1)
        self.assertEqual(spy[0][0]["diagnostic_id"], "battle-A")
        self.assertEqual(spy[0][0]["source"], "STLS.lostEntities")
        self.assertEqual(self.view.current_time, 20)

    def test_focus_transfer_and_fullscreen_retains_original_analysis_rail(self):
        self.view.focus_diagnosis(self.payload)
        self.view.seek(20)
        state = self.view.capture_view_state()
        target = ReplayMapView(spatial_model())
        try:
            target.set_fullscreen_mode(True)
            self.assertIsNotNone(target.advanced_analysis)
            self.assertEqual(len(target.advanced_analysis.buttons), 18)
            target.restore_view_state(state)
            self.assertEqual(target.canvas.diagnosis_points[0]["time"], 14)
            self.assertEqual(target.current_time, 20)
            # 修订23的观看状态没有新开关，恢复时仍须保留原有分析入口。
            state.pop("advanced_exploration_enabled", None)
            target.restore_view_state(state)
            self.assertTrue(target.advanced_analysis._enabled)
        finally:
            target.cleanup()
            target.close()

    def test_invalid_position_is_not_invented(self):
        self.assertFalse(self.view.focus_diagnosis({"id": "x", "events": [{"time": 2}]}))


if __name__ == "__main__":
    unittest.main()
