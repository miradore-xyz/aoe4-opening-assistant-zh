"""混合时间开局回归：保留原索引、原稿空时间和上游完整时间语义。"""
import copy
from pathlib import Path
import unittest

from core import read_order, timer_steps, timed_ids
from common.build_order_tools import get_build_order_timer_steps, get_build_order_timer_step_ids


MISSING = object()


def opening(*times):
    """仅构造核心计时需要的节点，标签便于检查来源索引。"""
    nodes = []
    for index, time in enumerate(times):
        step = {"notes": [f"原始节点{index}"]}
        if time is not MISSING:
            step["time"] = time
        nodes.append(step)
    return {"build_order": nodes}


class MixedTimingTests(unittest.TestCase):
    def test_mixed_times_progress_with_original_indices_and_do_not_modify_source(self):
        data = opening("0:00", "", "1:00", MISSING, "2:00", "")
        original = copy.deepcopy(data)
        steps = timer_steps(data)
        self.assertEqual(len(steps), len(data["build_order"]))
        self.assertEqual([step["time_sec"] for step in steps], [0, 0, 60, 60, 120, 120])
        for seconds, expected in ((0, [0, 1]), (59, [0, 1]), (60, [2, 3]),
                                  (119, [2, 3]), (120, [4, 5]), (1200, [4, 5])):
            with self.subTest(seconds=seconds):
                self.assertEqual(timed_ids(data, seconds, starting=True), expected)
        self.assertEqual(data, original)
        self.assertEqual(steps[1]["time"], "")
        self.assertNotIn("time", steps[3])

    def test_leading_conditions_show_before_the_first_known_time(self):
        data = opening(MISSING, "", "0:30", "", "1:00")
        self.assertEqual(timed_ids(data, 0, starting=True), [0, 1])
        self.assertEqual(timed_ids(data, 29, starting=True), [0, 1])
        self.assertEqual(timed_ids(data, 30, starting=True), [2, 3])
        self.assertEqual(timed_ids(data, 60, starting=True), [4])
        # 结束语义：初始说明在0秒；随后是截至30秒应完成的时间组。
        self.assertEqual(timed_ids(data, 0, starting=False), [0, 1])
        self.assertEqual(timed_ids(data, 1, starting=False), [2, 3])
        self.assertEqual(timed_ids(data, 31, starting=False), [4])

    def test_trailing_conditions_keep_the_last_group_in_ending_semantics(self):
        data = opening("0:00", "0:10", "", MISSING)
        self.assertEqual(timed_ids(data, 0, starting=False), [0])
        self.assertEqual(timed_ids(data, 1, starting=False), [1, 2, 3])
        self.assertEqual(timed_ids(data, 10, starting=False), [1, 2, 3])
        self.assertEqual(timed_ids(data, 1200, starting=False), [1, 2, 3])

    def test_equal_known_times_merge_with_interleaved_conditions(self):
        data = opening("0:00", "0:10", "", "0:10", "", "0:20")
        self.assertEqual(timed_ids(data, 10, starting=True), [1, 2, 3, 4])
        self.assertEqual(timed_ids(data, 10, starting=False), [1, 2, 3, 4])
        self.assertEqual(timed_ids(data, 20, starting=True), [5])

    def test_all_missing_times_remain_manual(self):
        for data in (opening(), opening("", MISSING, "  "), opening(MISSING)):
            with self.subTest(data=data):
                self.assertEqual(timer_steps(data), [])
                self.assertEqual(timed_ids(data, 1200, starting=True), [])

    def test_invalid_or_backwards_known_times_do_not_silently_enable_auto(self):
        for value in ("1:60", "0:5", "-1:00", "~1:00", "等资源", " 1:00 ", None, 60):
            with self.subTest(value=value):
                self.assertFalse(timer_steps(opening("0:00", "", value)))
        self.assertFalse(timer_steps(opening("0:30", "", "0:20")))
        self.assertFalse(timer_steps({"build_order": [{"time": "0:00"}]}))

    def test_complete_timestamp_orders_keep_exact_upstream_behavior(self):
        for data in (opening("0:00", "0:10", "0:20"),
                     opening("0:10", "0:10", "0:20"),
                     opening("0:00", "0:10", "0:10")):
            upstream = get_build_order_timer_steps(data)
            self.assertEqual(timer_steps(data), upstream)
            for starting in (False, True):
                for seconds in (0, 1, 9, 10, 11, 20, 21, 1200):
                    with self.subTest(times=data, starting=starting, seconds=seconds):
                        self.assertEqual(timed_ids(data, seconds, starting=starting),
                                         get_build_order_timer_step_ids(upstream, seconds, starting_flag=starting))

    def test_actual_english_default_reaches_its_last_time_and_condition(self):
        path = Path(__file__).resolve().parent / "默认开局/06-英格兰.json"
        data = read_order(path)
        original = copy.deepcopy(data)
        self.assertTrue(timer_steps(data))
        for seconds, expected in ((0, [0]), (60, [2]), (130, [3]), (131, [4]),
                                  (240, [8]), (340, [9, 10]), (1200, [9, 10])):
            with self.subTest(seconds=seconds):
                self.assertEqual(timed_ids(data, seconds, starting=True), expected)
        self.assertEqual(data, original)
        self.assertEqual(data["build_order"][-1]["time"], "")


if __name__ == "__main__":
    unittest.main()
