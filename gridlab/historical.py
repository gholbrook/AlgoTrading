"""Bounded, public Coinbase Exchange candle downloads and immutable snapshots."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import tempfile
import time
from datetime import date, datetime, time as daytime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from gridlab.market import validate_bar

API_ROOT = "https://api.exchange.coinbase.com/products"
BINANCE_ROOT = "https://data-api.binance.vision/api/v3"
TIMEOUT_SECONDS = 15
MAX_BARS = 30_000
PAGE_SIZE = 299  # leave one slot for Coinbase's allowed pre-start candle
REQUEST_DELAY_SECONDS = 0.1
MAX_PAGE_RETRIES = 3
INTERVALS = {"5m": 300, "1h": 3600}
MARKET_RE = re.compile(r"^[A-Z0-9]{1,12}/[A-Z0-9]{1,12}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _date(value: str, name: str) -> date:
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise ValueError(f"{name} must be a UTC date in YYYY-MM-DD format.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid UTC date in YYYY-MM-DD format.") from exc


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def _request_json(url: str, opener=urlopen, venue="Coinbase"):
    req = Request(url, headers={"User-Agent": "Gridline/1.0", "Accept": "application/json"})
    retries = 0
    while True:
        try:
            with opener(req, timeout=TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            message = str(exc)
            exc.close()
            if exc.code != 429 or retries >= MAX_PAGE_RETRIES:
                raise RuntimeError(f"{venue} candle request failed: {message}") from exc
            try:
                delay = float((exc.headers or {}).get("Retry-After", 0.25 * (2 ** retries)))
            except (TypeError, ValueError):
                delay = 0.25 * (2 ** retries)
        except OSError as exc:
            reason = exc.reason if isinstance(exc, URLError) else exc
            if not isinstance(reason, TimeoutError):
                raise RuntimeError(f"{venue} candle request failed: {exc}") from exc
            if retries >= MAX_PAGE_RETRIES:
                raise RuntimeError(f"{venue} timed out while fetching candles after {retries + 1} attempts. Try the same date range again.") from exc
            delay = 0.25 * (2 ** retries)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"{venue} candle request failed: {exc}") from exc
        time.sleep(min(max(delay, 0.1), 3.0))
        retries += 1


def _atomic_create(path: Path, content: bytes) -> None:
    """Publish a complete file without replacing a previous immutable snapshot."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() == content:
            return
        raise RuntimeError(f"immutable history file already exists with different contents: {path.name}")
    fd, temp_name = tempfile.mkstemp(prefix=".history-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp_name, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise RuntimeError(f"immutable history file already exists with different contents: {path.name}")
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _page(market, timeframe, page_start, page_end, opener):
    interval = INTERVALS[timeframe]
    product_id = market.replace("/", "-")
    rows_by_ts = {}
    query = urlencode({"granularity": interval, "start": _iso(page_start), "end": _iso(page_end)})
    url = f"{API_ROOT}/{product_id}/candles?{query}"
    payload = _request_json(url, opener=opener)
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error")
        raise RuntimeError(f"Coinbase rejected {product_id} candle request: {message or 'unexpected response'}")
    if not isinstance(payload, list):
        raise RuntimeError("Coinbase candle response must be a JSON array.")
    if len(payload) > 300:
        raise RuntimeError("Coinbase returned more than 300 candles for one page.")
    for row in payload:
        if not isinstance(row, (list, tuple)) or len(row) != 6:
            raise RuntimeError("Coinbase returned a malformed candle row.")
        try:
            bar = validate_bar({"ts": row[0], "low": row[1], "high": row[2],
                                "open": row[3], "close": row[4], "volume": row[5]})
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Coinbase returned an invalid candle: {exc}") from exc
        ts = bar["ts"]
        if ts % interval:
            raise RuntimeError(f"Coinbase candle timestamp {ts} is not aligned to {timeframe}.")
        if page_start <= ts < page_end:
            previous = rows_by_ts.get(ts)
            if previous is not None and previous != bar:
                raise RuntimeError(f"Coinbase returned conflicting candles at {_iso(ts)}.")
            rows_by_ts[ts] = bar
    return rows_by_ts


def _binance_symbol(market, opener):
    """Resolve and validate the pair against Binance's public exchange catalog."""
    base, quote = market.split("/")
    payload = _request_json(f"{BINANCE_ROOT}/exchangeInfo?{urlencode({'symbol': base + quote})}", opener=opener, venue="Binance")
    if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
        raise RuntimeError("Binance exchangeInfo response is malformed.")
    matches = [item for item in payload["symbols"] if isinstance(item, dict)
               and item.get("baseAsset") == base and item.get("quoteAsset") == quote
               and item.get("status") == "TRADING"]
    if not matches:
        raise ValueError(f"Binance does not list {market} as a trading pair.")
    return matches[0]["symbol"]


def _binance_page(market, timeframe, page_start, page_end, opener, symbol):
    interval = INTERVALS[timeframe]
    query = urlencode({"symbol": symbol, "interval": timeframe,
                       "startTime": page_start * 1000,
                       "endTime": page_end * 1000 - 1, "limit": 1000})
    payload = _request_json(f"{BINANCE_ROOT}/klines?{query}", opener=opener, venue="Binance")
    if not isinstance(payload, list):
        raise RuntimeError("Binance candle response must be a JSON array.")
    if len(payload) > 1000:
        raise RuntimeError("Binance returned more than 1,000 candles for one page.")
    result = {}
    for row in payload:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            raise RuntimeError("Binance returned a malformed candle row.")
        try:
            ts = int(row[0]) // 1000
            bar = validate_bar({"ts": ts, "open": row[1], "high": row[2],
                                "low": row[3], "close": row[4], "volume": row[5]})
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError(f"Binance returned an invalid candle: {exc}") from exc
        if ts % interval:
            raise RuntimeError(f"Binance candle timestamp {ts} is not aligned to {timeframe}.")
        if page_start <= ts < page_end:
            if ts in result and result[ts] != bar:
                raise RuntimeError(f"Binance returned conflicting candles at {_iso(ts)}.")
            result[ts] = bar
    return result


def _venue_page(market, timeframe, start, end, opener, venue, symbol=None):
    if venue == "Coinbase":
        return _page(market, timeframe, start, end, opener)
    return _binance_page(market, timeframe, start, end, opener, symbol)


def coverage_start(market, timeframe, end, *, now=None, opener=urlopen, venue="Coinbase"):
    """Find the most recent downloadable UTC date range in the snapshot window."""
    if not isinstance(market, str) or not MARKET_RE.fullmatch(market) or timeframe not in INTERVALS:
        raise ValueError("Choose a BASE/QUOTE pair and a supported interval.")
    if venue not in ("Coinbase", "Binance"):
        raise ValueError("venue must be Coinbase or Binance.")
    last_day = _date(end, "end")
    now = int(time.time()) if now is None else now
    if last_day > datetime.fromtimestamp(now, timezone.utc).date():
        raise ValueError("End date cannot be in the future (UTC).")
    interval = INTERVALS[timeframe]
    midnight_after = int(datetime.combine(last_day + timedelta(days=1), daytime.min, timezone.utc).timestamp())
    days = MAX_BARS // (86400 // interval)
    floor = midnight_after - days * 86400
    closed_end = min(midnight_after, int(now // interval) * interval)
    if closed_end <= midnight_after - 86400:
        raise ValueError("The selected end date has no closed candles yet.")
    symbol = _binance_symbol(market, opener) if venue == "Binance" else None
    page_size = 1000 if venue == "Binance" else PAGE_SIZE
    cursor = closed_end
    range_end = closed_end
    skipped_gap = None
    while cursor > floor:
        start = max(floor, cursor - page_size * interval)
        page = _venue_page(market, timeframe, start, cursor, opener, venue, symbol)
        for ts in range(cursor - interval, start - 1, -interval):
            if ts >= range_end or ts in page:
                continue
            day_start = ts // 86400 * 86400
            next_day = day_start + 86400
            if next_day < range_end:
                return {"start": _iso(next_day)[:10], "end": _iso(range_end - 1)[:10],
                        "gap": _iso(ts), "skipped_gap": skipped_gap, "days_limit": days}
            if skipped_gap is None:
                skipped_gap = _iso(ts)
            range_end = day_start
        cursor = min(start, range_end)
        if cursor > floor:
            time.sleep(REQUEST_DELAY_SECONDS)
    return {"start": _iso(floor)[:10] if floor < range_end else None,
            "end": _iso(range_end - 1)[:10] if floor < range_end else end,
            "gap": None, "skipped_gap": skipped_gap, "days_limit": days}


def download_snapshot(market: str, timeframe: str, start: str, end: str,
                      directory: str | Path, *, now: int | None = None,
                      opener=urlopen, venue="Coinbase") -> tuple[Path, dict]:
    """Download a date-inclusive UTC range, verify continuity, and save it.

    Coinbase Exchange publishes at most 300 rows per candles request and may
    include a row before the requested start, so pages cover at most 299 target
    intervals. The requested range is conservatively capped at 30,000 bars.
    """
    if not isinstance(market, str) or not MARKET_RE.fullmatch(market):
        raise ValueError("market must use uppercase BASE/QUOTE symbols, such as ETH/USD.")
    if timeframe not in INTERVALS:
        raise ValueError("Historical intervals are limited to 5m and 1h.")
    if venue not in ("Coinbase", "Binance"):
        raise ValueError("venue must be Coinbase or Binance.")
    start_date, end_date = _date(start, "start"), _date(end, "end")
    if start_date > end_date:
        raise ValueError("Start date must be on or before the end date.")
    now = int(time.time()) if now is None else int(now)
    today = datetime.fromtimestamp(now, timezone.utc).date()
    if end_date > today:
        raise ValueError("End date cannot be in the future (UTC).")

    interval = INTERVALS[timeframe]
    start_ts = int(datetime.combine(start_date, daytime.min, timezone.utc).timestamp())
    requested_end = int(datetime.combine(end_date + timedelta(days=1), daytime.min, timezone.utc).timestamp())
    if math.ceil((requested_end - start_ts) / interval) > MAX_BARS:
        raise ValueError(f"Requested range exceeds the {MAX_BARS:,}-bar safety limit.")
    # Never persist the current, still-forming candle.
    end_ts = min(requested_end, (now // interval) * interval)
    if end_ts <= start_ts:
        raise ValueError("Requested range contains no closed candles yet.")

    symbol = _binance_symbol(market, opener) if venue == "Binance" else None
    page_size = 1000 if venue == "Binance" else PAGE_SIZE

    rows_by_ts = {}
    page_start = start_ts
    while page_start < end_ts:
        page_end = min(page_start + page_size * interval, end_ts)
        rows_by_ts.update(_venue_page(market, timeframe, page_start, page_end, opener, venue, symbol))
        page_start = page_end
        if page_start < end_ts:
            time.sleep(REQUEST_DELAY_SECONDS)

    expected = range(start_ts, end_ts, interval)
    for ts in expected:
        if ts not in rows_by_ts:
            raise RuntimeError(f"{market} {venue} history has a missing {timeframe} candle at {_iso(ts)}. Use Find available start to check the continuous range ending on your chosen date.")
    bars = [rows_by_ts[ts] for ts in expected]
    return write_snapshot(bars, market, timeframe, start_date.isoformat(), end_date.isoformat(),
                          directory, venue=venue)


def write_snapshot(bars, market, timeframe, start, end, directory, *, venue,
                   provider=None, provenance=None, id_prefix=None):
    """Publish normalized candles in the shared immutable history format."""
    if not bars:
        raise ValueError("Cannot publish an empty history snapshot.")
    output_dir = Path(directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    base, quote = market.split("/")
    safe_start, safe_end = start, end
    csv_lines = ["timestamp,open,high,low,close,volume\n"]
    for bar in bars:
        csv_lines.append(",".join((_iso(bar["ts"]), *(format(bar[k], ".15g")
                         for k in ("open", "high", "low", "close", "volume")))) + "\n")
    csv_content = "".join(csv_lines).encode("utf-8")
    fingerprint = hashlib.sha256(csv_content).hexdigest()
    venue_id = id_prefix or venue.lower()
    dataset_id = f"{venue_id}_{base}_{quote}_{timeframe}_{safe_start}_{safe_end}_{fingerprint[:12]}"
    csv_name = dataset_id + ".csv"
    metadata = {
        "id": dataset_id, "market": market, "timeframe": timeframe,
        "bars": len(bars), "start": _iso(bars[0]["ts"]), "end": _iso(bars[-1]["ts"]),
        "venue": venue, "provider": provider or ("Coinbase Exchange public candles" if venue == "Coinbase"
                                       else "Binance public candles"),
        "requested_start": safe_start, "requested_end": safe_end,
        "sha256": fingerprint, "filename": csv_name,
    }
    if provenance:
        metadata["provenance"] = provenance
    sidecar = (json.dumps(metadata, sort_keys=True, indent=2) + "\n").encode("utf-8")
    _atomic_create(output_dir / csv_name, csv_content)
    _atomic_create(output_dir / (dataset_id + ".json"), sidecar)
    return output_dir / csv_name, metadata
