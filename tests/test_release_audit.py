"""Strict release-audit tests for the Task 00-20 reset baseline."""

from __future__ import annotations

import hashlib
import json
import os
import runpy
import subprocess
from copy import deepcopy
from datetime import UTC, datetime, timedelta
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
    generated_at = datetime.now(UTC).isoformat()
    validated_at = generated_at
    remote_freshness = {
        "method": "git_ls_remote",
        "status": "CURRENT",
        "remote": "origin",
        "remote_ref": "refs/heads/main",
        "local_oid": "b" * 40,
        "remote_oid": "b" * 40,
        "checked_at": validated_at,
        "error": None,
    }
    execution = {
        "audited_commit": "a" * 40,
        "branch": None,
        "detached": True,
        "configured_upstream": None,
        "base_ref": "origin/main",
        "base_commit": "b" * 40,
        "merge_base": "b" * 40,
        "ahead": 12,
        "behind": 0,
        "tracked_dirty_paths": [],
        "untracked_count": 0,
        "remote_freshness": {
            **remote_freshness,
            "status": "ABSENT",
            "remote": None,
            "remote_ref": None,
            "local_oid": None,
            "remote_oid": None,
        },
        "tracked_clean_before_and_after": True,
    }
    candidate = {
        **execution,
        "branch": "feature/prd-reset",
        "detached": False,
        "configured_upstream": "origin/main",
        "tracked_dirty_paths": [],
        "remote_freshness": remote_freshness,
    }
    candidate.pop("tracked_clean_before_and_after")
    wheel = _wheel_evidence()
    wheel["generated_at"] = generated_at
    wheel.pop("imported_from")
    restore = _restore_evidence()
    restore["generated_at"] = generated_at
    restore["audited_commit"] = "a" * 40
    restore.pop("disposable")
    artifact = {
        "sha256": "1" * 64,
        "bytes": 1,
        "generated_at": generated_at,
        "age_seconds_at_audit": 0.0,
        "validated_at": validated_at,
    }
    return {
        "git": {"execution_source": execution, "candidate": candidate},
        "migration": {
            "valid": True,
            "implementation_commit": "a" * 40,
            "source_artifacts": {
                "wheel": artifact,
                "restore": {**artifact, "sha256": "2" * 64},
            },
            "graph": {
                "head": "0010_ai_tasks",
                "revision_count": 18,
                "release_manifest_sha256": "3" * 64,
                "schema_contract_sha256": "4" * 64,
            },
            "wheel": wheel,
            "restore": restore,
        },
        "task00_20": {
            "valid": True,
            "implementation_commit": "c" * 40,
            "attestation": "committed_execution_provenance_only",
            "bundle_manifest_sha256": "5" * 64,
            "executed_gates": {"required": 21, "failed": 0, "skipped": 0},
            "inventory": [
                {
                    "task": task,
                    "files": [
                        {
                            "path": f"docs/task-{task:02d}.md",
                            "sha256": f"{task + 10:064x}",
                            "bytes": task + 1,
                        }
                    ],
                }
                for task in range(21)
            ],
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
        mutation(payload["git"]["candidate"])

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
            ("for-each-ref", "--format=%(refname)", "refs/replace/"): ("", 0),
            ("rev-parse", "HEAD"): ("a" * 40 + "\n", 0),
            ("symbolic-ref", "--quiet", "--short", "HEAD"): (
                "feature/test\n",
                0,
            ),
            (
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{upstream}",
            ): (
                "",
                128,
            ),
            ("config", "--get", "branch.feature/test.remote"): ("", 1),
            ("config", "--get", "branch.feature/test.merge"): ("", 1),
            ("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"): (
                "origin/main\n",
                0,
            ),
            ("rev-parse", "origin/main"): ("b" * 40 + "\n", 0),
            ("merge-base", "HEAD", "origin/main"): ("b" * 40 + "\n", 0),
            ("rev-list", "--left-right", "--count", "origin/main...HEAD"): (
                "0\t3\n",
                0,
            ),
            ("status", "--porcelain=v1", "-z", "--untracked-files=all"): (
                " M tracked.txt\0?? untracked.txt\0",
                0,
            ),
        }
        assert argv[:2] == ["git", "--no-replace-objects"]
        stdout, returncode = responses[tuple(argv[2:])]
        return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)

    monkeypatch.setitem(auditor["collect_git_state"].__globals__, "_run", fake_run)
    monkeypatch.setitem(
        auditor["collect_git_state"].__globals__,
        "_remote_freshness",
        lambda _root, **_kwargs: {
            "method": "git_ls_remote",
            "status": "ABSENT",
            "remote": None,
            "remote_ref": None,
            "local_oid": None,
            "remote_oid": None,
            "checked_at": datetime.now(UTC).isoformat(),
            "error": None,
        },
    )

    state = auditor["collect_git_state"](tmp_path)

    assert state["tracked_dirty_paths"] == ["tracked.txt"]
    assert state["untracked_count"] == 1
    assert state["configured_upstream"] is None
    assert all(isinstance(call[0], list) for call in calls)
    assert all(call[1]["env"]["GIT_NO_REPLACE_OBJECTS"] == "1" for call in calls)
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
        "native_counts": {"backend": 1},
        "quality_gates": {"ruff_lint": "pass"},
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


