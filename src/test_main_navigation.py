"""主导航、单场分析跳转与精简设置的真实Qt交互；不访问网络。"""
import json
import copy
import unittest
from unittest.mock import patch

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QLabel, QPushButton, QLineEdit
from core import atomic_json
from development_placeholder import DevelopmentPlaceholder
from test_app import timed_example
import test_brand_name as fixtures
from test_match_view_alignment import snapshot


class MainNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.BrandNameTests.setUpClass()
        cls.application = fixtures.BrandNameTests.application

    @classmethod
    def tearDownClass(cls):
        fixtures.BrandNameTests.tearDownClass()

    def setUp(self):
        self.fixture = fixtures.BrandNameTests('test_product_name_and_license_credit_in_actual_widgets')
        self.fixture.setUp()
        self.manager = self.fixture.manager
        self.manager.resize(760, 520)
        self.manager.show()
        self.application.processEvents()

    def tearDown(self):
        self.fixture.tearDown()

    def test_selected_match_opens_main_analysis_and_preserves_player_query(self):
        manager = self.manager
        self.assertEqual([button.text() for button in manager.navigation],
                         ['开局库', '玩家查询', '对局分析', '设置', '个人', '关于'])
        manager.navigate(3)
        page = manager.analysis_page
        page._show_snapshot(snapshot())
        page.query.setText('保留搜索内容')
        QTest.qWait(20)
        table = page.game_table
        table.selectRow(1)
        page.scroll.verticalScrollBar().setValue(page.scroll.verticalScrollBar().maximum())
        scroll_position = page.scroll.verticalScrollBar().value()
        selected_game = table.item(0, 0).data(Qt.UserRole)
        with patch.object(manager.replay_page, 'import_public_match') as imported:
            table.cellWidget(0, 6).findChild(QPushButton, 'GetMatchReplay').click()
            self.assertEqual(manager.tabs.currentIndex(), 4)
            self.assertTrue(manager.navigation[2].isChecked())
            self.assertEqual(imported.call_args.args[:2], (7, selected_game))
            self.assertEqual(imported.call_args.args[2]['game_id'], selected_game)
        manager.navigation[1].click()
        self.application.processEvents()
        self.assertEqual(page.query.text(), '保留搜索内容')
        self.assertFalse(hasattr(page, 'mode'))
        self.assertEqual(page.game_table.rowCount(), 5)
        self.assertIs(page.game_table, table)
        self.assertEqual(table.currentRow(), 1)
        self.assertEqual(page.scroll.verticalScrollBar().value(), scroll_position)
        self.assertFalse(hasattr(page, 'section_buttons'))
        self.assertFalse(hasattr(page, 'replay_page'))

    def test_manual_replay_entry_uses_the_same_independent_analysis_page(self):
        with patch.object(self.manager.replay_page, 'import_path') as imported:
            self.manager.open_replay()
            imported.assert_not_called()
            self.manager.open_replay('明确指定.rec')
            imported.assert_called_once_with('明确指定.rec')
            self.assertEqual(self.manager.tabs.currentIndex(), 4)

    def test_personal_placeholder_preserves_existing_routes_and_only_shows_status(self):
        manager = self.manager
        self.assertEqual([item.property('pageIndex') for item in manager.navigation], [0, 3, 4, 1, 5, 2])
        manager.navigation[4].click()
        self.assertEqual(manager.tabs.currentIndex(), 5)
        self.assertIs(manager.tabs.currentWidget(), manager.personal_page)
        self.assertEqual(manager.page_title.text(), '个人')
        self.assertEqual(manager.personal_page.findChildren(QLabel), [])
        self.assertEqual(len(manager.personal_page.findChildren(DevelopmentPlaceholder)), 1)
        self.assertEqual(manager.personal_page.findChildren(QPushButton), [])
        self.assertEqual(manager.personal_page.findChildren(QLineEdit), [])
        self.assertTrue(manager.navigation[4].isChecked())
        manager.navigation[-1].click()
        self.assertEqual(manager.tabs.currentIndex(), 2)
        self.assertEqual(manager.page_title.text(), '关于')
        links = [item for item in manager.help_scroll.findChildren(QLabel) if '<a ' in item.text()]
        self.assertEqual(len(links), 3)
        self.assertTrue(all(item.openExternalLinks() for item in links))

    def test_online_opening_button_stays_internal_and_returns_to_preserved_list(self):
        manager = self.manager
        for number in range(12):
            data = copy.deepcopy(timed_example())
            data['name'] = f'返回验证{number:02d}'
            atomic_json(manager.orders_dir / f'{number:02d}.json', data)
        manager.refresh_library()
        manager.search.setText('返回验证')
        manager.library.setCurrentRow(7)
        manager.layout_library()
        self.application.processEvents()
        bar = manager.library.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(min(140, bar.maximum()))
        before = (manager.library.currentRow(), bar.value(), manager.search.text())
        with patch('webbrowser.open') as browser_open, patch('PyQt5.QtGui.QDesktopServices.openUrl') as desktop_open:
            manager.online_orders_button.click()
            self.application.processEvents()
            browser_open.assert_not_called()
            desktop_open.assert_not_called()
        self.assertEqual(manager.tabs.currentIndex(), 6)
        self.assertEqual(manager.page_title.text(), '在线开局库')
        self.assertEqual(manager.online_orders_page.findChildren(QLabel), [])
        self.assertEqual(len(manager.online_orders_page.findChildren(DevelopmentPlaceholder)), 1)
        self.assertEqual(manager.online_orders_page.findChildren(QPushButton), [])
        self.assertTrue(manager.navigation[0].isChecked())
        self.assertEqual(sum(item.isChecked() for item in manager.navigation), 1)
        manager.navigation[0].click()
        self.application.processEvents()
        self.assertEqual(manager.tabs.currentIndex(), 0)
        self.assertEqual(manager.work_views.currentIndex(), 0)
        self.assertEqual((manager.library.currentRow(), bar.value(), manager.search.text()), before)

    def test_settings_have_simple_styles_three_speeds_no_dimensions_or_preset_hotkeys(self):
        manager = self.manager
        manager.navigate(1)
        self.assertEqual([manager.overlay_style_input.itemText(i) for i in range(3)], ['字幕', '侧栏', '浮层'])
        self.assertEqual([manager.overlay_style_input.itemData(i) for i in range(3)], ['a', 'c', 'b'])
        self.assertFalse(hasattr(manager, 'width_input'))
        self.assertFalse(hasattr(manager, 'height_input'))
        labels = [label.text() for label in manager.tabs.widget(1).findChildren(QLabel)]
        self.assertNotIn('窗口宽度', labels)
        self.assertNotIn('窗口高度', labels)
        self.assertNotIn('点击输入框后按组合键。', labels)
        self.assertEqual(manager.rate_input.currentText(), '默认')
        self.assertEqual([manager.rate_input.itemData(i) for i in range(3)], [0, 2, -2])
        before_volume = manager.settings['volume']
        manager.rate_input.setCurrentIndex(manager.rate_input.findData(2))
        self.assertEqual(manager.settings['rate'], 2)
        self.assertEqual(manager.settings['volume'], before_volume)
        manager.settings.update(voice_engine='online', speech=True)
        manager._online_job = 55
        manager.on_online_prepared(55)
        self.assertTrue(manager._online_ready)
        self.assertFalse(manager.voice_status.isVisible())
        self.assertEqual(manager.voice_status.text(), '')
        manager.speech_problem('明确的语音错误')
        self.assertTrue(manager.voice_status.isVisible())
        self.assertIn('明确的语音错误', manager.voice_status.text())
