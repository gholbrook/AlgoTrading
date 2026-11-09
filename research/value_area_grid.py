"""Causal duration-signal and spot-grid sensitivity study on exported instances.

python -m research.value_area_grid INPUT.json --output var/value-area-grid
Reuses duration features and frozen-boundary observation rules. The grid uses
one equal-budget cell per adjacent price pair, following sector_grid.simulate's
cell design, with next-open entry, costs, and explicit next-open liquidation.
"""
import argparse
import copy
import csv
import json
from bisect import bisect_left
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

from gridlab.datasets import combine_datasets, load_history_datasets, INTERVAL
from research.value_area_predictability import features, rate
from research.value_area_stays import observe, settings

SIGNALS = [('all', 10, 0, 0, None), ('rising_3',3,1,0,None), ('rising_5',5,1,0,None),
           ('rising_10',10,1,0,None), ('rise25_5',5,1.25,0,None),
           ('rise50_5',5,1.5,0,None), ('rise25_min24_5',5,1.25,24,None),
           ('rising_5_cap100',5,1,0,100)]


def simulate_grid(levels, anchor, future, exit_bar, fee_bps=2, slip_bps=1, initial_inventory_pct=None, record_fill=None):
    """One unit of quote capital. Pending limits persist; each cell fills once per bar.

    Above-market cells buy seed inventory at the first future open. Other cells
    wait at their lower level. A buy enables an upper-level sell on the following
    candle; a sell enables a new lower-level buy on the following candle.
    Liquidation at exit_bar.open occurs before any further grid fills.
    With initial_inventory_pct, allocate that share of quote budget equally to
    initial sells above the signal price, and the remainder to buys below it.
    Each funded sleeve then cycles between its adjacent levels.
    """
    fee, slip = fee_bps/10000, slip_bps/10000
    n=len(levels)-1
    if not future:
        raise ValueError('A grid requires at least one future candle')
    entry=future[0]['open']*(1+slip)
    pairs = list(zip(levels, levels[1:]))
    if initial_inventory_pct is None:
        budgets = [1/n]*n
        initial_seeds = [lower >= anchor for lower, upper in pairs]
    else:
        if not 0 <= initial_inventory_pct <= 100:
            raise ValueError('initial_inventory_pct must be between 0 and 100')
        fraction = initial_inventory_pct / 100
        buy_pairs = [(low, high) for low, high in pairs if low < anchor]
        sell_pairs = [(low, high) for low, high in pairs if high > anchor]
        if (fraction < 1 and not buy_pairs) or (fraction > 0 and not sell_pairs):
            raise ValueError('The signal price must leave room for the configured buy and sell allocations')
        # Separate sleeves allow a straddling interval to hold both an initial
        # buy order and an initial sell order without double-spending capital.
        pairs = (buy_pairs if fraction < 1 else []) + (sell_pairs if fraction > 0 else [])
        budgets = ([(1-fraction)/len(buy_pairs)]*len(buy_pairs) if fraction < 1 else [])
        budgets += ([fraction/len(sell_pairs)]*len(sell_pairs) if fraction > 0 else [])
        initial_seeds = ([False]*len(buy_pairs) if fraction < 1 else [])
        initial_seeds += ([True]*len(sell_pairs) if fraction > 0 else [])
        n = len(pairs)
    cash=budgets.copy(); inventory=[0.0]*n; seeded=[False]*n
    fees=turnover=0.0
    fills=cycles=profitable_cycles=0
    buy_cost = [0.0] * n
    for k in range(n):
        if initial_seeds[k]:
            inventory[k]=cash[k]/(entry*(1+fee))
            notional=inventory[k]*entry
            fees+=notional*fee;turnover+=notional;fills+=1
            if record_fill:
                record_fill(future[0]["ts"], notional)
            cash[k]=0.0;seeded[k]=True
    seed_quantity=sum(inventory)
    seed_cash=sum(cash)
    equity_path=[1.0]
    peak=1.0;drawdown=0.0
    for i,bar in enumerate(future):
        for k in range(n):
            if inventory[k] > 0:
                if i==0 and seeded[k]:
                    continue
                if bar['high']>=pairs[k][1]:
                    price=max(bar['open'],pairs[k][1])
                    notional=inventory[k]*price
                    cash[k]=notional*(1-fee);inventory[k]=0
                    fees+=notional*fee;turnover+=notional;fills+=1
                    if record_fill:
                        record_fill(bar["ts"], notional)
                    if not seeded[k]:
                        cycles += 1
                        profitable_cycles += cash[k] > buy_cost[k]
                    seeded[k]=False
            elif bar['low']<=pairs[k][0]:
                price=min(bar['open'],pairs[k][0])
                buy_cost[k] = cash[k]
                inventory[k]=cash[k]/(price*(1+fee))
                notional=inventory[k]*price
                cash[k]=0;fees+=notional*fee;turnover+=notional;fills+=1
                if record_fill:
                    record_fill(bar["ts"], notional)
        equity=sum(cash)+sum(inventory)*bar['close']
        equity_path.append(equity)
        peak=max(peak,equity);drawdown=min(drawdown,equity/peak-1)
    exit_price=exit_bar['open']*(1-slip)
    liquidated=sum(inventory)*exit_price
    value=sum(cash)+liquidated*(1-fee)
    if liquidated:
        fills += 1
        if record_fill:
            record_fill(exit_bar["ts"], liquidated)
    fees+=liquidated*fee;turnover+=liquidated
    drawdown=min(drawdown,value/peak-1)
    equity_path.append(value)
    passive=seed_cash+seed_quantity*exit_price*(1-fee)
    return dict(return_pct=100*(value-1), passive_pct=100*(passive-1),
                excess_pct=100*(value-passive), fees_pct=100*fees,
                initial_inventory_allocation_pct=100*(1-seed_cash),
                turnover=turnover, fills=fills, cycles=cycles, profitable_cycles=profitable_cycles, local_drawdown_pct=100*drawdown, equity_path=equity_path)


