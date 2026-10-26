"""完整地图的生产接线：参考边界、资料位置、镜头和复盘时间互不混用。"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QEvent, QPoint
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from full_map_view import FullMapView
from map_pack_location import load_map_pack_location, save_map_pack_location
from replay_map_view import ReplayMapView
from test_full_map_view import FakeCatalog


class _CatalogWithLocation(FakeCatalog):
    """只替换资料输入，视图与复盘控件均使用生产类。"""
    def __init__(self, builtin_ready=True):
        super().__init__()
        self.root = Path('builtin' if builtin_ready else 'missing.aoe4maps')
        self.missing = not builtin_ready

    def set_root(self, path):
        self.root = Path(path)
        self.missing = self.root.name != 'builtin' and not self.root.is_file()


class FullMapIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.patch = patch('full_map_catalog.FullMapCatalog', side_effect=_CatalogWithLocation)
        self.patch.start()
        self.windows = []
        self.model = {'map_name': 'first', 'duration': 120, 'players': [{'id': 1, 'name': '真实局内编号'}],
                      'events': [{'id': 'move-1', 'player_id': 1, 'time': 15, 'x': 500, 'y': 300, 'kind': 'command'}]}

    def tearDown(self):
        for view in self.windows:
            view.cleanup()
            view.close()
            view.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()
        self.patch.stop()
        self.temporary.cleanup()

    def make_view(self):
        view = ReplayMapView(self.model, map_data_dir=self.directory)
        self.windows.append(view)
        view.resize(900, 650)
        view.show()
        QTest.qWait(5)
        return view

    def test_production_uses_full_map_and_reference_never_receives_match_event_layer(self):
        view = self.make_view()
        self.assertIsInstance(view.official_map, FullMapView)
        self.assertEqual(view.official_map.entry['key'], 'first')
        self.assertEqual(view.official_map.canvas.style, 'natural')
        view.seek(45)
        view.view_mode.setCurrentIndex(view.view_mode.findData('reference'))
        self.application.processEvents()
        self.assertIs(view.map_stack.currentWidget(), view.official_map)
        self.assertFalse(view.fit_button.isVisible())
        self.assertFalse(view.reset_button.isVisible())
        self.assertNotIn(view.layer, view.official_map.canvas.scene().items())
        self.assertFalse(view.slider.isEnabled())
        self.assertEqual(view.current_time, 45)
        self.assertFalse(view.map_pack_button.isVisible())
        view.view_mode.setCurrentIndex(view.view_mode.findData('records'))
        self.assertTrue(view.slider.isEnabled())
        self.assertTrue(view.fit_button.isVisible())
        self.assertEqual(view.current_time, 45)
        self.assertEqual(len(view.events), 1)

    def test_select_pack_preserves_camera_time_and_remembers_explicit_location(self):
        view = self.make_view()
        view.view_mode.setCurrentIndex(view.view_mode.findData('reference'))
        view.seek(55)
        canvas = view.official_map.canvas
        canvas.zoom_at(QPoint(260, 180), 2.0)
        before = view.capture_view_state()
        chosen = self.directory / 'chosen.aoe4maps'
        chosen.write_bytes(b'isolated-input-handled-by-fake-catalog')
        self.assertTrue(view.use_map_pack(chosen))
        self.assertEqual(load_map_pack_location(self.directory), chosen)
        after = view.capture_view_state()
        self.assertEqual(after['time'], before['time'])
        self.assertEqual(after['view_mode'], before['view_mode'])
        self.assertAlmostEqual(after['official_map']['camera']['zoom'], before['official_map']['camera']['zoom'])
        self.assertEqual(view.model, self.model)
        reopened = self.make_view()
        self.assertEqual(reopened.official_map.catalog.root, Path('builtin'))

    def test_saved_external_pack_is_used_when_builtin_tiles_are_missing(self):
        chosen = self.directory / 'chosen.aoe4maps'
        chosen.write_bytes(b'isolated-input-handled-by-fake-catalog')
        save_map_pack_location(self.directory, chosen)
        self.patch.stop()
        self.patch = patch('full_map_catalog.FullMapCatalog', side_effect=lambda: _CatalogWithLocation(False))
        self.patch.start()
        view = self.make_view()
        self.assertEqual(view.official_map.catalog.root, chosen)
        self.assertEqual(view.official_map.catalog.data_status()['state'], 'ready')

    def test_builtin_compact_maps_take_priority_over_saved_legacy_pack_during_restore(self):
        legacy = self.directory / 'legacy.aoe4maps'
        legacy.write_bytes(b'legacy-placeholder')
        save_map_pack_location(self.directory, legacy)
        view = self.make_view()
        self.assertEqual(view.official_map.catalog.root, Path('builtin'))
        state = view.capture_view_state()
        view.restore_view_state(state)
        self.assertEqual(view.official_map.catalog.root, Path('builtin'))
        view.view_mode.setCurrentIndex(view.view_mode.findData('reference'))
        self.assertFalse(view.map_pack_button.isVisible())

    def test_unreadable_selected_pack_does_not_overwrite_working_location(self):
        view = self.make_view()
        original = view.official_map.catalog.root
        view.official_map.catalog.missing = True
        self.assertFalse(view.use_map_pack(self.directory / 'wrong.aoe4maps'))
        self.assertEqual(view.official_map.catalog.root, original)
        self.assertIsNone(load_map_pack_location(self.directory))
        self.assertEqual(len(view.events), 1)


if __name__ == '__main__':
    unittest.main()
