"""公开玩家头像：按玩家编号缓存，后台读取，缺失时使用人物图形占位。

avatar=头像，profile=玩家资料，cache=本机缓存；不查询昵称、不登录 Steam。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import threading
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid
import weakref

from PyQt5.QtCore import QByteArray, QBuffer, QIODevice, QObject, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QImage, QImageReader, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import QLabel

from version import APP_VERSION


AVATAR_HOSTS = frozenset({"avatars.steamstatic.com", "avatars.akamai.steamstatic.com",
                          "avatars.fastly.steamstatic.com"})
MAX_AVATAR_BYTES = 2 * 1024 * 1024
AVATAR_TIMEOUT = 8
_pending = {}
_pending_lock = threading.Lock()


def _trusted_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        parsed = urlparse(value)
        return (parsed.scheme == "https" and parsed.hostname in AVATAR_HOSTS
                and parsed.port in (None, 443) and parsed.username is None
                and parsed.password is None and not any(ord(char) < 32 for char in value))
    except ValueError:
        return False


def _identity(profile):
    """只能把公开资料中的准确玩家编号与该资料自己的头像地址配对。"""
    if not isinstance(profile, dict):
        return None, None
    value = profile.get("profile_id")
    identity = int(str(value)) if re.fullmatch(r"[1-9][0-9]{0,11}", str(value)) else None
    avatars = profile.get("avatars")
    if identity is None or not isinstance(avatars, dict):
        return identity, None
    for kind in ("medium", "small", "full"):
        value = avatars.get(kind)
        if _trusted_url(value):
            return identity, value
    return identity, None


def _cache_path(identity, url, data_dir):
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return Path(data_dir) / "玩家头像" / f"{identity}-{digest}.png"


class _TrustedAvatarRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        if not _trusted_url(new_url):
            raise ValueError("头像跳转地址不属于公开头像服务。")
        return super().redirect_request(request, response, code, message, headers, new_url)


def _open_avatar(request):
    """跳转前验证目标，避免公开头像字段把请求引向其他主机。"""
    return build_opener(_TrustedAvatarRedirect()).open(request, timeout=AVATAR_TIMEOUT)


def _decode_avatar(data):
    """先核对图像尺寸，再解码缩小；后台仅使用 QImage，不创建界面资源。"""
    if not data or len(data) > MAX_AVATAR_BYTES:
        return QImage()
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.ReadOnly)
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    dimensions = reader.size()
    if (not dimensions.isValid() or dimensions.width() > 4096 or dimensions.height() > 4096):
        return QImage()
    if dimensions.width() > 256 or dimensions.height() > 256:
        reader.setScaledSize(dimensions.scaled(256, 256, Qt.KeepAspectRatio))
    return reader.read()


def _png_bytes(image):
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    buffer.open(QIODevice.WriteOnly)
    return bytes(encoded) if image.save(buffer, "PNG") else b""


def _cached_png(path):
    try:
        if not path.is_file() or path.stat().st_size > MAX_AVATAR_BYTES:
            return b""
        data = path.read_bytes()
        return data if not _decode_avatar(data).isNull() else b""
    except OSError:
        return b""


def _download_avatar(url, path):
    request = Request(url, headers={
        "User-Agent": f"AOE4HELP/{APP_VERSION} (public player avatar; user initiated query)",
        "Accept": "image/*",
    })
    with _open_avatar(request) as response:
        if not _trusted_url(response.geturl()):
            raise ValueError("头像响应地址不受支持。")
        length = response.headers.get("Content-Length")
        if length is not None and int(length) > MAX_AVATAR_BYTES:
            raise ValueError("头像文件过大。")
        data = response.read(MAX_AVATAR_BYTES + 1)
    image = _decode_avatar(data)
    if image.isNull():
        raise ValueError("公开头像不是受支持的图片。")
    encoded = _png_bytes(image)
    if not encoded:
        raise ValueError("头像无法转换为缓存图像。")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp-" + uuid.uuid4().hex)
    try:
        temporary.write_bytes(encoded)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return encoded


def _avatar_pixmap(data, size):
    """保持头像比例居中裁切为圆角方形；仅在界面主线程创建 QPixmap。"""
    result = QPixmap(size, size)
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    bounds = QRectF(0.5, 0.5, size - 1, size - 1)
    radius = size * 0.24
    clip = QPainterPath()
    clip.addRoundedRect(bounds, radius, radius)
    painter.setClipPath(clip)
    painter.fillRect(bounds, QColor("#202624"))
    image = _decode_avatar(data) if data else QImage()
    if not image.isNull():
        scaled = image.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        painter.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    else:
        # 中性人物轮廓，不从昵称生成首字母，也不冒充真实头像。
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#a6b4ad"))
        painter.drawEllipse(QRectF(size * .385, size * .22, size * .23, size * .23))
        body = QPainterPath()
        body.moveTo(size * .23, size * .79)
        body.cubicTo(size * .23, size * .40, size * .77, size * .40, size * .77, size * .79)
        body.closeSubpath()
        painter.drawPath(body)
    painter.setClipping(False)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor("#3c4942"), 1))
    painter.drawRoundedRect(bounds, radius, radius)
    painter.end()
    return result


def load_cached_avatar(profile, data_dir, size=32):
    """只读取本机缓存，返回可用于列表项目的 QIcon；没有缓存则返回人物占位图。"""
    identity, url = _identity(profile)
    data = _cached_png(_cache_path(identity, url, data_dir)) if identity and url else b""
    return QIcon(_avatar_pixmap(data, max(16, int(size))))


class _AvatarSignals(QObject):
    finished = pyqtSignal(str, bytes)


def _request_avatar(url, path, signals, token):
    """相同缓存文件的并发显示共享一次请求，订阅只保存弱引用。"""
    key = str(path.resolve())
    with _pending_lock:
        listener = (weakref.ref(signals), token)
        if key in _pending:
            _pending[key].append(listener)
            return
        _pending[key] = [listener]

    def work():
        try:
            data = _cached_png(path) or _download_avatar(url, path)
        except Exception:
            data = b""
        with _pending_lock:
            listeners = _pending.pop(key, [])
        for reference, request_token in listeners:
            target = reference()
            if target is not None:
                try:
                    target.finished.emit(request_token, data)
                except RuntimeError:
                    # 页面已经销毁，Qt 会断开其连接；缓存仍供下一次主动查询使用。
                    pass

    threading.Thread(target=work, name="公开玩家头像", daemon=True).start()


class PlayerAvatar(QLabel):
    """精确玩家头像控件；迟到结果通过独立请求标识隔离，切换玩家不会串图。"""

    def __init__(self, parent=None, size=36):
        super().__init__(parent)
        self._size = max(16, int(size))
        self.profile_id = None
        self._token = ""
        self._active_key = None
        self._loading = False
        self._signals = _AvatarSignals(self)
        self._signals.finished.connect(self._finished)
        self.setObjectName("PlayerAvatar")
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(self._size, self._size)
        self.setPixmap(_avatar_pixmap(b"", self._size))

    def set_profile(self, profile, data_dir, *, allow_network=True):
        identity, url = _identity(profile)
        key = (identity, url, str(Path(data_dir).resolve()))
        if self._loading and key == self._active_key and allow_network:
            return
        self.profile_id = identity
        self._token = uuid.uuid4().hex
        self._active_key = key
        self._loading = False
        self.setAccessibleName(f"玩家 {identity} 的头像" if identity else "玩家头像")
        data = _cached_png(_cache_path(identity, url, data_dir)) if identity and url else b""
        self.setPixmap(_avatar_pixmap(data, self._size))
        self.setToolTip("公开玩家头像" if data else "未提供公开头像")
        if data or not identity or not url or not allow_network:
            return
        self._loading = True
        _request_avatar(url, _cache_path(identity, url, data_dir), self._signals, self._token)

    def _finished(self, token, data):
        if token != self._token:
            return
        self._loading = False
        if data:
            self.setPixmap(_avatar_pixmap(data, self._size))
            self.setToolTip("公开玩家头像")
