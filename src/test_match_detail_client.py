"""单场详情获取与离线重开：隔离资料目录，不访问真实网站。"""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError

from match_detail import MatchDetailError
from match_detail_client import MatchDetailClient
from test_match_detail import summary


class DetailClientTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.opener = Mock(side_effect=lambda *a, **k: io.BytesIO(json.dumps(summary()).encode()))
        self.client = MatchDetailClient(self.directory, self.opener)

    def tearDown(self):
        self.temporary.cleanup()

    def test_single_explicit_fetch_is_reused_offline(self):
        first = self.client.fetch(10, 101)
        self.opener.side_effect = AssertionError("重开不得联网")
        second = self.client.fetch(10, 101)
        self.assertEqual(first, second)
        self.assertEqual(self.opener.call_count, 1)
        self.assertEqual(self.opener.call_args.args[0].full_url, "https://aoe4world.com/players/10/games/101/summary")
        self.assertEqual(first["focus_profile_id"], 10)
        self.assertFalse(list(self.directory.rglob("*.tmp")))

    def test_mismatched_game_or_player_is_not_saved(self):
        for profile, game in ((10, 999), (99, 101)):
            with self.subTest(profile=profile, game=game), self.assertRaises(MatchDetailError):
                self.client.fetch(profile, game)
        self.assertFalse(self.client.directory.exists())

    def test_invalid_identifier_never_contacts_service(self):
        for value in (0, -1, "../101", True):
            with self.subTest(value=value), self.assertRaises(MatchDetailError):
                self.client.fetch(10, value)
        self.opener.assert_not_called()

    def test_missing_public_detail_is_clear_and_does_not_cache_error(self):
        self.opener.side_effect = HTTPError("https://example.invalid", 403, "denied", {}, None)
        with self.assertRaisesRegex(MatchDetailError, "暂未提供公开"):
            self.client.fetch(10, 101)
        self.assertFalse(self.client.directory.exists())

    def test_malformed_cache_does_not_associate_another_game(self):
        self.client.store_summary(summary(), 10, 101)
        path = self.client._path(10, 101)
        cached = json.loads(path.read_bytes())
        cached["game_id"] = 999
        path.write_text(json.dumps(cached), encoding="utf-8")
        self.assertIsNone(self.client.load_cached(10, 101))
        self.assertEqual(self.client.fetch(10, 101)["game_id"], 101)
        self.assertEqual(self.opener.call_count, 1)


if __name__ == "__main__":
    unittest.main()
