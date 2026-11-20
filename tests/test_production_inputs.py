"""Isolated contract fixtures, never production evidence or supplier assets."""

import argparse
import copy
import importlib
import importlib.util
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from test_production_trust import rsa_keys as rsa_keys
from test_reset_backtest import (
    ENG_DAYS,
    ENG_FLAT,
    ENG_VALUES_BY_DAY,
    _eng_anchor,
    _eng_config,
    _eng_frames,
    _factor_frames,
)

from dragon_radar.production.trust import canonical_json
from dragon_radar.research.engine import run_backtest
from dragon_radar.research.models import BacktestConfig
from dragon_radar.research.pit import PitAnchor, PitFrame, UniversePolicy


def production():
    assert importlib.util.find_spec("dragon_radar.research.production"), (
        "production input adapter is not implemented"
    )
    return importlib.import_module("dragon_radar.research.production")


def digest(value):
    return sha256(canonical_json(value)).hexdigest()


def parse(model, value):
    return model.model_validate_json(canonical_json(value))


def receipt(domain, record, available, *, source="vendor-a", version="s1"):
    return {
        "domain": domain,
        "source_id": source,
        "source_version": version,
        "record_sha256": digest(record),
        "source_ref": "https://supplier.example/immutable/" + digest(record),
        "published_at": available,
        "first_received_at": available,
        "received_at": available,
        "provenance": "real",
    }


def refresh_receipts(bundle):
    receipts = {}
    for frame in bundle["frames"]:
        market = frame["market"]
        records = (
            [("calendar", market["calendar"], "2025-01-01T00:00:00Z")]
            + [("state", row, row["available_at"]) for row in market["security_states"]]
            + [("market", row, row["available_at"]) for row in market["bars"]]
        )
        topic = frame.get("topic")
        if topic is not None:
            records += [
                (
                    "topic",
                    row,
                    row.get("available_at", row.get("complete_as_of", topic["as_of"])),
                )
                for row in (
                    *topic["topics"],
                    *topic["memberships"],
                    *topic["coverage_cells"],
                    topic["page_manifest"],
                )
            ]
        for domain, row, available in records:
            item = receipt(domain, row, available)
            receipts[(domain, item["record_sha256"])] = item
    bundle["receipts"] = list(receipts.values())
    for frame, lineage in zip(
        bundle["frames"],
        bundle["factor_archive"]["lineage"],
        strict=True,
    ):
        market = frame["market"]
        lineage["source_refs"] = sorted(
            {
                digest(market["calendar"]),
                *(digest(row) for row in market["security_states"]),
                *(digest(row) for row in market["bars"]),
            }
        )
        topic = frame.get("topic")
        if topic is not None:
            lineage["source_refs"] = sorted(
                {
                    *lineage["source_refs"],
                    *(
                        digest(row)
                        for row in (
                            *topic["topics"],
                            *topic["memberships"],
                            *topic["coverage_cells"],
                            topic["page_manifest"],
                        )
                    ),
                }
            )
    for frame, publication in zip(
        bundle["frames"], bundle["publications"], strict=True
    ):
        publication["frame_sha256"] = digest(frame)


