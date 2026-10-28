"""查询页黑灰层级与双方名单的实际Qt回归；只使用隔离合成资料。"""
from __future__ import annotations
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, QRectF, Qt
from PyQt5.QtGui import QFontDatabase, QFontMetricsF, QPainter
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QTableWidgetItem, QWidget

from analysis_page import AnalysisPage
from match_widgets import CARD_AUX, CARD_MODE, CARD_SURFACE, MATCH_METADATA_ROLE, MatchActionHeader, ModeSummaryCard, RoundedMatchTable, style_recent_matches
from test_match_analysis import game
from visual import CARD_GAP, STYLE, ui_font


def four_player_snapshot():
    """同名焦点与对手分别为7和8，不以名字判断胜负、分队或打开目标。"""
    match = game(501, kind="rm_4v4")
    match["teams"][0][0]["player"]["name"] = "相同昵称"
    match["teams"][1][1]["player"]["name"] = "相同昵称"
    for team_index, team in enumerate(match["teams"]):
        for offset in range(2):
            team.append({"player": {"profile_id": 20 + team_index * 2 + offset,
                                    "name": "超长名称仍能通过悬停看到全部资料" + str(offset),
                                    "civilization": "mongols", "result": "loss" if team_index == 0 else "win",
                                    "mmr": None if offset else 1200}})
    return {"profile_id": 7, "profile": {"profile_id": 7, "name": "相同昵称"},
            "games": [match], "source": "cache", "warning": "", "fetched_at": "2026-10-25T16:00:00Z"}