def _commit_task20_fixture(
    root: Path, verifier_bytes: bytes | None
) -> tuple[str, Path]:
    _manifest, summary = _write_task20_bundle(root)
    verifier = root / "scripts" / "verify_task20_bundle.py"
    if verifier_bytes is not None:
        verifier.parent.mkdir(parents=True)
        verifier.write_bytes(verifier_bytes)
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "task21@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Task21 Test"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "-c", "core.autocrlf=false", "add", "--all"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "test fixture"],
        cwd=root,
        check=True,
    )
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
    ).strip()
    return commit, summary


def _isolate_task20_collection(auditor, monkeypatch):
    globals_ = auditor["collect_task20_evidence"].__globals__
    monkeypatch.setitem(globals_, "_is_ancestor", lambda *_: True)
    monkeypatch.setitem(globals_, "collect_task_inventory", lambda *_: [])


def test_task20_collection_uses_committed_verifier_and_artifacts(
    auditor,
    tmp_path,
    monkeypatch,
):
    committed_verifier = (ROOT / "scripts/verify_task20_bundle.py").read_bytes()
    commit, summary = _commit_task20_fixture(tmp_path, committed_verifier)
    _isolate_task20_collection(auditor, monkeypatch)

    (tmp_path / "scripts/verify_task20_bundle.py").write_text(
        "raise SystemExit('uncommitted verifier executed')\n",
        encoding="utf-8",
    )
    summary.write_text("uncommitted artifact bytes", encoding="utf-8")

    result = auditor["collect_task20_evidence"](tmp_path, commit)

    assert result["valid"] is True
    assert result["implementation_commit"] == "c" * 40


@pytest.mark.parametrize(
    "committed_verifier",
    [
        None,
        (b"from pathlib import Path\nraise SystemExit(Path(__file__).resolve())\n"),
    ],
    ids=("missing", "tampered"),
)
def test_task20_collection_rejects_missing_or_tampered_committed_verifier(
    auditor,
    tmp_path,
    monkeypatch,
    committed_verifier,
):
    commit, _summary = _commit_task20_fixture(tmp_path, committed_verifier)
    checkout_verifier = tmp_path / "scripts/verify_task20_bundle.py"
    checkout_verifier.parent.mkdir(parents=True, exist_ok=True)
    checkout_verifier.write_bytes(
        (ROOT / "scripts/verify_task20_bundle.py").read_bytes()
    )
    _isolate_task20_collection(auditor, monkeypatch)

    with pytest.raises(ValueError) as exc_info:
        auditor["collect_task20_evidence"](tmp_path, commit)

    message = str(exc_info.value)
    assert str(tmp_path.resolve()) not in message
    assert str(ROOT.resolve()) not in message


def test_task20_collection_invokes_strict_verifier_and_rejects_tamper(
    auditor,
    tmp_path,
    monkeypatch,
):
    manifest, summary = _write_task20_bundle(tmp_path)
    calls = []
    verifier_bytes = (ROOT / "scripts/verify_task20_bundle.py").read_bytes()
    blobs = {
        "scripts/verify_task20_bundle.py": verifier_bytes,
        manifest.relative_to(tmp_path).as_posix(): manifest.read_bytes(),
        summary.relative_to(tmp_path).as_posix(): summary.read_bytes(),
    }
    observed_verifier = {}
    globals_ = auditor["collect_task20_evidence"].__globals__

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[0] == os.fspath(Path(os.sys.executable)):
            observed_verifier["bytes"] = Path(argv[1]).read_bytes()
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
    monkeypatch.setitem(
        globals_,
        "_git_blob",
        lambda _root, _commit, path: blobs[path],
    )

    result = auditor["collect_task20_evidence"](tmp_path, "a" * 40)

    assert result["valid"] is True
    assert result["executed_gates"] == {
        "required": 21,
        "failed": 0,
        "skipped": 0,
    }
    assert result["attestation"] == "committed_execution_provenance_only"
    assert observed_verifier["bytes"] == verifier_bytes
    assert calls[-1][0][0] == os.fspath(Path(os.sys.executable))
    assert calls[-1][0][2] == "docs/acceptance/evidence/task20/manifest.json"
    assert calls[-1][1]["cwd"] != tmp_path.resolve()
    assert "PYTHONPATH" not in calls[-1][1]["env"]
    assert "PYTHONHOME" not in calls[-1][1]["env"]
    assert all(call[1]["timeout"] == 30 for call in calls)

    blobs[summary.relative_to(tmp_path).as_posix()] = b"tampered"
    with pytest.raises(ValueError, match="SHA-256"):
        auditor["collect_task20_evidence"](tmp_path, "a" * 40)


