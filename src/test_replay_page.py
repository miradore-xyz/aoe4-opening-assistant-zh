"""本地复盘界面验证：合成记录、隔离目录，不联网、不读取真实玩家资料。

fixture=测试样例，pagination=分页，responsive=界面可响应。
"""
from __future__ import annotations

import os
import gzip
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt, QPoint, QPointF, QTimer
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QAbstractScrollArea

from replay_analysis import analyze_replay
from replay_page import ReplayPage, _civilization_name
from replay_parser import ReplayError
from public_replay import PublicReplayError
from public_replay import PublicReplayClient
from match_detail import MatchDetailError, normalize_summary
from test_match_detail import summary as detail_fixture
from visual import STYLE


def fixture():
    """虚构两名玩家并保留未知类型，验证界面不会把未知数据当作已知。"""
    commands = []
    for index in range(30):
        commands.append({"time": index * 10, "tick": index * 80, "offset": 100 + index,
                         "player_id": 1001 if index % 2 == 0 else 1002,
                         "type": "move" if index % 3 else "queue_unit", "known": True,
                         "type_id": 62 if index % 3 else 3,
                         "entity_ids": [3333, 4444], "unit_type_id": 5555,
                         "target": {"x": 10, "y": 20}})
    commands.append({"time": None, "tick": None, "offset": 999, "player_id": 1002,
                     "type": "unknown", "type_id": 888, "known": False})
    return {"schema_version": 1,
            "source": {"name": "合成界面验证.rec", "sha256": "a" * 64, "format": "synthetic", "size": 1234},
            "metadata": {"map": "Dry Arabia", "duration": 300,
                         "players": [{"id": 1001, "name": "<b>合成甲</b>", "civilization": "english", "team": 1},
                                     {"id": 1002, "name": "合成乙", "civilization": "mongols", "team": 2}]},
            "commands": commands, "events": [], "resources": [],
            "coverage": {"commands_total": 31, "commands_decoded": 30, "unknown_types": {"888": 1},
                         "ticks_per_second": 8, "warnings": ["合成验证保留未知指令。"]}}


class ReplayPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.quit_policy = cls.app.quitOnLastWindowClosed()
        cls.app.setQuitOnLastWindowClosed(False)
        QTimer.singleShot(0, cls.app.quit)
        cls.app.exec_()

    @classmethod
    def tearDownClass(cls):
        cls.app.setQuitOnLastWindowClosed(cls.quit_policy)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.app.setStyleSheet(STYLE)
        self.page = ReplayPage(self.directory)
        self.detail_fetch = patch("replay_page.MatchDetailClient.fetch", side_effect=MatchDetailError("暂无详细统计。"))
        self.detail_fetch.start()

    def tearDown(self):
        self.detail_fetch.stop()
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()
        self.temporary.cleanup()

    def wait_finished(self):
        deadline = time.monotonic() + 4
        while self.page.busy and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(self.page.busy, "后台处理应当结束")

    def test_startup_and_cancel_do_not_read_replay_or_contact_network(self):
        self.assertIsNone(self.page.report)
        self.assertFalse((self.directory / "本地复盘").exists())
        with patch("replay_page.QFileDialog.getOpenFileName", return_value=("", "")), \
                patch("replay_page.parse_replay") as parse:
            self.page.choose_replay()
            parse.assert_not_called()
        self.assertIsNone(self.page.report)
        self.assertFalse(self.page.busy)

    def test_import_reopen_delete_keeps_source_and_deduplicates(self):
        original = self.directory / "原录像.rec"
        original.write_bytes(b"synthetic source")
        with patch("replay_page.parse_replay", return_value=fixture()):
            self.page.import_path(original)
            self.wait_finished()
            report_id = self.page.report_id
            self.page.import_path(original)
            self.wait_finished()
        self.assertEqual(self.page.archive.count(), 1)
        self.assertEqual(self.page.report_id, report_id)
        self.assertEqual(self.page.players_table.rowCount(), 2)
        self.assertEqual(self.page.players_table.item(0, 0).text(), "<b>合成甲</b>")
        restored = ReplayPage(self.directory)
        try:
            self.assertIsNone(restored.report, "启动不自动打开私人录像")
            restored.open_archive()
            deadline = time.monotonic() + 4
            while restored.busy and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertEqual(restored.report_id, report_id)
            restored.delete_archive()
            self.assertIsNone(restored.report)
            self.assertEqual(restored.archive.count(), 0)
            self.assertEqual(original.read_bytes(), b"synthetic source")
        finally:
            restored.close()
            restored.deleteLater()
            self.app.processEvents()

    def test_public_match_download_and_cache_analyze_without_manual_file_steps(self):
        """下载与缓存都直接生成报告；第二次使用缓存不会产生重复的归档。"""
        downloaded = self.directory / "公开回放.gz"
        downloaded.write_bytes(b"synthetic public replay")
        replies = [{"path": downloaded, "cached": cached, "profile_id": 5497834, "game_id": 257256901}
                   for cached in (False, True)]
        with patch("replay_page.PublicReplayClient") as client, \
                patch("replay_page.parse_replay", side_effect=lambda path: fixture()) as parse:
            client.return_value.fetch_match.side_effect = replies
            self.page.import_public_match(5497834, 257256901, {"map": "Flankwoods", "duration": 1391})
            self.wait_finished()
            self.assertIn("已下载并分析", self.page.notice.text())
            self.assertEqual(self.page.players_table.rowCount(), 2)
            self.assertEqual(self.page.report["source"]["origin"], "aoe4world-public-replay")
            self.assertEqual(self.page.report["source"]["game_id"], 257256901)
            self.assertEqual(self.page.report["source"]["profile_id"], 5497834)
            self.assertEqual(self.page.report["metadata"]["map"], "Dry Arabia", "网站信息不得覆盖录像已知地图")
            self.assertEqual(self.page.report["metadata"]["duration"], 300, "网站时长不得覆盖文件记录时长")
            self.assertIsNone(self.page.report["focus_player_id"], "缺少账号映射时保持所有玩家，不按姓名猜测")
            self.assertIsNone(self.page.player_filter.currentData())
            original_id = self.page.report_id
            self.page.import_public_match(5497834, 257256901)
            self.wait_finished()
            self.assertIn("已从缓存分析", self.page.notice.text())
            self.assertEqual(self.page.archive.count(), 1)
            self.assertEqual(self.page.report_id, original_id)
            self.assertEqual(self.page.library.load(original_id)["source"]["game_id"], 257256901)
            self.assertEqual(parse.call_count, 2)
            self.assertEqual(client.return_value.fetch_match.call_count, 2)
            parse.assert_called_with(downloaded)

    def test_public_detail_charts_reopen_from_cache_without_network(self):
        """主动获取后保留详细图表；再次打开报告只读取本地详情。"""
        from match_detail_client import MatchDetailClient
        detail = MatchDetailClient(self.directory).store_summary(detail_fixture(), 10, 101)
        downloaded = self.directory / "公开回放.gz"
        downloaded.write_bytes(b"synthetic public replay")
        with patch("replay_page.PublicReplayClient") as client, \
                patch("replay_page.parse_replay", return_value=fixture()), \
                patch("replay_page.MatchDetailClient.fetch", return_value=detail) as fetched:
            client.return_value.fetch_match.return_value = {"path": downloaded, "cached": True, "profile_id": 10, "game_id": 101}
            self.page.import_public_match(10, 101)
            self.wait_finished()
            self.assertIsNotNone(self.page.detail_view)
            self.assertTrue(self.page.raw_report.isHidden())
            self.page.raw_toggle.click()
            self.assertFalse(self.page.raw_report.isHidden())
            self.page.open_archive()
            self.wait_finished()
            self.assertEqual(fetched.call_count, 1)
            self.assertEqual(self.page.match_detail["game_id"], 101)
            self.assertIsNotNone(self.page.detail_view)

    def test_fallback_download_owner_does_not_change_focus_or_reopened_detail_identity(self):
        """同名双方中借用另一公开入口时，原关注账号及归档详情不能变成来源账号。"""
        from match_detail_client import MatchDetailClient
        detail = MatchDetailClient(self.directory).store_summary(detail_fixture(), 10, 101)
        parsed = fixture()
        for player, account in zip(parsed["metadata"]["players"], (10, 20)):
            player.update(name="同名玩家", profile_id=account)
        detail_response = {"statusCode": 0, "matchSummary": {"matchId": "101"}, "playerList": [
            {"userId": 10, "profileId": 0, "isHuman": True, "userName": "同名玩家", "matchReplayAvailable": False},
            {"userId": 20, "profileId": 0, "isHuman": True, "userName": "同名玩家", "matchReplayAvailable": True}]}
        responses = [HTTPError("https://api.ageofempires.com/", 404, "missing", None, None),
                     json.dumps(detail_response).encode("utf-8"), gzip.compress(b"synthetic replay")]
        requests = []

        def open_response(request, timeout):
            requests.append(request)
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return io.BytesIO(response)

        client = PublicReplayClient(self.directory, opener=open_response)
        with patch("replay_page.PublicReplayClient", return_value=client), \
                patch("replay_page.parse_replay", return_value=parsed), \
                patch("replay_page.MatchDetailClient.fetch", return_value=detail) as fetched:
            self.page.import_public_match(10, 101)
            self.wait_finished()
            self.assertEqual(len(requests), 3)
            self.assertEqual(requests[1].get_method(), "POST")
            self.assertTrue(requests[2].full_url.endswith("profileId=20"))
            self.assertTrue(client.fetch(20, 101)["cached"])
            fetched.assert_called_once_with(10, 101)
            self.assertEqual(self.page.report["focus_player_id"], 1001)
            self.assertEqual(self.page.player_filter.currentData(), 1001)
            report_id = self.page.report_id
            saved = self.page.library.load(report_id)
            self.assertEqual(saved["source"]["profile_id"], 10)
            self.assertEqual(saved["source"]["download_profile_id"], 20)
            self.page.open_archive()
            self.wait_finished()
            self.assertEqual(self.page.report_id, report_id)
            self.assertEqual(self.page.report["focus_player_id"], 1001)
            self.assertEqual(self.page.match_detail["focus_profile_id"], 10)
            self.assertIsNotNone(self.page.detail_view)
            fetched.assert_called_once_with(10, 101)
            self.assertEqual(len(requests), 3, "归档重开只读本地详情和报告，不重新下载或核对官网")

    def test_public_match_failure_keeps_report_and_rejects_duplicate_requests(self):
        """后台获取失败不清空当前报告，也不接受连续重复下载。"""
        report = analyze_replay(fixture())
        self.page._show_report(report)
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(10)

        def delayed(*args):
            time.sleep(.15)
            raise PublicReplayError("这场对局尚未提供公开回放。")

        with patch("replay_page.PublicReplayClient") as client, patch("replay_page.parse_replay") as parse:
            client.return_value.fetch_match.side_effect = delayed
            self.page.import_public_match(5497834, 257256901)
            self.assertTrue(self.page.busy)
            self.assertFalse(self.page.import_button.isEnabled())
            self.assertIn("正在获取", self.page.notice.text())
            self.page.import_public_match(5497834, 257256901)
            self.wait_finished()
            client.return_value.fetch_match.assert_called_once_with(5497834, 257256901)
            parse.assert_not_called()
        timer.stop()
        self.assertGreater(len(ticks), 3)
        self.assertIs(self.page.report, report)
        self.assertEqual(self.page.notice.text(), "这场对局尚未提供公开回放。")
        self.assertTrue(self.page.import_button.isEnabled())

    def test_downloaded_replay_parse_failure_keeps_cache_and_previous_report(self):
        """真实下载器保存缓存后，解析失败不能改报下载失败或删除可重用文件。"""
        previous = analyze_replay(fixture())
        self.page._show_report(previous)
        body = gzip.compress(b"unsupported replay test")
        client = PublicReplayClient(self.directory)
        requests = []

        def open_response(request, timeout):
            requests.append(request.full_url)
            return io.BytesIO(body)

        client.opener = open_response
        with patch("replay_page.PublicReplayClient", return_value=client), \
                patch("replay_page.parse_replay", side_effect=ReplayError("暂不支持此录像版本")), \
                patch("replay_page.MatchDetailClient.fetch") as detail:
            for _ in range(2):
                self.page.import_public_match(5497834, 257256901)
                self.wait_finished()
                self.assertIn("录像已下载，但暂时无法解析", self.page.notice.text())
                self.assertIn("本地录像已保留", self.page.notice.text())
                self.assertIs(self.page.report, previous)
                self.assertTrue(self.page.import_button.isEnabled())
            detail.assert_not_called()
        self.assertEqual(len(requests), 1, "重新解析复用已下载缓存，不重复请求")
        cached = client.fetch(5497834, 257256901)
        self.assertTrue(cached["cached"])
        self.assertEqual(cached["path"].read_bytes(), body)
        self.assertEqual(self.page.archive.count(), 0, "解析失败不生成虚构报告")

    def test_public_match_selects_player_only_with_unique_account_mapping(self):
        """账号编号精确映射可选中本人；同名或重复编号不会被猜作本人。"""
        parsed = fixture()
        parsed["metadata"]["players"][0]["name"] = "Miradore"
        parsed["metadata"]["players"][1]["profile_id"] = 5497834
        parsed["metadata"].update(map=None, duration=None)
        downloaded = {"path": self.directory / "公开回放.gz", "cached": False,
                      "profile_id": 5497834, "game_id": 257256901}
        with patch("replay_page.PublicReplayClient") as client, \
                patch("replay_page.parse_replay", return_value=parsed):
            client.return_value.fetch_match.return_value = downloaded
            self.page.import_public_match(5497834, 257256901, {"name": "Miradore", "map": "Flankwoods", "duration": 1391})
            self.wait_finished()
        self.assertEqual(self.page.report["focus_player_id"], 1002)
        self.assertEqual(self.page.player_filter.currentData(), 1002)
        self.assertEqual(self.page.report["metadata"]["map"], "Flankwoods")
        self.assertEqual(self.page.report["metadata"]["duration"], 1391)
        self.assertEqual(self.page.report["metadata"]["duration_source"], "public_match")
        parsed = fixture()
        for player in parsed["metadata"]["players"]:
            player["profile_id"] = 5497834
        with patch("replay_page.PublicReplayClient") as client, \
                patch("replay_page.parse_replay", return_value=parsed):
            client.return_value.fetch_match.return_value = downloaded
            self.page.import_public_match(5497834, 257256901)
            self.wait_finished()
        self.assertIsNone(self.page.report["focus_player_id"])
        self.assertIsNone(self.page.player_filter.currentData())

    def test_filters_pagination_and_unknown_time_are_preserved(self):
        self.page._show_report(analyze_replay(fixture()))
        self.assertEqual(self.page.timeline_table.rowCount(), self.page.PAGE_SIZE)
        self.assertTrue(self.page.next_button.isEnabled())
        self.page.next_button.click()
        self.assertEqual(self.page.page_index, 1)
        self.page.player_filter.setCurrentIndex(self.page.player_filter.findData(1002))
        self.assertEqual(self.page.page_index, 0)
        self.assertEqual(len(self.page.filtered_events), 16)
        self.page.category_filter.setCurrentIndex(self.page.category_filter.findData("unknown"))
        self.assertEqual(self.page.timeline_table.rowCount(), 1)
        self.assertEqual(self.page.timeline_table.item(0, 0).text(), "时间未知")
        self.assertEqual(self.page.timeline_table.item(0, 2).text(), "未识别指令")
        self.assertIn("888", self.page.timeline_table.item(0, 2).toolTip())
        self.page.keyword.setText("不会出现的内容")
        self.assertEqual(self.page.timeline_table.rowCount(), 0)
        self.assertFalse(self.page.next_button.isEnabled())
        self.assertIn("没有符合", self.page.event_count.text())

    def test_default_details_hide_internal_identifiers_and_evidence_jump_works(self):
        report = analyze_replay(fixture())
        self.page._show_report(report)
        detail = self.page.timeline_table.item(0, 3)
        self.assertNotIn("3333", detail.text())
        self.assertNotIn("5555", detail.text())
        self.assertIn("5555", detail.toolTip())
        target = report["timeline"][26]
        self.page._jump_evidence(target)
        self.assertEqual(self.page.player_filter.currentData(), target["player_id"])
        selected = self.page.timeline_table.item(self.page.timeline_table.currentRow(), 0).data(Qt.UserRole)
        self.assertEqual(selected["offset"], target["offset"])

    def test_coverage_separates_type_recognition_from_parameters_and_exact_civ_aliases(self):
        parsed = fixture()
        parsed["coverage"].update(commands_identified=31, commands_decoded=5)
        self.page._show_report(analyze_replay(parsed))
        self.assertIn("类型识别 30 / 31", self.page.coverage_label.text())
        self.assertNotIn("5 /", self.page.coverage_label.text())
        self.assertIn("参数完整解析 5", self.page.scope_detail.text())
        self.assertEqual(_civilization_name("chinese_ha_01"), "朱熹的传承")
        self.assertEqual(_civilization_name("hre_ha_01"), "龙之骑士团")
        self.assertEqual(_civilization_name("sultanate_ha_tug"), "图格鲁克王朝")
        self.assertEqual(_civilization_name("unknown_chinese_variant"), "未确认")

    def test_failure_does_not_erase_report_and_worker_keeps_event_loop_responsive(self):
        report = analyze_replay(fixture())
        self.page._show_report(report)
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(10)
        def delayed(path):
            time.sleep(.15)
            raise ReplayError("合成损坏录像")
        with patch("replay_page.parse_replay", side_effect=delayed):
            self.page.import_path("合成录像.rec")
            self.assertTrue(self.page.busy)
            self.wait_finished()
        timer.stop()
        self.assertGreater(len(ticks), 3)
        self.assertIs(self.page.report, report)
        self.assertIn("合成损坏", self.page.notice.text())
        self.assertTrue(self.page.import_button.isEnabled())

    def test_small_layout_and_all_scrollbars_are_hidden(self):
        self.page._show_report(analyze_replay(fixture()))
        self.page.raw_toggle.click()
        self.page.resize(760, 650)
        self.page.show()
        self.app.processEvents()
        self.assertLessEqual(self.page.body.minimumSizeHint().width(), 760)
        for area in self.page.findChildren(QAbstractScrollArea):
            self.assertEqual(area.verticalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
            self.assertEqual(area.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        self.assertTrue(self.page.legacy_tools.isHidden())
        self.assertFalse(self.page.import_button.isVisible())
        scrollbar = self.page.scroll.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        scrollbar.setValue(0)
        viewport = self.page.scroll.viewport()
        position = QPointF(100, 100)
        wheel = QWheelEvent(position, QPointF(viewport.mapToGlobal(QPoint(100, 100))),
                            QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                            Qt.ScrollUpdate, False)
        self.app.sendEvent(viewport, wheel)
        self.assertGreater(scrollbar.value(), 0, "隐藏滚动条时滚轮仍应能浏览后续内容")

    def test_destroying_page_does_not_wait_for_worker_or_raise_thread_error(self):
        errors = []
        def delayed(path):
            time.sleep(.15)
            raise ReplayError("结束时的合成错误")
        with patch("replay_page.parse_replay", side_effect=delayed), \
                patch.object(threading, "excepthook", side_effect=errors.append):
            self.page.import_path("合成录像.rec")
            started = time.monotonic()
            self.page.close()
            self.page.deleteLater()
            self.assertLess(time.monotonic() - started, .1)
            QTest.qWait(250)
            self.assertEqual(errors, [])
        # tearDown（测试结束清理）需要一个未销毁的界面对象。
        self.page = ReplayPage(self.directory)

    def test_player_query_is_direct_and_has_no_nested_replay_navigation(self):
        from analysis_page import AnalysisPage
        page = AnalysisPage(self.directory)
        try:
            from PyQt5.QtWidgets import QLabel
            headings = [label.text() for label in page.findChildren(QLabel) if label.objectName() == "PageTitle"]
            self.assertEqual(headings, ["玩家查询"])
            self.assertFalse(hasattr(page, "section_buttons"))
            self.assertFalse(hasattr(page, "replay_page"))
        finally:
            page.close()
            page.deleteLater()


if __name__ == "__main__":
    unittest.main()
