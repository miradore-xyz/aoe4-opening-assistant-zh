"""切换公开玩家的真实Qt回归；资料和头像均为合成数据，隔离目录且不联网。"""
import os
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import sip
from PyQt5.QtCore import QCoreApplication, QEvent, QTimer, Qt
from PyQt5.QtGui import QColor, QFont, QFontDatabase, QImage
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel

from analysis_page import AnalysisPage
import player_avatars
from visual import STYLE


def profile_snapshot(number, name, **details):
    """两个不同长短昵称和编号，避免把游戏等级与排位数据混用。"""
    return {"profile_id": number, "profile": {"profile_id": number, "name": name, **details},
            "games": [], "source": "cache", "fetched_at": "2026-10-25T16:00:00+00:00", "warning": ""}


def candidates():
    return [{"profile_id": 7000000 + index, "name": "同名玩家" + str(index),
             "game_level": 225 - index, "country": "cn", "platform": "steam"} for index in range(12)]


class ProfileSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.quit_policy = cls.application.quitOnLastWindowClosed()
        cls.application.setQuitOnLastWindowClosed(False)
        QTimer.singleShot(0, cls.application.quit)
        cls.application.exec_()
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            path = Path("C:/Windows/Fonts") / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
        cls.application.setFont(QFont("Microsoft YaHei UI", 10))
        cls.application.setStyleSheet(STYLE)

    @classmethod
    def tearDownClass(cls):
        cls.application.setQuitOnLastWindowClosed(cls.quit_policy)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.page = AnalysisPage(self.directory.name)
        self.page.resize(760, 650)
        self.page.show()
        self.old = profile_snapshot(7, "第一位玩家的较长昵称ABCDEFG", game_level=225, country="cn", platform="steam")
        self.new = profile_snapshot(8, "乙", game_level=15, country="de", platform="xbox")

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()
        self.directory.cleanup()

    def wait_until(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate(), "隔离的后台响应没有按时完成")

    def test_two_same_name_accounts_remain_in_history_and_restore_the_chosen_id(self):
        self.page._add_history("同名玩家", 7, "同名玩家")
        self.page._add_history("同名玩家", 8, "同名玩家")
        self.assertEqual([entry["profile_id"] for entry in self.page.history], [8, 7])
        self.assertIn("8", self.page.history_combo.itemText(0))
        self.assertIn("7", self.page.history_combo.itemText(1))
        for index, wanted in ((0, 8), (1, 7)):
            with patch.object(self.page, "_job") as job, patch.object(self.page.client, "fetch_profile") as fetch:
                self.page._use_history(index)
                job.call_args.args[0]()
                fetch.assert_called_once_with(wanted)

    def test_deleting_other_same_name_history_does_not_clear_the_selected_account(self):
        self.page._add_history("同名玩家", 7, "同名玩家")
        self.page._add_history("同名玩家", 8, "同名玩家")
        self.page.query.setText("同名玩家")
        self.page._save_query(8)
        self.page.history_combo.setCurrentIndex(1)
        self.page._delete_history()
        saved = json.loads((Path(self.directory.name) / "战绩查询.json").read_text("utf-8"))
        self.assertEqual(saved["profile_id"], 8)
        self.assertEqual([entry["profile_id"] for entry in self.page.history], [8])

    def test_replacing_snapshot_removes_nested_header_labels_before_next_paint(self):
        self.page._show_snapshot(self.old)
        QTest.qWait(20)
        old_name, old_meta, old_avatar = self.page.profile_header_name, self.page.profile_meta, self.page.profile_avatar
        self.assertTrue(old_name.isVisible())
        self.page._show_snapshot(self.new)
        # 删除前就退出可见树，不能等下一轮事件循环继续叠画。
        self.assertTrue(old_name.isHidden())
        self.assertTrue(old_meta.isHidden())
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()
        self.assertTrue(sip.isdeleted(old_name))
        self.assertTrue(sip.isdeleted(old_meta))
        self.assertTrue(sip.isdeleted(old_avatar))
        self.assertEqual(self.page.profile_header_name.text(), "乙")
        self.assertIn("编号 8", self.page.profile_meta.text())
        self.assertNotIn(self.old["profile"]["name"], [label.text() for label in self.page.body.findChildren(QLabel)])

    def test_same_player_refresh_does_not_leave_any_old_header(self):
        previous = []
        for level, name in ((225, "同一玩家的长昵称"), (8, "短名"), (3, "末次")):
            self.page._show_snapshot(profile_snapshot(7, name, game_level=level))
            previous.append((self.page.profile_header_name, self.page.profile_meta))
            QTest.qWait(10)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()
        self.assertTrue(all(sip.isdeleted(label) for group in previous[:-1] for label in group))
        self.assertEqual(self.page.profile_header_name.text(), "末次")
        self.assertIn("等级 3", self.page.profile_meta.text())

    def test_identity_responses_in_reverse_order_cannot_rewrite_current_player(self):
        entered, release = threading.Event(), threading.Event()

        def fetch(number, **kwargs):
            if number == 7:
                entered.set()
                release.wait(2)
            return {"profile_id": number, "game_level": 77 if number == 7 else 18,
                    "country": "cn" if number == 7 else "de"}

        with patch.object(self.page.identity_client, "fetch_identity", side_effect=fetch):
            try:
                self.page._show_snapshot(self.old)
                self.page._start_identities([7], include_avatar=True)
                self.assertTrue(entered.wait(1))
                # 同一轮搜索中选择另一候选，编号保护仍必须生效。
                self.page._show_snapshot(self.new)
                self.page._start_identities([8], include_avatar=True)
                self.wait_until(lambda: "等级 18" in self.page.profile_meta.text())
                release.set()
                QTest.qWait(60)
                self.assertEqual(self.page.profile_header_name.text(), "乙")
                self.assertIn("编号 8", self.page.profile_meta.text())
                self.assertIn("等级 18", self.page.profile_meta.text())
                self.assertNotIn("77", self.page.profile_meta.text())
                self.assertEqual(self.page.profile_avatar.profile_id, 8)
            finally:
                release.set()

    def test_late_avatar_of_deleted_header_cannot_overlay_the_new_avatar(self):
        entered, release = threading.Event(), threading.Event()
        urls = {7: "https://avatars.steamstatic.com/synthetic-profile-a.jpg",
                8: "https://avatars.steamstatic.com/synthetic-profile-b.jpg"}

        def download(url, path):
            if url == urls[7]:
                entered.set()
                release.wait(2)
            image = QImage(48, 48, QImage.Format_ARGB32)
            image.fill(QColor("#e04030" if url == urls[7] else "#3060e0"))
            return player_avatars._png_bytes(image)

        with patch("player_avatars._download_avatar", side_effect=download):
            try:
                self.page._show_snapshot(self.old)
                old_avatar = self.page.profile_avatar
                self.page._identities_ready([{"profile_id": 7, "avatars": {"medium": urls[7]}}], 0)
                self.assertTrue(entered.wait(1))
                self.page._show_snapshot(self.new)
                self.page._identities_ready([{"profile_id": 8, "avatars": {"medium": urls[8]}}], 0)
                self.wait_until(lambda: not self.page.profile_avatar._loading)
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                self.assertTrue(sip.isdeleted(old_avatar))
                release.set()
                self.wait_until(lambda: not player_avatars._pending)
                QTest.qWait(30)
                self.assertEqual(self.page.profile_avatar.profile_id, 8)
                self.assertEqual(self.page.profile_avatar.pixmap().toImage().pixelColor(20, 20).name(), "#3060e0")
                self.assertEqual(self.page.profile_header_name.text(), "乙")
            finally:
                release.set()
                self.wait_until(lambda: not player_avatars._pending)

    def test_candidate_grid_columns_and_complete_identity_follow_available_width(self):
        with patch.object(self.page, "_start_identities"):
            self.page._job_result(candidates(), "search")
            for width, expected_columns in ((760, 2), (1100, 3)):
                with self.subTest(width=width):
                    self.page.resize(width, 650)
                    QTest.qWait(30)
                    choices = self.page.player_choices
                    rects = [choices.visualItemRect(choices.item(index)) for index in range(choices.count())]
                    first_row = [rect for rect in rects if rect.top() == rects[0].top()]
                    self.assertEqual(len(first_row), expected_columns)
                    for left, right in zip(first_row, first_row[1:]):
                        self.assertLess(left.right(), right.left())
                    for index, rect in enumerate(rects[:expected_columns * 3]):
                        self.assertTrue(choices.viewport().rect().contains(rect))
                        widget = choices.itemWidget(choices.item(index))
                        self.assertIn(str(7000000 + index), widget.details.text())
                        self.assertIn("中国", widget.details.text())
                        self.assertIn("等级", widget.details.text())
                        self.assertGreaterEqual(widget.details.height(), widget.details.heightForWidth(widget.details.width()))
            self.page.player_choices.setFixedWidth(420)
            QTest.qWait(20)
            choices = self.page.player_choices
            first, second = [choices.visualItemRect(choices.item(index)) for index in (0, 1)]
            self.assertEqual(first.left(), second.left())
            self.assertLess(first.bottom(), second.top())

    def test_candidate_keyboard_arrows_and_enter_select_the_exact_account(self):
        with patch.object(self.page, "_start_identities"), patch.object(self.page, "_job") as job, \
                patch.object(self.page.client, "fetch_profile") as fetch:
            self.page._job_result(candidates(), "search")
            QTest.qWait(30)
            choices = self.page.player_choices
            choices.setFocus()
            QTest.keyClick(choices, Qt.Key_Right)
            self.assertEqual(choices.currentRow(), 1)
            QTest.keyClick(choices, Qt.Key_Down)
            self.assertEqual(choices.currentRow(), 3)
            QTest.keyClick(choices, Qt.Key_Left)
            self.assertEqual(choices.currentRow(), 2)
            QTest.keyClick(choices, Qt.Key_Return)
            self.assertEqual(job.call_count, 1)
            job.call_args.args[0]()
            fetch.assert_called_once_with(7000002)

    def test_query_is_explicit_serial_and_clears_old_candidates_before_loading(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def search(query):
            calls.append(query)
            if query == "玩家甲":
                entered.set()
                release.wait(2)
            return [{"profile_id": 7 if query == "玩家甲" else 8, "name": query, "game_level": 18}]

        with patch.object(self.page.client, "search_players", side_effect=search), \
                patch.object(self.page, "_start_identities"):
            try:
                self.page._job_result(candidates(), "search")
                self.page.query.setText("玩家甲")
                self.assertEqual(calls, [], "修改文本不应逐字请求")
                self.page.lookup()
                self.assertTrue(entered.wait(1))
                first_generation = self.page._identity_generation
                self.assertTrue(self.page.busy)
                self.assertEqual(self.page.player_choices.count(), 0)
                self.assertTrue(self.page.player_choices.isHidden())
                self.assertFalse(self.page.query_button.isEnabled())
                self.assertIn("正在搜索", self.page.notice.text())
                self.page.query.setText("玩家乙")
                self.page.lookup()
                self.assertEqual(calls, ["玩家甲"], "忙碌期间不叠加第二个战绩请求")
                release.set()
                self.wait_until(lambda: not self.page.busy)
                self.page.lookup()
                self.wait_until(lambda: not self.page.busy)
                self.assertEqual(calls, ["玩家甲", "玩家乙"])
                self.assertEqual(self.page.player_choices.count(), 1)
                self.assertEqual(self.page.player_choices.item(0).data(Qt.UserRole), 8)
                # 旧查询范围内迟到的身份结果，即使编号相同也不能覆盖新一轮资料。
                self.page._identities_ready([{"profile_id": 8, "game_level": 999}], first_generation)
                widget = self.page.player_choices.itemWidget(self.page.player_choices.item(0))
                self.assertIn("等级 18", widget.details.text())
                self.assertNotIn("999", widget.details.text())
            finally:
                release.set()
                self.wait_until(lambda: not self.page.busy)


if __name__ == "__main__":
    unittest.main()
