"""Live websocket data feed and paper trading engine.

Connects to Kraken via WebSocket (5-min candles), downloads historical data
first so indicators have sufficient lookback, then appends new candles as they
arrive. The strategy runs on the combined historical + live dataset. Results are
saved to a JSON file so the UI can display them.

Usage:
    python live_paper_trade.py [--pairs ETH/BTC BTC/USD] [--timeframe 5min]
                               [--output live_trades.json] [--history-days 30]
"""

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import ccxt
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_PAIRS = ["ETH/BTC"]
DEFAULT_TIMEFRAME = "5min"
DEFAULT_OUTPUT = "live_trades.json"
LIVE_DATA_DIR = "data/live"

# Strategy parameters (mirrors main.py)
SMA_WINDOW = 200
BB_WINDOW = 20
BB_ALPHA = 2.0
TP_TIER_1 = 0.0005       # 0.05% micro-churn for quick exits
SL_STOP = 0.005          # 0.5% hard stop (tighter for testing)
INITIAL_CASH = 50000.0
FEES = 0.0020            # Kraken Tier 4 Maker
SIZE_PCT = 0.02          # 2% per position


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class Position:
    """A single open paper position."""
    def __init__(self, symbol: str, direction: str, entry_price: float,
                 entry_time: pd.Timestamp, size: float):
        self.symbol = symbol
        self.direction = direction  # "long" or "short"
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.size = size           # notional USD
        self.consecutive_count = 1  # tracks consecutive entries for tiered TP


class TradeRecord:
    """A completed trade (entry + exit)."""
    def __init__(self, symbol: str, direction: str, entry_price: float,
                 exit_price: Optional[float], entry_time: pd.Timestamp,
                 exit_time: Optional[pd.Timestamp], pnl: Optional[float],
                 exit_reason: str):
        self.symbol = symbol
        self.direction = direction
        self.entry_price = round(entry_price, 8)
        self.exit_price = round(exit_price, 8) if exit_price else None
        self.entry_time = entry_time.isoformat()
        self.exit_time = exit_time.isoformat() if exit_time else None
        self.pnl = round(pnl, 4) if pnl is not None else None
        self.exit_reason = exit_reason

    def to_dict(self) -> dict:
        return {
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "entry_time": self.entry_time,
            "exit_time": self.exit_time,
            "pnl": self.pnl,
            "exit_reason": self.exit_reason,
            "is_long": self.direction == "long",
        }


# ---------------------------------------------------------------------------
# Indicator computation
# ---------------------------------------------------------------------------

def compute_indicators(df: pd.DataFrame) -> dict:
    """Compute SMA(200) and BBands(20,2) from OHLCV DataFrame."""
    close = df["close"]
    sma = close.rolling(window=SMA_WINDOW).mean()
    sma_rising = sma > sma.shift(5)

    bb_mid = close.rolling(window=BB_WINDOW).mean()
    bb_std = close.rolling(window=BB_WINDOW).std()
    bb_upper = bb_mid + BB_ALPHA * bb_std
    bb_lower = bb_mid - BB_ALPHA * bb_std

    return {
        "sma": sma,
        "sma_rising": sma_rising,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "bb_mid": bb_mid,
        "band_height": (bb_upper - bb_mid) / bb_mid,
    }


# ---------------------------------------------------------------------------
# Paper trading engine
# ---------------------------------------------------------------------------

