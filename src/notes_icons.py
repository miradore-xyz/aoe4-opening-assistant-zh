"""补充提示的图标编辑与按文明分类选择器。

编辑时显示游戏图案，保存与复制时仍是原 RTS Overlay 的 @相对路径@ 标记。
单位名称和文明可用性取自 https://github.com/aoe4world/data 的固定提交
ce8f375004d62ac23946dcc377199f9aa50eb37d（资料版本 16.2.10604）。
仅保留与包内原图精确对应的单位；来源及权利说明沿用“文明资料”随包说明。
本模块运行时不联网，不推测解锁条件，不读取或操作游戏。
"""
from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

from PyQt5.QtCore import QMimeData, QRect, QSize, Qt, QUrl
from PyQt5.QtGui import QColor, QFont, QIcon, QPen, QTextCursor, QTextDocument, QTextFormat, QTextImageFormat
from PyQt5.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget,
    QListWidgetItem, QPushButton, QStyle, QStyledItemDelegate, QTextEdit, QVBoxLayout, QWidget,
)

from catalog import civilization_codes, load_catalog
from choice_controls import ClickComboBox
from core import NoteRenderer, civilization_text
from edit_menus import style_edit_menu
from game_icons import tile_image, tile_pixmap
from windowing import DialogBar, FramelessDialog


SOURCE = Path(__file__).resolve().parent
ASSETS = Path(sys._MEIPASS) / "assets" if hasattr(sys, "_MEIPASS") else SOURCE / "upstream/docs/assets"
TOKEN_PROPERTY = QTextFormat.UserProperty + 140
IMAGE_PREFIX = "aoe4-note:"
TOKEN_PATTERN = re.compile(r"@([^@\r\n]+)@")
KIND_NAMES = {"resource": "资源", "unit": "单位", "building": "建筑", "technology": "科技"}
ICON_SIZE = 24


