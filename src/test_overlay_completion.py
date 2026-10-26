"""演练末阶段宽限与偏好验证；全部使用隔离设置及合成开局。"""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from core import atomic_json, load_settings, overlay_end_deadline
from test_app import timed_example


class CompletionDeadlineTests(unittest.TestCase):
    def test_last_timed_step_gets_two_minutes_and_does_not_change_input(self):
        order = timed_example()
        order['build_order'][-1]['time'] = '10:00'
        before = deepcopy(order)
        self.assertEqual(overlay_end_deadline(order), 720)
        self.assertEqual(order, before)

    def test_manual_and_bad_timelines_have_no_fabricated_deadline(self):
        for order in (None, {}, {'build_order': []}):
            self.assertIsNone(overlay_end_deadline(order))
        manual = timed_example()
        for step in manual['build_order']:
            step.pop('time', None)
        self.assertIsNone(overlay_end_deadline(manual))
        manual['build_order'][0]['time'] = '99:xx'
        self.assertIsNone(overlay_end_deadline(manual))

    def test_trailing_untimed_conditions_share_last_known_time(self):
        order = timed_example()
        order['build_order'][-1]['time'] = '9:30'
        tail = deepcopy(order['build_order'][-1])
        tail.pop('time')
        order['build_order'].append(tail)
        self.assertEqual(overlay_end_deadline(order), 690)

    def test_approved_style_values_and_saved_choice_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '设置.json'
            settings, _ = load_settings(path)
            self.assertEqual(settings['overlay_style'], 'a')
            for style in ('a', 'c', 'b'):
                settings['overlay_style'] = style
                atomic_json(path, settings)
                self.assertEqual(load_settings(path)[0]['overlay_style'], style)
            for invalid in ('d', 'unknown', '', None, 2):
                settings['overlay_style'] = invalid
                atomic_json(path, settings)
                self.assertEqual(load_settings(path)[0]['overlay_style'], 'a')


if __name__ == '__main__':
    unittest.main()
