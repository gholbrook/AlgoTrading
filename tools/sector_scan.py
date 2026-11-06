"""Scan saved five-minute exchange snapshots for candidate price sectors.

Example: python3 -m tools.sector_scan --as-of 2026-11-02T12:00:00Z
All boundaries and candidates use only candles closed by the decision time.
"""
import argparse
import json
import math
from bisect import bisect_left
from datetime import datetime, timezone
from pathlib import Path
from statistics import median, quantiles, stdev

from gridlab.datasets import CombinedDataset, combine_datasets, load_history_datasets


def sector_setup(bars, fee_bps=20, slip_bps=2, max_span_pct=3.0):
    """Estimate a box from prior closes and use the newest close to test it."""
    closes = [row["close"] for row in bars[:-1]]
    cuts = quantiles(closes, n=10, method="inclusive")
    floor, ceiling = cuts[0], cuts[-1]
    width = ceiling - floor
    mid = (floor + ceiling) / 2
    log_prices = [math.log(price) for price in closes + [bars[-1]["close"]]]
    center = (len(log_prices) - 1) / 2
    slope = (sum((i - center) * price for i, price in enumerate(log_prices)) /
             sum((i - center) ** 2 for i in range(len(log_prices))))
    returns = [b - a for a, b in zip(log_prices, log_prices[1:])]
    vector = {"slope_log_pct_per_hour": slope * 12 * 100,
              "volatility_log_pct_per_5m": stdev(returns) * 100}
    passes_by_half = [0, 0]
    last_side = None
    traversals = 0
    for i, price in enumerate(closes):
        half = int(i >= len(closes) // 2)
        if i == len(closes) // 2:
            last_side = None
        side = None
        if price <= floor + width * 0.20:
            side = "lower"
        elif price >= ceiling - width * 0.20:
            side = "upper"
        if side:
            if last_side is not None and side != last_side:
                traversals += 1
                passes_by_half[half] += 1
            last_side = side

    price = bars[-1]["close"]
    high = max(bar["high"] for bar in bars)
    low = min(bar["low"] for bar in bars)
    span_pct = 200 * (high - low) / (high + low)
    centers = [median(closes[i * len(closes) // 4:(i + 1) * len(closes) // 4])
               for i in range(4)]
    reasons = []
    if width == 0:
        reasons.append("zero width")
    if not floor <= price <= ceiling:
        reasons.append("outside sector")
    if span_pct > max_span_pct:
        reasons.append("swing too wide")
    if width and high - low > 2 * width:
        reasons.append("large excursion outside box")
    if max(centers) - min(centers) > 0.5 * width:
        reasons.append("price center shifted")
    if min(passes_by_half) < 1 or traversals < 3:
        reasons.append("too few sustained crossings")

    # Minimum adjacent sell/buy price ratio after both legs' modeled costs.
    fee, slip = fee_bps / 10000, slip_bps / 10000
    breakeven = (1 + fee) * (1 + slip) / ((1 - fee) * (1 - slip))
    candidates = []
    if not reasons:
        for intervals in (2, 3, 4, 6, 8):
            ratio = (ceiling / floor) ** (1 / intervals)
            if ratio > breakeven:
                candidates.append({"intervals": intervals, "step_pct": round(100 * (ratio - 1), 4),
                                   "levels": [floor * ratio ** i for i in range(intervals + 1)]})
        if not candidates:
            reasons.append("too narrow after costs")
    return {"sector_status": reasons[0] if reasons else "candidate",
            "rejection_reasons": reasons, "wick_span_pct": span_pct,
            "traversals_by_half": passes_by_half, "vector": vector, "floor": floor, "ceiling": ceiling,
            "width_pct_of_mid": 100 * width / mid, "traversals": traversals,
            "close": price,
            "breakeven_step_pct": 100 * (breakeven - 1), "candidate_grids": candidates}


def evaluate_sector(dataset, cutoff, hours, sector, chart_bars=None):
    """Count complete future candles inside the original bounds before the first breach."""
    end = cutoff + hours * 3600
    members = dataset.members if isinstance(dataset, CombinedDataset) else (dataset,)
    future = {}
    for member in members:
        meta = member.describe()
        if (datetime.fromisoformat(meta["end"]).timestamp() < cutoff or
                datetime.fromisoformat(meta["start"]).timestamp() >= end):
            continue
        rows = member.rows
        start = bisect_left(rows, cutoff, key=lambda bar: bar["ts"])
        stop = bisect_left(rows, end, key=lambda bar: bar["ts"])
        future.update((bar["ts"], bar) for bar in rows[start:stop])
    if chart_bars is not None:
        chart_bars.extend(future[t] for t in sorted(future))
    held_minutes = 0
    outcome = "held through horizon"
    for ts in range(cutoff, end, 300):
        bar = future.get(ts)
        if bar is None:
            outcome = "incomplete future coverage"
            break
        if bar["low"] < sector["floor"] or bar["high"] > sector["ceiling"]:
            outcome = "breached"
            break
        held_minutes += 5
    return {"horizon_hours": hours, "fully_contained_minutes": held_minutes, "outcome": outcome}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, metavar="FILE.png",
                        help="Save a candlestick chart of the scan with candidate sectors boxed.")
    parser.add_argument("--venue", choices=("Binance", "Coinbase"))
    parser.add_argument("--quote", nargs="+", default=["BTC", "ETH"],
                        help="Quote currencies to scan (default: BTC ETH). Use ALL for every quote.")
    parser.add_argument("--as-of", help="Decision time in ISO format with timezone. Default: latest common close.")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--evaluate-hours", type=int, metavar="HOURS",
                        help="Measure how long subsequent candles stay inside the frozen sector.")
    parser.add_argument("--max-span-pct", type=float, default=3.0,
                        help="Maximum high-to-low swing across the window, in percent of midpoint (default 3).")
    parser.add_argument("--fee-bps", type=float, default=20)
    parser.add_argument("--slip-bps", type=float, default=2)
    parser.add_argument("--history-dir", type=Path, default=Path(__file__).resolve().parents[1] / "var/history")
    args = parser.parse_args()
    if args.image:
        if args.image.suffix.lower() != ".png":
            parser.error("--image expects a .png filename.")
        try:
            from tools.sector_chart import draw_scan
        except ModuleNotFoundError as exc:
            parser.error(f"Image output requires matplotlib ({exc}). Use the project virtual environment.")
    if not math.isfinite(args.max_span_pct) or args.max_span_pct <= 0:
        parser.error("Use a positive finite maximum span percent.")
    if args.hours < 1 or not all(math.isfinite(x) and 0 <= x < 10000 for x in (args.fee_bps, args.slip_bps, args.max_span_pct)):
        parser.error("Use a positive lookback and per-side costs from 0 to under 10,000 bps.")
    if args.evaluate_hours is not None and args.evaluate_hours < 1:
        parser.error("Use a positive evaluation horizon.")
    snapshots = [d for d in load_history_datasets(args.history_dir).values() if d.timeframe == "5m"]
    quotes = {quote.upper() for quote in args.quote}
    snapshots = [d for d in snapshots if (not args.venue or d.venue == args.venue)
                 and ("ALL" in quotes or d.market.split("/")[1] in quotes)]
    if not snapshots:
        parser.error("No five-minute exchange snapshots found.")
    catalog = combine_datasets({d.id: d for d in snapshots})
    if args.as_of:
        try:
            when = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
            if when.tzinfo is None:
                raise ValueError()
            cutoff = int(when.timestamp())
        except ValueError:
            parser.error("Use an ISO timestamp with a timezone.")
    else:
        ends = {}
        for d in catalog.values():
            key = (d.venue, d.market)
            last_open = datetime.fromisoformat(d.describe()["end"]).timestamp()
            ends[key] = max(ends.get(key, 0), int(last_open) + 300)
        cutoff = min(ends.values())
    cutoff = cutoff // 300 * 300
    count = args.hours * 12 + 1
    first_open = cutoff - count * 300
    windows = {}
    conflicts = set()
    for dataset in catalog.values():
        key = (dataset.venue, dataset.market)
        candles, ranges, sources = windows.setdefault(key, ({}, [], []))
        meta = dataset.describe()
        ranges.append({k: meta[k] for k in ("id", "start", "end", "bars")})
        members = dataset.members if isinstance(dataset, CombinedDataset) else (dataset,)
        for d in members:
            metadata = d.describe()
            if (datetime.fromisoformat(metadata["end"]).timestamp() < first_open or
                    datetime.fromisoformat(metadata["start"]).timestamp() >= cutoff):
                continue
            rows = d.rows
            start = bisect_left(rows, first_open, key=lambda bar: bar["ts"])
            end = bisect_left(rows, cutoff, key=lambda bar: bar["ts"])
            for bar in rows[start:end]:
                ts = bar["ts"]
                if ts in candles and candles[ts] != bar:
                    conflicts.add(key)
                candles[ts] = bar
            sources.append(d.id)
    results = []
    panels = []
    for key, (candles, ranges, sources) in sorted(windows.items()):
        chart_bars = [] if args.image else None
        venue, market = key
        covering = [r["id"] for r in ranges if
                    datetime.fromisoformat(r["start"]).timestamp() <= first_open and
                    datetime.fromisoformat(r["end"]).timestamp() >= cutoff - 300]
        result = {"venue": venue, "market": market, "available_ranges": ranges,
                  "datasets": covering, "source_snapshots": sources, "window_bars": len(candles)}
        if key in conflicts:
            result["sector_status"] = "conflicting snapshot data"
        elif not covering or len(candles) != count:
            result["sector_status"] = "missing coverage"
        else:
            bars = [candles[ts] for ts in range(first_open, cutoff, 300)]
            if args.image:
                chart_bars.extend(bars)
            result.update(sector_setup(bars, args.fee_bps, args.slip_bps, args.max_span_pct))
            if args.evaluate_hours and result["sector_status"] == "candidate":
                if len(covering) == 1:
                    result["evaluation"] = evaluate_sector(catalog[covering[0]], cutoff,
                                                           args.evaluate_hours, result, chart_bars)
                else:
                    result["evaluation"] = {"outcome": "ambiguous dataset"}
        if len(covering) == 1 and key not in conflicts:
            result["dataset"] = covering[0]
        results.append(result)
        if args.image:
            panels.append((result, chart_bars))
    if args.image:
        draw_scan(args.image, panels, cutoff)
    print(json.dumps({"as_of_utc": datetime.fromtimestamp(cutoff, timezone.utc).isoformat(),
                      "lookback_hours": args.hours, "pairs": results}, indent=2))


if __name__ == "__main__":
    main()
