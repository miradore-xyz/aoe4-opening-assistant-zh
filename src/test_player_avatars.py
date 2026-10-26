"""玩家头像的隔离缓存、受限请求与迟到回调验证；全部使用本机构造图片。"""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent
from PyQt5.QtGui import QColor, QImage
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5 import sip
import player_avatars as avatars


URL = "https://avatars.fastly.steamstatic.com/example_medium.jpg"


def profile(identity=7, url=URL):
    return {"profile_id": identity, "name": "同名玩家", "avatars": {"medium": url}}


def picture(color):
    image = QImage(64, 48, QImage.Format_ARGB32)
    image.fill(QColor(color))
    return avatars._png_bytes(image)


class Response:
    def __init__(self, data, url=URL, length=None):
        self.data = data
        self.url = url
        self.headers = {"Content-Length": str(len(data) if length is None else length)}
        self.read_size = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def geturl(self):
        return self.url

    def read(self, size):
        self.read_size = size
        return self.data[:size]


class PlayerAvatarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            if not sip.isdeleted(widget):
                widget.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()
        self.directory.cleanup()

    def widget(self):
        widget = avatars.PlayerAvatar()
        self.widgets.append(widget)
        return widget

    def wait_until(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(condition(), "头像后台请求未在测试时限内完成")

    def center_color(self, widget):
        return widget.pixmap().toImage().pixelColor(18, 18)

    def test_verified_avatar_downloads_off_thread_and_reuses_exact_profile_cache(self):
        widget = self.widget()
        response = Response(picture("#e04030"))
        with patch("player_avatars._open_avatar", return_value=response) as request:
            widget.set_profile(profile(), self.folder)
            self.wait_until(lambda: not widget._loading)
            self.assertEqual(self.center_color(widget).name(), "#e04030")
            self.assertEqual(widget.profile_id, 7)
            self.assertEqual(widget.text(), "")
            self.assertEqual(widget.pixmap().toImage().pixelColor(0, 0).alpha(), 0)
            self.assertEqual(response.read_size, avatars.MAX_AVATAR_BYTES + 1)
            self.assertEqual(request.call_args.args[0].full_url, URL)
            cached = list((self.folder / "玩家头像").glob("7-*.png"))
            self.assertEqual(len(cached), 1)
            other = self.widget()
            other.set_profile(profile(), self.folder, allow_network=False)
            self.assertEqual(self.center_color(other).name(), "#e04030")
            self.assertFalse(avatars.load_cached_avatar(profile(), self.folder).isNull())
            widget.set_profile(profile(), self.folder)
            request.assert_called_once()

    def test_no_avatar_untrusted_hosts_and_cache_only_never_request_network(self):
        widget = self.widget()
        with patch("player_avatars._open_avatar") as request:
            for value in (None, {"profile_id": 7, "avatars": None}, profile(url="http://avatars.steamstatic.com/a.jpg"),
                          profile(url="https://avatars.steamstatic.com.evil.example/a.jpg"),
                          profile(url="https://user:secret@avatars.steamstatic.com/a.jpg"),
                          profile(url="https://avatars.steamstatic.com:444/a.jpg"),
                          profile(identity=True)):
                widget.set_profile(value, self.folder)
                self.assertFalse(widget._loading)
                self.assertEqual(widget.text(), "")
                self.assertFalse(widget.pixmap().isNull())
            widget.set_profile(profile(), self.folder, allow_network=False)
            request.assert_not_called()

    def test_late_result_for_same_name_does_not_replace_a_different_profile_avatar(self):
        widget = self.widget()
        release = threading.Event()
        entered = threading.Event()
        old_url = "https://avatars.steamstatic.com/old.jpg"
        new_url = "https://avatars.akamai.steamstatic.com/new.jpg"

        def respond(request):
            if request.full_url == old_url:
                entered.set()
                release.wait(2)
                return Response(picture("#e04030"), old_url)
            return Response(picture("#3060e0"), new_url)

        with patch("player_avatars._open_avatar", side_effect=respond):
            started = time.monotonic()
            widget.set_profile(profile(7, old_url), self.folder)
            self.assertLess(time.monotonic() - started, .3)
            self.assertTrue(entered.wait(1))
            widget.set_profile(profile(8, new_url), self.folder)
            self.wait_until(lambda: not widget._loading)
            self.assertEqual(widget.profile_id, 8)
            self.assertEqual(self.center_color(widget).name(), "#3060e0")
            release.set()
            self.wait_until(lambda: not avatars._pending)
            QTest.qWait(20)
            self.assertEqual(widget.profile_id, 8)
            self.assertEqual(self.center_color(widget).name(), "#3060e0")
            self.assertEqual(len(list((self.folder / "玩家头像").glob("*.png"))), 2)

    def test_failed_or_oversized_avatar_preserves_the_placeholder_without_cache(self):
        for data, length in ((b"not an image", 12), (picture("#e04030"), avatars.MAX_AVATAR_BYTES + 1),
                             (b"a" * (avatars.MAX_AVATAR_BYTES + 1), 1)):
            with self.subTest(length=length):
                widget = self.widget()
                placeholder = widget.pixmap().toImage()
                with patch("player_avatars._open_avatar", return_value=Response(data, length=length)):
                    widget.set_profile(profile(), self.folder)
                    self.wait_until(lambda: not widget._loading)
                self.assertEqual(widget.pixmap().toImage(), placeholder)
                self.assertFalse((self.folder / "玩家头像").exists())

    def test_destroyed_page_safely_discards_the_worker_signal(self):
        parent = QWidget()
        self.widgets.append(parent)
        widget = avatars.PlayerAvatar(parent)
        release = threading.Event()
        entered = threading.Event()

        def respond(_request):
            entered.set()
            release.wait(2)
            return Response(picture("#3060e0"))

        with patch("player_avatars._open_avatar", side_effect=respond), patch("threading.excepthook") as problem:
            widget.set_profile(profile(), self.folder)
            self.assertTrue(entered.wait(1))
            parent.deleteLater()
            self.application.sendPostedEvents(None, QEvent.DeferredDelete)
            self.assertTrue(sip.isdeleted(widget))
            release.set()
            self.wait_until(lambda: not avatars._pending)
            QTest.qWait(20)
            problem.assert_not_called()

    def test_redirect_and_timeout_use_the_same_bounded_public_source_rules(self):
        handler = avatars._TrustedAvatarRedirect()
        with self.assertRaises(ValueError):
            handler.redirect_request(Request(URL), None, 302, "Found", {}, "https://example.com/avatar.jpg")
        self.assertEqual(handler.redirect_request(Request(URL), None, 302, "Found", {},
                          "https://avatars.steamstatic.com/other.jpg").full_url,
                         "https://avatars.steamstatic.com/other.jpg")
        opener = Mock()
        with patch("player_avatars.build_opener", return_value=opener):
            avatars._open_avatar(Request(URL))
            self.assertEqual(opener.open.call_args.kwargs["timeout"], 8)


if __name__ == "__main__":
    unittest.main()
