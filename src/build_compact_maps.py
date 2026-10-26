"""构建内置紧凑高程地图；Pillow仅用于离线构建，不是程序运行依赖。

示例：python build_compact_maps.py --source-pack 原资料包.aoe4maps --output 文明资料/地图/完整实例
WebP是有损显示派生，不改变原场景范围、实体坐标或源数据精度。
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import time
from zipfile import ZipFile


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)
    return digest(raw)


def compact_levels(render, maximum):
    """场景坐标尺寸保持原值；各层倍率可为浮点，X/Y使用同一比例。"""
    width, height = render["width"], render["height"]
    scene_per_pixel = max(1.0, max(width, height) / maximum)
    valid = [width / scene_per_pixel, height / scene_per_pixel]
    raster = [math.ceil(value) for value in valid]
    highest = max(0, math.ceil(math.log2(max(raster) / 512)))
    levels = []
    for level in range(highest + 1):
        divisor = 2 ** (highest - level)
        scale = scene_per_pixel * divisor
        actual = [math.ceil(value / divisor) for value in raster]
        content = [value / divisor for value in valid]
        levels.append({"level": level, "width": actual[0], "height": actual[1],
                       "cols": math.ceil(actual[0] / 512), "rows": math.ceil(actual[1] / 512),
                       "tile_size": 512, "image_pixel_scale": scale, "valid_content_size": content,
                       "padding_pixels": {"right": actual[0] - content[0], "bottom": actual[1] - content[1]},
                       "source_units_per_pixel": render.get("source_units_per_pixel", 1) * scale,
                       "tile_template": "natural/{level}/{x}/{y}.webp"})
    return levels


def convert_one(source_pack, entry, output, maximum, quality):
    from PIL import Image

    began = time.perf_counter()
    levels = compact_levels(entry["render"], maximum)
    top = levels[-1]
    original_levels = entry["render"]["styles"]["natural"]["levels"]
    source_level = next((item for item in original_levels if max(item["width"], item["height"]) >= maximum), original_levels[-1])
    # 最近足够大的已有LOD本身来自已核验原图，避免重读不必要的高层数百图块。
    image = Image.new("RGB", (source_level["width"], source_level["height"]))
    with ZipFile(source_pack) as archive:
        for y in range(source_level["rows"]):
            for x in range(source_level["cols"]):
                relative = entry["base_path"] + source_level["tile_template"].format(level=source_level["level"], x=x, y=y)
                with Image.open(BytesIO(archive.read(relative))) as tile:
                    image.paste(tile.convert("RGB"), (x * 512, y * 512))
    highest = image.resize((top["width"], top["height"]), Image.Resampling.LANCZOS)
    inventory = []
    base_path = f"tiles/{entry['key']}/"
    for layer in levels:
        # 从整张高层图生成低层，再切块，保证缩小滤波不会在块边界截断。
        raster = highest if layer is top else highest.resize((layer["width"], layer["height"]), Image.Resampling.LANCZOS)
        for y in range(layer["rows"]):
            for x in range(layer["cols"]):
                relative = base_path + layer["tile_template"].format(level=layer["level"], x=x, y=y)
                path = Path(output) / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                tile = raster.crop((x * 512, y * 512, min((x + 1) * 512, raster.width), min((y + 1) * 512, raster.height)))
                tile.save(path, "WEBP", quality=quality, method=4)
                raw = path.read_bytes()
                inventory.append({"path": relative, "bytes": len(raw), "sha256": digest(raw), "width": tile.width, "height": tile.height})
    result = deepcopy(entry)
    result["base_path"] = base_path
    result["render"]["styles"] = {"natural": {"label_zh": "高程地形", "thumbnail": "natural/0/0/0.webp", "levels": levels}}
    result["render"]["max_level"] = levels[-1]["level"]
    result["render"]["raster_size"] = [top["width"], top["height"]]
    result["render"]["raster_format"] = "webp"
    result["display_derivation"] = {"policy": "compact_natural_webp_v1", "lossy": True, "maximum_raster_dimension": maximum,
                                    "webp_quality": quality, "webp_method": 4,
                                    "source_lod": source_level["level"], "source_raster_size": [source_level["width"], source_level["height"]],
                                    "world_extent_unchanged": True, "entity_coordinates_unchanged": True,
                                    "note_zh": "仅压缩底图显示栅格；不增加源精度，不改变地形范围或实体坐标。"}
    result["tile_count"] = len(inventory)
    # 源说明中的双风格/12像素描述仍是原制作源事实；另明确本程序实际显示派生。
    result["source_detail"] = entry.get("source_detail", "") + f" 本程序内置仅高程样式，显示栅格上限{maximum}像素，WebP质量{quality}有损压缩；原场景范围与实体坐标保持。"
    return result, inventory, time.perf_counter() - began


def build(source_pack, output, maximum=3072, quality=90, workers=4, expect_sha256=None):
    from PIL import __version__ as pillow_version

    started = time.perf_counter()
    source_pack, output = Path(source_pack), Path(output)
    source_sha = file_digest(source_pack)
    if expect_sha256 and source_sha != expect_sha256:
        raise ValueError("原资料包SHA256不符，未开始转换。")
    output.mkdir(parents=True, exist_ok=True)
    with ZipFile(source_pack) as archive:
        source_raw = archive.read("manifest.json")
        original = json.loads(source_raw)
        if original.get("kind") != "official_full_map_reference_catalog" or len(original.get("entries", [])) != 74:
            raise ValueError("原资料包不是已核验的74张官方代表地图。")
        # 这些小资料字节原样保留，不更改实体、校准或原始证明。
        for entry in original["entries"]:
            for field, sha_field in (("entities_file", "entities_gzip_sha256"), ("calibration_file", "calibration_sha256"), ("proof_file", "proof_sha256")):
                relative = entry[field]
                raw = archive.read(relative)
                if digest(raw) != entry[sha_field]:
                    raise ValueError(f"原资料摘要不符：{relative}")
                path = output / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
        for info in archive.infolist():
            if info.filename.startswith("diagnostics/") and info.filename.endswith(".json"):
                path = output / info.filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(info.filename))
    rows, inventory, timings = [], [], []
    with ThreadPoolExecutor(max_workers=max(1, min(4, workers))) as executor:
        pending = {executor.submit(convert_one, source_pack, entry, output, maximum, quality): entry["key"] for entry in original["entries"]}
        for future in as_completed(pending):
            entry, tiles, elapsed = future.result()
            rows.append(entry)
            inventory.extend(tiles)
            timings.append({"key": entry["key"], "seconds": elapsed, "bytes": sum(item["bytes"] for item in tiles)})
            print(f"完成 {len(rows)}/74 {entry['key']} {sum(item['bytes'] for item in tiles)}字节", flush=True)
    result = deepcopy(original)
    result.update({"entries": sorted(rows, key=lambda row: row["key"]), "default_style": "natural", "tile_format": "webp",
                   "tile_count": len(inventory), "pack_name": None,
                   "note_zh": f"74张官方规则生成代表实例，不是当前对局；仅高程底图，{maximum}像素上限WebP有损显示派生，实体与世界范围保持原值。",
                   "display_derivation": {"policy": "compact_natural_webp_v1", "lossy": True, "maximum_raster_dimension": maximum,
                                          "webp_quality": quality, "source_pack_sha256": source_sha, "source_manifest_sha256": digest(source_raw),
                                          "source_manifest_file": "compact-source-manifest.json", "inventory_file": "compact-derivation.json"}})
    (output / "compact-source-manifest.json").write_bytes(source_raw)
    derivation = {"kind": "compact_natural_display_derivation", "not_match_reconstruction": True, "lossy": True,
                  "maximum_raster_dimension": maximum, "webp_quality": quality, "webp_method": 4, "pillow_version": pillow_version,
                  "source_pack_name": source_pack.name, "source_pack_sha256": source_sha, "source_manifest_sha256": digest(source_raw),
                  "source_entity_bytes_unchanged": True, "source_proof_bytes_unchanged": True, "source_coordinates_unchanged": True,
                  "tile_count": len(inventory), "tile_bytes": sum(item["bytes"] for item in inventory),
                  "wall_seconds": time.perf_counter() - started, "map_timings": sorted(timings, key=lambda row: row["key"]),
                  "files": sorted(inventory, key=lambda row: row["path"]),
                  "note_zh": "WebP有损压缩仅作用于底图颜色。全部LOD由同域图像重采样，未改变原场景范围、实体坐标、世界映射或遗迹河流源材质例外。原制作包保留作为再生成来源。"}
    write_json(output / "compact-derivation.json", derivation)
    write_json(output / "manifest.json", result)
    print(json.dumps({key: derivation[key] for key in ("tile_count", "tile_bytes", "wall_seconds", "source_pack_sha256")}, ensure_ascii=False), flush=True)
    return derivation


def main():
    parser = argparse.ArgumentParser(description="将原74张代表地图离线转换为内置紧凑高程图。")
    parser.add_argument("--source-pack", type=Path, required=True, help="已核验的原PNG资料包，不会修改。")
    parser.add_argument("--output", type=Path, required=True, help="内置完整实例目录。")
    parser.add_argument("--maximum", type=int, default=3072, help="最高显示栅格长边，默认3072。")
    parser.add_argument("--quality", type=int, default=90, help="WebP显示质量，默认90。")
    parser.add_argument("--workers", type=int, default=4, help="离线并行工作数，上限4。")
    parser.add_argument("--expect-source-sha256", help="可选原包SHA256；不符则拒绝转换。")
    args = parser.parse_args()
    if args.maximum < 512 or args.maximum > 8192 or not 1 <= args.quality <= 100:
        parser.error("显示栅格应为512~8192，WebP质量应为1~100。")
    build(args.source_pack, args.output, args.maximum, args.quality, args.workers, args.expect_source_sha256)


if __name__ == "__main__":
    main()
