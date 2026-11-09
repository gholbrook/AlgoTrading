import unittest

from research.value_area_predictability import features, target


class PredictabilityTests(unittest.TestCase):
    def test_predictors_do_not_see_future_or_unfinished_instances(self):
        episodes = [dict(id=i, anchor=10*i, observed_end=10*i+5,
                         duration_candles=i, censored=False) for i in range(8)]
        # The first episode's outcome is not available for a long time.
        episodes[0]['observed_end'] = 1000
        rows = features(episodes, 2)
        self.assertEqual(rows[0]['id'], 5)
        self.assertEqual(rows[0]['recent_mean'], 3.5)
        self.assertEqual(rows[0]['earlier_mean'], 1.5)
        before = rows[0]['recent_mean']
        episodes[5]['duration_candles'] = 10000
        self.assertEqual(features(episodes, 2)[0]['recent_mean'], before)

    def test_censored_targets_are_known_only_above_threshold(self):
        self.assertEqual(target({'duration': 40, 'censored': True}, 24), 1)
        self.assertIsNone(target({'duration': 10, 'censored': True}, 24))
        self.assertEqual(target({'duration': 10, 'censored': False}, 24), 0)