# 精简资料：基础单位标识、简体名称、包内图标路径、起始时代、准确文明代码。
_UNIT_ROWS = (
    ('arbaletrier', '弓弩手', 'unit_byzantines/arbaletrier-3.webp', 3, 'by,je'),
    ('arbaletrier', '弓弩手', 'unit_french/arbaletrier-3.webp', 3, 'fr'),
    ('archer', '步弓手', 'unit_infantry/archer-2.webp', 2, 'ab,ay,by,ch,de,fr,gol,hr,je,kt,ma,mo,ot,ru,tug'),
    ('atabeg', '阿塔贝格', 'unit_ayyubids/atabeg-1.webp', 1, 'ay'),
    ('atakebune', '安宅船', 'unit_japanese/atakebune-4.webp', 4, 'ja,sen'),
    ('baghlah', '巴格拉战船', 'unit_ship/baghlah.webp', 2, 'ab,ay,de,tug'),
    ('baochuan', '宝船', 'unit_ship/baochuan.webp', 4, 'ch,gol,jin,mo,zx'),
    ('battering-ram', '攻城锤', 'unit_siege/battering-ram.webp', 1, 'ab'),
    ('battering-ram', '攻城锤', 'unit_siege/battering-ram.webp', 2, 'ay,ch,de,en,fr,gol,hl,hr,ja,je,jin,kt,ma,mo,od,ot,ru,sen,tug,zx'),
    ('bed-crossbow', '弓床弩', 'unit_jin/bed-crossbow.webp', 2, 'jin'),
    ('bedouin-skirmisher', '贝都因掷矛手', 'unit_ayyubids/bedouin-skirmisher-2.webp', 2, 'ay'),
    ('bedouin-swordsman', '贝都因剑士', 'unit_ayyubids/bedouin-swordsman-1.webp', 1, 'ay'),
    ('bogmadr', '波莫', 'unit_macedonian/bogmadr-2.webp', 2, 'mac'),
    ('bombard', '手推炮', 'unit_siege/bombard.webp', 4, 'by,ch,de,en,gol,ja,jin,ma,mac,mo,ru,sen,tug,zx'),
    ('buddhist-monk', '佛教僧侣', 'unit_japanese/buddhist-monk-3.webp', 3, 'ja'),
    ('camel-archer', '骆驼射手', 'unit_abbasid/camel-archer-2.webp', 2, 'ab'),
    ('camel-archer', '骆驼射手', 'unit_byzantines/camel-archer-2.webp', 2, 'by'),
    ('camel-lancer', '骆驼长矛骑兵', 'unit_ayyubids/camel-lancer-3.webp', 3, 'ay'),
    ('camel-rider', '骆驼骑兵', 'unit_abbasid/camel-rider-3.webp', 3, 'ab'),
    ('camel-rider', '骆驼骑兵', 'unit_byzantines/camel-rider-3.webp', 3, 'by'),
    ('cannon', '加农炮', 'unit_french/cannon-4.webp', 4, 'by,fr,je,mac'),
    ('carrack', '大帆船', 'unit_ship/carrack.webp', 4, 'by,en,fr,hl,hr,je,od,ot'),
    ('cataphract', '甲胄骑兵', 'unit_byzantines/cataphract-3.webp', 3, 'by'),
    ('cheirosiphon', '基耶罗喷火筒', 'unit_byzantines/cheirosiphon-3.webp', 3, 'by,mac'),
    ('crossbowman', '弩手', 'unit_infantry/crossbowman-3.webp', 3, 'ab,ay,by,ch,de,en,gol,hl,hr,jin,kt,mac,mo,ot,ru,tug,zx'),
    ('culverin', '长管炮', 'unit_siege/culverin-4.webp', 4, 'ab,ay,hl,hr,od'),
    ('daimyo', '大名', 'unit_sengoku/daimyo.webp', 2, 'sen'),
    ('demilancer', '老练短矛骑兵', 'unit_lancaster/demilancer.webp', 1, 'hl'),
    ('demolition-ship', '爆破船', 'unit_ship/demolition-ship.webp', 2, 'by,en,fr,hl,hr,je,kt,ma,mac,od,ot'),
    ('desert-raider', '沙漠掠夺者', 'unit_ayyubids/desert-raider-2.webp', 2, 'ay'),
    ('desert-raider', '沙漠掠夺者', 'unit_byzantines/desert-raider-2.webp', 2, 'by'),
    ('dhow', '阿拉伯帆船', 'unit_ship/dhow.webp', 2, 'ab,ay,de,ot,tug'),
    ('donso', '当佐兵', 'unit_malians/donso-1.webp', 1, 'ma'),
    ('dromon', '德罗蒙战舰', 'unit_byzantines/dromon-2.webp', 2, 'by,mac'),
    ('emissary', '使者', 'unit_jin/emissary.webp', 1, 'jin'),
    ('eruptor', '爆破手', 'unit_jin/eruptor.webp', 4, 'jin'),
    ('explosive-dhow', '爆破型阿拉伯帆船', 'unit_ship/explosive-dhow.webp', 2, 'ab,ay,de,tug'),
    ('explosive-junk', '爆破型戎克船', 'unit_ship/explosive-junk.webp', 2, 'ch,gol,ja,jin,mo,sen,zx'),
    ('fire-lancer', '火长矛骑兵', 'unit_chinese/fire-lancer-3.webp', 3, 'ch'),
    ('fishing-boat', '渔船', 'unit_ship/fishing-boat.webp', 1, 'hl,jin'),
    ('fishing-boat', '渔船', 'unit_ship/fishing-boat.webp', 2, 'ab,ay,by,ch,de,en,fr,gol,hr,ja,je,kt,ma,mac,mo,od,ot,sen,tug,zx'),
    ('galleass', '三桅战船', 'unit_french/galleass.webp', 3, 'fr,je'),
    ('galley', '箭船', 'unit_ship/galley.webp', 2, 'by,en,fr,hl,hr,je,kt,mac,od'),
    ('ghazi-raider', '加齐掠夺者', 'unit_delhi/ghazi-raider-2.webp', 2, 'de'),
    ('ghulam', '古拉姆', 'unit_abbasid/ghulam-3.webp', 3, 'ab,ay'),
    ('ghulam', '古拉姆', 'unit_byzantines/ghulam-3.webp', 3, 'by'),
    ('gilded-archer', '镀金步弓手', 'unit_dragon/gilded-archer-2.webp', 2, 'od'),
    ('gilded-crossbowman', '镀金弩手', 'unit_dragon/gilded-crossbowman-3.webp', 3, 'od'),
    ('gilded-horseman', '镀金骑手', 'unit_dragon/gilded-horseman-2.webp', 2, 'od'),
    ('gilded-knight', '镀金骑士', 'unit_dragon/gilded-knight-3.webp', 3, 'od'),
    ('gilded-landsknecht', '镀金国土佣仆', 'unit_dragon/gilded-landsknecht-3.webp', 3, 'od'),
    ('gilded-man-at-arms', '早期镀金武士', 'unit_dragon/gilded-man-at-arms-2.webp', 2, 'od'),
    ('gilded-spearman', '镀金长矛兵', 'unit_dragon/gilded-spearman-1.webp', 1, 'od'),
    ('great-bombard', '重型火炮', 'unit_ottomans/great-bombard-4.webp', 4, 'ot'),
    ('grenadier', '掷弹兵', 'unit_byzantines/grenadier-4.webp', 4, 'by,zx'),
    ('grenadier', '掷弹兵', 'unit_chinese/grenadier-4.webp', 4, 'ch'),
    ('handcannoneer', '火枪兵', 'unit_infantry/handcannoneer-4.webp', 4, 'ab,ay,by,ch,de,en,fr,gol,hl,hr,je,mac,mo,tug'),
    ('horse-archer', '骑射手', 'unit_byzantines/horse-archer-3.webp', 3, 'by'),
    ('horse-archer', '骑射手', 'unit_rus/horse-archer-3.webp', 3, 'ru'),
    ('horseman', '早期骑手', 'unit_cavalry/horseman-1.webp', 1, 'gol,mo'),
    ('hulk', '趸船', 'unit_ship/hulk.webp', 2, 'en,hl,hr,kt,od,ot'),
    ('hunting-canoe', '狩猎独木舟', 'unit_malians/hunting-canoe-2.webp', 2, 'ma'),
    ('imam', '伊玛目', 'unit_abbasid/imam.webp', 2, 'tug'),
    ('imam', '伊玛目', 'unit_religious/imam-3.webp', 3, 'ab,ma,ot'),
    ('imperial-official', '朝廷命官', 'unit_chinese/imperial-official.webp', 1, 'ch,zx'),
    ('iron-pagoda', '铁浮屠', 'unit_jin/iron-pagoda.webp', 2, 'jin'),
    ('janissary', '苏丹亲兵', 'unit_ottomans/janissary-3.webp', 3, 'ot'),
    ('javelin-thrower', '掷枪兵', 'unit_byzantines/javelin-thrower-2.webp', 2, 'by'),
    ('javelin-thrower', '掷枪兵', 'unit_malians/javelin-thrower-2.webp', 2, 'ma'),
    ('jeanne-darc-blast-cannon', '圣女贞德 - 爆破加农炮', 'unit_jeanne/jeanne-darc-blast-cannon-4.webp', 4, 'je'),
    ('jeanne-darc-hunter', '圣女贞德 - 猎人', 'unit_jeanne/jeanne-darc-hunter-2.webp', 2, 'je'),
    ('jeanne-darc-knight', '圣女贞德 - 骑士', 'unit_jeanne/jeanne-darc-knight-3.webp', 3, 'je'),
    ('jeanne-darc-markswoman', '圣女贞德 - 神射手', 'unit_jeanne/jeanne-darc-markswoman-4.webp', 4, 'je'),
    ('jeanne-darc-mounted-archer', '圣女贞德 - 骑射手', 'unit_jeanne/jeanne-darc-mounted-archer-3.webp', 3, 'je'),
    ('jeanne-darc-peasant', '圣女贞德 - 农民', 'unit_jeanne/jeanne-darc-peasant-1.webp', 1, 'je'),
    ('jeanne-darc-woman-at-arms', '圣女贞德 - 披甲女战士', 'unit_jeanne/jeanne-darc-woman-at-arms-2.webp', 2, 'je'),
    ('jeannes-champion', '贞德的冠军剑士', 'unit_jeanne/jeannes-champion-3.webp', 3, 'je'),
    ('jeannes-rider', '贞德的骑兵', 'unit_jeanne/jeannes-rider-3.webp', 3, 'je'),
    ('junk', '戎克船', 'unit_chinese/junk.webp', 2, 'ch,ja,jin,sen,zx'),
    ('katana-bannerman', '刀剑旗本武士', 'unit_japanese/katana-bannerman-2.webp', 2, 'ja'),
    ('keshik', '怯薛骑兵', 'unit_byzantines/keshik-2.webp', 2, 'by,gol'),
    ('keshik', '怯薛骑兵', 'unit_mongols/keshik-2.webp', 2, 'mo'),
    ('khan', '汗', 'unit_mongols/khan-1.webp', 1, 'mo'),
    ('khans-hunter', '汗的猎人', 'unit_mongols/khans-hunter.webp', 2, 'mo'),
    ('kharash', '肉盾', 'unit_golden_horde/kharash.webp', 2, 'gol'),
    ('king', '国王', 'unit_english/king-2.webp', 2, 'en'),
    ('knight', '早期骑士', 'unit_cavalry/knight-2.webp', 2, 'ru'),
    ('lancer', '长矛骑兵', 'unit_cavalry/lancer-3.webp', 3, 'ab,ch,de,ot,tug,zx'),
    ('landsknecht', '国土佣仆', 'unit_byzantines/landsknecht-3.webp', 3, 'by'),
    ('landsknecht', '国土佣仆', 'unit_hre/landsknecht-3.webp', 3, 'hr'),
    ('light-junk', '轻型戎克船', 'unit_ship/light-junk-2.webp', 2, 'gol,mo'),
    ('limitanei', '边防军', 'unit_byzantines/limitanei-1.webp', 1, 'by'),
    ('lodya-attack-ship', '罗德亚攻击船', 'unit_rus/lodya-attack-ship.webp', 2, 'ru'),
    ('lodya-demolition-ship', '罗德亚爆破船', 'unit_rus/lodya-demolition-ship.webp', 2, 'ru'),
    ('lodya-fishing-boat', '罗德亚渔船', 'unit_rus/lodya-fishing-boat.webp', 2, 'ru'),
    ('lodya-trade-ship', '罗德亚商船', 'unit_rus/lodya-trade-ship.webp', 2, 'ru'),
    ('lodya-transport-ship', '罗德亚运输船', 'unit_rus/lodya-transport-ship.webp', 2, 'ru'),
    ('longbowman', '长弓兵', 'unit_byzantines/longbowman-2.webp', 2, 'by'),
    ('longbowman', '长弓兵', 'unit_english/longbowman-2.webp', 2, 'en'),
    ('man-at-arms', '先锋武士', 'unit_infantry/man-at-arms-1.webp', 1, 'en'),
    ('mangonel', '轻型投石车', 'unit_siege/mangonel-3.webp', 3, 'ab,by,de,en,fr,gol,hl,hr,ja,je,kt,ma,mac,mo,od,ot,ru,sen,tug'),
    ('mangudai', '蒙古突骑', 'unit_byzantines/mangudai.webp', 2, 'by'),
    ('mangudai', '蒙古突骑', 'unit_mongols/mangudai.webp', 2, 'mo'),
    ('manjaniq', '投石机', 'unit_ayyubids/manjaniq-3.webp', 3, 'ay'),
    ('mehter', '奥斯曼军乐队', 'unit_ottomans/mehter-2.webp', 2, 'ot'),
    ('mohe-tribesman', '靺鞨族人', 'unit_jin/mohe-tribesman.webp', 2, 'jin'),
    ('monk', '僧侣', 'unit_religious/monk-3.webp', 3, 'by,ch,en,fr,hl,je,jin,kt,mac,zx'),
    ('mounted-samurai', '骑马武士', 'unit_japanese/mounted-samurai-3.webp', 3, 'ja,sen'),
    ('mounted-villager', '乡骑', 'unit_jin/mounted-villager.webp', 1, 'jin'),
    ('musofadi-gunner', '穆索法迪火枪兵', 'unit_malians/musofadi-gunner-4.webp', 4, 'ma'),
    ('musofadi-warrior', '穆索法迪战士', 'unit_byzantines/musofadi-warrior-2.webp', 2, 'by'),
    ('musofadi-warrior', '穆索法迪战士', 'unit_malians/musofadi-warrior-2.webp', 2, 'ma'),
    ('nest-of-bees', '一窝蜂', 'unit_chinese/nest-of-bees.webp', 3, 'by,ch,jin,mac,zx'),
    ('onna-bugeisha', '女武艺者', 'unit_japanese/onna-bugeisha-2.webp', 2, 'ja'),
    ('onna-musha', '女武者', 'unit_japanese/onna-musha-3.webp', 3, 'ja'),
    ('ozutsu', '大筒兵', 'unit_japanese/ozutsu-4.webp', 4, 'ja,sen'),
    ('palace-guard', '皇宫卫兵', 'unit_chinese/palace-guard-3.webp', 3, 'ch,zx'),
    ('prelate', '高级教士', 'unit_hre/prelate.webp', 1, 'hr'),
    ('prelate', '高级教士', 'unit_hre/prelate.webp', 3, 'od'),
    ('reindeer-trader', '驯鹿商人', 'unit_jin/reindeer-trader.webp', 2, 'jin'),
    ('ribauldequin', '风琴炮', 'unit_siege/ribauldequin-4.webp', 4, 'en,fr,ja,je,ot'),
    ('riddari', '瑞塔利', 'unit_macedonian/riddari-3.webp', 3, 'mac'),
    ('royal-knight', '皇家骑士', 'unit_byzantines/royal-knight-2.webp', 2, 'by,je'),
    ('royal-knight', '皇家骑士', 'unit_french/royal-knight-2.webp', 2, 'fr'),
    ('samurai', '武士', 'unit_japanese/samurai-1.webp', 1, 'ja'),
    ('scholar', '学者', 'unit_delhi/scholar.webp', 2, 'de'),
    ('scout', '侦察兵', 'unit_cavalry/scout.webp', 1, 'ab,ay,by,ch,de,en,fr,gol,hl,hr,ja,je,jin,kt,ma,mac,mo,od,ot,ru,sen,tug,zx'),
    ('shaman', '萨满', 'unit_mongols/shaman.webp', 3, 'gol,mo'),
    ('shaolin-monk', '少林僧侣', 'unit_zhuxi/shaolin-monk-3.webp', 3, 'zx'),
    ('shinobi', '忍者', 'unit_japanese/shinobi-2.webp', 2, 'ja'),
    ('shinto-priest', '神道教神主', 'unit_japanese/shinto-priest-3.webp', 3, 'ja'),
    ('siege-tower', '攻城塔', 'unit_siege/siege-tower.webp', 2, 'by,ch,de,en,fr,gol,hl,hr,ja,je,jin,kt,ma,mac,mo,od,ot,ru,sen,tug,zx'),
    ('sipahi', '西帕希骑兵', 'unit_byzantines/sipahi-2.webp', 2, 'by'),
    ('sipahi', '西帕希骑兵', 'unit_ottomans/sipahi-2.webp', 2, 'ot'),
    ('sofa', '索法骑手', 'unit_malians/sofa-2.webp', 2, 'ma'),
    ('spearman', '长矛兵', 'unit_infantry/spearman-1.webp', 1, 'ab,ay,ch,de,fr,gol,hl,hr,ja,je,jin,kt,mo,ot,ru,sen,tug,zx'),
    ('springald', '扭力弩炮', 'unit_siege/springald.webp', 3, 'ab,ay,by,ch,de,en,fr,gol,hl,hr,ja,je,kt,ma,mac,mo,od,ot,ru,sen,zx'),
    ('streltsy', '射击军', 'unit_byzantines/streltsy.webp', 4, 'by'),
    ('streltsy', '射击军', 'unit_rus/streltsy.webp', 4, 'ru'),
    ('torguud', '土尔扈特', 'unit_golden_horde/torguud.webp', 2, 'gol'),
    ('tower-elephant', '箭塔象', 'unit_delhi/tower-elephant-3.webp', 3, 'de'),
    ('tower-of-the-sultan', '苏丹之塔', 'unit_ayyubids/tower-of-the-sultan-3.webp', 3, 'ay'),
    ('traction-trebuchet', '牵引式巨型投石机', 'unit_mongols/traction-trebuchet.webp', 3, 'gol,jin,mo'),
    ('trade-ship', '商船', 'unit_ship/trade-ship.webp', 2, 'ab,ay,by,ch,de,en,fr,gol,hl,hr,ja,je,jin,ma,mac,mo,od,ot,sen,tug,zx'),
    ('trader', '商人', 'unit_worker/trader.webp', 1, 'mo,sen'),
    ('trader', '商人', 'unit_worker/trader.webp', 2, 'by,ch,de,en,fr,gol,hl,hr,ja,je,jin,ma,mac,od,ot,ru,tug,zx'),
    ('transport-ship', '运输船', 'unit_ship/transport-ship.webp', 1, 'hl'),
    ('transport-ship', '运输船', 'unit_ship/transport-ship.webp', 2, 'ab,ay,by,ch,de,en,fr,gol,hr,ja,je,jin,kt,ma,mac,mo,od,ot,sen,tug,zx'),
    ('uma-bannerman', '骑马旗本武士', 'unit_japanese/uma-bannerman-2.webp', 2, 'ja'),
    ('varangian-guard', '瓦兰吉卫队', 'unit_byzantines/varangian-guard-3.webp', 3, 'by,mac'),
    ('villager', '村民', 'unit_worker/villager-abbasid.webp', 1, 'ab,ay'),
    ('villager', '村民', 'unit_worker/villager-china.webp', 1, 'ch,zx'),
    ('villager', '村民', 'unit_worker/villager-delhi.webp', 1, 'de,tug'),
    ('villager', '村民', 'unit_worker/villager-japanese.webp', 1, 'ja,sen'),
    ('villager', '村民', 'unit_worker/villager-malians.webp', 1, 'ma'),
    ('villager', '村民', 'unit_worker/villager-mongols.webp', 1, 'gol,mo'),
    ('villager', '村民', 'unit_worker/villager-ottomans.webp', 1, 'ot'),
    ('villager', '村民', 'unit_worker/villager.webp', 1, 'by,en,fr,hl,hr,je,jin,kt,mac,ru'),
    ('war-canoe', '战争独木舟', 'unit_malians/war-canoe-2.webp', 2, 'ma'),
    ('war-cog', '柯克战船', 'unit_french/war-cog.webp', 2, 'fr,je'),
    ('war-elephant', '战象', 'unit_byzantines/war-elephant.webp', 3, 'by,tug'),
    ('war-elephant', '战象', 'unit_delhi/war-elephant.webp', 3, 'de'),
    ('war-junk', '戎克战船', 'unit_ship/war-junk.webp', 3, 'ch,gol,ja,jin,mo,sen,zx'),
    ('warrior-monk', '僧侣战士', 'unit_rus/warrior-monk.webp', 3, 'ru'),
    ('warrior-scout', '战士侦察兵', 'unit_malians/warrior-scout-2.webp', 2, 'ma'),
    ('wynguard-ranger', '温嘉德长弓兵', 'unit_english/wynguard-ranger-4.webp', 4, 'en'),
    ('xebec', '三桅帆船', 'unit_ship/xebec.webp', 4, 'ab,ay,de,tug'),
    ('yatai', '屋台', 'unit_sengoku/yatai.webp', 1, 'sen'),
    ('yuan-raider', '元朝掠夺者', 'unit_zhuxi/yuan-raider-4.webp', 4, 'zx'),
    ('yumi-ashigaru', '弓足轻', 'unit_japanese/yumi-ashigaru-2.webp', 2, 'ja,sen'),
    ('yumi-bannerman', '弓箭旗本武士', 'unit_japanese/yumi-bannerman-2.webp', 2, 'ja'),
    ('zhanma-swordsman', '斩马刀', 'unit_jin/zhanma-swordsman.webp', 4, 'jin'),
    ('zhuge-nu', '诸葛弩手', 'unit_byzantines/zhuge-nu-2.webp', 2, 'by,zx'),
    ('zhuge-nu', '诸葛弩手', 'unit_chinese/zhuge-nu-2.webp', 2, 'ch'),
)


