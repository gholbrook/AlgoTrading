"""Run with python -m research.bounce_edge_grid. Five-minute Binance OHLCV."""
import csv
import json
from math import prod
from pathlib import Path
from statistics import mean, median

from gridlab.datasets import combine_datasets, load_history_datasets, utc_iso
from research.quiet_edge_grid import simulate, forward

RADIUS = 3
LOOKBACK = 72
TOLERANCE = 0.0005
DEPARTURE = 0.001
MAX_WIDTH = 0.005


def zones(bars, pivots, end, side, touches):
    # Confirm independent visits to approximately the same price. A breach
    # outside the tolerance ends the sequence; later tests may form a new zone.
    recent = [i for i in pivots if end-LOOKBACK < i <= end-RADIUS]
    for last in reversed(recent):
        level = bars[last][side]
        outside = level * (1-TOLERANCE if side == 'low' else 1+TOLERANCE)
        hits = [last]
        for earlier in reversed([i for i in recent if i < last]):
            if abs(bars[earlier][side]/level-1) > TOLERANCE:
                continue
            between = bars[earlier+1:hits[-1]]
            if side == 'low':
                moved = any(b['high'] >= level*(1+DEPARTURE) for b in between)
            else:
                moved = any(b['low'] <= level*(1-DEPARTURE) for b in between)
            if moved and hits[-1]-earlier > RADIUS:
                hits.append(earlier)
            if len(hits)==touches:
                segment = bars[earlier:end+1]
                broken = (min(b['low'] for b in segment)<outside if side=='low'
                          else max(b['high'] for b in segment)>outside)
                if not broken:
                    yield median(bars[i][side] for i in hits), tuple(reversed(hits))
                break


def run(bars, first, stop, touches):
    pivots = {'low':[], 'high':[]}
    used = set()
    available = first
    rows = []
    for end in range(max(2*RADIUS,first-LOOKBACK),stop-2):
        pivot = end-RADIUS
        neighbors = bars[pivot-RADIUS:pivot]+bars[pivot+1:end+1]
        for field in pivots:
            prices = [b[field] for b in neighbors]
            p = bars[pivot][field]
            # Last candle of a flat extremum, allowing equal prices on the left.
            if ((field=='low' and p<=min(prices) and p<min(prices[RADIUS:])) or
                (field=='high' and p>=max(prices) and p>max(prices[RADIUS:]))):
                pivots[field].append(pivot)
            pivots[field] = [i for i in pivots[field] if end-LOOKBACK<i]
        if end < available:
            continue
        close = bars[end]['close']
        support = next((z for z in zones(bars,pivots['low'],end,'low',touches) if z[0]<close),None)
        resistance = next((z for z in zones(bars,pivots['high'],end,'high',touches) if z[0]>close),None)
        if not support or not resistance:
            continue
        low, lows = support
        high, highs = resistance
        if high/low-1>MAX_WIDTH or (lows,highs) in used:
            continue
        used.add((lows,highs))
        last = end+1
        while last<stop-2 and bars[last]['low']>=low and bars[last]['high']<=high:
            last+=1
        available=last+1
        trade=simulate(bars,end+1,last,low,high,bars[available]['open'])
        rows.append(dict(signal_utc=utc_iso(bars[end]['ts']+300),exit_utc=utc_iso(bars[available]['ts']),
                         support=low,resistance=high,width_pct=100*(high/low-1),
                         support_hits=[utc_iso(bars[i]['ts']) for i in lows],
                         resistance_hits=[utc_iso(bars[i]['ts']) for i in highs],**trade))
    return rows


def main():
    output=Path('var/bounce-edge-grid')
    output.mkdir(exist_ok=True)
    summary={}
    all_rows=[]
    for d in combine_datasets(load_history_datasets(Path('var/history'))).values():
        if d.market not in ('BTC/USDT','ETH/BTC') or d.timeframe!='5m' or d.describe()['venue']!='Binance':
            continue
        bars=[b for b in d.open().rows if '2026-01-01'<=utc_iso(b['ts'])[:10]<='2026-10-31']
        assert all(b['ts']-a['ts']==300 for a,b in zip(bars,bars[1:]))
        split=next(i for i,b in enumerate(bars) if utc_iso(b['ts'])[:10]=='2026-08-01')
        for name,start,stop in [('Jan-Jul',0,split),('Aug-Oct',split,len(bars))]:
            result={'one_hour':forward(bars[start:stop],100*MAX_WIDTH)}
            for touches in (2,3):
                rows=run(bars,start,stop,touches)
                result[str(touches)]=dict(grids=len(rows), mean_net_pct=mean(r['net_pct'] for r in rows) if rows else None,
                    compounded_pct=100*(prod(1+r['net_pct']/100 for r in rows)-1),
                    cycles=sum(r['cycles'] for r in rows),
                    any_cycle_pct=100*mean(r['cycles']>0 for r in rows) if rows else None)
                all_rows.extend(dict(market=d.market,period=name,touches=touches,**r) for r in rows)
            summary[d.market+' '+name]=result
            print(d.market,name,result,flush=True)
    (output/'summary.json').write_text(json.dumps(summary,indent=2))
    with (output/'grids.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(all_rows[0]))
        writer.writeheader()
        writer.writerows(all_rows)


if __name__=='__main__':
    main()
