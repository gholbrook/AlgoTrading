"""Download historical OHLCV data from CCXT exchanges and save as CSV.

Usage:
    python download_data.py                  # Default: ETH/BTC, 1h, last 90 days
    python download_data.py --pairs BTC/USD ETH/BTC SOL/USDT
    python download_data.py --exchange binance --timeframe 15min
    python download_data.py --days 365 --timeframe 1d
"""

import argparse
import os
import sys
from datetime import datetime, timedelta

import ccxt
import pandas as pd


def download_ohlcv(
    symbol: str,
    exchange_name: str = "binance",
    timeframe: str = "1h",
    days: int = 90,
    save_dir: str = "data",
    fallback_exchanges: list[str] | None = None,
) -> str | None:
    """Download OHLCV data and return the saved file path."""
    if fallback_exchanges is None:
        fallback_exchanges = ["kraken", "okx", "bitfinex", "coinbase", "gate"]

    # Try primary exchange first, then fall back
    exchanges_to_try = [exchange_name] + [
        e for e in fallback_exchanges if e != exchange_name
    ]

    last_error = None
    for ex_name in exchanges_to_try:
        try:
            return _download_from_exchange(
                symbol, ex_name, timeframe, days, save_dir
            )
        except Exception as e:
            last_error = e
            print(f"  {ex_name} failed: {str(e)[:100]}")

    print(f"  ERROR: All exchanges failed. Last error: {last_error}")
    return None


def _download_from_exchange(
    symbol: str,
    exchange_name: str,
    timeframe: str,
    days: int,
    save_dir: str,
) -> str | None:
    """Download from a single exchange (raises on failure)."""
    # Initialize exchange (no API keys needed for public market data)
    print(f"  Connecting to {exchange_name}...")
    exchange_class = getattr(ccxt, exchange_name, None)
    if exchange_class is None:
        print(f"  ERROR: Exchange '{exchange_name}' not found in ccxt")
        return None

    exchange = exchange_class({"enableRateLimit": True})

    # Check symbol exists
    try:
        exchange.load_markets()
    except Exception as e:
        raise RuntimeError(f"Failed to load markets from {exchange_name}: {e}") from e

    if symbol not in exchange.symbols:
        print(f"  ERROR: Symbol '{symbol}' not available on {exchange_name}")
        # Try with : spot suffix
        alt = f"{symbol}:spot"
        if alt in exchange.symbols:
            symbol = alt
        else:
            return None

    # Calculate start date
    since = exchange.parse8601(
        (datetime.utcnow() - timedelta(days=days)).isoformat()
    )

    print(f"  Downloading {symbol} ({timeframe}, last {days} days)...")
    all_rows = []
    limit = 1000  # max per request for most exchanges

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

        # CCXT returns [timestamp, open, high, low, close, volume]
        if len(df) < limit:
            break  # No more data

        # Move since forward by the last timestamp
        since = int(df[-1][0]) + 1

        # Rate limiting pause
        import time

        time.sleep(exchange.rateLimit / 1000.0)

    if not all_rows:
        print(f"  WARNING: No data returned for {symbol}")
        return None

    # Build DataFrame
    df = pd.DataFrame(
        all_rows,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)

    # Save
    os.makedirs(save_dir, exist_ok=True)
    safe_symbol = symbol.replace("/", "_").replace(":", "_")
    filename = f"{safe_symbol}_{timeframe}.csv"
    filepath = os.path.join(save_dir, filename)
    df.to_csv(filepath)

    print(
        f"  Saved {len(df)} bars to {filepath} "
        f"({df.index[0].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')})"
    )
    return filepath


def main():
    parser = argparse.ArgumentParser(description="Download OHLCV data from CCXT")
    parser.add_argument(
        "--pairs",
        nargs="+",
        default=["ETH/BTC"],
        help="Asset pairs to download (e.g. BTC/USD ETH/BTC SOL/USDT)",
    )
    parser.add_argument(
        "--exchange",
        default="binance",
        help="Exchange name (default: binance)",
    )
    parser.add_argument(
        "--timeframe",
        default="1h",
        help="Candlestick timeframe (default: 1h, also: 5m, 15m, 1d)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Number of days of history (default: 90)",
    )
    parser.add_argument(
        "--save-dir",
        default="data",
        help="Directory to save CSV files (default: data)",
    )
    args = parser.parse_args()

    print(f"Downloading {len(args.pairs)} asset pair(s) from {args.exchange}")
    print(f"Timeframe: {args.timeframe}, Days: {args.days}\n")

    saved = []
    for symbol in args.pairs:
        path = download_ohlcv(
            symbol, args.exchange, args.timeframe, args.days, args.save_dir,
            fallback_exchanges=["kraken", "okx", "bitfinex", "coinbase", "gate"],
        )
        if path:
            saved.append(path)
        print()

    print(f"Done. {len(saved)}/{len(args.pairs)} pairs saved.")
    for p in saved:
        print(f"  {p}")


if __name__ == "__main__":
    main()
