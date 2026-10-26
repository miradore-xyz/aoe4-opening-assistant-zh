"""本地录像报告测试；以下样例均为合成结构，不是真实战绩。"""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from replay_analysis import (
    ReplayAnalysisError, ReplayLibrary, analyze_replay, filter_timeline, focus_report,
)


def fixture():
    """合成多玩家输入，编号故意不连续，也不按队伍排序。"""
    return {
        "source": {"name": "合成测试.rec", "sha256": "a" * 64, "format": "relic-rec", "size": 1000},
        "metadata": {"game_version": "合成版本", "map": "合成地图", "duration": 200,
                     "players": [{"id": 1042, "name": "甲", "team": 2},
                                 {"id": 1007, "name": "乙", "team": 3},
                                 {"id": 1099, "name": "队伍未知", "team": None},
                                 {"id": 1001, "name": "同队", "team": 2}]},
        "commands": [
            {"time": 61, "player_id": 1007, "type": "attack_move", "type_id": 71, "offset": 200, "known": True},
            {"time": 0, "player_id": 1042, "type": "build", "type_id": 65, "offset": 100, "known": True,
             "units": [5, 8], "unit_type_id": 42, "target": {"x": 10.5, "y": 20}, "queued": False},
            {"time": 5, "player_id": 1042, "type": "queue_unit", "type_id": 3, "offset": 150, "known": True},
            {"time": 65, "player_id": 1042, "type": "cancel", "type_id": 63, "offset": 250, "known": True},
            {"time": 170, "player_id": 1099, "type": "unknown", "type_id": 250, "offset": 300, "known": False},
            {"time": 15, "player_id": 1042, "type": "periodic", "type_id": 152, "known": True},
            {"time": 16, "player_id": 1007, "type": "control", "type_id": 146, "known": True},
            {"time": 17, "player_id": 1007, "type": "PostCommand", "type_id": 148, "known": True},
        ],
        "events": [], "resources": [],
        "coverage": {"commands_total": 8, "commands_decoded": 7, "ticks_per_second": 8,
                     "unknown_types": {"250": 1}, "warnings": []},
    }


