"""发行版更新：HTTPS（加密连接）查询、备用源和安装包完整性验证。

release=正式发行版，manifest=更新清单，asset=发行附件，digest/hash=摘要。
本模块只返回已校验的文件，不执行安装器。GitHub 接口依据：
https://docs.github.com/en/rest/releases/releases#get-the-latest-release
https://docs.github.com/en/rest/releases/assets#get-a-release-asset
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


USER_AGENT = "EmpireOpeningAssistant/1.6.0 (desktop release updater)"
TIMEOUT = 12
MAX_METADATA = 1024 * 1024
MAX_INSTALLER = 2 * 1024 * 1024 * 1024
DEFAULT_ASSET_NAME = "AoE4OpeningAssistant-Setup.exe"


class UpdateError(ValueError):
    """可直接展示的中文更新错误；失败时没有可安装的新文件。"""


class UpdateCancelled(UpdateError):
    """用户取消下载；立即清理临时文件，不继续尝试备用源。"""


def version_key(value):
    """把数字版本转为可比较元组，例如 1.10 大于 1.9；不接受预览版。"""
    if (not isinstance(value, str) or len(value) > 64
            or not re.fullmatch(r"[vV]?\d+(?:\.\d+){1,3}", value)):
        raise UpdateError("更新版本号格式无效。")
    numbers = tuple(int(part) for part in value.lstrip("vV").split("."))
    if any(number > 999999 for number in numbers):
        raise UpdateError("更新版本号格式无效。")
    return numbers + (0,) * (4 - len(numbers))


def _version(value):
    numbers = version_key(value)
    return ".".join(map(str, numbers if numbers[3] else numbers[:3]))


def _https(value):
    """拒绝明文地址、账户密码和无效端口；重定向也使用同一规则。"""
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 for c in value):
        raise UpdateError("更新地址无效。")
    try:
        parsed = urlparse(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.fragment or parsed.port not in (None, 443)):
            raise UpdateError("更新地址必须使用不含账号密码的 HTTPS 加密连接。")
    except ValueError as exc:
        raise UpdateError("更新地址无效。") from exc
    return value


def _asset_name(value):
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}\.exe", value)
            or ".." in value):
        raise UpdateError("安装包名称无效，必须是明确的 .exe 安装包。")
    return value


def _sha256(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise UpdateError("发行版未提供有效的 SHA256 文件摘要，不能安装。")
    return value.lower()


def _size(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_INSTALLER:
        raise UpdateError("安装包大小无效。")
    return value


class _HTTPSRedirect(HTTPRedirectHandler):
    """在发送重定向请求前拒绝 HTTPS 降为 HTTP（明文连接）。"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class ReleaseInfo:
    """已验证的发行元数据；fallbacks（备用附件）必须属于完全相同的版本和文件。"""
    version: str
    notes: str
    download_url: str
    sha256: str
    size: int
    source: str
    page_url: str
    asset_name: str = DEFAULT_ASSET_NAME
    fallbacks: tuple["ReleaseInfo", ...] = field(default=(), repr=False, compare=False)