@pytest.fixture
def inputs():
    """Reuse engine fixtures only within tests; complete production-required fields."""
    cfg = _eng_config().model_dump(mode="json")
    cfg["experiment"]["legs"] = ["baseline"]
    cfg["input_manifests"]["versions"] = {
        "adjustment": "a1",
        "calendar": "c1",
        "pool": "p1",
    }
    frames = [
        f.model_dump(mode="json")
        for f in _eng_frames({day: dict(ENG_FLAT) for day in ENG_DAYS})
    ]
    grant = {
        "source_id": "vendor-a",
        "approved": True,
        "reference": "external-license",
        "starts_at": "2025-01-01T00:00:00Z",
        "expires_at": "2027-01-01T00:00:00Z",
        "local_storage": True,
        "local_display": True,
        "export": False,
        "ai": False,
        "sharing": False,
    }
    candidate = {
        "code": "source-sha256:" + "a" * 64,
        "dataset": "s1",
        "rules": "rules-v1",
        "model": "none",
        "prompt": "none",
        "retrieval": "none",
        "methods": {"FAC-R01": "relative-return-approved-v1"},
    }
    publications = []
    for index, frame in enumerate(frames, start=1):
        market = frame["market"]
        market.update(source_id="vendor-a", source_time=market["as_of"], grant=grant)
        market["calendar"]["exchange"] = "SSE"
        for state in market["security_states"]:
            state.update(
                source_id="vendor-a",
                exchange="SSE",
                board_category="sh_main",
                security_type="common_stock",
                is_st=False,
                is_delisting=False,
            )
        for bar in market["bars"]:
            bar.update(
                source_id="vendor-a",
                open=bar["close"],
                high=bar["close"],
                low=bar["close"],
            )
        publication_id = str(UUID(int=index))
        market_id = str(UUID(int=100 + index))
        publications.append(
            {
                "snapshot_id": publication_id,
                "market_snapshot_id": market_id,
                "topic_snapshot_id": None,
                "publication_sequence": frame["publication_sequence"],
                "published_at": market["as_of"],
                "frame_sha256": digest(frame),
            }
        )
    cfg["snapshot_id"] = publications[-1]["snapshot_id"]
    cfg["input_manifests"]["domains"] = [
        {"domain": "market", "snapshot_id": publications[-1]["market_snapshot_id"]}
    ]
    factors = [f.model_dump(mode="json") for f in _factor_frames(ENG_VALUES_BY_DAY)]
    bundle = {
        "schema": "dragon-radar-production-input/v1",
        "candidate": {k: v for k, v in candidate.items() if k != "methods"},
        "methods": candidate["methods"],
        "snapshot_id": cfg["snapshot_id"],
        "publication_sequence": 500,
        "config": cfg,
        "anchor": _eng_anchor().model_dump(mode="json"),
        "frames": frames,
        "publications": publications,
        "policy": UniversePolicy().model_dump(mode="json"),
        "receipts": [],
        "benchmark_closes": {},
        "factor_archive": {
            "schema": "dragon-radar-production-factors/v1",
            "method_version": candidate["methods"]["FAC-R01"],
            "frames": factors,
            "lineage": [
                {
                    "trade_date": frame["trade_date"],
                    "available_at": frame["market"]["as_of"],
                    "market_snapshot_id": pub["market_snapshot_id"],
                    "topic_snapshot_id": None,
                    "publication_sequence": frame["publication_sequence"],
                    "source_refs": [],
                }
                for frame, pub in zip(frames, publications, strict=True)
            ],
        },
    }
    refresh_receipts(bundle)
    suppliers = {
        "schema": "dragon-radar-production-suppliers/v1",
        "suppliers": [
            {
                "supplier_id": "licensed-vendor",
                "source_id": "vendor-a",
                "source_version": "s1",
                "domains": ["market", "state", "calendar"],
                "grant": grant,
                "provenance": "real",
                "methods": candidate["methods"],
            }
        ],
    }
    now = datetime(2026, 11, 16, tzinfo=UTC)
    result = run_backtest(
        parse(BacktestConfig, cfg),
        tuple(parse(PitFrame, f) for f in frames),
        _factor_frames(ENG_VALUES_BY_DAY),
        anchor=parse(PitAnchor, bundle["anchor"]),
    ).model_dump(mode="json")
    run = {
        "schema": "dragon-radar-production-run/v1",
        "candidate": bundle["candidate"],
        "snapshot_id": bundle["snapshot_id"],
        "publication_sequence": 500,
        "config_sha256": digest(cfg),
        "input_sha256": digest(bundle),
        "result_sha256": digest(result),
        "result": result,
        "started_at": "2026-11-15T00:00:00Z",
        "finished_at": "2026-11-15T00:01:00Z",
    }
    return bundle, run, suppliers, candidate, now


def test_validated_input_run_reexecutes_kernel_and_returns_safe_summary(inputs):
    bundle, run, suppliers, candidate, now = inputs
    summary = production().validate_input_run(bundle, run, suppliers, candidate, now)
    assert summary["input_sha256"] == digest(bundle)
    assert summary["config_sha256"] == digest(bundle["config"])
    assert summary["result_sha256"] == digest(run["result"])
    assert summary["snapshot_id"] == bundle["snapshot_id"]
    assert summary["publication_sequence"] == 500
    assert summary["started_at"] == run["started_at"]
    assert summary["finished_at"] == run["finished_at"]
    assert "frames" not in summary and "receipts" not in summary


