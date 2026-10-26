"""悬浮提示只保留当前和前一个时间组；朗读仍仅使用当前选择。"""
import copy
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QFontDatabase, QTextDocument
from PyQt5.QtWidgets import QApplication

from app import STYLE
from core import timed_ids
from test_overlay_controls import Panel
import test_auto_narration as narration_tests


def grouped_order(prototype):
    order = {"name": "双阶段展示验证", "civilization": "English", "build_order": []}
    for time, notes, resources in (
        ("00:00", ["起始组独有内容 @unit_worker/villager.webp@"], {"food": 6}),
        ("", ["起始组附属条件"], {}),
        ("00:20", ["封建组独有内容 @unit_worker/villager.webp@"], {"food": 10, "wood": 3, "gold": 2}),
        ("0:20", ["封建组同刻行动"], {"wood": 5}),
        ("", ["封建组附属条件"], {}),
        ("00:40", ["城堡组独有内容 @unit_worker/villager.webp@", "<script>文本保留</script>"], {"food": 18, "wood": 4, "gold": 6}),
        ("01:00", ["帝王组独有内容 @unit_worker/villager.webp@"], {"food": 22, "wood": 10, "gold": 8}),
    ):
        step = copy.deepcopy(prototype)
        step.update(time=time, notes=notes)
        step["resources"] = {key: resources.get(key, -1) for key in ("food", "wood", "gold", "stone")}
        order["build_order"].append(step)
    return order


class OverlayStagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            path = Path("C:/Windows/Fonts") / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))

    def setUp(self):
        self.application.setStyleSheet(STYLE)
        self.panel = Panel()
        self.panel.order = grouped_order(self.panel.order["build_order"][0])
        self.original = copy.deepcopy(self.panel.order)
        self.panel.settings.update(width=330, height=220, font_size=11)
        self.overlay = self.panel.overlay
        self.overlay.apply_settings()
        self.overlay.show()
        self.application.processEvents()

    def tearDown(self):
        self.overlay.resize_save.stop()
        self.overlay.hide()
        self.overlay.deleteLater()
        self.panel.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)

    def render_at(self, seconds):
        self.panel.clock.seek(seconds)
        self.panel.ids = timed_ids(self.panel.order, seconds, starting=True)
        self.overlay.render()
        self.application.processEvents()
        return self.overlay.notes.text()

    def test_automatic_first_to_fourth_keep_current_and_two_previous_groups(self):
        first = self.render_at(0)
        self.assertIn("当前阶段 · 00:00", first)
        self.assertNotIn("上一阶段", first)
        self.assertEqual(self.overlay.current_stage_ids, [0, 1])
        self.assertEqual(self.overlay.previous_stage_ids, [])
        second = self.render_at(20)
        self.assertIn("起始组独有内容", second)
        self.assertIn("封建组独有内容", second)
        self.assertNotIn("城堡组独有内容", second)
        third = self.render_at(40)
        self.assertIn("城堡组独有内容", third)
        self.assertIn("封建组独有内容", third)
        self.assertIn("起始组独有内容", third)
        self.assertEqual(self.overlay.previous_previous_stage_ids, [0, 1])
        fourth = self.render_at(60)
        document = QTextDocument()
        document.setHtml(fourth)
        self.assertNotIn("起始组独有内容", document.toPlainText())
        self.assertIn("封建组独有内容", fourth)
        self.assertIn("城堡组独有内容", fourth)
        self.assertIn("帝王组独有内容", fourth)
        self.assertEqual(third.count('data-stage="current"'), 1)
        self.assertEqual(third.count('data-stage="previous"'), 1)
        self.assertEqual(third.count('data-stage="previous_previous"'), 1)
        self.assertEqual(self.panel.order, self.original)

    def test_equal_times_and_unset_conditions_remain_in_their_group(self):
        rendered = self.render_at(40)
        self.assertEqual(self.overlay.previous_stage_ids, [2, 3, 4])
        self.assertEqual(self.overlay.current_stage_ids, [5])
        self.assertIn("封建组同刻行动", rendered)
        self.assertIn("封建组附属条件", rendered)
        self.assertIn("条件提示", rendered)
        self.assertEqual(self.overlay.resources.cells["food"][2].text(), "18")
        self.assertEqual(self.overlay.resources.cells["gold"][2].text(), "6")

    def test_manual_jump_uses_target_previous_group_not_viewing_history(self):
        self.render_at(40)
        self.panel.ids = [3]
        self.overlay.render()
        rendered = self.overlay.notes.text()
        self.assertEqual(self.panel.ids, [3], "展示不改写计时与朗读的选择索引")
        self.assertEqual(self.overlay.current_stage_ids, [2, 3, 4])
        self.assertEqual(self.overlay.previous_stage_ids, [0, 1])
        self.assertIn("起始组独有内容", rendered)
        self.assertIn("封建组附属条件", rendered)
        self.assertNotIn("城堡组独有内容", rendered)
        self.assertEqual(self.overlay.resources.cells["food"][2].text(), "10")
        self.assertEqual(self.overlay.resources.cells["wood"][2].text(), "5")

    def test_both_groups_preserve_rich_icons_and_current_is_first(self):
        rendered = self.render_at(40)
        current, previous = rendered.split('<div data-stage="previous">', 1)
        self.assertIn("<img", current)
        self.assertIn("<img", previous)
        self.assertIn("&lt;script&gt;", current)
        self.assertNotIn("<script>", rendered)
        self.assertIn("font-size:11px", current)
        self.assertIn("font-size:11px", previous)
        document = QTextDocument()
        document.setHtml(rendered)
        plain = document.toPlainText()
        self.assertLess(plain.index("当前阶段"), plain.index("上一阶段"))
        self.assertLess(plain.index("城堡组独有内容"), plain.index("封建组独有内容"))

    def test_stage_change_returns_scroll_to_current_and_removes_banner_and_flag_space(self):
        self.panel.order["build_order"][2]["notes"] = ["长提示 " * 180]
        self.render_at(20)
        bar = self.overlay.scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(bar.maximum())
        self.render_at(40)
        self.assertEqual(bar.value(), 0)
        self.assertEqual(self.overlay.scroll.verticalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        self.assertEqual(self.overlay.width(), 330)
        content_cap = round(QApplication.primaryScreen().availableGeometry().height() * .75)
        self.assertLessEqual(self.overlay.height(), content_cap + 18)
        self.assertLessEqual(self.overlay.scroll.geometry().bottom(), content_cap,
                             "额外18像素是正文外的渐隐，不得用来突破正文75%屏高上限")
        self.assertEqual(self.panel.settings["height"], 220, "自动增高不回写为用户设置")
        self.assertFalse(hasattr(self.overlay, "civ_image"))
        self.assertFalse(hasattr(self.overlay, "alert"))
        self.assertEqual(self.overlay.title.text(), self.panel.order["name"])
        self.panel.step_alert = "到点提醒 · 00:40 · 第6步"
        self.overlay.render()
        self.assertNotIn("到点提醒", self.overlay.notes.text())

    def test_all_manual_old_orders_keep_adjacent_steps_without_invented_times(self):
        for step in self.panel.order["build_order"]:
            step["time"] = ""
        self.panel.ids = [3]
        self.overlay.render()
        self.assertEqual(self.overlay.current_stage_ids, [3])
        self.assertEqual(self.overlay.previous_stage_ids, [2])
        self.assertNotIn("00:20", self.overlay.notes.text())


class OverlayStageNarrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        narration_tests.AutoNarrationManagerTests.setUpClass()

    def test_real_automatic_progress_renders_previous_but_only_speaks_current(self):
        case = narration_tests.AutoNarrationManagerTests("test_exact_boundary_advances_and_speaks_once_without_beep")
        case.setUp()
        try:
            manager = case.manager
            manager.order = grouped_order(manager.order["build_order"][0])
            manager.ids = timed_ids(manager.order, 0, starting=True)
            manager.settings.update(speech=True, auto=True, voice_engine="system", timing="starting")
            with patch.object(manager.speech, "say") as speak:
                manager.toggle_clock()
                manager.clock.seek(20)
                speak.reset_mock()
                manager.tick()
                self.assertIn("起始组独有内容", manager.overlay.notes.text())
                self.assertIn("封建组独有内容", manager.overlay.notes.text())
                self.assertIn("封建组独有内容", speak.call_args.args[0])
                self.assertNotIn("起始组独有内容", speak.call_args.args[0])
                self.assertNotIn("上一阶段", speak.call_args.args[0])
                self.assertIn(speak.call_args.args[0], manager.voice_texts())
                manager.clock.seek(40)
                speak.reset_mock()
                manager.tick()
                self.assertIn("封建组独有内容", manager.overlay.notes.text())
                self.assertIn("起始组独有内容", manager.overlay.notes.text())
                self.assertIn("城堡组独有内容", speak.call_args.args[0])
                self.assertNotIn("封建组独有内容", speak.call_args.args[0])
                speak.assert_called_once()
                manager.clock.seek(60)
                speak.reset_mock()
                manager.tick()
                document = QTextDocument()
                document.setHtml(manager.overlay.notes.text())
                self.assertNotIn("起始组独有内容", document.toPlainText())
                self.assertIn("帝王组独有内容", speak.call_args.args[0])
                self.assertNotIn("城堡组独有内容", speak.call_args.args[0])
        finally:
            case.tearDown()


if __name__ == "__main__":
    unittest.main()
