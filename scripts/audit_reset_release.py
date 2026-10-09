"""Derive and validate the Task 21 reset release audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import runpy
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from uuid import uuid4

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "dragon-radar.reset-release-audit"
SCHEMA_VERSION = 1
SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
SUPPORTED_FROM = (
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
TASK_EVIDENCE_MAP = {
    0: ("docs/acceptance/reset-baseline.md",),
    1: (
        "docs/DragonRadar_开发需求PRD.md",
        "docs/contracts/workbench-api.md",
        "docs/superpowers/specs/2026-09-20-prd-reset-decisions.md",
    ),
    2: ("docs/acceptance/reset-api-navigation-local.md",),
    3: ("docs/acceptance/reset-api-navigation-local.md",),
    4: ("docs/acceptance/reset-api-navigation-local.md",),
    5: ("docs/acceptance/reset-api-navigation-local.md",),
    6: ("docs/acceptance/reset-api-navigation-local.md",),
    7: ("docs/acceptance/reset-ingestion.md",),
    8: ("docs/acceptance/reset-publication.md",),
    9: ("docs/acceptance/reset-market.md",),
    10: ("docs/acceptance/reset-role-contracts.md",),
    11: ("docs/acceptance/reset-role-publication.md",),
    12: ("docs/acceptance/reset-leaders.md",),
    13: ("docs/acceptance/reset-analysis.md",),
    14: ("docs/acceptance/reset-news.md",),
    15: ("docs/acceptance/reset-regulation-rules.md",),
    16: ("docs/acceptance/reset-regulation-rules.md",),
    17: ("docs/acceptance/reset-base.md",),
    18: ("docs/acceptance/reset-base.md",),
    19: ("docs/acceptance/reset-base.md",),
    20: (
        "docs/acceptance/reset-base.md",
        "docs/acceptance/evidence/task20/manifest.json",
        "docs/acceptance/evidence/task20/acceptance-summary.json",
        "docs/acceptance/evidence/task20/native-output.txt",
        "docs/acceptance/evidence/task20/performance-raw.json",
        "docs/acceptance/evidence/task20/performance-summary.json",
    ),
}
ROOT_INPUT_FIELDS = {
    "git",
    "migration",
    "task00_20",
    "external_gates",
    "supply_chain",
}
GIT_FIELDS = {
    "audited_commit",
    "branch",
    "detached",
    "configured_upstream",
    "base_ref",
    "base_commit",
    "merge_base",
    "ahead",
    "behind",
    "tracked_dirty_paths",
    "untracked_count",
    "remote_freshness",
}
MIGRATION_DECISION_FIELDS = {
    "valid",
    "implementation_commit",
    "source_artifacts",
}
MIGRATION_FULL_FIELDS = MIGRATION_DECISION_FIELDS | {
    "graph",
    "wheel",
    "restore",
}
TASK_DECISION_FIELDS = {
    "valid",
    "implementation_commit",
    "inventory",
}
TASK_FULL_FIELDS = TASK_DECISION_FIELDS | {
    "attestation",
    "bundle_manifest_sha256",
    "executed_gates",
}
MANIFEST_FIELDS = {
    "schema",
    "schema_version",
    "implementation_commit",
    "implementation_commit_relation",
    "git",
    "migration",
    "task00_20",
    "external_gates",
    "supply_chain",
    "decisions",
}
DECISION_FIELDS = {
    "migration_readiness",
    "task00_20_engineering",
    "branch_cut",
    "production_release",
    "overall_status",
}


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_strict_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"cannot read strict JSON artifact: {type(exc).__name__}"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError("strict JSON artifact must be an object")
    return value


def _exact_object(
    value: object,
    fields: set[str],
    location: str,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    missing = sorted(fields - set(value))
    if missing:
        raise ValueError(f"{location} missing field: {missing[0]}")
    unexpected = sorted(set(value) - fields)
    if unexpected:
        raise ValueError(f"{location} unexpected field: {unexpected[0]}")
    return value


def _exact_int(value: object, location: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{location} must be an integer >= {minimum}")
    return value


def _exact_number(value: object, location: str, *, minimum: float = 0) -> float:
    if type(value) not in {int, float} or value < minimum:
        raise ValueError(f"{location} must be a number >= {minimum}")
    return float(value)


def _nonempty_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{location} must be a non-empty string")
    return value


def _exact_sha(value: object, location: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise ValueError(f"{location} must be a lowercase SHA-256")
    return value


def _exact_commit(value: object, location: str) -> str:
    if not isinstance(value, str) or COMMIT.fullmatch(value) is None:
        raise ValueError(f"{location} must be a 40-character lowercase Git commit")
    return value


def validate_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("path must be a normalized relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) in {"", "."}:
        raise ValueError("path must be a normalized relative path")
    if re.match(r"^[A-Za-z]:", value):
        raise ValueError("path must be a normalized relative path")
    return path.as_posix()


def _run(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: float = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=cwd,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _git_output(
    root: Path,
    argv: Sequence[str],
    *,
    required: bool = True,
) -> str | None:
    result = _run(["git", *argv], cwd=root)
    if result.returncode != 0:
        if required:
            raise ValueError(f"Git command failed: git {' '.join(argv)}")
        return None
    return result.stdout.strip()


def _tracked_paths(root: Path, commit: str | None = None) -> set[str]:
    argv = ["-c", "core.quotepath=false"]
    argv.extend(["ls-tree", "-r", "--name-only", commit] if commit else ["ls-files"])
    output = _git_output(root, argv)
    return {line.replace("\\", "/") for line in (output or "").splitlines() if line}


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    result = _run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=root,
    )
    if result.returncode not in {0, 1}:
        raise ValueError("cannot validate Git ancestry")
    return result.returncode == 0


def _parse_porcelain(output: str) -> tuple[list[str], int]:
    tracked: list[str] = []
    untracked = 0
    entries = [entry for entry in output.split("\0") if entry]
    index = 0
    while index < len(entries):
        entry = entries[index]
        code = entry[:2]
        path = entry[3:].replace("\\", "/")
        if code == "??":
            untracked += 1
        else:
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            tracked.append(path)
            if "R" in code or "C" in code:
                index += 1
        index += 1
    return sorted(set(tracked)), untracked


def _remote_freshness(root: Path) -> dict[str, object]:
    git_dir = _git_output(root, ["rev-parse", "--git-common-dir"])
    if git_dir is None:
        return {
            "status": "STALE",
            "fetch_head_present": False,
            "fetch_age_seconds": None,
        }
    directory = Path(git_dir)
    if not directory.is_absolute():
        directory = root / directory
    fetch_head = directory.resolve() / "FETCH_HEAD"
    try:
        age = max(int(time.time() - fetch_head.stat().st_mtime), 0)
    except OSError:
        return {
            "status": "STALE",
            "fetch_head_present": False,
            "fetch_age_seconds": None,
        }
    return {
        "status": "CURRENT" if age <= 86400 else "STALE",
        "fetch_head_present": True,
        "fetch_age_seconds": age,
    }


def collect_git_state(root: Path) -> dict[str, object]:
    """Capture source state before any output directory is created."""
    root = Path(root).resolve()
    head = _exact_commit(
        _git_output(root, ["rev-parse", "HEAD"]),
        "audited HEAD",
    )
    branch = _git_output(
        root,
        ["symbolic-ref", "--quiet", "--short", "HEAD"],
        required=False,
    )
    upstream = _git_output(
        root,
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        required=False,
    )
    base_ref = upstream or _git_output(
        root,
        ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        required=False,
    )
    base_commit = (
        _git_output(root, ["rev-parse", base_ref], required=False) if base_ref else None
    )
    merge_base = (
        _git_output(root, ["merge-base", "HEAD", base_ref], required=False)
        if base_ref
        else None
    )
    ahead = behind = None
    if base_ref:
        counts = _git_output(
            root,
            ["rev-list", "--left-right", "--count", f"{base_ref}...HEAD"],
            required=False,
        )
        if counts:
            values = counts.split()
            if len(values) == 2 and all(value.isdigit() for value in values):
                behind, ahead = map(int, values)
    status_result = _run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=root,
    )
    if status_result.returncode != 0:
        raise ValueError("Git status capture failed")
    tracked_dirty, untracked_count = _parse_porcelain(status_result.stdout)
    return {
        "audited_commit": head,
        "branch": branch or None,
        "detached": branch is None,
        "configured_upstream": upstream or None,
        "base_ref": base_ref or None,
        "base_commit": base_commit or None,
        "merge_base": merge_base or None,
        "ahead": ahead,
        "behind": behind,
        "tracked_dirty_paths": tracked_dirty,
        "untracked_count": untracked_count,
        "remote_freshness": _remote_freshness(root),
    }


def _hash_file(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def collect_task_inventory(root: Path, audited_commit: str) -> list[dict[str, object]]:
    tracked = _tracked_paths(root, audited_commit)
    if set(TASK_EVIDENCE_MAP) != set(range(21)):
        raise ValueError("Task evidence map must cover Task00-20 exactly")
    inventory = []
    for task in range(21):
        files = []
        for raw_path in TASK_EVIDENCE_MAP[task]:
            path_value = validate_relative_path(raw_path)
            if path_value not in tracked:
                raise ValueError(
                    f"Task{task:02d} evidence is not tracked: {path_value}"
                )
            path = root / path_value
            if not path.is_file():
                raise ValueError(f"Task{task:02d} evidence is missing: {path_value}")
            files.append({"path": path_value, **_hash_file(path)})
        inventory.append({"task": task, "files": files})
    return inventory


def _validate_task20_manifest(
    root: Path,
    manifest: dict[str, object],
    tracked: set[str],
) -> None:
    _exact_object(
        manifest,
        {
            "schema",
            "schema_version",
            "implementation_commit",
            "execution_provenance",
            "files",
        },
        "Task20 manifest",
    )
    if manifest["schema"] != "dragon-radar.task20-committed-evidence":
        raise ValueError("Task20 manifest schema is invalid")
    if _exact_int(manifest["schema_version"], "Task20 schema_version") != 1:
        raise ValueError("Task20 schema_version is invalid")
    files = manifest["files"]
    if not isinstance(files, list) or not files:
        raise ValueError("Task20 file list is invalid")
    base = root / "docs/acceptance/evidence/task20"
    paths = []
    for index, raw in enumerate(files):
        item = _exact_object(
            raw,
            {"path", "sha256", "bytes"},
            f"Task20 file {index}",
        )
        relative = validate_relative_path(item["path"])
        paths.append(relative)
        repository_path = (base / relative).relative_to(root).as_posix()
        if repository_path not in tracked:
            raise ValueError(f"Task20 evidence is not tracked: {repository_path}")
        content = (base / relative).read_bytes()
        if hashlib.sha256(content).hexdigest() != _exact_sha(
            item["sha256"],
            f"Task20 file {index} SHA-256",
        ):
            raise ValueError(f"Task20 file {index} SHA-256 does not match")
        if _exact_int(item["bytes"], f"Task20 file {index} bytes") != len(content):
            raise ValueError(f"Task20 file {index} byte count does not match")
    if paths != sorted(set(paths)):
        raise ValueError("Task20 evidence paths must be unique and sorted")


def _validate_task20_summary(
    summary: dict[str, object],
    implementation_commit: str,
) -> dict[str, int]:
    _exact_object(
        summary,
        {
            "schema",
            "schema_version",
            "implementation_commit",
            "execution_provenance",
            "native_counts",
            "quality_gates",
            "external_blockers",
            "migration_added",
        },
        "Task20 summary",
    )
    if (
        summary["schema"] != "dragon-radar.task20-acceptance-summary"
        or _exact_int(summary["schema_version"], "Task20 summary schema_version") != 1
    ):
        raise ValueError("Task20 summary schema is invalid")
    if summary["implementation_commit"] != implementation_commit:
        raise ValueError("Task20 implementation commits differ")
    provenance = _exact_object(
        summary["execution_provenance"],
        {"source", "tracked_clean_before_and_after", "focused", "full"},
        "Task20 execution provenance",
    )
    if provenance["tracked_clean_before_and_after"] is not True:
        raise ValueError("Task20 execution was not tracked-clean")
    full = _exact_object(
        provenance["full"],
        {
            "mode",
            "automated_acceptance",
            "required_gates",
            "failed_gates",
            "skipped_tests",
        },
        "Task20 full execution",
    )
    if (
        full["mode"] != "full"
        or full["automated_acceptance"] != "pass"
        or _exact_int(full["required_gates"], "Task20 required gates") != 21
        or _exact_int(full["failed_gates"], "Task20 failed gates") != 0
        or _exact_int(full["skipped_tests"], "Task20 skipped tests") != 0
    ):
        raise ValueError("Task20 did not execute 21/21 gates with zero skips")
    return {"required": 21, "failed": 0, "skipped": 0}


def collect_task20_evidence(
    root: Path,
    audited_commit: str,
) -> dict[str, object]:
    root = Path(root).resolve()
    manifest_path = root / "docs/acceptance/evidence/task20/manifest.json"
    verifier = root / "scripts/verify_task20_bundle.py"
    result = _run([sys.executable, str(verifier), str(manifest_path)], cwd=root)
    if result.returncode != 0:
        raise ValueError("strict Task20 bundle verification failed")
    manifest = load_strict_json(manifest_path)
    tracked = _tracked_paths(root, audited_commit)
    _validate_task20_manifest(root, manifest, tracked)
    implementation_commit = _exact_commit(
        manifest["implementation_commit"],
        "Task20 implementation commit",
    )
    if not _is_ancestor(root, implementation_commit, audited_commit):
        raise ValueError("Task20 implementation is not an ancestor of audited HEAD")
    summary_path = root / "docs/acceptance/evidence/task20/acceptance-summary.json"
    executed = _validate_task20_summary(
        load_strict_json(summary_path),
        implementation_commit,
    )
    inventory = collect_task_inventory(root, audited_commit)
    return {
        "valid": True,
        "implementation_commit": implementation_commit,
        "attestation": "committed_execution_provenance_only",
        "bundle_manifest_sha256": hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest(),
        "executed_gates": executed,
        "inventory": inventory,
    }


WHEEL_FIELDS = {
    "schema",
    "schema_version",
    "wheel_filename",
    "wheel_sha256",
    "audited_commit",
    "imported_from",
    "fresh",
    "supported_upgrades",
}
RESTORE_FIELDS = {
    "schema",
    "schema_version",
    "rollback_mode",
    "backup_format",
    "backup_sha256",
    "source_fingerprint",
    "restore_fingerprint",
    "source_unchanged",
    "quiescence_enforced",
    "measured_data_loss_rows",
    "data_loss_window_seconds",
    "write_freeze_seconds",
    "elapsed_seconds",
    "revision",
    "restored_ready",
    "sentinel_preserved",
    "permissions_preserved",
    "app_semantics_verified",
    "ready_before_canonicalization",
    "canonicalized_schema_objects",
    "source_counts",
    "restored_counts",
    "cleanup",
    "disposable",
    "targets",
}


def _validate_wheel_evidence(
    value: dict[str, object],
    audited_commit: str,
) -> dict[str, object]:
    wheel = _exact_object(value, WHEEL_FIELDS, "wheel evidence")
    if (
        wheel["schema"] != "dragon-radar.installed-wheel-migrations"
        or _exact_int(wheel["schema_version"], "wheel schema_version") != 1
    ):
        raise ValueError("wheel evidence schema is invalid")
    _nonempty_string(wheel["wheel_filename"], "wheel filename")
    _exact_sha(wheel["wheel_sha256"], "wheel SHA-256")
    if wheel["audited_commit"] != audited_commit:
        raise ValueError("wheel audited commit does not match implementation commit")
    fresh = _exact_object(wheel["fresh"], {"to_revision", "ready"}, "wheel fresh")
    if fresh["to_revision"] != "0010_ai_tasks" or fresh["ready"] is not True:
        raise ValueError("wheel fresh migration evidence is invalid")
    upgrades = wheel["supported_upgrades"]
    if not isinstance(upgrades, list) or len(upgrades) != len(SUPPORTED_FROM):
        raise ValueError("wheel supported upgrade matrix is invalid")
    sanitized_upgrades = []
    legacy_fields = {
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
    }
    for index, (raw, revision) in enumerate(zip(upgrades, SUPPORTED_FROM, strict=True)):
        fields = {
            "from_revision",
            "to_revision",
            "sentinel",
            "sentinel_preserved",
            "ready",
        }
        if revision == "0009_regulation":
            fields |= {"sentinel_before", "sentinel_after"}
        upgrade = _exact_object(raw, fields, f"wheel supported upgrade {index}")
        if (
            upgrade["from_revision"] != revision
            or upgrade["to_revision"] != "0010_ai_tasks"
            or upgrade["sentinel_preserved"] is not True
            or upgrade["ready"] is not True
        ):
            raise ValueError("wheel supported upgrade evidence is invalid")
        _nonempty_string(upgrade["sentinel"], "wheel upgrade sentinel")
        if revision == "0009_regulation":
            before = _exact_object(
                upgrade["sentinel_before"],
                legacy_fields,
                "legacy sentinel before",
            )
            after = _exact_object(
                upgrade["sentinel_after"],
                legacy_fields,
                "legacy sentinel after",
            )
            if before != after:
                raise ValueError("legacy supported upgrade sentinel changed")
        sanitized_upgrades.append(dict(upgrade))
    return {
        "schema": wheel["schema"],
        "schema_version": 1,
        "wheel_filename": wheel["wheel_filename"],
        "wheel_sha256": wheel["wheel_sha256"],
        "audited_commit": wheel["audited_commit"],
        "fresh": dict(fresh),
        "supported_upgrades": sanitized_upgrades,
    }


def _validate_restore_evidence(value: dict[str, object]) -> dict[str, object]:
    restore = _exact_object(value, RESTORE_FIELDS, "restore evidence")
    if (
        restore["schema"] != "dragon-radar.backup-restore-drill"
        or _exact_int(restore["schema_version"], "restore schema_version") != 1
        or restore["rollback_mode"] != "backup_restore_only"
        or restore["backup_format"] != "custom"
        or restore["revision"] != "0010_ai_tasks"
    ):
        raise ValueError("restore evidence schema or policy is invalid")
    backup_sha = _exact_sha(restore["backup_sha256"], "backup SHA-256")
    source_fingerprint = _exact_sha(
        restore["source_fingerprint"],
        "source fingerprint",
    )
    restore_fingerprint = _exact_sha(
        restore["restore_fingerprint"],
        "restore fingerprint",
    )
    if source_fingerprint != restore_fingerprint:
        raise ValueError("restore fingerprint differs from source fingerprint")
    required_true = (
        "source_unchanged",
        "quiescence_enforced",
        "restored_ready",
        "sentinel_preserved",
        "permissions_preserved",
        "app_semantics_verified",
    )
    if any(restore[field] is not True for field in required_true):
        raise ValueError("restore evidence did not pass every strict check")
    if type(restore["ready_before_canonicalization"]) is not bool:
        raise ValueError("restore ready_before_canonicalization must be boolean")
    if _exact_int(restore["measured_data_loss_rows"], "data loss rows") != 0:
        raise ValueError("restore evidence measured data loss")
    if _exact_number(restore["data_loss_window_seconds"], "data loss window") != 0:
        raise ValueError("restore evidence data loss window is nonzero")
    _exact_number(restore["write_freeze_seconds"], "write freeze seconds")
    _exact_number(restore["elapsed_seconds"], "restore elapsed seconds")
    _exact_int(
        restore["canonicalized_schema_objects"],
        "canonicalized schema objects",
    )
    if restore["source_counts"] != restore["restored_counts"]:
        raise ValueError("restore source and restored counts differ")
    cleanup = _exact_object(
        restore["cleanup"],
        {
            "source_database_removed",
            "restore_database_removed",
            "app_role_removed",
            "backup_file_removed",
        },
        "restore cleanup",
    )
    if any(value is not True for value in cleanup.values()):
        raise ValueError("restore cleanup is incomplete")
    targets = _exact_object(
        restore["targets"],
        {"rpo_seconds", "rto_seconds", "approval_status"},
        "restore targets",
    )
    if (
        _exact_int(targets["rpo_seconds"], "restore RPO") != 3600
        or _exact_int(targets["rto_seconds"], "restore RTO") != 14400
        or targets["approval_status"] != "suggested_unapproved"
    ):
        raise ValueError("restore targets are invalid")
    return {
        key: deepcopy_value
        for key, deepcopy_value in restore.items()
        if key != "disposable"
    } | {
        "backup_sha256": backup_sha,
        "source_fingerprint": source_fingerprint,
        "restore_fingerprint": restore_fingerprint,
    }


def collect_migration_evidence(
    wheel_path: Path,
    restore_path: Path,
    audited_commit: str,
) -> dict[str, object]:
    audited_commit = _exact_commit(audited_commit, "audited implementation commit")
    wheel = _validate_wheel_evidence(load_strict_json(wheel_path), audited_commit)
    restore = _validate_restore_evidence(load_strict_json(restore_path))
    migrations = REPOSITORY_ROOT / "src/dragon_radar/migrations"
    verifier_namespace = runpy.run_path(
        str(REPOSITORY_ROOT / "scripts/verify_release_migrations.py")
    )
    graph = verifier_namespace["verify_manifest"](migrations)
    if graph != {
        "head": "0010_ai_tasks",
        "revision_count": 18,
        "errors": [],
    }:
        raise ValueError("migration graph or frozen hashes are invalid")
    return {
        "valid": True,
        "implementation_commit": audited_commit,
        "source_artifacts": {
            "wheel": _hash_file(Path(wheel_path)),
            "restore": _hash_file(Path(restore_path)),
        },
        "graph": {
            "head": graph["head"],
            "revision_count": graph["revision_count"],
            "release_manifest_sha256": hashlib.sha256(
                (migrations / "release_manifest.json").read_bytes()
            ).hexdigest(),
            "schema_contract_sha256": hashlib.sha256(
                (
                    REPOSITORY_ROOT / "src/dragon_radar/storage/schema_contract.json"
                ).read_bytes()
            ).hexdigest(),
        },
        "wheel": wheel,
        "restore": restore,
    }


def require_fresh_artifact(path: Path, *, generated_after: float) -> None:
    try:
        modified = Path(path).stat().st_mtime
    except OSError as exc:
        raise ValueError("migration artifact is missing") from exc
    if modified < generated_after:
        raise ValueError("migration artifact is stale")


def _validate_gate_map(
    value: object,
    expected: tuple[str, ...],
    location: str,
) -> dict[str, object]:
    gates = _exact_object(value, set(expected), location)
    for gate in expected:
        if gates[gate] == "VERIFIED":
            raise ValueError(f"{location}.{gate} cannot self-assert trusted evidence")
        if gates[gate] != "BLOCKED":
            raise ValueError(f"{location}.{gate} has an invalid evidence state")
    return gates


def derive_decisions(inputs: Mapping[str, object]) -> dict[str, str]:
    payload = _exact_object(dict(inputs), ROOT_INPUT_FIELDS, "release inputs")
    git = _exact_object(payload["git"], GIT_FIELDS, "release inputs.git")
    migration_fields = (
        MIGRATION_FULL_FIELDS
        if isinstance(payload["migration"], dict)
        and set(payload["migration"]) == MIGRATION_FULL_FIELDS
        else MIGRATION_DECISION_FIELDS
    )
    migration = _exact_object(
        payload["migration"], migration_fields, "release inputs.migration"
    )
    task_fields = (
        TASK_FULL_FIELDS
        if isinstance(payload["task00_20"], dict)
        and set(payload["task00_20"]) == TASK_FULL_FIELDS
        else TASK_DECISION_FIELDS
    )
    task = _exact_object(payload["task00_20"], task_fields, "release inputs.task00_20")
    _validate_gate_map(
        payload["external_gates"],
        EXTERNAL_GATES,
        "release inputs.external_gates",
    )
    _validate_gate_map(
        payload["supply_chain"],
        SUPPLY_CHAIN_GATES,
        "release inputs.supply_chain",
    )
    migration_status = "GO" if migration["valid"] is True else "NO_GO"
    engineering_status = "GO" if task["valid"] is True else "NO_GO"
    freshness = _exact_object(
        git["remote_freshness"],
        {"status", "fetch_head_present", "fetch_age_seconds"},
        "release inputs.git.remote_freshness",
    )
    branch_ready = (
        git["tracked_dirty_paths"] == []
        and git["configured_upstream"] is not None
        and git["base_ref"] is not None
        and git["base_commit"] is not None
        and git["merge_base"] is not None
        and git["behind"] == 0
        and freshness["status"] == "CURRENT"
    )
    branch_status = "GO" if branch_ready else "NO_GO"
    production_status = "NO_GO"
    decisions = {
        "migration_readiness": migration_status,
        "task00_20_engineering": engineering_status,
        "branch_cut": branch_status,
        "production_release": production_status,
    }
    decisions["overall_status"] = (
        "GO" if all(value == "GO" for value in decisions.values()) else "NO_GO"
    )
    return decisions


def validate_manifest_safety(value: object) -> None:
    unsafe_keys = re.compile(
        r"(?:password|token|environment|database_url|credential)",
        re.IGNORECASE,
    )
    absolute_path = re.compile(
        r"(?:^[A-Za-z]:[\\/]|^\\\\|^/(?:home|Users|private|tmp)/)",
        re.IGNORECASE,
    )

    def visit(item: object, path: str) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if unsafe_keys.search(str(key)):
                    raise ValueError(f"unsafe release manifest field: {path}.{key}")
                visit(child, f"{path}.{key}")
        elif isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f"{path}[{index}]")
        elif isinstance(item, str):
            if "://" in item or absolute_path.search(item):
                raise ValueError(f"unsafe release manifest value: {path}")

    visit(value, "$")


def write_release_manifest(path: Path, report: Mapping[str, object]) -> None:
    validate_manifest_safety(report)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                report,
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_commit_relation(
    root: Path,
    implementation_commit: str,
    current_head: str,
    declared_relation: str,
) -> None:
    if current_head == implementation_commit:
        if declared_relation != "same_head_or_docs_evidence_only_child":
            raise ValueError("implementation commit relation is invalid")
        return
    parent = _git_output(root, ["rev-parse", f"{current_head}^"])
    if (
        declared_relation != "same_head_or_docs_evidence_only_child"
        or parent != implementation_commit
    ):
        raise ValueError("HEAD is not the implementation or its direct evidence child")
    changed = (
        _git_output(
            root,
            ["diff-tree", "--no-commit-id", "--name-only", "-r", current_head],
        )
        or ""
    ).splitlines()
    allowed = {
        "docs/acceptance/evidence/task21/release-manifest.json",
        "docs/acceptance/reset-release.md",
    }
    if not changed or any(path not in allowed for path in changed):
        raise ValueError("HEAD child is not docs/evidence-only")


def _validate_manifest_shape(value: dict[str, object]) -> None:
    _exact_object(value, MANIFEST_FIELDS, "release manifest")
    if (
        value["schema"] != SCHEMA
        or _exact_int(value["schema_version"], "release manifest schema_version")
        != SCHEMA_VERSION
    ):
        raise ValueError("release manifest schema is unsupported")
    _exact_commit(value["implementation_commit"], "implementation commit")
    if value["implementation_commit_relation"] != (
        "same_head_or_docs_evidence_only_child"
    ):
        raise ValueError("implementation commit relation is invalid")
    decisions = _exact_object(value["decisions"], DECISION_FIELDS, "decisions")
    inputs = {field: value[field] for field in ROOT_INPUT_FIELDS}
    expected = derive_decisions(inputs)
    if decisions != expected:
        raise ValueError("release decisions were not derived from evidence")
    validate_manifest_safety(value)


def _validate_inventory_at_head(
    root: Path,
    inventory: object,
    implementation_commit: str,
) -> None:
    if not isinstance(inventory, list) or len(inventory) != 21:
        raise ValueError("Task00-20 inventory is incomplete")
    tracked = _tracked_paths(root, implementation_commit)
    for task_number, raw in enumerate(inventory):
        item = _exact_object(raw, {"task", "files"}, f"Task{task_number:02d}")
        if item["task"] != task_number or not isinstance(item["files"], list):
            raise ValueError("Task00-20 inventory ordering is invalid")
        for raw_file in item["files"]:
            file_item = _exact_object(
                raw_file,
                {"path", "sha256", "bytes"},
                f"Task{task_number:02d} file",
            )
            path_value = validate_relative_path(file_item["path"])
            if path_value not in tracked:
                raise ValueError(f"inventory path is not tracked: {path_value}")
            content = (root / path_value).read_bytes()
            if hashlib.sha256(content).hexdigest() != _exact_sha(
                file_item["sha256"],
                "inventory SHA-256",
            ):
                raise ValueError(f"inventory SHA-256 mismatch: {path_value}")
            if len(content) != _exact_int(file_item["bytes"], "inventory bytes"):
                raise ValueError(f"inventory byte count mismatch: {path_value}")


def check_release_manifest(root: Path, path: Path) -> dict[str, object]:
    root = Path(root).resolve()
    manifest_path = Path(path).resolve()
    manifest = load_strict_json(manifest_path)
    _validate_manifest_shape(manifest)
    try:
        relative = manifest_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("release manifest must be inside the repository") from exc
    current_head = _exact_commit(
        _git_output(root, ["rev-parse", "HEAD"]),
        "current HEAD",
    )
    tracked = _tracked_paths(root, current_head)
    if relative not in tracked:
        raise ValueError("release manifest is not tracked at current HEAD")
    implementation_commit = str(manifest["implementation_commit"])
    _validate_commit_relation(
        root,
        implementation_commit,
        current_head,
        str(manifest["implementation_commit_relation"]),
    )
    task = manifest["task00_20"]
    assert isinstance(task, dict)
    _validate_inventory_at_head(
        root,
        task["inventory"],
        implementation_commit,
    )
    return manifest


def build_release_manifest(
    root: Path,
    wheel_evidence: Path,
    restore_evidence: Path,
) -> dict[str, object]:
    git = collect_git_state(root)
    implementation_commit = str(git["audited_commit"])
    migration = collect_migration_evidence(
        wheel_evidence,
        restore_evidence,
        implementation_commit,
    )
    task = collect_task20_evidence(root, implementation_commit)
    external = {gate: "BLOCKED" for gate in EXTERNAL_GATES}
    supply_chain = {gate: "BLOCKED" for gate in SUPPLY_CHAIN_GATES}
    inputs = {
        "git": git,
        "migration": migration,
        "task00_20": task,
        "external_gates": external,
        "supply_chain": supply_chain,
    }
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "implementation_commit": implementation_commit,
        "implementation_commit_relation": ("same_head_or_docs_evidence_only_child"),
        **inputs,
        "decisions": derive_decisions(inputs),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        type=Path,
        help="validate a committed release manifest without rerunning evidence",
    )
    parser.add_argument("--wheel-evidence", type=Path)
    parser.add_argument("--restore-evidence", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.check is not None:
            check_release_manifest(REPOSITORY_ROOT, args.check)
            print(f"release audit manifest verified: {args.check}")
            return 0
        if not all(
            value is not None
            for value in (args.wheel_evidence, args.restore_evidence, args.output)
        ):
            raise ValueError(
                "--wheel-evidence, --restore-evidence, and --output are required"
            )
        report = build_release_manifest(
            REPOSITORY_ROOT,
            args.wheel_evidence,
            args.restore_evidence,
        )
        write_release_manifest(args.output, report)
        print(
            f"release audit written: {args.output} "
            f"overall={report['decisions']['overall_status']}"
        )
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"release audit failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
