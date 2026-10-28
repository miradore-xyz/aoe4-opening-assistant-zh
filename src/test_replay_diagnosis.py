"""真实诊断规则的合成边界测试；不使用网络、游戏或桌面。"""
import copy
import json
import unittest

from replay_diagnosis import build_replay_diagnosis, METRIC_DEFINITIONS


def inputs():
    players = [dict(id=1000, profile_id=10, name="甲", team=0, civilization="english"),
               dict(id=1001, profile_id=20, name="乙", team=1, civilization="french"),
               dict(id=1002, profile_id=30, name="队友", team=0, civilization="english")]
    report = {"source": {"game_id": 101, "sha256": "a" * 64}, "players": players,
              "metadata": {"duration": 300, "build_number": 7, "map": "测试地图", "players": players},
              "timeline": [{"player_id": 1000, "known": True}, {"player_id": 1000, "known": False}]}
    sources = {"game_id": 101, "identity_links": [dict(player_id=p['id'], profile_id=p['profile_id'], verified=True) for p in players],
               "stls": {"game_id": 101, "summary_sha256": "b" * 64, "header": {"game_length": 300}, "created": [], "lost": []},
               "detail": {"game_id": 101, "players": [dict(profile_id=p['profile_id'], name=p['name'], team=p['team'], events=[], series={}) for p in players]},
               "provenance": [{"kind": "official_statistics", "game_id": 101, "report_sha256": "a" * 64, "summary_sha256": "b" * 64}],
               "entity_observations": []}
    return report, sources


def loss(time, x, entity, target=1000, attacker=1001, target_type="unit_spear"):
    return dict(time=time, target_x=x, target_y=0, target_player_id=target, target_entity_id=entity,
                target_type=target_type, has_target=1, attacker_player_id=attacker, attacker_entity_id=999,
                attacker_type="unit_archer", has_attacker=1, offset=int(time * 100 + entity))


def completed(time, worker=True, pbgid=11):
    return dict(time=time, profile_id=10, kind="production", entity_type="Unit", is_villager=worker,
                pbgid=pbgid, quantity=1, source="build_order[].finished")


def resource_series(sources, times, stock, rate):
    player = sources['detail']['players'][0]
    player['series'] = {'bank_food': {'points': list(map(list, zip(times, stock)))},
                        'income_food': {'points': list(map(list, zip(times, rate)))}}


