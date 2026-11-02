"""FastAPI application serving the VectorBT backtest visualization UI."""

from typing import Optional
import json

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse


class _NumpyEncoder(json.JSONEncoder):
    """Recursively convert numpy types to Python native types for JSON serialization."""

    def default(self, obj):  # type: ignore[override]
        import datetime as dt

        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_)):
            return bool(obj)
        if isinstance(obj, np.datetime64):
            return pd.Timestamp(obj).isoformat()
        if isinstance(obj, dt.datetime):
            return obj.isoformat()
        return super().default(obj)


def _fig_to_json_safe(fig):
    """Serialize a Plotly figure dict to JSON-compatible Python objects."""
    raw = fig.to_dict()
    return json.loads(json.dumps(raw, cls=_NumpyEncoder))

from .chart_builder import build_candlestick_chart, build_empty_chart
from .data_store import DataStore
from .models import AssetPair, BacktestSummary, TradeRecord

app = FastAPI(
    title="AlgoTrading UI",
    description="Interactive VectorBT backtest visualization dashboard.",
    version="0.1.0",
)

# Global in-memory store — wired by the host application
data_store: DataStore = DataStore()


# ============================================================
# HTML page (served inline so no static-file dependency needed)
# ============================================================
HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AlgoTrading — Backtest Visualizer</title>
    <script src="https://cdn.plot.ly/plotly-2.35.0.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f6fa;
            color: #2c3e50;
        }
        .header {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: white;
            padding: 16px 32px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            box-shadow: 0 2px 8px rgba(0,0,0,0.2);
        }
        .header h1 { font-size: 22px; font-weight: 600; letter-spacing: -0.5px; }
        .controls {
            display: flex;
            gap: 12px;
            align-items: center;
        }
        select, button {
            padding: 8px 16px;
            border-radius: 6px;
            border: 1px solid #d1d5db;
            font-size: 14px;
            background: white;
            cursor: pointer;
            transition: all 0.15s;
        }
        select:hover, button:hover { border-color: #3b82f6; }
        select:focus, button:focus { outline: none; box-shadow: 0 0 0 2px rgba(59,130,246,0.3); }
        button.primary {
            background: #3b82f6;
            color: white;
            border-color: #3b82f6;
        }
        button.primary:hover { background: #2563eb; }
        .stats-bar {
            display: flex;
            gap: 16px;
            padding: 12px 32px;
            background: white;
            border-bottom: 1px solid #e5e7eb;
            flex-wrap: wrap;
        }
        .stat-card {
            flex: 1;
            min-width: 140px;
            padding: 12px 16px;
            background: #f9fafb;
            border-radius: 8px;
            border: 1px solid #e5e7eb;
        }
        .stat-card .label { font-size: 11px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; }
        .stat-card .value { font-size: 20px; font-weight: 700; margin-top: 2px; }
        .stat-card .value.positive { color: #10b981; }
        .stat-card .value.negative { color: #ef4444; }
        .chart-container {
            padding: 16px 24px;
        }
        #chart {
            width: 100%;
            height: 75vh;
            border-radius: 8px;
            background: white;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }
        .no-data {
            display: flex;
            align-items: center;
            justify-content: center;
            height: 75vh;
            font-size: 18px;
            color: #9ca3af;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>AlgoTrading — Backtest Visualizer</h1>
        <div class="controls">
            <select id="asset-select" onchange="loadAsset()">
                <option value="">Loading assets…</option>
            </select>
            <button class="primary" onclick="toggleTrades()" id="trade-btn">Hide Trades</button>
        </div>
    </div>
    <div class="stats-bar" id="stats-bar"></div>
    <div class="chart-container">
        <div id="chart"></div>
    </div>

    <script>
        let currentSymbol = '';
        let showTrades = true;

        async function loadAssets() {
            const res = await fetch('/api/assets');
            const assets = await res.json();
            const sel = document.getElementById('asset-select');
            sel.innerHTML = '<option value="">Select asset…</option>';
            assets.forEach(a => {
                const opt = document.createElement('option');
                opt.value = a.symbol;
                opt.textContent = `${a.symbol} (${a.timeframe})`;
                sel.appendChild(opt);
            });
        }

        async function loadAsset() {
            const sel = document.getElementById('asset-select');
            currentSymbol = sel.value;
            if (!currentSymbol) {
                Plotly.purge('chart');
                document.getElementById('stats-bar').innerHTML = '';
                return;
            }
            await refreshChart();
        }

        async function refreshChart() {
            const params = new URLSearchParams({ symbol: currentSymbol, show_trades: showTrades });
            const res = await fetch(`/api/chart?${params}`);
            if (!res.ok) return;
            const data = await res.json();

            if (data.error) {
                Plotly.purge('chart');
                document.getElementById('stats-bar').innerHTML = '';
                return;
            }

            Plotly.react('chart', data.figure.data, data.figure.layout);

            // Update stats
            const bar = document.getElementById('stats-bar');
            if (data.summary) {
                const s = data.summary;
                bar.innerHTML = `
                    <div class="stat-card"><div class="label">Total Trades</div><div class="value">${s.total_trades}</div></div>
                    <div class="stat-card"><div class="label">Win Rate</div><div class="value">${s.win_rate}%</div></div>
                    <div class="stat-card"><div class="label">Total Return</div><div class="value ${s.total_return_pct >= 0 ? 'positive' : 'negative'}">${s.total_return_pct.toFixed(2)}%</div></div>
                    <div class="stat-card"><div class="label">Max Drawdown</div><div class="value negative">${s.max_drawdown_pct.toFixed(2)}%</div></div>
                    ${s.sharpe_ratio !== null ? `<div class="stat-card"><div class="label">Sharpe Ratio</div><div class="value">${s.sharpe_ratio}</div></div>` : ''}
                `;
            } else {
                bar.innerHTML = '<div class="no-data">No backtest registered for this asset pair.</div>';
            }
        }

        function toggleTrades() {
            showTrades = !showTrades;
            document.getElementById('trade-btn').textContent = showTrades ? 'Hide Trades' : 'Show Trades';
            if (currentSymbol) refreshChart();
        }

        // Init
        loadAssets();
    </script>
</body>
</html>
"""


# ============================================================
# Routes
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main dashboard page."""
    return HTMLPage()


def HTMLPage():
    return HTML_PAGE


@app.get("/api/assets")
async def api_list_assets():
    """Return a list of all registered asset symbols with metadata."""
    symbols = data_store.list_symbols()
    result = []
    for sym in symbols:
        info = data_store.get_asset_info(sym)
        if info:
            result.append({
                "symbol": info["symbol"],
                "timeframe": info["timeframe"],
                "start_date": info["start_date"],
                "end_date": info["end_date"],
            })
    return result


@app.get("/api/chart")
async def api_chart(symbol: str = Query(..., description="Asset pair symbol (e.g. ETH/BTC)"), show_trades: bool = Query(True)):
    """Return Plotly chart JSON for *symbol*.

    If a backtest is registered for this symbol, trade markers are overlaid.
    """
    df = data_store.get_asset_df(symbol)
    if df is None or df.empty:
        fig = build_empty_chart(symbol)
        return {
            "figure": _fig_to_json_safe(fig),
            "summary": None,
        }

    trades = None
    summary = None

    portfolio = data_store.get_portfolio(symbol)
    if portfolio is not None:
        trades = DataStore.extract_trades(portfolio)
        summary = DataStore.build_summary(portfolio).model_dump()

    fig = build_candlestick_chart(df, symbol, trades=trades, show_trades=show_trades and trades is not None)
    return {
        "figure": _fig_to_json_safe(fig),
        "summary": summary,
    }


@app.get("/api/backtest")
async def api_backtest_info(symbol: str = Query(..., description="Asset pair symbol")):
    """Return backtest summary for *symbol* (no chart)."""
    if not data_store.has_backtest_for(symbol):
        raise HTTPException(status_code=404, detail=f"No backtest registered for '{symbol}'")

    portfolio = data_store.get_portfolio(symbol)
    trades = DataStore.extract_trades(portfolio)
    summary = DataStore.build_summary(portfolio)

    return {
        "symbol": symbol,
        "trades_count": len(trades),
        "summary": summary.model_dump(),
    }


# ============================================================
# Helpers for the host application to wire data in
# ============================================================

def register_asset(symbol: str, df: pd.DataFrame, timeframe: str, start_date: str, end_date: str) -> None:
    """Host app calls this to add an asset pair."""
    data_store.add_asset(symbol, df, timeframe, start_date, end_date)


def register_backtest(symbol: str, portfolio) -> None:
    """Host app calls this after running a backtest.

    Parameters
    ----------
    symbol : str
        The asset pair the backtest was run on.
    portfolio
        A vectorbt.Portfolio instance.
    """
    data_store.register_backtest(symbol, portfolio)