class UpdateClient:
    """无 Qt（界面库）依赖的更新客户端，适合放进界面的后台线程。"""
    def __init__(self, config, current_version="1.6.0", opener=None):
        if not isinstance(config, dict):
            raise UpdateError("更新配置格式无效。")
        self.current_version = _version(current_version)
        self.asset_name = _asset_name(config.get("asset_name", DEFAULT_ASSET_NAME))
        repository = config.get("github_repository", "")
        if repository and (not isinstance(repository, str)
                           or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", repository)):
            raise UpdateError("GitHub 仓库必须填写为“账户名/仓库名”。")
        self.repository = repository
        mirrors = config.get("mirrors", [])
        if not isinstance(mirrors, list) or len(mirrors) > 8:
            raise UpdateError("备用更新源配置无效。")
        self.mirrors = []
        for mirror in mirrors:
            if not isinstance(mirror, dict) or set(mirror) != {"name", "manifest_url"}:
                raise UpdateError("备用源需要填写名称和更新清单地址。")
            name = mirror["name"]
            if not isinstance(name, str) or not name.strip() or len(name) > 80:
                raise UpdateError("备用更新源名称无效。")
            self.mirrors.append({"name": name.strip(), "manifest_url": _https(mirror["manifest_url"])})
        if not self.repository and not self.mirrors:
            raise UpdateError("尚未配置更新仓库。")
        self._opener = opener or build_opener(_HTTPSRedirect()).open
        self.last_errors = ()

    def _open(self, url, accept="application/json"):
        _https(url)
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept,
                                       "X-GitHub-Api-Version": "2026-03-10"})
        try:
            response = self._opener(request, timeout=TIMEOUT)
            # 注入测试响应可以没有 geturl；真实连接必须保持 HTTPS。
            try:
                if hasattr(response, "geturl"):
                    _https(response.geturl())
            except UpdateError:
                response.close()
                raise
            return response
        except HTTPError as exc:
            raise UpdateError(f"更新连接失败（网络状态 {exc.code}）。") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise UpdateError("更新连接失败，请稍后重试。") from exc

    def _bytes(self, url):
        try:
            with self._open(url) as response:
                data = response.read(MAX_METADATA + 1)
        except (URLError, TimeoutError, OSError) as exc:
            raise UpdateError("更新信息读取失败，请稍后重试。") from exc
        if len(data) > MAX_METADATA:
            raise UpdateError("更新信息超过允许大小。")
        return data

    def _json(self, url):
        try:
            return json.loads(self._bytes(url).decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise UpdateError("服务器返回的更新信息格式无效。") from exc

    def _manifest(self, data, source):
        """严格读取版本 1 清单；大小和摘要不可缺省，不猜测下载文件。"""
        required = {"schema_version", "version", "asset"}
        allowed = required | {"notes", "page_url"}
        if (not isinstance(data, dict) or not required <= set(data) or set(data) - allowed
                or type(data.get("schema_version")) is not int or data["schema_version"] != 1):
            raise UpdateError("更新清单结构无效。")
        version = _version(data["version"])
        asset = data["asset"]
        if not isinstance(asset, dict) or set(asset) != {"name", "url", "sha256", "size"}:
            raise UpdateError("更新清单中的安装包信息不完整。")
        if asset["name"] != self.asset_name:
            raise UpdateError("更新清单中的安装包名称不匹配。")
        notes, page = data.get("notes", ""), data.get("page_url", "")
        if not isinstance(notes, str) or len(notes) > 100000:
            raise UpdateError("更新说明格式无效。")
        if page:
            _https(page)
        elif not isinstance(page, str):
            raise UpdateError("发行页面地址无效。")
        release = ReleaseInfo(version, notes, _https(asset["url"]), _sha256(asset["sha256"]),
                              _size(asset["size"]), source, page, self.asset_name)
        self._validate_release(release)
        return release

    def _github(self):
        data = self._json(f"https://api.github.com/repos/{self.repository}/releases/latest")
        if not isinstance(data, dict):
            raise UpdateError("GitHub 发行信息格式无效。")
        if data.get("draft") is not False or data.get("prerelease") is not False:
            raise UpdateError("该发行版尚未正式发布。")
        version = _version(data.get("tag_name"))
        if version_key(version) <= version_key(self.current_version):
            return None
        assets = data.get("assets")
        if not isinstance(assets, list):
            raise UpdateError("发行版未提供安装包。")
        matches = [asset for asset in assets if isinstance(asset, dict)
                   and asset.get("name") == self.asset_name and asset.get("state") == "uploaded"]
        if len(matches) != 1:
            raise UpdateError("发行版未提供唯一匹配的安装包。")
        asset = matches[0]
        digest = asset.get("digest")
        checksum = None
        if isinstance(digest, str) and digest.startswith("sha256:"):
            checksum = _sha256(digest[7:])
        elif digest:
            raise UpdateError("发行附件摘要格式不受支持。")
        if checksum is None:
            # 清单属于同一 release（发行版），必须再次核对版本、名称、大小和地址。
            manifests = [item for item in assets if isinstance(item, dict)
                         and item.get("name") == "update.json" and item.get("state") == "uploaded"]
            if len(manifests) == 1:
                manifest = self._manifest(self._json(manifests[0].get("browser_download_url")), "GitHub")
                if (manifest.version != version or manifest.size != asset.get("size")
                        or manifest.download_url != asset.get("browser_download_url")):
                    raise UpdateError("发行附件与该版本更新清单不一致。")
                checksum = manifest.sha256
            else:
                for name in ("SHA256SUMS", "SHA256SUMS.txt"):
                    sums = [item for item in assets if isinstance(item, dict)
                            and item.get("name") == name and item.get("state") == "uploaded"]
                    if len(sums) == 1:
                        try:
                            lines = self._bytes(sums[0].get("browser_download_url")).decode("utf-8-sig").splitlines()
                        except UnicodeError as exc:
                            raise UpdateError("文件摘要清单格式无效。") from exc
                        matches = [match.group(1) for line in lines
                                   if (match := re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line))
                                   and match.group(2) == self.asset_name]
                        if len(matches) == 1:
                            checksum = _sha256(matches[0])
                            break
        if checksum is None:
            raise UpdateError("发行版未提供 SHA256 文件摘要，不能安装。")
        notes, page = data.get("body") or "", data.get("html_url") or ""
        if not isinstance(notes, str) or not isinstance(page, str):
            raise UpdateError("GitHub 更新说明格式无效。")
        if page:
            _https(page)
        result = ReleaseInfo(version, notes, _https(asset.get("browser_download_url")), checksum,
                             _size(asset.get("size")), "GitHub", page, self.asset_name)
        self._validate_release(result)
        return result

    def check(self):
        """查询所有已配置来源；新版本返回元数据，没有更新返回 None。"""
        candidates, errors = [], []
        successful = 0
        if self.repository:
            try:
                release = self._github()
                successful += 1
                if release is not None:
                    candidates.append(release)
            except UpdateError as exc:
                errors.append(f"GitHub：{exc}")
        for mirror in self.mirrors:
            try:
                release = self._manifest(self._json(mirror["manifest_url"]), mirror["name"])
                successful += 1
                if version_key(release.version) > version_key(self.current_version):
                    candidates.append(release)
            except UpdateError as exc:
                errors.append(f"{mirror['name']}：{exc}")
        self.last_errors = tuple(errors)
        if not candidates:
            if successful:
                return None
            raise UpdateError("；".join(errors) or "没有可用的更新源。")
        # 相同版本保持 GitHub 优先；不同版本取版本号最高者，不按字典序比较。
        candidates.sort(key=lambda item: version_key(item.version), reverse=True)
        chosen = candidates[0]
        fallbacks = tuple(item for item in candidates[1:]
                          if item.version == chosen.version and item.sha256 == chosen.sha256
                          and item.size == chosen.size and item.download_url != chosen.download_url)
        return replace(chosen, fallbacks=fallbacks)

    def _validate_release(self, release):
        if not isinstance(release, ReleaseInfo):
            raise UpdateError("发行信息无效。")
        version_key(release.version)
        _sha256(release.sha256)
        _size(release.size)
        if _asset_name(release.asset_name) != self.asset_name:
            raise UpdateError("安装包名称不匹配。")
        _https(release.download_url)
        if unquote(Path(urlparse(release.download_url).path).name) != self.asset_name:
            raise UpdateError("下载地址中的安装包名称或扩展名不匹配。")

    def verify_download(self, path, release):
        """下载后或安装前复查文件，返回布尔结果；不会运行任何程序。"""
        self._validate_release(release)
        path = Path(path)
        try:
            if path.name != self.asset_name or path.stat().st_size != release.size:
                return False
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                if stream.read(2) != b"MZ":
                    return False
                stream.seek(0)
                for chunk in iter(lambda: stream.read(256 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest() == release.sha256.lower()
        except OSError:
            return False

    def download(self, release, folder, on_progress=None):
        """逐块下载并校验摘要、大小与安装包头；成功后才原子替换缓存文件。

        on_progress（进度回调）接收“已下载字节数、总字节数”，在调用线程执行。
        同版本备用源采用同一摘要；失败后调用者可再次调用本方法重试。
        """
        self._validate_release(release)
        if version_key(release.version) <= version_key(self.current_version):
            raise UpdateError("不能下载当前版本或更旧的版本。")
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / self.asset_name
        if self.verify_download(target, release):
            if on_progress:
                on_progress(release.size, release.size)
            return target
        errors = []
        for candidate in (release,) + tuple(release.fallbacks):
            self._validate_release(candidate)
            if (candidate.version != release.version or candidate.sha256.lower() != release.sha256.lower()
                    or candidate.size != release.size):
                raise UpdateError("备用安装包与目标版本不一致。")
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(prefix="更新-", suffix=".part", dir=folder, delete=False) as stream:
                    temporary = Path(stream.name)
                    received, digest, prefix = 0, hashlib.sha256(), b""
                    if on_progress:
                        on_progress(0, release.size)
                    with self._open(candidate.download_url, "application/octet-stream") as response:
                        headers = getattr(response, "headers", {})
                        declared = headers.get("Content-Length")
                        if declared is not None and str(declared) != str(release.size):
                            raise UpdateError("服务器提供的安装包大小与发行信息不一致。")
                        while True:
                            chunk = response.read(256 * 1024)
                            if not chunk:
                                break
                            received += len(chunk)
                            if received > release.size:
                                raise UpdateError("安装包超出声明大小，已停止下载。")
                            if len(prefix) < 2:
                                prefix = (prefix + chunk)[:2]
                            digest.update(chunk)
                            stream.write(chunk)
                            if on_progress:
                                on_progress(received, release.size)
                    if received != release.size or digest.hexdigest() != release.sha256.lower() or prefix != b"MZ":
                        raise UpdateError("安装包校验失败，未提供安装文件。")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
                return target
            except UpdateCancelled:
                raise
            except (UpdateError, URLError, TimeoutError, OSError) as exc:
                errors.append(f"{candidate.source}：{exc if isinstance(exc, UpdateError) else '下载失败，请重试。'}")
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        raise UpdateError("；".join(errors) or "安装包下载失败，请重试。")