class ReplayDiagnosisTests(unittest.TestCase):
    def test_empty_input_is_blocked_and_does_not_invent_findings(self):
        model = build_replay_diagnosis()
        self.assertEqual(model['quality']['status'], 'blocked')
        self.assertEqual(model['findings'], [])
        self.assertEqual(model['battles'], [])
        self.assertTrue(METRIC_DEFINITIONS)
        for definition in model['metric_definitions'].values():
            for key in ('inputs', 'formula', 'unit', 'time_granularity', 'applicability', 'confidence'):
                self.assertTrue(definition[key])

    def test_cross_game_bad_digest_or_clock_blocks_all_rules(self):
        for change in ('game', 'report_digest', 'summary_digest', 'clock'):
            report, sources = inputs()
            sources['stls']['lost'] = [loss(10, 0, 1), loss(11, 1, 2)]
            if change == 'game':
                sources['game_id'] = 999
            elif change == 'report_digest':
                sources['provenance'][0]['report_sha256'] = 'c' * 64
            elif change == 'summary_digest':
                sources['stls']['summary_sha256'] = None
                sources['provenance'][0]['summary_sha256'] = None
            else:
                report['metadata']['duration'] = 400
            model = build_replay_diagnosis(report, sources)
            self.assertEqual(model['quality']['status'], 'blocked', change)
            self.assertEqual(model['battles'], [], change)

    def test_same_name_cannot_repair_missing_or_conflicting_identity(self):
        for verified in (False, True):
            report, sources = inputs()
            for player in report['players']:
                player['name'] = '相同名字'
            sources['identity_links'][0]['verified'] = verified
            if verified:
                sources['identity_links'].append(dict(player_id=1000, profile_id=20, verified=True))
            model = build_replay_diagnosis(report, sources, selected_player_id=1000)
            self.assertEqual(model['quality']['status'], 'blocked')

    def test_unknown_commands_use_player_command_denominator_and_do_not_become_errors(self):
        report, sources = inputs()
        model = build_replay_diagnosis(report, sources, selected_player_id=1000)
        self.assertEqual(model['quality']['unknown_command_rate'], .5)
        self.assertEqual(model['findings'], [])

    def test_valid_zero_team_and_combination_entity_identity_are_preserved(self):
        report, sources = inputs()
        sources['stls']['lost'] = [loss(10, 0, 5), loss(11, 1, 6, target=1001, attacker=1000)]
        battle = build_replay_diagnosis(report, sources)['battles'][0]
        self.assertEqual(battle['teams'], [0, 1])
        self.assertEqual([row['count'] for row in battle['losses']], [1, 1])
        self.assertEqual(battle['position']['unit'], '原始坐标单位')
        self.assertFalse(battle['boundary_verified'])
        self.assertTrue(all(row['resource_loss'] is None for row in battle['losses']))

    def test_unknown_attribution_and_zero_targets_are_separate_from_confirmed_enemy_losses(self):
        report, sources = inputs()
        event = loss(10, 0, 1)
        sources['stls']['lost'] = [event, dict(event, offset=9999), loss(11, 1, 2),
            dict(loss(12, 1, 3), has_attacker=0), loss(13, 1, 4, attacker=1002),
            loss(14, 1, 0), loss(15, 1, 7, target_type='0'), loss(16, 1, 8, attacker=9999)]
        model = build_replay_diagnosis(report, sources)
        self.assertEqual(sum(row['count'] for row in model['battles'][0]['losses']), 3)
        self.assertEqual(model['quality']['battle_exclusions']['duplicate_loss_record'], 1)
        self.assertEqual(model['quality']['battle_exclusions']['attribution_same_team'], 1)
        self.assertEqual(model['quality']['battle_exclusions']['target_UID_zero_or_missing'], 1)
        self.assertEqual(len(model['battles'][0]['nearby_unattributed_losses']), 1)
        self.assertTrue(all(row['total_battle_loss_count'] is None for row in model['battles'][0]['losses']))

    def test_fixed_anchor_prevents_spatial_chain_expansion(self):
        report, sources = inputs()
        sources['stls']['lost'] = [loss(index * 5, index * 20, index + 1) for index in range(4)]
        battles = build_replay_diagnosis(report, sources, settings={'battle_radius': 25})['battles']
        self.assertEqual(len(battles), 2)
        self.assertEqual([row['time_range'] for row in battles], [[0, 5], [10, 15]])

    def test_max_span_prevents_temporal_chain_expansion(self):
        report, sources = inputs()
        sources['stls']['lost'] = [loss(index * 15, 0, index + 1) for index in range(7)]
        battles = build_replay_diagnosis(report, sources, settings={'battle_max_seconds': 40})['battles']
        self.assertEqual(len(battles), 2)
        self.assertTrue(all(row['time_range'][1] - row['time_range'][0] <= 40 for row in battles))

    def test_different_loss_times_for_same_UID_and_ambiguous_identity_are_quarantined_globally(self):
        report, sources = inputs()
        sources['stls']['lost'] = [loss(10, 0, 1), loss(11, 1, 1), loss(12, 2, 2), loss(13, 3, 3)]
        sources['entity_observations'] = [dict(player_id=1000, entity_id=3, identity_ambiguous=True)]
        model = build_replay_diagnosis(report, sources)
        self.assertEqual(model['battles'], [])
        self.assertEqual(len(model['loss_ledger']['events']), 1)
        self.assertEqual(model['quality']['battle_exclusions']['conflicting_lifecycle_records'], 3)

    def test_selected_player_must_participate_as_reported_target_or_attacker(self):
        report, sources = inputs()
        sources['stls']['lost'] = [loss(10, 0, 1), loss(11, 1, 2)]
        self.assertEqual(build_replay_diagnosis(report, sources, selected_player_id=1002)['battles'], [])
        self.assertEqual(len(build_replay_diagnosis(report, sources, selected_player_id=1001)['battles']), 1)

    def test_reference_cost_requires_validation_build_and_all_four_resource_fields(self):
        report, sources = inputs()
        sources['stls']['lost'] = [loss(10, 0, 1), loss(11, 1, 2)]
        cost = dict(validated=True, build_number=7, source='原游戏成本字段', resources=dict(food=60, wood=20, gold=0, stone=0))
        context = {'unit_costs': {'unit_spear': cost}}
        model = build_replay_diagnosis(report, sources, context=context)
        self.assertEqual(model['battles'][0]['losses'][0]['resource_loss'], 160)
        for key, value in (('validated', False), ('build_number', 8), ('resources', dict(food=60, wood=20))):
            changed = copy.deepcopy(context)
            changed['unit_costs']['unit_spear'][key] = value
            model = build_replay_diagnosis(report, sources, context=changed)
            self.assertIsNone(model['battles'][0]['losses'][0]['resource_loss'])

    def test_worker_gap_is_between_completed_events_not_queue_or_tail_idle(self):
        report, sources = inputs()
        sources['detail']['players'][0]['events'] = [completed(10), completed(30), completed(100),
                                                   dict(completed(0), kind='initial'), dict(completed(200), kind='queue')]
        model = build_replay_diagnosis(report, sources, selected_player_id=1000)
        findings = [row for row in model['observations'] if row['rule_id'] == 'worker_completion_gaps']
        self.assertEqual([row['time_range'] for row in findings], [[30, 100]])
        self.assertEqual(findings[0]['metrics'][0]['value'], 70)
        self.assertIn('不能直接判为停产', findings[0]['inference'])
        self.assertTrue(any('实际队列' in text for text in findings[0]['unknowns']))
        self.assertTrue(all(row['time_range'][1] < 200 for row in findings))

    def test_nonvillager_does_not_mean_military_and_military_requires_exact_current_classification(self):
        report, sources = inputs()
        sources['detail']['players'][0]['events'] = [completed(10, False, 22), completed(120, False, 22)]
        self.assertEqual(build_replay_diagnosis(report, sources)['findings'], [])
        row = dict(role='military', validated=True, build_number=7, source='原蓝图军事分类')
        context = {'unit_classifications': {'22': row}}
        findings = build_replay_diagnosis(report, sources, context=context)['observations']
        self.assertEqual(findings[0]['rule_id'], 'military_completion_gaps')
        row['build_number'] = 8
        self.assertEqual(build_replay_diagnosis(report, sources, context=context)['findings'], [])

    def test_single_completion_does_not_create_production_gap(self):
        report, sources = inputs()
        sources['detail']['players'][0]['events'] = [completed(10)]
        model = build_replay_diagnosis(report, sources)
        self.assertEqual(model['findings'], [])
        self.assertEqual(model['quality']['metrics']['worker_completion_gaps']['status'], 'missing')

    def test_reserve_uses_positive_rate_normalization_and_explicit_nonzero_observation_standard(self):
        report, sources = inputs()
        resource_series(sources, [0, 20, 40, 60, 80], [600] * 5, [100] * 5)
        model = build_replay_diagnosis(report, sources, selected_player_id=1000)
        finding = next(row for row in model['observations'] if row['rule_id'] == 'resource_high_reserve_spans')
        self.assertEqual(model['settings']['reserve_minutes'], 2)
        self.assertEqual(finding['series'][0]['points'], [[0, 6], [20, 6], [40, 6], [60, 6], [80, 6]])
        self.assertEqual(finding['time_range'], [0, 80])
        self.assertIn('不能据此认定资源浪费', finding['inference'])

    def test_missing_stock_or_rate_and_large_sampling_gap_break_high_reserve_spans(self):
        for change in ('stock', 'rate', 'gap'):
            report, sources = inputs()
            times, stock, rate = [0, 20, 40, 60, 80], [600] * 5, [100] * 5
            if change == 'stock':
                stock[2] = None
            elif change == 'rate':
                rate[2] = None
            else:
                times = [0, 20, 100, 120, 140]
            resource_series(sources, times, stock, rate)
            model = build_replay_diagnosis(report, sources)
            self.assertEqual(model['findings'], [], change)

    def test_zero_or_negative_income_does_not_become_infinite_reserve(self):
        for rate in (0, -1, None):
            report, sources = inputs()
            resource_series(sources, [0, 20, 40, 60], [1000] * 4, [rate] * 4)
            model = build_replay_diagnosis(report, sources)
            self.assertEqual(model['findings'], [])
            self.assertEqual(model['quality']['metrics']['resource_reserve_minutes']['status'], 'missing')
            json.dumps(model, allow_nan=False)

    def test_bad_series_axis_is_not_sorted_against_values(self):
        report, sources = inputs()
        resource_series(sources, [0, 60, 20, 80], [600] * 4, [100] * 4)
        self.assertEqual(build_replay_diagnosis(report, sources)['findings'], [])

    def test_fact_inference_unknowns_and_training_are_separate_no_minimum_finding_count(self):
        report, sources = inputs()
        sources['detail']['players'][0]['events'] = [completed(10), completed(100)]
        model = build_replay_diagnosis(report, sources)
        self.assertEqual(len(model['findings']), 0)
        finding = model['observations'][0]
        self.assertEqual(finding['grade'], 'needs_review')
        self.assertEqual(finding['fact_grade'], 'confirmed_fact')
        self.assertFalse(finding['causal_claim'])
        self.assertEqual(finding['kind'], 'observation')
        for key in ('facts', 'inference', 'unknowns', 'train_goal', 'time_range', 'evidence'):
            self.assertTrue(finding[key])

    def test_readonly_inputs_serializable_output_and_invalid_settings(self):
        report, sources = inputs()
        before = copy.deepcopy((report, sources))
        model = build_replay_diagnosis(report, sources)
        self.assertEqual((report, sources), before)
        json.dumps(model, allow_nan=False)
        self.assertEqual(build_replay_diagnosis(report, sources, selected_player_id='无效')['quality']['status'], 'blocked')
        for value in (0, -1, True, float('inf')):
            with self.assertRaises(ValueError):
                build_replay_diagnosis(report, sources, settings={'reserve_minutes': value})


