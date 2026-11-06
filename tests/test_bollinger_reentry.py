import unittest

from tests.test_file_engine import STRATEGIES, bar, state_for
from gridlab.file_engine import step_file


class BollingerReentryTests(unittest.TestCase):
    def setup_signal(self, early_price=80, previous_close=90):
        code = (STRATEGIES / "bollinger_reentry_grid.py").read_text()
        state = state_for(code, fee_bps=20)
        for ts, price in enumerate([early_price] * 100 + [100] * 103 + [previous_close], 1):
            step_file(state, bar(ts, price, price, price, price), trade=False)
        return state, step_file(state, bar(205))

    def test_requires_rising_regime_and_confirmed_reentry(self):
        _, falling = self.setup_signal(early_price=120)
        _, inside = self.setup_signal(previous_close=100)
        self.assertEqual(falling["orders"], [])
        self.assertEqual(inside["orders"], [])

    def test_one_grid_four_equal_budgets_and_sixteen_linked_sells(self):
        state, signal = self.setup_signal()
        self.assertEqual([o.get("price") for o in signal["orders"]], [None, 98, 96, 94])
        actual_buys = step_file(state, bar(206, 101, 101, 93, 95))
        buys = actual_buys["events"]
        self.assertEqual([e["price"] for e in buys], [101, 98, 96, 94])
        for e in buys:
            self.assertAlmostEqual(e["qty"] * e["price"] + e["fee"], 250)
        self.assertAlmostEqual(state["cash"], 0)
        self.assertEqual(actual_buys["orders"], [])
        exits = step_file(state, bar(207, 95, 110, 95, 108))["events"]
        self.assertEqual(len(exits), 16)
        for buy in buys:
            sells = [e for e in exits if e["lot_id"] == buy["lot_id"]]
            self.assertEqual(len(sells), 4)
            for i, sell in enumerate(sells, 1):
                self.assertAlmostEqual(sell["qty"], buy["qty"] / 4)
                self.assertAlmostEqual(sell["price"], buy["price"] * (1 + i * 0.02))
        self.assertFalse(state["positions"])
        self.assertNotIn("grid", state["strategy_memory"])

    def test_unused_buys_stay_working_until_flat(self):
        state, _ = self.setup_signal()
        seed = step_file(state, bar(206))
        self.assertEqual(len(seed["events"]), 1)
        self.assertEqual([o["price"] for o in seed["orders"]], [98, 96, 94])
        partial = step_file(state, bar(207, 100, 102.1, 99, 102))
        self.assertEqual(len(partial["events"]), 1)
        self.assertEqual([o["price"] for o in partial["orders"]], [98, 96, 94])
        flat = step_file(state, bar(208, 102, 109, 102, 108))
        self.assertEqual(len(flat["events"]), 3)
        self.assertEqual(flat["orders"], [])
        self.assertEqual(step_file(state, bar(209, 108, 108, 93, 99))["events"], [])
