"""默认开局安装与升级验证；仅使用临时数据目录，不接触玩家资料。"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import atomic_json, read_order
import default_openings as defaults
from default_openings import seed_default_openings


def opening(civilization="Mongols", identifier="guide-mongols"):
    """构造兼容原工具的短开局，模拟保留原稿编号的默认包。"""
    return {
        "name": "中文默认开局", "civilization": civilization,
        "author": "原作者；中文整理", "source": f"https://aoe4guides.com/builds/{identifier}",
        "_zh_default_id": defaults.DEFAULT_PREFIX + civilization,
        "_zh_reference": {"id": identifier, "url": f"https://aoe4guides.com/builds/{identifier}"},
        "build_order": [{"population_count": -1, "villager_count": 6, "age": 1,
                         "time": "0:00", "resources": {"food": 6, "wood": -1, "gold": -1, "stone": -1},
                         "notes": ["持续训练村民，开始采集食物。"]}],
    }


class DefaultOpeningTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.pack = self.root / "默认资料"
        self.data = self.root / "个人资料"
        self.orders = self.data / "开局库"
        self.pack.mkdir()
        self.orders.mkdir(parents=True)
        atomic_json(self.pack / "蒙古.json", opening())
        atomic_json(self.pack / "英格兰.json", opening("English", "guide-english"))

    def tearDown(self):
        self.temporary.cleanup()

    def seed(self):
        return seed_default_openings(self.data, directory=self.pack)

    def test_new_install_once_and_deletion_stays_deleted(self):
        self.assertEqual(len(self.seed()), 2)
        target = self.orders / "默认-蒙古.json"
        target.unlink()
        self.assertEqual(self.seed(), [])
        self.assertFalse(target.exists())
        self.assertEqual(len(list(self.orders.glob("*.json"))), 1)

    def test_upgrade_existing_library_does_not_replace_namesake(self):
        user = opening()
        user.pop("_zh_default_id")
        user.pop("_zh_reference")
        user.update(name="自己的蒙古开局", source="自己编写")
        target = self.orders / "默认-蒙古.json"
        atomic_json(target, user)
        (self.data / ".已初始化").write_text("旧版本已初始化", encoding="utf-8")
        self.assertEqual(len(self.seed()), 2)
        self.assertEqual(read_order(target), user)
        self.assertTrue((self.orders / "默认-蒙古-2.json").exists())

    def test_existing_import_of_same_guide_is_not_duplicated(self):
        imported = opening()
        imported.pop("_zh_default_id")
        imported.update(name="此前自己导入并改名", author="玩家改过署名")
        atomic_json(self.orders / "自选.json", imported)
        self.assertEqual(len(self.seed()), 1)
        self.assertEqual(len(list(self.orders.glob("*.json"))), 2)
        self.assertEqual(read_order(self.orders / "自选.json"), imported)
        (self.orders / "自选.json").unlink()
        self.assertEqual(self.seed(), [])

    def test_future_civilization_adds_once_without_overwriting_edits(self):
        self.seed()
        path = self.orders / "默认-蒙古.json"
        edited = read_order(path)
        edited["build_order"][0]["notes"] = ["玩家自己的提醒。"]
        atomic_json(path, edited)
        atomic_json(self.pack / "中国.json", opening("Chinese", "guide-chinese"))
        self.assertEqual(len(self.seed()), 1)
        self.assertEqual(read_order(path), edited)
        self.assertEqual(self.seed(), [])

    def test_only_untouched_controlled_examples_are_backed_up(self):
        factory = opening()
        factory.update(name="旧出厂样例", source="受控测试样例")
        changed = copy.deepcopy(factory)
        changed["build_order"][0]["notes"] = ["用户已经编辑，必须保留。"]
        atomic_json(self.orders / "旧样例.json", factory)
        atomic_json(self.orders / "已编辑旧样例.json", changed)
        with patch.object(defaults, "LEGACY_EXAMPLE_HASHES", {defaults._fingerprint(factory)}):
            self.seed()
        self.assertFalse((self.orders / "旧样例.json").exists())
        self.assertEqual(read_order(self.data / "旧示例备份/旧样例.json"), factory)
        self.assertEqual(read_order(self.orders / "已编辑旧样例.json"), changed)

    def test_bad_record_is_preserved_without_reviving_removed_defaults(self):
        record = self.data / defaults.RECORD_NAME
        record.write_text("这是损坏的旧记录", encoding="utf-8")
        self.assertEqual(self.seed(), [])
        self.assertEqual(record.read_text(encoding="utf-8"), "这是损坏的旧记录")
        self.assertEqual(list(self.orders.glob("*.json")), [])

    def test_bundle_requires_one_specific_civilization_per_file(self):
        data = opening()
        data["civilization"] = "any"
        atomic_json(self.pack / "通用.json", data)
        with self.assertRaisesRegex(ValueError, "具体文明"):
            defaults.load_default_openings(self.pack)

    def test_frozen_program_loads_pack_from_internal_assets(self):
        with patch.object(defaults.sys, "frozen", True, create=True), patch.object(defaults.sys, "_MEIPASS", str(self.root), create=True):
            self.assertEqual(defaults.bundle_directory(), self.root / "assets/default_openings")

    def test_release_includes_default_bundle_and_covers_all_civilizations(self):
        entries = defaults.validate_default_bundle()
        self.assertEqual(len(entries), 23)
        import release_build
        with patch.object(release_build, "run") as command:
            release_build.freeze_program(self.root)
        self.assertTrue(any("默认开局;assets/default_openings" in value for value in command.call_args.args[0]))


if __name__ == "__main__":
    unittest.main()