def _wheel_evidence(commit="a" * 40):
    return {
        "schema": "dragon-radar.installed-wheel-migrations",
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
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
    counts = {
        "source_grants": 1,
        "import_batches": 1,
        "snapshots": 1,
        "audit_events": 1,
    }
    return {
        "schema": "dragon-radar.backup-restore-drill",
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "audited_commit": "a" * 40,
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


def test_migration_collection_validates_actual_decisive_data(
    auditor,
    tmp_path,
    monkeypatch,
):
    verifier_bytes = b"# audited verifier\n"
    verifier_sha = hashlib.sha256(verifier_bytes).hexdigest()
    verification = {
        "schema": "dragon-radar.release-migration-verification",
        "schema_version": 1,
        "audited_commit": "a" * 40,
        "head": "0010_ai_tasks",
        "revision_count": 18,
        "release_manifest_sha256": "f" * 64,
        "schema_contract_sha256": "f" * 64,
        "verifier_sha256": verifier_sha,
        "schema_validator_sha256": "f" * 64,
        "errors": [],
    }
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(verification),
            stderr="",
        )

    wheel_path = tmp_path / "wheel.json"
    restore_path = tmp_path / "restore.json"
    wheel_path.write_text(json.dumps(_wheel_evidence()), encoding="utf-8")
    restore_path.write_text(json.dumps(_restore_evidence()), encoding="utf-8")
    monkeypatch.setitem(
        auditor["collect_migration_evidence"].__globals__,
        "_run",
        run,
    )
    monkeypatch.setitem(
        auditor["collect_migration_evidence"].__globals__,
        "_git_blob_fingerprint",
        lambda *_: {"sha256": "f" * 64, "bytes": 1},
    )
    monkeypatch.setitem(
        auditor["collect_migration_evidence"].__globals__,
        "_git_blob",
        lambda *_: verifier_bytes,
    )

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
    assert len(calls) == 1
    assert calls[0][0][0] == os.fspath(Path(os.sys.executable))
    assert Path(calls[0][0][1]).name == "verify_release_migrations.py"
    assert calls[0][0][2:] == [
        "--repository-root",
        os.fspath(ROOT),
        "--audited-commit",
        "a" * 40,
        "--json",
    ]
    assert "PYTHONPATH" not in calls[0][1]["env"]


def test_manifest_check_reexecutes_migration_verifier(
    auditor,
    monkeypatch,
):
    migration = valid_inputs()["migration"]
    calls = []
    verifier_bytes = b"# audited verifier\n"
    verifier_sha = hashlib.sha256(verifier_bytes).hexdigest()
    verification = {
        "schema": "dragon-radar.release-migration-verification",
        "schema_version": 1,
        "audited_commit": "a" * 40,
        "head": "0010_ai_tasks",
        "revision_count": 18,
        "release_manifest_sha256": "3" * 64,
        "schema_contract_sha256": "4" * 64,
        "verifier_sha256": verifier_sha,
        "schema_validator_sha256": "4" * 64,
        "errors": [],
    }
    globals_ = auditor["_validate_migration_at_commit"].__globals__
    monkeypatch.setitem(
        globals_,
        "_run",
        lambda argv, **kwargs: (
            calls.append((argv, kwargs))
            or SimpleNamespace(
                returncode=0,
                stdout=json.dumps(verification),
                stderr="",
            )
        ),
    )
    monkeypatch.setitem(
        globals_,
        "_git_blob_fingerprint",
        lambda _root, _commit, blob_path: {
            "sha256": (
                "3" * 64 if blob_path.endswith("release_manifest.json") else "4" * 64
            ),
            "bytes": 1,
        },
    )
    monkeypatch.setitem(globals_, "_git_blob", lambda *_: verifier_bytes)

    auditor["_validate_migration_at_commit"](ROOT, migration, "a" * 40)

    assert len(calls) == 1
    assert Path(calls[0][0][1]).name == "verify_release_migrations.py"
    assert "--audited-commit" in calls[0][0]


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
    monkeypatch,
):
    wheel = _wheel_evidence()
    restore = _restore_evidence()
    mutation(wheel if artifact == "wheel" else restore)
    wheel_path = tmp_path / "wheel.json"
    restore_path = tmp_path / "restore.json"
    wheel_path.write_text(json.dumps(wheel), encoding="utf-8")
    restore_path.write_text(json.dumps(restore), encoding="utf-8")
    monkeypatch.setitem(
        auditor["collect_migration_evidence"].__globals__,
        "_git_blob_fingerprint",
        lambda *_: {"sha256": "f" * 64, "bytes": 1},
    )

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
    committed = evidence.read_bytes()
    monkeypatch.setitem(
        auditor["collect_task_inventory"].__globals__,
        "_git_blob_fingerprint",
        lambda *_: {
            "sha256": hashlib.sha256(committed).hexdigest(),
            "bytes": len(committed),
        },
    )

    inventory = auditor["collect_task_inventory"](tmp_path, "a" * 40)

    assert [item["task"] for item in inventory] == list(range(21))
    assert (
        inventory[0]["files"][0]["sha256"]
        == hashlib.sha256(evidence.read_bytes()).hexdigest()
    )

    evidence.unlink()
    assert auditor["collect_task_inventory"](tmp_path, "a" * 40) == inventory


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
        "schema_version": 2,
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
    payload["git"]["candidate"]["configured_upstream"] = None
    payload["git"]["candidate"]["remote_freshness"].update(
        status="ABSENT",
        remote=None,
        remote_ref=None,
        local_oid=None,
        remote_oid=None,
    )
    payload["decisions"]["branch_cut"] = "NO_GO"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    globals_ = auditor["check_release_manifest"].__globals__
    monkeypatch.setitem(globals_, "_tracked_paths", lambda *_: {"manifest.json"})
    monkeypatch.setitem(globals_, "_git_output", lambda *_args, **_kwargs: "b" * 40)
    monkeypatch.setitem(globals_, "_validate_commit_relation", lambda *_: None)
    monkeypatch.setitem(globals_, "_validate_inventory_at_head", lambda *_: None)
    monkeypatch.setitem(globals_, "_validate_migration_at_commit", lambda *_: None)
    monkeypatch.setitem(
        globals_,
        "collect_task20_evidence",
        lambda *_args, **_kwargs: payload["task00_20"],
    )
    monkeypatch.setitem(
        globals_,
        "_git_blob",
        lambda _root, _commit, blob_path: (
            json.dumps(payload).encode()
            if blob_path == "manifest.json"
            else pytest.fail(f"unexpected blob: {blob_path}")
        ),
    )
    live = {
        key: value
        for key, value in deepcopy(payload["git"]["execution_source"]).items()
        if key != "tracked_clean_before_and_after"
    }
    live["audited_commit"] = "b" * 40
    monkeypatch.setitem(
        globals_,
        "collect_git_state",
        lambda *_args, **_kwargs: deepcopy(live),
    )

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


