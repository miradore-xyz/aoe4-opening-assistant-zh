"""地图显示验证：官方用语、接口名称差异和未知地图保留原文。"""
import copy
import unittest

from map_names import MAP_NAMES, map_name
from match_analysis import analyze_games, map_name as public_map_name, normalize_games
from test_match_analysis import game


class OfficialMapNameTests(unittest.TestCase):
    def test_screenshot_maps_use_official_simplified_chinese(self):
        # 对照 16.3.11308.0 游戏 UCS（本地化文本）的相同编号，非社区直译。
        expected = {
            "Wetlands": "湿地", "Turtle Ridge": "海龟山脊",
            "Flankwoods": "弗兰克伍兹森林", "Wasteland": "荒地",
            "Waterholes": "水洼", "Hidden Valley": "隐秘山谷",
            "Forts": "堡垒", "French Pass": "法兰西隘口",
            "King of the Hill": "占山为王",
        }
        for english, chinese in expected.items():
            with self.subTest(map=english):
                self.assertEqual(map_name(english), chinese)

    def test_previous_handwritten_names_are_corrected(self):
        for english, chinese in (("Dry Arabia", "干燥阿拉伯"), ("High View", "高视野区"),
                                 ("Ancient Spires", "古代尖山"), ("Boulder Bay", "巨石湾"),
                                 ("Mountain Pass", "隘口"), ("Himeyama", "姫山")):
            with self.subTest(map=english):
                self.assertEqual(map_name(english), chinese)

    def test_interface_case_and_separator_variants_keep_exact_map_identity(self):
        for name in ("King of Hill", "King of The Hill", "king_of_the_hill", "KING-OF-HILL"):
            self.assertEqual(map_name(name), "占山为王")
        self.assertEqual(map_name("dry_arabia"), "干燥阿拉伯")
        self.assertEqual(map_name("Cliff Side"), "悬崖边")
        self.assertEqual(map_name("Westlake"), "西湖")
        # 独立的游戏原名不会仅因旧文件名相近而被错误合并。
        self.assertEqual(map_name("Ponds"), "池塘")
        self.assertEqual(map_name("Wetlands"), "湿地")

    def test_unknown_and_custom_maps_keep_original_without_suffix(self):
        for name in ("Future Official Map", "EGC Custom Arabia", "玩家自制湿地", "wetlands-v2"):
            with self.subTest(map=name):
                self.assertEqual(map_name(name), name)
                self.assertNotIn("地图原名", map_name(name))
        self.assertEqual(map_name("湿地"), "湿地")
        for missing in (None, "", "   "):
            self.assertEqual(map_name(missing), "未提供地图")

    def test_public_history_and_grouping_preserve_raw_map_fields(self):
        self.assertIs(public_map_name, map_name)
        original = game()
        original["map"] = "Wetlands"
        untouched = copy.deepcopy(original)
        rows, _ = normalize_games([original], 7)
        grouped = analyze_games([original], 7)["by_mode"]["rm_2v2"]["maps"]
        self.assertEqual(rows[0]["map"], "Wetlands")
        self.assertEqual(map_name(rows[0]["map"]), "湿地")
        self.assertEqual(map_name(grouped[0]["key"]), "湿地")
        self.assertEqual(original, untouched)

    def test_published_name_snapshot_has_no_explanatory_suffix_or_test_pseudolocale(self):
        self.assertGreaterEqual(len(MAP_NAMES), 74)
        for english, chinese in MAP_NAMES.items():
            with self.subTest(map=english):
                self.assertNotIn("地图原名", chinese)
                self.assertFalse(english.startswith("TEST MAP"))
                self.assertNotIn("CN直角", chinese)
                self.assertEqual(map_name(english), chinese)


if __name__ == "__main__":
    unittest.main()
