# Gridlab

A local workspace for blind grid replay, strategy backtesting, and paper trading. Backtests, recorded rehearsals, and Kraken paper sessions share one execution engine.

## Start the workbench

Requires Python 3.11 or newer. The workbench uses the Python standard library. For optional sector chart images, install Matplotlib with `python -m pip install -r requirements.txt` in your virtual environment. From the repository root, run:

```sh
python3 server.py
```

Open [Grid replay](http://127.0.0.1:8765) to draw and test a channel candle by candle. Open the [Workbench](http://127.0.0.1:8765/workbench) to download historical candles, edit strategies, and run backtests or paper sessions. The service binds to loopback. The browser UI and charts work offline. Exchange downloads and Kraken paper sessions need network access.

The default research catalog contains Binance and Coinbase exchange history. Use `--db /path/to/gridline.sqlite3` or `--port 8766` to change the database or port.

## Repository layout

- `gridlab/` contains the server, simulation engine, storage, and market data code.
- `static/` contains the browser interface. Editable trading strategies live in `strategies/`.
- `tools/` contains the bulk downloader and sector scanner. Run tools as modules from the repository root.
- `tests/` contains the offline suite and `examples/` contains sample candles.
- `var/` holds the local database, downloaded candles, and generated charts.

## Create or edit a strategy

Open **Strategies** to browse the Python files in `strategies/`. Each file contains its own entry and exit rules. Select a file to see its description, run settings, and Python source. **Duplicate as draft** creates an editable copy. Give it a new filename and click **Save Python file**. Files added with an external editor appear after **Refresh directory**. Select a file under **Research** to backtest it, or **Paper** to rehearse or run on the Kraken feed.

Start with **fixed_grid.py** for a fixed two-sided ladder, **adaptive_grid.py** for indicator-driven spacing, **recovery_grid.py** for fixed entries and profit exits that change after costs and remaining stop risk are covered, or **trend_cross.py** for a non-grid strategy.

Change a value under **Test a variation** to override a setting for one run. Edit the file’s `SETTINGS` dictionary to change its saved defaults. Edit `on_bar(ctx)` to change trading logic. Use a module docstring to explain what the strategy does. The Studio displays it above the settings. File strategies run as trusted local Python.

**Writing rules.** Each completed candle provides `ctx["bar"]`, up to 10,000 candles in `ctx["history"]`, current positions, available cash, realized P&L, fills, and the active settings. Read historical values from that context and store any state needed across bars or restarts in the JSON-compatible `ctx["memory"]`. For reproducible runs, use the captured inputs and keep all strategy-specific logic in the file. Declare `WARMUP` if the indicators need earlier candles.

**Drawing price indicators.** Add an optional `chart_indicators(ctx)` function to the strategy file. Gridline calls it once per closed candle, including warmup. Return a dictionary of line names and price values. Use `None` during warmup or for a gap. The function receives `bar`, `history`, `helpers`, `settings`, and the engine's standard `indicators`. The same values are available to trading rules in `ctx["plots"]` and stored with each candle for replay and hover. For example:

```python
def chart_indicators(ctx):
    closes = [bar["close"] for bar in ctx["history"][-20:]]
    middle, upper, lower = ctx["helpers"]["bollinger"](closes, 20, 2.0)
    return {"Upper band": upper, "Middle band": middle, "Lower band": lower}
```


Return a list of orders for the following candle. For example, `{"type":"limit","direction":"long","price":99,"notional":100}` requests a long entry. A lot can carry protective `stop_pct` and `target_pct` values, or the strategy can submit an explicit exit such as `{"type":"limit","action":"exit","lot_id":lot["lot_id"],"price":103}`. Pending orders expire after one candle. Return them again to keep them working, or change their prices at the next close. In the candle inspector, expand **View orders placed at close** to distinguish a requested order from a fill.

Each run captures the exact source and settings. Editing or deleting the original file leaves the run’s replay available. **Copy settings from displayed run** can recover the captured source as a new draft. Earlier named presets and the built-in execution models remain available in the same picker.

The local file API is `GET /api/strategy-files` (returns `{files: [...]}`) and `POST /api/strategy-files` with `{filename, source, previous_hash?}` (returns `{file: ...}`). Omit `previous_hash` only when creating a new filename. Updating an existing file requires its current `hash`; a stale hash returns HTTP 409. Filenames are `.py` basenames, not paths. The endpoint accepts source up to 256 KB and writes atomically beneath the strategy directory. Run creation can select a file with top-level `strategy_file`, optional `strategy_hash`, and `strategy_settings` overrides.

## Try the confidence workflow

1. Open **Paper** and choose **Recorded candles**. Choose a dataset, check the direction policy and costs, then start a rehearsal. The initial warmup bars (205 with default settings) initialize the indicators with the account in cash.
2. Advance 12 bars. Inspect the latest candle, fills, open layers, stops and targets. Run **Verify replay**. It rebuilds the decisions from the saved candles and parameters and compares them to the session record.
3. Stop the server with Ctrl+C, restart it against the same database and reopen the session. The cursor, pending signal, positions, cash and fees are saved together. Advance again to continue. **Stop run** instead archives the session at its last mark.
4. To observe the strategy forward, start a paper run with **Kraken live**. The first successful poll warms the engine from the latest confirmed bars needed for warmup and starts with zero exposure. Later polls process new closed candles in timestamp order. Leave the service running. Reopening the browser is optional. You can monitor the last poll, last committed candle and any feed error.
5. In **Research**, select a local dataset, date window and settings to run a backtest. Dates use UTC. The engine uses preceding bars up to the configured warmup length for warmup when available. For an exact comparison with a rehearsal, both the captured bars and the first trading-eligible candle must agree.

Sessions persist in `var/gridline.sqlite3` by default. When backing up the file, stop the server first, or use SQLite's online backup API. The app stores no exchange credentials.

## Chart replay and longer backtests

Drag the chart or use **Earlier** and **Later** to move its time window. Use the zoom buttons to change the number of visible candles. The inspector and chart share a cursor. Scrub the slider, step one candle, or press **Play** to advance automatically. Both price and equity end at the inspected candle. The fill log follows the cursor. In Research, account metrics and active layers also follow the inspected candle close. Paper sessions show their latest account.

In **Research → Add historical data**, choose Coinbase or Binance public market data, enter a spot pair as BASE/QUOTE, choose 5m or 1h, and select an inclusive UTC date range. A download can include up to 30,000 candles. Gridline checks continuity, excludes forming candles and saves the completed snapshot alongside the database in the history directory. A successful download selects the dataset for your next backtest. Each snapshot uses a single venue. If Coinbase coverage has gaps, check the same pair and interval on Binance. Live paper sessions use Kraken, so prices can differ across venues. [Coinbase candles API](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles)

**Bulk five-minute history.** Under **Research → Add historical data → Collect monthly archive**, enter the pairs and a range of completed UTC months. Keep the tab open while the batch runs. Compatible months at the same venue and interval appear together under the pair’s quote currency. The collector verifies the exchange ZIP checksum, exact month boundaries and every five-minute interval. For a larger batch, run the same collector from the project root:

```sh
python3 -m tools.bulk_history --pairs SOL/BTC BNB/BTC SOL/ETH --start-month 2026-08 --end-month 2026-10
python3 -m tools.sector_scan --venue Binance --quote BTC --as-of 2026-10-01T06:00:00Z
```

Rerun a batch after an interruption to reuse verified archives. Refresh the workbench after CLI imports. The sector scanner uses the same combined dataset IDs as the workbench. Each pair lists its full available ranges (dates and candle counts), the source snapshots read for the selected window, and any missing or conflicting coverage. By default it evaluates the last 24 hours at the latest common close for BTC and ETH quotes; use `--hours` or `--as-of` to change that window. Monthly collection supports currently listed Binance spot pairs. [Binance public data](https://github.com/binance/binance-public-data)

A sector is a candidate price area for a grid. Its floor and ceiling are the 10th and 90th percentiles of preceding closes. The decision close must be inside the box. By default the full 24-hour high-to-low swing (including wicks) must fit within 3% of its midpoint and within twice the box width. Tune the duration with `--hours` and the swing cap with `--max-span-pct`. The four quarter-window median closes must stay within half a box width of each other. Price must cross between the outer fifths of the box at least three times, including a crossing within each half of the window. At least one proposed grid spacing must clear modeled fees and slippage. Rejected windows list their reasons in the JSON output. The sector vector measures fitted log-price slope per hour and the standard deviation of five-minute log returns, both in percent. Add `--evaluate-hours 6` to a historical scan to freeze those bounds and count fully contained minutes before the first subsequent candle wick crosses either edge. Forward outcomes are research labels. Grid candidates currently use equal percentage spacing and a modeled cost floor. Initial buy allocation, sell spacing, and reuse of sale proceeds are separate policy choices to compare against that baseline.

## What the model simulates

| Decision | Rule |
| --- | --- |
| Signal | Use the closed candle's wick against a 20-close Bollinger band at two population standard deviations. A rising 200-close SMA versus five bars earlier enables lower-wick entries. An unchanged or falling SMA enables upper-wick entries. |
| Direction | The default `legacy-long` opens long on either signal, matching the original backtest's entry routing. `long-short` opens a fully collateralized simulated short on an upper-wick signal. |
| Entry | Queue the close signal for the next available candle open. Default layer size is 2% of available quote cash. Starting capital defaults to 50,000 for USD or USDT, and 1 for a BTC or ETH quote. |
| Exits | First signal in a streak uses the fixed target (0.45% by default). Consecutive signal bars use 1.5 times the Bollinger half-width as a fraction of the middle band. Each layer retains its target. Default adverse stop is 2%. Gaps through a level fill at the open. If the candle touches both thresholds after the open, evaluate the stop first. |
| Costs and marking | Defaults are 20 basis points in fees and 2 basis points in adverse slippage per fill. Both are configurable assumptions. Mark open positions at the most recent close. Closed trade P&L includes entry and exit fees. Stopping a run retains its last marked positions. |

This version changes execution timing, warmup, and per-layer handling from the original VectorBT script. Its returns should be evaluated as a new series. OHLC candles cannot reveal intrabar trade order, executable size, bid/ask spread or actual borrowing costs. Replay verifies the computation on captured inputs. It cannot verify that an exchange would have supplied those fills.

## Data and recovery

Snapshots use `BASE_QUOTE_5m.csv` or `BASE_QUOTE_1h.csv`, with `timestamp,open,high,low,close,volume` columns and timezone-aware timestamps. Historical snapshot files carry no exchange provenance beyond what the original downloader captured. Reports identify the file and its SHA-256 digest. The importer discards the final row because these exporters may save an unfinished candle. It checks bar continuity and OHLCV validity.

Kraken's OHLC endpoint includes an unfinished final row and bounds the available history. Gridline excludes that row. A missing interval blocks advancement and a subsequent contiguous response can recover the run. If the gap is beyond Kraken's retention, stop the affected session and start a fresh baseline. The saved session remains available for inspection. [Kraken OHLC reference](https://docs.kraken.com/api-reference/market-data/get-ohlc-data)

Paper simulation uses **bar-close processing**. An entry labeled at a candle's open is reconstructed only after that candle closes, with the signal already fixed from the preceding bar. The service polls every 30 seconds under normal conditions. Network timeouts and multi-market requests can lengthen that interval. Live paper charts display up to the latest 1,500 points. Historical and recorded rehearsal charts include the full run. Replay verification covers every captured candle in all modes.

## Verification

```sh
python3 -m unittest discover -s tests -v
node tests/test_manual_engine.js
```

The offline suite covers execution accounting, closed-bar ingestion, replay, persistence and the HTTP workflow. The [grid research log](notes/grid-research-log.md) records the walk-forward protocol, findings, and candidate configurations.
