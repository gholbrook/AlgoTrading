"""Value executed quote notional at the Coinbase USD candle open for its timestamp."""
from bisect import bisect_left
from contextlib import closing
from pathlib import Path
import sqlite3
from urllib.request import urlopen

from gridlab.historical import _page


def usd_volume(store, run_id):
    with closing(store._connect()) as db:
        db.execute("BEGIN")
        row = db.execute("SELECT market FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        quote = row["market"].split("/")[-1]
        fills = db.execute("""
            SELECT p.ts, count(*), sum((f.value ->> 'price') * (f.value ->> 'qty'))
            FROM points p, json_each(p.point_json, '$.events') f
            WHERE p.run_id=? AND (f.value ->> 'kind') IN ('entry','exit')
            GROUP BY p.ts ORDER BY p.ts
        """, (run_id,)).fetchall()

    rates = quote_usd_rates(store, quote, [r[0] for r in fills])
    total = 0.0
    missing_count = 0
    cumulative = []
    for ts, count, notional in fills:
        rate = 1.0 if quote == "USD" else rates.get(ts)
        if rate is None:
            missing_count += count
        else:
            total += notional * rate
        cumulative.append([ts, total, missing_count])
    return {"cumulative": cumulative}


def quote_usd_rates(store, quote, timestamps):
    timestamps = sorted(set(timestamps))
    rates = {}
    if quote != "USD" and timestamps:
        path = Path(store.path).parent / "coinbase_usd_5m.sqlite3"
        with closing(sqlite3.connect(path, timeout=30)) as cache:
            cache.execute("CREATE TABLE IF NOT EXISTS rates (quote TEXT, ts INTEGER, price REAL, PRIMARY KEY(quote,ts))")
            rates.update(cache.execute("SELECT ts,price FROM rates WHERE quote=? AND ts BETWEEN ? AND ?",
                                       (quote, timestamps[0], timestamps[-1])))
            missing = [ts for ts in timestamps if ts not in rates]
            i = 0
            while i < len(missing):
                stop = bisect_left(missing, missing[i] + 299*300, lo=i+1)
                try:
                    page = _page(quote+"/USD", "5m", missing[i], missing[stop-1]+300, urlopen)
                except RuntimeError:
                    break  # Leave unavailable timestamps unresolved and retry on a later request.
                rates.update((ts, bar["open"]) for ts,bar in page.items())
                with cache:
                    cache.executemany("INSERT OR IGNORE INTO rates VALUES(?,?,?)",
                                      ((quote,ts,bar["open"]) for ts,bar in page.items()))
                i = stop
    return {ts: rates[ts] for ts in timestamps if ts in rates}
