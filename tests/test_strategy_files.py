"""Tests for safe strategy-file persistence and HTTP endpoints."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from gridlab.strategy_files import StrategyFileConflict, StrategyFileError, StrategyFiles


SOURCE = '''"""Simple trend strategy."""
NAME = "Trend"
SETTINGS = {"threshold": 1.25}
WARMUP = 0

def on_bar(ctx):
    return []
'''


class StrategyFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "strategies"
        self.files = StrategyFiles(self.directory)

    def test_save_read_and_hash_guarded_update(self):
        saved = self.files.save("trend.py", SOURCE)
        self.assertEqual(saved["name"], "Trend")
        self.assertEqual(saved["description"], "Simple trend strategy.")
        self.assertEqual(saved["settings"], {"threshold": 1.25})
        self.assertEqual(saved["warmup"], 0)
        self.assertEqual(saved["hash"], hashlib.sha256(SOURCE.encode()).hexdigest())
        updated = self.files.save("trend.py", SOURCE + "\n# update\n", saved["hash"])
        self.assertNotEqual(updated["hash"], saved["hash"])
        with self.assertRaises(StrategyFileConflict):
            self.files.save("trend.py", SOURCE, saved["hash"])
        self.assertEqual(self.files.list()[0]["source"], SOURCE + "\n# update\n")

    def test_rejects_bad_names_source_and_symbolic_links(self):
        for name in ("../escape.py", "sub/escape.py", ".hidden.py", "plain.txt"):
            with self.subTest(name=name), self.assertRaises(StrategyFileError):
                self.files.save(name, SOURCE)
        with self.assertRaises(StrategyFileError):
            self.files.save("bad.py", "def broken(:\n")
        outside = Path(self.temp.name) / "outside.py"
        outside.write_text(SOURCE)
        (self.directory / "linked.py").symlink_to(outside)
        with self.assertRaises(StrategyFileError):
            self.files.get("linked.py")
        self.assertEqual(self.files.list(), [])

    def test_rejects_async_callback_and_non_json_settings(self):
        with self.assertRaises(StrategyFileError):
            self.files.save("async.py", 'async def on_bar(ctx):\n    return []\n')
        with self.assertRaises(StrategyFileError):
            self.files.save("bad-settings.py", 'SETTINGS = {1, 2}\ndef on_bar(ctx):\n    return []\n')
