import unittest

from gridlab.file_engine import step_file
from tests.test_file_engine import STRATEGIES, bar, source, state_for


class StrategyPlotTests(unittest.TestCase):
    def test_reentry_bands_follow_strategy_setting_during_warmup(self):
        state = state_for((STRATEGIES / "bollinger_reentry_grid.py").read_text())
        state["config"]["strategy_settings"] = {"band_deviations": 1.0}
        for ts in range(1, 21):
            price = 99 if ts % 2 else 101
            point = step_file(state, bar(ts, price, price, price, price), trade=False)
            if ts < 20:
                self.assertIsNone(point["plots"]["Upper band"])
        self.assertEqual(point["plots"], {"Upper band": 101, "Middle band": 100,
                                          "Lower band": 99, "Regime SMA": None})
        self.assertEqual(point["orders"], [])

    def test_optional_hook_and_invalid_value(self):
        code = source("return []")
        self.assertNotIn("plots", step_file(state_for(code), bar(1)))
        with self.assertRaisesRegex(ValueError, "chart_indicators.*finite price"):
            step_file(state_for(code + 'def chart_indicators(ctx):\n    return {"x": float("nan")}\n'), bar(1))
