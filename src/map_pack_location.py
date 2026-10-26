"""记住用户明确选择的地图资料包；不移动资料，不扫描其他磁盘。"""
import json
from pathlib import Path

from core import atomic_json


def load_map_pack_location(data_dir):
    if data_dir is None:
        return None
    try:
        data = json.loads((Path(data_dir) / '地图资料位置.json').read_text(encoding='utf-8'))
        path = Path(data['path'])
        return path if path.is_absolute() and path.is_file() else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_map_pack_location(data_dir, path):
    if data_dir is not None:
        atomic_json(Path(data_dir) / '地图资料位置.json', {'path': str(Path(path).resolve())})
