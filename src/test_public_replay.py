"""公开回放获取测试：隔离下载目录与网络替身，不发送真实请求。"""
import gzip
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from public_replay import PublicReplayClient, PublicReplayError, _PublicRedirectHandler


_URL = "https://api.ageofempires.com/api/GameStats/AgeIV/GetMatchReplay/?matchId=101&profileId=7"
_BODY = gzip.compress(b"sample replay")
_DETAIL_URL = "https://api.ageofempires.com/api/GameStats/AgeIV/GetMatchDetail"


def match_detail(players=None, game_id="101"):
    """仿官网单场字段；profileId=0 不作为下载账号，唯一身份取 userId。"""
    if players is None:
        players = [{"userId": 7, "profileId": 0, "userName": "同名玩家", "isHuman": True,
                    "matchReplayAvailable": False},
                   {"userId": 8, "profileId": 0, "userName": "同名玩家", "isHuman": True,
                    "matchReplayAvailable": True}]
    return {"statusCode": 0, "errorMessage": None, "matchSummary": {"matchId": game_id},
            "playerList": players}


def detail_body(detail):
    return json.dumps(detail, ensure_ascii=False).encode("utf-8")


class Response(io.BytesIO):
    def __init__(self, value, headers=None, status=None):
        super().__init__(value)
        self.headers = headers or {}
        self.status = status


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append(request)
        if not self.responses:
            raise AssertionError("不应再发送网络请求")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, Response) else Response(response)


