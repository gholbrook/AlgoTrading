import json
import unittest
from pathlib import Path

from indicators.volume_profile import volume_profile, validate_profile
from gridlab.file_engine import step_file
from tests.test_file_engine import state_for, bar


class VolumeProfileTests(unittest.TestCase):
    def test_uniform_allocation_flat_candle_and_value_area(self):
        history = [dict(ts=0, low=100, high=104, volume=40),
                   dict(ts=300, low=100, high=100, volume=20)]
        profile = volume_profile({'bar': history[-1], 'history': history}, bins=4)
        self.assertEqual(profile['volumes'], [30, 10, 10, 10])
        self.assertEqual(profile['poc'], 100.5)
        self.assertEqual((profile['value_low'], profile['value_high']), (100, 103))
        self.assertFalse(profile['complete'])
        validate_profile(json.loads(json.dumps(profile)))

    def test_trailing_month_excludes_old_and_future_candles(self):
        history = [dict(ts=i * 300, low=100, high=101, volume=1) for i in range(9001)]
        profile = volume_profile({'bar': history[8999], 'history': history}, bins=8)
        self.assertEqual(profile['candles'], 8640)
        self.assertAlmostEqual(profile['total_volume'], 8640)
        self.assertTrue(profile['complete'])
        history.pop(4000)
        profile = volume_profile({'bar': history[-2], 'history': history})
        self.assertFalse(profile['complete'])

    def test_zero_volume_and_invalid_settings(self):
        history = [dict(ts=1, low=100, high=100, volume=0)]
        ctx = {'bar': history[0], 'history': history}
        profile = volume_profile(ctx)
        self.assertIsNone(profile['poc'])
        validate_profile(profile)
        for kwargs in ({'bins': 1}, {'days': float('nan')}, {'value_area': 2}):
            with self.assertRaises(ValueError):
                volume_profile(ctx, **kwargs)

    def test_strategy_import_persistence_and_replay(self):
        source = (Path(__file__).resolve().parents[1] / 'strategies/volume_profile.py').read_text()
        state = state_for(source)
        first = step_file(state, bar(300))
        self.assertEqual(first['orders'], [])
        state = json.loads(json.dumps(state))
        second = step_file(state, bar(600))
        self.assertEqual(first['plots']['Monthly volume profile']['candles'], 1)
        self.assertEqual(second['plots']['Monthly volume profile']['candles'], 2)
        invalid = dict(second['plots']['Monthly volume profile'], volumes=[float('nan'), 1])
        with self.assertRaises(ValueError):
            validate_profile(invalid)
