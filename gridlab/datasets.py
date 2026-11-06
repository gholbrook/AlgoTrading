"""Read timestamped local candle snapshots once and identify their exact contents."""
import csv
import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from io import StringIO
from pathlib import Path
import re

from gridlab.market import validate_bar

def iso_timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


INTERVAL = {"5m": 300, "1h": 3600}
SAMPLE = "BTC_USD_5m"


def utc_iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


class Dataset:
    def __init__(self, path, metadata=None, *, discard_last=True):
        if metadata is None:
            match = re.fullmatch(r"([A-Z0-9]+)_([A-Z0-9]+)_(5m|1h)", path.stem)
            if not match:
                raise ValueError(f"Unsupported dataset filename {path.name}")
            base, quote, self.timeframe = match.groups()
            self.market = f"{base}/{quote}"
            self.id = path.stem
            self.venue = None
            self.provenance = None
        else:
            self.id = metadata["id"]
            self.market = metadata["market"]
            self.timeframe = metadata["timeframe"]
            self.venue = metadata.get("venue")
            self.provenance = metadata.get("provider")
        raw = path.read_bytes()
        self.fingerprint = sha256(raw).hexdigest()
        self.rows = []
        reader = csv.DictReader(StringIO(raw.decode("utf-8-sig")))
        # Snapshot exporters can include the forming candle. Its finality cannot
        # be established from a static file, so always discard the last row.
        if not {"timestamp", "open", "high", "low", "close", "volume"}.issubset(reader.fieldnames or []):
            raise ValueError(f"{path.name} is missing timestamp or OHLCV columns")
        source_rows = list(reader)
        if discard_last:
            source_rows = source_rows[:-1]
        for row in source_rows:
            stamp = datetime.fromisoformat(row.pop("timestamp"))
            if stamp.utcoffset() is None:
                raise ValueError(f"{path.name} needs timestamps with a timezone")
            bar = validate_bar({**row, "ts": stamp.timestamp()})
            interval = INTERVAL[self.timeframe]
            if bar["ts"] % interval or (self.rows and bar["ts"] != self.rows[-1]["ts"] + interval):
                raise ValueError(f"{path.name} has an unaligned or missing candle at {stamp}")
            self.rows.append(bar)
        if not self.rows:
            raise ValueError(f"{path.name} needs at least one closed candle")

    def describe(self):
        result = {"id": self.id, "market": self.market, "timeframe": self.timeframe,
                  "bars": len(self.rows), "start": utc_iso(self.rows[0]["ts"]),
                  "end": utc_iso(self.rows[-1]["ts"]), "sha256": self.fingerprint}
        if self.venue:
            result["venue"] = self.venue
        return result

    def source(self):
        if self.provenance:
            return f"{self.provenance} · {self.market} {self.timeframe} · sha256 {self.fingerprint}"
        return f"{self.id}.csv · sha256 {self.fingerprint}"

    def select(self, start=None, end=None, *, warmup_count=205):
        try:
            lower = datetime.combine(date.fromisoformat(start), time.min, timezone.utc).timestamp() if start else 0
            upper = datetime.combine(date.fromisoformat(end) + timedelta(days=1), time.min, timezone.utc).timestamp() if end else float("inf")
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Use a UTC date in YYYY-MM-DD format.") from exc
        if lower >= upper:
            raise ValueError("Start date must be on or before the end date.")
        eligible = [bar for bar in self.rows if bar["ts"] < lower]
        warmup = eligible[-warmup_count:] if warmup_count else []
        selected = [bar for bar in self.rows if lower <= bar["ts"] < upper]
        if not selected:
            raise ValueError("The selected dates contain no candles in this snapshot.")
        return warmup, selected


