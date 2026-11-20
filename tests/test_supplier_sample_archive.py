"""Immutable engineering samples: exact bytes, current rights, no production trust."""

import importlib
import json
import os
import subprocess
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256

import pytest
from test_supplier_archive_sample_cli import inputs as inputs
from test_supplier_collection import NOW
from test_supplier_collection import root as root

from dragon_radar.domain.models import SourceGrant
from dragon_radar.owner.files import secure_output
from dragon_radar.production.trust import EvidenceError, canonical_json


def service():
    return importlib.import_module("dragon_radar.ingestion.archive_sample")


def archive_module():
    return importlib.import_module("dragon_radar.ingestion.sample_archive")


def evaluate(inputs):
    raw = {
        key: path.read_bytes()
        for key, path in inputs[0].items()
        if key != "archive-root"
    }
    return service().evaluate_archive(
        raw, archive_root=inputs[0]["archive-root"], clock=lambda: NOW
    )


def save_raw(path, raw):
    with secure_output(path) as stream:
        stream.write(raw)


def read(path, raw, grant, clock=lambda: NOW):
    return archive_module().read_sample_archive(
        path, expected_sha256=sha256(raw).hexdigest(), grant=grant, clock=clock
    )


def test_roundtrip_keeps_full_sample_receipts_exact_values_and_lineage(inputs):
    result = evaluate(inputs)
    assert result.report["status"] == "pass"
    package = result.archive
    mod = archive_module()
    raw = mod.archive_bytes(package, clock=lambda: NOW)
    path = inputs[0]["sample"].parent / "normalized.json"
    assert (
        mod.write_sample_archive(path, package, clock=lambda: NOW)
        == sha256(raw).hexdigest()
    )
    loaded = read(path, raw, package.sample.batch.grant)
    assert loaded == package
    assert loaded.sample.batch.bars[0].close.as_tuple() == (
        package.sample.batch.bars[0].close.as_tuple()
    )
    assert loaded.sample.batch.calendar == package.sample.batch.calendar
    assert loaded.sample.batch.bars[0].available_at == NOW
    assert loaded.sample.batch.as_of == NOW
    assert loaded.sample.batch.complete_pages is True
    assert loaded.lineage.collection_fingerprint == inputs[1].fingerprint
    assert loaded.lineage.chunks[0].raw_sha256 == inputs[1].chunks[0].raw_sha256
    assert loaded.lineage.chunks[0].first_received_at == NOW
    assert loaded.production_approved is False
    assert loaded.historical_pit_status == "unverified"
    for name in ("request", "grant", "extraction"):
        assert (
            getattr(loaded.lineage, name + "_sha256")
            == sha256(inputs[0][name].read_bytes()).hexdigest()
        )
    assert b"PRIVATE_TEST_TOKEN" not in raw
    assert b"raw_path" not in raw
    assert b'"data"' not in raw
    assert canonical_json(json.loads(raw)) == raw


@pytest.mark.parametrize("inputs", ["missing_history", "bad_row"], indirect=True)
def test_failed_or_blocked_mapping_returns_no_archive(inputs):
    result = evaluate(inputs)
    assert result.report["status"] != "pass"
    assert result.archive is None


