"""官方图鉴的真实Qt交互：参考身份、独立画布、检索、降级和全屏状态。"""
import copy
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QColor, QImage, QMouseEvent, QWheelEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QGraphicsPixmapItem

from official_map_view import OfficialMapView
from replay_map_view import ReplayMapView
from replay_page import ReplayPage
from test_replay_fullscreen_state import fullscreen_report
from visual import STYLE


class _LegacyReferenceView(OfficialMapView):
    """保留旧二维规则参考组件的隔离合约；完整实例的生产接线另有专测。"""
    # 旧组件测试仅模拟宿主所需的内置资料可用性，不改变旧图鉴的绘制。
    catalog = SimpleNamespace(data_status=lambda: {"state": "ready"})

    def cleanup(self):
        self._image_cache.clear()


class OfficialMapViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        cls.application.setStyleSheet(STYLE)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.preview_path = str(Path(self.directory.name) / "official-preview.png")
        preview = QImage(720, 720, QImage.Format_ARGB32)
        preview.fill(QColor("#2b5579"))
        self.assertTrue(preview.save(self.preview_path))
        self.entries = [
            {"key": "dry", "name_zh": "干燥阿拉伯", "name_en": "Dry Arabia", "config_path": "maps/dry.lua"},
            {"key": "river", "name_zh": "河间地", "name_en": "Riverlands", "config_path": "maps/river.lua"},
            {"key": "missing", "name_zh": "缺图地图", "name_en": "Missing", "config_path": "maps/missing.lua"}]
        self.patches = [
            patch("replay_map_view.FullMapView", _LegacyReferenceView),
            patch("official_map_view.catalog_entries", return_value=self.entries),
            patch("official_map_view.find_official_map", side_effect=lambda value: next(
                (entry for entry in self.entries if value in (entry["key"], entry["name_zh"], entry["name_en"])), None)),
            patch("official_map_view.map_layout_reference", side_effect=lambda entry: {"test_key": entry["key"]}
                  if entry["key"] == "dry" else None),
            patch("official_map_view.map_preview_path", side_effect=lambda entry: self.preview_path
                  if entry["key"] in ("dry", "river") else None),
            patch("official_map_view.render_layout_reference", side_effect=self.render_layout)]
        for replacement in self.patches:
            replacement.start()
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            if hasattr(window, "cleanup"):
                window.cleanup()
            window.close()
            window.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()
        for replacement in reversed(self.patches):
            replacement.stop()
        self.directory.cleanup()

    @staticmethod
    def render_layout(_layout, size=1536):
        """测试材料只验证显示管线，实际地形渲染另有真实官方数据测试。"""
        image = QImage(size, size, QImage.Format_ARGB32)
        image.fill(QColor("#957f45"))
        return image

    def make_atlas(self, value="Dry Arabia"):
        view = OfficialMapView()
        self.windows.append(view)
        view.resize(760, 520)
        view.set_match_map(value)
        view.show()
        QTest.qWait(10)
        return view

    def make_replay_view(self):
        model = {"map_name": "Dry Arabia", "duration": 120,
                 "players": [{"id": 101, "name": "同名", "profile_id": 880001},
                             {"id": 202, "name": "同名", "profile_id": 990002}],
                 "events": [{"id": 1, "time": 5, "player_id": 101, "x": 20, "y": 30,
                             "kind": "command", "label": "移动目标"},
                            {"id": 2, "time": 15, "player_id": 202, "x": 50, "y": 40,
                             "kind": "command", "label": "移动目标"}]}
        view = ReplayMapView(model)
        self.windows.append(view)
        view.resize(760, 520)
        view.show()
        QTest.qWait(10)
        return view

    def test_auto_match_search_and_visible_reference_identity(self):
        atlas = self.make_atlas()
        self.assertEqual(atlas.entry["key"], "dry")
        self.assertEqual(atlas.actual_representation, "layout")
        self.assertTrue(atlas.source_label.isVisible())
        self.assertEqual(atlas.source_label.text(), "官方地图参考 · 非本场实战地形")
        self.assertIn("并非本场真实矿点", atlas.status_label.text())
        self.assertEqual(atlas.canvas.picture.pixmap().toImage().pixelColor(10, 10), QColor("#957f45"))
        completer = atlas.map_selector.completer()
        completer.setCompletionPrefix("Arabia")
        self.assertEqual(completer.completionCount(), 1)
        self.assertIn("Dry Arabia", completer.currentCompletion())
        self.assertEqual(atlas.map_selector.count(), 3)

    def test_unknown_map_never_defaults_to_another_map_and_missing_assets_remain_empty(self):
        atlas = self.make_atlas("未知本场地图")
        self.assertIsNone(atlas.entry)
        self.assertEqual(atlas.map_selector.currentIndex(), -1)
        self.assertIsNone(atlas.canvas.picture)
        self.assertIn("未匹配", atlas.status_label.text())
        atlas.select_map("missing")
        self.assertIsNone(atlas.actual_representation)
        self.assertIsNone(atlas.canvas.picture)
        self.assertEqual(atlas.canvas.scene().items(), [])
        self.assertIn("未绘制替代地形", atlas.status_label.text())

    def test_keyboard_search_selects_a_map_without_loading_a_replay(self):
        atlas = self.make_atlas("未知本场地图")
        atlas.activateWindow()
        editor = atlas.map_selector.lineEdit()
        editor.setFocus()
        editor.setText("River")
        completer = atlas.map_selector.completer()
        completer.setCompletionPrefix("River")
        completer.complete()
        QTest.qWait(10)
        popup = completer.popup()
        QTest.keyClick(popup, Qt.Key_Down)
        QTest.keyClick(popup, Qt.Key_Return)
        QTest.qWait(10)
        self.assertEqual(atlas.entry["key"], "river")
        self.assertEqual(atlas.actual_representation, "preview")

    def test_layout_falls_back_to_official_preview_and_user_can_switch_representations(self):
        atlas = self.make_atlas("Riverlands")
        self.assertEqual(atlas.actual_representation, "preview")
        self.assertEqual(atlas.representation.currentData(), "preview")
        self.assertIn("布局参考暂缺", atlas.status_label.text())
        self.assertEqual(atlas.canvas.picture.pixmap().toImage().pixelColor(10, 10), QColor("#2b5579"))
        atlas.select_map("dry")
        self.assertEqual(atlas.actual_representation, "preview")
        atlas.representation.setCurrentIndex(atlas.representation.findData("layout"))
        self.assertEqual(atlas.actual_representation, "layout")
        self.assertEqual(atlas.canvas.picture.pixmap().toImage().pixelColor(10, 10), QColor("#957f45"))

    def test_reference_mode_pauses_and_disables_time_without_overlaying_real_events(self):
        view = self.make_replay_view()
        unchanged = copy.deepcopy(view.model)
        view.player_filter.setCurrentIndex(view.player_filter.findData(202))
        view.speed.setCurrentIndex(view.speed.findData(4))
        view.select_event(view.events[1])
        view.seek(35)
        view.canvas.zoom_at(view.canvas.viewport().rect().center(), 2)
        view.toggle_playback()
        before = view.capture_view_state()
        view.view_mode.setCurrentIndex(view.view_mode.findData("reference"))
        self.assertFalse(view.timer.isActive())
        self.assertTrue(view.canvas.isHidden())
        self.assertIs(view.map_stack.currentWidget(), view.official_map)
        self.assertTrue(view.official_map.isVisible())
        self.assertFalse(view.slider.isEnabled())
        self.assertFalse(view.play_button.isEnabled())
        self.assertFalse(view.speed.isEnabled())
        self.assertNotIn(view.layer, view.official_map.canvas.scene().items())
        self.assertTrue(all(isinstance(item, QGraphicsPixmapItem) for item in view.official_map.canvas.scene().items()))
        view.play_button.click()
        view.back_button.click()
        view.forward_button.click()
        view.toggle_playback()
        view.fit_button.click()
        view.reset_button.click()
        self.assertFalse(view.timer.isActive())
        self.assertEqual(view.current_time, 35)
        view.view_mode.setCurrentIndex(0)
        after = view.capture_view_state()
        for key in ("time", "player_id", "speed", "selected_event_id"):
            self.assertEqual(after[key], before[key], key)
        for key in ("extent", "zoom", "user_camera"):
            self.assertEqual(after["camera"][key], before["camera"][key], key)
        # 两种工具栏高度不同，Qt整像素滚动条在重排时允许两个物理像素的中心量化误差。
        for current, original in zip(after["camera"]["center"], before["camera"]["center"]):
            self.assertAlmostEqual(current, original, delta=2 / view.canvas.transform().m11() + 1e-9)
        self.assertEqual(view.model, unchanged)
        self.assertTrue(view.slider.isEnabled())

    def test_reference_zoom_drag_and_fit_use_only_its_own_camera(self):
        atlas = self.make_atlas()
        canvas = atlas.canvas
        point = canvas.viewport().rect().center() + QPoint(45, 25)
        before = canvas.mapToScene(point)
        wheel = QWheelEvent(QPointF(point), QPointF(canvas.viewport().mapToGlobal(point)), QPoint(), QPoint(0, 120),
                            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        self.application.sendEvent(canvas.viewport(), wheel)
        self.assertAlmostEqual(canvas.zoom_ratio(), 1.2, places=6)
        after = canvas.mapToScene(point)
        self.assertLess((before - after).manhattanLength(), .01)
        initial = canvas.mapToScene(canvas.viewport().rect().center())
        start = canvas.viewport().rect().center()
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=start)
        target = start + QPoint(45, 30)
        move = QMouseEvent(QEvent.MouseMove, QPointF(target), QPointF(canvas.viewport().mapToGlobal(target)),
                           Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        self.application.sendEvent(canvas.viewport(), move)
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=target)
        self.assertTrue(canvas.user_camera)
        self.assertGreater((canvas.mapToScene(canvas.viewport().rect().center()) - initial).manhattanLength(), 1)
        atlas.fit_all()
        self.assertAlmostEqual(canvas.zoom_ratio(), 1)
        self.assertFalse(canvas.user_camera)

    def test_fullscreen_round_trip_keeps_reference_map_kind_and_camera(self):
        page = ReplayPage(self.directory.name)
        self.windows.append(page)
        page.resize(1100, 780)
        page._show_report(fullscreen_report())
        page.show()
        QTest.qWait(10)
        view = page.map_view
        view.seek(38)
        view.view_mode.setCurrentIndex(view.view_mode.findData("reference"))
        atlas = view.official_map
        atlas.select_map("river")
        atlas.canvas.zoom_at(atlas.canvas.viewport().rect().center(), 2.4)
        atlas.canvas.centerOn(QPointF(300, 320))
        expected = atlas.capture_view_state()
        page._expand_map()
        QTest.qWait(15)
        dialog = page._map_dialog
        self.assertTrue(dialog.isFullScreen())
        large = dialog.map_view
        self.assertEqual(large.view_mode.currentData(), "reference")
        self.assertEqual(large.current_time, 38)
        self.assertFalse(large.slider.isEnabled())
        actual = large.official_map.capture_view_state()
        self.assertEqual(actual["map_key"], expected["map_key"])
        self.assertEqual(actual["representation"], "preview")
        self.assertAlmostEqual(actual["camera"]["zoom"], 2.4)
        for a, b in zip(actual["camera"]["center"], expected["camera"]["center"]):
            self.assertAlmostEqual(a, b, delta=3)
        large.official_map.select_map("dry")
        large.official_map.representation.setCurrentIndex(0)
        dialog.return_button.click()
        self.assertEqual(view.view_mode.currentData(), "reference")
        self.assertEqual(view.official_map.entry["key"], "dry")
        self.assertEqual(view.official_map.actual_representation, "layout")
        self.assertEqual(view.current_time, 38)

    def test_small_reference_view_keeps_source_and_disabled_time_axis_visible(self):
        view = self.make_replay_view()
        view.resize(760, 520)
        view.view_mode.setCurrentIndex(view.view_mode.findData("reference"))
        QTest.qWait(10)
        source = view.official_map.source_label
        self.assertLessEqual(source.mapTo(view, QPoint(0, source.height())).y(), view.height())
        self.assertLessEqual(view.slider.mapTo(view, QPoint(0, view.slider.height())).y(), view.height())
        self.assertGreaterEqual(view.official_map.canvas.height(), 200)
        self.assertFalse(view.grab().isNull())


if __name__ == "__main__":
    unittest.main()
