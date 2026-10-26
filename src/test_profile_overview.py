"""官方总览与近期样本口径测试；所有玩家、对局均为合成资料。"""
import copy
import unittest

from match_analysis import profile_overview, analyze_games
from test_match_analysis import game


class ProfileOverviewTests(unittest.TestCase):
    def test_official_season_counts_are_independent_of_recent_sample(self):
        profile = {"modes": {"rm_team": {"season": 14, "games_count": 18, "wins_count": 6,
                                          "losses_count": 12, "win_rate": 33.3, "rating": 580},
                             "rm_4v4_elo": {"games_count": 18, "wins_count": 6, "losses_count": 12,
                                            "win_rate": 33.3, "rating": 774},
                             "qm_4v4": {"games_count": 34, "wins_count": 14, "losses_count": 20,
                                        "win_rate": 41.2, "rating": 860}}}
        original = copy.deepcopy(profile)
        overview = {row["mode"]: row for row in profile_overview(profile)}
        recent = analyze_games([game(101, "win", "rm_4v4"), game(102, "loss", "rm_4v4")], 7)
        self.assertEqual(recent["by_mode"]["rm_4v4"]["win_rate"], 50)
        self.assertEqual(overview["rm_team"]["win_rate"], 33.3)
        self.assertEqual(overview["rm_team"]["scope"], "第14赛季")
        self.assertEqual(overview["qm_4v4"]["games"], 34)
        self.assertEqual(overview["qm_4v4"]["scope"], "官方模式累计")
        self.assertEqual(overview["rm_4v4_elo"]["rating"], 774)
        self.assertEqual(overview["rm_4v4_elo"]["rating_label"], "匹配分")
        self.assertEqual(profile, original)

    def test_missing_disputed_and_invalid_fields_never_become_zero_losses(self):
        result = {row["mode"]: row for row in profile_overview({"modes": {
            "rm_team": {"games_count": 3, "wins_count": 1, "losses_count": 1, "win_rate": 150},
            "qm_4v4": {"games_count": True, "wins_count": None, "losses_count": -2, "rating": float("nan")},
            "rm_solo": {"games_count": 2, "wins_count": 1, "losses_count": 1},
        }})}
        self.assertIsNone(result["rm_team"]["win_rate"])
        self.assertIsNone(result["qm_4v4"]["games"])
        self.assertIsNone(result["qm_4v4"]["losses"])
        self.assertIsNone(result["qm_4v4"]["rating"])
        self.assertEqual(result["rm_solo"]["win_rate"], 50)
        self.assertEqual(profile_overview(None), [])
        self.assertEqual(profile_overview({"modes": []}), [])


if __name__ == "__main__":
    unittest.main()