_RESOURCE_ROWS = (
    ("resource_food", "食物"), ("resource_wood", "木材"),
    ("resource_gold", "黄金"), ("resource_stone", "石料"),
    ("sheep", "绵羊"), ("deer", "鹿"), ("boar", "野猪"),
    ("berrybush", "浆果丛"), ("fish", "鱼"), ("cattle", "牛"),
    ("gaiatreeprototypetree", "树木"), ("relics", "圣物"),
    ("sacred_sites", "圣地"), ("time", "时间"), ("repair", "修理"),
)


def icon_entries(civilization):
    """生成候选；多文明只取共同条目，未知文明仍可插入通用资源。"""
    renderer = NoteRenderer(ASSETS)
    entries = []
    for identity, name in _RESOURCE_ROWS:
        token = "resource/" + identity + ".webp"
        if renderer.image_path(token):
            entries.append(dict(id=identity, name_zh=name, icon=token, kind="resource", age=0))
    requested = civilization_codes(civilization)
    units = {}
    for identity, name, token, age, civs in _UNIT_ROWS:
        available = requested.intersection(civs.split(","))
        if not available:
            continue
        record = units.setdefault(identity, [dict(id=identity, name_zh=name, icon=token,
                                                 kind="unit", age=age), set()])
        record[1].update(available)
    for entry, available in units.values():
        if requested and available == requested and renderer.image_path(entry["icon"]):
            entries.append(entry)
    for entry in load_catalog(civilization):
        if renderer.image_path(entry.get("icon", "")):
            entries.append(entry)
    return sorted(entries, key=lambda entry: (list(KIND_NAMES).index(entry["kind"]),
                                               entry.get("age", 0), entry["name_zh"]))


