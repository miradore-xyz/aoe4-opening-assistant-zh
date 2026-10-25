"""中文开局数据与计时。沿用 RTS Overlay 的校验、时间选择和图标标记规则。

本文件的英文标识符对应：order=开局，step=步骤，settings=设置，clock=时钟。
修改版日期：2026-10-25。按 GNU GPL 第三版分发，原作者 CraftySalamander。
"""
from __future__ import annotations

import copy
import html
import json
import re
import sys
import time
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
sys.path.insert(0, str(SOURCE / "upstream" / "python"))
from aoe4.aoe4_build_order import check_valid_aoe4_build_order
from aoe4.aoe4_civ_icon import aoe4_civilization_icon
from common.build_order_tools import get_build_order_timer_steps, get_build_order_timer_step_ids
from common.label_display import MultiQLabelDisplay, split_multi_label_line

CIVS = dict(zip(aoe4_civilization_icon, [
    "阿拔斯王朝", "阿尤布王朝", "拜占庭", "中国", "德里苏丹国", "英格兰", "法兰西",
    "金帐汗国", "兰开斯特家族", "神圣罗马帝国", "日本", "圣女贞德", "金朝",
    "圣殿骑士团", "马其顿王朝", "马里", "蒙古", "龙之骑士团", "奥斯曼",
    "罗斯", "战国大名", "图格鲁克王朝", "朱熹的传承",
]))
CIVS.update({"any": "通用", "Any": "通用", "Generic": "通用"})
FIELDS = {"food": "食物村民", "wood": "木材村民", "gold": "黄金村民", "stone": "石料村民",
          "builder": "建造村民", "villager_count": "村民总数", "population_count": "人口", "age": "时代"}
DEFAULT_HOTKEYS = {"previous": "Ctrl+Alt+F6", "next": "Ctrl+Alt+F7", "pause": "Ctrl+Alt+F8",
                   "visible": "Ctrl+Alt+F9", "passthrough": "Ctrl+Alt+F10", "reset": "Ctrl+Alt+F11"}
HOTKEY_LABELS = {"previous": "上一步", "next": "下一步", "pause": "开始／暂停",
                 "visible": "显示／隐藏悬浮窗", "passthrough": "开关鼠标穿透", "reset": "计时归零"}
DEFAULTS = {"font_size": 7, "ui_font_size": 12, "width": 330, "height": 220, "opacity": 92, "x": 70, "y": 70,
            "speech": False, "voice": "", "volume": 80, "rate": 0, "auto": False, "sound": True,
            "voice_engine": "online", "online_voice": "zh-CN-XiaoxiaoNeural",
            "timing": "ending", "reminders": False, "reminder_seconds": 40,
            "reminder_text": "检查城镇中心造村民；看人口提前补房；检查军队持续补充；查看金矿余量和备用矿。",
            "hotkeys": DEFAULT_HOTKEYS.copy(), "last_order": "", "passthrough": False}


def civilization_text(value):
    """只翻译界面中的文明名称，导出时保留英文键名以兼容原工具。"""
    if isinstance(value, list):
        return "、".join(CIVS.get(v, str(v)) for v in value)
    return CIVS.get(value, str(value))


