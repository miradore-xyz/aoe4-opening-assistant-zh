"""规则地形参考像素验收：待定/水/树林不同，标记只表示已声明区域。"""
import copy
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QColor, QFontDatabase
from PyQt5.QtWidgets import QApplication

from official_grid_renderer import render_layout_reference


def layout(rows):
    """独立规则样例，不读取玩家资料，也不模拟真实比赛。"""
    return {"kind": "official_rule_reference", "not_match_reconstruction": True, "status": "supported",
            "grid": [[{"terrain": value} if isinstance(value, str) else value for value in row] for row in rows]}


def center(image, row, column, rows, columns):
    return round(14 + (column + .5) * (image.width() - 28) / columns), round(14 + (row + .5) * (image.height() - 28) / rows)


class OfficialGridRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")

    def test_surfaces_have_distinct_actual_pixels_without_coordinate_grid(self):
        reference = layout([["tt_none", "tt_plains", "tt_ocean"],
                            ["tt_forest_circular_large", "tt_mountains", "tt_beach"]])
        untouched = copy.deepcopy(reference)
        image = render_layout_reference(reference, size=1024)
        self.assertEqual((image.width(), image.height()), (1024, 1024))
        colors = [image.pixelColor(*center(image, row, column, 2, 3)) for row in range(2) for column in range(3)]
        unknown, grass, water, forest, mountain, shore = colors
        self.assertEqual(len({color.rgb() for color in colors}), 6)
        self.assertLess(abs(unknown.red() - unknown.green()), 12, "待定区必须保留中性灰，而非确定草地")
        self.assertGreater(grass.red() - grass.blue(), 15)
        self.assertGreater(water.blue() - water.red(), 25)
        self.assertGreater(forest.green() - forest.red(), 12)
        self.assertLess(forest.lightness(), grass.lightness())
        self.assertGreater(shore.lightness(), mountain.lightness())
        self.assertNotEqual(image.pixelColor(0, 0), grass, "留出清楚的图像外框")
        self.assertEqual(image.pixelColor(0, 0).alpha(), 255)
        self.assertEqual(reference, untouched)
        self.assertEqual(image.text("not_match_reconstruction"), "true")
        self.assertIn("待定", image.text("legend"))

    def test_none_unknown_and_map_qualified_start_do_not_become_grass_or_water(self):
        none = render_layout_reference(layout([["tt_none"]]), size=1024)
        unknown = render_layout_reference(layout([["tt_unresolved_future_type"]]), size=1024)
        start = render_layout_reference(layout([["tt_player_start_four_lakes"]]), size=1024)
        self.assertEqual(none, unknown)
        self.assertEqual(none, start, "四湖出生区的地图名不能误当湖水类型")
        self.assertNotEqual(none, render_layout_reference(layout([["tt_plains"]]), size=1024))
        self.assertNotEqual(none, render_layout_reference(layout([["tt_lake_medium"]]), size=1024))

    def test_explicit_region_icons_and_zero_based_player_have_visible_circular_marks(self):
        rows = [["tt_plains"] * 5 for _ in range(5)]
        plain = render_layout_reference(layout(rows), size=1024)
        for row, column, terrain in ((1, 1, "tt_gold_large_plains"), (1, 3, "tt_stone_large_plains"),
                                     (3, 1, "tt_holy_site_plains"), (3, 3, "tt_settlement_plains")):
            rows[row][column] = terrain
        rows[2][2] = {"terrain": "tt_player_start_classic_plains", "player_index": 0}
        marked = render_layout_reference(layout(rows), size=1024)
        for row, column in ((1, 1), (1, 3), (3, 1), (3, 3), (2, 2)):
            x, y = center(marked, row, column, 5, 5)
            differences = sum(marked.pixelColor(x + dx, y + dy) != plain.pixelColor(x + dx, y + dy)
                              for dx in range(-18, 19) for dy in range(-18, 19))
            self.assertGreater(differences, 150, "每个明确区域均应有可读图符，席位0也不能被当作缺失")
        other_player = copy.deepcopy(rows)
        other_player[2][2]["player_index"] = 1
        self.assertNotEqual(marked, render_layout_reference(layout(other_player), size=1024))

    def test_adjacent_resource_cells_are_one_region_not_individual_mines(self):
        rows = [["tt_plains"] * 4 for _ in range(3)]
        plain = render_layout_reference(layout(rows), size=1024)
        rows[1][1] = rows[1][2] = "tt_gold_large_plains"
        marked = render_layout_reference(layout(rows), size=1024)
        self.assertNotEqual(marked.pixelColor(*center(marked, 1, 1, 3, 4)),
                            plain.pixelColor(*center(plain, 1, 1, 3, 4)))
        self.assertEqual(marked.pixelColor(*center(marked, 1, 2, 3, 4)),
                         plain.pixelColor(*center(plain, 1, 2, 3, 4)),
                         "相连金矿类型格应合成区域标记，不能误报两座矿")
        self.assertEqual(render_layout_reference(layout([["tt_golden_heights"]]), size=1024),
                         render_layout_reference(layout([["tt_none"]]), size=1024),
                         "golden只是地图限定词，不能变成已知金矿区域")

    def test_returned_image_is_independent_and_output_size_is_bounded(self):
        reference = layout([["tt_forest_natural_large", "tt_lake_shallow"]])
        first = render_layout_reference(reference, size=1024)
        expected = first.pixelColor(300, 300)
        first.setPixelColor(300, 300, QColor("#ff00ff"))
        second = render_layout_reference(reference, size=1024)
        self.assertEqual(second.pixelColor(300, 300), expected, "调用方写图不能污染已缓存的地图")
        self.assertEqual(render_layout_reference(reference).width(), 1536)
        self.assertEqual(render_layout_reference(reference, size=8).width(), 1024)
        self.assertEqual(render_layout_reference(reference, size=4096).width(), 2048)

    def test_unsupported_or_falsely_declared_match_is_not_rendered(self):
        reference = layout([["tt_plains"]])
        for bad in ({**reference, "status": "unsupported"}, {**reference, "kind": "match"},
                    {**reference, "not_match_reconstruction": False}, {**reference, "grid": []},
                    {**reference, "grid": [[{"terrain": "tt_none"}], []]}):
            with self.subTest(status=bad.get("status"), kind=bad.get("kind")), self.assertRaises(ValueError):
                render_layout_reference(bad)


if __name__ == "__main__":
    unittest.main()
