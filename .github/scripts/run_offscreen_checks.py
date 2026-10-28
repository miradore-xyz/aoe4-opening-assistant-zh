"""CI专用离屏分组：从原始字体字节登记，避免英语Windows的路径编码限制。

Windows插件在独立进程执行严格字体测试；本进程执行其余全部测试，不改运行源码。
"""
import faulthandler
import json
import os
from pathlib import Path
import sys
import unittest

# 原生Qt异常也保留当前Python栈；测试失败不等到整套完成才知正在运行哪项。
faulthandler.enable()
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
expected_path = font_root() / "得意黑/SmileySans-Oblique.ttf"
assert paths == [expected_path], "只能使用随附的唯一得意黑原字体。"
identities = [QFontDatabase.addApplicationFontFromData(QByteArray(path.read_bytes())) for path in paths]
assert all(identity >= 0 for identity in identities), "离屏插件未能从原字节登记真实字体。"

# 独立核对离屏实际字形的源表，不能仅靠字体名称或替代字体继续测试。
layout = QTextLayout("玩家查询 对局分析 Smiley Sans APM 0123456789 −×→↗", make_ui_font(14))
layout.beginLayout()
line = layout.createLine()
line.setLineWidth(1000)
layout.endLayout()
runs = layout.glyphRuns()
expected = QRawFont(QByteArray(expected_path.read_bytes()), 14)
assert expected.isValid(), "随附得意黑原字体必须可读。"
smiley_matches = bool(runs) and all(
    bytes(expected.fontTable(table)) and bytes(run.rawFont().fontTable(table)) == bytes(expected.fontTable(table))
    for run in runs for table in ("OS/2", "cmap", "glyf", "hmtx"))
assert smiley_matches, "离屏实际中英数字字形必须全部来自随附得意黑。"
assert all(glyph != 0 for run in runs for glyph in run.glyphIndexes()), "不能包含缺失字形。"
print(json.dumps({"platform": application.platformName(), "font_files_loaded": len(identities),
                  "smiley_tables_match": smiley_matches, "glyphs_nonzero": True}, ensure_ascii=False), flush=True)

def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item

# 用户已移除地形图与参考地图浏览；这些历史专属模块不属于当前发行能力。
# 名单逐项列出，不按名称片段或失败结果过滤；网格、地图名、全屏、拖动等当前测试继续运行。
RETIRED_TEST_MODULES = {
    "test_full_map_catalog": "完整地形资料包读取已退出当前产品",
    "test_full_map_integration": "完整参考地图与实战双模式接线已移除",
    "test_full_map_view": "地形瓦片与参考实例浏览组件已退出当前产品",
    "test_official_grid_renderer": "旧规则地形图绘制已移除，当前世界坐标网格另有专测",
    "test_official_map_browse": "无需录像的官方地图浏览入口已移除",
    "test_official_map_view": "旧官方参考图组件及双模式接线已移除",
    "test_official_maps": "旧74张参考地形目录与预览图已不打包",
}
loader = unittest.defaultTestLoader
all_cases = []
retired = []
for path in sorted(SOURCE.glob("test_*.py")):
    if path.stem in RETIRED_TEST_MODULES:
        retired.append({"module": path.stem, "reason": RETIRED_TEST_MODULES[path.stem]})
        continue
    # 按现有文件发现，旧专属模块不作为测试入口导入；其余加载错误照常作为失败呈现。
    all_cases.extend(cases(loader.loadTestsFromName(path.stem)))
font_cases = [case for case in all_cases if case.__class__.__module__ == "test_app_fonts"]
remaining = [case for case in all_cases if case.__class__.__module__ != "test_app_fonts"]
assert len(font_cases) == 2, "严格字体测试应由前置Windows插件步骤完整执行2项。"
print(json.dumps({"retired_feature_test_modules": retired}, ensure_ascii=False), flush=True)
print(f"Windows字体分组2项；本进程离屏分组{len(remaining)}项；合计{len(all_cases)}项。", flush=True)
result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(remaining))
raise SystemExit(0 if result.wasSuccessful() else 1)
