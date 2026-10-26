"""精简发行包：运行文件完整，源码在仓库提供，保留许可证和来源链接。

payload=随安装器或便携包分发的程序内容；metadata=更新与校验资料。
使用临时合成文件，不冻结程序、不执行安装器、不读取用户数据。
"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import release_build


class ReleasePayloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.project = self.root / "project"
        self.source = self.project / "src"
        self.distribution = self.root / "dist"
        self.source.mkdir(parents=True)
        self.distribution.mkdir()
        self.version = "1.9.4"
        self.repository = "example/aoe4-release-test"
        self.write(self.source / "version.py", 'APP_VERSION="1.9.4"\nAPP_CODENAME="王兰花版本"\n')
        self.write(self.source / "update_config.json", json.dumps({
            "github_repository": self.repository, "asset_name": release_build.SETUP_NAME}))
        # 开发目录和构建目录的额外文件均不能混入玩家安装内容。
        for name in ("app.py", "test_private_source.py", "build.ps1", "installer.iss"):
            self.write(self.source / name, "开发文件，不应随程序分发")
        self.write(self.source / "界面素材/只在开发目录的图片.png", b"source-only-image")
        self.write(self.distribution / "test_build_notes.py", "构建旁注，不应分发")
        self.write(self.distribution / "源码/developer.py", "旧目录旁注，不应分发")
        self.write(self.distribution / "启动中文助手.exe", b"synthetic-frozen-executable")
        self.runtime_files = {
            "python311.dll": b"synthetic-runtime",
            "base_library.zip": b"synthetic-python-runtime-archive",
            "assets/design/龙序.ico": b"synthetic-runtime-icon",
            "assets/design/字体/Sen/OFL.txt": "随字体提供的许可".encode("utf-8"),
            "assets/catalog/地图/完整实例/tiles/forest/natural/0/0/0.webp": b"synthetic-runtime-map",
            "assets/catalog/地图/来源说明.txt": "地图资料来源与版权说明".encode("utf-8"),
            "qt_zh_CN.qm": b"synthetic-chinese-translations",
        }
        for relative, content in self.runtime_files.items():
            self.write(self.distribution / "_internal" / relative, content)
        self.license = "GNU GENERAL PUBLIC LICENSE\nVersion 3\n"
        self.third_party = "第三方依赖的版权与许可声明"
        self.write(self.project / "LICENSE", self.license)
        self.write(self.project / "许可与依赖/第三方许可.txt", self.third_party)
        self.patch_source = patch.object(release_build, "SOURCE", self.source)
        self.patch_project = patch.object(release_build, "PROJECT", self.project)
        self.patch_source.start()
        self.patch_project.start()
        self.addCleanup(self.patch_source.stop)
        self.addCleanup(self.patch_project.stop)

    @staticmethod
    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))

    def assert_runtime_payload(self, payload):
        """检查玩家实际收到的文件和来源资料，不检查构建函数的内部调用顺序。"""
        self.assertEqual((payload / "启动中文助手.exe").read_bytes(),
                         (self.distribution / "启动中文助手.exe").read_bytes())
        for relative, expected in self.runtime_files.items():
            self.assertEqual((payload / "_internal" / relative).read_bytes(), expected, relative)
        self.assertEqual((payload / "LICENSE").read_text("utf-8"), self.license)
        self.assertEqual((payload / "许可与依赖/第三方许可.txt").read_text("utf-8"), self.third_party)
        notes = (payload / "版本与来源说明.txt").read_text("utf-8")
        self.assertIn(self.version, notes)
        self.assertIn("https://github.com/" + self.repository, notes)
        self.assertIn("https://github.com/CraftySalamander/RTS_Overlay", notes)
        self.assertIn("GNU GPL", notes)
        self.assertFalse((payload / "源码").exists())
        for path in payload.rglob("*"):
            if path.is_file():
                self.assertNotIn(path.suffix.lower(), {".py", ".ps1", ".iss"}, str(path))
                self.assertFalse(path.name.startswith("test_"), str(path))
                self.assertNotEqual(path.name, "AoE4OpeningAssistant-Source.zip")

    def test_stage_and_portable_keep_runtime_and_licenses_without_source(self):
        payload = self.root / "payload"
        release_build.stage_payload(self.distribution, payload, self.version)
        self.assert_runtime_payload(payload)
        archive_path = self.root / release_build.PORTABLE_NAME
        release_build.make_zip(payload, archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            self.assertIn("启动中文助手.exe", names)
            self.assertIn("LICENSE", names)
            self.assertIn("许可与依赖/第三方许可.txt", names)
            self.assertIn("_internal/assets/design/龙序.ico", names)
            self.assertFalse(any(name.startswith("源码/") or name.endswith(".py") for name in names))
            self.assertEqual(archive.read("_internal/qt_zh_CN.qm"), self.runtime_files["qt_zh_CN.qm"])

    def test_metadata_needs_only_installer_portable_and_update_manifest(self):
        output = self.root / "metadata"
        output.mkdir()
        for name in (release_build.SETUP_NAME, release_build.PORTABLE_NAME):
            self.write(output / name, ("发行验证：" + name).encode("utf-8"))
        release_build.write_release_metadata(output, self.version)
        manifest = json.loads((output / "update.json").read_text("utf-8"))
        self.assertEqual(manifest["version"], self.version)
        self.assertEqual(manifest["asset"]["name"], release_build.SETUP_NAME)
        self.assertEqual(manifest["asset"]["sha256"],
                         hashlib.sha256((output / release_build.SETUP_NAME).read_bytes()).hexdigest())
        checksums = {}
        for line in (output / "SHA256SUMS.txt").read_text("ascii").splitlines():
            digest, name = line.split("  ", 1)
            checksums[name] = digest
        self.assertEqual(set(checksums), {release_build.SETUP_NAME, release_build.PORTABLE_NAME, "update.json"})
        for name, digest in checksums.items():
            self.assertEqual(digest, hashlib.sha256((output / name).read_bytes()).hexdigest())
        build = json.loads((output / "release-build.json").read_text("utf-8"))
        self.assertEqual(set(build["assets"]), set(checksums) | {"SHA256SUMS.txt"})
        self.assertFalse((output / "AoE4OpeningAssistant-Source.zip").exists())

    def test_release_entrypoint_does_not_create_a_source_archive(self):
        output = self.root / "release"
        installer = self.root / "synthetic-iscc.exe"
        installer.write_bytes(b"not-executed")

        def compile_stub(arguments):
            """只替代外部编译器；程序暂存、便携压缩、元数据仍运行真实函数。"""
            self.assertEqual(Path(arguments[0]), installer)
            self.write(output / release_build.SETUP_NAME, b"synthetic-compiled-installer")

        arguments = ["release_build.py", "--version", self.version, "--dist-dir", str(self.distribution),
                     "--output-dir", str(output), "--iscc", str(installer)]
        with patch("sys.argv", arguments), patch("default_openings.validate_default_bundle"), \
                patch.object(release_build, "run", side_effect=compile_stub):
            self.assertEqual(release_build.main(), 0)
        self.assertEqual({path.name for path in output.iterdir()}, {
            release_build.SETUP_NAME, release_build.PORTABLE_NAME, "update.json", "SHA256SUMS.txt", "release-build.json"})
        with zipfile.ZipFile(output / release_build.PORTABLE_NAME) as archive:
            archive.extractall(self.root / "portable")
        self.assert_runtime_payload(self.root / "portable")


if __name__ == "__main__":
    unittest.main()
