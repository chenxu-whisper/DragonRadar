"""Q03 engineering fixtures only: no real suppliers or PIT approval."""

import copy
import importlib
from datetime import timedelta
from decimal import Decimal, localcontext
from hashlib import sha256

import pytest
from test_supplier_collection import root as root
from test_supplier_sample import CHECK_TIME, SESSIONS, sample_payload

from dragon_radar.ingestion.sample_archive import SampleArchive, archive_bytes
from dragon_radar.owner.files import secure_output
from dragon_radar.production.trust import canonical_json


@pytest.fixture(autouse=True)
def private_workspace(root):
    return root


def digest(value):
    return sha256(canonical_json(value)).hexdigest()


def module():
    name = "dragon_radar.ingestion.comparison"
    assert importlib.util.find_spec(name) is not None, "Q03 comparator is missing"
    return importlib.import_module(name)


def contracts():
    name = "dragon_radar.ingestion.comparison_contracts"
    assert importlib.util.find_spec(name) is not None, "Q03 contracts are missing"
    return importlib.import_module(name)


def private_json(path, value):
    with secure_output(path) as stream:
        stream.write(canonical_json(value))


def package(side, *, count=1, change=None):
    payload = sample_payload()
    batch = payload["batch"]
    supplier = f"synthetic-{side}"
    batch.update(source_id=supplier, provenance="retrospective")
    batch["grant"].update(source_id=supplier, reference="PRIVATE_TEST_SECRET")
    batch["expected_security_ids"] = [f"{side}-{i:06}" for i in range(count)]
    original_state = batch["security_states"][0]
    original_bars = batch["bars"]
    batch["security_states"] = []
    batch["bars"] = []
    for i, sid in enumerate(batch["expected_security_ids"]):
        state = copy.deepcopy(original_state)
        state.update(
            security_id=sid,
            code=f"{600000 + i}",
            source_id=supplier,
            name="PRIVATE_TEST_SECRET",
            available_at=CHECK_TIME.isoformat(),
        )
        batch["security_states"].append(state)
        for row in original_bars:
            bar = copy.deepcopy(row)
            bar.update(
                security_id=sid,
                source_id=supplier,
                available_at=CHECK_TIME.isoformat(),
            )
            batch["bars"].append(bar)
    if change:
        change(payload)
    lineage = {
        key: sha256(f"{side}-{key}".encode()).hexdigest()
        for key in (
            "request_sha256",
            "grant_sha256",
            "profile_sha256",
            "sample_file_sha256",
            "extraction_sha256",
            "collection_fingerprint",
        )
    }
    lineage["chunks"] = [
        {
            "index": 0,
            "fingerprint": sha256(f"{side}-fingerprint".encode()).hexdigest(),
            "raw_sha256": sha256(f"{side}-raw".encode()).hexdigest(),
            "archive_sha256": sha256(f"{side}-archive".encode()).hexdigest(),
            "first_received_at": CHECK_TIME.isoformat(),
        }
    ]
    return SampleArchive.model_validate_json(
        canonical_json(
            {
                "schema": "dragon-radar-normalized-sample-archive/v1",
                "sample": payload,
                "lineage": lineage,
                "production_approved": False,
                "historical_pit_status": "unverified",
            }
        )
    )


def source_evidence(archive, upstream, benchmark_id):
    b = archive.sample.batch
    return {
        "supplier_id": b.source_id,
        "upstream_id": upstream,
        "source_version": b.source_version,
        "pool_version": b.pool_version,
        "calendar_version": b.calendar_version,
        "adjustment_version": b.adjustment_version,
        "collection_fingerprint": archive.lineage.collection_fingerprint,
        "request_sha256": archive.lineage.request_sha256,
        "extraction_sha256": archive.lineage.extraction_sha256,
        "sample_file_sha256": archive.lineage.sample_file_sha256,
        "raw_sha256": [c.raw_sha256 for c in archive.lineage.chunks],
        "benchmark_id": benchmark_id,
        "currency": "CNY",
        "price_unit": "currency/share",
        "volume_unit": "share",
        "amount_unit": "currency",
    }