class PublicReplayTests(unittest.TestCase):
    def test_real_empty_204_match_detail_is_not_mislabeled_as_identity_mismatch(self):
        """真实故障结构：原账号404后官网详情204；不换人、不猜过期，保留HTTP204。"""
        profile, game = 15223183, 257145099
        url = f"https://api.ageofempires.com/api/GameStats/AgeIV/GetMatchReplay/?matchId={game}&profileId={profile}"
        transport = Transport(HTTPError(url, 404, "Match not found.", None, None),
                              Response(b"", status=204))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PublicReplayError) as caught:
                PublicReplayClient(directory, opener=transport).fetch_match(profile, game)
            self.assertEqual((caught.exception.reason, caught.exception.http_status), ("unavailable", 204))
            self.assertIn("无内容", str(caught.exception))
            self.assertNotIn("过期", str(caught.exception))
            self.assertNotIn("未能核对本场编号", str(caught.exception))
            self.assertEqual(len(transport.calls), 2)
            self.assertEqual(transport.calls[0].full_url, url)
            self.assertEqual(json.loads(transport.calls[1].data), {"matchId": game, "game": "age4", "profileId": profile})
            self.assertEqual(list(Path(directory).rglob("*.aoe4replay")), [])

    def test_empty_or_malformed_200_detail_keeps_http_status_and_never_changes_identity(self):
        """200空/坏资料与204区分；仍不可从失配资料中挑其他账号。"""
        for body in (b"", b"  \n", b"<html>error</html>", detail_body(match_detail(game_id="102"))):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as directory:
                transport = Transport(HTTPError(_URL, 404, "missing", None, None), Response(body, status=200))
                with self.assertRaises(PublicReplayError) as caught:
                    PublicReplayClient(directory, opener=transport).fetch_match(7, 101)
                self.assertEqual((caught.exception.reason, caught.exception.http_status), ("invalid_response", 200))
                self.assertEqual(len(transport.calls), 2)

    def test_match_selected_success_never_queries_other_players_or_metadata(self):
        transport = Transport(_BODY)
        with tempfile.TemporaryDirectory() as directory:
            client = PublicReplayClient(directory, opener=transport)
            result = client.fetch_match("7", "101")
            self.assertEqual((result["requested_profile_id"], result["download_profile_id"]), (7, 7))
            self.assertTrue(client.fetch_match(7, 101)["cached"])
            self.assertEqual([call.full_url for call in transport.calls], [_URL])

    def test_match_fallback_same_names_preserves_source_cache_and_requested_identity(self):
        unavailable = lambda: HTTPError(_URL, 404, "unavailable", None, None)
        detail = match_detail()
        for player in detail["playerList"]:
            player["userId"] = str(player["userId"])
        transport = Transport(unavailable(), detail_body(detail), _BODY,
                              unavailable(), detail_body(detail))
        with tempfile.TemporaryDirectory() as directory:
            client = PublicReplayClient(directory, opener=transport)
            result = client.fetch_match(7, 101)
            self.assertEqual((result["requested_profile_id"], result["profile_id"], result["download_profile_id"]),
                             (7, 8, 8))
            self.assertEqual(result["path"].name, "玩家-8-对局-101.aoe4replay")
            metadata = json.loads(result["path"].with_suffix(".json").read_bytes())
            self.assertEqual(metadata["profile_id"], 8)
            self.assertNotIn("userName", metadata)
            post = transport.calls[1]
            self.assertEqual(post.get_method(), "POST")
            self.assertEqual(post.full_url, _DETAIL_URL)
            self.assertEqual(json.loads(post.data), {"matchId": 101, "game": "age4", "profileId": 7})
            cached = client.fetch_match(7, 101)
            self.assertTrue(cached["cached"])
            self.assertEqual(cached["download_profile_id"], 8)
            self.assertEqual([call.full_url for call in transport.calls],
                             [_URL, _DETAIL_URL, _URL.replace("profileId=7", "profileId=8"), _URL, _DETAIL_URL])

    def test_match_only_explicit_official_button_candidates_receive_downloads(self):
        selected, eligible = match_detail()["playerList"]
        players = [selected, dict(eligible, userId=9, userName="  "),
                   dict(eligible, userId=10, isHuman=False),
                   dict(eligible, userId=11, matchReplayAvailable=False),
                   dict(eligible, userId=12, matchReplayAvailable=1),
                   dict(eligible, userId=0), dict(eligible, userId="13/other"), eligible]
        transport = Transport(HTTPError(_URL, 403, "denied", None, None),
                              detail_body(match_detail(players)), _BODY)
        with tempfile.TemporaryDirectory() as directory:
            result = PublicReplayClient(directory, opener=transport).fetch_match(7, 101)
            self.assertEqual(result["download_profile_id"], 8)
            self.assertEqual(len(transport.calls), 3)
            self.assertTrue(transport.calls[-1].full_url.endswith("profileId=8"))
            self.assertFalse(any(call.full_url.endswith("profileId=0") for call in transport.calls))

    def test_match_metadata_requires_exact_game_and_original_human_account(self):
        base = match_detail()
        wrong_human = match_detail([dict(player, isHuman=False) if player["userId"] == 7 else player
                                    for player in base["playerList"]])
        cases = [match_detail(game_id="102"), match_detail([base["playerList"][1]]), wrong_human,
                 dict(base, statusCode=True), dict(base, statusCode="0"), dict(base, statusCode=1),
                 dict(base, errorMessage="官网返回的错误"),
                 dict(base, matchSummary=dict(base["matchSummary"], errorMessage="本场资料不可用")),
                 match_detail([]), match_detail(base["playerList"] * 5), None]
        for payload in cases:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                body = detail_body(payload) if payload is not None else b"<html>error</html>"
                transport = Transport(HTTPError(_URL, 404, "unavailable", None, None), body)
                with self.assertRaises(PublicReplayError) as caught:
                    PublicReplayClient(directory, opener=transport).fetch_match(7, 101)
                self.assertEqual(caught.exception.reason, "invalid_response")
                self.assertIn("核对", str(caught.exception))
                self.assertEqual(len(transport.calls), 2)

    def test_match_network_service_limit_or_invalid_file_never_triggers_fallback(self):
        cases = [(HTTPError(_URL, code, "test", None, None), reason)
                 for code, reason in ((429, "rate_limited"), (503, "service_error"), (418, "http_error"))]
        cases += [(URLError(TimeoutError("timeout")), "network_timeout"),
                  (URLError("offline"), "network_error"), (b"<html>no replay</html>", "invalid_content")]
        for response, reason in cases:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                transport = Transport(response)
                with self.assertRaises(PublicReplayError) as caught:
                    PublicReplayClient(directory, opener=transport).fetch_match(7, 101)
                self.assertEqual(caught.exception.reason, reason)
                self.assertEqual(len(transport.calls), 1)

    def test_match_candidate_service_failure_stops_before_next_available_candidate(self):
        detail = match_detail()
        detail["playerList"].append(dict(detail["playerList"][1], userId=9))
        cases = [(HTTPError(_URL, code, "test", None, None), reason)
                 for code, reason in ((429, "rate_limited"), (500, "service_error"))]
        cases += [(URLError(TimeoutError("timeout")), "network_timeout"),
                  (URLError("offline"), "network_error"), (b"<html>error</html>", "invalid_content")]
        for response, reason in cases:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                transport = Transport(HTTPError(_URL, 404, "missing", None, None), detail_body(detail), response)
                with self.assertRaises(PublicReplayError) as caught:
                    PublicReplayClient(directory, opener=transport).fetch_match(7, 101)
                self.assertEqual(caught.exception.reason, reason)
                self.assertEqual(len(transport.calls), 3)

    def test_match_candidates_are_sequential_deduplicated_and_exhaustion_is_clear(self):
        detail = match_detail()
        detail["playerList"] += [dict(detail["playerList"][1]), dict(detail["playerList"][1], userId=9)]
        for success in (False, True):
            with self.subTest(success=success), tempfile.TemporaryDirectory() as directory:
                final = _BODY if success else HTTPError(_URL, 409, "not ready", None, None)
                transport = Transport(HTTPError(_URL, 401, "denied", None, None), detail_body(detail),
                                      HTTPError(_URL, 404, "missing", None, None), final)
                client = PublicReplayClient(directory, opener=transport)
                if success:
                    self.assertEqual(client.fetch_match(7, 101)["download_profile_id"], 9)
                else:
                    with self.assertRaisesRegex(PublicReplayError, "均无法获取") as caught:
                        client.fetch_match(7, 101)
                    self.assertEqual(caught.exception.reason, "unavailable")
                    self.assertEqual(caught.exception.http_status, 409)
                self.assertEqual(len(transport.calls), 4)
                self.assertTrue(transport.calls[2].full_url.endswith("profileId=8"))
                self.assertTrue(transport.calls[3].full_url.endswith("profileId=9"))

    def test_match_no_public_candidates_and_metadata_failure_do_not_start_other_downloads(self):
        unmarked = dict(match_detail()["playerList"][1])
        del unmarked["matchReplayAvailable"]
        detail = match_detail([dict(match_detail()["playerList"][0], userName=""), unmarked])
        cases = [(detail_body(detail), "unavailable", "未列出"),
                 (HTTPError(_DETAIL_URL, 429, "limited", None, None), "rate_limited", "429"),
                 (HTTPError(_DETAIL_URL, 503, "unavailable", None, None), "service_error", "503"),
                 (URLError("offline"), "network_error", "网络连接")]
        for response, reason, message in cases:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                transport = Transport(HTTPError(_URL, 404, "missing", None, None), response)
                with self.assertRaises(PublicReplayError) as caught:
                    PublicReplayClient(directory, opener=transport).fetch_match(7, 101)
                self.assertEqual(caught.exception.reason, reason)
                self.assertIn(message, str(caught.exception))
                self.assertEqual(len(transport.calls), 2)

    def test_single_official_request_download_and_offline_cache(self):
        transport = Transport(Response(_BODY, {"Content-Length": str(len(_BODY)), "Content-Type": "application/zip"}))
        with tempfile.TemporaryDirectory() as directory:
            client = PublicReplayClient(directory, opener=transport)
            result = client.fetch(7, 101)
            self.assertFalse(result["cached"])
            self.assertEqual(result["path"].read_bytes(), _BODY)
            self.assertEqual(result["source_url"], _URL)
            self.assertEqual(result["sha256"], hashlib.sha256(_BODY).hexdigest())
            self.assertEqual(result["profile_id"], 7)
            self.assertEqual(result["game_id"], 101)
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(transport.calls[0].full_url, _URL)
            self.assertTrue(transport.calls[0].get_header("User-agent").startswith("AOE4HELP/"))
            self.assertTrue(client.fetch(7, 101)["cached"])
            self.assertEqual(len(transport.calls), 1)
            metadata = json.loads(result["path"].with_suffix(".json").read_bytes())
            self.assertEqual(set(metadata), {"schema", "profile_id", "game_id", "url", "filesize", "sha256"})

    def test_zip_download_without_length_is_supported(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("AgeIV_Replay_101", "sample replay")
        body = output.getvalue()
        with tempfile.TemporaryDirectory() as directory:
            result = PublicReplayClient(directory, opener=Transport(body)).fetch(7, 101)
            self.assertEqual(result["filesize"], len(body))
            self.assertEqual(result["path"].read_bytes(), body)

    def test_private_or_missing_replay_has_no_other_endpoint_fallback(self):
        messages = []
        for code in (401, 403, 404, 409):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                transport = Transport(HTTPError(_URL, code, "not public", None, None))
                with self.assertRaises(PublicReplayError) as caught:
                    PublicReplayClient(directory, opener=transport).fetch(7, 101)
                self.assertEqual(caught.exception.http_status, code)
                self.assertEqual(caught.exception.reason, "permission" if code in (401, 403) else "unavailable")
                self.assertIn(str(code), str(caught.exception))
                messages.append(str(caught.exception))
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(list(Path(directory).rglob("*.aoe4replay")), [])
        self.assertEqual(len(set(messages)), 4, "四种服务响应提供不同中文说明")

    def test_rate_limit_service_and_other_http_errors_are_distinct_without_retry(self):
        for code, reason in ((429, "rate_limited"), (500, "service_error"), (503, "service_error"), (418, "http_error")):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                transport = Transport(HTTPError(_URL, code, "test", None, None))
                with self.assertRaises(PublicReplayError) as caught:
                    PublicReplayClient(directory, opener=transport).fetch(7, 101)
                self.assertEqual((caught.exception.reason, caught.exception.http_status), (reason, code))
                self.assertIn(str(code), str(caught.exception))
                self.assertEqual(len(transport.calls), 1)

    def test_connection_timeout_and_local_write_failure_are_not_confused(self):
        for error, reason in ((URLError(TimeoutError("timeout")), "network_timeout"),
                              (TimeoutError("timeout"), "network_timeout"),
                              (URLError("offline"), "network_error"),
                              (ConnectionResetError("reset"), "network_error")):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                transport = Transport(error)
                with self.assertRaises(PublicReplayError) as caught:
                    PublicReplayClient(directory, opener=transport).fetch(7, 101)
                self.assertEqual(caught.exception.reason, reason)
                self.assertIsNone(caught.exception.http_status)
                self.assertEqual(len(transport.calls), 1)
        with tempfile.TemporaryDirectory() as directory:
            transport = Transport(_BODY)
            with patch("public_replay.tempfile.NamedTemporaryFile", side_effect=PermissionError("readonly")), \
                    self.assertRaises(PublicReplayError) as caught:
                PublicReplayClient(directory, opener=transport).fetch(7, 101)
            self.assertEqual(caught.exception.reason, "storage_error")
            self.assertIn("本地", str(caught.exception))
            self.assertEqual(len(transport.calls), 1)

    def test_stream_connection_failure_is_network_and_invalid_body_is_not_permission(self):
        class BrokenResponse(Response):
            def read(self, size=-1):
                raise ConnectionResetError("stream reset")

        for response, reason in ((BrokenResponse(b""), "network_error"),
                                 (Response(b"<html>unavailable</html>"), "invalid_content")):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                transport = Transport(response)
                with self.assertRaises(PublicReplayError) as caught:
                    PublicReplayClient(directory, opener=transport).fetch(7, 101)
                self.assertEqual(caught.exception.reason, reason)
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(list(Path(directory).rglob("*.tmp")), [])

    def test_truncated_oversized_or_invalid_file_not_saved(self):
        cases = [Response(_BODY[:-1], {"Content-Length": str(len(_BODY))}),
                 Response(_BODY + b"extra", {"Content-Length": str(len(_BODY))}),
                 Response(b"<html>error</html>"),
                 Response(b"", {"Content-Length": "0"}),
                 Response(b"", {"Content-Length": str(128 * 1024 * 1024 + 1)}),
                 Response(b"", {"Content-Length": "invalid"})]
        for response in cases:
            with self.subTest(response=response.headers), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(PublicReplayError):
                    PublicReplayClient(directory, opener=Transport(response)).fetch(7, 101)
                self.assertEqual(list(Path(directory).rglob("*.aoe4replay")), [])
                self.assertEqual(list(Path(directory).rglob("*.tmp")), [])

    def test_stream_limit_applies_without_length_header(self):
        with tempfile.TemporaryDirectory() as directory, patch("public_replay._MAX_REPLAY_BYTES", 8):
            with self.assertRaisesRegex(PublicReplayError, "128 MB"):
                PublicReplayClient(directory, opener=Transport(_BODY)).fetch(7, 101)
            self.assertEqual(list(Path(directory).rglob("*.aoe4replay")), [])

    def test_modified_cached_file_is_downloaded_again_even_when_same_size(self):
        transport = Transport(_BODY, _BODY)
        with tempfile.TemporaryDirectory() as directory:
            client = PublicReplayClient(directory, opener=transport)
            first = client.fetch(7, 101)
            damaged = _BODY[:-1] + bytes([_BODY[-1] ^ 1])
            first["path"].write_bytes(damaged)
            self.assertFalse(client.fetch(7, 101)["cached"])
            self.assertEqual(first["path"].read_bytes(), _BODY)
            self.assertEqual(len(transport.calls), 2)

    def test_cache_metadata_cannot_change_identity_or_network_target(self):
        for field, value in (("profile_id", 8), ("game_id", 102), ("url", "https://evil.test/replay")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                transport = Transport(_BODY, _BODY)
                client = PublicReplayClient(directory, opener=transport)
                result = client.fetch(7, 101)
                path = result["path"].with_suffix(".json")
                metadata = json.loads(path.read_bytes())
                metadata[field] = value
                path.write_text(json.dumps(metadata), encoding="utf-8")
                self.assertFalse(client.fetch(7, 101)["cached"])
                self.assertTrue(all(call.full_url == _URL for call in transport.calls))

    def test_network_errors_are_chinese_and_invalid_ids_make_no_request(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(PublicReplayError, "网络连接"):
                PublicReplayClient(directory, opener=Transport(URLError("offline"))).fetch(7, 101)
            transport = Transport()
            client = PublicReplayClient(directory, opener=transport)
            for player, match in (("../7", 101), (0, 101), (7, 0), (True, 101), (7, "101/summary")):
                for method in (client.fetch, client.fetch_match):
                    with self.subTest(player=player, match=match, method=method.__name__), self.assertRaises(PublicReplayError):
                        method(player, match)
            self.assertEqual(transport.calls, [])

    def test_redirect_to_unconfirmed_host_is_blocked(self):
        from urllib.request import Request
        with self.assertRaisesRegex(PublicReplayError, "未确认的地址"):
            _PublicRedirectHandler().redirect_request(Request(_URL), None, 302, "Found", {}, "https://evil.test/replay.gz")


if __name__ == "__main__":
    unittest.main()