def test_malformed_bool_integer_is_rejected(
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


def test_decision_derivation_rejects_false_internal_evidence(auditor):
    payload = valid_inputs()
    payload["migration"]["valid"] = False
    payload["task00_20"]["valid"] = False

    decisions = auditor["derive_decisions"](payload)

    assert decisions["migration_readiness"] == "NO_GO"
    assert decisions["task00_20_engineering"] == "NO_GO"
    assert decisions["overall_status"] == "NO_GO"


def _init_committed_evidence_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "task21@example.test"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Task 21"],
        cwd=repository,
        check=True,
    )
    evidence = repository / "evidence.md"
    evidence.write_bytes(b"committed\nbytes\n")
    subprocess.run(["git", "add", "evidence.md"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "evidence"], cwd=repository, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repository, commit


def test_task_inventory_hashes_committed_git_blob_not_crlf_checkout(
    auditor,
    tmp_path,
    monkeypatch,
):
    repository, commit = _init_committed_evidence_repository(tmp_path)
    (repository / "evidence.md").write_bytes(b"committed\r\nbytes\r\n")
    monkeypatch.setitem(
        auditor["collect_task_inventory"].__globals__,
        "TASK_EVIDENCE_MAP",
        {task: ("evidence.md",) for task in range(21)},
    )

    inventory = auditor["collect_task_inventory"](repository, commit)

    expected = hashlib.sha256(b"committed\nbytes\n").hexdigest()
    assert inventory[0]["files"] == [
        {"path": "evidence.md", "sha256": expected, "bytes": 16}
    ]


def test_task_inventory_rejects_empty_mapping_duplicate_path_and_traversal(
    auditor,
    tmp_path,
    monkeypatch,
):
    repository, commit = _init_committed_evidence_repository(tmp_path)
    globals_ = auditor["collect_task_inventory"].__globals__
    invalid_mappings = (
        {**{task: ("evidence.md",) for task in range(21)}, 4: ()},
        {**{task: ("evidence.md",) for task in range(21)}, 4: ("evidence.md",) * 2},
        {**{task: ("evidence.md",) for task in range(21)}, 4: ("../evidence.md",)},
    )
    for mapping in invalid_mappings:
        monkeypatch.setitem(globals_, "TASK_EVIDENCE_MAP", mapping)
        with pytest.raises(ValueError):
            auditor["collect_task_inventory"](repository, commit)


def test_remote_freshness_uses_configured_upstream_ls_remote_argv(
    auditor,
    tmp_path,
    monkeypatch,
):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=("b" * 40) + "\trefs/heads/main\n",
            stderr="",
        )

    monkeypatch.setitem(auditor["_remote_freshness"].__globals__, "_run", run)
    result = auditor["_remote_freshness"](
        tmp_path,
        remote="origin",
        remote_ref="refs/heads/main",
        local_oid="b" * 40,
        verify_remote=True,
        checked_at="2026-10-09T12:00:00+00:00",
    )

    assert result == {
        "method": "git_ls_remote",
        "status": "CURRENT",
        "remote": "origin",
        "remote_ref": "refs/heads/main",
        "local_oid": "b" * 40,
        "remote_oid": "b" * 40,
        "checked_at": "2026-10-09T12:00:00+00:00",
        "error": None,
    }
    assert calls == [
        (
            [
                "git",
                "--no-replace-objects",
                "ls-remote",
                "--exit-code",
                "origin",
                "refs/heads/main",
            ],
            {
                "cwd": tmp_path,
                "timeout": 30,
                "env": calls[0][1]["env"],
                "text": True,
            },
        )
    ]
    assert calls[0][1]["env"]["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert all("FETCH_HEAD" not in " ".join(call[0]) for call in calls)


def test_remote_freshness_absent_or_unverified_upstream_is_not_current(
    auditor,
    tmp_path,
):
    absent = auditor["_remote_freshness"](
        tmp_path,
        remote=None,
        remote_ref=None,
        local_oid=None,
        verify_remote=True,
        checked_at="2026-10-09T12:00:00+00:00",
    )
    unverified = auditor["_remote_freshness"](
        tmp_path,
        remote="origin",
        remote_ref="refs/heads/main",
        local_oid="b" * 40,
        verify_remote=False,
        checked_at="2026-10-09T12:00:00+00:00",
    )

    assert absent["status"] == "ABSENT"
    assert unverified["status"] == "NOT_VERIFIED"


def test_execution_source_must_be_clean_detached_and_stable(auditor):
    state = {
        "audited_commit": "a" * 40,
        "branch": None,
        "detached": True,
        "tracked_dirty_paths": [],
        "untracked_count": 0,
    }
    auditor["validate_execution_source"](state, state)

    for mutation in (
        lambda value: value.update(branch="feature/prd-reset", detached=False),
        lambda value: value["tracked_dirty_paths"].append("tracked.txt"),
        lambda value: value.update(untracked_count=1),
        lambda value: value.update(audited_commit="b" * 40),
    ):
        after = json.loads(json.dumps(state))
        mutation(after)
        with pytest.raises(ValueError, match="execution source"):
            auditor["validate_execution_source"](state, after)


def test_artifact_validation_binds_commit_and_historical_age(auditor):
    audited_commit = "a" * 40
    validated_at = datetime(2026, 10, 9, 12, tzinfo=UTC)
    wheel = _wheel_evidence(audited_commit)
    restore = _restore_evidence()
    generated_at = validated_at - timedelta(hours=23)
    wheel["generated_at"] = generated_at.isoformat()
    restore["generated_at"] = generated_at.isoformat()
    restore["audited_commit"] = audited_commit

    auditor["_validate_wheel_evidence"](
        wheel,
        audited_commit,
        validated_at=validated_at,
    )
    auditor["_validate_restore_evidence"](
        restore,
        audited_commit,
        validated_at=validated_at,
    )

    for mutation in (
        lambda value: value.update(audited_commit="b" * 40),
        lambda value: value.update(
            generated_at=(validated_at - timedelta(hours=25)).isoformat()
        ),
        lambda value: value.update(
            generated_at=(validated_at + timedelta(seconds=1)).isoformat()
        ),
    ):
        candidate = dict(restore)
        mutation(candidate)
        with pytest.raises(ValueError):
            auditor["_validate_restore_evidence"](
                candidate,
                audited_commit,
                validated_at=validated_at,
            )


def _release_manifest_payload():
    inputs = valid_inputs()
    return {
        "schema": "dragon-radar.reset-release-audit",
        "schema_version": 2,
        "implementation_commit": "a" * 40,
        "implementation_commit_relation": "same_head_or_docs_evidence_only_child",
        **inputs,
        "decisions": {
            "migration_readiness": "GO",
            "task00_20_engineering": "GO",
            "branch_cut": "GO",
            "production_release": "NO_GO",
            "overall_status": "NO_GO",
        },
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["migration"]["wheel"]["fresh"].update(ready=False),
        lambda value: value["migration"]["restore"]["cleanup"].update(
            backup_file_removed=False
        ),
        lambda value: value["migration"]["graph"].update(revision_count=17),
        lambda value: value["migration"]["source_artifacts"]["wheel"].update(
            sha256="invalid"
        ),
        lambda value: value["git"]["execution_source"].update(
            branch="feature/prd-reset",
            detached=False,
        ),
        lambda value: value["git"]["execution_source"]["tracked_dirty_paths"].append(
            "tracked.txt"
        ),
        lambda value: value["git"]["candidate"]["remote_freshness"].update(
            local_oid="c" * 40,
            remote_oid="c" * 40,
        ),
        lambda value: value["task00_20"]["executed_gates"].update(required=20),
        lambda value: value["task00_20"].update(bundle_manifest_sha256="6" * 64),
        lambda value: value["task00_20"].update(implementation_commit="d" * 40),
        lambda value: value["task00_20"]["inventory"].pop(),
        lambda value: value["task00_20"]["inventory"].append(
            deepcopy(value["task00_20"]["inventory"][0])
        ),
        lambda value: value["task00_20"]["inventory"][0]["files"][0].update(
            path="../escape.md"
        ),
        lambda value: value["task00_20"]["inventory"][0]["files"][0].update(
            sha256="9" * 64
        ),
        lambda value: value["external_gates"].pop("licensed_real_input"),
        lambda value: value["supply_chain"].update(secret_scan="VERIFIED"),
        lambda value: value["decisions"].update(
            production_release="GO",
            overall_status="GO",
        ),
    ],
)
def test_check_recomputes_all_decisive_evidence_and_rejects_tamper(
    auditor,
    tmp_path,
    monkeypatch,
    mutation,
):
    payload = _release_manifest_payload()
    committed_task = deepcopy(payload["task00_20"])
    path = tmp_path / "manifest.json"
    globals_ = auditor["check_release_manifest"].__globals__
    monkeypatch.setitem(globals_, "_tracked_paths", lambda *_: {"manifest.json"})
    monkeypatch.setitem(globals_, "_git_output", lambda *_args, **_kwargs: "b" * 40)
    monkeypatch.setitem(globals_, "_validate_commit_relation", lambda *_: None)
    monkeypatch.setitem(globals_, "_validate_inventory_at_head", lambda *_: None)
    monkeypatch.setitem(
        globals_,
        "_git_blob_fingerprint",
        lambda _root, _commit, blob_path: {
            "sha256": (
                "3" * 64 if blob_path.endswith("release_manifest.json") else "4" * 64
            ),
            "bytes": 1,
        },
    )
    monkeypatch.setitem(
        globals_,
        "collect_task20_evidence",
        lambda *_args, **_kwargs: deepcopy(committed_task),
    )
    monkeypatch.setitem(
        globals_,
        "_git_blob",
        lambda _root, _commit, blob_path: (
            json.dumps(payload).encode()
            if blob_path == "manifest.json"
            else pytest.fail(f"unexpected blob: {blob_path}")
        ),
    )
    monkeypatch.setitem(
        globals_,
        "_verify_migration_commit",
        lambda _root, commit: {
            "schema": "dragon-radar.release-migration-verification",
            "schema_version": 1,
            "audited_commit": commit,
            "head": "0010_ai_tasks",
            "revision_count": 18,
            "release_manifest_sha256": "3" * 64,
            "schema_contract_sha256": "4" * 64,
            "errors": [],
        },
    )
    monkeypatch.setitem(globals_, "_validate_live_git_decisions", lambda *_: None)
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert auditor["check_release_manifest"](tmp_path, path) == payload

    mutation(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        auditor["check_release_manifest"](tmp_path, path)


def test_check_loads_manifest_from_head_blob_not_checkout(
    auditor,
    tmp_path,
    monkeypatch,
):
    committed = _release_manifest_payload()
    committed["git"]["candidate"]["configured_upstream"] = None
    committed["git"]["candidate"]["remote_freshness"].update(
        status="ABSENT",
        remote=None,
        remote_ref=None,
        local_oid=None,
        remote_oid=None,
    )
    committed["decisions"]["branch_cut"] = "NO_GO"
    checkout = deepcopy(committed)
    checkout["decisions"].update(production_release="GO", overall_status="GO")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(checkout), encoding="utf-8")
    globals_ = auditor["check_release_manifest"].__globals__
    monkeypatch.setitem(globals_, "_tracked_paths", lambda *_: {"manifest.json"})
    monkeypatch.setitem(globals_, "_git_output", lambda *_args, **_kwargs: "b" * 40)
    monkeypatch.setitem(globals_, "_validate_commit_relation", lambda *_: None)
    monkeypatch.setitem(globals_, "_validate_inventory_at_head", lambda *_: None)
    monkeypatch.setitem(globals_, "_validate_migration_at_commit", lambda *_: None)
    monkeypatch.setitem(
        globals_,
        "collect_task20_evidence",
        lambda *_args, **_kwargs: deepcopy(committed["task00_20"]),
    )
    monkeypatch.setitem(
        globals_,
        "_git_blob",
        lambda _root, _commit, blob_path: (
            json.dumps(committed).encode()
            if blob_path == "manifest.json"
            else pytest.fail(f"unexpected blob: {blob_path}")
        ),
    )
    monkeypatch.setitem(
        globals_,
        "collect_git_state",
        lambda *_args, **_kwargs: {
            key: value
            for key, value in (
                deepcopy(valid_inputs()["git"]["execution_source"])
                | {"audited_commit": "b" * 40}
            ).items()
            if key != "tracked_clean_before_and_after"
        },
    )

    assert auditor["check_release_manifest"](tmp_path, path) == committed


