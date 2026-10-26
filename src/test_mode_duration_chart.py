"""逐场时长柱图：样本不减少、旧到新、同尺度、缺失值和最近柱命中。"""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, QPointF
from PyQt5.QtGui import QFontDatabase, QHelpEvent
from PyQt5.QtWidgets import QApplication

from match_widgets import ModeSummaryGrid, RecentDurationChart


def row(identity, duration, result="win"):
    return {"game_id": identity, "started_at": "2026-10-25T12:00:00Z", "duration": duration, "result": result}


def summary(code, rows):
    wins = sum(item["result"] == "win" for item in rows)
    return {"code": code, "name": code, "rows": rows, "count": len(rows), "wins": wins,
            "losses": len(rows) - wins, "win_rate": wins * 100 / len(rows) if rows else None}


class DurationChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))

    def tearDown(self):
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_fifty_matches_keep_old_to_new_and_nearest_tooltip_even_between_thin_bars(self):
        chart = RecentDurationChart([row(index, index * 60, "loss" if index == 50 else "win")
                                     for index in range(50, 0, -1)], 3000)
        chart.resize(128, 36)
        chart.show()
        self.application.processEvents()
        self.assertEqual([item["game_id"] for item in chart.rows], list(range(1, 51)))
        for index in range(50):
            # 每个槽位左边是柱间空隙，仍能命中这场；最后一场不会被舍掉。
            position = QPointF((index + .01) * chart.width() / 50, 4)
            self.assertEqual(chart.index_at(position), index)
            self.assertGreater(chart.bar_rect(index).width(), 0)
        self.assertEqual(chart.index_at(QPointF(chart.width(), 3)), 49)
        self.assertIsNone(chart.index_at(QPointF(-1, 3)))
        position = QPoint(chart.width() - 1, 3)
        with patch("match_widgets.QToolTip.showText") as shown:
            self.application.sendEvent(chart, QHelpEvent(QEvent.ToolTip, position, chart.mapToGlobal(position)))
            self.assertIn("对局 50", shown.call_args.args[1])
            self.assertIn("10-25", shown.call_args.args[1])
            self.assertIn("失利 · 50:00", shown.call_args.args[1])
        chart.close()
        chart.deleteLater()

    def test_modes_use_common_seconds_scale_instead_of_rescaling_each_card(self):
        grid = ModeSummaryGrid([summary("快速", [row(2, 1200), row(1, 600)]),
                                summary("排位", [row(3, 2400)])])
        grid.resize(400, 110)
        grid.show()
        self.application.processEvents()
        first, second = (card.duration_chart for card in grid.cards)
        # 底部文字与基线有明确呼吸空间，增高后柱图仍保留原有的可读高度。
        for chart in (first, second):
            self.assertGreaterEqual(chart.caption_rect().top() - chart.plot_rect().bottom(), 8)
            self.assertGreaterEqual(chart.plot_rect().height(), 23)
            self.assertLessEqual(chart.caption_rect().bottom(), chart.height())
        self.assertEqual((first.duration_max, second.duration_max), (2400, 2400))
        self.assertAlmostEqual(first.bar_rect(0).height() / second.bar_rect(0).height(), .25)
        self.assertAlmostEqual(first.bar_rect(1).height() / second.bar_rect(0).height(), .5)
        self.assertEqual(len(second.rows), 1)
        self.assertLessEqual(second.bar_rect(0).width(), 8)
        grid.close()
        grid.deleteLater()

    def test_missing_durations_keep_slots_without_zero_bars_or_division_by_zero(self):
        chart = RecentDurationChart([row(1, None), row(2, 0), row(3, float("nan"))])
        chart.resize(128, 36)
        chart.show()
        self.application.processEvents()
        self.assertIsNone(chart.duration_max)
        self.assertEqual(len(chart.rows), 3)
        for index in range(3):
            self.assertIsNone(chart.bar_rect(index))
            self.assertIn("时长未提供", chart.match_tooltip(index))
            self.assertNotIn("0:00", chart.match_tooltip(index))
            self.assertEqual(chart.index_at(QPointF((index + .5) * chart.width() / 3, 4)), index)
        self.assertFalse(chart.grab().isNull())
        chart.close()
        chart.deleteLater()

    def test_extreme_long_match_does_not_give_short_match_a_fabricated_minimum_height(self):
        grid = ModeSummaryGrid([summary("短局", [row(1, 10), row(2, None)]),
                                summary("长局", [row(3, 24000, "loss")])])
        grid.show()
        self.application.processEvents()
        short, long = (card.duration_chart for card in grid.cards)
        self.assertIsNone(short.bar_rect(0))
        self.assertAlmostEqual(short.bar_rect(1).height() / long.bar_rect(0).height(), 10 / 24000)
        self.assertIn("胜利 · 0:10", short.match_tooltip(1))
        grid.close()
        grid.deleteLater()


if __name__ == "__main__":
    unittest.main()
