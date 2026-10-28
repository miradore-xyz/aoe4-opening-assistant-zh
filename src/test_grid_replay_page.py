"""网格主页面接线：不依赖地形图，筛选/选点/展开共用真实指令模型。"""
import os
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication
from replay_analysis import analyze_replay
from replay_page import ReplayPage
from test_replay_page import fixture


class GridReplayPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.page = ReplayPage(self.tmp.name)
        self.page.resize(1100, 800)
        self.page.show()
        self.app.processEvents()

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()
        self.tmp.cleanup()

    def load(self):
        raw = fixture()
        for index, event in enumerate(raw['commands']):
            if event.get('target'):
                event['target']['z'] = index * 5
        report = analyze_replay(raw)
        self.page._show_report(report, '网格测试')
        self.app.processEvents()
        return self.page.map_view

    def test_empty_and_real_report_do_not_create_reference_map(self):
        self.assertIsNone(self.page.map_view.official_map)
        view = self.load()
        self.assertEqual(view.view_mode.currentData(), 'records')
        self.assertEqual(view.view_mode.findData('reference'), -1)
        self.assertIs(view.map_stack.currentWidget(), view.canvas)
        self.assertFalse(hasattr(self.page, '_browse_official_maps'))
        self.assertGreater(len(view.events), 0)
        self.assertFalse(self.page.spatial_model['coverage']['unit_positions_available'])

    def test_player_filter_and_selected_operation_use_real_model(self):
        view = self.load()
        choice = self.page.grid_player_filter
        self.assertGreater(choice.count(), 1)
        choice.setCurrentIndex(1)
        self.assertEqual(view.player_filter.currentData(), choice.currentData())
        self.assertTrue(all(e['player_id'] == choice.currentData() for e in view.layer.filtered_events))
        event = view.layer.filtered_events[0]
        view.select_event(event)
        self.assertIn(event['label'], self.page.grid_event_detail.text())
        self.assertIn('目标', self.page.grid_event_detail.text())

    def test_fullscreen_grid_returns_to_same_time_without_map_assets(self):
        view = self.load()
        view.seek(30)
        self.page._expand_map()
        self.app.processEvents()
        dialog = self.page._map_dialog
        self.assertIsNone(dialog.map_view.official_map)
        self.assertEqual(dialog.map_view.current_time, 30)
        dialog.map_view.seek(60)
        dialog.close()
        self.app.processEvents()
        self.assertIsNone(self.page._map_dialog)
        self.assertEqual(view.current_time, 60)
        self.assertFalse(view._cleaned)


if __name__ == '__main__':
    unittest.main()