def make_pair(root, *, count=1, left=None, right=None):
    left = left or package("left", count=count)
    right = right or package("right", count=count)
    entries = [
        {
            "left_security_id": f"left-{i:06}",
            "right_security_id": f"right-{i:06}",
            "canonical_security_id": f"SSE:{600000 + i}",
            "exchange": "SSE",
            "code": f"{600000 + i}",
            "security_type": "common_stock",
            "board_category": "sh_main",
            "effective_from": SESSIONS[0].isoformat(),
            "effective_to": None,
        }
        for i in range(count)
    ]
    mapping = {"schema": "dragon-radar.supplier-identity-map/v1", "entries": entries}
    benchmark = {
        "canonical_benchmark_id": "benchmark:SSE-test",
        "currency": "CNY",
        "price_unit": "currency/share",
        "method_version": "synthetic-close-v1",
    }
    basis = {
        "as_of": CHECK_TIME.isoformat(),
        "decision_sessions": [SESSIONS[-1].isoformat()],
        "pool_sha256": digest(
            {"security_ids": [e["canonical_security_id"] for e in entries]}
        ),
        "calendar_sha256": digest(
            {
                "exchange": "SSE",
                "sessions": [d.isoformat() for d in SESSIONS],
            }
        ),
        "adjustment_semantics": "raw_ohlc_with_multiplier",
        "benchmark_definition": benchmark,
    }
    evidence = {
        "schema": "dragon-radar.supplier-comparison-evidence/v1",
        "kind": "test",
        "left": source_evidence(left, "upstream-left", "left-index"),
        "right": source_evidence(right, "upstream-right", "right-index"),
        "basis": basis,
        "tolerances": None,
    }
    proof = digest(evidence)
    private_json(root / f"{proof}.json", evidence)
    plan = {
        "schema": "dragon-radar.supplier-comparison/v1",
        "kind": "test",
        "left_archive_sha256": sha256(
            archive_bytes(left, clock=lambda: CHECK_TIME)
        ).hexdigest(),
        "right_archive_sha256": sha256(
            archive_bytes(right, clock=lambda: CHECK_TIME)
        ).hexdigest(),
        "left_supplier_id": left.sample.batch.source_id,
        "right_supplier_id": right.sample.batch.source_id,
        "left_upstream_id": "upstream-left",
        "right_upstream_id": "upstream-right",
        "independence_evidence_sha256": proof,
        "as_of": basis["as_of"],
        "decision_sessions": basis["decision_sessions"],
        "window": 20,
        "pool_sha256": basis["pool_sha256"],
        "calendar_sha256": basis["calendar_sha256"],
        "adjustment_semantics": "raw_ohlc_with_multiplier",
        "adjustment_evidence_sha256": proof,
        "identity_mapping_sha256": digest(mapping),
        "benchmark_definition_sha256": digest(benchmark),
        "fields": ["close", "benchmark_close"],
        "max_security_count": 100,
        "tolerances": {
            "close": {"abs": "0", "rel": "0"},
            "benchmark_close": {"abs": "0", "rel": "0"},
        },
        "tolerance_evidence_sha256": None,
    }
    return {
        "left": left,
        "right": right,
        "plan": plan,
        "mapping": mapping,
        "evidence": evidence,
        "root": root,
    }


@pytest.fixture
def pair(tmp_path):
    return make_pair(tmp_path)


def compare(pair, **kwargs):
    mod = module()
    c = contracts()
    return mod.compare_samples(
        pair["left"],
        pair["right"],
        c.ComparisonPlan.model_validate_json(canonical_json(pair["plan"])),
        c.IdentityMap.model_validate_json(canonical_json(pair["mapping"])),
        evidence_root=pair["root"],
        clock=lambda: CHECK_TIME,
        **kwargs,
    )


def reproof(pair):
    proof = digest(pair["evidence"])
    private_json(pair["root"] / f"{proof}.json", pair["evidence"])
    pair["plan"]["independence_evidence_sha256"] = proof
    pair["plan"]["adjustment_evidence_sha256"] = proof


def test_independent_exact_pair_passes_without_secrets(pair):
    report = compare(pair)
    assert report.status == "pass"
    assert report.test_only is True
    assert report.production_approved is False
    assert report.historical_pit_status == "unverified"
    assert report.counts.expected == report.counts.agree == 42
    assert report.counts.conflict == report.counts.unavailable == 0
    assert report == compare(pair)
    assert "PRIVATE_TEST_SECRET" not in report.model_dump_json()
    assert len(report.comparison_id) == 64
    assert report.selection.security_ids == ("SSE:600000",)
    assert report.selection.sessions == SESSIONS


