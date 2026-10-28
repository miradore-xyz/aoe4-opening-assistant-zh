"""全历史计数与15天缓存回归：不把增量数/重复模式/旧赛季当累计。"""
import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from match_analysis import PublicMatchClient, retain_rank_history
from test_match_analysis import game


class OverviewHistoryDataTests(unittest.TestCase):
    def test_incremental_total_is_replaced_by_unfiltered_count_without_fetching_history(self):
        now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            client = PublicMatchClient(directory, now=lambda: now)
            with patch.object(client, '_get', side_effect=[{'profile_id': 7},
                    {'games': [game()], 'total_count': 120, 'filters': {'since': None}}]):
                first = client.fetch_profile(7)
            self.assertEqual(first['history_totals']['count'], 120)
            with patch.object(client, '_get', side_effect=[{'profile_id': 7},
                    {'games': [game()], 'total_count': 5, 'filters': {'since': '2026-10-20'}},
                    {'games': [game()], 'total_count': 121, 'filters': {'since': None}}]) as get:
                updated = client.fetch_profile(7, refresh=True)
            self.assertEqual(updated['history_totals'],
                             {'count': 121, 'scope': 'public_history', 'fetched_at': now.isoformat()})
            self.assertIn('since', get.call_args_list[1].args[1])
            self.assertEqual(get.call_args_list[2].args[1], {'limit': 1, 'page': 1})
            self.assertEqual(len(updated['games']), 1, '读取总数不能把历史逐页拉到缓存')

    def test_missing_or_filtered_total_is_not_estimated_from_mode_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            client = PublicMatchClient(directory)
            for result in ({'games': [game()]}, {'games': [game()], 'total_count': True},
                           {'games': [game()], 'total_count': 5, 'filters': {'leaderboard': 'rm_team'}}):
                with patch.object(client, '_get', side_effect=[{'profile_id': 7,
                        'modes': {'rm_team': {'games_count': 999}}}, result, result]):
                    snapshot = client.fetch_profile(7, refresh=True)
                self.assertNotIn('history_totals', snapshot)

    def test_same_season_keeps_real_snapshots_and_new_values_win(self):
        now = datetime(2026, 10, 27, 12).astimezone()
        stamp = lambda days: str(int((now-timedelta(days=days)).timestamp()))
        old = {'modes': {'rm_team': {'season': 14, 'rating_history': {
            stamp(2): {'rating': 1200}, stamp(3): {'rating': 1250}, stamp(16): {'rating': 900}}}}}
        new = {'modes': {'rm_team': {'season': 14, 'rating_history': {stamp(2): {'rating': 1300}}}}}
        before = copy.deepcopy(old)
        retain_rank_history(new, old, now)
        self.assertEqual(new['modes']['rm_team']['rating_history'],
                         {stamp(2): {'rating': 1300}, stamp(3): {'rating': 1250}})
        self.assertEqual(old, before)
        new['modes']['rm_team']['season'] = 15
        new['modes']['rm_team']['rating_history'] = {}
        retain_rank_history(new, old, now)
        self.assertEqual(new['modes']['rm_team']['rating_history'], {})


if __name__ == '__main__':
    unittest.main()