@pytest.mark.parametrize(
    "mutation",
    [
        "raw_missing",
        "st_missing",
        "delisting_missing",
        "retrospective",
        "fixture_source",
        "late_receipt",
        "backfilled_first",
        "permission_expired",
        "source_version",
        "domain",
        "method",
        "config",
        "snapshot",
        "sequence",
        "factor_lineage",
        "missing_factor",
        "unknown_field",
        "walk_forward",
    ],
)
def test_inputs_fail_closed_on_unusable_or_mismatched_dependencies(inputs, mutation):
    bundle, run, suppliers, candidate, now = inputs
    if mutation == "raw_missing":
        bundle["frames"][0]["market"]["bars"][0]["open"] = None
    elif mutation == "st_missing":
        bundle["frames"][0]["market"]["security_states"][0]["is_st"] = None
    elif mutation == "delisting_missing":
        bundle["frames"][0]["market"]["security_states"][0]["is_delisting"] = None
    elif mutation == "retrospective":
        bundle["frames"][0]["market"]["provenance"] = "retrospective"
    elif mutation == "fixture_source":
        suppliers["suppliers"][0]["provenance"] = "fixture"
    elif mutation == "late_receipt":
        bundle["receipts"][-1]["received_at"] = "2026-11-16T00:00:00Z"
    elif mutation == "backfilled_first":
        bundle["receipts"][-1]["first_received_at"] = None
    elif mutation == "permission_expired":
        suppliers["suppliers"][0]["grant"]["expires_at"] = "2026-01-01T00:00:00Z"
    elif mutation == "source_version":
        suppliers["suppliers"][0]["source_version"] = "different"
    elif mutation == "domain":
        suppliers["suppliers"][0]["domains"].remove("state")
    elif mutation == "method":
        bundle["factor_archive"]["method_version"] = "invented"
    elif mutation == "config":
        bundle["config"]["parameters"]["top_n"] = 1
    elif mutation == "snapshot":
        bundle["publications"][-1]["market_snapshot_id"] = str(UUID(int=999))
    elif mutation == "sequence":
        bundle["publication_sequence"] = 999
    elif mutation == "factor_lineage":
        bundle["factor_archive"]["lineage"][0]["publication_sequence"] = 999
    elif mutation == "missing_factor":
        bundle["factor_archive"]["frames"][0]["points"][0]["value"] = None
        bundle["factor_archive"]["frames"][0]["points"][0]["reasons"] = [
            "factor_value_unavailable"
        ]
    elif mutation == "unknown_field":
        bundle["completed"] = True
    elif mutation == "walk_forward":
        bundle["config"]["walk_forward"] = {
            "folds": 2,
            "train_sessions": 1,
            "test_sessions": 1,
        }
    run["input_sha256"] = digest(bundle)
    with pytest.raises(ValueError):
        production().validate_input_run(bundle, run, suppliers, candidate, now)


def test_claimed_result_with_self_consistent_hash_is_not_an_actual_run(inputs):
    bundle, run, suppliers, candidate, now = inputs
    run["result"]["equity"][0]["equity"] = "99999999"
    run["result_sha256"] = digest(run["result"])
    with pytest.raises(ValueError, match="result"):
        production().validate_input_run(bundle, run, suppliers, candidate, now)


def test_frozen_bundle_and_archive_no_overwrite(inputs, tmp_path):
    from test_production_trust import protect

    protect(tmp_path)
    bundle, _, suppliers, candidate, now = inputs
    module = production()
    frozen = module.freeze_input_bundle(bundle, suppliers, candidate, now)
    before = frozen.input_sha256
    bundle["config"]["parameters"]["top_n"] = 1
    assert frozen.input_sha256 == before
    assert frozen.config.parameters.top_n == 2
    ref = module.write_input_archive(tmp_path, frozen)
    assert ref == {"path": before + ".json", "sha256": before}
    assert module.read_input_archive(tmp_path, ref) == frozen.to_dict()
    with pytest.raises(OSError):
        module.write_input_archive(tmp_path, frozen)
    with pytest.raises(ValueError):
        module.read_input_archive(tmp_path, {"path": "../bad", "sha256": before})


def history_frames():
    """Explicit approved sessions and genuine-per-row timing for test arithmetic."""
    days = tuple(date(2025, 1, 1) + timedelta(days=i) for i in range(125))
    base = _eng_frames({day: dict(ENG_FLAT) for day in ENG_DAYS})[0]
    data = base.model_dump(mode="json")
    market = data["market"]
    market["calendar"]["sessions"] = [day.isoformat() for day in days]
    market["calendar"]["exchange"] = "SSE"
    bars = []
    template = market["bars"][0]
    for index, day in enumerate(days):
        row = copy.deepcopy(template)
        row.update(
            trade_date=day.isoformat(),
            close=str(100 + index),
            open=str(100 + index),
            high=str(100 + index),
            low=str(100 + index),
            available_at=day.isoformat() + "T16:00:00Z",
        )
        bars.append(row)
    market["bars"] = bars
    market["expected_security_ids"] = [template["security_id"]]
    data["trade_date"] = days[-1].isoformat()
    market["trade_date"] = data["trade_date"]
    market["as_of"] = days[-1].isoformat() + "T16:30:00Z"
    return (parse(PitFrame, data),), {day: Decimal(100) for day in days}


@pytest.mark.parametrize("window", [20, 60, 120])
def test_relative_strength_materializer_reuses_approved_decimal_primitives(window):
    frames, benchmark = history_frames()
    factors = production().materialize_relative_strength(
        frames,
        metric=f"relative_strength_{window}",
        benchmark_closes=benchmark,
    )
    expected = Decimal(224) / Decimal(224 - window) - 1
    assert abs(factors[0].points[0].value - expected) < Decimal("1e-27")
    assert factors[0].window == f"{window}d"
    assert factors[0].unit == "ratio"