def test_digest_mismatch_and_existing_target_fail_without_overwrite(inputs):
    mod = archive_module()
    package = evaluate(inputs).archive
    path = inputs[0]["sample"].parent / "normalized.json"
    digest = mod.write_sample_archive(path, package, clock=lambda: NOW)
    before = path.read_bytes()
    with pytest.raises(mod.SampleArchiveError):
        mod.write_sample_archive(path, package, clock=lambda: NOW)
    with pytest.raises(mod.SampleArchiveError):
        mod.read_sample_archive(
            path,
            expected_sha256="0" * 64,
            grant=package.sample.batch.grant,
            clock=lambda: NOW,
        )
    assert path.read_bytes() == before
    assert digest == sha256(before).hexdigest()


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(production_approved=True),
        lambda p: p.update(historical_pit_status="verified"),
        lambda p: p.update(secret="SECRET_VALUE"),
        lambda p: p["sample"]["batch"].update(provenance="contemporaneous"),
        lambda p: p["sample"]["batch"].update(complete_pages=False),
        lambda p: p["sample"]["batch"]["bars"][0].update(
            available_at="2020-01-01T00:00:00Z"
        ),
        lambda p: p["lineage"]["chunks"][0].update(index=2),
        lambda p: p["lineage"]["chunks"][0].update(raw_sha256="SECRET_VALUE"),
        lambda p: p["lineage"]["chunks"][0].update(
            first_received_at="2099-01-01T00:00:00Z"
        ),
        lambda p: p["lineage"].update(chunks=[]),
    ],
)
def test_corrupt_contract_rejected_even_with_matching_digest(inputs, change):
    package = evaluate(inputs).archive
    payload = package.model_dump(mode="json", by_alias=True)
    change(payload)
    raw = canonical_json(payload)
    path = inputs[0]["sample"].parent / "tampered.json"
    save_raw(path, raw)
    with pytest.raises(archive_module().SampleArchiveError) as error:
        read(path, raw, package.sample.batch.grant)
    assert "SECRET_VALUE" not in str(error.value)


@pytest.mark.parametrize("kind", ["whitespace", "duplicate", "empty", "budget"])
def test_noncanonical_duplicate_empty_or_oversized_bytes_fail(inputs, kind):
    package = evaluate(inputs).archive
    raw = archive_module().archive_bytes(package, clock=lambda: NOW)
    raw = {
        "whitespace": raw + b"\n",
        "duplicate": b'{"secret":"SECRET_VALUE","secret":true}',
        "empty": b"",
        "budget": b" " * (16 * 1024 * 1024 + 1),
    }[kind]
    path = inputs[0]["sample"].parent / "invalid.json"
    save_raw(path, raw)
    with pytest.raises(archive_module().SampleArchiveError):
        read(path, raw, package.sample.batch.grant)


@pytest.mark.parametrize("phase", ["before", "after", "changed"])
def test_read_requires_current_unchanged_authorization(inputs, phase):
    package = evaluate(inputs).archive
    mod = archive_module()
    raw = mod.archive_bytes(package, clock=lambda: NOW)
    path = inputs[0]["sample"].parent / "normalized.json"
    save_raw(path, raw)
    grant = package.sample.batch.grant
    if phase == "changed":
        grant = SourceGrant.model_validate(grant.model_dump() | {"reference": "OTHER"})
    times = iter((NOW, NOW + timedelta(days=2)))
    clock = (
        (lambda: NOW + timedelta(days=2))
        if phase == "before"
        else (lambda: next(times))
        if phase == "after"
        else (lambda: NOW)
    )
    with pytest.raises(mod.SampleArchiveError):
        read(path, raw, grant, clock)


def test_expired_write_creates_no_file(inputs):
    package = evaluate(inputs).archive
    path = inputs[0]["sample"].parent / "normalized.json"
    with pytest.raises(archive_module().SampleArchiveError):
        archive_module().write_sample_archive(
            path, package, clock=lambda: NOW + timedelta(days=2)
        )
    assert not path.exists()


def test_read_rejects_hardlink(inputs):
    package = evaluate(inputs).archive
    raw = archive_module().archive_bytes(package, clock=lambda: NOW)
    path = inputs[0]["sample"].parent / "normalized.json"
    save_raw(path, raw)
    alias = path.with_name("alias.json")
    os.link(path, alias)
    with pytest.raises(archive_module().SampleArchiveError):
        read(path, raw, package.sample.batch.grant)


def test_read_rejects_public_file(inputs):
    package = evaluate(inputs).archive
    raw = archive_module().archive_bytes(package, clock=lambda: NOW)
    path = inputs[0]["sample"].parent / "public.json"
    save_raw(path, raw)
    if os.name == "nt":
        subprocess.run(
            ["icacls.exe", str(path), "/grant", "*S-1-1-0:(R)"],
            check=True,
            capture_output=True,
            timeout=20,
        )
    else:
        path.chmod(0o644)
    with pytest.raises(archive_module().SampleArchiveError):
        read(path, raw, package.sample.batch.grant)


