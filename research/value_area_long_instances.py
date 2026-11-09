"""Hindsight diagnostic: grid returns on instances whose duration exceeds a threshold.

python -m research.value_area_long_instances --threshold 150
Selection uses eventual duration and is intentionally not an executable signal.
"""
import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from gridlab.datasets import combine_datasets, load_history_datasets
from research.value_area_grid import simulate_grid, portfolio
from research.value_area_predictability import features

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--threshold',type=int,default=150)
    args=parser.parse_args()
    source=json.loads((ROOT/'var/value-area-grid/results.json').read_text())
    report=json.loads(Path(source['source']).read_text())
    dataset=combine_datasets(load_history_datasets(ROOT/'var/history'))[source['dataset']['id']]
    if dataset.describe()['sha256']!=source['dataset']['sha256']: raise ValueError('Dataset fingerprint mismatch')
    data=dataset.open() if hasattr(dataset,'open') else dataset
    bars=data.rows;index={b['ts']:i for i,b in enumerate(bars)}
    split=datetime.fromisoformat(source['split']).timestamp()
    duration_ids={r['id'] for r in features(report['episodes'],5) if r['recent_mean']>r['earlier_mean']}
    candidates=[];future_map={};unusable=[]
    for e in report['episodes']:
        if e['duration_candles']<=args.threshold:continue
        anchor=index[e['anchor']];last=index[e['observed_end']]
        if e['outcome'] in ('gap','data_end') or last+1>=len(bars) or bars[last+1]['ts']-bars[last]['ts']!=300:
            unusable.append(e['id']);continue
        c=dict(id=e['id'],anchor=e['anchor'],entry_ts=bars[anchor+1]['ts'],exit_ts=bars[last+1]['ts'],
               low=e['value_low'],high=e['value_high'],duration=e['duration_candles'],
               censored=e['censored'],outcome=e['outcome'])
        candidates.append(c)
        future_map[e['id']]=(bars[anchor]['close'],bars[anchor+1:last+1],bars[last+1])
    results=[];details=[]
    for exposure in (0,25,50):
        for intervals in (4,8,12):
            for fee in (0,2,20):
                simulations={}
                for c in candidates:
                    levels=[c['low']+(c['high']-c['low'])*k/intervals for k in range(intervals+1)]
                    anchor,future,exit_bar=future_map[c['id']]
                    simulations[c['id']]=simulate_grid(levels,anchor,future,exit_bar,fee_bps=fee,
                                                       initial_inventory_pct=exposure)
                for period in ('training','evaluation'):
                    for subset in ('all_long','rising5_and_long'):
                        allowed={c['id'] for c in candidates if subset=='all_long' or c['id'] in duration_ids}
                        summary,trades=portfolio(candidates,allowed,simulations,period,split,300)
                        results.append(dict(period=period,subset=subset,exposure=exposure,
                                            intervals=intervals,fee_bps=fee,**summary))
                        if period=='evaluation' and subset=='all_long' and intervals==8 and fee==2:
                            details.extend(dict(exposure=exposure,**trade) for trade in trades)
    output=ROOT/'var/value-area-long-instances';output.mkdir(parents=True,exist_ok=True)
    (output/'results.json').write_text(json.dumps(dict(threshold_exclusive=args.threshold,
        source=source['source'],dataset=source['dataset'],split=source['split'],
        qualifying_instances=len(candidates),unusable=unusable,
        assumptions=['Hindsight selection based on eventual duration; no real-time deployability claim.',
                     'Enter at first following candle open, before knowing whether the instance will qualify.',
                     'Exit at next open after confirmed breakout or 288-candle maximum observation.',
                     '1 bp market slippage, fixed initial BTC allocation, same cell execution model as prior tests.',
                     'Single active grid; no overlapping capital deployment.'],results=results),indent=2,allow_nan=False))
    with (output/'scenarios.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(results[0]));writer.writeheader();writer.writerows(results)
    (output/'evaluation-trades.json').write_text(json.dumps(details,indent=2))
    for row in results:
        if row['period']=='evaluation' and row['subset']=='all_long' and row['intervals']==8:
            print(row['exposure'],row['fee_bps'],row['trades'],round(row['return_pct'],3),
                  round(row['mean_return_pct'] or 0,4),round(row['win_pct'] or 0,1),round(row['mean_excess_pct'] or 0,4))


if __name__=='__main__':
    main()
