"""验证实际品牌显示和最小窗口布局；不访问网络或用户数据。"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QTimer
from PyQt5.QtGui import QFont, QFontDatabase, QFontMetrics
from PyQt5.QtWidgets import QApplication, QLabel

from app import Manager, STYLE
from core import atomic_json


class BrandNameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.original_quit_policy = cls.application.quitOnLastWindowClosed()
        cls.original_font = cls.application.font()
        cls.application.setQuitOnLastWindowClosed(False)
        # 清除前组主窗口退出留下的事件循环状态，再测量真实布局。
        QTimer.singleShot(0, cls.application.quit)
        cls.application.exec_()
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            path = Path("C:/Windows/Fonts") / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
        cls.application.setFont(QFont("Microsoft YaHei UI", 10))
        cls.application.setStyleSheet(STYLE)

    @classmethod
    def tearDownClass(cls):
        cls.application.setFont(cls.original_font)
        cls.application.setQuitOnLastWindowClosed(cls.original_quit_policy)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        folder = Path(self.directory.name)
        atomic_json(folder / "设置.json", {
            "speech": False, "sound": False, "voice_engine": "system", "hotkeys": {},
            "timed_playback_policy": 1,
        })
        self.patches = [
            patch.object(Manager, "seed"),
            patch("app.Speech.start"),
            patch("app.Manager.register_hotkeys"),
            patch("update_ui.UpdateController.check"),
        ]
        for item in self.patches:
            item.start()
        self.manager = Manager(self.application, folder)
        self.manager.tick_timer.stop()

    def tearDown(self):
        from test_app import destroy_test_manager
        # 与其他真实Manager夹具共用精确销毁；含独立正文窗及未入布局的资源格。
        destroy_test_manager(self.application, self.manager)
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def test_product_name_and_license_credit_in_actual_widgets(self):
        """产品名称一致，同时保留上游作者与原项目的许可署名。"""
        manager = self.manager
        self.assertEqual(manager.windowTitle(), "AoE4 HELP")
        self.assertEqual(manager.accessibleName(), "AoE4 HELP")
        self.assertEqual(manager.window_bar.title.text(), "AoE4 HELP")
        self.assertEqual(manager.window_bar.title.accessibleName(), "AoE4 HELP")
        self.assertEqual(manager.window_bar.title.parentWidget().accessibleName(), "AoE4 HELP")
        credit = "\n".join(label.text() for label in manager.help_scroll.findChildren(QLabel))
        self.assertIn("AoE4 HELP", credit)
        self.assertIn("CraftySalamander", credit)
        self.assertIn("RTS_Overlay", credit)
        self.assertNotIn("帝国开局助手", credit)

    def test_minimum_width_keeps_brand_and_navigation_complete(self):
        """760 像素最小窗口中，文字宽度、导航和关闭按钮均不互相裁切。"""
        manager = self.manager
        manager.resize(760, 520)
        manager.show()
        self.application.processEvents()
        bar = manager.window_bar
        metrics = QFontMetrics(bar.title.font())
        self.assertGreaterEqual(bar.title.contentsRect().width(), metrics.horizontalAdvance("AoE4 HELP"))
        self.assertGreaterEqual(bar.title.contentsRect().height(), metrics.height())
        self.assertEqual(bar.crest_label.width(), 26)
        self.assertEqual(bar.crest_label.height(), 26)
        self.assertLessEqual(bar.title.parentWidget().geometry().right(), bar.navigation_container.geometry().left())
        self.assertLessEqual(bar.navigation_container.geometry().right(), bar.minimize_button.geometry().left())
        self.assertLess(bar.close_button.geometry().right(), bar.width())

    def test_largest_body_font_keeps_navigation_and_speed_text_complete(self):
        """760窄窗口与18像素正文下，导航和默认语速保留完整字宽。"""
        from PyQt5.QtWidgets import QStyle, QStyleOptionComboBox
        manager = self.manager
        manager.settings['ui_font_size'] = 18
        manager.apply_ui_font()
        manager.resize(760, 650)
        manager.show()
        manager.navigate(1)
        self.application.processEvents()
        for control in manager.navigation:
            self.assertGreaterEqual(control.width(), control.fontMetrics().horizontalAdvance(control.text()) + 26)
        bar = manager.window_bar
        self.assertLess(bar.navigation_container.geometry().right(), bar.minimize_button.geometry().left())
        option = QStyleOptionComboBox()
        manager.rate_input.initStyleOption(option)
        field = manager.rate_input.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, manager.rate_input)
        self.assertGreaterEqual(field.width(), manager.rate_input.fontMetrics().horizontalAdvance(manager.rate_input.currentText()))

    def test_about_page_has_factual_statement_and_both_source_links(self):
        """关于页移除使用教程，并同时保留上游署名与本项目源码入口。"""
        manager = self.manager
        manager.navigate(2)
        self.assertEqual(manager.navigation[-1].text(), "关于")
        self.assertEqual(manager.page_title.text(), "关于")
        self.assertEqual(manager.page_subtitle.text(), "")
        texts = "\n".join(label.text() for label in manager.help_scroll.findChildren(QLabel))
        self.assertNotIn("开始一次演练", texts)
        self.assertNotIn("按时间提示，按局势调整", texts)
        self.assertIn("不读取或修改游戏运行时数据", texts)
        self.assertIn("不提供自动操作游戏的脚本功能", texts)
        self.assertIn("游戏的意义不止于胜负", texts)
        self.assertIn("原项目与许可证", texts)
        self.assertIn("GitHub 源码", texts)
        self.assertIn("Gitee 源码", texts)
        self.assertIn("https://github.com/miradore-xyz/aoe4-opening-assistant-zh", texts)

    def test_command_line_help_preserves_replay_option(self):
        """命令行说明显示新名称，显式打开本地录像的入口仍然存在。"""
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("app.py")), "--help"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("AoE4 HELP", result.stdout)
        self.assertIn("--replay", result.stdout)


if __name__ == "__main__":
    unittest.main()
