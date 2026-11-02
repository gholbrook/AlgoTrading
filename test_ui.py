"""Smoke tests for the algo_trading_ui package."""

import pandas as pd
import numpy as np
import vectorbt as vbt

from algo_trading_ui.data_store import DataStore
from algo_trading_ui.chart_builder import build_candlestick_chart, build_empty_chart
from algo_trading_ui.app import app


def _make_ohlcv(n=200, start_price=100):
    """Generate synthetic OHLCV data for testing."""
    dates = pd.date_range("2026-01-01", periods=n, freq="5min")
    closes = start_price + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame(index=dates)
    df["open"] = closes
    df["high"] = closes + np.abs(np.random.randn(n) * 0.3)
    df["low"] = closes - np.abs(np.random.randn(n) * 0.3)
    df["close"] = closes
    df["volume"] = np.random.randint(100, 10000, n)
    return df


def test_data_store():
    ds = DataStore()
    df = _make_ohlcv()
    ds.add_asset("BTC/USD", df, "5min", "2026-01-01", "2026-01-02")

    assert ds.list_symbols() == ["BTC/USD"]
    assert ds.get_asset_df("BTC/USD") is not None
    assert ds.get_asset_df("NONEXIST") is None
    print("✓ DataStore OK")


def test_chart_no_trades():
    df = _make_ohlcv()
    fig = build_candlestick_chart(df, "ETH/BTC", trades=None)
    assert len(fig.data) == 2  # candlestick + volume
    print("✓ Chart (no trades) OK")


def test_chart_with_trades():
    df = _make_ohlcv(500)

    ds = DataStore()
    ds.add_asset("ETH/BTC", df, "5min", "2026-10-01", "2026-11-01")

    # Run a real vectorbt backtest
    close = df["close"]
    sma = vbt.MA.run(close, window=20).ma
    entries = close > sma
    portfolio = vbt.Portfolio.from_signals(
        close=close,
        entries=entries,
        exits=None,
        init_cash=10000,
        fees=0.001,
        size=0.01,
        size_type="percent",
    )

    ds.register_backtest("ETH/BTC", portfolio)

    # Extract trades
    trades = DataStore.extract_trades(portfolio)
    summary = DataStore.build_summary(portfolio)

    assert len(trades) > 0
    assert summary.total_trades == len(trades)
    print(f"✓ Trade extraction OK ({len(trades)} trades)")

    # Build chart with trade markers
    fig = build_candlestick_chart(df, "ETH/BTC", trades=trades, show_trades=True)
    trace_names = [t.name for t in fig.data]
    assert "Long Entry" in trace_names
    print("✓ Chart (with trades) OK")


def test_empty_chart():
    fig = build_empty_chart("XRP/USD")
    assert len(fig.data) == 0  # only annotations, no traces
    print("✓ Empty chart OK")


def test_api_endpoints():
    """Quick check that API routes return expected shapes."""
    df = _make_ohlcv()
    from algo_trading_ui.app import data_store
    from starlette.testclient import TestClient

    data_store.add_asset("TEST/USD", df, "5min", "2026-01-01", "2026-01-02")

    with TestClient(app) as client:
        resp = client.get("/api/assets")
        assert resp.status_code == 200
        body = resp.json()
        assert any(a["symbol"] == "TEST/USD" for a in body)

        # Chart (no backtest)
        resp = client.get("/api/chart", params={"symbol": "TEST/USD"})
        assert resp.status_code == 200
        chart_data = resp.json()
        assert "figure" in chart_data
        assert chart_data["summary"] is None

        # Nonexistent symbol
        resp = client.get("/api/chart", params={"symbol": "NONEXIST"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["figure"]["data"]) == 0  # empty chart annotation

        # Backtest for nonexistent
        resp = client.get("/api/backtest", params={"symbol": "NONEXIST"})
        assert resp.status_code == 404

    print("✓ API endpoints OK")


if __name__ == "__main__":
    test_data_store()
    test_chart_no_trades()
    test_chart_with_trades()
    test_empty_chart()
    test_api_endpoints()
    print("\n✅ All tests passed!")
