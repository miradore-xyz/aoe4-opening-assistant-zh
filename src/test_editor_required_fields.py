"""编辑器必填表单验证：实际 Qt（桌面控件）输入、焦点与保存，不读用户数据。"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt, QEvent, QPoint
from PyQt5.QtTest import QTest, QSignalSpy
from PyQt5.QtWidgets import QApplication, QDialog, QLabel

from app import Editor, STYLE
from core import atomic_json, read_order, timer_steps, validate_order


def opening(*times):
    steps = [Editor.blank() for _ in times]
    for step, value in zip(steps, times):
        if value is not None:
            step["time"] = value
    return {"name": "已有开局", "author": "原作者", "source": "原始来源说明",
            "civilization": "Order of the Dragon", "build_order": steps,
            "external_metadata": {"keep": ["原扩展数据", 2]}}


class EditorRequiredFieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.quit_policy = cls.application.quitOnLastWindowClosed()
        cls.application.setQuitOnLastWindowClosed(False)
        cls.application.setStyleSheet(STYLE)

    @classmethod
    def tearDownClass(cls):
        cls.application.setQuitOnLastWindowClosed(cls.quit_policy)

    def setUp(self):
        self.windows = []
        self.modal = patch("app.message", side_effect=AssertionError("必填错误应在表单提示，不能弹出阻断对话框"))
        self.modal.start()

    def tearDown(self):
        for editor in self.windows:
            editor.reject()
            editor.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()
        self.modal.stop()

    def editor(self, data=None):
        editor = Editor(None, data)
        self.windows.append(editor)
        editor.show()
        editor.activateWindow()
        QTest.qWait(25)
        return editor

    def test_new_form_starts_empty_and_marks_required_fields(self):
        editor = self.editor()
        self.assertEqual(editor.name.text(), "")
        self.assertEqual(editor.author.text(), "")
        self.assertEqual(editor.source.text(), "")
        self.assertEqual(editor.time.text(), "")
        self.assertEqual(editor.time.placeholderText(), "例如 00:10")
        self.assertNotIn("time", editor.data["build_order"][0])
        for field in (editor.name, editor.author, editor.time):
            with self.subTest(field=field.accessibleName()):
                self.assertTrue(field.property("requiredMissing"))
                self.assertTrue(editor.required_labels[field].property("requiredMissing"))
                self.assertIn("*", editor.required_labels[field].text())
                self.assertTrue(field.toolTip())
        self.assertIsNone(editor.source.property("requiredMissing"))

    def test_population_input_removed_but_imported_values_survive_editing(self):
        original = opening("00:10", "00:20")
        original["build_order"][0]["population_count"] = 42
        original["build_order"][1]["population_count"] = 67
        editor = self.editor(original)
        self.assertNotIn("population_count", editor.numbers)
        self.assertNotIn("人口", [item.text() for item in editor.findChildren(QLabel)])
        editor.numbers["gold"].setValue(7)
        editor.steps.setCurrentRow(1)
        editor.duplicate()
        editor.save()
        self.assertEqual(editor.result(), QDialog.Accepted)
        self.assertEqual([step["population_count"] for step in editor.data["build_order"]], [42, 67, 67])
        self.assertEqual(editor.data["build_order"][0]["resources"]["gold"], 7)
        self.assertEqual(original["build_order"][0]["population_count"], 42)
        reopened = self.editor(editor.data)
        self.assertNotIn("population_count", reopened.numbers)
        reopened.commit()
        self.assertEqual([step["population_count"] for step in reopened.data["build_order"]], [42, 67, 67])
        validate_order(reopened.data)

    def test_whitespace_is_invalid_and_typing_restores_styles_immediately(self):
        editor = self.editor()
        for field in (editor.name, editor.author):
            field.setText(" \u3000 ")
            self.assertTrue(field.property("requiredMissing"))
            field.setFocus()
            field.selectAll()
            QTest.keyClicks(field, "Writer")
            self.assertFalse(field.property("requiredMissing"))
            self.assertFalse(editor.required_labels[field].property("requiredMissing"))
            self.assertEqual(field.toolTip(), "")
            field.clear()
            self.assertTrue(field.property("requiredMissing"))

    def test_save_focuses_missing_name_then_author_then_time(self):
        editor = self.editor()
        accepted = QSignalSpy(editor.accepted)
        editor.save()
        self.assertEqual(len(accepted), 0)
        self.assertIs(self.application.focusWidget(), editor.name)
        editor.name.setText("我的练习开局")
        editor.save()
        self.assertIs(self.application.focusWidget(), editor.author)
        editor.author.setText("玩家署名")
        editor.save()
        self.assertIs(self.application.focusWidget(), editor.time)
        self.assertEqual(len(accepted), 0)
        editor.time.setText("00:10")
        self.assertFalse(editor.time.property("requiredMissing"))
        editor.save()
        self.assertEqual(len(accepted), 1)
        self.assertEqual(editor.result(), QDialog.Accepted)
        self.assertEqual(editor.data["source"], "")
        self.assertEqual(editor.data["build_order"][0]["time"], "00:10")
        self.assertTrue(timer_steps(editor.data))

    def test_invalid_time_is_rejected_and_valid_complete_time_recovers(self):
        editor = self.editor(opening("00:10"))
        for invalid in ("", "  ", "十秒", "00:60", "00:1", "-1:10", "1:00:10", "1234567890:00"):
            with self.subTest(time=invalid):
                editor.time.setText(invalid)
                self.assertTrue(editor.time.property("requiredMissing"))
                editor.save()
                self.assertNotEqual(editor.result(), QDialog.Accepted)
                self.assertIs(self.application.focusWidget(), editor.time)
        for valid in ("00:00", "0:10", "120:59"):
            editor.time.setText(valid)
            self.assertFalse(editor.time.property("requiredMissing"))
        editor.save()
        self.assertEqual(editor.result(), QDialog.Accepted)

    def test_save_finds_unselected_missing_and_invalid_steps_without_overwriting(self):
        original = opening("00:10", None, "wrong")
        editor = self.editor(original)
        editor.notes.setPlainText("第一步修改后的提示")
        editor.numbers["gold"].setValue(7)
        editor.save()
        self.assertEqual(editor.index, 1)
        self.assertEqual(editor.steps.currentRow(), 1)
        self.assertEqual(editor.time.text(), "")
        self.assertIs(self.application.focusWidget(), editor.time)
        self.assertEqual(editor.data["build_order"][0]["time"], "00:10")
        self.assertEqual(editor.data["build_order"][0]["resources"]["gold"], 7)
        self.assertEqual(editor.data["build_order"][0]["notes"], ["第一步修改后的提示"])
        editor.time.setText("00:20")
        editor.save()
        self.assertEqual(editor.index, 2)
        self.assertEqual(editor.time.text(), "wrong")
        self.assertEqual(editor.data["build_order"][1]["time"], "00:20")
        editor.time.setText("00:30")
        editor.save()
        self.assertEqual(editor.result(), QDialog.Accepted)
        self.assertEqual([step["time"] for step in editor.data["build_order"]], ["00:10", "00:20", "00:30"])
        self.assertEqual(original["build_order"][0]["notes"], Editor.blank()["notes"])

    def test_backwards_time_rejected_equal_time_allowed(self):
        editor = self.editor(opening("00:20", "00:10"))
        editor.save()
        self.assertEqual(editor.index, 1)
        self.assertTrue(editor.time.property("requiredMissing"))
        self.assertIn("不能早于", editor.time.toolTip())
        editor.time.setText("00:20")
        self.assertFalse(editor.time.property("requiredMissing"))
        editor.save()
        self.assertEqual(editor.result(), QDialog.Accepted)

    def test_existing_fields_extensions_and_export_schema_roundtrip(self):
        original = opening("00:10")
        editor = self.editor(original)
        self.assertEqual(editor.name.text(), original["name"])
        self.assertEqual(editor.author.text(), original["author"])
        self.assertEqual(editor.source.text(), original["source"])
        editor.save()
        self.assertEqual(editor.result(), QDialog.Accepted)
        self.assertEqual(editor.data["external_metadata"], original["external_metadata"])
        self.assertEqual(original, opening("00:10"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "开局.json"
            atomic_json(path, editor.data)
            reopened = self.editor(read_order(path))
            self.assertEqual(reopened.name.text(), original["name"])
            self.assertEqual(reopened.author.text(), original["author"])
            self.assertEqual(reopened.source.text(), original["source"])
            reopened.save()
            self.assertEqual(reopened.data, editor.data)

    def test_legacy_manual_file_still_imports_and_is_not_automatically_retimed(self):
        original = opening(None)
        original.pop("author")
        validate_order(original)
        editor = self.editor(original)
        self.assertEqual(editor.author.text(), "")
        self.assertEqual(editor.time.text(), "")
        self.assertNotIn("time", editor.data["build_order"][0])
        self.assertFalse(timer_steps(original))
        editor.save()
        self.assertNotEqual(editor.result(), QDialog.Accepted)
        self.assertNotIn("time", editor.data["build_order"][0])
        self.assertNotIn("author", original)

    def test_right_click_selects_target_and_commits_previous_step(self):
        editor = self.editor(opening("00:10", "00:20"))
        editor.notes.setPlainText("切换前尚未保存的第一步提示")
        point = editor.steps.visualItemRect(editor.steps.item(1)).center()
        menu = editor.build_step_menu(point)
        self.assertEqual(editor.index, 1)
        self.assertEqual(editor.data["build_order"][0]["notes"], ["切换前尚未保存的第一步提示"])
        actions = {action.text(): action for action in menu.actions() if not action.isSeparator()}
        self.assertTrue(actions["复制时间点"].isEnabled())
        self.assertTrue(actions["移除时间点"].isEnabled())
        self.assertTrue(actions["上移"].isEnabled())
        self.assertFalse(actions["下移"].isEnabled())
        actions["复制时间点"].trigger()
        self.assertEqual(len(editor.data["build_order"]), 3)
        self.assertEqual(editor.index, 2)
        self.assertEqual(editor.data["build_order"][1], editor.data["build_order"][2])

    def test_menu_empty_space_and_single_item_disable_unsafe_actions(self):
        editor = self.editor(opening("00:10"))
        item_menu = editor.build_step_menu(editor.steps.visualItemRect(editor.steps.item(0)).center())
        actions = {action.text(): action for action in item_menu.actions() if not action.isSeparator()}
        self.assertTrue(actions["复制时间点"].isEnabled())
        self.assertFalse(actions["移除时间点"].isEnabled())
        self.assertFalse(actions["上移"].isEnabled())
        self.assertFalse(actions["下移"].isEnabled())
        blank = QPoint(5, editor.steps.viewport().height() - 5)
        self.assertIsNone(editor.steps.itemAt(blank))
        blank_menu = editor.build_step_menu(blank)
        actions = {action.text(): action for action in blank_menu.actions() if not action.isSeparator()}
        self.assertTrue(actions["添加时间点"].isEnabled())
        self.assertTrue(all(not action.isEnabled() for caption, action in actions.items() if caption != "添加时间点"))
        actions["添加时间点"].trigger()
        self.assertEqual(len(editor.data["build_order"]), 2)
        self.assertEqual(editor.time.text(), "")

    def test_list_shortcuts_manage_steps_but_text_delete_does_not(self):
        original = opening("00:10", "00:20")
        original["build_order"][0]["notes"] = ["第一步"]
        original["build_order"][1]["notes"] = ["第二步"]
        editor = self.editor(original)
        for shortcut in editor.step_shortcuts.values():
            self.assertEqual(shortcut.context(), Qt.WidgetShortcut)
        editor.steps.setFocus()
        QTest.keyClick(editor.steps, Qt.Key_Down, Qt.AltModifier)
        self.assertEqual(editor.index, 1)
        self.assertEqual(editor.data["build_order"][1]["notes"], ["第一步"])
        QTest.keyClick(editor.steps, Qt.Key_Up, Qt.AltModifier)
        self.assertEqual(editor.index, 0)
        self.assertEqual(editor.data["build_order"][0]["notes"], ["第一步"])
        QTest.keyClick(editor.steps, Qt.Key_Delete)
        self.assertEqual(len(editor.data["build_order"]), 1)
        QTest.keyClick(editor.steps, Qt.Key_Delete)
        self.assertEqual(len(editor.data["build_order"]), 1)
        QTest.keyClick(editor.steps, Qt.Key_Insert)
        self.assertEqual(len(editor.data["build_order"]), 2)
        QTest.keyClick(editor.steps, Qt.Key_D, Qt.ControlModifier)
        self.assertEqual(len(editor.data["build_order"]), 3)
        before = copy.deepcopy(editor.data["build_order"])
        editor.name.setText("ABC")
        editor.name.setFocus()
        editor.name.setCursorPosition(0)
        QTest.keyClick(editor.name, Qt.Key_Delete)
        self.assertEqual(editor.name.text(), "BC")
        QTest.keyClick(editor.name, Qt.Key_Insert)
        QTest.keyClick(editor.name, Qt.Key_D, Qt.ControlModifier)
        self.assertEqual(editor.data["build_order"], before)


if __name__ == "__main__":
    unittest.main()
