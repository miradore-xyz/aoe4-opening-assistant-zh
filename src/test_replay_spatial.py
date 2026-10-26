"""二维地图数据测试；全部输入均为合成数据，不冒充真实游戏状态。"""
import copy
import json
import unittest

from replay_analysis import analyze_replay
from replay_spatial import build_spatial_model


def fixture():
    """同名玩家和两套编号故意并存，用于验证身份不会靠名字猜测。"""
    return {
        "source": {"sha256": "a" * 64, "origin": "aoe4world-public-replay",
                   "profile_id": 5497834, "game_id": 257337217},
        "metadata": {"duration": 1047.5, "duration_source": "last_recorded_tick",
                     "map": "合成地图", "players": [
                         {"id": 1003, "profile_id": None, "name": "同名", "team": 1},
                         {"id": 1007, "profile_id": 7007, "name": "同名", "team": 2}]},
        "coverage": {"ticks_per_second": 8, "commands_total": 5},
        "commands": [
            {"time": 10, "tick": 80, "player_id": 1003, "offset": 100, "type_id": 62,
             "type": "move", "known": True, "units": [5, 6], "payload_decoded": False,
             "target": {"x": -10.5, "y": 21.5, "z": 55.25}, "queued": False},
            {"time": 20, "tick": 160, "player_id": 1007, "offset": 200, "type_id": 71,
             "type": "attack_move", "known": True, "units": [12],
             "target": {"x": 20, "y": 12, "z": -40}, "target_entity_id": 77},
            {"time": 30, "player_id": 1003, "offset": 300, "type": "queue_unit",
             "type_id": 3, "known": True, "units": [55], "unit_type_id": 987},
            {"time": 35, "player_id": 1007, "offset": 400, "type": "move",
             "type_id": 62, "target_entity_id": 6},
            {"time": 40, "player_id": 1003, "offset": 500, "type": "periodic", "type_id": 152},
        ],
    }


