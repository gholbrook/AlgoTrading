"""Compare ETH/BTC sector boundary forecasts and frozen grids on the same quiet setups."""
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

from gridlab.datasets import utc_iso
from research.sector_grid import TICK, prefix_moments, read_bars, simulate, variance

ROOT = Path(__file__).resolve().parents[1]
N = 12  # One hour for formation and one hour for evaluation.


def tick(price):
    return round(price / TICK)


def price(index):
    return round(index * TICK, 5)


def profile(box, weighted):
    """Central 70% by mass; a candle's mass is spread evenly over its touched ticks."""
    hist = defaultdict(float)
    for bar in box:
        lo, hi = tick(bar["low"]), tick(bar["high"])
        mass = bar["volume"] if weighted else 1
        for level in range(lo, hi+1):
            hist[level] += mass / (hi-lo+1)
    total = sum(hist.values())
    if not total:
        return None
    bounds = []
    running = 0
    for level, mass in sorted(hist.items()):
        running += mass
        while len(bounds) < 2 and running >= (0.15, 0.85)[len(bounds)] * total:
            bounds.append(level)
    return tuple(bounds)


def score(bounds, future, start, spacing):
    low, high = bounds
    actual_low = min(tick(b["low"]) for b in future)
    actual_high = max(tick(b["high"]) for b in future)
    scale = TICK / start * 10000
    result = dict(lower_error_bp=abs(low-actual_low)*scale, upper_error_bp=abs(high-actual_high)*scale,
                  contained=int(low <= actual_low and actual_high <= high), deployed=0, cycle=0, pnl_bp=0)
    if high-low < 2 or not low <= tick(start) <= high:
        return result
    cells = min(12,high-low,max(2,round((high-low)/spacing)))
    levels = [price(round(low+(high-low)*k/cells)) for k in range(cells+1)]
    exit_at = next((i for i,b in enumerate(future) if not low <= tick(b["close"]) <= high),len(future))
    held = future[:exit_at]
    result.update(deployed=1, cycle=int(bool(held) and simulate(levels,start,held)["cycles"]>0),
                  pnl_bp=simulate(levels,start,future[:exit_at+1])["pnl_bp"])
    return result


def run():
    bars = read_bars(ROOT/"var/history")
    moments = prefix_moments(bars)
    groups = defaultdict(lambda: defaultdict(list))
    example = None
    i = 288
    while i+N < len(bars):
        future = bars[i+1:i+1+N]
        day = utc_iso(bars[i]["ts"]+300)[:10]
        period_end = "2026-10-01" if day < "2026-10-01" else "2026-11-01" if day < "2026-11-01" else "2026-11-04"
        if (utc_iso(future[-1]["ts"]+300)[:10] >= period_end or
                future[-1]["ts"]-bars[i-288]["ts"] != (288+N)*300 or
                variance(moments,i-N,i) >= 0.65**2 * variance(moments,i-288,i-N)):
            i += 1
            continue
        box = bars[i-N+1:i+1]
        volume = profile(box,True)
        if volume is None:
            i += 1
            continue
        bounds = {"volume":volume, "equal":profile(box,False),
                  "extremes":(min(tick(b["low"]) for b in box),max(tick(b["high"]) for b in box))}
        spacing = max(1,0.5*median(tick(b["high"])-tick(b["low"]) for b in box))
        for name,limits in bounds.items():
            groups[period_end][name].append(score(limits,future,box[-1]["close"],spacing))
        if day.startswith("2026-10") and example is None:
            example = (box,future,bounds)
        i += 2*N  # disjoint formation + observation windows, regardless of method
    out = ROOT/"var/volume-profile"
    out.mkdir(parents=True,exist_ok=True)
    report = {}
    for end, methods in groups.items():
        report[end] = {}
        for name, records in methods.items():
            report[end][name] = {"setups":len(records), **{k:mean(r[k] for r in records) for k in records[0]}}
    (out/"results.json").write_text(json.dumps(report,indent=2))
    from matplotlib.figure import Figure
    box,future,bounds = example
    fig = Figure(figsize=(9,4.6),layout="constrained")
    ax = fig.subplots()
    data = box+future
    x = [(i-N+1)*5 for i in range(len(data))]
    ax.vlines(x,[b["low"] for b in data],[b["high"] for b in data],color="0.65")
    ax.plot(x,[b["close"] for b in data],".-",color="#23465b",label="Five-minute close")
    for (name,limits),color,style in zip(bounds.items(),["#b64d24","#29825d","#6f65a8"],["-","--",":"]):
        ax.hlines([price(v) for v in limits],0,60,color=color,linestyle=style,label=name)
    ax.axvline(0,color="0.5",linewidth=.8)
    ax.set(title="First October setup · "+utc_iso(box[-1]["ts"]+300),xlabel="Minutes from forecast",ylabel="BTC per ETH")
    ax.legend(ncol=2)
    fig.savefig(out/"example.png",dpi=140)
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    run()
