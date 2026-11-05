"""Test relative USDT returns as advance warnings of ETH/BTC sector exits."""
import argparse
import json
import math
from bisect import bisect_left
from datetime import datetime, timezone
from pathlib import Path
from statistics import median, quantiles, stdev

from gridlab.datasets import load_history_datasets, utc_iso

PAIRS = ("ETH/BTC", "ETH/USDT", "BTC/USDT")
MODELS = ("boundary", "direct", "usdt", "combined")
HORIZON = 3  # next three closed five-minute bars


def aligned(history):
    markets = {pair: {} for pair in PAIRS}
    for dataset in load_history_datasets(history).values():
        if dataset.venue != "Binance" or dataset.timeframe != "5m" or dataset.market not in markets:
            continue
        book = markets[dataset.market]
        for row in dataset.rows:
            if book.setdefault(row["ts"], row) != row:
                raise ValueError(f"Conflicting {dataset.market} data at {utc_iso(row['ts'])}")
    common = sorted(set.intersection(*(set(book) for book in markets.values())))
    return [(ts, *(markets[p][ts] for p in PAIRS)) for ts in common]


def experiment(rows):
    # A fresh two-hour formation after every exit keeps sector episodes disjoint.
    episodes = []
    i = 288
    while i + HORIZON < len(rows):
        if rows[i][0] - rows[i-288][0] != 288 * 300:
            i += 1
            continue
        history = [r[1] for r in rows[i-288:i+1]]
        returns = [math.log(b["close"]/a["close"]) for a,b in zip(history, history[1:])]
        formation = history[-24:]
        lo = min(b["low"] for b in formation)
        hi = max(b["high"] for b in formation)
        # Compare current volatility to the preceding 22 hours, excluding formation.
        if hi <= lo or stdev(returns[-24:]) >= 0.65 * stdev(returns[:-24]):
            i += 1
            continue
        log_width = math.log(hi/lo)
        typ = median(math.log(b["high"]/b["low"]) for b in formation)
        intervals = min(12, max(2, int(log_width / typ))) if typ else 2
        episode = dict(start=i, floor=lo, ceiling=hi, observations=[],
                       grid=[lo * math.exp(log_width*k/intervals) for k in range(intervals+1)])
        j = i
        while j + HORIZON < len(rows) and j-i < 72:
            if rows[j+HORIZON][0]-rows[j][0] != HORIZON*300:
                break
            _, direct, eth, btc = rows[j]
            if direct["close"] > hi or direct["close"] < lo:
                break
            prev = rows[j-3]
            own = math.log(direct["close"]/prev[1]["close"]) / log_width
            relative = (math.log(eth["close"]/prev[2]["close"]) -
                        math.log(btc["close"]/prev[3]["close"])) / log_width
            position = math.log(direct["close"]/lo)/log_width - 0.5
            outcome = 0
            for lead in range(1, HORIZON+1):
                close = rows[j+lead][1]["close"]
                if close > hi or close < lo:
                    outcome = 1 if close > hi else -1
                    break
            scores = (position, position+own, position+relative, position+(own+relative)/2)
            episode["observations"].append((j, outcome, lead, scores))
            j += 1
        episode["end"] = j
        if episode["observations"]:
            episodes.append(episode)
        i = j + 24
    return episodes


def assess(train, test):
    report = {}
    for k, name in enumerate(MODELS):
        calibration = [abs(scores[k]) for _,_,_,scores in train]
        threshold = quantiles(calibration, n=10, method="inclusive")[-1]
        alerts = hits = 0
        leads = []
        for _,outcome,lead,scores in test:
            score = scores[k]
            if abs(score) < threshold or score == 0:
                continue
            alerts += 1
            if outcome * score > 0:
                hits += 1
                leads.append(lead*5)
        report[name] = dict(threshold=threshold, alerts=alerts, correct=hits,
                            precision=hits/alerts if alerts else None,
                            median_correct_lead_minutes=median(leads) if leads else None)
    return report


