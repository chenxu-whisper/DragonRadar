import importlib
import json
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256

import pytest


def module():
    assert importlib.util.find_spec("dragon_radar.production.shadow"), (
        "append-only shadow collector missing"
    )
    return importlib.import_module("dragon_radar.production.shadow")


def digest(value):
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


NOW = datetime(2026, 11, 16, 12, tzinfo=UTC)
VERSIONS = {
    "code": "a" * 40,
    "dataset": "d1",
    "rules": "r1",
    "model": "m1",
    "prompt": "p1",
    "retrieval": "r1",
}


def history():
    days, entries = [], []
    day = date(2026, 10, 1)
    while len(days) < 20:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    previous = "0" * 64
    for i, day in enumerate(days):
        record = {
            "trade_date": day.isoformat(),
            "calendar_version": "c1",
            "versions": VERSIONS,
            "publication_id": f"snapshot-{i}",
            "publication_sequence": i + 1,
            "input_sha256": f"{i + 1:064x}",
            "source_received_at": f"{day}T07:01:00Z",
            "available_at": f"{day}T07:02:00Z",
            "started_at": f"{day}T08:00:00Z",
            "finished_at": f"{day}T08:01:00Z",
            "recorded_at": f"{day}T08:02:00Z",
            "real": True,
            "status": "completed",
            "errors": [],
            "worker": "worker",
            "reviewer": "human",
            "reviewed_at": f"{day}T08:02:00Z",
            "metrics": {"p95_seconds": 1.0, "writes_on_get": 0, "quality_failures": 0},
            "previous_entry_hash": previous,
        }
        previous = digest(record)
        entries.append({**record, "entry_hash": previous})
    calendar = {
        "version": "c1",
        "sessions": [
            {"trade_date": str(day), "close_at": f"{day}T07:00:00Z"} for day in days
        ],
    }
    return calendar, {"entries": entries}


def test_genuine_contiguous_days_validate_and_count():
    calendar, entries = history()
    assert (
        module().validate_shadow(calendar, entries, VERSIONS, NOW)["trading_days"] == 20
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "replay",
        "backfill",
        "drift",
        "failure",
        "tamper",
        "gap",
        "late_input",
        "unreviewed",
        "duplicate_snapshot",
        "future_day",
    ],
)
def test_shadow_cannot_be_filled_by_replay_or_backdated_records(mutation):
    calendar, records = history()
    entries = records["entries"]
    if mutation == "replay":
        entries.append(entries[-1])
    elif mutation == "gap":
        del entries[5]
    else:
        if mutation == "backfill":
            entries[-1]["recorded_at"] = NOW.isoformat()
        elif mutation == "drift":
            entries[-1]["versions"] = {**VERSIONS, "model": "m2"}
        elif mutation == "failure":
            entries[-1]["status"] = "failed"
        elif mutation == "tamper":
            entries[-1]["input_sha256"] = "f" * 64
        elif mutation == "late_input":
            entries[-1]["available_at"] = entries[-1]["finished_at"]
        elif mutation == "unreviewed":
            entries[-1]["reviewer"] = entries[-1]["worker"]
        elif mutation == "duplicate_snapshot":
            entries[-1]["publication_id"] = entries[0]["publication_id"]
        elif mutation == "future_day":
            calendar["sessions"][-1]["close_at"] = "2027-01-01T07:00:00Z"
        if mutation != "tamper":
            unsigned = {k: v for k, v in entries[-1].items() if k != "entry_hash"}
            entries[-1]["entry_hash"] = digest(unsigned)
    with pytest.raises(ValueError):
        module().validate_shadow(calendar, records, VERSIONS, NOW)


def test_append_is_idempotent_conflicting_day_cannot_overwrite(tmp_path):
    _, records = history()
    collector = module().ShadowCollector(tmp_path)
    first = records["entries"][0]
    assert collector.append(first) == first["entry_hash"]
    assert collector.append(first) == first["entry_hash"]
    changed = {**first, "status": "failed"}
    with pytest.raises(ValueError):
        collector.append(changed)
    assert collector.read() == {"entries": [first]}


def test_collector_cli_never_claims_production_approval(tmp_path, capsys):
    assert hasattr(module(), "main"), "local shadow CLI missing"
    _, records = history()
    from dragon_radar.owner.files import secure_output
    from dragon_radar.production.trust import canonical_json

    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    record = root / "entry.json"
    with secure_output(record) as stream:
        stream.write(canonical_json(records["entries"][0]))
    if __import__("os").name == "nt":
        from test_production_trust import protect

        protect(root)
    assert module().main(["--record", str(record), "--store", str(root / "store")]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["trust"] == "UNVERIFIED_COLLECTION"
    assert "pass" not in report.values()
