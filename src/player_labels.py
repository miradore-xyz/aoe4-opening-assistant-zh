"""玩家显示标签：数据仍使用账号或局内编号，同名时才补充可辨认编号。"""
from collections import Counter
import unicodedata


def _identifier(value):
    """账号编号须为正整数；此处只格式化，不把局内编号当账号。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return str(value)
    if isinstance(value, str) and value.isdecimal() and int(value) > 0:
        return str(int(value))
    return None


def player_display_labels(players, id_key="id"):
    """按输入顺序返回标签；不修改昵称、编号或原始比赛资料。"""
    players = list(players)
    names = [str(player.get("name") or "未提供昵称").strip() or "未提供昵称" for player in players]
    keys = [unicodedata.normalize("NFC", name).casefold() for name in names]
    counts = Counter(keys)
    labels = []
    for index, (player, name, key) in enumerate(zip(players, names, keys)):
        if counts[key] == 1:
            labels.append(name)
            continue
        account = _identifier(player.get("profile_id"))
        local = player.get(id_key) if id_key != "profile_id" else None
        suffix = (f"账号{account}" if account else f"局内{local}" if local is not None
                  else f"席位{index + 1}")
        labels.append(f"{name} · {suffix}")
    return labels


def player_labels(players, id_key="id"):
    """返回原编号到显示标签的映射；缺失编号不编造数据关联键。"""
    players = list(players)
    return {player[id_key]: label for player, label in zip(players, player_display_labels(players, id_key))
            if player.get(id_key) is not None}