def test_report_only_service_does_not_apply_archive_serialization_budget(
    inputs, monkeypatch
):
    # Scale down only the archive encoder's ceiling; configurations still fit.
    # A serialization failure must affect explicit persistence, not diagnostics.
    def bounded(value):
        content = canonical_json(value)
        if value.get("schema") == "dragon-radar-normalized-sample-archive/v1":
            raise EvidenceError()
        return content

    monkeypatch.setattr(service(), "canonical_json", bounded, raising=False)
    result = evaluate(inputs)
    assert result.report["status"] == "pass"
    assert result.archive is not None
    mod = archive_module()
    monkeypatch.setattr(mod, "MAX_ARCHIVE_BYTES", 1)
    target = inputs[0]["sample"].parent / "too-large.json"
    with pytest.raises(mod.SampleArchiveError):
        mod.write_sample_archive(target, result.archive, clock=lambda: NOW)
    assert not target.exists()


def test_service_rechecks_authorization_after_quality_evaluation(inputs, monkeypatch):
    mod = service()
    expired = False
    original = mod.evaluate_sample

    def expire(request, *, now):
        nonlocal expired
        report = original(request, now=now)
        expired = True
        return report

    monkeypatch.setattr(mod, "evaluate_sample", expire)
    raw = {
        key: path.read_bytes()
        for key, path in inputs[0].items()
        if key != "archive-root"
    }
    result = mod.evaluate_archive(
        raw,
        archive_root=inputs[0]["archive-root"],
        clock=lambda: NOW + timedelta(days=2) if expired else NOW,
    )
    assert result.report["status"] == "blocked"
    assert result.archive is None
    assert result.report["issues"][0]["code"] == "storage_denied"


@pytest.mark.parametrize("inputs", ["multi_chunk"], indirect=True)
def test_multi_chunk_roundtrip_keeps_actual_receipts_order_and_decimal_scale(inputs):
    now = NOW + timedelta(seconds=3)
    raw = {
        key: path.read_bytes()
        for key, path in inputs[0].items()
        if key != "archive-root"
    }
    result = service().evaluate_archive(
        raw, archive_root=inputs[0]["archive-root"], clock=lambda: now
    )
    assert result.report["status"] == "pass", result.report
    package = result.archive
    mod = archive_module()
    path = inputs[0]["sample"].parent / "multi.json"
    digest = mod.write_sample_archive(path, package, clock=lambda: now)
    loaded = mod.read_sample_archive(
        path,
        expected_sha256=digest,
        grant=package.sample.batch.grant,
        clock=lambda: now,
    )
    assert loaded == package
    assert len(loaded.lineage.chunks) == 3
    assert tuple(chunk.first_received_at for chunk in loaded.lineage.chunks) == (
        NOW + timedelta(seconds=1),
        NOW + timedelta(seconds=2),
        NOW + timedelta(seconds=3),
    )
    assert tuple(bar.available_at for bar in loaded.sample.batch.bars) == (
        (NOW + timedelta(seconds=1),) * 10
        + (NOW + timedelta(seconds=2),) * 10
        + (NOW + timedelta(seconds=3),)
    )
    assert tuple(bar.trade_date for bar in loaded.sample.batch.bars) == (
        loaded.sample.batch.calendar.sessions
    )
    assert loaded.sample.batch.bars[0].close.as_tuple() == (
        Decimal("10.00000000000000000000000001").as_tuple()
    )


def test_sample_archive_expiry_during_fsync_removes_file_and_returns_no_digest(
    inputs, monkeypatch
):
    package = evaluate(inputs).archive
    mod = archive_module()
    original = os.fsync
    expired = False

    def expire(fd):
        nonlocal expired
        original(fd)
        expired = True

    monkeypatch.setattr(os, "fsync", expire)
    target = inputs[0]["sample"].parent / "expired-durable.json"
    with pytest.raises(mod.SampleArchiveError, match="^storage_denied$"):
        mod.write_sample_archive(
            target,
            package,
            clock=lambda: NOW + timedelta(days=2) if expired else NOW,
        )
    assert not target.exists()
