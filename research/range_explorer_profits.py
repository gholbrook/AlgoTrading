"""Annotate exported value-area instances with independent grid simulations.

Run: python -m research.range_explorer_profits INPUT --output var/range-explorer.json
"""
import argparse
import json
from pathlib import Path

from gridlab.datasets import combine_datasets, load_history_datasets, INTERVAL
from research.value_area_grid import simulate_grid


def annotate(report, bars, interval, intervals=8, fee_bps=2, exposure=25):
    index = {bar['ts']: i for i, bar in enumerate(bars)}
    report['grid_simulation'] = dict(intervals=intervals, fee_bps=fee_bps,
        slip_bps=1, initial_inventory_pct=exposure,
        description='Independent grids entered at the next open after the anchor; '
        'liquidated at the next open after confirmed exit or observation limit. '
        'Returns include fees, slippage, and liquidation of remaining inventory. '
        'Hindsight duration selection; not a combined portfolio return.')
    for episode in report['episodes']:
        anchor = index.get(episode['anchor'])
        last = index.get(episode['observed_end'])
        reason = None
        if anchor is None or last is None or last + 1 >= len(bars):
            reason = 'Missing entry or liquidation candle'
        elif episode['outcome'] in ('gap', 'data_end'):
            reason = 'Incomplete observation at a data gap or end'
        elif any(bars[i]['ts'] - bars[i-1]['ts'] != interval for i in range(anchor+1, last+2)):
            reason = 'Missing candles during execution'
        if reason:
            episode['grid_result'] = dict(unavailable=reason)
            continue
        levels = [episode['value_low'] + (episode['value_high']-episode['value_low'])*i/intervals
                  for i in range(intervals+1)]
        try:
            result = simulate_grid(levels, bars[anchor]['close'], bars[anchor+1:last+1],
                bars[last+1], fee_bps=fee_bps, initial_inventory_pct=exposure)
            result.pop('equity_path')
            episode['grid_result'] = dict(**result, entry_ts=bars[anchor+1]['ts'], exit_ts=bars[last+1]['ts'])
        except ValueError as exc:
            episode['grid_result'] = dict(unavailable=str(exc))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path, default=Path('var/range-explorer.json'))
    parser.add_argument('--intervals', type=int, default=8)
    parser.add_argument('--fee-bps', type=float, default=2)
    parser.add_argument('--exposure', type=float, default=25)
    args = parser.parse_args()
    if args.intervals < 1 or args.fee_bps < 0 or not 0 <= args.exposure <= 100:
        parser.error('Use positive intervals, nonnegative fees, and exposure from 0 to 100.')
    report = json.loads(args.input.read_text())
    dataset = combine_datasets(load_history_datasets(Path('var/history')))[report['dataset']['id']]
    if dataset.describe()['sha256'] != report['dataset']['sha256']:
        raise ValueError('Dataset fingerprint mismatch')
    data = dataset.open() if hasattr(dataset, 'open') else dataset
    annotate(report, data.rows, INTERVAL[data.timeframe], args.intervals, args.fee_bps, args.exposure)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, allow_nan=False))
    selected = [e for e in report['episodes'] if e['duration_candles'] >= 150]
    print(f"Annotated {len(selected)} ranges >=150; {sum('unavailable' in e['grid_result'] for e in selected)} unavailable")


if __name__ == '__main__':
    main()
