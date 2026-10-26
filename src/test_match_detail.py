"""详细对局图表数据测试：核对实际字段含义、玩家身份和估算边界。"""
import copy
import json
import os
import unittest
from pathlib import Path

from match_detail import (MatchDetailError, compare_players, estimate_worker_loss, load_entity_catalog,
                          normalize_summary, time_label)


CATALOG = {
    "11": {"name": "村民", "classes": ["villager", "worker"], "icon": "villager.png"},
    "22": {"name": "房屋", "classes": [], "icon": "house.png"},
    "33": {"name": "手推车", "classes": [], "icon": "wheelbarrow.png"},
    "99": {"name": "绵羊", "classes": [], "icon": "sheep.png"},
}


def player(identity, team, income=100):
    return {"profile_id": identity, "name": "玩家" + str(identity), "civilization": "english", "team": team,
        "result": "loss" if team == 0 else "win", "apm": 60,
        "scores": {"economy": 100}, "total_resources_gathered": {"food": 500, "total": 800},
        "total_resources_spent": {"food": 300, "total": 600},
        "resources": {"timestamps": [0, 20, 40, 60, 80], "food": [200, 100, 200, 100, 300],
            "wood": [150] * 5, "gold": [100] * 5, "stone": [0] * 5,
            "food_per_min": [income] * 5, "wood_per_min": [0] * 5,
            "gold_per_min": [0] * 5, "stone_per_min": [0] * 5,
            "food_gathered": [0, 100, 100, 300, 300], "economy": [60, 70, 80, 90, 100]},
        "build_order": [
            {"pbgid": 11, "type": "Unit", "finished": [0] * 6 + [20, 40, 80], "destroyed": [60]},
            {"pbgid": 22, "type": "Building", "constructed": [0, 21], "destroyed": [78]},
            {"pbgid": 33, "type": "Upgrade", "finished": [51]},
            {"pbgid": 99, "type": "Unit", "finished": [0], "destroyed": [50]},
        ]}


def summary():
    return {"game_id": 101, "duration": 100, "leaderboard": "rm_2v2", "summary_version": 16,
        "players": [player(20, 1, 200), player(30, 0, 90), player(10, 0, 100)]}


