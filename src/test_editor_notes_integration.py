"""编辑器与内嵌图标的实际保存验证，不联网或读取用户资料。"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import QApplication, QLabel

from app import Editor, STYLE
from core import FIELDS, NoteRenderer
from notes_icons import ASSETS, IconNotesEdit, icon_entries
from visual import Resources


class EditorNotesIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setStyleSheet(STYLE)

    def test_editor_placeholder_and_icon_save_remain_compatible(self):
        editor = Editor(None)
        try:
            self.assertIsInstance(editor.notes, IconNotesEdit)
            self.assertEqual(editor.notes.toPlainText(), "")
            self.assertEqual(editor.notes.placeholderText(), "当前时间节点的补充说明\n简述操作要点与注意事项。")
            self.assertNotIn("补充提示（每行一条）", [label.text() for label in editor.findChildren(QLabel)])
            editor.name.setText("图标保存验证")
            editor.author.setText("隔离测试")
            editor.time.setText("00:10")
            entry = next(item for item in icon_entries(editor.civ.currentData())
                         if item["kind"] == "unit" and item["id"] == "gilded-spearman")
            self.assertTrue(editor.notes.insert_icon(entry))
            editor.notes.moveCursor(QTextCursor.End)
            editor.notes.insertPlainText(" 持续补充")
            expected = "@" + entry["icon"] + "@ 持续补充"
            self.assertEqual(editor.notes.toPlainText(), expected)
            editor.save()
            self.assertEqual(editor.result(), Editor.Accepted)
            self.assertEqual(editor.data["build_order"][0]["notes"], [expected])
            self.assertNotIn("\ufffc", expected)
            rendered = NoteRenderer(ASSETS).html(expected)
            self.assertIn("<img", rendered)
            reopened = Editor(None, editor.data)
            try:
                self.assertEqual(reopened.notes.toPlainText(), expected)
                reopened.save()
                self.assertEqual(reopened.result(), Editor.Accepted)
                self.assertEqual(reopened.data, editor.data)
            finally:
                reopened.deleteLater()
        finally:
            editor.deleteLater()
            self.application.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_all_resource_allocation_labels_use_one_mapping(self):
        expected = {"food": "吃肉村民", "wood": "采木村民", "gold": "采金村民", "stone": "采石村民"}
        for key, caption in expected.items():
            self.assertEqual(FIELDS[key], caption)
            self.assertEqual(Resources.definitions[key][0], caption)


if __name__ == "__main__":
    unittest.main()
