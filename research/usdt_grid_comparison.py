"""Select an early-history finalist with --select, then score the USDT checks."""
import argparse
import json
from statistics import mean
from research.score_walk_forward import CONTROL, ROOT, interval, read


def panel(mode):
    files={p:read('Binance-'+p,mode) for p in CONTROL}
    months=sorted(set.intersection(*(set(f['months']) for f in files.values())))
    choices=next(iter(files.values()))['choices']
    assert all(f['choices']==choices for f in files.values())
    return choices,{m:{p:[[r for r in rs if r[0] in dates] for rs in f['months'][m]] for p,f in files.items()}
                    for m in months if (dates:=set.intersection(*({r[0] for r in f['months'][m][0]} for f in files.values())))}


def stats(data,idx,months):
    monthly=[[r for pair in data[m].values() for r in pair[idx]] for m in months]
    rets=[mean(r[1] for r in rows) for rows in monthly]
    return dict(ret=mean(rets),vol=mean(mean(r[3] for r in rows) for rows in monthly),
                active=mean(mean(r[5]>0 for r in rows) for rows in monthly),n=sum(map(len,monthly)),
                hold=mean(mean(r[4] for r in rows) for rows in monthly),
                dd=max(r[2] for rows in monthly for r in rows),ci=interval(rets) if len(rets)>=6 else [])


def select():
    choices,data=panel('challenge')
    dates=[m for m in data if '2022-07'<=m<'2025']
    ranks=[dict(config=c,**stats(data,i,dates)) for i,c in enumerate(choices)]
    chosen=max((r for r in ranks if r['vol']>=1 and r['active']>=.25),key=lambda r:r['ret'])
    (ROOT/'usdt-selection.json').write_text(json.dumps(dict(**chosen,candidates=ranks),indent=2))
    print(chosen)


def report():
    choices,data=panel('usdt-check')
    _,stress=panel('usdt-stress')
    assert data.keys()==stress.keys()
    names=['12% outside-only, always replenish','12% two-sided reference','4% two-sided, 2% stop','USDT entry-rule finalist']
    out=['# Binance USDT grid comparison','','72-hour sessions on common Monday and Thursday starts. Equal weight per market and calendar month. Returns are in USDT after both sides of the trade, including final liquidation. Base costs: 0.02% per fill and 0.01% market slippage. Higher costs: 0.04% and 0.03%, respectively.','',
         'The eight markets are ADA, BNB, BTC, DOGE, ETH, LINK, SOL and XRP against USDT. The follow-up screened 24 width, movement and entry combinations on July 2022–December 2024. Skipped sessions earned zero. Eligibility required 1× average turnover and activity on at least 25% of starts. The subsequent periods have appeared in earlier research.','',
         '| Period | Configuration | Sessions | Mean return | Higher costs | Turnover | Worst session drawdown | Buy & hold |',
         '|---|---|---:|---:|---:|---:|---:|---:|']
    for label,start,end in [('2025','2025','2026'),('Jan–Oct 2026','2026-01','2026-11'),('Nov 2 & 5, 2026','2026-11','2026-12')]:
        months=[m for m in data if start<=m<end]
        for i,name in enumerate(names):
            v=stats(data,i,months);risk=stats(stress,i,months)
            out.append(f"| {label} | {name} | {v['n']} | {v['ret']:+.3%} | {risk['ret']:+.3%} | {v['vol']:.2f}× | {v['dd']:.2%} | {v['hold']:+.3%} |")
    out+=['','## Returns by pair','','Each cell shows mean 72-hour return in 2025 / January–October 2026.','','| Pair | '+' | '.join(names)+' |','|---|---:|---:|---:|---:|']
    for p in CONTROL:
        entry=[]
        for i in range(len(choices)):
            forperiod=lambda months:mean(mean(r[1] for r in data[m][p][i]) for m in months)
            entry.append(' / '.join(f'{forperiod([m for m in data if a<=m<b]):+.3%}' for a,b in [('2025','2026'),('2026-01','2026-11')]))
        out.append('| '+p.replace('_','/')+' | '+' | '.join(entry)+' |')
    out+=['','All four use a 2% protective stop and a 0.5% trail at the paired sell line in a rising daily EMA regime. The two-sided reference and 4% challenger use 8-line near-edge moves, a one-line outside cap and 720-minute / +0.02% replenishment. The USDT finalist is specified below.','',json.dumps(choices[-1]),'']
    (ROOT/'usdt-report.md').write_text('\n'.join(out))
    print('\n'.join(out))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--select',action='store_true')
    select() if parser.parse_args().select else report()
