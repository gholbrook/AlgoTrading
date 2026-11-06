"""Behavioral tests for trusted file strategies and their paper accounting."""

import json
import unittest
from pathlib import Path

from gridlab import engine
from gridlab.file_engine import file_warmup, load_program, step_file

STRATEGIES = Path(__file__).resolve().parents[1] / "strategies"


def bar(ts, open_=100.0, high=100.0, low=100.0, close=100.0):
    return {"ts": ts, "open": open_, "high": high, "low": low,
            "close": close, "volume": 10.0}


def source(body, *, name="Sample", warmup=0):
    lines = [f"NAME = {name!r}", f"WARMUP = {warmup}", "SETTINGS = {}", "def on_bar(ctx):"]
    lines.extend("    " + line for line in body.splitlines())
    return "\n".join(lines) + "\n"


def state_for(code, *, filename="sample_strategy.py", **config):
    state = engine.new_state({"capital": 1000, "size_pct": 10, "fee_bps": 0,
                              "slip_bps": 0, **config})
    state["config"]["strategy_file"] = {"filename": filename, "source": code}
    state["config"]["strategy_settings"] = {}
    return state


class FileProgramTests(unittest.TestCase):
    def test_load_program_metadata_and_warmup(self):
        program = load_program(STRATEGIES / "trend_cross.py")
        self.assertEqual(program.name, "Trend Cross")
        self.assertEqual(program.warmup, 31)
        self.assertIn("fast_period", program.settings)
        config = {"strategy_file": {"filename": "trend_cross.py", "source": program.source}}
        self.assertEqual(file_warmup(config), 31)

    def test_warmup_suppresses_callbacks_and_records_closed_candles(self):
        code = source("ctx['memory']['called'] = ctx['memory'].get('called', 0) + 1\n"
                      "return [{'type': 'market', 'direction': 'long', 'notional': 100}]",
                      warmup=2)
        state = state_for(code)
        first = step_file(state, bar(1), trade=True)
        self.assertFalse(first["strategy_ready"])
        self.assertEqual(first["orders"], [])
        self.assertEqual(state["strategy_memory"], {})
        second = step_file(state, bar(2), trade=True)
        self.assertTrue(second["strategy_ready"])
        self.assertEqual(len(second["orders"]), 1)
        self.assertEqual(state["strategy_memory"]["called"], 1)

    def test_multiple_fills_have_unique_lot_ids_and_fee_accounting(self):
        code = source("if ctx['memory'].get('sent'):\n    return []\n"
                      "ctx['memory']['sent'] = True\n"
                      "return [{'type':'market','direction':'long','notional':100,'label':'first'},"
                      " {'type':'market','direction':'short','notional':100,'label':'second'}]")
        state = state_for(code, fee_bps=10, slip_bps=5)
        step_file(state, bar(1), trade=True)
        point = step_file(state, bar(2, high=101, low=99), trade=True)
        entries = [event for event in point["events"] if event["kind"] == "entry"]
        self.assertEqual([event["lot_id"] for event in entries], [1, 2])
        self.assertEqual([event["label"] for event in entries], ["first", "second"])
        self.assertEqual(len(state["positions"]), 2)
        self.assertGreater(state["fees"], 0)
        json.dumps(state)

    def test_limit_entry_respects_price_and_defers_pre_fill_target(self):
        code = source("if ctx['memory'].get('sent'):\n    return []\n"
                      "ctx['memory']['sent'] = True\n"
                      "return [{'type':'limit','direction':'long','price':100,'notional':100,"
                      "'target_price':100.5}]")
        state = state_for(code, slip_bps=100)
        step_file(state, bar(1), trade=True)
        filled = step_file(state, bar(2, open_=101, high=102, low=99, close=101), trade=True)
        self.assertEqual([event["kind"] for event in filled["events"]], ["entry"])
        self.assertLessEqual(filled["events"][0]["price"], 100)
        exited = step_file(state, bar(3, open_=100, high=101, low=99, close=100.8), trade=True)
        exit_event = next(event for event in exited["events"] if event["kind"] == "exit")
        self.assertEqual(exit_event["reason"], "target")
        self.assertGreaterEqual(exit_event["price"], 100.5)
        self.assertEqual(exit_event["lot_id"], 1)

    def test_intrabar_limit_entry_can_hit_stop(self):
        code = source("if ctx['memory'].get('sent'):\n    return []\n"
                      "ctx['memory']['sent'] = True\n"
                      "return [{'type':'limit','direction':'long','price':100,'notional':100,"
                      "'stop_price':99,'target_price':101}]")
        state = state_for(code)
        step_file(state, bar(1), trade=True)
        stopped = step_file(state, bar(2, open_=101, high=102, low=98, close=100), trade=True)
        self.assertEqual([event["reason"] for event in stopped["events"]],
                         ["strategy_entry", "stop"])
        self.assertEqual(state["positions"], [])

    def test_explicit_limit_exit_expires_then_refills_next_bar(self):
        code = source(
            "if not ctx['positions'] and not ctx['memory'].get('entered'):\n"
            "    ctx['memory']['entered'] = True\n"
            "    return [{'type':'market','direction':'long','notional':100,'stop_price':99}]\n"
            "if ctx['positions']:\n"
            "    return [{'type':'limit','action':'exit','lot_id':ctx['positions'][0]['lot_id'],'price':105}]\n"
            "return []", name="Limit exit")
        state = state_for(code)
        step_file(state, bar(1), trade=True)
        step_file(state, bar(2), trade=True)
        missed = step_file(state, bar(3, open_=100, high=104, low=99.5, close=102), trade=True)
        self.assertEqual(missed["events"], [])
        filled = step_file(state, bar(4, open_=103, high=106, low=102, close=105), trade=True)
        exit_event = next(event for event in filled["events"] if event["kind"] == "exit")
        self.assertEqual(exit_event["reason"], "strategy_exit")
        self.assertGreaterEqual(exit_event["price"], 105)

    def test_marketable_limit_exit_at_open_precedes_intrabar_stop(self):
        code = source(
            "if not ctx['positions'] and not ctx['memory'].get('entered'):\n"
            "    ctx['memory']['entered'] = True\n"
            "    return [{'type':'market','direction':'long','notional':100,'stop_price':99}]\n"
            "if ctx['positions']:\n"
            "    return [{'type':'limit','action':'exit','lot_id':ctx['positions'][0]['lot_id'],'price':100.5}]\n"
            "return []", name="Marketable limit")
        state = state_for(code)
        step_file(state, bar(1), trade=True)
        step_file(state, bar(2), trade=True)
        result = step_file(state, bar(3, open_=101, high=102, low=98, close=100), trade=True)
        exits = [event for event in result["events"] if event["kind"] == "exit"]
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0]["reason"], "strategy_exit")
        self.assertGreaterEqual(exits[0]["price"], 100.5)

    def test_callback_errors_include_filename_and_candle(self):
        code = source("raise RuntimeError('boom')", name="broken")
        state = state_for(code, filename="bad_strategy.py")
        with self.assertRaisesRegex(ValueError, r"bad_strategy.py on_bar failed at candle 123: boom"):
            step_file(state, bar(123), trade=True)

    def test_trend_cross_example_metadata(self):
        program = load_program(STRATEGIES / "trend_cross.py")
        self.assertEqual(program.name, "Trend Cross")
        self.assertEqual(program.warmup, 31)


if __name__ == "__main__":
    unittest.main()