def _entry_icon(entry, renderer):
    path = renderer.image_path(entry["icon"])
    return QIcon(tile_pixmap(path, entry["icon"], 48, kind=entry["kind"]))


class _IconGridDelegate(QStyledItemDelegate):
    """图案上置、名称两行；不会用图标本身的宽度裁切中文名称。"""
    def sizeHint(self, option, index):
        return QSize(92, 92)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(painter.Antialiasing)
        rect = option.rect.adjusted(3, 3, -3, -3)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        if selected or hovered:
            painter.setBrush(QColor("#293f32" if selected else "#29292f"))
            painter.setPen(QPen(QColor("#8ccba4" if selected else "#484851"), 1))
            painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 7, 7)
        icon = index.data(Qt.DecorationRole)
        if isinstance(icon, QIcon):
            icon.paint(painter, QRect(rect.center().x() - 20, rect.top() + 6, 42, 42))
        font = QFont(option.font)
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff" if selected else "#d8d8de"))
        text_rect = rect.adjusted(3, 52, -3, -3)
        painter.drawText(text_rect, Qt.AlignTop | Qt.AlignHCenter | Qt.TextWordWrap,
                         str(index.data(Qt.DisplayRole) or ""))
        painter.restore()


class NotesIconPicker(FramelessDialog):
    """紧凑网格：按文明、类别和中文名称查找，双击或回车插入。"""
    def __init__(self, civilization, parent=None):
        super().__init__(parent)
        self.civilization = copy.deepcopy(civilization)
        self.entries = icon_entries(civilization)
        self.renderer = NoteRenderer(ASSETS)
        self.setWindowTitle("插入图标")
        self.resize(640, 560)
        self.setMinimumSize(440, 380)
        shell = QVBoxLayout(self)
        shell.setContentsMargins(1, 1, 1, 1)
        shell.setSpacing(0)
        shell.addWidget(DialogBar(self, "插入图标"))
        body = QWidget()
        shell.addWidget(body, 1)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(10)
        self.civilization_label = QLabel(civilization_text(civilization))
        self.civilization_label.setTextFormat(Qt.PlainText)
        self.civilization_label.setObjectName("Muted")
        layout.addWidget(self.civilization_label)
        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.search = QLineEdit()
        self.search.setAccessibleName("搜索图标")
        self.search.setPlaceholderText("搜索图标名称")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedHeight(32)
        filters.addWidget(self.search, 1)
        self.kind = ClickComboBox()
        self.kind.setAccessibleName("图标分类")
        self.kind.addItem("全部分类", "")
        for key, name in KIND_NAMES.items():
            self.kind.addItem(name, key)
        self.kind.setFixedSize(104, 32)
        filters.addWidget(self.kind)
        layout.addLayout(filters)
        self.list = QListWidget()
        self.list.setObjectName("NotesIconGrid")
        self.list.setAccessibleName("当前文明的图标目录")
        self.list.setViewMode(QListView.IconMode)
        self.list.setResizeMode(QListView.Adjust)
        self.list.setMovement(QListView.Static)
        self.list.setIconSize(QSize(42, 42))
        self.list.setGridSize(QSize(92, 92))
        self.list.setItemDelegate(_IconGridDelegate(self.list))
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(True)
        self.list.setSpacing(3)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setMouseTracking(True)
        self.list.setStyleSheet("""
            QListWidget {background:#141416;border:1px solid #2d2d33;border-radius:8px;padding:4px;}
            QListWidget::item {background:transparent;color:#d8d8de;border:1px solid transparent;
                               border-radius:7px;padding:5px;font-size:12px;}
            QListWidget::item:hover {background:#29292f;border-color:#484851;}
            QListWidget::item:selected {background:#293f32;border:1px solid #73b58c;color:white;}
            QListWidget::item:selected:focus {border:1px solid #b2e2c2;}
        """)
        layout.addWidget(self.list, 1)
        self.empty = QLabel("没有找到图标，请调整名称或分类。")
        self.empty.setObjectName("Muted")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.selection_label = QLabel()
        self.selection_label.setTextFormat(Qt.PlainText)
        self.selection_label.setObjectName("Muted")
        bottom.addWidget(self.selection_label, 1)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setFixedHeight(32)
        self.cancel_button.clicked.connect(self.reject)
        bottom.addWidget(self.cancel_button)
        self.insert_button = QPushButton("插入图标")
        self.insert_button.setObjectName("Primary")
        self.insert_button.setFixedHeight(32)
        self.insert_button.setDefault(True)
        self.insert_button.clicked.connect(self._accept_selection)
        bottom.addWidget(self.insert_button)
        layout.addLayout(bottom)
        self.search.textChanged.connect(self._filter)
        self.kind.currentIndexChanged.connect(self._filter)
        self.list.currentItemChanged.connect(self._show_selection)
        self.list.itemDoubleClicked.connect(self._accept_selection)
        self._filter()
        self.search.setFocus(Qt.OtherFocusReason)

    def _filter(self, *_):
        query = self.search.text().strip().casefold()
        kind = self.kind.currentData()
        self.list.clear()
        for entry in self.entries:
            if kind and entry["kind"] != kind:
                continue
            if query and query not in (entry["name_zh"] + " " + entry["id"]).casefold():
                continue
            item = QListWidgetItem(_entry_icon(entry, self.renderer), entry["name_zh"])
            item.setData(Qt.UserRole, entry)
            item.setTextAlignment(Qt.AlignHCenter)
            item.setToolTip(KIND_NAMES[entry["kind"]] + " · " + entry["name_zh"])
            self.list.addItem(item)
        self.empty.setVisible(not self.list.count())
        self.insert_button.setEnabled(bool(self.list.count()))
        if self.list.count():
            self.list.setCurrentRow(0)
        self._show_selection()

    def _show_selection(self, *_):
        item = self.list.currentItem()
        self.selection_label.setText("已选：" + item.text() if item else "请选择图标")

    def _accept_selection(self, *_):
        if self.list.currentItem() is not None:
            self.accept()

    def selected_entry(self):
        item = self.list.currentItem()
        return copy.deepcopy(item.data(Qt.UserRole)) if item is not None else None


