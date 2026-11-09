"""Plot exported value-area instances and evaluate chronological duration signals.

python -m research.value_area_predictability INPUT.json --output var/value-area-predictability
Recent mean compares five known observed durations with the preceding five.
Unfinished instances contribute lower bounds to features. A censored target is
usable only when its observed lower bound already exceeds the tested threshold.
"""
import argparse
import csv
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean


def percentile(values, fraction):
    ordered = sorted(values)
    location = (len(ordered)-1)*fraction
    lower = int(location)
    upper = min(lower+1, len(ordered)-1)
    return ordered[lower]+(ordered[upper]-ordered[lower])*(location-lower)


def features(episodes, lookback):
    rows = []
    known = []
    pending = sorted(episodes, key=lambda e: e['observed_end'])
    pointer = 0
    for episode in sorted(episodes, key=lambda e: e['anchor']):
        while pointer < len(pending) and pending[pointer]['observed_end'] <= episode['anchor']:
            previous = pending[pointer]
            if previous['anchor'] < episode['anchor']:
                known.append(previous)
            pointer += 1
        if len(known) < lookback*2:
            continue
        recent = mean(e['duration_candles'] for e in known[-lookback:])
        earlier = mean(e['duration_candles'] for e in known[-2*lookback:-lookback])
        rows.append(dict(id=episode['id'], anchor=episode['anchor'], observed_end=episode['observed_end'],
                         recent_mean=recent, earlier_mean=earlier, trend=recent-earlier,
                         duration=episode['duration_candles'], censored=episode['censored']))
    return rows


def target(row, threshold):
    if row['duration'] >= threshold:
        return 1
    if row['censored']:
        return None
    return 0


def rate(rows, threshold):
    values = [target(row, threshold) for row in rows]
    known = [value for value in values if value is not None]
    return dict(n=len(known), excluded=len(values)-len(known),
                long=sum(known), probability=mean(known) if known else None)


def difference(rows, threshold):
    up = rate([row for row in rows if row['trend'] > 0], threshold)['probability']
    other = rate([row for row in rows if row['trend'] <= 0], threshold)['probability']
    return None if up is None or other is None else up-other


def bootstrap(rows, threshold, block=20, repeats=1000):
    """Resample contiguous instance blocks to retain some local dependence."""
    if len(rows) < 2*block:
        return None
    rng = random.Random(20261106)
    differences = []
    for _ in range(repeats):
        sample = []
        while len(sample) < len(rows):
            start = rng.randrange(len(rows)-block+1)
            sample.extend(rows[start:start+block])
        value = difference(sample[:len(rows)], threshold)
        if value is not None:
            differences.append(value)
    return [percentile(differences, .025), percentile(differences, .975)] if differences else None


def analyze(report, lookback=5, threshold=24):
    if report['config']['stride'] != 0:
        raise ValueError('Use an export with stride=0 so forward observation windows do not overlap.')
    rows = features(report['episodes'], lookback)
    split = report['evaluation_start'] + .7*(report['evaluation_end']-report['evaluation_start'])
    train = [row for row in rows if row['observed_end'] < split]
    test = [row for row in rows if row['anchor'] >= split]
    if len(train) < 20 or len(test) < 40:
        raise ValueError('Need at least 20 training and 40 evaluation instances after feature warmup.')
    cuts = [percentile([r['recent_mean'] for r in train], q) for q in (.25,.5,.75)]
    for row in rows:
        row['recent_bucket'] = sum(row['recent_mean'] > cut for cut in cuts)
        row['partition'] = 'evaluation' if row['anchor'] >= split else 'training' if row['observed_end'] < split else 'split_boundary'
    thresholds = sorted(set([12, threshold, 72]))
    metrics = {}
    for length in thresholds:
        metrics[str(length)] = dict(
            training=dict(rising=rate([r for r in train if r['trend']>0], length),
                          other=rate([r for r in train if r['trend']<=0], length)),
            evaluation=dict(all=rate(test,length), rising=rate([r for r in test if r['trend']>0],length),
                            other=rate([r for r in test if r['trend']<=0],length)),
            rising_difference=difference(test,length),
            block_bootstrap_95=bootstrap(test,length),
            recent_mean_buckets=[rate([r for r in test if r['recent_bucket']==bucket],length) for bucket in range(4)])
    months = sorted({datetime.fromtimestamp(r['anchor'], timezone.utc).strftime('%Y-%m') for r in test})
    monthly = {}
    for month in months:
        sample = [r for r in test if datetime.fromtimestamp(r['anchor'],timezone.utc).strftime('%Y-%m')==month]
        monthly[month] = dict(rising=rate([r for r in sample if r['trend']>0],threshold),
                             other=rate([r for r in sample if r['trend']<=0],threshold))
    return rows, dict(split=datetime.fromtimestamp(split,timezone.utc).isoformat(),
                      training_n=len(train), evaluation_n=len(test), lookback=lookback, threshold=threshold,
                      recent_mean_quartile_cuts=cuts, metrics=metrics, monthly=monthly,
                      limitations=['Evaluation window was selected after viewing the overall duration pattern.',
                                   'Censored prior durations enter predictors as lower bounds.',
                                   'Value profiles share formation candles, so episodes remain dependent.',
                                   'Bootstrap uses 20-instance blocks; intervals are exploratory.',
                                   'Thresholds and parameters need prospective validation on new data.'])


