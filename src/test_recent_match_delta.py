"""最近对局积分变化：真实Qt绘制、原色徽章与逐局资料隔离回归。"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QRectF, Qt
from PyQt5.QtGui import QFontDatabase, QPainter, QPixmap
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from analysis_page import AnalysisPage
from match_widgets import CARD_LOSS, CARD_WIN, MATCH_METADATA_ROLE, MUTED
from rank_display import rank_badge
from test_match_analysis import game
from visual import STYLE


def delta_snapshot():
    """逐局白银升降段；当前玩家是征服者，缺失记录另有很高匹配分。"""
    games = []
    cases = ((3101, "rm_2v2", 596, 20, "win"),
             (3102, "rm_2v2", 605, -13, "loss"),
             (3103, "rm_2v2", None, None, "win"),
             (3104, "qm_2v2", 2500, 99, "win"))
    for offset, (identity, mode, rating, change, result) in enumerate(cases, 1):
        match = game(identity, result=result, kind=mode)
        match["started_at"] = f"2026-10-25T10:0{offset}:00Z"
        match["leaderboard"] = "rm_team" if mode == "rm_2v2" else "qm_2v2"
        focus = match["teams"][1][1]["player"]
        focus.update(rating=rating, rating_diff=change, mmr=3500)
        games.append(match)
    return {"profile_id": 7,
            "profile": {"profile_id": 7, "name": "逐局积分测试玩家", "modes": {
                "rm_team": {"rating": 2200, "rank_level": "conqueror_3", "season": 14}}},
            "games": games, "source": "cache", "warning": "",
            "fetched_at": "2026-10-25T16:00:00+00:00"}


class RecentMatchDeltaDrawingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))
        cls.application.setStyleSheet(STYLE)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.page = AnalysisPage(self.directory.name)
        self.source = delta_snapshot()
        self.original = copy.deepcopy(self.source)
        self.page._show_snapshot(self.source)
        self.page.show()

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()
        self.directory.cleanup()

    def test_visible_signed_changes_use_match_badges_and_never_matching_or_current_rank(self):
        # 已知案例直接指定预期徽章；不以被测函数重算本局段位或增减。
        expected = {3101: ("+20", CARD_WIN, "team_silver_2.png"),
                    3102: ("−13", CARD_LOSS, "team_silver_1.png"),
                    3103: ("—", MUTED, None), 3104: ("—", MUTED, None)}
        initial_metadata = {
            self.page.game_table.item(row, 0).data(Qt.UserRole):
                copy.deepcopy(self.page.game_table.item(row, 0).data(MATCH_METADATA_ROLE))
            for row in range(self.page.game_table.rowCount())}
        initial_ids = [self.page.game_table.item(row, 0).data(Qt.UserRole)
                       for row in range(self.page.game_table.rowCount())]
        for width in (760, 1100, 1500):
            with self.subTest(width=width):
                self.page.resize(width, 1100)
                QTest.qWait(15)
                table, text_calls, image_calls = self.page.game_table, [], []
                self.assertFalse(table.isColumnHidden(3))
                self.assertEqual(table.horizontalHeaderItem(3).text(), "积分变化")
                self.assertLessEqual(table.horizontalHeader().length(), table.viewport().width())
                self.assertEqual(table.horizontalScrollBar().maximum(), 0)
                original_text, original_pixmap = QPainter.drawText, QPainter.drawPixmap

                def capture_text(painter, *args):
                    if len(args) == 3 and isinstance(args[0], QRectF):
                        text_calls.append((str(args[2]), QRectF(args[0]),
                                           painter.pen().color().name(),
                                           painter.fontMetrics().horizontalAdvance(str(args[2]))))
                    return original_text(painter, *args)

                def capture_pixmap(painter, *args):
                    if len(args) == 3 and isinstance(args[0], QRectF) and isinstance(args[1], QPixmap):
                        image_calls.append((QRectF(args[0]), args[1].cacheKey(), QRectF(args[2]),
                                            painter.compositionMode(), painter.opacity()))
                    return original_pixmap(painter, *args)

                with patch.object(QPainter, "drawText", capture_text), \
                     patch.object(QPainter, "drawPixmap", capture_pixmap):
                    self.assertFalse(table.horizontalHeader().grab().isNull())
                    self.assertEqual([text for text, _, _, _ in text_calls if text == "积分变化"], ["积分变化"])
                    text_calls.clear()
                    self.assertFalse(table.viewport().grab().isNull())
                self.assertEqual([table.item(row, 0).data(Qt.UserRole) for row in range(table.rowCount())], initial_ids)
                for row in range(table.rowCount()):
                    identity = table.item(row, 0).data(Qt.UserRole)
                    cell = QRectF(table.visualItemRect(table.item(row, 3)))
                    visible_text = [call for call in text_calls if cell.contains(call[1].center())]
                    visible_images = [call for call in image_calls if cell.contains(call[0].center())]
                    text, color, icon_name = expected[identity]
                    self.assertEqual([(value, ink) for value, _, ink, _ in visible_text], [(text, color)])
                    self.assertTrue(table.viewport().rect().contains(cell.toRect()))
                    for _, area, _, text_width in visible_text:
                        self.assertTrue(cell.contains(area))
                        self.assertLessEqual(text_width, area.width())
                    if icon_name is None:
                        self.assertEqual(visible_images, [])
                    else:
                        self.assertEqual(len(visible_images), 1)
                        target, cache_key, source_rect, composition, opacity = visible_images[0]
                        original_badge = rank_badge(icon_name)
                        self.assertFalse(original_badge.isNull())
                        # 同一原图缓存标识、完整取样、普通合成与不透明绘制，确保无整体染色。
                        self.assertEqual(cache_key, original_badge.cacheKey())
                        self.assertEqual(source_rect, QRectF(original_badge.rect()))
                        self.assertEqual((target.width(), target.height()), (28, 28))
                        self.assertTrue(cell.contains(target))
                        self.assertEqual(composition, QPainter.CompositionMode_SourceOver)
                        self.assertEqual(opacity, 1)
                    self.assertEqual(table.item(row, 0).data(MATCH_METADATA_ROLE), initial_metadata[identity])
                self.assertEqual(set(initial_metadata), set(expected))
        self.assertEqual(self.source, self.original)
        self.assertEqual(self.page.snapshot, self.original)
        # 模型提示保留完整赛后分与升降段；绘制只显示本局带符号增减。
        by_id = {self.page.game_table.item(row, 0).data(Qt.UserRole): self.page.game_table.item(row, 3)
                 for row in range(self.page.game_table.rowCount())}
        self.assertIn("白银 II · 616 分", by_id[3101].toolTip())
        self.assertIn("赛前 596 → 赛后 616", by_id[3101].toolTip())
        self.assertIn("白银 I · 592 分", by_id[3102].toolTip())
        self.assertIn("赛前 605 → 赛后 592", by_id[3102].toolTip())
        self.assertIn("没有提供完整", by_id[3103].toolTip())
        self.assertIn("非排位", by_id[3104].toolTip())


if __name__ == "__main__":
    unittest.main()
