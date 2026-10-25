"""离线文明建筑与科技目录。

英文标识对应：catalog=目录，civilization/civ=文明，building=建筑，
technology=科技，item=条目，resolve=解析路径。按固定资料版本筛选候选，
不读取游戏内存，不推测当前游戏是否满足研究条件。
"""
from __future__ import annotations

import copy
import json
import sys
from functools import lru_cache
from pathlib import Path


SOURCE = Path(__file__).resolve().parent
ASSET_ROOT = (Path(sys._MEIPASS) / "assets") if hasattr(sys, "_MEIPASS") else SOURCE
CATALOG_ROOT = ASSET_ROOT / "catalog" if hasattr(sys, "_MEIPASS") else SOURCE / "文明资料"


@lru_cache(maxsize=1)
def _document():
    """一次读取离线 JSON；打包版与源码版共用相同内容。"""
    try:
        document = json.loads((CATALOG_ROOT / "catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"civilizations": {}, "items": [], "patch": ""}
    return document


def _key(value):
    """允许英文名称、中文名称、资料缩写与资料路径名，不做相似匹配。"""
    return str(value).strip().casefold().replace("’", "'")


@lru_cache(maxsize=1)
def _aliases():
    aliases = {}
    for code, civ in _document().get("civilizations", {}).items():
        for name in (code, civ.get("name"), civ.get("slug"), civ.get("id"),
                     civ.get("name_zh"), civ.get("source_name_zh")):
            if name:
                aliases[_key(name)] = code
    return aliases


def civilization_codes(civilization):
    """将选择转为精确文明代码。通用选择代表所有已收录文明。"""
    choices = civilization if isinstance(civilization, (list, tuple, set)) else [civilization]
    result = set()
    for choice in choices:
        key = _key(choice)
        if key in ("generic", "any", "通用"):
            result.update(_document().get("civilizations", {}))
        elif key in _aliases():
            result.add(_aliases()[key])
        else:
            return frozenset()
    return frozenset(result)


def load_catalog(civilization, kind=None):
    """按文明与类型列出候选；多文明仅保留共同可用条目。

    kind（类型）使用 building（建筑）或 technology（科技）。每个返回字典
    都是独立副本，编辑器附加数量等信息不会修改全局目录。
    """
    if kind in ("buildings", "建筑"):
        kind = "building"
    elif kind in ("technologies", "科技"):
        kind = "technology"
    if kind not in (None, "building", "technology"):
        return []
    requested = civilization_codes(civilization)
    if not requested:
        return []
    matches = {}
    for item in _document().get("items", []):
        if kind is not None and item.get("kind") != kind:
            continue
        available = requested.intersection(item.get("civs", []))
        if not available:
            continue
        identity = (item["kind"], item["id"])
        if identity not in matches:
            matches[identity] = (item, set(available), [item])
        else:
            matches[identity][1].update(available)
            matches[identity][2].append(item)
    result = []
    for item, available, variants in matches.values():
        if available == requested:
            value = copy.deepcopy(item)
            value["civs"] = sorted(available)
            if item['kind'] == 'technology':
                locations = list(dict.fromkeys(v.get('location_zh', '') for v in variants))
                if len(locations) > 1:
                    value['produced_by'] = sorted({producer for variant in variants for producer in variant.get('produced_by', [])})
                    value['location_zh'] = '因文明而异：' + '；'.join(locations)
            result.append(value)
    return sorted(result, key=lambda item: (item["age"], item["name_zh"], item["id"]))


def technology_buildings(civilization, technologies):
    """按真实研究位置生成分类；同一科技可以同时归入多个建筑。"""
    requested = civilization_codes(civilization)
    buildings = {}
    for item in _document().get('items', []):
        if item['kind'] == 'building' and requested.intersection(item.get('civs', [])):
            buildings.setdefault(item['base_id'], item)
    groups = {}
    for technology in technologies:
        producers = list(technology.get('produced_by', []))
        if not producers:
            producers = ['special:' + (technology.get('location_zh') or '其他研究位置')]
        for producer in producers:
            lookup = 'town-center' if producer == 'capital-town-center' else producer
            building = buildings.get(lookup, {})
            title = ('首都城镇中心' if producer == 'capital-town-center' else
                     producer[len('special:'):] if producer.startswith('special:') else
                     building.get('name_zh', '其他研究位置'))
            group = groups.setdefault(producer, {'id': producer, 'title': title,
                'icon': building.get('icon', ''), 'technology_ids': set()})
            group['technology_ids'].add(technology['id'])
    priority = ['mill', 'lumber-camp', 'mining-camp', 'town-center', 'capital-town-center',
                'blacksmith', 'barracks', 'archery-range', 'stable', 'siege-workshop',
                'university', 'monastery', 'dock', 'outpost', 'keep']
    order = {name: i for i, name in enumerate(priority)}
    return sorted(groups.values(), key=lambda group: (order.get(group['id'], 100), group['title']))


def resolve_icon(item):
    """把 catalog/icons/… 标记解析为包内路径，保留原图分辨率。"""
    icon = item.get("icon", "") if isinstance(item, dict) else str(item)
    relative = Path(icon.replace("\\", "/"))
    parts = relative.parts
    if parts[:2] != ("catalog", "icons") or ".." in parts:
        return CATALOG_ROOT / "icons" / "不存在.png"
    return CATALOG_ROOT.joinpath(*parts[1:])


def catalog_icon(item):
    """延迟引入 Qt，允许资料检查脚本在没有界面环境时加载目录。"""
    from PyQt5.QtGui import QIcon
    from game_icons import tile_pixmap
    return QIcon(tile_pixmap(resolve_icon(item), item.get('icon', ''), 96, kind=item.get('kind')))


def catalog_metadata():
    """返回界面可展示的补丁号、来源和覆盖说明。"""
    document = _document()
    return {key: copy.deepcopy(document.get(key)) for key in
            ("source", "commit", "patch", "language", "built_at", "notes_zh", "civilizations")}


def coverage_note(civilization):
    """对未知文明与通用资料交集给出准确说明。"""
    if not civilization_codes(civilization):
        return "该文明暂未收录建筑与科技资料，可继续手动填写提示。"
    choices = civilization if isinstance(civilization, (list, tuple, set)) else [civilization]
    if any(_key(choice) in ("generic", "any", "通用") for choice in choices):
        return "通用开局仅展示所有文明共有的条目；选择具体文明后可添加其建筑与科技。"
    if len(civilization_codes(civilization)) > 1:
        return "仅展示所选文明共同可用的建筑与科技。"
    return ""
