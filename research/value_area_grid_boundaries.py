"""Describe grid outcomes by frozen boundary width and initial price location.

python -m research.value_area_grid_boundaries --input var/value-area-grid
Uses the eight-interval, 2-bps evaluation trades saved by value_area_grid.
Groups are descriptive comparisons on the already-inspected evaluation period.
"""
import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

from gridlab.datasets import combine_datasets, load_history_datasets
from research.value_area_predictability import percentile


def summarize(rows):
    if not rows:
        return dict(n=0)
    return dict(n=len(rows), wins=sum(r['return_pct']>0 for r in rows),
                win_pct=100*mean(r['return_pct']>0 for r in rows),
                mean_return_pct=mean(r['return_pct'] for r in rows),
                median_return_pct=median(r['return_pct'] for r in rows),
                mean_excess_pct=mean(r['excess_pct'] for r in rows),
                mean_cycles=mean(r['cycles'] for r in rows),
                mean_fees_pct=mean(r['fees_pct'] for r in rows),
                median_width_pct=median(r['width_pct'] for r in rows),
                mean_entry_position_pct=mean(r['entry_position_pct'] for r in rows),
                mean_initial_inventory_allocation_pct=mean(r['initial_inventory_allocation_pct'] for r in rows),
                no_fill_instances=sum(r['fills']==0 for r in rows),
                mean_lower_distance_pct=mean(r['lower_distance_pct'] for r in rows),
                mean_upper_distance_pct=mean(r['upper_distance_pct'] for r in rows))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,default=Path('var/value-area-grid'))
    p.add_argument('--history-dir',type=Path,default=Path('var/history'))
    p.add_argument('--signal',default='rising_5')
    args=p.parse_args()
    experiment=json.loads((args.input/'results.json').read_text())
    trades=json.loads((args.input/'evaluation-trades.json').read_text())
    catalog=combine_datasets(load_history_datasets(args.history_dir))
    dataset=catalog[experiment['dataset']['id']]
    if dataset.describe()['sha256']!=experiment['dataset']['sha256']:
        raise ValueError('The candle dataset fingerprint differs from the simulation.')
    data=dataset.open() if hasattr(dataset,'open') else dataset
    candles={bar['ts']:bar for bar in data.rows}
    enriched=[]
    for signal,records in trades.items():
        for row in records:
            width=row['high']-row['low']
            entry=candles[row['entry_ts']]['open']
            close=candles[row['anchor']]['close']
            enriched.append({**row,'signal':signal,
                'entry_utc':datetime.fromtimestamp(row['entry_ts'],timezone.utc).isoformat(),
                'entry_open':entry,'signal_close':close,
                'width_quote':width,'width_pct':100*width/((row['high']+row['low'])/2),
                'step_quote':width/8,'step_pct_at_entry':100*width/8/entry,
                'entry_position_pct':100*(entry-row['low'])/width,
                'signal_position_pct':100*(close-row['low'])/width,
                'initial_inventory_allocation_pct':100*sum(row['low']+width*k/8>=close for k in range(8))/8,
                'lower_distance_pct':100*(entry-row['low'])/entry,
                'upper_distance_pct':100*(row['high']-entry)/entry,
                'same_price_round_trip_cost_pct':100*((1+.0002)/(1-.0002)-1)})
    with (args.input/'grid-boundaries.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(enriched[0]));writer.writeheader();writer.writerows(enriched)
    rows=[r for r in enriched if r['signal']==args.signal]
    if not rows: raise ValueError('No grids for this signal')
    width_groups={name:[r for r in rows if low<=r['width_pct']<high] for name,low,high in
                  [('<0.5%',0,.5),('0.5–1%',.5,1),('1–2%',1,2),('≥2%',2,float('inf'))]}
    position_groups={name:[r for r in rows if low<=r['entry_position_pct']<high] for name,low,high in
                     [('Below VAL',-float('inf'),0),('Bottom quarter',0,25),('Lower middle',25,50),
                      ('Upper middle',50,75),('Top quarter',75,100.00000001),('Above VAH',100.00000001,float('inf'))]}
    # Also show quartiles to prevent fixed bucket sizes hiding most observations.
    cuts=[percentile([r['width_pct'] for r in rows],q) for q in (.25,.5,.75)]
    quartiles={str(k+1):summarize([r for r in rows if sum(r['width_pct']>cut for cut in cuts)==k]) for k in range(4)}
    report=dict(signal=args.signal,total=summarize(rows),
                boundary_values={key:dict(min=min(r[key] for r in rows),median=median(r[key] for r in rows),max=max(r[key] for r in rows)) for key in ('low','high','width_quote','width_pct','step_pct_at_entry')},
                width_groups={k:summarize(v) for k,v in width_groups.items()},
                position_groups={k:summarize(v) for k,v in position_groups.items()},
                width_quartile_cuts=cuts,width_quartiles=quartiles,
                winners=summarize([r for r in rows if r['return_pct']>0]),
                losers=summarize([r for r in rows if r['return_pct']<0]),
                below_fee_hurdle=sum(r['step_pct_at_entry']<r['same_price_round_trip_cost_pct'] for r in rows),
                note='Descriptive association on evaluation data, not a newly validated entry filter. Equal allocation across 8 cells, 2 bps fees each side. Distances use next-open market price before slippage.')
    (args.input/'boundary-analysis.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    plot(rows,report,args.input)
    print(json.dumps(report,indent=2))


def plot(rows,report,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,2,figsize=(13,8),layout='constrained')
    colors=['#16745c' if r['return_pct']>0 else '#bd654b' for r in rows]
    for ax,key,label in [(axes[0,0],'width_pct','Value-area width / midpoint · %'),
                         (axes[0,1],'entry_position_pct','Entry location · 0 = VAL, 100 = VAH')]:
        ax.scatter([r[key] for r in rows],[r['return_pct'] for r in rows],c=colors,s=22,alpha=.65)
        ax.set(xlabel=label,ylabel='Net return per grid · %')
    for ax,groups,title in [(axes[1,0],report['width_groups'],'Outcomes by value-area width'),
                             (axes[1,1],report['position_groups'],'Outcomes by entry position')]:
        pairs=[(k,v) for k,v in groups.items() if v['n']]
        ax.bar([k for k,v in pairs],[v['mean_return_pct'] for k,v in pairs],color='#73869b')
        for i,(name,stats) in enumerate(pairs):
            ax.annotate(f"n={stats['n']}\n{stats['win_pct']:.0f}% wins",(i,stats['mean_return_pct']),
                        xytext=(0,5 if stats['mean_return_pct']>=0 else -5),textcoords='offset points',
                        ha='center',va='bottom' if stats['mean_return_pct']>=0 else 'top',fontsize=9)
        ax.margins(y=.35)
        ax.set(title=title,ylabel='Mean net return per grid · %')
        ax.tick_params(axis='x',rotation=15)
    for ax in axes.flat:
        ax.axhline(0,color='gray',lw=.8);ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',alpha=.15)
    fig.suptitle(f"BTC/USDT · {report['signal']} · {len(rows)} grids · 8 intervals · 0.02% fee per fill",fontsize=14)
    fig.savefig(output/'boundary-analysis.png',dpi=170)
    plt.close(fig)


if __name__=='__main__':
    main()
