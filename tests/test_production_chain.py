"""Test-only signed chains exercise the full production report boundary."""

import copy
from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from test_production_inputs import (
    digest,
    parse,
    refresh_receipts,
)
from test_production_inputs import (
    inputs as inputs,
)
from test_production_shadow import NOW, history
from test_production_trust import OPENSSL, protect, run
from test_production_trust import rsa_keys as rsa_keys

from dragon_radar.production.report import evaluate_production
from dragon_radar.production.shadow import entry_hash
from dragon_radar.production.trust import (
    TrustedIssuer,
    TrustedKey,
    TrustPolicy,
    canonical_json,
)
from dragon_radar.research.engine import FactorFrame, run_backtest
from dragon_radar.research.models import BacktestConfig
from dragon_radar.research.pit import PitAnchor, PitFrame


@pytest.fixture
def signed_chain(inputs, tmp_path, rsa_keys):
    template, _, supplier, candidate, _ = inputs
    root = tmp_path / "test-only-chain"
    root.mkdir()
    private, public = rsa_keys
    (root / "key.pem").write_bytes(public)
    context = dict(
        subject="test-owner", purpose="research", target="local", audience="owner"
    )
    versions = template["candidate"]
    candidate = {**candidate, "rule_scope": {"exchange": "SSE"}}
    policy = TrustPolicy(
        openssl_executable=Path(OPENSSL),
        issuers={
            "test-only-issuer": TrustedIssuer(
                keys={
                    "key": TrustedKey(
                        public_key_path="key.pem", sha256=sha256(public).hexdigest()
                    )
                },
                gates=frozenset(
                    {"real_input", "official_rules", "shadow_run_20_trading_days"}
                ),
                scopes={key: frozenset({value}) for key, value in context.items()},
                approvers=frozenset({"test-only-reviewer"}),
            )
        },
    )
    envelopes = {}

    def sign(name, gate, payload, materials, issued, approved):
        refs = []
        for key, value in materials.items():
            content = canonical_json(value)
            path = f"{name}-{key}.json"
            (root / path).write_bytes(content)
            refs.append(
                dict(material_id=key, path=path, sha256=sha256(content).hexdigest())
            )
        record = {
            "schema": "dragon-radar-production-evidence/v1",
            "gate_id": gate,
            "evidence_id": name,
            "kind": "real",
            "issuer": "test-only-issuer",
            "key_id": "key",
            "approval_id": f"approval-{name}",
            "approver": "test-only-reviewer",
            "issued_at": issued,
            "approved_at": approved,
            "valid_from": approved,
            "expires_at": "2026-11-17T00:00:00Z",
            "revoked": False,
            "candidate": versions,
            "scopes": {key: [value] for key, value in context.items()},
            "materials": refs,
            "payload": payload,
            "signature": {"algorithm": "RSA-PSS-SHA256", "path": f"{name}.sig"},
        }
        (root / f"{name}.json").write_bytes(canonical_json(record))
        (root / f"{name}.sig").write_bytes(
            run(
                OPENSSL,
                "dgst",
                "-sha256",
                "-sign",
                str(private),
                "-sigopt",
                "rsa_padding_mode:pss",
                "-sigopt",
                "rsa_pss_saltlen:32",
                "-sigopt",
                "rsa_mgf1_md:sha256",
                str(root / f"{name}.json"),
            )
        )
        envelopes[name] = record

    calendar, records = history()
    entries = records["entries"]
    previous = "0" * 64
    last_materials = None
    for index, entry in enumerate(entries):
        day = datetime.fromisoformat(entry["trade_date"]).date()
        days = [day]
        while len(days) < 5:
            day -= timedelta(days=1)
            if day.weekday() < 5:
                days.append(day)
        dates = dict(
            zip(
                [frame["trade_date"] for frame in template["frames"]],
                [value.isoformat() for value in reversed(days)],
                strict=True,
            )
        )

        def shifted(value, dates=dates):
            if isinstance(value, dict):
                return {key: shifted(item) for key, item in value.items()}
            if isinstance(value, list):
                return [shifted(item) for item in value]
            if isinstance(value, str) and value[:10] in dates:
                mapped = dates[value[:10]] + value[10:]
                if "T" in value:
                    # Engine fixtures use 16:00 UTC; collect at 07:00 UTC instead.
                    return (
                        (
                            datetime.fromisoformat(mapped.replace("Z", "+00:00"))
                            - timedelta(hours=9)
                        )
                        .isoformat()
                        .replace("+00:00", "Z")
                    )
                return mapped
            return value

        bundle = shifted(copy.deepcopy(template))
        sequence_offset = (index + 1) * 1000
        bundle["publication_sequence"] += sequence_offset
        bundle["anchor"]["publication_sequence"] += sequence_offset
        for position, (frame, publication, lineage) in enumerate(
            zip(
                bundle["frames"],
                bundle["publications"],
                bundle["factor_archive"]["lineage"],
                strict=True,
            )
        ):
            frame["publication_sequence"] += sequence_offset
            publication["publication_sequence"] += sequence_offset
            publication["snapshot_id"] = str(UUID(int=sequence_offset + position))
            publication["market_snapshot_id"] = str(
                UUID(int=100000 + sequence_offset + position)
            )
            lineage["publication_sequence"] += sequence_offset
            lineage["market_snapshot_id"] = publication["market_snapshot_id"]
        bundle["snapshot_id"] = bundle["publications"][-1]["snapshot_id"]
        bundle["config"]["snapshot_id"] = bundle["snapshot_id"]
        bundle["config"]["input_manifests"]["domains"][0]["snapshot_id"] = bundle[
            "publications"
        ][-1]["market_snapshot_id"]
        refresh_receipts(bundle)
        actual = run_backtest(
            parse(BacktestConfig, bundle["config"]),
            tuple(parse(PitFrame, frame) for frame in bundle["frames"]),
            tuple(
                parse(FactorFrame, frame)
                for frame in bundle["factor_archive"]["frames"]
            ),
            anchor=parse(PitAnchor, bundle["anchor"]),
        ).model_dump(mode="json")
        artifact = {
            "schema": "dragon-radar-production-run/v1",
            "candidate": versions,
            "snapshot_id": bundle["snapshot_id"],
            "publication_sequence": bundle["publication_sequence"],
            "config_sha256": digest(bundle["config"]),
            "input_sha256": digest(bundle),
            "result_sha256": digest(actual),
            "result": actual,
            "started_at": entry["started_at"],
            "finished_at": entry["finished_at"],
        }
        entry.update(
            versions=versions,
            publication_id=bundle["snapshot_id"],
            publication_sequence=bundle["publication_sequence"],
            input_sha256=digest(bundle),
            source_received_at=max(item["received_at"] for item in bundle["receipts"]),
            available_at=bundle["factor_archive"]["lineage"][-1]["available_at"],
            reviewer="test-only-reviewer",
            previous_entry_hash=previous,
        )
        entry["entry_hash"] = previous = entry_hash(entry)
        last_materials = dict(bundle=bundle, run=artifact, supplier=supplier)
        sign(
            f"day-{index}",
            "shadow_run_20_trading_days",
            dict(
                type="shadow_day",
                entry="entry",
                bundle="bundle",
                run="run",
                supplier="supplier",
            ),
            {"entry": entry, **last_materials},
            entry["finished_at"],
            entry["reviewed_at"],
        )
    sign(
        "calendar",
        "official_rules",
        dict(
            type="trading_calendar",
            calendar="calendar",
            exchange="SSE",
            window_start=entries[0]["trade_date"],
            window_end=entries[-1]["trade_date"],
            complete=True,
        ),
        {"calendar": calendar},
        "2026-09-29T12:00:00Z",
        "2026-09-29T13:00:00Z",
    )
    sign(
        "aggregate",
        "shadow_run_20_trading_days",
        dict(
            calendar="calendar",
            entries="entries",
            calendar_evidence="calendar.json",
            daily_evidence=[f"day-{index}.json" for index in range(20)],
        ),
        dict(calendar=calendar, entries=records),
        "2026-11-15T00:00:00Z",
        "2026-11-15T01:00:00Z",
    )
    sign(
        "input",
        "real_input",
        dict(bundle="bundle", run="run", supplier="supplier"),
        last_materials,
        "2026-11-15T00:00:00Z",
        "2026-11-15T01:00:00Z",
    )
    protect(root)

    def evaluate(override=policy):
        return evaluate_production(
            root,
            override,
            versions,
            context,
            {
                "real_input": "input.json",
                "shadow_run_20_trading_days": "aggregate.json",
            },
            NOW,
            expected_code=versions["code"],
            semantic_candidate={
                key: value for key, value in candidate.items() if key not in versions
            },
        )

    def drift_calendar():
        bundle = last_materials["bundle"]
        for frame in bundle["frames"]:
            frame["market"]["calendar"]["version"] = "c2"
            frame["market"]["calendar_version"] = "c2"
        bundle["config"]["input_manifests"]["versions"]["calendar"] = "c2"
        refresh_receipts(bundle)
        artifact = last_materials["run"]
        artifact["config_sha256"] = digest(bundle["config"])
        artifact["input_sha256"] = digest(bundle)
        artifact["result"] = run_backtest(
            parse(BacktestConfig, bundle["config"]),
            tuple(parse(PitFrame, frame) for frame in bundle["frames"]),
            tuple(
                parse(FactorFrame, frame)
                for frame in bundle["factor_archive"]["frames"]
            ),
            anchor=parse(PitAnchor, bundle["anchor"]),
        ).model_dump(mode="json")
        artifact["result_sha256"] = digest(artifact["result"])
        entry = entries[-1]
        entry["input_sha256"] = digest(bundle)
        entry["entry_hash"] = entry_hash(entry)
        day_record = envelopes["day-19"]
        sign(
            "day-19",
            day_record["gate_id"],
            day_record["payload"],
            {"entry": entry, **last_materials},
            day_record["issued_at"],
            day_record["approved_at"],
        )
        aggregate = envelopes["aggregate"]
        sign(
            "aggregate",
            aggregate["gate_id"],
            aggregate["payload"],
            dict(calendar=calendar, entries=records),
            aggregate["issued_at"],
            aggregate["approved_at"],
        )
        input_record = envelopes["input"]
        sign(
            "input",
            input_record["gate_id"],
            input_record["payload"],
            last_materials,
            input_record["issued_at"],
            input_record["approved_at"],
        )
        protect(root)

    return root, evaluate, policy, drift_calendar


