"""整张最近对局表的七组实际绘制几何；不把短昵称的自然末端当成列边界。"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, QRectF, Qt
from PyQt5.QtGui import QFontDatabase, QPainter
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QPushButton

from analysis_page import AnalysisPage
from match_widgets import MATCH_METADATA_ROLE
from test_query_visual_hierarchy import four_player_snapshot
from visual import STYLE


class MatchGroupSpacingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))
        cls.application.setStyleSheet(STYLE)

    def test_seven_painted_groups_share_gaps_header_centres_and_name_hit_geometry(self):
        with tempfile.TemporaryDirectory() as folder:
            page = AnalysisPage(folder)
            sample = four_player_snapshot()
            sample["games"][0]["map"] = "长地图名字也必须省略并保持首列16像素起点"
            page._show_snapshot(sample)
            page.show()
            try:
                for width in (760, 1100, 1500):
                    with self.subTest(width=width):
                        page.resize(width, 1000)
                        QTest.qWait(15)
                        table, calls = page.game_table, []
                        original = QPainter.drawText
                        def capture(painter, *args):
                            if len(args) == 3 and isinstance(args[0], QRectF):
                                calls.append((str(args[2]), QRectF(args[0])))
                            return original(painter, *args)
                        with patch.object(QPainter, "drawText", capture):
                            table.horizontalHeader().grab()
                        def header(text):
                            return next(rect for value, rect in calls
                                        if value == text or text == "地图" and value.startswith(text))
                        scores = sorted((rect for text, rect in calls if text == "ELO"), key=lambda area: area.left())
                        groups = [header("地图"), header("日期"), scores[0].united(header("玩家")),
                                  scores[1].united(header("对手")), header("积分变化"), header("结果"), header("分析")]
                        gaps = [right.left() - left.right() for left, right in zip(groups, groups[1:])]
                        self.assertGreaterEqual(min(gaps), 15.5)
                        self.assertLessEqual(max(gaps) - min(gaps), 1)
                        self.assertEqual(groups[0].left(), 16)
                        self.assertLessEqual(abs(groups[-1].right() - (table.viewport().width() - 16)), 1)
                        self.assertEqual(table.horizontalHeader().length(), table.viewport().width())
                        self.assertEqual(table.horizontalScrollBar().maximum(), 0)
                        calls.clear()
                        with patch.object(QPainter, "drawText", capture):
                            table.viewport().grab()
                        raw_date = table.item(0, 0).text().split("\n", 1)[0].split()
                        for value in raw_date:
                            area = next(rect for text, rect in calls if text == value)
                            self.assertLessEqual(abs(area.center().x() - groups[1].center().x()), 1)
                        result = next(rect for text, rect in calls if text == "胜利")
                        self.assertLessEqual(abs(result.center().x() - groups[5].center().x()), 1)
                        button = table.cellWidget(0, 6).findChild(QPushButton, "GetMatchReplay")
                        origin = button.mapTo(table.viewport(), QPoint())
                        self.assertLessEqual(abs(origin.x() + button.width() / 2 - groups[6].center().x()), 1)
                        delegate = table.itemDelegate()
                        cell = table.visualItemRect(table.item(0, 2))
                        data = table.item(0, 0).data(MATCH_METADATA_ROLE)
                        layouts = delegate.player_name_layout(delegate.content_rect(cell, 2), data)
                        for entry in layouts:
                            self.assertLessEqual(entry["name_rect"].width(), 135)
                            target = delegate.player_at(cell, data, entry["hit_rect"].center())
                            self.assertEqual(target["profile_id"], entry["player"]["profile_id"])
                        self.assertTrue(any("…" in entry["visible_text"] for entry in layouts))
            finally:
                page.close()
                page.deleteLater()
                self.application.sendPostedEvents(None, QEvent.DeferredDelete)
                self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