class ReplaySpatialTests(unittest.TestCase):
    def test_original_coordinates_clock_and_instances_remain_distinct(self):
        model = build_spatial_model(fixture(), {"duration": 1139})
        first, second = model["events"]
        self.assertEqual((first["time"], first["tick"]), (10, 80))
        self.assertEqual(first["target"], {"x": -10.5, "y": 21.5, "z": 55.25})
        self.assertEqual((first["x"], first["y"]), (-10.5, 55.25))
        self.assertEqual(first["units"], [5, 6])
        self.assertEqual(second["target_entity_id"], 77)
        self.assertFalse(first["payload_decoded"])
        self.assertEqual(model["duration"], 1047.5)
        self.assertEqual(model["detail_duration"], 1139)
        self.assertEqual(model["duration_source"], "last_recorded_tick")
        self.assertTrue(model["warnings"])

    def test_identity_uses_explicit_player_profile_not_download_origin_or_name(self):
        model = build_spatial_model(fixture(), {"players": [
            {"profile_id": 5497834, "name": "同名"}, {"profile_id": 7007, "name": "同名"}]})
        self.assertIsNone(model["players"][0]["profile_id"])
        self.assertIsNone(model["events"][0]["profile_id"])
        self.assertEqual(model["events"][1]["profile_id"], 7007)
        self.assertEqual([player["id"] for player in model["players"]], [1003, 1007])

    def test_observed_extrema_never_become_map_edges_or_unit_states(self):
        model = build_spatial_model(fixture())
        self.assertIsNone(model["bounds"])
        self.assertEqual(model["observed_bounds"], {"x_min": -10.5, "x_max": 20, "y_min": -40, "y_max": 55.25})
        self.assertFalse(model["coordinate_system"]["calibrated"])
        self.assertEqual(model["coordinate_system"]["axes"], ["x", "z"])
        for key in ("births_available", "deaths_available", "terrain_available", "unit_positions_available"):
            self.assertFalse(model["coverage"][key])
        self.assertTrue(all(event["kind"] == "command" for event in model["events"]))
        self.assertNotIn("trajectories", model)

    def test_entity_only_targets_and_production_are_counted_without_invented_points(self):
        model = build_spatial_model(fixture())
        coverage = model["coverage"]
        self.assertEqual((coverage["commands_total"], coverage["records_received"], coverage["player_commands"]), (5, 5, 4))
        self.assertEqual((coverage["plottable"], coverage["unlocated"], coverage["processed_system_commands"]), (2, 2, 1))
        self.assertEqual(coverage["player_commands"], coverage["plottable"] + coverage["unlocated"] + coverage["untimed_coordinates"])
        self.assertFalse(any(event["action"] == "queue_unit" for event in model["events"]))

    def test_cached_archive_and_raw_commands_produce_same_actual_point_ids(self):
        data = fixture()
        normalized = analyze_replay(data)
        cached = build_spatial_model({"id": "a" * 64, "report": normalized})
        raw = build_spatial_model(data)
        self.assertEqual([(event["id"], event["time"], event["target"], event["units"]) for event in cached["events"]],
                         [(event["id"], event["time"], event["target"], event["units"]) for event in raw["events"]])
        self.assertEqual(cached["coverage"]["commands_total"], 5)
        self.assertEqual(cached["coverage"]["records_received"], 4)
        self.assertEqual(cached["coverage"]["processed_system_commands"], 0)
        self.assertEqual(cached["coverage"]["source_system_commands"], 1)
        self.assertEqual(cached["events"][0]["source"], "report.timeline")

    def test_unknown_and_partial_coordinates_remain_explicit(self):
        data = fixture()
        data["commands"] = [
            {"time": 1, "player_id": 1003, "type": "unknown", "type_id": 250,
             "known": False, "target": {"x": 0, "z": 0}},
            {"time": 2, "player_id": 1003, "type": "move", "target": {"x": 3, "y": 2}},
            {"time": 3, "player_id": 1003, "type": "move", "target": {"x": float("nan"), "z": 4}},
            {"time": 4, "player_id": 1003, "type": "move", "target": {"x": True, "z": 4}},
            {"time": 5, "player_id": 1003, "type": "move", "target": {"x": 3, "y": float("inf"), "z": 7}},
        ]
        model = build_spatial_model(data)
        self.assertEqual(len(model["events"]), 2)
        self.assertEqual(model["events"][0]["label"], "未识别指令")
        self.assertIsNone(model["events"][1]["target"]["y"])
        self.assertEqual(model["coverage"]["invalid_or_incomplete_coordinates"], 3)
        self.assertEqual(model["coverage"]["unknown_types"], {"250": 1})
        json.dumps(model, allow_nan=False)

    def test_tick_conversion_requires_an_explicit_rate(self):
        data = fixture()
        data["commands"] = [{"tick": 16, "player_id": 1003, "type": "move", "target": {"x": 1, "z": 2}}]
        self.assertEqual(build_spatial_model(data)["events"][0]["time"], 2)
        data["coverage"].pop("ticks_per_second")
        model = build_spatial_model(data)
        self.assertFalse(model["events"])
        self.assertEqual(model["coverage"]["untimed_coordinates"], 1)

    def test_fallback_duration_uses_unlocated_commands_too_never_detail(self):
        data = fixture()
        data["metadata"].pop("duration")
        data["metadata"].pop("duration_source")
        model = build_spatial_model(data, {"duration": 2000})
        self.assertEqual(model["duration"], 35)
        self.assertEqual(model["duration_source"], "last_command_time")
        self.assertEqual(model["detail_duration"], 2000)
        data["commands"] = []
        self.assertIsNone(build_spatial_model(data, {"duration": 2000})["duration"])

    def test_time_sort_does_not_rewrite_time_or_lose_stable_source_identity(self):
        data = fixture()
        before = build_spatial_model(data)
        data["commands"].reverse()
        after = build_spatial_model(data)
        self.assertEqual([event["id"] for event in before["events"]], [event["id"] for event in after["events"]])
        self.assertEqual([event["time"] for event in after["events"]], [10, 20])
        data["commands"].append(copy.deepcopy(data["commands"][-1]))
        events = build_spatial_model(data)["events"]
        self.assertEqual(len({event["id"] for event in events}), len(events))

    def test_controls_missing_players_and_invalid_rows_do_not_enter_player_points(self):
        data = fixture()
        data["commands"] += [None, {"time": 1, "type": "move", "target": {"x": 1, "z": 2}},
                             {"time": 2, "player_id": 0, "type": "move", "target": {"x": 9, "z": 9}},
                             {"time": 3, "player_id": 1007, "type": "PostCommand", "target": {"x": 9, "z": 9}},
                             {"time": 4, "player_id": 1007, "type": "unknown", "category": "system", "target": {"x": 9, "z": 9}}]
        model = build_spatial_model(data)
        self.assertEqual(len(model["events"]), 2)
        self.assertEqual(model["coverage"]["invalid_records"], 1)
        self.assertEqual(model["coverage"]["unassigned_commands"], 1)
        self.assertEqual(model["coverage"]["processed_system_commands"], 4)
        self.assertNotIn(0, [player["id"] for player in model["players"]])

    def test_commands_past_reported_end_remain_available_for_verification(self):
        data = fixture()
        data["metadata"]["duration"] = 15
        model = build_spatial_model(data)
        self.assertEqual(model["duration"], 15)
        self.assertEqual(model["events"][-1]["time"], 20)
        self.assertEqual(model["coverage"]["beyond_reported_duration"], 1)

    def test_conversion_does_not_modify_input_or_share_mutable_metadata(self):
        data = fixture()
        original = copy.deepcopy(data)
        model = build_spatial_model(data)
        self.assertEqual(data, original)
        model["events"][0]["units"].append(100)
        model["players"][0]["name"] = "已修改"
        self.assertEqual(data, original)


if __name__ == "__main__":
    unittest.main()