def test_relative_strength_cannot_invent_history_benchmark_or_other_methods():
    module = production()
    frames, benchmark = history_frames()
    with pytest.raises(ValueError):
        module.materialize_relative_strength(
            frames,
            metric="weighted_score",
            benchmark_closes=benchmark,
        )
    with pytest.raises(ValueError):
        module.materialize_relative_strength(
            frames,
            metric="relative_strength_20",
            benchmark_closes={},
        )
    raw = frames[0].model_dump(mode="json")
    raw["market"]["bars"] = raw["market"]["bars"][-1:]
    with pytest.raises(ValueError):
        module.materialize_relative_strength(
            (parse(PitFrame, raw),),
            metric="relative_strength_20",
            benchmark_closes=benchmark,
        )


def test_raw_history_received_earlier_is_legitimate(inputs):
    bundle, run, suppliers, candidate, now = inputs
    # A state received on the first session remains valid in later frames.
    first = bundle["frames"][0]["market"]["security_states"][0]["available_at"]
    assert bundle["frames"][-1]["market"]["security_states"][0]["available_at"] == first
    production().validate_input_run(bundle, run, suppliers, candidate, now)


def test_owner_cli_exposes_only_explicit_production_request():
    from dragon_radar.owner.cli import add_commands

    parser = argparse.ArgumentParser()
    add_commands(parser.add_subparsers(dest="command"))
    normal = parser.parse_args(["analysis-worker"])
    assert normal.research_production_request is None
    explicit = parser.parse_args(
        [
            "analysis-worker",
            "--research-production-request",
            "protected/request.json",
        ]
    )
    assert explicit.research_production_request == Path("protected/request.json")


def test_parent_semantic_metadata_does_not_override_signed_versions(inputs):
    bundle, run, suppliers, candidate, now = inputs
    candidate.update(
        rule_scope={
            "exchange": "SSE",
            "board": "main",
            "security_status": "ordinary",
            "trade_date": "2026-11-16",
        },
        official_source_hosts=["exchange.example"],
        regulation_rules=[],
    )
    production().validate_input_run(bundle, run, suppliers, candidate, now)
    bundle["candidate"]["rule_scope"] = candidate["rule_scope"]
    with pytest.raises(ValueError):
        production().validate_input_run(bundle, run, suppliers, candidate, now)


@pytest.fixture
def signed_inputs(inputs, tmp_path, rsa_keys):
    from pathlib import Path

    from test_production_trust import OPENSSL, protect
    from test_production_trust import run as openssl_run

    from dragon_radar.owner.files import secure_output
    from dragon_radar.production.trust import TrustedIssuer, TrustedKey, TrustPolicy

    bundle, artifact, suppliers, candidate, now = inputs
    context = {
        "subject": "owner-a",
        "purpose": "research",
        "target": "local",
        "audience": "owner",
    }
    root = tmp_path / "assets"
    root.mkdir()
    output = tmp_path / "runs"
    output.mkdir()
    protect(root)
    protect(output)
    private, public = rsa_keys
    materials = {
        "bundle": canonical_json(bundle),
        "run": canonical_json(artifact),
        "supplier": canonical_json(suppliers),
    }
    for name, content in {"key.pem": public, **materials}.items():
        with secure_output(root / name) as stream:
            stream.write(content)
    record = {
        "schema": "dragon-radar-production-evidence/v1",
        "gate_id": "real_input",
        "evidence_id": "isolated-test-evidence",
        "kind": "real",
        "issuer": "isolated-test-issuer",
        "key_id": "isolated-test-key",
        "approval_id": "isolated-test-approval",
        "approver": "isolated-test-reviewer",
        "issued_at": "2026-11-14T00:00:00Z",
        "approved_at": "2026-11-14T01:00:00Z",
        "valid_from": "2026-11-14T02:00:00Z",
        "expires_at": "2026-11-17T00:00:00Z",
        "revoked": False,
        "candidate": bundle["candidate"],
        "scopes": {k: [v] for k, v in context.items()},
        "materials": [
            {"material_id": name, "path": name, "sha256": sha256(content).hexdigest()}
            for name, content in materials.items()
        ],
        "payload": {"bundle": "bundle", "run": "run", "supplier": "supplier"},
        "signature": {"algorithm": "RSA-PSS-SHA256", "path": "record.sig"},
    }
    with secure_output(root / "record.json") as stream:
        stream.write(canonical_json(record))
    openssl_run(
        OPENSSL,
        "dgst",
        "-sha256",
        "-sign",
        str(private),
        "-sigopt",
        "rsa_padding_mode:pss",
        "-sigopt",
        "rsa_pss_saltlen:32",
        "-out",
        str(root / "record.sig"),
        str(root / "record.json"),
    )
    protect(root)
    policy = TrustPolicy(
        openssl_executable=Path(OPENSSL),
        issuers={
            "isolated-test-issuer": TrustedIssuer(
                keys={
                    "isolated-test-key": TrustedKey(
                        public_key_path="key.pem",
                        sha256=sha256(public).hexdigest(),
                    )
                },
                gates=frozenset({"real_input"}),
                scopes={k: frozenset({v}) for k, v in context.items()},
                approvers=frozenset({"isolated-test-reviewer"}),
            )
        },
    )
    return (
        {
            "asset_root": root,
            "record_path": "record.json",
            "suppliers_path": root / "supplier",
            "output_root": output,
            "policy": policy,
            "candidate": candidate,
            "context": context,
            "clock": lambda: now,
            "expected_code": lambda: candidate["code"],
            "publication_sequence": lambda identity: next(
                p["publication_sequence"]
                for p in bundle["publications"]
                if p["snapshot_id"] == str(identity)
            ),
        },
        bundle,
        suppliers,
        now,
    )


