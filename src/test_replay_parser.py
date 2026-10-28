"""录像解析回归：原创合成结构与可选的本地真实录像。

合成样本只用于定向验证字段边界；真实样本只从环境变量指定的本地
目录读取，不联网、不分发录像，也不输出玩家姓名或聊天。公开样本来源
为 aoe4world/replays-api/examples；该库未声明许可证，样本不随软件发布。
"""

import gzip
import hashlib
import io
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from replay_parser import ReplayError, parse_replay


def _pack(fmt, *values):
    return struct.pack("<" + fmt, *values)


def _text(value, wide=False):
    raw = value.encode("utf-16-le" if wide else "utf-8")
    return _pack("I", len(raw) // (2 if wide else 1)) + raw


def _chunk(kind, ident, payload, version=1):
    return _pack("4s4sII", kind, ident, version, len(payload)) + _text("") + payload


def _chunk_file(node):
    return b"Relic Chunky\r\n\x1a\x00" + _pack("II", 4, 1) + node


def _player(pid, name, civilization, team, version=58, human=1, extra=0):
    # 按二进制字段声明构造，避免依赖解析实现的内部读取器。
    result = _pack("B", human) + _text(name, True) + _pack("IIB", team, pid, 1)
    result += _text(civilization) + _pack("HHI", 0, 34, 127 if human else 1)
    result += _text("default") + bytes(49)
    result += _text("测试账号不应输出", True) + bytes(30)
    if version == 60:
        result += bytes(8)
    if version >= 58:
        result += _pack("I", extra)
    else:
        extra = human
    if extra:
        result += bytes(69) + _text("{}") + bytes(44)
    if extra > 1:
        result += bytes(69) + _text("{}") + bytes(44)
    if extra > 2:
        result += bytes(117)
    return result


def _command(kind, payload=b"", player=1000, flags=0, size=None):
    length = 24 + len(payload) if size is None else size
    return _pack("hBBH", length, kind, flags, 1) + bytes(14) + _pack("I", player) + payload


def _tick(tick, commands=(), host=1):
    block = _pack("III", host, 0, sum(len(command) for command in commands)) + b"".join(commands)
    payload = _pack("BIII", 32, tick, 0, 1 if commands else 0)
    if commands:
        payload += block
    return _pack("II", 0, len(payload)) + payload


def _replay(stream=None, version=58, players=None):
    if players is None:
        players = [_player(0, "甲", "english", 0, version),
                   _player(7, "乙", "french", 1, version)]
    setup = bytes(10) + _pack("I", len(players)) + b"".join(players)
    post = _chunk_file(_chunk(b"FOLD", b"POST", _chunk(b"DATA", b"DATA", bytes(4))))
    description = _chunk_file(_chunk(b"DATA", b"SDSC", b"", 3023))
    info = _chunk(b"FOLD", b"INFO", _chunk(b"DATA", b"DATA", setup, version), 5)
    header = bytes(2) + _pack("H", 11308) + b"AOE4_RE\x00" + bytes(64)
    return header + post + description + info + (_tick(8) if stream is None else stream)


def _zip(members, compression=zipfile.ZIP_DEFLATED):
    """在内存中生成 ZIP（压缩包）测试样本，不需要外部回放文件。"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, data in members:
            archive.writestr(name, data)
    return output.getvalue()


class ReplayParserTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "测试.rec"

    def parse(self, data, packed=False):
        self.path.write_bytes(gzip.compress(data) if packed else data)
        before = self.path.read_bytes()
        result = parse_replay(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        return result

    def test_multiple_players_commands_and_unknown_boundary(self):
        unit = b"\x20\x00\x01\x02"  # 单个单位 258，大端 24 位编号。
        move = _command(62, unit + b"\x01\x0d\x02" + _pack("fff", 1.5, 2.5, 3.5), 1000, 128)
        unknown = _command(250, "不可误当后续指令".encode("utf-8"), 1007)
        cancel = _command(63, unit, 1007)
        result = self.parse(_replay(_tick(8, [move, unknown, cancel]) + _tick(80)))
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["metadata"]["duration"], 10)
        self.assertEqual(result["metadata"]["duration_source"], "last_recorded_tick")
        self.assertIsNone(result["metadata"]["game_version"])
        self.assertEqual(result["metadata"]["build_number"], 11308)
        self.assertEqual([p["id"] for p in result["metadata"]["players"]], [1000, 1007])
        self.assertEqual(result["commands"][0]["units"], [258])
        self.assertEqual(result["commands"][0]["target"], {"x": 1.5, "y": 2.5, "z": 3.5})
        self.assertIsNone(result["commands"][0]["queued"])
        self.assertTrue(result["commands"][0]["queued_candidate"])
        self.assertFalse(result["commands"][1]["known"])
        self.assertTrue(result["commands"][2]["known"])
        self.assertEqual(result["coverage"]["unknown_types"], {"250": 1})
        self.assertEqual(result["events"], [])
        self.assertEqual(result["resources"], [])

    def test_production_target_and_verified_current_parameters(self):
        prefix = b"\x10\x00\x04\x34"  # 建筑实体编号 1076。
        queue_payload = prefix + b"\x15\x0a\x01" + _pack("iIB", 134753, 1000, 0)
        new_payload = prefix + b"\x15\x0b\x01" + _pack("iIBB", 134753, 1007, 0, 0)
        rally_payload = prefix + b"\x01\x05\x04" + _pack("I", 333)
        result = self.parse(_replay(_tick(16, [_command(3, queue_payload),
                                              _command(3, new_payload, 1007),
                                              _command(12, rally_payload)])))
        first, newer, rally = result["commands"]
        self.assertEqual(first["producer_entity_id"], 1076)
        self.assertEqual(first["unit_type_id"], 134753)
        self.assertTrue(first["payload_decoded"])
        self.assertTrue(newer["known"])
        self.assertTrue(newer["payload_decoded"])
        self.assertEqual(newer["payload_bytes_remaining"], 0)
        self.assertEqual(rally["target_entity_id"], 333)
        self.assertEqual(result["coverage"]["commands_identified"], 3)
        self.assertEqual(result["coverage"]["commands_decoded"], 3)

    def test_multiple_unit_encoding_and_extended_count(self):
        for prefix in (b"\x42", b"\x80\x02"):
            selected = prefix + b"\x02\x01\x00\x20\x34\x04\x00\x10"
            result = self.parse(_replay(_tick(1, [_command(63, selected)])))
            self.assertEqual(result["commands"][0]["units"], [258, 1076])

    def test_control_periodic_and_player_zero_are_not_actions(self):
        stream = _tick(8, [_command(146, bytes(5)), _command(148, bytes(4)),
                           _command(152, bytes(29)), _command(153, bytes(5)),
                           _command(250, b"", 0)])
        result = self.parse(_replay(stream))
        self.assertTrue(all(c["is_system"] for c in result["commands"]))
        self.assertNotIn(0, [p["id"] for p in result["metadata"]["players"]])
        self.assertEqual(result["coverage"]["commands_decoded"], 0)

    def test_unknown_payload_does_not_desynchronize_later_commands(self):
        result = self.parse(_replay(_tick(8, [_command(62, b"\x20\x00\x00\x01\x06"),
                                              _command(63, b"\x20\x00\x00\x02")])))
        self.assertFalse(result["commands"][0]["known"])
        self.assertEqual(result["coverage"]["payload_errors"], 1)
        self.assertTrue(result["commands"][1]["known"])
        self.assertEqual(result["commands"][1]["units"], [2])

    def test_supported_player_layouts_do_not_expose_account_field(self):
        for version in (56, 58, 60):
            with self.subTest(version=version):
                result = self.parse(_replay(version=version))
                self.assertEqual([p["civilization"] for p in result["metadata"]["players"]],
                                 ["english", "french"])
                self.assertEqual([p["team"] for p in result["metadata"]["players"]], [0, 1])
                self.assertNotIn("测试账号不应输出", repr(result))
                self.assertTrue(all(p["profile_id"] is None for p in result["metadata"]["players"]))

    def test_unknown_metadata_version_falls_back_to_command_ids(self):
        result = self.parse(_replay(_tick(1, [_command(63, b"\x20\x00\x00\x01", 1007)]), version=61))
        self.assertEqual(result["metadata"]["players"], [{"id": 1007, "name": "玩家 1007",
                         "civilization": None, "team": None, "profile_id": None}])
        self.assertTrue(any("61" in warning for warning in result["coverage"]["warnings"]))

    def test_compression_magic_and_source_hash(self):
        result = self.parse(_replay(), packed=True)
        self.assertEqual(result["source"]["format"], "aoe4-replay-gzip")
        self.assertEqual(result["source"]["sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(result["source"]["size"], self.path.stat().st_size)

    def test_official_zip_extensionless_replay_and_outer_hash(self):
        self.path = self.path.with_suffix(".zip")
        archive = _zip([("说明.txt", "无需打开附件".encode("utf-8")),
                        ("AgeIV_Replay_123456789", _replay())])
        result = self.parse(archive)
        self.assertEqual(result["source"]["format"], "aoe4-replay-zip")
        self.assertEqual(result["source"]["archive_member"], "AgeIV_Replay_123456789")
        self.assertEqual(result["source"]["sha256"], hashlib.sha256(archive).hexdigest())
        self.assertEqual(result["source"]["size"], len(archive))
        self.assertEqual(result["coverage"]["expanded_size"], len(_replay()))
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [self.path])

    def test_zip_rec_member_and_nested_name_are_never_extracted(self):
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            with self.subTest(compression=compression):
                name = "../录像目录/选择这场.rec"
                result = self.parse(_zip([(name, _replay())], compression))
                self.assertEqual(result["source"]["archive_member"], name)
                self.assertEqual(result["metadata"]["duration"], 1.0)
                self.assertEqual(list(Path(self.temporary.name).iterdir()), [self.path])

    def test_zip_without_replay_is_clear_error(self):
        for members in ([], [("README.txt", b"not a replay" * 30)],
                        [("AgeIV_Replay_123", bytes(76))],
                        [("AgeIV_Replay_123", b"\x00\x00\x00\x00AOE4_RE\x00")]):
            with self.subTest(members=len(members)):
                self.path.write_bytes(_zip(members))
                with self.assertRaisesRegex(ReplayError, "没有可识别"):
                    parse_replay(self.path)

    def test_zip_multiple_replays_requires_selecting_one(self):
        self.path.write_bytes(_zip([("AgeIV_Replay_1", _replay()),
                                    ("AgeIV_Replay_2", _replay(_tick(16)))]))
        with self.assertRaisesRegex(ReplayError, "多场录像.*解压"):
            parse_replay(self.path)

    def test_zip_damage_and_member_truncation_are_not_success(self):
        valid_zip = _zip([("AgeIV_Replay_1", _replay())])
        for broken in (b"PK\x03\x04unfinished", valid_zip[:-10],
                       _zip([("AgeIV_Replay_1", _replay()[:-1])])):
            with self.subTest(size=len(broken)):
                self.path.write_bytes(broken)
                with self.assertRaises(ReplayError):
                    parse_replay(self.path)

    def test_zip_expanded_replay_limit_is_enforced(self):
        self.path.write_bytes(_zip([("AgeIV_Replay_1", _replay())]))
        with patch("replay_parser._MAX_EXPANDED_BYTES", 100):
            with self.assertRaisesRegex(ReplayError, "解压后过大"):
                parse_replay(self.path)

    def test_chat_is_skipped_without_exposing_text(self):
        text = "不要导出这条聊天"
        chat_payload = _pack("IIIII", 1, 0, 8, 0, 0) + _text("甲", True) + _text(text, True)
        chat = _pack("II", 1, len(chat_payload)) + chat_payload
        result = self.parse(_replay(_tick(1) + chat + _tick(8)))
        self.assertEqual(result["coverage"]["chat_records_skipped"], 1)
        self.assertNotIn(text, repr(result))

    def test_truncation_and_invalid_framing_are_errors(self):
        valid = _replay(_tick(1, [_command(63, b"\x20\x00\x00\x02")]))
        for data in (b"", b"not a replay", valid[:75], valid[:-1], valid[:-10],
                     _replay(_pack("II", 9, 0)), _replay(_tick(8) + _tick(7)),
                     _replay(_tick(1, [_command(250, b"", size=1)])),
                     _replay(_tick(1, [_command(250, b"", size=32767)])),
                     _replay(b""), gzip.compress(valid)[:-5]):
            with self.subTest(size=len(data)):
                self.path.write_bytes(data)
                with self.assertRaises(ReplayError):
                    parse_replay(self.path)

    def test_missing_file_is_chinese_error(self):
        with self.assertRaisesRegex(ReplayError, "请选择"):
            parse_replay(self.path)


class RealReplayRegressionTests(unittest.TestCase):
    """显式指定目录后验证真实完整录像；默认不读取用户游戏目录。"""

    @unittest.skipUnless(os.environ.get("AOE4_REPLAY_TEST_DIR"), "未指定本地真实样本目录")
    def test_public_full_replays(self):
        root = Path(os.environ["AOE4_REPLAY_TEST_DIR"])
        expected = {
            "M_152635995_12.0.2246_Crackedy_full.gz": (23266, 2, 1919.375),
            "M_31882073_5.0.12973_Beasty_full.gz": (11553, 2, 981.875),
            "M_51867168_5.0.26139_Beasty_3v3_full.gz": (29328, 6, 955.75),
        }
        for name, (commands, players, duration) in expected.items():
            with self.subTest(sample=name):
                result = parse_replay(root / name)
                self.assertEqual(result["coverage"]["commands_total"], commands)
                self.assertTrue(result["coverage"]["stream_complete"])
                self.assertEqual(len(result["metadata"]["players"]), players)
                self.assertEqual(result["metadata"]["duration"], duration)
                self.assertTrue(any(c["type"] == "queue_unit" and c["known"] for c in result["commands"]))
                self.assertTrue(any(c["type"] == "move" and c["known"] for c in result["commands"]))

    @unittest.skipUnless(os.environ.get("AOE4_PRIVATE_REPLAY_TEST_FILE"), "未指定私人只读验证副本")
    def test_current_private_replay_without_logging_players(self):
        result = parse_replay(Path(os.environ["AOE4_PRIVATE_REPLAY_TEST_FILE"]))
        self.assertEqual(result["metadata"]["build_number"], 11308)
        self.assertEqual(result["coverage"]["metadata_version"], 60)
        self.assertEqual(len(result["metadata"]["players"]), 8)
        self.assertEqual(result["coverage"]["commands_total"], 6349)
        self.assertEqual(result["metadata"]["duration"], 320.875)
        self.assertTrue(result["coverage"]["stream_complete"])
        self.assertTrue(any(c["type"] == "queue_unit" and c["known"] for c in result["commands"]))


if __name__ == "__main__":
    unittest.main()
