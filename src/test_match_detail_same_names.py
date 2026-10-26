"""同名账号详细复盘：编号关联、双方选择、图例和事件归属保持独立。"""
import copy
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import QApplication

from match_detail import MatchDetailError, compare_players, normalize_summary
from match_detail_view import MatchDetailView
from test_match_detail import CATALOG, summary


def same_name_report():
    """隔离样例：同名两账号的队伍、速率、出现数和损失数均不同。"""
    raw = summary()
    for player in raw["players"]:
        if player["profile_id"] in (10, 20):
            player["name"] = "同名玩家"
        if player["profile_id"] == 20:
            player["resources"]["food_per_min"] = [300] * 5
            player["build_order"][0]["finished"] = [0] * 6 + [20] * 2 + [40] * 3
            player["build_order"][0]["destroyed"] = [70]
        elif player["profile_id"] == 10:
            player["build_order"][0]["destroyed"] = [60, 60]
    return raw, normalize_summary(raw, 10, 101, CATALOG)


class SameNameDetailDataTests(unittest.TestCase):
    def test_same_names_never_merge_series_events_or_comparison_identity(self):
        raw, report = same_name_report()
        players = {player["profile_id"]: player for player in report["players"]}
        self.assertEqual(set(players), {10, 20, 30})
        self.assertEqual(players[10]["name"], players[20]["name"])
        self.assertEqual(players[10]["series"]["income_total"]["points"][0], [0, 100])
        self.assertEqual(players[20]["series"]["income_total"]["points"][0], [0, 300])
        for identity, quantity, time in ((10, 2, 60), (20, 1, 70)):
            losses = [event for event in players[identity]["events"]
                      if event["kind"] == "loss" and event["is_villager"]]
            self.assertEqual([(event["profile_id"], event["time"], event["quantity"])
                              for event in losses], [(identity, time, quantity)])
        self.assertEqual([row["profile_id"] for row in report["comparisons"]], [20])
        self.assertEqual(compare_players(players[10], players[20])["income_gap"][0], [0, 200])
        self.assertEqual(compare_players(players[20], players[10])["income_gap"][0], [0, -200])
        self.assertEqual(raw["players"][0]["name"], "同名玩家", "显示消歧不改写原始昵称")

    def test_missing_profile_is_not_replaced_by_same_name_or_local_id(self):
        raw, _ = same_name_report()
        player = raw["players"][0]
        player["id"] = 20  # 局内编号不能当作公开账号编号。
        player.pop("profile_id")
        with self.assertRaisesRegex(MatchDetailError, "编号缺失或重复"):
            normalize_summary(raw, 10, 101, CATALOG)


class SameNameDetailViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        for font in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + font)

    def setUp(self):
        _, self.detail = same_name_report()
        self.original = copy.deepcopy(self.detail)
        self.view = MatchDetailView(self.detail)
        self.view.resize(760, 760)
        self.view.show()
        self.application.processEvents()

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()

    def test_selectors_legends_hover_and_bars_distinguish_same_accounts(self):
        view = self.view
        self.assertEqual(view.player_combo.currentText(), "同名玩家 · 账号10")
        self.assertEqual(view.opponent_combo.currentText(), "同名玩家 · 账号20 · 对手")
        unique_index = view.player_combo.findData(30)
        self.assertEqual(view.player_combo.itemText(unique_index), "玩家30")
        view.metric_combo.setCurrentIndex(view.metric_combo.findData("income_total"))
        samples = view.chart.samples_at(60)
        self.assertEqual([(row["profile_id"], row["label"], row["value"]) for row in samples],
                         [(10, "同名玩家 · 账号10", 100), (20, "同名玩家 · 账号20", 300)])
        self.assertEqual([(line["profile_id"], line["label"], line["points"][0][1])
                          for line in view.event_chart.lines],
                         [(10, "同名玩家 · 账号10", 2), (20, "同名玩家 · 账号20", 5)])
        fact = view.fact_labels["事实"].text()
        self.assertIn("同名玩家 · 账号10", fact)
        self.assertIn("同名玩家 · 账号20", fact)
        self.assertEqual(self.detail, self.original)

    def test_switch_same_name_primary_reverses_exact_account_and_loss_scenario(self):
        view = self.view
        self.assertEqual(sum(row["quantity"] for row in view.scenario["losses"]), 2)
        view.player_combo.setCurrentIndex(view.player_combo.findData(20))
        self.assertEqual(view.primary_player()["profile_id"], 20)
        self.assertEqual(view.other_player()["profile_id"], 10)
        self.assertEqual(view.comparison["profile_id"], 10)
        self.assertEqual(view.comparison["income_gap"][0], [0, -200])
        self.assertEqual(sum(row["quantity"] for row in view.scenario["losses"]), 1)
        self.assertEqual(self.detail, self.original)

    def test_event_table_and_scopes_keep_same_name_losses_separate(self):
        view = self.view
        view.event_filter.setCurrentIndex(view.event_filter.findData("loss"))
        # 绵羊及建筑损失也属于“损失”；以村民说明过滤核对账号归属。
        view.event_search.setText("村民")
        rows = [(view.timeline_table.item(row, 0).text(), view.timeline_table.item(row, 1).text(),
                 view.timeline_table.item(row, 2).text()) for row in range(view.timeline_table.rowCount())]
        self.assertEqual(len(rows), 2)
        self.assertEqual([(row[0], row[1]) for row in rows],
                         [("01:00", "同名玩家 · 账号10"), ("01:10", "同名玩家 · 账号20")])
        for row in range(view.timeline_table.rowCount()):
            item = view.timeline_table.item(row, 1)
            self.assertGreaterEqual(view.timeline_table.visualItemRect(item).width(),
                                    view.timeline_table.fontMetrics().horizontalAdvance(item.text()) + 12,
                                    "短同名标签应完整可见，不能只在数据和提示中保留账号")
        view.event_scope.setCurrentIndex(view.event_scope.findData("primary"))
        self.assertEqual(view.timeline_table.rowCount(), 1)
        self.assertEqual(view.visible_events[0]["profile_id"], 10)
        view.event_scope.setCurrentIndex(view.event_scope.findData("other"))
        self.assertEqual(view.timeline_table.rowCount(), 1)
        self.assertEqual(view.visible_events[0]["profile_id"], 20)

    def test_report_replacement_rebuilds_labels_and_removes_unneeded_suffixes(self):
        replacement = copy.deepcopy(self.detail)
        for player in replacement["players"]:
            if player["profile_id"] == 20:
                player["name"] = "新的对手"
        self.view.set_detail(replacement)
        self.assertEqual(self.view.player_combo.currentText(), "同名玩家")
        self.assertEqual(self.view.opponent_combo.currentText(), "新的对手 · 对手")
        self.assertEqual([line["label"] for line in self.view.chart.lines], ["同名玩家", "新的对手"])
        self.view.set_detail({})
        self.assertEqual(self.view.player_combo.count(), 0)
        self.assertEqual(self.view.timeline_table.rowCount(), 0)


if __name__ == "__main__":
    unittest.main()