def published_snapshot(bundle):
    from dragon_radar.publication.models import Publication, PublishedSnapshot
    from dragon_radar.storage.models import Snapshot

    pub = bundle["publications"][-1]
    frame = parse(PitFrame, bundle["frames"][-1])
    market = Snapshot(
        id=UUID(pub["market_snapshot_id"]),
        batch_id=UUID(int=999),
        revision=1,
        supersedes=None,
        published_at=pub["published_at"],
        manifest={},
        batch=frame.market,
        quality_report={},
    )
    publication = Publication(
        id=UUID(bundle["snapshot_id"]),
        trade_date=frame.trade_date,
        version=1,
        supersedes=None,
        status="published",
        market_snapshot_id=market.id,
        topic_snapshot_id=None,
        news_input_id=None,
        regulation_input_id=None,
        role_input_id=None,
        domains={"market": "ready"},
        coverage={},
        quality_flags=(),
        command_json="{}",
        published_at=pub["published_at"],
    )
    return PublishedSnapshot(publication, market, ())


def test_production_compute_authenticates_actual_kernel_and_archives_run(signed_inputs):
    kwargs, bundle, suppliers, now = signed_inputs
    module = production()
    compute = module.ProductionResearchCompute(**kwargs)
    checkpoints = []
    result = compute(
        published_snapshot(bundle),
        parse(BacktestConfig, bundle["config"]),
        lambda: checkpoints.append(True),
    )
    assert checkpoints
    files = list(kwargs["output_root"].glob("*.json"))
    assert len(files) == 1
    artifact = module.read_input_archive(
        kwargs["output_root"],
        {"path": files[0].name, "sha256": files[0].stem},
    )
    assert result == artifact["result"]
    assert artifact["input_sha256"] == digest(bundle)
    module.validate_input_run(bundle, artifact, suppliers, kwargs["candidate"], now)


@pytest.mark.parametrize(
    "mutation",
    [
        "revoked",
        "source",
        "config",
        "snapshot",
        "supplier",
        "content",
        "sequence",
    ],
)
def test_production_compute_rechecks_trust_and_bindings_each_run(
    signed_inputs, mutation
):
    from dragon_radar.research.worker import ResearchInputsUnavailable

    kwargs, bundle, _, _ = signed_inputs
    module = production()
    if mutation == "sequence":
        kwargs["publication_sequence"] = lambda identity: 999
    live = {"policy": kwargs["policy"]}
    kwargs["policy"] = lambda: live["policy"]
    compute = module.ProductionResearchCompute(**kwargs)
    snapshot = published_snapshot(bundle)
    cfg = parse(BacktestConfig, bundle["config"])
    if mutation == "revoked":
        from dataclasses import replace

        live["policy"] = replace(
            live["policy"],
            revoked_evidence_ids=frozenset({"isolated-test-evidence"}),
        )
    elif mutation == "source":
        kwargs["candidate"]["code"] = "source-sha256:" + "b" * 64
    elif mutation == "config":
        raw = cfg.model_dump(mode="json")
        raw["parameters"]["top_n"] = 1
        cfg = parse(BacktestConfig, raw)
    elif mutation == "snapshot":
        snapshot.publication = snapshot.publication.model_copy(
            update={"status": "revoked"},
        )
    elif mutation == "supplier":
        kwargs["suppliers_path"].unlink()
    elif mutation == "content":
        from dataclasses import replace

        raw = snapshot.batch.model_dump(mode="json")
        raw["bars"][0]["amount"] = "1"
        from dragon_radar.domain.models import MarketBatch

        snapshot.market_snapshot = replace(
            snapshot.market_snapshot,
            batch=parse(MarketBatch, raw),
        )
    with pytest.raises(ResearchInputsUnavailable):
        compute(snapshot, cfg, lambda: None)
    assert not list(kwargs["output_root"].glob("*.json"))


