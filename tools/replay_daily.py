"""Cache daily closes used to warm up replay rules. Run python -m tools.replay_daily."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from gridlab.datasets import combine_datasets, load_history_datasets

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'var' / 'replay-daily'


def read_closes(venue, market):
    path = DIRECTORY / f"{venue}-{market.replace('/', '_')}.json"
    return json.loads(path.read_text()) if path.is_file() else []


def fetch(item):
    (venue, market), end = item
    day = 86400
    # UTC day preceding the last available intraday bar.
    end = (end // day) * day
    if venue == 'Binance':
        query = urlencode(dict(symbol=market.replace('/',''), interval='1d',
                               endTime=end*1000-1, limit=1000))
        with urlopen('https://data-api.binance.vision/api/v3/klines?'+query, timeout=20) as response:
            rows = [[int(r[0])//1000, float(r[4])] for r in json.load(response)]
    elif venue == 'Coinbase':
        rows = []
        for start in range(end-1000*day,end,290*day):
            query=urlencode(dict(granularity=day,start=datetime.fromtimestamp(start,timezone.utc).isoformat(),end=datetime.fromtimestamp(min(start+290*day,end),timezone.utc).isoformat()))
            url='https://api.exchange.coinbase.com/products/'+market.replace('/','-')+'/candles?'+query
            with urlopen(Request(url,headers={'User-Agent':'Gridline/1.0','Accept':'application/json'}),timeout=20) as response:
                rows.extend([int(r[0]),float(r[4])] for r in json.load(response) if start<=r[0]<end)
    else:
        return
    rows=sorted(dict(rows).items())
    if not rows or any(t%day or p<=0 for t,p in rows):
        raise ValueError(f'Invalid daily closes for {market}')
    DIRECTORY.mkdir(exist_ok=True)
    path=DIRECTORY / f"{venue}-{market.replace('/', '_')}.json"
    path.with_suffix('.tmp').write_text(json.dumps(rows))
    path.with_suffix('.tmp').replace(path)
    print(venue,market,len(rows),flush=True)


if __name__=='__main__':
    ends={}
    for d in combine_datasets(load_history_datasets(ROOT/'var/history')).values():
        key=d.venue,d.market
        ends[key]=max(ends.get(key,0),int(datetime.fromisoformat(d.describe()['end']).timestamp()))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch,ends.items()))
