"""游戏等级与头像身份测试：公开响应替身，不访问真实服务。"""
import io
import json
import tempfile
import unittest
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

from match_analysis import PublicMatchClient
from player_identity import IdentityClient, IdentityError, profile_identity


STEAM = "76561198000000007"
AVATAR = "https://avatars.fastly.steamstatic.com/0123456789_medium.jpg"


def official():
    return {"result": {"code": 0, "message": "SUCCESS"}, "statGroups": [
        {"id": 10, "type": 1, "members": [{"profile_id": 7, "name": "/steam/" + STEAM,
             "alias": "同名玩家", "level": 22, "xp": 27470, "country": "cn"}]},
        {"id": 11, "type": 1, "members": [{"profile_id": 8, "name": "/steam/76561198000000008",
             "alias": "同名玩家", "level": 40, "xp": 63265, "country": "de"}]}],
        "leaderboardStats": [{"profile_id": 7, "rating": 580, "rank_level": "silver_1"}]}


def steam_xml(identity=STEAM):
    return (f"<profile><steamID64>{identity}</steamID64><steamID>同名玩家</steamID>"
            f"<steamLevel>99</steamLevel><avatarMedium>{AVATAR}</avatarMedium></profile>").encode("utf-8")


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append(request)
        if not self.responses:
            raise AssertionError("不应额外联网")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return io.BytesIO(response if isinstance(response, bytes) else json.dumps(response).encode())


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = self.temporary.name
        self.transport = Transport(official())
        self.now = 10000
        self.client = IdentityClient(self.directory, self.transport, now=lambda: self.now)

    def test_single_official_request_matches_ids_not_duplicate_names(self):
        result = self.client.fetch_identities([8, 7])
        self.assertEqual(list(result), [8, 7])
        self.assertEqual((result[7]["name"], result[8]["name"]), ("同名玩家", "同名玩家"))
        self.assertEqual((result[7]["game_level"], result[8]["game_level"]), (22, 40))
        self.assertEqual(result[7]["platform"], "steam")
        self.assertEqual(result[7]["steam_id"], STEAM)
        self.assertEqual(result[7]["country"], "cn")
        self.assertEqual(len(self.transport.calls), 1)
        params = parse_qs(urlparse(self.transport.calls[0].full_url).query)
        self.assertEqual(params, {"title": ["age4"], "profile_ids": ["[8,7]"]})

    def test_load_cache_and_fresh_batch_do_not_connect(self):
        first = self.client.fetch_identities([7])
        self.assertEqual(self.client.load_cached(7), first[7])
        self.assertEqual(self.client.fetch_identities([7]), first)
        self.assertEqual(len(self.transport.calls), 1)

    def test_batch_never_performs_individual_steam_requests(self):
        result = self.client.fetch_identities([7, 8])
        self.assertTrue(all(not any(row["avatars"].values()) for row in result.values()))
        self.assertEqual(len(self.transport.calls), 1)

    def test_selected_player_gets_exact_steam_avatar_and_keeps_game_level(self):
        self.transport.responses.append(steam_xml())
        result = self.client.fetch_identity(7)
        self.assertEqual(result["game_level"], 22)
        self.assertNotEqual(result["game_level"], 99, "不能用Steam平台等级覆盖游戏等级")
        self.assertEqual(result["avatars"]["medium"], AVATAR)
        self.assertEqual(result["avatar_source"], f"https://steamcommunity.com/profiles/{STEAM}/?xml=1")
        self.assertEqual(len(self.transport.calls), 2)
        again = self.client.fetch_identity(7)
        self.assertEqual(again, result)
        self.assertEqual(len(self.transport.calls), 2)

    def test_selected_avatar_can_be_disabled(self):
        result = self.client.fetch_identity(7, include_avatar=False)
        self.assertEqual(result["game_level"], 22)
        self.assertEqual(len(self.transport.calls), 1)

    def test_other_steam_account_is_not_mistaken_for_same_named_player(self):
        self.transport.responses.append(steam_xml("76561198000000008"))
        result = self.client.fetch_identity(7)
        self.assertEqual(result["game_level"], 22)
        self.assertFalse(any(result["avatars"].values()))
        self.assertIn("账号", result["identity_warning"])
        self.client.fetch_identity(7)
        self.assertEqual(len(self.transport.calls), 2, "失败的头像检查也应短期缓存，避免重复请求")

    def test_avatar_failure_does_not_discard_reliable_game_level(self):
        self.transport.responses.append(URLError("offline"))
        result = self.client.fetch_identity(7)
        self.assertEqual(result["game_level"], 22)
        self.assertFalse(any(result["avatars"].values()))
        self.assertIn("identity_warning", result)

    def test_missing_game_level_does_not_fall_back_to_rating_or_rank(self):
        response = official()
        response["statGroups"][0]["members"][0].pop("level")
        self.transport.responses = [response]
        result = self.client.fetch_identities([7])[7]
        self.assertIsNone(result["game_level"])
        self.assertEqual(result["xp"], 27470, "不从经验值编造未验证的等级公式")

    def test_missing_member_is_not_replaced_by_matching_alias(self):
        result = self.client.fetch_identities([9])
        self.assertEqual(result, {})
        self.assertIsNone(self.client.load_cached(9))

    def test_expired_cache_fetches_one_batch_and_preserves_exact_same_steam_avatar(self):
        self.transport.responses.append(steam_xml())
        self.client.fetch_identity(7)
        self.now += 3601
        self.transport.responses.append(official())
        result = self.client.fetch_identities([7])[7]
        self.assertEqual(result["avatars"]["medium"], AVATAR)
        self.assertEqual(len(self.transport.calls), 3)

    def test_late_batch_without_avatar_does_not_erase_selected_avatar(self):
        self.transport.responses.append(steam_xml())
        selected = self.client.fetch_identity(7)
        late_batch = {**selected, "avatars": {"small": None, "medium": None, "full": None}}
        late_batch.pop("avatar_source")
        self.client._save(7, late_batch)
        cached = self.client.load_cached(7)
        self.assertEqual(cached["avatars"]["medium"], AVATAR)
        self.assertEqual(cached["avatar_source"], selected["avatar_source"])

    def test_avatar_is_checked_again_after_cache_expires(self):
        self.transport.responses.append(steam_xml())
        self.client.fetch_identity(7)
        self.now += 3601
        new_avatar = AVATAR.replace("0123456789", "9876543210")
        self.transport.responses.extend([official(), steam_xml().replace(AVATAR.encode(), new_avatar.encode())])
        result = self.client.fetch_identity(7)
        self.assertEqual(result["avatars"]["medium"], new_avatar)
        self.assertEqual(len(self.transport.calls), 4)

    def test_invalid_ids_never_contact_official_service(self):
        for identities in ([True], ["../7"], [0], list(range(1, 12))):
            with self.subTest(identities=identities), self.assertRaises(IdentityError):
                self.client.fetch_identities(identities)
        self.assertEqual(self.transport.calls, [])

    def test_service_error_does_not_store_rating_as_identity(self):
        self.transport.responses = [{"result": {"code": -1, "message": "not available"}}]
        with self.assertRaises(IdentityError):
            self.client.fetch_identities([7])
        self.assertIsNone(self.client.load_cached(7))

    def test_identity_keeps_provided_search_metadata_without_claiming_generic_level(self):
        value = profile_identity({"profile_id": 7, "name": "玩家", "steam_id": STEAM,
            "avatars": {"medium": AVATAR}, "country": "CN", "level": 580,
            "leaderboards": {"rm_team": {"rank_level": "silver_1"}}, "last_game_at": "2026-10-25T10:00:00Z"})
        self.assertEqual(value["avatars"]["medium"], AVATAR)
        self.assertEqual(value["modes"]["rm_team"]["rank_level"], "silver_1")
        self.assertEqual(value["last_game_at"], "2026-10-25T10:00:00Z")
        self.assertIsNone(value["game_level"], "只有已验证游戏资料中的level才能映射game_level")

    def test_search_no_longer_discards_avatar_and_disambiguation_metadata(self):
        source = {"profile_id": 7, "name": "玩家", "steam_id": STEAM, "country": "cn",
                  "avatars": {"medium": AVATAR}, "leaderboards": {"rm_team": {"rating": 580}}}
        transport = Transport({"players": [source]})
        client = PublicMatchClient(self.directory, opener=transport)
        result = client.search_players("同名玩家")[0]
        self.assertEqual(result["avatars"]["medium"], AVATAR)
        self.assertEqual(result["steam_id"], STEAM)
        self.assertEqual(result["country"], "cn")
        self.assertEqual(result["modes"]["rm_team"]["rating"], 580)
        self.assertIsNone(result["game_level"])
        self.assertEqual(len(transport.calls), 1)


if __name__ == "__main__":
    unittest.main()