def test_production_compute_keeps_checkpoint_budget_failures(signed_inputs):
    from dragon_radar.research.worker import ResearchBudgetExceeded

    kwargs, bundle, _, _ = signed_inputs
    module = production()
    compute = module.ProductionResearchCompute(**kwargs)

    def checkpoint():
        raise ResearchBudgetExceeded(1, 2)

    with pytest.raises(ResearchBudgetExceeded):
        compute(
            published_snapshot(bundle),
            parse(BacktestConfig, bundle["config"]),
            checkpoint,
        )
    assert not list(kwargs["output_root"].glob("*.json"))


def test_production_request_schema_cannot_install_unpinned_trust(tmp_path):
    from test_production_trust import protect

    from dragon_radar.owner.files import secure_output

    protect(tmp_path)
    path = tmp_path / "request.json"
    with secure_output(path) as stream:
        stream.write(
            canonical_json(
                {
                    "schema": "dragon-radar-production-research-request/v1",
                    "asset_root": str(tmp_path),
                    "self_approved": True,
                }
            )
        )
    with pytest.raises(ValueError):
        production().load_production_compute(path)


def test_missing_actual_publication_sequence_resolver_stays_closed(signed_inputs):
    from dragon_radar.research.worker import ResearchInputsUnavailable

    kwargs, bundle, _, _ = signed_inputs
    kwargs.pop("publication_sequence")
    compute = production().ProductionResearchCompute(**kwargs)
    with pytest.raises(ResearchInputsUnavailable):
        compute(
            published_snapshot(bundle),
            parse(BacktestConfig, bundle["config"]),
            lambda: None,
        )
    assert not list(kwargs["output_root"].glob("*.json"))


@pytest.mark.parametrize(
    "mutation",
    [
        "ohlc",
        "volume",
        "state",
        "late_state",
        "source",
        "listing",
        "factor",
        "future_factor",
        "raw_ref",
        "received_after_available",
        "grant_at_receipt",
    ],
)
def test_semantic_validation_independent_of_result_or_publication_hash(
    inputs, mutation
):
    bundle, _, suppliers, candidate, now = inputs
    market = bundle["frames"][0]["market"]
    if mutation == "ohlc":
        market["bars"][0]["low"] = None
    elif mutation == "volume":
        market["bars"][0]["volume"] = None
    elif mutation == "state":
        market["security_states"][0]["is_st"] = None
    elif mutation == "late_state":
        market["security_states"][0]["available_at"] = "2026-11-16T00:00:00Z"
    elif mutation == "source":
        market["bars"][0]["source_id"] = "unapproved"
    elif mutation == "listing":
        market["security_states"][0]["listing_date"] = None
    elif mutation == "factor":
        bundle["factor_archive"]["method_version"] = "unapproved"
    elif mutation == "future_factor":
        bundle["factor_archive"]["lineage"][0]["available_at"] = "2026-11-16T00:00:00Z"
    refresh_receipts(bundle)
    if mutation == "raw_ref":
        bundle["receipts"].pop()
    elif mutation == "received_after_available":
        bundle["receipts"][-1]["received_at"] = "2026-11-16T00:00:00Z"
    elif mutation == "grant_at_receipt":
        suppliers["suppliers"][0]["grant"]["starts_at"] = "2026-11-14T00:00:00Z"
        for frame in bundle["frames"]:
            frame["market"]["grant"] = suppliers["suppliers"][0]["grant"]
        refresh_receipts(bundle)
    with pytest.raises(ValueError):
        production().freeze_input_bundle(bundle, suppliers, candidate, now)


def test_signed_real_input_gate_with_full_parent_metadata(signed_inputs):
    from dragon_radar.production.report import evaluate_production

    kwargs, _, _, now = signed_inputs
    candidate = kwargs["candidate"]
    summary = evaluate_production(
        kwargs["asset_root"],
        kwargs["policy"],
        {
            k: candidate[k]
            for k in ("code", "dataset", "rules", "model", "prompt", "retrieval")
        },
        kwargs["context"],
        {"real_input": kwargs["record_path"]},
        now,
        expected_code=candidate["code"],
        semantic_candidate={
            "methods": candidate["methods"],
            "regulation_rules": [],
            "rule_scope": {},
            "official_source_hosts": [],
        },
    )
    assert summary["gates"]["real_input"]["status"] == "pass"
    assert summary["overall_status"] == "blocked"
    assert summary["production_release"] == "blocked"


