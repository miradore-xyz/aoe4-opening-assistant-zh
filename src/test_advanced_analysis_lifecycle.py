"""高级分析后台生命周期：强删Qt父对象、延迟结果、代次缓存与空闲停止。"""
import os
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5 import sip
from PyQt5.QtCore import QEvent, QObject
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QWidget
from advanced_analysis_panel import AdvancedAnalysisDock


def model(marker=None):
    return {'players': [], 'duration': 10, 'by_id': {}, 'marker': marker}


class AdvancedAnalysisLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.parents = []
        self.gates = []
        self.threads = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        for thread in self.threads:
            thread.join(3)
            self.assertFalse(thread.is_alive(), '受控后台任务必须结束')
        for parent in self.parents:
            if not sip.isdeleted(parent):
                sip.delete(parent)
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def dock(self):
        parent = QWidget()
        self.parents.append(parent)
        return parent, AdvancedAnalysisDock(parent)

    def gate(self):
        gate = threading.Event()
        self.gates.append(gate)
        return gate

    def wait(self, predicate):
        end = time.monotonic() + 3
        while not predicate() and time.monotonic() < end:
            QTest.qWait(5)
        self.assertTrue(predicate(), '后台交付应在有界时间内完成')

    def blocked_builder(self, gate, entered, marker):
        def build(**kwargs):
            self.threads.append(threading.current_thread())
            entered.set()
            if not gate.wait(3):
                raise AssertionError('测试未释放受控后台任务')
            return model(marker)
        return build

    def test_forced_parent_deletion_before_worker_completion_never_delivers(self):
        parent, dock = self.dock()
        gate, entered, delivered = self.gate(), threading.Event(), []
        dock.analysisReady.connect(lambda: delivered.append(threading.get_ident()))
        with patch('advanced_analysis_panel.build_advanced_analysis', self.blocked_builder(gate, entered, 'late')):
            dock.set_context(report={'marker': 'late'})
            self.assertTrue(entered.wait(1))
            # 测试刻意绕过cleanup，直接由Qt父子关系销毁；worker必须只持纯Python数据。
            sip.delete(parent)
            self.assertTrue(sip.isdeleted(dock))
            gate.set()
            self.threads[-1].join(3)
            QTest.qWait(40)
        self.assertFalse(delivered)
        self.assertFalse(self.threads[-1].is_alive())

    def test_cleanup_stops_delivery_and_ignores_late_result(self):
        _parent, dock = self.dock()
        gate, entered, delivered = self.gate(), threading.Event(), []
        dock.analysisReady.connect(lambda: delivered.append(True))
        with patch('advanced_analysis_panel.build_advanced_analysis', self.blocked_builder(gate, entered, 'late')):
            dock.set_context()
            self.assertTrue(entered.wait(1))
            self.assertTrue(dock._delivery_timer.isActive())
            dock.cleanup()
            self.assertFalse(dock._delivery_timer.isActive())
            gate.set()
            self.threads[-1].join(3)
            QTest.qWait(40)
        self.assertFalse(delivered)
        self.assertFalse(dock.is_ready())
        self.assertFalse(dock._workers)

    def test_repeated_parent_destruction_during_worker_completion(self):
        finished, delivered = [], []
        for batch in range(8):
            gate = self.gate()
            entered = []
            def build(**kwargs):
                self.threads.append(threading.current_thread())
                entered.append(True)
                if not gate.wait(3):
                    raise AssertionError('测试未释放批次')
                finished.append(True)
                return model(batch)
            with patch('advanced_analysis_panel.build_advanced_analysis', build):
                pairs = [self.dock() for _ in range(8)]
                for parent, dock in pairs:
                    dock.analysisReady.connect(lambda: delivered.append(True))
                    dock.set_context(report={'batch': batch})
                self.wait(lambda: len(entered) == 8)
                gate.set()
                # worker完成与新旧界面析构交错，Qt对象生命周期完全由GUI线程管理。
                for index, (parent, dock) in enumerate(pairs):
                    if index % 2:
                        dock.cleanup()
                    sip.delete(parent)
                    self.assertTrue(sip.isdeleted(dock))
                for thread in self.threads[-8:]:
                    thread.join(3)
                self.app.sendPostedEvents(None, QEvent.DeferredDelete)
                self.app.processEvents()
        self.assertEqual(len(finished), 64)
        self.assertFalse(delivered)

    def test_delivery_runs_on_gui_thread_and_cached_key_stays_idle(self):
        _parent, dock = self.dock()
        calls, delivered = [], []
        gui_thread = threading.get_ident()
        def build(**kwargs):
            self.threads.append(threading.current_thread())
            calls.append(kwargs['selected_player_id'])
            return model(kwargs['selected_player_id'])
        dock.analysisReady.connect(lambda: delivered.append(threading.get_ident()))
        with patch('advanced_analysis_panel.build_advanced_analysis', build):
            self.assertFalse(dock._delivery_timer.isActive())
            dock.set_context()
            self.wait(dock.is_ready)
            self.assertFalse(dock._delivery_timer.isActive())
            dock._rebuild_model()
            dock.set_time(2)
            self.assertEqual(calls, [None])
            self.assertFalse(dock._delivery_timer.isActive())
            dock.set_time(3, player_id=101)
            self.wait(dock.is_ready)
            self.assertEqual(calls, [None, 101])
            dock.set_time(4, player_id=None)
            self.assertTrue(dock.is_ready())
            self.assertEqual(calls, [None, 101], '回到同代次已缓存玩家不重复计算')
            self.assertFalse(dock._delivery_timer.isActive())
        self.assertTrue(delivered)
        self.assertTrue(all(identity == gui_thread for identity in delivered))

    def test_pending_key_deduplicates_and_old_generation_cannot_replace_current(self):
        _parent, dock = self.dock()
        gate, entered, calls = self.gate(), threading.Event(), []
        def build(**kwargs):
            self.threads.append(threading.current_thread())
            marker = kwargs['report']['marker']
            calls.append(marker)
            if marker == 'old':
                entered.set()
                if not gate.wait(3):
                    raise AssertionError('测试未释放旧代次')
            return model(marker)
        with patch('advanced_analysis_panel.build_advanced_analysis', build):
            dock.set_context(report={'marker': 'old'})
            self.assertTrue(entered.wait(1))
            old_thread = self.threads[-1]
            dock._rebuild_model()
            dock._rebuild_model()
            self.assertEqual(calls, ['old'])
            dock.set_context(report={'marker': 'current'})
            self.wait(dock.is_ready)
            self.assertEqual(dock._model['marker'], 'current')
            gate.set()
            old_thread.join(3)
            self.wait(lambda: not dock._delivery_timer.isActive())
        self.assertEqual(dock._model['marker'], 'current')
        self.assertEqual(calls, ['old', 'current'])
        self.assertFalse(dock._workers)

    def test_builder_error_is_delivered_once_and_timer_stops(self):
        _parent, dock = self.dock()
        with patch('advanced_analysis_panel.build_advanced_analysis', side_effect=ValueError('受控分析失败')):
            dock.set_context()
            self.wait(dock.is_ready)
        self.assertTrue(dock._model['by_id'])
        self.assertTrue(all(row['status'] == 'missing' for row in dock._model['by_id'].values()))
        self.assertFalse(dock._delivery_timer.isActive())
        self.assertFalse(dock._workers)

    def test_worker_closure_keeps_no_qobject_or_dock(self):
        _parent, dock = self.dock()
        with patch('advanced_analysis_panel.Thread') as worker:
            dock.set_context()
            target = worker.call_args.kwargs['target']
            captured = [cell.cell_contents for cell in target.__closure__]
            self.assertFalse(any(isinstance(value, QObject) for value in captured),
                             '后台闭包不能保留会被GUI父子析构的Qt对象')
            self.assertFalse(any(value is dock for value in captured))
            worker.return_value.start.assert_called_once()
        dock.cleanup()


if __name__ == '__main__':
    unittest.main(verbosity=2)
