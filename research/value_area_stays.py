"""Measure future residence in a frozen volume-profile value area.

Run in the UI at /value-area or use:
python -m research.value_area_stays --list
python -m research.value_area_stays --dataset ID --candles 288 --value-area 70 --grace 2

Profiles use only formation candles. Observation starts on the next candle.
By default, future observation windows do not overlap. Formation windows may
share past data. Outcomes at the horizon, dataset end, or a gap are censored.
"""
import argparse
import csv
import json
import math
from pathlib import Path
from statistics import mean, median

from gridlab.datasets import INTERVAL, combine_datasets, load_history_datasets, utc_iso
from indicators.volume_profile import volume_profile

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = dict(candles=288, bins=48, value_area=70, grace=2, horizon=288, inside="close", stride=0, allocation="adjacent", width_threshold=3.0)


def settings(values=None):
    if values is not None and not isinstance(values, dict):
        raise ValueError("config must be an object")
    config = {**DEFAULTS, **(values or {})}
    for name, lower, upper in (("candles", 2, 100000), ("bins", 2, 100),
                               ("grace", 0, 10000), ("horizon", 1, 100000), ("stride", 0, 100000)):
        if type(config[name]) is not int or not lower <= config[name] <= upper:
            raise ValueError(f"{name} must be an integer from {lower} to {upper}")
    if type(config['value_area']) not in (int, float) or not 0 < config['value_area'] <= 100:
        raise ValueError("value_area must be a percentage greater than 0 and at most 100")
    if config['allocation'] not in ('adjacent', 'balanced', 'adaptive'):
        raise ValueError('Unknown volume allocation')
    if config['allocation'] == 'adaptive':
        if config['value_area'] != 70:
            raise ValueError('Adaptive mode uses a 70% starting area')
        threshold = config['width_threshold']
        if type(threshold) not in (int, float) or not math.isfinite(threshold) or threshold <= 0:
            raise ValueError('Width threshold must be a positive percentage')
    if config['inside'] not in ('close', 'wick'):
        raise ValueError("inside must be close or wick")
    if config['grace'] >= config['horizon']:
        raise ValueError("grace must be smaller than the observation horizon")
    return config


def inside(bar, low, high, mode):
    if mode == 'wick':
        return low <= bar['low'] and bar['high'] <= high
    return low <= bar['close'] <= high


def observe(bars, anchor, low, high, config, interval):
    """An excursion is ignored only after price returns within its grace allowance."""
    observed = held = within = ignored = excursions = outside = 0
    first_exit = None
    outcome = 'horizon'
    last = anchor
    for index in range(anchor + 1, min(len(bars), anchor + 1 + config['horizon'])):
        if bars[index]['ts'] - bars[index - 1]['ts'] != interval:
            outcome = 'gap'
            break
        last = index
        observed += 1
        if inside(bars[index], low, high, config['inside']):
            within += 1
            if outside:
                excursions += 1
                ignored += outside
            outside = 0
            held = observed
        else:
            if first_exit is None:
                first_exit = observed - 1
            outside += 1
            if outside > config['grace']:
                outcome = 'exit'
                break
    else:
        if observed < config['horizon']:
            outcome = 'data_end'
    return dict(outcome=outcome, censored=outcome != 'exit', observed_candles=observed,
                duration_candles=held, inside_candles=within, ignored_candles=ignored,
                ignored_excursions=excursions, terminal_outside_candles=outside,
                strict_duration_candles=observed if first_exit is None else first_exit,
                strict_censored=first_exit is None, observed_end=bars[last]['ts'],
                exit_start=bars[last - outside + 1]['ts'] if outcome == 'exit' else None,
                confirmed_at=bars[last]['ts'] if outcome == 'exit' else None,
                last_index=last)


def summarize(episodes, interval):
    completed = [e for e in episodes if not e['censored']]
    durations = [e['duration_candles'] for e in completed]
    strict = [e['strict_duration_candles'] for e in episodes if not e['strict_censored']]
    average = mean(durations) if durations else None
    return dict(instances=len(episodes), completed_exits=len(completed), censored=len(episodes)-len(completed),
                mean_duration_candles=average, median_duration_candles=median(durations) if durations else None,
                mean_duration_hours=average * interval / 3600 if average is not None else None,
                mean_inside_candles=mean(e['inside_candles'] for e in completed) if completed else None,
                mean_strict_duration_candles=mean(strict) if strict else None,
                mean_observed_lower_bound_candles=mean(e['duration_candles'] for e in episodes) if episodes else None,
                mean_ignored_candles=mean(e['ignored_candles'] for e in completed) if completed else None,
                outcomes={name: sum(e['outcome'] == name for e in episodes)
                          for name in ('exit', 'horizon', 'data_end', 'gap')})


