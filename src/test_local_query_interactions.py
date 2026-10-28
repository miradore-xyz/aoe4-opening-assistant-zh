"""本地待发布查询交互：真实Qt状态像素及名字/对局双击目标，只用隔离合成资料。"""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt5.QtCore import QEvent,QPoint,QRectF,Qt
from PyQt5.QtGui import QHelpEvent
from PyQt5.QtTest import QSignalSpy,QTest
from PyQt5.QtWidgets import QApplication,QPushButton
from analysis_page import AnalysisPage
from match_widgets import MATCH_METADATA_ROLE, MATCH_FLAG_OFFSET
from test_query_visual_hierarchy import four_player_snapshot
from visual import STYLE

class LocalQueryInteractionsTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.application=QApplication.instance() or QApplication([])
  cls.application.setQuitOnLastWindowClosed(False);cls.application.setStyleSheet(STYLE)
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.page=AnalysisPage(self.temp.name)
  self.page.resize(760,900);self.page.move(0,0);self.page.show();self.page.activateWindow()
  self.sample=four_player_snapshot();self.page._show_snapshot(self.sample)
  for n in range(4):self.page._add_history(f'玩家{n}',700+n,f'玩家{n}')
  self.page.query.setText('保留查询');QTest.qWait(15)
 def tearDown(self):
  self.page.history_combo.hidePopup();self.page.close();self.page.deleteLater()
  self.application.sendPostedEvents(None,QEvent.DeferredDelete);self.application.processEvents();self.temp.cleanup()
 def layouts(self,width=760):
  self.page.resize(width,900);QTest.qWait(15);table=self.page.game_table
  metadata=table.item(0,0).data(MATCH_METADATA_ROLE)
  content=table.itemDelegate().content_rect(table.visualItemRect(table.item(0,2)),2)
  return table,metadata,table.itemDelegate().player_name_layout(content,metadata)
 def double_click(self,table,point):
  point=point.toPoint() if hasattr(point,'toPoint') else point
  QTest.mouseDClick(table.viewport(),Qt.LeftButton,pos=point);QTest.mouseRelease(table.viewport(),Qt.LeftButton,pos=point)
  self.application.processEvents()
 def test_toolbar_default_hover_pressed_and_disabled_share_real_painted_states(self):
  controls=(self.page.query_button,self.page.refresh_button,self.page.history_combo,self.page.delete_history)
  with patch.object(self.page,'_job'):
   for control in controls:
    with self.subTest(control=control.accessibleName()):
     control.setEnabled(True);QTest.mouseMove(self.page.query,QPoint(5,5));QTest.qWait(5)
     image=control.grab().toImage();self.assertEqual(image.pixelColor(2,control.height()//2).name(),'#202023')
     normal=image.copy(6,6,16,16)
     QTest.mouseMove(control,control.rect().center());QTest.qWait(5)
     image=control.grab().toImage();self.assertEqual(image.pixelColor(2,control.height()//2).name(),'#f4f4f5')
     self.assertTrue(any(max(image.pixelColor(x,y).getRgb()[:3])<80 for x in range(6,22) for y in range(6,22)))
     QTest.mousePress(control,Qt.LeftButton,pos=control.rect().center());QTest.qWait(5)
     image=control.grab().toImage();self.assertEqual(image.pixelColor(2,control.height()//2).name(),'#d4d4d8')
     QTest.mouseRelease(control,Qt.LeftButton,pos=control.rect().center());self.page.history_combo.hidePopup()
     QTest.mouseMove(self.page.query,QPoint(5,5));QTest.qWait(5)
     self.assertEqual(control.grab().toImage().pixelColor(2,control.height()//2).name(),'#202023')
     control.setEnabled(False);QTest.qWait(5);disabled=control.grab().toImage()
     self.assertEqual(disabled.pixelColor(2,control.height()//2).name(),'#202023')
     self.assertNotEqual(normal,disabled.copy(6,6,16,16))
     control.setEnabled(True)
 def test_single_click_selects_only_and_double_click_row_opens_exact_match_once(self):
  table,metadata,_=self.layouts();requested=QSignalSpy(self.page.public_match_requested)
  players=QSignalSpy(table.playerActivated);matches=QSignalSpy(table.matchActivated)
  point=table.visualItemRect(table.item(0,1)).center()
  QTest.mouseClick(table.viewport(),Qt.LeftButton,pos=point)
  self.assertEqual(table.currentRow(),0);self.assertEqual(len(requested),0)
  self.double_click(table,point)
  self.assertEqual(len(matches),1);self.assertEqual(len(players),0);self.assertEqual(len(requested),1)
  self.assertEqual(requested[0][:2],[7,metadata['game_id']]);self.assertEqual(requested[0][2]['focus_profile_id'],7)
 def test_both_same_names_query_explicit_ids_across_narrow_and_wide_windows(self):
  for width in (760,1100,1500):
   for number in (7,8):
    with self.subTest(width=width,profile=number):
     table,_,layouts=self.layouts(width);entry=next(row for row in layouts if row['player']['profile_id']==number)
     requested=QSignalSpy(self.page.public_match_requested)
     with patch.object(self.page,'_job') as job,patch.object(self.page.client,'fetch_profile') as fetch,patch.object(self.page,'lookup') as lookup:
      self.double_click(table,entry['hit_rect'].center());job.assert_called_once();self.assertEqual(job.call_args.args[1],'profile')
      job.call_args.args[0]();fetch.assert_called_once_with(number);lookup.assert_not_called()
     self.assertEqual(self.page.query.text(),str(number));self.assertEqual(len(requested),0)
 def test_elided_long_name_keeps_correct_id_and_no_match_fallback(self):
  # 宽窗也保持有限名字槽；省略后的实际文字仍只查精确编号，完整昵称保留在悬停提示。
  for width in (760,1100,1500):
   with self.subTest(width=width):
    table,_,layouts=self.layouts(width);entry=next(row for row in layouts if row['player']['profile_id']==22)
    self.assertNotEqual(entry['visible_text'],entry['text']);self.assertIn('…',entry['visible_text'])
    self.assertLessEqual(entry['name_rect'].width(),135);self.assertEqual(table.item(0,2).toolTip(),'')
    point=entry['hit_rect'].center().toPoint()
    with patch('match_widgets.QToolTip.showText') as shown:
     self.application.sendEvent(table.viewport(),QHelpEvent(QEvent.ToolTip,point,table.viewport().mapToGlobal(point)))
     shown.assert_called_once();self.assertEqual(shown.call_args.args[1],entry['player']['name'])
     self.assertIs(shown.call_args.args[2],table.viewport());self.assertEqual(shown.call_args.args[3],entry['hit_rect'].toAlignedRect())
    requested=QSignalSpy(self.page.public_match_requested)
    with patch.object(self.page,'_job') as job,patch.object(self.page.client,'fetch_profile') as fetch:
     self.double_click(table,entry['hit_rect'].center());job.assert_called_once();job.call_args.args[0]();fetch.assert_called_once_with(22)
    self.assertEqual(len(requested),0)
 def test_tooltip_is_empty_over_full_names_flags_scores_and_blank_space(self):
  # 鼠标真正在省略文字上才显示一个全名；整格、普通名字、分数与旗帜均不弹长说明。
  for width in (760,1100,1500):
   table,_,layouts=self.layouts(width);entry=next(row for row in layouts if row['player']['profile_id']==6)
   self.assertEqual(entry['visible_text'],entry['text'])
   points=[entry['hit_rect'].center(),entry['line'].center(),entry['line'].center(),entry['line'].center()]
   points[1].setX(entry['area'].left()+10);points[2].setX(entry['area'].left()+MATCH_FLAG_OFFSET+10)
   points[3].setX(entry['name_rect'].right()-2)
   for position in points:
    point=position.toPoint()
    with self.subTest(width=width,point=point),patch('match_widgets.QToolTip.showText') as shown,patch('match_widgets.QToolTip.hideText') as hidden:
     self.application.sendEvent(table.viewport(),QHelpEvent(QEvent.ToolTip,point,table.viewport().mapToGlobal(point)))
     shown.assert_not_called();hidden.assert_called_once()
 def test_score_flag_and_trailing_name_space_open_match_not_player(self):
  table,metadata,layouts=self.layouts(1500);entry=next(row for row in layouts if row['player']['profile_id']==7)
  points=[entry['line'].center() for _ in range(3)]
  points[0].setX(entry['area'].left()+10)
  points[1].setX(entry['area'].left()+MATCH_FLAG_OFFSET+10)
  points[2].setX(entry['name_rect'].right()-2)
  self.assertGreater(points[2].x(),entry['hit_rect'].right())
  for point in points:
   with self.subTest(point=point):
    requested=QSignalSpy(self.page.public_match_requested)
    with patch.object(self.page,'_job') as job:
     self.double_click(table,point);job.assert_not_called()
    self.assertEqual(len(requested),1);self.assertEqual(requested[0][1],metadata['game_id'])
 def test_missing_or_invalid_player_id_never_guesses_name_or_opens_match(self):
  for invalid in (None,0,True,'同名玩家'):
   with self.subTest(profile_id=invalid):
    sample=copy.deepcopy(self.sample);person=sample['games'][0]['teams'][1][0]['player']
    person.update(profile_id=invalid,name='没有有效编号的玩家');self.page._show_snapshot(sample)
    table,_,layouts=self.layouts();entry=next(row for row in layouts if row['player'].get('name')=='没有有效编号的玩家')
    requested=QSignalSpy(self.page.public_match_requested)
    with patch.object(self.page,'_job') as job,patch.object(self.page,'lookup') as lookup:
     self.double_click(table,entry['hit_rect'].center());job.assert_not_called();lookup.assert_not_called()
    self.assertEqual(len(requested),0);self.assertIn('未提供有效编号',self.page.notice.text())
 def test_existing_arrow_click_remains_one_internal_match_request(self):
  table,metadata,_=self.layouts();requested=QSignalSpy(self.page.public_match_requested)
  arrow=table.cellWidget(0,6).findChild(QPushButton,'GetMatchReplay');QTest.mouseClick(arrow,Qt.LeftButton)
  self.assertEqual(len(requested),1);self.assertEqual(requested[0][:2],[7,metadata['game_id']])

if __name__=='__main__':unittest.main()
