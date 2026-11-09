"""Compare two independent equal-capital spot grids on the same frozen episodes.

Run from repository root: python -m research.value_area_poc_halves
"""
import csv
import json
from pathlib import Path
from statistics import mean, median

from gridlab.datasets import combine_datasets, load_history_datasets
from research.value_area_bullish import daily_ema
from research.value_area_grid import simulate_grid


def section(low, high, anchor, future, exit_bar):
    # Four equal intervals per half. Start each account with identical exposure.
    # A sleeve with no eligible level keeps its initial cash or BTC until exit.
    levels = [low + (high-low)*i/4 for i in range(4)] + [high]
    cash = (simulate_grid(levels, anchor, future, exit_bar, initial_inventory_pct=0)['return_pct']
            if low < anchor else 0)
    hold = 100*(exit_bar['open']*(1-.0001)*(1-.0002) /
                (future[0]['open']*(1+.0001)*(1+.0002))-1)
    btc = (simulate_grid(levels, anchor, future, exit_bar, initial_inventory_pct=100)['return_pct']
           if high > anchor else hold)
    return .75*cash + .25*btc


def main():
    report = json.loads(Path('var/range-explorer.json').read_text())
    dataset = combine_datasets(load_history_datasets(Path('var/history')))[report['dataset']['id']]
    if dataset.describe()['sha256'] != report['dataset']['sha256'] or dataset.market != 'BTC/USDT' or dataset.timeframe != '5m':
        raise ValueError('Expected matching BTC/USDT five-minute dataset')
    bars = dataset.open().rows if hasattr(dataset,'open') else dataset.rows
    index = {b['ts']:i for i,b in enumerate(bars)}
    ema = daily_ema(bars)
    rows = []
    for e in report['episodes']:
        if e['duration_candles'] < 150:
            continue
        i, j = index[e['anchor']], index[e['observed_end']]
        row = dict(id=e['id'], anchor=e['anchor'], regime='unclassified', lower_pct=None, upper_pct=None, difference_pp=None)
        rows.append(row)
        day = e['anchor']//86400*86400
        a,b = ema.get(day-86400), ema.get(day-2*86400)
        if a is not None and b is not None:
            row['regime'] = 'bullish' if a>b else 'bearish' if a<b else 'flat'
        if (e['outcome'] not in ('horizon','exit') or j+1>=len(bars) or j<=i
            or bars[j+1]['ts']-bars[i]['ts'] != 300*(j+1-i)
            or not e['value_low'] < e['poc'] < e['value_high']):
            continue
        args = bars[i]['close'], bars[i+1:j+1], bars[j+1]
        lower = section(e['value_low'],e['poc'],*args)
        upper = section(e['poc'],e['value_high'],*args)
        row.update(lower_pct=lower, upper_pct=upper, difference_pp=lower-upper)
        if e.get('grid_result',{}).get('return_pct') is not None:
            # On a full band with 8 cells the same 75:25 sleeves reproduce the reference.
            levels=[e['value_low']+(e['value_high']-e['value_low'])*k/8 for k in range(9)]
            if levels[0]<args[0]<levels[-1]:
                combined=sum(w*simulate_grid(levels,*args,initial_inventory_pct=p)['return_pct']
                             for w,p in ((.75,0),(.25,100)))
                assert abs(combined-e['grid_result']['return_pct'])<1e-8
    out=Path('var/value-area-poc-halves.csv')
    with out.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    for regime in ('bullish','bearish','unclassified','flat'):
        group=[r for r in rows if r['regime']==regime and r['difference_pp'] is not None]
        if group:
            print(regime,len(group), 'mean lower, upper, paired difference, median difference:',
                  *[round(fn(r[k] for r in group),4) for fn,k in ((mean,'lower_pct'),(mean,'upper_pct'),(mean,'difference_pp'),(median,'difference_pp'))],
                  'lower wins',sum(r['difference_pp']>0 for r in group))
    print('unscored',sum(r['difference_pp'] is None for r in rows))


if __name__ == '__main__':
    main()
