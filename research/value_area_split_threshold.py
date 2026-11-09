"""Compare full grids with width-gated POC splits using the paired simulations."""
import argparse
import csv
import json
from pathlib import Path
from statistics import mean


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--thresholds', nargs='+', type=float, default=[1, 2, 3, 4])
    args = parser.parse_args()
    report = json.loads(Path('var/range-explorer.json').read_text())
    episodes = {e['id']: e for e in report['episodes'] if e['duration_candles'] >= 150}
    with Path('var/value-area-poc-halves.csv').open() as source:
        rows = list(csv.DictReader(source))
    if {int(row['id']) for row in rows} != episodes.keys():
        raise ValueError('Regenerate the paired half simulations for this report.')
    for row in rows:
        episode = episodes[int(row['id'])]
        if int(row['anchor']) != episode['anchor']:
            raise ValueError('Episode timestamps do not match.')
        row.update(width=episode['width_pct'], full=episode['grid_result']['return_pct'],
                   lower=float(row['lower_pct']), upper=float(row['upper_pct']))
    results = []
    for cutoff in args.thresholds:
        for regime in ('all', 'bullish', 'bearish', 'unclassified'):
            group = [row for row in rows if regime == 'all' or row['regime'] == regime]
            eligible = [row for row in group if row['width'] > cutoff]
            if not group:
                continue
            result = dict(threshold_pct=cutoff, regime=regime, instances=len(group),
                          split=len(eligible), full=mean(row['full'] for row in group))
            for side in ('lower', 'upper'):
                result[side] = mean(row[side] if row['width'] > cutoff else row['full']
                                    for row in group)
            result['both_equal_capital'] = (result['lower'] + result['upper']) / 2
            for side in ('full','lower','upper'):
                result['wide_' + side] = mean(row[side] for row in eligible) if eligible else None
            results.append(result)
    with Path('var/value-area-split-thresholds.csv').open('w', newline='') as dest:
        writer = csv.DictWriter(dest, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)


if __name__ == '__main__':
    main()
