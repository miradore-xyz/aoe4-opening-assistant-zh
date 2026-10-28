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
from PyQt5.QtGui import QFontDatabase, QRawFont, QTextLayout
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
        ascii_identity = None
        if arguments.platform == "offscreen":
            ascii_path = Path(directory) / path.name
            ascii_path.write_bytes(data)
            ascii_identity = QFontDatabase.addApplicationFont(str(ascii_path))
        rows.append({"path": str(path.relative_to(font_root())), "exists": path.is_file(),
                     "bytes": path.stat().st_size, "qt_font_id": identity,
                     "families": QFontDatabase.applicationFontFamilies(identity) if identity >= 0 else [],
                     "memory_font_id": memory_identity, "ascii_path_font_id": ascii_identity})
        if ascii_identity is not None and ascii_identity >= 0:
            # 移除ASCII对照注册后再清理临时目录，保留内存字体供字形核对。
            QFontDatabase.removeApplicationFont(ascii_identity)
# 英文与数字同时核验；逐表确认真实字形，不能用系统字体补缺冒充得意黑。
layout = QTextLayout("玩家查询 对局分析 Smiley Sans APM 0123456789 −×→↗", make_ui_font(14))
layout.beginLayout()
line = layout.createLine()
line.setLineWidth(1000)
layout.endLayout()
runs = layout.glyphRuns()
families = [run.rawFont().familyName() for run in runs]
# 家族名可被平台报告为别名，原始字体表才是实际来源证据。
expected_path = font_root() / "得意黑/SmileySans-Oblique.ttf"
expected = QRawFont(QByteArray(expected_path.read_bytes()), 14)
tables = ("OS/2", "cmap", "glyf", "hmtx")
smiley_matches = bool(runs) and expected.isValid() and all(
    bytes(expected.fontTable(table)) and bytes(run.rawFont().fontTable(table)) == bytes(expected.fontTable(table))
    for run in runs for table in tables)
glyphs_nonzero = bool(runs) and all(glyph != 0 for run in runs for glyph in run.glyphIndexes())
valid = (len(registered) == 1 and len(rows) == 1
         and rows[0]["path"] == str(expected_path.relative_to(font_root()))
         and rows[0]["qt_font_id"] >= 0 and smiley_matches and glyphs_nonzero)
print(json.dumps({"qt_headers": QT_VERSION_STR, "qt_runtime": qVersion(),
                  "windows_ansi_codepage": ctypes.windll.kernel32.GetACP(), "requested_platform": arguments.platform,
                  "actual_platform": application.platformName(), "font_root": str(font_root()),
                  "font_root_exists": font_root().is_dir(), "plugin_paths": QCoreApplication.libraryPaths(),
                  "initial_registered_count": len(registered), "files": rows,
                  "glyph_families": families, "smiley_tables_match": smiley_matches,
                  "glyphs_nonzero": glyphs_nonzero, "strict_font_probe_passed": valid}, ensure_ascii=False), flush=True)
raise SystemExit(0 if valid else 1)
