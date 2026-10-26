"""图标提示的真实 Qt 文档、文明过滤与菜单反馈验证。"""
from __future__ import annotations

import os
import re
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
from PyQt5.QtGui import QMouseEvent, QTextCursor
from PyQt5.QtWidgets import QApplication, QComboBox, QLineEdit, QTextEdit

from edit_menus import MENU_STYLE, install_chinese_edit_menus
from notes_icons import IconNotesEdit, NotesIconPicker, icon_entries


class NotesIconsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        install_chinese_edit_menus(cls.application)

    def editor(self):
        editor = IconNotesEdit(lambda: "English")
        self.addCleanup(editor.deleteLater)
        return editor

    def test_rendered_images_roundtrip_without_losing_unknown_tokens(self):
        editor = self.editor()
        text = "先吃羊 @resource/sheep.webp@\n未知 @outside/missing.webp@ 保留 👋\n末行\n"
        editor.setPlainText(text)
        self.assertEqual(editor.toPlainText(), text)
        self.assertIn("\ufffc", QTextEdit.toPlainText(editor))
        self.assertNotIn("\ufffc", editor.toPlainText())
        self.assertIn("@outside/missing.webp@", QTextEdit.toPlainText(editor))
        # 图片就是包内图案，不是把 HTML 标签当正文保存。
        self.assertIn("aoe4-note:resource/sheep.webp", editor.toHtml())

    def test_insert_replace_undo_redo_and_copy_paste_keep_original_tokens(self):
        editor = self.editor()
        editor.setPlainText("原文")
        editor.selectAll()
        self.assertTrue(editor.insert_icon("resource/resource_gold.webp"))
        expected = "@resource/resource_gold.webp@"
        self.assertEqual(editor.toPlainText(), expected)
        editor.undo()
        self.assertEqual(editor.toPlainText(), "原文")
        editor.redo()
        self.assertEqual(editor.toPlainText(), expected)
        editor.selectAll()
        editor.copy()
        self.assertEqual(self.application.clipboard().text(), expected)
        pasted = self.editor()
        pasted.paste()
        self.assertEqual(pasted.toPlainText(), expected)
        self.assertIn("\ufffc", QTextEdit.toPlainText(pasted))
        pasted.undo()
        self.assertEqual(pasted.toPlainText(), "")

    def test_partial_selection_uses_qt_unicode_positions(self):
        editor = self.editor()
        editor.setPlainText("👋采集 @resource/resource_wood.webp@ 完成")
        cursor = editor.textCursor()
        cursor.setPosition(2)
        cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
        editor.setTextCursor(cursor)
        self.assertEqual(editor.createMimeDataFromSelection().text(),
                         "采集 @resource/resource_wood.webp@ 完成")

    def test_rich_clipboard_is_plain_and_missing_images_preserve_text(self):
        editor = self.editor()
        mime = QMimeData()
        mime.setHtml('<img src="file:///C:/private.png"><script>unexpected</script>')
        mime.setText("保留 @unknown/file.webp@")
        editor.insertFromMimeData(mime)
        self.assertEqual(editor.toPlainText(), "保留 @unknown/file.webp@")
        before = editor.toPlainText()
        self.assertFalse(editor.insert_icon("../outside.png"))
        self.assertEqual(editor.toPlainText(), before)
        editor.setReadOnly(True)
        self.assertFalse(editor.insert_icon("resource/sheep.webp"))

    def test_unit_and_building_choices_use_exact_civilization(self):
        english = icon_entries("English")
        dragon = icon_entries("Order of the Dragon")
        french = icon_entries("French")
        ids = lambda entries: {entry["id"] for entry in entries if entry["kind"] == "unit"}
        self.assertIn("longbowman", ids(english))
        self.assertNotIn("longbowman", ids(french))
        self.assertIn("gilded-spearman", ids(dragon))
        self.assertNotIn("gilded-spearman", ids(english))
        buildings = lambda entries: {entry["base_id"] for entry in entries if entry["kind"] == "building"}
        self.assertIn("school-of-cavalry", buildings(french))
        self.assertNotIn("school-of-cavalry", buildings(english))
        common = icon_entries(["English", "French"])
        self.assertNotIn("longbowman", ids(common))
        self.assertTrue({"resource", "unit", "building", "technology"}.issubset(
            {entry["kind"] for entry in english}))
        unknown = icon_entries("未收录文明")
        self.assertTrue(unknown)
        self.assertEqual({entry["kind"] for entry in unknown}, {"resource"})

    def test_picker_filters_selection_and_fits_small_desktop(self):
        picker = NotesIconPicker("English")
        self.addCleanup(picker.deleteLater)
        picker.resize(560, 440)
        picker.show()
        self.application.processEvents()
        picker.kind.setCurrentIndex(picker.kind.findData("unit"))
        picker.search.setText("长弓")
        self.assertGreater(picker.list.count(), 0)
        self.assertTrue(all("长弓" in picker.list.item(i).text() for i in range(picker.list.count())))
        self.assertTrue(picker.insert_button.isEnabled())
        self.assertIn("已选：", picker.selection_label.text())
        self.assertLessEqual(picker.insert_button.geometry().right(), picker.width())
        self.assertEqual(picker.list.verticalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        picker.search.setText("不存在的图标")
        self.assertFalse(picker.insert_button.isEnabled())
        self.assertIsNone(picker.selected_entry())
        picker.close()

    def test_standard_menu_has_chinese_labels_shortcuts_and_visible_hover(self):
        editor = self.editor()
        editor.setPlainText("菜单反馈")
        editor.selectAll()
        menu = editor.build_context_menu()
        self.addCleanup(menu.deleteLater)
        captions = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertIn("插入图标…", captions)
        self.assertFalse(any(re.search(r"[（(]&?[A-Za-z][）)]", caption) for caption in captions))
        self.assertTrue(any("Ctrl+" in caption or not action.shortcut().isEmpty()
                            for caption, action in [(action.text(), action) for action in menu.actions()]))
        self.assertIn("QMenu::item:selected", menu.styleSheet())
        enabled = next(action for action in menu.actions() if action.isEnabled() and not action.isSeparator())
        menu.popup(QPoint(50, 50))
        self.application.processEvents()
        before = menu.grab().toImage()
        rect = menu.actionGeometry(enabled)
        # 无屏幕测试平台不产生系统光标位置，直接发送同样的 Qt 鼠标移动事件。
        move = QMouseEvent(QEvent.MouseMove, QPointF(rect.center()),
                           QPointF(menu.mapToGlobal(rect.center())),
                           Qt.NoButton, Qt.NoButton, Qt.NoModifier)
        self.application.sendEvent(menu, move)
        self.application.processEvents()
        after = menu.grab().toImage()
        self.assertIs(menu.activeAction(), enabled)
        self.assertNotEqual(before, after)
        menu.close()
        # 普通单行输入框也会在显示标准菜单时获得同样的中文菜单样式。
        line = QLineEdit("普通文本")
        self.addCleanup(line.deleteLater)
        other = line.createStandardContextMenu()
        self.addCleanup(other.deleteLater)
        other.popup(QPoint(50, 50))
        self.application.processEvents()
        self.assertEqual(other.styleSheet(), MENU_STYLE)
        self.assertFalse(any(re.search(r"\(&?[A-Za-z]\)", action.text()) for action in other.actions()))
        other.close()
        combo = QComboBox()
        combo.setEditable(True)
        combo.setEditText("可编辑选项")
        self.addCleanup(combo.deleteLater)
        combo_menu = combo.lineEdit().createStandardContextMenu()
        self.addCleanup(combo_menu.deleteLater)
        combo_menu.popup(QPoint(50, 50))
        self.application.processEvents()
        self.assertEqual(combo_menu.styleSheet(), MENU_STYLE)
        self.assertFalse(any(re.search(r"[（(]&?[A-Za-z][）)]", action.text())
                             for action in combo_menu.actions()))
        combo_menu.close()


if __name__ == "__main__":
    unittest.main()
