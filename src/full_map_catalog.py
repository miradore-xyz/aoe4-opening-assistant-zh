"""完整官方代表地图的本地目录与按需读取；代表实例不能当作当前对局。

英文标识：catalog=目录，tile=图块，manifest=清单，pack=独立资料包。
紧凑高程图可随程序内置；旧大图目录或Zip64资料包仍可按块读取。
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
from threading import RLock
from zipfile import BadZipFile, ZipFile

from catalog import CATALOG_ROOT, SOURCE
from official_maps import find_official_map

PACK_NAME = "AoE4OpeningAssistant-Maps.aoe4maps"
METADATA_ROOT = CATALOG_ROOT / "地图" / "完整实例"


class FullMapCatalogError(ValueError):
    """本地地图资料缺失、损坏或与所选实例不一致。"""


def default_pack_path():
    """只按明确开发覆盖或程序同目录寻找；不写死开发工作区。"""
    override = os.environ.get("AOE4_MAPS_PACK", "").strip()
    if override:
        return Path(override).expanduser()
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else SOURCE.parent
    return base / PACK_NAME


class FullMapCatalog:
    """优先读取内置紧凑图；实体最多缓存两图，图像缓存由视图管理。"""
    def __init__(self, root=None, manifest_path=None):
        self.root = Path(root) if root is not None else default_pack_path()
        self.manifest_path = Path(manifest_path) if manifest_path is not None else METADATA_ROOT / "manifest.json"
        if manifest_path is None and root is not None and self.root.is_dir() and (self.root / "manifest.json").is_file():
            self.manifest_path = self.root / "manifest.json"
        self.metadata_root = self.manifest_path.parent
        if root is None and not os.environ.get("AOE4_MAPS_PACK", "").strip() and (self.metadata_root / "tiles").is_dir():
            self.root = self.metadata_root
        self._document = None
        self._index = {}
        self._entities = OrderedDict()
        self._archive = None
        self._pack_index = None
        self._lock = RLock()

    def _archive_mode(self):
        return self.root.is_file() or (not self.root.is_dir() and self.root.suffix.lower() in (".aoe4maps", ".zip"))

    def _zip(self):
        if self._archive is None:
            try:
                self._archive = ZipFile(self.root, "r")
            except (OSError, BadZipFile) as error:
                raise FullMapCatalogError("地图资料包无法读取，请检查文件是否完整。") from error
        return self._archive

    @staticmethod
    def _relative(value):
        if not isinstance(value, str) or not value:
            raise FullMapCatalogError("地图资料相对路径无效。")
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or ":" in value:
            raise FullMapCatalogError("地图资料路径超出资料目录。")
        return path.as_posix()

    def _disk_asset(self, root, relative):
        root = Path(root).resolve()
        path = (root / self._relative(relative)).resolve()
        if not path.is_relative_to(root):
            raise FullMapCatalogError("地图资料路径超出资料目录。")
        return path

    def _small_bytes(self, relative):
        local = self._disk_asset(self.metadata_root, relative)
        if local.is_file():
            return local.read_bytes()
        return self._resource_bytes(relative)

    def _resource_bytes(self, relative):
        relative = self._relative(relative)
        with self._lock:
            if self._archive_mode():
                if not self.root.is_file():
                    return None
                try:
                    return self._zip().read(relative)
                except KeyError:
                    return None
                except (OSError, BadZipFile, RuntimeError) as error:
                    raise FullMapCatalogError("地图图块读取失败，资料包可能不完整。") from error
            path = self._disk_asset(self.root, relative)
            return path.read_bytes() if path.is_file() else None

    def _load(self):
        with self._lock:
            if self._document is not None:
                return self._document
            try:
                raw = self.manifest_path.read_bytes() if self.manifest_path.is_file() else self._resource_bytes("manifest.json")
                if raw is None:
                    return {"entries": []}
                data = json.loads(raw)
            except (OSError, ValueError, TypeError) as error:
                raise FullMapCatalogError("地图目录清单无法读取。") from error
            if (data.get("schema") != 1 or data.get("kind") != "official_full_map_reference_catalog"
                    or data.get("not_match_reconstruction") is not True or not isinstance(data.get("entries"), list)):
                raise FullMapCatalogError("地图目录格式不受支持。")
            index = {}
            for entry in data["entries"]:
                key = entry.get("key")
                if (not isinstance(key, str) or not key or key in index or entry.get("not_match_reconstruction") is not True
                        or entry.get("exact_match_reconstruction_verified") is not False
                        or set(entry.get("render", {}).get("styles", {})) not in ({"natural"}, {"natural", "medieval"})):
                    raise FullMapCatalogError("地图实例身份或参考范围不明确。")
                index[key] = entry
            self._document, self._index = data, index
            return data

    def entries(self):
        return deepcopy(self._load()["entries"])

    def info(self):
        return deepcopy({key: value for key, value in self._load().items() if key != "entries"})

    def _entry(self, key):
        self._load()
        if isinstance(key, dict):
            key = key.get("key")
        value = str(key or "").strip()
        if value in self._index:
            return self._index[value]
        official = find_official_map(value)
        return self._index.get(official["key"]) if official else None

    def get(self, key):
        entry = self._entry(key)
        if entry is None:
            return None
        try:
            return deepcopy(self._render_entry(entry))
        except FullMapCatalogError:
            # 无效外包由data_status明确报错，地图目录仍可选择。
            return deepcopy(entry)

    def _render_entry(self, entry):
        """同源旧外包使用它自己的LOD；内置坐标和实体身份不随包切换。"""
        if not self.root.exists():
            return entry
        packed = self._check_pack_identity(entry)
        return dict(entry, render=packed["render"], base_path=packed["base_path"])

    def _tile_relative(self, key, style, level, x, y):
        entry = self._entry(key)
        if entry is None or any(type(value) is not int for value in (level, x, y)):
            return None
        entry = self._render_entry(entry)
        definition = entry["render"]["styles"].get(style)
        if definition is None:
            return None
        layer = next((item for item in definition["levels"] if item["level"] == level), None)
        if layer is None or not (0 <= x < layer["cols"] and 0 <= y < layer["rows"]):
            return None
        relative = layer["tile_template"].format(style=style, level=level, x=x, y=y)
        return self._relative(entry["base_path"] + relative)

    def _check_pack_identity(self, entry):
        """换包只核小清单的所选实例身份，不扫描全部图块。"""
        with self._lock:
            if self._pack_index is None:
                if self._archive_mode():
                    try:
                        raw = self._zip().read("manifest.json")
                    except KeyError as error:
                        raise FullMapCatalogError("地图资料包缺少来源清单。") from error
                else:
                    path = self.root / "manifest.json"
                    if not path.is_file():
                        raise FullMapCatalogError("地图资料目录缺少来源清单。")
                    raw = path.read_bytes()
                try:
                    data = json.loads(raw)
                    self._pack_index = {row["key"]: row for row in data["entries"]}
                except (ValueError, TypeError, KeyError) as error:
                    raise FullMapCatalogError("地图资料包的来源清单无法读取。") from error
            packed = self._pack_index.get(entry["key"])
            fields = ("source_scenario_sha256", "source_layer_sha256", "entities_raw_sha256", "compiled_reference_exact")
            if packed is None or any(packed.get(field) != entry.get(field) for field in fields):
                raise FullMapCatalogError("地图资料包与程序中的代表实例不一致，请选择相应版本资料包。")
            if (set(packed.get("render", {}).get("styles", {})) not in ({"natural"}, {"natural", "medieval"})
                    or not isinstance(packed.get("base_path"), str)):
                raise FullMapCatalogError("地图资料包的高程图层格式不受支持。")
            return packed

    def tile_path(self, key, style, level, x, y):
        """目录模式可直接返回路径；压缩包模式请使用tile_bytes，不落地解压。"""
        relative = self._tile_relative(key, style, level, x, y)
        if relative is None or self._archive_mode():
            return None
        path = self._disk_asset(self.root, relative)
        if not path.is_file():
            return None
        self._check_pack_identity(self._entry(key))
        return path

    def tile_bytes(self, key, style, level, x, y):
        relative = self._tile_relative(key, style, level, x, y)
        if relative is None:
            return None
        if not self.root.exists():
            return None
        self._check_pack_identity(self._entry(key))
        return self._resource_bytes(relative)

    def data_status(self, key=None):
        """只检查所选图一个缩略块；不扫描全部图块或一次解压整包。"""
        try:
            entries = self._load()["entries"]
            if not entries:
                return {"state": "metadata_missing", "message_zh": "尚未安装完整地图目录。"}
            entry = self._entry(key) if key is not None else entries[0]
            if entry is None:
                return {"state": "unknown_map", "message_zh": "该地图尚未收录完整代表实例。"}
            entry = self._render_entry(entry)
            relative = entry["base_path"] + entry["render"]["styles"]["natural"]["thumbnail"]
            if self.root.exists():
                self._check_pack_identity(entry)
            with self._lock:
                if self._archive_mode():
                    available = self.root.is_file()
                    if available:
                        try:
                            self._zip().getinfo(self._relative(relative))
                        except KeyError:
                            available = False
                else:
                    available = self._disk_asset(self.root, relative).is_file()
            return {"state": "ready" if available else "tiles_missing", "map_key": entry["key"],
                    "message_zh": "官方代表实例 · 不是当前对局" if available else
                    "未找到地图图像资料，请修复安装或选择有效资料目录。"}
        except FullMapCatalogError as error:
            return {"state": "invalid_data", "message_zh": str(error)}

    def is_available(self, key=None):
        return self.data_status(key)["state"] == "ready"

    def available(self, entry=None):
        return self.is_available(entry)

    def set_root(self, path):
        """切换外部资料位置；内置目录及74项选择保持不变。"""
        with self._lock:
            self.close()
            self.root = Path(path)

    def load_entities(self, key):
        entry = self._entry(key)
        if entry is None:
            return None
        with self._lock:
            if entry["key"] in self._entities:
                self._entities.move_to_end(entry["key"])
                return deepcopy(self._entities[entry["key"]])
            compressed = self._small_bytes(entry["entities_file"])
            if compressed is None:
                raise FullMapCatalogError("该地图的实体资料缺失。")
            try:
                if hashlib.sha256(compressed).hexdigest() != entry["entities_gzip_sha256"]:
                    raise FullMapCatalogError("地图实体资料摘要不符。")
                raw = gzip.decompress(compressed)
                if hashlib.sha256(raw).hexdigest() != entry["entities_raw_sha256"]:
                    raise FullMapCatalogError("地图原始实体资料摘要不符。")
                data = json.loads(raw)
            except (OSError, ValueError, KeyError) as error:
                raise FullMapCatalogError("地图实体资料无法读取。") from error
            if (data.get("kind") != "official_generated_entities" or data.get("not_match_reconstruction") is not True
                    or data.get("source_scenario", {}).get("sha256") != entry["source_scenario_sha256"]
                    or data.get("source_layer", {}).get("sha256") != entry["source_layer_sha256"]
                    or data.get("counts", {}).get("entities") != len(data.get("entities", []))
                    or len(data["entities"]) != entry["entity_count"]):
                raise FullMapCatalogError("地图实体与所选代表实例来源不一致。")
            self._entities[entry["key"]] = data
            while len(self._entities) > 2:
                self._entities.popitem(last=False)
            return deepcopy(data)

    def clear_cache(self):
        with self._lock:
            self._entities.clear()

    def close(self):
        with self._lock:
            if self._archive is not None:
                self._archive.close()
                self._archive = None
            self._pack_index = None
            self._entities.clear()
