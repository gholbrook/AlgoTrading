import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gridlab.datasets import Dataset, LazyDataset, combine_datasets
from gridlab.server import Workbench


class CatalogTests(unittest.TestCase):
    def snapshot(self, root, name, timestamps, *, venue="Binance", bump=0):
        path = root / (name + ".csv")
        path.write_text("timestamp,open,high,low,close,volume\n" + "".join(
            f"2026-01-01T{t // 60:02}:{t % 60:02}:00+00:00,{10+bump},{11+bump},{9+bump},{10+bump},1\n"
            for t in timestamps))
        return Dataset(path, {"id": name, "market": "SOL/USDT", "timeframe": "5m", "venue": venue}, discard_last=False)

    def test_adjacent_and_identical_overlap_join_but_conflicts_and_gaps_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = self.snapshot(root, "a", [0, 5, 10])
            b = self.snapshot(root, "b", [10, 15, 20])
            c = self.snapshot(root, "c", [25, 30])
            before = (root / "a.csv").read_bytes()
            combined, = combine_datasets({d.id: d for d in [a, b, c]}).values()
            self.assertEqual(len(combined.rows), 7)
            self.assertEqual(combined.describe()["snapshots"], ["a", "b", "c"])
            self.assertEqual((root / "a.csv").read_bytes(), before)
            conflict = self.snapshot(root, "conflict", [10, 15], bump=1)
            gap = self.snapshot(root, "gap", [40, 45])
            venue = self.snapshot(root, "venue", [35], venue="Coinbase")
            self.assertEqual(len(combine_datasets({d.id: d for d in [a, b, conflict, gap, venue]})), 4)

    def test_adjacent_lazy_members_are_not_opened_for_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            originals = [self.snapshot(root, "a", [0, 5]), self.snapshot(root, "b", [10, 15])]
            members = [LazyDataset(root / (d.id + ".csv"), d.describe()) for d in originals]
            with patch.object(LazyDataset, "open", side_effect=AssertionError("opened")):
                combined, = combine_datasets({d.id: d for d in members}).values()
                self.assertEqual(combined.describe()["bars"], 4)
            self.assertEqual(len(combined.open().rows), 4)

    def test_timezone_suffixes_do_not_hide_conflicting_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = self.snapshot(root, "a", [0, 5, 10])
            b = self.snapshot(root, "b", [10, 15], bump=1)
            metadata = b.describe()
            metadata["start"] = metadata["start"].replace("+00:00", "Z")
            metadata["end"] = metadata["end"].replace("+00:00", "Z")
            lazy = LazyDataset(root / "b.csv", metadata)
            self.assertEqual(len(combine_datasets({"a": a, "b": lazy})), 2)

    def test_context_refresh_does_not_reload_unchanged_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Workbench(Path(tmp) / "state.sqlite", {})
            with patch("gridlab.server.load_history_datasets", side_effect=AssertionError("reloaded")):
                app.context()
                app.context()


if __name__ == "__main__":
    unittest.main()