class PaperTrader:
    """Manages open positions and executes trades."""

    def __init__(self, initial_cash: float = INITIAL_CASH):
        self.cash = initial_cash
        self.positions: dict[str, list[Position]] = {}  # symbol -> [positions]
        self.trades: list[TradeRecord] = []
        self.equity_history: list[dict] = []

    def check_exits(self, symbol: str, current_price: float,
                    current_time: pd.Timestamp, indicators: dict):
        """Check all open positions for TP/SL exits."""
        if symbol not in self.positions:
            return

        remaining = []
        for pos in self.positions[symbol]:
            exit_reason = None
            exit_price = None

            # Calculate P&L ratio
            if pos.direction == "long":
                pnl_ratio = (current_price - pos.entry_price) / pos.entry_price
            else:  # short
                pnl_ratio = (pos.entry_price - current_price) / pos.entry_price

            # Check stop loss first
            if pnl_ratio <= -SL_STOP:
                exit_reason = "sl"
                exit_price = current_price

            # Check take-profit tiers
            if exit_reason is None:
                consecutive = pos.consecutive_count
                band_h = indicators["band_height"].iloc[-1] if indicators.get("band_height") is not None else 0.005
                tp_target = TP_TIER_1 if consecutive <= 1 else min(band_h * 1.5, 0.015)

                if pnl_ratio >= tp_target:
                    exit_reason = "tp_tier_2" if consecutive > 1 else "tp_tier_1"
                    exit_price = current_price

            if exit_reason:
                # Calculate P&L
                if pos.direction == "long":
                    pnl = (current_price - pos.entry_price) / pos.entry_price * pos.size
                else:
                    pnl = (pos.entry_price - current_price) / pos.entry_price * pos.size

                trade = TradeRecord(
                    symbol=symbol,
                    direction=pos.direction,
                    entry_price=pos.entry_price,
                    exit_price=exit_price or current_price,
                    entry_time=pos.entry_time,
                    exit_time=current_time,
                    pnl=pnl,
                    exit_reason=exit_reason,
                )
                self.trades.append(trade)

                # Return capital + P&L
                self.cash += pos.size + pnl
            else:
                remaining.append(pos)

        self.positions[symbol] = remaining

    def check_entries(self, symbol: str, current_price: float,
                      current_time: pd.Timestamp, indicators: dict):
        """Check for new entry signals on this candle."""
        # Alternating buy/sell every candle (UI test strategy)
        if len(df_cache.get(symbol, pd.DataFrame())) > 0:
            bar_count = len(df_cache[symbol])
            if bar_count % 2 == 0:
                self._open_position(symbol, "long", current_price, current_time)
            else:
                self._open_position(symbol, "short", current_price, current_time)

    def _open_position(self, symbol: str, direction: str, price: float,
                       time: pd.Timestamp):
        """Open a new paper position."""
        size = INITIAL_CASH * SIZE_PCT  # notional per trade
        if size > self.cash:
            return

        pos = Position(symbol, direction, price, time, size)
        self.positions.setdefault(symbol, []).append(pos)
        self.cash -= size


# ---------------------------------------------------------------------------
# Historical data download (CCXT)
# ---------------------------------------------------------------------------

def _download_history_for_pair(symbol: str, timeframe: str = "5m",
                                days: int = 30,
                                save_dir: str = LIVE_DATA_DIR) -> Optional[str]:
    """Download historical OHLCV data and merge into live CSV.

    Downloads enough history so indicators (SMA-200, BBands) have sufficient
    lookback. Merges with any existing live data, preserving the newest candles.
    Saves combined dataset to data/live/{SYMBOL}_live_5m.csv.

    Returns the path to the saved file, or None on failure.
    """
    safe_symbol = symbol.replace("/", "_")
    # Always use 5m suffix for consistency
    csv_path = os.path.join(save_dir, f"{safe_symbol}_live_5m.csv")

    # Load any existing live data first (newer candles)
    existing_live = pd.DataFrame()
    if os.path.exists(csv_path):
        try:
            existing_live = pd.read_csv(csv_path, index_col=0, parse_dates=True)
            print(f"  Found {len(existing_live)} existing live bars for {symbol}")
        except Exception:
            pass

    # Also check for old _live_5min.csv files and load them as fallback
    if existing_live.empty:
        old_path = os.path.join(save_dir, f"{safe_symbol}_live_5min.csv")
        if os.path.exists(old_path):
            try:
                existing_live = pd.read_csv(old_path, index_col=0, parse_dates=True)
                print(f"  Found {len(existing_live)} bars in old _live_5min.csv for {symbol}")
                # Copy to new filename
                existing_live.to_csv(csv_path)
            except Exception:
                pass

    # Determine which exchange to use (Kraken is the same one we stream from)
    fallback = ["kraken", "okx", "bitfinex", "coinbase", "gate"]
    df_history = None

    for ex_name in ["kraken"] + [e for e in fallback if e != "kraken"]:
        try:
            df_history = _fetch_ohlcv(symbol, ex_name, timeframe, days)
            if df_history is not None and len(df_history) > 0:
                print(f"  Downloaded {len(df_history)} historical bars from {ex_name}")
                break
        except Exception as e:
            print(f"  {ex_name} failed: {str(e)[:100]}")

    if df_history is None or len(df_history) == 0:
        print(f"  WARNING: No historical data for {symbol}, will use WebSocket bars only")
        return csv_path

    # Merge: historical + existing live (deduplicate by timestamp, keep last)
    all_data = pd.concat([df_history, existing_live]) if len(existing_live) > 0 else df_history
    all_data = all_data[~all_data.index.duplicated(keep='last')]
    all_data = all_data.sort_index()

    # Save combined dataset
    os.makedirs(save_dir, exist_ok=True)
    all_data.to_csv(csv_path)
    print(f"  Saved combined {safe_symbol}: {len(all_data)} bars "
          f"({all_data.index[0].strftime('%Y-%m-%d')} to {all_data.index[-1].strftime('%Y-%m-%d')})")

    return csv_path


