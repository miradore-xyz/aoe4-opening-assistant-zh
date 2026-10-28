"""补源的同局、来源摘要及真实结构回归。"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import replay_analysis_sources as sources


class SourceBindingTests(unittest.TestCase):
    def setUp(self):
        self.report = {'source': {'game_id': 257756556, 'profile_id': 5497834, 'sha256': 'a' * 64},
                       'metadata': {'build_number': 11308, 'players': [
                           {'id': 1000, 'name': '甲', 'profile_id': None, 'civilization': 'english', 'team': 0},
                           {'id': 1001, 'name': '乙', 'profile_id': None, 'civilization': 'french', 'team': 1}]}}
        self.stats = {'sha256': 'b' * 64, 'players': [
            {'player_id': 1000, 'profile_id': 5497834, 'name': '甲', 'civilization': 'english'},
            {'player_id': 1001, 'profile_id': 4321, 'name': '乙', 'civilization': 'french'}]}
        self.manifest = {'schema_version': 1, 'game_id': 257756556, 'report_sha256': 'a' * 64,
                         'summary_sha256': 'b' * 64, 'build_number': 11308,
                         'participant_profile_ids': [5497834, 4321],
                         'available_replay_profile_ids': [5497834, 4321], 'download_profile_id': 5497834}

    def test_explicit_player_id_link_and_immutable_inputs(self):
        before = deepcopy((self.report, self.stats, self.manifest))
        links = sources._bind_statistics(self.report, self.stats, self.manifest)
        self.assertEqual(links[0]['player_id'], 1000)
        self.assertEqual(links[0]['profile_id'], 5497834)
        self.assertTrue(all(row['verified'] for row in links))
        self.assertEqual((self.report, self.stats, self.manifest), before)

    def test_reject_wrong_match_sha_build_and_participants(self):
        for key, value in [('game_id', 257337217), ('report_sha256', 'c' * 64),
                           ('summary_sha256', 'c' * 64), ('build_number', 999),
                           ('participant_profile_ids', [5497834, 999])]:
            with self.subTest(field=key):
                manifest = dict(self.manifest, **{key: value})
                with self.assertRaises(sources.AnalysisSourceError):
                    sources._bind_statistics(self.report, self.stats, manifest)

    def test_reject_account_civ_name_and_player_set_conflicts(self):
        for key, value in [('profile_id', 999), ('civilization', 'chinese'), ('name', '其他')]:
            with self.subTest(field=key):
                report = deepcopy(self.report)
                report['metadata']['players'][0][key] = value
                with self.assertRaises(sources.AnalysisSourceError):
                    sources._bind_statistics(report, self.stats, self.manifest)
        report = deepcopy(self.report)
        report['metadata']['players'].pop()
        with self.assertRaises(sources.AnalysisSourceError):
            sources._bind_statistics(report, self.stats, self.manifest)

    def test_no_network_and_no_writes_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            opener = unittest.mock.Mock(side_effect=AssertionError('默认禁止联网'))
            result = sources.collect_analysis_sources(self.report, directory, opener=opener)
            self.assertEqual(result['network_requests'], 0)
            self.assertFalse(result['capabilities']['fog_of_war'])
            self.assertEqual(list(Path(directory).iterdir()), [])
            opener.assert_not_called()

    def test_available_public_replays_are_subset_of_participants(self):
        manifest = dict(self.manifest, available_replay_profile_ids=[5497834])
        self.assertEqual(len(sources._bind_statistics(self.report, self.stats, manifest)), 2)
        manifest['available_replay_profile_ids'] = [5497834, 999]
        with self.assertRaises(sources.AnalysisSourceError):
            sources._bind_statistics(self.report, self.stats, manifest)

    def test_same_match_download_may_use_another_participant(self):
        manifest = dict(self.manifest, available_replay_profile_ids=[4321], download_profile_id=4321)
        self.assertEqual(len(sources._bind_statistics(self.report, self.stats, manifest)), 2)
        for changes in ({'download_profile_id': 999}, {'available_replay_profile_ids': [5497834]},
                        {'participant_profile_ids': [4321, 999]}):
            with self.subTest(changes=changes), self.assertRaises(sources.AnalysisSourceError):
                sources._bind_statistics(self.report, self.stats, dict(manifest, **changes))

    def test_duplicate_focus_summary_does_not_hide_conflict(self):
        row = {'profile_id': 5497834, 'datatype': 1, 'matchhistory_id': 257756556}
        with self.assertRaises(sources.AnalysisSourceError):
            sources._select_statistics_file([row, dict(row), {'profile_id': 4321, 'datatype': 1}], 5497834)

    def test_unidentified_local_report_does_not_request(self):
        opener = unittest.mock.Mock(side_effect=AssertionError('无对局不得联网'))
        result = sources.collect_analysis_sources({'source': {'sha256': 'a' * 64}}, allow_network=True, opener=opener)
        self.assertTrue(result['errors'])
        opener.assert_not_called()

    def test_reject_corrupt_compression(self):
        with self.assertRaises(sources.AnalysisSourceError):
            sources.parse_statistics_summary(b'\x1f\x8b\x08\x00' + b'x' * 32)

    def test_observations_do_not_join_compact_ids_or_interpolate(self):
        stls = {'created': [{'category': 46, 'player_id': 1000, 'entity_id': 50042,
                            'entity_type': 'unit_test', 'time': 10, 'x': 1, 'y': 2, 'offset': 100}],
                'lost': [{'has_target': 1, 'target_player_id': 1000, 'target_entity_id': 50042,
                          'target_type': 'unit_test', 'time': 50, 'target_x': 9, 'target_y': 6,
                          'has_attacker': 0, 'offset': 200}]}
        links = [{'player_id': 1000, 'profile_id': 5497834, 'verified': True}]
        result = sources.build_entity_observations(stls, links)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['entity_id'], 50042)
        self.assertEqual([row['time'] for row in result[0]['observations']], [10, 50])
        self.assertTrue(result[0]['position_changed_observed'])
        self.assertFalse(result[0]['continuous_path'])
        self.assertIsNone(result[0]['world_meter_scale'])
        stls['lost'][0]['target_entity_id'] = 42
        self.assertEqual(len(sources.build_entity_observations(stls, links)), 2)


class RealStatisticsSamples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[3]
        cls.path = cls.root / 'work/本地复盘验证/修订22高级分析/数据扩展/257756556-5497834-type1.gz'
        if not cls.path.exists():
            raise unittest.SkipTest('真实样本仅保存在研究目录，不随软件分发。')
        cls.raw = cls.path.read_bytes()
        cls.stats = sources.parse_statistics_summary(cls.raw)

    def test_real_257756556_full_decode_and_exact_identity(self):
        self.assertEqual(self.stats['sha256'], '9ba61de92b8fcd4da875a9f2fb3cbc4e12f9becbe6853e7a46f440db14968114')
        self.assertEqual(len(self.stats['stls']['created']), 4055)
        self.assertEqual(len(self.stats['stls']['lost']), 1823)
        self.assertEqual([(p['player_id'], p['profile_id']) for p in self.stats['players']],
                         [(1000, 5497834), (1001, 18878276), (1002, 15583085), (1003, 6992268),
                          (1004, 24358374), (1005, 25196695), (1006, 10892819), (1007, 3743786)])
        self.assertEqual({len(p['resource_timeline']) for p in self.stats['players']}, {104})
        self.assertIn('unknown_resources_1', self.stats['stls']['created'][0])
        self.assertIn('attacker_player_id', self.stats['stls']['lost'][0])

    def test_prior_real_sample_still_exact(self):
        path = self.root / 'work/本地复盘验证/实战地图可行性/257337217-5497834-type1.gz'
        if not path.exists():
            self.skipTest('旧真实样本未安装')
        result = sources.parse_statistics_summary(path.read_bytes())
        self.assertEqual(len(result['stls']['created']), 1192)
        self.assertEqual(len(result['stls']['lost']), 156)
        self.assertEqual({len(p['resource_timeline']) for p in result['players']}, {53})

    def test_truncated_chunks_rejected(self):
        import gzip
        raw = gzip.decompress(self.raw)
        for trimmed in (raw[:-1], raw[:100], raw[:len(raw)//2]):
            with self.assertRaises((sources.AnalysisSourceError, sources.ReplayError)):
                sources.parse_statistics_summary(trimmed)

    def test_compatibility_events_crosscheck_existing_public_detail(self):
        path = self.root / 'work/本地复盘验证/修订20初始资源证据/257756556-公开摘要-只读工作副本.json'
        if not path.exists():
            self.skipTest('独立既有详情未安装')
        from collections import Counter
        old = json.loads(path.read_bytes())
        report = {'metadata': {'players': []}}
        detail = sources._statistics_detail(self.stats, report, 257756556, 5497834)
        def events(value):
            return Counter((e['profile_id'], e['pbgid'], e['time'], e['kind'], e['quantity'])
                           for e in value['events'] if e['kind'] != 'age')
        # 时代节点在旧JSON由额外地标分析补全；不能当本二进制读取器也已有该字段。
        self.assertEqual(events(old), events(detail))
        self.assertEqual(sum(events(detail).values()), 4396)

    def test_real_unit_observations_have_exact_raw_ids_and_no_generated_points(self):
        links = [{'player_id': p['player_id'], 'profile_id': p['profile_id'], 'verified': True}
                 for p in self.stats['players']]
        groups = sources.build_entity_observations(self.stats['stls'], links)
        self.assertEqual(len(groups), 2667)
        self.assertEqual(sum(len(e['observations']) for e in groups), 5923)
        self.assertEqual(sum(e['position_changed_observed'] for e in groups), 1784)
        self.assertFalse(any(e['identity_ambiguous'] or e['continuous_path'] for e in groups))
        self.assertTrue(all(o['offset'] is not None for e in groups for o in e['observations']))

    def test_focus_without_public_replay_uses_verified_same_match_summary(self):
        import io
        directory = self.root / 'work/本地复盘验证/修订22高级分析/数据扩展'
        metadata = json.loads((directory / '257756556-replay-files.json').read_bytes())
        selected = next(row for row in metadata['replayFiles'] if row['profile_id'] == 5497834 and row['datatype'] == 1)
        metadata['replayFiles'] = [selected]
        report = json.loads((directory / '257756556-report.json').read_bytes())
        report['source']['profile_id'] = 25196695  # 本场第八人，官方清单没有其可用录像。
        metadata_raw = json.dumps(metadata).encode('utf-8')
        calls = []
        def opener(request, timeout):
            calls.append(request.full_url)
            if '/getReplayFiles?' in request.full_url:
                return io.BytesIO(metadata_raw)
            self.assertEqual(request.full_url, selected['url'])
            return io.BytesIO(self.raw)
        result = sources.collect_analysis_sources(report, allow_network=True, opener=opener)
        self.assertEqual(result['errors'], [])
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(result['identity_links']), 8)
        self.assertEqual(result['detail']['focus_profile_id'], 25196695)
        provenance = next(row for row in result['provenance'] if row['kind'] == 'official_statistics')
        self.assertEqual(provenance['download_profile_id'], 5497834)
        self.assertEqual(provenance['selection_basis'], 'same_match_available_summary')
        self.assertIn(25196695, provenance['participant_profile_ids'])
        self.assertNotIn(25196695, provenance['available_replay_profile_ids'])


if __name__ == '__main__':
    unittest.main()
