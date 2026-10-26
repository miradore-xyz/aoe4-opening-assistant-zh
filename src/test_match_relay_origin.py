"""同源中继入口验证；不访问外网、不改写数据模型。"""
import io
import json
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse

from match_analysis import MatchError, PublicMatchClient


class MatchRelayOriginTests(unittest.TestCase):
    def test_explicit_origin_changes_transport_but_keeps_route_query_and_data(self):
        calls = []
        payload = {'players': [{'profile_id': 7, 'name': '测试同名玩家', 'country': 'cn'}]}
        def opener(request, timeout):
            calls.append((request.full_url, timeout))
            return io.BytesIO(json.dumps(payload).encode())
        with tempfile.TemporaryDirectory() as directory:
            client = PublicMatchClient(directory, opener=opener, api_base_url='https://relay.example.test/')
            result = client.search_players('测试同名玩家')
        url = urlparse(calls[0][0])
        self.assertEqual(url.netloc, 'relay.example.test')
        self.assertEqual(url.path, '/api/v0/players/search')
        self.assertEqual(parse_qs(url.query), {'query': ['测试同名玩家'], 'page': ['1']})
        self.assertEqual(result[0]['profile_id'], 7)
        self.assertEqual(result[0]['name'], '测试同名玩家')

    def test_default_source_stays_original_and_bad_origins_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(PublicMatchClient(directory).api_base_url, 'https://aoe4world.com')
            for origin in ('http://relay.test', 'https://x@y.test', 'https://relay.test/api',
                           'https://relay.test?url=x', 'https://relay.test#x', None, 2):
                with self.subTest(origin=origin), self.assertRaises(MatchError):
                    PublicMatchClient(directory, api_base_url=origin)


if __name__ == '__main__':
    unittest.main()
