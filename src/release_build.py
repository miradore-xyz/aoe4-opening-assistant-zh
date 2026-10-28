"""发布构建：冻结程序、整理无个人数据的发行目录、编译安装包并生成校验值。

Windows（微软桌面系统）上运行；所有命令参数均通过列表传递，不拼接命令行。
源码树可以放在仓库 src/，也可以沿用中文发行目录的“源码/”结构。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile


SOURCE = Path(__file__).resolve().parent
PROJECT = SOURCE.parent
SETUP_NAME = "AoE4OpeningAssistant-Setup.exe"
PORTABLE_NAME = "AoE4OpeningAssistant-Portable.zip"
FORBIDDEN_NAMES = {"用户数据", "离线语音包", "战绩缓存", "本地复盘", "公开回放", "对局详情", "玩家身份", "玩家头像", "__pycache__", ".git", ".build-env", ".venv"}


def version_from_source() -> str:
    """读取版本常量，不执行待发布代码。"""
    path = SOURCE / "version.py"
    if path.exists():
        for statement in ast.parse(path.read_text(encoding="utf-8-sig")).body:
            if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Constant):
                names = {target.id for target in statement.targets if isinstance(target, ast.Name)}
                if names & {"APP_VERSION", "VERSION"} and isinstance(statement.value.value, str):
                    return statement.value.value
    return "1.6.0"


def codename_from_source() -> str:
    """版本代号只作人类可读名称，数字版本仍独立用于更新比较。"""
    for statement in ast.parse((SOURCE / "version.py").read_text(encoding="utf-8-sig")).body:
        if (isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
                and any(isinstance(target, ast.Name) and target.id == "APP_CODENAME" for target in statement.targets)):
            return statement.value.value
    return ""


def run(arguments: list[str], *, cwd: Path = SOURCE) -> None:
    print("正在执行：", arguments[0], flush=True)
    subprocess.run(arguments, cwd=cwd, check=True)


def excluded(name: str) -> bool:
    """只排除构建缓存与用户数据；保留所有运行源码和许可证。"""
    lowered = name.casefold()
    return name in FORBIDDEN_NAMES or name.endswith((".pyc", ".pyo")) or "kokoro" in lowered


def copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, ignore=lambda _path, names: [name for name in names if excluded(name)])


def github_repository() -> str:
    """读取已配置的公开源码仓库；构建过程不推送代码或发布附件。"""
    configuration = json.loads((SOURCE / "update_config.json").read_text(encoding="utf-8-sig"))
    repository = configuration.get("github_repository", "")
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", repository):
        raise ValueError("更新配置中的GitHub仓库无效。")
    return repository


def project_resource(name: str) -> Path:
    for path in (PROJECT / name, SOURCE / name):
        if path.exists():
            return path
    raise FileNotFoundError(f"缺少发行资料：{name}")


def freeze_program(directory: Path) -> Path:
    """使用固定依赖的当前环境构建 PyInstaller（独立程序打包器）目录。"""
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir", "--noupx",
               "--name", "启动中文助手", "--paths", str(SOURCE / "upstream/python"), "--icon", str(SOURCE / "界面素材/龙序.ico")]
    for source, target in (("upstream/docs/assets", "assets"), ("高清图标", "assets/hd"),
                           ("界面素材", "assets/design"), ("文明旗帜", "assets/flags"),
                           ("默认开局", "assets/default_openings"),
                           ("qt_zh_CN.qm", "."), ("update_config.json", "assets")):
        command.extend(("--add-data", f"{SOURCE / source};{target}"))
    # 新程序只使用网格战场；保留文明/实体目录，单独排除全部旧地形图资料。
    for item in sorted((SOURCE / "文明资料").iterdir()):
        if item.name == "地图" or excluded(item.name):
            continue
        target = f"assets/catalog/{item.name}" if item.is_dir() else "assets/catalog"
        command.extend(("--add-data", f"{item};{target}"))
    command.extend(("--hidden-import", "win32com.client", "--hidden-import", "pythoncom", "--hidden-import", "pywintypes",
                    "--collect-all", "edge_tts", "--collect-data", "certifi", "--exclude-module", "sherpa_onnx",
                    "--exclude-module", "sherpa_onnx_core", "--exclude-module", "numpy", "--distpath", str(directory / "dist"),
                    "--workpath", str(directory / "build"), "--specpath", str(directory), str(SOURCE / "app.py")))
    run(command)
    return directory / "dist/启动中文助手"


def stage_payload(distribution: Path, destination: Path, version: str) -> None:
    """从新构建目录的两个明确入口取程序，不遍历用户正在使用的便携目录。"""
    executable = distribution / "启动中文助手.exe"
    runtime = distribution / "_internal"
    if not executable.is_file() or not runtime.is_dir():
        raise FileNotFoundError("构建目录必须含启动中文助手.exe 和 _internal。")
    destination.mkdir(parents=True)
    shutil.copy2(executable, destination / executable.name)
    copy_tree(runtime, destination / "_internal")
    shutil.copy2(project_resource("LICENSE"), destination / "LICENSE")
    copy_tree(project_resource("许可与依赖"), destination / "许可与依赖")
    (destination / "版本与来源说明.txt").write_text(
        f"AoE4 HELP {version} · {codename_from_source()}\n\n"
        "本项目基于 CraftySalamander/RTS_Overlay，沿用 GNU GPL 第三版许可证。\n"
        "原项目：https://github.com/CraftySalamander/RTS_Overlay\n"
        "上游提交：aaee02c95e47e4aadb7c3269fd07fb12b5871620\n"
        f"公开源码与构建说明：https://github.com/{github_repository()}\n"
        "国内源码镜像：https://gitee.com/miradore/aoe4-opening-assistant-zh\n"
        f"程序版本：{version}；正式发行对应源码以仓库同版本标签为准。\n"
        "程序包不附带源码目录或源码素材副本；许可证与第三方署名继续随附。\n"
        "游戏图像版权归原权利人，文明资料来源见_internal/assets/catalog内的来源说明。\n"
        "安装包不含个人开局、搜索记录、账号或语音缓存。\n"
        "默认安装数据目录：%APPDATA%/AoE4OpeningAssistant。\n"
        "便携版默认资料在程序旁“用户数据”；已有 data-location.json 时沿用其路径。\n"
        "安装器支持选择已有的“用户数据”目录，安装与卸载都不会删除该目录。\n",
        encoding="utf-8")
    assert_clean_payload(destination)


def assert_clean_payload(directory: Path) -> None:
    for path in directory.rglob("*"):
        if any(excluded(part) for part in path.relative_to(directory).parts):
            raise ValueError(f"发行目录含个人资料或缓存：{path.relative_to(directory)}")
        if path.is_file() and path.name in {"settings.json", "战绩搜索记录.json", "战绩查询.json", "data-location.json"}:
            raise ValueError(f"发行目录含个人设置：{path.relative_to(directory)}")


def make_zip(directory: Path, output: Path) -> None:
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(directory))


def find_compiler(explicit: str | None) -> str:
    candidates = [explicit, shutil.which("ISCC.exe"), os.environ.get("ISCC_PATH")]
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            candidates.extend((str(Path(base) / "Inno Setup 6/ISCC.exe"), str(Path(base) / "Programs/Inno Setup 6/ISCC.exe")))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise FileNotFoundError("未找到 Inno Setup 6 编译器；请用 --iscc 指定 ISCC.exe。")


def write_release_metadata(output: Path, version: str) -> None:
    """生成同版本更新清单与校验表，保证以后打标签发版无需手工补清单。"""
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", version):
        raise ValueError("正式发行版本必须是数字版本号，例如1.6.1。")
    configuration = json.loads((SOURCE / "update_config.json").read_text(encoding="utf-8-sig"))
    repository = github_repository()
    if configuration.get("asset_name", SETUP_NAME) != SETUP_NAME:
        raise ValueError("更新配置中的安装包名称与构建输出不一致。")
    setup = output / SETUP_NAME
    with setup.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    page = f"https://github.com/{repository}/releases/tag/v{version}"
    manifest = {
        "schema_version": 1,
        "version": version,
        "notes": f"AoE4 HELP {version} · {codename_from_source()}。本次更新内容见发行说明；安装会保留已有开局与设置。",
        "page_url": page,
        "asset": {"name": SETUP_NAME,
                  "url": f"https://github.com/{repository}/releases/download/v{version}/{SETUP_NAME}",
                  "sha256": checksum, "size": setup.stat().st_size},
    }
    (output / "update.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assets = [output / name for name in (SETUP_NAME, PORTABLE_NAME, "update.json")]
    hashes = []
    for path in assets:
        with path.open("rb") as stream:
            hashes.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {path.name}\n")
    (output / "SHA256SUMS.txt").write_text("".join(hashes), encoding="ascii")
    (output / "release-build.json").write_text(
        json.dumps({"version": version, "assets": [path.name for path in assets] + ["SHA256SUMS.txt"]},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=version_from_source(), help="产品版本，例如1.6.0")
    parser.add_argument("--dist-dir", type=Path, help="复用已冻结的构建目录；省略时从源码构建")
    parser.add_argument("--output-dir", type=Path, default=PROJECT / "发行产物", help="发行文件保存目录")
    parser.add_argument("--iscc", help="Inno Setup（安装包编译工具）的ISCC.exe绝对路径")
    arguments = parser.parse_args()
    from default_openings import validate_default_bundle
    validate_default_bundle()
    output = arguments.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    compiler = find_compiler(arguments.iscc)
    with tempfile.TemporaryDirectory(prefix="aoe4-release-") as temporary:
        scratch = Path(temporary)
        distribution = arguments.dist_dir.resolve() if arguments.dist_dir else freeze_program(scratch)
        payload = scratch / "payload"
        stage_payload(distribution, payload, arguments.version)
        make_zip(payload, output / PORTABLE_NAME)
        run([compiler, f"/DPayloadDir={payload}", f"/DAppVersion={arguments.version}",
             f"/DAppCodename={codename_from_source()}", f"/O{output}", str(SOURCE / "installer.iss")])
    write_release_metadata(output, arguments.version)
    print(f"发布文件已完成：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
