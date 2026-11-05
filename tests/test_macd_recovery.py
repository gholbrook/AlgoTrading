import unittest
from copy import deepcopy

from gridlab.file_engine import step_file
from tests.test_file_engine import STRATEGIES, bar, source, state_for


class MacdRecoveryTests(unittest.TestCase):
    def test_recovery_returns_original_outlay_after_both_fees_then_stop_protects_runner(self):
        code = source("return [] if ctx['bar']['ts'] != 1 else "
                      "[{'direction':'long','notional':100,'stop_pct':2,'recover_cost_pct':2}]")
        state = state_for(code, fee_bps=20)
        step_file(state, bar(1))
        buy = step_file(state, bar(2))["events"][0]
        sale = step_file(state, bar(3, 100, 102, 99, 101))["events"][0]
        self.assertAlmostEqual(sale["qty"]*sale["price"]-sale["fee"], buy["qty"]*buy["price"]+buy["fee"])
        stop = step_file(state, bar(4, 99, 99, 97, 98))["events"][0]
        self.assertEqual((stop["reason"], stop["price"]), ("stop",98))
        self.assertAlmostEqual(sale["qty"]+stop["qty"],buy["qty"])

    def test_bullish_cross_stages_ladder(self):
        state = state_for((STRATEGIES/"macd_recovery.py").read_text())
        for ts, price in enumerate([80]*100+[100]*105+[98,96,94,94,94,94,94,94],1):
            point = step_file(state,bar(ts,price,price,price,price))
            if point["orders"]:
                break
        hist = state["strategy_memory"]["hist"]
        self.assertTrue(hist[-2] <= 0 < hist[-1])
        self.assertEqual([o["type"] for o in point["orders"]],["market","limit","limit"])
        buys = step_file(state,bar(ts+1,price,price,price*0.989,price))["events"]
        self.assertEqual(len(buys),3)
        for lot in state["positions"]:
            self.assertAlmostEqual(lot["stop_price"],lot["entry_price"]*0.98)

    def test_reversal_cancels_ladder_and_recovery_before_gap_up(self):
        state = state_for((STRATEGIES/"macd_recovery.py").read_text())
        for ts, price in enumerate([80]*100+[100]*105+[98,96,94]+[94]*15,1):
            if step_file(state,bar(ts,price,price,price,price))["orders"]:
                break
        buys = step_file(state,bar(ts+1,price,price,price*0.994,price))
        self.assertEqual(len(buys["events"]),2)
        self.assertEqual(len(buys["orders"]),1)
        for hist, signal in [([3, 2, 1], -1), ([-0.1, -0.2, -0.5], 1), ([2, 1, 0], 1)]:
            with self.subTest(hist=hist):
                run = deepcopy(state)
                run["strategy_memory"].update(fast=price, slow=price, signal=signal, hist=hist)
                reversal = step_file(run,bar(ts+2,price,price,price,price))
                self.assertEqual([o["action"] for o in reversal["orders"]],["exit","exit"])
                exits = step_file(run,bar(ts+3,price*1.05,price*1.06,price*0.98,price))
                self.assertEqual([e["reason"] for e in exits["events"]],["strategy_exit"]*2)
                self.assertEqual([e["qty"] for e in exits["events"]],[e["qty"] for e in buys["events"]])
                self.assertFalse(run["positions"])

    def test_entry_requires_cross_even_in_falling_regime(self):
        from strategies.macd_recovery import SETTINGS, on_bar

        for previous, signal, expected in [(-1, -1, 3), (0, -1, 3), (0.4, -1, 0), (-1, 1, 0)]:
            with self.subTest(previous=previous, signal=signal):
                ctx = dict(settings=SETTINGS, fee_bps=20, cash=1000, events=[], positions=[],
                           history=[{"close": 110}]*5 + [{"close": 100}]*200,
                           bar={"close": 100}, memory=dict(fast=100, slow=100, signal=signal,
                                                           hist=[-3, -2, previous]))
                self.assertEqual(len(on_bar(ctx)), expected)
