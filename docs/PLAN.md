# Gridline project plan

The first question this workbench should answer is “What did my strategy decide on the last closed candle, and can I reproduce that decision tomorrow?” Historical research and paper execution must produce the same ledger when their candles, warmup boundary, configuration and engine version match.

## What the existing project taught us

| Observation in the linked checkout | Design consequence |
| --- | --- |
| `main.py` combines the two Bollinger wick conditions into one VectorBT entry array. The paper runner instead treats an upper wick in a non-rising regime as a short. | Save the direction policy with each run. Start with the historical long-only routing and offer a separate directional simulation. |
| `run_backtest.py` generates sinusoidal prices. The exported return cannot establish how the grid behaved on an exchange. | Calculate the first report from a timestamped OHLCV snapshot and display the source. Keep research on local CSVs separate from Kraken sessions. |
| `live_paper_trade.py` evaluates the latest five-minute row every minute. Its second exit method overrides the first, and fills skip the configured fees. | Consume each closed bar once, calculate fills with one shared engine, and include both legs' fees in reported results. |
| The live runner writes CSV and JSON snapshots independently, stores positions in memory, and values open positions at cost. | Save the input candle, decision and resulting account checkpoint together. Mark open lots at the last close and show their targets and stops. |

## Delivery sequence

| Milestone | Scope | Acceptance |
| --- | --- | --- |
| 1 · Reproducible grid model | Define signal timing, sizing, direction, costs, tiered targets and ambiguous-bar rules. Separate market data, engine and persistence. | A fixed candle stream gives the same entries, exits, fees and equity after a restart. Hand-calculated cases cover both directions and adverse prices. |
| 2 · Research workbench | Select a market, timeframe, UTC date range and cost assumptions. Generate a saved report with price, bands, fills, equity, drawdown and open exposure. | A fresh checkout can run the included candle sample offline. A user can tell which file and settings produced a report and reopen it later. |
| 3 · Paper confidence | Use one market per session and one quote-currency account. Offer Kraken closed-bar polling, a manually advanced recorded session, transactional recovery, feed health and historical replay verification. | Repeated polls create no duplicate fills. A missing candle blocks advancement. Restart resumes the cursor. A recorded rehearsal matches a batch run with the same warmup. |
| 4 · Forward validation | Collect a sustained public-feed session. Compare the archived decisions to replay. Inspect every gap, restart, delayed candle and ambiguous exit. Re-run the same dates at several cost assumptions. | A reviewer can explain each discrepancy and identify which periods had usable feed coverage. Elapsed wall-clock coverage and observed fill counts are reported with the results. |
| 5 · Broader research and execution calibration | Add named strategy versions, side-by-side run comparison and walk-forward holdouts. If the candle model is too coarse, capture quotes/trades and calibrate spread, queue position and partial fills against observed conditions. | Reject changes that break deterministic replay. Report held-out results and execution sensitivity separately from tuning-period results. |

The first release implements milestones 1–3. Milestone 4 requires elapsed market time. Milestone 5 depends on what the forward sessions show. Exchange order submission is outside the project scope.

## Technical boundaries

The local browser UI owns selection and presentation. The Python service owns run creation, validation, polling and manual advancement. A pure state transition consumes one finalized bar. SQLite holds input bars, immutable parameters, fills and account checkpoints. A replay starts from initial capital and uses the captured bars and warmup flags. It can detect divergence or persistence errors. A matched replay still shares the same execution assumptions as the original run.

Every session has its own capital. Paper shorts use fully collateralized, simulated exposure. Exchange borrowing availability, funding, order-book depth and latency would require a separate execution model. A multi-asset portfolio also needs its own sizing and event-ordering rules before combining any of these accounts.

## Work split

Three lower-cost GPT-6 Luna agents reviewed the legacy strategy, paper runner and UI independently, then implemented the engine and unit tests, persistence and feed adapter, and browser workflow. The lead owns integration, the acceptance scenarios, review of accounting and execution order, and the launch handoff.
