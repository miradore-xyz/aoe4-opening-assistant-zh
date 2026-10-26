"""主窗口关闭后，独立悬浮窗不能继续回调已经销毁的设置控件。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import sip
from PyQt5.QtCore import QEvent, QTimer
from PyQt5.QtTest import QSignalSpy, QTest

# 仅导入模块，不把其中的 TestCase（测试类）暴露为本模块成员，避免重复收集。
import test_brand_name as brand_fixture


class BrandFixtureCleanupTests(unittest.TestCase):
    """测试夹具必须清理自己的独立顶层，不能把生命周期推给随机垃圾回收。"""

    @classmethod
    def setUpClass(cls):
        brand_fixture.BrandNameTests.setUpClass()
        cls.application = brand_fixture.BrandNameTests.application

    @classmethod
    def tearDownClass(cls):
        brand_fixture.BrandNameTests.tearDownClass()

    def test_each_fixture_teardown_destroys_its_independent_windows_before_next_case(self):
        for _ in range(3):
            fixture = brand_fixture.BrandNameTests("test_product_name_and_license_credit_in_actual_widgets")
            fixture.setUp()
            manager = fixture.manager
            overlay, goals, handle = manager.overlay, manager.overlay.goals, manager.overlay.lock_handle
            manager.show()
            overlay.show()
            self.application.processEvents()
            fixture.tearDown()
            for widget in (manager, overlay, goals, handle):
                self.assertTrue(sip.isdeleted(widget), "下一用例开始前，本轮Qt窗口必须已经销毁")
            # 保留Python引用仍须销毁原生对象；不能靠del或GC恰巧发生才通过。
            self.application.processEvents()


class OverlayLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        brand_fixture.BrandNameTests.setUpClass()
        cls.application = brand_fixture.BrandNameTests.application

    @classmethod
    def tearDownClass(cls):
        brand_fixture.BrandNameTests.tearDownClass()

    def setUp(self):
        self.fixture = brand_fixture.BrandNameTests("test_product_name_and_license_credit_in_actual_widgets")
        self.fixture.setUp()
        self.manager_deleted = False

    def tearDown(self):
        fixture, manager = self.fixture, self.fixture.manager
        overlay = manager.overlay
        if self.manager_deleted:
            # 主窗口已在测试内销毁，不能再调用通用 fixture 的 manager.close()。
            overlay.resize_save.stop()
            overlay.hide()
            self.application.removeNativeEventFilter(manager.hotkeys)
            overlay.deleteLater()
            self.application.sendPostedEvents(None, QEvent.DeferredDelete)
            self.application.processEvents()
            for item in reversed(fixture.patches):
                item.stop()
            fixture.directory.cleanup()
        else:
            fixture.tearDown()

    def test_pending_resize_is_saved_and_cannot_call_deleted_manager(self):
        manager = self.fixture.manager
        overlay = manager.overlay
        manager.show()
        overlay.show()
        self.application.processEvents()
        overlay.resize(410, 280)
        self.application.processEvents()
        self.assertTrue(overlay.resize_save.isActive(), "实际调整尺寸应排入250毫秒后的保存任务")
        final_size = (overlay.width(), overlay.height())
        timeout_calls = QSignalSpy(overlay.resize_save.timeout)
        settings_path = Path(self.fixture.directory.name) / "设置.json"

        manager.close()
        manager.deleteLater()
        self.application.sendPostedEvents(None, QEvent.DeferredDelete)
        self.manager_deleted = sip.isdeleted(manager)
        self.assertTrue(self.manager_deleted)
        self.assertFalse(sip.isdeleted(overlay), "悬浮窗为独立顶层窗口，验证它仍存活的真实场景")

        # 独立心跳证明关闭后仍实际处理事件，不能因QApp退出状态而空等后误判通过。
        heartbeat = QTimer()
        heartbeat.setInterval(20)
        heartbeats = QSignalSpy(heartbeat.timeout)
        heartbeat.start()
        QTest.qWait(350)
        heartbeat.stop()
        self.assertGreaterEqual(len(heartbeats), 3)
        self.assertEqual(len(timeout_calls), 0, "关闭后不应再触发访问已销毁主窗口的尺寸保存")
        saved = json.loads(settings_path.read_text(encoding="utf-8"))
        self.assertEqual((saved["width"], saved["height"]), final_size,
                         "取消延迟任务后仍应保存关闭前最后的真实尺寸")


if __name__ == "__main__":
    unittest.main()
