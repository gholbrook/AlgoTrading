"""Compare ETH/BTC sectors with repeated center crossings against matched quiet sectors."""
import argparse
import json
import math
from bisect import bisect_left, bisect_right
from datetime import datetime
from pathlib import Path
from random import Random
from statistics import mean, quantiles

from research.sector_grid import TICK, prefix_moments, read_bars, run, simulate, variance

ROOT = Path(__file__).resolve().parents[1]


def crossing_distance(formation, levels):
    """Count close crossings and the furthest grid line reached after any crossing."""
    ticks = [round(level/TICK) for level in levels]
    center = ticks[0] + ticks[-1]  # doubled to represent half ticks exactly
    previous_side = None
    crossings = depth = 0
    for bar in formation:
        close = 2 * round(bar["close"]/TICK)
        if close == center:
            continue
        above = close > center
        if previous_side is not None and previous_side != above:
            crossings += 1
        previous_side = above
        if crossings:
            reached = (sum(center < 2*t <= close for t in ticks) if above else
                       sum(close <= 2*t < center for t in ticks))
            depth = max(depth, reached)
    return crossings, depth


def observations(bars, moments, timestamps, n, begin, stop):
    records = run(bars, moments, n, begin, stop, quiet=True)[0.5]
    result = []
    for r in records:
        i = bisect_left(timestamps, int(datetime.fromisoformat(r["start"]).timestamp())-300)
        levels = r["levels"]
        end = i + r["minutes"]//5
        # Exclude the whole breakout candle: OHLC can't order its fills and breach.
        stop_idx = end+1 if r["direction"] == "timeout" else end
        future = bars[i+1:stop_idx]
        cycles = simulate(levels, bars[i]["close"], future)["cycles"] if future else 0
        count, depth = crossing_distance(bars[i-n+1:i+1], levels)
        result.append(dict(start=r["start"], repeated=count>=2, crossing_count=count,
                           grid_cells=len(levels)-1, deep=depth>=2,
                           width=math.log(levels[-1]/levels[0]),
                           volatility=math.sqrt(variance(moments,i-n,i)),
                           success=int(cycles>0), pnl_bp=r["pnl_bp"]))
    return result


def match(records, feature="repeated"):
    treated = [r for r in records if r[feature]]
    control = sorted((r for r in records if not r[feature]),key=lambda r:r["width"])
    widths = [r["width"] for r in control]
    edges = []
    for i,r in enumerate(treated):
        for j in range(bisect_left(widths,r["width"]/1.2),bisect_right(widths,r["width"]*1.2)):
            if feature == "deep" and (r["crossing_count"], r["grid_cells"]) != (
                    control[j]["crossing_count"], control[j]["grid_cells"]):
                continue
            v,w = r["volatility"],control[j]["volatility"]
            if max(v,w)>1.2*min(v,w):
                continue
            distance = math.hypot(math.log(r["width"]/widths[j]), math.log(v/w) if v else 0)
            edges.append((distance,i,j))
    used_t, used_c, pairs = set(),set(),[]
    for _,i,j in sorted(edges):
        if i not in used_t and j not in used_c:
            pairs.append((treated[i],control[j]))
            used_t.add(i)
            used_c.add(j)
    return pairs


def compare(records, feature="repeated"):
    if feature == "deep":
        records = [r for r in records if r["crossing_count"] > 0]
    pairs = match(records, feature)
    result = dict(sectors=len(records), **{feature:sum(r[feature] for r in records)}, pairs=len(pairs))
    if pairs:
        differences = [a["success"]-b["success"] for a,b in pairs]
        rng = Random(2026)
        bounds = quantiles([100*mean(rng.choices(differences,k=len(pairs))) for _ in range(500)],
                           n=40,method="inclusive")
        result.update(repeated_successes=sum(a["success"] for a,b in pairs),
                      control_successes=sum(b["success"] for a,b in pairs),
                      difference_pp=100*mean(differences), approximate_pair_interval=[bounds[0],bounds[-1]],
                      repeated_mean_pnl_bp=mean(a["pnl_bp"] for a,b in pairs),
                      control_mean_pnl_bp=mean(b["pnl_bp"] for a,b in pairs))
    if pairs and feature == "deep":
        for suffix in ("successes", "mean_pnl_bp"):
            result["deep_"+suffix] = result.pop("repeated_"+suffix)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distance", action="store_true", help="Test travel of at least two grid lines after crossing.")
    args = parser.parse_args()
    bars = read_bars(ROOT/"var/history")
    moments = prefix_moments(bars)
    timestamps = [r["ts"] for r in bars]
    report = {}
    periods = [("development","2026-01-01","2026-10-01"),("evaluation","2026-10-01","2026-11-01")]
    if args.distance:
        periods.append(("november_check","2026-11-01","2026-11-04"))
    for label,first,last in periods:
        begin,stop = [bisect_left(timestamps,datetime.fromisoformat(d+"T00:00:00+00:00").timestamp()) for d in (first,last)]
        report[label] = {n*5:compare(observations(bars,moments,timestamps,n,begin,stop), "deep" if args.distance else "repeated") for n in (6,12,24)}
    path = ROOT/"var/sector-grid"/("crossing-distance.json" if args.distance else "center-crossings.json")
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,indent=2))
    print(path.read_text())


if __name__ == "__main__":
    main()
