"""用户点选一场对局后，从帝国时代官网的正常下载接口获取公开回放。

public replay=公开回放，cache=本地缓存，SHA-256=文件完整性摘要。
下载地址依据官网回放按钮；不批量查询动态摘要，不使用账号凭证或游戏进程。
"""
from __future__ import annotations

import hashlib
from http.client import HTTPException
import json
import re
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from version import APP_VERSION


_DOWNLOAD_BASE = "https://api.ageofempires.com/api/GameStats/AgeIV/GetMatchReplay/"
_MATCH_DETAIL_URL = "https://api.ageofempires.com/api/GameStats/AgeIV/GetMatchDetail"
_DOWNLOAD_HOST = "api.ageofempires.com"
_MAX_REPLAY_BYTES = 128 * 1024 * 1024
_MAX_MATCH_DETAIL_BYTES = 1024 * 1024
_USER_AGENT = f"AOE4HELP/{APP_VERSION} (public replay import; user initiated match request)"


class PublicReplayError(ValueError):
    """可以直接展示的公开回放获取错误。"""

    def __init__(self, message, *, reason="unknown", http_status=None):
        super().__init__(message)
        # reason（稳定原因）用于后续流程判断，中文正文仍可直接展示给玩家。
        self.reason = reason
        self.http_status = http_status


def _storage_call(operation, *args, **kwargs):
    """仅包裹本地文件操作，避免把磁盘失败误报为网络失败。"""
    try:
        return operation(*args, **kwargs)
    except OSError as error:
        raise PublicReplayError("公开回放无法保存到本地，请检查助手数据目录的权限和剩余空间。",
                                reason="storage_error") from error


def _network_error(error):
    """区分连接超时与其它传输失败；不因此自动改换下载来源。"""
    cause = error.reason if isinstance(error, URLError) else error
    if isinstance(cause, TimeoutError):
        return PublicReplayError("获取公开回放超时，请稍后重试。", reason="network_timeout")
    return PublicReplayError("获取公开回放时网络连接失败，请检查网络后重试。", reason="network_error")


def _http_error(error):
    """将官网响应转成稳定原因；只有权限或不可用原因允许查同场公开来源。"""
    messages = {
        401: ("官网回放服务要求身份验证，当前公开下载入口无法访问（HTTP 401）。", "permission"),
        403: ("官网拒绝了这份回放的访问请求（HTTP 403），当前入口没有访问权限。", "permission"),
        404: ("官网未找到这份公开回放（HTTP 404），当前入口未提供可下载文件。", "unavailable"),
        409: ("官网当前无法提供这份回放（HTTP 409），可能尚未生成或状态冲突。", "unavailable"),
        429: ("官网回放请求过于频繁（HTTP 429），请稍后再试。", "rate_limited"),
    }
    message, reason = messages.get(error.code, (
        f"官网回放服务暂时异常（HTTP {error.code}），请稍后重试。" if 500 <= error.code < 600 else
        f"官网回放请求失败（HTTP {error.code}），请稍后重试。",
        "service_error" if 500 <= error.code < 600 else "http_error"))
    return PublicReplayError(message, reason=reason, http_status=error.code)


def _positive_id(value):
    return int(str(value)) if re.fullmatch(r"[1-9][0-9]{0,11}", str(value)) else None


def _official_url(profile_id, game_id):
    return _DOWNLOAD_BASE + "?" + urlencode({"matchId": game_id, "profileId": profile_id})


def _replay_magic(value):
    """官网可能返回 gzip（压缩文件）或 ZIP（压缩包）；不依赖响应的类型标签。"""
    return value.startswith(b"\x1f\x8b") or value.startswith(b"PK\x03\x04")


