"""Append-only local shadow collection; trust comes from independent signing."""

import argparse
import json
import os
import re
import stat
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

from dragon_radar.production.gates import exact

ENTRY_FIELDS = {
    "trade_date",
    "calendar_version",
    "versions",
    "publication_id",
    "publication_sequence",
    "input_sha256",
    "source_received_at",
    "available_at",
    "started_at",
    "finished_at",
    "recorded_at",
    "real",
    "status",
    "errors",
    "worker",
    "reviewer",
    "reviewed_at",
    "metrics",
    "previous_entry_hash",
    "entry_hash",
}
HASH = re.compile("[0-9a-f]{64}")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("shadow timestamp invalid")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("shadow timestamp must be UTC")
    return parsed


def entry_hash(entry):
    from dragon_radar.production.trust import canonical_json

    unsigned = {key: value for key, value in entry.items() if key != "entry_hash"}
    return sha256(canonical_json(unsigned)).hexdigest()


def validate_shadow(calendar, records, candidate, now):
    """Verify signed historic evidence, never count replayed historic backtests."""
    exact(calendar, {"version", "sessions"})
    exact(records, {"entries"})
    sessions = calendar["sessions"]
    entries = records["entries"]
    if (
        not isinstance(sessions, list)
        or not sessions
        or len(sessions) > 10000
        or not isinstance(entries, list)
        or not 20 <= len(entries) <= 250
    ):
        raise ValueError("at least 20 bounded genuine trading days required")
    dates, cutoffs = [], {}
    for session in sessions:
        exact(session, {"trade_date", "close_at"})
        day = date.fromisoformat(session["trade_date"])
        close = timestamp(session["close_at"])
        if close.astimezone(SHANGHAI).date() != day:
            raise ValueError("calendar close date mismatch")
        if dates and day <= dates[-1]:
            raise ValueError("approved calendar must be ordered and unique")
        dates.append(day)
        cutoffs[day] = close
    previous_hash, previous_day, previous_sequence = "0" * 64, None, 0
    snapshots, inputs = set(), set()
    for entry in entries:
        exact(entry, ENTRY_FIELDS)
        day = date.fromisoformat(entry["trade_date"])
        if day not in cutoffs or cutoffs[day] > now:
            raise ValueError("unfinished or unknown shadow trading day")
        if previous_day is not None:
            if dates.index(day) != dates.index(previous_day) + 1:
                raise ValueError("shadow trading day gap or duplicate")
        if entry["calendar_version"] != calendar["version"]:
            raise ValueError("shadow calendar version drift")
        from dragon_radar.production.trust import CANDIDATE_FIELDS

        if entry["versions"] != {key: candidate[key] for key in CANDIDATE_FIELDS}:
            raise ValueError("shadow semantic version drift")
        if (
            entry["previous_entry_hash"] != previous_hash
            or not isinstance(entry["entry_hash"], str)
            or HASH.fullmatch(entry["entry_hash"]) is None
            or entry["entry_hash"] != entry_hash(entry)
        ):
            raise ValueError("shadow entry chain invalid")
        if (
            entry["real"] is not True
            or entry["status"] != "completed"
            or entry["errors"] != []
        ):
            raise ValueError("shadow genuine completed run required")
        if (
            not isinstance(entry["publication_id"], str)
            or not entry["publication_id"]
            or entry["publication_id"] in snapshots
            or type(entry["publication_sequence"]) is not int
            or entry["publication_sequence"] <= previous_sequence
            or not isinstance(entry["input_sha256"], str)
            or HASH.fullmatch(entry["input_sha256"]) is None
            or entry["input_sha256"] in inputs
        ):
            raise ValueError("shadow duplicate or invalid publication/input")
        received, available, started, finished, recorded, reviewed = (
            timestamp(entry[field])
            for field in (
                "source_received_at",
                "available_at",
                "started_at",
                "finished_at",
                "recorded_at",
                "reviewed_at",
            )
        )
        day_start = datetime.combine(day, time.min, SHANGHAI).astimezone(UTC)
        day_end = day_start + timedelta(days=1)
        if not (
            day_start
            <= received
            <= available
            <= started
            <= finished
            <= reviewed
            <= recorded
            < day_end
            and cutoffs[day] <= started
            and recorded <= now
        ):
            raise ValueError("shadow day was backfilled or has late input")
        if (
            not isinstance(entry["worker"], str)
            or not entry["worker"]
            or not isinstance(entry["reviewer"], str)
            or not entry["reviewer"]
            or entry["worker"] == entry["reviewer"]
        ):
            raise ValueError("independent shadow review required")
        metrics = exact(
            entry["metrics"],
            {
                "p95_seconds",
                "writes_on_get",
                "quality_failures",
            },
        )
        if (
            type(metrics["p95_seconds"]) not in {int, float}
            or not 0 <= metrics["p95_seconds"] <= 2.0
            or type(metrics["writes_on_get"]) is not int
            or metrics["writes_on_get"] != 0
            or type(metrics["quality_failures"]) is not int
            or metrics["quality_failures"] != 0
        ):
            raise ValueError("shadow quality or performance failed")
        previous_hash, previous_day = entry["entry_hash"], day
        previous_sequence = entry["publication_sequence"]
        snapshots.add(entry["publication_id"])
        inputs.add(entry["input_sha256"])
    return {"trading_days": len(entries), "chain_sha256": previous_hash}


