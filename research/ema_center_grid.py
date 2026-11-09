"""Run with python -m research.ema_center_grid from the repository root."""
import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

from gridlab.datasets import combine_datasets, load_history_datasets, utc_iso
from research.quiet_edge_grid import FEE, SLIP


def daily_rising_counts(bars, market):
    """Count strict daily EMA increases in the 20 completed days before each UTC day."""
    path = Path('var/ema-daily-warmup') / (market.replace('/', '') + '.json')
    # Binance public spot /api/v3/klines, interval=1d, limit=1000,
    # endTime=1767225599999 (last millisecond of 2025 UTC).
    prior = json.loads(path.read_text())
    closes = [(int(row[0])//1000, float(row[4])) for row in prior]
    for i in range(0, len(bars), 288):
        day = bars[i:i+288]
        assert len(day)==288 and day[0]['ts']%86400==0 and day[-1]['ts']==day[0]['ts']+86100
        closes.append((day[0]['ts'], day[-1]['close']))
    assert len(prior)>=220 and all(b[0]-a[0]==86400 for a,b in zip(closes,closes[1:]))
    ema = mean(price for _,price in closes[:200])
    rises = []
    counts = {}
    for ts,price in closes[200:]:
        updated = ema + 2/201*(price-ema)
        rises.append(updated > ema)
        ema = updated
        if len(rises)>=20:
            counts[ts+86400] = sum(rises[-20:])
    return counts


def simulate(bars, start, end, width_pct, usd_proxy, daily_counts=None, stop_pct=0):
    ema = mean(bar['close'] for bar in bars[:200])
    for bar in bars[200:start]:
        ema += 2/201 * (bar['close'] - ema)
    capital = 10_000 / usd_proxy[bars[start]['ts']]
    cash = [capital/4]*4
    units = [0.0]*4
    target = [0.0]*4
    stop_prices = [0.0]*4
    cost = [0.0]*4
    monthly = defaultdict(float)
    peak = capital
    drawdown = realized = 0.0
    buys = cycles = stops = all_funded = 0
    for b in bars[start:end]:
        month = utc_iso(b['ts'])[:7]
        allow_buys = daily_counts is None or daily_counts[b['ts']//86400*86400] >= 15
        turnover = 0.0
        for k in range(4):
            distance = width_pct/200 * (1 - .4*k/3)
            bid = ema*(1-distance)
            opened = False
            if not units[k]:
                if not allow_buys or b['low'] > bid:
                    continue
                entry = min(bid, b['open'])
                cost[k] = cash[k]
                notional = cash[k]/(1+FEE)
                units[k] = notional/entry
                cash[k] = 0.0
                target[k] = ema*(1+distance)
                stop_prices[k] = entry*(1-stop_pct/100)
                turnover += notional
                buys += 1
                opened = True
            # A resting target executable at the open precedes intrabar moves.
            # Otherwise give the stop priority when both levels trade in one bar.
            if not opened and b['open'] >= target[k]:
                exit_price = b['open']
                cycles += 1
            elif stop_pct and b['low'] <= stop_prices[k]:
                exit_price = min(stop_prices[k], b['open'])*(1-SLIP)
                stops += 1
            elif not opened and b['high'] >= target[k]:
                exit_price = target[k]
                cycles += 1
            else:
                continue
            proceeds = units[k]*exit_price
            cash[k] = proceeds*(1-FEE)
            realized += cash[k]-cost[k]
            units[k] = 0.0
            turnover += proceeds
        monthly[month] += turnover*usd_proxy[b['ts']]
        equity = sum(cash)+sum(units)*b['close']
        peak = max(peak,equity)
        drawdown = min(drawdown,equity/peak-1)
        all_funded += all(units)
        ema += 2/201*(b['close']-ema)
    unrealized = sum(q*bars[end-1]['close']-c for q,c in zip(units,cost) if q)
    assert abs(equity-capital-realized-unrealized)<1e-8*capital
    assert buys-cycles-stops==sum(bool(q) for q in units)
    return dict(net_pct=100*(equity/capital-1), closed_profit_pct=100*realized/capital,
                open_pnl_pct=100*unrealized/capital, max_drawdown_pct=100*drawdown,
                cycles=cycles, stops=stops, open_lines=buys-cycles-stops, all_lines_invested_pct=100*all_funded/(end-start),
                mean_monthly_usdt=mean(monthly.values()), monthly_usdt=monthly)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--daily-filter', action='store_true', help='Require 15 rising daily EMA 200 readings in the last 20 completed days for buys.')
    parser.add_argument('--stop-pct', type=float, default=0, help='Per-fill stop loss in percent (0 disables).')
    args = parser.parse_args()
    if not 0 <= args.stop_pct < 100:
        parser.error('Stop percentage must be between 0 and 100 (exclusive of 100).')
    catalog=combine_datasets(load_history_datasets(Path('var/history')))
    markets={d.market:d for d in catalog.values() if d.market in ('ETH/BTC','BTC/USDT')
             and d.timeframe=='5m' and d.describe()['venue']=='Binance'}
    prices={b['ts']:b['open'] for b in markets['BTC/USDT'].open().rows}
    results={}
    for market,d in markets.items():
        bars=[b for b in d.open().rows if '2026-01-01' <= utc_iso(b['ts'])[:10] <= '2026-10-31']
        assert all(b['ts']-a['ts']==300 for a,b in zip(bars,bars[1:]))
        fx=prices if market=='ETH/BTC' else dict.fromkeys(prices,1.0)
        split=next(i for i,b in enumerate(bars) if utc_iso(b['ts'])[:10]=='2026-08-01')
        counts = daily_rising_counts(bars, market) if args.daily_filter else None
        for period,start in [('Jan-Oct',200),('Aug-Oct',split)]:
            for width in (.25,.5,1):
                r=simulate(bars,start,len(bars),width,fx,counts,args.stop_pct)
                if counts is not None:
                    days = range(bars[start]['ts']//86400, bars[-1]['ts']//86400+1)
                    r['eligible_days'] = sum(counts[day*86400]>=15 for day in days)
                    r['total_days'] = len(days)
                key=f'{market} {period} {width}%'
                results[key]=r
                print(key, {k:round(v,3) for k,v in r.items() if k!='monthly_usdt'})
    suffix = '-daily-filter' if args.daily_filter else ''
    if args.stop_pct:
        suffix += f'-stop{args.stop_pct:g}'
    Path(f'var/ema-center-grid{suffix}.json').write_text(json.dumps(results,indent=2))


if __name__=='__main__':
    main()
