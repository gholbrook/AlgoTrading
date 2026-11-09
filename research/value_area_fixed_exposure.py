"""Paired grid sensitivity on exactly the same previously selected setups.

python -m research.value_area_fixed_exposure
Initial quote-budget allocations to BTC are 0%, 25%, and 50%; remaining cash
is allocated across buy levels below the signal price. Sell inventory is split
across targets above the signal price. Both use adjacent-level cycling.
"""
import argparse
import csv
import json
from pathlib import Path
from statistics import mean, median

from gridlab.datasets import combine_datasets, load_history_datasets
from research.value_area_grid import simulate_grid, portfolio


def summary(rows):
    if not rows: return dict(n=0)
    return dict(n=len(rows), mean_return_pct=mean(r['return_pct'] for r in rows),
                median_return_pct=median(r['return_pct'] for r in rows),
                mean_excess_pct=mean(r['excess_pct'] for r in rows),
                win_pct=100*mean(r['return_pct']>0 for r in rows),
                no_fill=sum(r['fills']==0 for r in rows),
                mean_cycles=mean(r['cycles'] for r in rows))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=Path('var/value-area-grid'))
    parser.add_argument('--output',type=Path,default=Path('var/value-area-fixed-exposure'))
    parser.add_argument('--history-dir',type=Path,default=Path('var/history'))
    args=parser.parse_args()
    source=json.loads((args.input/'results.json').read_text())
    saved=json.loads((args.input/'evaluation-trades.json').read_text())
    catalog=combine_datasets(load_history_datasets(args.history_dir))
    dataset=catalog[source['dataset']['id']]
    if dataset.describe()['sha256']!=source['dataset']['sha256']: raise ValueError('Dataset fingerprint mismatch')
    data=dataset.open() if hasattr(dataset,'open') else dataset
    bars=data.rows
    index={b['ts']:i for i,b in enumerate(bars)}
    results=[];details=[]
    for signal in ('rising_5','all'):
        candidates=saved[signal]
        for exposure in (0,25,50):
            for intervals in (4,8,12):
                simulations={}
                for candidate in candidates:
                    anchor=bars[index[candidate['anchor']]]
                    start=index[candidate['entry_ts']];stop=index[candidate['exit_ts']]
                    low,high=candidate['low'],candidate['high']
                    levels=[low+(high-low)*k/intervals for k in range(intervals+1)]
                    simulation=simulate_grid(levels,anchor['close'],bars[start:stop],bars[stop],
                                             fee_bps=2,slip_bps=1,initial_inventory_pct=exposure)
                    simulations[candidate['id']]=simulation
                totals,records=portfolio(candidates,set(simulations),simulations,'evaluation',-1,300)
                # Use the decision close to classify location, so a future filter
                # could evaluate these same categories before submitting orders.
                for record in records:
                    price=bars[index[record['anchor']]]['close']
                    record['position_pct']=100*(price-record['low'])/(record['high']-record['low'])
                    record['width_pct']=200*(record['high']-record['low'])/(record['high']+record['low'])
                    record.update(signal=signal, exposure=exposure, intervals=intervals)
                positions={name:summary([r for r in records if low<=r['position_pct']<high]) for name,low,high in
                           [('bottom',0,25),('lower_middle',25,50),('upper_middle',50,75),('top',75,100.000001)]}
                widths={name:summary([r for r in records if low<=r['width_pct']<high]) for name,low,high in
                        [('<0.5%',0,.5),('0.5–1%',.5,1),('1–2%',1,2),('≥2%',2,float('inf'))]}
                results.append(dict(signal=signal,exposure=exposure,intervals=intervals,**totals,
                                    position_groups=positions,width_groups=widths))
                details.extend(records)
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'results.json').write_text(json.dumps(dict(results=results,
        source=str(args.input.resolve()),dataset=source['dataset'],fee_bps=2,slip_bps=1,
        assumptions=['Exactly the same selected 171 rising-five and 325 baseline setups as the previous experiment.',
                     'Same frozen bounds, next-open entry and liquidation, and maximum 24h hold.',
                     'Fixed initial BTC budget split across sell targets above signal close; remaining cash across buys below.',
                     'Capital stays within its adjacent interval sleeve after each fill; no shorts.',
                     'One limit fill per sleeve per candle; newly enabled opposite orders wait until the next candle.',
                     'Position groups use signal close and are observational; width and market conditions can differ.',
                     'The evaluation period has already been inspected; findings require new-data validation.']),indent=2))
    with (args.output/'trades.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(details[0]));writer.writeheader();writer.writerows(details)
    plot(results,args.output)
    for r in results:
        if r['signal']=='rising_5':
            print(r['exposure'],r['intervals'],round(r['return_pct'],3),
                  {k:(v['n'],round(v.get('mean_return_pct',0),4)) for k,v in r['position_groups'].items()})


def plot(results,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,3,figsize=(16,5),layout='constrained')
    selected=[r for r in results if r['signal']=='rising_5']
    for exposure,color in [(0,'#16745c'),(25,'#c38b28'),(50,'#617fa2')]:
        rows=sorted([r for r in selected if r['exposure']==exposure],key=lambda r:r['intervals'])
        axes[0].plot([r['intervals'] for r in rows],[r['return_pct'] for r in rows],'o-',color=color,label=f'{exposure}% initially BTC')
        row=next(r for r in rows if r['intervals']==8)
        position_keys=['bottom','lower_middle','upper_middle','top']
        axes[1].plot(range(4),[row['position_groups'][k].get('mean_return_pct',float('nan')) for k in position_keys],'o-',color=color)
        axes[2].plot(range(4),[v.get('mean_return_pct',float('nan')) for v in row['width_groups'].values()],'o-',color=color)
    axes[0].set(title='Grid spacing · same 171 setups',xlabel='Grid intervals',ylabel='Compounded account return %',xticks=[4,8,12]);axes[0].legend(fontsize=9)
    axes[1].set(title='Entry position · 8 intervals',ylabel='Mean return per grid %',xticks=range(4),xticklabels=['Bottom\nn=84','Lower middle\nn=6','Upper middle\nn=4','Top\nn=77'])
    axes[2].set(title='Value-area width · 8 intervals',ylabel='Mean return per grid %',xticks=range(4),xticklabels=['<0.5%','0.5–1%','1–2%','≥2%'])
    for ax in axes:
        ax.axhline(0,color='gray',lw=.8);ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',alpha=.2)
    fig.suptitle('BTC/USDT · Fixed initial BTC allocation · 0.02% fee per fill',fontsize=15)
    fig.savefig(output/'comparison.png',dpi=170);plt.close(fig)


if __name__=='__main__':
    main()
