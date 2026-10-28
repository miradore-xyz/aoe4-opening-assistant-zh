"""时间节点就地编辑的真实Qt单击、草稿保留、校验及保存；仅隔离数据。"""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QPushButton
from app import Editor, STYLE
from core import atomic_json, read_order
from test_editor_required_fields import opening


class EditorInlineTimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.messages = patch('app.message', side_effect=AssertionError('时间错误不得弹对话框'))
        self.menu = patch.object(Editor, 'show_step_menu')
        self.messages.start()
        self.menu.start()
        self.editor = Editor(None, opening('00:10', '02:00'))
        self.editor.show()
        self.editor.activateWindow()
        QTest.qWait(20)

    def tearDown(self):
        QTest.qWait(20)
        self.editor.reject()
        self.editor.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.menu.stop()
        self.messages.stop()

    def click_node(self, row, button=Qt.LeftButton):
        item = self.editor.steps.item(row)
        self.editor.steps.scrollToItem(item)
        QTest.mouseClick(self.editor.steps.viewport(), button,
                         pos=self.editor.steps.visualItemRect(item).center())
        QTest.qWait(5)

    def type_time(self, text):
        self.editor.time.selectAll()
        QTest.keyClicks(self.editor.time, text)

    def test_single_click_and_second_click_on_selected_node_edit_in_the_left_list(self):
        editor = self.editor
        self.assertTrue(editor.time.isHidden())
        self.click_node(0)
        self.assertTrue(editor.time.isVisible())
        self.assertIs(self.app.focusWidget(), editor.time)
        self.assertIs(editor.time.parentWidget(), editor.steps.viewport())
        self.assertTrue(editor.steps.visualItemRect(editor.steps.item(0)).contains(editor.time.geometry()))
        self.type_time('01:05')
        QTest.keyClick(editor.time, Qt.Key_Return)
        self.assertTrue(editor.time.isHidden())
        self.assertEqual(editor.data['build_order'][0]['time'], '01:05')
        self.assertEqual(editor.steps.item(0).text(), '1分05秒')
        self.assertIsNone(editor.steps.item(0).data(Qt.ForegroundRole), '有效时间应使用深色主题的文字颜色')
        self.click_node(0)
        self.assertTrue(editor.time.isVisible(), '已选中节点也必须单击可编辑')
        self.assertEqual(editor.time.text(), '01:05')

    def test_focus_loss_and_switch_keep_raw_draft_on_the_original_step(self):
        editor = self.editor
        self.click_node(0)
        self.type_time('00:60')
        self.click_node(1)
        self.assertEqual(editor.index, 1)
        self.assertEqual(editor.data['build_order'][0]['time'], '00:60')
        self.assertEqual(editor.time.text(), '02:00')
        self.click_node(0)
        self.assertEqual(editor.time.text(), '00:60')
        self.assertTrue(editor.time.property('requiredMissing'))
        self.assertIsNotNone(editor.steps.item(0).data(Qt.ForegroundRole), '错误时间节点仍保留错误色')
        self.type_time('00:20')
        QTest.mouseClick(editor.notes.viewport(), Qt.LeftButton)
        self.assertTrue(editor.time.isHidden())
        self.assertEqual(editor.data['build_order'][0]['time'], '00:20')
        self.assertEqual(editor.index, 0)

    def test_unset_node_click_save_validation_equal_time_and_disk_roundtrip(self):
        editor = self.editor
        editor.add()
        self.assertEqual(editor.steps.item(1).text(), '未设置时间')
        self.click_node(1)
        self.assertEqual(editor.time.text(), '')
        self.type_time('00:60')
        accepted = QSignalSpy(editor.accepted)
        editor.save()
        self.assertEqual(len(accepted), 0)
        self.assertTrue(editor.time.isVisible())
        self.assertIs(self.app.focusWidget(), editor.time)
        self.type_time('00:10')
        editor.save()
        self.assertEqual(editor.result(), QDialog.Accepted)
        self.assertEqual([s['time'] for s in editor.data['build_order']], ['00:10','00:10','02:00'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'开局.json'
            atomic_json(path, editor.data)
            self.assertEqual(read_order(path), editor.data)

    def test_order_validation_and_structural_changes_do_not_overwrite_another_step(self):
        editor = self.editor
        original_second = copy.deepcopy(editor.data['build_order'][1])
        self.click_node(0)
        self.type_time('03:00')
        editor.save()
        self.assertEqual(editor.index, 1, '时间顺序错误仍定位到原先规则的首个错误节点')
        self.assertTrue(editor.time.property('requiredMissing'))
        self.assertEqual(editor.data['build_order'][1], original_second)
        self.click_node(0)
        self.type_time('00:15')
        editor.duplicate()
        self.assertEqual([s['time'] for s in editor.data['build_order']], ['00:15','00:15','02:00'])
        self.click_node(1)
        self.type_time('09:00')
        editor.remove()
        self.assertEqual([s['time'] for s in editor.data['build_order']], ['00:15','02:00'])
        self.assertEqual(editor.index, 1)
        self.assertEqual(editor.time.text(), '02:00')

    def test_removed_right_rows_leave_working_building_and_technology_buttons(self):
        editor = self.editor
        texts = [label.text() for label in editor.findChildren(QLabel)]
        self.assertNotIn('时间 *', texts)
        self.assertNotIn('建筑与科技', texts)
        self.assertNotIn('添加本时刻要建造的建筑或研究的科技', texts)
        self.assertTrue(editor.step_actions.scroll.isHidden(), '空动作列表不能留下说明行空位')
        for kind, caption in (('building','+ 建筑'), ('technology','+ 科技')):
            control = next(b for b in editor.step_actions.findChildren(QPushButton) if b.text()==caption)
            with patch.object(editor.step_actions, '_pick') as picked:
                QTest.mouseClick(control, Qt.LeftButton)
                picked.assert_called_once_with(kind)
        self.click_node(1, Qt.RightButton)
        self.assertTrue(editor.time.isHidden(), '右键管理节点仍不能变成就地编辑')


if __name__ == '__main__':
    unittest.main()
