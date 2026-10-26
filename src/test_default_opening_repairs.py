"""旧开局精确指纹校订回归：fixture=合成样本，backup=原字节备份。

不读取真实用户目录或工作区外部原稿，测试可随源码独立运行。
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import catalog
from core import atomic_json, read_order
import default_openings as defaults


REAL_REPAIRS = defaults.CONTROLLED_OPENING_REPAIRS.copy()


def opening(name, guide="synthetic-keshik"):
    """合成独立小样本，不把玩家的私人开局作为测试素材打包。"""
    return {
        "name": name, "civilization": "Mongols", "author": "测试作者",
        "source": f"https://aoe4guides.com/builds/{guide}",
        "build_order": [{"population_count": -1, "villager_count": 6, "age": 1,
                         "time": "0:00",
                         "resources": {"food": 5, "wood": 0, "gold": 0, "stone": 0, "builder": 1},
                         "notes": ["测试用旧提醒。"]}],
    }


class DefaultOpeningRepairTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / "个人资料"
        self.orders = self.data / "开局库"
        self.orders.mkdir(parents=True)
        self.pack = self.root / "默认资料"
        self.pack.mkdir()
        self.catalog = self.root / "离线文明资料"
        (self.catalog / "修正开局").mkdir(parents=True)
        self.old = opening("合成旧怯薛稿")
        # 保留 BOM（字节顺序标记）与缩进，验证备份字节原样，且匹配不做格式归一化。
        self.original_bytes = b"\xef\xbb\xbf" + json.dumps(self.old, ensure_ascii=False, indent=4).encode("utf-8")
        self.repaired = copy.deepcopy(self.old)
        self.repaired["build_order"][0]["notes"] = ["已校订的实际操作。"]
        atomic_json(self.catalog / "修正开局/校订样本.json", self.repaired)
        atomic_json(self.pack / "蒙古.json", opening("新默认长矛路线", "synthetic-default"))
        controlled = {hashlib.sha256(self.original_bytes).hexdigest(): "校订样本.json"}
        repair_patch = patch.object(defaults, "CONTROLLED_OPENING_REPAIRS", controlled)
        repair_patch.start()
        self.addCleanup(repair_patch.stop)
        catalog_patch = patch.object(catalog, "CATALOG_ROOT", self.catalog)
        catalog_patch.start()
        self.addCleanup(catalog_patch.stop)
        # 模拟已经升级过、已有默认处理记录的老用户，校订不能被该记录挡住。
        atomic_json(self.data / defaults.RECORD_NAME, {
            "schema": 1, "handled": [defaults.DEFAULT_PREFIX + "Mongols"],
            "legacy_examples_migrated": True,
        })

    def seed(self):
        return defaults.seed_default_openings(self.data, directory=self.pack)

    def write_original(self, name="玩家自行改的文件名.json"):
        target = self.orders / name
        target.write_bytes(self.original_bytes)
        return target

    def test_exact_bytes_repaired_in_place_with_original_backup_and_selection(self):
        target = self.write_original()
        settings = self.data / "设置.json"
        atomic_json(settings, {"last_order": str(target)})
        settings_before = settings.read_bytes()
        self.assertEqual(self.seed(), [])
        self.assertEqual(read_order(target), self.repaired)
        self.assertEqual((self.data / "开局修正前备份" / target.name).read_bytes(), self.original_bytes)
        self.assertEqual(settings.read_bytes(), settings_before)
        self.assertEqual(list(self.orders.glob("*.json")), [target])

    def test_second_start_does_not_repeat_repair_or_backup(self):
        target = self.write_original()
        self.seed()
        repaired_bytes = target.read_bytes()
        backup = self.data / "开局修正前备份"
        self.seed()
        self.assertEqual(target.read_bytes(), repaired_bytes)
        self.assertEqual([path.name for path in backup.glob("*.json")], [target.name])
        self.assertEqual((backup / target.name).read_bytes(), self.original_bytes)

    def test_player_changes_and_format_only_changes_are_preserved(self):
        edited = copy.deepcopy(self.old)
        edited["build_order"][0]["notes"] = ["玩家自己的操作。"]
        edited_target = self.orders / "玩家改内容.json"
        atomic_json(edited_target, edited)
        format_target = self.orders / "玩家只改缩进.json"
        # 多一个换行，解析结果不变，仍然不允许覆盖。
        format_target.write_bytes(self.original_bytes + b"\n")
        before = {path: path.read_bytes() for path in (edited_target, format_target)}
        self.seed()
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertFalse((self.data / "开局修正前备份").exists())

    def test_deleted_original_is_not_restored(self):
        target = self.write_original()
        target.unlink()
        self.seed()
        self.assertFalse(target.exists())
        self.assertEqual(list(self.orders.glob("*.json")), [])
        self.assertFalse((self.data / "开局修正前备份").exists())

    def test_damaged_default_record_does_not_block_controlled_repair(self):
        target = self.write_original()
        record = self.data / defaults.RECORD_NAME
        record.write_text("损坏的默认处理记录", encoding="utf-8")
        self.assertEqual(self.seed(), [])
        self.assertEqual(read_order(target), self.repaired)
        self.assertEqual(record.read_text(encoding="utf-8"), "损坏的默认处理记录")
        self.assertEqual((self.data / "开局修正前备份" / target.name).read_bytes(), self.original_bytes)

    def test_all_23_defaults_including_new_mongol_route_remain_unchanged(self):
        entries = defaults.validate_default_bundle()
        self.assertEqual(len(entries), 23)
        fresh_data = self.root / "全新用户"
        fresh_orders = fresh_data / "开局库"
        fresh_orders.mkdir(parents=True)
        old_target = fresh_orders / "旧怯薛.json"
        old_target.write_bytes(self.original_bytes)
        created = defaults.seed_default_openings(fresh_data)
        self.assertEqual(len(created), 23)
        for _, expected in entries:
            target = fresh_orders / f"默认-{defaults.CIVS[expected['civilization']]}.json"
            self.assertEqual(read_order(target), expected)
        self.assertEqual(read_order(old_target), self.repaired)
        self.assertEqual(len(list(fresh_orders.glob("*.json"))), 24)
        self.assertEqual(defaults.seed_default_openings(fresh_data), [])


class BundledRepairTests(unittest.TestCase):
    def test_real_repair_is_valid_and_bound_to_confirmed_original_fingerprint(self):
        self.assertEqual(set(REAL_REPAIRS), {
            "597892a4a304204142222af479f8a232925236c6a998c8d3e4816bda446d7df9",
        })
        for fingerprint, filename in REAL_REPAIRS.items():
            data = read_order(catalog.CATALOG_ROOT / "修正开局" / filename)
            self.assertEqual(data["_zh_revision"]["original_sha256"], fingerprint)
            self.assertEqual(data["civilization"], "Mongols")
            self.assertEqual(data["_zh_reference"]["id"], "Ku7MObO7OKAJnxRs2EiX")
            self.assertIn("Richarddurodo", data["author"])
            for step in data["build_order"]:
                if step["villager_count"] >= 0:
                    self.assertEqual(sum(step["resources"].values()), step["villager_count"])
                for action in step.get("_zh_actions", []):
                    self.assertIn("mo", action["civs"])
                    self.assertLessEqual(action["age"], step["age"])
                self.assertNotIn("原稿", "\n".join(step["notes"]))
            # 牧场从4:55开始、单人20秒；5:00不能提前把施工者计入采食物。
            at_five = next(step for step in data["build_order"] if step["time"] == "5:00")
            self.assertEqual(at_five["resources"], {"food": 11, "wood": 4, "gold": 2, "stone": 0, "builder": 1})


if __name__ == "__main__":
    unittest.main()
