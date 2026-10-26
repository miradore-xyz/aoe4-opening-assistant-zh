"""发行代号验证：名称进入展示说明，数字版本继续用于更新协议；不构建程序。"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import release_build
from update_client import UpdateClient, UpdateError, version_key
from version import APP_CODENAME, APP_VERSION


class ReleaseCodenameTests(unittest.TestCase):
    def write_metadata(self, directory, version):
        """只准备小型附件替身并调用元数据函数，不运行打包器或安装器。"""
        output = Path(directory)
        for name in (release_build.SETUP_NAME, release_build.PORTABLE_NAME):
            (output / name).write_bytes(("仅用于发行元数据验证：" + name).encode("utf-8"))
        with patch.object(release_build, "run", side_effect=AssertionError("元数据验证不应执行外部构建")):
            release_build.write_release_metadata(output, version)
        return json.loads((output / "update.json").read_text(encoding="utf-8"))

    def test_codename_keeps_numeric_update_comparison_independent(self):
        self.assertEqual(release_build.version_from_source(), APP_VERSION)
        self.assertEqual(release_build.codename_from_source(), APP_CODENAME)
        self.assertEqual(APP_CODENAME, "王兰花版本")
        self.assertGreater(version_key(APP_VERSION), version_key("1.7.8"))
        self.assertGreater(version_key("1.10.0"), version_key(APP_VERSION))
        with self.assertRaises(UpdateError):
            version_key(f"{APP_VERSION} · {APP_CODENAME}")

    def test_distribution_notes_contain_codename_but_manifest_stays_numeric(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.write_metadata(directory, APP_VERSION)
            self.assertTrue(manifest["notes"].startswith("AoE4 HELP "))
            self.assertIn(APP_CODENAME, manifest["notes"])
            self.assertEqual(manifest["version"], APP_VERSION)
            self.assertTrue(manifest["page_url"].endswith("/tag/v" + APP_VERSION))
            self.assertIn("/download/v" + APP_VERSION + "/", manifest["asset"]["url"])
            self.assertNotIn(APP_CODENAME, manifest["asset"]["url"])
            configuration = json.loads((release_build.SOURCE / "update_config.json").read_text(encoding="utf-8-sig"))
            release = UpdateClient(configuration, current_version="1.7.8")._manifest(manifest, "本地元数据验证")
            self.assertEqual(release.version, APP_VERSION)
            setup = Path(directory) / release_build.SETUP_NAME
            self.assertEqual(release.sha256, hashlib.sha256(setup.read_bytes()).hexdigest())
            build = json.loads((Path(directory) / "release-build.json").read_text(encoding="utf-8"))
            self.assertEqual(build["version"], APP_VERSION)

    def test_old_source_without_codename_still_generates_usable_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "version.py").write_text('VERSION = "1.7.8"\n', encoding="utf-8")
            configuration = {"github_repository": "example/aoe4-release-test", "asset_name": release_build.SETUP_NAME}
            (source / "update_config.json").write_text(json.dumps(configuration), encoding="utf-8")
            output = root / "metadata"
            output.mkdir()
            with patch.object(release_build, "SOURCE", source):
                self.assertEqual(release_build.version_from_source(), "1.7.8")
                self.assertEqual(release_build.codename_from_source(), "")
                manifest = self.write_metadata(output, release_build.version_from_source())
            release = UpdateClient(configuration, current_version="1.7.7")._manifest(manifest, "旧版兼容验证")
            self.assertEqual(release.version, "1.7.8")
            self.assertNotIn(APP_CODENAME, release.notes)

    def test_installer_display_name_does_not_replace_machine_version(self):
        installer = (release_build.SOURCE / "installer.iss").read_text(encoding="utf-8-sig")
        self.assertIn('  #define ProductName "AoE4 HELP"', installer.splitlines())
        self.assertIn("AppVersion={#AppVersion}", installer.splitlines())
        self.assertIn('  #define AppCodename ""', installer.splitlines())
        self.assertIn("AppVerName={#ProductName} {#AppVersion} · {#AppCodename}", installer.splitlines())


if __name__ == "__main__":
    unittest.main()
