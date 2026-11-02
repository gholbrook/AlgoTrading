"""FastAPI application serving the VectorBT backtest visualization UI."""

from typing import Optional
import base64
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
    """Serialize a Plotly figure dict to JSON-compatible Python objects (no base64)."""
    raw = fig.to_dict()

    def decode_bdata(obj):
        """Recursively replace {'dtype': ..., 'bdata': ...} with decoded plain lists."""
        if isinstance(obj, dict):
            if 'bdata' in obj and 'dtype' in obj:
                np_dtype = np.dtype(obj['dtype'])
                arr = np.frombuffer(base64.b64decode(obj['bdata']), dtype=np_dtype)
                return _convert_array(arr)
            return {k: decode_bdata(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [decode_bdata(item) for item in obj]
        if isinstance(obj, np.ndarray):
            return _convert_array(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_)):
            return bool(obj)
        if isinstance(obj, np.datetime64):
            return pd.Timestamp(obj).isoformat()
        return obj

    def _convert_array(arr):
        """Convert a numpy array to a list of Python native types."""
        result = []
        for val in arr:
            if isinstance(val, np.datetime64):
                result.append(pd.Timestamp(val).isoformat())
            elif isinstance(val, (np.integer,)):
                result.append(int(val))
            elif isinstance(val, (np.floating,)):
                result.append(float(val))
            elif isinstance(val, (np.bool_)):
                result.append(bool(val))
            else:
                result.append(val)
        return result

    return decode_bdata(raw)

from .chart_builder import build_candlestick_chart, build_empty_chart
from .data_store import DataStore
from .models import AssetPair, BacktestSummary, TradeRecord

app = FastAPI(
    title="AlgoTrading UI",
    description="Interactive VectorBT backtest visualization dashboard.",
    version="0.1.0",
)

# Global in-memory store — wired by the host application or auto-loaded from backtest_results.json
data_store: DataStore = DataStore()


def _load_backtest_from_disk() -> None:
    """Auto-load backtest results from JSON file if present."""
    import os as _os

    script_dir = _os.path.dirname(_os.path.abspath(__file__))
    parent_dir = _os.path.dirname(script_dir)
    json_path = _os.path.join(parent_dir, "backtest_results.json")

    if not _os.path.exists(json_path):
        return

    print(f"Loading backtest results from {json_path}...")
    with open(json_path, "r") as f:
        result = json.load(f)

    # Reconstruct full OHLCV DataFrame from backtest data (for timestamp lookup)
    raw = result["data"]
    reconstructed = {}
    for idx_str, row in raw.items():
        for k, v in row.items():
            if k not in reconstructed:
                reconstructed[k] = []
            reconstructed[k].append(v)

    full_df = pd.DataFrame(reconstructed, index=pd.to_datetime(list(raw.keys())))

    # Build timestamp lookup: position -> ISO timestamp string
    timestamp_map = {i: ts.isoformat() for i, ts in enumerate(full_df.index)}

    # Enrich trades with timestamps from the backtest data indices
    enriched_trades = []
    for t in result.get("trades", []):
        entry_idx = t.get("entry_idx")
        exit_idx = t.get("exit_idx")
        trade = dict(t)
        if entry_idx is not None and entry_idx in timestamp_map:
            trade["entry_time"] = timestamp_map[entry_idx]
        else:
            trade["entry_time"] = ""
        if exit_idx is not None and exit_idx in timestamp_map:
            trade["exit_time"] = timestamp_map[exit_idx]
        else:
            trade["exit_time"] = ""
        enriched_trades.append(trade)

    # Create a dummy portfolio object with the trades data for chart rendering
    # We store trades separately since we can't serialize vectorbt.Portfolio to JSON
    class _TradesStore:
        def __init__(self, trades_data, summary_data, timestamp_map):
            self._trades = trades_data
            self._summary = summary_data
            self._timestamp_map = timestamp_map

        @property
        def records(self):
            import numpy as np
            if not self._trades:
                return pd.DataFrame()
            return pd.DataFrame(self._trades)

        def count(self):
            return len(self._trades)

    trades_store = _TradesStore(enriched_trades, result.get("summary", {}), timestamp_map)

    # Patch the DataStore to serve these trades
    data_store._manual_trades = {result["symbol"]: trades_store}
    print(f"  Loaded {len(enriched_trades)} backtest trades for {result['symbol']}")


def _load_live_trades() -> None:
    """Auto-load live paper trading results from JSON file if present."""
    import os as _os

    script_dir = _os.path.dirname(_os.path.abspath(__file__))
    parent_dir = _os.path.dirname(script_dir)
    json_path = _os.path.join(parent_dir, "live_trades.json")

    if not _os.path.exists(json_path):
        return

    print(f"Loading live trades from {json_path}...")
    with open(json_path, "r") as f:
        result = json.load(f)

    symbol = result.get("symbol", "")
    if not symbol:
        return

    # Live trades already have entry_time/exit_time — no enrichment needed
    trades_data = result.get("trades", [])

    class _TradesStore:
        def __init__(self, trades_data, summary_data):
            self._trades = trades_data
            self._summary = summary_data

        @property
        def records(self):
            if not self._trades:
                return pd.DataFrame()
            return pd.DataFrame(self._trades)

        def count(self):
            return len(self._trades)

    trades_store = _TradesStore(trades_data, result.get("summary", {}))

    # Store live trades under a special key so the UI can distinguish them
    if not hasattr(data_store, "_live_trades"):
        data_store._live_trades = {}
    data_store._live_trades[symbol] = trades_store
    print(f"  Loaded {len(trades_data)} live trades for {symbol}")


def _get_live_trades(symbol: str) -> Optional[dict]:
    """Read live trades from JSON file on each request (hot-reload)."""
    import os as _os

    script_dir = _os.path.dirname(_os.path.abspath(__file__))
    parent_dir = _os.path.dirname(script_dir)
    json_path = _os.path.join(parent_dir, "live_trades.json")

    if not _os.path.exists(json_path):
        return None

    try:
        with open(json_path, "r") as f:
            result = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None

    live_symbol = result.get("symbol", "")
    if live_symbol != symbol:
        return None

    trades_data = result.get("trades", [])
    summary = result.get("summary", {})

    class _TradesStore:
        def __init__(self, trades_data, summary_data):
            self._trades = trades_data
            self._summary = summary_data

        @property
        def records(self):
            if not self._trades:
                return pd.DataFrame()
            return pd.DataFrame(self._trades)

        def count(self):
            return len(self._trades)

    return {"trades": trades_data, "summary": summary}


def _load_csv_assets() -> None:
    """Auto-load OHLCV CSV files from the data/ directory."""
    import os as _os

    script_dir = _os.path.dirname(_os.path.abspath(__file__))
    parent_dir = _os.path.dirname(script_dir)
    data_dir = _os.path.join(parent_dir, "data")

    if not _os.path.isdir(data_dir):
        return

    csv_files = sorted([f for f in _os.listdir(data_dir) if f.endswith(".csv")])
    if not csv_files:
        return

    print(f"Found {len(csv_files)} CSV file(s) in data/: {', '.join(csv_files)}")

    for csv_file in csv_files:
        filepath = _os.path.join(data_dir, csv_file)
        try:
            # Parse filename: ETH_BTC_1h.csv -> symbol=ETH/BTC, timeframe=1h
            stem = csv_file.replace(".csv", "")
            parts = stem.rsplit("_", 1)
            if len(parts) != 2:
                print(f"  Skipping {csv_file}: unexpected filename format")
                continue

            timeframe = parts[1]
            symbol = parts[0].replace("_", "/")

            # Load CSV
            df = pd.read_csv(filepath, index_col=0, parse_dates=True)

            # Ensure standard column names
            required = ["open", "high", "low", "close", "volume"]
            if not all(col in df.columns for col in required):
                print(f"  Skipping {csv_file}: missing OHLCV columns")
                continue

            start_date = str(df.index[0].date())
            end_date = str(df.index[-1].date())

            register_asset(symbol, df, timeframe, start_date, end_date)
            print(f"  Loaded {symbol} ({len(df)} bars, {timeframe}, {start_date} to {end_date})")

        except Exception as e:
            print(f"  ERROR loading {csv_file}: {e}")


_load_backtest_from_disk_called = False


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

        /* Trades section */
        .trades-section {
            padding: 16px 24px 24px;
        }
        .trades-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 12px;
        }
        .trades-header h2 { font-size: 18px; font-weight: 600; color: #374151; }
        .trade-table-wrap {
            background: white;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
            overflow: hidden;
        }
        .trade-table-scroll {
            max-height: 500px;
            overflow-y: auto;
        }
        table.trade-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        table.trade-table thead {
            position: sticky;
            top: 0;
            z-index: 1;
        }
        table.trade-table th {
            background: #f9fafb;
            padding: 10px 14px;
            text-align: left;
            font-weight: 600;
            color: #6b7280;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-bottom: 1px solid #e5e7eb;
            cursor: pointer;
            user-select: none;
            white-space: nowrap;
        }
        table.trade-table th:hover { color: #3b82f6; }
        table.trade-table th .sort-arrow { margin-left: 4px; font-size: 10px; }
        table.trade-table td {
            padding: 9px 14px;
            border-bottom: 1px solid #f3f4f6;
            white-space: nowrap;
        }
        table.trade-table tbody tr:hover { background: #f9fafb; }
        .pnl-positive { color: #10b981; font-weight: 600; }
        .pnl-negative { color: #ef4444; font-weight: 600; }
        .badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 9999px;
            font-size: 11px;
            font-weight: 600;
        }
        .badge-long { background: #d1fae5; color: #065f46; }
        .badge-short { background: #fee2e2; color: #991b1b; }
        .trade-empty {
            padding: 48px;
            text-align: center;
            color: #9ca3af;
            font-size: 15px;
        }

        /* Live indicator */
        .live-badge {
            display: none;
            align-items: center;
            gap: 6px;
            padding: 4px 12px;
            border-radius: 9999px;
            background: #fef3c7;
            color: #92400e;
            font-size: 12px;
            font-weight: 600;
        }
        .live-badge.active { display: inline-flex; }
        .live-dot {
            width: 8px; height: 8px;
            background: #f59e0b;
            border-radius: 50%;
            animation: pulse 1.5s ease-in-out infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.3; }
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
            <div class="live-badge" id="live-badge"><span class="live-dot"></span>LIVE</div>
        </div>
    </div>
    <div class="stats-bar" id="stats-bar"></div>
    <div class="chart-container">
        <div id="chart"></div>
    </div>
    <div class="trades-section">
        <div class="trades-header">
            <h2 id="trades-title">Trades</h2>
            <span id="trades-count" style="color:#6b7280;font-size:13px;"></span>
        </div>
        <div class="trade-table-wrap">
            <div class="trade-table-scroll">
                <table class="trade-table" id="trade-table">
                    <thead>
                        <tr>
                            <th onclick="sortTrades('idx')"># <span class="sort-arrow"></span></th>
                            <th onclick="sortTrades('side')">Side <span class="sort-arrow"></span></th>
                            <th onclick="sortTrades('entry_time')">Entry Time <span class="sort-arrow"></span></th>
                            <th onclick="sortTrades('entry_price')">Entry Price <span class="sort-arrow"></span></th>
                            <th onclick="sortTrades('exit_time')">Exit Time <span class="sort-arrow"></span></th>
                            <th onclick="sortTrades('exit_price')">Exit Price <span class="sort-arrow"></span></th>
                            <th onclick="sortTrades('pnl')">P&L <span class="sort-arrow"></span></th>
                        </tr>
                    </thead>
                    <tbody id="trade-body">
                        <tr><td colspan="7" class="trade-empty">Select an asset with a backtest to view trades.</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        let currentSymbol = '';
        let showTrades = true;
        let tradeData = [];
        let sortCol = 'idx';
        let sortAsc = true;
        let liveRefreshInterval = null;

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
                renderTrades([]);
                stopLiveRefresh();
                return;
            }
            await refreshChart();
        }

        function startLiveRefresh() {
            stopLiveRefresh();
            liveRefreshInterval = setInterval(async () => {
                if (currentSymbol) await refreshChart();
            }, 5000); // poll every 5 seconds
        }

        function stopLiveRefresh() {
            if (liveRefreshInterval) {
                clearInterval(liveRefreshInterval);
                liveRefreshInterval = null;
            }
        }

        async function refreshChart() {
            const params = new URLSearchParams({ symbol: currentSymbol, show_trades: showTrades });
            const res = await fetch(`/api/chart?${params}`);
            if (!res.ok) return;
            const data = await res.json();

            if (data.error) {
                Plotly.purge('chart');
                document.getElementById('stats-bar').innerHTML = '';
                renderTrades([]);
                return;
            }

            Plotly.newPlot('chart', data.figure.data, data.figure.layout, {
                responsive: true,
                displayModeBar: true,
                modeBarButtonsToRemove: ['lasso2d', 'select2d'],
            });

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
                // Show live badge when we have summary data (backtest or live)
                document.getElementById('live-badge').classList.add('active');
            } else {
                bar.innerHTML = '<div class="no-data">No backtest registered for this asset pair.</div>';
                document.getElementById('live-badge').classList.remove('active');
            }

            // Load trades list
            loadTradesList();

            // Start live polling if we have data
            startLiveRefresh();
        }

        async function loadTradesList() {
            try {
                const res = await fetch(`/api/trades?symbol=${encodeURIComponent(currentSymbol)}`);
                if (!res.ok) {
                    renderTrades([]);
                    return;
                }
                const data = await res.json();
                tradeData = (data.trades || []).map((t, i) => ({ idx: i + 1, ...t }));
                renderTrades(tradeData);
            } catch {
                renderTrades([]);
            }
        }

        function renderTrades(trades) {
            const body = document.getElementById('trade-body');
            const countEl = document.getElementById('trades-count');
            const titleEl = document.getElementById('trades-title');

            if (!currentSymbol || trades.length === 0) {
                body.innerHTML = '<tr><td colspan="7" class="trade-empty">No trades to display.</td></tr>';
                countEl.textContent = '';
                titleEl.textContent = 'Trades';
                return;
            }

            const symbol = currentSymbol;
            countEl.textContent = `${trades.length} trade${trades.length !== 1 ? 's' : ''}`;
            titleEl.textContent = `Trades — ${symbol}`;

            // Sort
            trades.sort((a, b) => {
                let va = a[sortCol], vb = b[sortCol];
                if (va == null) return sortAsc ? 1 : -1;
                if (vb == null) return sortAsc ? -1 : 1;
                if (typeof va === 'number') return sortAsc ? va - vb : vb - va;
                return sortAsc ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
            });

            body.innerHTML = trades.map(t => {
                const side = t.is_long !== false ? 'Long' : 'Short';
                const sideClass = t.is_long !== false ? 'badge-long' : 'badge-short';
                const pnlStr = t.pnl != null ? (t.pnl >= 0 ? `+${t.pnl.toFixed(4)}` : t.pnl.toFixed(4)) : '—';
                const pnlClass = t.pnl != null ? (t.pnl >= 0 ? 'pnl-positive' : 'pnl-negative') : '';
                return `<tr>
                    <td>${t.idx}</td>
                    <td><span class="badge ${sideClass}">${side}</span></td>
                    <td>${t.entry_time || '—'}</td>
                    <td>${t.entry_price != null ? t.entry_price.toFixed(6) : '—'}</td>
                    <td>${t.exit_time || '—'}</td>
                    <td>${t.exit_price != null ? t.exit_price.toFixed(6) : '—'}</td>
                    <td class="${pnlClass}">${pnlStr}</td>
                </tr>`;
            }).join('');

            // Update sort arrows
            document.querySelectorAll('#trade-table th .sort-arrow').forEach(el => {
                el.textContent = '';
            });
        }

        function sortTrades(col) {
            if (sortCol === col) {
                sortAsc = !sortAsc;
            } else {
                sortCol = col;
                sortAsc = true;
            }
            renderTrades(tradeData);
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
        # fig = build_empty_chart(symbol)
        return {
            "figure": _fig_to_json_safe(fig),
            "summary": None,
        }

    trades = None
    summary = None

    # Check for manually loaded backtest trades (from JSON file) first
    if hasattr(data_store, "_manual_trades") and symbol in data_store._manual_trades:
        manual = data_store._manual_trades[symbol]
        trades = manual._trades
        summary = manual._summary

    # Check live paper trades — hot-reload from disk on each request
    if trades is None:
        live = _get_live_trades(symbol)
        if live:
            trades = live["trades"]
            summary = live["summary"]

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


@app.get("/api/trades")
async def api_trades_list(symbol: str = Query(..., description="Asset pair symbol")):
    """Return the full list of trades for *symbol* (backtest or live)."""
    # Check manually loaded backtest trades first (from JSON)
    if hasattr(data_store, "_manual_trades") and symbol in data_store._manual_trades:
        manual = data_store._manual_trades[symbol]
        trades_data = manual._trades
        if isinstance(trades_data, list):
            return {"symbol": symbol, "trades": trades_data}

    # Check live paper trades — hot-reload from disk on each request
    live = _get_live_trades(symbol)
    if live:
        return {"symbol": symbol, "trades": live["trades"]}

    # Fall back to registered portfolio
    portfolio = data_store.get_portfolio(symbol)
    if portfolio is None:
        raise HTTPException(status_code=404, detail=f"No trades found for '{symbol}'")

    trades = DataStore.extract_trades(portfolio)
    return {"symbol": symbol, "trades": [t.model_dump() for t in trades]}


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


# ============================================================
# Auto-load backtest results after all functions are defined
# ============================================================

if not _load_backtest_from_disk_called:
    _load_backtest_from_disk()

# Auto-load CSV data files from data/ directory
_load_csv_assets()

# Auto-load live paper trades
_load_live_trades()
