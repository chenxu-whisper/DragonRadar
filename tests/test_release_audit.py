"""Strict release-audit tests for the Task 00-20 reset baseline."""

from __future__ import annotations

import hashlib
import json
import os
import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_reset_release.py"
EXTERNAL_GATES = (
    "licensed_real_input",
    "approved_official_rules",
    "real_model_evaluation",
    "public_sharing_permit",
    "twenty_trading_day_shadow_run",
)
SUPPLY_CHAIN_GATES = (
    "secret_scan",
    "python_dependency_scan",
    "node_dependency_scan",
    "sbom",
    "container_scan",
)


@pytest.fixture
def auditor():
    assert SCRIPT.exists(), "Task 21 release auditor has not been implemented"
    return runpy.run_path(str(SCRIPT))


def valid_inputs():
    return {
        "git": {
            "audited_commit": "a" * 40,
            "branch": None,
            "detached": True,
            "configured_upstream": "origin/main",
            "base_ref": "origin/main",
            "base_commit": "b" * 40,
            "merge_base": "b" * 40,
            "ahead": 12,
            "behind": 0,
            "tracked_dirty_paths": [],
            "untracked_count": 0,
            "remote_freshness": {
                "status": "CURRENT",
                "fetch_head_present": True,
                "fetch_age_seconds": 1,
            },
        },
        "migration": {
            "valid": True,
            "implementation_commit": "a" * 40,
            "source_artifacts": {
                "wheel": {"sha256": "1" * 64},
                "restore": {"sha256": "2" * 64},
            },
        },
        "task00_20": {
            "valid": True,
            "implementation_commit": "c" * 40,
            "inventory": [],
        },
        "external_gates": {gate: "BLOCKED" for gate in EXTERNAL_GATES},
        "supply_chain": {gate: "BLOCKED" for gate in SUPPLY_CHAIN_GATES},
    }


def test_external_blockers_and_scans_cannot_be_overridden(auditor):
    payload = valid_inputs()

    decisions = auditor["derive_decisions"](payload)

    assert decisions == {
        "migration_readiness": "GO",
        "task00_20_engineering": "GO",
        "branch_cut": "GO",
        "production_release": "NO_GO",
        "overall_status": "NO_GO",
    }
    assert tuple(payload["external_gates"]) == EXTERNAL_GATES
    assert tuple(payload["supply_chain"]) == SUPPLY_CHAIN_GATES


@pytest.mark.parametrize("section", ["external_gates", "supply_chain"])
def test_caller_cannot_self_assert_trusted_gate_evidence(auditor, section):
    payload = valid_inputs()
    payload[section] = {gate: "VERIFIED" for gate in payload[section]}

    with pytest.raises(ValueError, match="cannot self-assert"):
        auditor["derive_decisions"](payload)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("root", "overall_status", "GO"),
        ("root", "status", "pass"),
        ("migration", "status", "GO"),
        ("task00_20", "pass", True),
        ("external_gates", "licensed_input", "GO"),
        ("supply_chain", "dependency_scan", "GO"),
    ],
)
def test_caller_fields_and_unknown_gate_ids_are_rejected(
    auditor,
    section,
    field,
    value,
):
    payload = valid_inputs()
    target = payload if section == "root" else payload[section]
    target[field] = value

    with pytest.raises(ValueError, match="unexpected field"):
        auditor["derive_decisions"](payload)


def test_dirty_or_unbased_or_stale_branch_blocks_branch_cut(auditor):
    for mutation in (
        lambda git: git["tracked_dirty_paths"].append("tracked.txt"),
        lambda git: git.update(configured_upstream=None),
        lambda git: git["remote_freshness"].update(status="STALE"),
    ):
        payload = valid_inputs()
        mutation(payload["git"])
        if payload["git"]["configured_upstream"] is None:
            payload["git"]["branch"] = "feature/prd-reset"
            payload["git"]["detached"] = False

        assert auditor["derive_decisions"](payload)["branch_cut"] == "NO_GO"


