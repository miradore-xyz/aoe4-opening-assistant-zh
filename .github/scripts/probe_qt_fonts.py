"""在隔离进程诊断Qt插件与随附字体；严格检查中英字形，不修改运行源码。"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument("--platform", choices=("offscreen", "windows"), required=True)
arguments = parser.parse_args()
os.environ["QT_QPA_PLATFORM"] = arguments.platform
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from PyQt5.QtCore import QByteArray, QCoreApplication, QT_VERSION_STR, qVersion
from PyQt5.QtGui import QFontDatabase, QTextLayout
from PyQt5.QtWidgets import QApplication
from app_fonts import font_root, make_ui_font, register_ui_fonts

application = QApplication([])
# 先测生产入口的首次装载，后续逐文件诊断不能掩盖首次失败。
registered = register_ui_fonts()
rows = []
# 对照相同字体原字节的内存装载及纯ASCII路径，区分插件缺能力与路径编码问题。
with tempfile.TemporaryDirectory(prefix="qt-font-probe-") as directory:
    for path in sorted(font_root().rglob("*")):
        if path.suffix.lower() not in {".ttf", ".otf"}:
            continue
        identity = QFontDatabase.addApplicationFont(str(path))
        data = path.read_bytes()
        memory_identity = QFontDatabase.addApplicationFontFromData(QByteArray(data))
        ascii_path = Path(directory) / path.name
        ascii_path.write_bytes(data)
        ascii_identity = QFontDatabase.addApplicationFont(str(ascii_path))
        rows.append({"path": str(path.relative_to(font_root())), "exists": path.is_file(),
                     "bytes": path.stat().st_size, "qt_font_id": identity,
                     "families": QFontDatabase.applicationFontFamilies(identity) if identity >= 0 else [],
                     "memory_font_id": memory_identity, "ascii_path_font_id": ascii_identity})
layout = QTextLayout("玩家查询 Sen 1234", make_ui_font(14))
layout.beginLayout()
line = layout.createLine()
line.setLineWidth(1000)
layout.endLayout()
runs = layout.glyphRuns()
families = [run.rawFont().familyName() for run in runs]
# Windows字体插件可能按系统语言返回“思源黑体”，与已登记文件的英文家族名同源。
valid = (len(registered) == 7 and len(rows) == 7 and all(row["qt_font_id"] >= 0 for row in rows)
         and any(name.startswith("Sen") for name in families)
         and any(name in {"Source Han Sans SC", "思源黑体"} for name in families)
         and all(glyph != 0 for run in runs for glyph in run.glyphIndexes()))
print(json.dumps({"qt_headers": QT_VERSION_STR, "qt_runtime": qVersion(),
                  "windows_ansi_codepage": ctypes.windll.kernel32.GetACP(), "requested_platform": arguments.platform,
                  "actual_platform": application.platformName(), "font_root": str(font_root()),
                  "font_root_exists": font_root().is_dir(), "plugin_paths": QCoreApplication.libraryPaths(),
                  "initial_registered_count": len(registered), "files": rows,
                  "glyph_families": families, "strict_font_probe_passed": valid}, ensure_ascii=False), flush=True)
raise SystemExit(0 if valid else 1)
