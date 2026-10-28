"""原UID状态索引的时间边界、身份隔离及禁止伪造当前位置的针对性验证。"""
from copy import deepcopy
import unittest

from replay_unit_state import UnitStateIndex, build_unit_state_index


def fixture():
    report = {'source': {'game_id': 10, 'sha256': 'fixture-a'},
              'metadata': {'build_number': 11308}, 'players': [{'id': 1000, 'name': '甲'}],
              'timeline': [{'time': 12, 'player_id': 1000, 'type': 'move', 'type_id': 62,
                            'target': {'x': 200, 'y': 300}, 'queued': False,
                            'selected_refs_verified': True,
                            'selected_refs': [{'kind': 2, 'original_id': 50100,
                                               'packed_raw': 0x20000064, 'encoding_build': 11308}]}]}
    sources = {'game_id': 10, 'identity_links': [
        {'player_id': 1000, 'profile_id': 1, 'verified': True},
        {'player_id': 1001, 'profile_id': 2, 'verified': True}],
        'entity_observations': [{'player_id': 1000, 'entity_id': 50100, 'observations': [
            {'time': 10, 'kind': 'birth', 'entity_type': 'unit_spear', 'position': {'x': 0, 'y': 0}},
            {'time': 20, 'kind': 'attack', 'entity_type': 'unit_spear', 'position': {'x': 5, 'y': 2}},
            {'time': 30, 'kind': 'loss', 'entity_type': 'unit_spear', 'position': {'x': 6, 'y': 2}}]}]}
    return report, sources


def index_for(report, sources):
    return UnitStateIndex(build_unit_state_index(report, sources), report)


class UnitStateTests(unittest.TestCase):
    def setUp(self):
        self.report, self.sources = fixture()

    def test_before_birth_is_absent(self):
        self.assertIsNone(index_for(self.report, self.sources).query_unit(1000, 50100, 9.999))

    def test_birth_inclusive_loss_exclusive(self):
        index = index_for(self.report, self.sources)
        self.assertIsNotNone(index.query_unit(1000, 50100, 10))
        self.assertIsNotNone(index.query_unit(1000, 50100, 29.999))
        self.assertIsNone(index.query_unit(1000, 50100, 30))

    def test_position_is_only_exact_observation(self):
        index = index_for(self.report, self.sources)
        self.assertEqual(index.query_unit(1000, 50100, 20)['position'], {'x': 5, 'y': 2})
        state = index.query_unit(1000, 50100, 21)
        self.assertIsNone(state['position'])
        self.assertEqual(state['last_position_observation']['time'], 20)

    def test_destination_never_becomes_position(self):
        state = index_for(self.report, self.sources).query_unit(1000, 50100, 12)
        self.assertIsNone(state['position'])
        self.assertEqual(state['last_command']['target'], {'x': 200, 'y': 300})

    def test_no_future_command(self):
        self.assertIsNone(index_for(self.report, self.sources).query_unit(1000, 50100, 11)['last_command'])

    def test_players_with_same_uid_stay_separate(self):
        self.report['timeline'][0]['player_id'] = 1001
        index = index_for(self.report, self.sources)
        self.assertIsNone(index.query_unit(1000, 50100, 12)['last_command'])
        self.assertEqual(index.document['coverage']['linked_commands'], 0)

    def test_compact_units_are_never_guessed(self):
        command = self.report['timeline'][0]
        command.pop('selected_refs')
        command['units'] = [100]
        self.assertEqual(index_for(self.report, self.sources).document['coverage']['linked_unit_references'], 0)

    def test_different_kind_never_binds_same_integer(self):
        self.report['timeline'][0]['selected_refs'][0]['kind'] = 1
        self.assertEqual(index_for(self.report, self.sources).document['coverage']['linked_commands'], 0)

    def test_wrong_encoding_build_rejected(self):
        self.report['timeline'][0]['selected_refs'][0]['encoding_build'] = 99999
        index = index_for(self.report, self.sources)
        self.assertEqual(index.document['coverage']['rejected_references'], 1)
        self.assertEqual(index.document['coverage']['linked_commands'], 0)

    def test_unverified_ref_rejected(self):
        self.report['timeline'][0]['selected_refs_verified'] = False
        self.assertEqual(index_for(self.report, self.sources).document['coverage']['linked_commands'], 0)

    def test_first_selected_initial_unit_has_no_invented_birth(self):
        self.sources['entity_observations'] = []
        index = index_for(self.report, self.sources)
        self.assertIsNone(index.query_unit(1000, 50100, 11))
        state = index.query_unit(1000, 50100, 12)
        self.assertIsNone(state['recorded_type'])
        self.assertIsNone(state['birth_time'])
        self.assertIsNone(state['position'])

    def test_future_loss_type_is_not_backfilled(self):
        group = self.sources['entity_observations'][0]
        group['observations'] = [group['observations'][-1]]
        state = index_for(self.report, self.sources).query_unit(1000, 50100, 12)
        self.assertIsNone(state['recorded_type'])

    def test_ambiguous_identity_is_not_counted(self):
        self.sources['entity_observations'][0]['identity_ambiguous'] = True
        self.assertEqual(index_for(self.report, self.sources).snapshot(20)['units'], [])

    def test_wrong_match_is_rejected(self):
        self.sources['game_id'] = 11
        with self.assertRaises(ValueError):
            build_unit_state_index(self.report, self.sources)

    def test_wrong_report_digest_is_rejected(self):
        document = build_unit_state_index(self.report, self.sources)
        report = deepcopy(self.report)
        report['source']['sha256'] = 'fixture-b'
        with self.assertRaises(ValueError):
            UnitStateIndex(document, report)

    def test_duplicate_ref_does_not_double_count(self):
        refs = self.report['timeline'][0]['selected_refs']
        refs.append(dict(refs[0]))
        index = index_for(self.report, self.sources)
        self.assertEqual(index.document['coverage']['linked_unit_references'], 1)
        self.assertEqual(index.snapshot(20)['counts'][0]['count'], 1)

    def test_invalid_position_is_not_current(self):
        self.sources['entity_observations'][0]['observations'][0]['position']['x'] = 3.4e38
        state = index_for(self.report, self.sources).query_unit(1000, 50100, 10)
        self.assertIsNone(state['position'])

    def test_queued_command_is_preserved_without_prediction(self):
        self.report['timeline'][0]['queued'] = True
        state = index_for(self.report, self.sources).query_unit(1000, 50100, 12)
        self.assertTrue(state['last_command']['queued'])
        self.assertIsNone(state['position'])

    def test_preorder_does_not_create_finished_unit(self):
        self.report['timeline'][0]['time'] = 5
        index = index_for(self.report, self.sources)
        self.assertIsNone(index.query_unit(1000, 50100, 5))
        self.assertEqual(index.document['command_time_checks']['before_appearance'], 1)

    def test_stale_selection_does_not_resurrect_lost_unit(self):
        self.report['timeline'][0]['time'] = 35
        index = index_for(self.report, self.sources)
        self.assertIsNone(index.query_unit(1000, 50100, 35))
        self.assertEqual(index.document['command_time_checks']['at_or_after_loss'], 1)


if __name__ == '__main__':
    unittest.main()
