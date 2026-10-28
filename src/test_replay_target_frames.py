"""修订23目标外层的严格消费与未知用途门控；合成字段不代表真实操作结果。"""
from collections import Counter
import math
import struct
import unittest

from replay_parser import _Reader, _command
from test_replay_parser import _command as packet


UNIT = bytes.fromhex('200000da')
PRODUCER = bytes.fromhex('1000128e')


def parse(kind, payload):
    raw = packet(kind, payload)
    return _command(_Reader(raw), 80, 1, Counter())


class ReplayTargetFrameTests(unittest.TestCase):
    def test_new_move_prefix_frame_preserves_coordinates_and_raw_prefix(self):
        payload = UNIT + bytes([6, 17]) + bytes(4) + bytes([2]) + struct.pack('<fff', 1.25, 2.5, -3.75)
        row = parse(62, payload)
        self.assertTrue(row['known'])
        self.assertTrue(row['payload_decoded'])
        self.assertEqual(row['target'], {'x': 1.25, 'y': 2.5, 'z': -3.75})
        self.assertEqual(row['target_frame_type_id'], 6)
        self.assertEqual(row['target_frame_prefix_raw'], 0)

    def test_new_rally_prefix_frame_preserves_entity_attribute(self):
        payload = PRODUCER + bytes([6, 9]) + struct.pack('<I', 1) + bytes.fromhex('0301020304')
        row = parse(12, payload)
        self.assertTrue(row['payload_decoded'])
        self.assertEqual(row['target_entity_id'], 0x04030201)
        self.assertEqual(row['target_attribute_type_id'], 3)
        self.assertEqual(row['target_frame_prefix_raw'], 1)

    def test_position_tail_retains_four_floats_without_claiming_orientation(self):
        body = bytes([2]) + struct.pack('<fff', 4, 5, 6) + bytes([5]) + struct.pack('<ffff', .1, .2, .3, 1)
        row = parse(62, UNIT + bytes([3, 30]) + body)
        self.assertTrue(row['payload_decoded'])
        self.assertEqual(row['target'], {'x': 4, 'y': 5, 'z': 6})
        self.assertEqual(len(row['target_extension_values_raw']), 4)
        self.assertNotIn('heading', row)
        self.assertNotIn('path', row)

    def test_original_reflectdb_names_confirm_attack_unload_and_gather(self):
        for kind, name in ((67, 'attack'), (76, 'unload'), (105, 'gather')):
            row = parse(kind, UNIT + bytes([1, 5, 4]) + struct.pack('<I', 50186))
            self.assertEqual(row['type'], name)
            self.assertTrue(row['known'])
            self.assertTrue(row['payload_decoded'])
            self.assertTrue(row['payload_structure_decoded'])
            self.assertEqual(row['target_entity_id'], 50186)
            self.assertEqual(row['units'], [218])
            self.assertEqual(row['payload_bytes_remaining'], 0)

    def test_new_frames_reject_bad_lengths_prefixes_tags_and_nonfinite_values(self):
        normal = bytes([2]) + struct.pack('<fff', 1, 2, 3)
        tails = [bytes([6, 17]) + struct.pack('<I', 2) + normal,
                 bytes([6, 17]) + struct.pack('<I', 1) + normal,
                 bytes([6, 16]) + bytes(4) + normal,
                 bytes([6, 18]) + bytes(4) + normal,
                 bytes([3, 30]) + normal + bytes([4]) + struct.pack('<ffff', 0, 0, 0, 1),
                 bytes([3, 30]) + normal + bytes([5]) + struct.pack('<ffff', 0, 0, math.inf, 1),
                 bytes([3, 29]) + normal + bytes([5]) + struct.pack('<ffff', 0, 0, 0, 1)]
        for tail in tails:
            row = parse(62, UNIT + tail)
            self.assertFalse(row['known'], tail.hex())
            self.assertFalse(row['payload_decoded'])

    def test_unknown_structure_failure_keeps_original_fields_and_next_packet_boundary(self):
        bad = packet(67, UNIT + bytes([1, 6, 4]) + struct.pack('<I', 50186))
        good = packet(62, UNIT + bytes([1, 13, 2]) + struct.pack('<fff', 1, 2, 3))
        reader = _Reader(bad + good)
        row = _command(reader, 80, 1, Counter())
        self.assertFalse(row['payload_structure_decoded'])
        self.assertEqual(row['units'], [])
        self.assertNotIn('target_entity_id', row)
        self.assertEqual(reader.pos, len(bad))
        row = _command(reader, 80, 1, Counter())
        self.assertTrue(row['payload_decoded'])
        self.assertEqual(reader.pos, len(bad + good))


if __name__ == '__main__':
    unittest.main()