def test_check_recollects_git_and_rejects_self_consistent_branch_cut_forgery(
    auditor,
    tmp_path,
    monkeypatch,
):
    forged = _release_manifest_payload()
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(forged), encoding="utf-8")
    globals_ = auditor["check_release_manifest"].__globals__
    monkeypatch.setitem(globals_, "_tracked_paths", lambda *_: {"manifest.json"})
    monkeypatch.setitem(globals_, "_git_output", lambda *_args, **_kwargs: "b" * 40)
    monkeypatch.setitem(globals_, "_validate_commit_relation", lambda *_: None)
    monkeypatch.setitem(globals_, "_validate_inventory_at_head", lambda *_: None)
    monkeypatch.setitem(globals_, "_validate_migration_at_commit", lambda *_: None)
    monkeypatch.setitem(
        globals_,
        "collect_task20_evidence",
        lambda *_args, **_kwargs: deepcopy(forged["task00_20"]),
    )
    monkeypatch.setitem(
        globals_,
        "_git_blob",
        lambda _root, _commit, blob_path: (
            json.dumps(forged).encode()
            if blob_path == "manifest.json"
            else pytest.fail(f"unexpected blob: {blob_path}")
        ),
    )
    live = deepcopy(valid_inputs()["git"]["execution_source"])
    live["audited_commit"] = "b" * 40
    monkeypatch.setitem(
        globals_,
        "collect_git_state",
        lambda *_args, **_kwargs: deepcopy(live),
    )

    with pytest.raises(ValueError, match="live Git evidence"):
        auditor["check_release_manifest"](tmp_path, path)


