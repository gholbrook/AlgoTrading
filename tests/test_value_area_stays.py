import unittest
from types import SimpleNamespace

from research.value_area_stays import balanced_bounds, observe, run_experiment, settings, summarize


def candles(prices):
    return [dict(ts=300*i, open=p, high=p, low=p, close=p, volume=10) for i,p in enumerate(prices)]


class ValueAreaStayTests(unittest.TestCase):
    def test_short_excursions_return_and_final_streak_is_excluded(self):
        rows = candles([100, 100, 110, 110, 100, 110, 110, 110])
        result = observe(rows, 0, 99, 101, settings({'grace': 2}), 300)
        self.assertEqual(result['outcome'], 'exit')
        self.assertEqual(result['duration_candles'], 4)
        self.assertEqual(result['inside_candles'], 2)
        self.assertEqual(result['ignored_candles'], 2)
        self.assertEqual(result['ignored_excursions'], 1)
        self.assertEqual(result['strict_duration_candles'], 1)
        self.assertEqual(result['exit_start'], 1500)
        self.assertEqual(result['confirmed_at'], 2100)

    def test_zero_allowance_and_wicks(self):
        rows = candles([100, 100, 100])
        rows[1]['high'] = 102
        close = observe(rows, 0, 99, 101, settings({'grace': 0}), 300)
        wick = observe(rows, 0, 99, 101, settings({'grace': 0, 'inside': 'wick'}), 300)
        self.assertEqual(close['outcome'], 'data_end')
        self.assertEqual(wick['outcome'], 'exit')
        self.assertEqual(wick['duration_candles'], 0)

    def test_censoring_and_unreturned_excursion(self):
        rows = candles([100, 100, 110])
        result = observe(rows, 0, 99, 101, settings({'grace': 2}), 300)
        self.assertTrue(result['censored'])
        self.assertEqual(result['ignored_candles'], 0)
        self.assertEqual(result['duration_candles'], 1)
        summary = summarize([result], 300)
        self.assertIsNone(summary['mean_duration_candles'])
        self.assertEqual(summary['mean_observed_lower_bound_candles'], 1)
        result = observe(candles([100]*10), 0, 99, 101, settings({'horizon': 3}), 300)
        self.assertEqual(result['outcome'], 'horizon')
        self.assertEqual(result['duration_candles'], 3)

    def test_gap_censors_instead_of_crossing_missing_candles(self):
        rows = candles([100]*5)
        rows.pop(2)
        result = observe(rows, 0, 99, 101, settings(), 300)
        self.assertEqual(result['outcome'], 'gap')
        self.assertEqual(result['duration_candles'], 1)

    def test_boundaries_use_formation_only_and_observations_do_not_overlap(self):
        rows = candles([100]*6 + [200]*4 + [100]*8)
        dataset = SimpleNamespace(timeframe='5m', select=lambda *args, **kwargs: ([], rows),
                                  describe=lambda: {'id': 'fixture'})
        config = {'candles': 3, 'grace': 0, 'horizon': 4}
        result = run_experiment(dataset, config)
        first = result['episodes'][0]
        rows[-1]['high'] = 10000
        changed = run_experiment(dataset, config)
        self.assertEqual(first, changed['episodes'][0])
        self.assertLess(first['value_high'], 101)
        for a,b in zip(result['episodes'], result['episodes'][1:]):
            self.assertGreaterEqual(b['anchor'], a['observed_end'])

    def test_balanced_area_counts_half_the_poc_bin_on_each_side(self):
        profile = dict(low=0, high=4, volumes=[20, 40, 10, 30], total_volume=100)
        self.assertEqual(balanced_bounds(profile, 0.5), (0.75, 2.5))
        profile['volumes'] = [40, 30, 20, 10]
        self.assertIsNone(balanced_bounds(profile, 0.5)[0])

    def test_invalid_config(self):
        for config in ({'candles': 1}, {'bins': 101}, {'value_area': 0}, {'grace': 5, 'horizon': 5}):
            with self.assertRaises(ValueError):
                settings(config)
