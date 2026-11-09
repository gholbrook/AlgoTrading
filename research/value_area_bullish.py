"""Test causal Bitcoin daily-EMA regime filters on duration-selected spot grids.

python -m research.value_area_bullish
Daily EMAs use complete 288-candle UTC days, seeded with the first 200 daily
closes. Entry gates use yesterday's finalized EMA, its prior reading, and the
signal candle close. Regime is an entry gate; original breakout/time exits apply.
"""
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from gridlab.datasets import combine_datasets, load_history_datasets
from research.value_area_predictability import features
from research.value_area_stays import observe, settings
from research.value_area_grid import simulate_grid, portfolio

ROOT=Path(__file__).resolve().parents[1]


def daily_ema(rows, period=200):
    days=defaultdict(list)
    for bar in rows:
        days[bar['ts']//86400*86400].append(bar)
    closes=[];result={};last=None
    for day,bars in sorted(days.items()):
        if len(bars)!=288 or bars[0]['ts']!=day or bars[-1]['ts']!=day+86100:
            closes=[];last=None;continue
        if last is not None and day-last!=86400: closes=[]
        last=day
        closes.append(bars[-1]['close'])
        if len(closes)==period:
            value=mean(closes)
        elif len(closes)>period:
            value+=(2/(period+1))*(closes[-1]-value)
        else: continue
        result[day]=value
    return result


def regimes(bar, ema):
    yesterday=bar['ts']//86400*86400-86400
    latest=ema.get(yesterday)
    prior=ema.get(yesterday-86400)
    older=ema.get(yesterday-5*86400)
    if latest is None or prior is None or older is None: return None
    rising=latest>prior
    above=bar['close']>latest
    return dict(all=True,ema_rising=rising,price_above=above,
                rising_and_above=rising and above,ema_rising_5d=latest>older,
                ema=latest,ema_slope_pct=100*(latest/prior-1))


def main():
    source=json.loads((ROOT/'var/value-area-grid/results.json').read_text())
    report=json.loads(Path(source['source']).read_text())
    dataset=combine_datasets(load_history_datasets(ROOT/'var/history'))[source['dataset']['id']]
    if dataset.describe()['sha256']!=source['dataset']['sha256']: raise ValueError('Dataset fingerprint changed')
    data=dataset.open() if hasattr(dataset,'open') else dataset
    bars=data.rows;index={b['ts']:i for i,b in enumerate(bars)}
    ema=daily_ema(bars)
    split=datetime.fromisoformat(source['split']).timestamp()
    common={r['id'] for r in features(report['episodes'],10)}
    rising_ids={r['id'] for r in features(report['episodes'],5) if r['recent_mean']>r['earlier_mean']} & common
    config=settings(report['config'])
    candidates=[];future_map={};masks={};readings=[]
    for e in report['episodes']:
        if e['id'] not in common or e['anchor']<split:continue
        anchor=index[e['anchor']]
        flags=regimes(bars[anchor],ema)
        if flags is None:continue
        observed=observe(bars,anchor,e['value_low'],e['value_high'],config,300)
        last=observed['last_index']
        if observed['outcome'] in ('gap','data_end') or last+1>=len(bars) or bars[last+1]['ts']-bars[last]['ts']!=300:continue
        c=dict(id=e['id'],anchor=e['anchor'],entry_ts=bars[anchor+1]['ts'],exit_ts=bars[last+1]['ts'],
               low=e['value_low'],high=e['value_high'],duration=observed['duration_candles'],
               censored=observed['censored'],outcome=observed['outcome'])
        candidates.append(c);masks[e['id']]=flags
        future_map[e['id']]=(bars[anchor]['close'],bars[anchor+1:last+1],bars[last+1])
        readings.append(dict(id=e['id'],signal_utc=datetime.fromtimestamp(e['anchor'],timezone.utc).isoformat(),
                             duration_signal=e['id'] in rising_ids,**flags))
    gates=('all','ema_rising','price_above','rising_and_above','ema_rising_5d')
    results=[];details=[]
    for exposure in (0,25,50):
        for intervals in (4,8,12):
            simulations={}
            for c in candidates:
                levels=[c['low']+(c['high']-c['low'])*i/intervals for i in range(intervals+1)]
                anchor,future,exit_bar=future_map[c['id']]
                simulations[c['id']]=simulate_grid(levels,anchor,future,exit_bar,initial_inventory_pct=exposure)
            for duration_filter in (False,True):
                for gate in gates:
                    eligible={c['id'] for c in candidates if masks[c['id']][gate]
                              and (not duration_filter or c['id'] in rising_ids)}
                    summary,records=portfolio(candidates,eligible,simulations,'evaluation',split,300)
                    results.append(dict(exposure=exposure,intervals=intervals,duration_filter=duration_filter,regime=gate,**summary))
                    if exposure==25 and intervals==8 and duration_filter:
                        details.extend(dict(regime=gate,**record) for record in records)
    output=ROOT/'var/value-area-bullish';output.mkdir(parents=True,exist_ok=True)
    result=dict(dataset=source['dataset'],split=source['split'],profile_config=report['config'],
                ema_first_day=datetime.fromtimestamp(min(ema),timezone.utc).isoformat(),
                assumptions=['BTC/USDT daily EMA proxies the Bitcoin regime; it uses the same Binance feed as the trades.',
                             'EMA is seeded with the first 200 complete daily closes available, not a full pre-2026 history.',
                             'Use only finalized prior daily EMA values, plus price known at the signal candle close.',
                             'Entry gate only; no liquidation when EMA regime flips.',
                             'Fixed BTC allocation; 2 bps fee per fill and 1 bp market slippage.',
                             'Same previously inspected evaluation period; retrospective findings.'],results=results)
    (output/'results.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    (output/'trades.json').write_text(json.dumps(details,indent=2,allow_nan=False))
    for filename,rows in [('scenarios.csv',results),('regime-readings.csv',readings)]:
        with (output/filename).open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    plot(results,output)
    for r in results:
        if r['duration_filter'] and r['intervals']==8:
            print(r['exposure'],r['regime'],r['trades'],round(r['return_pct'],3),round(r['mean_return_pct'] or 0,4))


def plot(results,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    labels=['All regimes','EMA rising','Price above EMA','Rising + above','EMA rising over 5D']
    gates=['all','ema_rising','price_above','rising_and_above','ema_rising_5d']
    fig,axes=plt.subplots(1,2,figsize=(14,5),layout='constrained')
    for exposure,color in [(0,'#16745c'),(25,'#c38b28'),(50,'#617fa2')]:
        rows=[next(r for r in results if r['exposure']==exposure and r['intervals']==8 and r['duration_filter'] and r['regime']==gate) for gate in gates]
        for ax,field in zip(axes,['return_pct','mean_return_pct']):
            ax.plot(range(5),[r[field] for r in rows],'o-',color=color,label=f'{exposure}% initially BTC')
    axes[0].set(title='Compounded account return',ylabel='Return %');axes[0].legend()
    axes[1].set(title='Mean return per deployed grid',ylabel='Return per grid %')
    for ax in axes:
        ax.set_xticks(range(5),labels,rotation=15);ax.axhline(0,color='gray',lw=.8)
        ax.grid(axis='y',alpha=.2);ax.spines[['top','right']].set_visible(False)
    fig.suptitle('BTC/USDT · Rising-duration setups with daily EMA 200 regime filters · 8 intervals')
    fig.savefig(output/'comparison.png',dpi=170);plt.close(fig)


if __name__=='__main__':
    main()
