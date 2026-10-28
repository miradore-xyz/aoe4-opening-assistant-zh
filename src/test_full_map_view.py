"""原生完整地图：非方形分块、受限内存、同源实体、镜头与参考边界。"""
import copy
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QBuffer, QEvent, QIODevice, QPoint, QPointF, Qt
from PyQt5.QtGui import QColor, QImage, QPixmap, QTransform
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication, QGraphicsView

from full_map_view import FullMapView, _TileCache, _TreeLayerCache, build_entity_index


def png(width, height, color):
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


class FakeCatalog:
    """合成有色图块与保存实体，不访问真实游戏资料、网络或用户窗口。"""
    def __init__(self):
        self.missing, self.closed = False, False
        self.entity_reads, self.tile_reads, self.payloads = [], [], {}
        self.models, self.data = {}, {}
        for key, digest in (("first", "a" * 64), ("second", "b" * 64)):
            levels = [{"level": 0, "width": 257, "height": 195, "cols": 1, "rows": 1, "tile_size": 512, "image_pixel_scale": 4},
                      {"level": 1, "width": 513, "height": 389, "cols": 2, "rows": 1, "tile_size": 512, "image_pixel_scale": 2},
                      {"level": 2, "width": 1025, "height": 777, "cols": 3, "rows": 2, "tile_size": 512, "image_pixel_scale": 1}]
            render = {"width": 1025, "height": 777, "valid_content_size": [1025, 777], "tile_size": 512,
                      "pixels_per_source_unit": 1, "source_grid": {"width": 1026, "height": 778},
                      "source_sha256": digest, "styles": {style: {"levels": copy.deepcopy(levels)} for style in ("natural",)}}
            self.models[key] = {"key": key, "name_zh": "第一张参考地图" if key == "first" else "第二张参考地图",
                                "render": render, "not_match_reconstruction": True, "source_sha256": digest}
            points = [{"id": "tree-" + key, "category": "tree", "blueprint_reference_raw": "tree_a", "scale": 1,
                       "position": {"x": 100, "y": 4, "z": 100}, "grid_position": {"column": 100, "row": 100}},
                      {"id": "gold-" + key, "category": "gold", "blueprint_reference_raw": "resource_gold_deposit", "scale": 1,
                       "position": {"x": 500, "y": 7.5, "z": 300}, "grid_position": {"column": 500, "row": 300}},
                      {"id": "spawn-" + key, "category": "unknown", "blueprint_reference_raw": "starting_position_shared_territory", "scale": 1,
                       "position": {"x": 700, "y": 8, "z": 500}, "grid_position": {"column": 700, "row": 500}},
                      {"id": "logical-" + key, "category": "unknown", "blueprint_reference_raw": "dynamic_spawn_marker", "scale": 1,
                       "position": {"x": 700, "y": 8, "z": 501}, "grid_position": {"column": 700, "row": 501}}]
            self.data[key] = {"kind": "official_generated_entities", "schema_version": 1,
                              "source_scenario": {"sha256": digest}, "counts": {"entities": len(points)},
                              "coordinates": {"calibrated": True, "source_sha256": digest,
                                  "world_to_grid": {"origin_x": 0, "origin_z": 0, "column_x": 1, "column_z": 0, "row_x": 0, "row_z": 1}},
                              "entities": points}

    def entries(self):
        return copy.deepcopy(list(self.models.values()))

    def get(self, key):
        if key == "第一张参考地图":
            key = "first"
        return copy.deepcopy(self.models.get(key))

    def load_entities(self, key):
        self.entity_reads.append(key)
        return copy.deepcopy(self.data[key])

    def data_status(self, key=None):
        return {"state": "tiles_missing" if self.missing else "ready", "message_zh": "地图资料包未安装，请选择完整资料包。"}

    def close(self):
        self.closed = True

    def tile_bytes(self, key, style, level, x, y):
        identity = key, style, level, x, y
        self.tile_reads.append(identity)
        if self.missing:
            return None
        if identity not in self.payloads:
            selected = self.models[key]["render"]["styles"][style]["levels"][level]
            if not (0 <= x < selected["cols"] and 0 <= y < selected["rows"]):
                return None
            color = "#d0b87a" if style == "medieval" else "#49a65c" if key == "first" else "#3d6da5"
            if x == 2:
                color = "#ed5e48"
            self.payloads[identity] = png(min(512, selected["width"] - x * 512), min(512, selected["height"] - y * 512), color)
        return self.payloads[identity]


class FullMapViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.catalog = FakeCatalog()
        self.views = []

    def tearDown(self):
        for view in self.views:
            view.cleanup()
            view.close()
            view.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()
        self.views.clear()

    def view(self, width=760, height=520, show=True):
        view = FullMapView(catalog=self.catalog)
        self.views.append(view)
        view.resize(width, height)
        if show:
            view.show()
            self.application.processEvents()
        return view

    def paint(self, view):
        view.canvas.viewport().update()
        self.application.processEvents()
        return view.canvas.viewport().grab().toImage()

    def test_reference_scope_default_style_and_only_current_map_is_loaded(self):
        view = self.view()
        self.assertEqual(self.catalog.entity_reads, [])
        self.assertTrue(view.set_map("first"))
        self.paint(view)
        self.assertEqual(self.catalog.entity_reads, ["first"])
        self.assertTrue(self.catalog.tile_reads)
        self.assertTrue(all(row[0] == "first" for row in self.catalog.tile_reads))
        self.assertEqual(view.canvas.style, "natural")
        self.assertFalse(hasattr(view, "representation"))
        self.assertIn("非本场实战", view.source_label.text())
        self.assertEqual(len(view.canvas.index["points"]), 3)
        self.assertEqual(view.canvas.index["points"][2]["role"], "spawn_marker")
        self.assertEqual(view.canvas.extent.width() / view.canvas.extent.height(), 1025 / 777)

    def test_odd_non_square_boundary_uses_real_one_pixel_edge_tile(self):
        view = self.view()
        view.set_map("first")
        view.canvas.setTransform(QTransform.fromScale(1, 1))
        view.canvas.centerOn(QPointF(950, 700))
        view.canvas.user_camera = True
        image = self.paint(view)
        self.assertEqual(view.canvas.last_level, 2)
        self.assertIn((2, 1), view.canvas.last_tiles)
        picture = view.canvas.tile_cache.items[("first", "natural", 2, 2, 1)][0]
        self.assertEqual((picture.width(), picture.height()), (1, 265))
        point = view.canvas.mapFromScene(QPointF(1024, 700))
        self.assertTrue(image.rect().contains(point))
        color = image.pixelColor(point)
        self.assertGreater(color.red(), color.green(), "最后一列应显示红色1像素边块，不能拉伸为512或丢失")

    def test_zoom_anchor_and_pan_preserve_entity_positions(self):
        view = self.view()
        view.set_map("first")
        before = copy.deepcopy(view.canvas.index["points"])
        cursor = QPoint(180, 170)
        anchor = view.canvas.mapToScene(cursor)
        view.canvas.zoom_at(cursor, 2)
        self.assertLess((view.canvas.mapToScene(cursor) - anchor).manhattanLength(), 3)
        camera = view.canvas.capture_camera()
        self.paint(view)
        self.assertEqual(view.canvas.index["points"], before)
        self.assertAlmostEqual(view.canvas.zoom_ratio(), camera["zoom"])
        self.assertEqual(view.canvas.style, "natural")
        center = view.canvas.mapToScene(view.canvas.viewport().rect().center())
        view.canvas.centerOn(center + QPointF(80, 45))
        moved = view.canvas.mapToScene(view.canvas.viewport().rect().center())
        self.assertGreater((moved - center).manhattanLength(), 100)

    def test_click_selects_saved_resource_and_spawn_is_not_a_villager(self):
        view = self.view()
        view.set_map("first")
        spy = QSignalSpy(view.entitySelected)
        point = view.canvas.mapFromScene(QPointF(500, 300))
        QTest.mouseClick(view.canvas.viewport(), Qt.LeftButton, pos=point)
        self.assertEqual(len(spy), 1)
        self.assertEqual(spy[0][0]["position"], {"x": 500, "y": 7.5, "z": 300})
        self.assertIn("金矿", view.status_label.text())
        self.assertIn("高度 Y 7.50", view.status_label.text())
        QTest.mouseClick(view.canvas.viewport(), Qt.LeftButton, pos=view.canvas.mapFromScene(QPointF(700, 500)))
        self.assertIn("玩家出生标记", view.status_label.text())
        self.assertIn("不表示已生成村民或城镇中心", view.status_label.toolTip())

    def test_source_mismatch_and_changed_saved_coordinates_cannot_draw_resources(self):
        render = self.catalog.models["first"]["render"]
        data = self.catalog.load_entities("first")
        self.assertIsNotNone(build_entity_index(data, render))
        data["coordinates"]["source_sha256"] = "b" * 64
        self.assertIsNone(build_entity_index(data, render))
        data = self.catalog.load_entities("first")
        data["entities"][0]["grid_position"]["column"] += 2
        self.assertIsNone(build_entity_index(data, render))

    def test_cache_limits_apply_to_decoded_pixmaps_and_switch_clears_old_map(self):
        cache = _TileCache(max_tiles=3, max_bytes=2 * 512 * 512 * 4)
        payload = png(512, 512, "#668899")
        for index in range(8):
            cache.get((index,), lambda: payload)
        self.assertLessEqual(len(cache.items), 3)
        self.assertLessEqual(cache.bytes, cache.max_bytes)
        self.assertEqual(len(cache.items), 2)
        view = self.view()
        view.set_map("first")
        self.paint(view)
        view.set_map("second")
        self.paint(view)
        self.assertTrue(view.canvas.tile_cache.items)
        self.assertTrue(all(key[0] == "second" for key in view.canvas.tile_cache.items))
        self.assertEqual(self.catalog.entity_reads, ["first", "second"])

    def test_hidden_fullscreen_restore_keeps_map_style_camera_and_selection(self):
        original = self.view()
        original.set_map("first")
        original.canvas.zoom_at(original.canvas.viewport().rect().center(), 2.5)
        original.canvas.selected = original.canvas.index["points"][1]
        state = original.capture_view_state()
        state["representation"] = "medieval"  # 旧版手绘存档应迁移为唯一高程版。
        restored = self.view(width=1100, height=720, show=False)
        restored.restore_view_state(state)
        restored.show()
        self.application.processEvents()
        self.paint(restored)
        saved = restored.capture_view_state()
        self.assertEqual((saved["map_key"], saved["representation"], saved["selected_entity_id"]),
                         (state["map_key"], "natural", state["selected_entity_id"]))
        self.assertAlmostEqual(saved["camera"]["zoom"], 2.5)
        for value, expected in zip(saved["camera"]["center"], state["camera"]["center"]):
            self.assertAlmostEqual(value, expected, delta=3)

    def test_missing_external_pack_retains_catalog_without_detached_entity_points(self):
        self.catalog.missing = True
        view = self.view()
        view.set_map("first")
        self.paint(view)
        self.assertEqual(view.map_selector.count(), 2)
        self.assertEqual(view.entry["key"], "first")
        self.assertIsNone(view.canvas.render)
        self.assertIsNone(view.canvas.index)
        self.assertIn("资料包未安装", view.status_label.text())
        self.assertEqual(self.catalog.entity_reads, [])
        self.assertEqual(self.catalog.tile_reads, [])

    def test_unknown_map_clears_previous_terrain_and_resources(self):
        view = self.view()
        view.set_map("first")
        self.assertFalse(view.set_match_map("不存在的地图"))
        self.assertIsNone(view.entry)
        self.assertIsNone(view.canvas.render)
        self.assertIsNone(view.canvas.index)
        self.assertEqual(view.map_selector.currentIndex(), -1)
        self.assertFalse(view.source_button.isEnabled())

    def test_refresh_after_package_change_preserves_camera_style_selection_and_cleanup_closes_catalog(self):
        view = self.view()
        view.set_map("first")
        view.canvas.zoom_at(view.canvas.viewport().rect().center(), 2)
        view.canvas.selected = view.canvas.index["points"][1]
        before = view.capture_view_state()
        view.refresh_tiles()
        after = view.capture_view_state()
        self.assertEqual(after["selected_entity_id"], before["selected_entity_id"])
        self.assertEqual(after["representation"], "natural")
        self.assertAlmostEqual(after["camera"]["zoom"], before["camera"]["zoom"])
        view.cleanup()
        self.assertTrue(self.catalog.closed)
        self.assertEqual(len(view.canvas.tile_cache.items), 0)
        self.assertEqual(len(view.canvas.tree_cache.items), 0)
        self.assertEqual(len(view.canvas.tree_geometry_cache.items), 0)


    def test_static_tree_layers_reuse_while_panning_and_zooming_and_keep_exact_picking(self):
        view = self.view()
        view.set_map("first")
        self.paint(view)
        canvas = view.canvas
        counts = canvas.tree_cache.builds, canvas.tree_geometry_cache.builds
        saved = copy.deepcopy(canvas.index["points"])
        self.paint(view)
        canvas.centerOn(canvas.mapToScene(canvas.viewport().rect().center()) + QPointF(10, 10))
        self.paint(view)
        self.assertEqual((canvas.tree_cache.builds, canvas.tree_geometry_cache.builds), counts)
        # 同一清晰度档不重建图层，下一档也复用保存的矢量绘图命令。
        canvas.zoom_at(canvas.viewport().rect().center(), 1.01)
        self.paint(view)
        self.assertEqual((canvas.tree_cache.builds, canvas.tree_geometry_cache.builds), counts)
        canvas.zoom_at(canvas.viewport().rect().center(), 1.8)
        self.paint(view)
        self.assertEqual(canvas.tree_geometry_cache.builds, counts[1])
        self.assertEqual(canvas.index["points"], saved)
        tree = canvas.index["points"][0]
        chosen = canvas.pick_entity(canvas.mapFromScene(QPointF(tree["px"], tree["py"])))
        self.assertEqual(chosen["entity"]["id"], "tree-first")
        view.set_map("second")
        self.assertFalse(canvas.tree_cache.items)
        self.assertFalse(canvas.tree_geometry_cache.items)
        self.paint(view)
        self.assertEqual(canvas.index["points"][0]["entity"]["id"], "tree-second")

    def test_reference_grid_only_appears_outside_terrain_and_empty_stays_solid(self):
        view = self.view()
        canvas = view.canvas
        canvas.set_reference_grid("#161619", "#26262c")
        empty = self.paint(view)
        self.assertEqual(empty.pixelColor(8, 8).name(), "#0f0f11")
        view.set_map("first")
        canvas.zoom_at(canvas.viewport().rect().center(), .5)
        image = self.paint(view)
        colors = {image.pixelColor(x, y).name() for x in range(70) for y in range(70)}
        self.assertIn("#161619", colors)
        self.assertIn("#26262c", colors)
        position = canvas.mapFromScene(QPointF(300, 400))
        self.assertEqual(image.pixelColor(position).name(), "#49a65c", "底纹不得画在真实地形矩形内")
        view.set_match_map("unknown")
        self.assertEqual(self.paint(view).pixelColor(8, 8).name(), "#0f0f11")

    def test_clipped_reference_viewport_keeps_qt_exposed_region_cleanup(self):
        view = self.view()
        view.set_map("first")
        # 圆角父容器与高DPI滚动并非每次都由本画布填满暴露区域；不能跳过Qt底层清理。
        self.assertFalse(view.canvas.viewport().testAttribute(Qt.WA_OpaquePaintEvent))
        view.canvas.set_reference_grid("#161619", "#26262c")
        view.canvas.zoom_at(view.canvas.viewport().rect().center(), 4)
        view.canvas.centerOn(QPointF(100, 100))
        self.paint(view)
        self.assertFalse(view.canvas.viewport().testAttribute(Qt.WA_OpaquePaintEvent))

    def test_background_cache_avoids_fractional_dpi_scroll_rounding(self):
        view = self.view()
        canvas = view.canvas
        for ratio, expected in ((1, QGraphicsView.CacheBackground), (1.25, QGraphicsView.CacheNone),
                                (1.5, QGraphicsView.CacheNone), (2, QGraphicsView.CacheBackground)):
            with self.subTest(ratio=ratio), patch.object(canvas.viewport(), "devicePixelRatioF", return_value=ratio):
                canvas._sync_background_cache_mode()
                self.assertEqual(canvas.cacheMode(), expected)
                self.assertEqual(canvas._background_cache_dpr, ratio)

    def test_static_background_keeps_selection_dynamic_and_same_map_inputs_reused(self):
        view = self.view()
        view.set_map("first")
        canvas = view.canvas
        self.paint(view)
        original = copy.deepcopy(canvas.index["points"])
        counts = (len(self.catalog.entity_reads), canvas.tile_cache.loads, canvas.tree_cache.builds)
        point = canvas.mapFromScene(QPointF(500, 300))
        QTest.mouseClick(canvas.viewport(), Qt.LeftButton, pos=point)
        selected = self.paint(view)
        edge = point + QPoint(8, 0)
        self.assertGreater(selected.pixelColor(edge).red(), 230)
        QTest.mouseClick(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(300, 400)))
        cleared = self.paint(view)
        self.assertIsNone(canvas.selected)
        self.assertEqual(cleared.pixelColor(edge).name(), "#49a65c", "选中环不能残留在静态缓存")
        state = view.capture_view_state()
        view.set_match_map("first")
        view.restore_view_state(state)
        self.paint(view)
        self.assertEqual((len(self.catalog.entity_reads), canvas.tile_cache.loads, canvas.tree_cache.builds), counts)
        self.assertEqual(canvas.index["points"], original)

    def test_static_tree_cache_has_a_memory_and_item_limit(self):
        cache = _TreeLayerCache(max_bytes=5000, max_items=2)
        for index in range(8):
            def build():
                picture = QPixmap(20, 20)
                picture.fill(Qt.transparent)
                return (picture, None), 20 * 20 * 4
            cache.get((index,), build)
        self.assertEqual(len(cache.items), 2)
        self.assertLessEqual(cache.bytes, cache.max_bytes)
        before = cache.builds
        cache.get((7,), build)
        self.assertEqual(cache.builds, before)
        cache.clear()
        self.assertEqual(cache.bytes, 0)
        self.assertFalse(cache.items)

    def test_source_popup_is_nonblocking_and_labels_reference_boundary(self):
        view = self.view()
        view.set_map("first")
        spy = QSignalSpy(view.sourceRequested)
        view.source_button.click()
        self.application.processEvents()
        self.assertEqual(len(spy), 1)
        self.assertTrue(view._source_dialog.isVisible())
        self.assertIn("不代表当前对局", view._source_dialog.description.text())
        self.assertIn("source_sha256", view._source_dialog.details.toPlainText())
        view._source_dialog.close()
        self.application.processEvents()
        self.assertIsNone(view._source_dialog)


if __name__ == "__main__":
    unittest.main(verbosity=2)
