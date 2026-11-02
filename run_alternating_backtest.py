"""Simple alternating-entry strategy for UI testing.

Buys every other candle, sells on the next candle.
This produces many clean entry/exit pairs so you can verify
that markers render correctly at different positions on the chart.

Usage:
    python run_alternating_backtest.py          # Default: ETH/BTC 5m
    python run_alternating_backtest.py --pairs BTC/USD SOL/USDT --timeframe 5m
"""

import argparse
import json
import os
from datetime import datetime, timedelta

import ccxt
import pandas as pd
import vectorbt as vbt


def download_ohlcv(symbol: str, timeframe: str = "5m", days: int = 14) -> pd.DataFrame | None:
    """Download OHLCV data from Kraken."""
    exchange = ccxt.kraken({"enableRateLimit": True})
    exchange.load_markets()

    if symbol not in exchange.symbols:
        print(f"  Symbol {symbol} not found on Kraken")
        return None

    since = exchange.parse8601((datetime.utcnow() - timedelta(days=days)).isoformat())

    all_rows = []
    limit = 1000
    while True:
        df = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        if not df or len(df) < limit:
            all_rows.extend(df)
            break
        all_rows.extend(df)
        since = int(df[-1][0]) + 1
        import time
        time.sleep(exchange.rateLimit / 1000.0)

    if not all_rows:
        return None

    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    return df


def run_alternating_strategy(close: pd.Series, price: pd.Series) -> vbt.Portfolio:
    """Buy on even-indexed candles, sell on odd-indexed.

    Uses vectorbt's from_in_order_signals for manual signal arrays.
    Entry signal: True on every other bar (0, 2, 4, ...)
    Exit signal: True on every other bar (1, 3, 5, ...)
    """
    n = len(close)

    # Alternating entry/exit signals
    entries = pd.Series(False, index=close.index)
    exits = pd.Series(False, index=close.index)

    for i in range(n):
        if i % 2 == 0:
            entries.iloc[i] = True  # buy on even bars
        else:
            exits.iloc[i] = True    # sell on odd bars

    portfolio = vbt.Portfolio.from_signals(
        close=close,
        entries=entries,
        exits=exits,
        freq="5min",
        size=100,  # fixed notional per trade for simplicity
    )
    return portfolio


def main():
    parser = argparse.ArgumentParser(description="Alternating-entry backtest for UI testing")
    parser.add_argument("--pairs", nargs="+", default=["ETH/BTC"], help="Asset pairs")
    parser.add_argument("--timeframe", default="5m", help="Candle timeframe (default: 5m)")
    parser.add_argument("--days", type=int, default=14, help="Days of history")
    parser.add_argument("--output", default="alternating_backtest.json", help="Output JSON file")
    args = parser.parse_args()

    results = []

    for symbol in args.pairs:
        print(f"\n{'='*60}")
        print(f"Processing {symbol} ({args.timeframe}, {args.days} days)")
        print("=" * 60)

        # Download data if not already cached
        safe_symbol = symbol.replace("/", "_")
        csv_path = os.path.join("data", f"{safe_symbol}_{args.timeframe}.csv")
        has_csv = os.path.exists(csv_path)

        if has_csv:
            print(f"  Loading from {csv_path}")
            df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
        else:
            print(f"  Downloading from Kraken...")
            df = download_ohlcv(symbol, args.timeframe, args.days)
            if df is None:
                print(f"  SKIPPED (no data)")
                continue
            os.makedirs("data", exist_ok=True)
            df.to_csv(csv_path)
            print(f"  Saved {len(df)} bars to {csv_path}")

        print(f"  Bars: {len(df)}, Range: {df.index[0]} to {df.index[-1]}")

        # Run strategy
        portfolio = run_alternating_strategy(df["close"], df["close"])

        # Extract trades as dicts for JSON serialization
        rec = portfolio.trades.records
        trades_list = []
        for i in range(len(rec)):
            row = rec.iloc[i]
            entry_price = float(row.get("entry_price", 0)) if pd.notna(row.get("entry_price")) else None
            exit_price = float(row.get("exit_price", 0)) if pd.notna(row.get("exit_price")) else None
            pnl_val = float(row.get("pnl", 0)) if pd.notna(row.get("pnl")) else None

            trades_list.append({
                "entry_idx": int(row["entry_idx"]) if pd.notna(row.get("entry_idx")) else None,
                "exit_idx": int(row["exit_idx"]) if pd.notna(row.get("exit_idx")) else None,
                "entry_price": round(entry_price, 8) if entry_price else None,
                "exit_price": round(exit_price, 8) if exit_price else None,
                "pnl": round(pnl_val, 4) if pnl_val is not None else None,
                "is_long": True,
            })

        # Build summary
        total = len(trades_list)
        winning = sum(1 for t in trades_list if t["pnl"] and t["pnl"] > 0)
        total_return = float(portfolio.total_return())
        max_dd = float(portfolio.max_drawdown())

        summary = {
            "total_trades": total,
            "winning_trades": winning,
            "losing_trades": total - winning,
            "win_rate": round(winning / total * 100, 2) if total > 0 else 0.0,
            "total_return_pct": round(total_return * 100, 4),
            "max_drawdown_pct": round(max_dd * 100, 4),
            "sharpe_ratio": None,
        }

        # Build data dict for chart (OHLCV)
        data_dict = {}
        for idx, row in df.iterrows():
            ts_key = str(idx)
            data_dict[ts_key] = {
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }

        result = {
            "symbol": symbol,
            "timeframe": args.timeframe,
            "data": data_dict,
            "trades": trades_list,
            "summary": summary,
        }
        results.append(result)

        print(f"  Trades: {total} ({winning} wins, {total - winning} losses)")
        print(f"  Win rate: {summary['win_rate']}%")
        print(f"  Total return: {summary['total_return_pct']:.2f}%")
        print(f"  Max drawdown: {summary['max_drawdown_pct']:.2f}%")

    # Save all results to one JSON file
    output_path = args.output
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Saved {len(results)} backtest(s) to {output_path}")
    for r in results:
        print(f"  {r['symbol']}: {r['summary']['total_trades']} trades")


if __name__ == "__main__":
    main()
