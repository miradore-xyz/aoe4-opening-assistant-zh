"""文明目录与开局动作的集成验证。

catalog=文明目录，action=建筑／科技动作，roundtrip=保存并重新打开。
使用真实离线目录、真实编辑器与隔离临时目录；不请求在线语音。
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent
from PyQt5.QtGui import QImage
from PyQt5.QtWidgets import QApplication, QLabel, QPushButton

from app import Editor, Manager, STYLE
from catalog import catalog_icon, load_catalog, resolve_icon
from core import DEFAULTS, atomic_json, load_settings, read_order, validate_order
from order_actions import manual_notes


CIVILIZATION = "Order of the Dragon"  # 龙之骑士团的原格式文明标识。
SOURCE = Path(__file__).resolve().parent
WORK = SOURCE.parents[2] / "work"


def catalog_entry(kind, base_id, count=1):
    """取真实条目快照，按选择器约定附加文明和数量。"""
    entry = next(item for item in load_catalog(CIVILIZATION, kind)
                 if item.get("base_id") == base_id)
    entry.update(civilization=CIVILIZATION, count=count)
    return entry


def fixture():
    """保留用户正文和未知扩展字段，验证编辑不会破坏既有内容。"""
    first, second = Editor.blank(), Editor.blank()
    first["notes"] = ["持续造村民，侦察地图。", "手写提示保留 @resource/wood.webp@"]
    second["notes"] = ["第二个时间点的独立提示。"]
    return {
        "name": "文明目录集成验证",
        "civilization": CIVILIZATION,
        "author": "隔离测试",
        "source": "本地验证数据",
        "build_order": [first, second],
        "external_metadata": {"preserve": ["用户扩展字段", 2]},
    }


class CatalogEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setStyleSheet(STYLE)

    def editor(self, data=None):
        editor = Editor(None, data if data is not None else fixture())
        self.addCleanup(editor.deleteLater)
        return editor

    def flush_deleted_widgets(self):
        """清理重绘产生的旧行，使按钮查找只针对当前动作行。"""
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()

    def test_catalog_filters_civilization_and_decodes_chinese_technology_icons(self):
        """城主宫殿属于本文明，法国专属地标不会混入候选。"""
        buildings = load_catalog(CIVILIZATION, "building")
        names = {item["name_zh"] for item in buildings}
        self.assertIn("城主宫殿", names)
        self.assertNotIn("骑兵学校", names)
        self.assertIn("骑兵学校", {item["name_zh"] for item in load_catalog("French", "building")})
        self.assertTrue(all("od" in item["civs"] for item in buildings))
        technologies = load_catalog(CIVILIZATION, "technology")
        self.assertTrue(technologies)
        for item in technologies:
            with self.subTest(technology=item["id"]):
                self.assertRegex(item["name_zh"], r"[\u4e00-\u9fff]")
                self.assertTrue(resolve_icon(item).is_file())
                self.assertFalse(QImage(str(resolve_icon(item))).isNull())
        wheelbarrow = catalog_entry("technology", "wheelbarrow")
        self.assertEqual(wheelbarrow["name_zh"], "独轮推车")
        self.assertTrue(wheelbarrow["location_zh"])
        self.assertFalse(catalog_icon(wheelbarrow).isNull())

    def test_actions_save_reopen_without_duplicate_notes_or_lost_extensions(self):
        """真实编辑操作合并建筑数量、去重科技，并保留用户正文。"""
        original = fixture()
        editor = self.editor(original)
        house = catalog_entry("building", "house", 2)
        technology = catalog_entry("technology", "wheelbarrow")
        editor.step_actions.add_action(house)
        editor.step_actions.add_action(catalog_entry("building", "house", 1))
        editor.step_actions.add_action(technology)
        editor.step_actions.add_action(technology)
        self.assertEqual(editor.notes.toPlainText().splitlines(), original["build_order"][0]["notes"])
        actions = editor.step_actions.actions()
        self.assertEqual(len(actions), 2)
        self.assertEqual(next(item["count"] for item in actions if item["kind"] == "building"), 3)
        self.assertEqual(next(item["count"] for item in actions if item["kind"] == "technology"), 1)
        editor.time.setText("3:20")
        editor.save()
        self.assertEqual(editor.result(), Editor.Accepted)
        saved = copy.deepcopy(editor.data)
        step = saved["build_order"][0]
        self.assertEqual(manual_notes(step), original["build_order"][0]["notes"])
        self.assertEqual(len(step["_zh_action_notes"]), 2)
        self.assertTrue(any("建造房屋 × 3" in note for note in step["notes"]))
        self.assertTrue(any("研究独轮推车" in note for note in step["notes"]))
        self.assertEqual(saved["external_metadata"], original["external_metadata"])
        validate_order(saved)
        # 真正经过磁盘 JSON（开局文件格式）再打开，不只比较同一个内存对象。
        with tempfile.TemporaryDirectory(prefix="catalog-roundtrip-", dir=str(WORK)) as folder:
            path = Path(folder) / "开局.json"
            atomic_json(path, saved)
            reopened = self.editor(read_order(path))
            self.assertEqual(reopened.notes.toPlainText().splitlines(), original["build_order"][0]["notes"])
            self.assertEqual(reopened.step_actions.actions(), actions)
            reopened.save()
            self.assertEqual(reopened.result(), Editor.Accepted)
            self.assertEqual(reopened.data, saved)

    def test_time_point_selection_keeps_actions_and_manual_text_independent(self):
        """切换列表触发提交与载入，各时间点动作互不串入。"""
        editor = self.editor()
        self.assertEqual(editor.steps.item(0).text(), "未设置时间")
        self.assertNotRegex(editor.steps.item(0).text(), r"第\s*\d+\s*步")
        editor.time.setText("3:20")
        self.assertEqual(editor.steps.item(0).text(), "3分20秒")
        first_actions = [catalog_entry("building", "house", 2), catalog_entry("technology", "wheelbarrow")]
        for action in first_actions:
            editor.step_actions.add_action(action)
        editor.steps.setCurrentRow(1)
        self.assertEqual(editor.index, 1)
        self.assertEqual(editor.time.text(), "")
        self.assertEqual(editor.steps.item(1).text(), "未设置时间")
        self.assertEqual(editor.step_actions.actions(), [])
        self.assertEqual(editor.notes.toPlainText(), "第二个时间点的独立提示。")
        second_action = catalog_entry("building", "burgrave-palace")
        editor.step_actions.add_action(second_action)
        editor.time.setText("5:05")
        self.assertEqual(editor.steps.item(1).text(), "5分05秒")
        editor.steps.setCurrentRow(0)
        self.assertEqual(editor.time.text(), "3:20")
        self.assertEqual(editor.step_actions.actions(), first_actions)
        self.assertEqual(editor.notes.toPlainText().splitlines(), fixture()["build_order"][0]["notes"])
        editor.steps.setCurrentRow(1)
        self.assertEqual(editor.step_actions.actions(), [second_action])
        editor.save()
        self.assertEqual(editor.data["build_order"][0]["_zh_actions"], first_actions)
        self.assertEqual(editor.data["build_order"][1]["_zh_actions"], [second_action])

    def test_removing_actions_does_not_remove_user_notes(self):
        """使用实际行内移除按钮，删除生成段落而保留原有提示。"""
        editor = self.editor()
        editor.step_actions.add_action(catalog_entry("building", "house", 3))
        editor.step_actions.add_action(catalog_entry("technology", "wheelbarrow"))
        editor.save()
        reopened = self.editor(editor.data)
        for name in ("房屋", "独轮推车"):
            self.flush_deleted_widgets()
            control = next(button for button in reopened.step_actions.findChildren(QPushButton)
                           if button.accessibleName() == "移除" + name)
            control.click()
        self.assertEqual(reopened.step_actions.actions(), [])
        reopened.save()
        step = reopened.data["build_order"][0]
        self.assertEqual(step["notes"], fixture()["build_order"][0]["notes"])
        self.assertNotIn("_zh_actions", step)
        self.assertNotIn("_zh_action_notes", step)
        validate_order(reopened.data)

    def test_same_civilization_actions_are_not_marked_as_foreign(self):
        """合法本文明地标不误报；改选文明也不会静默丢弃已有动作。"""
        editor = self.editor()
        palace = catalog_entry("building", "burgrave-palace")
        editor.step_actions.add_action(palace)
        self.flush_deleted_widgets()
        texts = "\n".join(label.text() for label in editor.step_actions.findChildren(QLabel))
        self.assertNotIn("其他文明／目录外项目", texts)
        editor.civ.setCurrentIndex(editor.civ.findData("French"))
        self.flush_deleted_widgets()
        texts = "\n".join(label.text() for label in editor.step_actions.findChildren(QLabel))
        self.assertIn("其他文明／目录外项目", texts)
        self.assertEqual(editor.step_actions.actions(), [palace])

    def test_manager_renders_catalog_icons_and_prepares_chinese_voice_text(self):
        """保存后的普通 notes 同时用于演练、悬浮窗与中文播报文本。"""
        editor = self.editor()
        actions = [catalog_entry("building", "burgrave-palace"), catalog_entry("technology", "wheelbarrow")]
        for action in actions:
            editor.step_actions.add_action(action)
        editor.save()
        with tempfile.TemporaryDirectory(prefix="catalog-manager-", dir=str(WORK)) as folder:
            folder = Path(folder)
            (folder / ".已初始化").touch()
            path = folder / "开局库" / "目录验证.json"
            atomic_json(path, editor.data)
            atomic_json(folder / "设置.json", {
                "speech": False, "voice_engine": "online", "last_order": str(path),
                "hotkeys": {key: "" for key in ("previous", "next", "pause", "visible", "passthrough", "reset")},
            })
            with patch("app.OnlineSpeech.prepare", side_effect=AssertionError("关闭语音时不应请求联网准备")):
                manager = Manager(self.application, folder)
                try:
                    manager.open_session()
                    for action in actions:
                        uri = resolve_icon(action).as_uri()
                        self.assertIn(uri, manager.preview.text())
                        self.assertIn(uri, manager.overlay.notes.text())
                    spoken = "。".join(manager.voice_texts())
                    self.assertIn("建造城主宫殿", spoken)
                    self.assertIn("研究独轮推车", spoken)
                    self.assertIn("持续造村民，侦察地图。", spoken)
                    self.assertNotIn("catalog/icons/", spoken)
                    self.assertFalse(manager.settings["speech"])
                    # 新用户默认关闭循环检查；写入其他设置不会意外打开它。
                    self.assertFalse(DEFAULTS["reminders"])
                    self.assertFalse(manager.settings["reminders"])
                    self.assertFalse(manager.reminders_check.isChecked())
                    self.assertFalse(load_settings(folder / "不存在的设置.json")[0]["reminders"])
                finally:
                    manager.close()


if __name__ == "__main__":
    unittest.main()
