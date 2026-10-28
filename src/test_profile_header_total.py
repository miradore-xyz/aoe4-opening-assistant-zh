"""资料旁全部场次与恢复双栏概览的Qt（界面框架）回归；仅隔离合成资料。"""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import QEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel

from analysis_page import AnalysisPage, _ProfileHeader, _history_total_text
from app_fonts import make_ui_font
from match_analysis import PublicMatchClient
from test_query_visual_hierarchy import four_player_snapshot
from visual import STYLE


class ProfileHeaderTotalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyleSheet(STYLE)
        cls.app.setFont(make_ui_font(14))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.directory.cleanup()

    def test_header_total_never_uses_recent_samples_or_mode_sums(self):
        for totals, expected in (({'count': 0, 'scope': 'public_history'}, '全部场次 0 场'),
                ({'count': 123456, 'scope': 'public_history'}, '全部场次 123,456 场'),
                ({'count': True, 'scope': 'public_history'}, '全部场次 暂无数据'),
                ({'count': -1, 'scope': 'public_history'}, '全部场次 暂无数据'),
                ({'count': 65, 'scope': 'season'}, '全部场次 暂无数据'),
                ({}, '全部场次 暂无数据')):
            with self.subTest(totals=totals):
                snapshot = {'history_totals': totals, 'games': [None] * 50,
                            'profile': {'modes': {'rm_team': {'games_count': 999}}}}
                before = copy.deepcopy(snapshot)
                self.assertEqual(_history_total_text(snapshot), expected)
                self.assertEqual(snapshot, before)

    def test_header_reflows_total_below_identity_without_nickname_overlap(self):
        snapshot = {'profile_id': 7, 'history_totals': {'count': 65, 'scope': 'public_history'}}
        header = _ProfileHeader({'profile_id': 7, 'name': '很长的玩家昵称' * 20}, snapshot, self.directory.name)
        self.widgets.append(header)
        header.show()
        for width in (900, 320, 760):
            header.resize(width, header.sizeHint().height())
            QTest.qWait(10)
            self.assertEqual(header.total.text(), '全部场次 65 场')
            self.assertFalse(header.total.toolTip())
            self.assertGreaterEqual(header.name.width(), 180)
            if width < 560:
                self.assertTrue(header._stacked)
                self.assertEqual(header.total.y() - header.identity.y() - header.identity.height(), 16)
            else:
                self.assertFalse(header._stacked)
                self.assertEqual(header.total.x() - header.identity.x() - header.identity.width(), 16)
            self.assertLessEqual(header.total.geometry().right(), header.width())

    def test_actual_query_page_shows_total_in_header_and_no_statistic_cards(self):
        page = AnalysisPage(self.directory.name)
        self.widgets.append(page)
        sample = four_player_snapshot()
        sample['history_totals'] = {'count': 65, 'scope': 'public_history'}
        with patch.object(PublicMatchClient, '_get', side_effect=AssertionError('本测试禁止网络')) as network:
            page.resize(1300, 900)
            page._show_snapshot(sample)
            page.show()
            QTest.qWait(20)
            self.assertEqual(page.profile_history_total.text(), '全部场次 65 场')
            self.assertIs(page.profile_history_total.parentWidget(), page.profile_header)
            self.assertEqual(page.rank_overview_row.grid.count(), 2)
            for removed in ('stats_column', 'total_card', 'kd_card'):
                self.assertFalse(hasattr(page.rank_overview_row, removed))
            self.assertFalse(any('KD（' in label.text() for label in page.findChildren(QLabel)))
            self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
            network.assert_not_called()


if __name__ == '__main__':
    unittest.main()