class QueryVisualHierarchyTests(unittest.TestCase):
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

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()
        self.directory.cleanup()

    def test_search_success_has_no_notice_row_but_preserves_accessibility_and_errors(self):
        """成功候选没有多余提示条；完整账号、键盘说明及错误/空结果反馈仍保留。"""
        self.page.resize(760, 560)
        self.page.show()
        self.page.query.setText("同名账号")
        name = "长中文昵称和Sen拉丁字母Miradore保留完整辅助文字"
        with patch.object(self.page, "_start_identities"):
            self.page._job_result([{"profile_id": 5497834, "name": name, "game_level": 22}], "search")
        self.assertTrue(self.page.notice.isHidden())
        self.assertEqual(self.page.notice.text(), "")
        self.assertTrue(self.page.player_choices.isVisible())
        self.assertIn("方向键", self.page.player_choices.accessibleDescription())
        item = self.page.player_choices.item(0)
        self.assertIn(name, item.data(Qt.AccessibleTextRole))
        self.assertIn("5497834", item.data(Qt.AccessibleTextRole))
        choice = self.page.player_choices.itemWidget(item)
        self.assertEqual(choice.name.text(), name)
        self.assertIn(name, choice.toolTip())
        self.page._job_error("网络暂时不可用，请重试")
        self.assertTrue(self.page.notice.isVisible())
        self.assertIn("网络暂时不可用", self.page.notice.text())
        with patch.object(self.page, "_start_identities"):
            self.page._job_result([], "search")
        self.assertTrue(self.page.notice.isVisible())
        self.assertIn("没有找到", self.page.notice.text())


    def test_card_has_distinct_captions_mode_and_compact_value_with_all_rows(self):
        card = ModeSummaryCard({"name": "排位 4 对 4", "wins": 17, "losses": 33,
                                "win_rate": 34, "count": 50,
                                "rows": [{"game_id": number, "duration": 1200 + number,
                                          "result": "win" if number < 17 else "loss"} for number in range(50)]})
        try:
            card.resize(163, 132)
            card.show()
            self.application.processEvents()
            self.assertEqual(card.heading_label.text(), "排位匹配")
            self.assertEqual(card.name_label.text(), "4 对 4")
            self.assertEqual(card.rate_label.font().pixelSize(), 16)
            self.assertIn(CARD_AUX, card.rate_caption.styleSheet())
            self.assertIn(CARD_MODE, card.name_label.styleSheet())
            self.assertEqual(len(card.duration_chart.rows), 50)
            for widget in (card.heading_label, card.wins_label, card.losses_label,
                           card.name_label, card.rate_label, card.duration_chart):
                self.assertTrue(card.rect().contains(widget.mapTo(card, widget.rect().bottomRight())))
            self.assertLess(card.name_label.geometry().right(), card.rate_label.geometry().left())
            self.assertFalse(card.grab().isNull())
        finally:
            card.close()
            card.deleteLater()

    def test_four_by_four_names_flags_and_unknown_scores_survive_narrow_and_wide_layouts(self):
        source = four_player_snapshot()
        original = copy.deepcopy(source)
        self.page._show_snapshot(source)
        self.page.show()
        for width in (760, 1100, 1500):
            self.page.resize(width, 900)
            QTest.qWait(10)
            table = self.page.game_table
            data = table.item(0, 0).data(MATCH_METADATA_ROLE)
            self.assertEqual(data["focus_profile_id"], 7)
            self.assertEqual([len(team["players"]) for team in data["display_teams"]], [4, 4])
            self.assertEqual({person["profile_id"] for person in data["display_teams"][0]["players"]}, {6, 7, 22, 23})
            self.assertEqual(table.item(0, 2).toolTip(), "")
            self.assertEqual(table.rowHeight(0), 104)
            self.assertTrue(table.isColumnHidden(4))
            self.assertFalse(table.isColumnHidden(3))
            self.assertGreaterEqual(table.columnWidth(2), 280)
            self.assertLessEqual(table.horizontalHeader().length(), table.viewport().width())
            self.assertFalse(table.alternatingRowColors())
            self.assertEqual(table.horizontalHeader().logicalIndex(0), 1)
            # 积分与双段位在同一个概览行中，外层横纵卡片共用固定16逻辑像素。
            self.assertEqual(self.page.rank_overview_row.y() - self.page.mode_cards.geometry().bottom() - 1, CARD_GAP)
            self.assertEqual(self.page.recent_panel.y() - self.page.rank_overview_row.geometry().bottom() - 1, CARD_GAP)
            self.assertIs(self.page.recent_heading.parentWidget(), self.page.recent_panel)
            self.assertIs(table.parentWidget(), self.page.recent_panel)
            self.assertEqual(table.y() - self.page.recent_heading.geometry().bottom() - 1, 1)
            self.assertFalse(table.grab().isNull())
        self.assertEqual(source, original)

    def test_missing_rate_is_explicit_without_zero_or_elo(self):
        card = ModeSummaryCard({"name": "快速 2 对 2", "count": 0})
        try:
            self.assertEqual(card.rate_label.text(), "—")
            self.assertNotIn("Elo", card.accessibleName())
            self.assertEqual(card.duration_chart.rows, [])
        finally:
            card.deleteLater()

    def test_map_header_and_both_lines_start_sixteen_pixels_inside_visible_card(self):
        """从真实绘制调用量首列留白，长地图省略但完整提示及原始资料仍保留。"""
        source = four_player_snapshot()
        full_name = "长地图名称用于核对首列增加留白后仍会正确省略并能悬停查看全文"
        source["games"][0]["map"] = full_name
        original_source = copy.deepcopy(source)
        self.page._show_snapshot(source)
        self.page.show()
        for width in (760, 1100, 1500):
            self.page.resize(width, 900)
            QTest.qWait(10)
            table, panel = self.page.game_table, self.page.recent_panel
            header, item = table.horizontalHeader(), table.item(0, 1)
            self.assertEqual(header.logicalIndex(0), 1)
            self.assertEqual(table.visualItemRect(item).left(), 0)
            self.assertEqual(table.viewport().mapTo(panel, QPoint()).x(), 0)
            self.assertEqual(header.viewport().mapTo(panel, QPoint()).x(), 0)
            calls = []
            original_draw = QPainter.drawText
            def capture(painter, *args):
                if len(args) == 3 and isinstance(args[0], QRectF):
                    calls.append((str(args[2]), QRectF(args[0]),
                                  painter.fontMetrics().horizontalAdvance(str(args[2]))))
                return original_draw(painter, *args)
            with patch.object(QPainter, "drawText", capture):
                self.assertFalse(header.grab().isNull())
                header_call = next(row for row in calls if row[0].startswith("地图"))
                calls.clear()
                self.assertFalse(table.viewport().grab().isNull())
            map_call = next(row for row in calls if row[0].startswith("长地图"))
            mode_call = next(row for row in calls if row[0].startswith("排位 4 对 4"))
            for text, rect, text_width in (header_call, map_call, mode_call):
                self.assertEqual(rect.left(), 16)
                self.assertLessEqual(rect.right(), table.columnWidth(1))
                self.assertLessEqual(text_width, rect.width())
            self.assertIn("…", map_call[0])
            self.assertIn(full_name, item.toolTip())
            self.assertEqual(item.text().split("\n", 1)[0], full_name)
            self.assertEqual(table.item(0, 0).data(MATCH_METADATA_ROLE)["game_id"], 501)
            self.assertLessEqual(header.length(), table.viewport().width())
            self.assertEqual(table.horizontalScrollBar().maximum(), 0)
        self.assertEqual(source, original_source)

    def test_mixed_one_and_four_player_groups_share_row_centres_and_header_columns(self):
        """混排行按整组中心对齐，表头ELO/旗名槽与实际绘制坐标逐项核对。"""
        source = four_player_snapshot()
        single = game(502, kind="rm_1v1")
        single["started_at"] = "2026-10-19T10:00:00Z"
        single["teams"] = [[single["teams"][0][0]], [single["teams"][1][1]]]
        single["teams"][0][0]["player"]["name"] = "单人对手"
        single["teams"][1][0]["player"]["name"] = "单人玩家"
        source["games"].append(single)
        original = copy.deepcopy(source)
        self.page._show_snapshot(source)
        self.page.show()
        for width in (760, 1100, 1500):
            self.page.resize(width, 900)
            QTest.qWait(10)
            table, panel = self.page.game_table, self.page.recent_panel
            delegate, header = table.itemDelegate(), table.horizontalHeader()
            calls = []
            original_text = delegate._text
            def capture(painter, rect, text, *args, **kwargs):
                calls.append((str(text), QRectF(rect)))
                return original_text(painter, rect, text, *args, **kwargs)
            with patch.object(delegate, "_text", side_effect=capture):
                table.viewport().grab()
            self.assertEqual([table.rowHeight(row) for row in range(2)], [104, 56])
            self.assertEqual(table.horizontalHeaderItem(2).text(), "ELO / 玩家 / ELO / 对手")
            self.assertIn("与赛后排位积分分开", table.horizontalHeaderItem(2).toolTip())
            self.assertFalse(any(text in ("我方", "对手") for text, _ in calls))
            team_headers = header.team_header_rects()
            first_score, first_people = team_headers[0]
            second_score, second_people = team_headers[1]
            # 两队间距也参与整表统一分配，宽窗不再把余白集中在对手之后。
            self.assertGreaterEqual(second_score.left() - first_people.right(), 16)
            self.assertLessEqual(first_people.width() + 40, 205)
            self.assertLessEqual(second_people.width() + 40, 205)
            for row in range(2):
                row_rect = QRectF(table.visualItemRect(table.item(row, 2)))
                self.assertAlmostEqual(first_score.left(), delegate.content_rect(row_rect, 2).left(), places=1)
                data = table.item(row, 0).data(MATCH_METADATA_ROLE)
                for group, (score_header, people_header) in zip(data["display_teams"], team_headers):
                    names = {person["display_name"] for person in group["players"]}
                    painted = [rect for text, rect in calls if text in names and row_rect.contains(rect.center())]
                    self.assertEqual(len(painted), len(group["players"]))
                    self.assertAlmostEqual((min(rect.center().y() for rect in painted) +
                                            max(rect.center().y() for rect in painted)) / 2,
                                           row_rect.center().y(), places=1)
                    for rect in painted:
                        self.assertAlmostEqual(rect.left() - 70, score_header.left(), places=1)
                        self.assertAlmostEqual(rect.left() - 30, people_header.left(), places=1)
                        self.assertLessEqual(rect.width(), 135)
                # 日期与时间分成两行，整组按行中心居中，保持日期表头的水平中心线。
                date_text, time_text = table.item(row, 0).text().split("\n", 1)[0].split()
                date_rect, time_rect = [next(rect for value, rect in calls if value == text and
                                            row_rect.top() <= rect.center().y() <= row_rect.bottom())
                                        for text in (date_text, time_text)]
                date_center = header.sectionViewportPosition(0) + header.sectionSize(0) / 2
                self.assertAlmostEqual(date_rect.center().x(), date_center, places=1)
                self.assertAlmostEqual(time_rect.center().x(), date_center, places=1)
                self.assertAlmostEqual((date_rect.top() + time_rect.bottom()) / 2,
                                       row_rect.center().y(), places=1)
                self.assertGreaterEqual(time_rect.top() - date_rect.bottom(), 3)
                self.assertLessEqual(time_rect.top() - date_rect.bottom(), 4)
                for column in (5,):
                    text = table.item(row, column).text().split("\n", 1)[0]
                    painted = next(rect for value, rect in calls if value == text and
                                   row_rect.top() <= rect.center().y() <= row_rect.bottom())
                    self.assertAlmostEqual(painted.center().y(), row_rect.center().y(), places=1)
                action = table.cellWidget(row, 6)
                from PyQt5.QtWidgets import QPushButton
                button = action.findChild(QPushButton, "GetMatchReplay")
                top = button.mapTo(table.viewport(), QPoint()).y()
                self.assertLessEqual(abs(top + button.height() / 2 - row_rect.center().y()), .5)
            self.assertIs(table.parentWidget(), panel)
            self.assertIs(self.page.recent_heading.parentWidget(), panel)
            self.assertEqual(self.page.recent_heading.contentsMargins().left(), 14)
            self.assertFalse(panel.mask().contains(QPoint(0, 0)))
            self.assertFalse(panel.mask().contains(panel.rect().bottomRight()))
            self.assertLessEqual(table.horizontalHeader().length(), table.viewport().width())
        self.assertEqual(source, original)

    def test_four_and_five_digit_matching_scores_fit_the_actual_number_font(self):
        """实际数字字体测量；四位/五位公开分数不得在固定槽内被省略。"""
        from match_widgets import MATCH_SCORE_WIDTH
        metrics = QFontMetricsF(ui_font(10, numeric=True))
        for score in ("9999", "10000", "99999"):
            self.assertLessEqual(metrics.horizontalAdvance(score), MATCH_SCORE_WIDTH, score)

    def test_both_teams_paint_fixed_right_aligned_scores_before_flags_and_names(self):
        """检查实际绘制调用的槽位，分数不再漂到两组名单的右边远端。"""
        self.page._show_snapshot(four_player_snapshot())
        self.page.show()
        for width in (760, 1100, 1500):
            self.page.resize(width, 900)
            QTest.qWait(10)
            table = self.page.game_table
            delegate = table.itemDelegate()
            calls = []
            original = delegate._text
            def capture(painter, rect, text, *args, **kwargs):
                calls.append((str(text), QRectF(rect), kwargs))
                return original(painter, rect, text, *args, **kwargs)
            with patch.object(delegate, "_text", side_effect=capture):
                self.assertFalse(table.viewport().grab().isNull())
            date, time = table.item(0, 0).text().split("\n", 1)[0].split()
            date_calls = [(rect, options) for text, rect, options in calls if text in (date, time)]
            header = table.horizontalHeader()
            date_center = header.sectionViewportPosition(0) + header.sectionSize(0) / 2
            self.assertEqual(len(date_calls), 2)
            for rect, options in date_calls:
                self.assertAlmostEqual(rect.center().x(), date_center, places=1)
                self.assertTrue(options["alignment"] & Qt.AlignHCenter)
            self.assertTrue(table.horizontalHeaderItem(0).textAlignment() & Qt.AlignHCenter)
            names = [(text, rect) for text, rect, _ in calls if text in ("相同昵称 · 账号7", "相同昵称 · 账号8")]
            self.assertEqual(len(names), 2)
            for name, name_rect in names:
                score = "1750" if name.endswith("账号7") else "1500"
                matches = [(rect, options) for text, rect, options in calls if text == score
                           and abs(rect.top() - name_rect.top()) < 1 and rect.right() < name_rect.left()]
                self.assertEqual(len(matches), 1)
                score_rect, options = matches[0]
                self.assertEqual(score_rect.width(), 32)
                self.assertGreaterEqual(name_rect.left() - score_rect.right(), 37)
                self.assertTrue(options["alignment"] & Qt.AlignRight)
            # 缺失分数仍绘制—，且仅在32像素分数槽里，不替换为0。
            self.assertTrue(any(text == "—" and rect.width() == 32 for text, rect, _ in calls))

    def test_missing_date_is_one_centred_line_without_changing_item_data(self):
        """缺失日期保持单行空态，绘制不能改原文字、提示、编号或对局元数据。"""
        self.page._show_snapshot(four_player_snapshot())
        self.page.resize(760, 900)
        self.page.show()
        QTest.qWait(10)
        table = self.page.game_table
        item = table.item(0, 0)
        item.setText("未提供时间\n排位 4 对 4")
        item.setToolTip("原始时间未知，保留此完整提示")
        before = (item.text(), item.toolTip(), item.data(Qt.UserRole), item.data(MATCH_METADATA_ROLE))
        calls = []
        delegate = table.itemDelegate()
        original_text = delegate._text
        def capture(painter, rect, text, *args, **kwargs):
            calls.append((str(text), QRectF(rect)))
            return original_text(painter, rect, text, *args, **kwargs)
        with patch.object(delegate, "_text", side_effect=capture):
            self.assertFalse(table.viewport().grab().isNull())
        painted = [rect for text, rect in calls if text == "未提供时间"]
        self.assertEqual(len(painted), 1)
        self.assertEqual(painted[0].center(), QRectF(table.visualItemRect(item)).center())
        self.assertEqual((item.text(), item.toolTip(), item.data(Qt.UserRole), item.data(MATCH_METADATA_ROLE)), before)

    def test_whole_table_clips_header_and_bottom_corners_to_parent_background(self):
        """从父窗口实际像素核对四角，避免只写border-radius而被矩形子视口盖住。"""
        parent = QWidget()
        parent.setObjectName("RoundProofParent")
        parent.setStyleSheet("QWidget#RoundProofParent{background:#0f0f11;}")
        table = RoundedMatchTable(1, 7, parent)
        table.setHorizontalHeader(MatchActionHeader(6, table))
        table.setHorizontalHeaderLabels(["日期", "地图", "双方", "段位", "时长", "结果", "分析"])
        for column in range(7):
            table.setItem(0, column, QTableWidgetItem(""))
        table.verticalHeader().hide()
        table.setShowGrid(False)
        style_recent_matches(table, [{"game_id": 1, "result": "win"}])
        try:
            parent.show()
            for width in (760, 1100):
                parent.resize(width, 160)
                table.setGeometry(10, 10, width - 20, 86)
                self.application.processEvents()
                # 抗锯齿圆角由轻量角覆盖绘制，不再依赖会产生硬斜边的窗口mask。
                self.assertTrue(table.mask().isEmpty())
                image = parent.grab().toImage()
                scale = parent.devicePixelRatioF()
                def pixel(x, y):
                    return image.pixelColor(round(x * scale), round(y * scale)).name()
                for x, y in ((10, 10), (width - 11, 10), (10, 95), (width - 11, 95)):
                    self.assertEqual(pixel(x, y), "#0f0f11")
                self.assertEqual(pixel(width // 2, 12), CARD_SURFACE)
                self.assertEqual(pixel(width // 2, 88), CARD_SURFACE)
        finally:
            parent.close()
            parent.deleteLater()


if __name__ == "__main__":
    unittest.main()
