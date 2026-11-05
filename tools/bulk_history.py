"""Verified Binance spot monthly 5m archive imports; no credentials required."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import re
import sys
import zipfile
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

from gridlab.historical import MARKET_RE, _atomic_create, _binance_symbol, write_snapshot
from gridlab.market import validate_bar

ARCHIVE_ROOT = 'https://data.binance.vision/data/spot/monthly/klines'
DEFAULT_HISTORY_DIR = Path(__file__).resolve().parents[1] / 'var' / 'history'


def month_date(value):
    if not re.fullmatch(r'\d{4}-\d{2}', value):
        raise ValueError('Months must use YYYY-MM.')
    return date.fromisoformat(value + '-01')


def next_month(value):
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1)


def months(start, end):
    cursor, last = month_date(start), month_date(end)
    if cursor > last:
        raise ValueError('Start month must be on or before end month.')
    while cursor <= last:
        yield cursor
        cursor = next_month(cursor)


def _read(url, opener):
    with opener(Request(url, headers={'User-Agent': 'Gridline/1.0'}), timeout=60) as response:
        return response.read()


def parse_archive(content, filename, month):
    start = int(datetime(month.year, month.month, 1, tzinfo=timezone.utc).timestamp())
    following = next_month(month)
    end = int(datetime(following.year, following.month, 1, tzinfo=timezone.utc).timestamp())
    bars = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if archive.namelist() != [filename]:
            raise ValueError('Archive must contain exactly the expected symbol CSV.')
        with archive.open(filename) as stream:
            for row in csv.reader(io.TextIOWrapper(stream, encoding='utf-8')):
                if len(row) != 12:
                    raise ValueError('Malformed Binance archive candle row.')
                raw_ts, close_ts = int(row[0]), int(row[6])
                # Binance spot archive timestamps switch to microseconds in 2025.
                scale = 1_000_000 if month >= date(2025, 1, 1) else 1000
                if raw_ts % scale or close_ts != raw_ts + 300 * scale - 1:
                    raise ValueError('Invalid archive candle open/close timestamp.')
                ts = raw_ts // scale
                if ts != start + len(bars) * 300 or ts >= end:
                    raise ValueError('Archive has a missing, duplicate, out-of-order or out-of-range candle.')
                bars.append(validate_bar(dict(ts=ts, open=row[1], high=row[2], low=row[3], close=row[4], volume=row[5])))
    if len(bars) != (end - start) // 300:
        raise ValueError('Archive does not cover the exact complete UTC month.')
    return bars


def import_month(market, month, directory, *, opener=urlopen, symbol=None):
    if not MARKET_RE.fullmatch(market):
        raise ValueError('Pair must use uppercase BASE/QUOTE.')
    following = next_month(month)
    if following > datetime.now(timezone.utc).date():
        raise ValueError('Only completed UTC months may be imported.')
    symbol = symbol or _binance_symbol(market, opener)
    if symbol != market.replace('/', '') or not re.fullmatch(r'[A-Z0-9]+', symbol):
        raise ValueError('Binance symbol does not match the requested pair.')
    name = f'{symbol}-5m-{month:%Y-%m}.zip'
    url = f'{ARCHIVE_ROOT}/{symbol}/5m/{name}'
    checksum_bytes = _read(url + '.CHECKSUM', opener)
    parts = checksum_bytes.decode('ascii').split()
    if len(parts) != 2 or not re.fullmatch('[0-9a-fA-F]{64}', parts[0]) or parts[1].lstrip('*') != name:
        raise ValueError('Invalid remote SHA256 checksum document.')
    expected = parts[0].lower()
    cache = Path(directory) / '.archives' / expected / name
    content = cache.read_bytes() if cache.exists() else None
    if content is None or hashlib.sha256(content).hexdigest() != expected:
        content = _read(url, opener)
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError('Archive SHA256 does not match remote checksum.')
    bars = parse_archive(content, name[:-4] + '.csv', month)
    _atomic_create(cache, content)
    _atomic_create(cache.with_suffix('.zip.CHECKSUM'), checksum_bytes)
    return write_snapshot(bars, market, '5m', month.isoformat(),
                          (following - timedelta(days=1)).isoformat(), directory,
                          venue='Binance', id_prefix='binance_spot_monthly', provider='Binance spot monthly public archives',
                          provenance={'archive_url': url, 'archive_sha256': expected,
                                      'checksum_url': url + '.CHECKSUM', 'symbol': symbol})


def collect_month(market, month, directory=DEFAULT_HISTORY_DIR):
    """Collect one complete UTC month; report an already verified snapshot."""
    parsed = month_date(month)
    before = set(Path(directory).glob('binance_spot_monthly_*.json'))
    path, metadata = import_month(market, parsed, directory)
    return metadata, path.with_suffix('.json') in before


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-month', required=True)
    parser.add_argument('--end-month', required=True)
    parser.add_argument('--pairs', nargs='+', required=True)
    parser.add_argument('--history-dir', type=Path, default=DEFAULT_HISTORY_DIR)
    args = parser.parse_args(argv)
    try:
        requested = list(months(args.start_month, args.end_month))
    except ValueError as exc:
        parser.error(str(exc))
    failed = 0
    for pair in dict.fromkeys(args.pairs):
        try:
            if not MARKET_RE.fullmatch(pair):
                raise ValueError('Pair must use uppercase BASE/QUOTE.')
            symbol = _binance_symbol(pair, urlopen)
        except Exception as exc:
            failed += len(requested)
            print(f'FAILED {pair}: {exc}', file=sys.stderr, flush=True)
            continue
        for month in requested:
            try:
                path, metadata = import_month(pair, month, args.history_dir, symbol=symbol)
                print(f'OK {pair} {month:%Y-%m}: {metadata["bars"]} bars {path}', flush=True)
            except Exception as exc:
                failed += 1
                print(f'FAILED {pair} {month:%Y-%m}: {exc}', file=sys.stderr, flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