def test_full_signed_inputs_and_original_twenty_day_chain_pass_independently(
    signed_chain,
):
    _, evaluate, _, _ = signed_chain
    report = evaluate()
    assert report["gates"]["real_input"]["status"] == "pass", report
    assert report["gates"]["shadow_run_20_trading_days"]["status"] == "pass", report
    assert report["gates"]["shadow_run_20_trading_days"]["trading_days"] == 20
    assert report["gates"]["official_rules"]["status"] == "blocked"
    assert report["production_release"] == "blocked"


def test_nested_revocations_and_raw_tamper_cannot_be_hidden_by_signed_aggregate(
    signed_chain,
):
    root, evaluate, policy, _ = signed_chain
    for identity in ("calendar", "day-0", "day-19"):
        revoked = replace(policy, revoked_evidence_ids=frozenset({identity}))
        report = evaluate(revoked)
        assert report["gates"]["shadow_run_20_trading_days"]["status"] == "fail", (
            identity
        )
        assert report["gates"]["real_input"]["status"] == "pass"
    raw = root / "day-19-bundle.json"
    raw.write_bytes(raw.read_bytes() + b" ")
    protect(root)
    report = evaluate()
    assert report["gates"]["shadow_run_20_trading_days"]["status"] == "fail"
    assert report["gates"]["real_input"]["status"] == "pass"


def test_valid_signed_run_using_different_calendar_cannot_count_as_shadow_day(
    signed_chain,
):
    _, evaluate, _, drift_calendar = signed_chain
    drift_calendar()
    report = evaluate()
    assert report["gates"]["real_input"]["status"] == "pass", report
    assert report["gates"]["shadow_run_20_trading_days"]["status"] == "fail", report
