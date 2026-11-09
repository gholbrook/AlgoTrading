"""Build the human-readable walk-forward report from the locked comparisons."""
import json
from statistics import mean
from research.score_walk_forward import ROOT,PRIMARY,CONTROL,read,interval
scores=json.loads((ROOT/'scores.json').read_text())
picks=json.loads((ROOT/'picks.json').read_text())
locked=json.loads((ROOT/'locked.json').read_text())
transfer=['ADA_BTC','LINK_BTC','LTC_BTC','XRP_BTC']
pct=lambda x:f'{100*x:+.3f}%'


def compare(keys,mode,index,start,end):
    files=[read(k,mode) for k in keys]
    months=sorted(m for m in set.intersection(*(set(f['months']) for f in files)) if start<=m<end)
    means=[];volume=[];dd=0;n=0;pair={k:[] for k in keys}
    for m in months:
        common=set.intersection(*({r[0] for r in f['months'][m][0]} for f in files))
        if not common:continue
        rows=[]
        for k,f in zip(keys,files):
            j=index(k) if callable(index) else index
            rs=[r for r in f['months'][m][j] if r[0] in common]
            rows.extend(rs);pair[k].append(mean(r[1] for r in rs))
        n+=len(rows);means.append(mean(r[1] for r in rows));volume.append(mean(r[3] for r in rows));dd=max(dd,max(r[2] for r in rows))
    return {'ret':mean(means),'vol':mean(volume),'dd':dd,'n':n,'months':len(means),'ci':interval(means) if len(means)>=6 else [],
            'pair':{k:mean(v) for k,v in pair.items()}}


def symbols(pairs):return ['Binance-'+p for p in pairs]


lines=['# Walk-forward grid study','',
'Average net return per scheduled 72-hour session, measured in quote currency. Every comparison uses equal pair weights and equal month weights. A new session starts with fresh capital. A result is not an annualized or compounded portfolio return.','',
'## Decision','',
'The search did not establish a unique profitable configuration for the five primary crypto crosses. The original 12% channel is a more defensible paper-test benchmark than switching to the early winner on return alone. It capped losses better in the 2025 confirmation period.','',
'## Chronological tests','',
'| Group and period | Pair-sessions | Selected monthly from prior six months | Frozen early winner | 12% reference | Buy and hold |','|---|---:|---:|---:|---:|---:|']
for group,r in scores.items():
    for phase,label in [('confirmation','2025'),('previously-studied','Jan–Oct 2026'),('rolling-history','Jan 2023–Oct 2026')]:
        v=r['periods'][phase];s=v['stats'];frozen=pct(s['fixed']['ret']) if phase!='rolling-history' else '—'
        lines.append(f"| {group}, {label} | {v['sessions']:,} | {pct(s['rolling']['ret'])} | {frozen} | {pct(s['reference']['ret'])} | {pct(s['rolling']['hold'])} |")
lines+=['','All stop, trail and shift settings are per grid. Both the crypto early winner and the reference use 8-line near-edge shifts, a one-line outside catch-up cap, replenishment on a 720-minute EMA change of at least +0.02% per five-minute candle, and a 0.5% trail on reaching the paired sell line in a rising daily EMA regime. The early winner uses 4% total width and no protective fill stop. The reference uses 12% width and a 2% stop. The USDT early winner uses 12% width, outside-only movement capped at one line, a 2% stop and always-on replenishment with the same bullish trail.','',
'## 2025 confirmation and cost stress','',
'| Group | Configuration | Mean net return | Three-month block 95% interval | Return at higher costs | Turnover | Worst session drawdown |','|---|---|---:|---:|---:|---:|---:|']
for g,ps in [('crypto',PRIMARY),('usdt',CONTROL)]:
    r=scores[g]
    for label,idkey in [('fixed','id'),('reference','reference_id')]:
        cid=r['best'][idkey];s=r['periods']['confirmation']['stats'][label]
        stress=compare(symbols(ps),'stress',lambda key:picks[key].index(cid),'2025','2026')
        lines.append(f"| {g} | {label} | {pct(s['ret'])} | {pct(s['ci'][0])} to {pct(s['ci'][1])} | {pct(stress['ret'])} | {s['volume']:.2f}× | {100*s['dd']:.2f}% |")