def test_task20_missing_declared_blob_fails_collection_and_check(
    auditor,
    tmp_path,
    monkeypatch,
):
    manifest_path, summary_path = _write_task20_bundle(tmp_path)
    globals_ = auditor["collect_task20_evidence"].__globals__
    monkeypatch.setitem(
        globals_,
        "_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="verified",
            stderr="",
        ),
    )
    monkeypatch.setitem(
        globals_,
        "_tracked_paths",
        lambda *_: {
            manifest_path.relative_to(tmp_path).as_posix(),
            summary_path.relative_to(tmp_path).as_posix(),
        },
    )

    def missing_blob(root, _commit, blob_path):
        if blob_path.endswith("acceptance-summary.json"):
            raise ValueError("cannot read committed Git blob")
        return (root / blob_path).read_bytes()

    monkeypatch.setitem(globals_, "_git_blob", missing_blob)
    with pytest.raises(ValueError, match="cannot read committed Git blob"):
        auditor["collect_task20_evidence"](tmp_path, "a" * 40)

    payload = _release_manifest_payload()
    release_path = tmp_path / "release-manifest.json"
    release_path.write_text(json.dumps(payload), encoding="utf-8")
    check_globals = auditor["check_release_manifest"].__globals__
    monkeypatch.setitem(
        check_globals,
        "_git_blob",
        lambda _root, _commit, blob_path: (
            json.dumps(payload).encode()
            if blob_path == "release-manifest.json"
            else missing_blob(tmp_path, _commit, blob_path)
        ),
    )
    monkeypatch.setitem(
        check_globals,
        "_tracked_paths",
        lambda *_: {
            "release-manifest.json",
            manifest_path.relative_to(tmp_path).as_posix(),
            summary_path.relative_to(tmp_path).as_posix(),
        },
    )
    monkeypatch.setitem(
        check_globals,
        "_git_output",
        lambda *_args, **_kwargs: "b" * 40,
    )
    monkeypatch.setitem(check_globals, "_validate_commit_relation", lambda *_: None)
    monkeypatch.setitem(check_globals, "_validate_migration_at_commit", lambda *_: None)

    with pytest.raises(ValueError, match="cannot read committed Git blob"):
        auditor["check_release_manifest"](tmp_path, release_path)