def _fetch_ohlcv(symbol: str, exchange_name: str, timeframe: str,
                 days: int) -> pd.DataFrame:
    """Fetch OHLCV data from a single CCXT exchange."""
    exchange_class = getattr(ccxt, exchange_name, None)
    if exchange_class is None:
        raise RuntimeError(f"Exchange '{exchange_name}' not found in ccxt")

    exchange = exchange_class({"enableRateLimit": True})
    exchange.load_markets()

    # CCXT timeframe format (Kraken uses 5m, Binance uses 5m too)
    if timeframe == "5min":
        tf = "5m"
    elif timeframe == "1h":
        tf = "1h"
    else:
        tf = timeframe

    since = exchange.parse8601(
        (datetime.utcnow() - timedelta(days=days)).isoformat()
    )

    all_rows = []
    limit = 1000

    while True:
        try:
            df = exchange.fetch_ohlcv(symbol, timeframe=tf, since=since, limit=limit)
        except Exception as e:
            raise RuntimeError(f"Fetch failed from {exchange_name}: {e}") from e

        if not df or len(df) == 0:
            break

        all_rows.extend(df)
        if len(df) < limit:
            break

        since = int(df[-1][0]) + 1
        time.sleep(exchange.rateLimit / 1000.0)

    if not all_rows:
        raise RuntimeError(f"No data returned for {symbol} from {exchange_name}")

    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    return df


# ---------------------------------------------------------------------------
# Live CSV management
# ---------------------------------------------------------------------------

def _append_live_csv(symbol: str, timestamp: pd.Timestamp, open_price: float,
                     high_price: float, low_price: float, close_price: float,
                     volume: float):
    """Append a new candle to the live CSV file for UI consumption."""
    os.makedirs(LIVE_DATA_DIR, exist_ok=True)
    safe_symbol = symbol.replace("/", "_")
    csv_path = os.path.join(LIVE_DATA_DIR, f"{safe_symbol}_live_5m.csv")

    # Check if file exists; if so, check if we already have this timestamp
    if os.path.exists(csv_path):
        try:
            existing = pd.read_csv(csv_path, index_col=0, parse_dates=True)
            if len(existing) > 0 and existing.index[-1] >= timestamp:
                # Already have this candle or newer — update in-place
                existing.loc[timestamp] = [open_price, high_price, low_price, close_price, volume]
                existing.to_csv(csv_path)
                return
        except Exception:
            pass

    # Append new row
    row = pd.DataFrame({
        "open": [open_price], "high": [high_price], "low": [low_price],
        "close": [close_price], "volume": [volume],
    }, index=pd.DatetimeIndex([timestamp]))

    if os.path.exists(csv_path):
        try:
            existing = pd.read_csv(csv_path, index_col=0, parse_dates=True)
            combined = pd.concat([existing, row])
            # Remove duplicates keeping last
            combined = combined[~combined.index.duplicated(keep='last')]
            combined.to_csv(csv_path)
        except Exception:
            row.to_csv(csv_path)
    else:
        row.to_csv(csv_path)


# ---------------------------------------------------------------------------
# Global state for live trading engine
# ---------------------------------------------------------------------------

# Global cache for OHLCV data (symbol -> DataFrame) — includes historical + live
df_cache: dict[str, pd.DataFrame] = {}
paper_trader: Optional[PaperTrader] = None
output_file: str = DEFAULT_OUTPUT


