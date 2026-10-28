"""页面补源生命周期：受控线程、独立目录、纯队列交付，不联网。

delivery=主线程交付；token=任务代次；blocked worker=等待测试放行的后台任务。
"""
import copy
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5 import sip
from PyQt5.QtCore import QEvent, QObject, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QWidget
from replay_page import ReplayPage


class ReplayAnalysisDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.quit_policy = cls.app.quitOnLastWindowClosed()
        cls.app.setQuitOnLastWindowClosed(False)
        QTimer.singleShot(0, cls.app.quit)
        cls.app.exec_()

    @classmethod
    def tearDownClass(cls):
        cls.app.setQuitOnLastWindowClosed(cls.quit_policy)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='replay-delivery-', dir=os.environ.get('AOE4_TEST_WORK'))
        self.pages, self.workers, self.releases = [], [], []
        real_thread = threading.Thread
        def create_worker(*args, **kwargs):
            worker = real_thread(*args, **kwargs)
            self.workers.append(worker)
            return worker
        self.thread_patch = patch('replay_page.threading.Thread', side_effect=create_worker)
        self.thread_patch.start()
        self.page = self.create_page()

    def tearDown(self):
        for release in self.releases:
            release.set()
        for worker in self.workers:
            worker.join(2)
            self.assertFalse(worker.is_alive(), '受控后台任务必须在本项结束前退出')
        for page in self.pages:
            if not sip.isdeleted(page):
                page.close()
                page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.thread_patch.stop()
        self.directory.cleanup()

    def create_page(self, parent=None):
        page = ReplayPage(Path(self.directory.name), parent)
        page.report = {'source': {'game_id': 123, 'sha256': 'a' * 64}, 'players': [], 'metadata': {}}
        page.match_detail = None
        self.pages.append(page)
        return page

    def wait_until(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(5)
        self.assertTrue(predicate(), '受控补源交付未在期限内完成')

    def gate(self):
        started, release = threading.Event(), threading.Event()
        self.releases.append(release)
        return started, release

    def test_idle_has_no_delivery_timer(self):
        self.assertEqual(self.page._analysis_jobs, {})
        self.assertFalse(self.page._analysis_delivery_timer.isActive())

    def test_success_delivered_on_gui_preserves_map_and_clock(self):
        gui_thread = threading.get_ident()
        worker_threads, delivered_threads, options = [], [], []
        report_before = copy.deepcopy(self.page.report)
        result = {'game_id': 123, 'detail': {'players': []}}
        original_ready = self.page._analysis_sources_ready
        def collect(report, data_dir, *, allow_network):
            worker_threads.append(threading.get_ident())
            options.append((report is self.page.report, data_dir, allow_network))
            return result
        def ready(*args):
            delivered_threads.append(threading.get_ident())
            original_ready(*args)
        before = self.page.map_view.capture_view_state()
        model = self.page.map_view.model
        with patch('replay_page.collect_analysis_sources', side_effect=collect), \
                patch.object(self.page, '_analysis_sources_ready', side_effect=ready):
            self.page._start_analysis_sources(allow_network=False)
            self.assertTrue(self.page._analysis_delivery_timer.isActive())
            self.wait_until(lambda: not self.page._analysis_jobs)
        self.assertIs(self.page.analysis_sources, result)
        self.assertEqual(delivered_threads, [gui_thread])
        self.assertNotEqual(worker_threads, [gui_thread])
        self.assertEqual(options, [(True, Path(self.directory.name), False)])
        self.assertEqual(self.page.report, report_before)
        self.assertIs(self.page.map_view.model, model)
        self.assertEqual(self.page.map_view.capture_view_state(), before)
        self.assertIs(self.page.map_view._analysis_context['sources'], result)
        self.assertFalse(self.page._analysis_delivery_timer.isActive())

    def test_error_result_and_loading_complete_on_gui(self):
        loading = []
        with patch('replay_page.collect_analysis_sources', side_effect=ValueError('受控失败')), \
                patch.object(self.page, '_analysis_set_loading', side_effect=loading.append):
            self.page._start_analysis_sources()
            self.wait_until(lambda: not self.page._analysis_jobs)
        self.assertEqual(self.page.analysis_sources, {'errors': ['受控失败']})
        self.assertEqual(loading, [True, False])
        self.assertFalse(self.page._analysis_delivery_timer.isActive())

    def test_same_generation_does_not_start_duplicate_worker(self):
        started, release = self.gate()
        def collect(*args, **kwargs):
            started.set()
            self.assertTrue(release.wait(3))
            return {'detail': {}}
        with patch('replay_page.collect_analysis_sources', side_effect=collect) as collector:
            self.page._start_analysis_sources()
            self.assertTrue(started.wait(1))
            self.page._start_analysis_sources()
            self.assertEqual(collector.call_count, 1)
            self.assertEqual(len(self.page._analysis_jobs), 1)
            release.set()
            self.wait_until(lambda: not self.page._analysis_jobs)

    def test_old_generation_finishes_without_stopping_current_loading(self):
        old = self.page.report
        new = dict(old, source={'game_id': 456})
        old_token = self.page._analysis_token
        old_started, old_release = self.gate()
        new_started, new_release = self.gate()
        loading = []
        def collect(report, *args, **kwargs):
            started, release = (old_started, old_release) if report is old else (new_started, new_release)
            started.set()
            self.assertTrue(release.wait(3))
            return {'game_id': report['source']['game_id']}
        with patch('replay_page.collect_analysis_sources', side_effect=collect), \
                patch.object(self.page, '_analysis_set_loading', side_effect=loading.append):
            self.page._start_analysis_sources()
            self.assertTrue(old_started.wait(1))
            self.page._analysis_token += 1
            new_token = self.page._analysis_token
            self.page.report = new
            self.page._start_analysis_sources()
            self.assertTrue(new_started.wait(1))
            self.assertEqual(set(self.page._analysis_jobs), {old_token, new_token})
            old_release.set()
            self.wait_until(lambda: old_token not in self.page._analysis_jobs)
            self.assertIsNone(self.page.analysis_sources)
            self.assertEqual(loading, [True, True])
            self.assertTrue(self.page._analysis_delivery_timer.isActive())
            new_release.set()
            self.wait_until(lambda: not self.page._analysis_jobs)
        self.assertEqual(self.page.analysis_sources, {'game_id': 456})
        self.assertEqual(loading, [True, True, False])
        self.assertFalse(self.page._analysis_delivery_timer.isActive())

    def test_changed_report_identity_rejects_same_token_result(self):
        started, release = self.gate()
        def collect(*args, **kwargs):
            started.set()
            self.assertTrue(release.wait(3))
            return {'detail': {}}
        with patch('replay_page.collect_analysis_sources', side_effect=collect):
            self.page._start_analysis_sources()
            self.assertTrue(started.wait(1))
            self.page.report = copy.deepcopy(self.page.report)
            release.set()
            self.wait_until(lambda: not self.page._analysis_jobs)
        self.assertIsNone(self.page.analysis_sources)

    def test_close_discards_queue_and_late_result(self):
        started, release = self.gate()
        def collect(*args, **kwargs):
            started.set()
            self.assertTrue(release.wait(3))
            return {'detail': {}}
        with patch('replay_page.collect_analysis_sources', side_effect=collect):
            self.page._start_analysis_sources()
            self.assertTrue(started.wait(1))
            old_queue = self.page._analysis_results
            self.page.close()
            self.assertEqual(self.page._analysis_jobs, {})
            self.assertFalse(self.page._analysis_delivery_timer.isActive())
            self.assertIsNot(self.page._analysis_results, old_queue)
            release.set()
            self.workers[-1].join(2)
            self.app.processEvents()
        self.assertIsNone(self.page.analysis_sources)
        self.assertTrue(self.page._analysis_results.empty())

    def test_parent_destroyed_while_blocked_worker_has_no_qt_reference(self):
        # 受控重现真实风险窗口；强删父对象不走closeEvent，后台仍须只写纯队列。
        for index in range(16):
            parent = QWidget()
            page = self.create_page(parent)
            started, release = self.gate()
            def collect(*args, **kwargs):
                started.set()
                self.assertTrue(release.wait(3))
                return {'iteration': index}
            with patch('replay_page.collect_analysis_sources', side_effect=collect):
                page._start_analysis_sources()
                self.assertTrue(started.wait(1))
                worker = self.workers[-1]
                captured = [cell.cell_contents for cell in worker._target.__closure__]
                self.assertFalse(any(isinstance(value, QObject) for value in captured))
                sip.delete(parent)
                self.assertTrue(sip.isdeleted(page))
                release.set()
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
