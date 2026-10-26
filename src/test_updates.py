"""更新验证：使用本地 HTTP（网络请求）替身，不请求真实发行 API。"""
import hashlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from urllib.error import URLError
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QWidget

from update_client import (DEFAULT_ASSET_NAME, TIMEOUT, USER_AGENT, ReleaseInfo,
                           UpdateCancelled, UpdateClient, UpdateError, version_key)
from update_ui import UpdateController


REPOSITORY = "example/empire-opening-assistant"
API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
PACKAGE = b"MZ" + b"\x00installer-test-content" * 80
CHECKSUM = hashlib.sha256(PACKAGE).hexdigest()
URL = f"https://github.com/{REPOSITORY}/releases/download/v1.10.0/{DEFAULT_ASSET_NAME}"
MIRROR = "https://download.example.cn/update.json"
MIRROR_URL = f"https://download.example.cn/releases/1.10.0/{DEFAULT_ASSET_NAME}"


class Response(io.BytesIO):
    """同时模拟响应正文、响应头和实际连接地址。"""
    def __init__(self, data, url, headers=None):
        super().__init__(data)
        self.url, self.headers = url, headers or {}

    def geturl(self):
        return self.url


class Network:
    """按地址提供固定回复，保留请求用于验证超时、次数与请求头。"""
    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        reply = self.replies[request.full_url]
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            return reply()
        body = json.dumps(reply).encode("utf-8") if isinstance(reply, dict) else reply
        return Response(body, request.full_url)


def github(version="v1.10.0", digest=True):
    """最小真实字段结构；安装包名称始终明确匹配。"""
    asset = {"name": DEFAULT_ASSET_NAME, "state": "uploaded", "size": len(PACKAGE),
             "browser_download_url": URL}
    if digest:
        asset["digest"] = "sha256:" + CHECKSUM
    return {"tag_name": version, "body": "缩小字体，加入应用内更新。", "draft": False,
            "prerelease": False, "html_url": f"https://github.com/{REPOSITORY}/releases/tag/{version}",
            "assets": [asset]}


def manifest(version="1.10.0", url=MIRROR_URL):
    return {"schema_version": 1, "version": version, "notes": "中文更新说明。",
            "page_url": "https://download.example.cn/",
            "asset": {"name": DEFAULT_ASSET_NAME, "url": url, "sha256": CHECKSUM,
                      "size": len(PACKAGE)}}


def client(network, mirror=False, current="1.9.0"):
    config = {"github_repository": REPOSITORY, "asset_name": DEFAULT_ASSET_NAME,
              "mirrors": [{"name": "国内下载", "manifest_url": MIRROR}] if mirror else []}
    return UpdateClient(config, current_version=current, opener=network)


