"""公开战绩验证：隔离的真实结构样例与 HTTP（网络请求）替身。

结构依据 2026-10-25 实际读取的公开 /api/v0/players/4635035/games?limit=2；
测试将昵称与编号替换为虚构值，不读取或发布用户个人战绩。
"""
import copy
import io
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from match_analysis import (
    MatchError, PublicMatchClient, USER_AGENT, analyze_games, normalize_games,
    match_page_url, parse_player_input, summary_url,
)


def game(game_id=101, result="win", kind="rm_2v2", duration=1200):
    """本人位于第二队的第二名，不假定 teams[0][0]（第一队第一位）就是本人。"""
    return {"game_id": game_id, "started_at": "2026-10-20T10:00:00.000Z",
            "updated_at": "2026-10-20T11:00:00.000Z", "duration": duration,
            "map": "Hill and Dale", "state": "processed", "ongoing": False,
            "kind": kind, "leaderboard": "rm_team", "season": 14, "patch": 11308,
            "teams": [[{"player": {"profile_id": 8, "name": "甲", "mmr": 1500,
                                     "rating": 1450, "result": "loss", "civilization": "english"}},
                       {"player": {"profile_id": 9, "name": "乙", "mmr": 1500,
                                     "rating": 1500, "result": "loss", "civilization": "french"}}],
                      [{"player": {"profile_id": 6, "name": "队友", "mmr": 1650, "rating": 1600}},
                       {"player": {"profile_id": 7, "name": "测试玩家", "mmr": 1750,
                                     "rating": 1700, "rating_diff": 25, "result": result,
                                     "civilization": "order_of_the_dragon"}}]]}