def plot(report, rows, analysis, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as dates
    from matplotlib.ticker import PercentFormatter
    plt.rcParams.update({'font.size':10, 'axes.spines.top':False, 'axes.spines.right':False})
    fig = plt.figure(figsize=(14,10), layout='constrained')
    grid=fig.add_gridspec(3,2,height_ratios=[1.25,1,1])
    timeline=fig.add_subplot(grid[0,:])
    episodes=report['episodes']
    timestamp=lambda ts: datetime.fromtimestamp(ts,timezone.utc)
    for censored, marker, label in [(False,'.','Confirmed exit duration'),(True,'^','Unfinished duration lower bound')]:
        values=[e for e in episodes if e['censored']==censored]
        timeline.scatter([timestamp(e['anchor']) for e in values],[e['duration_candles'] for e in values],
                         s=13 if not censored else 25,alpha=.5,marker=marker,label=label,color='#73869b' if not censored else '#d59128')
    timeline.plot([timestamp(r['anchor']) for r in rows],[r['recent_mean'] for r in rows],color='#16745c',lw=1.4,label=f"Mean of last {analysis['lookback']} known durations")
    timeline.axvline(datetime.fromisoformat(analysis['split']),color='#b94848',ls='--',label='Evaluation starts')
    timeline.set(ylabel='Duration · candles',title=f"{report['dataset']['market']} · {report['dataset']['timeframe']} · instance duration through time")
    timeline.xaxis.set_major_formatter(dates.DateFormatter('%b'))
    timeline.legend(loc='upper left',ncol=2,fontsize=8)
    trend=fig.add_subplot(grid[1,0]); bucket=fig.add_subplot(grid[1,1])
    metric=analysis['metrics'][str(analysis['threshold'])]
    groups=[metric['evaluation']['other'],metric['evaluation']['rising']]
    trend.bar(['Recent mean flat / falling','Recent mean rising'],[g['probability'] or 0 for g in groups],color=['#73869b','#16745c'])
    for i,g in enumerate(groups):
        trend.text(i,(g['probability'] or 0)+.015,f"{g['probability']:.1%}\nn={g['n']}" if g['probability'] is not None else 'No observations',ha='center')
    trend.set(ylim=(0,1),ylabel=f"P(next duration ≥ {analysis['threshold']} candles)",title='Does increasing duration precede a long next instance?')
    trend.yaxis.set_major_formatter(PercentFormatter(1))
    bins=metric['recent_mean_buckets']
    bucket.bar(['Lowest','Lower-middle','Upper-middle','Highest'],[g['probability'] or 0 for g in bins],color='#16745c')
    for i,g in enumerate(bins):
        bucket.text(i,(g['probability'] or 0)+.015,f"{g['probability']:.1%}\nn={g['n']}" if g['probability'] is not None else 'No observations',ha='center')
    bucket.set(ylim=(0,1),title='Does the recent duration level matter?',xlabel=f"Last-{analysis['lookback']} mean buckets · boundaries learned before evaluation")
    bucket.yaxis.set_major_formatter(PercentFormatter(1))
    monthly=fig.add_subplot(grid[2,0]); sensitivity=fig.add_subplot(grid[2,1])
    months=list(analysis['monthly'])
    for key,color,offset,label in [('other','#73869b',-.18,'Flat / falling'),('rising','#16745c',.18,'Rising')]:
        monthly.bar([i+offset for i in range(len(months))],
                    [analysis['monthly'][m][key]['probability'] or 0 for m in months],width=.36,color=color,label=label)
    monthly.set_xticks(range(len(months)),months)
    monthly.set(ylim=(0,1),title='Evaluation results by month',ylabel=f"P(next duration ≥ {analysis['threshold']} candles)")
    monthly.yaxis.set_major_formatter(PercentFormatter(1)); monthly.legend(fontsize=8)
    lengths=[int(k) for k in analysis['metrics']]
    deltas=[analysis['metrics'][str(k)]['rising_difference'] or 0 for k in lengths]
    intervals=[analysis['metrics'][str(k)]['block_bootstrap_95'] for k in lengths]
    sensitivity.scatter(range(len(lengths)),[100*d for d in deltas],color='#16745c')
    for i,interval in enumerate(intervals):
        if interval: sensitivity.vlines(i,interval[0]*100,interval[1]*100,color='#16745c',lw=2)
    sensitivity.axhline(0,color='#73869b',ls='--');sensitivity.set_xticks(range(len(lengths)),[f'≥ {k} candles' for k in lengths])
    sensitivity.set(title='Rising minus flat / falling · exploratory 95% block intervals',ylabel='Difference · percentage points')
    fig.suptitle('Value-area duration clustering and next-instance predictability',fontsize=17)
    fig.savefig(output/'duration-analysis.png',dpi=170)
    fig.savefig(output/'duration-analysis.svg')
    plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    parser.add_argument('--output',type=Path,default=Path('var/value-area-predictability'))
    parser.add_argument('--lookback',type=int,default=5)
    parser.add_argument('--threshold',type=int,default=24)
    args=parser.parse_args()
    if args.lookback<1 or args.threshold<1: parser.error('lookback and threshold must be positive')
    report=json.loads(args.input.read_text())
    rows,analysis=analyze(report,args.lookback,args.threshold)
    args.output.mkdir(parents=True,exist_ok=True)
    analysis['source']=str(args.input.resolve());analysis['config']=report['config'];analysis['dataset']=report['dataset']
    (args.output/'analysis.json').write_text(json.dumps(analysis,indent=2,allow_nan=False))
    with (args.output/'instances.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    plot(report,rows,analysis,args.output)
    print(json.dumps({k:analysis[k] for k in ('split','training_n','evaluation_n','metrics','monthly')},indent=2))


if __name__=='__main__':
    main()
