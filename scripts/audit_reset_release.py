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
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "dragon-radar.reset-release-audit"
SCHEMA_VERSION = 2
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
GIT_STATE_FIELDS = {
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
EXECUTION_GIT_FIELDS = GIT_STATE_FIELDS | {"tracked_clean_before_and_after"}
GIT_FIELDS = {"execution_source", "candidate"}
REMOTE_FRESHNESS_FIELDS = {
    "method",
    "status",
    "remote",
    "remote_ref",
    "local_oid",
    "remote_oid",
    "checked_at",
    "error",
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
        content = Path(path).read_bytes()
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"cannot read strict JSON artifact: {type(exc).__name__}"
        ) from exc
    return load_strict_json_bytes(content)


def load_strict_json_bytes(content: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
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


def _utc_timestamp(value: object, location: str) -> datetime:
    text = _nonempty_string(value, location)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{location} must be an ISO-8601 timestamp") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != datetime.min.replace(tzinfo=UTC).utcoffset()
    ):
        raise ValueError(f"{location} must be UTC")
    return parsed


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


def _git_blob(root: Path, commit: str, path: str) -> bytes:
    commit = _exact_commit(commit, "Git blob commit")
    relative = validate_relative_path(path)
    result = subprocess.run(
        ["git", "cat-file", "blob", f"{commit}:{relative}"],
        cwd=root,
        shell=False,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise ValueError(f"cannot read committed Git blob: {relative}")
    return result.stdout


def _git_blob_fingerprint(root: Path, commit: str, path: str) -> dict[str, object]:
    content = _git_blob(root, commit, path)
    return {
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


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


def _remote_freshness(
    root: Path,
    *,
    remote: str | None,
    remote_ref: str | None,
    local_oid: str | None,
    verify_remote: bool,
    checked_at: str,
) -> dict[str, object]:
    result = {
        "method": "git_ls_remote",
        "status": "ABSENT" if remote is None or remote_ref is None else "NOT_VERIFIED",
        "remote": remote,
        "remote_ref": remote_ref,
        "local_oid": local_oid,
        "remote_oid": None,
        "checked_at": checked_at,
        "error": None,
    }
    if remote is None or remote_ref is None:
        return result
    if not verify_remote:
        return result
    try:
        query = _run(
            ["git", "ls-remote", "--exit-code", remote, remote_ref],
            cwd=root,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        result["status"] = "ERROR"
        result["error"] = "remote_query_failed"
        return result
    if query.returncode != 0:
        result["status"] = "ERROR"
        result["error"] = "remote_query_failed"
        return result
    lines = [line.split() for line in query.stdout.splitlines() if line.strip()]
    if len(lines) != 1 or len(lines[0]) != 2:
        result["status"] = "ERROR"
        result["error"] = "remote_response_invalid"
        return result
    remote_oid, returned_ref = lines[0]
    if COMMIT.fullmatch(remote_oid) is None or returned_ref != remote_ref:
        result["status"] = "ERROR"
        result["error"] = "remote_response_invalid"
        return result
    result["remote_oid"] = remote_oid
    result["status"] = "CURRENT" if remote_oid == local_oid else "STALE"
    return result


def collect_git_state(
    root: Path,
    *,
    verify_remote: bool = False,
) -> dict[str, object]:
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
    remote = (
        _git_output(
            root,
            ["config", "--get", f"branch.{branch}.remote"],
            required=False,
        )
        if branch
        else None
    )
    merge_ref = (
        _git_output(root, ["config", "--get", f"branch.{branch}.merge"], required=False)
        if branch
        else None
    )
    if remote == ".":
        remote = None
    if merge_ref and not merge_ref.startswith("refs/heads/"):
        merge_ref = None
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
    checked_at = datetime.now(UTC).isoformat()
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
        "remote_freshness": _remote_freshness(
            root,
            remote=remote,
            remote_ref=merge_ref,
            local_oid=base_commit,
            verify_remote=verify_remote,
            checked_at=checked_at,
        ),
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
        expected_paths = TASK_EVIDENCE_MAP[task]
        if not isinstance(expected_paths, (tuple, list)) or not expected_paths:
            raise ValueError(f"Task{task:02d} evidence mapping must be non-empty")
        files = []
        normalized_paths = [validate_relative_path(path) for path in expected_paths]
        if len(normalized_paths) != len(set(normalized_paths)):
            raise ValueError(f"Task{task:02d} evidence paths must be unique")
        for path_value in normalized_paths:
            if path_value not in tracked:
                raise ValueError(
                    f"Task{task:02d} evidence is not tracked: {path_value}"
                )
            files.append(
                {
                    "path": path_value,
                    **_git_blob_fingerprint(root, audited_commit, path_value),
                }
            )
        inventory.append({"task": task, "files": files})
    return inventory


def _validate_task20_manifest(
    root: Path,
    manifest: dict[str, object],
    tracked: set[str],
    audited_commit: str,
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
    _exact_commit(manifest["implementation_commit"], "Task20 implementation commit")
    _nonempty_string(manifest["execution_provenance"], "Task20 provenance")
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
        content = _git_blob(root, audited_commit, repository_path)
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
    _nonempty_string(provenance["source"], "Task20 execution source")
    _exact_object(
        provenance["focused"],
        {"mode", "selected_gates", "overall_status", "reason"},
        "Task20 focused execution",
    )
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
    native_counts = summary["native_counts"]
    if not isinstance(native_counts, dict) or not native_counts:
        raise ValueError("Task20 native counts are invalid")
    for key, value in native_counts.items():
        _nonempty_string(key, "Task20 native count name")
        _exact_int(value, f"Task20 native count {key}", minimum=1)
    quality_gates = summary["quality_gates"]
    if not isinstance(quality_gates, dict) or not quality_gates:
        raise ValueError("Task20 quality gates are invalid")
    for key, value in quality_gates.items():
        _nonempty_string(key, "Task20 quality gate name")
        if key == "entry_bundle_bytes":
            _exact_int(value, "Task20 entry bundle bytes", minimum=1)
        elif value != "pass":
            raise ValueError(f"Task20 quality gate {key} did not pass")
    blockers = summary["external_blockers"]
    if (
        not isinstance(blockers, list)
        or len(blockers) != len(set(blockers))
        or any(not isinstance(item, str) or not item for item in blockers)
    ):
        raise ValueError("Task20 external blockers are invalid")
    if summary["migration_added"] is not False:
        raise ValueError("Task20 migration_added must be false")
    return {"required": 21, "failed": 0, "skipped": 0}


def collect_task20_evidence(
    root: Path,
    audited_commit: str,
    *,
    run_verifier: bool = True,
) -> dict[str, object]:
    root = Path(root).resolve()
    manifest_path = root / "docs/acceptance/evidence/task20/manifest.json"
    verifier = root / "scripts/verify_task20_bundle.py"
    if run_verifier:
        result = _run([sys.executable, str(verifier), str(manifest_path)], cwd=root)
        if result.returncode != 0:
            raise ValueError("strict Task20 bundle verification failed")
    manifest_relative = "docs/acceptance/evidence/task20/manifest.json"
    manifest_bytes = _git_blob(root, audited_commit, manifest_relative)
    manifest = load_strict_json_bytes(manifest_bytes)
    tracked = _tracked_paths(root, audited_commit)
    _validate_task20_manifest(root, manifest, tracked, audited_commit)
    implementation_commit = _exact_commit(
        manifest["implementation_commit"],
        "Task20 implementation commit",
    )
    if not _is_ancestor(root, implementation_commit, audited_commit):
        raise ValueError("Task20 implementation is not an ancestor of audited HEAD")
    summary_relative = "docs/acceptance/evidence/task20/acceptance-summary.json"
    executed = _validate_task20_summary(
        load_strict_json_bytes(_git_blob(root, audited_commit, summary_relative)),
        implementation_commit,
    )
    inventory = collect_task_inventory(root, audited_commit)
    return {
        "valid": True,
        "implementation_commit": implementation_commit,
        "attestation": "committed_execution_provenance_only",
        "bundle_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "executed_gates": executed,
        "inventory": inventory,
    }


WHEEL_FIELDS = {
    "schema",
    "schema_version",
    "generated_at",
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
    "generated_at",
    "audited_commit",
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
    *,
    validated_at: datetime,
    embedded: bool = False,
) -> dict[str, object]:
    expected_fields = WHEEL_FIELDS - ({"imported_from"} if embedded else set())
    wheel = _exact_object(value, expected_fields, "wheel evidence")
    if (
        wheel["schema"] != "dragon-radar.installed-wheel-migrations"
        or _exact_int(wheel["schema_version"], "wheel schema_version") != 1
    ):
        raise ValueError("wheel evidence schema is invalid")
    _nonempty_string(wheel["wheel_filename"], "wheel filename")
    if not embedded:
        _nonempty_string(wheel["imported_from"], "wheel imported_from")
    _exact_sha(wheel["wheel_sha256"], "wheel SHA-256")
    if wheel["audited_commit"] != audited_commit:
        raise ValueError("wheel audited commit does not match implementation commit")
    generated_at = _validate_artifact_age(
        wheel["generated_at"],
        validated_at,
        "wheel generated_at",
    )
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
        "generated_at": generated_at.isoformat(),
        "wheel_filename": wheel["wheel_filename"],
        "wheel_sha256": wheel["wheel_sha256"],
        "audited_commit": wheel["audited_commit"],
        "fresh": dict(fresh),
        "supported_upgrades": sanitized_upgrades,
    }


def _validate_artifact_age(
    value: object,
    validated_at: datetime,
    location: str,
) -> datetime:
    generated_at = _utc_timestamp(value, location)
    if validated_at.tzinfo is None:
        raise ValueError("artifact validation time must be timezone-aware")
    age = (validated_at - generated_at).total_seconds()
    if age < 0:
        raise ValueError(f"{location} must not be in the future")
    if age > 86400:
        raise ValueError(f"{location} is older than 24 hours")
    return generated_at


def _validate_restore_evidence(
    value: dict[str, object],
    audited_commit: str,
    *,
    validated_at: datetime,
    embedded: bool = False,
) -> dict[str, object]:
    expected_fields = RESTORE_FIELDS - ({"disposable"} if embedded else set())
    restore = _exact_object(value, expected_fields, "restore evidence")
    if (
        restore["schema"] != "dragon-radar.backup-restore-drill"
        or _exact_int(restore["schema_version"], "restore schema_version") != 1
        or restore["rollback_mode"] != "backup_restore_only"
        or restore["backup_format"] != "custom"
        or restore["revision"] != "0010_ai_tasks"
    ):
        raise ValueError("restore evidence schema or policy is invalid")
    if restore["audited_commit"] != audited_commit:
        raise ValueError("restore audited commit does not match implementation commit")
    generated_at = _validate_artifact_age(
        restore["generated_at"],
        validated_at,
        "restore generated_at",
    )
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
    counts_fields = {
        "source_grants",
        "import_batches",
        "snapshots",
        "audit_events",
    }
    source_counts = _exact_object(
        restore["source_counts"],
        counts_fields,
        "restore source counts",
    )
    restored_counts = _exact_object(
        restore["restored_counts"],
        counts_fields,
        "restore restored counts",
    )
    for key in counts_fields:
        _exact_int(source_counts[key], f"restore source count {key}", minimum=1)
        _exact_int(restored_counts[key], f"restore restored count {key}", minimum=1)
    if source_counts != restored_counts:
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
    if not embedded:
        disposable = _exact_object(
            restore["disposable"],
            {"role", "source_database", "restore_database", "dump_path"},
            "restore disposable resources",
        )
        for key, item in disposable.items():
            _nonempty_string(item, f"restore disposable {key}")
    return {
        key: deepcopy_value
        for key, deepcopy_value in restore.items()
        if key != "disposable"
    } | {
        "generated_at": generated_at.isoformat(),
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
    validated_at = datetime.now(UTC)
    raw_wheel = load_strict_json(wheel_path)
    raw_restore = load_strict_json(restore_path)
    wheel = _validate_wheel_evidence(
        raw_wheel,
        audited_commit,
        validated_at=validated_at,
    )
    restore = _validate_restore_evidence(
        raw_restore,
        audited_commit,
        validated_at=validated_at,
    )
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
            "wheel": _artifact_metadata(
                Path(wheel_path),
                str(raw_wheel["generated_at"]),
                validated_at,
            ),
            "restore": _artifact_metadata(
                Path(restore_path),
                str(raw_restore["generated_at"]),
                validated_at,
            ),
        },
        "graph": {
            "head": graph["head"],
            "revision_count": graph["revision_count"],
            "release_manifest_sha256": _git_blob_fingerprint(
                REPOSITORY_ROOT,
                audited_commit,
                "src/dragon_radar/migrations/release_manifest.json",
            )["sha256"],
            "schema_contract_sha256": _git_blob_fingerprint(
                REPOSITORY_ROOT,
                audited_commit,
                "src/dragon_radar/storage/schema_contract.json",
            )["sha256"],
        },
        "wheel": wheel,
        "restore": restore,
    }


def _artifact_metadata(
    path: Path,
    generated_at: str,
    validated_at: datetime,
) -> dict[str, object]:
    generated = _validate_artifact_age(
        generated_at,
        validated_at,
        "artifact generated_at",
    )
    return {
        **_hash_file(path),
        "generated_at": generated.isoformat(),
        "age_seconds_at_audit": round(
            (validated_at - generated).total_seconds(),
            6,
        ),
        "validated_at": validated_at.isoformat(),
    }


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


def _validate_remote_freshness(value: object, location: str) -> dict[str, object]:
    freshness = _exact_object(value, REMOTE_FRESHNESS_FIELDS, location)
    if freshness["method"] != "git_ls_remote":
        raise ValueError(f"{location}.method is invalid")
    if freshness["status"] not in {
        "ABSENT",
        "NOT_VERIFIED",
        "CURRENT",
        "STALE",
        "ERROR",
    }:
        raise ValueError(f"{location}.status is invalid")
    _utc_timestamp(freshness["checked_at"], f"{location}.checked_at")
    for field in ("remote", "remote_ref"):
        if freshness[field] is not None:
            text = _nonempty_string(freshness[field], f"{location}.{field}")
            if "://" in text or "@" in text:
                raise ValueError(f"{location}.{field} exposes a remote URL")
    for field in ("local_oid", "remote_oid"):
        if freshness[field] is not None:
            _exact_commit(freshness[field], f"{location}.{field}")
    if freshness["error"] not in {
        None,
        "remote_query_failed",
        "remote_response_invalid",
    }:
        raise ValueError(f"{location}.error is invalid")
    if freshness["status"] == "CURRENT" and (
        freshness["local_oid"] is None
        or freshness["local_oid"] != freshness["remote_oid"]
    ):
        raise ValueError(f"{location} current OIDs differ")
    return freshness


def _validate_git_state(
    value: object,
    location: str,
    *,
    execution: bool,
) -> dict[str, object]:
    fields = EXECUTION_GIT_FIELDS if execution else GIT_STATE_FIELDS
    state = _exact_object(value, fields, location)
    _exact_commit(state["audited_commit"], f"{location}.audited_commit")
    if state["branch"] is not None:
        _nonempty_string(state["branch"], f"{location}.branch")
    if type(state["detached"]) is not bool:
        raise ValueError(f"{location}.detached must be boolean")
    if state["detached"] != (state["branch"] is None):
        raise ValueError(f"{location} branch/detached state is inconsistent")
    for field in ("configured_upstream", "base_ref"):
        if state[field] is not None:
            _nonempty_string(state[field], f"{location}.{field}")
    for field in ("base_commit", "merge_base"):
        if state[field] is not None:
            _exact_commit(state[field], f"{location}.{field}")
    for field in ("ahead", "behind"):
        if state[field] is not None:
            _exact_int(state[field], f"{location}.{field}")
    paths = state["tracked_dirty_paths"]
    if not isinstance(paths, list):
        raise ValueError(f"{location}.tracked_dirty_paths must be a list")
    normalized = [validate_relative_path(path) for path in paths]
    if normalized != sorted(set(normalized)):
        raise ValueError(f"{location}.tracked_dirty_paths must be unique and sorted")
    _exact_int(state["untracked_count"], f"{location}.untracked_count")
    freshness = _validate_remote_freshness(
        state["remote_freshness"],
        f"{location}.remote_freshness",
    )
    if freshness["local_oid"] is not None and (
        freshness["local_oid"] != state["base_commit"]
    ):
        raise ValueError(f"{location} upstream local OID differs from base commit")
    if state["configured_upstream"] is not None:
        remote = freshness["remote"]
        remote_ref = freshness["remote_ref"]
        if (
            not isinstance(remote, str)
            or not isinstance(remote_ref, str)
            or not remote_ref.startswith("refs/heads/")
            or state["configured_upstream"]
            != f"{remote}/{remote_ref.removeprefix('refs/heads/')}"
        ):
            raise ValueError(f"{location} configured upstream identity differs")
    if execution and (
        state["tracked_clean_before_and_after"] is not True
        or state["detached"] is not True
        or state["branch"] is not None
        or state["tracked_dirty_paths"] != []
    ):
        raise ValueError(
            "execution source must be detached and tracked-clean before and after"
        )
    return state


def validate_execution_source(
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> None:
    for state in (before, after):
        if (
            state.get("detached") is not True
            or state.get("branch") is not None
            or state.get("tracked_dirty_paths") != []
        ):
            raise ValueError("execution source must be detached and tracked-clean")
    if before.get("audited_commit") != after.get("audited_commit"):
        raise ValueError("execution source commit changed during audit")


def derive_decisions(inputs: Mapping[str, object]) -> dict[str, str]:
    payload = _exact_object(dict(inputs), ROOT_INPUT_FIELDS, "release inputs")
    git = _exact_object(payload["git"], GIT_FIELDS, "release inputs.git")
    execution = _validate_git_state(
        git["execution_source"],
        "release inputs.git.execution_source",
        execution=True,
    )
    candidate = _validate_git_state(
        git["candidate"],
        "release inputs.git.candidate",
        execution=False,
    )
    migration = _exact_object(
        payload["migration"], MIGRATION_FULL_FIELDS, "release inputs.migration"
    )
    task = _exact_object(
        payload["task00_20"], TASK_FULL_FIELDS, "release inputs.task00_20"
    )
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
    freshness = candidate["remote_freshness"]
    assert isinstance(freshness, dict)
    branch_ready = (
        execution["audited_commit"] == migration["implementation_commit"]
        and candidate["tracked_dirty_paths"] == []
        and candidate["configured_upstream"] is not None
        and candidate["base_ref"] is not None
        and candidate["base_commit"] is not None
        and candidate["merge_base"] is not None
        and candidate["behind"] == 0
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


def _validate_source_artifact(
    value: object,
    *,
    generated_at: str,
    location: str,
) -> None:
    artifact = _exact_object(
        value,
        {
            "sha256",
            "bytes",
            "generated_at",
            "age_seconds_at_audit",
            "validated_at",
        },
        location,
    )
    _exact_sha(artifact["sha256"], f"{location}.sha256")
    _exact_int(artifact["bytes"], f"{location}.bytes", minimum=1)
    generated = _utc_timestamp(artifact["generated_at"], f"{location}.generated_at")
    validated = _utc_timestamp(artifact["validated_at"], f"{location}.validated_at")
    if generated.isoformat() != generated_at:
        raise ValueError(f"{location} generated_at differs from embedded evidence")
    expected_age = round((validated - generated).total_seconds(), 6)
    age = _exact_number(
        artifact["age_seconds_at_audit"],
        f"{location}.age_seconds_at_audit",
    )
    if expected_age < 0 or expected_age > 86400 or age != expected_age:
        raise ValueError(f"{location} historical age is invalid")


def _validate_migration_at_commit(
    root: Path,
    value: object,
    implementation_commit: str,
) -> None:
    migration = _exact_object(value, MIGRATION_FULL_FIELDS, "migration evidence")
    if migration["valid"] is not True:
        raise ValueError("migration evidence is not valid")
    if migration["implementation_commit"] != implementation_commit:
        raise ValueError("migration implementation commit differs")
    artifacts = _exact_object(
        migration["source_artifacts"],
        {"wheel", "restore"},
        "migration source artifacts",
    )
    wheel_artifact = _exact_object(
        artifacts["wheel"],
        {
            "sha256",
            "bytes",
            "generated_at",
            "age_seconds_at_audit",
            "validated_at",
        },
        "wheel source artifact",
    )
    restore_artifact = _exact_object(
        artifacts["restore"],
        {
            "sha256",
            "bytes",
            "generated_at",
            "age_seconds_at_audit",
            "validated_at",
        },
        "restore source artifact",
    )
    wheel_validated = _utc_timestamp(
        wheel_artifact["validated_at"],
        "wheel artifact validated_at",
    )
    restore_validated = _utc_timestamp(
        restore_artifact["validated_at"],
        "restore artifact validated_at",
    )
    wheel = _validate_wheel_evidence(
        migration["wheel"],
        implementation_commit,
        validated_at=wheel_validated,
        embedded=True,
    )
    restore = _validate_restore_evidence(
        migration["restore"],
        implementation_commit,
        validated_at=restore_validated,
        embedded=True,
    )
    if wheel != migration["wheel"] or restore != migration["restore"]:
        raise ValueError("embedded migration evidence is not canonical")
    _validate_source_artifact(
        wheel_artifact,
        generated_at=str(wheel["generated_at"]),
        location="wheel source artifact",
    )
    _validate_source_artifact(
        restore_artifact,
        generated_at=str(restore["generated_at"]),
        location="restore source artifact",
    )
    graph = _exact_object(
        migration["graph"],
        {
            "head",
            "revision_count",
            "release_manifest_sha256",
            "schema_contract_sha256",
        },
        "migration graph",
    )
    if (
        graph["head"] != "0010_ai_tasks"
        or _exact_int(graph["revision_count"], "migration revision count") != 18
    ):
        raise ValueError("migration graph is invalid")
    expected_release = _git_blob_fingerprint(
        root,
        implementation_commit,
        "src/dragon_radar/migrations/release_manifest.json",
    )["sha256"]
    expected_schema = _git_blob_fingerprint(
        root,
        implementation_commit,
        "src/dragon_radar/storage/schema_contract.json",
    )["sha256"]
    if graph["release_manifest_sha256"] != expected_release:
        raise ValueError("migration release manifest hash differs")
    if graph["schema_contract_sha256"] != expected_schema:
        raise ValueError("migration schema contract hash differs")


def _validate_manifest_shape(
    value: dict[str, object],
    *,
    root: Path | None = None,
) -> None:
    _exact_object(value, MANIFEST_FIELDS, "release manifest")
    if (
        value["schema"] != SCHEMA
        or _exact_int(value["schema_version"], "release manifest schema_version")
        != SCHEMA_VERSION
    ):
        raise ValueError("release manifest schema is unsupported")
    implementation_commit = _exact_commit(
        value["implementation_commit"],
        "implementation commit",
    )
    if value["implementation_commit_relation"] != (
        "same_head_or_docs_evidence_only_child"
    ):
        raise ValueError("implementation commit relation is invalid")
    decisions = _exact_object(value["decisions"], DECISION_FIELDS, "decisions")
    inputs = {field: value[field] for field in ROOT_INPUT_FIELDS}
    if root is not None:
        _validate_migration_at_commit(root, value["migration"], implementation_commit)
        expected_task = collect_task20_evidence(
            root,
            implementation_commit,
            run_verifier=False,
        )
        if value["task00_20"] != expected_task:
            raise ValueError("Task00-20 evidence differs from committed evidence")
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
    expected_inventory = collect_task_inventory(root, implementation_commit)
    for task_number, raw in enumerate(inventory):
        item = _exact_object(raw, {"task", "files"}, f"Task{task_number:02d}")
        if (
            type(item["task"]) is not int
            or item["task"] != task_number
            or not isinstance(item["files"], list)
            or not item["files"]
        ):
            raise ValueError("Task00-20 inventory ordering is invalid")
        expected_paths = list(TASK_EVIDENCE_MAP[task_number])
        actual_paths = [
            raw_file.get("path") if isinstance(raw_file, dict) else None
            for raw_file in item["files"]
        ]
        if actual_paths != expected_paths:
            raise ValueError("Task00-20 inventory mapping differs")
        for raw_file in item["files"]:
            file_item = _exact_object(
                raw_file,
                {"path", "sha256", "bytes"},
                f"Task{task_number:02d} file",
            )
            path_value = validate_relative_path(file_item["path"])
            if path_value not in tracked:
                raise ValueError(f"inventory path is not tracked: {path_value}")
            content = _git_blob(root, implementation_commit, path_value)
            if hashlib.sha256(content).hexdigest() != _exact_sha(
                file_item["sha256"],
                "inventory SHA-256",
            ):
                raise ValueError(f"inventory SHA-256 mismatch: {path_value}")
            if len(content) != _exact_int(file_item["bytes"], "inventory bytes"):
                raise ValueError(f"inventory byte count mismatch: {path_value}")
    if inventory != expected_inventory:
        raise ValueError("Task00-20 inventory differs from committed evidence")


def check_release_manifest(root: Path, path: Path) -> dict[str, object]:
    root = Path(root).resolve()
    manifest_path = Path(path).resolve()
    manifest = load_strict_json(manifest_path)
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
    _validate_manifest_shape(manifest, root=root)
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
    *,
    candidate_worktree: Path,
    verify_remote: bool = False,
) -> dict[str, object]:
    execution_before = collect_git_state(root)
    implementation_commit = str(execution_before["audited_commit"])
    candidate = collect_git_state(
        candidate_worktree,
        verify_remote=verify_remote,
    )
    if candidate["audited_commit"] != implementation_commit:
        raise ValueError("candidate worktree is not at the implementation commit")
    migration = collect_migration_evidence(
        wheel_evidence,
        restore_evidence,
        implementation_commit,
    )
    task = collect_task20_evidence(root, implementation_commit)
    execution_after = collect_git_state(root)
    validate_execution_source(execution_before, execution_after)
    execution = {
        **execution_before,
        "tracked_clean_before_and_after": True,
    }
    external = {gate: "BLOCKED" for gate in EXTERNAL_GATES}
    supply_chain = {gate: "BLOCKED" for gate in SUPPLY_CHAIN_GATES}
    inputs = {
        "git": {
            "execution_source": execution,
            "candidate": candidate,
        },
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
    parser.add_argument(
        "--candidate-worktree",
        type=Path,
        help="original candidate worktree used for branch and dirty-state evidence",
    )
    parser.add_argument(
        "--verify-remote",
        action="store_true",
        help="query the configured candidate upstream with git ls-remote",
    )
    args = parser.parse_args(argv)
    try:
        if args.check is not None:
            check_release_manifest(REPOSITORY_ROOT, args.check)
            print(f"release audit manifest verified: {args.check}")
            return 0
        if not all(
            value is not None
            for value in (
                args.wheel_evidence,
                args.restore_evidence,
                args.output,
                args.candidate_worktree,
            )
        ):
            raise ValueError(
                "--wheel-evidence, --restore-evidence, --output, and "
                "--candidate-worktree are required"
            )
        report = build_release_manifest(
            REPOSITORY_ROOT,
            args.wheel_evidence,
            args.restore_evidence,
            candidate_worktree=args.candidate_worktree,
            verify_remote=args.verify_remote,
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
