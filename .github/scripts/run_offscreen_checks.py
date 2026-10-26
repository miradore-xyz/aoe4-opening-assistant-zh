"""CI专用离屏分组：从原始字体字节登记，避免英语Windows的路径编码限制。

Windows插件在独立进程执行严格字体测试；本进程执行其余全部测试，不改运行源码。
"""
import json
import os
from pathlib import Path
import sys
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src"
sys.path.insert(0, str(SOURCE))

from PyQt5.QtCore import QByteArray
from PyQt5.QtGui import QFontDatabase, QRawFont, QTextLayout
from PyQt5.QtWidgets import QApplication
from app_fonts import font_root, make_ui_font

application = QApplication([])
application.setQuitOnLastWindowClosed(False)
paths = sorted(path for path in font_root().rglob("*") if path.suffix.lower() in {".ttf", ".otf"})
assert len(paths) == 7, "必须使用随附的全部7个字体文件。"
identities = [QFontDatabase.addApplicationFontFromData(QByteArray(path.read_bytes())) for path in paths]
assert all(identity >= 0 for identity in identities), "离屏插件未能从原字节登记真实字体。"

# 独立核对离屏实际字形的源表，不能仅靠字体名称或替代字体继续测试。
layout = QTextLayout("玩家查询 Sen 1234", make_ui_font(14))
layout.beginLayout()
line = layout.createLine()
line.setLineWidth(1000)
layout.endLayout()
runs = layout.glyphRuns()
sen = QRawFont(QByteArray((font_root() / "Sen/Sen-Regular.ttf").read_bytes()), 14)
han = QRawFont(QByteArray((font_root() / "思源黑体/SourceHanSansSC-Regular.otf").read_bytes()), 14)
def same_tables(actual, expected, tables):
    return all(bytes(expected.fontTable(table)) and bytes(actual.fontTable(table)) == bytes(expected.fontTable(table))
               for table in tables)
sen_matches = any(same_tables(run.rawFont(), sen, ("OS/2", "glyf", "hmtx")) for run in runs)
han_matches = any(same_tables(run.rawFont(), han, ("OS/2", "cmap", "hmtx")) for run in runs)
assert sen_matches and han_matches, "离屏实际中英字形必须来自随附Sen与思源字体。"
assert all(glyph != 0 for run in runs for glyph in run.glyphIndexes()), "不能包含缺失字形。"
print(json.dumps({"platform": application.platformName(), "font_files_loaded": len(identities),
                  "sen_tables_match": sen_matches, "source_han_tables_match": han_matches,
                  "glyphs_nonzero": True}, ensure_ascii=False), flush=True)

def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item

all_cases = list(cases(unittest.defaultTestLoader.discover(str(SOURCE), pattern="test_*.py")))
font_cases = [case for case in all_cases if case.__class__.__module__ == "test_app_fonts"]
remaining = [case for case in all_cases if case.__class__.__module__ != "test_app_fonts"]
assert len(font_cases) == 2, "严格字体测试应由前置Windows插件步骤完整执行2项。"
print(f"Windows字体分组2项；本进程离屏分组{len(remaining)}项；合计{len(all_cases)}项。", flush=True)
result = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(remaining))
raise SystemExit(0 if result.wasSuccessful() else 1)
