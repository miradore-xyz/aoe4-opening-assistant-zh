"""公开战绩界面回归：全部模式、内部分析、滚动返回与选中对比度。"""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QColor, QFontDatabase, QPalette
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel, QStyle, QStyleOptionViewItem

from analysis_page import AnalysisPage
from test_match_analysis import game
from visual import STYLE
from match_widgets import MATCH_ACTION_SIZE


def snapshot():
    """快速对局数量更多，但最新一场是排位，复现截图对应的模式遗漏。"""
    games = []
    for index in range(4):
        item = game(100 + index, kind="qm_4v4")
        item["started_at"] = f"2026-10-2{index}T10:00:00Z"
        games.append(item)
    newest = game(200, result="loss", kind="rm_4v4")
    newest["started_at"] = "2026-10-25T15:00:00Z"
    return {"profile_id": 7, "profile": {"profile_id": 7, "name": "测试玩家"},
            "games": games + [newest], "source": "network",
            "fetched_at": "2026-10-25T16:00:00+00:00", "warning": ""}


class MatchViewAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))
        cls.application.setStyleSheet(STYLE)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.page = AnalysisPage(self.directory.name)
        self.page.resize(760, 640)
        self.page.show()
        self.page._show_snapshot(snapshot())
        QTest.qWait(30)

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        QTest.qWait(1)
        self.directory.cleanup()

    def test_default_recent_matches_include_ranked_and_quick_in_time_order(self):
        page = self.page
        self.assertFalse(hasattr(page, "mode"))
        self.assertEqual(page.game_table.rowCount(), 5)
        ids = [page.game_table.item(row, 0).data(Qt.UserRole) for row in range(5)]
        self.assertEqual(ids, [200, 103, 102, 101, 100])
        self.assertIn("排位", page.game_table.item(0, 0).text())
        self.assertIn("快速", page.game_table.item(1, 0).text())
        self.assertEqual(len(page.mode_cards.cards), 2)
        # 玩家头部直接承接模式卡片；移除的主页按钮和重复场数标题不留空占位。
        from PyQt5.QtWidgets import QLabel, QPushButton
        self.assertFalse(any("个人主页" in button.text() for button in page.body.findChildren(QPushButton)))
        self.assertFalse(any(label.text().startswith("近期模式 · 已完成")
                             for label in page.detail.findChildren(QLabel)))
        self.assertIs(page.detail_layout.itemAt(0).widget(), page.mode_cards)
        self.assertEqual({card.summary["code"] for card in page.mode_cards.cards}, {"qm_4v4", "rm_4v4"})
        self.assertTrue(all(card.isVisible() for card in page.mode_cards.cards))
        for card in page.mode_cards.cards:
            expected = [row["game_id"] for row in reversed(page.report["rows"])
                        if row["mode"] == card.summary["code"]]
            self.assertEqual([row["game_id"] for row in card.duration_chart.rows], expected)
            self.assertEqual(len(card.duration_chart.rows), card.summary["count"])
            self.assertEqual(card.wins_label.text(), f"{card.summary['wins']}胜")
            self.assertEqual(card.losses_label.text(), f"{card.summary['losses']}负")
        self.assertIn("非赛季累计", page.scope_label.toolTip())

    def test_fixed_all_modes_ignores_legacy_filter_and_refresh_keeps_every_match(self):
        page = self.page
        self.assertFalse(hasattr(page, "mode"))
        self.assertEqual(page.game_table.rowCount(), 5)
        self.assertEqual(page.recent_heading.text(), "最近对局")
        page.scroll.verticalScrollBar().setValue(page.scroll.verticalScrollBar().maximum())
        self.assertGreater(page.scroll.verticalScrollBar().value(), 0)
        self.assertFalse(any(widget.accessibleName() in ("战绩统计模式", "对局模式筛选")
                             for widget in page.findChildren(QLabel)))
        changed = snapshot()
        newer = game(300, kind="rm_4v4")
        newer["started_at"] = "2026-10-25T16:00:00Z"
        changed["games"].append(newer)
        page._show_snapshot(changed)
        QTest.qWait(30)
        self.assertEqual(page.game_table.rowCount(), 6)
        self.assertEqual(page.scroll.verticalScrollBar().value(), 0)
        # 旧版保存的模式字段即使仍在磁盘，也不能偷偷缩小实际记录范围。
        (Path(self.directory.name) / "战绩查询.json").write_text(json.dumps(
            {"query": "测试玩家", "profile_id": 7, "mode": "qm_4v4"}), encoding="utf-8")
        with patch.object(page.client, "cached_profile", return_value=changed):
            page._restore_query()
        self.assertEqual(page.game_table.rowCount(), 6)
        self.assertEqual(page.game_table.item(0, 0).data(Qt.UserRole), 300)
        self.assertLessEqual(page.body.minimumSizeHint().width(), 760)
        self.assertGreaterEqual(page.game_table.viewport().width(), page.game_table.horizontalHeader().length())

    def test_switching_player_starts_with_all_modes(self):
        other = copy.deepcopy(snapshot())
        other["profile_id"] = 6
        for item in other["games"]:
            item["teams"][1][0]["player"]["result"] = "win"
        self.page._show_snapshot(other)
        self.assertFalse(hasattr(self.page, "mode"))
        self.assertEqual(self.page.game_table.rowCount(), 5)
        self.assertEqual({card.summary["code"] for card in self.page.mode_cards.cards}, {"qm_4v4", "rm_4v4"})

    def test_scroll_top_preserves_query_exact_match_and_table(self):
        """返回顶部仅移动现有滚动位置，不重新查询或丢失已选对局。"""
        page = self.page
        page.query.setText("测试玩家 · 账号7")
        table = page.game_table
        table.selectRow(2)
        selected_id = table.item(2, 0).data(Qt.UserRole)
        bar = page.scroll.verticalScrollBar()
        button = page.scroll_top_button
        self.assertEqual(bar.value(), 0)
        self.assertFalse(button.isVisible())
        bar.setValue(bar.maximum())
        self.application.processEvents()
        self.assertGreater(bar.value(), 0)
        self.assertTrue(button.isVisible())
        self.assertIs(button.parentWidget(), page.scroll.viewport())
        self.assertEqual((button.width(), button.height()), (44, 44))
        self.assertTrue(page.scroll.viewport().rect().contains(button.geometry()))
        QTest.mouseClick(button, Qt.LeftButton)
        self.application.processEvents()
        self.assertEqual(bar.value(), 0)
        self.assertFalse(button.isVisible())
        self.assertIs(page.game_table, table)
        self.assertEqual(page.query.text(), "测试玩家 · 账号7")
        self.assertEqual(table.currentRow(), 2)
        self.assertEqual(table.item(table.currentRow(), 0).data(Qt.UserRole), selected_id)

    def test_selected_text_is_bright_and_result_colors_survive_selection(self):
        table = self.page.game_table
        table.selectRow(0)
        delegate = table.itemDelegate()
        for row, column, expected in ((0, 0, "#f4f4f5"), (0, 5, "#ef4d4d"), (1, 5, "#17b85b")):
            option = QStyleOptionViewItem()
            option.state |= QStyle.State_Selected
            delegate.initStyleOption(option, table.model().index(row, column))
            self.assertEqual(option.palette.color(QPalette.HighlightedText), QColor(expected))
            self.assertEqual(option.palette.color(QPalette.Inactive, QPalette.HighlightedText), QColor(expected))

    def test_official_season_total_is_separate_from_recent_mode_samples(self):
        official = [{"mode": "rm_team", "name": "团队排位", "scope": "第 14 赛季",
                     "games": 18, "wins": 6, "losses": 12, "win_rate": 100 / 3,
                     "rating": 580, "rating_label": "排位积分", "season": 14}]
        with patch("analysis_page.profile_overview", return_value=official):
            self.page._show_snapshot(snapshot())
        page = self.page
        self.assertEqual(page.game_table.rowCount(), 5)
        self.assertEqual(len(page.mode_cards.cards), 2)
        self.assertEqual(page.overview_table.rowCount(), 1)
        self.assertEqual(page.overview_table.item(0, 2).text(), "18 场 · 6 胜 12 负")
        self.assertEqual(page.overview_table.item(0, 3).text(), "33.3%")
        self.assertIn("赛季", page.overview_table.item(0, 1).text())
        self.assertGreater(page.detail_layout.indexOf(page.overview_table),
                           page.detail_layout.indexOf(page.recent_panel))
        empty = snapshot()
        empty["games"] = []
        with patch("analysis_page.profile_overview", return_value=official):
            page._show_snapshot(empty)
        self.assertEqual(page.overview_table.item(0, 2).text(), "18 场 · 6 胜 12 负")

    def test_arrow_is_the_only_internal_analysis_action_and_never_opens_browser(self):
        """单一箭头鼠标/键盘均进入软件内分析，不再打开外部对局网页。"""
        page = self.page
        actions = page.game_table.cellWidget(0, 6)
        from PyQt5.QtWidgets import QPushButton
        analyze = actions.findChild(QPushButton, "GetMatchReplay")
        self.assertIsNone(actions.findChild(QPushButton, "OpenMatchWeb"))
        self.assertEqual(actions.findChildren(QPushButton), [analyze])
        self.assertEqual(analyze.text(), "↗")
        self.assertEqual(analyze.accessibleName(), "对局分析")
        self.assertIn("对局分析", analyze.toolTip())
        # 箭头与表头水平同心、与行中结果垂直同心，三种窗口均无额外外链槽。
        for width in (760, 1100, 1500):
            page.resize(width, 640)
            QTest.qWait(15)
            table = page.game_table
            badge = table.itemDelegate().result_badge_rect(table.visualItemRect(table.item(0, 5)))
            analyze_origin = analyze.mapTo(table.viewport(), QPoint())
            self.assertEqual(analyze.size(), MATCH_ACTION_SIZE)
            self.assertLessEqual(abs(analyze_origin.y() + analyze.height() / 2 - badge.center().y()), 1)
            self.assertGreaterEqual(analyze_origin.x() - badge.right(), 12)
            self.assertLessEqual(analyze_origin.x() + analyze.width(), table.viewport().width())
            self.assertLessEqual(table.horizontalHeader().length(), table.viewport().width())
            self.assertEqual([table.horizontalHeaderItem(column).text() for column in (3, 4, 5, 6)],
                             ["赛后段位", "时长", "结果", "分析"])
            self.assertLessEqual(abs(table.horizontalHeader().action_text_rect().center().x()
                                     - (analyze_origin.x() + analyze.width() / 2)), 1)
            # 实际表头必须有可见文字，不能只有位置正确的空绘制槽。
            header = table.horizontalHeader()
            pixels = header.grab().toImage()
            ratio = header.devicePixelRatioF()
            region = header.action_text_rect()
            visible_text = sum(min(pixels.pixelColor(x, y).getRgb()[:3]) >= 80
                               for y in range(round(region.top() * ratio), round(region.bottom() * ratio))
                               for x in range(round(region.left() * ratio), round(region.right() * ratio)))
            self.assertGreater(visible_text, 8)
        from PyQt5.QtTest import QSignalSpy
        requested = QSignalSpy(page.public_match_requested)
        with patch("PyQt5.QtGui.QDesktopServices.openUrl") as opened, patch("webbrowser.open") as browser:
            analyze.click()
            opened.assert_not_called()
            browser.assert_not_called()
            self.assertEqual(len(requested), 1)
            self.assertEqual(requested[0][:2], [7, 200])
            self.assertEqual(requested[0][2]["game_id"], 200)
            analyze.setFocus()
            QTest.keyClick(analyze, Qt.Key_Space)
            self.assertEqual(len(requested), 2)
            self.assertEqual(requested[1][:2], [7, 200])
            self.assertEqual(requested[1][2]["game_id"], 200)
            opened.assert_not_called()
            browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
