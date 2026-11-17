"""Verified offline archive reads and explicit mapping with real receipt times."""

import importlib
import json
from dataclasses import FrozenInstanceError
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256

import pytest
from factories import bar, state
from pydantic import ValidationError
from test_supplier_collection import NOW, client, grant
from test_supplier_collection import request as collection_request
from test_supplier_collection import root as root

from dragon_radar.ingestion import supplier_collection as collection
from dragon_radar.ingestion.supplier_transport import HttpResponse, SupplierError
from dragon_radar.owner.files import secure_output


@pytest.fixture
def archive(root, request):
    bars = [bar().model_dump(mode="json", exclude={"source_id", "source_version"})]
    states = [state().model_dump(mode="json", exclude={"source_id", "source_version"})]
    for row in (*bars, *states):
        # A vendor timestamp must never override the archived receipt timestamp.
        row["available_at"] = "2000-01-01T00:00:00Z"
    profile = {
        "schema": "dragon-radar-supplier-mapping/v1",
        "profile_id": "explicit-profile",
        "source_id": "ifind",
        "source_version": "v1",
        "bar_fields": {key: key for key in bars[0] if key != "available_at"},
        "state_fields": {key: key for key in states[0] if key != "available_at"},
    }
    raw_profile = json.dumps(profile).encode()
    plan = collection_request(profile_sha256=sha256(raw_profile).hexdigest())
    calls = []
    scenario = getattr(request, "param", None)
    if scenario in {"constant_bar_date", "constant_state_date"}:
        kind, date_field = (
            ("bar", "trade_date")
            if scenario == "constant_bar_date"
            else ("state", "effective_from")
        )
        profile[f"{kind}_fields"].pop(date_field)
        rows = bars if kind == "bar" else states
        profile[f"{kind}_constants"] = {date_field: rows[0].pop(date_field)}
        raw_profile = json.dumps(profile).encode()
        plan = collection_request(profile_sha256=sha256(raw_profile).hexdigest())

    def send(req, *_):
        calls.append(req)
        response_bars = [dict(row) for row in bars]
        if scenario == "bad_close" and len(calls) == 2:
            response_bars[0]["close"] = "SECRET_VALUE"
        return HttpResponse(
            200,
            json.dumps(
                {"errorcode": 0, "tables": [{"bars": response_bars, "states": states}]}
            ).encode(),
        )

    result = collection.collect(
        plan,
        grant=grant(),
        archive_root=root,
        transport=client(send),
        clock=lambda: NOW + timedelta(seconds=len(calls) if scenario == "timed" else 0),
    )
    return plan, result, raw_profile, calls, root


def reader(archive, **extras):
    assert hasattr(collection, "read_collection"), "offline archive reader missing"
    plan, _, _, _, root = archive
    options = {"grant": grant(), "archive_root": root, "clock": lambda: NOW}
    return collection.read_collection(plan, **(options | extras))


def api():
    name = "dragon_radar.ingestion.supplier_archive_mapping"
    assert importlib.util.find_spec(name) is not None, "archive mapping bridge missing"
    return importlib.import_module(name)


def mapped(archive, **extras):
    module = api()
    plan, _, profile, _, root = archive
    options = {
        "grant": grant(),
        "archive_root": root,
        "mapping_json": profile,
        "extraction": module.RowExtraction(
            bar_path=("tables", 0, "bars"), state_path=("tables", 0, "states")
        ),
        "clock": lambda: NOW,
    }
    return module.normalize_collection(plan, **(options | extras))


def test_read_only_complete_archive_returns_same_verified_bytes(archive):
    before = {p.name: p.read_bytes() for p in archive[4].iterdir()}
    result = reader(archive)
    assert result.fingerprint == archive[1].fingerprint
    assert len(result.chunks) == 4
    assert result.chunks[0].metadata == archive[1].chunks[0]
    assert json.loads(result.chunks[0].raw)["errorcode"] == 0
    assert "tables" not in repr(result)
    assert len(archive[3]) == 4
    assert before == {p.name: p.read_bytes() for p in archive[4].iterdir()}
    with pytest.raises(FrozenInstanceError):
        result.chunks = ()


