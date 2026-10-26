"""失败诊断验证：临时目录、网络替身，不访问真实用户资料或官网。"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QCoreApplication, QEvent, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from match_detail import MatchDetailError
from public_replay import PublicReplayError
from replay_diagnostics import DIAGNOSTIC_FILENAME, record_public_replay_failure
from replay_page import ReplayPage
from replay_parser import ReplayError
from test_replay_page import fixture


def records(directory):
    path = Path(directory) / DIAGNOSTIC_FILENAME
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class ReplayDiagnosticStorageTests(unittest.TestCase):
    def test_retains_last_100_with_utc_and_required_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            for number in range(107):
                self.assertTrue(record_public_replay_failure(
                    directory, 10, number + 1, "download",
                    PublicReplayError("原始提示", reason="network_timeout")))
            rows = records(directory)
            self.assertEqual(len(rows), 100)
            self.assertEqual([row["game_id"] for row in rows], list(range(8, 108)))
            self.assertEqual(set(rows[-1]), {"timestamp_utc", "game_id", "profile_id", "stage",
                                             "reason", "http_status", "message"})
            self.assertEqual(rows[-1]["reason"], "network_timeout")
            moment = datetime.fromisoformat(rows[-1]["timestamp_utc"].replace("Z", "+00:00"))
            self.assertEqual(moment.utcoffset(), timedelta(0))

    def test_records_stable_reason_and_status_without_exception_body(self):
        secret = "Cookie: private; https://example.test/?token=secret C:\\Users\\private\\replay.rec\nTraceback"
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(record_public_replay_failure(
                directory, "10", "101", "download",
                PublicReplayError(secret, reason="permission", http_status=403)))
            row = records(directory)[0]
            self.assertEqual((row["profile_id"], row["game_id"], row["http_status"]), (10, 101, 403))
            self.assertEqual(row["reason"], "permission")
            text = (Path(directory) / DIAGNOSTIC_FILENAME).read_text(encoding="utf-8")
            for forbidden in ("Cookie", "https://", "token", "Users", "Traceback", "private"):
                self.assertNotIn(forbidden, text)

    def test_failed_atomic_replace_preserves_previous_records_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            error = PublicReplayError("超时", reason="network_timeout")
            record_public_replay_failure(directory, 10, 101, "download", error)
            path = Path(directory) / DIAGNOSTIC_FILENAME
            original = path.read_bytes()
            with patch("replay_diagnostics.os.replace", side_effect=PermissionError("不可写")):
                self.assertFalse(record_public_replay_failure(directory, 10, 102, "download", error))
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_invalid_replay_response_keeps_stable_content_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            record_public_replay_failure(directory, 10, 101, "download",
                                         PublicReplayError("原始网页正文", reason="invalid_content"))
            row = records(directory)[0]
            self.assertEqual(row["reason"], "invalid_content")
            self.assertNotIn("原始网页正文", row["message"])


class ReplayDiagnosticPageTests(unittest.TestCase):
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
        self.page = ReplayPage(self.directory)

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.temporary.cleanup()

    def wait_finished(self):
        deadline = time.monotonic() + 4
        while self.page.busy and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(self.page.busy, "后台任务应当结束")

    def test_public_download_failure_records_identity_and_keeps_original_notice(self):
        error = PublicReplayError("官网请求太频繁，请稍后重试。", reason="rate_limited", http_status=429)
        with patch("replay_page.PublicReplayClient") as client, patch("replay_page.parse_replay") as parse:
            client.return_value.fetch_match.side_effect = error
            self.page.import_public_match(10, 101)
            self.wait_finished()
        row = records(self.directory)[0]
        self.assertEqual((row["stage"], row["reason"], row["http_status"]), ("download", "rate_limited", 429))
        self.assertEqual((row["profile_id"], row["game_id"]), (10, 101))
        self.assertEqual(self.page.notice.text(), str(error))
        parse.assert_not_called()

    def test_parse_failure_keeps_download_and_logs_requested_not_fallback_identity(self):
        downloaded = self.directory / "已下载录像.aoe4replay"
        downloaded.write_bytes(b"synthetic retained replay")
        with patch("replay_page.PublicReplayClient") as client, \
                patch("replay_page.parse_replay", side_effect=ReplayError("暂不支持 C:\\private\\file.rec")):
            client.return_value.fetch_match.return_value = {
                "path": downloaded, "cached": True, "profile_id": 20,
                "requested_profile_id": 10, "download_profile_id": 20, "game_id": 101}
            self.page.import_public_match(10, 101)
            self.wait_finished()
        row = records(self.directory)[0]
        self.assertEqual((row["stage"], row["reason"], row["profile_id"]), ("parse", "parse_error", 10))
        self.assertEqual(downloaded.read_bytes(), b"synthetic retained replay")
        self.assertIn("录像已下载，但暂时无法解析", self.page.notice.text())
        self.assertNotIn("private", json.dumps(row))

    def test_log_storage_failure_does_not_replace_public_download_error(self):
        error = PublicReplayError("原本的下载超时提示。", reason="network_timeout")
        with patch("replay_page.PublicReplayClient") as client, \
                patch("replay_diagnostics.os.replace", side_effect=PermissionError("日志保存失败")):
            client.return_value.fetch_match.side_effect = error
            self.page.import_public_match(10, 101)
            self.wait_finished()
        self.assertEqual(self.page.notice.text(), str(error))
        self.assertFalse((self.directory / DIAGNOSTIC_FILENAME).exists())

    def test_success_local_failure_and_cancel_do_not_record_public_failure(self):
        downloaded = self.directory / "合成录像.gz"
        downloaded.write_bytes(b"synthetic replay")
        with patch("replay_page.PublicReplayClient") as client, \
                patch("replay_page.parse_replay", return_value=fixture()), \
                patch("replay_page.MatchDetailClient.fetch", side_effect=MatchDetailError("暂无详情")):
            client.return_value.fetch_match.return_value = {
                "path": downloaded, "cached": False, "profile_id": 10, "game_id": 101}
            self.page.import_public_match(10, 101)
            self.wait_finished()
        self.assertIn("已下载并分析", self.page.notice.text())
        with patch("replay_page.parse_replay", side_effect=ReplayError("本地文件无法解析")):
            self.page.import_replay(downloaded)
            self.wait_finished()
        with patch("replay_page.QFileDialog.getOpenFileName", return_value=("", "")):
            self.page.choose_replay()
        self.assertFalse((self.directory / DIAGNOSTIC_FILENAME).exists())


if __name__ == "__main__":
    unittest.main()
