"""Run a backtest, save results to JSON, then start the UI server.

The backtest runs in a subprocess to avoid numba/loky multiprocessing conflicts
with uvicorn's async event loop. Results are saved to disk and loaded by the server.
"""

import json
import os
import sys
import pandas as pd
import numpy as np


def generate_data(symbol: str = "ETH/BTC", timeframe: str = "5min") -> dict:
    """Generate OHLCV data and return as serializable dict."""
    index = pd.date_range(start="2026-10-01", end="2026-11-01", freq=timeframe)
    close = pd.Series(np.sin(np.linspace(0, 500, len(index))) * 0.003 + 0.07, index=index)

    df = pd.DataFrame(index=index)
    df["close"] = close
    df["open"] = close.shift(1).fillna(0.07)
    df["high"] = close + 0.0002
    df["low"] = close - 0.0002
    df["volume"] = np.random.randint(100, 5000, len(index))

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "start_date": str(index[0]),
        "end_date": str(index[-1]),
        # Convert Timestamp keys to strings for JSON compatibility
        "data": {str(k): v for k, v in df.to_dict(orient="index").items()},
    }


def run_backtest(data: dict) -> dict:
    """Run the backtest in this process (numba won't conflict with server yet)."""
    import vectorbt as vbt

    raw = data["data"]
    # Reconstruct DataFrame from dict
    from collections import OrderedDict
    reconstructed = {}
    for idx_str, row in raw.items():
        for k, v in row.items():
            if k not in reconstructed:
                reconstructed[k] = []
            reconstructed[k].append(v)

    df = pd.DataFrame(reconstructed, index=pd.to_datetime(list(raw.keys())))
    close_prices = df["close"]

    regime_sma = vbt.MA.run(close_prices, window=200).ma
    sma_is_rising = regime_sma > regime_sma.shift(5)

    bb = vbt.BBANDS.run(close_prices, window=20, alpha=2.0)

    wick_touch_entry = (df["low"] <= bb.lower) & sma_is_rising
    wick_touch_short = (df["high"] >= bb.upper) & (~sma_is_rising)
    all_strategy_entries = wick_touch_entry | wick_touch_short

    base_band_height = ((bb.upper - bb.middle) / bb.middle)
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

    # Extract trades as serializable data from records DataFrame
    rec = portfolio.trades.records
    trades_data = []
    for i in range(len(rec)):
        row = rec.iloc[i]
        entry_idx_val = row["entry_idx"]
        exit_idx_val = row["exit_idx"]
        entry_price = float(row["entry_price"])
        exit_price = float(row["exit_price"]) if pd.notna(row.get("exit_price")) else None
        pnl_val = float(row["pnl"]) if pd.notna(row.get("pnl")) else None

        trades_data.append({
            "entry_idx": int(entry_idx_val) if pd.notna(entry_idx_val) else None,
            "exit_idx": int(exit_idx_val) if pd.notna(exit_idx_val) else None,
            "entry_price": round(entry_price, 8),
            "exit_price": round(exit_price, 8) if exit_price is not None else None,
            "pnl": round(pnl_val, 4) if pnl_val is not None else None,
            "is_long": bool(int(row.get("direction", 1)) == 1),
        })

    summary = {
        "total_trades": int(len(rec)),
        "winning_trades": int((rec["pnl"] > 0).sum()) if "pnl" in rec.columns else 0,
        "losing_trades": int((rec["pnl"] <= 0).sum()) if "pnl" in rec.columns else 0,
        "win_rate": round(float((rec["pnl"] > 0).mean()) * 100, 2) if "pnl" in rec.columns and len(rec) > 0 else 0.0,
        "total_return_pct": round(float(portfolio.total_return()) * 100, 4),
        "max_drawdown_pct": round(float(portfolio.max_drawdown()) * 100, 4),
    }

    return {
        "symbol": data["symbol"],
        "timeframe": data["timeframe"],
        "start_date": data["start_date"],
        "end_date": data["end_date"],
        "data": df.to_dict(orient="index"),
        "trades": trades_data,
        "summary": summary,
    }


def main():
    os.environ["NUMBA_NUM_THREADS"] = "1"
    os.environ["NUMBA_PARALLEL"] = "0"

    symbol = "ETH/BTC"
    print(f"Generating data for {symbol}...")
    data = generate_data(symbol, "5min")
    print(f"  {len(data['data'])} bars generated")

    print("Running backtest...")
    result = run_backtest(data)
    print(f"  {result['summary']['total_trades']} trades found")
    print(f"  Win rate: {result['summary']['win_rate']}%")
    print(f"  Total return: {result['summary']['total_return_pct']}%")

    # Clean result data for JSON serialization (convert any remaining non-serializable types)
    clean_data = {}
    for ts_key, row_dict in result["data"].items():
        clean_row = {}
        for col_name, val in row_dict.items():
            if isinstance(val, (np.integer,)):
                clean_row[col_name] = int(val)
            elif isinstance(val, (np.floating,)):
                clean_row[col_name] = float(val)
            elif isinstance(val, np.ndarray):
                clean_row[col_name] = val.tolist()
            else:
                clean_row[col_name] = val
        clean_data[str(ts_key)] = clean_row
    result["data"] = clean_data

    output_path = os.path.join(os.path.dirname(__file__), "backtest_results.json")
    with open(output_path, "w") as f:
        json.dump(result, f)
    print(f"\n✓ Results saved to {output_path}")
    print("  Now start the server: python -m uvicorn algo_trading_ui.app:app --host 127.0.0.1 --port 8000")


if __name__ == "__main__":
    main()
