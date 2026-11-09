"""Find maximal windows bounded by a percentage high-low width on Binance 5m data."""
import argparse
import csv
from collections import deque
from pathlib import Path

from gridlab.datasets import combine_datasets, load_history_datasets, utc_iso


def windows(bars, percent):
    highs, lows = deque(), deque()
    left = 0

    def record(right):
        high, low = bars[highs[0]]['high'], bars[lows[0]]['low']
        return dict(start_utc=utc_iso(bars[left]['ts']), end_utc=utc_iso(bars[right]['ts']+300),
                    candles=right-left+1, low=low, high=high, width_pct=100*(high/low-1))

    for i, bar in enumerate(bars):
        if i and bar['ts'] != bars[i-1]['ts']+300:
            if highs:
                yield record(i-1)
            highs.clear()
            lows.clear()
            left = i
        if highs:
            high = max(bar['high'], bars[highs[0]]['high'])
            low = min(bar['low'], bars[lows[0]]['low'])
            if 100*(high/low-1) > percent:
                yield record(i-1)
        while highs and bars[highs[-1]]['high'] <= bar['high']:
            highs.pop()
        while lows and bars[lows[-1]]['low'] >= bar['low']:
            lows.pop()
        highs.append(i)
        lows.append(i)
        while highs and 100*(bars[highs[0]]['high']/bars[lows[0]]['low']-1) > percent:
            if highs[0] == left:
                highs.popleft()
            if lows[0] == left:
                lows.popleft()
            left += 1
    if highs:
        yield record(len(bars)-1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--width', nargs='+', type=float, default=[0.25,0.5,1.0])
    args = parser.parse_args()
    if not all(0 < w < float('inf') for w in args.width):
        parser.error('Widths must be positive and finite.')
    catalog = combine_datasets(load_history_datasets(Path('var/history')))
    with Path('var/quiet-price-windows.csv').open('w', newline='') as out:
        fields = ['market','limit_pct','start_utc','end_utc','candles','low','high','width_pct']
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        for d in catalog.values():
            if d.market not in ('BTC/USDT','ETH/BTC') or d.timeframe!='5m' or d.describe()['venue']!='Binance':
                continue
            source = d.open() if hasattr(d,'open') else d
            bars = [b for b in source.rows if '2026-01-01' <= utc_iso(b['ts'])[:10] <= '2026-10-31']
            for width in args.width:
                best = None
                for row in windows(bars,width):
                    writer.writerow(dict(market=d.market,limit_pct=width,**row))
                    if best is None or row['candles'] > best['candles']:
                        best = row
                print(d.market,width,best)


if __name__ == '__main__':
    main()
