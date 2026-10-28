"""原ReflectDB枚举与原EXE选择引用读写的定向回归。"""
from collections import Counter
import struct
import unittest

from replay_parser import (_Reader, _selected_units, _command, ReplayError, COMMAND_ENUM_SOURCE, REFERENCE_ENCODING_SOURCE)
from replay_analysis import analyze_replay
from test_replay_parser import _command as packet


def parsed(kind, payload, player=1000):
    return _command(_Reader(packet(kind, payload, player)), 80, 1, Counter())


class OriginalEncodingTests(unittest.TestCase):
    def test_single_big_endian_word_decodes_original_uid_with_explicit_kind_bases(self):
        for kind, base in ((0, 1000), (1, 1000005000), (2, 50000), (3, 100000)):
            word = (kind << 28) | 218
            refs = []
            legacy = _selected_units(_Reader(word.to_bytes(4, 'big')), refs)
            self.assertEqual(legacy, [218])
            self.assertEqual(refs, [{'kind': kind, 'original_id': base + 218, 'packed_raw': word, 'encoding_build': 11308}])
        self.assertEqual(REFERENCE_ENCODING_SOURCE['read_rva'], '0xAE9340')

    def test_multiple_little_endian_words_keep_separate_reference_kinds(self):
        words = [(1 << 28) | 4750, (2 << 28) | 218]
        refs = []
        legacy = _selected_units(_Reader(bytes([0x42]) + struct.pack('<II', *words)), refs)
        self.assertEqual(legacy, [4750, 218])
        self.assertEqual([row['original_id'] for row in refs], [1000009750, 50218])
        self.assertEqual([row['kind'] for row in refs], [1, 2])

    def test_native_two_byte_count_above_255_and_four_byte_count_are_bounded(self):
        word = (2 << 28) | 5
        for header, count in ((bytes([0x81, 1]), 257), ((0xC0000000 | 2).to_bytes(4, 'big'), 2)):
            refs = []
            self.assertEqual(len(_selected_units(_Reader(header + struct.pack('<I', word) * count), refs)), count)
            self.assertEqual(len(refs), count)
        for raw in (bytes([0x81, 1]) + struct.pack('<I', word), bytes([0xFF, 0xFF, 0xFF, 0xFF])):
            with self.assertRaises(ReplayError):
                _selected_units(_Reader(raw))

    def test_out_of_range_kind_is_rejected_without_committing_refs(self):
        refs = []
        with self.assertRaises(ReplayError):
            _selected_units(_Reader(bytes([0x41]) + struct.pack('<I', 0x40000001)), refs)
        self.assertEqual(refs, [])

    def test_producer_ref_preserves_building_reference_kind(self):
        payload = bytes.fromhex('1000128e150b01') + struct.pack('<iI', 123, 1000) + bytes([0, 1])
        row = parsed(3, payload)
        self.assertTrue(row['payload_decoded'])
        self.assertEqual(row['producer_ref'], row['selected_refs'][0])
        self.assertEqual(row['producer_ref']['kind'], 1)
        self.assertEqual(row['producer_ref']['original_id'], 1000009750)
        self.assertEqual(row['queue_trailing_flags_raw'], '0001')
        self.assertIsNone(row['queued'])

    def test_reflectdb_confirms_stop_and_gather_and_legacy_aliases_are_corrected(self):
        self.assertEqual(COMMAND_ENUM_SOURCE['SquadCommandType'][63], 'SCMD_Stop')
        self.assertEqual(COMMAND_ENUM_SOURCE['SquadCommandType'][105], 'SCMD_Gather')
        stop = parsed(63, bytes.fromhex('200000daff'))
        self.assertEqual(stop['type'], 'stop')
        report = analyze_replay({'commands': [dict(stop, type='cancel')], 'coverage': {}})
        self.assertEqual(report['timeline'][0]['type'], 'stop')
        self.assertEqual(report['players'][0]['counts']['cancel'], 0)

    def test_entity_cancel_ability_research_unload_and_target_commands_are_strict(self):
        producer = bytes.fromhex('1000128e')
        cases = [(5, producer + bytes([5, 4]) + struct.pack('<I', 1), 'cancel_production'),
                 (14, producer + bytes([36, 6, 1]) + struct.pack('<I', 123) + bytes([0]), 'entity_ability'),
                 (16, producer + bytes([21, 11, 1]) + struct.pack('<iI', 123, 1000) + bytes(2), 'research'),
                 (20, producer + bytes([255]), 'unload_squads'),
                 (56, producer + bytes([255]), 'cancel_construction'),
                 (22, producer + bytes([1, 5, 4]) + struct.pack('<I', 50001), 'attack_forced')]
        for kind, payload, name in cases:
            row = parsed(kind, payload)
            self.assertTrue(row['known'], kind)
            self.assertTrue(row['payload_decoded'], kind)
            self.assertEqual(row['type'], name)
            self.assertFalse(parsed(kind, payload[:-1])['known'], kind)

    def test_research_is_blueprint_request_not_completion_or_actual_queue(self):
        payload = bytes.fromhex('1000128e150b01') + struct.pack('<iI', 123, 1000) + bytes(2)
        row = parsed(16, payload)
        self.assertEqual(row['research_type_id'], 123)
        self.assertNotIn('completed', row)
        self.assertNotIn('queue_active', row)

    def test_target_three_preserves_all_four_original_uid_bytes(self):
        payload = bytes.fromhex('200000da010503') + struct.pack('<I', 1000005295)
        row = parsed(67, payload)
        self.assertEqual(row['target_entity_id'], 1000005295)
        self.assertEqual(row['target_attribute_type_id'], 3)
        self.assertTrue(row['payload_decoded'])


if __name__ == '__main__':
    unittest.main()