class DetailDataTests(unittest.TestCase):
    def report(self, raw=None):
        return normalize_summary(raw or summary(), 10, 101, entity_catalog=CATALOG)

    def focus(self, report=None):
        return next(p for p in (report or self.report())["players"] if p["profile_id"] == 10)

    def test_exact_profile_mapping_and_zero_based_teams(self):
        report = self.report()
        self.assertEqual(report["focus_profile_id"], 10)
        self.assertEqual({p["profile_id"]: p["relation"] for p in report["players"]}, {20: "opponent", 30: "ally", 10: "self"})
        self.assertEqual([c["profile_id"] for c in report["comparisons"]], [20])
        self.assertEqual(self.focus(report)["apm"], 60)

    def test_bad_identity_cannot_silently_select_another_player(self):
        for focus, game, expected in ((99, 101, "所选玩家"), (10, 999, "编号")):
            with self.subTest(focus=focus), self.assertRaisesRegex(MatchDetailError, expected):
                normalize_summary(summary(), focus, game, CATALOG)
        raw = summary()
        raw["players"][0]["profile_id"] = 10
        with self.assertRaisesRegex(MatchDetailError, "缺失或重复"):
            self.report(raw)

    def test_bank_rate_score_and_ambiguous_cumulative_are_separate(self):
        p = self.focus()
        self.assertEqual(p["series"]["bank_food"]["points"][-1], [80, 300])
        self.assertEqual(p["series"]["income_food"]["points"][-1], [80, 100])
        self.assertEqual(p["series"]["score_economy"]["points"][-1], [80, 100])
        self.assertEqual(p["series"]["reported_usage_food"]["kind"], "unverified")
        self.assertNotIn("gathered_food", p["series"])
        self.assertEqual(p["totals"]["gathered"]["food"], 500)
        self.assertEqual(p["totals"]["spent"]["food"], 300)
        self.assertIsNone(p["totals"]["gathered"]["wood"])
        self.assertEqual(p["series"]["bank_total"]["points"][-1], [80, 550])

    def test_missing_samples_stay_none_and_total_does_not_invent_zero(self):
        raw = summary()
        raw["players"][2]["resources"]["food"] = [200, None, 50]
        series = self.focus(self.report(raw))["series"]
        self.assertEqual(series["bank_food"]["points"], [[0, 200], [20, None], [40, 50], [60, None], [80, None]])
        self.assertIsNone(series["bank_total"]["points"][-1][1])

    def test_invalid_timestamps_are_not_sorted_against_wrong_values(self):
        raw = summary()
        raw["players"][2]["resources"]["timestamps"] = [0, 40, 20, 60, 80]
        p = self.focus(self.report(raw))
        self.assertNotIn("bank_food", p["series"])
        self.assertTrue(any("时间轴" in warning for warning in p["warnings"]))

    def test_events_have_exact_seconds_names_and_initials_not_production(self):
        p = self.focus()
        initial = next(e for e in p["events"] if e["kind"] == "initial" and e["is_villager"])
        self.assertEqual((initial["time"], initial["quantity"], initial["entity_name"]), (0, 6, "村民"))
        births = [e for e in p["events"] if e["kind"] == "production" and e["is_villager"]]
        self.assertEqual([e["time"] for e in births], [20, 40, 80])
        building = next(e for e in p["events"] if e["kind"] == "building")
        self.assertEqual((building["time"], building["entity_name"]), (21, "房屋"))
        self.assertTrue(all(e["killer_profile_id"] is None for e in p["events"]))
        self.assertEqual(p["series"]["villagers_alive"]["points"], [[0, 6], [20, 7], [40, 8], [60, 7], [80, 8]])
        self.assertEqual(p["series"]["villagers_produced"]["points"][-1], [80, 3])
        self.assertEqual(p["series"]["villagers_lost"]["points"][-1], [80, 1])

    def test_unknown_entities_do_not_get_guessed_names_or_worker_class(self):
        raw = summary()
        raw["players"][2]["build_order"].append({"pbgid": 444, "type": "Unit", "icon": "icons/units/villager", "destroyed": [70]})
        p = self.focus(self.report(raw))
        event = next(e for e in p["events"] if e["pbgid"] == 444)
        self.assertEqual(event["entity_name"], "未识别单位")
        self.assertFalse(event["name_known"])
        self.assertFalse(event["is_villager"])
        self.assertEqual(p["series"]["villagers_lost"]["points"][-1], [80, 1])

    def test_transform_is_not_death_and_marks_worker_count_partial(self):
        raw = summary()
        raw["players"][2]["build_order"][0]["transformed"] = [30]
        p = self.focus(self.report(raw))
        self.assertTrue(any(e["kind"] == "transformed" for e in p["events"]))
        self.assertEqual(p["series"]["villagers_lost"]["points"][-1][1], 1)
        self.assertEqual(p["series"]["villagers_alive"]["confidence"], "partial")

    def test_mod_entity_reusing_base_game_pbgid_is_not_called_a_villager(self):
        raw = summary()
        raw["players"][2]["build_order"][0]["modid"] = "custom-mod"
        p = self.focus(self.report(raw))
        self.assertNotIn("villagers_alive", p["series"])
        self.assertTrue(all(not e["is_villager"] for e in p["events"]))
        self.assertTrue(any("模组" in warning for warning in p["warnings"]))

    def test_same_time_multiple_events_group_quantities_without_losing_players(self):
        raw = summary()
        raw["players"][2]["build_order"][0]["destroyed"] = [60, 60]
        report = self.report(raw)
        events = [e for e in report["events"] if e["time"] == 60 and e["kind"] == "loss" and e["is_villager"]]
        self.assertEqual({e["profile_id"]: e["quantity"] for e in events}, {10: 2, 20: 1, 30: 1})

    def test_unavailable_telemetry_is_not_fabricated(self):
        report = self.report()
        for p in report["players"]:
            for key in ("worker_jobs", "unit_positions", "selection_events", "kill_attribution"):
                self.assertIsNone(p[key])
        for key in ("has_worker_jobs", "has_selection", "has_unit_paths", "has_kill_attribution"):
            self.assertFalse(report["coverage"][key])

    def test_income_divergence_is_a_sampled_interval_not_a_defeat_cause(self):
        comparison = self.report()["comparisons"][0]
        interval = comparison["first_sustained_income_gap"]
        self.assertEqual((interval["start"], interval["end"], interval["gap_end"]), (0, 60, 100))
        self.assertEqual(comparison["final_gathered_gap"], 0)
        self.assertTrue(comparison["evidence"])
        self.assertIn("不能据此断定", comparison["findings"][-1])

    def test_missing_teams_do_not_assume_every_other_player_is_enemy(self):
        raw = summary()
        for p in raw["players"]:
            p.pop("team")
        report = self.report(raw)
        self.assertEqual(report["comparisons"], [])
        self.assertEqual([p["relation"] for p in report["players"]], ["unknown", "unknown", "self"])

    def test_comparison_can_switch_focus_and_recompute_relationship(self):
        report = self.report()
        new_focus, other = report["players"][:2]
        comparison = compare_players(new_focus, other)
        self.assertEqual(comparison["relation"], "opponent")
        self.assertEqual(comparison["income_gap"][0], [0, -110])

    def test_landmark_age_has_explicit_localized_age_not_duplicate_placeholder(self):
        raw = summary()
        raw["players"][2]["analysis"] = {"landmarks": [{"pbgid": 22, "game_time": 75, "new_age": 2}]}
        raw["players"][2]["build_order"].append({"pbgid": 123, "type": "Age", "finished": [75]})
        events = [e for e in self.focus(self.report(raw))["events"] if e["kind"] == "age"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["label"], "进入封建时代")
        self.assertEqual(events[0]["time_label"], "01:15")

    def test_only_normalized_whitelisted_report_is_retained(self):
        raw = summary()
        raw["_recent_game_hash"] = {"replays": [{"url": "https://private.example/token"}], "raw": "sensitive"}
        raw["players"][2]["private_field"] = "unused"
        encoded = json.dumps(self.report(raw))
        self.assertNotIn("private.example", encoded)
        self.assertNotIn("sensitive", encoded)
        self.assertNotIn("private_field", encoded)

    def test_shipped_exact_entity_mapping_includes_gilded_villager(self):
        catalog = load_entity_catalog()
        self.assertEqual(catalog["2513732"]["name"], "镀金村民")
        self.assertIn("villager", catalog["2513732"]["classes"])
        self.assertEqual(catalog["7287301"]["name"], "村民")
        self.assertEqual(time_label(1391), "23:11")


