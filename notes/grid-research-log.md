# Grid configuration research

## Decision protocol

Use the five BTC- and ETH-quoted Binance pairs as the primary group. Score the eight USDT pairs separately. A completed, gap-free 72-hour replay starts on a Monday or Thursday, centered on the revealed close. Use equal initial quote capital per session and equal pair weight; the metric is quote-currency return after liquidation. These starts measure the grid independently of discretionary entry selection. Include zero-interest cash and matched buy-and-hold benchmarks.

Compare widths of 2%, 4%, 8%, and 12%, fixed channels, outside-only moves and two-sided 8-line shifts with a one-line catch-up cap. For each, compare replenishment off, always on, daily EMA, and the 720-minute / +0.02% EMA. Keep a 2% fill stop and a 0.5% daily-bullish trail. At 4% and 12% also test disabling or tightening the stop, disabling the trail, and combining the 15-minute lower shift with intraday trailing. Report turnover alongside return.

Pick the highest average net return over the previous six calendar months and score only the following month. Separately freeze a single configuration using data through December 2024 and score it on 2025. Treat 2026 as a previously studied period. Resample whole months in contiguous three-month blocks, keeping pairs together. Before calling a configuration a robust winner, look for positive lower confidence bounds against cash and the existing 12% preset, positive returns across most primary pairs, and resilience to higher simulated costs. If candidates are indistinguishable, report that uncertainty.

## Other configurations to test

| Idea | Specific experiment | What would justify keeping it |
|---|---|---|
| Width from recent volatility (24h tested; 72h pending) | Set width to a multiple of the previous 24h or 72h true range; compare with fixed width on the same start | Improves net return across quiet and volatile months |
| User-selected support and resistance | Save blind drawing decisions; replay the exact entries with drawn bounds and with a centered fixed width | Better results on unseen drawings |
| Separate pause and resume thresholds | Pause replacements below zero and resume above +0.02% for two candles | Fewer losing repeat buys without an excessive drop in turnover |
| Inventory cap | Limit simultaneous funded buys to 25%, 50%, or 75% of capital | Better downside and comparable net return |
| Recenter less often | Wait for two outside closes or a volatility-scaled overshoot | Lower churn and fewer adverse exits |
| Liquidity-aware fills | Require trade-through or sufficient traded volume at a level; vary size and maker/taker costs | Still profitable after realistic execution assumptions |
| Cash as an explicit choice | Allow walking forward without arming if every candidate has a negative trailing return | Improves drawdowns and out-of-sample net return |

## Follow-up protocol after the first comparison

The initial frozen choice did not separate from the reference in 2025 and its worst session drawdown was larger. Test 4%, 12%, and twice the most recent 24-hour high–low width; fixed versus two-sided shifting channels; and four mechanical entry schedules. The schedules are unconditional, quiet, contained, and both quiet and contained. Quiet means the last 24-hour high–low distance is at or below the median of the preceding six 24-hour distances (each divided by its closing price). Contained means absolute net movement over the last day is at most 25% of that day's high–low distance. A skipped opportunity holds cash and earns zero. Keep the 2% stop, 720-minute replenishment and 0.5% daily-bullish trail. Choose by early net return with at least one starting balance of gross turnover per scheduled session and at least 25% of opportunities traded. Test the locked choice on LTC/BTC, XRP/BTC, ADA/BTC and LINK/BTC without using those pairs for selection. Reusing the primary 2025 year here is a diagnostic, not a second independent confirmation.

## Findings and continuing work

- The 56-setting search picked a 4% channel without a protective stop on early crypto-cross history. On 470 paired 2025 sessions it earned 0.023% per session with 1.91× turnover and a worst session drawdown of 17.97%. The 12% reference earned 0.091% with 1.31× turnover and 5.16% worst drawdown.
- Reselecting from the previous six months at the start of every month produced −0.009% per session over 46 scored months on the primary crosses. The four new BTC pairs each stayed out of configuration selection. Across these pairs all three locked configurations averaged losses in 2025.
- The 24-combination follow-up picked a 4% grid with a 2% stop and unconditional entry. The quietness filter and recent-day width did not establish a better entry policy at the prespecified turnover floor. The 4% grid with a stop produced 2.06× turnover and 0.045% return on the primary 2025 sample, but −0.245% on the new pairs. No finalist passed the decision protocol.
- The live research queue starts with paired blind manual boundaries versus centered channels, a cap on funded inventory, and a liquidity-aware fill simulation. Keep the locked comparison intact when opening new experiments.
- The first prospective start is Thursday, November 12, 2026 UTC. A weekly job downloads completed UTC days and runs the frozen engine. Review monthly results descriptively; make a formal selection only at annual checkpoints with sufficient matched windows. Compare every surviving finalist pairwise, require positive block-adjusted margins over cash and rivals, check per-pair sign and stressed costs, and report gross turnover. A winner must survive on a majority of primary pairs. If multiple annual looks are needed, use a summable error budget (total 5%, allocated as 0.05/[k(k+1)] at look k, with a simultaneous comparison correction). If none passes, continue collecting and retain the existing replay defaults.

[Historical report](../var/walk-forward/report.md) · [Frozen configurations and engine hashes](../var/walk-forward/forward-lock.json)

## USDT follow-up

The eight Binance USDT markets received the same 24-candidate entry and width comparison. A fixed channel at twice the prior 24-hour high–low distance, armed on every scheduled start, led the eligible training configurations. On 752 matched 2025 sessions it averaged −0.191%; on 624 sessions in January–October 2026 it averaged +0.011%. The 12% always-replenishing outside-only grid produced substantially higher volume and a 30.21% worst session drawdown in 2025. The 4% two-sided grid with a 2% stop and the adaptive-width finalist join the existing two USDT benchmarks for prospective starts from November 12. [USDT comparison and per-pair results](../var/walk-forward/usdt-report.md)