class _PublicRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        try:
            target = urlparse(newurl)
            valid = (target.scheme == "https" and target.hostname == _DOWNLOAD_HOST
                     and target.port in (None, 443) and not target.username
                     and not target.password and not target.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise PublicReplayError("官网回放服务跳转到了未确认的地址，已停止下载。", reason="redirect_blocked")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


class PublicReplayClient:
    """fetch（获取）只处理指定的一场对局；可在界面后台任务中调用。"""

    def __init__(self, data_dir, opener=None):
        self.directory = Path(data_dir) / "公开回放"
        self.opener = opener or build_opener(_PublicRedirectHandler()).open

    def _paths(self, profile_id, game_id):
        stem = f"玩家-{profile_id}-对局-{game_id}"
        return self.directory / (stem + ".json"), self.directory / (stem + ".aoe4replay")

    def _cached_metadata(self, path, profile_id, game_id):
        try:
            if path.stat().st_size > 4096:
                return None
            value = json.loads(path.read_bytes())
            if (not isinstance(value, dict) or value.get("schema") != 1
                    or value.get("profile_id") != profile_id or value.get("game_id") != game_id
                    or value.get("url") != _official_url(profile_id, game_id)
                    or type(value.get("filesize")) is not int
                    or not 0 < value["filesize"] <= _MAX_REPLAY_BYTES
                    or not isinstance(value.get("sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])):
                return None
            return value
        except (OSError, ValueError):
            return None

    @staticmethod
    def _valid_file(path, metadata):
        try:
            if path.stat().st_size != metadata["filesize"]:
                return False
            digest = hashlib.sha256()
            with path.open("rb") as source:
                first = source.read(64 * 1024)
                if not _replay_magic(first):
                    return False
                digest.update(first)
                while chunk := source.read(64 * 1024):
                    digest.update(chunk)
            return digest.hexdigest() == metadata["sha256"]
        except OSError:
            return False

    def _save_metadata(self, path, value):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix="下载资料-", suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                output.write(json.dumps(value, ensure_ascii=False).encode("utf-8"))
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _download(self, url, path):
        temporary = None
        try:
            request = Request(url, headers={"User-Agent": _USER_AGENT,
                                           "Accept": "application/gzip, application/zip, application/octet-stream"})
            with self.opener(request, timeout=30) as response:
                declared = getattr(response, "headers", {}).get("Content-Length")
                try:
                    expected = int(declared) if declared is not None else None
                except (ValueError, TypeError) as error:
                    raise PublicReplayError("官网回放的文件大小无效，已停止下载。", reason="invalid_response") from error
                if expected is not None and not 0 < expected <= _MAX_REPLAY_BYTES:
                    raise PublicReplayError("公开回放为空或超过 128 MB，无法下载。", reason="invalid_response")
                output = _storage_call(tempfile.NamedTemporaryFile, dir=self.directory,
                                       prefix="完整回放-", suffix=".tmp", delete=False)
                try:
                    temporary = Path(output.name)
                    received, prefix = 0, b""
                    digest = hashlib.sha256()
                    while True:
                        chunk = response.read(min(64 * 1024, _MAX_REPLAY_BYTES - received + 1))
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > _MAX_REPLAY_BYTES or (expected is not None and received > expected):
                            raise PublicReplayError("公开回放超出声明大小或 128 MB，已停止下载。", reason="invalid_response")
                        if len(prefix) < 4:
                            prefix += chunk[:4 - len(prefix)]
                        _storage_call(output.write, chunk)
                        digest.update(chunk)
                finally:
                    _storage_call(output.close)
            if expected is not None and received != expected:
                raise PublicReplayError("公开回放未下载完整，请重试。", reason="incomplete_download")
            if not _replay_magic(prefix):
                raise PublicReplayError("官网没有返回有效的游戏回放文件，已停止分析。", reason="invalid_content")
            _storage_call(temporary.replace, path)
            return received, digest.hexdigest()
        except HTTPError:
            raise
        except (URLError, TimeoutError, OSError, HTTPException) as error:
            raise _network_error(error) from error
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    # 临时文件清理失败不覆盖已经确定的下载或保存原因。
                    pass

    def fetch(self, profile_id, game_id):
        """返回 path（本地路径）及 cached（是否复用下载）；不解析游戏内容。"""
        player, game = _positive_id(profile_id), _positive_id(game_id)
        if not player or not game:
            raise PublicReplayError("玩家编号或对局编号无效。", reason="invalid_input")
        metadata_path, replay_path = self._paths(player, game)
        url = _official_url(player, game)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            metadata = self._cached_metadata(metadata_path, player, game)
            cached = metadata is not None and self._valid_file(replay_path, metadata)
            if not cached:
                size, digest = self._download(url, replay_path)
                # 不保存昵称、聊天或完整网页资料，只保存本场文件的来源与完整性信息。
                metadata = {"schema": 1, "profile_id": player, "game_id": game,
                            "url": url, "filesize": size, "sha256": digest}
                self._save_metadata(metadata_path, metadata)
            return {"path": replay_path, "cached": cached, "profile_id": player, "game_id": game,
                    "source_url": url, "filesize": metadata["filesize"], "sha256": metadata["sha256"]}
        except HTTPError as error:
            raise _http_error(error) from error
        except (URLError, TimeoutError, HTTPException) as error:
            raise _network_error(error) from error
        except OSError as error:
            raise PublicReplayError("公开回放无法保存到本地，请检查助手数据目录的权限和剩余空间。",
                                    reason="storage_error") from error

    def _match_candidates(self, profile_id, game_id):
        """只核对官网这一场的公开按钮资格，不保存昵称、不按昵称关联身份。"""
        request = Request(_MATCH_DETAIL_URL,
                          data=json.dumps({"matchId": game_id, "game": "age4", "profileId": profile_id}).encode("utf-8"),
                          headers={"User-Agent": _USER_AGENT, "Accept": "application/json",
                                   "Content-Type": "application/json"}, method="POST")
        try:
            with self.opener(request, timeout=30) as response:
                status = getattr(response, "status", None)
                status = status if type(status) is int else None
                body = response.read(_MAX_MATCH_DETAIL_BYTES + 1)
        except HTTPError as error:
            failure = _http_error(error)
            raise PublicReplayError(f"无法核对本场其他公开回放：{failure}",
                                    reason=failure.reason, http_status=failure.http_status) from error
        except (URLError, TimeoutError, OSError, HTTPException) as error:
            raise _network_error(error) from error
        if status == 204:
            # 204是官网明确的“无内容”，不是玩家/对局字段核对失败，更不能断言录像过期。
            raise PublicReplayError("官网未提供本场对局详情（HTTP 204，无内容）。无法核对原查询玩家和同场公开回放来源，已停止尝试；可稍后重试。",
                                    reason="unavailable", http_status=status)
        if not body.strip():
            label = f"（HTTP {status}）" if status is not None else ""
            raise PublicReplayError(f"官网返回了空的本场对局资料{label}，无法核对同场公开回放来源，已停止尝试。",
                                    reason="invalid_response", http_status=status)
        try:
            if len(body) > _MAX_MATCH_DETAIL_BYTES:
                raise ValueError("对局资料过大")
            detail = json.loads(body)
            summary = detail.get("matchSummary") if isinstance(detail, dict) else None
            players = detail.get("playerList") if isinstance(detail, dict) else None
            if (not isinstance(detail, dict) or type(detail.get("statusCode")) is not int
                    or detail["statusCode"] != 0 or detail.get("errorMessage") not in (None, "")
                    or not isinstance(summary, dict)
                    or summary.get("errorMessage") not in (None, "")
                    or _positive_id(summary.get("matchId")) != game_id
                    or not isinstance(players, list) or not 1 <= len(players) <= 8):
                raise ValueError("官网单场资料不匹配")
            # profileId 在已验证官网响应中可为 0；下载按钮实际使用 userId。
            if not any(isinstance(player, dict) and player.get("isHuman") is True
                       and _positive_id(player.get("userId")) == profile_id for player in players):
                raise ValueError("原查询账号不在本场玩家中")
        except (ValueError, TypeError, UnicodeDecodeError) as error:
            raise PublicReplayError("官网对局资料未能核对本场编号及原查询玩家，已停止尝试其他回放。",
                                    reason="invalid_response", http_status=status) from error
        candidates, seen = [], {profile_id}
        for player in players:
            if not isinstance(player, dict):
                continue
            user_id = _positive_id(player.get("userId"))
            name = player.get("userName")
            # 与官网按钮分支一致；非空昵称仅为按钮资格，不作为账号身份依据。
            if (user_id and user_id not in seen and player.get("isHuman") is True
                    and isinstance(name, str) and name.strip()
                    and player.get("matchReplayAvailable") is True):
                seen.add(user_id)
                candidates.append(user_id)
        return candidates

    def fetch_match(self, profile_id, game_id):
        """先取原查询账号；仅不可用时，依官网明确公开的同场来源顺序获取。"""
        player, game = _positive_id(profile_id), _positive_id(game_id)
        # fetch（指定来源下载）负责输入验证，成功时不额外查询官网对局资料。
        try:
            result = self.fetch(profile_id, game_id)
        except PublicReplayError as original:
            if original.reason not in {"permission", "unavailable"}:
                raise
            candidates = self._match_candidates(player, game)
            if not candidates:
                raise PublicReplayError(f"{original} 官网未列出本场其他可下载的公开回放。",
                                        reason="unavailable", http_status=original.http_status) from original
            last_error = original
            for candidate in candidates:
                try:
                    result = self.fetch(candidate, game)
                    break
                except PublicReplayError as error:
                    if error.reason not in {"permission", "unavailable"}:
                        # 限流、服务或传输失败不是换人的理由，立即停止以免连续请求。
                        raise
                    last_error = error
            else:
                raise PublicReplayError(f"官网标记可下载的同场公开回放目前均无法获取。最后响应：{last_error}",
                                        reason="unavailable", http_status=last_error.http_status) from last_error
        # 原查询身份和实际下载来源独立保留；缓存仍归实际来源，避免冒充原玩家文件。
        return dict(result, requested_profile_id=player, download_profile_id=result["profile_id"])
