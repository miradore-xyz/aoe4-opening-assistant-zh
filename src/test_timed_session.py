"""使用真实英格兰默认开局验证窗口推进、暂停与条件资源合并。"""
import copy
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PyQt5.QtCore import QEvent
from PyQt5.QtWidgets import QApplication
from app import Manager, resource_step
from core import atomic_json,read_order
from test_app import destroy_test_manager

class TimedSessionTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.application=QApplication.instance() or QApplication([])
  cls.application.setQuitOnLastWindowClosed(False)
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory()
  folder=Path(self.temp.name)
  self.order=read_order(Path(__file__).resolve().parent/'默认开局/06-英格兰.json')
  self.path=folder/'开局库/英格兰.json'
  atomic_json(self.path,self.order)
  atomic_json(folder/'设置.json',{'last_order':str(self.path),'speech':False,'sound':False,'voice_engine':'system','hotkeys':{},'timed_playback_policy':1})
  self.patches=[patch.object(Manager,'seed'),patch('app.Speech.start'),patch('app.Manager.register_hotkeys'),patch('update_ui.UpdateController.check')]
  for item in self.patches:item.start()
  self.manager=Manager(self.application,folder)
  self.manager.tick_timer.stop()
 def tearDown(self):
  destroy_test_manager(self.application,self.manager)
  for item in reversed(self.patches):item.stop()
  self.temp.cleanup()
 def test_actual_english_auto_progresses_after_a_tail_with_no_timestamp(self):
  m=self.manager
  self.assertTrue(m.settings['auto'])
  self.assertTrue(m.auto_check.isChecked())
  self.assertEqual(m.ids,[0])
  m.toggle_clock()
  for seconds,expected in [(20,[1]),(60,[2]),(240,[8]),(340,[9,10]),(1402,[9,10])]:
   m.clock.seek(seconds);m.tick()
   self.assertEqual(m.ids,expected)
   self.assertEqual(m.steps_list.currentRow(),expected[0])
  self.assertIn('10–11',m.session_step.text())
  self.assertIn('条件提示',m.overlay.notes.text())
  self.assertIn('控图后',m.overlay.notes.text())
  self.assertEqual(resource_step(m.order,m.ids)['resources']['food'],11)
  self.assertEqual(resource_step(m.order,m.ids)['resources']['wood'],11)
  self.assertTrue(m.overlay.resources.cells['food'][0].isVisibleTo(m.overlay.resources))
 def test_paused_timer_does_not_advance_and_resume_selects_current_group(self):
  m=self.manager
  m.clock.seek(240);m.tick()
  self.assertFalse(m.clock.running)
  self.assertEqual(m.ids,[0])
  m.toggle_clock()
  self.assertTrue(m.clock.running)
  self.assertEqual(m.ids,[8])
  m.toggle_clock()
  m.clock.seek(340);m.tick()
  self.assertEqual(m.ids,[8])
 def test_conditional_resource_merge_preserves_source_and_explicit_zero(self):
  data=copy.deepcopy(self.order)
  data['build_order'][-1]['resources']['wood']=0
  original=copy.deepcopy(data)
  merged=resource_step(data,[9,10])
  self.assertEqual(merged['resources']['food'],11)
  self.assertEqual(merged['resources']['wood'],0)
  self.assertEqual(data,original)

if __name__=='__main__':unittest.main()
