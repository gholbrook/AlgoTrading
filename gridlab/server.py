"""Gridline local research and paper execution workbench. Run with python3 server.py."""
import argparse
import json
import logging
import re
import sys
import threading
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
# A direct script launch puts gridlab/ itself on the import path.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(ROOT))

from tools.replay_daily import read_closes
from gridlab.replay_history import replay_prefix
from gridlab.usd_volume import quote_usd_rates, usd_volume
from research.value_area_stays import run_experiment
from gridlab import engine
from tools.bulk_history import collect_month
from gridlab.datasets import Dataset, LazyDataset, CombinedDataset, SAMPLE, combine_datasets, load_bulk_datasets, load_datasets, load_history_datasets
from gridlab.historical import coverage_start, download_snapshot
from gridlab.market import fetch_closed
from gridlab.storage import Store, StoreError
from gridlab.strategy_files import StrategyFiles, StrategyFileError, StrategyFileConflict, StrategyFileNotFound

MARKETS = ("BTC/USD", "ETH/BTC", "SOL/USDT")
TIMEFRAMES = ("5m", "1h")
POLL_SECONDS = 30


class Workbench:
    def __init__(self, database, datasets, fetch=fetch_closed, strategy_dir=None):
        first_launch = not Path(database).exists() or Path(database).stat().st_size == 0
        self.store = Store(database)
        self.strategy_files = StrategyFiles(strategy_dir or ROOT / "strategies")
        self.history_dir = Path(database).resolve().parent / "history"
        self.dataset_lock = threading.RLock()
        self.original_datasets = dict(datasets)
        self.original_datasets.update(load_history_datasets(self.history_dir))
        self.datasets = combine_datasets(self.original_datasets)
        self.dataset_archive = {**self.original_datasets, **self.datasets}
        self._restore_combined_datasets()
        self._save_combined_datasets()
        self.history_signature = self._history_signature()
        self.fetch = fetch
        self.wake = threading.Event()
        self.shutdown = threading.Event()
        self.advance_lock = threading.Lock()
        self.sample_id = None
        sample = datasets.get(SAMPLE)
        if sample is None:
            return
        source = sample.source() + " · sample"
        old = next((r for r in self.store.list() if r["source"] == source and r["status"] == "completed"), None)
        self.sample_id = old["id"] if old else None
        if first_launch:
            self.sample_id = self.store.backtest(sample.market, sample.timeframe, engine.validate_config({}),
                                                 source, [], sample.rows)

    def _restore_combined_datasets(self):
        manifest = self.history_dir / "combined_catalog.json"
        if not manifest.exists():
            return
        try:
            for ids in json.loads(manifest.read_text()):
                members = {key: self.original_datasets[key] for key in ids}
                self.dataset_archive.update(combine_datasets(members))
        except (ValueError, TypeError, KeyError, OSError) as exc:
            logging.warning("Cannot restore combined dataset catalog: %s", exc)

    def _save_combined_datasets(self):
        combinations = [list(member.id for member in data.members)
                        for data in self.dataset_archive.values() if hasattr(data, "members")]
        if combinations:
            self.history_dir.mkdir(parents=True, exist_ok=True)
            manifest = self.history_dir / "combined_catalog.json"
            content = json.dumps(combinations)
            if not manifest.exists() or manifest.read_text() != content:
                temporary = manifest.with_suffix(".tmp")
                temporary.write_text(content)
                temporary.replace(manifest)

    def _history_signature(self):
        return tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size)
                     for path in sorted(self.history_dir.glob("*")) if path.is_file())

    def refresh_datasets(self):
        with self.dataset_lock:
            signature = self._history_signature()
            if signature == self.history_signature:
                return
            discovered = load_history_datasets(self.history_dir)
            current = {key: value for key, value in self.original_datasets.items()
                       if not getattr(value, "venue", None)}
            current.update(discovered)
            self.original_datasets = current
            self.datasets = combine_datasets(current)
            self.dataset_archive.update(current)
            self.dataset_archive.update(self.datasets)
            self._save_combined_datasets()
            self.history_signature = self._history_signature()

    def imported_dataset(self, original_id):
        with self.dataset_lock:
            return next(data for data in self.datasets.values()
                        if data.id == original_id or
                        any(member.id == original_id for member in getattr(data, "members", ())))

    def context(self):
        self.refresh_datasets()
        with self.dataset_lock:
            datasets = list(self.datasets.values())
        return {"datasets": [data.describe() for data in datasets],
                "markets": MARKETS, "timeframes": TIMEFRAMES, "defaults": engine.DEFAULTS,
                "runs": self.store.list(), "sample_id": self.sample_id}

    def dataset(self, key):
        with self.dataset_lock:
            if not isinstance(key, str) or key not in self.dataset_archive:
                raise ValueError("Choose an available local dataset.")
            data = self.dataset_archive[key]
        return data.open() if isinstance(data, (LazyDataset, CombinedDataset)) else data

    def config(self, payload, market):
        partial = payload.get("config")
        strategy_id, strategy_version = payload.get("strategy_id"), payload.get("strategy_version")
        if (strategy_id is None) != (strategy_version is None):
            raise ValueError("strategy_id and strategy_version must be supplied together.")
        if partial is None and strategy_id is not None:
            partial = self.store.get_strategy(strategy_id, strategy_version)["config"]
        if partial is None:
            partial = {}
        if not isinstance(partial, dict):
            raise ValueError("Strategy settings must be an object.")
        if "strategy_file" in partial or "strategy_settings" in partial:
            raise ValueError("Inline strategy source must be selected from the local strategy directory.")
        if market.endswith(("/BTC", "/ETH")):
            partial = {"capital": 1, **partial}
        config = engine.validate_config(partial)
        strategy_file = payload.get("strategy_file")
        strategy_settings = payload.get("strategy_settings", {})
        if strategy_file is None:
            if strategy_settings:
                raise ValueError("strategy_settings require a strategy_file.")
            return config
        if not isinstance(strategy_file, str):
            raise ValueError("strategy_file must be a filename.")
        if not isinstance(strategy_settings, dict):
            raise ValueError("strategy_settings must be an object.")
        saved = self.strategy_files.get(strategy_file)
        if saved["error"]:
            raise ValueError(f"The saved strategy file is invalid: {saved['error']}")
        expected_hash = payload.get("strategy_hash")
        if expected_hash is not None and expected_hash != saved["hash"]:
            raise StoreError("The selected strategy file changed. Refresh the strategy library.")
        config["strategy_file"] = {key: saved[key] for key in
                                   ("filename", "source", "hash", "name", "description")}
        config["strategy_settings"] = {**saved["settings"], **strategy_settings}
        return config

    def backtest(self, payload):
        data = self.dataset(payload.get("dataset"))
        config = self.config(payload, data.market)
        warmup, selected = data.select(payload.get("start"), payload.get("end"),
                                       warmup_count=engine.warmup_count(config))
        return self.store.backtest(data.market, data.timeframe, config,
                                   data.source(), warmup, selected,
                                   strategy_id=payload.get("strategy_id"),
                                   strategy_version=payload.get("strategy_version"))

    def history(self, payload):
        path, metadata = download_snapshot(
            payload.get("market"), payload.get("timeframe"),
            payload.get("start"), payload.get("end"), self.history_dir,
            venue=payload.get("venue", "Coinbase"),
        )
        dataset = Dataset(path, metadata, discard_last=False)
        self.refresh_datasets()
        return self.imported_dataset(metadata["id"]).describe()

    def paper(self, payload):
        market, timeframe = payload.get("market"), payload.get("timeframe")
        if market not in MARKETS or timeframe not in TIMEFRAMES:
            raise ValueError("Choose a supported paper market and interval.")
        config = self.config(payload, market)
        rid = self.store.create("paper", market, timeframe, config, "Kraken public OHLC",
                                strategy_id=payload.get("strategy_id"),
                                strategy_version=payload.get("strategy_version"))
        self.wake.set()
        return rid

    def rehearse(self, payload):
        data = self.dataset(payload.get("dataset"))
        config = self.config(payload, data.market)
        warmup_count = max(1, engine.warmup_count(config))
        if len(data.rows) <= warmup_count:
            raise ValueError(f"A rehearsal needs more than {warmup_count} closed candles.")
        return self.store.create("rehearsal", data.market, data.timeframe, config,
                                 data.source(), warmup_bars=data.rows[:warmup_count],
                                 strategy_id=payload.get("strategy_id"),
                                 strategy_version=payload.get("strategy_version"))

    def advance(self, rid, count, after=None):
        if type(count) is not int or not 1 <= count <= 500:
            raise ValueError("Advance between 1 and 500 candles.")
        with self.advance_lock:
            run = self.store.get(rid)
            if after is not None and after != run["last_ts"]:
                raise StoreError("The session advanced since you opened it. Refresh to see the latest candle.")
            if run["mode"] != "rehearsal" or run["status"] != "running":
                raise ValueError("Only a running recorded rehearsal can advance.")
            with self.dataset_lock:
                datasets = list(self.dataset_archive.values())
            data = next((d for d in datasets if d.source() == run["source"]), None)
            if data is None:
                raise ValueError("The original candle snapshot has changed or is missing.")
            if isinstance(data, (LazyDataset, CombinedDataset)):
                data = data.open()
            pending = [bar for bar in data.rows if bar["ts"] > run["last_ts"]]
            self.store.ingest(rid, pending[:count], completed=len(pending) <= count)
        return rid

    def poll(self):
        # Multiple configurations of the same market can share a public response.
        groups = {}
        for run in self.store.active():
            key = run["market"], run["timeframe"], run["last_ts"]
            groups.setdefault(key, {})[run["id"]] = run
        for (market, timeframe, cursor), configs in groups.items():
            if self.shutdown.is_set():
                break
            try:
                rows = self.fetch(market, timeframe, cursor)
            except (OSError, ValueError, RuntimeError) as exc:
                logging.warning("Feed for %s %s: %s", market, timeframe, exc)
                for rid in configs:
                    self.store.health(rid, str(exc))
                continue
            for rid, run in configs.items():
                try:
                    subset = rows
                    if cursor is None:
                        required = max(1, run["warmup_count"])
                        if len(rows) < required:
                            raise ValueError(f"Waiting for {required} consecutive closed candles to warm the indicators.")
                        subset = rows[-required:]
                    self.store.ingest(rid, subset, warmup=(cursor is None))
                    self.store.health(rid, "")
                except (ValueError, StoreError) as exc:
                    self.store.health(rid, str(exc))
                    logging.warning("Paper session %d: %s", rid, exc)

    def worker(self):
        while not self.shutdown.is_set():
            self.wake.clear()
            try:
                self.poll()
            except Exception:
                logging.exception("Paper worker could not finish its polling cycle")
            self.wake.wait(POLL_SECONDS)