def compare_exit_leads(episodes, models):
    """Match correct alerts to distinct close-breaks, counting silence as zero lead."""
    tally = dict(exits=0, usdt_earlier=0, boundary_earlier=0, same_lead=0, both_silent=0)
    for episode in episodes:
        events = {}
        for j, direction, lead, scores in episode["observations"]:
            if not direction:
                continue
            latest = events.setdefault(j + lead, [0, 0])
            for n, (k, model) in enumerate(((0, "boundary"), (2, "usdt"))):
                if direction * scores[k] >= models[model]["threshold"]:
                    latest[n] = max(latest[n], lead)
        for boundary, usdt in events.values():
            tally["exits"] += 1
            if boundary == usdt == 0:
                tally["both_silent"] += 1
            elif usdt > boundary:
                tally["usdt_earlier"] += 1
            elif boundary > usdt:
                tally["boundary_earlier"] += 1
            else:
                tally["same_lead"] += 1
    return tally


def plot(path, rows, episode, threshold):
    from matplotlib.figure import Figure
    fig = Figure(figsize=(10,5), layout="constrained")
    top, bottom = fig.subplots(2, 1, sharex=True, height_ratios=(2,1))
    start, end = episode["start"], episode["end"]
    indices = list(range(start-23, min(end+4,len(rows))))
    x = [(i-start)*5 for i in indices]
    top.plot(x,[rows[i][1]["close"] for i in indices],color="#204460",label="ETH/BTC close")
    for price in episode["grid"]:
        top.hlines(price, 0, (end-start)*5, color="#599e86", linewidth=.8)
    top.axvspan(-115,0,color="#91abc1",alpha=.18,label="Two-hour formation")
    top.set_title("First holdout sector  ·  "+utc_iso(rows[start][0]+300))
    top.set_ylabel("BTC per ETH")
    top.legend()
    obs = episode["observations"]
    for k,label in [(1,"ETH/BTC"),(2,"USDT legs")]:
        bottom.plot([(j-start)*5 for j,*_ in obs],[s[k] for _,_,_,s in obs],label=label,alpha=.8)
    for level in (-threshold,threshold):
        bottom.axhline(level,linestyle=":",color="#985c16")
    bottom.set(ylabel="Warning score",xlabel="Minutes from grid setup")
    bottom.legend()
    fig.savefig(path,dpi=135)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--history-dir",type=Path,default=root/"var/history")
    parser.add_argument("--holdout",default="2026-10-01",help="First UTC day of untouched evaluation data")
    parser.add_argument("--image",type=Path)
    args = parser.parse_args()
    rows = aligned(args.history_dir)
    split = bisect_left([r[0] for r in rows],datetime.fromisoformat(args.holdout).replace(tzinfo=timezone.utc).timestamp())
    episodes = experiment(rows)
    training = [e for e in episodes if e["end"]+HORIZON < split]
    testing = [e for e in episodes if e["start"] >= split]
    if not training or not testing:
        parser.error("Need aligned data and sectors on both sides of the holdout date.")
    train,test = ([o for e in group for o in e["observations"]] for group in (training,testing))
    scores = assess(train,test)
    if args.image:
        args.image.parent.mkdir(parents=True,exist_ok=True)
        plot(args.image,rows,testing[0],scores["usdt"]["threshold"])
    print(json.dumps(dict(first_open=utc_iso(rows[0][0]), last_open=utc_iso(rows[-1][0]),
                          holdout=args.holdout, aligned_candles=len(rows), training_sectors=len(training),
                          distinct_exits=compare_exit_leads(testing,scores),
                          holdout_sectors=len(testing),holdout_decisions=len(test),
                          future_exit_decisions=sum(y!=0 for _,y,_,_ in test),models=scores),indent=2))


if __name__ == "__main__":
    main()