lines+=['','Base cost is 0.02% per fill with 0.01% market slippage. The cost stress reruns execution with 0.04% and 0.03%, respectively. Turnover is both sides’ executed notional divided by starting capital. Drawdown is peak-to-trough candle-close equity within the worst individual session. Intervals use 10,000 circular three-month block resamples of portfolio months. Twelve confirmation months offer limited precision.','',
'## Pair transfer and entry-rule challenge','',
'None of the four transfer pairs contributed to choosing the configurations. The second search covered 24 combinations, counting overlaps with the first round. Its early winner was a 4% grid with the 2% stop and unconditional arming. Quiet-day and containment filters did not win the training screen with the required turnover. Dates and many underlying crypto exposures are shared across pairs.','',
'| Group and test dates | Configuration | Pair-sessions | Mean net | Turnover | Worst drawdown |','|---|---|---:|---:|---:|---:|']
for title,keys,mode,idx,start,end,label in [
    ('Primary 2025',symbols(PRIMARY),'challenge',locked['challenger'],'2025','2026','4%, 2% stop'),
    ('Four new BTC pairs 2025',symbols(transfer),'transfer',0,'2025','2026','4%, no fill stop'),
    ('Four new BTC pairs 2025',symbols(transfer),'transfer',1,'2025','2026','12% reference'),
    ('Four new BTC pairs 2025',symbols(transfer),'challenge-transfer',0,'2025','2026','4%, 2% stop'),
    ('Four new BTC pairs 2025',symbols(transfer),'transfer-stress',0,'2025','2026','4%, no fill stop, higher costs'),
    ('Four new BTC pairs 2025',symbols(transfer),'transfer-stress',1,'2025','2026','12% reference, higher costs'),
    ('Four new BTC pairs 2025',symbols(transfer),'challenge-stress',0,'2025','2026','4%, 2% stop, higher costs'),
    ('Coinbase ETH/USD 2025',['Coinbase-ETH_USD'],'locked-control',lambda k:picks[k].index(scores['usdt']['best']['id']),'2025','2026','USDT early winner'),
    ('Coinbase ETH/USD 2025',['Coinbase-ETH_USD'],'locked-control',lambda k:picks[k].index(scores['usdt']['best']['reference_id']),'2025','2026','12% reference'),
    *[(title,symbols(PRIMARY),'audit',idx,start,end,label) for title,start,end in [('Primary Oct–Dec 2021','2021','2022'),('Primary Nov 2 & 5 starts, 2026','2026-11','2026-12')] for idx,label in enumerate(['4%, no fill stop','12% reference','4%, 2% stop'])]
]:
    v=compare(keys,mode,idx,start,end)
    lines.append(f"| {title} | {label} | {v['n']} | {pct(v['ret'])} | {v['vol']:.2f}× | {100*v['dd']:.2f}% |")
lines+=['','## Interpreting the results','',
'The first candidate was frozen using equal-weight mean returns from July 2022 through December 2024. The rolling selector makes a fresh selection each month using only the preceding six calendar months. The second search was prompted by weak confirmation results, so its repeat on primary 2025 is exploratory. The small November check uses the latest two complete scheduled sessions; neither it nor cross-pair transfer replaces future paper trading.','',
'Data come from Binance spot archives with published SHA-256 checksums and Coinbase Exchange public candles. Abnormal, incomplete, or missing five-minute candles were excluded. Sessions crossing gaps were skipped; the second search also requires a complete preceding week. The sessions use the UI order engine and pair only common starting dates across the markets being compared. Fill-on-touch OHLC simulation does not model order-book queue position, available depth, or exchange size rounding. Actual manually chosen support and resistance starts are a different experiment.','',
'[Research protocol and candidate backlog](../../notes/grid-research-log.md) · [Monthly results and selection record](scores.json) · [Locked challenger](locked.json)']
(ROOT/'report.md').write_text('\n'.join(lines)+'\n')
print('\n'.join(l for l in lines if l.startswith('|')))