ASSETS = {"/range-explorer": ("range-explorer.html", "text/html; charset=utf-8"),
          "/static/range-explorer.js": ("range-explorer.js", "text/javascript; charset=utf-8"),
          "/static/range-explorer.css": ("range-explorer.css", "text/css; charset=utf-8"),
          "/value-area": ("value-area.html", "text/html; charset=utf-8"),
          "/static/value-area.js": ("value-area.js", "text/javascript; charset=utf-8"),
          "/manual": ("manual.html", "text/html; charset=utf-8"),
          "/static/manual.css": ("manual.css", "text/css; charset=utf-8"),
          "/static/manual.js": ("manual.js", "text/javascript; charset=utf-8"),
          "/static/manual-engine.js": ("manual-engine.js", "text/javascript; charset=utf-8"),
          "/": ("grid-replay.html", "text/html; charset=utf-8"),
          "/grid-replay": ("grid-replay.html", "text/html; charset=utf-8"),
          "/static/grid-replay.js": ("grid-replay.js", "text/javascript; charset=utf-8"),
          "/static/grid-replay.css": ("grid-replay.css", "text/css; charset=utf-8"),
          "/workbench": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/static/style.css": ("style.css", "text/css; charset=utf-8"),
          "/static/inspect.js": ("inspect.js", "text/javascript; charset=utf-8"),
          "/static/inspect.css": ("inspect.css", "text/css; charset=utf-8"),
          "/static/app.js": ("app.js", "text/javascript; charset=utf-8")}


