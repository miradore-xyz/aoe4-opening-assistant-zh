"""赛后图表交互验证：缺点、时刻同步、双方选择和估算假设。"""
import copy
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication

from match_detail_view import DetailChart, MatchDetailView, build_review_windows, line_segments, resource_gap_changes


def detail_fixture():
    """独立虚构样例，不读取玩家个人对局或需要联网的数据。"""
    events = [
        {"time": 0, "profile_id": 11, "kind": "initial", "label": "初始拥有 村民 ×8", "entity_name": "村民", "entity_type": "Unit", "quantity": 8, "is_villager": True},
        {"time": 60, "profile_id": 11, "kind": "loss", "label": "损失 村民 ×2", "entity_name": "村民", "entity_type": "Unit", "quantity": 2, "is_villager": True},
        {"time": 80, "profile_id": 11, "kind": "production", "label": "单位完成 村民", "entity_name": "村民", "entity_type": "Unit", "quantity": 1, "is_villager": True},
    ]
    events.extend({"time": 100 + index, "profile_id": 22, "kind": "production", "label": "单位完成 长矛兵", "entity_name": "长矛兵", "entity_type": "Unit", "quantity": 1, "is_villager": False} for index in range(22))
    players = []
    for profile_id, name, team, values in ((11, "测试甲", 0, [100, 150, 200, 250]), (22, "测试乙", 1, [120, 200, 700, 650])):
        series = {}
        for key, label, unit, kind in (("bank_total", "四资源库存", "资源", "bank"), ("income_total", "四资源采集速率", "资源/分", "rate")):
            series[key] = {"label": label, "unit": unit, "kind": kind, "source": "隔离测试样例", "points": [[index * 60, value] for index, value in enumerate(values)], "confidence": "reported"}
        series["villagers_alive"] = {"label": "村民事件计数", "unit": "人", "kind": "step", "points": [[0, 8], [60, 6], [80, 7]], "confidence": "event_count"}
        series["reported_usage_total"] = {"kind": "unverified", "points": [[0, 99999]]}
        players.append({"profile_id": profile_id, "name": name, "team": team, "civilization": "english", "result": "loss" if profile_id == 11 else "win", "series": series,
                        "totals": {"gathered": {"total": None}, "spent": {"total": None}}, "scores": {},
                        "events": [event for event in events if event["profile_id"] == profile_id]})
    return {"schema_version": 1, "kind": "match-detail", "game_id": 321, "duration": 180, "focus_profile_id": 11,
            "map_name": "Wetlands", "players": players, "events": events, "coverage": {"gaps": ["未提供逐帧位置"]}}


class MatchDetailViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def view(self):
        view = MatchDetailView(detail_fixture())
        view.resize(720, 740)
        view.show()
        self.widgets.append(view)
        self.app.processEvents()
        return view

    def test_missing_samples_break_lines_and_never_become_zero_or_nearest_measurement(self):
        self.assertEqual(line_segments([[0, 2], [20, None], [40, 5]]), [[(0, 2)], [(40, 5)]])
        chart = DetailChart()
        self.widgets.append(chart)
        chart.set_data([{"label": "样本", "points": [[0, 2], [20, None], [40, 5]]}], 60)
        self.assertIsNone(chart.samples_at(10)[0]["value"])
        self.assertIsNone(chart.samples_at(20)[0]["value"])
        self.assertEqual(chart.samples_at(40)[0]["value"], 5)
        chart.set_data([{"label": "事件计数", "kind": "step", "points": [[0, 8], [30, 6], [50, None]]}], 60)
        self.assertEqual(chart.samples_at(40)[0]["value"], 6)
        self.assertIsNone(chart.samples_at(55)[0]["value"])

    def test_hover_click_keyboard_and_selected_time_are_synchronized(self):
        view = self.view()
        chart = view.chart
        spy = QSignalSpy(view.timeSelected)
        rect = chart.plot_rect()
        point = QPoint(int(rect.left() + rect.width() / 3), int(rect.center().y()))
        QTest.mouseMove(chart, point)
        self.app.processEvents()
        self.assertEqual(chart.hover_time, 60)
        self.assertEqual([item["value"] for item in chart.hover_details], [150, 200])
        QTest.mouseClick(chart, Qt.LeftButton, pos=point)
        self.assertEqual(spy[-1][0], 60)
        self.assertEqual(view.event_chart.selected_time, 60)
        self.assertEqual(view.scenario_chart.selected_time, 60)
        QTest.keyClick(chart, Qt.Key_Right)
        self.assertEqual(view.chart.selected_time, 120)
        self.assertIn("02:00", view.selected_label.text())

    def test_player_and_metric_switches_refresh_both_curves_without_mutating_detail(self):
        original = detail_fixture()
        untouched = copy.deepcopy(original)
        view = MatchDetailView(original)
        self.widgets.append(view)
        self.assertEqual(view.primary_player()["profile_id"], 11)
        self.assertEqual(view.other_player()["profile_id"], 22)
        view.player_combo.setCurrentIndex(view.player_combo.findData(22))
        self.assertEqual(view.primary_player()["profile_id"], 22)
        self.assertEqual(view.other_player()["profile_id"], 11)
        self.assertEqual(view.comparison["relation"], "opponent")
        view.metric_combo.setCurrentIndex(view.metric_combo.findData("villagers_alive"))
        self.assertEqual(view.chart.lines[0]["points"][-1], [180, 7])
        self.assertIn("事件", view.chart_note.text())
        self.assertNotIn("reported_usage_total", [view.metric_combo.itemData(i) for i in range(view.metric_combo.count())])
        self.assertEqual(original, untouched)

    def test_timeline_filters_pages_and_jumps_to_chart_time(self):
        view = self.view()
        view.tabs.setCurrentIndex(2)
        self.assertEqual(view.timeline_table.rowCount(), 12)
        self.assertTrue(view.next_button.isEnabled())
        view.next_button.click()
        self.assertEqual(view.page_index, 1)
        view.event_filter.setCurrentIndex(view.event_filter.findData("loss"))
        self.assertEqual(view.page_index, 0)
        self.assertEqual(view.timeline_table.rowCount(), 1)
        self.assertIn("村民", view.timeline_table.item(0, 2).text())
        view._timeline_clicked(0, 0)
        self.assertEqual(view.tabs.currentIndex(), 0)
        self.assertEqual(view.chart.selected_time, 60)
        view.event_search.setText("不存在的科技")
        self.assertEqual(view.timeline_table.rowCount(), 0)
        self.assertFalse(view.next_button.isEnabled())

    def test_scenario_uses_explicit_recovery_not_next_normal_villager(self):
        view = self.view()
        self.assertEqual(view.scenario_rate.value(), 40)
        self.assertIsNone(view.scenario_delay.currentData())
        # 80 秒的正常训练村民不改变 60 秒损失的两名村民未补回假设。
        self.assertEqual(view.scenario["estimated_loss"], 160)
        view.scenario_delay.setCurrentIndex(view.scenario_delay.findData(60))
        self.assertEqual(view.scenario["estimated_loss"], 80)
        view.scenario_delay.setCurrentIndex(view.scenario_delay.findData("custom"))
        view.scenario_seconds.setValue(90)
        self.assertEqual(view.scenario["estimated_loss"], 120)
        view.scenario_rate.setValue(60)
        self.assertEqual(view.scenario["estimated_loss"], 180)
        self.assertIn("情景", view.scenario_summary.text())
        self.assertIn("不是实测", view.scenario_note.text())

    def test_narrow_layout_and_empty_detail_remain_usable(self):
        view = self.view()
        self.assertLessEqual(view.minimumSizeHint().width(), 700)
        self.assertEqual(view.timeline_table.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        view.tabs.setCurrentIndex(1)
        self.app.processEvents()
        self.assertLessEqual(view.scenario_delay.mapTo(view, QPoint(view.scenario_delay.width(), 0)).x(), view.width())
        view.set_detail({})
        self.assertEqual(view.player_combo.count(), 0)
        self.assertEqual(view.timeline_table.rowCount(), 0)
        self.assertEqual(view.chart.lines, [])
        self.assertEqual(view.scenario["estimated_loss"], 0)

    def test_review_windows_select_three_nonoverlapping_periods_and_first_positive_gap(self):
        primary = {"series": {"income_total": {"points": [[t, 100] for t in range(0, 361, 20)]}}}
        gaps = [0, 20, 40, 100, 100, 100, 110, 130, 200, 310, 310, 320, 320, 340, 400, 500, 510, 515, 520]
        other = {"series": {"income_total": {"points": [[index * 20, 100 + gap] for index, gap in enumerate(gaps)]}}}
        windows = build_review_windows(primary, other)
        swings = [window for window in windows if window["kind"] == "swing"]
        self.assertEqual(len(swings), 3)
        self.assertEqual((swings[0]["start"], swings[0]["end"]), (120, 180))
        for index, left in enumerate(swings):
            for right in swings[index + 1:]:
                self.assertTrue(left["end"] <= right["start"] or left["start"] >= right["end"])
        first = [window for window in windows if window["kind"] == "first_gap"]
        self.assertEqual(len(first), 1)
        self.assertEqual((first[0]["start"], first[0]["end"]), (20, 80))
        # 中间的 None（缺失值）不能被差值计算丢弃后误当成连续样本。
        other["series"]["income_total"]["points"][8][1] = None
        self.assertTrue(all(not (window["start"] <= 160 <= window["end"]) for window in build_review_windows(primary, other)))

    def test_review_window_switch_updates_evidence_and_jump_target(self):
        view = self.view()
        self.assertGreaterEqual(view.review_window_combo.count(), 2)
        previous = view.gap_window["start"]
        view.review_window_combo.setCurrentIndex(1)
        self.assertNotEqual(view.gap_window["start"], previous)
        self.assertIn(view.gap_window["start_label"], view.gap_title.text())
        spy = QSignalSpy(view.timeSelected)
        view.gap_jump.click()
        self.assertEqual(spy[-1][0], view.gap_window["start"])
        self.assertEqual(view.chart.selected_time, view.gap_window["start"])
        self.assertEqual(view.tabs.currentIndex(), 0)

    def test_resource_rate_breakdown_preserves_missing_endpoints(self):
        primary, other = detail_fixture()["players"]
        primary["series"]["income_food"] = {"points": [[60, 20], [120, 20]], "kind": "rate"}
        other["series"]["income_food"] = {"points": [[60, 40], [120, 140]], "kind": "rate"}
        primary["series"]["income_gold"] = {"points": [[60, 10], [120, 30]], "kind": "rate"}
        other["series"]["income_gold"] = {"points": [[60, 10], [120, None]], "kind": "rate"}
        primary["series"]["income_stone"] = {"points": [[60, 10], [120, 10]], "kind": "rate"}
        other["series"]["income_stone"] = {"points": [[60, 0], [120, 0]], "kind": "rate"}
        changes = resource_gap_changes(primary, other, {"start": 60, "end": 120})
        self.assertEqual(changes, {"food": 100, "wood": None, "gold": None, "stone": 0})
        view = MatchDetailView({**detail_fixture(), "players": [primary, other]})
        self.widgets.append(view)
        fact = view.fact_labels["事实"].text()
        self.assertIn("食物 +100", fact)
        self.assertIn("木材 未提供", fact)
        self.assertIn("黄金 未提供", fact)
        self.assertIn("石料 0", fact)
        self.assertIn("资源/分", fact)


if __name__ == "__main__":
    unittest.main()