def test_collect_git_state_uses_argv_and_captures_before_output_creation(
    auditor,
    tmp_path,
    monkeypatch,
):
    calls = []
    output = tmp_path / "nested" / "release-manifest.json"

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs, output.parent.exists()))
        responses = {
            ("git", "rev-parse", "HEAD"): ("a" * 40 + "\n", 0),
            ("git", "symbolic-ref", "--quiet", "--short", "HEAD"): (
                "feature/test\n",
                0,
            ),
            (
                "git",
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{upstream}",
            ): (
                "",
                128,
            ),
            ("git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"): (
                "origin/main\n",
                0,
            ),
            ("git", "rev-parse", "origin/main"): ("b" * 40 + "\n", 0),
            ("git", "merge-base", "HEAD", "origin/main"): ("b" * 40 + "\n", 0),
            ("git", "rev-list", "--left-right", "--count", "origin/main...HEAD"): (
                "0\t3\n",
                0,
            ),
            ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all"): (
                " M tracked.txt\0?? untracked.txt\0",
                0,
            ),
            ("git", "rev-parse", "--git-common-dir"): (".git\n", 0),
        }
        stdout, returncode = responses[tuple(argv)]
        return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setitem(
        auditor["collect_git_state"].__globals__,
        "_remote_freshness",
        lambda _root: {
            "status": "STALE",
            "fetch_head_present": True,
            "fetch_age_seconds": 100000,
        },
    )

    state = auditor["collect_git_state"](tmp_path)

    assert state["tracked_dirty_paths"] == ["tracked.txt"]
    assert state["untracked_count"] == 1
    assert state["configured_upstream"] is None
    assert all(isinstance(call[0], list) for call in calls)
    assert all(call[1]["shell"] is False for call in calls)
    assert all(call[2] is False for call in calls)


def _write_task20_bundle(root: Path):
    evidence = root / "docs/acceptance/evidence/task20"
    evidence.mkdir(parents=True)
    summary = {
        "schema": "dragon-radar.task20-acceptance-summary",
        "schema_version": 1,
        "implementation_commit": "c" * 40,
        "execution_provenance": {
            "source": "Executed by scripts/verify_reset.py.",
            "tracked_clean_before_and_after": True,
            "focused": {
                "mode": "focused",
                "selected_gates": "pass",
                "overall_status": "blocked_by_design",
                "reason": "full-only gates omitted",
            },
            "full": {
                "mode": "full",
                "automated_acceptance": "pass",
                "required_gates": 21,
                "failed_gates": 0,
                "skipped_tests": 0,
            },
        },
        "native_counts": {},
        "quality_gates": {},
        "external_blockers": [],
        "migration_added": False,
    }
    summary_path = evidence / "acceptance-summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    manifest = {
        "schema": "dragon-radar.task20-committed-evidence",
        "schema_version": 1,
        "implementation_commit": "c" * 40,
        "execution_provenance": "Executed evidence, not cryptographic attestation.",
        "files": [
            {
                "path": "acceptance-summary.json",
                "sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
                "bytes": len(summary_path.read_bytes()),
            }
        ],
    }
    manifest_path = evidence / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, summary_path


def test_task20_collection_invokes_strict_verifier_and_rejects_tamper(
    auditor,
    tmp_path,
    monkeypatch,
):
    manifest, summary = _write_task20_bundle(tmp_path)
    calls = []
    globals_ = auditor["collect_task20_evidence"].__globals__

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="verified", stderr="")

    monkeypatch.setitem(globals_, "_run", run)
    monkeypatch.setitem(
        globals_,
        "_tracked_paths",
        lambda _root, _commit=None: {
            manifest.relative_to(tmp_path).as_posix(),
            summary.relative_to(tmp_path).as_posix(),
        },
    )
    monkeypatch.setitem(globals_, "_is_ancestor", lambda *_: True)
    monkeypatch.setitem(globals_, "collect_task_inventory", lambda *_: [])

    result = auditor["collect_task20_evidence"](tmp_path, "a" * 40)

    assert result["valid"] is True
    assert result["executed_gates"] == {
        "required": 21,
        "failed": 0,
        "skipped": 0,
    }
    assert result["attestation"] == "committed_execution_provenance_only"
    assert calls[0][0][1].endswith("verify_task20_bundle.py")

    summary.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        auditor["collect_task20_evidence"](tmp_path, "a" * 40)