class Handler(BaseHTTPRequestHandler):
    server_version = "Gridline/1"

    def send_bytes(self, status, body, mime):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        with suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)

    def send_json(self, status, data):
        self.send_bytes(status, json.dumps(data, allow_nan=False, separators=(",", ":")).encode(),
                        "application/json; charset=utf-8")

    def handle_request(self, method):
        # Reject cross-site writes and DNS-rebinding access to local session data.
        host = self.headers.get("Host", "")
        if host not in self.server.allowed_hosts:
            return self.send_json(403, {"error": "Use the local Gridline address."})
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + host:
            return self.send_json(403, {"error": "Cross-origin requests are disabled."})
        app = self.server.workbench
        path = urlsplit(self.path).path
        try:
            if method == "GET":
                if path == "/" and any(k in parse_qs(urlsplit(self.path).query) for k in ("run", "view")):
                    path = "/workbench"
                if path in ASSETS:
                    filename, mime = ASSETS[path]
                    return self.send_bytes(200, (ROOT / "static" / filename).read_bytes(), mime)
                if re.fullmatch(r"/api/research/range-explorer(?:-balanced50|-adaptive[1-4])?", path):
                    saved = ROOT / "var" / (path.rsplit("/", 1)[1] + ".json")
                    if not saved.exists():
                        return self.send_json(404, {"error": "Import an exported value-area JSON to begin."})
                    return self.send_bytes(200, saved.read_bytes(), "application/json; charset=utf-8")
                if path == "/api/context":
                    return self.send_json(200, app.context())
                if path == "/api/strategies":
                    return self.send_json(200, {"strategies": app.store.strategies()})
                if path == "/api/strategy-files":
                    return self.send_json(200, {"files": app.strategy_files.list()})
                if match := re.fullmatch(r"/api/runs/(\d+)/usd-volume", path):
                    return self.send_json(200, usd_volume(app.store, int(match[1])))
                if match := re.fullmatch(r"/api/runs/(\d+)", path):
                    return self.send_json(200, app.store.detail(int(match[1])))
            else:
                if self.headers.get_content_type() != "application/json":
                    return self.send_json(415, {"error": "Send an application/json request."})
                size = int(self.headers.get("Content-Length", "0"))
                max_size = 2 * 1024 * 1024 if path == "/api/strategy-files" else 16384
                if not 0 < size <= max_size:
                    raise ValueError(f"Expected a JSON request of up to {max_size // 1024} KB.")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object.")
                if path == "/api/research/value-area":
                    data = app.dataset(body.get("dataset"))
                    return self.send_json(200, run_experiment(data, body.get("config"), body.get("start"), body.get("end")))
                if path == "/api/manual/usd-rates":
                    quote = body.get("quote", "")
                    timestamps = body.get("timestamps")
                    if not isinstance(quote, str) or not re.fullmatch(r"[A-Z0-9]{1,12}", quote):
                        raise ValueError("Invalid quote currency.")
                    if not isinstance(timestamps, list) or not 1 <= len(timestamps) <= 100 or any(
                            type(ts) is not int or not 0 < ts < 4102444800 or ts % 300 for ts in timestamps):
                        raise ValueError("Expected up to 100 five-minute timestamps.")
                    return self.send_json(200, quote_usd_rates(app.store, quote, timestamps))
                if path == "/api/manual/candles":
                    data = app.dataset(body.get("dataset"))
                    warmup, selected = data.select(body.get("start"), body.get("end"), warmup_count=120)
                    prefix = []
                    if "prehistory" in body:
                        if body.get("start") or body.get("end"):
                            raise ValueError("Blind replay history requires the full dataset.")
                        prefix = replay_prefix(data, app.history_dir.parent / "replay-prehistory", body["prehistory"])
                    return self.send_json(200, {"market": data.market, "timeframe": data.timeframe,
                                                "fingerprint": data.fingerprint, "prefix_count": len(prefix),
                                                "daily_history": read_closes(data.venue, data.market) if "prehistory" in body else [],
                                                "bars": prefix + warmup + selected, "index": len(prefix) + len(warmup)})
                actions = {"/api/backtests": app.backtest, "/api/paper": app.paper,
                           "/api/rehearsals": app.rehearse}
                if match := re.fullmatch(r"/api/(backtests|runs)/(\d+)/delete", path):
                    rid = int(match[2])
                    app.store.delete_archived_run(rid, body.get("created_at"), backtest_only=match[1] == "backtests")
                    if app.sample_id == rid:
                        app.sample_id = None
                    return self.send_json(200, {"deleted": rid})
                if path == "/api/backtests/clear":
                    count = app.store.clear_backtests(body.get("count"))
                    app.sample_id = None
                    return self.send_json(200, {"deleted": count})
                if path == "/api/history/bulk-month":
                    market, month = body.get("market"), body.get("month")
                    if not isinstance(market, str) or not isinstance(month, str):
                        raise ValueError("Choose a spot pair and a UTC month.")
                    try:
                        metadata, skipped = collect_month(market, month, app.history_dir)
                    except OSError as exc:
                        raise RuntimeError(f"Could not collect {market} for {month}: {exc}") from exc
                    app.refresh_datasets()
                    return self.send_json(200 if skipped else 201, {
                        "dataset": app.imported_dataset(metadata["id"]).describe(), "skipped": skipped})
                if path == "/api/history/coverage":
                    return self.send_json(200, coverage_start(body.get("market"), body.get("timeframe"), body.get("end"),
                                                              venue=body.get("venue", "Coinbase")))
                if path == "/api/history":
                    return self.send_json(201, {"dataset": app.history(body)})
                if path == "/api/strategies":
                    strategy = app.store.save_strategy(body.get("name"), body.get("config"))
                    return self.send_json(201, {"strategy": strategy})
                if path == "/api/strategy-files":
                    file = app.strategy_files.save(body.get("filename"), body.get("source"),
                                                   body.get("previous_hash"))
                    return self.send_json(200 if body.get("previous_hash") else 201, {"file": file})
                if match := re.fullmatch(r"/api/strategies/(\d+)", path):
                    strategy = app.store.save_strategy(body.get("name"), body.get("config"),
                                                       int(match[1]), body.get("version"))
                    return self.send_json(200, {"strategy": strategy})
                if path in actions:
                    return self.send_json(201, {"id": actions[path](body)})
                if match := re.fullmatch(r"/api/runs/(\d+)/(audit|stop|advance)", path):
                    rid = int(match[1])
                    if match[2] == "audit":
                        return self.send_json(200, app.store.audit(rid))
                    if match[2] == "advance":
                        app.advance(rid, body.get("count", 12), body.get("after"))
                    else:
                        if app.store.get(rid)["mode"] == "backtest":
                            raise ValueError("A completed backtest cannot be stopped.")
                        app.store.finish(rid)
                    return self.send_json(200, {"id": rid})
            self.send_json(404, {"error": "Unknown resource."})
        except StrategyFileNotFound as exc:
            self.send_json(404, {"error": str(exc)})
        except LookupError:
            self.send_json(404, {"error": "Run not found."})
        except StoreError as exc:
            self.send_json(409, {"error": str(exc)})
        except StrategyFileConflict as exc:
            self.send_json(409, {"error": str(exc)})
        except StrategyFileError as exc:
            self.send_json(400, {"error": str(exc)})
        except RuntimeError as exc:
            self.send_json(502, {"error": str(exc)})
        except (ValueError, TypeError, OverflowError) as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception:
            logging.exception("Request failed")
            self.send_json(500, {"error": "The local service failed. See the server log."})

    def do_GET(self):
        self.handle_request("GET")

    def do_POST(self):
        self.handle_request("POST")


def make_server(app, port):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.workbench = app
    bound = server.server_port
    server.allowed_hosts = {f"127.0.0.1:{bound}", f"localhost:{bound}"}
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=ROOT / "var" / "gridline.sqlite3")
    parser.add_argument("--data-dir", type=Path, help="Opt in to a directory of local test fixtures.")
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    app = Workbench(args.db, load_datasets(args.data_dir) if args.data_dir else {})
    with make_server(app, args.port) as server:
        thread = threading.Thread(target=app.worker, name="kraken-paper", daemon=True)
        thread.start()
        print(f"Gridline is ready at http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            app.shutdown.set()
            app.wake.set()
            thread.join(timeout=15)


if __name__ == "__main__":
    main()
