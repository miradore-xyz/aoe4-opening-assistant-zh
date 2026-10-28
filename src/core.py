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
FIELDS = {"food": "吃肉村民", "wood": "采木村民", "gold": "采金村民", "stone": "采石村民",
          "builder": "建造村民", "villager_count": "村民总数", "population_count": "人口", "age": "时代"}
DEFAULT_HOTKEYS = {key: "" for key in ("previous", "next", "pause", "visible", "passthrough", "reset")}
# 仅用于一次性识别旧版自动预设，不再向新用户登记这些组合键。
_LEGACY_HOTKEYS = dict(zip(DEFAULT_HOTKEYS, (f"Ctrl+Alt+F{number}" for number in range(6, 12))))
HOTKEY_LABELS = {"previous": "上一步", "next": "下一步", "pause": "开始／暂停",
                 "visible": "显示／隐藏悬浮窗", "passthrough": "锁定／解锁悬浮窗", "reset": "计时归零"}
DEFAULTS = {"font_size": 14, "font_size_unit": "px", "overlay_readability_policy": 2, "overlay_style": "a", "ui_font_size": 12, "width": 400, "height": 220, "opacity": 65,
            "overlay_color": "#1b1b1e", "overlay_color_policy": 1, "overlay_text_color": "", "text_shadow": True, "text_shadow_strength": 75, "x": 70, "y": 70,
            "speech": True, "voice": "", "volume": 80, "rate": 0, "auto": True,
            "timed_playback_policy": 1, "overlay_interaction_policy": 2,
            "voice_engine": "online", "online_voice": "zh-CN-XiaoxiaoNeural",
            "timing": "starting",
            "hotkeys": DEFAULT_HOTKEYS.copy(), "hotkeys_policy": 1, "last_order": "", "passthrough": False}


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


