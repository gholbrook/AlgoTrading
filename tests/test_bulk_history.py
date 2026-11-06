import hashlib
import io
import tempfile
import unittest
import zipfile
from datetime import date, datetime, timezone
from unittest.mock import patch

from tools import bulk_history as bulk


def archive(month, missing=False):
    start = int(datetime(month.year, month.month, 1, tzinfo=timezone.utc).timestamp())
    end = bulk.next_month(month)
    stop = int(datetime(end.year, end.month, 1, tzinfo=timezone.utc).timestamp())
    scale = 1000000 if month.year >= 2025 else 1000
    rows = [f'{ts*scale},2,3,1,2,4,{(ts+300)*scale-1},8,1,2,4,0\n'
            for ts in range(start, stop, 300)]
    if missing:
        rows.pop(4)
    result = io.BytesIO()
    name = f'BTCUSDT-5m-{month:%Y-%m}.csv'
    with zipfile.ZipFile(result, 'w') as target:
        target.writestr(name, ''.join(rows))
    return result.getvalue(), name


class BulkTests(unittest.TestCase):
    def test_exact_month_both_timestamp_units(self):
        for month in (date(2024, 2, 1), date(2025, 2, 1)):
            content, name = archive(month)
            self.assertEqual(len(bulk.parse_archive(content, name, month)), (29 if month.year == 2024 else 28)*288)
            content, name = archive(month, True)
            with self.assertRaisesRegex(ValueError, 'missing'):
                bulk.parse_archive(content, name, month)

    def test_verified_resume_and_source_identity(self):
        month = date(2024, 2, 1)
        content, name = archive(month)
        calls = []
        def opener(req, timeout):
            calls.append(req.full_url)
            if req.full_url.endswith('.CHECKSUM'):
                return io.BytesIO(f'{hashlib.sha256(content).hexdigest()}  {name[:-4]}.zip\n'.encode())
            return io.BytesIO(content)
        with tempfile.TemporaryDirectory() as directory, patch.object(bulk, '_binance_symbol', return_value='BTCUSDT'):
            path, first = bulk.import_month('BTC/USDT', month, directory, opener=opener)
            again, second = bulk.import_month('BTC/USDT', month, directory, opener=opener)
            self.assertEqual(path, again)
            self.assertEqual(first, second)
            self.assertTrue(first['id'].startswith('binance_spot_monthly_'))
            self.assertEqual(sum(not c.endswith('.CHECKSUM') for c in calls), 1)

    def test_reject_checksum_wrong_member_and_future(self):
        month = date(2024, 2, 1)
        content, name = archive(month)
        with self.assertRaises(ValueError):
            bulk.parse_archive(content, 'ETHUSDT.csv', month)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                bulk.import_month('BTC/USDT', month, directory, symbol='BTCUSDT',
                    opener=lambda req, timeout: io.BytesIO((b'0'*64 + b' BTCUSDT-5m-2024-02.zip') if req.full_url.endswith('.CHECKSUM') else content))
            with self.assertRaises(ValueError):
                bulk.collect_month('BTC/USDT', '2999-01', directory)


if __name__ == '__main__':
    unittest.main()