def _no_links(path):
    for item in (path, *path.parents):
        if not item.exists() and not item.is_symlink():
            continue
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("shadow storage link forbidden")


class ShadowCollector:
    """Persist raw records without replacement; no permission to declare pass.

    Collector artifacts remain untrusted until independently reviewed and
    signed. Failed days are retained and cannot be overwritten with successes.
    """

    def __init__(self, root: Path):
        self.root = Path(root).absolute()
        _no_links(self.root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def read(self):
        from dragon_radar.production.trust import strict_json

        _no_links(self.root)
        entries = []
        paths = sorted(self.root.glob("????-??-??.json"))
        if len(paths) > 10000:
            raise ValueError("shadow storage count exceeded")
        for path in paths:
            _no_links(path)
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("shadow entry too large")
            record = strict_json(path.read_bytes())
            exact(record, ENTRY_FIELDS)
            if (
                path.stem != record["trade_date"]
                or entry_hash(record) != record["entry_hash"]
            ):
                raise ValueError("shadow stored entry invalid")
            entries.append(record)
        return {"entries": entries}

    def append(self, record):
        from dragon_radar.production.trust import canonical_json

        exact(record, ENTRY_FIELDS)
        day = date.fromisoformat(record["trade_date"])
        if (
            str(day) != record["trade_date"]
            or entry_hash(record) != record["entry_hash"]
        ):
            raise ValueError("shadow entry identity or hash invalid")
        content = canonical_json(record)
        if len(content) > 1024 * 1024:
            raise ValueError("shadow entry too large")
        _no_links(self.root)
        lock = self.root / ".append.lock"
        _no_links(lock)
        try:
            lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ValueError("shadow collector already locked") from None
        try:
            records = self.read()["entries"]
            for existing in records:
                if existing["trade_date"] == str(day):
                    if existing != record:
                        raise ValueError("shadow day conflict cannot overwrite")
                    return existing["entry_hash"]
            previous = records[-1]["entry_hash"] if records else "0" * 64
            if (
                record["previous_entry_hash"] != previous
                or records
                and day <= date.fromisoformat(records[-1]["trade_date"])
            ):
                raise ValueError("shadow append chain order invalid")
            path = self.root / f"{day}.json"
            _no_links(path)
            from dragon_radar.owner.files import secure_output

            with secure_output(path) as stream:
                stream.write(content)
            return record["entry_hash"]
        finally:
            os.close(lock_fd)
            lock.unlink()


def main(argv=None):
    """Collect original local records; independent signing remains mandatory."""
    from dragon_radar.production.trust import EvidenceError, read_asset, strict_json

    parser = argparse.ArgumentParser(
        description="Append original shadow records locally"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--record", type=Path)
    source.add_argument("--history", type=Path)
    parser.add_argument("--store", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        path = (args.record or args.history).absolute()
        value = strict_json(read_asset(path.parent, path.name, 16 * 1024 * 1024))
        if args.history is not None:
            exact(value, {"entries"})
            entries = value["entries"]
            if not isinstance(entries, list) or not 1 <= len(entries) <= 250:
                raise ValueError("shadow history count invalid")
        else:
            entries = [value]
        collector = ShadowCollector(args.store)
        last = None
        for record in entries:
            last = collector.append(record)
        print(
            json.dumps(
                {
                    "trust": "UNVERIFIED_COLLECTION",
                    "collected": len(entries),
                    "chain_sha256": last,
                },
                sort_keys=True,
            )
        )
        return 0
    except (EvidenceError, ValueError, OSError, TypeError, KeyError):
        print(
            json.dumps({"trust": "UNVERIFIED_COLLECTION", "error": "collection_failed"})
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