class PublicMatchTests(unittest.TestCase):
    def test_strict_profile_or_name_input(self):
        self.assertEqual(parse_player_input(" 7 "), 7)
        self.assertEqual(parse_player_input("https://aoe4world.com/players/7-test-name"), 7)
        self.assertEqual(parse_player_input("aoe4world.com/players/7"), 7)
        self.assertEqual(parse_player_input("测试玩家"), "测试玩家")
        for value in ("https://evil.test/players/7", "https://aoe4world.com.evil.test/players/7",
                      "http://aoe4world.com/players/7", "https://me@aoe4world.com/players/7",
                      "https://aoe4world.com:8888/players/7", "https://aoe4world.com/players/7/games",
                      "0", "999999999999999999", "短名", ""):
            with self.subTest(value=value), self.assertRaises(MatchError):
                parse_player_input(value)

    def test_open_link_uses_readable_match_page_not_summary_data(self):
        expected = "https://aoe4world.com/players/7/games/101"
        self.assertEqual(match_page_url(7, 101), expected)
        self.assertEqual(summary_url(7, 101), expected)
        for player, match in (("../7", 101), (7, "101/summary"), (0, 101), (7, False)):
            with self.subTest(player=player, match=match):
                self.assertEqual(match_page_url(player, match), "")

    def test_cached_match_rebuilds_readable_url_without_network(self):
        stale = game()
        stale["url"] = "https://aoe4world.com/players/7/games/101/summary"
        replies = [{"profile_id": 7, "name": "测试玩家"}, {"games": [stale]}]

        def opener(request, timeout):
            return io.BytesIO(json.dumps(replies.pop(0)).encode("utf-8"))

        with tempfile.TemporaryDirectory() as directory:
            client = PublicMatchClient(directory, opener=opener)
            client.fetch_profile(7)
            cached = client.fetch_profile(7)
            self.assertEqual(cached["source"], "cache")
            self.assertEqual(replies, [])
            rows, _ = normalize_games(cached["games"], 7)
            self.assertEqual(rows[0]["url"], "https://aoe4world.com/players/7/games/101")
            self.assertEqual(cached["games"][0]["url"], stale["url"])

    def test_second_team_identity_and_complete_team_strength(self):
        rows, skipped = normalize_games([game()], 7)
        self.assertEqual(skipped, {})
        self.assertEqual(rows[0]["result"], "win")
        self.assertEqual(rows[0]["civilization"], "order_of_the_dragon")
        self.assertEqual(rows[0]["opponents"], ["甲", "乙"])
        self.assertEqual(rows[0]["strength_delta"], -200)
        self.assertEqual(rows[0]["strength_kind"], "mmr")
        self.assertEqual(rows[0]["rating_diff"], 25)

    def test_missing_fields_are_unknown_and_ffa_has_no_strength(self):
        original = game(duration=None)
        del original["teams"][0][0]["player"]["mmr"]
        rows, _ = normalize_games([original], 7)
        self.assertEqual(rows[0]["strength_kind"], "rating")
        del original["teams"][0][0]["player"]["rating"]
        rows, _ = normalize_games([original], 7)
        self.assertIsNone(rows[0]["strength_delta"])
        self.assertIsNone(rows[0]["duration"])
        original["teams"].append([{"player": {"profile_id": 10, "mmr": 1800}}])
        rows, _ = normalize_games([original], 7)
        self.assertIsNone(rows[0]["strength_delta"])
        stats = analyze_games([original], 7)["by_mode"]["rm_2v2"]
        self.assertIsNone(stats["median_duration"])
        self.assertEqual(stats["duration_count"], 0)

    def test_completed_results_exclude_ongoing_unknown_and_duplicates(self):
        ongoing, unknown, foreign = game(102), game(103), game(104)
        ongoing["ongoing"] = True
        unknown["teams"][1][1]["player"]["result"] = None
        foreign["teams"][1][1]["player"]["profile_id"] = 77
        rows, skipped = normalize_games([game(), ongoing, unknown, foreign, game()], 7)
        self.assertEqual(len(rows), 1)
        self.assertEqual(skipped, {"尚未结束": 1, "结果未确认": 1, "未找到本人": 1, "无效或重复对局": 1})

    def test_modes_are_never_merged_in_win_rate(self):
        ranked = [game(101, "loss", duration=600), game(102, "win", duration=1800)]
        quick = game(103, "win", "qm_2v2", 1200)
        quick["teams"][1][1]["player"]["civilization"] = "mongols"
        data = analyze_games(ranked + [quick], 7)
        self.assertNotIn("win_rate", data)
        self.assertEqual(data["by_mode"]["rm_2v2"]["win_rate"], 50)
        self.assertEqual(data["by_mode"]["qm_2v2"]["win_rate"], 100)
        self.assertEqual(data["by_mode"]["rm_2v2"]["median_duration"], 1200)
        self.assertEqual(data["by_mode"]["rm_2v2"]["short_losses"], 1)
        self.assertEqual(data["by_mode"]["rm_2v2"]["weaker_losses"], 1)
        self.assertEqual(data["by_mode"]["rm_2v2"]["civilizations"][0]["count"], 2)
        self.assertEqual(analyze_games(ranked + [quick], 7, "qm_2v2")["completed_count"], 1)

    def test_cache_refresh_incremental_and_network_failure_fallback(self):
        calls, replies = [], [{"profile_id": 7, "name": "测试玩家"}, {"games": [game()]}]

        def opener(request, timeout):
            calls.append((request, timeout))
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return io.BytesIO(json.dumps(reply).encode("utf-8"))

        clock = datetime(2026, 10, 25, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            client = PublicMatchClient(directory, opener=opener, now=lambda: clock)
            fresh = client.fetch_profile(7)
            self.assertEqual(fresh["source"], "network")
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0][0].get_header("User-agent"), USER_AGENT)
            self.assertEqual(calls[0][1], 12)
            self.assertIn("limit=50", calls[1][0].full_url)
            cached = client.fetch_profile(7)
            self.assertEqual(cached["source"], "cache")
            self.assertEqual(len(calls), 2)
            newer = game(105, result="loss")
            newer["started_at"] = "2026-10-25T10:00:00.000Z"
            replies.extend([{"profile_id": 7, "name": "测试玩家"}, {"games": [newer]}])
            updated = client.fetch_profile(7, refresh=True)
            self.assertEqual(len(updated["games"]), 2)
            params = parse_qs(urlparse(calls[-1][0].full_url).query)
            self.assertEqual(params["since"], ["2026-10-20T04:00:00+00:00"])
            replies.append(URLError("offline"))
            fallback = client.fetch_profile(7, refresh=True)
            self.assertEqual(fallback["source"], "cache")
            self.assertEqual(len(fallback["games"]), 2)
            self.assertIn("上次保存", fallback["warning"])
            self.assertEqual(fallback["fetched_at"], updated["fetched_at"])
            self.assertTrue((Path(directory) / "战绩缓存" / "玩家-7.json").is_file())

    def test_search_and_no_cache_error(self):
        replies = [{"players": [{"profile_id": 7, "name": "测试玩家"}, {"name": "不完整"}]}]
        def opener(request, timeout):
            return io.BytesIO(json.dumps(replies.pop(0)).encode("utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            client = PublicMatchClient(directory, opener=opener)
            identities = client.search_players("测试玩家")
            self.assertEqual(len(identities), 1)
            self.assertEqual(identities[0]["profile_id"], 7)
            self.assertEqual(identities[0]["name"], "测试玩家")
            self.assertIsNone(identities[0]["game_level"])
            self.assertEqual(identities[0]["avatars"], {"small": None, "medium": None, "full": None})
            failed = PublicMatchClient(directory, opener=lambda *a, **k: (_ for _ in ()).throw(URLError("offline")))
            with self.assertRaises(MatchError):
                failed.fetch_profile(7)
            with self.assertRaises(MatchError):
                failed.fetch_profile("../../etc")

    def test_incremental_refresh_revisits_older_unfinished_games(self):
        old, recent = game(101), game(102)
        old["ongoing"] = True
        recent["started_at"] = "2026-10-22T10:00:00.000Z"
        calls, replies = [], [{"profile_id": 7}, {"games": [recent, old]},
                              {"profile_id": 7}, {"games": [game(101)]}]
        def opener(request, timeout):
            calls.append(request.full_url)
            return io.BytesIO(json.dumps(replies.pop(0)).encode("utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            client = PublicMatchClient(directory, opener=opener)
            client.fetch_profile(7)
            updated = client.fetch_profile(7, refresh=True)
            params = parse_qs(urlparse(calls[-1]).query)
            self.assertEqual(params["since"], ["2026-10-20T09:59:00+00:00"])
            self.assertEqual(analyze_games(updated["games"], 7)["completed_count"], 2)


class AnalysisPageTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        # 身份补全单独测试；战绩界面回归不访问真实服务。
        identity = patch("analysis_page.PlayerIdentityClient.fetch_identity", return_value={})
        identity.start()
        self.addCleanup(identity.stop)

    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtGui import QFontDatabase
        from visual import STYLE
        cls.application = QApplication.instance() or QApplication([])
        for path in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/segoeui.ttf"):
            QFontDatabase.addApplicationFont(path)
        cls.application.setStyleSheet(STYLE)

    def test_page_renders_real_shape_and_background_query_stays_responsive(self):
        from analysis_page import AnalysisPage
        from PyQt5.QtCore import QTimer
        from PyQt5.QtTest import QTest
        with tempfile.TemporaryDirectory() as directory:
            page = AnalysisPage(directory)
            page.resize(696, 436)
            page.show()
            snapshot = {"profile_id": 7, "profile": {"profile_id": 7, "name": "测试玩家"},
                        "games": [game(101), game(102, "loss")], "source": "network",
                        "fetched_at": "2026-10-25T12:00:00+00:00", "warning": ""}
            page._show_snapshot(snapshot)
            QTest.qWait(50)
            self.assertEqual(page.game_table.rowCount(), 2)
            self.assertGreater(page.scroll.verticalScrollBar().maximum(), 0)
            self.assertLessEqual(page.body.minimumSizeHint().width(), 696)
            ticks = []
            timer = QTimer()
            timer.setInterval(10)
            timer.timeout.connect(lambda: ticks.append(True))
            timer.start()
            def slow_query():
                time.sleep(.15)
                return snapshot
            page._job(slow_query, "profile")
            self.assertTrue(page.busy)
            QTest.qWait(300)
            self.assertFalse(page.busy)
            self.assertGreater(len(ticks), 3)
            timer.stop()
            page.close()

    def test_search_history_persists_is_bounded_and_delete_keeps_matches(self):
        from analysis_page import AnalysisPage
        with tempfile.TemporaryDirectory() as directory:
            page = AnalysisPage(directory)
            for number in range(1, 25):
                page._add_history(f"测试玩家{number}", number, f"名字{number}")
            self.assertEqual(len(page.history), 20)
            page._add_history("新的名字", 24, "新昵称")
            self.assertEqual(len(page.history), 20)
            self.assertEqual(sum(entry["profile_id"] == 24 for entry in page.history), 1)
            saved_match = page.client._path(24)
            saved_match.parent.mkdir(parents=True, exist_ok=True)
            saved_match.write_text('{"retained":true}', encoding="utf-8")
            restored = AnalysisPage(directory)
            self.assertEqual(restored.history[0]["name"], "新昵称")
            restored.history_combo.setCurrentIndex(0)
            restored._delete_history()
            self.assertEqual(len(restored.history), 19)
            self.assertTrue(saved_match.is_file())
            again = AnalysisPage(directory)
            self.assertNotIn(24, [entry["profile_id"] for entry in again.history])
            page.close()
            restored.close()
            again.close()

    def test_closing_page_does_not_wait_for_query(self):
        from analysis_page import AnalysisPage
        from PyQt5.QtTest import QTest
        from unittest.mock import patch
        errors = []
        with tempfile.TemporaryDirectory() as directory, patch.object(threading, "excepthook", side_effect=errors.append):
            page = AnalysisPage(directory)
            def delayed():
                time.sleep(.15)
                raise MatchError("测试网络错误")
            page._job(delayed, "profile")
            started = time.monotonic()
            page.close()
            page.deleteLater()
            self.assertLess(time.monotonic() - started, .1)
            QTest.qWait(250)
            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
