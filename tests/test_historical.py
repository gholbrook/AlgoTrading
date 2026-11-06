"""Offline tests for bounded Coinbase history import and registration."""

import io
import json
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

from gridlab import historical
from gridlab import server as server_module
from gridlab.datasets import SAMPLE, load_datasets, load_history_datasets
from gridlab.historical import download_snapshot
from gridlab.server import ROOT, Workbench, make_server


START = 1_704_067_200  # 2024-01-01T00:00:00Z


def coinbase_row(ts):
    price = 100 + (ts - START) / 100000
    return [ts, price - 1, price + 1, price, price + 0.2, 3.5]


class CoinbaseHistoryTests(unittest.TestCase):
    def opener_for(self, *, omit_ts=None):
        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            query = parse_qs(urlsplit(request.full_url).query)
            start = int(datetime.fromisoformat(query["start"][0].replace("Z", "+00:00")).timestamp())
            end = int(datetime.fromisoformat(query["end"][0].replace("Z", "+00:00")).timestamp())
            candles = []
            ts = start - 300  # Coinbase may include one candle before requested start.
            while ts < end:
                if ts != omit_ts:
                    candles.append(coinbase_row(ts))
                ts += 300
            return io.BytesIO(json.dumps(candles).encode())

        return opener, calls

    def test_pages_and_persists_immutable_dataset_with_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            opener, calls = self.opener_for()
            with patch.object(historical, "PAGE_SIZE", 2):
                path, metadata = download_snapshot("ETH/USD", "5m", "2024-01-01", "2024-01-01",
                                                   temp, now=START + 5 * 300, opener=opener)
            self.assertEqual(len(calls), 3)
            self.assertEqual(metadata["bars"], 5)
            self.assertEqual(metadata["venue"], "Coinbase")
            self.assertEqual(metadata["start"], "2024-01-01T00:00:00Z")
            self.assertEqual(metadata["end"], "2024-01-01T00:20:00Z")
            original = path.read_bytes()
            reloaded = load_history_datasets(temp)
            dataset = reloaded[metadata["id"]]
            self.assertEqual(dataset.describe()["venue"], "Coinbase")
            self.assertEqual(len(dataset.rows), 5)
            self.assertIn("Coinbase Exchange", dataset.source())

            # Repeating the exact same range is idempotent and never replaces it.
            again, _ = download_snapshot("ETH/USD", "5m", "2024-01-01", "2024-01-01",
                                         temp, now=START + 5 * 300, opener=self.opener_for()[0])
            self.assertEqual(again, path)
            self.assertEqual(path.read_bytes(), original)

    def test_forming_bar_is_excluded_and_missing_intervals_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            opener, _ = self.opener_for()
            path, metadata = download_snapshot("ETH/USD", "5m", "2024-01-01", "2024-01-01",
                                               temp, now=START + 4 * 300 + 120, opener=opener)
            self.assertEqual(metadata["bars"], 4)
            self.assertNotIn("2024-01-01T00:20:00Z", path.read_text())

        with tempfile.TemporaryDirectory() as temp:
            missing_opener, _ = self.opener_for(omit_ts=START + 600)
            with self.assertRaisesRegex(RuntimeError, "missing 5m candle"):
                download_snapshot("ETH/USD", "5m", "2024-01-01", "2024-01-01",
                                  temp, now=START + 5 * 300, opener=missing_opener)
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_finder_skips_incomplete_end_day_across_pages(self):
        day = 86400
        gaps = {START + 3 * day - 300, START + 300}
        def page(market, timeframe, start, end, opener):
            return set(range(start, end, 300)) - gaps
        with patch.object(historical, "_page", side_effect=page), patch.object(historical.time, "sleep"):
            result = historical.coverage_start("ETH/BTC", "5m", "2024-01-03", now=START + 3 * day)
        self.assertEqual((result["start"], result["end"]), ("2024-01-02", "2024-01-02"))
        self.assertEqual(result["skipped_gap"], "2024-01-03T23:55:00Z")

    def test_finder_stops_at_limit_when_every_day_is_incomplete(self):
        with patch.object(historical, "_page", return_value={}) as page, patch.object(historical, "MAX_BARS", 576), patch.object(historical.time, "sleep"):
            result = historical.coverage_start("ETH/BTC", "5m", "2024-01-02", now=START + 172800)
        self.assertIsNone(result["start"])
        self.assertEqual(result["days_limit"], 2)
        self.assertEqual(page.call_args_list[0].args[2], START + 172800 - 299 * 300)

    def test_range_limits_and_exchange_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            opener, calls = self.opener_for()
            with self.assertRaisesRegex(ValueError, "30,000-bar"):
                download_snapshot("ETH/USD", "5m", "2020-01-01", "2021-01-01",
                                  temp, now=START, opener=opener)
            self.assertEqual(calls, [])
            with self.assertRaises(ValueError):
                download_snapshot("ETH/USD", "15m", "2024-01-01", "2024-01-01", temp, now=START)

            def rejected(request, timeout):
                return io.BytesIO(b'{"message":"product not found"}')

            with self.assertRaisesRegex(RuntimeError, "product not found"):
                download_snapshot("ETH/USD", "5m", "2024-01-01", "2024-01-01",
                                  temp, now=START + 300, opener=rejected)

    def test_handshake_timeout_retries_same_page_and_stops_after_four_attempts(self):
        timeout = URLError(TimeoutError("The handshake operation timed out"))
        opener = Mock(side_effect=[timeout, io.BytesIO(b"[]")])
        with patch.object(historical.time, "sleep"):
            self.assertEqual(historical._request_json("https://example.invalid", opener), [])
            self.assertEqual(opener.call_args_list[0], opener.call_args_list[1])
            stalled = Mock(side_effect=timeout)
            with self.assertRaisesRegex(RuntimeError, "after 4 attempts"):
                historical._request_json("https://example.invalid", stalled)
            self.assertEqual(stalled.call_count, 4)

    def test_rate_limit_retries_are_bounded(self):
        attempts = []

        def throttled_then_ok(request, timeout):
            attempts.append(request.full_url)
            if len(attempts) <= 3:
                raise HTTPError(request.full_url, 429, "rate limited", {}, None)
            return io.BytesIO(b"[]")

        with patch.object(historical.time, "sleep") as sleep:
            self.assertEqual(historical._request_json("https://example.invalid", throttled_then_ok), [])
        self.assertEqual(len(attempts), 4)
        self.assertEqual(sleep.call_count, 3)

        def always_throttled(request, timeout):
            attempts.append(request.full_url)
            raise HTTPError(request.full_url, 429, "rate limited", {}, None)

        attempts.clear()
        with patch.object(historical.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "429"):
                historical._request_json("https://example.invalid", always_throttled)
        self.assertEqual(len(attempts), 4)

    def test_binance_download_preserves_five_minute_candles_and_venue(self):
        def market_data(request, timeout):
            url = urlsplit(request.full_url)
            self.assertEqual(url.hostname, "data-api.binance.vision")
            params = parse_qs(url.query)
            self.assertEqual(params["symbol"], ["ETHBTC"])
            if url.path.endswith("exchangeInfo"):
                payload = {"symbols": [{"symbol": "ETHBTC", "baseAsset": "ETH", "quoteAsset": "BTC", "status": "TRADING"}]}
            else:
                self.assertEqual(params["interval"], ["5m"])
                first = int(params["startTime"][0])
                last = int(params["endTime"][0])
                payload = [[ts, "0.029", "0.030", "0.028", "0.0291", "2", ts + 299999,
                            "0.058", 2, "1", "0.029", "0"] for ts in range(first, last + 1, 300000)]
            return io.BytesIO(json.dumps(payload).encode())
        with tempfile.TemporaryDirectory() as temp:
            path, meta = download_snapshot("ETH/BTC", "5m", "2024-01-01", "2024-01-01",
                                          temp, now=START + 600, opener=market_data, venue="Binance")
            dataset = load_history_datasets(temp)[meta["id"]]
            self.assertEqual(dataset.describe()["venue"], "Binance")
            self.assertEqual([row["ts"] for row in dataset.rows], [START, START + 300])
            self.assertIn("Binance", dataset.source())

    def test_history_http_route_returns_selectable_persisted_dataset(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "state.sqlite3"
            datasets = load_datasets(ROOT / "examples")
            self.assertIn(SAMPLE, datasets)
            app = Workbench(database, datasets, lambda *_: [])
            opener, _ = self.opener_for()

            def offline_download(market, timeframe, start, end, directory, *, venue):
                self.assertEqual(venue, "Coinbase")
                return download_snapshot(market, timeframe, start, end, directory,
                                         now=START + 5 * 300, opener=opener)

            with patch.object(server_module, "download_snapshot", side_effect=offline_download):
                with make_server(app, 0) as httpd:
                    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                    thread.start()
                    try:
                        body = json.dumps({"market": "ETH/USD", "timeframe": "5m",
                                           "start": "2024-01-01", "end": "2024-01-01"}).encode()
                        request = Request(f"http://127.0.0.1:{httpd.server_port}/api/history",
                                          data=body, headers={"Content-Type": "application/json"})
                        with urlopen(request, timeout=5) as response:
                            payload = json.load(response)
                        self.assertEqual(payload["dataset"]["venue"], "Coinbase")
                        dataset_id = payload["dataset"]["id"]
                    finally:
                        httpd.shutdown()
                        thread.join(timeout=5)

            restarted = Workbench(database, datasets, lambda *_: [])
            recovered = next(d for d in restarted.context()["datasets"] if d["id"] == dataset_id)
            self.assertEqual(recovered["market"], "ETH/USD")
            self.assertEqual(recovered["bars"], 5)
            self.assertEqual(recovered["venue"], "Coinbase")


if __name__ == "__main__":
    unittest.main()
