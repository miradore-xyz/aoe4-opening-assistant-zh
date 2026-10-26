"""独立地图包读取回归：隔离小样本，不读取用户设置、不联网。"""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZIP_STORED

import full_map_catalog as maps


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture(folder):
    folder = Path(folder)
    metadata, tiles = folder / "metadata", folder / "tiles"
    metadata.mkdir()
    tiles.mkdir()
    model = {"kind": "official_generated_entities", "not_match_reconstruction": True,
             "source_scenario": {"sha256": "source-a"}, "source_layer": {"sha256": "layer-a"},
             "counts": {"entities": 1}, "entities": [{"id": "instance-a:entity:1", "category": "tree",
             "position": {"x": -0.0, "y": 2.5, "z": 3.0}, "orientation_matrix3x3": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]}]}
    raw = json.dumps(model, ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(raw, mtime=0)
    (metadata / "entities").mkdir()
    (metadata / "entities/dry_arabia.json.gz").write_bytes(compressed)
    level = {"level": 0, "width": 2, "height": 2, "cols": 1, "rows": 1, "tile_size": 512, "image_pixel_scale": 1}
    styles = {name: {"thumbnail": f"{name}/0/0/0.png", "levels": [dict(level, tile_template=f"{name}/{{level}}/{{x}}/{{y}}.png")]} for name in ("natural", "medieval")}
    entry = {"key": "dry_arabia", "instance_key": "synthetic-source-a", "name_zh": "隔离合成地图",
             "not_match_reconstruction": True, "exact_match_reconstruction_verified": False, "base_path": "dry_arabia/",
             "render": {"width": 2, "height": 2, "styles": styles}, "entities_file": "entities/dry_arabia.json.gz",
             "entities_gzip_sha256": digest(compressed), "entities_raw_sha256": digest(raw), "entity_count": 1,
             "source_scenario_sha256": "source-a", "source_layer_sha256": "layer-a", "compiled_reference_exact": False,
             "material_source_note_zh": "隔离测试标记，不是实际地图。"}
    document = {"schema": 1, "kind": "official_full_map_reference_catalog", "not_match_reconstruction": True, "entries": [entry]}
    for target in (metadata / "manifest.json", tiles / "manifest.json"):
        target.write_text(json.dumps(document), encoding="utf-8")
    for style in styles:
        target = tiles / "dry_arabia" / style / "0/0/0.png"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"isolated-tile-" + style.encode())
    return metadata, tiles, document, model


def archive(path, folder, document=None):
    with ZipFile(path, "w", compression=ZIP_STORED, allowZip64=True) as stream:
        for item in folder.rglob("*"):
            if item.is_file() and item.name != "manifest.json":
                stream.write(item, item.relative_to(folder).as_posix())
        raw = json.dumps(document).encode() if document is not None else (folder / "manifest.json").read_bytes()
        stream.writestr("manifest.json", raw)