def atomic_json(path, value):
    """先写临时文件再替换，确保设置和开局保存完整。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_settings(path):
    result = copy.deepcopy(DEFAULTS)
    warning = ""
    if Path(path).exists():
        try:
            saved = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if not isinstance(saved, dict):
                raise ValueError()
            for key in DEFAULTS:
                if key in saved and type(saved[key]) is type(DEFAULTS[key]):
                    result[key] = saved[key]
        except (ValueError, OSError):
            warning = "设置文件无法读取，已使用默认设置。原文件仍保留。"
    for key, low, high in [("font_size", 6, 28), ("ui_font_size", 11, 18), ("width", 280, 900), ("height", 160, 1400), ("opacity", 35, 100),
                           ("volume", 0, 100), ("rate", -5, 5), ("reminder_seconds", 15, 600)]:
        result[key] = max(low, min(high, result[key]))
    result["hotkeys"] = {k: result["hotkeys"].get(k, v) if isinstance(result["hotkeys"].get(k, v), str) else v
                         for k, v in DEFAULT_HOTKEYS.items()}
    if result["timing"] not in ("starting", "ending"):
        result["timing"] = "ending"
    if result["voice_engine"] not in ("system", "online"):
        result["voice_engine"] = "online"
    if result["online_voice"] not in ("zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "zh-CN-XiaoyiNeural"):
        result["online_voice"] = "zh-CN-XiaoxiaoNeural"
    # 启动始终允许鼠标操作，防止穿透后找不到恢复入口。
    result["passthrough"] = False
    return result, warning


def validate_order(data):
    """先给出中文结构错误，再调用上游校验器保持文件兼容。"""
    if not isinstance(data, dict):
        raise ValueError("开局文件的最外层必须是一个对象。")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        raise ValueError("缺少开局名称。")
    if not isinstance(data.get("build_order"), list) or not data["build_order"]:
        raise ValueError("开局至少需要一个步骤。")
    for i, step in enumerate(data["build_order"], 1):
        prefix = f"第 {i} 步："
        if not isinstance(step, dict):
            raise ValueError(prefix + "必须是一个步骤对象。")
        for field in ("age", "villager_count", "population_count"):
            if type(step.get(field)) is not int:
                raise ValueError(prefix + FIELDS[field] + "必须是整数；不指定可填 -1。")
        if step["age"] > 4:
            raise ValueError(prefix + "时代最大为 4。")
        resource = step.get("resources")
        if not isinstance(resource, dict):
            raise ValueError(prefix + "缺少资源分配。")
        for field in ("food", "wood", "gold", "stone", "builder"):
            if field == "builder" and field not in resource:
                continue
            if type(resource.get(field)) is not int:
                raise ValueError(prefix + FIELDS[field] + "必须是整数；不指定可填 -1。")
        if not isinstance(step.get("notes"), list) or not all(isinstance(n, str) for n in step["notes"]):
            raise ValueError(prefix + "提示应为文字列表。")
        if "time" in step and not isinstance(step["time"], str):
            raise ValueError(prefix + "时间必须为文字，例如 3:20（3 分 20 秒）。")
    civ = data.get("civilization")
    choices = civ if isinstance(civ, list) else [civ]
    if not choices or not all(isinstance(v, str) and (v in CIVS) for v in choices):
        raise ValueError("文明名称无法识别。请选择原工具支持的文明；原文件没有被修改。")
    # Generic（通用）是原格式历史别名，临时转换用于上游校验，保存时不改原数据。
    checking = copy.deepcopy(data)
    checking["civilization"] = ["any" if v == "Generic" else v for v in choices] if isinstance(civ, list) else ("any" if civ == "Generic" else civ)
    valid, _ = check_valid_aoe4_build_order(checking)
    if not valid:
        raise ValueError("开局结构未通过原工具校验。请检查时代、资源分配和提示文字。")
    return data


def read_order(path):
    try:
        return validate_order(json.loads(Path(path).read_text(encoding="utf-8-sig")))
    except json.JSONDecodeError as error:
        raise ValueError(f"文件不是有效的开局数据：第 {error.lineno} 行、第 {error.colno} 列有格式错误。") from error
    except UnicodeError as error:
        raise ValueError("文件编码无法读取，请另存为 UTF-8（通用字符编码）后导入。") from error


def timer_steps(order):
    return get_build_order_timer_steps(order)


def timed_ids(order, seconds, starting=False):
    """沿用原版 AoE4 默认规则：时间是步骤结束时刻，也可在设置中改为开始时刻。"""
    return get_build_order_timer_step_ids(timer_steps(order), int(seconds), starting_flag=starting)


def timestamp(seconds):
    value = max(0, int(seconds))
    return f"{value // 60:02d}:{value % 60:02d}"


class Clock:
    """使用单调时钟，避免系统校时打断计时；暂停时不推进。"""
    def __init__(self):
        self.base = 0.0
        self.started = None

    @property
    def running(self):
        return self.started is not None

    @property
    def seconds(self):
        return self.base + (time.monotonic() - self.started if self.running else 0)

    def pause(self):
        self.base = self.seconds
        self.started = None

    def start(self):
        if not self.running:
            self.started = time.monotonic()

    def seek(self, seconds):
        self.base = max(0.0, float(seconds))
        if self.running:
            self.started = time.monotonic()


class NoteRenderer:
    """沿用上游 @图标路径@ 解析；文字转义后显示，兼容中文自动换行。"""
    def __init__(self, assets):
        self.assets = Path(assets)
        self.catalog_root = Path(sys._MEIPASS) / 'assets/catalog' if getattr(sys, 'frozen', False) else SOURCE / '文明资料'
        self.upstream = MultiQLabelDisplay("Microsoft YaHei UI", 13, 0, 4, [235, 239, 245],
                                        image_height=24, game_pictures_folder=str(self.assets / "aoe4"),
                                        common_pictures_folder=str(self.assets / "common"))

    def image_path(self, token):
        # 仅解析随程序附带的相对图标，禁止引用本地其他目录。
        if ".." in Path(token).parts or Path(token).is_absolute() or ":" in token:
            return None
        if token.startswith('catalog/'):
            path = self.catalog_root / token[len('catalog/'):]
            return str(path) if path.is_file() else None
        return self.upstream.get_image_path(token)

    def html(self, line, size=24):
        parts = []
        for token in split_multi_label_line(line):
            path = self.image_path(token)
            if path:
                parts.append(f'<img src="{Path(path).as_uri()}" height="{size}" width="{size}" />')
            else:
                parts.append(html.escape(token).replace("\n", "<br>"))
        return "".join(parts)

    def spoken(self, line):
        result = []
        for token in split_multi_label_line(line):
            if self.image_path(token):
                continue
            # 没有随包提供的图标路径不作为英语文件名朗读。
            result.append(re.sub(r"\b[\w/.-]+\.(?:webp|png|jpg)\b", "", token))
        return " ".join(result)
