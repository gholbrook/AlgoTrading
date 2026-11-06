import json
import unittest

from gridlab.engine import DEFAULTS, new_state, open_lots, step, summary, validate_config, warmup_count


def bar(ts, price=100.0, *, high=None, low=None, open_=None):
    o = price if open_ is None else open_
    return {"ts": ts, "open": o,
            "high": max(price, o) if high is None else high,
            "low": min(price, o) if low is None else low, "close": price, "volume": 10.0}


class EngineTests(unittest.TestCase):
    def test_config_defaults_and_validation(self):
        self.assertEqual(validate_config({}), DEFAULTS)
        with self.assertRaises(ValueError):
            validate_config({"typo": 1})
        with self.assertRaises(ValueError):
            validate_config({"direction": "short"})
        for bad in ({"size_pct": 0}, {"tp_pct": 100}, {"sl_pct": 0},
                    {"slip_bps": 10000}, {"capital": float("inf")}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_config(bad)
        for bad in ({"bb_period": 0}, {"bb_mult": float("nan")},
                    {"trend_period": True}, {"trend_lookback": 0},
                    {"tier2_mult": -1}, {"signal_model": "unknown"},
                    {"bull_price": "median"}, {"bear_op": "<"},
                    {"bull_level": "vwap"}, {"bear_trend": "falling"},
                    {"execution_model": "mystery"}, {"grid_lines": 0},
                    {"grid_spacing": "random"}, {"grid_after_exits": "random"},
                    {"grid_step_pct": 0}, {"strategy_file": "x.py"},
                    {"strategy_settings": [1]},
                    {"trend_period": 715, "trend_lookback": 5}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_config(bad)
        self.assertEqual(warmup_count({}), 205)
        self.assertEqual(warmup_count({"bb_period": 40, "trend_period": 50,
                                       "trend_lookback": 4}), 54)

    def test_fresh_state_summary_and_open_lots(self):
        state = new_state()
        self.assertEqual(summary(state)["equity"], 50000)
        self.assertEqual(open_lots(state), [])

    def test_rejects_bad_timestamps_and_ohlcv(self):
        for bad_bar in (bar(float("nan")), {**bar(1), "volume": float("inf")},
                        {**bar(1), "high": 90}):
            with self.subTest(bad_bar=bad_bar):
                with self.assertRaises(ValueError):
                    step(new_state(), bad_bar)

    def test_state_is_json_serializable_and_history_bounded(self):
        state = new_state()
        for i in range(500):
            step(state, bar(i, 100 + (i % 7) * .01), trade=False)
        json.dumps(state)
        self.assertEqual(len(state["history"]), 205)
        self.assertEqual(state["positions"], [])
        self.assertEqual(state["pending"], [])

    def test_resumed_legacy_config_gets_new_defaults(self):
        state = new_state()
        state["config"] = {key: value for key, value in DEFAULTS.items()
                           if key in ("capital", "size_pct", "fee_bps", "slip_bps",
                                      "tp_pct", "sl_pct", "direction")}
        point = step(state, bar(1), trade=False)
        self.assertIsNone(point["sma"])
        self.assertEqual(state["config"]["bb_period"], 20)
        self.assertEqual(state["config"]["signal_model"], "wick-grid")

    def test_warmup_and_signal_queues_for_next_open(self):
        state = new_state({"capital": 1000, "fee_bps": 0, "slip_bps": 0})
        # Stable rising closes produce an SMA regime and a lower-band wick.
        for i in range(204):
            point = step(state, bar(i, 100 + i * .01), trade=False)
            if i < 199:
                self.assertIsNone(point["sma"])
            self.assertEqual(point["events"], [])
        # First eligible indicator bar; force a lower wick without changing close.
        point = step(state, bar(204, 102.04, low=90), trade=True)
        self.assertEqual(point["signal"], "long")
        self.assertEqual(point["events"], [])
        self.assertEqual(len(state["pending"]), 1)
        point = step(state, bar(205, 102.5), trade=True)
        self.assertEqual(point["events"][0]["kind"], "entry")
        self.assertEqual(point["events"][0]["price"], 102.5)
        self.assertEqual(len(state["positions"]), 1)

    def test_configurable_bollinger_and_tier_two_multiple(self):
        state = new_state({"bb_period": 3, "bb_mult": 1.0, "trend_period": 3,
                           "trend_lookback": 1, "tier2_mult": 2.0,
                           "fee_bps": 0, "slip_bps": 0})
        for i, close in enumerate((100, 100, 100, 101)):
            step(state, bar(i, close), trade=False)
        point = step(state, bar(4, 102), trade=False)
        self.assertAlmostEqual(point["upper"], 101.81649658)
        state["pending"] = [{"direction": "long", "streak": 2, "band_pct": 1.25,
                             "reason": "lower_wick"}]
        step(state, bar(5, 102), trade=True)
        self.assertAlmostEqual(open_lots(state)[0]["target"], 104.55)

    def test_sma_cross_signal_and_reason(self):
        state = new_state({"signal_model": "sma-cross", "bb_period": 2,
                           "trend_period": 3, "trend_lookback": 1,
                           "direction": "long-short"})
        for i, close in enumerate((100, 100, 100)):
            point = step(state, bar(i, close), trade=True)
        point = step(state, bar(3, 101), trade=True)
        self.assertEqual(point["signal"], "long")
        self.assertEqual(state["pending"][0]["direction"], "long")
        self.assertEqual(state["pending"][0]["reason"], "sma_cross_up")

    def test_custom_signal_rules_and_bullish_priority(self):
        state = new_state({"signal_model": "custom", "bb_period": 2,
                           "trend_period": 2, "trend_lookback": 1,
                           "bull_price": "close", "bull_op": ">=",
                           "bull_level": "sma", "bull_trend": "any",
                           "bear_price": "close", "bear_op": "<=",
                           "bear_level": "sma", "bear_trend": "any",
                           "direction": "long-short"})
        for i, price in enumerate((100, 100)):
            step(state, bar(i, price), trade=False)
        point = step(state, bar(2, 100), trade=True)
        self.assertEqual(point["signal"], "long")
        self.assertEqual(state["pending"][0]["direction"], "long")
        self.assertEqual(state["pending"][0]["reason"], "custom_bull")
        state = new_state({"signal_model": "custom", "bb_period": 2,
                           "trend_period": 2, "trend_lookback": 1,
                           "bull_price": "close", "bull_op": ">=",
                           "bull_level": "sma", "bull_trend": "any",
                           "bear_price": "high", "bear_op": ">=",
                           "bear_level": "upper", "bear_trend": "disabled"})
        step(state, bar(0, 100), trade=False)
        step(state, bar(1, 100), trade=False)
        point = step(state, bar(2, 100, high=101), trade=True)
        self.assertEqual(point["signal"], "long")
        self.assertEqual(state["pending"][0]["reason"], "custom_bull")

    def test_single_position_executes_only_one_queued_entry(self):
        state = new_state({"execution_model": "single-position", "fee_bps": 0,
                           "slip_bps": 0, "sl_pct": 20, "tp_pct": 20})
        state["pending"] = [{"direction": "long", "streak": 1, "band_pct": .5,
                             "reason": "first"},
                            {"direction": "short", "streak": 1, "band_pct": .5,
                             "reason": "second"}]
        point = step(state, bar(0), trade=True)
        self.assertEqual(len([e for e in point["events"] if e["kind"] == "entry"]), 1)
        self.assertEqual(len(state["positions"]), 1)
        state["pending"] = [{"direction": "short", "streak": 1, "band_pct": .5,
                             "reason": "later"}]
        point = step(state, bar(1), trade=True)
        self.assertFalse(any(e["kind"] == "entry" for e in point["events"]))

    def test_price_grid_adverse_gap_add_has_bounded_layers_and_diagnostics(self):
        state = new_state({"execution_model": "price-grid", "grid_lines": 2,
                           "grid_spacing": "fixed", "grid_step_pct": 1.0,
                           "fee_bps": 0, "slip_bps": 2, "tp_pct": 10, "sl_pct": 10})
        state["pending"] = [{"direction": "long", "streak": 1, "band_pct": 1.0,
                             "reason": "lower_wick"}]
        point = step(state, bar(0, 100), trade=True)
        self.assertEqual(point["events"][0]["reason"], "grid_seed_lower_wick")
        self.assertAlmostEqual(point["grid"]["next_add_price"], 99.0198)
        point = step(state, bar(1, 99, open_=98.5, high=99, low=98.5), trade=True)
        add = next(e for e in point["events"] if e["kind"] == "entry")
        self.assertEqual(add["reason"], "grid_add_layer_2")
        self.assertAlmostEqual(add["price"], 98.5 * 1.0002)
        self.assertEqual(point["grid"]["layers"], 2)
        self.assertIsNone(point["grid"]["next_add_price"])
        self.assertIn("level on touch", point["grid"]["next_add_fill_rule"])

    def test_profit_coverage_reprices_targets_once_after_bar(self):
        state = new_state({"execution_model": "price-grid", "grid_lines": 3,
                           "grid_after_exits": "fixed", "grid_after_step_pct": .5,
                           "fee_bps": 0, "slip_bps": 0})
        state["basket"] = {"id": 1, "direction": "long", "layers": 2,
                           "realized_net": 0.0, "band_pct": 1.0,
                           "coverage_active": False}
        state["positions"] = [
            {"position_id": 1, "basket_id": 1, "grid_layer": 1,
             "direction": "long", "direction_sign": 1, "entry_price": 100.,
             "qty": 1., "notional": 100., "tp_pct": 1., "entry_fee": 0.,
             "stop_price": 95., "target_price": 100.2},
            {"position_id": 2, "basket_id": 1, "grid_layer": 2,
             "direction": "long", "direction_sign": 1, "entry_price": 100.,
             "qty": 1., "notional": 100., "tp_pct": 1., "entry_fee": 0.,
             "stop_price": 99.9, "target_price": 100.8},
        ]
        state["cash"] = 800.0
        point = step(state, bar(0, 100.4, high=100.5, low=99.95, open_=100), trade=True)
        self.assertAlmostEqual(state["basket"]["realized_net"], .2)
        self.assertTrue(state["basket"]["coverage_active"])
        self.assertAlmostEqual(state["positions"][0]["target_price"], 100.4 * 1.005)
        self.assertTrue(point["grid"]["risk_covered"])
        self.assertAlmostEqual(point["grid"]["targets"][0]["price"], 100.4 * 1.005)
        # A later bar with no exits does not repeatedly move the latched target.
        target = state["positions"][0]["target_price"]
        step(state, bar(1, 100.3, high=100.4, low=100.2, open_=100.3), trade=True)
        self.assertEqual(state["positions"][0]["target_price"], target)

    def test_strategy_file_dispatch_uses_declared_warmup_and_next_bar_orders(self):
        source = '''NAME = "tiny"
WARMUP = 12
SETTINGS = {"cutoff": 999}
def on_bar(ctx):
    if ctx["bar"]["close"] > ctx["settings"]["cutoff"]:
        return [{"type": "market", "direction": "long", "notional": 50}]
    return []
'''
        config = {"bb_period": 2, "trend_period": 3, "trend_lookback": 1,
                  "strategy_file": {"filename": "tiny.py", "source": source},
                  "strategy_settings": {"cutoff": 100}}
        self.assertEqual(warmup_count(config), 12)
        state = new_state(config)
        for i in range(12):
            first = step(state, bar(i, 101), trade=True)
        self.assertEqual(first["strategy"], "tiny")
        self.assertEqual(first["events"], [])
        second = step(state, bar(12, 102), trade=True)
        self.assertEqual(second["events"][0]["kind"], "entry")
        self.assertAlmostEqual(second["events"][0]["price"], 102.0204)
        self.assertEqual(open_lots(state)[0]["lot_id"], second["events"][0]["lot_id"])

    def test_long_short_marking_and_fee_accounting(self):
        state = new_state({"capital": 1000, "size_pct": 20, "fee_bps": 10,
                           "slip_bps": 0, "direction": "long-short"})
        state["positions"] = [{"direction": "short", "direction_sign": -1,
                               "entry_price": 100., "qty": 2., "notional": 200.,
                               "tp_pct": 50., "entry_fee": .2}]
        state["cash"] = 799.8
        state["fees"] = .2
        state["history"] = [90.]
        result = summary(state)
        self.assertAlmostEqual(result["unrealized_pnl"], 19.8)
        self.assertAlmostEqual(result["equity"], 1019.8)

    def test_ambiguous_bar_stops_first_and_accounts_fee(self):
        state = new_state({"capital": 1000, "fee_bps": 10, "slip_bps": 0})
        state["positions"] = [{"direction": "long", "direction_sign": 1,
                               "entry_price": 100., "qty": 2., "notional": 200.,
                               "tp_pct": .45, "entry_fee": .2}]
        state["cash"] = 799.8
        state["fees"] = .2
        point = step(state, bar(1, 100, high=103, low=97), trade=True)
        self.assertEqual(point["events"][0]["reason"], "sl")
        self.assertAlmostEqual(point["events"][0]["price"], 98)
        self.assertEqual(summary(state)["closed_trades"], 1)
        self.assertEqual(summary(state)["open_positions"], 0)

    def test_trade_false_does_not_fill_queued_order(self):
        state = new_state({"capital": 1000})
        state["pending"].append({"direction": "long", "streak": 1, "band_pct": .5})
        point = step(state, bar(1), trade=False)
        self.assertEqual(point["events"], [])
        self.assertEqual(len(state["pending"]), 1)
        self.assertEqual(state["cash"], 1000)

    def test_entry_sizing_precedes_intrabar_exit_proceeds(self):
        state = new_state({"capital": 1000, "size_pct": 10, "fee_bps": 0, "slip_bps": 0})
        state["cash"] = 900
        state["positions"] = [{"direction": "long", "direction_sign": 1,
                               "entry_price": 100., "qty": 1., "notional": 100.,
                               "tp_pct": 1., "entry_fee": 0., "entry_ts": 0}]
        state["pending"] = [{"direction": "long", "streak": 1, "band_pct": .5,
                             "reason": "lower_wick"}]
        point = step(state, bar(1, 100, high=102, low=99), trade=True)
        entry = next(e for e in point["events"] if e["kind"] == "entry")
        self.assertAlmostEqual(entry["qty"] * entry["price"], 90)
        self.assertTrue(any(e["kind"] == "exit" for e in point["events"]))

    def test_round_trip_fees_can_turn_gross_winner_into_net_loser(self):
        state = new_state({"capital": 1000, "fee_bps": 100, "slip_bps": 0})
        state["cash"] = 899
        state["fees"] = 1
        state["positions"] = [{"direction": "long", "direction_sign": 1,
                               "entry_price": 100., "qty": 1., "notional": 100.,
                               "tp_pct": .45, "entry_fee": 1., "entry_ts": 0}]
        point = step(state, bar(1, 100, high=101, low=99), trade=True)
        closed = next(e for e in point["events"] if e["kind"] == "exit")
        self.assertGreater(closed["price"] - 100, 0)
        self.assertLess(closed["pnl"], 0)
        self.assertEqual(summary(state)["win_rate"], 0)
        self.assertLess(summary(state)["realized_pnl"], 0)

    def test_tier_two_is_uncapped_and_inspector_shares_target_levels(self):
        state = new_state({"capital": 1000, "fee_bps": 0, "slip_bps": 0})
        state["pending"] = [{"direction": "short", "streak": 2, "band_pct": 2.0,
                             "reason": "upper_wick"}]
        point = step(state, bar(1), trade=True)
        entry = next(e for e in point["events"] if e["kind"] == "entry")
        lot = open_lots(state)[0]
        self.assertEqual(entry["reason"], "upper_wick_tier2")
        self.assertAlmostEqual(lot["target"], entry["price"] * .97)
        self.assertAlmostEqual(lot["stop"], entry["price"] * 1.02)
        state = new_state({"capital": 1000, "fee_bps": 0, "slip_bps": 0})
        state["pending"] = [{"direction": "short", "streak": 2, "band_pct": 100.0,
                             "reason": "upper_wick"}]
        step(state, bar(1), trade=True)
        self.assertIsNone(open_lots(state)[0]["target"])

    def test_trade_false_warmup_does_not_advance_signal_streak(self):
        state = new_state()
        state["signal_streak"] = 3
        for i in range(205):
            point = step(state, bar(i, 100 + i * .01), trade=False)
        self.assertEqual(state["signal_streak"], 3)
        self.assertEqual(state["pending"], [])
        self.assertIsNotNone(point["sma"])


if __name__ == "__main__":
    unittest.main()