class WorkerLossScenarioTests(unittest.TestCase):
    def losses(self):
        return [{"profile_id": 10, "kind": "loss", "time": 300, "quantity": 2,
                 "is_villager": True, "pbgid": 11, "source": "build_order[].destroyed"},
                {"profile_id": 10, "kind": "production", "time": 320, "quantity": 1, "is_villager": True},
                {"profile_id": 20, "kind": "loss", "time": 300, "quantity": 20, "is_villager": True},
                {"profile_id": 10, "kind": "transformed", "time": 300, "quantity": 5, "is_villager": True},
                {"profile_id": 10, "kind": "loss", "time": 300, "quantity": 5, "is_villager": False}]

    def test_two_workers_five_minutes_at_forty_equals_four_hundred(self):
        report = estimate_worker_loss(self.losses(), 10, 40, None, 600)
        self.assertEqual(report["estimated_loss"], 400)
        self.assertEqual(len(report["losses"]), 1)
        self.assertEqual(report["confidence"], "scenario")
        self.assertIn("不是录像直接测得", report["caution"])

    def test_sixty_second_recovery_caps_each_loss_without_claiming_observed_replacement(self):
        report = estimate_worker_loss(self.losses(), 10, 40, 60, 600)
        self.assertEqual(report["estimated_loss"], 80)
        self.assertEqual(dict(report["points"])[360], 80)
        self.assertEqual(dict(report["points"])[600], 80)

    def test_subsequent_normal_birth_is_not_automatically_replacement(self):
        events = self.losses()
        with_birth = estimate_worker_loss(events, 10, 40, None, 600)
        without_birth = estimate_worker_loss([e for e in events if e["kind"] != "production"], 10, 40, None, 600)
        self.assertEqual(with_birth["points"], without_birth["points"])

    def test_rate_is_explicit_user_assumption_not_civilization_multiplier(self):
        report = estimate_worker_loss(self.losses(), 10, 50, 120, 600)
        self.assertEqual(report["estimated_loss"], 200)
        self.assertEqual(report["rate_per_worker"], 50)
        self.assertTrue(any("50" in item for item in report["assumptions"]))

    def test_unknown_losses_and_no_losses_do_not_create_assumed_deaths(self):
        report = estimate_worker_loss([], 10, until=600)
        self.assertEqual(report["estimated_loss"], 0)
        self.assertEqual(report["losses"], [])
        self.assertEqual(estimate_worker_loss(self.losses(), 10, until=299)["estimated_loss"], 0)

    def test_invalid_scenario_arguments_are_clear_errors(self):
        for kwargs in ({"rate_per_worker": -1}, {"rate_per_worker": float("nan")}, {"recovery_seconds": -1}, {"sample_seconds": 0}, {"until": -1}, {"until": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(MatchDetailError):
                estimate_worker_loss(self.losses(), 10, **kwargs)


if __name__ == "__main__":
    unittest.main()