def _wheel_evidence(commit="a" * 40):
    return {
        "schema": "dragon-radar.installed-wheel-migrations",
        "schema_version": 1,
        "wheel_filename": "dragon_radar-0.1.0-py3-none-any.whl",
        "wheel_sha256": "1" * 64,
        "audited_commit": commit,
        "imported_from": "installed/dragon_radar/__init__.py",
        "fresh": {"to_revision": "0010_ai_tasks", "ready": True},
        "supported_upgrades": [
            {
                "from_revision": revision,
                "to_revision": "0010_ai_tasks",
                "sentinel": f"{revision}_sentinel",
                "sentinel_preserved": True,
                "ready": True,
                **(
                    {
                        "sentinel_before": {
                            key: None
                            for key in (
                                "rule_record_id",
                                "event_id",
                                "regulation_snapshot_id",
                                "binding_snapshot_id",
                                "binding_event_id",
                                "source_id",
                                "source_version",
                                "event_source_completeness",
                                "event_source_id",
                                "event_source_version",
                            )
                        },
                        "sentinel_after": {
                            key: None
                            for key in (
                                "rule_record_id",
                                "event_id",
                                "regulation_snapshot_id",
                                "binding_snapshot_id",
                                "binding_event_id",
                                "source_id",
                                "source_version",
                                "event_source_completeness",
                                "event_source_id",
                                "event_source_version",
                            )
                        },
                    }
                    if revision == "0009_regulation"
                    else {}
                ),
            }
            for revision in (
                "0001_foundation",
                "0003_private_review",
                "0004a_topic_attempt_ranges",
                "0006_market_readmodels",
                "0007_role_readmodels",
                "0007c_analysis_jobs",
                "0008_news",
                "0009_regulation",
                "0009d_reg_event_complete_set",
                "0010_ai_tasks",
            )
        ],
    }


def _restore_evidence():
    counts = {"snapshots": 1}
    return {
        "schema": "dragon-radar.backup-restore-drill",
        "schema_version": 1,
        "rollback_mode": "backup_restore_only",
        "backup_format": "custom",
        "backup_sha256": "2" * 64,
        "source_fingerprint": "3" * 64,
        "restore_fingerprint": "3" * 64,
        "source_unchanged": True,
        "quiescence_enforced": True,
        "measured_data_loss_rows": 0,
        "data_loss_window_seconds": 0.0,
        "write_freeze_seconds": 0.1,
        "elapsed_seconds": 1.0,
        "revision": "0010_ai_tasks",
        "restored_ready": True,
        "sentinel_preserved": True,
        "permissions_preserved": True,
        "app_semantics_verified": True,
        "ready_before_canonicalization": True,
        "canonicalized_schema_objects": 0,
        "source_counts": counts,
        "restored_counts": counts,
        "cleanup": {
            "source_database_removed": True,
            "restore_database_removed": True,
            "app_role_removed": True,
            "backup_file_removed": True,
        },
        "disposable": {
            "role": "task21_role_token",
            "source_database": "task21_source_token",
            "restore_database": "task21_restore_token",
            "dump_path": "/tmp/task21_restore_token.dump",
        },
        "targets": {
            "rpo_seconds": 3600,
            "rto_seconds": 14400,
            "approval_status": "suggested_unapproved",
        },
    }


def test_migration_collection_validates_actual_decisive_data(auditor, tmp_path):
    wheel_path = tmp_path / "wheel.json"
    restore_path = tmp_path / "restore.json"
    wheel_path.write_text(json.dumps(_wheel_evidence()), encoding="utf-8")
    restore_path.write_text(json.dumps(_restore_evidence()), encoding="utf-8")

    result = auditor["collect_migration_evidence"](
        wheel_path,
        restore_path,
        "a" * 40,
    )

    assert result["valid"] is True
    assert result["implementation_commit"] == "a" * 40
    assert result["wheel"]["wheel_sha256"] == "1" * 64
    assert len(result["wheel"]["supported_upgrades"]) == 10
    assert result["restore"]["source_fingerprint"] == "3" * 64
    assert set(result["source_artifacts"]) == {"wheel", "restore"}
    assert "disposable" not in result["restore"]


