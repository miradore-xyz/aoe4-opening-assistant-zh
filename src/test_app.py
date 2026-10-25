"""本地验证：开局兼容性、原计时语义、编辑保存和显示设置。

运行方式见「源码构建说明」。test=测试，assert=断言。
"""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtTest import QTest, QSignalSpy
from PyQt5.QtWidgets import QApplication, QPushButton
from core import Clock, atomic_json, read_order, validate_order, timer_steps, timed_ids, load_settings
from app import Manager, Editor, ROOT, STYLE, VOICE_TEST
from native import GlobalHotkeys


def timed_example():
    """只用于离线回归的独立夹具，不依赖或安装出厂默认开局。"""
    return {
        "name": "计时回归夹具", "civilization": "any", "author": "隔离测试",
        "source": "仅供测试", "_zh_language": "zh-CN", "_zh_timing": "starting",
        "build_order": [
            {"population_count": 10 + index, "villager_count": 5 + index, "age": 1,
             "time": f"0:{index * 10:02d}",
             "resources": {"food": 5, "wood": index, "gold": 0, "stone": 0},
             "notes": [caption, "@unit_worker/villager.webp@ 村民；@resource/resource_gold.webp@ 黄金。"]}
            for index, caption in enumerate(("第一步：开始验证。", "第二步：十秒验证。", "第三步：验证完成。"))
        ],
    }


def manual_example():
    data = timed_example()
    data.update(name="龙之骑士团 · 手动回归夹具", civilization="Order of the Dragon")
    for step in data["build_order"]:
        step.pop("time")
    return data


def seed_test_examples(manager):
    """界面测试显式安装两份夹具，不受23文明默认库的内容和顺序影响。"""
    for name, data in (("01-手动夹具.json", manual_example()), ("02-计时功能演示.json", timed_example())):
        path = manager.orders_dir / name
        if not path.exists():
            atomic_json(path, data)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.sample = timed_example()

    def test_original_validator_accepts_both_examples(self):
        from aoe4.aoe4_build_order import check_valid_aoe4_build_order
        for data in (manual_example(), timed_example()):
            self.assertTrue(check_valid_aoe4_build_order(data)[0])

    def test_original_timer_semantics_and_grouping(self):
        self.assertEqual(timed_ids(self.sample, 9, starting=True), [0])
        self.assertEqual(timed_ids(self.sample, 10, starting=True), [1])
        self.assertEqual(timed_ids(self.sample, 1, starting=False), [1])
        self.assertEqual(timed_ids(self.sample, 10, starting=False), [1])
        self.assertEqual(timed_ids(self.sample, 11, starting=False), [2])
        self.sample["build_order"][2]["time"] = "0:10"
        self.assertEqual(timed_ids(self.sample, 10, starting=True), [1, 2])

    def test_missing_and_unsorted_times_use_manual(self):
        self.sample["build_order"][1].pop("time")
        self.assertFalse(timer_steps(self.sample))
        self.sample["build_order"][1]["time"] = "0:50"
        self.assertFalse(timer_steps(self.sample))

    def test_bad_file_has_chinese_error(self):
        self.sample["build_order"][0]["resources"]["food"] = "six"
        with self.assertRaisesRegex(ValueError, "食物村民必须是整数"):
            validate_order(self.sample)

    def test_clock_start_pause_seek(self):
        with patch("core.time.monotonic", return_value=100) as monotonic:
            clock = Clock()
            clock.start()
            monotonic.return_value = 104.5
            self.assertEqual(clock.seconds, 4.5)
            clock.pause()
            monotonic.return_value = 110
            self.assertEqual(clock.seconds, 4.5)
            clock.start()
            monotonic.return_value = 111
            self.assertEqual(clock.seconds, 5.5)
            clock.seek(30)
            monotonic.return_value = 112
            self.assertEqual(clock.seconds, 31)

    def test_global_shortcut_parser(self):
        self.assertEqual(GlobalHotkeys.parse("Ctrl+Alt+F7"), (0x4003, 0x76))
        with self.assertRaises(ValueError):
            GlobalHotkeys.parse("A")


class InterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setStyleSheet(STYLE)

    def setUp(self):
        replacement = patch.object(Manager, "seed", seed_test_examples)
        replacement.start()
        self.addCleanup(replacement.stop)

    def test_complete_local_workflow(self):
        with tempfile.TemporaryDirectory(prefix="aoe4-zh-", dir=os.environ.get("AOE4_TEST_WORK")) as folder:
            folder = Path(folder)
            # 本测试不占用真实桌面正在使用的快捷键。
            atomic_json(folder / "设置.json", {"voice_engine": "system", "hotkeys": {key: "" for key in ["previous", "next", "pause", "visible", "passthrough", "reset"]}})
            manager = Manager(self.application, folder)
            self.assertEqual(manager.library.count(), 2)
            self.assertIn("龙之骑士团", manager.order["name"])
            self.assertFalse(manager.settings["speech"])
            manager.step(1)
            self.assertEqual(manager.ids, [1])
            manager.step(-1)
            self.assertEqual(manager.ids, [0])
            manager.toggle_clock()
            self.assertTrue(manager.clock.running)
            manager.toggle_clock()
            self.assertFalse(manager.clock.running)
            manager.font_input.setValue(17)
            manager.width_input.setValue(500)
            manager.height_input.setValue(420)
            manager.x_input.setValue(120)
            manager.y_input.setValue(80)
            manager.interval_input.setValue(15)
            manager.reminders_check.setChecked(True)
            manager.apply_settings()
            settings, warning = load_settings(folder / "设置.json")
            self.assertEqual(settings["font_size"], 17)
            self.assertEqual(settings["width"], 500)
            self.assertEqual(settings["height"], 420)
            self.assertEqual((manager.overlay.width(), manager.overlay.height()), (500, 420))
            self.assertFalse(hasattr(manager.overlay, "footer"))
            self.assertNotIn("面板", [b.text() for b in manager.overlay.findChildren(QPushButton)])
            self.assertTrue(all(not b.text() and not b.icon().isNull() for b in [manager.overlay.previous, manager.overlay.pause, manager.overlay.next]))
            self.assertEqual((settings["x"], settings["y"]), (120, 80))
            self.assertFalse(warning)
            demo = timed_example()
            # 模拟现有英文原版开局，保留作者扩展字段与原文。
            demo["name"] = "English compatibility fixture"
            demo["author"] = "Original author"
            demo["build_order"][0]["notes"] = ["Keep making villagers @unit_worker/villager.webp@"]
            demo["external_metadata"] = {"preserve": [1, 2, "原文"]}
            demo.pop("_zh_language", None)
            fixture = folder / "原版格式.json"
            atomic_json(fixture, demo)
            before = fixture.read_bytes()
            first = manager.import_file(fixture)
            second = manager.import_file(fixture)
            self.assertNotEqual(first, second)
            self.assertEqual(before, fixture.read_bytes())
            self.assertEqual(read_order(first), demo)
            manager.refresh_library(first)
            self.assertIn("正文保留原语言", manager.order_info.text())
            self.assertIn("Keep making villagers", manager.overlay.notes.text())
            self.assertIn("<img", manager.overlay.notes.text())
            self.assertTrue(manager.settings["auto"], "带完整时间的开局应默认自动切换，避免计时却不提醒")
            # 回归用户遇到的情况：试听能响，但开关未应用导致实际流程没有朗读。
            with patch.object(manager.speech, "say") as speak:
                manager.quick_speech.setChecked(True)
                self.assertTrue(manager.settings["speech"])
                self.assertTrue(manager.speech_check.isChecked())
                self.assertTrue(load_settings(folder / "设置.json")[0]["speech"])
                speak.assert_called_once()
                speak.reset_mock()
                manager.toggle_clock()
                self.assertTrue(manager.clock.running)
                self.assertIn("Keep making villagers", speak.call_args.args[0])
                speak.reset_mock()
                manager.clock.seek(11)
                with patch("app.winsound.PlaySound"):
                    manager.tick()
                self.assertIn("第二步", speak.call_args.args[0])
                manager.toggle_clock()
                manager.reset_clock()
                manager.quick_speech.setChecked(False)
            exported = folder / "导出.json"
            atomic_json(exported, manager.order)
            self.assertEqual(read_order(exported), demo)
            editor = Editor(manager, demo)
            editor.name.setText("验证中文编辑保存")
            editor.notes.setPlainText("持续造村民\n检查备用金矿")
            editor.numbers["gold"].setValue(7)
            editor.save()
            self.assertEqual(editor.result(), editor.Accepted)
            self.assertEqual(editor.data["build_order"][0]["notes"], ["持续造村民", "检查备用金矿"])
            self.assertEqual(editor.data["build_order"][0]["resources"]["gold"], 7)
            self.assertEqual(editor.data["external_metadata"], demo["external_metadata"])
            atomic_json(exported, editor.data)
            self.assertEqual(read_order(exported)["name"], "验证中文编辑保存")
            manager.auto_check.setChecked(True)
            manager.clock.seek(11)
            manager.clock.start()
            with patch("app.winsound.PlaySound") as sound:
                manager.tick()
                sound.assert_called_once()
            self.assertEqual(manager.ids, [1])
            self.assertIn("到点提醒", manager.step_alert)
            manager.clock.seek(30)
            manager.tick()
            self.assertEqual(manager.ids, [2])
            self.assertIn("检查", manager.reminder_text)
            manager.reset_clock()
            self.assertEqual(manager.clock.seconds, 0)
            self.assertFalse(manager.clock.running)
            manager.passthrough_check.setChecked(True)
            self.assertTrue(manager.overlay.windowFlags() & Qt.WindowTransparentForInput)
            manager.passthrough_check.setChecked(False)
            self.assertFalse(manager.overlay.windowFlags() & Qt.WindowTransparentForInput)
            self.assertIn("&lt;script&gt;", manager.renderer.html("<script>文本</script>"))
            self.assertIsNone(manager.renderer.image_path("../LICENSE"))
            manager.close()
            reopened = Manager(self.application, folder)
            self.assertEqual(reopened.settings["font_size"], 17)
            self.assertEqual(reopened.settings["width"], 500)
            self.assertEqual(reopened.settings["height"], 420)
            self.assertEqual(reopened.order["name"], demo["name"])
            reopened.close()

    def test_compact_window_remove_undo_and_web_link(self):
        """验证实际卡片叉号、空库恢复及窗口缩小后的可用尺寸。"""
        with tempfile.TemporaryDirectory(prefix="aoe4-ui-") as folder:
            folder = Path(folder)
            atomic_json(folder / "设置.json", {"hotkeys": {key: "" for key in ["previous", "next", "pause", "visible", "passthrough", "reset"]}})
            manager = Manager(self.application, folder)
            try:
                manager.show()
                manager.resize(760, 520)
                self.application.processEvents()
                self.assertEqual((manager.width(), manager.height()), (760, 520))
                self.assertEqual((manager.overlay.width(), manager.overlay.height()), (330, 220))
                self.assertEqual([item.text() for item in manager.navigation], ["开局库", "战绩分析", "设置", "帮助"])
                self.assertEqual(manager.collection_count.text(), "2")
                self.assertEqual(manager.work_views.currentIndex(), 0)
                current = manager.library.item(0)
                bounds = manager.library.visualItemRect(current)
                QTest.mouseClick(manager.library.viewport(), Qt.LeftButton, pos=bounds.center())
                self.application.processEvents()
                self.assertEqual(manager.work_views.currentIndex(), 1)
                self.assertTrue(manager.preview.isVisible())
                self.assertTrue(manager.console.isVisible())
                self.assertFalse(manager.pause_button.text())
                QTest.mouseClick(manager.options_button, Qt.LeftButton)
                self.application.processEvents()
                self.assertTrue(manager.quick_speech.isVisible())
                self.assertTrue(manager.rect().contains(manager.pause_button.mapTo(manager, QPoint(0, manager.pause_button.height() - 1))))
                QTest.mouseClick(manager.options_button, Qt.LeftButton)
                self.application.processEvents()
                self.assertLessEqual(manager.console.geometry().bottom(), manager.tabs.widget(0).height())
                QTest.mouseClick(manager.steps_list.viewport(), Qt.LeftButton,
                                 pos=manager.steps_list.visualItemRect(manager.steps_list.item(1)).center())
                self.assertEqual(manager.ids, [1])
                manager.show_library()
                self.application.processEvents()
                self.assertFalse(manager.page_subtitle.isVisible())
                self.assertGreaterEqual(manager.library.viewport().height(), 90)
                current = manager.library.item(0)
                path = Path(current.data(Qt.UserRole))
                original = path.read_bytes()
                bounds = manager.library.visualItemRect(current)
                QTest.mouseClick(manager.library.viewport(), Qt.LeftButton,
                                 pos=QPoint(bounds.right() - 14, bounds.top() + 17))
                self.assertFalse(path.exists())
                self.assertEqual(manager.work_views.currentIndex(), 0)
                self.assertEqual(manager.library.count(), 1)
                self.assertEqual(manager.collection_count.text(), "1")
                manager.undo_remove()
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(manager.library.count(), 2)
                with patch("app.webbrowser.open") as open_web:
                    manager.open_online_orders()
                    open_web.assert_called_once_with("https://aoe4guides.com/")
                while manager.library.count():
                    manager.remove_order(manager.library.item(0).data(Qt.UserRole))
                self.assertIsNone(manager.order)
                self.assertFalse(manager.clock.running)
                manager.undo_remove()
                self.assertEqual(manager.library.count(), 1)
                self.assertIsNotNone(manager.order)
            finally:
                manager.close()

    def test_library_layout_and_scroll_settle_after_resize_and_filter(self):
        """六张紧凑卡片跨列与滚动后保持稳定；搜索不触发滚动条宽度往复变化。"""
        with tempfile.TemporaryDirectory(prefix="aoe4-scroll-") as folder:
            folder = Path(folder)
            atomic_json(folder / "设置.json", {"hotkeys": {key: "" for key in ["previous", "next", "pause", "visible", "passthrough", "reset"]}})
            extra = timed_example()
            # 两张内置开局加四张测试开局，紧凑卡片在窄窗也会真实产生滚动范围。
            for number in range(1, 5):
                extra['name'] = '滚动验证开局 ' + str(number)
                atomic_json(folder / ('开局库/滚动验证-' + str(number) + '.json'), extra)
            manager = Manager(self.application, folder)
            try:
                manager.show()
                QTest.qWait(150)
                self.assertEqual(manager.library.count(), 6)
                first_row = [manager.library.visualItemRect(manager.library.item(i)).top() for i in range(3)]
                self.assertEqual(len(set(first_row)), 1, '默认宽窗的前三张卡片应稳定处于同一行')
                updates = QSignalSpy(manager.library.model().dataChanged)
                QTest.qWait(150)
                self.assertEqual(len(updates), 0, '静止时不应持续改写卡片尺寸')
                manager.resize(760, 520)
                QTest.qWait(150)
                scroll = manager.library.verticalScrollBar()
                self.assertGreater(scroll.maximum(), 0)
                scroll.setValue(scroll.maximum())
                position = scroll.value()
                width = manager.library.viewport().width()
                QTest.qWait(150)
                self.assertEqual(scroll.value(), position, '滚动位置不应被重排重置')
                manager.search.setText('滚动验证开局 1')
                QTest.qWait(100)
                self.assertEqual(sum(not manager.library.item(i).isHidden()
                                     for i in range(manager.library.count())), 1)
                self.assertEqual(manager.library.viewport().width(), width)
                manager.search.clear()
                manager.resize(1000, 680)
                QTest.qWait(150)
                rows = [manager.library.visualItemRect(manager.library.item(i)).top() for i in range(3)]
                self.assertEqual(len(set(rows)), 1)
                stable = [manager.library.visualItemRect(manager.library.item(i)) for i in range(3)]
                QTest.qWait(150)
                self.assertEqual(stable, [manager.library.visualItemRect(manager.library.item(i)) for i in range(3)])
            finally:
                manager.close()

    def test_online_voice_prepares_before_clock_and_routes_timed_steps(self):
        """准备阶段不计时；旧准备结果无效，实际流程只播放缓存接口。"""
        with tempfile.TemporaryDirectory(prefix="aoe4-online-ui-") as folder:
            folder = Path(folder)
            atomic_json(folder / "设置.json", {"voice_engine": "system", "hotkeys": {key: "" for key in ["previous", "next", "pause", "visible", "passthrough", "reset"]}})
            manager = Manager(self.application, folder)
            try:
                demo = folder / "开局库/02-计时功能演示.json"
                manager.refresh_library(demo)
                with patch.object(manager.online, "ready_for", return_value=False), \
                     patch.object(manager.online, "prepare", return_value="current-cache-job") as prepare, \
                     patch.object(manager.online, "say", return_value=True) as play:
                    manager.engine_input.setCurrentIndex(manager.engine_input.findData("online"))
                    manager.quick_speech.setChecked(True)
                    self.assertTrue(prepare.called)
                    self.assertFalse(manager.pause_button.isEnabled())
                    self.assertFalse(manager.clock.running)
                    manager.on_online_prepared("obsolete-job")
                    self.assertFalse(manager._online_ready)
                    manager.on_online_prepared("current-cache-job")
                    self.assertTrue(manager.pause_button.isEnabled())
                    manager.toggle_clock()
                    self.assertTrue(manager.clock.running)
                    self.assertIn("第一步", play.call_args.args[0])
                    manager.clock.seek(11)
                    with patch("app.winsound.PlaySound"):
                        manager.tick()
                    self.assertIn("第二步", play.call_args.args[0])
                    self.assertEqual(play.call_args.args[1:], ("zh-CN-XiaoxiaoNeural", 80, 0))
                    self.assertIn(VOICE_TEST, manager.voice_texts())
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