def test_complete_raw_archive_does_not_repair_missing_marker(archive):
    marker = archive[4] / "chunk-00003.done.json"
    marker.unlink()
    assert len(reader(archive).chunks) == 4
    assert not marker.exists()


def test_incomplete_collection_never_returns_partial_data(archive):
    root = archive[4]
    (root / archive[1].chunks[0].raw_path).unlink()
    (root / "chunk-00000.done.json").unlink()
    with pytest.raises(SupplierError) as error:
        reader(archive)
    assert error.value.code == "collection_incomplete"
    assert len(archive[3]) == 4


def test_later_corrupt_marker_rejects_all_results(archive):
    path = archive[4] / "chunk-00003.done.json"
    path.unlink()
    with secure_output(path) as stream:
        stream.write(b'{"SECRET_VALUE":true}')
    with pytest.raises(SupplierError) as error:
        reader(archive)
    assert error.value.code == "checkpoint_mismatch"
    assert "SECRET_VALUE" not in str(error.value)


def test_read_requires_current_storage_grant(archive):
    assert hasattr(collection, "read_collection"), "offline archive reader missing"
    with pytest.raises(SupplierError) as error:
        collection.read_collection(
            archive[0],
            grant=grant(),
            archive_root=archive[4],
            clock=lambda: NOW + timedelta(days=2),
        )
    assert error.value.code == "storage_denied"


def test_read_needs_no_token_or_transport(archive, monkeypatch):
    monkeypatch.delenv("IFIND_ACCESS_TOKEN")
    monkeypatch.setattr(
        collection,
        "IfindTransport",
        lambda *_: pytest.fail("offline reader constructed transport"),
    )
    assert len(reader(archive).chunks) == 4


@pytest.mark.parametrize("budget", [0, -1, True, 64 * 1024 * 1024 + 1])
def test_invalid_archive_budgets_fail_closed(archive, budget):
    with pytest.raises(SupplierError) as error:
        reader(archive, max_total_bytes=budget)
    assert error.value.code == "invalid_limits"


def test_archive_total_byte_budget_is_enforced(archive):
    with pytest.raises(SupplierError) as error:
        reader(archive, max_total_bytes=1)
    assert error.value.code == "archive_budget_exceeded"


def test_explicit_mapping_uses_receipt_and_keeps_canonical_precision(archive):
    result = mapped(archive)
    assert result.collection_fingerprint == archive[1].fingerprint
    assert result.profile_sha256 == sha256(archive[2]).hexdigest()
    assert result.production_approved is False
    assert result.historical_pit_status == "unverified"
    assert result.provenance == "retrospective"
    assert len(result.normalization.bars) == len(result.normalization.states) == 4
    assert result.normalization.bars[0].close == Decimal("10")
    assert not result.normalization.issues
    assert all(
        row.available_at == NOW
        for row in (*result.normalization.bars, *result.normalization.states)
    )
    with pytest.raises(FrozenInstanceError):
        result.provenance = "point_in_time"


@pytest.mark.parametrize("field", ["profile_id", "source_id", "source_version"])
def test_mapping_identity_mismatch_is_rejected(archive, field):
    data = json.loads(archive[2])
    data[field] = "SECRET_VALUE"
    with pytest.raises(SupplierError) as error:
        mapped(archive, mapping_json=json.dumps(data).encode())
    assert error.value.code == "mapping_binding_mismatch"
    assert "SECRET_VALUE" not in str(error.value)


def test_mapping_byte_digest_is_bound_not_just_semantics(archive):
    with pytest.raises(SupplierError) as error:
        mapped(archive, mapping_json=archive[2] + b"\n")
    assert error.value.code == "mapping_binding_mismatch"


@pytest.mark.parametrize("kind", ["bar", "state"])
@pytest.mark.parametrize("slot", ["fields", "constants"])
def test_profile_cannot_backdate_available_at(archive, kind, slot):
    data = json.loads(archive[2])
    data.setdefault(f"{kind}_{slot}", {})["available_at"] = (
        "available_at" if slot == "fields" else "2000-01-01T00:00:00Z"
    )
    raw = json.dumps(data).encode()
    updated = (collection_request(profile_sha256=sha256(raw).hexdigest()), *archive[1:])
    with pytest.raises(SupplierError) as error:
        mapped(updated, mapping_json=raw)
    assert error.value.code == "invalid_extraction"


