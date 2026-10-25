"""编辑器界面改进的离线集成验证。

picker=选择器，filter=筛选，roundtrip=保存后重新打开，geometry=控件几何。
使用实际资料和实际 Qt 控件，不联网，不读取或修改用户开局与设置。
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt, QPoint, QTimer, QEvent
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog

from app import Editor, STYLE
from catalog import catalog_icon, load_catalog, technology_buildings
from core import DEFAULTS, FIELDS, atomic_json, load_settings, read_order
from editor_actions import ActionPicker
from windowing import DialogBar, FramelessDialog


SOURCE = Path(__file__).resolve().parent
WORK = SOURCE.parents[2] / "work"
DRAGON = "Order of the Dragon"  # 龙之骑士团的兼容格式名称。


def fixture(civilization=DRAGON):
    """构造合法隔离开局，保留扩展字段供保存验证。"""
    return {
        "name": "界面改进离线验证", "civilization": civilization,
        "author": "隔离验证", "source": "本地测试资料",
        "build_order": [Editor.blank()],
        "external_metadata": {"preserve": ["用户扩展字段", 3]},
    }


class InterfaceRefinementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        # Manager.closeEvent 会明确退出共用的 QApplication，留下已退出的循环状态。
        # 测试夹具没有真实主窗口，先完整运行一次立即结束的主循环，隔离前组状态。
        cls.original_quit_policy = cls.application.quitOnLastWindowClosed()
        cls.application.setQuitOnLastWindowClosed(False)
        QTimer.singleShot(0, cls.application.quit)
        cls.application.exec_()
        # Windows 离屏插件不会自动发现全部字体，显式载入以测量真实中文布局。
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            font = Path("C:/Windows/Fonts") / name
            if font.is_file():
                QFontDatabase.addApplicationFont(str(font))
        WORK.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.application.setQuitOnLastWindowClosed(cls.original_quit_policy)

    def setUp(self):
        self.application.setStyleSheet(STYLE)
        self.windows = []

    def tearDown(self):
        for window in reversed(self.windows):
            window.reject()
            window.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()

    def editor(self, civilization=DRAGON):
        editor = Editor(None, fixture(civilization))
        self.windows.append(editor)
        return editor

    def picker(self, civilization=DRAGON, kind="technology"):
        picker = ActionPicker(civilization, kind)
        self.windows.append(picker)
        return picker

    def group(self, picker, group_id):
        """通过真实左侧建筑列表切换分类，验证绑定后的筛选结果。"""
        for row in range(picker.building_list.count()):
            if picker.building_list.item(row).data(Qt.UserRole) == group_id:
                picker.building_list.setCurrentRow(row)
                self.application.processEvents()
                return picker.building_list.item(row)
        self.fail("缺少研究位置分类：" + group_id)

    @staticmethod
    def entries(picker):
        return [picker.list.item(row).data(Qt.UserRole)
                for row in range(picker.list.count())]

    def select_technology(self, picker, base_id):
        for row, entry in enumerate(self.entries(picker)):
            if entry.get("base_id") == base_id:
                picker.list.setCurrentRow(row)
                return picker.selected_action()
        self.fail("当前分类缺少科技：" + base_id)

    def test_mill_and_lumber_camp_keep_research_locations_separate(self):
        """磨坊显示独轮推车，林业仅出现在伐木场分类。"""
        picker = self.picker()
        self.group(picker, "mill")
        mill = {entry["base_id"] for entry in self.entries(picker)}
        self.assertIn("wheelbarrow", mill)
        self.assertNotIn("forestry", mill)
        self.group(picker, "lumber-camp")
        lumber = {entry["base_id"] for entry in self.entries(picker)}
        self.assertIn("forestry", lumber)
        self.assertNotIn("wheelbarrow", lumber)

    def test_shared_blacksmith_technology_and_landmark_exclusive(self):
        """共有升级在两处可选，黄金胸甲只在迈因维克宫殿。"""
        picker = self.picker()
        self.group(picker, "blacksmith")
        smith_ids = {entry["base_id"] for entry in self.entries(picker)}
        smith_action = self.select_technology(picker, "bloomery")
        self.assertNotIn("golden-cuirass", smith_ids)
        self.group(picker, "meinwerk-palace")
        landmark_ids = {entry["base_id"] for entry in self.entries(picker)}
        landmark_action = self.select_technology(picker, "bloomery")
        self.assertEqual(smith_action, landmark_action)
        self.assertIn("golden-cuirass", landmark_ids)
        golden = self.select_technology(picker, "golden-cuirass")
        self.assertEqual(golden["produced_by"], ["meinwerk-palace"])

    def test_capital_town_center_and_special_research_categories(self):
        """首都城镇中心、英雄升级、帝国议会独立呈现，分类本身不是科技。"""
        picker = self.picker()
        capital = self.group(picker, "capital-town-center")
        self.assertEqual(capital.text(), "首都城镇中心")
        self.assertFalse(capital.icon().isNull())
        textiles = self.select_technology(picker, "textiles")
        self.assertIn("capital-town-center", textiles["produced_by"])
        for civilization, group_id, title in (
            ("Jeanne d'Arc", "special:英雄升级", "英雄升级"),
            ("Ottomans", "special:帝国议会", "帝国议会"),
        ):
            with self.subTest(civilization=civilization):
                special = self.picker(civilization)
                self.assertEqual(self.group(special, group_id).text(), title)
                choices = self.entries(special)
                self.assertTrue(choices)
                self.assertTrue(all(entry["kind"] == "technology" for entry in choices))
                self.assertTrue(all(entry["location_zh"] == title for entry in choices))
                action = special.selected_action()
                self.assertIn(action["id"], {entry["id"] for entry in choices})
                self.assertNotEqual(action["id"], group_id)

    def test_search_age_and_building_intersect_and_save_real_technology(self):
        """三种筛选取交集，确认保存完整科技条目，而非建筑分类标题。"""
        picker = self.picker()
        self.group(picker, "mill")
        picker.search.setText("独轮推车")
        picker.age.setCurrentIndex(picker.age.findData(2))
        self.assertEqual(picker.list.count(), 0)
        self.assertFalse(picker.add_button.isEnabled())
        picker.age.setCurrentIndex(picker.age.findData(1))
        self.assertEqual(picker.list.count(), 1)
        self.assertTrue(picker.add_button.isEnabled())
        action = picker.selected_action()
        self.assertEqual(action["base_id"], "wheelbarrow")
        self.assertEqual(action["kind"], "technology")
        self.assertEqual(action["civilization"], DRAGON)
        self.assertEqual(action["produced_by"], ["mill"])
        self.assertEqual(action["count"], 1)
        self.assertFalse(catalog_icon(action).isNull())
        picker.search.setText("林业")
        self.assertEqual(picker.list.count(), 0)
        picker.search.setText("独轮推车")
        QTimer.singleShot(0, picker.add_button.click)
        self.assertEqual(picker.exec_(), QDialog.Accepted)
        editor = self.editor()
        editor.step_actions.add_action(action)
        editor.save()
        self.assertEqual(editor.result(), QDialog.Accepted)
        saved = editor.data["build_order"][0]["_zh_actions"][0]
        self.assertEqual(saved, action)
        self.assertNotEqual(saved["id"], "mill")

    def test_multi_civilization_research_locations_are_aggregated(self):
        """跨文明共有科技明确提示位置因文明而异，不沿用首个文明的说明。"""
        civilizations = ["English", DRAGON]
        technology = next(entry for entry in load_catalog(civilizations, "technology")
                          if entry["base_id"] == "bloomery")
        self.assertTrue(technology["location_zh"].startswith("因文明而异："))
        self.assertIn("铁匠铺", technology["location_zh"])
        self.assertIn("迈因维克宫殿", technology["location_zh"])
        self.assertEqual(set(technology["produced_by"]), {"blacksmith", "meinwerk-palace"})
        groups = technology_buildings(civilizations, [technology])
        self.assertEqual({group["id"] for group in groups}, {"blacksmith", "meinwerk-palace"})
        self.assertTrue(all(technology["id"] in group["technology_ids"] for group in groups))

    def test_generic_aliases_have_one_choice_and_preserve_original_on_roundtrip(self):
        """历史通用别名仅显示一次，原文件未改文明时保留兼容标识。"""
        with tempfile.TemporaryDirectory(prefix="ui-generic-", dir=str(WORK)) as folder:
            for original in ("any", "Any", "Generic"):
                with self.subTest(alias=original):
                    editor = self.editor(original)
                    generic_rows = [row for row in range(editor.civ.count())
                                    if editor.civ.itemText(row) == "通用"]
                    self.assertEqual(len(generic_rows), 1)
                    self.assertEqual(editor.civ.itemData(generic_rows[0]), "any")
                    self.assertEqual(editor.civ.currentData(), "any")
                    editor.save()
                    self.assertEqual(editor.result(), QDialog.Accepted)
                    self.assertEqual(editor.data["civilization"], original)
                    path = Path(folder) / (original + ".json")
                    atomic_json(path, editor.data)
                    reopened = Editor(None, read_order(path))
                    self.windows.append(reopened)
                    reopened.save()
                    self.assertEqual(reopened.data, editor.data)
                    self.assertEqual(reopened.data["external_metadata"], fixture()["external_metadata"])

    def test_editor_and_pickers_use_frameless_modal_dialog_controls(self):
        """自绘关闭与 Esc 都取消；保存或添加仍返回 Accepted（已确认）。"""
        factories = (
            (self.editor, lambda dialog: dialog.save()),
            (self.picker, lambda dialog: dialog.add_button.click()),
            (lambda: self.picker(kind="building"), lambda dialog: dialog.add_button.click()),
        )
        for make, accept in factories:
            for action in ("close", "escape", "accept"):
                dialog = make()
                with self.subTest(dialog=type(dialog).__name__, action=action):
                    self.assertIsInstance(dialog, FramelessDialog)
                    self.assertIsInstance(dialog.dialog_bar, DialogBar)
                    self.assertTrue(dialog.windowFlags() & Qt.FramelessWindowHint)
                    if action == "close":
                        callback = dialog.dialog_bar.close_button.click
                    elif action == "escape":
                        callback = lambda current=dialog: QTest.keyClick(current, Qt.Key_Escape)
                    else:
                        callback = lambda current=dialog, handler=accept: handler(current)
                    QTimer.singleShot(0, callback)
                    expected = QDialog.Accepted if action == "accept" else QDialog.Rejected
                    self.assertEqual(dialog.exec_(), expected)
                    self.assertFalse(dialog._windowing_active)

    def test_resource_inputs_keep_width_row_and_column_alignment_at_both_sizes(self):
        """八个资源输入框两列等宽，每行垂直居中，窄窗口不挤压数字框。"""
        editor = self.editor()
        keys = list(FIELDS)
        for width in (880, 760):
            with self.subTest(width=width):
                editor.resize(width, 700)
                editor.show()
                self.application.processEvents()
                boxes = [editor.numbers[key] for key in keys]
                self.assertEqual(len({box.width() for box in boxes}), 1)
                self.assertTrue(all(box.width() <= 96 for box in boxes))
                # 紧凑输入仍必须给文字和底边留足高度，不能靠裁切内容缩小。
                self.assertTrue(all(box.height() >= box.fontMetrics().height() + 8 for box in boxes))
                self.assertTrue(all(box.lineEdit().height() >= box.fontMetrics().height() for box in boxes))
                positions = [box.mapTo(editor, QPoint(0, 0)) for box in boxes]
                self.assertEqual(len({position.x() for position in positions[::2]}), 1)
                self.assertEqual(len({position.x() for position in positions[1::2]}), 1)
                self.assertGreater(positions[1].x(), positions[0].x() + boxes[0].width())
                for left, right in zip(positions[::2], positions[1::2]):
                    self.assertEqual(left.y(), right.y())
                self.assertTrue(all(position.x() + box.width() <= editor.width()
                                    for position, box in zip(positions, boxes)))

    def test_default_interface_font_is_twelve_and_eleven_remains_available(self):
        """默认主界面字号为 12 像素，用户已选 11 像素仍保留。"""
        self.assertEqual(DEFAULTS["ui_font_size"], 12)
        with tempfile.TemporaryDirectory(prefix="ui-font-", dir=str(WORK)) as folder:
            path = Path(folder) / "设置.json"
            self.assertEqual(load_settings(path)[0]["ui_font_size"], 12)
            atomic_json(path, {"ui_font_size": 11})
            settings, warning = load_settings(path)
            self.assertEqual(settings["ui_font_size"], 11)
            self.assertEqual(warning, "")


if __name__ == "__main__":
    unittest.main()
