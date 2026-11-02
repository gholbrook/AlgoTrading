"""Run a backtest and register it with the AlgoTrading UI.

Usage:
    python run_backtest_and_show.py        # Runs ETH/BTC, starts the server
    python run_backtest_and_show.py --no-server  # Just registers data, doesn't start server
"""

import sys
import pandas as pd
import numpy as np
import vectorbt as vbt

from algo_trading_ui.app import register_asset, register_backtest


def generate_data(symbol: str = "ETH/BTC", timeframe: str = "5min") -> pd.DataFrame:
    """Generate OHLCV data (replace with your real Kraken/CCXT download)."""
    index = pd.date_range(start="2026-10-01", end="2026-11-01", freq=timeframe)
    df = pd.DataFrame(index=index)

    # Simulated price series (replace with real data from main.py or CCXTData)
    close = pd.Series(np.sin(np.linspace(0, 500, len(index))) * 0.003 + 0.07, index=index)
    df["close"] = close
    df["open"] = close.shift(1).fillna(0.07)
    df["high"] = close + 0.0002
    df["low"] = close - 0.0002
    df["volume"] = np.random.randint(100, 5000, len(index))

    return df


def run_backtest(df: pd.DataFrame) -> vbt.Portfolio:
    """Run the grid strategy backtest and return a Portfolio."""
    close_prices = df["close"]

    # Indicators
    regime_sma = vbt.MA.run(close_prices, window=200).ma
    sma_is_rising = regime_sma > regime_sma.shift(5)

    bb = vbt.BBANDS.run(close_prices, window=20, alpha=2.0)
    bb_lower = bb.lower
    bb_upper = bb.upper

    # Signals
    wick_touch_entry = (df["low"] <= bb_lower) & sma_is_rising
    wick_touch_short = (df["high"] >= bb_upper) & (~sma_is_rising)
    all_strategy_entries = wick_touch_entry | wick_touch_short

    # TP/SL matrices
    base_band_height = ((bb_upper - bb.middle) / bb.middle)
    tp_tier_1 = 0.0045
    tp_tier_2 = base_band_height * 1.5

    consecutive_entries = all_strategy_entries.groupby((~all_strategy_entries).cumsum()).cumsum()
    tp_target_matrix = np.where(consecutive_entries <= 1, tp_tier_1, tp_tier_2)
    tp_target_matrix = np.where(all_strategy_entries, tp_target_matrix, 0.015)
    sl_target_matrix = np.where(all_strategy_entries, 0.020, np.nan)

    portfolio = vbt.Portfolio.from_signals(
        close=close_prices,
        entries=all_strategy_entries,
        exits=None,
        tp_stop=tp_target_matrix,
        sl_stop=sl_target_matrix,
        init_cash=50000,
        fees=0.0020,
        size=0.02,
        size_type="percent",
        accumulate=True,
    )

    return portfolio


def main():
    # Disable numba parallel to avoid multiprocessing conflicts with uvicorn
    import os
    os.environ["NUMBA_NUM_THREADS"] = "1"
    os.environ["NUMBA_PARALLEL"] = "0"

    symbol = "ETH/BTC"
    timeframe = "5min"

    print(f"Generating data for {symbol}...")
    df = generate_data(symbol, timeframe)
    print(f"  {len(df)} bars loaded")

    # Register asset in UI
    register_asset(symbol, df, timeframe, "2026-10-01", "2026-11-01")

    # Run backtest
    print("Running backtest...")
    portfolio = run_backtest(df)
    print(portfolio.stats())

    # Register backtest in UI (only this asset pair can show it)
    register_backtest(symbol, portfolio)

    print(f"\n✓ {symbol} registered with {portfolio.trades.count()} trades")

    # Start the server
    import uvicorn
    print("\nStarting UI server on http://127.0.0.1:8000 ...")
    uvicorn.run("algo_trading_ui.app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
