"""同名消歧保留原账号与局内编号；不把名字当数据关联键。"""
import copy
import unittest
from player_labels import player_display_labels, player_labels
from match_analysis import normalize_games
import test_match_analysis as match_fixture


class PlayerLabelTests(unittest.TestCase):
    def test_duplicate_names_have_distinct_accounts_and_input_is_unchanged(self):
        players = [{"id": 1000, "profile_id": 7, "name": "同名玩家"},
                   {"id": 1001, "profile_id": 8, "name": "同名玩家"},
                   {"id": 1002, "profile_id": 9, "name": "其他玩家"}]
        before = copy.deepcopy(players)
        self.assertEqual(player_labels(players), {1000: "同名玩家 · 账号7", 1001: "同名玩家 · 账号8", 1002: "其他玩家"})
        self.assertEqual(players, before)

    def test_missing_accounts_use_local_ids_or_explicit_seats_without_inventing_keys(self):
        players = [{"id": 1000, "name": "同名"}, {"name": "同名"}, {"id": "x", "name": "同名"}]
        self.assertEqual(player_display_labels(players), ["同名 · 局内1000", "同名 · 席位2", "同名 · 局内x"])
        self.assertEqual(set(player_labels(players)), {1000, "x"})

    def test_visually_equivalent_whitespace_case_and_unicode_names_are_qualified(self):
        players = [{"profile_id": 7, "name": " AOE "}, {"profile_id": 8, "name": "aoe"},
                   {"profile_id": 9, "name": "e\u0301"}, {"profile_id": 10, "name": "é"}]
        labels = player_labels(players, "profile_id")
        for key in (7, 8, 9, 10):
            self.assertIn("账号" + str(key), labels[key])

    def test_same_name_opponents_are_identified_without_altering_result_or_strength(self):
        source = match_fixture.game()
        source["teams"][0][0]["player"]["name"] = "同名"
        source["teams"][0][1]["player"]["name"] = "同名"
        source["teams"][1][1]["player"]["name"] = "同名"
        original = copy.deepcopy(source)
        rows, skipped = normalize_games([source], 7)
        self.assertEqual(skipped, {})
        self.assertEqual(rows[0]["opponents"], ["同名 · 账号8", "同名 · 账号9"])
        self.assertEqual(rows[0]["result"], "win")
        self.assertEqual(rows[0]["strength_delta"], -200)
        self.assertEqual(source, original)