@pytest.mark.parametrize(
    ("artifact", "mutation", "message"),
    [
        ("wheel", lambda value: value.update(status="pass"), "unexpected field"),
        ("wheel", lambda value: value["fresh"].update(ready=1), "fresh"),
        (
            "wheel",
            lambda value: value.update(audited_commit="b" * 40),
            "audited commit",
        ),
        (
            "wheel",
            lambda value: value["supported_upgrades"][0].update(
                sentinel_preserved=False
            ),
            "supported upgrade",
        ),
        (
            "restore",
            lambda value: value.update(source_unchanged=False),
            "restore evidence",
        ),
        (
            "restore",
            lambda value: value.update(restore_fingerprint="4" * 64),
            "fingerprint",
        ),
        (
            "restore",
            lambda value: value["cleanup"].update(backup_file_removed=False),
            "cleanup",
        ),
    ],
)
def test_migration_tamper_and_caller_status_are_rejected(
    auditor,
    tmp_path,
    artifact,
    mutation,
    message,
):
    wheel = _wheel_evidence()
    restore = _restore_evidence()
    mutation(wheel if artifact == "wheel" else restore)
    wheel_path = tmp_path / "wheel.json"
    restore_path = tmp_path / "restore.json"
    wheel_path.write_text(json.dumps(wheel), encoding="utf-8")
    restore_path.write_text(json.dumps(restore), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        auditor["collect_migration_evidence"](
            wheel_path,
            restore_path,
            "a" * 40,
        )


def test_strict_json_rejects_duplicates_and_path_traversal(auditor, tmp_path):
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema":"first","schema":"second"}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        auditor["load_strict_json"](duplicate)

    for value in ("../escape.json", "/private/evidence.json", r"C:\private\a.json"):
        with pytest.raises(ValueError, match="normalized relative path"):
            auditor["validate_relative_path"](value)


def test_task_inventory_requires_exact_tracked_hashes(auditor, tmp_path, monkeypatch):
    evidence = tmp_path / "docs/acceptance/reset-baseline.md"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("baseline\n", encoding="utf-8")
    mapping = {task: ["docs/acceptance/reset-baseline.md"] for task in range(21)}
    monkeypatch.setitem(
        auditor["collect_task_inventory"].__globals__,
        "TASK_EVIDENCE_MAP",
        mapping,
    )
    monkeypatch.setitem(
        auditor["collect_task_inventory"].__globals__,
        "_tracked_paths",
        lambda *_: {"docs/acceptance/reset-baseline.md"},
    )

    inventory = auditor["collect_task_inventory"](tmp_path, "a" * 40)

    assert [item["task"] for item in inventory] == list(range(21))
    assert (
        inventory[0]["files"][0]["sha256"]
        == hashlib.sha256(evidence.read_bytes()).hexdigest()
    )

    evidence.unlink()
    with pytest.raises(ValueError, match="missing"):
        auditor["collect_task_inventory"](tmp_path, "a" * 40)


def test_tracked_paths_disables_git_path_quoting(auditor, tmp_path, monkeypatch):
    observed = []

    def output(_root, argv, **_kwargs):
        observed.append(argv)
        return "docs/DragonRadar_开发需求PRD.md\n"

    monkeypatch.setitem(auditor["_tracked_paths"].__globals__, "_git_output", output)

    assert auditor["_tracked_paths"](tmp_path, "a" * 40) == {
        "docs/DragonRadar_开发需求PRD.md"
    }
    assert observed == [
        [
            "-c",
            "core.quotepath=false",
            "ls-tree",
            "-r",
            "--name-only",
            "a" * 40,
        ]
    ]


def test_atomic_manifest_failure_preserves_original(auditor, tmp_path, monkeypatch):
    output = tmp_path / "nested" / "manifest.json"
    output.parent.mkdir()
    output.write_text("original\n", encoding="utf-8")
    monkeypatch.setattr(
        os,
        "replace",
        lambda *_: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(OSError, match="replace failed"):
        auditor["write_release_manifest"](output, {"schema": "example"})

    assert output.read_text(encoding="utf-8") == "original\n"
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))


