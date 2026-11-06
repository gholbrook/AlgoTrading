"""Archive catalog discovery stays metadata-only until a snapshot is opened."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gridlab.datasets import LazyDataset, SAMPLE, load_datasets, load_history_datasets
from gridlab.historical import write_snapshot
from gridlab.server import ROOT, Workbench


class BulkCatalogTests(unittest.TestCase):
    def snapshot(self, directory):
        path, meta = write_snapshot(
            [{"ts": 1704067200 + i * 300, "open": 100., "high": 101.,
              "low": 99., "close": 100., "volume": 1.} for i in range(3)],
            "BTC/USDT", "5m", "2024-01-01", "2024-01-01", directory,
            venue="Binance", provider="Binance spot monthly public archives")
        old_sidecar = path.with_suffix('.json')
        meta['id'] = meta['id'].replace('binance_', 'binance_spot_monthly_', 1)
        new_path = path.with_name(meta['id'] + '.csv')
        path.rename(new_path)
        old_sidecar.unlink()
        meta['filename'] = new_path.name
        new_path.with_suffix('.json').write_text(json.dumps(meta))
        return new_path, meta

    def test_discovery_does_not_read_csv_and_open_verifies(self):
        with tempfile.TemporaryDirectory() as temp:
            path, meta = self.snapshot(temp)
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('eager CSV read')):
                data = load_history_datasets(temp)[meta['id']]
                self.assertIsInstance(data, LazyDataset)
                self.assertEqual(data.describe()['bars'], 3)
                self.assertIn('monthly public archives', data.source())
            self.assertEqual(len(data.open().rows), 3)
            path.write_text(path.read_text().replace('100,101', '100,102'))
            with self.assertRaisesRegex(ValueError, 'checksum'):
                data.open()

    def test_running_workbench_refresh_discovers_and_removes_external_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Workbench(Path(temp) / 'gridline.sqlite3', load_datasets(ROOT / 'examples'))
            self.assertEqual([d['id'] for d in app.context()['datasets']], [SAMPLE])
            path, meta = self.snapshot(app.history_dir)
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('eager CSV read')):
                self.assertIn(meta['id'], [d['id'] for d in app.context()['datasets']])
            self.assertEqual(len(app.dataset(meta['id']).rows), 3)
            path.unlink()
            self.assertNotIn(meta['id'], [d['id'] for d in app.context()['datasets']])

    def test_rehearsal_uses_archive_source_and_detects_changed_file(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Workbench(Path(temp) / 'gridline.sqlite3', load_datasets(ROOT / 'examples'))
            path, meta = self.snapshot(app.history_dir)
            app.context()
            with patch('gridlab.server.engine.warmup_count', return_value=1):
                rid = app.rehearse({'dataset': meta['id']})
            self.assertEqual(app.store.get(rid)['source'], app.datasets[meta['id']].source())
            path.write_text(path.read_text().replace('100,101', '100,102'))
            with self.assertRaisesRegex(ValueError, 'checksum'):
                app.advance(rid, 1)


if __name__ == '__main__':
    unittest.main()
