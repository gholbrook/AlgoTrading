"""Offline regression for persisted historical account replay."""
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from gridlab import engine
from gridlab.storage import Store
from tests.test_storage import candles


class ReplayCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'runs.sqlite3'
        self.store = Store(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def backtest(self):
        bars = candles(230)
        return self.store.backtest('BTC/USD', '1h', {}, 'offline', bars[:205], bars[205:])

    def uncache(self, run_id):
        with sqlite3.connect(self.path) as db:
            db.execute('DELETE FROM replay_accounts WHERE run_id=?', (run_id,))

    def test_creation_piggybacks_and_reopened_reads_do_no_engine_replay(self):
        with patch('gridlab.storage.engine.step', wraps=engine.step) as step:
            run_id = self.backtest()
            self.assertEqual(step.call_count, 230)
        with patch('gridlab.storage.engine.step', side_effect=AssertionError('unexpected replay')):
            detail = Store(self.path).detail(run_id)
            self.assertEqual(Store(self.path).detail(run_id)['replay_account'], detail['replay_account'])
        expected = Store._replay_account(detail['points'], detail['run']['config'])
        self.assertEqual(detail['replay_account'], expected)
        self.assertEqual(len(expected['summaries']), 230)

    def test_old_database_migrates_and_lazily_caches_once(self):
        run_id = self.backtest()
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TABLE replay_accounts')
        store = Store(self.path)
        with patch.object(Store, '_replay_account', wraps=Store._replay_account) as replay:
            first = store.detail(run_id)
            self.assertEqual(store.detail(run_id), first)
            self.assertEqual(replay.call_count, 1)

    def test_source_and_config_changes_invalidate_cache(self):
        run_id = self.backtest()
        original = Path.read_bytes
        def changed_source(path):
            content = original(path)
            return content + b'\n# engine upgrade' if path.name == 'engine.py' else content
        with patch.object(Path, 'read_bytes', changed_source), \
             patch.object(Store, '_replay_account', wraps=Store._replay_account) as replay:
            self.store.detail(run_id)
            self.store.detail(run_id)
            self.assertEqual(replay.call_count, 1)
        with sqlite3.connect(self.path) as db:
            config = json.loads(db.execute('SELECT config_json FROM runs WHERE id=?', (run_id,)).fetchone()[0])
            config['fee_bps'] = 35
            db.execute('UPDATE runs SET config_json=? WHERE id=?', (json.dumps(config), run_id))
        with patch.object(Store, '_replay_account', wraps=Store._replay_account) as replay:
            self.store.detail(run_id)
            self.store.detail(run_id)
            self.assertEqual(replay.call_count, 1)

    def test_concurrent_lazy_readers_and_cascading_delete(self):
        run_id = self.backtest()
        self.uncache(run_id)
        barrier = threading.Barrier(2)
        original = Store._replay_account
        def replay(*args):
            barrier.wait(timeout=10)
            return original(*args)
        with patch.object(Store, '_replay_account', side_effect=replay), ThreadPoolExecutor(2) as pool:
            results = list(pool.map(self.store.detail, [run_id, run_id]))
        self.assertEqual(results[0], results[1])
        self.store.delete_archived_run(run_id, results[0]['run']['created_at'])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM replay_accounts').fetchone()[0], 0)

    def test_delete_and_id_reuse_during_lazy_replay_cannot_cache_old_result(self):
        run_id = self.backtest()
        self.uncache(run_id)
        original = Store._replay_account
        def replay(*args):
            created = self.store.get(run_id)['created_at']
            self.store.delete_archived_run(run_id, created)
            replacement = self.backtest()
            self.assertEqual(replacement, run_id)
            self.uncache(replacement)
            return original(*args)
        with patch.object(Store, '_replay_account', side_effect=replay):
            self.store.detail(run_id)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM replay_accounts').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
