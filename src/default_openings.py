"""按文明安装内置中文开局；升级补齐一次，保留玩家编辑与删除。

bundle=随程序分发的开局包，handled=已经处理的默认开局标识。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys

from core import CIVS, atomic_json, read_order


SOURCE = Path(__file__).resolve().parent
RECORD_NAME = ".默认开局记录.json"
DEFAULT_PREFIX = "aoe4guides-default:"
# 只迁移内容完全一致的受控旧样例；玩家改过任何字段都不匹配。
LEGACY_EXAMPLE_HASHES = {
    "553a4aa2a5000a427aba2f6639c4eeba6045f2c2eff8094e7b528b7a7079e03b",
    "068ea06a639c137df77dfa6b4227e8c860058d216bfd432199472b3f76834ada",
}


def bundle_directory():
    """源码与冻结程序使用同一组资料，不依赖用户资料目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "assets" / "default_openings"
    return SOURCE / "默认开局"


def load_default_openings(directory=None):
    """读取合法、单文明的内置开局，稳定标识与选稿文件名无关。"""
    result = []
    identifiers = set()
    for path in sorted(Path(directory or bundle_directory()).glob("*.json")):
        data = read_order(path)
        civilization = data.get("civilization")
        if not isinstance(civilization, str) or civilization in ("any", "Any", "Generic"):
            raise ValueError(f"默认开局必须属于一个具体文明：{path.name}")
        identifier = DEFAULT_PREFIX + civilization
        if data.get("_zh_default_id", identifier) != identifier or identifier in identifiers:
            raise ValueError(f"默认开局标识重复或与文明不符：{path.name}")
        data["_zh_default_id"] = identifier
        identifiers.add(identifier)
        result.append((path, data))
    return result


def validate_default_bundle(directory=None):
    """构建前确认每个受支持文明恰好一套，没有通用演示混入。"""
    entries = load_default_openings(directory)
    expected = set(CIVS) - {"any", "Any", "Generic"}
    found = {data["civilization"] for _, data in entries}
    if found != expected or len(entries) != len(expected):
        missing = "、".join(CIVS[value] for value in sorted(expected - found))
        raise ValueError("默认开局包未覆盖全部文明" + (f"，缺少：{missing}" if missing else "。"))
    return entries


def _source_ids(data):
    """识别此前手动导入的同一指南，避免换中文文件名后重复。"""
    result = set()
    reference = data.get("_zh_reference", {})
    if isinstance(reference, dict):
        identifier = reference.get("id")
        if isinstance(identifier, str) and identifier:
            result.add(identifier)
        reference_url = reference.get("url", "")
    else:
        reference_url = ""
    identifier = data.get("_zh_source_id")
    if isinstance(identifier, str) and identifier:
        result.add(identifier)
    for value in (data.get("source", ""), data.get("_zh_source_url", ""), reference_url):
        if isinstance(value, str):
            result.update(re.findall(r"aoe4guides\.com/builds/([A-Za-z0-9_-]+)", value))
    return result


def _fingerprint(data):
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _unused_path(directory, name):
    """同名用户文件保持原样，默认开局和旧样例备份使用新的空闲文件名。"""
    destination = directory / name
    stem, suffix = destination.stem, destination.suffix
    number = 2
    while destination.exists():
        destination = directory / f"{stem}-{number}{suffix}"
        number += 1
    return destination


def seed_default_openings(data_dir, orders_dir=None, directory=None):
    """对每个默认标识只处理一次；已处理后删除、改名和编辑均不自动恢复。"""
    data_dir = Path(data_dir)
    orders_dir = Path(orders_dir) if orders_dir is not None else data_dir / "开局库"
    entries = load_default_openings(directory)
    if not entries:
        return []
    record_path = data_dir / RECORD_NAME
    state = {"schema": 1, "handled": [], "legacy_examples_migrated": False}
    if record_path.exists():
        try:
            saved = json.loads(record_path.read_text(encoding="utf-8-sig"))
            if (not isinstance(saved, dict) or saved.get("schema") != 1
                    or not isinstance(saved.get("handled"), list)
                    or not all(isinstance(value, str) for value in saved["handled"])):
                return []
            state.update(saved)
        except (OSError, ValueError):
            # 不重建损坏的删除记录，避免把玩家主动移除的开局重新加入。
            return []
    orders_dir.mkdir(parents=True, exist_ok=True)
    handled = set(state["handled"])
    existing = []
    for path in orders_dir.glob("*.json"):
        try:
            data = read_order(path)
        except (OSError, ValueError):
            continue
        if (not state.get("legacy_examples_migrated")
                and _fingerprint(data) in LEGACY_EXAMPLE_HASHES):
            backup = data_dir / "旧示例备份"
            backup.mkdir(parents=True, exist_ok=True)
            path.rename(_unused_path(backup, path.name))
        else:
            existing.append(data)
    state["legacy_examples_migrated"] = True
    created = []
    for _, data in entries:
        identifier = data["_zh_default_id"]
        if identifier in handled:
            continue
        civilization = data["civilization"]
        source_ids = _source_ids(data)
        duplicate = any(
            item.get("_zh_default_id") == identifier or (
                civilization in (item.get("civilization") if isinstance(item.get("civilization"), list)
                                 else [item.get("civilization")])
                and bool(source_ids & _source_ids(item))
            ) for item in existing
        )
        if not duplicate:
            destination = _unused_path(orders_dir, f"默认-{CIVS[civilization]}.json")
            atomic_json(destination, data)
            created.append(destination)
            existing.append(data)
        handled.add(identifier)
    state["handled"] = sorted(handled)
    atomic_json(record_path, state)
    return created