@pytest.mark.parametrize(
    "field",
    [
        "left_archive_sha256",
        "right_supplier_id",
        "right_upstream_id",
    ],
)
def test_same_identity_or_archive_rejected_before_cells(pair, field):
    if field == "left_archive_sha256":
        pair["plan"][field] = pair["plan"]["right_archive_sha256"]
    else:
        pair["plan"][field] = pair["plan"][field.replace("right", "left")]
    report = compare(pair)
    assert report.status == "fail"
    assert not report.cells


def test_shared_raw_content_cannot_be_relabelled_as_independent(pair):
    payload = pair["right"].model_dump(mode="json", by_alias=True)
    payload["lineage"]["chunks"][0]["raw_sha256"] = (
        pair["left"].lineage.chunks[0].raw_sha256
    )
    pair["right"] = SampleArchive.model_validate_json(canonical_json(payload))
    report = compare(pair)
    assert report.status == "fail"
    assert not report.cells


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(pool_sha256="0" * 64),
        lambda p: p.update(calendar_sha256="0" * 64),
        lambda p: p.update(as_of=(CHECK_TIME + timedelta(seconds=1)).isoformat()),
        lambda p: p.update(benchmark_definition_sha256="0" * 64),
    ],
)
def test_basis_mismatch_never_claims_numeric_agreement(pair, change):
    change(pair["plan"])
    report = compare(pair)
    assert report.status == "incompatible"
    assert not report.cells


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("left", "currency", "USD"),
        ("left", "volume_unit", "lot"),
        ("left", "collection_fingerprint", "0" * 64),
    ],
)
def test_source_bound_evidence_rejects_units_and_lineage(pair, target, field, value):
    pair["evidence"][target][field] = value
    reproof(pair)
    report = compare(pair)
    assert report.status in {"fail", "incompatible"}
    assert not report.cells


def test_missing_evidence_is_blocked(pair):
    pair["plan"]["independence_evidence_sha256"] = "0" * 64
    report = compare(pair)
    assert report.status == "blocked"
    assert not report.cells


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_security_count", 101),
        ("max_security_count", True),
        ("window", 20.0),
        ("fields", ["close", "close"]),
        ("left_supplier_id", " "),
        ("left_supplier_id", "x" * 101),
        ("decision_sessions", [0]),
        ("as_of", "2026-10-02T12:00:00"),
        ("adjustment_semantics", "qfq"),
        ("tolerances", {"close": {"abs": "-1", "rel": "0"}}),
    ],
)
def test_plan_rejects_invalid_contract(pair, field, value):
    from pydantic import ValidationError

    pair["plan"][field] = value
    with pytest.raises(ValidationError):
        contracts().ComparisonPlan.model_validate_json(canonical_json(pair["plan"]))


def test_defaults_are_zero_and_fields_are_canonical(pair):
    pair["plan"]["fields"] = ["benchmark_close", "close"]
    del pair["plan"]["max_security_count"]
    del pair["plan"]["tolerances"]
    p = contracts().ComparisonPlan.model_validate_json(canonical_json(pair["plan"]))
    assert p.max_security_count == 100
    assert p.fields == ("close", "benchmark_close")
    assert all(t.abs == t.rel == Decimal(0) for t in p.tolerances.values())


def test_conflict_retains_both_values_without_average(tmp_path):
    right = package(
        "right", change=lambda p: p["batch"]["bars"][0].update(close="10.125")
    )
    pair = make_pair(tmp_path, right=right)
    report = compare(pair)
    assert report.status == "fail"
    conflict = next(c for c in report.cells if c.status == "conflict")
    assert (conflict.left, conflict.right) == ("10", "10.125")
    assert conflict.absolute_difference == "0.125"
    assert conflict.limit == "0"
    assert report.counts.conflict == 1
    assert report.counts.expected == report.counts.agree + report.counts.conflict


