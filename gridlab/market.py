"""Public Kraken Spot OHLC adapter using only Python's standard library."""

from __future__ import annotations

import json
import math
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://api.kraken.com/0/public/OHLC"
TIMEOUT_SECONDS = 10

TIMEFRAMES = {
    "1m": (1, 60), "5m": (5, 300), "15m": (15, 900),
    "30m": (30, 1800), "1h": (60, 3600), "4h": (240, 14400),
    "1d": (1440, 86400), "1w": (10080, 604800),
}

# Kraken's public REST aliases use XBT for Bitcoin. These common currency
# spellings are equivalent on Spot; Kraken resolves pair names case-insensitively.
ASSET_CODES = {"BTC": "XBT", "XBT": "XBT", "USD": "USD", "USDT": "USDT",
               "ETH": "ETH", "SOL": "SOL", "ADA": "ADA", "DOT": "DOT",
               "XRP": "XRP", "EUR": "EUR", "GBP": "GBP", "USDC": "USDC"}


def _interval(timeframe: str) -> int:
    try:
        return TIMEFRAMES[timeframe][1]
    except KeyError:
        raise ValueError(f"unsupported Kraken timeframe {timeframe!r}") from None


def _pair(market: str) -> str:
    try:
        base, quote = market.upper().split("/")
    except ValueError:
        raise ValueError(f"market must be BASE/QUOTE: {market!r}") from None
    if not base or not quote or base not in ASSET_CODES or quote not in ASSET_CODES:
        raise ValueError(f"unsupported Kraken market {market!r}")
    return ASSET_CODES[base] + ASSET_CODES[quote]


def validate_bar(bar: dict) -> dict:
    """Validate and normalize an OHLCV bar, returning timestamp in seconds."""
    if not isinstance(bar, dict):
        raise ValueError("bar must be a mapping")
    try:
        raw_ts = bar["ts"]
        ts_num = float(raw_ts)
        if not math.isfinite(ts_num) or not ts_num.is_integer() or ts_num < 0:
            raise ValueError("ts must be a non-negative integer Unix timestamp in seconds")
        values = {key: float(bar[key]) for key in ("open", "high", "low", "close", "volume")}
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError(f"invalid OHLCV bar: {exc}") from exc
    if not all(math.isfinite(v) for v in values.values()):
        raise ValueError("OHLCV values must be finite")
    if any(values[k] <= 0 for k in ("open", "high", "low", "close")):
        raise ValueError("OHLC prices must be positive")
    if values["volume"] < 0:
        raise ValueError("volume must be non-negative")
    if values["high"] < max(values["open"], values["close"], values["low"]):
        raise ValueError("high must be at least open, close, and low")
    if values["low"] > min(values["open"], values["close"], values["high"]):
        raise ValueError("low must be at most open, close, and high")
    return {"ts": int(ts_num), **values}


def fetch_closed(market: str, timeframe: str, since: int | None = None) -> list[dict]:
    """Fetch verified closed Kraken candles, excluding the forming final row.

    Kraken's Spot OHLC response always includes its current, uncommitted candle
    as the final entry. We unconditionally drop that entry. The endpoint is
    bounded to its most recent 720 rows; callers should persist the cursor and
    use ``since`` for incremental polling. A bootstrap older than two candle
    intervals is rejected as stale rather than silently starting from old data.
    """
    minutes, interval = TIMEFRAMES.get(timeframe, (None, None))
    if minutes is None:
        raise ValueError(f"unsupported Kraken timeframe {timeframe!r}")
    params = {"pair": _pair(market), "interval": minutes}
    if since is not None:
        if isinstance(since, bool) or int(since) < 0:
            raise ValueError("since must be a non-negative Unix timestamp in seconds")
        params["since"] = int(since)
    request = Request(BASE_URL + "?" + urlencode(params), headers={"User-Agent": "GridLab/1.0"})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Kraken OHLC request failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Kraken OHLC response is not a JSON object")
    errors = payload.get("error") or []
    if errors:
        raise RuntimeError("Kraken OHLC error: " + "; ".join(map(str, errors)))
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Kraken OHLC response has no result object")
    pair_keys = [key for key in result if key != "last"]
    if len(pair_keys) != 1 or not isinstance(result[pair_keys[0]], list):
        raise RuntimeError("Kraken OHLC response has an unexpected pair payload")
    rows = result[pair_keys[0]]
    if not rows:
        raise RuntimeError("Kraken returned no OHLC rows")
    if len(rows) == 1 and since is None:
        raise RuntimeError("Kraken returned only the current forming candle")
    closed_rows = rows[:-1]
    output = []
    for row in closed_rows:
        if not isinstance(row, (list, tuple)) or len(row) < 7:
            raise RuntimeError("Kraken returned a malformed OHLC row")
        bar = validate_bar({"ts": row[0], "open": row[1], "high": row[2], "low": row[3],
                            "close": row[4], "volume": row[6]})
        if bar["ts"] % interval != 0:
            raise RuntimeError(f"Kraken candle timestamp {bar['ts']} is not aligned to {timeframe}")
        if since is None or bar["ts"] > int(since):
            output.append(bar)
    output.sort(key=lambda b: b["ts"])
    now = int(time.time())
    if not output:
        if since is not None:
            lag = now - (int(since) + interval)
            if lag > interval * 2:
                raise RuntimeError(f"Kraken OHLC data is stale by {lag} seconds")
            return []
        raise RuntimeError("Kraken returned no closed candles")
    newest_close = output[-1]["ts"] + interval
    if newest_close > now:
        # A timestamp near the current bucket boundary is not enough proof that
        # Kraken has committed the frame; discard rather than trade it.
        output = [bar for bar in output if bar["ts"] + interval <= now]
    if not output:
        raise RuntimeError("Kraken returned no confirmed closed candles")
    age = now - (output[-1]["ts"] + interval)
    if age > interval * 2:
        raise RuntimeError(f"Kraken OHLC data is stale by {age} seconds")
    return output
