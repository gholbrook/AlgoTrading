"""Download 5-minute OHLCV data for UI testing.

Usage:
    python download_5min.py              # Default pairs, last 14 days
    python download_5min.py --pairs BTC/USD ETH/BTC SOL/USDT --days 30
"""

import argparse
import os
from datetime import datetime, timedelta

import ccxt
import pandas as pd


def download_5min(
    symbol: str,
    exchange_name: str = "kraken",
    days: int = 14,
    save_dir: str = "data",
) -> str | None:
    """Download 5-minute OHLCV data from an exchange."""
    print(f"  Connecting to {exchange_name}...")
    exchange_class = getattr(ccxt, exchange_name, None)
    if exchange_class is None:
        print(f"  ERROR: Exchange '{exchange_name}' not found in ccxt")
        return None

    exchange = exchange_class({"enableRateLimit": True})

    try:
        exchange.load_markets()
    except Exception as e:
        raise RuntimeError(f"Failed to load markets from {exchange_name}: {e}") from e

    if symbol not in exchange.symbols:
        print(f"  ERROR: Symbol '{symbol}' not available on {exchange_name}")
        return None

    since = exchange.parse8601(
        (datetime.utcnow() - timedelta(days=days)).isoformat()
    )

    timeframe = "5m"
    limit = 1000

    print(f"  Downloading {symbol} ({timeframe}, last {days} days)...")
    all_rows = []

    while True:
        try:
            df = exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, since=since, limit=limit
            )
        except Exception as e:
            print(f"  ERROR fetching data: {e}")
            return None

        if not df or len(df) == 0:
            break

        all_rows.extend(df)

        if len(df) < limit:
            break

        since = int(df[-1][0]) + 1
        import time
        time.sleep(exchange.rateLimit / 1000.0)

    if not all_rows:
        print(f"  WARNING: No data returned for {symbol}")
        return None

    df = pd.DataFrame(
        all_rows,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)

    os.makedirs(save_dir, exist_ok=True)
    safe_symbol = symbol.replace("/", "_").replace(":", "_")
    filename = f"{safe_symbol}_{timeframe}.csv"
    filepath = os.path.join(save_dir, filename)
    df.to_csv(filepath)

    print(
        f"  Saved {len(df)} bars to {filepath} "
        f"({df.index[0].strftime('%Y-%m-%d %H:%M')} to {df.index[-1].strftime('%Y-%m-%d %H:%M')})"
    )
    return filepath


def main():
    parser = argparse.ArgumentParser(description="Download 5-minute OHLCV data for UI testing")
    parser.add_argument(
        "--pairs",
        nargs="+",
        default=["ETH/BTC", "BTC/USD"],
        help="Asset pairs to download (e.g. BTC/USD ETH/BTC SOL/USDT)",
    )
    parser.add_argument("--exchange", default="kraken", help="Exchange name (default: kraken)")
    parser.add_argument("--days", type=int, default=14, help="Number of days (default: 14)")
    parser.add_argument("--save-dir", default="data", help="Directory to save CSV files")
    args = parser.parse_args()

    print(f"Downloading {len(args.pairs)} asset pair(s) at 5-min from {args.exchange}")
    print(f"Days: {args.days}\n")

    saved = []
    for symbol in args.pairs:
        path = download_5min(symbol, args.exchange, args.days, args.save_dir)
        if path:
            saved.append(path)
        print()

    print(f"Done. {len(saved)}/{len(args.pairs)} pairs saved.")
    for p in saved:
        print(f"  {p}")


if __name__ == "__main__":
    main()
