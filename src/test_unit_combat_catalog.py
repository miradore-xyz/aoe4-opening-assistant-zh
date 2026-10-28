"""官方单位目录：原命名空间/负载链/版本/变体/未知科技效果的验收。"""
from copy import deepcopy
from pathlib import Path
import unittest

from unit_combat_catalog import CombatCatalog, load_catalog, resolve_unit, resolve_stls_unit, validated_context


class RealOfficialCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=Path(__file__).resolve().parent/'文明资料/combat_catalog.json.gz'
        if not path.exists():
            raise unittest.SkipTest('官方派生目录未安装')
        cls.catalog=load_catalog(path)

    def unit(self, **kwargs):
        return resolve_unit(catalog=self.catalog,build_number=11308,**kwargs)

    def test_all_selected_namespaces_fully_parsed(self):
        coverage=self.catalog.document['coverage']
        self.assertEqual(coverage['source_member_count'],8858)
        self.assertEqual(coverage['parsed_member_count'],8858)
        self.assertEqual(coverage['errors'],[])
        self.assertEqual(coverage['namespace_counts'],{'ebps':3823,'racebps':44,'sbps':1935,'upgrade':1595,'weapon':1461})
        self.assertGreaterEqual(len(coverage['civ_branch_counts']),31)

    def test_exact_english_spearman_base_fields_and_chinese(self):
        unit=self.unit(internal_name='unit_spearman_2_eng',civ='english')
        self.assertEqual(unit['identity']['pbgid'],166311)
        self.assertEqual(unit['name_zh'],'坚韧长矛兵')
        self.assertEqual(unit['base_stats']['health']['hitpoints'],90)
        self.assertTrue(unit['is_military'])
        self.assertEqual((unit['base_cost']['food'],unit['base_cost']['wood']), (60,20))
        self.assertEqual(unit['base_cost']['total_common_resources'],80)
        self.assertTrue(unit['base_cost']['variant_invariant'])
        self.assertEqual(len(unit['weapons']),4)

    def test_squad_to_entity_is_explicit_loadout_and_distinct_ids(self):
        unit=self.unit(type_id=166417,namespace='sbps',civ='english')
        self.assertEqual(unit['identity']['pbgid'],166311)
        self.assertEqual(unit['squads'][0]['pbgid'],166417)
        self.assertIsNone(self.unit(type_id=166417,namespace='ebps'))
        self.assertIsNone(self.unit(type_id=166311,namespace='sbps'))

    def test_worker_not_inferred_as_military(self):
        unit=self.unit(internal_name='unit_villager_1_hre_ha_01',civ='hre_ha_01')
        self.assertEqual(unit['name_zh'],'镀金村民')
        self.assertIn('villager',unit['classes'])
        self.assertFalse(unit['is_military'])
        self.assertEqual(unit['base_cost']['food'],60)

    def test_campaign_variant_not_silently_merged(self):
        default=self.unit(internal_name='unit_spearman_2_eng',variant='default')
        campaign=self.unit(internal_name='unit_spearman_2_eng',variant='campaign')
        self.assertEqual(default['base_stats']['health']['hitpoints'],90)
        self.assertEqual(campaign['base_stats']['health']['hitpoints'],110)
        self.assertIsNone(self.unit(internal_name='unit_spearman_2_eng',variant='unknown_mode'))

    def test_wrong_build_and_owner_are_rejected(self):
        self.assertIsNone(resolve_unit(internal_name='unit_spearman_2_eng',build_number=11200,catalog=self.catalog))
        self.assertIsNone(self.unit(internal_name='unit_spearman_2_eng',civ='不存在的阵营'))
        context=validated_context({'metadata':{'build_number':11200}},catalog=self.catalog)
        self.assertFalse(context['version_match'])
        self.assertEqual(context['unit_costs'],{})
        self.assertEqual(context['military_pbgids'],[])

    def test_unknown_upgrade_never_changes_base_stats(self):
        unit=self.unit(internal_name='unit_spearman_2_eng',as_of_upgrades=['unknown_upgrade'])
        self.assertFalse(unit['upgrades_applied'])
        self.assertIsNone(unit['stats_after_upgrades'])
        self.assertFalse(unit['requested_upgrades'][0]['resolved'])
        self.assertEqual(unit['base_stats']['health']['hitpoints'],90)

    def test_known_upgrade_retains_scope_but_is_not_hard_applied(self):
        record=next(row for row in self.catalog.document['records'] if row['namespace']=='upgrade'
                    and row['base'].get('upgrade_bag',{}).get('filter_conditions'))
        unit=self.unit(internal_name='unit_spearman_2_eng',as_of_upgrades=[record['pbgid']])
        upgrade=unit['requested_upgrades'][0]
        self.assertEqual(upgrade['namespace'],'upgrade')
        self.assertIn('filter_conditions',upgrade['definition']['upgrade_bag'])
        self.assertFalse(upgrade['numeric_effects_resolved'])
        self.assertFalse(upgrade['applied'])
        self.assertEqual(unit['base_stats']['health']['hitpoints'],90)

    def test_official_blacksmith_modifier_has_real_enum_constant_and_scope(self):
        upgrade=self.catalog.resolve_upgrade(internal_name='upgrade_melee_damage_i',build_number=11308)
        self.assertTrue(upgrade['modifier_values_decoded'])
        melee=next(row for row in upgrade['compiled_modifier_observations'] if row.get('weapon_category',{}).get('names')==['MELEE'])
        self.assertEqual(melee['usage']['names'],['MUT_Addition'])
        self.assertEqual(melee['constant']['value'],1)
        self.assertEqual(melee['modifier_type'],'damage_weapon_modifier')
        self.assertEqual(melee['replacement_key']['name'],'upgrade_melee_damage')
        self.assertFalse(melee['fully_applicable'])
        self.assertFalse(upgrade['applied'])

    def test_full_upgrade_reference_coverage_keeps_dynamic_gaps(self):
        coverage=self.catalog.document['coverage']['compiled_upgrade_references']
        self.assertEqual(coverage['upgrades_total'],1595)
        self.assertEqual(coverage['upgrades_with_modifier_observations'],851)
        self.assertEqual(coverage['unique_constant_modifiers'],1220)
        self.assertEqual(coverage['unique_dynamic_modifiers'],669)
        self.assertEqual(coverage['fully_verified_application_count'],0)

    def test_default_compiled_modifiers_not_mixed_into_other_variant(self):
        upgrade=self.catalog.resolve_upgrade(internal_name='upgrade_melee_damage_i',variant='campaign',build_number=11308)
        if upgrade is None:
            self.skipTest('该官方UP没有campaign分支')
        self.assertFalse(upgrade['modifier_values_decoded'])
        self.assertEqual(upgrade['compiled_modifier_observations'],[])
        self.assertNotIn('compiled_modifier_observations',upgrade['definition'])

    def test_weapon_damage_and_timing_are_separate_original_fields(self):
        unit=self.unit(internal_name='unit_spearman_2_eng')
        weapon=next(row for row in unit['weapons'] if row['internal_name']=='weapon_spearman_2')
        self.assertEqual(weapon['definition']['damage']['min'],8)
        self.assertEqual(weapon['definition']['damage']['max'],8)
        self.assertEqual(weapon['definition']['cooldown']['duration']['min'],.75)
        self.assertIn('wind_up',weapon['definition']['fire'])
        self.assertNotIn('dps',weapon)

    def test_source_fields_have_actual_member_sha_and_offsets(self):
        unit=self.unit(internal_name='unit_spearman_2_eng')
        self.assertTrue(unit['source']['member'].endswith('/unit_spearman_2_eng.rgd'))
        self.assertEqual(len(unit['source']['sha256']),64)
        fields={row['path']:row for row in unit['source']['fields']}
        self.assertIn('default/health_ext/hitpoints',fields)
        self.assertIn('campaign/health_ext/hitpoints',fields)
        self.assertIsInstance(fields['default/health_ext/hitpoints']['payload_offset'],int)

    def test_returned_values_cannot_modify_cached_profiles(self):
        unit=self.unit(internal_name='unit_spearman_2_eng')
        unit['base_stats']['health']['hitpoints']=99999
        unit['classes'].append('fake')
        unit['weapons'][0]['definition']['damage']['min']=99999
        again=self.unit(internal_name='unit_spearman_2_eng')
        self.assertEqual(again['base_stats']['health']['hitpoints'],90)
        self.assertNotIn('fake',again['classes'])
        self.assertNotEqual(again['weapons'][0]['definition']['damage']['min'],99999)

    def test_validated_context_is_nominal_and_exact_build_only(self):
        context=validated_context({'metadata':{'build_number':11308}},catalog=self.catalog)
        self.assertTrue(context['version_match'])
        self.assertIn(166417,context['military_pbgids'])
        self.assertNotIn(2513732,context['military_pbgids'])
        cost=context['unit_costs']['unit_spearman_2_eng']
        self.assertTrue(cost['nominal_only'])
        self.assertTrue(cost['validated_for_scalar_comparison'])
        self.assertEqual(cost['total_common_resources'],80)

    def test_stls_camel_alias_uses_original_squad_loadout(self):
        unit=resolve_stls_unit('unit_camel_rider_4_abb',civ='abbasid',build_number=11308,catalog=self.catalog)
        self.assertEqual(unit['stls_label_namespace'],'sbps')
        self.assertEqual(unit['identity']['internal_name'],'unit_camel_4_abb')
        self.assertEqual(unit['squads'][0]['pbgid'],200052)
        self.assertTrue(unit['is_military'])

    def test_official_typo_is_not_fixed_by_guessing(self):
        unit=resolve_stls_unit('unit_szlatcha_knight_4_tem',civ='templar',build_number=11308,catalog=self.catalog)
        self.assertEqual(unit['identity']['internal_name'],'unit_szlactha_knight_4_tem')
        self.assertEqual(unit['stls_label_namespace'],'sbps')
        self.assertTrue(unit['squads'][0]['source']['fields'])


class AmbiguousCatalogTests(unittest.TestCase):
    def test_duplicate_id_is_not_arbitrarily_chosen(self):
        row={'namespace':'ebps','pbgid':10,'internal_name':'unit_a','base':{},'source':{}}
        catalog=CombatCatalog({'schema_version':1,'game_build':11308,'records':[row,dict(row,internal_name='unit_b')]})
        self.assertIsNone(catalog.resolve_unit(type_id=10,build_number=11308))

    def test_same_squad_name_or_number_does_not_create_implicit_link(self):
        ebp={'namespace':'ebps','pbgid':10,'internal_name':'unit_a','base':{},'source':{}}
        sbp={'namespace':'sbps','pbgid':10,'internal_name':'unit_a','base':{'loadout_raw':{}},'source':{}}
        catalog=CombatCatalog({'schema_version':1,'game_build':11308,'records':[ebp,sbp]})
        self.assertIsNone(catalog.resolve_unit(type_id=10,namespace='sbps',build_number=11308))


if __name__=='__main__':unittest.main()
