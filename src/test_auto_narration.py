"""按时间自动推进与朗读：修复手动查看后卡住、旧开关及同一轮播报抢占。"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QGroupBox
from PyQt5.QtWidgets import QApplication
from app import Manager, STYLE
from core import DEFAULTS, atomic_json, load_settings, timeline_selection
from test_app import timed_example, destroy_test_manager


def narration_order():
    data = timed_example()
    prototype = data["build_order"][0]
    data["build_order"] = [dict(prototype, time=time, notes=[text]) for time, text in (
        ("00:00", "第一步：开始采肉"), ("00:20", "第二步：建造房屋"),
        ("00:40", "第三步：采集木材"), ("01:00", "第四步：采集黄金"),
        ("1:00", "同时补充村民"))]
    return data


class AutoNarrationSettingsTests(unittest.TestCase):
    def test_defaults_and_old_disabled_settings_enable_timed_narration_once(self):
        self.assertTrue(DEFAULTS["auto"])
        self.assertTrue(DEFAULTS["speech"])
        self.assertNotIn("sound", DEFAULTS)
        self.assertNotIn("reminders", DEFAULTS)
        self.assertEqual(DEFAULTS["timing"], "starting")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "设置.json"
            atomic_json(path, dict(auto=False, speech=False, sound=True, timing="ending"))
            original = path.read_bytes()
            settings, warning = load_settings(path)
            self.assertFalse(warning)
            self.assertTrue(settings["auto"])
            self.assertTrue(settings["speech"])
            self.assertNotIn("sound", settings)
            self.assertEqual(settings["timing"], "starting")
            self.assertEqual(path.read_bytes(), original)
            settings.update(auto=False, speech=False, sound=True, timing="ending")
            atomic_json(path, settings)
            reopened, _ = load_settings(path)
            self.assertFalse(reopened["auto"])
            self.assertFalse(reopened["speech"])
            self.assertNotIn("sound", reopened)
            self.assertEqual(reopened["timing"], "ending")

    def test_manual_view_holds_until_next_time_group_and_delays_skip_old_groups(self):
        order = narration_order()
        selected, anchor = timeline_selection(order, 35, [0], manual_anchor=(1,))
        self.assertEqual(selected, [0])
        self.assertEqual(anchor, (1,))
        selected, anchor = timeline_selection(order, 40, selected, anchor)
        self.assertEqual(selected, [2])
        self.assertIsNone(anchor)
        selected, anchor = timeline_selection(order, 90, [0])
        self.assertEqual(selected, [3, 4])
        self.assertIsNone(anchor)


class AutoNarrationManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        # 主窗口关闭会退出事件循环；重置上一测试类遗留的退出状态再建新窗口。
        with patch("update_ui.UpdateController.check"):
            QTimer.singleShot(0, cls.application.quit)
            cls.application.exec_()
        cls.application.setStyleSheet(STYLE)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        folder = Path(self.directory.name)
        path = folder / "开局库/到点朗读回归.json"
        atomic_json(path, narration_order())
        # 故意保存旧版关闭状态，验证不用重新勾选就能得到新的默认行为。
        atomic_json(folder / "设置.json", dict(last_order=str(path), auto=False, speech=False,
                                                sound=True, reminders=True, reminder_seconds=20,
                                                reminder_text="这条旧循环检查不应朗读", timing="ending", voice_engine="system",
                                                hotkeys={}))
        self.patches = [patch.object(Manager, "seed"), patch("app.Speech.start"),
                        patch("app.Manager.register_hotkeys"), patch("update_ui.UpdateController.check")]
        for item in self.patches:
            item.start()
        self.manager = Manager(self.application, folder)
        self.manager.tick_timer.stop()
        self.manager.updates._startup_timer.stop()

    def tearDown(self):
        # 关闭并销毁独立正文窗及Manager，再处理事件；补丁保留到清理完成。
        destroy_test_manager(self.application, self.manager)
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def test_exact_boundary_advances_and_speaks_once_without_beep(self):
        manager = self.manager
        self.assertTrue(manager.quick_speech.isChecked())
        self.assertTrue(manager.speech_check.isChecked())
        self.assertTrue(manager.auto_check.isChecked())
        self.assertFalse(hasattr(manager, "sound_check"))
        with patch.object(manager.speech, "say") as speak, patch("winsound.PlaySound") as beep:
            manager.toggle_clock()
            self.assertIn("第一步", speak.call_args.args[0])
            speak.reset_mock()
            manager.clock.seek(20)
            manager.tick()
            self.assertEqual(manager.ids, [1])
            self.assertNotIn("到点提醒", manager.status_bar.text())
            speak.assert_called_once()
            self.assertIn("第二步", speak.call_args.args[0])
            for _ in range(4):
                manager.tick()
            manager.clock.seek(35)
            manager.tick()
            self.assertEqual(manager.ids, [1])
            speak.assert_called_once()
            beep.assert_not_called()

    def test_manual_previous_does_not_disable_auto_or_immediately_snap_back(self):
        manager = self.manager
        with patch.object(manager.speech, "say") as speak:
            manager.toggle_clock()
            manager.clock.seek(25)
            manager.tick()
            manager.step(-1)
            self.assertEqual(manager.ids, [0])
            self.assertTrue(manager.settings["auto"])
            self.assertTrue(manager.auto_check.isChecked())
            speak.reset_mock()
            manager.tick()
            manager.clock.seek(35)
            manager.tick()
            self.assertEqual(manager.ids, [0])
            speak.assert_not_called()
            manager.clock.seek(40)
            manager.tick()
            self.assertEqual(manager.ids, [2])
            self.assertIn("第三步", speak.call_args.args[0])
            speak.assert_called_once()

    def test_delayed_tick_reads_only_the_current_same_time_group(self):
        manager = self.manager
        with patch.object(manager.speech, "say") as speak:
            manager.toggle_clock()
            speak.reset_mock()
            manager.clock.seek(65)
            manager.tick()
            self.assertEqual(manager.ids, [3, 4])
            speak.assert_called_once()
            text = speak.call_args.args[0]
            self.assertIn("第四步", text)
            self.assertIn("同时补充村民", text)
            self.assertNotIn("第二步", text)
            self.assertNotIn("第三步", text)
            self.assertIn(text, manager.voice_texts())
            manager.tick()
            speak.assert_called_once()

    def test_manual_next_keeps_auto_and_does_not_repeat_the_previewed_node(self):
        manager = self.manager
        with patch.object(manager.speech, "say") as speak:
            manager.toggle_clock()
            manager.step(1)
            self.assertEqual(manager.ids, [1])
            self.assertTrue(manager.settings["auto"])
            speak.reset_mock()
            manager.clock.seek(5)
            manager.tick()
            self.assertEqual(manager.ids, [1])
            manager.clock.seek(20)
            manager.tick()
            self.assertEqual(manager.ids, [1])
            speak.assert_not_called()
            manager.clock.seek(40)
            manager.tick()
            self.assertEqual(manager.ids, [2])
            speak.assert_called_once()

    def test_conditional_notes_are_cached_with_their_timed_narration_group(self):
        manager = self.manager
        original = manager.order["build_order"][1]
        manager.order["build_order"].insert(2, dict(original, time="", notes=["根据侦察补充房屋"]))
        with patch.object(manager.speech, "say") as speak:
            manager.toggle_clock()
            speak.reset_mock()
            manager.clock.seek(20)
            manager.tick()
            self.assertEqual(manager.ids, [1, 2])
            speak.assert_called_once()
            self.assertIn("根据侦察补充房屋", speak.call_args.args[0])
            self.assertIn(speak.call_args.args[0], manager.voice_texts())

    def test_pause_holds_and_resume_announces_the_current_group_once(self):
        manager = self.manager
        with patch.object(manager.speech, "say") as speak:
            manager.toggle_clock()
            manager.toggle_clock()
            speak.reset_mock()
            manager.clock.seek(25)
            manager.tick()
            self.assertEqual(manager.ids, [0])
            speak.assert_not_called()
            manager.toggle_clock()
            self.assertEqual(manager.ids, [1])
            speak.assert_called_once()
            manager.tick()
            speak.assert_called_once()

    def test_old_loop_and_sound_settings_never_create_audio_or_controls(self):
        manager = self.manager
        for key in ("reminders", "reminder_seconds", "reminder_text", "sound"):
            self.assertNotIn(key, manager.settings)
        self.assertFalse(hasattr(manager.overlay, "reminder"))
        self.assertFalse(hasattr(manager, "reminders_check"))
        self.assertNotIn("这条旧循环检查不应朗读", manager.voice_texts())
        with patch.object(manager.speech, "say") as speak, patch("winsound.PlaySound") as beep:
            manager.toggle_clock()
            speak.reset_mock()
            manager.clock.seek(20)
            manager.tick()
            speak.assert_called_once()
            self.assertIn("第二步", speak.call_args.args[0])
            manager.tick()
            speak.assert_called_once()
            manager.clock.seek(65)
            manager.tick()
            speak.reset_mock()
            manager.clock.seek(85)
            manager.tick()
            speak.assert_not_called()
            beep.assert_not_called()

    def test_hotkey_settings_fill_the_former_right_column_and_stack_in_narrow_windows(self):
        manager = self.manager
        manager.show()
        manager.navigate(1)
        titles = [group.title() for group in manager.settings_scroll.findChildren(QGroupBox)]
        self.assertNotIn("循环检查提醒", titles)
        self.assertEqual(manager.settings_columns.indexOf(manager.appearance_group), 0)
        # 右栏包含两个独立分类；软件与数据不能变成快捷键组的子控件。
        self.assertEqual(manager.settings_columns.indexOf(manager.settings_right_column), 1)
        self.assertIs(manager.hotkeys_group.parentWidget(), manager.settings_right_column)
        self.assertIs(manager.software_data_group.parentWidget(), manager.settings_right_column)
        self.assertEqual(manager.settings_right_column.layout().indexOf(manager.hotkeys_group), 0)
        self.assertEqual(manager.settings_right_column.layout().indexOf(manager.software_data_group), 1)
        self.assertTrue(all(manager.hotkeys_group.isAncestorOf(widget)
                            for widget in manager.key_inputs.values()))
        manager.resize(1100, 780)
        QTest.qWait(30)
        appearance = manager.appearance_group.mapTo(manager, QPoint())
        hotkeys = manager.hotkeys_group.mapTo(manager, QPoint())
        self.assertGreater(hotkeys.x(), appearance.x())
        self.assertEqual(hotkeys.y(), appearance.y())
        software = manager.software_data_group.mapTo(manager, QPoint())
        self.assertEqual(software.x(), hotkeys.x())
        self.assertGreaterEqual(software.y(), hotkeys.y() + manager.hotkeys_group.height() + 12)
        manager.resize(760, 620)
        QTest.qWait(30)
        appearance = manager.appearance_group.mapTo(manager, QPoint())
        hotkeys = manager.hotkeys_group.mapTo(manager, QPoint())
        self.assertEqual(hotkeys.x(), appearance.x())
        self.assertGreater(hotkeys.y(), appearance.y())
        software = manager.software_data_group.mapTo(manager, QPoint())
        self.assertEqual(software.x(), hotkeys.x())
        self.assertGreaterEqual(software.y(), hotkeys.y() + manager.hotkeys_group.height() + 12)


if __name__ == "__main__":
    unittest.main()
