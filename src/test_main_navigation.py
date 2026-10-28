"""主导航、单场分析跳转与精简设置的真实Qt交互；不访问网络。"""
import json
import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt5.QtCore import Qt, QPoint, QRect
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QLabel, QPushButton
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
        # 页面切换与鼠标释放的延后布局需在窗口仍存活时处理，再销毁夹具。
        QTest.qWait(20)
        self.fixture.tearDown()

    def test_rehearsal_playback_is_neutral_in_every_real_control_state(self):
        """逐状态比较三按钮的真实背景与边框，播放暂停行为和原尺寸保留。"""
        manager = self.manager
        opening = manager.orders_dir / '中性播放回归.json'
        atomic_json(opening, timed_example())
        manager.refresh_library(opening)
        manager.open_session()
        control = manager.pause_button
        QTest.qWait(20)
        self.assertEqual((control.width(), control.height()), (64, 40))

        def colors_of(button):
            # 透明按钮单独grab（截图）含透明像素，须从实际整窗合成结果取样背景。
            image = manager.grab().copy(QRect(button.mapTo(manager, QPoint()), button.size())).toImage()
            for y in range(image.height()):
                for x in range(image.width()):
                    color = image.pixelColor(x, y)
                    # 栅格抗锯齿可产生少量冷色偏差；这里只排除旧金色，主断言仍是三者状态色相等。
                    self.assertFalse(color.red() > color.green() + 10 and color.green() > color.blue() + 10,
                                     (x, y, color.name()))
            return image.pixelColor(4, 20).name(), image.pixelColor(0, 20).name()

        buttons = [next(button for button in manager.preview_card.findChildren(QPushButton)
                        if button.accessibleName() == caption) for caption in ('上一步', '开始计时', '下一步')]
        self.assertEqual(len({button.styleSheet() for button in buttons}), 1)
        for button in buttons:
            icon_image = button.icon().pixmap(48, 48).toImage()
            solid = {icon_image.pixelColor(x, y).name() for y in range(icon_image.height())
                     for x in range(icon_image.width()) if icon_image.pixelColor(x, y).alpha() == 255}
            self.assertEqual(solid, {'#c6c7cb'})
        for state in ('默认', '悬停', '按下', '键盘焦点', '禁用'):
            samples = []
            for button in buttons:
                QTest.mouseMove(manager.preview, QPoint(2, 2))
                for item in buttons:
                    item.clearFocus()
                if state in ('悬停', '按下'):
                    QTest.mouseMove(button, button.rect().center())
                if state == '按下':
                    QTest.mousePress(button, Qt.LeftButton, pos=button.rect().center())
                    self.assertTrue(button.isDown())
                elif state == '键盘焦点':
                    button.setFocus(Qt.TabFocusReason)
                    self.assertTrue(button.hasFocus())
                elif state == '禁用':
                    button.setEnabled(False)
                QTest.qWait(5)
                samples.append(colors_of(button))
                if state == '按下':
                    QTest.mouseRelease(button, Qt.LeftButton, pos=button.rect().center())
                    manager.clock.pause()
                    manager.update_display()
                button.setEnabled(True)
            self.assertEqual(len(set(samples)), 1, (state, samples))

        QTest.mouseClick(control, Qt.LeftButton, pos=control.rect().center())
        self.assertTrue(manager.clock.running)
        self.assertEqual(control.accessibleName(), '暂停计时')
        colors_of(control)  # update_display（状态刷新）切换暂停图标后仍为中性色。
        QTest.mouseClick(control, Qt.LeftButton, pos=control.rect().center())
        self.assertFalse(manager.clock.running)
        self.assertEqual(control.accessibleName(), '开始计时')

    def test_about_font_links_match_bundled_official_sources_and_remain_clickable(self):
        """绿色来源链接指向实际打包字体的官方项目，中文、英文、数字共用一种字体。"""
        self.manager.navigate(2)
        label = self.manager.help_scroll.findChild(QLabel, 'FontSourceLinks')
        self.assertIsNotNone(label)
        self.assertTrue(label.openExternalLinks())
        self.assertTrue(label.textInteractionFlags() & Qt.LinksAccessibleByMouse)
        self.assertTrue(label.textInteractionFlags() & Qt.LinksAccessibleByKeyboard)
        project = 'atelier-anchor/smiley-sans'
        metadata = json.loads((Path(__file__).parent / '界面素材/字体/得意黑/来源.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['官方仓库'], f'https://github.com/{project}')
        self.assertIn(f'href="https://github.com/{project}"', label.text())
        self.assertIn('得意黑', label.text())
        self.assertNotIn('Sen', label.text())
        self.assertNotIn('思源', label.text())
        self.assertEqual(label.text().count('color:#78c99c;'), 1)

    def test_about_external_links_keep_targets_and_use_green_lucide_images(self):
        import base64
        import re
        from PyQt5.QtGui import QImage
        from match_analysis import API_SOURCE
        self.manager.navigate(2)
        content = self.manager.help_scroll
        labels = (content.findChildren(QLabel, 'AboutExternalLink') +
                  [content.findChild(QLabel, 'FontSourceLinks'), content.findChild(QLabel, 'DataSourceLink')])
        expected = {'https://github.com/CraftySalamander/RTS_Overlay',
                    'https://github.com/miradore-xyz/aoe4-opening-assistant-zh',
                    'https://gitee.com/miradore/aoe4-opening-assistant-zh',
                    'https://github.com/atelier-anchor/smiley-sans', API_SOURCE}
        actual, images = set(), []
        for label in labels:
            self.assertTrue(label.openExternalLinks())
            self.assertTrue(label.textInteractionFlags() & Qt.LinksAccessibleByKeyboard)
            self.assertNotIn('↗', label.text())
            actual.update(re.findall(r'href="([^"]+)"', label.text()))
            images.extend(re.findall(r'data:image/png;base64,([^"]+)', label.text()))
        self.assertEqual(actual, expected)
        self.assertEqual(len(images), 5)
        for encoded in images:
            icon = QImage.fromData(base64.b64decode(encoded), 'PNG')
            self.assertFalse(icon.isNull())
            colors = [icon.pixelColor(x, y) for y in range(icon.height()) for x in range(icon.width())
                      if icon.pixelColor(x, y).alpha() > 200]
            self.assertGreater(len(colors), 10)
            self.assertTrue(all(abs(color.red() - 120) <= 2 and abs(color.green() - 201) <= 2
                                and abs(color.blue() - 156) <= 2 for color in colors))

    def test_query_footer_is_removed_while_unconfirmed_results_stay_excluded(self):
        manager = self.manager
        data = snapshot()
        unknown = copy.deepcopy(data['games'][0])
        unknown['game_id'] = 999
        unknown['teams'][1][1]['player']['result'] = None
        data['games'].append(unknown)
        manager.analysis_page._show_snapshot(data)
        page = manager.analysis_page
        self.assertEqual(page.game_table.rowCount(), 5)
        self.assertGreater(sum(page.report['skipped'].values()), 0)
        captions = [label.text() for label in page.body.findChildren(QLabel)]
        self.assertFalse(any('统计已排除' in value or '数据来源与接口说明' in value for value in captions))

    def test_selected_match_opens_main_analysis_and_preserves_player_query(self):
        manager = self.manager
        self.assertEqual([button.text() for button in manager.navigation],
                         ['开局库', '玩家查询', '对局分析', '设置', '关于'])
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

    def test_apm_name_signal_queries_exact_profile_id_on_player_page(self):
        manager, number = self.manager, 4294967301
        manager.navigate(4)
        page = manager.analysis_page
        with patch.object(page, '_job') as job, patch.object(page.client, 'fetch_profile') as fetch:
            manager.replay_page.player_query_requested.emit(number, '同名玩家')
            self.assertEqual(manager.tabs.currentIndex(), 3)
            self.assertEqual(page.query.text(), str(number))
            self.assertEqual(job.call_args.args[1], 'profile')
            job.call_args.args[0]()
            fetch.assert_called_once_with(number)

    def test_personal_entry_removed_preserves_existing_routes(self):
        """移除个人入口，保留其余路由编号和关于链接，不复活历史占位页。"""
        manager = self.manager
        self.assertEqual([item.property('pageIndex') for item in manager.navigation], [0, 3, 4, 1, 2])
        self.assertNotIn('个人', [item.text() for item in manager.navigation])
        self.assertFalse(hasattr(manager, 'personal_page'))
        self.assertFalse(manager.tabs.isTabEnabled(5))
        self.assertEqual(manager.tabs.tabText(5), '')
        for item in manager.navigation:
            item.click()
            self.assertEqual(manager.tabs.currentIndex(), item.property('pageIndex'))
            self.assertEqual(manager.page_title.text(), item.text())
            self.assertTrue(item.isChecked())
        # 历史内部编号5不能通过编程入口重新显示个人页或改变当前页面。
        manager.navigate(5)
        self.assertEqual(manager.tabs.currentIndex(), 2)
        self.assertEqual(manager.page_title.text(), '关于')
        links = [item for item in manager.help_scroll.findChildren(QLabel) if '<a ' in item.text()]
        self.assertEqual(len(links), 5)  # 三个项目链接、得意黑链接及移入关于的数据来源链接。
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
        self.assertEqual([label.text() for label in manager.online_orders_page.findChildren(QLabel)], ['开发中'])
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
