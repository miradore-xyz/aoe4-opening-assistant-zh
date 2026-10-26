"""玩家标题段位摘要：真实积分/徽章、缺失与零值、窄窗口和玩家切换。"""
import copy
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from profile_rank_summary import ProfileRankSummary, profile_rank_rows


def profile():
    """仅合成玩家资料；字段结构与现有公开玩家缓存一致。"""
    return {"profile_id": 77, "name": "示例玩家", "modes": {
        "rm_solo": {"rating": 912, "rank_level": "gold_3", "season": 14},
        "rm_team": {"rating": 1050, "rank_level": "platinum_1", "season": 14}}}


class ProfileRankDataTests(unittest.TestCase):
    def test_current_explicit_modes_use_rank_level_and_preserve_source(self):
        source = profile()
        original = copy.deepcopy(source)
        rows = profile_rank_rows(source)
        self.assertEqual([(r["name"], r["label"], r["points"]) for r in rows],
                         [("单排", "黄金 III", 912), ("组排", "白金 I", 1050)])
        self.assertEqual([r["icon_name"] for r in rows], ["solo_gold_3.png", "team_platinum_1.png"])
        self.assertIn("第14赛季", rows[0]["tooltip"])
        self.assertEqual(source, original)

    def test_missing_rank_never_comes_from_matching_score_history_or_leaderboard_position(self):
        source = {"modes": {
            "rm_solo": {"mmr": 1900, "rank": 1, "rank_level": "unranked",
                        "previous_seasons": [{"rating": 2000, "rank_level": "conqueror_3"}]},
            "rm_team": {"rating": 580, "mmr": 1700},
            "rm_4v4_elo": {"rating": 2000, "rank_level": "conqueror_3"},
            "qm_1v1": {"rating": 2400, "rank_level": "conqueror_3"}}}
        solo, team = profile_rank_rows(source)
        self.assertEqual((solo["label"], solo["points"], solo["icon_name"]), ("暂无段位", None, None))
        self.assertEqual((team["label"], team["points"], team["icon_name"]), ("暂无段位", 580, None))
        self.assertFalse(solo["available"])

    def test_zero_is_real_while_invalid_scores_do_not_become_zero(self):
        source = {"modes": {"rm_solo": {"rating": 0, "rank_level": "bronze_1", "season": 14}}}
        row = profile_rank_rows(source)[0]
        self.assertEqual((row["points"], row["points_text"], row["label"]), (0, "0 分", "青铜 I"))
        for value in (None, True, -1, 3.5, float("nan"), float("inf"), "580"):
            with self.subTest(value=value):
                self.assertIsNone(profile_rank_rows({"modes": {"rm_solo": {"rating": value}}})[0]["points"])
        for missing in (None, {}, {"modes": []}, {"ranked_ratings": {"rm_solo": 1000}}):
            with self.subTest(source=missing):
                self.assertTrue(all(r["label"] == "暂无段位" and not r["available"] for r in profile_rank_rows(missing)))


class ProfileRankWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()
        self.widgets.clear()

    def component(self, source=None):
        widget = ProfileRankSummary(source)
        self.widgets.append(widget)
        widget.show()
        self.application.processEvents()
        return widget

    def test_actual_local_badges_and_both_scores_are_drawn(self):
        widget = self.component(profile())
        self.assertTrue(widget.details.isVisible())
        self.assertFalse(widget.empty_label.isVisible())
        self.assertEqual([label.text() for label in widget.points_labels], ["912 分", "1050 分"])
        for badge, row in zip(widget.badge_labels, widget.rows):
            self.assertIsNotNone(badge.pixmap())
            self.assertFalse(badge.pixmap().isNull())
            self.assertLessEqual(badge.pixmap().width(), 28)
            self.assertLessEqual(badge.pixmap().height(), 28)
            self.assertIn(row["label"], badge.accessibleName())
        self.assertLessEqual(widget.width(), 244)
        self.assertLessEqual(widget.height(), 62)
        self.assertFalse(widget.grab().isNull())

    def test_switching_player_to_missing_data_clears_old_badges_and_scores(self):
        widget = self.component(profile())
        widget.set_profile({"profile_id": 88, "modes": {"rm_4v4_elo": {"rating": 1900}}})
        self.application.processEvents()
        self.assertTrue(widget.empty_label.isVisible())
        self.assertFalse(widget.details.isVisible())
        self.assertEqual(widget.accessibleName(), "暂无段位")
        self.assertEqual([label.text() for label in widget.points_labels], ["—", "—"])
        self.assertTrue(all(badge.pixmap() is None or badge.pixmap().isNull() for badge in widget.badge_labels))

    def test_zero_points_and_missing_second_mode_stay_explicit(self):
        widget = self.component({"modes": {"rm_solo": {"rating": 0, "rank_level": "bronze_1"}}})
        self.assertEqual(widget.points_labels[0].text(), "0 分")
        self.assertEqual(widget.rank_labels[1].text(), "暂无段位")
        self.assertIsNotNone(widget.badge_labels[0].pixmap())

    def test_existing_overview_rows_keep_real_points_without_inventing_a_badge(self):
        widget = self.component()
        widget.set_overview([
            {"mode": "rm_team", "rating": 580, "rating_label": "排位积分", "season": 14},
            {"mode": "rm_4v4_elo", "rating": 2000, "rating_label": "匹配分"},
            {"mode": "rm_solo", "rating": 2100, "rating_label": "匹配分"}])
        self.assertEqual(widget.points_labels[1].text(), "580 分")
        self.assertEqual(widget.rank_labels[1].text(), "暂无段位")
        self.assertIsNone(widget.rows[0]["points"])
        self.assertIsNone(widget.rows[1]["icon_name"])

    def test_760_pixel_header_keeps_badges_names_and_points_inside_view(self):
        header = QWidget()
        self.widgets.append(header)
        header.setStyleSheet("background:#0f0f11;")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(10)
        avatar = QLabel("头像")
        avatar.setFixedSize(40, 40)
        layout.addWidget(avatar)
        identity = QVBoxLayout()
        identity.addWidget(QLabel("这是明确的玩家昵称"))
        identity.addWidget(QLabel("等级 32 · 玩家编号 77"))
        layout.addLayout(identity, 1)
        summary = ProfileRankSummary(profile(), parent=header)
        layout.addWidget(summary, 0, Qt.AlignRight | Qt.AlignVCenter)
        header.resize(760, 80)
        header.show()
        self.application.processEvents()
        self.assertEqual(header.width(), 760)
        self.assertLessEqual(summary.x() + summary.width(), 744)
        self.assertGreaterEqual(summary.x(), avatar.x() + avatar.width() + 10)
        for label in (*summary.rank_labels, *summary.points_labels):
            self.assertGreaterEqual(label.width(), label.fontMetrics().horizontalAdvance(label.text()))
        self.assertFalse(header.grab().isNull())


if __name__ == "__main__":
    unittest.main(verbosity=2)
