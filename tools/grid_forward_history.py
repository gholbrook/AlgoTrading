"""Collect completed UTC days for the locked grid experiment. Run from repo root."""
import argparse
import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
from itertools import groupby
from pathlib import Path
from urllib.request import urlopen
import zipfile

from gridlab.historical import _page, write_snapshot
from gridlab.market import validate_bar
from tools.bulk_history import _read

ROOT = Path('var/walk-forward')
HISTORY = Path('var/history')
FIRST = date(2026, 11, 9)


def get_day(name, day):
    venue, pair = name.split('-')
    market = pair.replace('_','/')
    symbol = pair.replace('_','')
    stamp = int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    provenance = {}
    if venue == 'Binance':
        filename = f'{symbol}-5m-{day}.zip'
        url = f'https://data.binance.vision/data/spot/daily/klines/{symbol}/5m/{filename}'
        sha, target = _read(url+'.CHECKSUM', urlopen).decode().split()
        archive = _read(url, urlopen)
        if target.lstrip('*') != filename or hashlib.sha256(archive).hexdigest() != sha:
            raise ValueError(f'Archive verification failed for {name} {day}')
        provenance = {'archive_url':url, 'archive_sha256':sha}
        rows = []
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            if z.namelist() != [filename[:-4]+'.csv']:
                raise ValueError('Unexpected archive members')
            for r in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
                ts = int(r[0])//1_000_000
                if ts*1_000_000 != int(r[0]) or ts%300 or not stamp<=ts<stamp+86400 or (rows and ts<=rows[-1]['ts']):
                    raise ValueError('Invalid candle timestamp')
                if int(r[6])!=(ts+300)*1_000_000-1:
                    continue
                rows.append(validate_bar(dict(ts=ts,open=r[1],high=r[2],low=r[3],close=r[4],volume=r[5])))
    else:
        rows = sorted(_page(market,'5m',stamp,stamp+86400,urlopen).values(),key=lambda r:r['ts'])
    if not rows:
        raise ValueError(f'No completed candles for {name} {day}')
    for _, segment in groupby(enumerate(rows),key=lambda item:item[1]['ts']-300*item[0]):
        write_snapshot([r for _,r in segment],market,'5m',day.isoformat(),day.isoformat(),HISTORY,
                       venue=venue,id_prefix=venue.lower()+'_daily',provenance=provenance)
    return len(rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--through',type=date.fromisoformat,default=datetime.now(timezone.utc).date()-timedelta(days=1))
    end=parser.parse_args().through
    if end>=datetime.now(timezone.utc).date():
        parser.error('Only completed UTC days are eligible.')
    manifest=ROOT/'forward-downloads.json'
    done=json.loads(manifest.read_text()) if manifest.exists() else {}
    names=json.loads((ROOT/'forward-lock.json').read_text())['markets']
    jobs=[(name,FIRST+timedelta(days=i)) for i in range((end-FIRST).days+1) for name in names
          if f'{name} {FIRST+timedelta(days=i)}' not in done]
    errors=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        tasks=[(name,day,pool.submit(get_day,name,day)) for name,day in jobs]
        for name,day,task in tasks:
            try:
                done[f'{name} {day}']=task.result()
            except Exception as exc:
                errors.append(f'{name} {day}: {exc}')
                continue
            tmp=manifest.with_suffix('.tmp')
            tmp.write_text(json.dumps(done,indent=2))
            tmp.replace(manifest)
    print(f'{len(jobs)-len(errors)} imported days, {len(errors)} errors')
    if errors:raise SystemExit('\n'.join(errors))


if __name__=='__main__':
    main()
