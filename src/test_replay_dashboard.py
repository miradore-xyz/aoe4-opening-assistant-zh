"""首屏真实Qt回归：响应布局、网格目标、同场身份与全屏返回。"""
import os,tempfile,unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt5.QtCore import QEvent,QPointF,QPoint,Qt,QSize
from PyQt5.QtTest import QTest
from PyQt5.QtGui import QImage,QPainter
from PyQt5.QtWidgets import QApplication,QBoxLayout,QLabel,QPushButton,QWidget
from full_map_catalog import FullMapCatalog
from replay_analysis import analyze_replay
from replay_page import ReplayPage,_RoundedMapCard,REPLAY_CARD_GAP
from test_replay_page import fixture
from visual import STYLE

class ReplayDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application=QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False);cls.application.setStyleSheet(STYLE)
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.page=ReplayPage(self.directory.name)
        self.page.resize(1100,780);self.page.show();QTest.qWait(15)
    def tearDown(self):
        self.page.close();self.page.deleteLater()
        self.application.sendPostedEvents(None,QEvent.DeferredDelete);self.application.processEvents();self.directory.cleanup()
    def preview(self,game_id=101,name='King of the Hill'):
        self.page._preview_public_context(game_id,{'map':name,'mode':'rm_4v4','duration':1180});QTest.qWait(10)
    def test_empty_start_does_not_load_arbitrary_map(self):
        self.assertIsNone(self.page.map_view.official_map)
        with patch.object(FullMapCatalog,'load_entities') as load:
            empty=ReplayPage(self.directory.name)
            try:
                self.assertIsNone(empty.map_view.official_map);load.assert_not_called()
                self.assertTrue(empty.legacy_tools.isHidden());self.assertEqual(empty.layout().count(),1)
            finally:empty.close();empty.deleteLater()
        self.assertEqual(self.page.map_view.maximumHeight(),16777215)
    def test_exact_preview_and_unknown_name_empty(self):
        self.preview();view=self.page.map_view
        self.assertIsNone(view.official_map)
        self.assertIs(view.map_stack.currentWidget(),view.canvas)
        self.assertEqual(view.view_mode.currentData(),'records')
        self.assertEqual(view.view_mode.findData('reference'),-1)
        self.assertIn('真实地图边界尚未提供',view.canvas.accessibleDescription())
        self.assertEqual(self.page.summary_name.text(),'占山为王')
        self.preview(102,'King of the Hill unknown suffix')
        self.assertIsNone(self.page.map_view.official_map,'地图名不能触发已移除的地形加载')
        self.assertEqual(self.page.spatial_model['map_name'],'King of the Hill unknown suffix')
        self.assertEqual(self.page.spatial_model['events'],[]);self.assertTrue(self.page.analysis_content.isHidden())
    def test_embedded_grid_fills_available_card_and_keeps_aspect_ratio(self):
        self.preview();view=self.page.map_view;canvas=view.canvas
        self.assertFalse(any(label.text()=='推演分析' for label in self.page.map_card.findChildren(QLabel)))
        self.assertEqual(self.page.map_card.layout().contentsMargins().left(),0)
        self.assertEqual(self.page.map_card.layout().contentsMargins().top(),0)
        self.assertEqual(canvas.viewport().mapTo(self.page.map_card,QPoint(0,0)),QPoint(0,0))
        self.assertEqual(canvas.viewport().height(),view.map_stack.height())
        self.assertLess(canvas.viewport().height(),self.page.map_card.height(),'原播放条单独占位')
        # 网格适应完整记录范围，不沿用已移除参考地形的裁切铺满规则。
        cover=canvas.mapFromScene(canvas.extent).boundingRect()
        self.assertLessEqual(cover.width(),canvas.viewport().width())
        self.assertLessEqual(cover.height(),canvas.viewport().height())
        self.assertAlmostEqual(canvas.transform().m11(),canvas.transform().m22())
        self.assertEqual(canvas.backgroundBrush().color().name(),'#161619')
        self.assertTrue(all(widget.isHidden() for widget in (view.notice,view.scope,view.detail_heading,view.detail_coordinates)))
        canvas.fit_all();self.application.processEvents()
        image=QImage(canvas.viewport().size(),QImage.Format_ARGB32)
        painter=QPainter(image);canvas.viewport().render(painter);painter.end()
        self.assertIn('#0f0f11',{image.pixelColor(x,y).name() for x in range(1,9) for y in range(1,9)})
        extent=canvas.mapFromScene(canvas.extent).boundingRect()
        self.assertLessEqual(extent.width(),canvas.viewport().width())
        self.assertLessEqual(extent.height(),canvas.viewport().height())
        fit_zoom=view.capture_view_state()['camera']['zoom'];view.zoom_by(1.5)
        self.assertGreater(view.capture_view_state()['camera']['zoom'],fit_zoom)
        view.fit_all();self.assertAlmostEqual(view.capture_view_state()['camera']['zoom'],fit_zoom)
    def test_transparent_data_area_and_apm_own_card_keep_other_controls_outside(self):
        from test_apm_chart import summary
        page=self.page;area=page.analysis_card
        self.assertFalse(hasattr(area,'heading'))
        self.assertEqual(area.minimumHeight(),0)
        self.assertEqual(area.layout().contentsMargins().top(),0)
        self.assertEqual(area.layout().contentsMargins().left(),0)
        self.assertTrue(page.analysis_placeholder.isHidden())
        self.assertEqual(page.analysis_placeholder.text(),'')
        self.assertTrue(area.layout().itemAt(0).isEmpty(),'初始提示不应占据一行布局')
        self.assertLess(area.height(),240,'空态不再保留原外层卡片固定高度')
        parsed=fixture();parsed['source']['game_id']=101
        page._show_report(analyze_replay(parsed),detail=summary());self.application.processEvents()
        self.assertIs(page.analysis_layout.itemAt(0).widget(),page.apm_card)
        self.assertEqual(page.apm_card.objectName(),'ReplayAPMCard')
        self.assertIs(page.apm_card.parentWidget(),page.analysis_content)
        self.assertEqual(page.apm_card.layout().contentsMargins().top(),16)
        self.assertEqual(page.apm_card.layout().contentsMargins().left(),16)
        self.assertEqual(page.apm_card.title.x(),16)
        self.assertTrue(page.analysis_placeholder.isHidden())
        self.assertFalse(any(label.text()=='数据与分析' for label in page.findChildren(QLabel)))
        for widget in (page.detail_toggle,page.detail_view,page.raw_toggle,page.raw_report):
            self.assertIs(widget.parentWidget(),page.analysis_content)
            self.assertFalse(page.apm_card.isAncestorOf(widget))
        self.assertGreaterEqual(page.detail_toggle.y()-page.apm_card.geometry().bottom()-1,16)
        page.detail_toggle.click();page.raw_toggle.click();self.application.processEvents()
        self.assertTrue(page.detail_view.isVisible());self.assertTrue(page.raw_report.isVisible())
    def test_compact_summary_overlay_and_round_clipping(self):
        self.preview();page=self.page;page._set_notice();self.application.processEvents()
        summary=page.summary_card
        labels=(summary.heading,page.summary_name,page.summary_info)
        self.assertEqual(summary.height(),32+sum(label.height() for label in labels)+16)
        self.assertEqual(page.notice.parentWidget(),summary)
        self.assertTrue(page.notice.isHidden())
        page._set_notice('这是一条需要完整阅读的回放下载错误。'*8,True);self.application.processEvents()
        self.assertGreaterEqual(page.notice.height(),page.notice.heightForWidth(page.notice.width()))
        self.assertEqual(page.sidebar.layout().count(),2)
        button=page.expand_map_button
        self.assertTrue(button.isVisible());self.assertEqual(button.text(),'')
        self.assertEqual(button.size(),QSize(36,36));self.assertEqual(button.iconSize(),QSize(18,18))
        self.assertEqual(button.accessibleName(),'全屏（F11）')
        self.assertEqual(button.parentWidget(),page.map_card)
        self.assertEqual(button.geometry().right(),page.map_card.width()-13)
        self.assertEqual(button.y(),12)
        self.assertTrue(page.map_card.mask().isEmpty(),'硬遮罩不能提供抗锯齿圆角')
        image=page.map_card.grab().toImage()
        self.assertEqual(image.pixelColor(0,0).name(),'#0f0f11')
        self.assertTrue(all(corner.testAttribute(Qt.WA_TransparentForMouseEvents) for corner in page.map_card._corners))
        self.assertEqual([b for b in page.sidebar.findChildren(QPushButton) if b.isVisible()],[])
    def test_map_corners_have_symmetric_antialiased_curves_over_full_bleed_child(self):
        """实际绘制四角：有过渡像素、镜像曲线一致，中心子内容仍完整铺满。"""
        card=_RoundedMapCard();card.resize(120,90)
        inner=QWidget(card);inner.setGeometry(card.rect());inner.setStyleSheet('background:#d0d0d0;')
        card.show();QTest.qWait(10)
        try:
            image=card.grab().toImage();self.assertEqual(image.pixelColor(30,30).name(),'#d0d0d0')
            shades=set()
            for y in range(12):
                for x in range(12):
                    colors=[image.pixelColor(px,py) for px,py in ((x,y),(119-x,y),(x,89-y),(119-x,89-y))]
                    shades.add(colors[0].red())
                    self.assertLessEqual(max(c.red() for c in colors)-min(c.red() for c in colors),1)
            self.assertGreater(len(shades-{15,208}),5,'圆弧边缘必须存在实际抗锯齿过渡')
            self.assertEqual(image.pixelColor(0,0).name(),'#0f0f11')
            self.assertEqual(image.pixelColor(11,11).name(),'#d0d0d0')
        finally:
            QTest.qWait(20);card.close();card.deleteLater()
            self.application.sendPostedEvents(None,QEvent.DeferredDelete)
    def test_responsive_cards_and_lower_area_reachable_without_move_growth(self):
        # 初始静态提示已移除；用实际报告验证有内容的数据区可以滚动抵达。
        self.page._show_report(analyze_replay(fixture()))
        for width in (760,1100,1500):
            with self.subTest(width=width):
                self.page.resize(width,800);QTest.qWait(15)
                self.assertLessEqual(self.page.body.width(),self.page.scroll.viewport().width())
                self.assertEqual(REPLAY_CARD_GAP,16)
                self.assertEqual(self.page.stage_layout.spacing(),REPLAY_CARD_GAP)
                self.assertEqual(self.page.analysis_card.y()-self.page.stage.geometry().bottom()-1,REPLAY_CARD_GAP)
                self.assertEqual(self.page.options_card.y()-self.page.summary_card.geometry().bottom()-1,REPLAY_CARD_GAP)
                if width==760:
                    self.assertEqual(self.page.stage_layout.direction(),QBoxLayout.TopToBottom)
                    self.assertEqual(self.page.sidebar.y()-self.page.map_card.geometry().bottom()-1,REPLAY_CARD_GAP)
                else:
                    self.assertEqual(self.page.stage_layout.direction(),QBoxLayout.LeftToRight)
                    self.assertEqual(self.page.sidebar.x()-self.page.map_card.geometry().right()-1,REPLAY_CARD_GAP)
                    self.assertEqual(self.page.map_card.y(),self.page.sidebar.y())
                    self.assertEqual(self.page.map_card.height(),self.page.sidebar.height())
                    self.assertAlmostEqual(self.page.map_card.width()/self.page.sidebar.width(),2,delta=.02)
                bar=self.page.scroll.verticalScrollBar();bar.setValue(bar.maximum())
                QTest.qWait(15)  # 等待报告内容引起的布局请求完成，再把移动前几何当作基准。
                self.assertLess(self.page.analysis_card.y()-bar.value(),self.page.scroll.viewport().height())
                before=(self.page.size(),self.page.body.height(),self.page.map_card.height())
                for offset in range(4):self.page.move(25+offset*5,25+offset*5);self.application.processEvents()
                self.assertEqual((self.page.size(),self.page.body.height(),self.page.map_card.height()),before)
    def test_parse_preserves_stage_geometry_and_fits_actual_grid_model(self):
        self.preview();page=self.page;view=page.map_view;view.zoom_by(1.6)
        view.canvas.centerOn(QPointF(140,160));self.assertTrue(view.canvas.user_camera)
        geometry=page.map_card.geometry();stage=page.stage;parsed=fixture()
        parsed['metadata']['map']='King of the Hill';parsed['source']['game_id']=101
        # 原样例只有x/y，当前真实目标平面要求完整x/y/z，不能拿未知高度猜地面坐标。
        for index,event in enumerate(parsed['commands']):
            if event.get('target'):event['target']['z']=index*5
        page._show_report(analyze_replay(parsed));QTest.qWait(10)
        self.assertIs(page.stage,stage);self.assertEqual(page.map_card.geometry(),geometry)
        actual=view.capture_view_state()
        self.assertIs(view.model,page.spatial_model);self.assertGreater(len(view.events),0)
        self.assertIsNone(actual['official_map'])
        self.assertAlmostEqual(actual['camera']['zoom'],1.0)
        self.assertFalse(actual['camera']['user_camera'],'新报告按真实点范围取景，不保留空预览的任意坐标')
        fitted=view.canvas.mapFromScene(view.canvas.extent).boundingRect()
        self.assertLessEqual(fitted.width(),view.canvas.viewport().width())
        self.assertLessEqual(fitted.height(),view.canvas.viewport().height())
        self.assertTrue(page.raw_report.isHidden())
        self.assertFalse(page.analysis_content.isHidden())
    def test_new_context_failure_hides_previous_report(self):
        report=analyze_replay(fixture());self.page._show_report(report);self.page.raw_toggle.click()
        self.assertTrue(self.page.raw_report.isVisible());self.preview(102)
        self.page._job_error('本场公开回放暂不可用。');self.page._job_finished()
        self.assertIs(self.page.report,report,'后台可保留旧对象')
        self.assertTrue(self.page.analysis_content.isHidden(),'不得把旧记录混在新地图下')
        self.assertFalse(self.page.raw_report.isVisible());self.assertEqual(self.page._public_context['game_id'],102)
        self.assertEqual(self.page.map_view.events,[]);self.assertIn('102',self.page.summary_info.text())
        self.assertIn('暂不可用',self.page.analysis_placeholder.text())
    def test_initial_fit_fullscreen_return_preserves_automatic_camera(self):
        self.preview();page=self.page;expected=page.map_view.capture_view_state()
        self.assertFalse(expected['camera']['user_camera'])
        page.expand_map_button.click();QTest.qWait(15);dialog=page._map_dialog
        actual=dialog.map_view.capture_view_state();self.assertIsNone(actual['official_map'])
        canvas=dialog.map_view.canvas;extent=canvas.mapFromScene(canvas.extent).boundingRect()
        self.assertLessEqual(extent.width(),canvas.viewport().width())
        self.assertLessEqual(extent.height(),canvas.viewport().height())
        QTest.keyClick(dialog,Qt.Key_Escape);QTest.qWait(5)
        restored=page.map_view.capture_view_state()
        self.assertAlmostEqual(restored['camera']['zoom'],expected['camera']['zoom'])
        self.assertFalse(restored['camera']['user_camera'])
    def test_grid_fullscreen_restores_camera_and_compact_controls(self):
        self.preview();page=self.page;page.map_view.zoom_by(1.5);page.expand_map_button.click();QTest.qWait(15)
        dialog=page._map_dialog;self.assertTrue(dialog.isFullScreen())
        self.assertEqual(dialog.map_view.view_mode.currentData(),'records')
        self.assertIsNone(dialog.map_view.official_map)
        self.assertEqual(dialog.map_view.canvas.backgroundBrush().color().name(),'#0f0f11')
        self.assertTrue(dialog.map_view.notice.isHidden())
        dialog.map_view.zoom_by(1.2);changed=dialog.map_view.capture_view_state();QTest.keyClick(dialog,Qt.Key_Escape);QTest.qWait(5)
        actual=page.map_view.capture_view_state();self.assertIsNone(page._map_dialog)
        self.assertIsNone(actual['official_map'])
        self.assertAlmostEqual(actual['camera']['zoom'],changed['camera']['zoom'])
        self.assertEqual(actual['view_mode'],'records');self.assertTrue(page.map_view.view_mode.isHidden())
        self.assertTrue(page.map_view.slider.isVisible())
        self.assertTrue(page.map_view.notice.isHidden())
        self.assertEqual(page.map_view.canvas.backgroundBrush().color().name(),'#161619')
if __name__=='__main__':unittest.main()