def _qt_slice(text, start, end):
    """Qt 文档位置按 UTF-16 计数，兼容中文及包含表情的用户正文。"""
    return text.encode("utf-16-le", "surrogatepass")[start * 2:end * 2].decode("utf-16-le", "surrogatepass")


class IconNotesEdit(QTextEdit):
    """保留纯文本接口，内部仅把可信包内图标变为可撤销的内嵌图片。"""
    def __init__(self, civilization_provider=None, parent=None):
        super().__init__(parent)
        self.civilization_provider = civilization_provider or (lambda: "Generic")
        self.renderer = NoteRenderer(ASSETS)
        self.setAcceptRichText(False)
        self.setAccessibleName("补充提示")
        self.setToolTip("为当前步骤补充操作说明；右键可插入当前文明的图标。每行一条。")
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def _format(self, token, name=""):
        path = self.renderer.image_path(token)
        if not path:
            return None
        image_name = IMAGE_PREFIX + token
        picture = tile_image(path, token)
        self.document().addResource(QTextDocument.ImageResource, QUrl(image_name), picture)
        image = QTextImageFormat()
        image.setName(image_name)
        image.setWidth(ICON_SIZE)
        image.setHeight(ICON_SIZE)
        image.setProperty(TOKEN_PROPERTY, token)
        image.setToolTip(name or token)
        image.setVerticalAlignment(QTextImageFormat.AlignMiddle)
        return image

    def _insert_text(self, cursor, text):
        position = 0
        for match in TOKEN_PATTERN.finditer(text):
            cursor.insertText(text[position:match.start()])
            image = self._format(match.group(1))
            if image is not None:
                cursor.insertImage(image)
            else:
                cursor.insertText(match.group(0))
            position = match.end()
        cursor.insertText(text[position:])

    def setPlainText(self, text):
        self.setUndoRedoEnabled(False)
        super().setPlainText("")
        cursor = self.textCursor()
        self._insert_text(cursor, str(text))
        cursor.movePosition(QTextCursor.Start)
        self.setTextCursor(cursor)
        self.setUndoRedoEnabled(True)
        self.document().setModified(False)

    def _plain_range(self, start=0, end=None):
        document = self.document()
        if end is None:
            end = document.characterCount() - 1
        parts = []
        block = document.begin()
        while block.isValid() and block.position() < end:
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                low = max(start, fragment.position())
                high = min(end, fragment.position() + fragment.length())
                if low < high:
                    text = _qt_slice(fragment.text(), low - fragment.position(), high - fragment.position())
                    fmt = fragment.charFormat()
                    if fmt.isImageFormat():
                        token = fmt.property(TOKEN_PROPERTY)
                        text = text.replace("\ufffc", "@" + str(token) + "@" if token else "")
                    parts.append(text)
                iterator += 1
            newline = block.position() + block.length() - 1
            if start <= newline < end and block.next().isValid():
                parts.append("\n")
            block = block.next()
        return "".join(parts)

    def toPlainText(self):
        return self._plain_range()

    def createMimeDataFromSelection(self):
        cursor = self.textCursor()
        mime = QMimeData()
        mime.setText(self._plain_range(cursor.selectionStart(), cursor.selectionEnd()))
        return mime

    def insertFromMimeData(self, source):
        if self.isReadOnly() or not source.hasText():
            return
        cursor = self.textCursor()
        cursor.beginEditBlock()
        self._insert_text(cursor, source.text())
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def insert_icon(self, entry):
        """在当前光标处插入选中图标；条目或相对路径均可。"""
        if self.isReadOnly():
            return False
        token = entry["icon"] if isinstance(entry, dict) else str(entry)
        image = self._format(token, entry.get("name_zh", "") if isinstance(entry, dict) else "")
        if image is None:
            return False
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.insertImage(image)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self.setFocus(Qt.OtherFocusReason)
        return True

    def open_icon_picker(self):
        cursor = QTextCursor(self.textCursor())
        picker = NotesIconPicker(self.civilization_provider(), self)
        if picker.exec_() == picker.Accepted:
            self.setTextCursor(cursor)
            entry = picker.selected_entry()
            if entry is not None:
                self.insert_icon(entry)
        picker.deleteLater()

    def build_context_menu(self):
        menu = self.createStandardContextMenu()
        menu.addSeparator()
        action = menu.addAction("插入图标…")
        action.setEnabled(not self.isReadOnly())
        action.triggered.connect(self.open_icon_picker)
        return style_edit_menu(menu)

    def contextMenuEvent(self, event):
        menu = self.build_context_menu()
        menu.exec_(event.globalPos())
        menu.deleteLater()