class UpdateTests(unittest.TestCase):
    def test_numeric_version_and_no_downgrade_or_preview(self):
        self.assertGreater(version_key("1.10.0"), version_key("v1.9.99"))
        self.assertEqual(version_key("v1.6"), version_key("1.6.0"))
        for version in ("1.6.0-beta.1", "latest", "1", "", "../1.6.0", "9" * 100):
            with self.subTest(version=version), self.assertRaises(UpdateError):
                version_key(version)
        for version in ("v1.9.0", "v1.8.0"):
            self.assertIsNone(client(Network({API: github(version)})).check())
        for flag in ("draft", "prerelease"):
            data = github()
            data[flag] = True
            with self.subTest(flag=flag), self.assertRaises(UpdateError):
                client(Network({API: data})).check()

    def test_digest_release_and_verified_download_progress(self):
        network = Network({API: github(), URL: PACKAGE})
        updater = client(network)
        release = updater.check()
        self.assertEqual(release.version, "1.10.0")
        self.assertEqual(release.sha256, CHECKSUM)
        self.assertEqual(release.source, "GitHub")
        self.assertEqual(network.calls[0][0].get_header("User-agent"), USER_AGENT)
        self.assertTrue(all(timeout == TIMEOUT for _, timeout in network.calls))
        progress = []
        with tempfile.TemporaryDirectory() as directory:
            path = updater.download(release, directory, lambda done, total: progress.append((done, total)))
            self.assertEqual(path.read_bytes(), PACKAGE)
            self.assertTrue(updater.verify_download(path, release))
            self.assertEqual(progress[-1], (len(PACKAGE), len(PACKAGE)))
            self.assertEqual(list(Path(directory).glob("*.part")), [])
            self.assertEqual(updater.download(release, directory), path)
            self.assertEqual(len(network.calls), 2)
            path.write_bytes(b"corrupted")
            self.assertFalse(updater.verify_download(path, release))

    def test_missing_digest_uses_same_release_manifest(self):
        data = github(digest=False)
        manifest_url = f"https://github.com/{REPOSITORY}/releases/download/v1.10.0/update.json"
        data["assets"].append({"name": "update.json", "state": "uploaded",
                               "browser_download_url": manifest_url})
        same_release_manifest = manifest(url=URL)
        updater = client(Network({API: data, manifest_url: same_release_manifest}))
        self.assertEqual(updater.check().sha256, CHECKSUM)
        same_release_manifest["version"] = "1.11.0"
        with self.assertRaises(UpdateError):
            updater.check()

    def test_checksum_list_is_exact_name_and_digest_required(self):
        data = github(digest=False)
        sums_url = f"https://github.com/{REPOSITORY}/releases/download/v1.10.0/SHA256SUMS.txt"
        data["assets"].append({"name": "SHA256SUMS.txt", "state": "uploaded",
                               "browser_download_url": sums_url})
        sums = f"{'0' * 64}  Other-Setup.exe\n{CHECKSUM} *{DEFAULT_ASSET_NAME}\n".encode()
        self.assertEqual(client(Network({API: data, sums_url: sums})).check().sha256, CHECKSUM)
        with self.assertRaises(UpdateError):
            client(Network({API: github(digest=False)})).check()
        data = github()
        data["assets"].append(dict(data["assets"][0]))
        with self.assertRaises(UpdateError):
            client(Network({API: data})).check()

    def test_github_failure_uses_configured_manifest_source(self):
        network = Network({API: URLError("offline"), MIRROR: manifest(), MIRROR_URL: PACKAGE})
        updater = client(network, mirror=True)
        release = updater.check()
        self.assertEqual(release.source, "国内下载")
        self.assertEqual(len(updater.last_errors), 1)
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(updater.download(release, directory).read_bytes(), PACKAGE)

    def test_download_uses_identical_mirror_and_never_crosses_version(self):
        network = Network({API: github(), MIRROR: manifest(), URL: URLError("unreachable"),
                           MIRROR_URL: PACKAGE})
        updater = client(network, mirror=True)
        release = updater.check()
        self.assertEqual(release.source, "GitHub")
        self.assertEqual(len(release.fallbacks), 1)
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(updater.download(release, directory).is_file())
        network.replies[MIRROR] = manifest(version="1.9.1")
        self.assertEqual(updater.check().fallbacks, ())
        mismatch = manifest()
        mismatch["asset"]["sha256"] = "0" * 64
        network.replies[MIRROR] = mismatch
        self.assertEqual(updater.check().fallbacks, ())

    def test_invalid_manifest_addresses_and_missing_fields(self):
        for change in ({"schema_version": True}, {"version": "1.10.0-beta"}, {"unexpected": "value"}):
            data = manifest()
            data.update(change)
            with self.subTest(change=change), self.assertRaises(UpdateError):
                client(Network({API: URLError("offline"), MIRROR: data}), mirror=True).check()
        for key, value in (("sha256", ""), ("size", True), ("size", -1),
                           ("url", "http://download.example.cn/Setup.exe"),
                           ("name", "different.exe"), ("url", "https://download.example.cn/file.zip")):
            data = manifest()
            data["asset"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(UpdateError):
                client(Network({API: URLError("offline"), MIRROR: data}), mirror=True).check()
        for url in ("http://example.cn/update.json", "https://user:pass@example.cn/update.json",
                    "https://example.cn:444/update.json"):
            with self.subTest(url=url), self.assertRaises(UpdateError):
                UpdateClient({"mirrors": [{"name": "国内", "manifest_url": url}]})

    def test_corruption_size_and_non_executable_never_replace_existing_file(self):
        for body in (b"bad", PACKAGE + b"extra", b"XX" + PACKAGE[2:]):
            updater = client(Network({API: github(), URL: body}))
            release = updater.check()
            with self.subTest(length=len(body)), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / DEFAULT_ASSET_NAME
                target.write_bytes(b"existing file")
                with self.assertRaises(UpdateError):
                    updater.download(release, directory)
                self.assertEqual(target.read_bytes(), b"existing file")
                self.assertEqual(list(Path(directory).glob("*.part")), [])

    def test_cancellation_cleans_partial_and_does_not_try_mirror(self):
        network = Network({API: github(), MIRROR: manifest(), URL: PACKAGE, MIRROR_URL: PACKAGE})
        updater = client(network, mirror=True)
        release = updater.check()
        def cancel(done, total):
            if done:
                raise UpdateCancelled("下载已取消")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(UpdateCancelled):
                updater.download(release, directory, cancel)
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertNotIn(MIRROR_URL, [request.full_url for request, _ in network.calls])

    def test_https_redirect_response_and_content_length_are_checked(self):
        for response in (lambda: Response(PACKAGE, "http://example.cn/file.exe"),
                         lambda: Response(PACKAGE, URL, {"Content-Length": "3"})):
            network = Network({API: github(), URL: response})
            updater = client(network)
            with self.subTest(response=response), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(UpdateError):
                    updater.download(updater.check(), directory)
                self.assertEqual(list(Path(directory).iterdir()), [])


class UpdateDialogTests(unittest.TestCase):
    """用真实 Qt 布局验证更新状态切换；不联网、不启动安装器。"""

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.quit_policy = cls.application.quitOnLastWindowClosed()
        cls.application.setQuitOnLastWindowClosed(False)

    @classmethod
    def tearDownClass(cls):
        cls.application.setQuitOnLastWindowClosed(cls.quit_policy)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        config = Path(self.directory.name) / "update.json"
        config.write_text(json.dumps({"github_repository": REPOSITORY}), encoding="utf-8")
        self.manager = QWidget()
        self.manager.data_dir = Path(self.directory.name)
        self.manager.window_bar = QWidget(self.manager)
        self.manager.window_bar.status_layout = QHBoxLayout(self.manager.window_bar)
        with patch("update_ui.config_path", return_value=config):
            self.controller = UpdateController(self.manager)
        self.controller._startup_timer.stop()
        self.dialog = self.controller._show_dialog()
        self.application.processEvents()

    def tearDown(self):
        self.controller._stopped.set()
        self.dialog.close()
        self.manager.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.application.processEvents()
        self.directory.cleanup()

    @staticmethod
    def release(notes="更新说明第一条。\n更新说明第二条。"):
        return ReleaseInfo("1.10.0", notes, URL, CHECKSUM, len(PACKAGE), "GitHub", "https://github.com/" + REPOSITORY)

    def test_latest_after_release_removes_panel_and_shrinks_dialog(self):
        """复用已展开的窗口检查到最新版后，说明、来源和空白高度一起撤销。"""
        self.controller._checked(self.release(), None, True)
        self.application.processEvents()
        expanded_height = self.dialog.height()
        self.assertTrue(self.dialog.notes.isVisible())
        self.assertTrue(self.dialog.primary.isVisible())
        with patch("update_ui.threading.Thread"):
            self.controller.check(manual=True)
        self.assertEqual(self.dialog.heading.text(), "正在检查更新…")
        self.assertFalse(self.dialog.notes.isVisible())
        self.controller._checked(None, None, True)
        self.application.processEvents()
        self.assertEqual(self.dialog.heading.text(), "已是最新版本")
        self.assertFalse(self.dialog.notes.isVisible())
        self.assertFalse(self.dialog.source.isVisible())
        self.assertFalse(self.dialog.status.isVisible())
        self.assertFalse(self.dialog.primary.isVisible())
        self.assertTrue(self.dialog.cancel.isVisible())
        self.assertLess(self.dialog.height(), expanded_height - 100)
        self.assertLess(self.dialog.height(), 250)
        self.assertLess(self.dialog.cancel.geometry().bottom(), self.dialog.cancel.parentWidget().height())

    def test_offline_and_configuration_errors_remain_visible_without_old_notes(self):
        """检查失败必须保留错误，不把上一次的更新说明当作错误区域。"""
        self.controller._checked(self.release(), None, True)
        message = "无法连接更新来源。\n请检查网络后重试。"
        self.controller._checked(None, message, True)
        self.application.processEvents()
        self.assertEqual(self.dialog.status.text(), message)
        self.assertTrue(self.dialog.status.isVisible())
        self.assertFalse(self.dialog.notes.isVisible())
        self.assertFalse(self.dialog.source.isVisible())
        self.assertFalse(self.dialog.primary.isVisible())
        self.assertGreaterEqual(self.dialog.status.height(), self.dialog.status.fontMetrics().height() * 2)
        self.controller.client = None
        self.controller._configuration_error = "更新配置无法读取。"
        self.controller.check(manual=True)
        self.assertTrue(self.dialog.status.isVisible())
        self.assertEqual(self.dialog.status.text(), "更新配置无法读取。")

    def test_new_release_without_notes_keeps_download_and_download_error(self):
        """没有发行说明时仍可下载，但不编造说明或留下空面板。"""
        for notes in ("", " \n\t "):
            with self.subTest(notes=repr(notes)):
                self.controller._checked(self.release(notes), None, True)
                self.application.processEvents()
                self.assertFalse(self.dialog.notes.isVisible())
                self.assertTrue(self.dialog.source.isVisible())
                self.assertTrue(self.dialog.primary.isVisible())
                self.assertTrue(self.dialog.primary.isEnabled())
                self.assertIn("1.10.0", self.dialog.heading.text())
        self.controller._downloaded(None, "安装包下载失败，请稍后重试。", False)
        self.assertTrue(self.dialog.status.isVisible())
        self.assertIn("下载失败", self.dialog.status.text())
        self.assertEqual(self.dialog.primary.text(), "重新下载")
        self.assertFalse(self.dialog.progress.isVisible())

    def test_error_reflows_for_larger_font_and_wrapped_messages(self):
        """微软雅黑负行间距和较大字号都要留足正文高度，宽度变化仍重新换行。"""
        for pixels in (14, 18):
            with self.subTest(pixels=pixels):
                self.dialog.status.setStyleSheet(f"font-family:'Microsoft YaHei UI';font-size:{pixels}px;")
                self.controller._checked(None, "无法连接更新来源。\n请检查网络后重试。", True)
                self.application.processEvents()
                self.assertGreaterEqual(self.dialog.status.height(), self.dialog.status.fontMetrics().height() * 2)
                message = "无法连接更新来源，请检查网络后重试。" * 5
                self.dialog.resize(460, self.dialog.height())
                self.dialog.show_summary("暂时无法检查更新", message)
                self.application.processEvents()
                narrow_height = self.dialog.status.height()
                self.assertGreater(narrow_height, self.dialog.status.fontMetrics().height() * 2)
                self.assertGreaterEqual(narrow_height,
                                        self.dialog.status.heightForWidth(self.dialog.status.width()))
                self.dialog.resize(720, self.dialog.height())
                self.dialog.refresh_layout()
                self.application.processEvents()
                self.assertLess(self.dialog.status.height(), narrow_height)
                self.assertGreaterEqual(self.dialog.status.height(),
                                        self.dialog.status.heightForWidth(self.dialog.status.width()))
                self.assertLess(self.dialog.status.geometry().bottom(), self.dialog.cancel.geometry().top())


if __name__ == "__main__":
    unittest.main()