class LazyDataset:
    """Metadata-only snapshot reference; opening verifies contents without caching rows."""
    def __init__(self, path, metadata):
        self.path = Path(path)
        self.metadata = dict(metadata)
        self.id = metadata["id"]
        self.market = metadata["market"]
        self.timeframe = metadata["timeframe"]
        self.venue = metadata.get("venue")
        self.provenance = metadata.get("provider")
        self.fingerprint = metadata["sha256"]
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("invalid snapshot id")
        if not isinstance(self.timeframe, str) or self.timeframe not in INTERVAL or not re.fullmatch(r"[A-Z0-9]+/[A-Z0-9]+", self.market):
            raise ValueError("invalid snapshot market or timeframe")
        if not isinstance(self.fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", self.fingerprint):
            raise ValueError("invalid snapshot checksum")
        if type(metadata["bars"]) is not int or metadata["bars"] < 1:
            raise ValueError("invalid snapshot row count")
        bounds = []
        for key in ("start", "end"):
            if not isinstance(metadata[key], str):
                raise ValueError("invalid snapshot bounds")
            stamp = datetime.fromisoformat(metadata[key].replace("Z", "+00:00"))
            if stamp.utcoffset() is None or stamp.timestamp() % INTERVAL[self.timeframe]:
                raise ValueError("invalid snapshot bounds")
            bounds.append(stamp.timestamp())
        if bounds[1] - bounds[0] != (metadata["bars"] - 1) * INTERVAL[self.timeframe]:
            raise ValueError("snapshot bounds do not match row count")

    def describe(self):
        result = {key: self.metadata[key] for key in
                  ("id", "market", "timeframe", "bars", "start", "end", "sha256")}
        if self.venue:
            result["venue"] = self.venue
        return result

    def source(self):
        return Dataset.source(self)

    def open(self):
        try:
            data = Dataset(self.path, self.metadata, discard_last=False)
        except (OSError, csv.Error) as exc:
            raise ValueError(f"Cannot open snapshot {self.id}: {exc}") from exc
        if data.fingerprint != self.fingerprint:
            raise ValueError("snapshot checksum does not match provenance")
        if len(data.rows) != self.metadata["bars"]:
            raise ValueError("snapshot row count does not match provenance")
        for key, row in (("start", data.rows[0]), ("end", data.rows[-1])):
            expected = datetime.fromisoformat(self.metadata[key].replace("Z", "+00:00")).timestamp()
            if row["ts"] != expected:
                raise ValueError("snapshot bounds do not match provenance")
        return data

    @property
    def rows(self):
        return self.open().rows

    def select(self, *args, **kwargs):
        return self.open().select(*args, **kwargs)


def load_bulk_datasets(history_dir):
    """Discover archive snapshots from sidecars without reading candle files."""
    return load_history_datasets(history_dir, bulk_only=True)


def load_history_datasets(history_dir, *, bulk_only=False):
    """Load immutable exchange snapshots using their adjacent provenance files."""
    catalog = {}
    if not history_dir or not Path(history_dir).is_dir():
        return catalog
    for pattern in ("coinbase_*.json", "binance_*.json"):
        for sidecar in sorted(Path(history_dir).glob(pattern)):
            try:
                metadata = json.loads(sidecar.read_text(encoding="utf-8"))
                if not isinstance(metadata, dict):
                    raise ValueError("snapshot provenance must be an object")
                filename = metadata.get("filename")
                if not isinstance(filename, str) or Path(filename).name != filename:
                    raise ValueError("invalid snapshot filename")
                path = Path(history_dir) / filename
                if not path.is_file():
                    raise ValueError("snapshot file is missing")
                is_bulk = sidecar.name.startswith("binance_spot_monthly_")
                if bulk_only and not is_bulk:
                    continue
                if is_bulk:
                    dataset = LazyDataset(path, metadata)
                    catalog[dataset.id] = dataset
                    continue
                dataset = Dataset(path, metadata, discard_last=False)
                if dataset.fingerprint != metadata.get("sha256"):
                    raise ValueError("snapshot checksum does not match provenance")
                if len(dataset.rows) != metadata.get("bars"):
                    raise ValueError("snapshot row count does not match provenance")
                catalog[dataset.id] = dataset
            except (ValueError, TypeError, OSError, KeyError) as exc:
                logging.warning("Skipping historical snapshot %s: %s", sidecar.name, exc)
    return catalog


def load_datasets(data_dir, history_dir=None):
    example = Path(__file__).resolve().parents[1] / "examples" / f"{SAMPLE}.csv"
    catalog = {SAMPLE: Dataset(example)}
    for path in sorted(Path(data_dir).glob("*.csv")):
        if re.fullmatch(r"[A-Z0-9]+_[A-Z0-9]+_(?:5m|1h)", path.stem):
            try:
                catalog[path.stem] = Dataset(path)
            except (ValueError, TypeError, OSError, csv.Error) as exc:
                # A bad import must not prevent existing paper accounts from resuming.
                logging.warning("Skipping %s: %s", path, exc)
    catalog.update(load_history_datasets(history_dir))
    return catalog


class CombinedDataset(Dataset):
    """Metadata-only contiguous view over immutable source snapshots."""
    def __init__(self, members):
        self.members = tuple(members)
        self.market, self.timeframe, self.venue = members[0].market, members[0].timeframe, members[0].venue
        self.provenance = self.venue or members[0].provenance or "Local history"
        identity = json.dumps([(d.id, d.fingerprint) for d in members], separators=(",", ":"))
        self.fingerprint = sha256(identity.encode()).hexdigest()
        self.id = "combined_" + self.fingerprint[:24]
        descriptions = [member.describe() for member in members]
        self.start = utc_iso(min(iso_timestamp(d["start"]) for d in descriptions))
        self.end = utc_iso(max(iso_timestamp(d["end"]) for d in descriptions))

    def describe(self):
        bars = int((datetime.fromisoformat(self.end).timestamp() -
                    datetime.fromisoformat(self.start).timestamp()) / INTERVAL[self.timeframe]) + 1
        result = {"id": self.id, "market": self.market, "timeframe": self.timeframe,
                  "bars": bars, "start": self.start, "end": self.end,
                  "sha256": self.fingerprint, "snapshots": [d.id for d in self.members]}
        if self.venue:
            result["venue"] = self.venue
        return result

    def open(self):
        rows = {}
        for member in self.members:
            data = member.open() if isinstance(member, LazyDataset) else member
            for row in data.rows:
                if row["ts"] in rows and rows[row["ts"]] != row:
                    raise ValueError("Combined snapshot overlap changed.")
                rows[row["ts"]] = row
        data = object.__new__(Dataset)
        data.__dict__.update(self.__dict__)
        data.rows = [rows[t] for t in sorted(rows)]
        return data

    @property
    def rows(self):
        return self.open().rows

    def select(self, *args, **kwargs):
        return self.open().select(*args, **kwargs)


def combine_datasets(catalog):
    """Expose maximal compatible ranges, retaining conflicts as separate choices.

    Adjacent snapshots can be joined from metadata alone; overlapping snapshots
    must agree on every shared bar before they can share a selectable range.
    """
    groups = {}
    for data in catalog.values():
        groups.setdefault((data.venue, data.market, data.timeframe), []).append(data)
    result = {}
    for members in groups.values():
        members.sort(key=lambda d: (iso_timestamp(d.describe()["start"]), iso_timestamp(d.describe()["end"]), d.id))
        ranges = []
        for candidate in members:
            desc = candidate.describe()
            start = datetime.fromisoformat(desc["start"]).timestamp()
            joined = False
            for index, current in enumerate(ranges):
                end = datetime.fromisoformat(current.describe()["end"]).timestamp()
                if start > end + INTERVAL[candidate.timeframe]:
                    continue
                sources = list(current.members) if isinstance(current, CombinedDataset) else [current]
                compatible = True
                for member in sources:
                    bounds = member.describe()
                    if iso_timestamp(bounds["end"]) < iso_timestamp(desc["start"]) or iso_timestamp(bounds["start"]) > iso_timestamp(desc["end"]):
                        continue
                    left = member.open() if isinstance(member, LazyDataset) else member
                    right = candidate.open() if isinstance(candidate, LazyDataset) else candidate
                    existing = {row["ts"]: row for row in left.rows}
                    if any(row["ts"] in existing and existing[row["ts"]] != row for row in right.rows):
                        compatible = False
                        break
                if not compatible:
                    continue
                ranges[index] = CombinedDataset(sources + [candidate])
                joined = True
                break
            if not joined:
                ranges.append(candidate)
        result.update((data.id, data) for data in ranges)
    return result
