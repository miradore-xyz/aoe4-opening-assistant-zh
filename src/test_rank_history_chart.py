"""排位积分图真实Qt回归：来源值、双轴、缺失记录、悬停与键盘，以及查询页接线。"""
import copy
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QFontMetricsF, QHelpEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QToolTip

from analysis_page import AnalysisPage
from rank_history_chart import RankHistoryCard, RankHistoryChart, local_time, ranking_history, recent_rank_history
from test_query_visual_hierarchy import four_player_snapshot
from visual import STYLE


def profile():
    return {"modes": {
        "rm_solo": {"rating_history": {"1792526400": {"rating": 1100}, "1792440000": {"rating": 1000}}},
        "rm_team": {"rating_history": {"1792440000": {"rating": 1300}, "1792612800": {"rating": 1400}}},
        "rm_solo_elo": {"rating_history": {"1792440000": {"rating": 1800}}},
        "rm_1v1": {"rating_history": {"1792440000": {"rating": 2222}}}}}


class RankHistoryChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.windows = []
        self.now = datetime(2026, 10, 27, 12).astimezone()

    def tearDown(self):
        for widget in self.windows:
            widget.close()
            widget.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def card(self, value=None):
        card = RankHistoryCard(profile() if value is None else value, now=self.now)
        self.windows.append(card)
        card.resize(680, card.sizeHint().height())
        card.show()
        card.activateWindow()
        QTest.qWait(10)
        return card

    def test_normalization_preserves_raw_ranked_values_and_excludes_other_metrics(self):
        raw = profile()
        before = copy.deepcopy(raw)
        history = ranking_history(raw)
        self.assertEqual(raw, before)
        self.assertEqual(list(history), ["rm_solo", "rm_team"])
        self.assertEqual([p["timestamp"] for p in history["rm_solo"]], [1792440000, 1792526400])
        self.assertEqual([p["rating"] for p in history["rm_solo"]], [1000, 1100])
        self.assertEqual([p["rating"] for p in history["rm_team"]], [1300, 1400])

    def test_invalid_dates_scores_and_missing_series_are_not_zero_points(self):
        raw = {"modes": {"rm_solo": {"rating_history": {
            "bad": {"rating": 1000}, "NaN": {"rating": 1000}, "999999999999999999999": {"rating": 1000},
            "1792440000.1": {"rating": 1000}, True: {"rating": 1000},
            "1792440000": {"rating": True}, "1792526400": {"rating": float("nan")},
            "1792612800": {"rating": float("inf")}, "1792699200": {"rating": -1},
            "1792785600": {"rating": "1000"}, "1792872000": {"rating": 0}}}}}
        history = ranking_history(raw)
        self.assertEqual(history["rm_team"], [])
        self.assertEqual(history["rm_solo"], [{"timestamp": 1792872000, "rating": 0}])
        for value in (None, [], {"modes": []}, {"modes": {"rm_solo": {"rating_history": []}}}):
            self.assertEqual(ranking_history(value), {"rm_solo": [], "rm_team": []})

    def test_two_series_use_same_timestamp_axis_and_score_axis(self):
        chart = self.card().chart
        solo = chart.series["rm_solo"][0]
        team = chart.series["rm_team"][0]
        self.assertEqual(chart.point_position(solo).x(), chart.point_position(team).x())
        same_score = {"timestamp": team["timestamp"], "rating": solo["rating"]}
        self.assertEqual(chart.point_position(solo).y(), chart.point_position(same_score).y())
        point = chart.series["rm_solo"][1]
        expected = (point['timestamp']-chart.time_bounds[0]) / (chart.time_bounds[1]-chart.time_bounds[0])
        self.assertAlmostEqual(chart.point_position(point).x(), chart.plot_rect().left()+expected*chart.plot_rect().width())

    def test_compact_chart_uses_dates_instead_of_intraday_clock_labels(self):
        def card_at(*dates):
            history = {str(int(datetime(*value).astimezone().timestamp())): {"rating": 1000 + index * 20}
                       for index, value in enumerate(dates)}
            return self.card({"modes": {"rm_solo": {"rating_history": history}}})

        same_day = card_at((2026, 10, 25, 2, 12), (2026, 10, 25, 7, 37), (2026, 10, 25, 13, 3))
        self.assertEqual(same_day.chart.height(), 160)
        self.assertEqual([label for _, label in same_day.chart.date_ticks()], ["10-13", "10-20", "10-27"])
        self.assertEqual(len(same_day.chart.series["rm_solo"]), 3, "日期合并不能丢失日内积分点")
        overnight = card_at((2026, 10, 25, 23, 30), (2026, 10, 26, 1, 30))
        self.assertEqual([label for _, label in overnight.chart.date_ticks()], ["10-13", "10-20", "10-27"])
        self.now = datetime(2027, 1, 1, 12).astimezone()
        new_year = card_at((2026, 12, 31, 23, 30), (2027, 1, 1, 1, 30))
        self.assertEqual([label for _, label in new_year.chart.date_ticks()], ["2026-12-18", "2026-12-25", "2027-01-01"])

    def test_fifteen_calendar_days_exclude_old_and_future_without_filling_missing_dates(self):
        start = datetime(2026, 10, 13).astimezone()
        stamps = [start.timestamp()-1, start.timestamp(), self.now.timestamp(), self.now.timestamp()+1]
        raw = {'modes': {'rm_team': {'rating_history': {str(int(stamp)): {'rating': 1000+i}
                                                      for i, stamp in enumerate(stamps)}}}}
        series, bounds, ticks = recent_rank_history(raw, self.now)
        self.assertEqual([point['rating'] for point in series['rm_team']], [1001, 1002])
        self.assertEqual(series['rm_solo'], [])
        self.assertEqual(local_time(bounds[0], '%Y-%m-%d'), '2026-10-13')
        self.assertEqual(local_time(bounds[1], '%Y-%m-%d'), '2026-10-28')
        self.assertEqual(len(ticks), 3)

    def test_keyboard_accessible_readout_preserves_both_real_scores_without_popup(self):
        card = self.card()
        chart = card.chart
        chart.setFocus(Qt.TabFocusReason)
        self.app.processEvents()
        self.assertIn(local_time(1792440000), chart.accessibleDescription())
        self.assertIn("单排 1000 分", chart.accessibleDescription())
        self.assertIn("组排 1300 分", chart.accessibleDescription())
        self.assertIn("本地时间", chart.accessibleDescription())
        QTest.keyClick(chart, Qt.Key_Right)
        self.assertEqual(chart.selected_timestamp, 1792526400)
        self.assertIn("1100", chart.accessibleDescription())
        self.assertNotIn("组排", chart.accessibleDescription(), "该时间没有组排快照时不得补值")
        QTest.keyClick(chart, Qt.Key_End)
        self.assertEqual(chart.selected_timestamp, 1792612800)
        QTest.keyClick(chart, Qt.Key_Home)
        self.assertEqual(chart.selected_timestamp, 1792440000)

    def test_mouse_hover_uses_point_timestamp_and_actual_value(self):
        card = self.card()
        chart = card.chart
        point = chart.series["rm_solo"][1]
        with patch.object(QToolTip, "showText") as popup:
            QTest.mouseMove(chart, chart.point_position(point).toPoint())
            self.app.processEvents()
            self.app.sendEvent(chart, QHelpEvent(QEvent.ToolTip, QPoint(1, 1), chart.mapToGlobal(QPoint(1, 1))))
            popup.assert_not_called()
        self.assertEqual(chart.selected_timestamp, point["timestamp"])
        self.assertIn(local_time(point["timestamp"]), chart.accessibleDescription())
        self.assertIn("单排 1100 分", chart.accessibleDescription())
        self.assertEqual(chart.toolTip(), "", "空白区域不应显示整图操作提示")
        self.assertEqual(card.layout().count(), 2, "卡片只保留标题行和图表，不留底部文字槽")
        self.assertFalse(QToolTip.isVisible(), "实际数据点也不得显示日期/积分浮窗")

    def test_irregular_timestamps_keep_actual_spacing_and_bounded_point_hit_area(self):
        raw = {"modes": {"rm_team": {"rating_history": {
            "1792440000": {"rating": 1000}, "1792440060": {"rating": 1100},
            "1792443600": {"rating": 1200}}}}}
        chart = RankHistoryChart(ranking_history(raw))
        self.windows.append(chart)
        chart.resize(680, 160)
        chart.show()
        points = chart.series["rm_team"]
        positions = [chart.point_position(point) for point in points]
        self.assertAlmostEqual((positions[1].x() - positions[0].x()) /
                               (positions[2].x() - positions[0].x()), 1 / 60)
        QTest.mouseMove(chart, positions[1].toPoint())
        self.app.processEvents()
        self.assertEqual(chart.selected_timestamp, 1792440060)
        QTest.mouseMove(chart, (positions[1] + QPointF(0, 12)).toPoint())
        self.app.processEvents()
        self.assertIsNone(chart.selected_timestamp, "命中范围外不得选择邻近记录")

    def test_team_line_is_continuous_without_dashed_gaps(self):
        raw = {"modes": {"rm_team": {"rating_history": {
            "1792440000": {"rating": 1200}, "1792526400": {"rating": 1200}}}}}
        chart = self.card(raw).chart
        image = chart.grab().toImage()
        y = round(chart.point_position(chart.series["rm_team"][0]).y())
        left, right = [chart.point_position(point).x() for point in chart.series['rm_team']]
        for x in range(round(left) + 5, round(right) - 5):
            colors = [image.pixelColor(x, iy) for iy in range(y - 2, y + 3)]
            self.assertTrue(any(color.blue() - color.red() > 12 for color in colors),
                            "细实线中出现了虚线空档")

    def test_narrow_cross_year_date_labels_do_not_overlap(self):
        self.now = datetime(2027, 1, 4, 23).astimezone()
        history = {str(int(datetime(*date).astimezone().timestamp())): {"rating": score}
                   for date, score in (((2026, 12, 31, 23, 0), 1000), ((2027, 1, 4, 12, 0), 1100))}
        card = self.card({"modes": {"rm_team": {"rating_history": history}}})
        card.resize(260, card.sizeHint().height())
        self.app.processEvents()
        chart = card.chart
        rectangles = [chart._date_rect(stamp, label) for stamp, label in chart.date_ticks()]
        for left, right in zip(rectangles, rectangles[1:]):
            self.assertGreaterEqual(right.left() - left.right(), 16)
        self.assertEqual(len(chart.series["rm_team"]), 2, "减少刻度标签不能删除真实记录")

    def test_empty_and_single_point_render_without_fabricated_line(self):
        empty = self.card({})
        self.assertEqual(empty.chart.timestamps, [])
        self.assertFalse(empty.chart.grab().isNull())
        one = self.card({"modes": {"rm_team": {"rating_history": {"1792440000": {"rating": 1250}}}}})
        self.assertEqual(one.chart.series["rm_solo"], [])
        point = one.chart.series["rm_team"][0]
        fraction = (point['timestamp']-one.chart.time_bounds[0]) / (one.chart.time_bounds[1]-one.chart.time_bounds[0])
        self.assertAlmostEqual(one.chart.point_position(point).x(), one.chart.plot_rect().left()+fraction*one.chart.plot_rect().width())
        self.assertEqual(len(one.chart.timestamps), 1)
        self.assertFalse(one.chart.grab().isNull())
        self.assertIn("单排暂无记录", one.summary)
        extreme = self.card({"modes": {"rm_team": {"rating_history": {"1792440000": {"rating": 1.79e308}}}}})
        self.assertFalse(extreme.chart.grab().isNull())

    def test_query_card_is_between_mode_cards_and_all_mode_list_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            page = AnalysisPage(directory)
            self.windows.append(page)
            sample = four_player_snapshot()
            sample["profile"]["modes"] = profile()["modes"]
            page.resize(760, 900)
            page.show()
            page._show_snapshot(sample)
            self.app.processEvents()
            children = [page.detail_layout.itemAt(i).widget() for i in range(page.detail_layout.count())]
            self.assertLess(children.index(page.mode_cards), children.index(page.rank_overview_row))
            self.assertLess(children.index(page.rank_overview_row), children.index(page.recent_panel))
            self.assertIs(page.rank_history_card.parentWidget(), page.rank_overview_row)
            self.assertIs(page.profile_rank_summary.parentWidget(), page.rank_overview_row.current_card)
            rows = page.game_table.rowCount()
            mode_rows = len(page.report["rows"])
            page.rank_history_card.chart.setFocus()
            QTest.keyClick(page.rank_history_card.chart, Qt.Key_End)
            self.assertEqual(page.game_table.rowCount(), rows)
            self.assertEqual(len(page.report["rows"]), mode_rows)
            self.assertLessEqual(page.rank_history_card.width(), page.scroll.viewport().width())
            page.close()
            self.windows.remove(page)
            page.deleteLater()
            self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_long_legends_keep_actual_font_width_and_wrap_below_title_on_narrow_cards(self):
        card = self.card({})
        for width in (220,260,680):
            card.resize(width,card.sizeHint().height())
            QTest.qWait(20)
            legends = card.legends
            self.assertEqual([legend.name for legend in legends],['单排 · 暂无记录','组排 · 暂无记录'])
            for legend in legends:
                self.assertTrue(legend.isVisible())
                self.assertEqual(legend.font().pixelSize(),11,'全局样式不应覆盖图例实际字体')
                metrics = QFontMetricsF(legend.font())
                self.assertGreaterEqual(legend.width()-22,metrics.horizontalAdvance(legend.name)+3)
                self.assertGreaterEqual(legend.height(),metrics.height())
                self.assertGreaterEqual(legend.x(),16)
                self.assertLessEqual(legend.x()+legend.width(),card.width()-16)
                self.assertFalse(legend.grab().isNull())
                self.assertEqual(legend.toolTip(),'')
                if legend.y()==card.heading.y():
                    self.assertGreaterEqual(legend.x()-card.heading.x()-card.heading.width(),16)
            if width<300:
                self.assertGreater(card._header_rows,1,'窄卡应换行保留两个完整图例')


if __name__ == "__main__":
    unittest.main()
