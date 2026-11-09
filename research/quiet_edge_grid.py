"""Run with python -m research.quiet_edge_grid. Returns are in quote currency."""
import json
from pathlib import Path
from statistics import mean
from gridlab.datasets import combine_datasets, load_history_datasets, utc_iso
from research.quiet_price_windows import windows

FEE = .0002
SLIP = .0001


def simulate(bars, start, last, low, high, liquidation):
    # Mirror four buys spaced evenly across the lower fifth into the upper fifth.
    cash = [.25]*4
    units = [0.0]*4
    width = high-low
    buys = [low + width*.2*k/3 for k in range(4)]
    sells = [high - width*.2*k/3 for k in range(4)]
    cycles = fills = 0
    turnover = 0.0
    for i in range(start,last+1):
        b = bars[i]
        for k in range(4):
            if units[k]:
                if b['high'] >= sells[k]:
                    notional = units[k]*max(b['open'],sells[k])
                    cash[k] = notional*(1-FEE)
                    units[k] = 0
                    turnover += notional
                    cycles += 1
                    fills += 1
            elif b['low'] <= buys[k]:
                notional = cash[k]/(1+FEE)
                units[k] = notional/min(b['open'],buys[k])
                cash[k] = 0
                turnover += notional
                fills += 1
    proceeds = sum(units)*liquidation*(1-SLIP)
    return dict(net_pct=100*(sum(cash)+proceeds*(1-FEE)-1), cycles=cycles,
                fills=fills+int(proceeds>0), turnover=turnover+proceeds)


def forward(bars, limit):
    results=[]
    i=11
    while i+2 < len(bars):
        formation=bars[i-11:i+1]
        low=min(b['low'] for b in formation)
        high=max(b['high'] for b in formation)
        if high<=low or 100*(high/low-1)>limit:
            i+=1
            continue
        last=i+1
        while last<len(bars)-2 and bars[last]['low']>=low and bars[last]['high']<=high:
            last+=1
        exit_index=last+1
        trade=simulate(bars,i+1,last,low,high,bars[exit_index]['open'])
        results.append(trade)
        i=exit_index
    factor=1
    for t in results:
        factor*=1+t['net_pct']/100
    return dict(grids=len(results),mean_net_pct=mean(t['net_pct'] for t in results),
                compounded_pct=100*(factor-1),cycles=sum(t['cycles'] for t in results),
                any_cycle_pct=100*mean(t['cycles']>0 for t in results))


def main():
    out={}
    for d in combine_datasets(load_history_datasets(Path('var/history'))).values():
        if d.market not in ('BTC/USDT','ETH/BTC') or d.timeframe!='5m' or d.describe()['venue']!='Binance':
            continue
        bars=[b for b in d.open().rows if '2026-01-01' <= utc_iso(b['ts'])[:10] <= '2026-10-31']
        assert all(b['ts']-a['ts']==300 for a,b in zip(bars,bars[1:]))
        indices={utc_iso(b['ts']):i for i,b in enumerate(bars)}
        for limit in (.25,.5,1):
            best=max(windows(bars,limit),key=lambda r:r['candles'])
            start=indices[best['start_utc']]
            last=start+best['candles']-1
            hindsight=simulate(bars,start,last,best['low'],best['high'],bars[last]['close'])
            split=next(i for i,b in enumerate(bars) if utc_iso(b['ts'])[:10]=='2026-08-01')
            results=dict(longest_window=best,hindsight=hindsight,jan_oct=forward(bars,limit),
                         aug_oct=forward(bars[split:],limit))
            out[f'{d.market} {limit}']=results
            print(d.market,limit,hindsight,results['jan_oct'],results['aug_oct'])
    Path('var/quiet-edge-grid.json').write_text(json.dumps(out,indent=2))


if __name__=='__main__':
    main()
