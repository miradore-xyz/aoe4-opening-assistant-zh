"""同名搜索以头像、游戏等级和唯一编号区分，不借用排位段位。"""
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from analysis_page import AnalysisPage, _identity_text, _merge_identity
from test_match_view_alignment import snapshot


class PlayerIdentityViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.page = AnalysisPage(self.directory.name)
        self.page.resize(760, 640)
        self.page.show()

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        QTest.qWait(1)
        self.directory.cleanup()

    def test_duplicate_names_show_separate_levels_and_choose_exact_id(self):
        players = [{"profile_id": number, "name": "同名玩家", "country": "cn"} for number in (7, 8)]
        with patch.object(self.page, "_start_identities"):
            self.page._job_result(players, "search")
            self.page._identities_ready([{**player, "game_level": level} for player, level in zip(players, (22, 40))], 0)
            first, second = [self.page.player_choices.item(index) for index in (0, 1)]
            a, b = [self.page.player_choices.itemWidget(item) for item in (first, second)]
            self.assertEqual(a.name.text(), b.name.text())
            self.assertIn("编号 7", a.details.text())
            self.assertIn("等级 22", a.details.text())
            self.assertIn("编号 8", b.details.text())
            self.assertIn("等级 40", b.details.text())
            with patch.object(self.page, "_job") as job, patch.object(self.page.client, "fetch_profile") as fetch:
                self.page._choose_player(second)
                job.call_args.args[0]()
                fetch.assert_called_once_with(8)

    def test_late_search_cannot_change_current_candidate(self):
        with patch.object(self.page, "_start_identities"):
            self.page._job_result([{"profile_id": 7, "name": "同名玩家"}], "search")
        self.page._identity_generation = 3
        self.page._identities_ready([{"profile_id": 7, "game_level": 99}], 2)
        row = self.page.player_choices.itemWidget(self.page.player_choices.item(0))
        self.assertIn("等级未提供", row.details.text())

    def test_cached_header_has_no_initial_letter_and_keeps_match_modes(self):
        data = snapshot()
        data["profile"]["modes"] = {"rm_team": {"rating": 600}}
        identity = {"profile_id": 7, "name": "不同昵称", "game_level": 22, "modes": {}, "country": "cn"}
        with patch.object(self.page.identity_client, "load_cached", return_value=identity), \
                patch.object(self.page, "_start_identities") as network:
            self.page._show_snapshot(data)
            network.assert_not_called()
        self.assertEqual(self.page.profile_avatar.text(), "")
        self.assertIn("等级 22", self.page.profile_meta.text())
        merged = _merge_identity(data["profile"], identity)
        self.assertEqual(merged["modes"], data["profile"]["modes"])
        self.assertEqual(merged["name"], data["profile"]["name"])

    def test_rank_is_never_mislabeled_as_account_level(self):
        text = _identity_text({"profile_id": 7, "rank_level": "conqueror_3", "rating": 1700})
        self.assertIn("等级未提供", text)
        self.assertNotIn("1700", text)


if __name__ == "__main__":
    unittest.main()
