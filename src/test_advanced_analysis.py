"""高级分析的计算与证据边界测试；以下数值均为合成测试输入。"""
import copy
import json
import unittest

from advanced_analysis import ANALYSIS_SPECS, build_advanced_analysis, integrate_excess
from match_detail import normalize_summary
from replay_analysis import analyze_replay
from test_match_detail import CATALOG, summary


def report():
    parsed = {"source": {"name": "合成.rec", "sha256": "a" * 64},
              "metadata": {"duration": 100, "build_number": 11308, "players": [
                  {"id": 1001, "profile_id": 10, "name": "同名", "team": 1},
                  {"id": 1002, "profile_id": 20, "name": "同名", "team": 2},
                  {"id": 1003, "profile_id": 30, "name": "队友", "team": 1}]},
              "commands": [], "coverage": {"stream_complete": True}}
    command = {"time": 10, "player_id": 1001, "type": "attack_move", "type_id": 71,
               "known": True, "units": [7, 8], "target": {"x": 1, "y": 0, "z": 2},
               "selected_refs_verified": True, "selected_refs": [
                   {"kind": 2, "original_id": 50007, "packed_raw": 0x20000007, "encoding_build": 11308},
                   {"kind": 2, "original_id": 50008, "packed_raw": 0x20000008, "encoding_build": 11308}],
               "command_flags_raw": 0, "queued_candidate": False,
               "payload_decoded": True, "payload_bytes_remaining": 0, "target_bytes_remaining": 0,
               "queued": False, "offset": 100}
    parsed["commands"] = [command, dict(command, time=10.5, offset=200),
                          dict(command, time=10.7, player_id=1003, offset=300),
                          dict(command, time=11, player_id=1002, offset=400),
                          {"time": 25, "player_id": 1001, "type": "move", "type_id": 12,
                           "known": True, "units": [7], "target_entity_id": 900, "offset": 500},
                          {"time": 30, "player_id": 1001, "type": "periodic", "type_id": 152}]
    result = analyze_replay(parsed)
    result["source"].update(game_id=101, profile_id=10)
    return result


def detail():
    raw = summary()
    for player in raw["players"]:
        player["resources"]["military"] = [10, 20, 30, 40, 50]
    return normalize_summary(raw, 10, 101, CATALOG)


def unit_sources():
    """同局STLS观测的合成结构，命令units=[7,8]不参与实体501的关联。"""
    groups = []
    for pid, profile, entity, finish in ((1001, 10, 501, (5, 6)), (1002, 20, 502, (1, 2))):
        groups.append({'player_id': pid, 'profile_id': profile, 'entity_id': entity,
                       'entity_types': ['unit_test'], 'identity_ambiguous': False,
                       'observations': [
                           {'time': 1, 'kind': 'birth', 'position': {'x': 1, 'y': 2}, 'offset': 31,
                            'source': 'STLS.createdEntities'},
                           {'time': 10, 'kind': 'attack', 'position': {'x': finish[0], 'y': finish[1]}, 'offset': 32,
                            'source': 'STLS.lostEntities.attacker'}],
                       'coordinate_system': 'STLS原坐标', 'continuous_path': False, 'world_meter_scale': None})
    return {'game_id': 101, 'identity_links': [
        {'player_id': 1001, 'profile_id': 10, 'verified': True},
        {'player_id': 1002, 'profile_id': 20, 'verified': True}], 'entity_observations': groups}


