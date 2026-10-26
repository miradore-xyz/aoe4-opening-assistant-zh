"""地图显示名称：游戏 16.3.11308.0 的官方简体中文静态快照。

名称按相同 UCS（本地化文本）编号连接英文与简体中文，不做机器翻译。
覆盖 74 个自动匹配地图配置，兼容归档中的旧版/备用地图英文名称。
来源、字符串编号、归档摘要和覆盖边界见 文明资料/地图中文名称来源.md。
本模块仅转换界面文字，不修改缓存、回放或开局里的原始地图字段。
"""
from __future__ import annotations

import re


# 每行依次为英文原名、官方简体中文、UCS（本地化文本）编号。
_OFFICIAL_MAP_ROWS = (
    ('Acropolis', '卫城', 11218546),
    ('African Waters', '非洲海域', 11218678),
    ('Altai', '阿尔泰', 11195606),
    ('Ancient Spires', '古代尖山', 11195607),
    ('Archipelago', '群岛', 11195608),
    ('Ascension', '飞升', 11272839),
    ('Atacama', '阿塔卡马', 11256460),
    ('Baltic', '波罗的海', 11246824),
    ('Black Forest', '黑森林', 11195609),
    ('Boulder Bay', '巨石湾', 11195610),
    ('Canal', '运河', 11256461),
    ('Canyon', '大峡谷', 11158428),
    ('Carmel', '卡梅尔', 11266434),
    ('Cliffsanity', '悬崖疯狂', 11266207),
    ('Cliffside', '悬崖边', 11248552),
    ('Confluence', '汇流处', 11195611),
    ('Continental', '大陆', 11219277),
    ('Craters', '陨石坑', 11266208),
    ('Danube River', '多瑙河', 11186165),
    ('Dry Arabia', '干燥阿拉伯', 11195612),
    ('Dungeon', '地牢', 11269813),
    ('Enchanted River', '魔法河', 11246511),
    ('Enlightened Horizon', '启蒙眼界', 11269425),
    ('Fangs', '尖牙', 11272694),
    ('Flankwoods', '弗兰克伍兹森林', 11264362),
    ('Forest Ponds', '森林与池塘', 11218813),
    ('Forts', '堡垒', 11256311),
    ('Four Lakes', '四个湖', 11246510),
    ('French Pass', '法兰西隘口', 11195613),
    ('Glade', '林间空地', 11256464),
    ('Golden Heights', '黄金高地', 11248534),
    ('Golden Pit', '黄金之坑', 11220941),
    ('Gorge', '峡谷', 11252883),
    ('Haunted Gulch', '阴森幽谷', 11163430),
    ('Haywire', '失控', 11256465),
    ('Hedgemaze', '树篱迷宫', 11269433),
    ('Hidden Valley', '隐秘山谷', 11256313),
    ('Hideout', '藏身处', 11219211),
    ('High View', '高视野区', 11195615),
    ('Highwoods', '茂密树林', 11265818),
    ('Hill and Dale', '高山深谷', 11195616),
    ('Himeyama', '姫山', 11257081),
    ('King of Hill', '占山为王', 11195617),
    ('King of The Hill', '占山为王', 11158745),
    ('King of the Hill', '占山为王', 11166667),
    ('Lake Side', '湖畔', 11265843),
    ('Land MegaRandom', '陆地超级随机', 11262973),
    ('Lipany', '利帕尼', 11195618),
    ('Marshland', '沼泽地', 11247033),
    ('Mediterranean', '地中海', 11170056),
    ('MegaRandom', '超级随机', 11161307),
    ('Michi', '开道', 11268542),
    ('Migration', '迁移', 11218811),
    ('Mongolian Heights', '蒙古高原', 11195620),
    ('Mountain Clearing', '山中空地', 11220676),
    ('Mountain Lakes', '山间湖泊', 11266558),
    ('Mountain Pass', '隘口', 11195621),
    ('Nagari', '那格利', 11195622),
    ('Narrows', '海峡', 11161372),
    ('Nomadic Ridges', '游牧山脊', 11270004),
    ('Nomadic Tarns', '游牧湖群', 11269115),
    ('Oasis', '绿洲', 11162757),
    ('Ocean Gateway', '海之门户', 11269116),
    ('Peagee', '佩艾吉', 11163528),
    ('Plains', '平原', 11170302),
    ('Pond Island', '池塘岛屿', 11221278),
    ('Ponds', '池塘', 11220677),
    ('Prairie', '草原', 11218529),
    ('Relic River', '遗迹河', 11265631),
    ('Rhinelands', '莱茵兰', 11161297),
    ('River Kingdom', '河流王国', 11163592),
    ('Rocky River', '岩石河', 11256463),
    ('Rolling Rivers', '奔流', 11195624),
    ('Rugged', '崎岖之地', 11220572),
    ('Sacred Crest', '神圣徽章', 11225102),
    ('Shadow Lake', '影子湖', 11265817),
    ('Snake River', '蛇河', 11272615),
    ('Socotra', '索科特拉岛', 11264359),
    ('Sunkenlands', '沉没之地', 11257203),
    ('Taylor Strait', '泰勒海峡', 11192306),
    ('The Channel', '海峡', 11169812),
    ('The Pit', '深坑', 11218941),
    ('Thickets', '灌木丛', 11256467),
    ('Turtle', '乌龟', 11160387),
    ('Turtle Ridge', '海龟山脊', 11255745),
    ('Volcanic Island', '火山岛', 11248249),
    ('Warring Islands', '敌对岛屿', 11195625),
    ('Wasteland', '荒地', 11265814),
    ('Waterholes', '水洼', 11218812),
    ('Waterlanes', '水道', 11257204),
    ('West Lake', '西湖', 11272897),
    ('Wetlands', '湿地', 11220678),

)

MAP_NAMES = {english: chinese for english, chinese, _string_id in _OFFICIAL_MAP_ROWS}


def _name_key(value):
    """兼容接口中的大小写、空格、下划线和连字符差异，不猜测相近地图。"""
    return re.sub(r"[\s_-]+", "", value).casefold()


_MAP_NAME_LOOKUP = {_name_key(english): chinese for english, chinese in MAP_NAMES.items()}


def map_name(value):
    """已核对地图显示官方中文；未知名称直接保留原文，不附加说明。"""
    text = str(value).strip() if value is not None else ""
    return _MAP_NAME_LOOKUP.get(_name_key(text), text) if text else "未提供地图"