@pytest.mark.parametrize(
    "path", [("missing",), ("tables", 9, "bars"), ("tables", 0), ("errorcode",)]
)
def test_missing_path_and_wrong_row_structure_fail(archive, path):
    module = api()
    with pytest.raises(SupplierError) as error:
        mapped(archive, extraction=module.RowExtraction(bar_path=path))
    assert error.value.code == "extraction_failed"


@pytest.mark.parametrize(
    "path", [(), ("*",), ("tables", True), ("tables", -1), ("x",) * 17]
)
def test_path_controls_are_strict(path):
    module = api()
    with pytest.raises(ValidationError):
        module.RowExtraction(bar_path=path)


def test_at_least_one_explicit_row_path_is_required():
    module = api()
    with pytest.raises(ValidationError):
        module.RowExtraction()


def test_unsafe_mapping_json_is_safe_failure(archive):
    with pytest.raises(SupplierError) as error:
        mapped(archive, mapping_json=b'{"profile_id":"x","profile_id":"SECRET_VALUE"}')
    assert error.value.code == "invalid_extraction"
    assert "SECRET_VALUE" not in str(error.value)


@pytest.mark.parametrize("archive", ["bad_close"], indirect=True)
def test_normalization_issues_keep_global_row_indices_without_input_values(archive):
    result = mapped(archive)
    assert len(result.normalization.bars) == 3
    assert len(result.normalization.states) == 4
    assert len(result.normalization.issues) == 1
    issue = result.normalization.issues[0]
    assert (issue.kind, issue.row_index, issue.field, issue.code) == (
        "bar",
        1,
        "close",
        "invalid_value",
    )
    assert "SECRET_VALUE" not in repr(result)


@pytest.mark.parametrize("archive", ["timed"], indirect=True)
def test_each_chunk_retains_its_own_receipt_time_and_mapping_is_unchanged(archive):
    original = archive[2]
    result = mapped(archive, clock=lambda: NOW + timedelta(seconds=10))
    assert [row.available_at for row in result.normalization.bars] == [
        NOW + timedelta(seconds=index) for index in range(1, 5)
    ]
    assert archive[2] == original
    assert all(
        row.available_at == metadata.first_received_at
        for row, metadata in zip(
            result.normalization.states, result.chunks, strict=True
        )
    )


def test_combined_row_budget_rejects_without_partial_result(archive, monkeypatch):
    module = api()
    monkeypatch.setattr(module, "_MAX_ROWS", 5)
    with pytest.raises(SupplierError) as error:
        mapped(archive)
    assert error.value.code == "extraction_failed"


def test_bridge_rejects_incomplete_archive_before_any_normalization(
    archive, monkeypatch
):
    module = api()
    (archive[4] / archive[1].chunks[3].raw_path).unlink()
    (archive[4] / "chunk-00003.done.json").unlink()
    monkeypatch.setattr(
        module,
        "normalize_records",
        lambda *_, **__: pytest.fail("partial collection reached normalization"),
    )
    with pytest.raises(SupplierError) as error:
        mapped(archive)
    assert error.value.code == "collection_incomplete"


@pytest.mark.parametrize(
    "archive", ["constant_bar_date", "constant_state_date"], indirect=True
)
def test_bridge_cannot_invent_effective_dates_from_constants(archive):
    with pytest.raises(SupplierError) as error:
        mapped(archive)
    assert error.value.code == "invalid_extraction"


def test_archive_budget_counts_done_markers_as_well_as_raw_envelopes(archive):
    raw_total = sum(
        (archive[4] / chunk.raw_path).stat().st_size for chunk in archive[1].chunks
    )
    with pytest.raises(SupplierError) as error:
        reader(archive, max_total_bytes=raw_total)
    assert error.value.code == "archive_budget_exceeded"
    all_total = sum(path.stat().st_size for path in archive[4].iterdir())
    assert len(reader(archive, max_total_bytes=all_total).chunks) == 4
