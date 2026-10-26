"""用户点选单场后获取详细统计，保存归一化结果供离线复盘。

只按需请求一次 AoE4 World（社区战绩站）的已结束对局摘要，不批量查询。
cache=本地缓存；原始下载链接和平台身份字段不进入保存的图表数据。
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from match_detail import MatchDetailError, SCHEMA_VERSION, normalize_summary
from version import APP_VERSION

MAX_BYTES = 16 * 1024 * 1024


def _id(value):
    return int(str(value)) if re.fullmatch(r"[1-9][0-9]{0,11}", str(value)) else None


class MatchDetailClient:
    """fetch（获取）用于主动分析；load_cached（读取缓存）用于打开已保存报告。"""

    def __init__(self, data_dir, opener=urlopen):
        self.directory = Path(data_dir) / "对局详情"
        self.opener = opener

    def _path(self, profile_id, game_id):
        player, game = _id(profile_id), _id(game_id)
        if player is None or game is None:
            raise MatchDetailError("玩家编号或对局编号无效。")
        return self.directory / f"玩家-{player}-对局-{game}.json"

    def load_cached(self, profile_id, game_id):
        path = self._path(profile_id, game_id)
        try:
            if path.stat().st_size > MAX_BYTES:
                return None
            value = json.loads(path.read_bytes())
            if (isinstance(value, dict) and value.get("schema_version") == SCHEMA_VERSION
                    and value.get("game_id") == _id(game_id)
                    and value.get("focus_profile_id") == _id(profile_id)
                    and isinstance(value.get("players"), list)
                    and any(player.get("profile_id") == _id(profile_id)
                            for player in value["players"] if isinstance(player, dict))):
                return value
        except (OSError, ValueError, TypeError):
            pass
        return None

    def store_summary(self, summary, profile_id, game_id):
        """归一化后再写入；实际场次和关注玩家必须与本次请求一致。"""
        detail = normalize_summary(summary, focus_profile_id=_id(profile_id), expected_game_id=_id(game_id))
        if not any(player.get("profile_id") == _id(profile_id) for player in detail["players"]):
            raise MatchDetailError("详细记录中没有找到这名玩家，已停止关联。")
        path = self._path(profile_id, game_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(json.dumps(detail, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return detail

    def fetch(self, profile_id, game_id):
        cached = self.load_cached(profile_id, game_id)
        if cached is not None:
            return cached
        player, game = _id(profile_id), _id(game_id)
        request = Request(f"https://aoe4world.com/players/{player}/games/{game}/summary", headers={
            "User-Agent": f"AOE4HELP/{APP_VERSION} (post-match detail; user initiated single match)",
            "Accept": "application/json",
        })
        try:
            with self.opener(request, timeout=30) as response:
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise MatchDetailError("这场对局的详细统计过大，暂时无法读取。")
            document = json.loads(raw)
        except HTTPError as error:
            if error.code in (401, 403, 404):
                raise MatchDetailError("这场对局暂未提供公开的经济与事件统计。") from error
            raise MatchDetailError("详细统计服务暂时不可用，已有回放报告仍可查看。") from error
        except (URLError, TimeoutError, OSError) as error:
            raise MatchDetailError("详细统计暂时无法获取，已有回放报告仍可查看。") from error
        except (ValueError, UnicodeError) as error:
            raise MatchDetailError("详细统计服务没有返回有效数据。") from error
        return self.store_summary(document, player, game)