def test_nonzero_tolerance_requires_approved_precision_then_inclusive_boundary(
    tmp_path,
):
    right = package(
        "right", change=lambda p: p["batch"]["bars"][0].update(close="10.125")
    )
    pair = make_pair(tmp_path, right=right)
    pair["plan"]["tolerances"]["close"]["abs"] = "0.125"
    assert compare(pair).status == "blocked"
    pair["evidence"]["tolerances"] = pair["plan"]["tolerances"]
    reproof(pair)
    pair["plan"]["tolerance_evidence_sha256"] = pair["plan"][
        "independence_evidence_sha256"
    ]
    assert compare(pair).status == "blocked"
    approvals = []

    def approve(digest, raw, purpose, now):
        approvals.append((digest, purpose, now))
        return purpose == "precision"

    report = compare(pair, evidence_approver=approve)
    assert report.status == "pass"
    assert approvals
    assert report.cells[0].limit == "0.125"


def test_missing_previous_close_is_unavailable_not_zero(pair):
    pair["plan"]["fields"] = ["previous_close"]
    pair["plan"]["tolerances"] = {"previous_close": {"abs": "0", "rel": "0"}}
    report = compare(pair)
    assert report.status == "blocked"
    assert report.counts.unavailable == 21
    assert all(
        c.left is c.right is c.absolute_difference is c.limit is None
        for c in report.cells
    )


def test_adjustment_factor_mismatch_rejected_even_when_not_selected(tmp_path):
    right = package(
        "right", change=lambda p: p["batch"]["bars"][0].update(adjustment_factor="2")
    )
    pair = make_pair(tmp_path, right=right)
    report = compare(pair)
    assert report.status == "incompatible"
    assert not report.cells


def test_pit_state_contradiction_fails_before_values(tmp_path):
    right = package(
        "right", change=lambda p: p["batch"]["security_states"][0].update(is_st=True)
    )
    pair = make_pair(tmp_path, right=right)
    report = compare(pair)
    assert report.status == "fail"
    assert not report.cells


def test_whole_pool_checked_before_first_hundred_selection(tmp_path):
    pair = make_pair(tmp_path, count=101)
    report = compare(pair)
    assert report.status == "pass"
    assert len(report.selection.security_ids) == 100
    assert report.selection.security_ids[-1] == "SSE:600099"
    assert report.counts.expected == 2121
    pair["mapping"]["entries"][-1]["code"] = "999999"
    pair["plan"]["identity_mapping_sha256"] = digest(pair["mapping"])
    report = compare(pair)
    assert report.status == "fail"
    assert not report.cells


def test_mapping_duplicates_and_missing_member_never_drop_denominator(pair):
    from pydantic import ValidationError

    pair["mapping"]["entries"] *= 2
    with pytest.raises(ValidationError):
        contracts().IdentityMap.model_validate_json(canonical_json(pair["mapping"]))


def test_decimal_context_is_local_not_global(tmp_path):
    right = package(
        "right",
        change=lambda p: p["batch"]["bars"][0].update(
            close="10.00000000000000000000000000000001"
        ),
    )
    pair = make_pair(tmp_path, right=right)
    with localcontext() as context:
        context.prec = 6
        report = compare(pair)
        assert context.prec == 6
    cell = next(c for c in report.cells if c.status == "conflict")
    assert Decimal(cell.absolute_difference) == Decimal("1e-32")


def test_report_contract_rejects_extra_claims_and_broken_counts(pair):
    from pydantic import ValidationError

    report = compare(pair).model_dump(mode="json", by_alias=True)
    report["counts"]["agree"] -= 1
    with pytest.raises(ValidationError):
        contracts().ComparisonReport.model_validate_json(canonical_json(report))
    report["average"] = "10"
    with pytest.raises(ValidationError):
        contracts().ComparisonReport.model_validate_json(canonical_json(report))


def test_expired_grant_is_safe_blocked_report(pair):
    mod = module()
    c = contracts()
    report = mod.compare_samples(
        pair["left"],
        pair["right"],
        c.ComparisonPlan.model_validate_json(canonical_json(pair["plan"])),
        c.IdentityMap.model_validate_json(canonical_json(pair["mapping"])),
        evidence_root=pair["root"],
        clock=lambda: CHECK_TIME + timedelta(days=1000),
    )
    assert report.status == "blocked"
    assert report.issues[0].code == "storage_denied"
    assert not report.cells


