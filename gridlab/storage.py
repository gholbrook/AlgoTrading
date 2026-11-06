"""Durable SQLite storage for GridLab market runs.

The database is the source of truth for captured candles and strategy decisions.
SQLite persists the full bar and decision trace, with an incremental engine
checkpoint for fast paper-session recovery.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from gridlab import engine
from gridlab.market import validate_bar

ENGINE_VERSION = "grid-v1"


def stored_config(config):
    # Before execution models existed, the custom signal evaluator treated
    # "disabled" as "any". Preserve that behavior for captured runs and versions.
    if "execution_model" not in config and config.get("signal_model") == "custom":
        config = dict(config)
        for key in ("bull_trend", "bear_trend"):
            if config.get(key) == "disabled":
                config[key] = "any"
    return engine.validate_config(config)


class StoreError(RuntimeError):
    """Base error for invalid run operations or persisted data."""


class GapError(StoreError):
    """Raised when a candle is not the next interval after the cursor."""


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY,
                    mode TEXT NOT NULL CHECK(mode IN ('paper','backtest','rehearsal')),
                    market TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('running','completed','stopped')),
                    created_at TEXT NOT NULL,
                    last_ts INTEGER,
                    last_poll TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    bars INTEGER NOT NULL DEFAULT 0,
                    state_json TEXT NOT NULL,
                    engine_version TEXT NOT NULL,
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    strategy_id INTEGER,
                    strategy_version INTEGER,
                    warmup_count INTEGER NOT NULL DEFAULT 205
                );
                CREATE TABLE IF NOT EXISTS strategies (
                    id INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(id, version)
                );
                CREATE TABLE IF NOT EXISTS strategy_ids (id INTEGER PRIMARY KEY AUTOINCREMENT);
                CREATE TABLE IF NOT EXISTS bars (
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    ts INTEGER NOT NULL,
                    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                    close REAL NOT NULL, volume REAL NOT NULL,
                    warmup INTEGER NOT NULL CHECK(warmup IN (0,1)),
                    PRIMARY KEY(run_id, ts)
                );
                CREATE TABLE IF NOT EXISTS points (
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    ts INTEGER NOT NULL,
                    point_json TEXT NOT NULL,
                    PRIMARY KEY(run_id, ts)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY,
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    ts INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS replay_accounts (
                    run_id INTEGER PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
                    cache_key TEXT NOT NULL,
                    account_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_run_ts ON events(run_id, ts, id);
            """)
            # Additive migration for databases created by older GridLab versions.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(runs)")}
            for column in ("strategy_id", "strategy_version"):
                if column not in columns:
                    db.execute(f"ALTER TABLE runs ADD COLUMN {column} INTEGER")
            if "warmup_count" not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN warmup_count INTEGER NOT NULL DEFAULT 205")

    def strategies(self) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT s.* FROM strategies s JOIN (SELECT id,MAX(version) version FROM strategies GROUP BY id) latest USING(id,version) ORDER BY id")
            return [self._strategy(row) for row in rows]

    def get_strategy(self, strategy_id: int, version: int) -> dict:
        if type(strategy_id) is not int or type(version) is not int:
            raise ValueError("Strategy id and version must be integers.")
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM strategies WHERE id=? AND version=?",
                             (strategy_id, version)).fetchone()
            if row is None:
                raise KeyError(f"unknown strategy {strategy_id} version {version}")
            return self._strategy(row)

    @staticmethod
    def _strategy(row):
        return {"id": row["id"], "name": row["name"], "version": row["version"],
                "config": stored_config(json.loads(row["config_json"]))}

    def save_strategy(self, name: str, config: dict, strategy_id=None, version=None) -> dict:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            raise ValueError("Strategy name must be 1 to 100 characters.")
        if not isinstance(config, dict):
            raise ValueError("Strategy config must be an object.")
        normalized = json.loads(self._json(engine.validate_config(config)))
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            if strategy_id is None:
                cur = db.execute("INSERT INTO strategy_ids DEFAULT VALUES")
                strategy_id = int(cur.lastrowid)
                version = 1
            else:
                if type(strategy_id) is not int or type(version) is not int:
                    raise ValueError("Strategy id and version must be integers.")
                latest = db.execute("SELECT MAX(version) version FROM strategies WHERE id=?", (strategy_id,)).fetchone()["version"]
                if latest is None:
                    raise KeyError(f"unknown strategy id {strategy_id}")
                if latest != version:
                    raise StoreError(f"Strategy changed since it was loaded (current version {latest}).")
                version += 1
            db.execute("INSERT INTO strategies VALUES(?,?,?,?,?)",
                       (strategy_id, version, name.strip(), self._json(normalized), self._utc_now()))
            row = db.execute("SELECT * FROM strategies WHERE id=? AND version=?", (strategy_id, version)).fetchone()
            db.commit()
            return self._strategy(row)

    def _resolve_strategy(self, db, config, strategy_id, strategy_version):
        if strategy_id is None and strategy_version is None:
            return None, None
        if type(strategy_id) is not int or type(strategy_version) is not int:
            raise ValueError("strategy_id and strategy_version must be supplied together as integers.")
        row = db.execute("SELECT config_json FROM strategies WHERE id=? AND version=?",
                         (strategy_id, strategy_version)).fetchone()
        if row is None:
            raise ValueError("Saved strategy version does not exist.")
        requested = json.loads(self._json(engine.validate_config(config)))
        if requested != stored_config(json.loads(row["config_json"])):
            raise ValueError("Run config must match the selected saved strategy version.")
        return strategy_id, strategy_version

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(self, mode: str, market: str, timeframe: str, config: dict,
               source: str, warmup_bars: Iterable[dict] = (), *,
               strategy_id: int | None = None, strategy_version: int | None = None) -> int:
        warmup = self._ordered_unique(warmup_bars)
        prepared = self._prepare_run(mode, market, timeframe, config, source)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            prepared["strategy_id"], prepared["strategy_version"] = self._resolve_strategy(
                db, prepared["config"], strategy_id, strategy_version)
            run_id = self._insert_run(db, prepared)
            if warmup:
                run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
                self._ingest_db(db, run, warmup, True)
            db.commit()
            return run_id

    @staticmethod
    def _interval(timeframe: str) -> int:
        units = {"m": 60, "h": 3600, "d": 86400}
        try:
            suffix, n = timeframe[-1], int(timeframe[:-1])
            interval = n * units[suffix]
        except (ValueError, KeyError, IndexError):
            raise ValueError(f"unsupported timeframe: {timeframe!r}") from None
        if interval <= 0:
            raise ValueError(f"unsupported timeframe: {timeframe!r}")
        return interval

    @staticmethod
    def _normalize_bar(bar: dict) -> dict:
        return validate_bar(bar)

    def _prepare_run(self, mode: str, market: str, timeframe: str,
                     config: dict, source: str) -> dict:
        if mode not in {"paper", "backtest", "rehearsal"}:
            raise ValueError("mode must be 'paper', 'backtest', or 'rehearsal'")
        if not market or not source:
            raise ValueError("market and source are required")
        self._interval(timeframe)
        immutable_config = json.loads(self._json(engine.validate_config(config)))
        state = engine.new_state(immutable_config)
        return {"mode": mode, "market": market, "timeframe": timeframe,
                "source": source, "config": immutable_config, "state": state,
                "summary": engine.summary(state),
                "warmup_count": engine.warmup_count(immutable_config)}

    def _insert_run(self, db: sqlite3.Connection, prepared: dict) -> int:
        cur = db.execute(
            "INSERT INTO runs(mode,market,timeframe,config_json,source,status,created_at,state_json,engine_version,summary_json,strategy_id,strategy_version,warmup_count) "
            "VALUES(?,?,?,?,?,'running',?,?,?,?,?,?,?)",
            (prepared["mode"], prepared["market"], prepared["timeframe"],
             self._json(prepared["config"]), prepared["source"], self._utc_now(),
             self._json(prepared["state"]), ENGINE_VERSION, self._json(prepared["summary"]),
             prepared.get("strategy_id"), prepared.get("strategy_version"),
             prepared["warmup_count"]),
        )
        return int(cur.lastrowid)

    @classmethod
    def _ordered_unique(cls, bars: Iterable[dict]) -> list[dict]:
        result = []
        for raw in bars:
            bar = cls._normalize_bar(raw)
            if result and bar["ts"] < result[-1]["ts"]:
                raise ValueError("input bars must be chronological")
            if result and bar["ts"] == result[-1]["ts"]:
                if bar != result[-1]:
                    raise StoreError(f"conflicting duplicate candle at {bar['ts']}")
                continue
            result.append(bar)
        return result

    def ingest(self, run_id: int, bars: Iterable[dict], warmup: bool = False,
               *, completed: bool = False) -> int:
        """Atomically capture candles and their corresponding engine decisions.

        ``warmup`` applies to every input bar in this call. Bars are committed
        with their point, events, state checkpoint, and cursor in one SQLite
        transaction. Exact duplicate candles are harmless; conflicting
        duplicates and interval gaps fail the entire batch.
        """
        normalized = self._ordered_unique(bars)
        if not normalized and not completed:
            return 0
        inserted = 0
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(f"unknown run id {run_id}")
            if run["status"] != "running":
                raise StoreError(f"run {run_id} is {run['status']}")
            if run["engine_version"] != ENGINE_VERSION:
                raise StoreError(f"run uses unsupported engine version {run['engine_version']!r}")
            inserted = self._ingest_db(db, run, normalized, warmup)
            if completed:
                db.execute("UPDATE runs SET status='completed' WHERE id=?", (run_id,))
            db.commit()
        return inserted

    def _ingest_db(self, db: sqlite3.Connection, run: sqlite3.Row,
                   normalized: list[dict], warmup: bool, replay=None) -> int:
        run_id = run["id"]
        state = json.loads(run["state_json"])
        state["config"] = stored_config(state["config"])
        cursor = run["last_ts"]
        interval = self._interval(run["timeframe"])
        inserted = 0
        for bar in normalized:
            prior = db.execute("SELECT * FROM bars WHERE run_id=? AND ts=?",
                               (run_id, bar["ts"])).fetchone()
            vals = tuple(bar[k] for k in ("open", "high", "low", "close", "volume"))
            if prior is not None:
                old_vals = tuple(prior[k] for k in ("open", "high", "low", "close", "volume"))
                if old_vals != vals or bool(prior["warmup"]) != bool(warmup):
                    raise StoreError(f"conflicting duplicate candle at {bar['ts']}")
                continue
            if cursor is not None:
                if bar["ts"] <= cursor:
                    raise StoreError(f"late candle at {bar['ts']} is behind cursor {cursor}")
                if bar["ts"] != cursor + interval:
                    raise GapError(f"expected candle at {cursor + interval}, received {bar['ts']}")
            db.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?,?)",
                       (run_id, bar["ts"], *vals, int(bool(warmup))))
            point = engine.step(state, bar, trade=not warmup)
            if point is None:
                point = {}
            if not isinstance(point, dict):
                raise StoreError("engine.step must return a point dictionary")
            db.execute("INSERT INTO points VALUES(?,?,?)", (run_id, bar["ts"], self._json(point)))
            if replay is not None:
                self._record_replay(replay, state, point, bar["ts"])
            cursor = bar["ts"]
            inserted += 1
        if inserted:
            summary = engine.summary(state)
            db.execute("UPDATE runs SET last_ts=?,bars=bars+?,state_json=?,summary_json=? WHERE id=?",
                       (cursor, inserted, self._json(state), self._json(summary), run_id))
        return inserted

    def delete_archived_run(self, run_id: int, created_at: str, *, backtest_only: bool = False) -> None:
        with closing(self._connect()) as db:
            deleted = db.execute(
                "DELETE FROM runs WHERE id=? AND created_at=? AND "
                "(mode='backtest' OR (?=0 AND mode IN ('paper','rehearsal') "
                "AND status IN ('stopped','completed')))", (run_id, created_at, backtest_only))
            if not deleted.rowcount:
                raise StoreError("This run is active or the archive changed. Refresh and check its status.")
            db.commit()

    def clear_backtests(self, expected_count: int) -> int:
        if type(expected_count) is not int or expected_count < 1:
            raise ValueError("Specify the number of saved backtests to clear.")
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) FROM runs WHERE mode='backtest'").fetchone()[0]
            if count != expected_count:
                raise StoreError("The archive changed. Refresh and review the backtest count.")
            db.execute("DELETE FROM runs WHERE mode='backtest'")
            db.commit()
            return count

    def finish(self, run_id: int, completed: bool = False) -> None:
        with closing(self._connect()) as db:
            run = db.execute("SELECT mode,status FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(f"unknown run id {run_id}")
            if run["status"] != "running":
                return
            status = "completed" if (run["mode"] == "backtest" or completed) else "stopped"
            db.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))

    def health(self, run_id: int, message: str) -> None:
        now = self._utc_now()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT last_ts,status FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(f"unknown run id {run_id}")
            if run["status"] != "running":
                return
            db.execute("UPDATE runs SET last_poll=?,error=? WHERE id=?",
                       (now, str(message), run_id))
            ts = int(run["last_ts"] or time.time())
            kind = "poll_error" if message else "poll_ok"
            db.execute("INSERT INTO events(run_id,ts,kind,payload_json) VALUES(?,?,?,?)",
                       (run_id, ts, kind, self._json({"message": str(message), "at": now})))
            db.commit()

    @staticmethod
    def _metadata(row: sqlite3.Row, *, include_file_source: bool = True) -> dict:
        config = stored_config(json.loads(row["config_json"]))
        if not include_file_source and isinstance(config.get("strategy_file"), dict):
            config["strategy_file"] = {key: value for key, value in config["strategy_file"].items()
                                        if key != "source"}
        return {
            "id": row["id"], "mode": row["mode"], "market": row["market"],
            "timeframe": row["timeframe"], "config": config,
            "source": row["source"], "status": row["status"],
            "created_at": row["created_at"], "last_ts": row["last_ts"],
            "last_poll": row["last_poll"], "error": row["error"], "bars": row["bars"],
            "engine_version": row["engine_version"],
            "strategy_id": row["strategy_id"], "strategy_version": row["strategy_version"],
            "strategy_name": row["strategy_name"] if "strategy_name" in row.keys() else None,
            "warmup_count": row["warmup_count"],
        }

    @staticmethod
    def _run_query(db, where="", params=()):
        return db.execute("SELECT r.*,s.name AS strategy_name FROM runs r LEFT JOIN strategies s "
                          "ON s.id=r.strategy_id AND s.version=r.strategy_version " + where, params)

    def list(self) -> list[dict]:
        with closing(self._connect()) as db:
            return [self._metadata(r, include_file_source=False)
                    for r in self._run_query(db, "ORDER BY r.id DESC")]

    def get(self, run_id: int) -> dict:
        """Return one run's metadata and checkpoint, without loading its trace."""
        with closing(self._connect()) as db:
            row = self._run_query(db, "WHERE r.id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown run id {run_id}")
            if row["engine_version"] != ENGINE_VERSION:
                raise StoreError(f"run uses unsupported engine version {row['engine_version']!r}")
            return {**self._metadata(row), "state": json.loads(row["state_json"]),
                    "summary": json.loads(row["summary_json"])}

    def active(self) -> list[dict]:
        with closing(self._connect()) as db:
            return [self._metadata(r, include_file_source=False) for r in self._run_query(
                db, "WHERE r.status='running' AND r.mode='paper' ORDER BY r.id DESC")]

    def detail(self, run_id: int) -> dict:
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            run = self._run_query(db, "WHERE r.id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(f"unknown run id {run_id}")
            cache_key = self._replay_cache_key(run) if run["mode"] == "backtest" else None
            cached = db.execute("SELECT account_json FROM replay_accounts WHERE run_id=? AND cache_key=?",
                                (run_id, cache_key)).fetchone() if cache_key else None
            points = []
            total_points = run["bars"]
            # Historical runs have a fixed end. Keep all candles seekable; bound
            # the recurring monitor response for open-ended Kraken sessions.
            point_limit = 1500 if run["mode"] == "paper" else -1
            for p in db.execute("SELECT p.ts,p.point_json,b.open,b.high,b.low,b.close,b.volume,b.warmup "
                                "FROM points p JOIN bars b USING(run_id,ts) WHERE p.run_id=? "
                                "ORDER BY p.ts DESC LIMIT ?", (run_id, point_limit)):
                point = json.loads(p["point_json"])
                point.update({"ts": p["ts"], "open": p["open"], "high": p["high"],
                              "low": p["low"], "close": p["close"], "volume": p["volume"],
                              "warmup": bool(p["warmup"])})
                points.append(point)
            points.reverse()
            # The monitor limits visible candles. Carry older fills into the
            # monthly ledger so scrubbing the visible window keeps full run totals.
            volume_before_window = []
            if points and total_points > len(points):
                older = db.execute("""
                    SELECT strftime('%Y-%m', p.ts, 'unixepoch') AS month,
                           SUM(CASE WHEN f.value IS NOT NULL THEN 1 ELSE 0 END) AS fills,
                           SUM(CASE WHEN f.value ->> 'side' = 'buy'
                               THEN (f.value ->> 'price') * (f.value ->> 'qty') ELSE 0 END) AS bought,
                           SUM(CASE WHEN f.value ->> 'side' = 'sell'
                               THEN (f.value ->> 'price') * (f.value ->> 'qty') ELSE 0 END) AS sold
                    FROM points p JOIN bars b USING(run_id,ts)
                    LEFT JOIN json_each(p.point_json, '$.events') f
                      ON (f.value ->> 'kind') IN ('entry','exit')
                    WHERE p.run_id=? AND p.ts<? AND b.warmup=0 GROUP BY month
                """, (run_id, points[0]["ts"]))
                volume_before_window = [dict(row) for row in older]

            positions = engine.open_lots(json.loads(run["state_json"]))
            health = [{"ts": e["ts"], "type": e["kind"], **json.loads(e["payload_json"])}
                      for e in db.execute("SELECT ts,kind,payload_json FROM events WHERE run_id=? "
                                          "AND kind IN ('poll_ok','poll_error') ORDER BY id DESC LIMIT 100",
                                          (run_id,))]
            health.reverse()
            detail = {"run": self._metadata(run), "points": points,
                    "summary": json.loads(run["summary_json"]), "positions": positions,
                    "volume_before_window": volume_before_window,
                    "health_events": health, "total_points": total_points}

        if run["mode"] == "backtest":
            detail["replay_account"] = (json.loads(cached["account_json"]) if cached else
                                        self._replay_account(points, detail["run"]["config"]))
            if not cached and run["status"] == "completed":
                # Close the read snapshot before the short write transaction.
                # Recheck identity to handle concurrent deletion and id reuse.
                with closing(self._connect()) as db:
                    db.execute("BEGIN IMMEDIATE")
                    current = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
                    if (current is not None and current["status"] == "completed"
                            and self._replay_cache_key(current) == cache_key):
                        self._save_replay(db, run_id, cache_key, detail["replay_account"])
                    db.commit()
        return detail

    @classmethod
    def _replay_cache_key(cls, run):
        # Include implementation and embedded strategy source/config: engine
        # fixes do not always bump ENGINE_VERSION.
        digest = hashlib.sha256()
        for filename in ("storage.py", "engine.py", "file_engine.py"):
            digest.update(Path(__file__).with_name(filename).read_bytes())
        digest.update(cls._json({key: run[key] for key in
                      ("created_at", "config_json", "engine_version", "bars", "last_ts")}).encode())
        return digest.hexdigest()

    def _save_replay(self, db, run_id, cache_key, account):
        db.execute("INSERT INTO replay_accounts VALUES(?,?,?) ON CONFLICT(run_id) DO UPDATE SET "
                   "cache_key=excluded.cache_key,account_json=excluded.account_json",
                   (run_id, cache_key, self._json(account)))

    @staticmethod
    def _new_replay():
        return {"summaries": [], "layers": {}, "latest_lots": {}}

    @staticmethod
    def _record_replay(replay, state, decision, ts):
        def identity(fill):
            return fill.get("lot_id", fill.get("position_id", fill["entry_ts"]))

        replay["summaries"].append(engine.summary(state))
        layers, latest_lots = replay["layers"], replay["latest_lots"]
        entries = {identity(event): event for event in decision.get("events", [])
                   if event["kind"] == "entry"}
        current = {}
        for lot in engine.open_lots(state):
            key = identity(lot)
            lot.pop("unrealized_pnl")
            current[key] = lot
            if key not in layers:
                layers[key] = {**lot, "entry_fee": lot.get("entry_fee", entries.get(key, {}).get("fee", 0)),
                               "exit_ts": None}
            elif lot != latest_lots.get(key):
                layers[key].setdefault("changes", []).append({"ts": ts, **lot})
        for key in latest_lots.keys() - current.keys():
            layers[key]["exit_ts"] = ts
        replay["latest_lots"] = current

    @staticmethod
    def _replay_result(replay):
        return {"summaries": replay["summaries"], "layers": list(replay["layers"].values())}

    @classmethod
    def _replay_account(cls, points, config):
        state = engine.new_state(config)
        replay = cls._new_replay()
        for point in points:
            decision = engine.step(state, point, trade=not point["warmup"])
            cls._record_replay(replay, state, decision, point["ts"])
        return cls._replay_result(replay)

    def audit(self, run_id: int) -> dict:
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(f"unknown run id {run_id}")
            if run["engine_version"] != ENGINE_VERSION:
                raise StoreError(f"run uses unsupported engine version {run['engine_version']!r}")
            expected_summary = json.loads(run["summary_json"])
            checkpoint = json.loads(run["state_json"])
            config = stored_config(json.loads(run["config_json"]))
            # Older checkpoints stored a partial config. Normalize it before
            # comparing with the replayed state under current defaults.
            checkpoint["config"] = stored_config(checkpoint.get("config", config))
            counts = db.execute(
                "SELECT (SELECT COUNT(*) FROM bars WHERE run_id=?) AS bars, "
                "(SELECT COUNT(*) FROM points WHERE run_id=?) AS points",
                (run_id, run_id),
            ).fetchone()
            state = engine.new_state(config)
            decisions_match = True
            first_mismatch_ts = None
            last_ts = None
            processed = 0
            rows = db.execute(
                "SELECT b.ts,b.open,b.high,b.low,b.close,b.volume,b.warmup,p.point_json "
                "FROM bars b LEFT JOIN points p ON p.run_id=b.run_id AND p.ts=b.ts "
                "WHERE b.run_id=? ORDER BY b.ts", (run_id,),
            )
            for row in rows:
                ts = row["ts"]
                bar = {key: row[key] for key in ("ts", "open", "high", "low", "close", "volume")}
                replayed = engine.step(state, bar, trade=not bool(row["warmup"])) or {}
                if row["point_json"] is None or replayed != json.loads(row["point_json"]):
                    decisions_match = False
                    if first_mismatch_ts is None:
                        first_mismatch_ts = ts
                processed += 1
                last_ts = ts
            summary = engine.summary(state)
            summary_match = summary == expected_summary
            checkpoint_match = state == checkpoint
            metadata_match = (processed == run["bars"] == counts["bars"] == counts["points"]
                              and last_ts == run["last_ts"])
            if not metadata_match and first_mismatch_ts is None:
                first_mismatch_ts = last_ts if last_ts is not None else run["last_ts"]
            if not summary_match and first_mismatch_ts is None:
                first_mismatch_ts = last_ts
            if not checkpoint_match and first_mismatch_ts is None:
                first_mismatch_ts = last_ts
            return {
                "match": decisions_match and summary_match and checkpoint_match and metadata_match,
                "bars": processed, "points": counts["points"],
                "summary_match": summary_match,
                "decisions_match": decisions_match,
                "checkpoint_match": checkpoint_match,
                "metadata_match": metadata_match,
                "first_mismatch_ts": first_mismatch_ts,
            }

    def backtest(self, market: str, timeframe: str, config: dict, source: str,
                 warmup_bars: Iterable[dict], bars: Iterable[dict], *,
                 strategy_id: int | None = None, strategy_version: int | None = None) -> int:
        """Create and ingest a complete backtest in one transaction."""
        warm = self._ordered_unique(warmup_bars)
        live = self._ordered_unique(bars)
        prepared = self._prepare_run("backtest", market, timeframe, config, source)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            prepared["strategy_id"], prepared["strategy_version"] = self._resolve_strategy(
                db, prepared["config"], strategy_id, strategy_version)
            run_id = self._insert_run(db, prepared)
            run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            replay = self._new_replay()
            if warm:
                self._ingest_db(db, run, warm, True, replay)
                run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if live:
                self._ingest_db(db, run, live, False, replay)
            db.execute("UPDATE runs SET status='completed' WHERE id=?", (run_id,))
            run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            self._save_replay(db, run_id, self._replay_cache_key(run), self._replay_result(replay))
            db.commit()
            return run_id
