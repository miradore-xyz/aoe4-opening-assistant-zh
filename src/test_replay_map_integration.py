"""二维地图页面联动：验证真实控件的时间同步、展开与资源清理。"""
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication

from match_detail import normalize_summary
from replay_analysis import analyze_replay
from replay_page import ReplayPage
from test_match_detail import summary
from test_replay_page import fixture


class ReplayMapIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.page = ReplayPage(self.temporary.name)
        raw = fixture()
        for index, event in enumerate(raw["commands"]):
            if event.get("target"):
                event["target"]["z"] = index * 5
        self.report = analyze_replay(raw)
        detail = summary()
        detail["duration"] = 360
        self.detail = normalize_summary(detail, 10, 101)
        self.page.resize(1100, 780)
        self.page._show_report(self.report, "合成地图", self.detail)
        self.page.show()
        self.app.processEvents()

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()
        self.temporary.cleanup()

    def test_map_is_primary_and_time_controls_fit_normal_window(self):
        self.assertEqual(len(self.page.spatial_model["events"]), 30)
        self.assertFalse(self.page.map_view.isHidden())
        self.assertTrue(self.page.detail_view.isHidden())
        self.assertTrue(self.page.raw_report.isHidden())
        slider = self.page.map_view.slider
        viewport = self.page.scroll.viewport()
        position = slider.mapTo(viewport, QPoint(0, slider.height()))
        self.assertLessEqual(position.y(), viewport.height())
        self.assertGreaterEqual(self.page.map_view.canvas.height(), 280)

    def test_map_and_chart_use_seconds_without_duration_scaling_or_recursion(self):
        self.page.map_view.seek(150)
        self.assertEqual(self.page.map_view.current_time, 150)
        self.assertIn("02:30", self.page.detail_view.selected_label.text())
        self.page.detail_view.focus_time(240)
        self.assertEqual(self.page.map_view.current_time, 240)
        self.page.detail_view.focus_time(340)
        self.assertEqual(self.page.map_view.current_time, 300)
        self.assertIn("05:40", self.page.detail_view.selected_label.text())

    def test_raw_event_selection_seeks_map_without_refiltering(self):
        self.page.raw_toggle.click()
        self.page.timeline_table.selectRow(5)
        self.assertEqual(self.page.map_view.current_time, 50)
        self.assertEqual(self.page.page_index, 0)
        self.assertEqual(len(self.page.filtered_events), 31)

    def test_expanded_map_keeps_time_and_main_map_remains_usable_after_close(self):
        main = self.page.map_view
        main.seek(120)
        main.toggle_playback()
        self.page.expand_map_button.click()
        self.app.processEvents()
        dialog = self.page._map_dialog
        self.assertIsNotNone(dialog)
        self.assertFalse(main.timer.isActive())
        self.assertFalse(main._cleaned)
        self.assertEqual(dialog.map_view.current_time, 120)
        dialog.map_view.seek(180)
        self.assertEqual(main.current_time, 180)
        self.assertIn("03:00", self.page.detail_view.selected_label.text())
        dialog.map_view.toggle_playback()
        dialog.close()
        self.assertIsNone(self.page._map_dialog)
        self.assertFalse(dialog.map_view.timer.isActive())
        main.toggle_playback()
        self.assertTrue(main.timer.isActive())

    def test_replacing_report_stops_both_old_maps_and_discards_dialog(self):
        old = self.page.map_view
        self.page._expand_map()
        dialog = self.page._map_dialog
        dialog.map_view.toggle_playback()
        self.page._show_report(self.report)
        self.assertTrue(old._cleaned)
        self.assertTrue(dialog.map_view._cleaned)
        self.assertFalse(dialog.map_view.timer.isActive())
        self.assertIsNone(self.page._map_dialog)
        self.assertIsNot(self.page.map_view, old)
        self.page.map_view.seek(30)
        self.assertEqual(self.page.map_view.current_time, 30)

    def test_hiding_page_pauses_without_permanently_disabling_map(self):
        view = self.page.map_view
        view.seek(50)
        view.toggle_playback()
        self.page.hide()
        self.assertFalse(view.timer.isActive())
        self.assertFalse(view._cleaned)
        self.page.show()
        view.seek(80)
        view.toggle_playback()
        self.assertEqual(view.current_time, 80)
        self.assertTrue(view.timer.isActive())


if __name__ == "__main__":
    unittest.main()
