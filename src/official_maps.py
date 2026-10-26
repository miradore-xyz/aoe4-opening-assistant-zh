"""按游戏内部配置引用核对的官方地图目录；参考布局与实战实例分开保存。"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import re

from catalog import CATALOG_ROOT

MAP_ROOT = CATALOG_ROOT / "地图"


@lru_cache(maxsize=1)
def _catalog():
    try:
        data = json.loads((MAP_ROOT / "目录.json").read_text(encoding="utf-8"))
        return data if data.get("schema") == 1 and isinstance(data.get("entries"), list) else {"entries": []}
    except (OSError, ValueError, AttributeError):
        return {"entries": []}


def catalog_entries():
    """74个安装版自动匹配配置，不冒充当前排位图池或战役/自定义地图全集。"""
    return deepcopy(_catalog()["entries"])


def catalog_info():
    return deepcopy({key: value for key, value in _catalog().items() if key != "entries"})


def _key(value):
    return re.sub(r"[\s_\-]+", "", str(value or "")).casefold()


def find_official_map(value):
    """仅匹配已核对的配置键、中英文名；未知或歧义不换成其他地图。"""
    wanted = _key(value)
    if not wanted:
        return None
    candidates = [entry for entry in _catalog()["entries"]
                  if wanted in ({_key(entry.get(field)) for field in ("key", "name_zh", "name_en")}
                                | {_key(alias) for alias in entry.get("aliases", [])})]
    return deepcopy(candidates[0]) if len(candidates) == 1 else None


def _asset(entry, field):
    relative = entry.get(field) if isinstance(entry, dict) else None
    if not isinstance(relative, str) or not relative:
        return None
    path = (MAP_ROOT / relative).resolve()
    return path if path.is_relative_to(MAP_ROOT.resolve()) and path.is_file() else None


def map_preview_path(entry):
    """官方示意美术只用于参考层，绝不作为实战坐标系。"""
    path = _asset(entry, "preview")
    return str(path) if path else None


@lru_cache(maxsize=80)
def _layout(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if (data.get("kind") == "official_rule_reference" and data.get("not_match_reconstruction") is True
                and data.get("status") == "supported" and isinstance(data.get("grid"), list) and data["grid"]):
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return None


def map_layout_reference(entry):
    path = _asset(entry, "layout")
    return deepcopy(_layout(str(path))) if path else None
