"""Run with python -m research.post_move_consolidation from the repository root."""
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

from gridlab.datasets import combine_datasets, load_history_datasets
from research.value_area_stays import observe
from research.value_area_grid import simulate_grid

STUDY = Path('var/post-move-consolidation.json')
BREAKOUT = dict(grace=2, horizon=288, inside='close')


def moves(bars, window, measure):
    values = [0.0] * len(bars)
    for i in range(window-1, len(bars)):
        segment = bars[i-window+1:i+1]
        opening = segment[0]['open']
        if measure == 'body':
            values[i] = 100 * abs(bars[i]['close'] / opening - 1)
        else:
            values[i] = 100 * (max(b['high'] for b in segment) -
                               min(b['low'] for b in segment)) / opening
    return values


def study(bars, start, stop, changes, cutoff, delay, quiet, cache):
    observations = []
    compounded = 1.0
    i = start
    while i + delay + 289 < stop:
        if changes is not None and not (changes[i-1] < cutoff <= changes[i]):
            i += 1
            continue
        anchor = i + delay
        if delay and quiet[anchor] > 0.5:
            i += 1
            continue
        if anchor not in cache:
            price = bars[anchor]['close']
            low, high = price * .99, price * 1.01
            episode = observe(bars, anchor, low, high, BREAKOUT, 300)
            last = episode['last_index']
            levels = [low + (high-low)*k/8 for k in range(9)]
            trade = simulate_grid(levels, price, bars[anchor+1:last+1], bars[last+1],
                                  fee_bps=2, slip_bps=1, initial_inventory_pct=25)
            cache[anchor] = (last+1, episode['duration_candles'], trade['return_pct'], trade['turnover'])
        exit_index, duration, net, turnover = cache[anchor]
        observations.append((duration, net, turnover * compounded))
        compounded *= 1 + net / 100
        i = exit_index
    if not observations:
        return dict(n=0)
    durations, returns, turnover = zip(*observations)
    return dict(n=len(returns), long_pct=100*mean(d>=150 for d in durations),
                median_candles=median(durations), mean_net_pct=mean(returns),
                portfolio_pct=100*(compounded-1), turnover_initial_capital=sum(turnover))


def main():
    dataset = json.loads(Path('var/range-explorer.json').read_text())['dataset']
    data = combine_datasets(load_history_datasets(Path('var/history')))[dataset['id']]
    assert data.describe()['sha256'] == dataset['sha256'] and data.timeframe == '5m'
    bars = data.open().rows
    assert all(b['ts']-a['ts']==300 for a,b in zip(bars,bars[1:]))
    split_ts = datetime(2026,8,1,tzinfo=timezone.utc).timestamp()
    split = next(i for i,b in enumerate(bars) if b['ts']>=split_ts)
    periods = dict(development=(12,split), evaluation=(split,len(bars)))
    quiet = moves(bars,6,'range')
    cache = {}
    results = []
    def evaluate(changes, cutoff, delay):
        return {name:study(bars,start,stop,changes,cutoff,delay,quiet,cache)
                for name,(start,stop) in periods.items()}
    baseline = evaluate(None,0,0)
    quiet_only = evaluate(None,0,6)
    for measure in ('body','range'):
        for window in (1,3,12):
            changes = moves(bars,window,measure)
            for cutoff in (.25,.5,1,1.5,2):
                for delay in (0,6):
                    results.append(dict(measure=measure, minutes=5*window, threshold_pct=cutoff,
                                        quiet_minutes=5*delay, **evaluate(changes,cutoff,delay)))
    # Rank on development frequency of long consolidations with at least 40 trades.
    winners = []
    for delay in (0,30):
        eligible = [r for r in results if r['quiet_minutes']==delay and r['development']['n']>=40]
        winners.append(max(eligible,key=lambda r:r['development']['long_pct']))
    STUDY.write_text(json.dumps(dict(dataset=dataset,split_utc='2026-08-01',baseline=baseline,
                                     quiet_only=quiet_only, winners=winners, results=results),indent=2))
    print(json.dumps([baseline,quiet_only,*winners],indent=1))


if __name__ == '__main__':
    main()
