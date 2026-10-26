"""按精确玩家编号显示游戏等级与公开头像，不按昵称猜账号。

World's Edge（游戏运营方）的只读社区资料提供 level（游戏账号等级）。
Steam（游戏平台）的公开 XML 只在点选单名玩家时补头像；不把平台等级、
排位积分或 rank_level（排位段位）当作游戏等级。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from version import APP_VERSION


IDENTITY_ENDPOINT = "https://aoe-api.worldsedgelink.com/community/leaderboard/getPersonalStat"
CACHE_SECONDS = 3600
MAX_BATCH = 10
MAX_BYTES = 2 * 1024 * 1024
AVATAR_KEYS = ("small", "medium", "full")


class IdentityError(ValueError):
    """补充身份资料失败；查询界面仍可显示原有公开战绩。"""


def _id(value):
    return int(str(value)) if re.fullmatch(r"[1-9][0-9]{0,11}", str(value)) else None


def _level(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _steam_id(value):
    return value if isinstance(value, str) and re.fullmatch(r"[0-9]{17}", value) else None


def _url(value):
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    return value if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password else None


def profile_identity(profile):
    """保留原搜索结果已提供的识别字段，缺失的游戏等级明确为None。"""
    value = profile if isinstance(profile, dict) else {}
    avatars = value.get("avatars") if isinstance(value.get("avatars"), dict) else {}
    steam_id = _steam_id(value.get("steam_id"))
    country = value.get("country")
    modes = value.get("modes") if isinstance(value.get("modes"), dict) else value.get("leaderboards")
    result = {"profile_id": _id(value.get("profile_id")),
        "name": str(value.get("name") or "未提供昵称")[:200],
        "steam_id": steam_id, "platform": "steam" if steam_id else None,
        "avatars": {key: _url(avatars.get(key)) for key in AVATAR_KEYS},
        "country": country.lower() if isinstance(country, str) and re.fullmatch("[A-Za-z]{2}", country) else None,
        "game_level": _level(value.get("game_level")), "xp": _level(value.get("xp")),
        "modes": modes if isinstance(modes, dict) else {},
        "last_game_at": value.get("last_game_at") if isinstance(value.get("last_game_at"), str) else None}
    for key in ("identity_source", "avatar_source", "identity_warning"):
        if isinstance(value.get(key), str):
            result[key] = value[key][:500]
    return result


def _official_identities(document, requested):
    """只按statGroups（统计组）成员的profile_id关联，绝不按alias（昵称）配对。"""
    if not isinstance(document, dict) or not isinstance(document.get("result"), dict) or document["result"].get("code") != 0:
        raise IdentityError("游戏等级资料暂时不可用。")
    groups = document.get("statGroups")
    if not isinstance(groups, list):
        raise IdentityError("游戏等级资料格式异常。")
    result = {}
    for group in groups:
        for member in group.get("members", []) if isinstance(group, dict) and isinstance(group.get("members"), list) else []:
            if not isinstance(member, dict):
                continue
            identity = _id(member.get("profile_id"))
            if identity not in requested:
                continue
            name = member.get("name")
            steam_match = re.fullmatch(r"/steam/([0-9]{17})", name) if isinstance(name, str) else None
            result[identity] = profile_identity({
                "profile_id": identity, "name": member.get("alias") or f"玩家 {identity}",
                "steam_id": steam_match.group(1) if steam_match else None,
                "country": member.get("country"), "game_level": _level(member.get("level")),
                "xp": _level(member.get("xp")), "identity_source": IDENTITY_ENDPOINT})
    return result


def _steam_avatars(raw, steam_id):
    """Steam公开XML必须返回同一个SteamID64；平台等级字段一律不读取。"""
    try:
        tree = ET.fromstring(raw)
    except (ET.ParseError, ValueError) as error:
        raise IdentityError("公开头像资料格式异常。") from error
    if tree.tag != "profile" or tree.findtext("steamID64") != steam_id:
        raise IdentityError("公开头像账号与所选玩家不一致。")
    return {key: _url(tree.findtext(field)) for key, field in
            (("small", "avatarIcon"), ("medium", "avatarMedium"), ("full", "avatarFull"))}


class IdentityClient:
    """独立的身份补全缓存；构造和load_cached（读缓存）都不会联网。"""

    def __init__(self, data_dir, opener=urlopen, now=time.time):
        self.directory = Path(data_dir) / "玩家身份"
        self.opener, self.now = opener, now
        self._cache_lock = threading.RLock()

    def _path(self, profile_id):
        identity = _id(profile_id)
        if identity is None:
            raise IdentityError("玩家编号无效。")
        return self.directory / f"玩家-{identity}.json"

    def _record(self, profile_id):
        try:
            record = json.loads(self._path(profile_id).read_bytes())
            if (isinstance(record, dict) and record.get("schema") == 1
                    and isinstance(record.get("identity"), dict)
                    and record["identity"].get("profile_id") == _id(profile_id)):
                return record
        except (OSError, ValueError, TypeError):
            pass
        return None

    def load_cached(self, profile_id):
        record = self._record(profile_id)
        return profile_identity(record["identity"]) if record else None

    def _fresh(self, record):
        stamp = record.get("fetched_at") if record else None
        return isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and math.isfinite(stamp) and 0 <= self.now() - stamp < CACHE_SECONDS

    def _save(self, profile_id, value, *, fetched_at=None, avatar_checked_at=None):
        # 候选批量补全与单选头像会并发返回，同一账号的已确认头像不能被空字段盖掉。
        with self._cache_lock:
            path = self._path(profile_id)
            old = self._record(profile_id) or {}
            identity = profile_identity(value)
            previous = old.get("identity") or {}
            same_steam = identity.get("steam_id") and identity.get("steam_id") == previous.get("steam_id")
            if same_steam:
                identity["avatars"] = {key: identity["avatars"].get(key) or previous.get("avatars", {}).get(key)
                                       for key in AVATAR_KEYS}
                if not identity.get("avatar_source") and previous.get("avatar_source"):
                    identity["avatar_source"] = previous["avatar_source"]
            record = {"schema": 1, "fetched_at": self.now() if fetched_at is None else fetched_at,
                      "identity": identity}
            checked = avatar_checked_at if avatar_checked_at is not None else old.get("avatar_checked_at") if same_steam else None
            if isinstance(checked, (int, float)):
                record["avatar_checked_at"] = checked
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.directory, suffix=".tmp", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8"))
                temporary.replace(path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def _read(self, url, accept):
        request = Request(url, headers={"User-Agent": f"AOE4HELP/{APP_VERSION} (public player identity; user initiated)",
                                       "Accept": accept})
        try:
            with self.opener(request, timeout=12) as response:
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise IdentityError("公开玩家资料过大。")
            return raw
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise IdentityError("玩家等级或头像暂时无法获取，仍可使用现有战绩。") from error

    def fetch_identities(self, profile_ids, refresh=False):
        """当前候选最多10人合并一次官方请求；不在批量流程中逐个获取Steam头像。"""
        identities = list(dict.fromkeys(_id(value) for value in profile_ids))
        if not identities or None in identities or len(identities) > MAX_BATCH:
            raise IdentityError("一次只补全当前页最多10名有效玩家。")
        result, pending = {}, []
        for identity in identities:
            record = self._record(identity)
            if not refresh and self._fresh(record):
                result[identity] = profile_identity(record["identity"])
            else:
                pending.append(identity)
        if pending:
            url = IDENTITY_ENDPOINT + "?" + urlencode({"title": "age4", "profile_ids": json.dumps(pending, separators=(",", ":"))})
            try:
                document = json.loads(self._read(url, "application/json"))
            except (json.JSONDecodeError, UnicodeError) as error:
                raise IdentityError("游戏等级资料格式异常。") from error
            fetched = _official_identities(document, set(pending))
            for identity, value in fetched.items():
                old = self.load_cached(identity)
                if old and old.get("steam_id") == value.get("steam_id"):
                    value["avatars"] = old["avatars"]
                    if old.get("avatar_source"):
                        value["avatar_source"] = old["avatar_source"]
                self._save(identity, value)
                result[identity] = value
        return {identity: result[identity] for identity in identities if identity in result}

    def fetch_identity(self, profile_id, include_avatar=True, refresh=False):
        """点选单名玩家后补等级和公开头像；Steam失败仍保留已获取的游戏等级。"""
        identity = _id(profile_id)
        if identity is None:
            raise IdentityError("玩家编号无效。")
        result = self.fetch_identities([identity], refresh=refresh).get(identity)
        if result is None:
            raise IdentityError("未找到这名玩家的公开游戏资料。")
        if not include_avatar or not result.get("steam_id"):
            return result
        record = self._record(identity) or {}
        checked = record.get("avatar_checked_at")
        if not refresh and isinstance(checked, (int, float)) and 0 <= self.now() - checked < CACHE_SECONDS:
            return result
        steam_id = result["steam_id"]
        url = f"https://steamcommunity.com/profiles/{steam_id}/?xml=1"
        try:
            result["avatars"] = _steam_avatars(self._read(url, "application/xml,text/xml"), steam_id)
            result["avatar_source"] = url
        except IdentityError as error:
            result["identity_warning"] = str(error)
        self._save(identity, result, fetched_at=record.get("fetched_at"), avatar_checked_at=self.now())
        return result


# 兼容较早接线方案中的完整类名；二者是同一个客户端。
PlayerIdentityClient = IdentityClient