def load_settings(path, logical_dpi=96):
    """读取设置；旧版悬浮字号由磅按当前屏幕逻辑分辨率换算为像素。"""
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
            # 精确旧默认才换成中性黑灰；升级后用户主动选回绿色也不再覆盖。
            if type(saved.get("overlay_color_policy")) is not int or saved["overlay_color_policy"] < 1:
                if result["overlay_color"].lower() == "#101b17":
                    result["overlay_color"] = DEFAULTS["overlay_color"]
                result["overlay_color_policy"] = 1
            # 逐项清除旧自动组合，混合配置中真正自定义的按键保持原样。
            if type(saved.get("hotkeys_policy")) is not int or saved["hotkeys_policy"] < 1:
                for action, previous in _LEGACY_HOTKEYS.items():
                    value = result["hotkeys"].get(action)
                    if isinstance(value, str) and value.strip().casefold() == previous.casefold():
                        result["hotkeys"][action] = ""
                result["hotkeys_policy"] = 1
            if type(saved.get("font_size")) is int and saved.get("font_size_unit") != "px":
                # 旧版 6–28 磅；px（像素）标记保存后，后续启动不再重复换算。
                old_points = max(6, min(28, saved["font_size"]))
                result["font_size"] = round(old_points * logical_dpi / 72)
            # 一次性落实新版到点自动切步与朗读；用户之后手动改过的开关仍会保留。
            if (type(saved.get("timed_playback_policy")) is not int
                    or saved["timed_playback_policy"] < 1):
                result.update(auto=True, speech=True, timing="starting",
                              timed_playback_policy=1)
            # 预览可直接点击开始；开始成功后自动锁定，独立的小锁仍能解锁。
            if (type(saved.get("overlay_interaction_policy")) is not int
                    or saved["overlay_interaction_policy"] < 2):
                result.update(passthrough=False, overlay_interaction_policy=2)
            # 仅兼容旧默认宽度；不再借样式升级改变用户保存的字号。
            if (type(saved.get("overlay_readability_policy")) is not int
                    or saved["overlay_readability_policy"] < 1):
                if result["width"] == 330:
                    result["width"] = 400
                result["overlay_readability_policy"] = 1
            result["overlay_readability_policy"] = 2
        except (ValueError, OSError):
            warning = "设置文件无法读取，已使用默认设置。原文件仍保留。"
    result["font_size_unit"] = "px"
    if result["overlay_style"] not in ("a", "c", "b"):
        result["overlay_style"] = "a"
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", result["overlay_color"]):
        result["overlay_color"] = DEFAULTS["overlay_color"]
    result["overlay_color"] = result["overlay_color"].lower()
    # 空值沿用原本的分层文字配色，升级时不把所有文字强制改成单一颜色。
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", result["overlay_text_color"]):
        result["overlay_text_color"] = ""
    result["overlay_text_color"] = result["overlay_text_color"].lower()
    for key, low, high in [("font_size", 8, 80), ("ui_font_size", 11, 18), ("width", 280, 900), ("height", 160, 1400), ("opacity", 0, 100),
                           ("text_shadow_strength", 0, 100),
                           ("volume", 0, 100), ("rate", -5, 5)]:
        result[key] = max(low, min(high, result[key]))
    result["hotkeys"] = {k: result["hotkeys"].get(k, v) if isinstance(result["hotkeys"].get(k, v), str) else v
                         for k, v in DEFAULT_HOTKEYS.items()}
    if result["timing"] not in ("starting", "ending"):
        result["timing"] = "starting"
    if result["voice_engine"] not in ("system", "online"):
        result["voice_engine"] = "online"
    if result["online_voice"] not in ("zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "zh-CN-XiaoyiNeural"):
        result["online_voice"] = "zh-CN-XiaoxiaoNeural"
    # 三档沿用同一底层语速单位；旧数值选最近一档，恰好居中时优先默认。
    result["rate"] = min((0, -2, 2), key=lambda value: abs(result["rate"] - value))
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
    """保留原节点索引，把无时间的条件说明并入其前一个已知时间组。

    time_sec（内部显示分组秒数）只存在于返回副本；原稿空时间不变，
    不表示条件已完成。前导条件属于初始显示组；全无时间仍使用手动。
    已知时间必须使用分:秒且不逆序，完整时间稿继续沿用上游返回结构。
    """
    if not isinstance(order, dict) or not isinstance(order.get("build_order"), list):
        return []
    entries = order["build_order"]
    parsed = []
    last_known = -1
    known_count = 0
    for step in entries:
        if not isinstance(step, dict) or "notes" not in step:
            return []
        value = step.get("time", "")
        if not isinstance(value, str):
            return []
        if not value.strip():
            parsed.append(None)
            continue
        match = re.fullmatch(r"([0-9]{1,9}):([0-5][0-9])", value)
        if not match:
            return []
        seconds = int(match[1]) * 60 + int(match[2])
        if seconds < last_known:
            return []
        parsed.append(seconds)
        last_known = seconds
        known_count += 1
    if not known_count:
        return []
    if known_count == len(entries):
        return get_build_order_timer_steps(order)
    result = []
    anchor = 0
    for step, seconds in zip(entries, parsed):
        inherited = seconds is None
        if not inherited:
            anchor = seconds
        copy_step = step.copy()
        copy_step["time_sec"] = anchor
        copy_step["_zh_timer_inherited"] = inherited
        result.append(copy_step)
    return result


def timed_ids(order, seconds, starting=False):
    """沿用开始/结束时刻语义，返回原始节点索引，混合稿同时显示同组条件。"""
    steps = timer_steps(order)
    selected = get_build_order_timer_step_ids(steps, int(seconds), starting_flag=starting)
    if selected and any(step.get("_zh_timer_inherited") for step in steps):
        # 上游在结束时刻超出末项后只返回最后一个节点；扩展回来保留其时间组。
        groups = {steps[index]["time_sec"] for index in selected}
        return [index for index, step in enumerate(steps) if step["time_sec"] in groups]
    return selected


def overlay_end_deadline(order, grace_seconds=120):
    """最后一个已知时间点后保留两分钟；全手动稿没有自动完成时刻。"""
    steps = timer_steps(order)
    if not steps:
        return None
    return max(step["time_sec"] for step in steps) + max(0, grace_seconds)


def timeline_selection(order, seconds, current_ids, manual_anchor=None, starting=True):
    """直接定位当前时间组，手动切步保持到下一个时间组，避免立即跳回或补读旧节点。

    manual_anchor（手动切换时的时间组）只控制临时预览；不会关闭自动切步。
    返回新的节点列表和仍有效的手动时间组，供暂停、继续与周期刷新共用。
    """
    selected = timed_ids(order, seconds, starting=starting) or list(current_ids)
    if manual_anchor is not None and tuple(selected) == tuple(manual_anchor):
        return list(current_ids), manual_anchor
    return selected, None


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


from app_fonts import FONT_FAMILY


class NoteRenderer:
    """沿用上游 @图标路径@ 解析；文字转义后显示，兼容中文自动换行。"""
    def __init__(self, assets):
        self.assets = Path(assets)
        self.catalog_root = Path(sys._MEIPASS) / 'assets/catalog' if getattr(sys, 'frozen', False) else SOURCE / '文明资料'
        self.upstream = MultiQLabelDisplay(FONT_FAMILY, 13, 0, 4, [235, 239, 245],
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
                from game_icons import icon_data_url
                image = icon_data_url(path, token)
                parts.append(f'<img src="{image}" height="{size}" width="{size}" />')
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
