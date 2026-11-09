"""One-hour CHOP vs width-matched controls. Run: python -m research.chop_width"""
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

from gridlab.datasets import combine_datasets, load_history_datasets
from research.value_area_grid import simulate_grid
from research.value_area_stays import observe

WINDOW = 12
THRESHOLD = 61.8
CONFIG = dict(grace=2, horizon=288, inside='close')


def candidates(bars):
    groups = defaultdict(list)
    for i in range(WINDOW, len(bars)-290):
        if (bars[i]['ts'] + 300) % 3600:
            continue
        window = bars[i-WINDOW+1:i+1]
        low = min(b['low'] for b in window)
        high = max(b['high'] for b in window)
        if not low < bars[i]['close'] < high:
            continue  # both sides of the 25%-inventory grid must be fundable
        path = sum(max(b['high'], prev['close']) - min(b['low'], prev['close'])
                   for b, prev in zip(window, bars[i-WINDOW:i]))
        chop = 100 * math.log(path/(high-low)) / math.log(WINDOW)
        month = datetime.fromtimestamp(bars[i+1]['ts'], timezone.utc).strftime('%Y-%m')
        groups[month].append(dict(i=i, low=low, high=high, chop=chop,
                                  width=200*(high-low)/(high+low)))
    return groups


def match(group):
    controls = [r for r in group if r['chop'] <= THRESHOLD]
    pairs = []
    for event in group:
        if event['chop'] <= THRESHOLD or not controls:
            continue
        idx = min(range(len(controls)), key=lambda k: abs(math.log(controls[k]['width']/event['width'])))
        if abs(math.log(controls[idx]['width']/event['width'])) <= math.log(1.05):
            pairs.append((event, controls.pop(idx)))
    return pairs


def evaluate(bars, event):
    i, low, high = event['i'], event['low'], event['high']
    observed = observe(bars,i,low,high,CONFIG,300)
    last = observed['last_index']
    fills = []
    levels = [low+(high-low)*k/8 for k in range(8)]+[high]
    trade = simulate_grid(levels,bars[i]['close'],bars[i+1:last+1],bars[last+1],
                          initial_inventory_pct=25,record_fill=lambda ts,n:fills.append((ts,n)))
    assert math.isclose(sum(n for _,n in fills), trade['turnover'])
    return dict(**event, duration=observed['duration_candles'], net=trade['return_pct'],
                cycles=trade['cycles'], profitable=trade['profitable_cycles'], fills=fills, exit=last+1)


def summary(rows, bars, rates, months):
    rows = sorted(rows,key=lambda r:r['i'])
    capital = 10000/rates[bars[rows[0]['i']]['ts']] if rows else 0
    monthly = dict.fromkeys(months, 0.0)
    available = 0
    for r in rows:
        if r['i'] < available:
            continue
        for ts,n in r['fills']:
            month = datetime.fromtimestamp(ts,timezone.utc).strftime('%Y-%m')
            monthly[month] += n * capital * rates[ts]
        capital *= 1+r['net']/100
        available = r['exit']
    return dict(n=len(rows), width=mean(r['width'] for r in rows),
                mean_stay=mean(r['duration'] for r in rows), median_stay=median(r['duration'] for r in rows),
                mean_cycles=mean(r['cycles'] for r in rows), profitable_cycles=mean(r['profitable'] for r in rows),
                net=mean(r['net'] for r in rows), monthly_usdt=monthly,
                avg_monthly_usdt=mean(monthly.values()))


def main():
    catalog = combine_datasets(load_history_datasets(Path('var/history')))
    datasets = {d.market:d for d in catalog.values() if d.timeframe=='5m' and d.market in ('BTC/USDT','ETH/BTC') and d.describe().get('venue')=='Binance'}
    reference = datasets['BTC/USDT'].open().rows
    rates = {b['ts']:b['open'] for b in reference}
    results = {}
    for market,dataset in datasets.items():
        bars = [b for b in dataset.open().rows if b['ts'] in rates]
        assert len(bars)==len(reference) and all(b['ts']-a['ts']==300 for a,b in zip(bars,bars[1:]))
        fx = rates if market=='ETH/BTC' else dict.fromkeys(rates,1.)
        groups = candidates(bars)
        results[market] = {}
        for name,months in [('Jan-Jul',sorted(groups)[:7]),('Aug-Oct',sorted(groups)[7:])]:
            end = datetime(2026,8 if name=='Jan-Jul' else 11,1,tzinfo=timezone.utc).timestamp()
            hi,lo=[],[]
            for m in months:
                # Keep the entire potential observation and liquidation in this period.
                pool = [r for r in groups[m] if bars[r['i']+289]['ts']<end]
                for a,b in match(pool):
                    hi.append(evaluate(bars,a))
                    lo.append(evaluate(bars,b))
            results[market][name] = {k:summary(rows,bars,fx,months) for k,rows in [('high',hi),('control',lo)]}
    Path('var/chop-width.json').write_text(json.dumps(results,indent=2))
    for market,periods in results.items():
        for period,cohorts in periods.items():
            for key,r in cohorts.items():
                print(market,period,key,*(round(r[k],4) for k in ('n','width','mean_stay','median_stay','mean_cycles','profitable_cycles','net','avg_monthly_usdt')))


if __name__=='__main__':
    main()
