"""原分队零值与外部未知零值隔离；归档不丢原证据和标志。"""
import unittest
from replay_analysis import _archive_report, analyze_replay, focus_report


class ReplayTeamEvidenceTests(unittest.TestCase):
    def report(self):
        players = [{"id": pid, "name": str(pid), "team": team,
                    "team_evidence": {"value": team, "source": "replay_setup", "setup_version": 60, "offset": pid}}
                   for pid, team in ((1000, 0), (1001, 0), (1002, 1))]
        return analyze_replay({"metadata": {"players": players, "duration": 20},
                               "commands": [{"time": 1, "player_id": 1000, "type": "move", "known": True,
                                             "command_flags_raw": 128, "queued_candidate": True}]})

    def test_zero_team_is_valid_only_with_original_setup_evidence(self):
        report = self.report()
        self.assertEqual([p["team"] for p in report["players"]], [0, 0, 1])
        legacy = analyze_replay({"metadata": {"players": [{"id": 1000, "team": 0}]}})
        self.assertIsNone(legacy["players"][0]["team"])

    def test_focus_uses_actual_partition_including_zero(self):
        report = focus_report(self.report(), 1000)
        self.assertEqual([p["relation"] for p in report["players"]], ["self", "ally", "opponent"])

    def test_archive_roundtrip_keeps_evidence_and_raw_header_flags(self):
        original = self.report()
        restored = _archive_report(original)
        self.assertEqual(restored["players"][0]["team_evidence"], original["players"][0]["team_evidence"])
        self.assertEqual(restored["timeline"][0]["command_flags_raw"], 128)
        self.assertIs(restored["timeline"][0]["queued_candidate"], True)
        self.assertIsNone(restored["timeline"][0]["queued"])


if __name__ == "__main__":
    unittest.main()