def portfolio(candidates, allowed, simulations, period, split, timeframe_seconds):
    records=[];available=-1;capital=peak=1.0;drawdown=0.0;passive=1.0
    for candidate in candidates:
        if candidate['id'] not in allowed or candidate['anchor']<available:
            continue
        # Purge trades spanning the training/evaluation boundary.
        if period=='training' and candidate['exit_ts']>=split: continue
        if period=='evaluation' and candidate['anchor']<split: continue
        if period=='training' and candidate['anchor']>=split: continue
        trade=simulations[candidate['id']]
        available=candidate['exit_ts']
        for relative_value in trade['equity_path']:
            equity=capital*relative_value
            peak=max(peak,equity)
            drawdown=min(drawdown,equity/peak-1)
        capital*=1+trade['return_pct']/100
        passive*=1+trade['passive_pct']/100
        peak=max(peak,capital);drawdown=min(drawdown,capital/peak-1)
        records.append({**candidate,**{k:v for k,v in trade.items() if k!='equity_path'},'capital':capital})
    result=dict(trades=len(records), return_pct=100*(capital-1), passive_return_pct=100*(passive-1),
                mean_return_pct=mean(r['return_pct'] for r in records) if records else None,
                median_return_pct=median(r['return_pct'] for r in records) if records else None,
                mean_excess_pct=mean(r['excess_pct'] for r in records) if records else None,
                win_pct=100*mean(r['return_pct']>0 for r in records) if records else None,
                close_marked_drawdown_pct=100*drawdown,
                cycles=sum(r['cycles'] for r in records),fills=sum(r['fills'] for r in records),
                turnover_sum=sum(r['turnover'] for r in records),
                active_hours=sum((r['exit_ts']-r['entry_ts'])/3600 for r in records))
    return result,records


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    parser.add_argument('--output',type=Path,default=Path('var/value-area-grid'))
    parser.add_argument('--history-dir',type=Path,default=Path('var/history'))
    args=parser.parse_args()
    report=json.loads(args.input.read_text())
    if report['config']['stride']!=0: parser.error('Use nonoverlapping source instances (stride=0).')
    catalog=combine_datasets(load_history_datasets(args.history_dir))
    data=catalog[report['dataset']['id']]
    if data.describe()['sha256'] != report['dataset']['sha256']: parser.error('Dataset fingerprint changed.')
    data=data.open() if hasattr(data,'open') else data
    bars=[b for b in data.rows if b['ts']<=report['evaluation_end']]
    times=[b['ts'] for b in bars];interval=INTERVAL[data.timeframe]
    split=report['evaluation_start']+.7*(report['evaluation_end']-report['evaluation_start'])
    feature_maps={};signal_ids={}
    for name,lookback,ratio,floor,cap in SIGNALS:
        episodes=copy.deepcopy(report['episodes'])
        if cap:
            for e in episodes: e['duration_candles']=min(cap,e['duration_candles'])
        rows=features(episodes,lookback)
        feature_maps[name]={r['id']:r for r in rows}
        signal_ids[name]={r['id'] for r in rows if name=='all' or
                          (r['recent_mean']>r['earlier_mean']*ratio and r['recent_mean']>=floor)}
    common=set(feature_maps['all'])
    signal_ids={name:ids & common for name,ids in signal_ids.items()}
    args.output.mkdir(parents=True,exist_ok=True)
    results=[];duration_results=[];curves={}
    # Main grid sensitivity uses a 24-hour horizon. 6h and 48h isolate holding-time sensitivity.
    scenarios=[(288,n,fee) for n in (4,8,12) for fee in (0,2,20)]+[(72,8,2),(576,8,2)]
    for horizon in (72,288,576):
        candidates=[]
        future_map={}
        config=settings({**report['config'],'horizon':horizon})
        for e in report['episodes']:
            if e['id'] not in common: continue
            anchor=bisect_left(times,e['anchor'])
            observed=observe(bars,anchor,e['value_low'],e['value_high'],config,interval)
            last=observed['last_index']
            # Need a following candle for causal liquidation. Gaps invalidate execution.
            if observed['outcome'] in ('gap','data_end') or last+1>=len(bars) or bars[last+1]['ts']-bars[last]['ts']!=interval:
                continue
            c=dict(id=e['id'],anchor=e['anchor'],entry_ts=bars[anchor+1]['ts'],exit_ts=bars[last+1]['ts'],
                   low=e['value_low'],high=e['value_high'],duration=observed['duration_candles'],
                   censored=observed['censored'],outcome=observed['outcome'],
                   exit_direction=('down' if bars[last]['close']<e['value_low'] else 'up')
                   if observed['outcome']=='exit' else 'timeout')
            candidates.append(c)
            future_map[e['id']]=(bars[anchor]['close'],bars[anchor+1:last+1],bars[last+1])
        if horizon==576:
            for name,*_ in SIGNALS:
                chosen=[c for c in candidates if c['anchor']>=split and c['id'] in signal_ids[name]]
                duration_results.append(dict(signal=name,instances=len(chosen),
                    survival={str(threshold):rate(chosen,threshold) for threshold in (24,72,288,576)}))
        for holding,n,fee in scenarios:
            if holding!=horizon: continue
            sims={}
            for c in candidates:
                levels=[c['low']+(c['high']-c['low'])*k/n for k in range(n+1)]
                start,future,exit_bar=future_map[c['id']]
                sims[c['id']]=simulate_grid(levels,start,future,exit_bar,fee_bps=fee)
            for name,*_ in SIGNALS:
                for period in ('training','evaluation'):
                    summary,trades=portfolio(candidates,signal_ids[name],sims,period,split,interval)
                    result=dict(signal=name,period=period,horizon=horizon,intervals=n,fee_bps=fee,**summary)
                    results.append(result)
                    if horizon==288 and n==8 and fee==2 and period=='evaluation': curves[name]=trades
            print(f'Finished horizon={horizon}, intervals={n}, fee={fee} bps',flush=True)
    eligible=[r for r in results if r['period']=='training' and r['fee_bps']==2 and r['horizon']==288 and r['intervals']==8 and r['signal']!='all' and r['trades']>=30]
    selected=max(eligible,key=lambda r:r['mean_excess_pct'])['signal']
    output=dict(source=str(args.input.resolve()),dataset=report['dataset'],profile_config=report['config'],
                split=datetime.fromtimestamp(split,timezone.utc).isoformat(),signals=SIGNALS,
                selected_on_training=selected,results=results,duration_results=duration_results,
                assumptions=['1 unit quote capital, fully allocated equally across grid cells; cash retained until buys fill.',
                             'Next-open entry and liquidation; market slippage 1 bp; specified fee per fill.',
                             'Limit fills at touched level or favorable open; sufficient liquidity, no exchange rounding.',
                             'At most one limit fill per cell per candle; new opposite order starts next candle.',
                             'Exit after 3 consecutive closes outside frozen bounds, or holding limit.',
                             'Single active grid; opportunities occurring during an active position skipped.',
                             'Passive comparator holds the same seeded quantity and cash until the same exit, with costs.',
                             'August–October has already been examined; this is retrospective exploration.',
                             'Censored past durations are lower bounds; source duration sensor capped at its 24h horizon.'])
    (args.output/'results.json').write_text(json.dumps(output,indent=2,allow_nan=False))
    with (args.output/'scenarios.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(results[0]));writer.writeheader();writer.writerows(results)
    (args.output/'evaluation-trades.json').write_text(json.dumps(curves,indent=2))
    plot(output,curves,args.output)
    print('Training-selected signal:',selected)


def plot(output,curves,path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,2,figsize=(15,9),layout='constrained')
    labels=[s[0] for s in SIGNALS]
    records=[r for r in output['results'] if r['period']=='evaluation' and r['horizon']==288 and r['intervals']==8 and r['fee_bps']==2]
    lookup={r['signal']:r for r in records}
    axes[0,0].bar(labels,[lookup[name]['return_pct'] for name in labels],color=['#8795a4' if name=='all' else '#16745c' for name in labels])
    axes[0,0].set(title='Evaluation compounded return · 8 intervals · 2 bps per fill',ylabel='Return %');axes[0,0].tick_params(axis='x',rotation=30)
    for name in dict.fromkeys(['all','rising_5',output['selected_on_training']]):
        trades=curves[name]
        axes[0,1].step([datetime.fromtimestamp(t['exit_ts'],timezone.utc) for t in trades],[100*(t['capital']-1) for t in trades],where='post',label=name)
    axes[0,1].set(title='Account value at each completed grid · idle cash earns zero',ylabel='Return %');axes[0,1].legend()
    for name in ['all','rising_5','rise25_min24_5']:
        fees=[0,2,20]
        values=[next(r for r in output['results'] if r['period']=='evaluation' and r['signal']==name and r['horizon']==288 and r['intervals']==8 and r['fee_bps']==f)['mean_return_pct'] for f in fees]
        axes[1,0].plot(fees,values,'o-',label=name)
    axes[1,0].set(title='Fee sensitivity · 8 grid intervals',xlabel='Fee per fill · basis points',ylabel='Mean return per grid %');axes[1,0].legend()
    survival={r['signal']:r for r in output['duration_results']}
    for name in ['all','rising_5','rise25_min24_5']:
        axes[1,1].plot([2,6,24,48],[100*(survival[name]['survival'][str(k)]['probability'] or 0) for k in (24,72,288,576)],'o-',label=name)
    axes[1,1].set(title='Future residence in the frozen value area',xlabel='Minimum duration · hours',ylabel='Instances meeting duration %');axes[1,1].legend()
    for ax in axes.flat:
        ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',alpha=.2)
    for ax in (axes[0,0],axes[0,1],axes[1,0]): ax.axhline(0,color='gray',lw=.7)
    fig.suptitle('BTC/USDT · Duration signals and spot-grid simulations',fontsize=16)
    fig.savefig(path/'comparison.png',dpi=170);plt.close(fig)


if __name__=='__main__':
    main()
