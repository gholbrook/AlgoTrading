"""Black-box checks for session lifecycle through the same HTTP API as the UI."""
import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from gridlab.datasets import SAMPLE, load_datasets
from gridlab.server import ROOT, Workbench, make_server


class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.datasets = load_datasets(ROOT / "examples")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "state.db"
        self.rows = self.datasets[SAMPLE].rows
        self.batch = self.rows[:205]
        self.app = Workbench(self.db, self.datasets, lambda *_: self.batch,
                             strategy_dir=Path(self.temp.name) / "strategies")
        self.server = make_server(self.app, 0)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02})
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def api(self, path, data=None):
        request = Request(self.base + path, headers={"Content-Type": "application/json"},
                          data=json.dumps(data).encode() if data is not None else None)
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_value_area_experiment_api(self):
        result = self.api("/api/research/value-area", {"dataset": SAMPLE,
                          "config": {"candles": 20, "horizon": 12, "grace": 1}})
        self.assertIn("mean_duration_candles", result["summary"])
        self.assertTrue(result["episodes"])
        with urlopen(self.base + "/value-area") as response:
            self.assertIn(b"Value-area", response.read())
        with self.assertRaises(HTTPError) as error:
            self.api("/api/research/value-area", {"dataset": SAMPLE, "config": {"candles": 1}})
        self.assertEqual(error.exception.code, 400)

    def test_manual_candles_and_page(self):
        result = self.api("/api/manual/candles", {"dataset": SAMPLE})
        self.assertEqual(result["bars"], self.rows)
        self.assertEqual(result["index"], 0)
        self.assertEqual(result["fingerprint"], self.datasets[SAMPLE].fingerprint)
        with urlopen(self.base + "/manual") as response:
            self.assertIn(b"Manual", response.read())
        with self.assertRaises(HTTPError) as error:
            self.api("/api/manual/candles", {"dataset": "missing"})
        self.assertEqual(error.exception.code, 400)

    def test_rehearsal_matches_same_warmed_batch_across_restart(self):
        rid = self.api("/api/rehearsals", {"dataset": SAMPLE})["id"]
        url = f"/api/runs/{rid}"
        self.assertEqual(self.api(url)["run"]["bars"], 205)
        self.api(url + "/advance", {"count": 12})
        self.app = Workbench(self.db, self.datasets)
        self.server.workbench = self.app
        self.api(url + "/advance", {"count": 500})
        self.api(url + "/advance", {"count": 500})
        result = self.api(url)
        self.assertEqual(result["run"]["status"], "completed")
        self.assertEqual(result["run"]["bars"], len(self.rows))
        self.assertTrue(self.api(url + "/audit", {})["match"])
        reference = self.app.store.backtest("BTC/USD", "5m", result["run"]["config"],
                                            "matching window", self.rows[:205], self.rows[205:])
        batch = self.app.store.detail(reference)
        self.assertEqual(result["summary"], batch["summary"])
        self.assertEqual(result["points"], batch["points"])
        self.assertGreater(result["summary"]["closed_trades"], 0)

    def test_live_bootstrap_recovery_duplicates_gap_and_stop(self):
        rid = self.api("/api/paper", {"market": "BTC/USD", "timeframe": "5m"})["id"]
        url = f"/api/runs/{rid}"
        self.assertEqual(self.api(url)["summary"]["open_positions"], 0)
        self.app.poll()
        self.assertTrue(all(p["warmup"] and not p["events"] for p in self.api(url)["points"]))
        self.batch = self.rows[205:240]
        self.app.poll()
        before = self.api(url)
        self.app.poll()
        self.assertEqual(self.api(url)["summary"], before["summary"])
        self.assertEqual(self.api(url)["run"]["bars"], 240)
        self.batch = self.rows[242:245]
        self.app.poll()
        blocked = self.api(url)["run"]
        self.assertTrue(blocked["error"])
        self.assertEqual(blocked["last_ts"], self.rows[239]["ts"])
        self.server.workbench = self.app = Workbench(self.db, self.datasets, lambda *_: self.rows[240:250])
        self.app.poll()
        recovered = self.api(url)["run"]
        self.assertEqual(recovered["error"], "")
        self.assertEqual(recovered["bars"], 250)
        self.assertTrue(self.api(url + "/audit", {})["match"])
        self.api(url + "/stop", {})
        self.batch = self.rows[250:]
        self.app.poll()
        self.assertEqual(self.api(url)["run"]["status"], "stopped")
        self.assertEqual(self.api(url)["run"]["bars"], 250)

    def test_long_backtest_can_replay_its_first_and_last_candles(self):
        rows = [dict(self.rows[i % len(self.rows)], ts=self.rows[0]["ts"] + 300 * i)
                for i in range(1800)]
        rid = self.app.store.backtest("BTC/USD", "5m", {}, "test stream", [], rows)
        detail = self.api(f"/api/runs/{rid}")
        self.assertEqual([p["ts"] for p in detail["points"]], [bar["ts"] for bar in rows])

    def test_filters_and_input_failures(self):
        context = self.api("/api/context")
        dataset = context["datasets"][0]
        start = dataset["end"][:10]
        result = self.api("/api/backtests", {"dataset": SAMPLE, "start": start, "end": start,
                                             "config": {"capital": 1000}})
        detail = self.api(f"/api/runs/{result['id']}")
        self.assertEqual(detail["run"]["status"], "completed")
        self.assertTrue(any(p["warmup"] for p in detail["points"]))
        self.assertEqual(detail["run"]["config"]["capital"], 1000)
        for payload in ({"dataset": "unknown"}, {"dataset": SAMPLE, "start": "2999-01-01"},
                        {"dataset": SAMPLE, "config": {"slip_bps": 10000}}):
            with self.assertRaises(HTTPError) as err:
                self.api("/api/backtests", payload)
            self.assertEqual(err.exception.code, 400)
        self.assertEqual(len(self.api("/api/context")["runs"]), 2)

    def test_zero_indicator_warmup_selects_no_preceding_rows(self):
        data = self.datasets[SAMPLE]
        start = datetime.fromtimestamp(data.rows[20]["ts"], timezone.utc).date().isoformat()
        warmup, selected = data.select(start=start, warmup_count=0)
        self.assertEqual(warmup, [])
        self.assertTrue(selected)

    def test_strategy_file_http_lifecycle_and_optimistic_lock(self):
        source = '"""Short description."""\nNAME = "HTTP strategy"\nSETTINGS = {"level": 2}\ndef on_bar(ctx):\n    return []\n'
        saved = self.api("/api/strategy-files", {"filename": "http_test.py", "source": source})["file"]
        self.assertEqual(saved["description"], "Short description.")
        self.assertEqual(saved["settings"], {"level": 2})
        self.assertEqual(self.api("/api/strategy-files")["files"][0]["hash"], saved["hash"])
        resolved = self.app.config({"strategy_file": "http_test.py", "strategy_hash": saved["hash"],
                                    "strategy_settings": {"level": 3}}, "BTC/USD")
        self.assertEqual(resolved["strategy_file"]["source"], source)
        self.assertEqual(resolved["strategy_settings"], {"level": 3})
        with self.assertRaises(ValueError):
            self.app.config({"config": {"strategy_file": {"source": source}}}, "BTC/USD")
        updated = self.api("/api/strategy-files", {"filename": "http_test.py", "source": source + "# revised\n",
                                                    "previous_hash": saved["hash"]})["file"]
        self.assertNotEqual(updated["hash"], saved["hash"])
        request = Request(self.base + "/api/strategy-files", headers={"Content-Type": "application/json"},
                          data=json.dumps({"filename": "http_test.py", "source": source,
                                           "previous_hash": saved["hash"]}).encode())
        with self.assertRaises(HTTPError) as err:
            urlopen(request, timeout=5)
        self.assertEqual(err.exception.code, 409)
