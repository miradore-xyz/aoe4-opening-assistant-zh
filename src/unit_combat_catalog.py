"""官方单位/武器/科技属性目录：独立命名空间、构建匹配、保留未经验证的效果。

EBP=实体蓝图，SBP=编队蓝图；只沿原squad_loadout引用关联，绝不按相同编号
或相似名字合并。返回官方default（基础）原字段及campaign/xp3等差分；科技
状态树未完整解释时只返回目标范围和原引用，不猜DPS、克制胜率或实际支付。
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from functools import lru_cache
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import sys


class CombatCatalogError(ValueError):
    """目录格式、身份或版本不符合当前请求。"""


def _list(value):
    return value if isinstance(value,list) else [value] if value is not None else []


def _number(value):
    return value if type(value) in (int,float) and math.isfinite(value) else None


def _unit_loadouts(record,profile=None):
    """只取编队明确unit_list成员，不将unit_attachments（附属）当主实体。"""
    raw=(profile if profile is not None else record.get('base',{})).get('loadout_raw',{}).get('unit_list',{})
    return [row for row in _list(raw.get('loadout_data')) if isinstance(row,dict)
            and isinstance(row.get('type'),dict) and row['type'].get('$PBGMAP')=='ebps'
            and row['type'].get('$PBGNAME')]


class CombatCatalog:
    """只读运行目录；实例可留在后台分析上下文中复用。"""

    def __init__(self, document, *, digest=None):
        if (not isinstance(document,dict) or document.get('schema_version')!=1
                or type(document.get('game_build')) is not int or not isinstance(document.get('records'),list)):
            raise CombatCatalogError('官方单位目录格式不支持。')
        self.document=document
        self.game_build=document['game_build']
        self.sha256=digest
        self.by_id=defaultdict(list)
        self.by_name=defaultdict(list)
        self.squads_by_entity=defaultdict(list)
        for record in document['records']:
            ns=record.get('namespace')
            if ns not in ('ebps','sbps','weapon','upgrade','racebps'):
                raise CombatCatalogError('官方目录包含未知命名空间。')
            self.by_id[(ns,record.get('pbgid'))].append(record)
            self.by_name[(ns,record.get('internal_name'))].append(record)
            if ns=='sbps':
                for loadout in _unit_loadouts(record):
                    self.squads_by_entity[loadout['type']['$PBGNAME']].append(record)

    def _record(self, namespace, type_id=None, internal_name=None):
        if type_id is None and internal_name is None:
            return None
        candidates=(self.by_id.get((namespace,type_id),[]) if type_id is not None
                    else self.by_name.get((namespace,internal_name),[]))
        if internal_name is not None:
            candidates=[row for row in candidates if row.get('internal_name')==internal_name]
        return candidates[0] if len(candidates)==1 else None

    @staticmethod
    def _profile(record, variant):
        if variant!='default' and variant not in record.get('variants',{}):
            return None
        profile=dict(record.get('base',{}))
        if variant!='default':
            profile.update(record['variants'][variant])
        return profile

    def evidence(self, record, path_prefix=None):
        """返回原RGD成员SHA与绝对字段载荷偏移；不是SGA压缩区内的伪偏移。"""
        source={key:value for key,value in record.get('source',{}).items() if key!='field_offsets'}
        paths=self.document.get('field_paths',[])
        fields=[]
        for row in record.get('source',{}).get('field_offsets',[]):
            if len(row)!=3 or not 0<=row[0]<len(paths):
                continue
            path=paths[row[0]]
            if path_prefix is None or path.startswith(path_prefix):
                fields.append({'path':path,'payload_offset':row[1],'kind':row[2]})
        source['fields']=fields
        return source

    def _owner_allowed(self, record, civ):
        if civ is None:
            return True
        if record.get('civ')==civ:
            return True
        if any(ref.get('internal_name')==civ for ref in record.get('base',{}).get('race_refs',[])):
            return True
        # 拜占庭等雇佣外文明实体必须有本文明SBP→EBP原loadout链，不能按同名借用。
        return any(squad.get('civ')==civ for squad in self.squads_by_entity.get(record['internal_name'],[]))

    def resolve_upgrade(self, type_id=None, internal_name=None, *, variant='default', build_number=None):
        if build_number is not None and build_number!=self.game_build:
            return None
        record=self._record('upgrade',type_id,internal_name)
        if record is None:
            return None
        profile=self._profile(record,variant)
        if profile is None:
            return None
        if variant!='default':
            # 编译树研究目前仅针对default原UP引用，不能把基础分支结果混入campaign等。
            profile={key:value for key,value in profile.items() if not key.startswith('compiled_') and key!='modifier_values_decoded'}
        return {'namespace':'upgrade','pbgid':record['pbgid'],'internal_name':record['internal_name'],
                'civ':record.get('civ'),'game_build':self.game_build,'variant':variant,
                'definition':deepcopy(profile),'source':self.evidence(record),
                'numeric_effects_resolved':False,'modifier_values_decoded':bool(profile.get('modifier_values_decoded')),
                'compiled_modifier_observations':deepcopy(profile.get('compiled_modifier_observations') or []),
                'applied':False}

    def resolve_unit(self, type_id=None, internal_name=None, civ=None, as_of_upgrades=None,
                     *, namespace='ebps',variant='default',build_number=None):
        if namespace not in ('ebps','sbps') or (build_number is not None and build_number!=self.game_build):
            return None
        record=self._record(namespace,type_id,internal_name)
        if record is None:
            return None
        squad=None
        if namespace=='sbps':
            squad=record
            squad_profile=self._profile(squad,variant)
            if squad_profile is None:
                return None
            members=_unit_loadouts(squad,squad_profile)
            defaults=[row for row in members if row.get('is_default_unit') is True]
            members=defaults if defaults else members
            names={row['type']['$PBGNAME'] for row in members}
            if len(names)!=1:
                return None  # 多种实体编队不能擅自挑一种充当整个单位。
            record=self._record('ebps',internal_name=next(iter(names)))
            if record is None:
                return None
        if not self._owner_allowed(record,civ) or (squad is not None and civ is not None and squad.get('civ') not in (civ,'common','core',None)):
            return None
        profile=self._profile(record,variant)
        if profile is None:
            return None
        squads=[squad] if squad else [row for row in self.squads_by_entity.get(record['internal_name'],[])
                                      if civ is None or row.get('civ')==civ]
        classes=list(profile.get('classes') or [])
        for row in squads:
            squad_profile=self._profile(row,variant)
            if squad_profile is None:
                continue
            if record['internal_name'] not in {member['type']['$PBGNAME'] for member in _unit_loadouts(row,squad_profile)}:
                continue
            for label in squad_profile.get('classes',[]):
                if label not in classes:
                    classes.append(label)
        ui_entries=[entry for row in squads for entry in (self._profile(row,variant) or {}).get('ui_entries',[])]
        names={entry['name_zh'] for entry in ui_entries if entry.get('name_zh')}
        icons={entry['icon_name'] for entry in ui_entries if entry.get('icon_name')}
        weapons=[];missing_weapons=[]
        for ref in profile.get('weapons',[]):
            weapon=self._record('weapon',internal_name=ref['internal_name'])
            weapon_profile=self._profile(weapon,variant) if weapon is not None else None
            if weapon is None or weapon_profile is None:
                missing_weapons.append(deepcopy(ref));continue
            weapons.append({'namespace':'weapon','pbgid':weapon['pbgid'],'internal_name':weapon['internal_name'],
                            'definition':deepcopy(weapon_profile['weapon_bag']),
                            'reference_path':ref.get('path'),'source':self.evidence(weapon)})
        requested=[]
        for upgrade in _list(as_of_upgrades):
            if isinstance(upgrade,int) and not isinstance(upgrade,bool):
                resolved=self.resolve_upgrade(type_id=upgrade,variant=variant,build_number=build_number)
            elif isinstance(upgrade,str):
                resolved=self.resolve_upgrade(internal_name=upgrade,variant=variant,build_number=build_number)
            else:
                resolved=None
            requested.append(resolved or {'requested':deepcopy(upgrade),'resolved':False,'applied':False})
        cost=profile.get('cost')
        valid_cost=(isinstance(cost,dict) and all(_number(cost.get(key)) is not None and cost[key]>=0 for key in ('food','wood','gold','stone')))
        cost_invariant=valid_cost and all('cost' not in diff or diff['cost']==record['base'].get('cost') for diff in record.get('variants',{}).values())
        base_cost=None
        if valid_cost:
            base_cost={key:cost.get(key) for key in ('food','wood','gold','stone','merc_byz','popcap')}
            base_cost['total_common_resources']=sum(cost[key] for key in ('food','wood','gold','stone'))
            base_cost['variant_invariant']=cost_invariant
            base_cost['semantics']='官方基础名义成本；不代表本局实际支付、返还或实际损失金额。'
        return {'identity':{'namespace':'ebps','pbgid':record['pbgid'],'internal_name':record['internal_name'],
                            'civ':record.get('civ'),'requested_owner_civ':civ},
                'requested_namespace':namespace,'requested_type_id':type_id,
                'game_build':self.game_build,'version_match':build_number==self.game_build if build_number is not None else None,
                'variant':variant,'name_zh':next(iter(names)) if len(names)==1 else None,
                'official_icon_key':next(iter(icons)) if len(icons)==1 else profile.get('ui',{}).get('icon_name') or None,
                'classes':classes,'is_military':('military' in classes) if classes else None,
                'base_cost':base_cost,'base_stats':deepcopy({key:profile.get(key) for key in ('health','population','movement','train_seconds')}),
                'weapons':weapons,'missing_weapon_definitions':missing_weapons,
                'squads':[{'namespace':'sbps','pbgid':row['pbgid'],'internal_name':row['internal_name'],
                           'civ':row.get('civ'),'source':self.evidence(row,'default/squad_loadout_ext')} for row in squads],
                'requested_upgrades':requested,'upgrades_applied':False,
                'stats_after_upgrades':None if requested else deepcopy(profile),
                'source':self.evidence(record),'available_variants':['default',*record.get('variants',{})],
                'limitations':['武器时间字段分开返回，未推算总攻击间隔/DPS。',
                               '单位基础值不包含本局科技、光环、地形、受伤与阵型等动态效果。',
                               '科技数值效果尚未完整解码，所选科技只保留真实定义，不修改基础值。']}

    def validated_context(self, report):
        """当前构建下的明确军事SBP类与精确STLS名字→名义成本；不直接计算经济损失。"""
        build=(report.get('metadata') or {}).get('build_number') if isinstance(report,dict) else None
        if build!=self.game_build:
            return {'game_build':self.game_build,'version_match':False,'military_pbgids':[], 'unit_costs':{},
                    'source':{'kind':'official_installed_catalog','catalog_sha256':self.sha256}}
        military=[];costs={}
        for record in self.document['records']:
            if record['namespace']=='sbps' and 'military' in record.get('base',{}).get('classes',[]):
                military.append(record['pbgid'])
            if record['namespace']!='ebps':
                continue
            cost=record.get('base',{}).get('cost')
            if not isinstance(cost,dict) or not all(_number(cost.get(k)) is not None and cost[k]>=0 for k in ('food','wood','gold','stone')):
                continue
            invariant=all('cost' not in diff or diff['cost']==cost for diff in record.get('variants',{}).values())
            costs[record['internal_name']]={**{k:cost.get(k) for k in ('food','wood','gold','stone','merc_byz')},
                'total_common_resources':sum(cost[k] for k in ('food','wood','gold','stone')),
                'cost_variant_invariant':invariant,'nominal_only':True,'namespace':'ebps','pbgid':record['pbgid'],
                'game_build':self.game_build,'source':{k:record['source'][k] for k in ('member','sha256')},
                'validated_for_scalar_comparison':bool(invariant and not cost.get('merc_byz'))}
        # STLS有少量标签使用SBP原名；只沿它明确的loadout加入别名，并保留两命名空间。
        for record in self.document['records']:
            if record['namespace']!='sbps' or record['internal_name'] in costs:
                continue
            members=_unit_loadouts(record)
            names={row['type']['$PBGNAME'] for row in members}
            if len(names)!=1 or next(iter(names)) not in costs:
                continue
            entity_name=next(iter(names))
            costs[record['internal_name']]=dict(costs[entity_name],stls_label_namespace='sbps',
                stls_label=record['internal_name'],entity_internal_name=entity_name,
                squad_pbgid=record['pbgid'],squad_loadout_source={k:record['source'][k] for k in ('member','sha256')})
        return {'game_build':self.game_build,'version_match':True,'military_pbgids':sorted(set(military)),
                'military_pbgid_namespace':'sbps','unit_costs':costs,
                'source':{'kind':'official_installed_catalog','catalog_sha256':self.sha256,
                          'archive_sha256':self.document.get('source',{}).get('archive_sha256')}}


def _path():
    root=Path(sys._MEIPASS)/'assets/catalog' if hasattr(sys,'_MEIPASS') else Path(__file__).resolve().parent/'文明资料'
    return root/'combat_catalog.json.gz'


@lru_cache(maxsize=1)
def load_catalog(path=None):
    """首次可在已有后台任务中读取；后续只用内存索引，不重读安装或联网。"""
    selected=Path(path) if path is not None else _path()
    raw=selected.read_bytes()
    if len(raw)>16*1024*1024:
        raise CombatCatalogError('官方单位目录压缩文件过大。')
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        data=stream.read(128*1024*1024+1)
    if len(data)>128*1024*1024:
        raise CombatCatalogError('官方单位目录解压后过大。')
    return CombatCatalog(json.loads(data),digest=hashlib.sha256(raw).hexdigest())


def resolve_unit(type_id=None, internal_name=None, civ=None, as_of_upgrades=None, *, build_number=None,
                 namespace='ebps', variant='default', catalog=None):
    """按明确ID/原始名称查单位；冲突、错构建、缺失变体或模糊loadout返回None。"""
    return (catalog or load_catalog()).resolve_unit(type_id,internal_name,civ,as_of_upgrades,
                     build_number=build_number,namespace=namespace,variant=variant)


def validated_context(report, *, catalog=None):
    return (catalog or load_catalog()).validated_context(report)


def resolve_stls_unit(internal_name, civ=None, as_of_upgrades=None, *, build_number=None,variant='default',catalog=None):
    """按STLS原标签先查实体；仅缺失时沿精确同名SBP的真实loadout查实体。

    例如官方SBP unit_camel_rider_4_abb明确加载EBP unit_camel_4_abb；保留原标签
    与两蓝图身份，不靠拼写修正、同号或模糊名字补图。
    """
    active=catalog or load_catalog()
    namespace='ebps' if active._record('ebps',internal_name=internal_name) is not None else 'sbps'
    result=active.resolve_unit(internal_name=internal_name,civ=civ,as_of_upgrades=as_of_upgrades,
                              namespace=namespace,build_number=build_number,variant=variant)
    if result is not None:
        result['stls_label']=internal_name
        result['stls_label_namespace']=namespace
    return result
