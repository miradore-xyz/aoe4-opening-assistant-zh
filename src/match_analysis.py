"""免登录公开战绩：有界查询、本地缓存和按模式统计。

profile=玩家资料，match/game=对局，snapshot=本地数据快照，cache=缓存。
仅访问 AoE4 World 文档中的公开接口；本模块不读取游戏内存或动态复盘接口。
"""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


BASE_URL = "https://aoe4world.com"
API_SOURCE = BASE_URL + "/api"
USER_AGENT = "EmpireOpeningAssistant/1.5 (public match history; user initiated desktop query)"
MAX_GAMES = 50
CACHE_SECONDS = 10 * 60
MODE_NAMES = {
    "rm_1v1": "单人排位", "rm_2v2": "排位 2 对 2", "rm_3v3": "排位 3 对 3",
    "rm_4v4": "排位 4 对 4", "rm_solo": "单人排位", "rm_team": "团队排位",
    "qm_1v1": "快速 1 对 1", "qm_2v2": "快速 2 对 2", "qm_3v3": "快速 3 对 3",
    "qm_4v4": "快速 4 对 4", "qm_ffa": "自由混战", "custom": "自定义对局",
    "unknown": "未提供模式",
}
CIV_NAMES = {
    "abbasid_dynasty": "阿拔斯王朝", "ayyubids": "阿尤布王朝", "byzantines": "拜占庭",
    "chinese": "中国", "delhi_sultanate": "德里苏丹国", "english": "英格兰",
    "french": "法兰西", "golden_horde": "金帐汗国", "holy_roman_empire": "神圣罗马帝国",
    "house_of_lancaster": "兰开斯特家族", "japanese": "日本", "jeanne_darc": "圣女贞德",
    "jin_dynasty": "金朝", "knights_templar": "圣殿骑士团", "macedonian_dynasty": "马其顿王朝",
    "malians": "马里", "mongols": "蒙古", "order_of_the_dragon": "龙之骑士团",
    "ottomans": "奥斯曼", "rus": "罗斯", "sengoku_daimyo": "战国大名",
    "tughlaq_dynasty": "图格鲁克王朝", "zhu_xis_legacy": "朱熹的传承",
}
MAP_NAMES = {"Dry Arabia": "阿拉伯旱地", "Lipany": "利帕尼", "High View": "高视角",
             "Hill and Dale": "高山深谷", "Mongolian Heights": "蒙古高原",
             "Black Forest": "黑森林", "Ancient Spires": "古代尖塔", "Danube River": "多瑙河",
             "French Pass": "法国山隘", "Mountain Pass": "山隘", "Boulder Bay": "巨石海湾",
             "Archipelago": "群岛", "Baltic": "波罗的海", "Cliffside": "悬崖边",
             "Gorge": "峡谷", "Himeyama": "姬山", "West Lake": "西湖"}


class MatchError(ValueError):
    """可直接展示给用户的查询错误。"""


def utc_now():
    return datetime.now(timezone.utc)


def _iso(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError):
        return None