@pytest.mark.parametrize(
    ("age", "expired"),
    [
        (timedelta(hours=24), False),
        (timedelta(hours=24, microseconds=1), True),
    ],
)
def test_manifest_check_revalidates_artifact_age_at_current_utc(
    auditor,
    monkeypatch,
    age,
    expired,
):
    now = datetime(2026, 10, 9, 12, tzinfo=UTC)
    generated_at = now - age
    migration = valid_inputs()["migration"]
    for name in ("wheel", "restore"):
        migration[name]["generated_at"] = generated_at.isoformat()
        artifact = migration["source_artifacts"][name]
        artifact["generated_at"] = generated_at.isoformat()
        artifact["validated_at"] = generated_at.isoformat()
        artifact["age_seconds_at_audit"] = 0.0
    globals_ = auditor["_validate_migration_at_commit"].__globals__
    monkeypatch.setitem(
        globals_,
        "_git_blob_fingerprint",
        lambda _root, _commit, blob_path: {
            "sha256": (
                "3" * 64 if blob_path.endswith("release_manifest.json") else "4" * 64
            ),
            "bytes": 1,
        },
    )
    monkeypatch.setitem(
        globals_,
        "_verify_migration_commit",
        lambda _root, commit: {
            "schema": "dragon-radar.release-migration-verification",
            "schema_version": 1,
            "audited_commit": commit,
            "head": "0010_ai_tasks",
            "revision_count": 18,
            "release_manifest_sha256": "3" * 64,
            "schema_contract_sha256": "4" * 64,
            "verifier_sha256": "5" * 64,
            "schema_validator_sha256": "6" * 64,
            "errors": [],
        },
    )

    if expired:
        with pytest.raises(ValueError, match="older than 24 hours"):
            auditor["_validate_migration_at_commit"](
                ROOT,
                migration,
                "a" * 40,
                now=now,
            )
    else:
        auditor["_validate_migration_at_commit"](
            ROOT,
            migration,
            "a" * 40,
            now=now,
        )