def test_manifest_safety_rejects_secrets_absolute_paths_and_environment(auditor):
    for value in (
        {"password": "secret"},
        {"database_url": "postgresql://user:secret@localhost/db"},
        {"path": r"C:\Users\Admin\private.txt"},
        {"environment": {"TOKEN": "secret"}},
    ):
        with pytest.raises(ValueError, match="unsafe release manifest"):
            auditor["validate_manifest_safety"](value)


def test_check_accepts_truthful_no_go_and_docs_only_head_relation(
    auditor,
    tmp_path,
    monkeypatch,
):
    payload = {
        "schema": "dragon-radar.reset-release-audit",
        "schema_version": 1,
        "implementation_commit": "a" * 40,
        "implementation_commit_relation": ("same_head_or_docs_evidence_only_child"),
        "git": valid_inputs()["git"],
        "migration": valid_inputs()["migration"],
        "task00_20": valid_inputs()["task00_20"],
        "external_gates": valid_inputs()["external_gates"],
        "supply_chain": valid_inputs()["supply_chain"],
        "decisions": {
            "migration_readiness": "GO",
            "task00_20_engineering": "GO",
            "branch_cut": "GO",
            "production_release": "NO_GO",
            "overall_status": "NO_GO",
        },
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    globals_ = auditor["check_release_manifest"].__globals__
    monkeypatch.setitem(globals_, "_tracked_paths", lambda *_: {"manifest.json"})
    monkeypatch.setitem(globals_, "_git_output", lambda *_args, **_kwargs: "b" * 40)
    monkeypatch.setitem(globals_, "_validate_commit_relation", lambda *_: None)
    monkeypatch.setitem(globals_, "_validate_inventory_at_head", lambda *_: None)

    assert auditor["check_release_manifest"](tmp_path, path) == payload


def test_commit_relation_rejects_non_docs_child(auditor, tmp_path, monkeypatch):
    calls = []

    def output(_root, argv, **_kwargs):
        calls.append(argv)
        if argv[:2] == ["diff-tree", "--no-commit-id"]:
            return "scripts/audit_reset_release.py\n"
        return "a" * 40

    monkeypatch.setitem(
        auditor["_validate_commit_relation"].__globals__,
        "_git_output",
        output,
    )
    with pytest.raises(ValueError, match="docs/evidence-only"):
        auditor["_validate_commit_relation"](
            tmp_path,
            "a" * 40,
            "b" * 40,
            "same_head_or_docs_evidence_only_child",
        )


def test_malformed_bool_integer_and_stale_artifact_are_rejected(
    auditor,
    tmp_path,
):
    wheel = _wheel_evidence()
    restore = _restore_evidence()
    wheel["schema_version"] = True
    wheel_path = tmp_path / "wheel.json"
    restore_path = tmp_path / "restore.json"
    wheel_path.write_text(json.dumps(wheel), encoding="utf-8")
    restore_path.write_text(json.dumps(restore), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        auditor["collect_migration_evidence"](
            wheel_path,
            restore_path,
            "a" * 40,
        )

    wheel = _wheel_evidence()
    wheel_path.write_text(json.dumps(wheel), encoding="utf-8")
    restore_path.write_text(json.dumps(restore), encoding="utf-8")
    wheel_path.touch()
    os.utime(wheel_path, (1, 1))
    with pytest.raises(ValueError, match="stale"):
        auditor["require_fresh_artifact"](wheel_path, generated_after=2)


def test_decision_derivation_rejects_false_internal_evidence(auditor):
    payload = valid_inputs()
    payload["migration"]["valid"] = False
    payload["task00_20"]["valid"] = False

    decisions = auditor["derive_decisions"](payload)

    assert decisions["migration_readiness"] == "NO_GO"
    assert decisions["task00_20_engineering"] == "NO_GO"
    assert decisions["overall_status"] == "NO_GO"
