"""本地／公开复盘的同名玩家：显示可辨认，筛选、地图与时间线仍按编号关联。"""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QFont, QFontMetrics
from PyQt5.QtTest import QTest, QSignalSpy
from PyQt5.QtWidgets import QApplication, QLabel

from match_detail import MatchDetailError
from replay_analysis import analyze_replay
from replay_map_view import _legend_caption
from replay_page import ReplayPage
from replay_spatial import build_spatial_model


def same_name_replay(*, accounts=True, nickname="同名玩家"):
    """账号与局内编号刻意不同，防止把显示后缀误用作事件关联键。"""
    players = [{"id": 101, "name": nickname, "team": 1, "civilization": "english"},
               {"id": 202, "name": nickname, "team": 2, "civilization": "mongols"}]
    if accounts:
        players[0]["profile_id"], players[1]["profile_id"] = 880001, 990002
    commands = [{"time": time, "tick": time * 8, "offset": index + 100, "player_id": player,
                 "type": "move", "type_id": 62, "known": True,
                 "target": {"x": index * 12, "y": 1, "z": index * 5}, "entity_ids": [player + 1]}
                for index, (time, player) in enumerate(((1, 101), (2, 202), (10, 101), (20, 202)))]
    return {"source": {"name": "同名复盘验证.rec", "sha256": "c" * 64, "format": "synthetic"},
            "metadata": {"duration": 40, "map": "Dry Arabia", "players": players},
            "commands": commands, "coverage": {"ticks_per_second": 8, "commands_total": 4}}


class ReplaySameNamePlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.page = ReplayPage(self.directory.name)
        self.page.resize(760, 800)

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()
        self.directory.cleanup()

    def show_report(self, parsed=None):
        report = analyze_replay(parsed or same_name_replay())
        report["findings"] = [{"player_id": 101, "title": "甲方线索", "body": "只属于局内101", "evidence": []},
                              {"player_id": 202, "title": "乙方线索", "body": "只属于局内202", "evidence": []}]
        self.page._show_report(report)
        self.page.show()
        self.application.processEvents()
        return report

    def test_player_table_dropdown_and_findings_distinguish_names_without_changing_ids(self):
        report = self.show_report()
        expected = ["同名玩家 · 账号880001", "同名玩家 · 账号990002"]
        self.assertEqual([self.page.players_table.item(row, 0).text() for row in range(2)], expected)
        self.assertEqual([self.page.players_table.item(row, 0).data(Qt.UserRole) for row in range(2)], [101, 202])
        self.assertEqual([self.page.player_filter.itemText(index) for index in (1, 2)], expected)
        self.assertEqual([self.page.player_filter.itemData(index) for index in (1, 2)], [101, 202])
        findings = [label.text() for label in self.page.findings_frame.findChildren(QLabel)]
        self.assertIn(expected[0] + " · 甲方线索", findings)
        self.assertIn(expected[1] + " · 乙方线索", findings)
        self.page.player_filter.setCurrentIndex(2)
        self.assertEqual({event["player_id"] for event in self.page.filtered_events}, {202})
        self.assertEqual(self.page.player_filter.toolTip(), expected[1])
        self.assertEqual([player["name"] for player in report["players"]], ["同名玩家", "同名玩家"],
                         "显示层不重写原始昵称")

    def test_timeline_and_selected_evidence_resolve_by_id_and_search_account_label(self):
        report = self.show_report()
        # 时间线原昵称可能已过时；显示关联始终以局内编号为准。
        report["timeline"][1]["player_name"] = "过时昵称"
        self.page._filter_events()
        self.assertEqual(self.page.timeline_table.item(1, 1).text(), "同名玩家 · 账号990002")
        self.page.timeline_table.selectRow(1)
        self.assertIn("同名玩家 · 账号990002", self.page.event_detail.text())
        self.assertIn("账号990002", self.page.event_detail.toolTip())
        self.page.keyword.setText("账号880001")
        self.assertEqual({event["player_id"] for event in self.page.filtered_events}, {101})
        self.assertEqual(self.page.timeline_table.rowCount(), 2)
        self.page._jump_evidence(report["timeline"][3])
        self.assertEqual(self.page.player_filter.currentData(), 202)
        event = self.page.timeline_table.item(self.page.timeline_table.currentRow(), 0).data(Qt.UserRole)
        self.assertEqual(event["player_id"], 202)
        self.assertEqual(event["offset"], report["timeline"][3]["offset"])

    def test_map_legend_filter_and_point_selection_keep_same_names_separate(self):
        self.show_report()
        view = self.page.map_view
        before = copy.deepcopy(view.model)
        self.assertEqual(view.names, {101: "同名玩家 · 账号880001", 202: "同名玩家 · 账号990002"})
        self.assertEqual([view.player_filter.itemData(index) for index in (1, 2)], [101, 202])
        self.assertEqual([name for name, _color in view.canvas.legend], list(view.names.values()))
        self.assertEqual(view.canvas.toolTip(), "")
        self.assertIn("账号880001", view.canvas.accessibleDescription())
        self.assertIn("账号990002", view.canvas.accessibleDescription())
        signal = QSignalSpy(view.eventSelected)
        event = next(event for event in view.events if event["player_id"] == 202)
        view.select_event(event)
        self.assertIn("同名玩家 · 账号990002", view.detail_heading.text())
        self.assertEqual(signal[-1][0]["player_id"], 202)
        view.player_filter.setCurrentIndex(1)
        self.assertEqual({event["player_id"] for event in view.layer.filtered_events}, {101})
        self.assertIsNone(view.layer.selected)
        self.assertEqual([name for name, _color in view.canvas.legend], [view.names[101]])
        self.assertEqual(view.canvas.toolTip(), "")
        self.assertNotIn("账号990002", view.canvas.accessibleDescription())
        self.assertEqual(view.model, before, "筛选和显示不修改原始资料")

    def test_long_legend_preserves_full_account_suffix_and_fits_small_window(self):
        self.show_report(same_name_replay(nickname="很长的相同玩家昵称" * 8))
        view = self.page.map_view
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(10)
        metrics = QFontMetrics(font)
        captions = [_legend_caption(metrics, label, 230) for label in view.names.values()]
        self.assertTrue(captions[0].endswith(" · 账号880001"))
        self.assertTrue(captions[1].endswith(" · 账号990002"))
        self.assertNotEqual(captions[0], captions[1])
        self.assertTrue(all(metrics.horizontalAdvance(label) <= 230 for label in captions))
        self.assertFalse(view.canvas.grab().isNull())
        self.assertLessEqual(view.width(), self.page.scroll.viewport().width())

    def test_missing_account_uses_local_id_and_missing_id_is_never_invented(self):
        self.show_report(same_name_replay(accounts=False))
        self.assertEqual(self.page.map_view.names, {101: "同名玩家 · 局内101", 202: "同名玩家 · 局内202"})
        self.assertEqual(self.page.player_filter.itemData(2), 202)
        parsed = same_name_replay()
        parsed["metadata"]["players"].append({"name": "同名玩家", "profile_id": 123456})
        parsed["commands"].append({"time": 25, "player_id": None, "type": "move", "target": {"x": 9, "z": 10}})
        report = analyze_replay(parsed)
        model = build_spatial_model(report)
        self.assertEqual({player["id"] for player in model["players"]}, {101, 202})
        self.assertNotIn(123456, {player["profile_id"] for player in model["players"]})
        self.assertEqual({event["player_id"] for event in model["events"]}, {101, 202})

    def test_public_import_chooses_exact_account_among_two_identical_names(self):
        path = Path(self.directory.name) / "public.rec"
        path.write_bytes(b"offline test fixture")
        with patch("replay_page.PublicReplayClient.fetch", return_value={"path": str(path), "cached": True,
                                                                         "profile_id": 990002, "game_id": 77}), \
                patch("replay_page.parse_replay", return_value=same_name_replay()), \
                patch("replay_page.MatchDetailClient.fetch", side_effect=MatchDetailError("离线验证")):
            self.page.import_public_match(990002, 77, {"name": "同名玩家"})
            for _ in range(200):
                if not self.page.busy:
                    break
                QTest.qWait(10)
            self.assertFalse(self.page.busy)
        self.assertEqual(self.page.report["focus_player_id"], 202)
        self.assertEqual(self.page.player_filter.currentData(), 202)
        self.assertEqual(self.page.player_filter.currentText(), "同名玩家 · 账号990002")
        self.assertEqual({event["player_id"] for event in self.page.filtered_events}, {202})


if __name__ == "__main__":
    unittest.main()
