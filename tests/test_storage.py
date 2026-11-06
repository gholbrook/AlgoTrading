"""Integration checks for durable and replayable run storage."""

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from gridlab import engine
from gridlab import market
from gridlab.market import validate_bar
from gridlab.storage import GapError, Store, StoreError


def candles(count, start=1_750_000_000, interval=3600):
    result = []
    for i in range(count):
        price = 100 + i * 0.03
        result.append({"ts": start + i * interval, "open": price,
                       "high": price + 0.2, "low": price - 0.2,
                       "close": price + 0.05, "volume": 10.0})
    return result


class StoreIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "runs.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_strategy_versions_are_immutable_and_optimistically_locked(self):
        first = self.store.save_strategy("Momentum", {"fee_bps": 8})
        self.assertEqual(first["version"], 1)
        second = self.store.save_strategy("Momentum v2", {"fee_bps": 9},
                                          first["id"], first["version"])
        self.assertEqual(second["version"], 2)
        self.assertEqual(self.store.strategies(), [second])
        with self.assertRaises(StoreError):
            self.store.save_strategy("stale", {}, first["id"], first["version"])
        self.assertEqual(self.store.save_strategy("historical ref", first["config"])["version"], 1)

    def test_runs_keep_saved_strategy_version_and_require_matching_config(self):
        saved = self.store.save_strategy("Momentum", {"fee_bps": 8})
        run_id = self.store.create("paper", "BTC/USD", "1h", saved["config"], "fixture",
                                   strategy_id=saved["id"], strategy_version=saved["version"])
        run = self.store.get(run_id)
        self.assertEqual((run["strategy_id"], run["strategy_version"]), (saved["id"], 1))
        with self.assertRaises(ValueError):
            self.store.create("paper", "BTC/USD", "1h", {}, "fixture",
                              strategy_id=saved["id"], strategy_version=saved["version"])
        with self.assertRaises(ValueError):
            self.store.create("paper", "BTC/USD", "1h", {}, "fixture",
                              strategy_id=saved["id"])

    def test_legacy_partial_config_audits_after_default_normalization(self):
        run_id = self.store.create("paper", "BTC/USD", "1h", {}, "fixture")
        self.store.ingest(run_id, candles(3))
        with closing(sqlite3.connect(self.path)) as db:
            config = json.loads(db.execute("SELECT config_json FROM runs WHERE id=?", (run_id,)).fetchone()[0])
            state = json.loads(db.execute("SELECT state_json FROM runs WHERE id=?", (run_id,)).fetchone()[0])
            # Mimic an older persisted schema that omitted later-added settings.
            for key in ("direction", "trend_period", "trend_lookback"):
                config.pop(key, None)
                state["config"].pop(key, None)
            db.execute("UPDATE runs SET config_json=?,state_json=? WHERE id=?",
                       (json.dumps(config), json.dumps(state), run_id))
            db.commit()
        self.assertTrue(self.store.audit(run_id)["match"])

    def test_run_metadata_includes_saved_strategy_name(self):
        saved = self.store.save_strategy("Named strategy", {})
        rid = self.store.create("paper", "BTC/USD", "1h", saved["config"], "fixture",
                                strategy_id=saved["id"], strategy_version=saved["version"])
        self.assertEqual(self.store.get(rid)["strategy_name"], "Named strategy")

    def test_run_summaries_omit_file_source_but_detail_keeps_snapshot(self):
        source = 'WARMUP = 0\ndef on_bar(ctx):\n    return []\n'
        run_id = self.store.create("paper", "BTC/USD", "1h", {
            "strategy_file": {"filename": "fixture.py", "source": source, "hash": "abc"},
            "strategy_settings": {},
        }, "fixture")
        self.assertNotIn("source", self.store.list()[0]["config"]["strategy_file"])
        self.assertEqual(self.store.get(run_id)["config"]["strategy_file"]["source"], source)

    def test_paper_checkpoint_idempotency_restart_and_audit(self):
        original_config = {"direction": "long-short", "fee_bps": 8}
        run_id = self.store.create("paper", "BTC/USD", "1h", original_config, "fixture")
        original_config["fee_bps"] = 999
        data = candles(207)
        self.assertEqual(self.store.ingest(run_id, data[:205], warmup=True), 205)
        self.assertEqual(self.store.ingest(run_id, data[:205], warmup=True), 0)
        self.assertEqual(self.store.ingest(run_id, data[205:]), 2)
        checkpoint = self.store.get(run_id)
        self.assertEqual(checkpoint["config"]["fee_bps"], 8.0)
        self.assertEqual(checkpoint["last_ts"], data[-1]["ts"])
        self.assertEqual(checkpoint["bars"], 207)

        reopened = Store(self.path)
        self.assertEqual(reopened.get(run_id)["state"], checkpoint["state"])
        detail = reopened.detail(run_id)
        self.assertEqual(len(detail["points"]), 207)
        self.assertTrue(detail["points"][0]["warmup"])
        self.assertFalse(detail["points"][-1]["warmup"])
        self.assertEqual(detail["positions"], engine.open_lots(checkpoint["state"]))
        self.assertTrue(reopened.audit(run_id)["match"])

    def test_gaps_and_conflicting_duplicates_are_rejected_atomically(self):
        run_id = self.store.create("paper", "BTC/USD", "5m", {}, "fixture")
        first = candles(1, interval=300)
        self.store.ingest(run_id, first)
        with self.assertRaises(GapError):
            self.store.ingest(run_id, [candles(1, start=first[0]["ts"] + 600, interval=300)[0]])
        self.assertEqual(self.store.get(run_id)["bars"], 1)
        changed = dict(first[0], volume=first[0]["volume"] + 1)
        with self.assertRaises(StoreError):
            self.store.ingest(run_id, [changed])
        self.assertEqual(self.store.get(run_id)["bars"], 1)

    def test_concurrent_identical_batches_are_idempotent(self):
        run_id = self.store.create("paper", "BTC/USD", "1h", {}, "fixture")
        batch = candles(80)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.ingest(run_id, batch), range(2)))
        self.assertEqual(sorted(results), [0, 80])
        self.assertEqual(self.store.get(run_id)["bars"], 80)
        self.assertTrue(self.store.audit(run_id)["match"])

    def test_audit_detects_a_tampered_decision_record(self):
        run_id = self.store.create("paper", "BTC/USD", "1h", {}, "fixture")
        self.store.ingest(run_id, candles(4))
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("BEGIN")
            db.execute("UPDATE points SET point_json='{}' WHERE run_id=? AND ts=?",
                       (run_id, candles(4)[1]["ts"]))
            db.commit()
        audit = self.store.audit(run_id)
        self.assertFalse(audit["match"])
        self.assertFalse(audit["decisions_match"])
        self.assertTrue(audit["checkpoint_match"])
        self.assertEqual(audit["first_mismatch_ts"], candles(4)[1]["ts"])

    def test_audit_checks_run_bar_count_and_cursor_metadata(self):
        run_id = self.store.create("paper", "BTC/USD", "1h", {}, "fixture")
        data = candles(3)
        self.store.ingest(run_id, data)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("BEGIN")
            db.execute("UPDATE runs SET bars=2,last_ts=? WHERE id=?",
                       (data[-2]["ts"], run_id))
            db.commit()
        audit = self.store.audit(run_id)
        self.assertFalse(audit["match"])
        self.assertFalse(audit["metadata_match"])
        self.assertEqual(audit["first_mismatch_ts"], data[-1]["ts"])

    def test_failed_backtest_is_removed_and_completed_backtest_archives(self):
        with self.assertRaises(GapError):
            self.store.backtest("BTC/USD", "1h", {}, "fixture", [],
                                [candles(1)[0], candles(1, start=1_750_007_200)[0]])
        self.assertEqual(self.store.list(), [])
        run_id = self.store.backtest("BTC/USD", "1h", {}, "fixture",
                                     candles(205), candles(2, start=1_750_738_000))
        self.assertEqual(self.store.get(run_id)["status"], "completed")
        self.assertEqual(self.store.active(), [])

    def test_health_state_and_paper_finish_status(self):
        run_id = self.store.create("paper", "BTC/USD", "5m", {}, "fixture")
        self.store.health(run_id, "temporary timeout")
        self.assertEqual(self.store.get(run_id)["error"], "temporary timeout")
        self.assertEqual(self.store.detail(run_id)["points"], [])
        self.store.health(run_id, "")
        self.assertEqual(self.store.get(run_id)["error"], "")
        self.assertEqual(len(self.store.active()), 1)
        self.store.finish(run_id)
        self.assertEqual(self.store.get(run_id)["status"], "stopped")

    def test_rehearsal_creation_commits_warmup_as_running_replay_state(self):
        warmup = candles(205)
        run_id = self.store.create("rehearsal", "BTC/USD", "1h", {}, "fixture",
                                   warmup_bars=warmup)
        run = self.store.get(run_id)
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["bars"], 205)
        self.assertEqual(run["last_ts"], warmup[-1]["ts"])
        self.assertEqual(self.store.detail(run_id)["total_points"], 205)
        self.assertTrue(self.store.audit(run_id)["match"])
        self.store.finish(run_id, completed=True)
        self.assertEqual(self.store.get(run_id)["status"], "completed")

    def test_ingest_can_atomically_complete_final_rehearsal_batch(self):
        warmup = candles(205)
        run_id = self.store.create("rehearsal", "BTC/USD", "1h", {}, "fixture",
                                   warmup_bars=warmup)
        last = candles(1, start=warmup[-1]["ts"] + 3600)[0]
        self.store.ingest(run_id, [last], completed=True)
        self.assertEqual(self.store.get(run_id)["status"], "completed")
        self.assertEqual(self.store.get(run_id)["bars"], 206)

    def test_health_after_stop_is_a_noop(self):
        run_id = self.store.create("paper", "BTC/USD", "5m", {}, "fixture")
        self.store.finish(run_id)
        before = self.store.get(run_id)
        self.store.health(run_id, "late poll error")
        after = self.store.get(run_id)
        self.assertEqual(after["last_poll"], before["last_poll"])
        self.assertEqual(after["error"], before["error"])

    def test_bar_validation(self):
        normalized = validate_bar({"ts": "1750000000", "open": "10", "high": "11",
                                   "low": "9", "close": "10.5", "volume": "0"})
        self.assertEqual(normalized["ts"], 1750000000)
        with self.assertRaises(ValueError):
            validate_bar({"ts": 1, "open": 10, "high": 9, "low": 8, "close": 10, "volume": 1})