@pytest.mark.parametrize("explicit", [False, True])
def test_owner_cli_injects_authenticated_compute_only_when_explicit(
    monkeypatch, explicit
):
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import Mock

    from dragon_radar.owner import cli

    module = production()

    def fake_compute(snapshot, config, checkpoint):
        return {}

    loader = Mock(spec=module.load_production_compute, return_value=fake_compute)
    monkeypatch.setattr(module, "load_production_compute", loader)
    for name in (
        "OwnerRepository",
        "PublicationRepository",
        "AnalysisJobRepository",
        "ResearchRunRepository",
        "AnalysisJobWorker",
    ):
        monkeypatch.setattr(cli, name, Mock(spec=getattr(cli, name)))
    research_worker = Mock(spec=cli.ResearchBacktestWorker)
    monkeypatch.setattr(cli, "ResearchBacktestWorker", research_worker)
    monkeypatch.setattr(cli.threading, "Thread", Mock(spec=cli.threading.Thread))
    repository = SimpleNamespace(database=object())
    request = Path("protected/request.json") if explicit else None
    args = SimpleNamespace(
        command="analysis-worker",
        worker_id="worker-a",
        poll_seconds=1,
        lease_seconds=30,
        max_attempts=3,
        research_production_request=request,
    )
    cli.execute(args, repository)
    selected = research_worker.call_args.kwargs["compute"]
    if explicit:
        assert selected is fake_compute
        loader.assert_called_once_with(request, database=repository.database)
    else:
        assert selected is cli.fail_closed_research_compute
        loader.assert_not_called()


@pytest.mark.parametrize("stage", ["reauth", "archive"])
def test_final_validation_and_output_remain_inside_checkpoint_budget(
    signed_inputs,
    monkeypatch,
    stage,
):
    from dragon_radar.research.worker import ResearchBudgetExceeded

    kwargs, bundle, _, _ = signed_inputs
    module = production()
    compute = module.ProductionResearchCompute(**kwargs)
    elapsed = {"ms": 0}
    if stage == "reauth":
        original = compute._authenticated
        calls = {"count": 0}

        def slow_final_validation():
            value = original()
            calls["count"] += 1
            if calls["count"] == 2:
                elapsed["ms"] = 2000
            return value

        monkeypatch.setattr(compute, "_authenticated", slow_final_validation)
    else:
        original = module.read_input_archive

        def slow_output_validation(root, ref):
            value = original(root, ref)
            elapsed["ms"] = 2000
            return value

        monkeypatch.setattr(module, "read_input_archive", slow_output_validation)

    def checkpoint():
        if elapsed["ms"] > 1000:
            raise ResearchBudgetExceeded(1000, elapsed["ms"])

    with pytest.raises(ResearchBudgetExceeded):
        compute(
            published_snapshot(bundle),
            parse(BacktestConfig, bundle["config"]),
            checkpoint,
        )
    if stage == "reauth":
        assert not list(kwargs["output_root"].glob("*.json"))


def test_factor_availability_includes_raw_available_at_not_only_receipt(inputs):
    bundle, _, suppliers, candidate, now = inputs
    day = bundle["frames"][0]["trade_date"]
    for item in bundle["receipts"]:
        if item["domain"] == "market" and item["received_at"].startswith(day):
            item.update(
                published_at=day + "T15:40:00Z",
                first_received_at=day + "T15:45:00Z",
                received_at=day + "T15:45:00Z",
            )
    bundle["factor_archive"]["lineage"][0]["available_at"] = day + "T15:50:00Z"
    with pytest.raises(ValueError, match="factor"):
        production().freeze_input_bundle(bundle, suppliers, candidate, now)