def test_git_blob_ignores_replace_objects_and_audit_rejects_replace_refs(
    auditor,
    tmp_path,
):
    repository, original = _init_committed_evidence_repository(tmp_path)
    evidence = repository / "evidence.md"
    evidence.write_bytes(b"replacement\n")
    subprocess.run(["git", "add", "evidence.md"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "replacement"], cwd=repository, check=True)
    replacement = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "replace", original, replacement],
        cwd=repository,
        check=True,
    )

    assert auditor["_git_blob"](repository, original, "evidence.md") == (
        b"committed\nbytes\n"
    )
    with pytest.raises(ValueError, match="replace"):
        auditor["collect_git_state"](repository)


@pytest.mark.parametrize(
    "name",
    [
        "GIT_REPLACE_REF_BASE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
    ],
)
def test_audit_rejects_dangerous_git_environment(
    auditor,
    monkeypatch,
    name,
):
    monkeypatch.setenv(name, "caller-controlled")

    with pytest.raises(ValueError, match=name):
        auditor["collect_git_state"](ROOT)


def test_git_object_and_ancestry_commands_disable_replace_objects(
    auditor,
    tmp_path,
    monkeypatch,
):
    calls = []

    def run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        if "cat-file" in argv:
            return subprocess.CompletedProcess(argv, 0, b"blob", b"")
        return subprocess.CompletedProcess(argv, 0, "", "")

    globals_ = auditor["_git_blob"].__globals__
    monkeypatch.setitem(globals_, "_run", run)

    assert auditor["_git_blob"](tmp_path, "a" * 40, "evidence.md") == b"blob"
    assert auditor["_is_ancestor"](tmp_path, "a" * 40, "b" * 40) is True
    auditor["_git_output"](tmp_path, ["rev-parse", "HEAD"])

    assert calls
    assert all(call[0][:2] == ["git", "--no-replace-objects"] for call in calls)
    assert all(call[1]["env"]["GIT_NO_REPLACE_OBJECTS"] == "1" for call in calls)


def test_audit_process_timeout_terminates_and_waits(
    auditor,
    monkeypatch,
    tmp_path,
):
    events = []

    class FakeProcess:
        returncode = 17

        def communicate(self, timeout=None):
            events.append(("communicate", timeout))
            if len(events) == 1:
                raise subprocess.TimeoutExpired(["helper"], timeout)
            return ("", "")

        def terminate(self):
            events.append(("terminate", None))

        def kill(self):
            events.append(("kill", None))

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    with pytest.raises(subprocess.TimeoutExpired):
        auditor["_run"](["helper"], cwd=tmp_path, timeout=0.01)

    assert events == [
        ("communicate", 0.01),
        ("terminate", None),
        ("communicate", 5),
    ]


@pytest.mark.parametrize(
    "value",
    [
        "/etc/dragon-radar/evidence.json",
        "/var/lib/dragon-radar/evidence.json",
        r"\\server\share\evidence.json",
        r"D:\evidence.json",
    ],
)
def test_manifest_safety_rejects_every_absolute_path(auditor, value):
    with pytest.raises(ValueError, match="unsafe release manifest"):
        auditor["validate_manifest_safety"]({"path": value})


def test_migration_verifier_runs_audited_blob_without_pythonpath(
    auditor,
    monkeypatch,
):
    verifier_bytes = b"# audited verifier\n"
    verifier_sha = hashlib.sha256(verifier_bytes).hexdigest()
    validator_sha = "6" * 64
    verification = {
        "schema": "dragon-radar.release-migration-verification",
        "schema_version": 1,
        "audited_commit": "a" * 40,
        "head": "0010_ai_tasks",
        "revision_count": 18,
        "release_manifest_sha256": "3" * 64,
        "schema_contract_sha256": "4" * 64,
        "verifier_sha256": verifier_sha,
        "schema_validator_sha256": validator_sha,
        "errors": [],
    }
    observed = {}
    globals_ = auditor["_verify_migration_commit"].__globals__

    def git_blob(_root, _commit, path):
        assert path == "scripts/verify_release_migrations.py"
        return verifier_bytes

    def run(argv, **kwargs):
        observed["argv"] = list(argv)
        observed["verifier"] = Path(argv[1]).read_bytes()
        observed["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(verification),
            stderr="",
        )

    monkeypatch.setenv("PYTHONPATH", "caller-controlled")
    monkeypatch.setitem(globals_, "_git_blob", git_blob)
    monkeypatch.setitem(globals_, "_run", run)
    monkeypatch.setitem(
        globals_,
        "_git_blob_fingerprint",
        lambda _root, _commit, path: {
            "sha256": (
                verifier_sha
                if path == "scripts/verify_release_migrations.py"
                else validator_sha
            ),
            "bytes": 1,
        },
    )

    assert auditor["_verify_migration_commit"](ROOT, "a" * 40) == verification
    assert observed["argv"][1] != os.fspath(
        ROOT / "scripts/verify_release_migrations.py"
    )
    assert observed["verifier"] == verifier_bytes
    assert "PYTHONPATH" not in observed["kwargs"]["env"]
