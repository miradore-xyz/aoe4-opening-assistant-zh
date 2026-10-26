"""地图目录按真实配置引用核对，地图参考绝不伪装成实战实例。"""
import hashlib
import json
import unittest
from PyQt5.QtGui import QImage
import official_maps


class OfficialMapCatalogTests(unittest.TestCase):
    def test_every_installed_automatch_configuration_has_its_own_verified_image(self):
        entries = official_maps.catalog_entries()
        self.assertEqual(len(entries), 74)
        self.assertEqual(len({entry["key"] for entry in entries}), 74)
        self.assertEqual(len({entry["config_path"] for entry in entries}), 74)
        for entry in entries:
            with self.subTest(map=entry["key"]):
                path = official_maps.map_preview_path(entry)
                self.assertIsNotNone(path)
                picture = QImage(path)
                self.assertFalse(picture.isNull())
                self.assertEqual((picture.width(), picture.height()), (720, 720))
                with open(path, "rb") as stream:
                    self.assertEqual(hashlib.file_digest(stream, "sha256").hexdigest(), entry["preview_sha256"])
                self.assertTrue(entry["config_path"].startswith("attrib/multiplayer/automatch_maps/"))
                self.assertNotIn("map_id", entry, "文本编号不冒充公开接口地图编号")

    def test_names_and_verified_aliases_match_but_unknown_map_has_no_substitute(self):
        for value in ("Dry Arabia", "dry_arabia", "dry-arabia", "干燥阿拉伯"):
            self.assertEqual(official_maps.find_official_map(value)["key"], "dry_arabia")
        self.assertEqual(official_maps.find_official_map("King of the Hill")["key"], "king_of_hill")
        for value in ("", "custom_dry_arabia_mod", "干燥阿拉伯自定义版", 11195612):
            self.assertIsNone(official_maps.find_official_map(value))

    def test_layouts_have_real_output_and_explicit_reference_boundary(self):
        count = 0
        for entry in official_maps.catalog_entries():
            layout = official_maps.map_layout_reference(entry)
            if layout is None:
                self.assertFalse(entry["layout"])
                continue
            count += 1
            self.assertEqual(layout["kind"], "official_rule_reference")
            self.assertTrue(layout["not_match_reconstruction"])
            self.assertEqual(layout["status"], "supported")
            self.assertTrue(layout["source"]["map_script_sha256"])
            widths = {len(row) for row in layout["grid"]}
            self.assertEqual(len(widths), 1)
            self.assertGreater(next(iter(widths)), 0)
            self.assertTrue(all(isinstance(cell["terrain"], str) for row in layout["grid"] for cell in row))
        self.assertEqual(count, official_maps.catalog_info()["layout_count"])
        self.assertGreaterEqual(count, 3)

    def test_callers_cannot_modify_the_cached_catalog_or_traverse_outside_assets(self):
        entry = official_maps.find_official_map("dry_arabia")
        entry["name_zh"] = "错误覆盖"
        self.assertEqual(official_maps.find_official_map("dry_arabia")["name_zh"], "干燥阿拉伯")
        self.assertIsNone(official_maps.map_preview_path({"preview": "../../catalog.json"}))
