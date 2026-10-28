"""设置自动保存的实际Qt交互：合并写盘、完整快捷键和演练连续性。"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeySequence
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QPushButton
from app import Manager, STYLE
from core import atomic_json
from test_app import destroy_test_manager, timed_example


class SettingsAutosaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        atomic_json(Path(self.folder.name) / "设置.json", {
            "voice_engine": "system", "speech": False, "hotkeys": {}, "timed_playback_policy": 1})
        self.patches = [patch.object(Manager, "seed"), patch("app.Speech.start"),
                        patch("app.GlobalHotkeys.apply", return_value=[]),
                        patch("update_ui.UpdateController.check"),
                        patch("core.time.monotonic", return_value=100.0)]
        for replacement in self.patches:
            replacement.start()
        self.application.setStyleSheet(STYLE)
        self.manager = Manager(self.application, self.folder.name)
        self.manager.updates._startup_timer.stop()
        self.manager.tick_timer.stop()
        self.manager.show()
        self.manager.navigate(1)
        QTest.qWait(30)

    def tearDown(self):
        destroy_test_manager(self.application, self.manager)
        for replacement in reversed(self.patches):
            replacement.stop()
        self.folder.cleanup()

    def saved(self):
        return json.loads(self.manager.settings_path.read_text(encoding="utf8"))

    def wait_saved(self):
        QTest.qWait(390)

    def test_initialization_is_not_an_edit_and_apply_button_is_removed(self):
        self.assertFalse(self.manager._settings_pending)
        self.assertFalse(self.manager._settings_save_timer.isActive())
        self.assertFalse(any(button.text() == "应用并保存设置"
                             for button in self.manager.findChildren(QPushButton)))

    def test_font_position_timing_merge_and_preserve_running_session(self):
        m = self.manager
        m.order = timed_example()
        m.ids = [1]
        m.clock.start()
        m.clock.seek(7)
        m.step_alert = "保留当前提醒"
        with patch.object(m, "save_settings", wraps=m.save_settings) as save:
            m.ui_font_input.setValue(15)
            m.x_input.setValue(101)
            m.x_input.setValue(120)
            m.y_input.setValue(80)
            m.timing_input.setCurrentIndex(m.timing_input.findData("starting"))
            QTest.qWait(80)
            save.assert_not_called()
            self.wait_saved()
            self.assertEqual(save.call_count, 1)
        saved = self.saved()
        self.assertEqual((saved["ui_font_size"], saved["x"], saved["y"], saved["timing"]),
                         (15, 120, 80, "starting"))
        self.assertEqual((m.overlay.x(), m.overlay.y()), (120, 80))
        self.assertEqual(m.ids, [1])
        self.assertTrue(m.clock.running)
        self.assertEqual(m.clock.seconds, 7)
        self.assertEqual(m.step_alert, "保留当前提醒")

    def test_program_position_sync_and_same_timing_do_not_write_or_loop(self):
        m = self.manager
        with patch.object(m, "save_settings") as save:
            m.x_input.setValue(250)
            m.settings.update(x=90, y=75)
            m.position_fields()
            m.timing_input.currentIndexChanged.emit(m.timing_input.currentIndex())
            self.wait_saved()
            save.assert_not_called()
        self.assertEqual((m.x_input.value(), m.y_input.value()), (90, 75))
        self.assertFalse(m._settings_save_timer.isActive())

    def test_actual_key_recording_waits_until_complete_before_registering(self):
        m = self.manager
        edit = m.key_inputs["previous"]
        edit.setFocus()
        with patch.object(m.hotkeys, "apply", return_value=[]) as apply:
            QTest.keyPress(edit, Qt.Key_F6, Qt.ControlModifier | Qt.AltModifier)
            QTest.qWait(400)
            apply.assert_not_called()
            QTest.keyRelease(edit, Qt.Key_F6, Qt.ControlModifier | Qt.AltModifier)
            QTest.keyRelease(edit, Qt.Key_Alt)
            QTest.keyRelease(edit, Qt.Key_Control)
            QTest.qWait(1550)
            self.assertEqual(m.settings["hotkeys"]["previous"], "Ctrl+Alt+F6")
            self.assertEqual(self.saved()["hotkeys"]["previous"], "Ctrl+Alt+F6")
            apply.assert_called_once()

    def test_one_finished_field_does_not_commit_another_unfinished_key(self):
        m = self.manager
        first, second = m.key_inputs["previous"], m.key_inputs["next"]
        first.setKeySequence(QKeySequence("Ctrl+Alt+F6"))
        first.editingFinished.emit()
        second.setKeySequence(QKeySequence("Ctrl+Alt+F7"))
        self.wait_saved()
        self.assertEqual(self.saved()["hotkeys"]["previous"], "Ctrl+Alt+F6")
        self.assertEqual(self.saved()["hotkeys"]["next"], "")
        second.editingFinished.emit()
        self.wait_saved()
        self.assertEqual(self.saved()["hotkeys"]["next"], "Ctrl+Alt+F7")

    def test_invalid_or_duplicate_keys_never_register_but_other_fields_save(self):
        m = self.manager
        for values in (("Ctrl+F7", "Ctrl+F7"), ("Ctrl+F7, Ctrl+F8", "")):
            with self.subTest(values=values), patch("app.message") as error, \
                    patch.object(m.hotkeys, "apply", return_value=[]) as apply:
                for key, value in zip(("previous", "next"), values):
                    m.key_inputs[key].setKeySequence(QKeySequence(value))
                    m.key_inputs[key].editingFinished.emit()
                m.ui_font_input.setValue(16 if m.ui_font_input.value() != 16 else 15)
                self.wait_saved()
                apply.assert_not_called()
                error.assert_called_once()
                self.assertEqual(m.settings["hotkeys"]["previous"], "")
                self.assertEqual(self.saved()["ui_font_size"], m.ui_font_input.value())

    def test_occupied_key_retains_previous_settings_and_restores_registration(self):
        m = self.manager
        previous = dict(m.settings["hotkeys"])
        m.key_inputs["previous"].setKeySequence(QKeySequence("Ctrl+Alt+F6"))
        m.key_inputs["previous"].editingFinished.emit()
        with patch.object(m.hotkeys, "apply", side_effect=[["previous"], []]) as apply:
            self.wait_saved()
        self.assertEqual(m.settings["hotkeys"], previous)
        self.assertEqual(apply.call_count, 2)
        self.assertEqual(apply.call_args_list[-1].args[0], previous)
        self.assertIn("占用", m.hotkey_status.text())
        self.assertTrue(m.hotkey_status.isVisible())

    def test_correcting_duplicate_commits_both_completed_fields(self):
        m = self.manager
        with patch("app.message"):
            for action in ("previous", "next"):
                m.key_inputs[action].setKeySequence(QKeySequence("Ctrl+F7"))
                m.key_inputs[action].editingFinished.emit()
            self.wait_saved()
        self.assertEqual(m.settings["hotkeys"]["previous"], "")
        # 其它有效设置照常保存，不能吞掉等待用户修正的快捷键字段。
        m.ui_font_input.setValue(15)
        self.wait_saved()
        m.key_inputs["next"].setKeySequence(QKeySequence("Ctrl+F8"))
        m.key_inputs["next"].editingFinished.emit()
        self.wait_saved()
        self.assertEqual(self.saved()["hotkeys"]["previous"], "Ctrl+F7")
        self.assertEqual(self.saved()["hotkeys"]["next"], "Ctrl+F8")

    def test_auto_write_failure_uses_existing_visible_problem_path(self):
        m = self.manager
        with patch("app.atomic_json", side_effect=OSError("只读测试")):
            m.ui_font_input.setValue(15)
            self.wait_saved()
        self.assertIn("设置未能写入", m.status_bar.text())
        self.assertTrue(m._problem_dialog.isVisible())

    def test_close_flushes_valid_pending_edits_without_waiting_for_debounce(self):
        m = self.manager
        m.ui_font_input.setValue(16)
        m.x_input.setValue(120)
        m.y_input.setValue(80)
        m.key_inputs["previous"].setKeySequence(QKeySequence("Ctrl+Alt+F6"))
        m.close()
        saved = self.saved()
        self.assertEqual((saved["ui_font_size"], saved["x"], saved["y"]), (16, 120, 80))
        self.assertEqual(saved["hotkeys"]["previous"], "Ctrl+Alt+F6")
        self.assertFalse(m._settings_save_timer.isActive())


if __name__ == "__main__":
    unittest.main()
