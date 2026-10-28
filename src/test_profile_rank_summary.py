"""玩家标题段位摘要：真实积分/徽章、缺失与零值、窄窗口和玩家切换。"""
import copy
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QHelpEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QLabel, QToolTip, QVBoxLayout, QWidget

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
        self.assertEqual((solo["label"], solo["points"], solo["icon_name"]), ("无段位", None, None))
        self.assertEqual((team["label"], team["points"], team["icon_name"]), ("无段位", 580, None))
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
                self.assertTrue(all(r["label"] == "无段位" and not r["available"] for r in profile_rank_rows(missing)))

    def test_leaderboard_positions_are_independent_positive_source_fields(self):
        source = {'modes':{'rm_solo':{'rank':4501,'rating':1195,'rank_level':'platinum_3'},
                           'rm_team':{'rank':4448,'rating':1401,'rank_level':'conqueror_1'}}}
        before = copy.deepcopy(source)
        solo,team = profile_rank_rows(source)
        self.assertEqual((solo['leaderboard_rank'],team['leaderboard_rank']),(4501,4448))
        self.assertEqual((solo['points'],team['points']),(1195,1401))
        self.assertEqual(source,before)
        for value in (None,0,-1,True,False,3.5,'4501',float('inf'),float('nan')):
            with self.subTest(rank=value):
                row = profile_rank_rows({'modes':{'rm_solo':{'rank':value,'mmr':9999,'rating':2000}}})[0]
                self.assertIsNone(row['leaderboard_rank'])
                self.assertEqual(row['leaderboard_text'],'排名未提供')
                self.assertEqual(row['label'],'无段位','名次和积分都不能反推明确段位')


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

    def component(self, source=None, *, expanded=False):
        widget = ProfileRankSummary(source, expanded=expanded)
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
        self.assertEqual(widget.accessibleName(), "无段位")
        self.assertEqual([label.text() for label in widget.points_labels], ["—", "—"])
        self.assertTrue(all(badge.pixmap() is None or badge.pixmap().isNull() for badge in widget.badge_labels))

    def test_zero_points_and_missing_second_mode_stay_explicit(self):
        widget = self.component({"modes": {"rm_solo": {"rating": 0, "rank_level": "bronze_1"}}})
        self.assertEqual(widget.points_labels[0].text(), "0 分")
        self.assertEqual(widget.rank_labels[1].text(), "无段位")
        self.assertIsNotNone(widget.badge_labels[0].pixmap())

    def test_existing_overview_rows_keep_real_points_without_inventing_a_badge(self):
        widget = self.component()
        widget.set_overview([
            {"mode": "rm_team", "rating": 580, "rating_label": "排位积分", "season": 14},
            {"mode": "rm_4v4_elo", "rating": 2000, "rating_label": "匹配分"},
            {"mode": "rm_solo", "rating": 2100, "rating_label": "匹配分"}])
        self.assertEqual(widget.points_labels[1].text(), "580 分")
        self.assertEqual(widget.rank_labels[1].text(), "无段位")
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

    def test_expanded_two_independent_cards_remain_for_two_one_and_zero_ranks(self):
        widget = self.component(profile(), expanded=True)
        widget.resize(338, 226)
        QTest.qWait(20)
        self.assertEqual(len(widget.rank_cards), 2)
        self.assertTrue(all(card.isVisible() for card in widget.rank_cards))
        self.assertIsNot(widget.rank_cards[0], widget.rank_cards[1])
        self.assertEqual([label.text() for label in widget.mode_labels], ['单排','组排'])
        self.assertNotIn('当前段位', [label.text() for label in widget.findChildren(QLabel)])
        widget.set_profile({'modes':{'rm_solo':{'rank_level':'bronze_1','rating':0}}})
        QTest.qWait(10)
        self.assertEqual([label.text() for label in widget.rank_labels], ['青铜 I','无段位'])
        self.assertEqual([label.text() for label in widget.points_labels], ['0 分','—'])
        self.assertEqual([label.text() for label in widget.leaderboard_labels], ['排名未提供','排名未提供'])
        self.assertTrue(widget.badge_labels[1].pixmap() is None or widget.badge_labels[1].pixmap().isNull())
        self.assertTrue(all(card.isVisible() for card in widget.rank_cards))
        widget.set_profile({})
        QTest.qWait(10)
        self.assertTrue(widget.details.isVisible())
        self.assertTrue(widget.empty_label.isHidden())
        self.assertEqual([label.text() for label in widget.rank_labels], ['无段位','无段位'])
        self.assertTrue(all(card.isVisible() for card in widget.rank_cards))
        self.assertTrue(all(badge.pixmap() is None or badge.pixmap().isNull() for badge in widget.badge_labels))
        widget.set_profile(profile())
        QTest.qWait(10)
        self.assertTrue(all(not badge.isHidden() and badge.pixmap() is not None for badge in widget.badge_labels))

    def test_all_summary_children_use_accessible_source_descriptions_without_hover_popups(self):
        widget = self.component(profile(), expanded=True)
        widget.resize(338, 226)
        QTest.qWait(20)
        with patch.object(QToolTip, 'showText') as popup:
            for child in (widget, *widget.findChildren(QWidget)):
                self.assertEqual(child.toolTip(), '')
                point = child.rect().center()
                self.application.sendEvent(child, QHelpEvent(QEvent.ToolTip, point, child.mapToGlobal(point)))
            QTest.qWait(20)
            popup.assert_not_called()
        self.assertFalse(QToolTip.isVisible())
        for label in (*widget.rank_labels,*widget.points_labels,*widget.badge_labels):
            self.assertIn('公开玩家资料',label.accessibleDescription())
        widget.set_profile({})
        self.assertTrue(all(child.toolTip()=='' for child in (widget,*widget.findChildren(QWidget))))

    def test_overview_two_rank_cards_share_history_top_bottom_and_fixed_sixteen_gap(self):
        from rank_history_chart import RankOverviewRow
        row = RankOverviewRow(profile())
        self.widgets.append(row)
        row.show()
        for width in (760,1100,1500):
            row.resize(width,row.sizeHint().height())
            QTest.qWait(20)
            left,right = row.history_card,row.current_card
            first,last = row.rank_summary.rank_cards
            f = first.mapTo(row,QPoint())
            s = last.mapTo(row,QPoint())
            self.assertEqual(s.y()-f.y()-first.height(),16)
            self.assertEqual(first.width(),last.width())
            self.assertEqual(first.width(),right.width())
            self.assertEqual(f.y(),right.y())
            self.assertEqual(s.y()+last.height(),right.y()+right.height())
            if width>=1100:
                self.assertEqual(left.y(),right.y())
                self.assertEqual(left.height(),right.height())
                self.assertEqual(right.x()-left.x()-left.width(),16)
                self.assertLessEqual(abs(left.width()-2*right.width()),2)
            else:
                self.assertEqual(right.y()-left.y()-left.height(),16)
            for removed in ('stats_column', 'total_card', 'kd_card'):
                self.assertFalse(hasattr(row, removed))


if __name__ == "__main__":
    unittest.main(verbosity=2)
