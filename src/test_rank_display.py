"""历史排位展示回归：赛前/赛后、晋降级、定位缺失与匹配分隔离。"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QHeaderView, QTableWidget, QTableWidgetItem

from match_widgets import MATCH_METADATA_ROLE, MATCH_ROW_HEIGHT, style_recent_matches
from rank_display import LOSS, MUTED, WIN, RANK_THRESHOLDS, badge_path, rank_for_points, ranked_result


def ranked(**values):
    row = {"mode": "rm_4v4", "leaderboard": "rm_team", "season": 14,
           "rating": 592, "rating_diff": -12, "result": "loss"}
    row.update(values)
    return row


class RankResultTests(unittest.TestCase):
    def test_api_rating_is_before_match_and_diff_produces_after_match(self):
        first = ranked_result(ranked())
        previous = ranked_result(ranked(rating=605, rating_diff=-13))
        self.assertEqual((first["points"], first["change"], first["tier"]), (580, -12, "silver_1"))
        self.assertEqual(previous["points"], 592)
        self.assertIn("赛前 592 → 赛后 580", first["tooltip"])
        self.assertEqual(first["color"], LOSS)

    def test_promotion_and_demotion_use_after_score_not_before(self):
        promotion = ranked_result(ranked(rating=596, rating_diff=20))
        demotion = ranked_result(ranked(rating=605, rating_diff=-13))
        self.assertEqual((promotion["tier"], promotion["change_text"], promotion["color"]), ("silver_2", "+20", WIN))
        self.assertEqual(demotion["tier"], "silver_1")

    def test_every_verified_threshold_and_neighbor(self):
        for index, (points, tier, _) in enumerate(RANK_THRESHOLDS):
            with self.subTest(points=points):
                self.assertEqual(rank_for_points(points)[0], tier)
                if index:
                    self.assertEqual(rank_for_points(points - 1)[0], RANK_THRESHOLDS[index - 1][1])
        self.assertEqual(rank_for_points(9999)[0], "conqueror_3")

    def test_quick_match_never_becomes_ranked_from_mmr_or_rating(self):
        result = ranked_result(ranked(mode="qm_4v4", leaderboard="qm_4v4", mmr=1800, mmr_diff=20))
        self.assertFalse(result["ranked"])
        self.assertIsNone(result["points"])
        self.assertIsNone(result["icon_name"])
        self.assertEqual(result["text"], "—")

    def test_missing_placement_or_delayed_diff_does_not_fabricate_rank(self):
        for patch in ({"rating": None, "rating_diff": None, "mmr": 1000, "mmr_diff": 24},
                      {"rating_diff": None}, {"rating": None, "rating_diff": 20},
                      {"rating": True}, {"rating_diff": float("nan")}, {"rating_diff": 0.5}):
            with self.subTest(patch=patch):
                result = ranked_result(ranked(**patch))
                self.assertIsNone(result["points"])
                self.assertEqual(result["label"], "—")
                self.assertIsNone(result["icon_name"])

    def test_zero_change_is_explicit_and_neutral(self):
        result = ranked_result(ranked(rating=600, rating_diff=0))
        self.assertEqual((result["points"], result["change_text"], result["color"]), (600, "+0", MUTED))

    def test_solo_team_console_badges_and_early_season(self):
        self.assertEqual(ranked_result(ranked())["icon_name"], "team_silver_1.png")
        self.assertEqual(ranked_result(ranked(mode="rm_1v1_console", leaderboard="rm_solo_console"))["icon_name"], "solo_silver_1.png")
        early = ranked_result(ranked(season=1))
        self.assertEqual(early["points"], 580)
        self.assertIsNone(early["icon_name"])
        confirmed = ranked_result(ranked(season=1, rank_level_after="gold_1"))
        self.assertEqual(confirmed["tier"], "gold_1")

    def test_all_badges_are_local_and_path_is_not_user_controlled(self):
        for _, tier, _ in RANK_THRESHOLDS:
            for family in ("solo", "team"):
                self.assertIsNotNone(badge_path(f"{family}_{tier}.png"))
        self.assertIsNone(badge_path("../../other.png"))


class RankRowDrawingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_seven_columns_have_rank_then_duration_then_result_and_fit_narrow_window(self):
        table = QTableWidget(1, 7)
        table.setHorizontalHeaderLabels(["时间", "文明 / 地图", "对手", "赛后段位", "时长", "结果", "复盘"])
        values = ["10-25 12:44\n排位 4 对 4", "山丘之王\n英格兰", "对手甲、对手乙\n对手队伍均分高 35", "白银 I\n580 分 · -12", "18:59", "失利", ""]
        for column, value in enumerate(values):
            table.setItem(0, column, QTableWidgetItem(value))
        table.verticalHeader().hide()
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        row = ranked(civilization="english")
        style_recent_matches(table, [row])
        table.resize(720, table.height())
        table.show()
        self.application.processEvents()
        self.assertEqual(table.item(0, 0).data(MATCH_METADATA_ROLE)["rating"], 592)
        self.assertIn("580", table.item(0, 3).toolTip())
        self.assertEqual(table.rowHeight(0), MATCH_ROW_HEIGHT)
        self.assertGreaterEqual(table.columnWidth(2), 100)
        self.assertLessEqual(table.horizontalHeader().length(), table.viewport().width())
        self.assertFalse(table.grab().isNull())
        table.close()
        table.deleteLater()

    def test_existing_six_column_tables_remain_supported(self):
        table = QTableWidget(1, 6)
        for column, value in enumerate(["时间", "地图\n文明", "对手", "胜利", "18:59", "打开"]):
            table.setItem(0, column, QTableWidgetItem(value))
        style_recent_matches(table, [ranked(result="win")])
        self.assertEqual(table.rowHeight(0), MATCH_ROW_HEIGHT)
        self.assertEqual(table.item(0, 3).text(), "胜利")
        table.close()


if __name__ == "__main__":
    unittest.main()
