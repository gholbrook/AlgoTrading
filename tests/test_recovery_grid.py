"""The recovery example switches exit spacing using net basket P&L."""
import unittest

from strategies.recovery_grid import SETTINGS, on_bar


class RecoveryGridTests(unittest.TestCase):
    def test_remaining_stop_costs_delay_the_switch_and_keep_entry_spacing(self):
        ctx = {"settings": dict(SETTINGS, recovery_step_pct=3), "memory": {},
               "positions": [], "cash": 10000, "realized_pnl": 100, "events": [],
               "bar": {"close": 100}, "fee_bps": 10, "slip_bps": 10}
        self.assertEqual(on_bar(ctx)[0]["type"], "market")
        ctx["positions"] = [{"lot_id": 1, "entry_price": 100, "stop_price": 98,
                             "qty": 1, "entry_fee": 0.1}]
        ctx["events"] = [{"kind": "entry", "price": 100}]
        ctx["realized_pnl"] = 102.1
        before = on_bar(ctx)
        self.assertNotIn("anchor", ctx["memory"])
        self.assertEqual(before[0]["price"], 100.8)
        ctx.update(events=[], realized_pnl=102.5)
        after = on_bar(ctx)
        self.assertEqual(after[0]["price"], 103)
        self.assertEqual(after[1], before[1])
        ctx["bar"] = {"close": 102}
        self.assertEqual(on_bar(ctx)[0]["price"], 103)
