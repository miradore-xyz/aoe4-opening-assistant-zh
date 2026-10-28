"""APM（每分钟操作数）接入验证：同场来源、缺失值、全员图形及切换对局。"""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QHelpEvent
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication, QToolTip

from apm_chart import APMCard, REFERENCE_LEVELS, apm_players
from match_detail import normalize_summary
from replay_analysis import analyze_replay
from replay_page import ReplayPage
from test_match_detail import player
from test_replay_page import fixture
from visual import STYLE


def summary(game_id=101):
    """合成摘要仅测试字段语义，不充当真实玩家资料。"""
    roster = [
        {"profile_id": 1001, "name": "合成甲", "apm": 64},
        {"profile_id": 1002, "name": "合成乙", "apm": 73},
        {"profile_id": 1003, "name": "未提供数值", "apm": None},
        {"profile_id": 1004, "name": "真实零值", "apm": 0}]
    raw_players = []
    for index, value in enumerate(roster):
        raw = player(value["profile_id"], index % 2)
        raw.update(value)
        raw_players.append(raw)
    return normalize_summary({"game_id": game_id, "duration": 100, "players": raw_players},
                             1001, game_id, entity_catalog={})


class APMChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        QTest.qWait(20)
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def test_requires_matching_game_and_preserves_missing_and_zero(self):
        detail = summary()
        original = copy.deepcopy(detail)
        self.assertEqual(apm_players(detail, 102), [])
        self.assertEqual(apm_players(detail, None), [])
        self.assertEqual(apm_players(detail, True), [])
        self.assertEqual([p["apm"] for p in apm_players(detail, "101")], [64, 73, None, 0])
        self.assertEqual(detail, original)
        for bad in (True, "64", -1, float("nan"), float("inf")):
            detail["players"][0]["apm"] = bad
            self.assertIsNone(apm_players(detail, 101)[0]["apm"])

    def test_four_and_eight_players_have_proportional_bars_and_elided_names(self):
        detail = summary()
        detail["players"] += [{"profile_id": i, "name": "用于窄窗口验证的很长很长的玩家昵称", "apm": 42}
                              for i in range(1005, 1009)]
        for count in (4, 8):
            for width in (500, 1050):
                with self.subTest(count=count, width=width):
                    value = dict(detail, players=detail["players"][:count])
                    card = APMCard(value, 101, 1001)
                    self.widgets.append(card)
                    card.resize(width, card.sizeHint().height())
                    card.show()
                    QTest.qWait(5)
                    chart = card.chart
                    plot, maximum, rows = chart.geometry_rows()
                    self.assertEqual(card.title.text(), "每分钟操作次数")
                    self.assertEqual(len(rows), count)
                    for row in rows:
                        self.assertTrue(chart.rect().contains(row["name_rect"].toAlignedRect()))
                        self.assertEqual(row["bar"].bottom(), plot.bottom())
                        if row["player"]["apm"] is not None:
                            self.assertAlmostEqual(row["bar"].height() / plot.height(), row["player"]["apm"] / maximum)
                    self.assertIsNone(rows[2]["player"]["apm"])
                    self.assertEqual(rows[3]["player"]["apm"], 0)
                    self.assertFalse(card.grab().isNull())
                    if count == 8:
                        self.assertTrue(rows[-1]["visible_name"].endswith("…"))

    def test_reference_ranges_share_linear_zero_based_scale_without_clipping_high_values(self):
        for value, expected_maximum in ((73, 700), (760, 800)):
            detail = summary()
            detail["players"][0]["apm"] = value
            card = APMCard(detail, 101, 1001)
            self.widgets.append(card)
            card.resize(700, card.sizeHint().height())
            card.show()
            QTest.qWait(5)
            plot, maximum, rows = card.chart.geometry_rows()
            self.assertEqual(maximum, expected_maximum)
            self.assertEqual(REFERENCE_LEVELS, (100, 200, 300, 400, 500, 600, 700))
            self.assertEqual(rows[0]["player"]["apm"], value)
            self.assertEqual(rows[0]["bar"].bottom(), plot.bottom())
            self.assertAlmostEqual(rows[0]["bar"].height() / plot.height(), value / maximum)
            self.assertGreaterEqual(rows[0]["bar"].top(), plot.top())
            self.assertFalse(card.grab().isNull())

    def test_only_values_above_700_are_red_including_current_player(self):
        detail = summary()
        for player, value in zip(detail["players"], (700, 701, 699, 1400)):
            player["apm"] = value
        card = APMCard(detail, 101, 1002)
        self.widgets.append(card)
        card.resize(760, card.sizeHint().height())
        card.show()
        QTest.qWait(5)
        image = card.chart.grab().toImage()
        rows = card.chart.geometry_rows()[2]
        for row, expected in zip(rows, ("#62626d", "#ef4d4d", "#62626d", "#ef4d4d")):
            point = row["bar"].center().toPoint()
            self.assertEqual(image.pixelColor(point).name(), expected)
        self.assertIn("异常操作量", card.high_legend.text())
        self.assertTrue(card.high_legend.isVisible())

    def test_left_double_click_names_keeps_exact_ids_for_same_and_elided_names(self):
        """显示文本不是身份：同名、省略名和大整数账号均保留各自唯一编号。"""
        names = ["同名玩家", "同名玩家", "窄窗口中显示不完整的同一个很长很长很长的姓名"]
        detail = {"game_id": 101, "players": [
            {"profile_id": number, "name": names[min(index, 2)], "apm": 64}
            for index, number in enumerate((1001, 4294967301, 1003, 1004))]}
        for width in (420, 1050):
            card = APMCard(detail, 101, 1001)
            self.widgets.append(card)
            card.resize(width, card.sizeHint().height())
            card.show()
            QTest.qWait(5)
            requested = QSignalSpy(card.playerActivated)
            rows = card.chart.geometry_rows()[2]
            if width == 420:
                self.assertTrue(rows[-1]["visible_name"].endswith("…"))
            for row in rows:
                QTest.mouseDClick(card.chart, Qt.LeftButton, pos=row["name_hit_rect"].center().toPoint())
            self.assertEqual(list(requested), [[p["profile_id"], p["name"]] for p in detail["players"]])

    def test_non_name_mouse_actions_and_tooltip_events_never_query(self):
        """姓名旁空白、柱体、右键与单击不触发；悬停不弹框，缺编号项不形成姓名入口。"""
        detail = summary()
        detail["players"].append({"profile_id": None, "name": "缺少编号", "apm": 200})
        card = APMCard(detail, 101, 1001)
        self.widgets.append(card)
        card.resize(900, card.sizeHint().height())
        card.show()
        QTest.qWait(5)
        chart = card.chart
        self.assertEqual(len(chart.players), 4)
        requested = QSignalSpy(card.playerActivated)
        row = chart.geometry_rows()[2][0]
        name = row["name_hit_rect"].center().toPoint()
        QTest.mouseClick(chart, Qt.LeftButton, pos=name)
        QTest.mouseClick(chart, Qt.RightButton, pos=name)
        QTest.mouseDClick(chart, Qt.RightButton, pos=name)
        QTest.mouseDClick(chart, Qt.LeftButton, pos=row["bar"].center().toPoint())
        blank = QPoint(int(row["name_rect"].left()) + 1, name.y())
        self.assertFalse(row["name_hit_rect"].contains(blank))
        QTest.mouseDClick(chart, Qt.LeftButton, pos=blank)
        QTest.mouseDClick(chart, Qt.LeftButton, pos=QPoint(3, 3))
        with patch("apm_chart.QToolTip.showText") as show_tip:
            QApplication.sendEvent(chart, QHelpEvent(QEvent.ToolTip, name, chart.mapToGlobal(name)))
            show_tip.assert_not_called()
        self.assertFalse(QToolTip.isVisible())
        self.assertEqual(len(requested), 0)

    def test_current_report_only_routes_player_requests_and_apm_fills_each_width(self):
        """旧卡片延迟销毁期间与新对局读取中均不得复用上一场的查询信号。"""
        with tempfile.TemporaryDirectory() as directory:
            page = ReplayPage(directory)
            self.widgets.append(page)
            parsed = fixture()
            parsed["source"].update(origin="aoe4world-public-replay", game_id=101, profile_id=1001)
            page._show_report(analyze_replay(parsed), detail=summary())
            page.show()
            for width in (760, 1100, 1500):
                page.resize(width, 650)
                QTest.qWait(10)
                self.assertEqual(page.apm_card.width(), page.scroll.viewport().width())
            requested = QSignalSpy(page.player_query_requested)
            old = page.apm_card
            old.chart.playerActivated.emit(1001, "合成甲")
            self.assertEqual(list(requested), [[1001, "合成甲"]])
            page._preview_public_context(102, {"map": "Dry Arabia"})
            old.playerActivated.emit(1001, "合成甲")
            self.assertEqual(len(requested), 1)
            parsed["source"]["game_id"] = 102
            detail = summary(102)
            detail["players"][0].update(profile_id=2001, name="下一场同名")
            page._show_report(analyze_replay(parsed), detail=detail)
            old.playerActivated.emit(1001, "合成甲")
            self.assertEqual(len(requested), 1)
            current = page.apm_card
            current.chart.playerActivated.emit(2001, "下一场同名")
            self.assertEqual(list(requested)[-1], [2001, "下一场同名"])
            self.assertEqual(len(requested), 2)
            current.playerActivated.emit(1001, "不属于本场")
            self.assertEqual(len(requested), 2)
            page._show_empty()
            current.playerActivated.emit(2001, "下一场同名")
            self.assertEqual(len(requested), 2)

    def test_first_data_item_and_new_context_never_reuses_previous_apm(self):
        with tempfile.TemporaryDirectory() as directory:
            page = ReplayPage(directory)
            self.widgets.append(page)
            parsed = fixture()
            parsed["source"].update(origin="aoe4world-public-replay", game_id=101, profile_id=1001)
            report = analyze_replay(parsed)
            page._show_report(report, detail=summary())
            page.show()
            QTest.qWait(10)
            self.assertIs(page.analysis_layout.itemAt(0).widget(), page.apm_card)
            self.assertEqual(page.apm_card.chart.focus_id, 1001)
            self.assertEqual([p["apm"] for p in page.apm_card.chart.players], [64, 73, None, 0])
            self.assertTrue(page.analysis_placeholder.isHidden())
            page.raw_toggle.click()
            page.raw_toggle.click()
            self.assertTrue(page.analysis_placeholder.isHidden(), "图表已可见时不插入说明占位")
            page._preview_public_context(102, {"map": "Dry Arabia"})
            self.assertFalse(page.apm_card.isVisible(), "切换到新对局立即隐藏旧数据")
            parsed["source"]["game_id"] = 102
            page._show_report(analyze_replay(parsed), detail=summary(101))
            self.assertEqual(page.apm_card.chart.players, [], "其他对局的摘要不得画入本场")
            self.assertIs(page.analysis_layout.itemAt(0).widget(), page.apm_card)
            page._show_report(analyze_replay(parsed))
            self.assertEqual(page.apm_card.chart.players, [], "无摘要时不能沿用旧APM或推算部分指令")
            page.close()


if __name__ == "__main__":
    unittest.main()