class AdvancedAnalysisTests(unittest.TestCase):
    def test_all_eighteen_missing_without_inputs_and_no_fabricated_values(self):
        model = build_advanced_analysis()
        self.assertEqual(len(model["results"]), 18)
        self.assertEqual(len({row["id"] for row in ANALYSIS_SPECS}), 18)
        for row in model["results"]:
            self.assertEqual(row["status"], "missing")
            self.assertEqual(row["metrics"], [])
            self.assertEqual(row["series"], [])
            self.assertTrue(row["missing"])

    def test_linear_threshold_crossing_integrates_only_positive_triangle(self):
        self.assertAlmostEqual(integrate_excess([[0, 0], [60, 200]], 100)["integral"], 25)
        self.assertAlmostEqual(integrate_excess([[0, 200], [60, 0]], 100)["integral"], 25)
        self.assertAlmostEqual(integrate_excess([[0, 200], [60, 300]], 100)["integral"], 150)

    def test_missing_samples_and_invalid_time_are_not_bridged_or_sorted(self):
        value = integrate_excess([[0, 100], [60, None], [120, 100], [180, 100]], 0)
        self.assertEqual(value["integral"], 100)
        self.assertEqual(value["covered_seconds"], 60)
        self.assertIsNone(value["points"][1][1])
        self.assertEqual(integrate_excess([[60, 100], [0, 100]])["points"], [])
        for reserve in (-1, float("inf"), True):
            with self.assertRaises(ValueError):
                integrate_excess([[0, 100], [60, 100]], reserve)

    def test_exact_adjacent_repeats_and_player_filter(self):
        model = build_advanced_analysis(report(), selected_player_id=1001)
        result = model["by_id"]["ineffective_actions"]
        self.assertEqual(result["metrics"][0]["value"], 1)
        self.assertEqual(result["rows"][0][3:], [0.5, 200, 100])
        self.assertEqual(model["coverage"]["commands"], 3)
        self.assertIn("不能判断无效", result["summary"])
        self.assertEqual(sum(value for _, value in model["by_id"]["attention"]["series"][2]["points"]), 2)

    def test_undecoded_or_different_parameters_cannot_be_repeat_candidates(self):
        for field, value in (("payload_decoded", False), ("target_bytes_remaining", 4),
                             ("selected_refs", [{"kind": 2, "original_id": 50009, "packed_raw": 0x20000009, "encoding_build": 11308}]),
                             ("queued", True)):
            data = report()
            data["timeline"][1][field] = value
            result = build_advanced_analysis(data)["by_id"]["ineffective_actions"]
            self.assertEqual(result["metrics"][0]["value"], 0, field)

    def test_summary_requires_same_explicit_game_and_unique_profile_identity(self):
        data, stats = report(), detail()
        stats["game_id"] = 999
        model = build_advanced_analysis(data, detail=stats)
        self.assertEqual(model["coverage"]["detail_players"], 0)
        stats = detail()
        for player in data["players"]:
            player["profile_id"] = None
        model = build_advanced_analysis(data, detail=stats, selected_player_id=1001)
        self.assertEqual(model["coverage"]["detail_players"], 0)
        self.assertIn("唯一且明确", model["coverage"]["identity_warning"])
        # 全体模式可显示同场摘要各账号；不能猜同名的局内归属。
        self.assertEqual(build_advanced_analysis(data, detail=stats)["coverage"]["detail_players"], 3)

    def test_verified_links_fill_only_in_memory_and_conflicts_are_refused(self):
        data, stats = report(), detail()
        data["players"][0]["profile_id"] = None
        before = copy.deepcopy(data)
        sources = {"game_id": 101, "detail": stats, "identity_links": [
            {"player_id": 1001, "profile_id": 10, "verified": True}]}
        model = build_advanced_analysis(data, selected_player_id=1001, sources=sources)
        self.assertEqual(model["coverage"]["detail_players"], 1)
        self.assertEqual(data, before)
        sources["identity_links"].append({"player_id": 1001, "profile_id": 20, "verified": True})
        self.assertEqual(build_advanced_analysis(data, selected_player_id=1001, sources=sources)["coverage"]["detail_players"], 0)

    def test_duplicate_local_profile_cannot_reuse_one_summary_identity(self):
        data = report()
        data["players"][1]["profile_id"] = 10
        model = build_advanced_analysis(data, detail=detail(), selected_player_id=1001)
        self.assertEqual(model["coverage"]["detail_players"], 0)

    def test_stock_and_income_preserve_raw_samples_and_never_emit_inventory_integral(self):
        model = build_advanced_analysis(report(), detail=detail(), selected_player_id=1001, reserve=100)
        result = model["by_id"]["resource_opportunity"]
        player = next(row for row in detail()["players"] if row["profile_id"] == 10)
        wood = next(row for row in result["series"] if row["resource"] == "wood" and row["measure"] == "stock")
        self.assertEqual(wood["points"], player["series"]["bank_wood"]["points"])
        self.assertEqual(wood["unit"], "资源")
        self.assertTrue(all(row["unit"] in {"资源", "资源/分"} for row in result["series"]))
        self.assertEqual(result["series"], build_advanced_analysis(report(), detail=detail(),
                            selected_player_id=1001, reserve=99999)["by_id"]["resource_opportunity"]["series"])
        for group in result["chart_groups"]:
            members = [row for row in result["series"] if row["id"] in group["series_ids"]]
            self.assertEqual({row["unit"] for row in members}, {group["unit"]})
            self.assertEqual(len({row["resource"] for row in members}), 1)
        self.assertEqual(len(model["by_id"]["technology_return"]["rows"]), 1)
        self.assertEqual(model["by_id"]["technology_return"]["rows"][0][0], "00:51")
        self.assertEqual(model["by_id"]["scouting"]["status"], "missing")
        self.assertEqual(model["by_id"]["threat_response"]["status"], "missing")
        self.assertIn("无法计算反应时间", model["by_id"]["threat_response"]["summary"])
        self.assertEqual(model["by_id"]["threat_response"]["metrics"], [])

    def test_movement_attack_and_shelter_do_not_substitute_for_scouting_or_reaction(self):
        data = report()
        data["timeline"] += [
            {"time": 40, "player_id": 1001, "type": "move", "known": True,
             "category": "movement", "target": {"x": 12, "z": 34}},
            {"time": 41, "player_id": 1001, "type": "patrol", "known": True,
             "category": "combat", "target": {"x": 56, "z": 78}},
            {"time": 42, "player_id": 1001, "type": "shelter", "known": True, "category": "ability"}]
        before = copy.deepcopy(data)
        for selected in (None, 1001):
            model = build_advanced_analysis(data, detail=detail(), sources=unit_sources(), selected_player_id=selected)
            for identity in ("scouting", "threat_response"):
                result = model["by_id"][identity]
                self.assertEqual(result["status"], "missing")
                self.assertTrue(result["missing"])
                for field in ("metrics", "series", "columns", "rows", "evidence"):
                    self.assertEqual(result[field], [], (identity, field))
            self.assertTrue(model["by_id"]["attention"]["series"])
        self.assertEqual(data, before)

    def test_reference_uses_exact_blueprint_and_does_not_invent_unmatched_event(self):
        reference = {"events": [{"time": 40, "kind": "upgrade", "pbgid": 33, "label": "计划科技"},
                                 {"time": 60, "kind": "building", "pbgid": 999, "label": "未完成的计划"}]}
        result = build_advanced_analysis(report(), detail=detail(), reference=reference,
                                         selected_player_id=1001)["by_id"]["strategy_deviation"]
        self.assertEqual(result["rows"][0][-1], 11)
        self.assertEqual(result["rows"][1][2:], ["未匹配", None])

    def test_reference_age_uses_explicit_age_number_without_guessing_blueprint(self):
        stats = detail()
        player = next(row for row in stats["players"] if row["profile_id"] == 10)
        player["events"].append({"time": 80, "profile_id": 10, "kind": "age", "new_age": 2,
                                 "pbgid": 123, "label": "进入封建", "quantity": 1})
        reference = {"events": [{"time": 75, "kind": "age", "new_age": 2, "label": "计划封建"}]}
        result = build_advanced_analysis(report(), detail=stats, reference=reference,
                                         selected_player_id=1001)["by_id"]["strategy_deviation"]
        self.assertEqual(result["rows"][0][-1], 5)

    def test_reported_attacker_byte_and_unknown_player_are_distinguished(self):
        sources = {"game_id": 101, "stls": {"created": [{"time": 5, "player_id": 1001,
                     "entity_id": 99, "entity_type": "unit_a", "x": 3, "y": 4}], "lost": [
            {"time": 30, "target_player_id": 1001, "target_entity_id": 99, "has_attacker": 1,
             "attacker_player_id": 1002, "attacker_entity_id": 88, "target_x": 5, "target_y": 6},
            {"time": 31, "target_player_id": 1001, "target_entity_id": 98, "has_attacker": 1,
             "attacker_player_id": 9999, "attacker_entity_id": 77, "target_x": 5, "target_y": 6}]}}
        model = build_advanced_analysis(report(), sources=sources, selected_player_id=1001)
        combat = model["by_id"]["combat_efficiency"]
        self.assertEqual(combat["metrics"][1]["value"], 1)
        self.assertEqual(combat["rows"][0][3], 1002)
        self.assertEqual(combat["rows"][1][3], "未明确")
        self.assertEqual(model["by_id"]["base_vulnerability"]["metrics"][0]["value"], 3)
        sources["game_id"] = 999
        self.assertEqual(build_advanced_analysis(report(), sources=sources)["coverage"]["state_lost"], 0)

    def test_unit_observations_preserve_points_filter_player_and_keep_existing_curves(self):
        sources = unit_sources()
        before = copy.deepcopy(sources)
        result = build_advanced_analysis(report(), detail=detail(), sources=sources,
                                         selected_player_id=1001)["by_id"]["army_utilization"]
        self.assertEqual([row["entity_id"] for row in result["unit_observations"]], [501])
        group = result["unit_observations"][0]
        self.assertEqual([row["time"] for row in group["observations"]], [1, 10])
        self.assertEqual([row["kind"] for row in group["observations"]], ["birth", "attack"])
        self.assertEqual(group["observations"][-1]["position"], {"x": 5, "y": 6})
        self.assertTrue(group["position_changed_observed"])
        self.assertFalse(group["continuous_path"])
        self.assertIsNone(group["world_meter_scale"])
        self.assertEqual(result["metrics"][-3:],[{'label': '有位置观测的单位', 'value': 1, 'unit': '个'},
                         {'label': '原始位置观测点', 'value': 2, 'unit': '个'},
                         {'label': '观测到位置变化的单位', 'value': 1, 'unit': '个'}])
        self.assertEqual(len(result["series"]), 3)
        self.assertIn("不插值", result["caveat"])
        self.assertEqual(sources, before)

    def test_unit_observations_require_same_game_verified_unique_profile_links(self):
        changes = ('wrong_game', 'wrong_profile', 'unverified', 'conflicting_link')
        for change in changes:
            sources = unit_sources()
            if change == 'wrong_game':
                sources['game_id'] = 999
            elif change == 'wrong_profile':
                sources['entity_observations'][0]['profile_id'] = 20
            elif change == 'unverified':
                sources['identity_links'][0]['verified'] = False
            else:
                sources['identity_links'].append({'player_id': 1001, 'profile_id': 20, 'verified': True})
            result = build_advanced_analysis(report(), sources=sources, selected_player_id=1001)["by_id"]["army_utilization"]
            self.assertEqual(result['unit_observations'], [], change)

    def test_ambiguous_unit_identity_or_simultaneous_conflicting_position_is_not_movement(self):
        for change in ('flag', 'duplicate_group', 'duplicate_birth', 'simultaneous_conflict'):
            sources = unit_sources()
            group = sources['entity_observations'][0]
            if change == 'flag':
                group['identity_ambiguous'] = True
            elif change == 'duplicate_group':
                sources['entity_observations'].append(copy.deepcopy(group))
            elif change == 'duplicate_birth':
                group['observations'][1].update(kind='birth', source='STLS.createdEntities')
            else:
                group['observations'][1]['time'] = 1
            result = build_advanced_analysis(report(), sources=sources, selected_player_id=1001)["by_id"]["army_utilization"]
            self.assertEqual(result['unit_observations'], [], change)

    def test_command_target_or_interpolated_point_cannot_enter_unit_observations(self):
        sources = unit_sources()
        group = sources['entity_observations'][0]
        group['observations'] += [
            {'time': 3, 'kind': 'move', 'position': {'x': 9, 'y': 10}, 'source': 'report.timeline'},
            {'time': 4, 'kind': 'attack', 'position': {'x': float('nan'), 'y': 10}, 'source': 'STLS.lostEntities.attacker'}]
        result = build_advanced_analysis(report(), sources=sources)["by_id"]["army_utilization"]
        self.assertEqual([len(row['observations']) for row in result['unit_observations']], [2, 2])
        self.assertFalse(result['unit_observations'][1]['position_changed_observed'])
        self.assertNotIn(7, [row['entity_id'] for row in result['unit_observations']])

    def test_team_zero_is_valid_and_nearest_input_is_not_support_delay(self):
        data, stats = report(), detail()
        result = build_advanced_analysis(data, detail=stats, selected_player_id=1001)["by_id"]["team_coordination"]
        self.assertEqual(result["rows"][0][2], 0)
        self.assertAlmostEqual(result["rows"][0][-1], 0.7)
        self.assertIn("不能把它解释成实际", result["summary"])

    def test_score_gaps_use_exact_timestamps_and_keep_missing_values(self):
        stats = detail()
        opponent = next(p for p in stats["players"] if p["profile_id"] == 20)
        opponent["series"]["score_military"]["points"][2][1] = None
        result = build_advanced_analysis(report(), detail=stats, selected_player_id=1001)["by_id"]["attack_defense_timing"]
        self.assertEqual(result["series"][0]["points"][0], [0, 0])
        self.assertIsNone(result["series"][0]["points"][2][1])

    def test_statistics_fallback_keeps_matching_existing_event_arrays(self):
        stats = detail()
        fallback = copy.deepcopy(stats)
        for player in fallback["players"]:
            player["events"] = []
        result = build_advanced_analysis(report(), detail=stats, sources={"game_id": 101, "detail": fallback})
        self.assertEqual(result["coverage"]["detail_events"], len(stats["events"]))
        self.assertEqual(fallback["players"][0]["events"], [])

    def test_isolated_duration_header_does_not_become_event_clock_offset(self):
        data, stats = report(), detail()
        stats["duration"] = 200
        sources = {"game_id": 101, "detail": stats, "stls": {"header": {"game_length": 100}}}
        model = build_advanced_analysis(data, sources=sources)
        self.assertIn("同局原始统计终时100秒与录像相符", model["coverage"]["clock_warning"])
        self.assertIn("不把孤立时长标头差异当作全部事件错位", model["coverage"]["clock_warning"])
        self.assertEqual(model["duration"], 100)

    def test_history_does_not_mix_profiles_definitions_or_duplicate_training_games(self):
        history = {"games": [
            {"profile_id": 10, "game_id": 1, "metric_definition": "v1", "features": {"库存": 100}},
            {"profile_id": 10, "game_id": 2, "metric_definition": "v1", "features": {"库存": 80}},
            {"profile_id": 20, "game_id": 3, "metric_definition": "v1", "features": {"库存": 1}},
            {"profile_id": 10, "game_id": 4, "metric_definition": "v2", "features": {"库存": 2}}],
            "training": [{"profile_id": 10, "feature": "库存", "metric_definition": "v1", "before_game_ids": [1], "after_game_ids": [2]}]}
        model = build_advanced_analysis(history=history)
        self.assertEqual(len(model["by_id"]["error_patterns"]["rows"]), 2)
        self.assertEqual(model["by_id"]["training_attribution"]["rows"][0][-1], -20)
        self.assertEqual(model["by_id"]["training_attribution"]["status"], "partial")

    def test_inputs_remain_unchanged_and_output_has_no_nan(self):
        data, stats = report(), detail()
        before = copy.deepcopy((data, stats))
        model = build_advanced_analysis(data, detail=stats)
        self.assertEqual((data, stats), before)
        json.dumps(model, ensure_ascii=False, allow_nan=False)
        self.assertEqual(build_advanced_analysis(data, detail=stats, selected_player_id=9999)["coverage"]["commands"], 0)
        self.assertEqual(build_advanced_analysis(data, detail=stats, selected_player_id="非法编号")["coverage"]["detail_players"], 0)

    def test_repeat_signature_distinguishes_research_ability_quantity_and_raw_flags(self):
        variants = (
            ({"type": "entity_upgrade", "type_id": 16, "research_type_id": 11}, {"research_type_id": 12}),
            ({"type": "ability", "type_id": 72, "argument_frame_type_id": 36, "argument_frame_raw": "00aa"},
             {"argument_frame_raw": "00bb"}),
            ({"type": "production", "type_id": 3, "queue_count_raw": 1}, {"queue_count_raw": 2}),
            ({}, {"command_flags_raw": 128, "queued_candidate": True}),
            ({"target_frame_type_id": 3, "target_extension_values_raw": [0, 1, 2, 3]},
             {"target_extension_values_raw": [0, 1, 2, 4]}),
        )
        for shared, changed in variants:
            data = report()
            for row in data["timeline"][:2]:
                row.update(shared)
            data["timeline"][1].update(changed)
            result = build_advanced_analysis(data, selected_player_id=1001)["by_id"]["ineffective_actions"]
            self.assertEqual(result["metrics"][0]["value"], 0, (shared, changed))

    def test_repeat_signature_requires_verified_full_identity_and_raw_command_header(self):
        for field, value in (("selected_refs_verified", False), ("selected_refs", []),
                             ("command_flags_raw", None), ("queued_candidate", None)):
            data = report()
            data["timeline"][1][field] = value
            result = build_advanced_analysis(data, selected_player_id=1001)["by_id"]["ineffective_actions"]
            self.assertEqual(result["metrics"][0]["value"], 0, field)
        data = report()
        # 兼容的旧紧凑码不覆盖已核原身份；选中集合次序也不制造对象变化。
        data["timeline"][1]["units"] = [999]
        data["timeline"][1]["selected_refs"].reverse()
        self.assertEqual(build_advanced_analysis(data, selected_player_id=1001)["by_id"]["ineffective_actions"]["metrics"][0]["value"], 1)
        data["timeline"][1]["selected_refs"][0]["kind"] = 1
        self.assertEqual(build_advanced_analysis(data, selected_player_id=1001)["by_id"]["ineffective_actions"]["metrics"][0]["value"], 0)

    def test_two_team_all_players_has_one_directed_gap_and_selected_opponent_reverses_it(self):
        stats = detail()
        for player in stats["players"]:
            value = 5 if player["profile_id"] == 20 else 10
            player["series"]["score_military"]["points"] = [[0, value], [20, value + 2]]
        all_players = build_advanced_analysis(report(), detail=stats)["by_id"]["attack_defense_timing"]
        opponent = build_advanced_analysis(report(), detail=stats, selected_player_id=1002)["by_id"]["attack_defense_timing"]
        self.assertEqual(len(all_players["series"]), 1)
        self.assertEqual(all_players["series"][0]["points"][0][1], 5)
        self.assertEqual(opponent["series"][0]["points"][0][1], -5)
        self.assertIn("非现有兵力", all_players["series"][0]["label"])
        self.assertEqual(all_players["series"][0]["comparison"]["aggregation"], "每玩家均值")

    def test_same_named_players_and_blueprints_are_not_merged_in_unit_composition(self):
        stats = detail()
        for player in stats["players"]:
            player["events"] = []
            if player["profile_id"] in (10, 20):
                player["name"] = "完全同名"
                player["events"] = [{"time": 60.125, "profile_id": player["profile_id"], "kind": "production",
                                     "entity_type": "Unit", "entity_name": "相同名称", "pbgid": 9, "quantity": 1}]
        result = build_advanced_analysis(report(), detail=stats)["by_id"]["tactical_adaptation"]
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual({row[2] for row in result["rows"]}, {10, 20})
        self.assertEqual([row[-1] for row in result["rows"]], [1, 1])

    def test_history_follows_selected_unique_account_and_refuses_other_account_training(self):
        games = [{"profile_id": profile, "game_id": profile * 10 + i, "metric_definition": "v1", "features": {"观测": i}}
                 for profile in (10, 20) for i in (1, 2)]
        training = [{"profile_id": profile, "feature": "观测", "metric_definition": "v1",
                     "before_game_ids": [profile * 10 + 1], "after_game_ids": [profile * 10 + 2]} for profile in (10, 20)]
        for selected, profile in ((1001, 10), (1002, 20)):
            model = build_advanced_analysis(report(), selected_player_id=selected, history={"games": games, "training": training})
            self.assertEqual({row[0] for row in model["by_id"]["error_patterns"]["rows"]}, {profile})
            self.assertEqual({row[0] for row in model["by_id"]["training_attribution"]["rows"]}, {profile})
        for selected in (9999, "非法编号"):
            model = build_advanced_analysis(report(), selected_player_id=selected, history={"games": games, "training": training})
            self.assertEqual(model["by_id"]["error_patterns"]["rows"], [])
            self.assertEqual(model["by_id"]["training_attribution"]["rows"], [])

    def test_conflicting_same_game_history_is_rejected_not_first_record_wins(self):
        games = [{"profile_id": 10, "game_id": game, "metric_definition": "v1", "features": {"观测": value}}
                 for game, value in ((1, 10), (1, 999), (2, 20))]
        model = build_advanced_analysis(report(), history={"games": games})
        self.assertEqual(model["by_id"]["error_patterns"]["rows"], [])
        games[1]["features"]["观测"] = 10
        result = build_advanced_analysis(report(), history={"games": games})["by_id"]["error_patterns"]
        self.assertEqual(len(result["rows"]), 2)

    def test_technology_uses_exact_completion_seconds_and_counts_are_not_continuous_lines(self):
        result = build_advanced_analysis(report(), detail=detail())["by_id"]
        tech = result["technology_return"]
        self.assertTrue(tech["event_records"])
        self.assertEqual(tech["series"][0]["points"][0][0], tech["event_records"][0]["time"])
        self.assertNotIn("bin_seconds", tech)
        self.assertEqual(tech["chart_type"], "bar")
        for identity in ("production_bottleneck", "army_utilization", "combat_efficiency"):
            if result[identity]["series"]:
                self.assertEqual(result[identity]["chart_type"], "bar")
                self.assertEqual(result[identity]["bin_seconds"], 30)

    def test_zero_resource_is_observed_missing_is_not_filled_and_units_are_separated(self):
        stats = detail()
        player = next(row for row in stats["players"] if row["profile_id"] == 10)
        player["series"]["bank_wood"]["points"] = [[0, 0], [20, None], [1000, 50]]
        result = build_advanced_analysis(report(), detail=stats, selected_player_id=1001)["by_id"]["resource_opportunity"]
        wood = next(row for row in result["series"] if row["resource"] == "wood" and row["measure"] == "stock")
        self.assertEqual(wood["points"], [[0, 0], [20, None], [1000, 50]])
        self.assertNotIn("integral", result)
        self.assertFalse(result["diagnostic_claim"])
        self.assertEqual(result["analysis_kind"], "observation")

    def test_event_account_cannot_conflict_with_parent_summary_player(self):
        stats = detail()
        player = next(row for row in stats["players"] if row["profile_id"] == 10)
        player["events"] = [{"time": 66, "profile_id": 20, "kind": "upgrade", "pbgid": 33,
                             "entity_name": "错误归属科技", "quantity": 1}]
        model = build_advanced_analysis(report(), detail=stats, selected_player_id=1001)
        self.assertEqual(model["coverage"]["detail_events"], 0)
        self.assertEqual(model["by_id"]["technology_return"]["status"], "missing")

    def test_unit_composition_zero_is_not_one_and_large_quantity_does_not_expand_entities(self):
        stats = detail()
        player = next(row for row in stats["players"] if row["profile_id"] == 10)
        player["events"] = [{"time": 60, "profile_id": 10, "kind": "production", "entity_type": "Unit",
                             "entity_name": "测试单位", "pbgid": 9, "quantity": 0},
                            {"time": 80, "profile_id": 10, "kind": "production", "entity_type": "Unit",
                             "entity_name": "测试单位", "pbgid": 9, "quantity": 1000000000}]
        result = build_advanced_analysis(report(), detail=stats, selected_player_id=1001)["by_id"]["tactical_adaptation"]
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0][-1], 1000000000)


if __name__ == "__main__":
    unittest.main()