class ReplayAnalysisTests(unittest.TestCase):
    def test_public_match_origin_survives_report_archive(self):
        """公开下载与归档后仍能核对场次，不保留原下载地址。"""
        parsed = fixture()
        parsed["source"].update(origin="aoe4world-public-replay", game_id=101, profile_id=7,
                                download_url="https://example.invalid/private-query")
        with tempfile.TemporaryDirectory() as folder:
            library = ReplayLibrary(folder)
            saved = library.save(analyze_replay(parsed))
            source = library.load(saved["id"])["source"]
            self.assertEqual(source["origin"], "aoe4world-public-replay")
            self.assertEqual(source["game_id"], 101)
            self.assertEqual(source["profile_id"], 7)
            self.assertNotIn("download_url", source)

    def test_player_identity_team_relations_and_system_exclusion(self):
        report = analyze_replay(fixture(), 1042)
        players = {row["id"]: row for row in report["players"]}
        self.assertEqual(players[1042]["command_count"], 3)
        self.assertEqual(players[1007]["command_count"], 1)
        self.assertEqual(players[1001]["command_count"], 0)
        self.assertEqual(players[1042]["counts"]["production"], 1)
        self.assertEqual(players[1042]["per_minute"], [{"minute": 0, "count": 2}, {"minute": 1, "count": 1}])
        self.assertEqual(players[1042]["relation"], "self")
        self.assertEqual(players[1001]["relation"], "ally")
        self.assertEqual(players[1007]["relation"], "opponent")
        self.assertEqual(players[1099]["relation"], "unknown")
        self.assertEqual(report["coverage"]["system_commands"], 3)
        self.assertEqual(report["coverage"]["commands_total"], 8)
        self.assertEqual(report["coverage"]["commands_analyzed"], 5)
        self.assertEqual(report["coverage"]["commands_known"], 4)
        self.assertEqual(report["coverage"]["commands_decoded"], 7)
        self.assertEqual([row["time"] for row in report["timeline"]], [0, 5, 61, 65, 170])

    def test_unknown_team_focus_and_multiple_other_teams(self):
        data = fixture()
        data["metadata"]["players"].append({"id": 1033, "name": "另一队", "team": 4})
        data["metadata"]["players"].append({"id": 1034, "name": "未组队", "team": 0})
        report = analyze_replay(data, 1099)
        self.assertTrue(all(row["relation"] == "unknown" for row in report["players"] if row["id"] != 1099))
        focused = focus_report(report, "1042")
        relations = {row["id"]: row["relation"] for row in focused["players"]}
        self.assertEqual(relations[1033], "opponent")
        self.assertEqual(relations[1034], "unknown")
        self.assertEqual(report["focus_player_id"], 1099)
        self.assertIsNone(focus_report(report, "不存在")["focus_player_id"])
        self.assertTrue(all(row["relation"] == "unknown" for row in analyze_replay(data)["players"]))

    def test_nonplayer_zero_and_explicit_system_category_are_not_players(self):
        data = fixture()
        data["metadata"]["players"].append({"id": 0, "name": "非玩家事件"})
        data["commands"] += [{"player_id": 0, "type": "unknown", "type_id": 5, "known": False},
                             {"player_id": 1007, "type": "unknown", "category": "system", "known": False}]
        report = analyze_replay(data)
        self.assertEqual(len(report["players"]), 4)
        self.assertNotIn(0, [row["player_id"] for row in report["timeline"]])
        self.assertEqual(report["coverage"]["commands_analyzed"], 5)
        self.assertEqual(report["coverage"]["system_commands"], 5)

    def test_missing_time_is_unknown_and_tick_conversion_is_explicit(self):
        data = {"commands": [{"player_id": 1010, "type": "move", "time": None, "tick": 16},
                             {"player_id": 1010, "type": "move", "time": float("nan")},
                             {"player_id": None, "type": "build", "time": 10}, None],
                "coverage": {"ticks_per_second": 8}}
        report = analyze_replay(data)
        self.assertEqual(report["timeline"][0]["time"], 2)
        self.assertIsNone(report["timeline"][1]["time"])
        self.assertEqual(report["players"][0]["per_minute"], [{"minute": 0, "count": 1}])
        self.assertIsNone(report["metadata"]["duration"])
        self.assertIsNone(report["metadata"]["map"])
        self.assertFalse(report["coverage"]["events_available"])
        self.assertFalse(report["coverage"]["resources_available"])
        self.assertEqual(report["coverage"]["unassigned_commands"], 1)
        self.assertEqual(len(report["coverage"]["warnings"]), 2)
        json.dumps(report, allow_nan=False)

    def test_unknown_semantics_cannot_be_upgraded_by_familiar_type_name(self):
        data = {"commands": [{"player_id": 1, "type": "build", "known": False, "type_id": 65},
                             {"player_id": 1, "type": "UnknownCommand16", "type_id": 16},
                             {"player_id": 1, "type": "MoveCommand", "known": True, "time": 5}]}
        report = analyze_replay(data)
        counts = report["players"][0]["counts"]
        self.assertEqual(counts["unknown"], 2)
        self.assertEqual(counts["construction"], 0)
        self.assertEqual(counts["movement"], 1)
        self.assertEqual(report["coverage"]["unknown_types"], [16, 65])

    def test_findings_are_questions_or_recorded_evidence_not_idle_diagnoses(self):
        report = analyze_replay(fixture(), 1042)
        findings = report["findings"]
        self.assertTrue(any(row["title"] == "回看取消指令的目的" for row in findings))
        for row in findings:
            self.assertIn(row["confidence"], ("recorded", "question"))
            self.assertTrue(row["evidence"])
            self.assertTrue(all(item["player_id"] == row["player_id"] for item in row["evidence"]))
            self.assertNotIn("断村", row["body"])
            self.assertNotIn("卡人口", row["body"])
            self.assertNotIn("闲军", row["body"])
            self.assertNotIn("有效操作", row["body"])
        self.assertEqual(analyze_replay(fixture(), 1007)["findings"], findings)

    def test_filtering_keeps_all_source_rows_and_supports_paging(self):
        report = analyze_replay(fixture())
        self.assertEqual(len(filter_timeline(report, player_id="1042")), 3)
        self.assertEqual(len(filter_timeline(report, category="construction", query="甲")), 1)
        rows = filter_timeline(report, offset=1, limit=2)
        self.assertEqual([row["time"] for row in rows], [5, 61])
        self.assertEqual(len(report["timeline"]), 5)
        self.assertEqual(filter_timeline(report, limit=0), [])

    def test_payload_coverage_and_recorded_duration_remain_distinct_after_archive(self):
        data = fixture()
        data["metadata"].update({"game_version": None, "build_number": 11308,
                                 "duration_source": "last_recorded_tick"})
        data["coverage"].update({"commands_identified": 7, "commands_decoded": 1,
                                 "decoded_definition": "参数全部遍历，不包含周期和控制记录。",
                                 "stream_complete": True, "metadata_version": 60, "payload_errors": 2})
        data["commands"][1].update({"payload_decoded": False, "payload_bytes_remaining": 4})
        report = analyze_replay(data)
        self.assertIsNone(report["metadata"]["game_version"])
        self.assertEqual(report["metadata"]["build_number"], 11308)
        self.assertEqual(report["coverage"]["commands_identified"], 7)
        self.assertEqual(report["coverage"]["commands_decoded"], 1)
        self.assertEqual(report["coverage"]["commands_known"], 4)
        self.assertEqual(report["timeline"][0]["payload_bytes_remaining"], 4)
        self.assertFalse(report["timeline"][0]["payload_decoded"])
        self.assertEqual(report["timeline"][0]["display_detail"], "涉及 2 个单位；已记录目标位置；立即发令")
        with tempfile.TemporaryDirectory() as directory:
            report["timeline"][0]["detail"] = "不允许混入的聊天正文"
            library = ReplayLibrary(directory)
            saved = library.save(report)
            loaded = library.load(saved["id"])
            self.assertTrue(loaded["coverage"]["stream_complete"])
            self.assertEqual(loaded["metadata"]["duration_source"], "last_recorded_tick")
            self.assertEqual(loaded["coverage"]["payload_errors"], 2)
            self.assertNotIn("聊天正文", Path(saved["path"]).read_text(encoding="utf-8"))

    def test_details_preserve_identifiers_but_not_raw_chat_or_private_paths(self):
        data = fixture()
        data["source"]["name"] = r"C:\Users\私人目录\合成测试.rec"
        data["metadata"]["chat"] = "不归档的聊天正文"
        data["commands"].append({"player_id": 1042, "type": "chat", "message": "不归档的聊天正文"})
        report = analyze_replay(data)
        detail = report["timeline"][0]["detail"]
        self.assertIn("涉及 2 个单位", detail)
        self.assertIn("对象类型编号 42", detail)
        self.assertIn("10.5 / 20", detail)
        text = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("不归档的聊天正文", text)
        self.assertNotIn("私人目录", text)
        self.assertEqual(report["source"]["name"], "合成测试.rec")

    def test_report_archive_deduplication_roundtrip_and_delete_only_report(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_file = Path(directory) / "合成测试.rec"
            raw_file.write_bytes(b"synthetic replay fixture")
            library = ReplayLibrary(directory)
            report = analyze_replay(fixture(), 1042)
            report["raw_bytes"] = "不归档的原始内容"
            report["chat"] = "不归档的聊天正文"
            saved = library.save(report)
            duplicate = library.save(report)
            self.assertFalse(saved["duplicate"])
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(saved["id"], "a" * 64)
            self.assertEqual(len(library.list()), 1)
            with patch.object(library, "load", side_effect=AssertionError("列表不应重建完整报告")):
                self.assertEqual(len(library.list()), 1)
            loaded = library.load(saved["id"])
            self.assertEqual(loaded["players"], report["players"])
            self.assertEqual(loaded["timeline"], report["timeline"])
            self.assertEqual(loaded["findings"], report["findings"])
            self.assertEqual(loaded["coverage"]["system_commands"], 3)
            self.assertEqual(loaded["coverage"]["commands_total"], 8)
            text = Path(saved["path"]).read_text(encoding="utf-8")
            self.assertNotIn("不归档的", text)
            self.assertNotIn(str(raw_file), text)
            self.assertTrue(library.delete(saved["id"]))
            self.assertFalse(library.delete(saved["id"]))
            self.assertTrue(raw_file.is_file())
            self.assertEqual(library.list(), [])

    def test_archive_missing_digest_corruption_and_identifier_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            library = ReplayLibrary(directory)
            report = analyze_replay({"source": {"name": "合成样例"}, "commands": []})
            first = library.save(report)
            self.assertRegex(first["id"], r"^[0-9a-f]{64}$")
            self.assertEqual(library.save(report)["id"], first["id"])
            Path(first["path"]).write_text("broken", encoding="utf-8")
            self.assertEqual(library.list(), [])
            with self.assertRaises(ReplayAnalysisError):
                library.load(first["id"])
            with self.assertRaises(ReplayAnalysisError):
                library.delete("../原录像")
            with self.assertRaises(ReplayAnalysisError):
                library.save({"schema_version": 100})

    def test_reimport_same_digest_updates_report_but_preserves_one_entry_and_created_at(self):
        with tempfile.TemporaryDirectory() as directory:
            library = ReplayLibrary(directory)
            first = library.save(analyze_replay(fixture()))
            first_payload = json.loads(Path(first["path"]).read_text(encoding="utf-8"))
            changed = fixture()
            changed["metadata"]["map"] = "重新解析得到的地图"
            changed["metadata"]["build_number"] = 11308
            changed["coverage"]["stream_complete"] = True
            saved = library.save(analyze_replay(changed))
            self.assertTrue(saved["duplicate"])
            self.assertTrue(saved["updated"])
            self.assertEqual(saved["id"], first["id"])
            self.assertEqual(saved["report"]["metadata"]["map"], "重新解析得到的地图")
            payload = json.loads(Path(saved["path"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["created_at"], first_payload["created_at"])
            self.assertGreaterEqual(payload["updated_at"], payload["created_at"])
            self.assertEqual(library.load(saved["id"])["metadata"]["build_number"], 11308)
            self.assertTrue(library.load(saved["id"])["coverage"]["stream_complete"])
            self.assertEqual(len(library.list()), 1)
            self.assertEqual(library.list()[0]["map"], "重新解析得到的地图")


if __name__ == "__main__":
    unittest.main()