def balanced_bounds(profile, fraction):
    """Find volume quantiles either side of the POC, interpolating within bins."""
    volumes = profile['volumes']
    peak = max(range(len(volumes)), key=volumes.__getitem__)
    width = (profile['high'] - profile['low']) / len(volumes)
    target = profile['total_volume'] * fraction / 2

    def boundary(direction):
        remaining = target
        for index in range(peak, len(volumes) if direction == 1 else -1, direction):
            edge = index if direction == 1 else index + 1
            start = peak + 0.5 if index == peak else edge
            mass = volumes[index] * (0.5 if index == peak else 1)
            if mass > 0 and remaining <= mass:
                return profile['low'] + width * (start + direction * remaining / volumes[index])
            remaining -= mass
        return None

    return boundary(-1), boundary(1)


def run_experiment(dataset, config=None, start=None, end=None):
    config = settings(config)
    warmup, selected = dataset.select(start, end, warmup_count=config['candles'] - 1)
    bars = warmup + selected
    interval = INTERVAL[dataset.timeframe]
    # Prefix counts let us reject formation windows spanning a data gap in O(1).
    gaps = [0]
    for previous, current in zip(bars, bars[1:]):
        gaps.append(gaps[-1] + int(current['ts'] - previous['ts'] != interval))
    episodes = []
    skipped = dict(outside=0, zero_volume=0, formation_gap=0, insufficient_side_volume=0)
    index = max(config['candles'] - 1, len(warmup))
    while index < len(bars) - 1:
        first = index - config['candles'] + 1
        if gaps[index] != gaps[first]:
            skipped['formation_gap'] += 1
            index += max(1, config['stride'])
            continue
        formation = bars[first:index + 1]
        profile = volume_profile({'bar': bars[index], 'history': formation},
                                 candles=config['candles'], bins=config['bins'], value_area=config['value_area']/100)
        low, high = profile['value_low'], profile['value_high']
        if low is None:
            skipped['zero_volume'] += 1
            index += max(1, config['stride'])
            continue
        original_width = 200 * (high - low) / (high + low)
        narrowed = config['allocation'] == 'adaptive' and original_width > config['width_threshold']
        area = 50 if narrowed else config['value_area']
        if narrowed or config['allocation'] == 'balanced':
            low, high = balanced_bounds(profile, area / 100)
            if low is None or high is None:
                skipped['insufficient_side_volume'] += 1
                index += max(1, config['stride'])
                continue
        if not inside(bars[index], low, high, config['inside']):
            skipped['outside'] += 1
            index += max(1, config['stride'])
            continue
        episode = observe(bars, index, low, high, config, interval)
        last = episode.pop('last_index')
        episode.update(id=len(episodes)+1, formation_start=bars[first]['ts'], anchor=bars[index]['ts'],
                       value_low=low, value_high=high, poc=profile['poc'],
                       width_pct=200*(high-low)/(high+low))
        if config['allocation'] == 'adaptive':
            episode.update(original_width_pct=original_width, applied_area_pct=area)
        episodes.append(episode)
        index = index + config['stride'] if config['stride'] else max(index + 1, last)
    months = sorted({utc_iso(e['anchor'])[:7] for e in episodes})
    return dict(dataset=dataset.describe(), config=config, start=start, end=end,
                evaluation_start=selected[0]['ts'], evaluation_end=selected[-1]['ts'],
                summary=summarize(episodes, interval), skipped=skipped,
                by_month={month: summarize([e for e in episodes if utc_iso(e['anchor']).startswith(month)], interval)
                          for month in months}, episodes=episodes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--dataset')
    parser.add_argument('--history-dir', type=Path, default=ROOT/'var/history')
    parser.add_argument('--start')
    parser.add_argument('--end')
    parser.add_argument('--output', type=Path, default=ROOT/'var/value-area-stays')
    for name in ('candles', 'bins', 'grace', 'horizon', 'stride'):
        parser.add_argument('--'+name, type=int, default=DEFAULTS[name])
    parser.add_argument('--value-area', type=float, default=70)
    parser.add_argument('--allocation', choices=['adjacent', 'balanced', 'adaptive'], default='adjacent')
    parser.add_argument('--width-threshold', type=float, default=3.0)
    parser.add_argument('--inside', choices=['close', 'wick'], default='close')
    args = parser.parse_args()
    catalog = combine_datasets(load_history_datasets(args.history_dir))
    if args.list:
        for key, dataset in catalog.items():
            print(key, dataset.market, dataset.timeframe, dataset.describe()['start'], dataset.describe()['end'])
        return
    if args.dataset not in catalog:
        parser.error('Choose a dataset ID from --list.')
    try:
        result = run_experiment(catalog[args.dataset], {name: getattr(args, name) for name in DEFAULTS}, args.start, args.end)
    except ValueError as exc:
        parser.error(str(exc))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'results.json').write_text(json.dumps(result, indent=2, allow_nan=False))
    with (args.output/'episodes.csv').open('w', newline='') as file:
        if result['episodes']:
            writer = csv.DictWriter(file, fieldnames=list(result['episodes'][0]))
            writer.writeheader()
            writer.writerows(result['episodes'])
    print(json.dumps(result['summary'], indent=2))
    print('Saved results to', args.output)


if __name__ == '__main__':
    main()