def _run_strategy(symbol: str, df: pd.DataFrame, timestamp: pd.Timestamp):
    """Run the paper trading strategy on a new candle."""
    global paper_trader

    if len(df) < SMA_WINDOW:
        return  # Not enough data for indicators

    if paper_trader is None:
        paper_trader = PaperTrader()

    indicators = compute_indicators(df)
    current_price = df["close"].iloc[-1]
    
    # Debug: log indicator state every poll (suppress after first 3 polls)
    if not hasattr(_run_strategy, '_poll_count'):
        _run_strategy._poll_count = 0
    _run_strategy._poll_count += 1
    pcnt = _run_strategy._poll_count
    sma_val = indicators["sma"].iloc[-1]
    bb_lower = indicators["bb_lower"].iloc[-1]
    bb_upper = indicators["bb_upper"].iloc[-1]
    sma_rising = indicators["sma_rising"].iloc[-1]
    if pcnt <= 3:
        print(f"  [STRATEGY] {symbol}: Price={current_price:.6f} SMA={sma_val:.6f} BB_L={bb_lower:.6f} BB_U={bb_upper:.6f} rising={sma_rising}")

    # Check exits first, then entries
    paper_trader.check_exits(symbol, current_price, timestamp, indicators)
    paper_trader.check_entries(symbol, current_price, timestamp, indicators)
    
    if pcnt <= 3:
        n_positions = len(paper_trader.positions.get(symbol, []))
        n_trades = sum(1 for t in paper_trader.trades if t.symbol == symbol)
        print(f"  [STRATEGY] {symbol}: positions={n_positions}, trades={n_trades}")

    # Record equity snapshot
    open_notional = sum(
        pos.size for pos_list in paper_trader.positions.values()
        for pos in pos_list
    )
    equity = paper_trader.cash + open_notional
    paper_trader.equity_history.append({
        "timestamp": timestamp.isoformat(),
        "equity": round(equity, 4),
        "cash": round(paper_trader.cash, 4),
        "positions": len(sum(paper_trader.positions.values(), [])),
    })


def save_results():
    """Save paper trading results to JSON — supports multiple symbols."""
    global paper_trader

    if paper_trader is None:
        return

    # Build per-symbol trade lists (completed trades)
    symbol_trades = {}
    for t in paper_trader.trades:
        sym = t.symbol
        if sym not in symbol_trades:
            symbol_trades[sym] = []
        symbol_trades[sym].append(t.to_dict())

    # Also include open positions as pending trades
    for sym, pos_list in paper_trader.positions.items():
        if sym not in symbol_trades:
            symbol_trades[sym] = []
        for pos in pos_list:
            symbol_trades[sym].append({
                "entry_price": pos.entry_price,
                "exit_price": None,
                "entry_time": pos.entry_time.isoformat(),
                "exit_time": None,
                "pnl": None,
                "exit_reason": "open",
                "is_long": pos.direction == "long",
            })

    # Build summary per symbol
    symbol_summary = {}
    for sym, trades in symbol_trades.items():
        symbol_summary[sym] = {
            "total_trades": len(trades),
            "winning_trades": sum(1 for t in trades if t.get("pnl") and t["pnl"] > 0),
            "losing_trades": sum(1 for t in trades if t.get("pnl") is not None and t["pnl"] <= 0),
            "total_pnl": round(sum(t.get("pnl", 0) or 0 for t in trades), 4),
        }

    # Build per-symbol OHLCV start dates
    symbol_start = {}
    for sym, df in df_cache.items():
        if len(df) > 0:
            symbol_start[sym] = str(df.index[0])

    result = {
        "symbols": list(symbol_trades.keys()),
        "trades": symbol_trades,
        "summary": symbol_summary,
        "start_dates": symbol_start,
        "end_date": str(datetime.now(timezone.utc).isoformat()),
    }

    # Merge with existing file if present
    if os.path.exists(output_file):
        try:
            with open(output_file) as f:
                existing = json.load(f)
            for sym in symbol_trades:
                existing_trades = {t["entry_time"]: t for t in existing.get("trades", {}).get(sym, [])}
                for t in symbol_trades[sym]:
                    existing_trades[t["entry_time"]] = t
                result["trades"][sym] = list(existing_trades.values())
            # Preserve existing data for symbols not yet in new result
            for sym in existing.get("symbols", []):
                if sym not in result["trades"]:
                    result["trades"][sym] = existing.get("trades", {}).get(sym, [])
                    result["summary"][sym] = existing.get("summary", {}).get(sym, {})
                    if sym in existing.get("start_dates", {}):
                        result.setdefault("start_dates", {})[sym] = existing["start_dates"][sym]
            result["symbols"] = list(result["trades"].keys())
            result["end_date"] = str(datetime.now(timezone.utc).isoformat())
        except (json.JSONDecodeError, KeyError):
            pass

    with open(output_file, "w") as f:
        json.dump(result, f, indent=2)

    total = sum(len(v) for v in result["trades"].values())
    print(f"[SAVE] {total} trades saved to {output_file} ({', '.join(result['symbols'])})")


# ---------------------------------------------------------------------------
# Live data polling (REST API)
# Kraken v2 WebSocket no longer supports the old subscribe format,
# so we poll the REST OHLC endpoint every 60 seconds instead.
# ---------------------------------------------------------------------------