def _number(value):
    """布尔值、空串和非有限数字均不能作为评分或时长。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _positive_id(value):
    text = str(value)
    return int(text) if re.fullmatch(r"[1-9][0-9]{0,11}", text) else None


def parse_player_input(value):
    """返回玩家编号或待搜索昵称；URL 只接受 AoE4 World 的个人主页。"""
    text = str(value).strip()
    if not text:
        raise MatchError("请输入玩家昵称、玩家编号或 AoE4 World 个人主页链接。")
    number = _positive_id(text)
    if number is not None:
        return number
    if text.isdecimal():
        raise MatchError("玩家编号必须是有效的正整数。")
    if "://" in text or text.lower().startswith(("www.", "aoe4world.com/")):
        candidate = text if "://" in text else "https://" + text
        parsed = urlparse(candidate)
        try:
            valid_port = parsed.port in (None, 443)
        except ValueError:
            valid_port = False
        path = re.fullmatch(r"/players/([1-9][0-9]{0,11})(?:-[^/?#]+)?/?", parsed.path)
        if (parsed.scheme != "https" or parsed.hostname not in ("aoe4world.com", "www.aoe4world.com")
                or parsed.username or parsed.password or not valid_port or not path):
            raise MatchError("请使用 https://aoe4world.com/players/玩家编号 形式的个人主页链接。")
        return int(path.group(1))
    if len(text) < 3:
        raise MatchError("昵称搜索至少需要 3 个字符；较短昵称请使用玩家编号或个人主页链接。")
    if len(text) > 120 or any(ord(char) < 32 for char in text):
        raise MatchError("昵称长度或格式无效。")
    return text


def mode_name(value):
    text = str(value or "unknown")
    if text.endswith("_console"):
        return MODE_NAMES.get(text[:-8], text[:-8] + "（模式代码）") + " · 主机"
    return MODE_NAMES.get(text, text + "（模式代码）")


def civilization_name(value):
    return CIV_NAMES.get(str(value), str(value) + "（文明代码）") if value else "未提供文明"


def map_name(value):
    return MAP_NAMES.get(str(value), str(value) + "（地图原名）") if value else "未提供地图"


def summary_url(profile_id, game_id):
    """仅生成供人点击的网页链接；不自动请求该动态页面。"""
    player, game = _positive_id(profile_id), _positive_id(game_id)
    return f"{BASE_URL}/players/{player}/games/{game}/summary" if player and game else ""


def normalize_games(games, profile_id):
    """定位本人所在队伍，排除未结束、无本人、未知结果与重复对局。"""
    rows, skipped, seen = [], defaultdict(int), set()
    wanted = _positive_id(profile_id)
    for game in games:
        if not isinstance(game, dict):
            skipped["资料不完整"] += 1
            continue
        game_id = _positive_id(game.get("game_id"))
        if not game_id or game_id in seen:
            skipped["无效或重复对局"] += 1
            continue
        seen.add(game_id)
        if game.get("ongoing") is True or game.get("state") in ("ongoing", "in_progress", "active"):
            skipped["尚未结束"] += 1
            continue
        teams = []
        for team in game.get("teams", []) or []:
            if not isinstance(team, list):
                continue
            players = [entry.get("player", entry) for entry in team if isinstance(entry, dict)]
            teams.append([player for player in players if isinstance(player, dict)])
        own_index, own = None, None
        for index, team in enumerate(teams):
            for player in team:
                if _positive_id(player.get("profile_id")) == wanted:
                    own_index, own = index, player
                    break
            if own is not None:
                break
        if own is None:
            skipped["未找到本人"] += 1
            continue
        result = own.get("result")
        if result not in ("win", "loss"):
            skipped["结果未确认"] += 1
            continue
        opponents = [player for index, team in enumerate(teams) if index != own_index for player in team]
        strength_delta, strength_kind = None, None
        # 多队混战不套用双方队伍强弱比较；任何评分缺失也不以零填补。
        if len(teams) == 2 and opponents and teams[own_index]:
            for field in ("mmr", "rating"):
                ours = [_number(player.get(field)) for player in teams[own_index]]
                theirs = [_number(player.get(field)) for player in opponents]
                if all(value is not None for value in ours + theirs):
                    strength_delta = sum(theirs) / len(theirs) - sum(ours) / len(ours)
                    strength_kind = field
                    break
        duration = _number(game.get("duration"))
        if duration is not None and duration <= 0:
            duration = None
        rows.append({"game_id": game_id, "started_at": game.get("started_at"),
                     "duration": duration, "mode": game.get("kind") or game.get("leaderboard") or "unknown",
                     "map": game.get("map"), "civilization": own.get("civilization"),
                     "result": result, "rating_diff": _number(own.get("rating_diff")),
                     "opponents": [str(player.get("name") or "未提供昵称") for player in opponents],
                     "strength_delta": strength_delta, "strength_kind": strength_kind,
                     "patch": game.get("patch"), "season": game.get("season"),
                     "url": summary_url(wanted, game_id)})
    rows.sort(key=lambda row: str(row.get("started_at") or ""), reverse=True)
    return rows, dict(skipped)


def _summary(rows):
    wins = sum(row["result"] == "win" for row in rows)
    durations = [row["duration"] for row in rows if row["duration"] is not None]
    comparable = [row for row in rows if row["strength_delta"] is not None]
    short = [row for row in rows if row["duration"] is not None and row["duration"] <= 900]
    weaker = [row for row in comparable if row["strength_delta"] <= -80]
    return {"count": len(rows), "wins": wins, "losses": len(rows) - wins,
            "win_rate": 100 * wins / len(rows) if rows else None,
            "median_duration": median(durations) if durations else None,
            "duration_count": len(durations), "comparable_count": len(comparable),
            "short_count": len(short), "short_losses": sum(row["result"] == "loss" for row in short),
            "weaker_count": len(weaker), "weaker_losses": sum(row["result"] == "loss" for row in weaker)}


def analyze_games(games, profile_id, mode=None):
    """总览仅提供数量；胜率和复盘线索始终分模式，避免混合排位与快速对局。"""
    rows, skipped = normalize_games(games, profile_id)
    modes = sorted({row["mode"] for row in rows})
    if mode is not None:
        rows = [row for row in rows if row["mode"] == mode]
    by_mode = {}
    for code in modes:
        selected = [row for row in rows if row["mode"] == code]
        if not selected:
            continue
        info = _summary(selected)
        for field, label in (("civilization", "civilizations"), ("map", "maps")):
            groups = defaultdict(list)
            for row in selected:
                groups[row[field] or ""].append(row)
            info[label] = [{"key": key, **_summary(group)} for key, group in groups.items()]
            info[label].sort(key=lambda group: (-group["count"], group["key"]))
        by_mode[code] = info
    return {"rows": rows, "modes": modes, "by_mode": by_mode, "skipped": skipped,
            "received_count": len(games), "completed_count": len(rows)}


class PublicMatchClient:
    """单次手动操作最多读取一页 50 场；10 分钟内复用缓存。"""

    def __init__(self, data_dir, opener=urlopen, now=utc_now):
        self.cache_dir = Path(data_dir) / "战绩缓存"
        self.opener, self.now = opener, now

    def _path(self, profile_id):
        number = _positive_id(profile_id)
        if number is None:
            raise MatchError("玩家编号无效。")
        return self.cache_dir / f"玩家-{number}.json"

    def _load(self, path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _save(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _get(self, route, params=None):
        url = BASE_URL + "/api/v0/" + route
        if params:
            url += "?" + urlencode(params)
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with self.opener(request, timeout=12) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise MatchError("战绩响应过大，请稍后重试。")
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise MatchError("战绩服务返回了无法识别的资料。")
            return data
        except HTTPError as error:
            if error.code == 404:
                raise MatchError("未找到该玩家或其公开战绩。") from error
            if error.code == 429:
                raise MatchError("查询过于频繁，请稍后再试。") from error
            if error.code in (401, 403):
                raise MatchError("该资料未公开或服务暂时拒绝访问。") from error
            raise MatchError(f"战绩服务暂时不可用（状态 {error.code}）。") from error
        except (URLError, TimeoutError, OSError) as error:
            raise MatchError("连接 AoE4 World 失败或超时，请检查网络后重试。") from error
        except (ValueError, UnicodeError) as error:
            raise MatchError("战绩服务返回格式异常，请稍后重试。") from error

    def search_players(self, query):
        parsed = parse_player_input(query)
        if isinstance(parsed, int):
            return [{"profile_id": parsed, "name": f"玩家 {parsed}"}]
        result = self._get("players/search", {"query": parsed, "page": 1})
        players = result.get("players", [])
        if not isinstance(players, list):
            raise MatchError("玩家搜索结果格式异常。")
        return [{"profile_id": player["profile_id"], "name": str(player.get("name") or "未提供昵称")}
                for player in players[:50] if isinstance(player, dict) and _positive_id(player.get("profile_id"))]

    def cached_profile(self, profile_id):
        cached = self._load(self._path(profile_id))
        if (not cached or cached.get("schema") != 1 or cached.get("profile_id") != int(profile_id)
                or not isinstance(cached.get("games"), list) or not isinstance(cached.get("profile"), dict)):
            return None
        return {**cached, "source": "cache", "warning": ""}

    def fetch_profile(self, profile_id, refresh=False):
        path = self._path(profile_id)
        number = int(profile_id)
        cached = self.cached_profile(number)
        fetched = _iso(cached.get("fetched_at")) if cached else None
        age = (self.now() - fetched).total_seconds() if fetched else None
        if not refresh and cached and age is not None and 0 <= age < CACHE_SECONDS:
            return cached
        try:
            profile = self._get(f"players/{number}")
            if _positive_id(profile.get("profile_id")) != number:
                raise MatchError("玩家资料与请求编号不一致。")
            params = {"limit": MAX_GAMES, "page": 1}
            # since（起始时间）通常回溯最新一局前六小时；尚未确认的旧局也要纳入，
            # 避免长局或延迟同步的胜负永久停留在旧缓存中。始终只读一页。
            if cached and cached["games"]:
                times = [_iso(game.get("started_at")) for game in cached["games"] if isinstance(game, dict)]
                times = [value for value in times if value is not None]
                if times:
                    since = max(times) - timedelta(hours=6)
                    for old_game in cached["games"]:
                        if not isinstance(old_game, dict):
                            continue
                        started = _iso(old_game.get("started_at"))
                        _, excluded = normalize_games([old_game], number)
                        if started and any(reason in excluded for reason in ("尚未结束", "结果未确认")):
                            since = min(since, started - timedelta(minutes=1))
                    params["since"] = since.isoformat()
            result = self._get(f"players/{number}/games", params)
            incoming = result.get("games")
            if not isinstance(incoming, list):
                raise MatchError("对局列表格式异常。")
            merged = {}
            for game in ((cached or {}).get("games", []) + incoming):
                if isinstance(game, dict) and _positive_id(game.get("game_id")):
                    merged[game["game_id"]] = game
            games = sorted(merged.values(), key=lambda game: str(game.get("started_at") or ""), reverse=True)[:MAX_GAMES]
            snapshot = {"schema": 1, "profile_id": number, "profile": profile, "games": games,
                        "fetched_at": self.now().isoformat(), "source_url": f"{BASE_URL}/api/v0/players/{number}/games"}
            try:
                self._save(path, snapshot)
                warning = ""
            except OSError:
                warning = "已获取战绩，但本地缓存保存失败；请检查数据目录权限。"
            return {**snapshot, "source": "network", "warning": warning}
        except MatchError as error:
            if cached:
                return {**cached, "source": "cache", "warning": str(error) + " 正在显示上次保存的战绩。"}
            raise

