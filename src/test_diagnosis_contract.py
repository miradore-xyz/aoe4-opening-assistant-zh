"""统一诊断契约：证据身份、时间筛选与不把严重程度混成可信度。"""
import unittest
from diagnosis_contract import capability, diagnostic_record, filter_diagnostics, stable_id


class DiagnosisContractTests(unittest.TestCase):
    def record(self, **kwargs):
        args = dict(players=[1000], facts=["原损失记录"], evidence=[{"source": "STLS", "offset": 9}],
                    confidence={"level": "high", "reason": "原记录逐项核对。"},
                    severity={"level": "review", "reason": "尚未认定错误。"})
        args.update(kwargs)
        return diagnostic_record("id", "battle", "交战片段", 10, 20, **args)

    def test_stable_identity_ignores_display_order_but_not_game_or_source(self):
        keys = [{"offset": 5}, {"offset": 9}]
        self.assertEqual(stable_id(1, "battle", keys), stable_id(1, "battle", keys[::-1] + keys))
        self.assertNotEqual(stable_id(1, "battle", keys), stable_id(2, "battle", keys))

    def test_high_confidence_record_does_not_become_high_severity_or_causal(self):
        result = self.record()
        self.assertEqual(result["confidence"]["level"], "high")
        self.assertEqual(result["severity"]["level"], "review")
        self.assertFalse(result["causal_claim"])

    def test_issue_requires_reference_gap_and_high_confidence(self):
        with self.assertRaises(ValueError):
            self.record(kind="confirmed_issue")
        self.assertEqual(self.record(kind="confirmed_issue", reference="选定计划", gap={"seconds": 30})["kind"], "confirmed_issue")

    def test_invalid_time_and_missing_evidence_stop_result(self):
        for start, end in ((-1, 3), (5, 2), (0, float("nan")), (False, 3)):
            with self.assertRaises(ValueError):
                diagnostic_record("a", "r", "t", start, end, facts=["事实"], evidence=[{}])
        with self.assertRaises(ValueError):
            self.record(evidence=[])

    def test_shared_filter_uses_overlap_and_exact_player(self):
        rows = [self.record()]
        self.assertEqual(filter_diagnostics(rows, 1000, [20, 30]), rows)
        self.assertEqual(filter_diagnostics(rows, 1001), [])
        self.assertEqual(filter_diagnostics(rows, 1000, [21, 30]), [])

    def test_missing_input_downgrades_capability(self):
        self.assertEqual(capability("vision", "B", available=False)["grade"], "D")


if __name__ == "__main__":
    unittest.main()