def _fetch_latest_candles(symbol: str, exchange: ccxt.kraken, timeframe: str = "5m",
                           limit: int = 10) -> pd.DataFrame:
    """Fetch the latest N candles from Kraken REST API."""
    try:
        rows = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True, drop=True)
        return df
    except Exception as e:
        print(f"  [FETCH] Error fetching {symbol}: {e}")
        return pd.DataFrame()


def _poll_and_update(symbol: str, exchange: ccxt.kraken, timeframe: str = "5m"):
    """Poll Kraken REST for latest candles and update live CSV + strategy."""
    print(f"[POLL] {symbol} starting...")
    df_new = _fetch_latest_candles(symbol, exchange, timeframe)
    if df_new.empty:
        print(f"  [POLL] {symbol}: df_new empty, skipping")
        return

    safe_symbol = symbol.replace("/", "_")
    csv_path = os.path.join(LIVE_DATA_DIR, f"{safe_symbol}_live_5m.csv")

    # Load existing live data
    existing = pd.DataFrame()
    if os.path.exists(csv_path):
        try:
            existing = pd.read_csv(csv_path, index_col=0, parse_dates=True)
            if not existing.empty:
                # Ensure consistent timezone-aware UTC index
                if existing.index.tzinfo is None:
                    existing.index = existing.index.tz_localize("UTC")
        except Exception as e:
            print(f"  [LOAD] Error reading {csv_path}: {e}")
            return

    if existing.empty and df_new.empty:
        return

    # Combine and deduplicate — ensure both have consistent index types
    try:
        if not existing.empty and not df_new.empty:
            all_data = pd.concat([existing, df_new])
        elif not existing.empty:
            all_data = existing
        else:
            all_data = df_new

        # Deduplicate by timestamp (keep last), sort
        all_data = all_data[~all_data.index.duplicated(keep='last')]
        all_data = all_data.sort_index()

        # Ensure timezone-aware UTC index
        if all_data.index.tzinfo is None:
            all_data.index = all_data.index.tz_localize("UTC")
    except Exception as e:
        print(f"  [MERGE] Error merging data for {symbol}: {e}")
        return

    # Save updated CSV
    os.makedirs(LIVE_DATA_DIR, exist_ok=True)
    all_data.to_csv(csv_path)

    # Update in-memory cache for strategy evaluation
    df_cache[symbol] = all_data

    # Run strategy on the latest candle
    if len(all_data) >= SMA_WINDOW:
        print(f"  [STRATEGY] Running for {symbol}, bars={len(all_data)}, last_ts={all_data.index[-1]}")
        _run_strategy(symbol, all_data, all_data.index[-1])
    else:
        print(f"  [STRATEGY] Skipping {symbol}: {len(all_data)} < {SMA_WINDOW}")


def main():
    global output_file

    parser = argparse.ArgumentParser(description="Live paper trading with Kraken REST API polling")
    parser.add_argument("--pairs", nargs="+", default=DEFAULT_PAIRS,
                        help="Trading pairs (e.g. ETH/BTC BTC/USD)")
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help="Output JSON file path")
    parser.add_argument("--history-days", type=int, default=30,
                        help="Days of historical data to download for indicator warmup (default: 30)")
    args = parser.parse_args()

    output_file = args.output

    # Signal handling for graceful shutdown
    def handle_signal(signum, frame):
        print(f"\n[SHUTDOWN] Received signal {signum}, saving results...")
        save_results()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Download historical data for each pair so indicators have warmup
    print(f"Downloading {args.history_days} days of history for indicator warmup...")
    for symbol in args.pairs:
        _download_history_for_pair(symbol, args.timeframe, args.history_days)

    # Load combined data into memory
    print("\nLoading combined data into memory...")
    for symbol in args.pairs:
        safe_symbol = symbol.replace("/", "_")
        csv_path = os.path.join(LIVE_DATA_DIR, f"{safe_symbol}_live_5m.csv")
        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
            df_cache[symbol] = df
            print(f"  Loaded {len(df)} bars for {symbol}")

    # Initialize CCXT exchange for polling
    exchange = ccxt.kraken({"enableRateLimit": True})
    exchange.load_markets()
    print(f"\nPolling Kraken REST API every 60 seconds...")
    print(f"Pairs: {args.pairs}")
    print(f"Output: {output_file}")
    print()

    while True:
        for symbol in args.pairs:
            _poll_and_update(symbol, exchange, "5m")
        time.sleep(60)  # Poll every 60 seconds
        # Save results periodically (every poll cycle)
        save_results()


if __name__ == "__main__":
    main()
