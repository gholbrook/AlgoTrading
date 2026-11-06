"""Compare frozen ETH/BTC spot grids after local volatility compression."""
import argparse
import json
import math
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

from gridlab.datasets import load_history_datasets, utc_iso

TICK = 0.00001
CLOCK = 12  # five-minute candles per hour


def read_bars(directory):
    candles = {}
    for data in load_history_datasets(directory).values():
        if (data.venue, data.market, data.timeframe) != ("Binance", "ETH/BTC", "5m"):
            continue
        for bar in data.rows:
            if candles.setdefault(bar["ts"], bar) != bar:
                raise ValueError("Conflicting ETH/BTC snapshots")
    return [candles[t] for t in sorted(candles)]


def prefix_moments(bars):
    sums, squares = [0.0], [0.0]
    for a, b in zip(bars, bars[1:]):
        r = math.log(b["close"] / a["close"])
        sums.append(sums[-1] + r)
        squares.append(squares[-1] + r*r)
    return sums, squares


def variance(moments, a, b):
    sums, squares = moments
    n = b-a
    return max(0.0, (squares[b]-squares[a] - (sums[b]-sums[a])**2/n)/(n-1))


def simulate(levels, start, future, monthly=None):
    # One equal-capital cell per adjacent pair. At setup, cells above the
    # market start in ETH and the others in BTC. Seed sales aren't cycles.
    n = len(levels)-1
    states = ["seed" if lower >= start else "cash" for lower in levels[:-1]]
    cash = [1/n if state == "cash" else 0 for state in states]
    eth = [1/(n*start) if state == "seed" else 0 for state in states]
    seed = sum(eth)
    turnover = cycles = 0
    for bar in future:
        if monthly is not None:
            bucket = monthly.setdefault(utc_iso(bar["ts"])[:7], {"fills": 0, "bought": 0.0, "sold": 0.0})
        for k, state in enumerate(states):
            buy, sell = levels[k:k+2]
            if state == "cash" and bar["low"] < buy:
                turnover += cash[k]
                if monthly is not None:
                    bucket["fills"] += 1
                    bucket["bought"] += cash[k]
                eth[k], cash[k], states[k] = cash[k]/buy, 0, "bought"
            elif state != "cash" and bar["high"] > sell:
                cash[k] = eth[k]*sell
                turnover += cash[k]
                if monthly is not None:
                    bucket["fills"] += 1
                    bucket["sold"] += cash[k]
                eth[k], states[k] = 0, "cash"
                cycles += state == "bought"
    mark = future[-1]["close"]
    value = sum(cash) + sum(eth)*mark
    return dict(cycles=cycles, turnover=turnover, inventory_pct=100*sum(eth)*mark/value,
                pnl_bp=10000*(value-1), excess_bp=10000*(value-(1+seed*(mark-start))))


def run(bars, moments, n, begin, stop, quiet, monthly=None):
    groups = defaultdict(list)
    i = max(288,begin)
    while i + 72 < stop:  # identical six-hour follow-up eligibility for all outcomes
        if bars[i+72]["ts"]-bars[i-288]["ts"] != 360*300:
            i += 1
            continue
        if quiet and variance(moments,i-n,i) >= 0.65**2 * variance(moments,i-288,i-n):
            i += 1
            continue
        box = bars[i-n+1:i+1]
        low = round(min(b["low"] for b in box)/TICK)
        high = round(max(b["high"] for b in box)/TICK)
        if high-low < 2:
            i += 1
            continue
        end = i+72
        direction = "timeout"
        for j in range(i+1,end+1):
            close = round(bars[j]["close"]/TICK)
            if close < low or close > high:
                direction = "down" if close < low else "up"
                end = j
                break
        typical = median((b["high"]-b["low"])/TICK for b in box)
        for multiple in (0.5,1.0,2.0):
            count = min(high-low,12,max(2,round((high-low)/max(1,multiple*typical))))
            levels = [round(low+(high-low)*k/count)*TICK for k in range(count+1)]
            levels = [round(p,5) for p in levels]
            record = simulate(levels,bars[i]["close"],bars[i+1:end+1],
                              monthly.setdefault(multiple, {}) if monthly is not None else None)
            record.update(start=utc_iso(bars[i]["ts"]+300),minutes=(end-i)*5,
                          direction=direction, levels=levels)
            groups[multiple].append(record)
        i = end+n
    return groups


def summarize(records):
    hours = sum(r["minutes"] for r in records)/60
    exits = [r for r in records if r["direction"] != "timeout"]
    return dict(sectors=len(records), breakouts=len(exits),
                median_observed_minutes=median(r["minutes"] for r in records),
                cycles_per_hour=sum(r["cycles"] for r in records)/hours,
                turnover_per_hour=sum(r["turnover"] for r in records)/hours,
                with_cycle_pct=100*mean(r["cycles"]>0 for r in records),
                mean_exit_eth_pct=mean(r["inventory_pct"] for r in exits) if exits else None,
                exit_eth_pct_by_direction={side: mean(r["inventory_pct"] for r in exits if r["direction"] == side)
                                           for side in ("up","down") if any(r["direction"] == side for r in exits)},
                mean_pnl_bp=mean(r["pnl_bp"] for r in records),
                mean_excess_vs_hold_bp=mean(r["excess_bp"] for r in records))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    p.add_argument("--history-dir",type=Path,default=root/"var/history")
    p.add_argument("--output",type=Path,default=root/"var/sector-grid")
    args = p.parse_args()
    bars = read_bars(args.history_dir)
    moments = prefix_moments(bars)
    results = []
    days = [utc_iso(b["ts"])[:10] for b in bars]
    for period, start, stop in [("development","2026-01-01","2026-10-01"),
                                ("evaluation","2026-10-01","2026-11-01")]:
        for quiet in (True,False):
            for n in (6,12,24):
                monthly = {}
                groups = run(bars,moments,n,bisect_left(days,start),bisect_left(days,stop),quiet,monthly)
                for multiple, records in groups.items():
                    results.append(dict(period=period,quiet=quiet,formation=n*5,spacing=multiple,
                                        monthly_volume_btc={month: {**v, "total": v["bought"] + v["sold"]}
                                                            for month,v in monthly[multiple].items()},
                                        **summarize(records)))
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/"results.json").write_text(json.dumps(results,indent=2))
    from matplotlib.figure import Figure
    fig = Figure(figsize=(10,4),layout="constrained")
    axes = fig.subplots(1,2)
    october = [r for r in results if r["period"]=="evaluation" and r["quiet"]]
    for ax, metric, title in zip(axes,["cycles_per_hour","mean_pnl_bp"],
                                 ["Completed buy–sell cycles / active hour","Mean marked return / sector (bp)"]):
        for n in (30,60,120):
            data = [r for r in october if r["formation"]==n]
            ax.plot([r["spacing"] for r in data],[r[metric] for r in data],"o-",label=f"{n} min")
        ax.set(xlabel="Spacing × median candle span",title=title,xticks=[0.5,1,2])
        ax.axhline(0,color="gray",lw=.6)
        ax.legend(title="Formation")
    fig.suptitle("ETH/BTC  ·  October 2026  ·  zero-fee simulation")
    fig.savefig(args.output/"comparison.png",dpi=140)


if __name__ == "__main__":
    main()