def test_supplier_descriptors_do_not_grant_real_approval(pair):
    # Isolate authority resolution, not successful real-sample acceptance.
    pair["plan"]["kind"] = pair["evidence"]["kind"] = "supplier_sample"
    reproof(pair)
    mod = module()
    plan = contracts().ComparisonPlan.model_validate_json(canonical_json(pair["plan"]))

    def resolve(approver):
        return mod._evidence(
            plan, pair["root"], pair["left"], pair["right"], approver, CHECK_TIME, 0
        )

    for approver in (None, lambda *args: False):
        with pytest.raises(mod.ComparisonError) as caught:
            resolve(approver)
        assert caught.value.code == "evidence_approval_unavailable"
    assert resolve(lambda *args: True).kind == "supplier_sample"

    def unavailable(*args):
        raise RuntimeError("PRIVATE_TEST_SECRET")

    with pytest.raises(mod.ComparisonError) as caught:
        resolve(unavailable)
    assert str(caught.value) == "evidence_approval_unavailable"


def test_relative_tolerance_uses_larger_magnitude(tmp_path):
    right = package(
        "right", change=lambda p: p["batch"]["bars"][0].update(close="10.1")
    )
    pair = make_pair(tmp_path, right=right)
    pair["plan"]["tolerances"]["close"]["rel"] = "0.01"
    pair["evidence"]["tolerances"] = pair["plan"]["tolerances"]
    reproof(pair)
    pair["plan"]["tolerance_evidence_sha256"] = pair["plan"][
        "independence_evidence_sha256"
    ]
    report = compare(pair, evidence_approver=lambda *args: True)
    assert report.status == "pass"
    assert report.cells[0].limit == "0.101"


def test_record_order_preserves_selection_and_cells_but_changed_bytes_change_identity(
    tmp_path,
):
    original = make_pair(tmp_path)
    baseline = compare(original)
    (tmp_path / f"{original['plan']['independence_evidence_sha256']}.json").unlink()
    left = package("left", change=lambda p: p["batch"]["bars"].reverse())
    right = package("right", change=lambda p: p["batch"]["bars"].reverse())
    changed = make_pair(tmp_path, left=left, right=right)
    report = compare(changed)
    assert report.status == "pass"
    assert report.selection == baseline.selection
    assert report.cells == baseline.cells
    # External file hashes cannot remain the same when frozen bytes change.
    assert report.comparison_id != baseline.comparison_id


def test_metadata_budget_includes_evidence(pair):
    report = compare(pair, metadata_bytes=4 * 1024 * 1024)
    assert report.status == "fail"
    assert report.issues[0].code == "budget"
    assert not report.cells


def test_row_budget_precedes_quality_validator(pair, monkeypatch):
    monkeypatch.setattr(module(), "MAX_ROWS", 1)
    report = compare(pair)
    assert report.status == "fail"
    assert report.issues[0].code == "budget"
    assert not report.cells


def test_full_pool_validation_budget_precedes_nested_work(pair, monkeypatch):
    monkeypatch.setattr(module(), "MAX_CELLS", 1)
    report = compare(pair)
    assert report.status == "fail"
    assert report.issues[0].code == "budget"
    assert not report.cells


@pytest.mark.parametrize("value", ["not-decimal", "NaN", "Infinity", "1e1001", 0.0])
def test_invalid_decimal_tolerance_is_normal_validation_error(pair, value):
    from pydantic import ValidationError

    pair["plan"]["tolerances"]["close"]["abs"] = value
    with pytest.raises(ValidationError):
        contracts().ComparisonPlan.model_validate_json(canonical_json(pair["plan"]))


def test_default_tolerance_with_invalid_fields_is_validation_error(pair):
    from pydantic import ValidationError

    del pair["plan"]["tolerances"]
    pair["plan"]["fields"] = 5
    with pytest.raises(ValidationError):
        contracts().ComparisonPlan.model_validate_json(canonical_json(pair["plan"]))


def test_report_status_cannot_contradict_numeric_conflict(pair):
    from pydantic import ValidationError

    report = compare(pair).model_dump(mode="json", by_alias=True)
    report["status"] = "blocked"
    report["cells"][0].update(status="conflict", reason="numeric_conflict")
    report["counts"]["agree"] -= 1
    report["counts"]["conflict"] += 1
    report["issues"] = [
        {
            "code": "numeric_conflict",
            "status": "fail",
            "canonical_security_id": "SSE:600000",
            "trade_date": SESSIONS[0].isoformat(),
            "field": "close",
        }
    ]
    with pytest.raises(ValidationError):
        contracts().ComparisonReport.model_validate_json(canonical_json(report))