class FullMapCatalogTests(unittest.TestCase):
    def test_builtin_entries_remain_visible_without_external_pack(self):
        with tempfile.TemporaryDirectory() as temporary:
            metadata, tiles, document, model = fixture(temporary)
            catalog = maps.FullMapCatalog(Path(temporary) / maps.PACK_NAME, metadata / "manifest.json")
            self.assertEqual(len(catalog.entries()), 1)
            self.assertEqual(catalog.data_status("dry_arabia")["state"], "tiles_missing")
            self.assertFalse(catalog.available(catalog.get("dry_arabia")))
            self.assertIsNone(catalog.tile_bytes("dry_arabia", "natural", 0, 0, 0))
            self.assertIsNone(catalog._archive)
            self.assertFalse(catalog.get("dry_arabia")["compiled_reference_exact"])

    def test_directory_reads_only_requested_tile_and_resolves_entry_by_known_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            metadata, tiles, document, model = fixture(temporary)
            catalog = maps.FullMapCatalog(tiles, metadata / "manifest.json")
            self.assertTrue(catalog.available("dry_arabia"))
            self.assertEqual(catalog.get("Dry Arabia")["key"], "dry_arabia")
            injected = {"key": "dry_arabia", "base_path": "../../unrelated/"}
            self.assertEqual(catalog.tile_bytes(injected, "natural", 0, 0, 0), b"isolated-tile-natural")
            self.assertTrue(catalog.tile_path("dry_arabia", "natural", 0, 0, 0).is_file())
            self.assertIsNone(catalog.tile_bytes("dry_arabia", "natural", 0, 1, 0))
            self.assertIsNone(catalog.tile_bytes("dry_arabia", "unknown", 0, 0, 0))

    def test_entity_gzip_keeps_complete_model_and_cache_does_not_share_mutable_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            metadata, tiles, document, model = fixture(temporary)
            catalog = maps.FullMapCatalog(tiles, metadata / "manifest.json")
            loaded = catalog.load_entities("dry_arabia")
            self.assertEqual(loaded, model)
            self.assertEqual(struct.pack("<f", loaded["entities"][0]["position"]["x"]), struct.pack("<f", -0.0))
            loaded["entities"][0]["id"] = "changed-by-view"
            self.assertEqual(catalog.load_entities("dry_arabia")["entities"][0]["id"], "instance-a:entity:1")
            (metadata / "entities/dry_arabia.json.gz").write_bytes(b"truncated")
            catalog.clear_cache()
            with self.assertRaises(maps.FullMapCatalogError):
                catalog.load_entities("dry_arabia")

    def test_zip_read_has_no_extraction_and_set_root_closes_previous_zip_keeps_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            metadata, tiles, document, model = fixture(temporary)
            first, second = Path(temporary) / "first.aoe4maps", Path(temporary) / "second.aoe4maps"
            archive(first, tiles)
            archive(second, tiles)
            catalog = maps.FullMapCatalog(first, metadata / "manifest.json")
            self.assertTrue(catalog.available("dry_arabia"))
            old_zip = catalog._archive
            before = set(Path(temporary).rglob("*"))
            self.assertEqual(catalog.tile_bytes("dry_arabia", "medieval", 0, 0, 0), b"isolated-tile-medieval")
            self.assertEqual(set(Path(temporary).rglob("*")), before)
            self.assertIsNone(catalog.tile_path("dry_arabia", "medieval", 0, 0, 0))
            original_metadata = catalog.entries()
            catalog.set_root(second)
            self.assertIsNone(old_zip.fp)
            self.assertEqual(catalog.entries(), original_metadata)
            self.assertEqual(catalog.tile_bytes("dry_arabia", "natural", 0, 0, 0), b"isolated-tile-natural")
            current_zip = catalog._archive
            catalog.close()
            self.assertIsNone(current_zip.fp)

    def test_same_map_key_with_different_source_package_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            metadata, tiles, document, model = fixture(temporary)
            different = deepcopy(document)
            different["entries"][0]["source_scenario_sha256"] = "another-seed-source"
            path = Path(temporary) / "other.aoe4maps"
            archive(path, tiles, different)
            catalog = maps.FullMapCatalog(path, metadata / "manifest.json")
            self.assertEqual(catalog.data_status("dry_arabia")["state"], "invalid_data")
            with self.assertRaises(maps.FullMapCatalogError):
                catalog.tile_bytes("dry_arabia", "natural", 0, 0, 0)
            catalog.close()

    def test_default_pack_location_uses_explicit_env_or_program_neighbor(self):
        with patch.dict(maps.os.environ, {"AOE4_MAPS_PACK": "chosen-map.aoe4maps"}):
            self.assertEqual(maps.default_pack_path(), Path("chosen-map.aoe4maps"))
        with patch.dict(maps.os.environ, {}, clear=True), patch.object(maps.sys, "frozen", True, create=True), \
                patch.object(maps.sys, "executable", "C:/isolated/AoE4.exe"):
            self.assertEqual(maps.default_pack_path(), Path("C:/isolated") / maps.PACK_NAME)

    def test_natural_only_builtin_tiles_need_no_external_pack(self):
        with tempfile.TemporaryDirectory() as temporary:
            metadata, tiles, document, model = fixture(temporary)
            entry = document["entries"][0]
            entry["render"]["styles"].pop("medieval")
            natural = entry["render"]["styles"]["natural"]
            natural["thumbnail"] = "natural/0/0/0.webp"
            natural["levels"][0]["tile_template"] = "natural/{level}/{x}/{y}.webp"
            entry["base_path"] = "tiles/dry_arabia/"
            path = metadata / "tiles/dry_arabia/natural/0/0/0.webp"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"compact-natural-tile")
            (metadata / "manifest.json").write_text(json.dumps(document), encoding="utf-8")
            with patch.dict(maps.os.environ, {}, clear=True):
                catalog = maps.FullMapCatalog(manifest_path=metadata / "manifest.json")
            self.assertEqual(catalog.root, metadata)
            self.assertTrue(catalog.available("dry_arabia"))
            self.assertEqual(set(catalog.get("dry_arabia")["render"]["styles"]), {"natural"})
            self.assertEqual(catalog.tile_bytes("dry_arabia", "natural", 0, 0, 0), b"compact-natural-tile")
            self.assertIsNone(catalog.tile_bytes("dry_arabia", "medieval", 0, 0, 0))
            self.assertEqual(catalog.load_entities("dry_arabia"), model)

    def test_same_source_legacy_pack_uses_its_own_lod_without_changing_builtin_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            metadata, tiles, original, model = fixture(temporary)
            compact = deepcopy(original)
            entry = compact["entries"][0]
            entry["base_path"] = "tiles/dry_arabia/"
            entry["render"]["styles"].pop("medieval")
            entry["render"]["styles"]["natural"]["levels"][0]["width"] = 1
            entry["render"]["styles"]["natural"]["levels"][0]["image_pixel_scale"] = 2.0
            (metadata / "manifest.json").write_text(json.dumps(compact), encoding="utf-8")
            pack = Path(temporary) / "legacy.aoe4maps"
            archive(pack, tiles)
            catalog = maps.FullMapCatalog(pack, metadata / "manifest.json")
            self.assertTrue(catalog.available("dry_arabia"))
            self.assertEqual(catalog.get("dry_arabia")["render"], original["entries"][0]["render"])
            self.assertEqual(catalog.tile_bytes("dry_arabia", "natural", 0, 0, 0), b"isolated-tile-natural")
            self.assertEqual(catalog.entries(), compact["entries"])
            self.assertEqual(catalog.load_entities("dry_arabia"), model)
            catalog.close()

    def test_compact_rectangular_raster_uses_one_scale_and_keeps_scene_coordinates(self):
        from build_compact_maps import compact_levels

        render = {"width": 5201, "height": 3001, "source_units_per_pixel": 1 / 12,
                  "valid_content_size": [5201.0, 3001.0]}
        original = deepcopy(render)
        levels = compact_levels(render, 3072)
        self.assertEqual(render, original)
        self.assertEqual(levels[-1]["width"], 3072)
        self.assertLessEqual(max(levels[0]["width"], levels[0]["height"]), 512)
        for layer in levels:
            factor = layer["image_pixel_scale"]
            self.assertAlmostEqual(layer["valid_content_size"][0] * factor, render["width"])
            self.assertAlmostEqual(layer["valid_content_size"][1] * factor, render["height"])
            self.assertLess(layer["padding_pixels"]["right"], 1)
            self.assertLess(layer["padding_pixels"]["bottom"], 1)
            self.assertAlmostEqual(layer["source_units_per_pixel"], factor / 12)


if __name__ == "__main__":
    unittest.main()