class KrakenAdapterTests(unittest.TestCase):
    def _response(self, rows, error=None):
        payload = {"error": error or [], "result": {"XXBTZUSD": rows, "last": "cursor"}}
        return io.BytesIO(json.dumps(payload).encode())

    @staticmethod
    def _row(ts, price=10):
        return [ts, str(price), str(price + 1), str(price - 1), str(price), "0", "3", 4]

    def test_excludes_forming_row_and_returns_closed_rows(self):
        base = 1_750_000_200  # aligned to 5-minute UTC boundaries
        response = self._response([self._row(base), self._row(base + 300), self._row(base + 600)])
        with patch.object(market, "urlopen", return_value=response), patch.object(market.time, "time", return_value=base + 610):
            bars = market.fetch_closed("BTC/USD", "5m")
        self.assertEqual([b["ts"] for b in bars], [base, base + 300])

    def test_since_repeat_returns_empty_but_stale_cursor_fails(self):
        base = 1_750_000_200
        response = self._response([self._row(base), self._row(base + 300)])
        with patch.object(market, "urlopen", return_value=response), patch.object(market.time, "time", return_value=base + 610):
            self.assertEqual(market.fetch_closed("BTC/USD", "5m", since=base), [])
        response = self._response([self._row(base + 600)])  # only forming row
        with patch.object(market, "urlopen", return_value=response), patch.object(market.time, "time", return_value=base + 1201):
            with self.assertRaisesRegex(RuntimeError, "stale"):
                market.fetch_closed("BTC/USD", "5m", since=base)

    def test_stale_bootstrap_and_exchange_errors_fail_closed(self):
        base = 1_750_000_200
        response = self._response([self._row(base), self._row(base + 300)])
        with patch.object(market, "urlopen", return_value=response), patch.object(market.time, "time", return_value=base + 1201):
            with self.assertRaisesRegex(RuntimeError, "stale"):
                market.fetch_closed("BTC/USD", "5m")
        response = self._response([], ["EGeneral:Temporary lockout"])
        with patch.object(market, "urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "Temporary lockout"):
                market.fetch_closed("BTC/USD", "5m")


if __name__ == "__main__":
    unittest.main()