@pytest.fixture
def topic_inputs(inputs):
    """A complete, explicit 61-session topic quality contract, only for tests."""
    bundle, _, suppliers, candidate, now = inputs
    first = date.fromisoformat(bundle["frames"][0]["trade_date"])
    sessions = [(first - timedelta(days=i)).isoformat() for i in range(60, 0, -1)] + [
        day.isoformat() for day in ENG_DAYS
    ]
    ids = bundle["frames"][0]["market"]["expected_security_ids"]
    for index, (frame, pub, line) in enumerate(
        zip(
            bundle["frames"],
            bundle["publications"],
            bundle["factor_archive"]["lineage"],
            strict=True,
        )
    ):
        market = frame["market"]
        market["calendar"]["sessions"] = sessions
        topic_id = str(UUID(int=300 + index))
        pub["topic_snapshot_id"] = topic_id
        line["topic_snapshot_id"] = topic_id
        cutoff = market["as_of"]
        known_at = bundle["frames"][0]["market"]["security_states"][0]["available_at"]
        covered = sessions[: 61 + index]
        topic = {
            "trade_date": frame["trade_date"],
            "as_of": cutoff,
            "source_time": cutoff,
            "provenance": "contemporaneous",
            "source_id": "vendor-a",
            "source_version": "s1",
            "grant": market["grant"],
            "exchange": "SSE",
            "calendar": market["calendar"],
            "calendar_version": "c1",
            "taxonomy_version": "taxonomy-v1",
            "membership_version": "membership-v1",
            "coverage_from": covered[0],
            "coverage_to": covered[-1],
            "topics": [
                {
                    "topic_id": "topic-a",
                    "name": "Topic A",
                    "effective_from": first.isoformat(),
                    "effective_to": None,
                    "available_at": known_at,
                    "source_reference": "https://supplier.example/definition",
                }
            ],
            "memberships": [
                {
                    "topic_id": "topic-a",
                    "security_id": sid,
                    "effective_from": first.isoformat(),
                    "effective_to": None,
                    "available_at": known_at,
                    "evidence_refs": ["https://supplier.example/membership/" + sid],
                }
                for sid in ids
            ],
            "coverage_cells": [
                {
                    "topic_id": "topic-a",
                    "trade_date": day,
                    "expected_member_count": len(ids)
                    if day >= first.isoformat()
                    else 0,
                    "received_member_count": len(ids)
                    if day >= first.isoformat()
                    else 0,
                    "complete_as_of": max(day, "2025-01-01") + "T16:00:00Z",
                    "status": "complete",
                    "reason": None,
                }
                for day in covered
            ],
            "page_manifest": {
                "request_from": covered[0],
                "request_to": covered[-1],
                "pages": [
                    {
                        "page_id": "page-a",
                        "request_cursor": None,
                        "next_cursor": None,
                        "page_hash": "c" * 64,
                        "received_row_count": 1 + len(ids),
                    }
                ],
                "declared_total": 1 + len(ids),
                "terminal_cursor": None,
            },
        }
        frame["topic"] = topic
    bundle["config"]["input_manifests"]["domains"].append(
        {
            "domain": "topic",
            "snapshot_id": bundle["publications"][-1]["topic_snapshot_id"],
        }
    )
    bundle["policy"]["topic_ids"] = ["topic-a"]
    suppliers["suppliers"][0]["domains"].append("topic")
    refresh_receipts(bundle)
    return bundle, suppliers, candidate, now


def test_topic_contract_uses_complete_shared_quality_validation(topic_inputs):
    bundle, suppliers, candidate, now = topic_inputs
    frozen = production().freeze_input_bundle(bundle, suppliers, candidate, now)
    assert frozen.config.input_manifests.domains[-1].domain == "topic"


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_member",
        "empty_coverage",
        "pagination",
        "count",
        "definition_interval",
    ],
)
def test_incomplete_topic_semantics_never_become_production_inputs(
    topic_inputs, mutation
):
    bundle, suppliers, candidate, now = topic_inputs
    topic = bundle["frames"][0]["topic"]
    if mutation == "unknown_member":
        topic["memberships"][0]["topic_id"] = "undeclared"
    elif mutation == "empty_coverage":
        topic["coverage_cells"] = []
    elif mutation == "pagination":
        topic["page_manifest"]["declared_total"] = 999
    elif mutation == "count":
        topic["coverage_cells"][-1]["expected_member_count"] = 999
    elif mutation == "definition_interval":
        topic["topics"][0]["effective_from"] = bundle["frames"][1]["trade_date"]
    refresh_receipts(bundle)
    with pytest.raises(ValueError, match="topic"):
        production().freeze_input_bundle(bundle, suppliers, candidate, now)


def test_factor_cannot_precede_topic_coverage_completion(topic_inputs):
    bundle, suppliers, candidate, now = topic_inputs
    day = bundle["frames"][0]["trade_date"]
    for item in bundle["receipts"]:
        if item["received_at"] >= day + "T16:00:00Z":
            item.update(
                published_at=day + "T15:40:00Z",
                first_received_at=day + "T15:45:00Z",
                received_at=day + "T15:45:00Z",
            )
    # Raw bars are available earlier in this probe; coverage alone remains 16:00.
    for bar in bundle["frames"][0]["market"]["bars"]:
        bar["available_at"] = day + "T15:45:00Z"
    refresh_receipts(bundle)
    for item in bundle["receipts"]:
        if item["domain"] == "topic" and item["received_at"] >= day + "T16:00:00Z":
            item.update(
                published_at=day + "T15:40:00Z",
                first_received_at=day + "T15:45:00Z",
                received_at=day + "T15:45:00Z",
            )
    bundle["factor_archive"]["lineage"][0]["available_at"] = day + "T15:50:00Z"
    with pytest.raises(ValueError, match="factor"):
        production().freeze_input_bundle(bundle, suppliers, candidate, now)