class LossLedgerClosureTests(unittest.TestCase):
    def model(self, events, *, context=None, selected=None):
        report, sources = inputs()
        sources['stls']['lost'] = events
        return build_replay_diagnosis(report, sources, context=context, selected_player_id=selected)

    def test_attacker_zero_UID_and_unknown_type_keep_the_known_target_loss(self):
        events = [dict(loss(10, 0, 1), attacker_entity_id=0, attacker_type='building_tower'),
                  dict(loss(11, 1, 2), attacker_entity_id=0, attacker_type='0')]
        model = self.model(events)
        self.assertEqual(len(model['battles'][0]['events']), 2)
        self.assertEqual(model['quality']['battle_exclusions']['enemy_loss_with_unresolved_attacker_entity'], 2)
        self.assertTrue(all(not e['attacker_entity_resolved'] for e in model['battles'][0]['events']))

    def test_zero_UID_buildings_at_same_time_are_independent_unknown_targets(self):
        model = self.model([loss(10, 0, 0, target_type='building_wall'), loss(10, 2, 0, target_type='building_wall')])
        self.assertEqual(model['battles'], [])
        self.assertEqual(len(model['loss_ledger']['unidentified_target_records']), 2)

    def test_global_conflict_cannot_reappear_in_another_candidate(self):
        model = self.model([loss(10, 0, 1), loss(11, 1, 2), loss(100, 100, 1), loss(101, 101, 3)])
        self.assertEqual(model['battles'], [])
        self.assertEqual([e['target_entity_id'] for e in model['loss_ledger']['events']], [2, 3])

    def test_cross_player_same_raw_UID_is_quarantined_not_treated_as_two_units(self):
        model = self.model([loss(10, 0, 1), loss(11, 1, 1, target=1001, attacker=1000)])
        self.assertEqual(model['loss_ledger']['events'], [])
        self.assertEqual(len(model['loss_ledger']['quarantined_lifecycles']), 1)

    def test_exact_duplicate_records_do_not_inflate_totals_and_keep_original_offsets(self):
        event = loss(10, 0, 1)
        model = self.model([event, dict(event, offset=9999), loss(11, 1, 2)])
        first = model['battles'][0]['events'][0]
        self.assertEqual(sum(s['count'] for s in model['battles'][0]['losses']), 2)
        self.assertEqual(first['source_offsets'], [1001, 9999])

    def test_unknown_source_is_nearby_context_without_fake_enemy_loss(self):
        model = self.model([loss(10, 0, 1), dict(loss(11, 1, 2), has_attacker=0), loss(12, 2, 3)])
        battle = model['battles'][0]
        self.assertEqual(sum(s['count'] for s in battle['losses']), 2)
        self.assertEqual(len(battle['nearby_unattributed_losses']), 1)
        self.assertIsNone(battle['nearby_unattributed_losses'][0]['attacker_player_id'])
        self.assertTrue(all(s['total_battle_loss_count'] is None for s in battle['losses']))

    def test_ambiguous_attacker_does_not_invalidate_clear_victim(self):
        report, sources = inputs()
        sources['stls']['lost'] = [loss(10, 0, 1), loss(11, 1, 2)]
        sources['entity_observations'] = [dict(player_id=1001, entity_id=999, identity_ambiguous=True)]
        model = build_replay_diagnosis(report, sources)
        self.assertEqual(len(model['battles'][0]['events']), 2)
        self.assertFalse(model['battles'][0]['events'][0]['attacker_entity_resolved'])

    def test_official_flat_costs_are_adapted_and_partial_cost_is_not_zero(self):
        row = dict(game_build=7, source={'member': 'original.rgd', 'sha256': 'd' * 64}, nominal_only=True,
                   namespace='ebps', cost_variant_invariant=True, validated_for_scalar_comparison=True,
                   food=60, wood=20, gold=0, stone=0, merc_byz=0)
        context = dict(game_build=7, unit_costs={'unit_spear': row})
        model = self.model([loss(10, 0, 1), loss(11, 1, 2)], context=context)
        self.assertEqual(model['battles'][0]['losses'][0]['resource_loss'], 160)
        mixed = self.model([loss(10, 0, 1), loss(11, 1, 2, target_type='unpriced_unit')], context=context)
        side = mixed['battles'][0]['losses'][0]
        self.assertIsNone(side['resource_loss'])
        self.assertEqual(side['known_reference_resources']['food'], 60)
        self.assertEqual(side['unpriced_loss_count'], 1)
        self.assertIsNone(mixed['battles'][0]['losses'][1]['resource_loss'])

    def test_official_variant_currency_or_wrong_build_never_becomes_scalar(self):
        base = dict(game_build=7, source='official', nominal_only=True, namespace='ebps',
                    cost_variant_invariant=True, validated_for_scalar_comparison=True,
                    food=60, wood=20, gold=0, stone=0, merc_byz=0)
        for key, value in [('cost_variant_invariant', False), ('merc_byz', 30), ('game_build', 8), ('wood', None)]:
            row = dict(base);row[key] = value
            side = self.model([loss(10, 0, 1), loss(11, 1, 2)], context={'game_build': 7, 'unit_costs': {'unit_spear': row}})['battles'][0]['losses'][0]
            self.assertIsNone(side['resource_loss'], key)
            self.assertEqual(side['unpriced_loss_count'], 2)

    def test_stable_ID_does_not_depend_on_input_order_or_player_filter(self):
        events = [loss(10, 0, 1), loss(11, 1, 2), loss(100, 100, 3, target=1002), loss(101, 101, 4, target=1002)]
        all_model = self.model(events)
        reversed_model = self.model(list(reversed(events)))
        selected = self.model(events, selected=1002)
        self.assertEqual([b['id'] for b in all_model['battles']], [b['id'] for b in reversed_model['battles']])
        self.assertEqual(selected['battles'][0]['id'], all_model['battles'][1]['id'])
        self.assertEqual(selected['battles'][0]['review_rank'], 2)

    def test_setup_team_zero_is_authoritative_and_partition_labels_may_differ(self):
        report, sources = inputs()
        for p in report['players']:
            p['team_evidence'] = dict(value=p['team'], source='replay_setup', setup_version=60, offset=p['id'])
        for p in sources['detail']['players']:
            p['team'] += 1
        sources['stls']['lost'] = [loss(10, 0, 1), loss(11, 1, 2)]
        model = build_replay_diagnosis(report, sources)
        self.assertEqual(model['battles'][0]['teams'], [0, 1])
        sources['detail']['players'][2]['team'] = 2
        blocked = build_replay_diagnosis(report, sources)
        self.assertEqual(blocked['quality']['status'], 'blocked')
        self.assertEqual(blocked['battles'], [])

    def test_setup_team_can_supply_missing_summary_team_without_guessing(self):
        report, sources = inputs()
        for p in report['players']:
            p['team_evidence'] = dict(value=p['team'], source='replay_setup', setup_version=58, offset=p['id'])
        for p in sources['detail']['players']:
            p['team'] = None
        sources['stls']['lost'] = [loss(10, 0, 1), loss(11, 1, 2)]
        self.assertEqual(len(build_replay_diagnosis(report, sources)['battles']), 1)
        for p in report['players']:
            p.pop('team_evidence')
        self.assertEqual(build_replay_diagnosis(report, sources)['battles'], [])

    def test_nonfinite_or_after_replay_end_is_not_silently_retimed(self):
        model = self.model([loss(10, 0, 1), loss(11, 1, 2), loss(301, 2, 3), loss(12, float('nan'), 1)])
        self.assertEqual(model['battles'], [])
        self.assertEqual(model['loss_ledger']['events'][0]['target_entity_id'], 2)
        json.dumps(model, allow_nan=False)

    def test_contract_fields_and_map_focus_points_remain_real_events(self):
        model = self.model([loss(10, 0, 1), loss(11, 1, 2)])
        b = model['findings'][0]
        for key in ('type', 'players', 'teams', 'entities', 'region', 'facts', 'reference', 'gap', 'evidence',
                    'confidence', 'severity', 'impacts', 'recommendations', 'training_goal', 'rule_version'):
            self.assertIn(key, b)
        self.assertEqual(b['battle_id'], b['diagnostic_id'])
        self.assertEqual(b['events'][0]['target_x'], 0)
        self.assertEqual(b['events'][0]['time'], 10)
        self.assertEqual(b['severity']['level'], 'review')
        self.assertEqual(b['kind'], 'review_segment')
        self.assertFalse(b['causal_claim'])

    def test_top_three_are_loss_segments_and_observations_do_not_crowd_them(self):
        report, sources = inputs()
        sources['stls']['lost'] = [loss(start + j, index * 100, index * 10 + j + 1)
                                  for index, start in enumerate((10, 70, 130, 190)) for j in range(index + 2)]
        sources['detail']['players'][0]['events'] = [completed(10), completed(200)]
        resource_series(sources, [0, 20, 40, 60, 80], [600] * 5, [100] * 5)
        model = build_replay_diagnosis(report, sources)
        self.assertEqual(len(model['findings']), 3)
        self.assertTrue(all(f['rule_id'] == 'battle_candidates' for f in model['findings']))
        self.assertTrue(all(o['kind'] == 'observation' for o in model['observations']))
        self.assertEqual([len(f['events']) for f in model['findings']], [5, 4, 3])

    def test_capabilities_separate_original_facts_formula_candidates_and_missing_state(self):
        model = self.model([loss(10, 0, 1), loss(11, 1, 2)])
        inventory = {r['id']: r for r in model['capability_inventory']}
        self.assertEqual(inventory['loss_events']['grade'], 'A')
        self.assertEqual(inventory['canonical_unit_losses']['grade'], 'B')
        self.assertEqual(inventory['battle_grouping']['grade'], 'C')
        for key in ('nominal_loss_cost', 'queue_state', 'scouting', 'first_visible'):
            self.assertEqual(inventory[key]['grade'], 'D')
        cost = dict(validated=True, build_number=7, source='same-build', resources=dict(food=60, wood=20, gold=0, stone=0))
        priced = self.model([loss(10, 0, 1), loss(11, 1, 2, target_type='unknown')], context={'unit_costs': {'unit_spear': cost}})
        value = next(r for r in priced['capability_inventory'] if r['id'] == 'nominal_loss_cost')
        self.assertEqual(value['grade'], 'B')
        self.assertEqual(value['coverage']['priced'], 1)
        self.assertFalse(value['coverage']['complete'])

    def test_failed_source_gate_downgrades_all_capabilities_to_D(self):
        report, sources = inputs()
        sources['stls']['lost'] = [loss(10, 0, 1), loss(11, 1, 2)]
        sources['game_id'] = 999
        model = build_replay_diagnosis(report, sources)
        self.assertTrue(model['capability_inventory'])
        self.assertTrue(all(r['grade'] == 'D' and not r['available'] for r in model['capability_inventory']))

    def test_advice_uses_observed_unit_types_workers_time_location_and_one_conditional_training_item(self):
        report, sources = inputs()
        sources['stls']['lost'] = [loss(10, 10, 1), loss(11, 11, 2, target_type='unit_villager'),
                                  loss(12, 12, 3, target_type='unit_villager')]
        sources['unit_state'] = dict(game_id=101, game_build=7, report_sha256='a' * 64, units=[
            dict(player_id=1000, original_id=1, definitions={'unit_spear': dict(name_zh='长矛兵', classes=['military'], is_military=True)}),
            dict(player_id=1000, original_id=2, definitions={'unit_villager': dict(name_zh='村民', classes=['villager'], is_military=False)}),
            dict(player_id=1000, original_id=3, definitions={'unit_villager': dict(name_zh='村民', classes=['villager'], is_military=False)})])
        finding = build_replay_diagnosis(report, sources)['findings'][0]
        advice = finding['recommendations'][0]
        for text in ('记录2名村民', '0:11', '原坐标', '外出工人单独编组', '撤往已确认安全的位置'):
            self.assertIn(text, advice)
        self.assertFalse(finding['training_goal']['reaction_time_measured'])
        self.assertIsNotNone(finding['training_goal']['condition'])
        self.assertEqual(finding['reference']['label'], '同候选双方记录损失与基础名义成本')

    def test_partial_setup_proof_maps_same_public_partition_instead_of_mixing_team_numbers(self):
        report, sources = inputs()
        for person in sources['detail']['players']:
            person['team'] += 1
        report['players'][0]['team_evidence'] = dict(value=0, source='replay_setup', setup_version=60, offset=123)
        sources['stls']['lost'] = [loss(10, 0, 1, attacker=1002), loss(11, 1, 2, attacker=1002)]
        model = build_replay_diagnosis(report, sources)
        self.assertEqual(model['battles'], [])
        self.assertEqual(model['quality']['battle_exclusions']['attribution_same_team'], 2)
        self.assertEqual(model['quality']['team_sources']['1002'], 'public_partition_mapped_to_replay_setup')

    def test_partial_setup_proof_leaves_unmapped_public_partition_unknown(self):
        report, sources = inputs()
        for person in sources['detail']['players']:
            person['team'] += 1
        report['players'][0]['team_evidence'] = dict(value=0, source='replay_setup', setup_version=60, offset=123)
        sources['stls']['lost'] = [loss(10, 0, 1), loss(11, 1, 2)]
        model = build_replay_diagnosis(report, sources)
        self.assertEqual(model['battles'], [])
        self.assertEqual(model['quality']['battle_exclusions']['attribution_unknown_team'], 2)
        report['players'][1]['team_evidence'] = dict(value=1, source='replay_setup', setup_version=60, offset=223)
        self.assertEqual(len(build_replay_diagnosis(report, sources)['battles']), 1)

    def test_selected_victim_receives_only_personal_advice_with_same_stable_candidate_ID(self):
        report, sources = inputs()
        sources['stls']['lost'] = [loss(10, 0, 1), loss(11, 1, 2, target=1001, attacker=1000)]
        sources['unit_state'] = dict(game_id=101, game_build=7, report_sha256='a' * 64, units=[
            dict(player_id=1000, original_id=1, definitions={'unit_spear': dict(name_zh='长矛兵', classes=['military'], is_military=True)}),
            dict(player_id=1001, original_id=2, definitions={'unit_spear': dict(name_zh='长矛兵', classes=['military'], is_military=True)})])
        all_model = build_replay_diagnosis(report, sources)
        a = build_replay_diagnosis(report, sources, selected_player_id=1000)['battles'][0]
        b = build_replay_diagnosis(report, sources, selected_player_id=1001)['battles'][0]
        self.assertEqual(a['id'], b['id'])
        self.assertIn('甲在', a['recommendations'][0])
        self.assertNotIn('乙在', a['recommendations'][0])
        self.assertIn('乙在', b['recommendations'][0])
        self.assertNotIn('甲在', b['recommendations'][0])
        self.assertEqual(len(all_model['battles'][0]['recommendations']), 2)
        self.assertEqual(a['training_goal']['player_id'], 1000)
        self.assertEqual(b['training_goal']['player_id'], 1001)

    def test_attacker_only_participant_is_not_given_opponents_loss_training(self):
        report, sources = inputs()
        sources['stls']['lost'] = [loss(10, 0, 1), loss(11, 1, 2)]
        battle = build_replay_diagnosis(report, sources, selected_player_id=1001)['battles'][0]
        self.assertEqual(battle['recommendations'], [])
        self.assertIsNone(battle['training_goal'])
        self.assertNotIn('1001', battle['recommendations_by_player'])


if __name__ == '__main__':
    unittest.main()
