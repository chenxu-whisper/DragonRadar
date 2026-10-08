"""Run or validate bounded Task20 acceptance evidence without self-certification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_SCHEMA = "dragon-radar.task20-evidence"
EVIDENCE_SCHEMA_VERSION = 2
REQUIRED_GATES = (
    "csv_units",
    "reset_acceptance_e2e",
    "recovery_drills",
    "performance",
    "public_no_write",
    "async_over_five_seconds",
    "full_backend",
    "ruff_lint",
    "ruff_format",
    "compileall",
    "frontend_tests",
    "frontend_builds",
    "frontend_bundle",
    "frontend_private_build",
    "frontend_e2e_legacy",
    "frontend_e2e_reset",
    "frontend_e2e_private",
    "frontend_e2e_ai_public",
    "frontend_e2e_ai_private",
    "frontend_e2e_production_isolation",
    "frontend_contract_runtime",
)
EXTERNAL_GATES = (
    "real_input",
    "official_rules",
    "real_model_evaluation",
    "public_sharing_permit",
    "shadow_run_20_trading_days",
)
RECOVERY_DRILLS = (
    "snapshot_revision_and_pinned_rollback",
    "withdrawal_and_revocation",
    "source_failure",
    "local_backup_restore",
    "owner_session_expiry_and_revoke",
    "ai_off",
)
PERFORMANCE_ARTIFACT = ROOT / ".temp" / "acceptance" / "reset-performance.json"
URL_CREDENTIAL = re.compile(r"(://[^:/\s]+:)[^@\s]+(@)")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SUMMARY_COUNT = re.compile(r"(?<![\w-])(\d+)\s+(passed|failed|skipped)\b")
TAP_COUNT = re.compile(r"^#\s+(pass|fail|skipped)\s+(\d+)\s*$", re.MULTILINE)


def _spec(
    command: list[str],
    *,
    cwd: str = ".",
    timeout: int = 900,
    environment_class: str = "default",
) -> dict[str, Any]:
    return {
        "command": command,
        "cwd": cwd,
        "timeout_seconds": timeout,
        "environment_class": environment_class,
    }


GATE_SPECS: dict[str, list[dict[str, Any]]] = {
    "csv_units": [
        _spec(
            [
                "npm",
                "exec",
                "--",
                "vitest",
                "run",
                "tests/unit/reset-export.test.ts",
            ],
            cwd="web",
        )
    ],
    "reset_acceptance_e2e": [
        _spec(
            [
                "npm",
                "exec",
                "--",
                "playwright",
                "test",
                "tests/e2e/reset-acceptance.spec.ts",
                "--config",
                "playwright.reset.config.ts",
            ],
            cwd="web",
        )
    ],
    "recovery_drills": [
        _spec(
            [
                "uv",
                "run",
                "pytest",
                "-q",
                "-rs",
                "tests/test_reset_publication.py::test_replay_is_idempotent_conflicting_key_rejected_and_revision_appends",
                "tests/test_reset_publication.py::test_fixed_publication_and_old_snapshot_ids_remain_pinned",
                "tests/test_reset_retention.py::test_display_withdrawal_blocks_reads_clears_cache_and_preserves_storage",
                "tests/test_reset_ingestion.py::test_materialization_failure_rolls_back_archive_and_workflow",
                "tests/test_owner_api.py::test_session_expiry_revocation_and_origin_binding",
                "tests/test_owner_api.py::test_owner_and_market_query_allowlists_and_source_error",
            ],
        ),
        _spec(
            [
                "npm",
                "exec",
                "--",
                "playwright",
                "test",
                "tests/e2e/reset-recovery.spec.ts",
                "--config",
                "playwright.reset.config.ts",
            ],
            cwd="web",
        ),
        _spec(["npm", "run", "test:e2e:ai:public"], cwd="web"),
    ],
    "performance": [
        _spec(
            [
                "uv",
                "run",
                "pytest",
                "-q",
                "-rs",
                "tests/performance/test_reset_queries.py",
            ],
            timeout=1200,
        )
    ],
    "full_backend": [_spec(["uv", "run", "pytest", "-q", "-rs"], timeout=2400)],
    "ruff_lint": [_spec(["uv", "run", "ruff", "check", "."])],
    "ruff_format": [_spec(["uv", "run", "ruff", "format", "--check", "."])],
    "compileall": [
        _spec(["uv", "run", "python", "-m", "compileall", "-q", "src", "tests"])
    ],
    "frontend_tests": [_spec(["npm", "test"], cwd="web", timeout=1200)],
    "frontend_builds": [_spec(["npm", "run", "build"], cwd="web")],
    "frontend_bundle": [_spec(["npm", "run", "check:bundle"], cwd="web")],
    "frontend_private_build": [
        _spec(
            ["npm", "run", "build"],
            cwd="web",
            environment_class="private-review",
        )
        | {"environment_overrides": {"VITE_PRIVATE_REVIEW": "1"}},
    ],
    "frontend_e2e_legacy": [_spec(["npm", "run", "test:e2e"], cwd="web", timeout=1200)],
    "frontend_e2e_reset": [
        _spec(["npm", "run", "test:e2e:reset"], cwd="web", timeout=1200)
    ],
    "frontend_e2e_private": [
        _spec(["npm", "run", "test:e2e:private"], cwd="web", timeout=1200)
    ],
    "frontend_e2e_ai_public": [_spec(["npm", "run", "test:e2e:ai:public"], cwd="web")],
    "frontend_e2e_ai_private": [
        _spec(["npm", "run", "test:e2e:ai:private"], cwd="web")
    ],
    "frontend_e2e_production_isolation": [
        _spec(["npm", "run", "test:e2e:production-isolation"], cwd="web")
    ],
    "frontend_contract_runtime": [
        _spec(["npm", "run", "test:contract-runtime"], cwd="web", timeout=1800)
    ],
}
FOCUSED_GATES = {
    "csv_units",
    "reset_acceptance_e2e",
    "recovery_drills",
    "performance",
    "public_no_write",
    "async_over_five_seconds",
    "ruff_lint",
    "ruff_format",
    "compileall",
}


def _redact(value: str) -> str:
    return URL_CREDENTIAL.sub(r"\1***\2", value)


def _run_version(command: list[str]) -> str | None:
    executable = shutil.which(command[0])
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, *command[1:]],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            shell=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (result.stdout or result.stderr).strip()
    return (
        _redact(output.splitlines()[0]) if result.returncode == 0 and output else None
    )


def _environment() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "node": _run_version(["node", "--version"]),
        "postgresql_evidence_available": bool(
            os.environ.get("DRAGON_TEST_DATABASE_URL")
        ),
    }


def _commit() -> dict[str, Any]:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        shell=False,
        timeout=15,
    )
    return {
        "tested_commit": result.stdout.strip() if result.returncode == 0 else None,
        "exit_status": result.returncode,
    }


def _counts(output: str, exit_status: int) -> dict[str, int]:
    output = ANSI_ESCAPE.sub("", output)
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for number, label in SUMMARY_COUNT.findall(output):
        counts[label] = max(counts[label], int(number))
    for label, number in TAP_COUNT.findall(output):
        normalized = "failed" if label == "fail" else label
        counts[normalized] = max(counts[normalized], int(number))
    if not any(counts.values()):
        counts["passed" if exit_status == 0 else "failed"] = 1
    counts["output_lines"] = len(output.splitlines())
    return counts


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def _artifact(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "path": _relative(path),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def _snapshot_artifact(source: Path, destination_dir: Path) -> dict[str, Any]:
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    destination = destination_dir / f"reset-performance-{digest}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.read_bytes() != content:
        raise RuntimeError("Content-addressed evidence artifact collision")
    destination.write_bytes(content)
    return _artifact(destination)


def _run_command(
    gate: str,
    index: int,
    spec: dict[str, Any],
    evidence_dir: Path,
) -> dict[str, Any]:
    cwd = ROOT / spec["cwd"]
    command = list(spec["command"])
    executable = shutil.which(command[0])
    process_environment = os.environ.copy()
    process_environment.update(spec.get("environment_overrides", {}))
    timed_out = False
    try:
        result = subprocess.run(
            [executable, *command[1:]] if executable else command,
            cwd=cwd,
            env=process_environment,
            text=True,
            capture_output=True,
            check=False,
            shell=False,
            timeout=spec["timeout_seconds"],
        )
        exit_status = result.returncode
        output = _redact((result.stdout or "") + (result.stderr or ""))
    except subprocess.TimeoutExpired as error:
        timed_out = True
        exit_status = 124
        stdout = (
            error.stdout.decode() if isinstance(error.stdout, bytes) else error.stdout
        )
        stderr = (
            error.stderr.decode() if isinstance(error.stderr, bytes) else error.stderr
        )
        output = _redact((stdout or "") + (stderr or "") + "\nCOMMAND TIMED OUT\n")
    except OSError as error:
        exit_status = 127
        output = f"{type(error).__name__}: {error}\n"
    log_path = evidence_dir / "logs" / f"{gate}-{index}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(output, encoding="utf-8")
    return {
        "argv": command,
        "cwd": spec["cwd"],
        "environment_class": spec["environment_class"],
        "exit_status": exit_status,
        "timed_out": timed_out,
        "observed_counts": _counts(output, exit_status),
        "artifact": _artifact(log_path),
    }


def _performance_errors(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["Performance artifact is not an object."]
    runs = value.get("runs")
    if not isinstance(runs, list) or len(runs) != 2:
        errors.append("Exactly two cold/warm performance runs are required.")
    else:
        for index, run in enumerate(runs):
            if not isinstance(run, dict):
                errors.append(f"Performance run {index} is malformed.")
                continue
            if (
                run.get("request_count") != 20
                or run.get("concurrency") != 20
                or run.get("successful") != 20
            ):
                errors.append(f"Performance run {index} is not 20/20 successful.")
            request_count = run.get("request_count")
            successful = run.get("successful")
            expected_error_rate = (
                (request_count - successful) / request_count
                if type(request_count) is int
                and request_count > 0
                and type(successful) is int
                else None
            )
            if run.get("error_rate") != expected_error_rate:
                errors.append(f"Performance run {index} error-rate math is invalid.")
            p50 = run.get("p50_seconds")
            p95 = run.get("p95_seconds")
            maximum = run.get("max_seconds")
            if (
                not all(type(item) in (int, float) for item in (p50, p95, maximum))
                or not 0 <= p50 <= p95 <= maximum
                or p95 > 2
            ):
                errors.append(f"Performance run {index} exceeds the P95 budget.")
            for field in ("process_cpu_seconds", "peak_memory_bytes", "rss_bytes"):
                if type(run.get(field)) not in (int, float) or run[field] < 0:
                    errors.append(f"Performance run {index} lacks {field}.")
    fixture = value.get("fixture")
    if not isinstance(fixture, dict) or fixture.get("kind") != "synthetic":
        errors.append("Performance fixture must be labelled synthetic.")
    elif not fixture.get("counts") or not fixture.get("data_volume"):
        errors.append("Performance fixture counts and data volume are required.")
    async_work = value.get("async_work")
    if not isinstance(async_work, dict):
        errors.append("Async workload evidence is missing.")
    else:
        if not isinstance(async_work.get("worker_elapsed_seconds"), (int, float)):
            errors.append("Async worker elapsed time is missing.")
        elif async_work["worker_elapsed_seconds"] <= 5:
            errors.append("Async worker workload did not actually exceed five seconds.")
        if not isinstance(async_work.get("submission_seconds"), (int, float)):
            errors.append("Owner submission elapsed time is missing.")
        elif async_work["submission_seconds"] >= 2:
            errors.append("Owner submission was not quick.")
        if async_work.get("status") != "succeeded":
            errors.append("Async worker did not succeed.")
    statements = value.get("public_sql_statements")
    if not isinstance(statements, list) or not statements:
        errors.append("Measured public SQL statements are missing.")
    elif any(
        not isinstance(item, dict)
        or item.get("verb")
        in {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "TRUNCATE"}
        for item in statements
    ):
        errors.append("A public request executed write or DDL SQL.")
    before = value.get("application_state_before")
    after = value.get("application_state_after")
    if not isinstance(before, dict) or not before or before != after:
        errors.append("Comprehensive application table fingerprints changed.")
    explains = value.get("measured_path_explains")
    if (
        not isinstance(explains, dict)
        or set(explains) != {"/api/snapshots/list", "/api/snapshots/{id}"}
        or any(not item for item in explains.values())
    ):
        errors.append("Measured-path EXPLAIN ANALYZE BUFFERS evidence is incomplete.")
    if (
        isinstance(runs, list)
        and isinstance(statements, list)
        and all(
            isinstance(run, dict) and type(run.get("sql_statement_count")) is int
            for run in runs
        )
    ):
        if sum(run["sql_statement_count"] for run in runs) != len(statements):
            errors.append("Performance SQL statement counts are inconsistent.")
    return errors


def _load_performance() -> tuple[dict[str, Any] | None, list[str]]:
    if not PERFORMANCE_ARTIFACT.is_file():
        return None, ["Performance artifact is missing."]
    try:
        value = json.loads(PERFORMANCE_ARTIFACT.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return None, [f"Performance artifact is unreadable: {type(error).__name__}."]
    errors = _performance_errors(value)
    return value, errors


def _performance_observations(
    value: dict[str, Any], evidence_dir: Path
) -> dict[str, Any]:
    return {
        "raw_artifact": _snapshot_artifact(
            PERFORMANCE_ARTIFACT, evidence_dir / "artifacts"
        ),
        "runs": value["runs"],
        "fixture": value["fixture"],
        "environment": value.get("environment"),
    }


def _public_observations(value: dict[str, Any]) -> dict[str, Any]:
    statements = value["public_sql_statements"]
    return {
        "sql_statement_count": len(statements),
        "sql_write_count": sum(
            item["verb"]
            in {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "TRUNCATE"}
            for item in statements
        ),
        "fingerprints_equal": (
            value["application_state_before"] == value["application_state_after"]
        ),
        "application_table_count": len(value["application_state_before"]),
        "measured_path_explains": value["measured_path_explains"],
    }


def _async_observations(value: dict[str, Any]) -> dict[str, Any]:
    return dict(value["async_work"])


def _run_gates(mode: str, evidence_dir: Path) -> dict[str, Any]:
    environment = _environment()
    selected = set(REQUIRED_GATES) if mode == "full" else FOCUSED_GATES
    records: list[dict[str, Any]] = []
    performance_commands: list[dict[str, Any]] | None = None
    performance_value: dict[str, Any] | None = None
    performance_errors: list[str] = []
    for gate in REQUIRED_GATES:
        if gate not in selected:
            continue
        if gate in {"public_no_write", "async_over_five_seconds"}:
            if performance_commands is None:
                continue
            commands = performance_commands
        else:
            commands = [
                _run_command(gate, index, spec, evidence_dir)
                for index, spec in enumerate(GATE_SPECS[gate], start=1)
            ]
        record = {
            "gate_id": gate,
            "commands": commands,
            "observations": {},
        }
        if gate == "performance":
            performance_commands = commands
            performance_value, performance_errors = _load_performance()
            if not performance_errors and performance_value is not None:
                record["observations"] = _performance_observations(
                    performance_value, evidence_dir
                )
        elif gate == "public_no_write" and performance_value is not None:
            record["observations"] = _public_observations(performance_value)
        elif gate == "async_over_five_seconds" and performance_value is not None:
            record["observations"] = _async_observations(performance_value)
        elif gate == "recovery_drills":
            record["observations"] = {"drills": list(RECOVERY_DRILLS)}
        records.append(record)
    commit = _commit()
    return {
        "schema": EVIDENCE_SCHEMA,
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "mode": mode,
        "generated_at": datetime.now(UTC).isoformat(),
        "tested_commit": commit["tested_commit"],
        "environment": environment,
        "records": records,
    }


def _resolve_artifact(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else ROOT / path


def _exact_fields(value: Any, expected: set[str], label: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{label} is not an object."]
    actual = set(value)
    return (
        []
        if actual == expected
        else [
            f"{label} fields do not match the schema: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}."
        ]
    )


def _artifact_value(value: Any, label: str) -> tuple[Any, list[str]]:
    errors: list[str] = []
    errors.extend(_exact_fields(value, {"path", "sha256", "bytes"}, label))
    if errors:
        return None, errors
    if (
        not isinstance(value["path"], str)
        or not isinstance(value["sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
        or type(value["bytes"]) is not int
        or value["bytes"] < 0
    ):
        return None, [f"{label} metadata is invalid."]
    path = _resolve_artifact(value["path"])
    if not path.is_file():
        return None, [f"{label} is missing."]
    try:
        actual = _artifact(path)
        content = path.read_bytes()
    except OSError as error:
        return None, [f"{label} is unreadable: {type(error).__name__}."]
    if actual["sha256"] != value["sha256"] or actual["bytes"] != value["bytes"]:
        errors.append(f"{label} hash or size does not match.")
    return content, errors


def _validate_command(value: Any, spec: dict[str, Any]) -> list[str]:
    required = {
        "argv",
        "cwd",
        "environment_class",
        "exit_status",
        "timed_out",
        "observed_counts",
        "artifact",
    }
    errors = _exact_fields(value, required, "Command evidence")
    if errors:
        return errors
    if value["argv"] != spec["command"]:
        errors.append("Command argv is not the exact allowlisted argument array.")
    if value["cwd"] != spec["cwd"]:
        errors.append("Command cwd is not allowlisted.")
    if value["environment_class"] != spec["environment_class"]:
        errors.append("Command environment class is not allowlisted.")
    counts = value["observed_counts"]
    count_fields = {"passed", "failed", "skipped", "output_lines"}
    count_errors = _exact_fields(counts, count_fields, "Observed counts")
    errors.extend(count_errors)
    if not count_errors and any(
        type(counts[name]) is not int or counts[name] < 0 for name in count_fields
    ):
        errors.append("Observed counts are invalid.")
    if type(value["exit_status"]) is not int or value["exit_status"] != 0:
        errors.append("Command exit status is non-zero.")
    if value["timed_out"] is not False:
        errors.append("Command timed out.")
    if not count_errors and (
        counts["passed"] <= 0 or counts["failed"] != 0 or counts["skipped"] != 0
    ):
        errors.append("Command requires positive passes with zero failures/skips.")
    content, artifact_errors = _artifact_value(value["artifact"], "Command artifact")
    errors.extend(artifact_errors)
    if content is not None and not count_errors:
        try:
            derived_counts = _counts(content.decode("utf-8"), value["exit_status"])
        except UnicodeError:
            errors.append("Command artifact is not UTF-8 text.")
        else:
            if counts != derived_counts:
                errors.append("Observed counts do not match the hashed command log.")
    return errors


def _raw_performance(observations: Any) -> tuple[dict[str, Any] | None, list[str]]:
    errors = _exact_fields(
        observations,
        {"raw_artifact", "runs", "fixture", "environment"},
        "Performance observations",
    )
    if errors:
        return None, errors
    content, artifact_errors = _artifact_value(
        observations["raw_artifact"], "Performance raw artifact"
    )
    errors.extend(artifact_errors)
    if content is None:
        return None, errors
    try:
        raw = json.loads(content)
    except (UnicodeError, json.JSONDecodeError) as error:
        return None, [
            *errors,
            f"Performance raw artifact is invalid JSON: {type(error).__name__}.",
        ]
    errors.extend(_performance_errors(raw))
    if isinstance(raw, dict):
        for field in ("runs", "fixture", "environment"):
            if observations[field] != raw.get(field):
                errors.append(f"Performance {field} does not match the raw artifact.")
    return raw if isinstance(raw, dict) else None, errors


def _validate_observations(
    gate: str,
    observations: Any,
    raw_performance: dict[str, Any] | None,
) -> list[str]:
    if not isinstance(observations, dict):
        return ["Gate observations are missing."]
    if gate == "performance":
        return _raw_performance(observations)[1]
    if gate == "recovery_drills":
        return (
            []
            if observations == {"drills": list(RECOVERY_DRILLS)}
            else ["Recovery drill observations are incomplete or duplicated."]
        )
    if gate == "public_no_write":
        if raw_performance is None:
            return ["Public no-write evidence lacks valid raw performance evidence."]
        return (
            []
            if observations == _public_observations(raw_performance)
            else ["Public no-write observations do not match the raw artifact."]
        )
    if gate == "async_over_five_seconds":
        if raw_performance is None:
            return ["Async evidence lacks valid raw performance evidence."]
        return (
            []
            if observations == _async_observations(raw_performance)
            else ["Async observations do not match the raw artifact."]
        )
    return [] if observations == {} else ["Unexpected gate observations were claimed."]


def _git_value(*args: str) -> str | None:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        shell=False,
        timeout=15,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _approved_tested_commit(
    tested_commit: Any,
    *,
    head: str | None,
    parent: str | None,
    changed_paths: list[str],
) -> tuple[bool, str | None]:
    if not isinstance(tested_commit, str) or not re.fullmatch(
        r"[0-9a-f]{40}", tested_commit
    ):
        return False, None
    if tested_commit == head:
        return True, "HEAD"
    allowed_prefixes = ("docs/acceptance/", "evidence/", ".github/evidence/")
    evidence_only = bool(changed_paths) and all(
        path.replace("\\", "/").startswith(allowed_prefixes) for path in changed_paths
    )
    if tested_commit == parent and evidence_only:
        return True, "HEAD^"
    return False, None


def _commit_relation(tested_commit: Any) -> tuple[bool, str | None]:
    head = _git_value("rev-parse", "HEAD")
    parent = _git_value("rev-parse", "HEAD^")
    changed = _git_value("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
    return _approved_tested_commit(
        tested_commit,
        head=head,
        parent=parent,
        changed_paths=changed.splitlines() if changed else [],
    )


def _validate_manifest(value: Any) -> tuple[dict[str, Any], int]:
    manifest_fields = {
        "schema",
        "schema_version",
        "mode",
        "generated_at",
        "tested_commit",
        "environment",
        "records",
    }
    global_errors = _exact_fields(value, manifest_fields, "Evidence manifest")
    manifest = value if isinstance(value, dict) else {}
    if manifest.get("schema") != EVIDENCE_SCHEMA:
        global_errors.append("Evidence schema is unsupported.")
    if manifest.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        global_errors.append("Evidence schema version is unsupported.")
    if manifest.get("mode") not in {"full", "focused"}:
        global_errors.append("Evidence mode is unsupported.")
    try:
        generated_at = datetime.fromisoformat(manifest.get("generated_at", ""))
        if generated_at.tzinfo is None:
            raise ValueError
    except (TypeError, ValueError):
        global_errors.append("Evidence generation time is invalid.")
    environment_errors = _exact_fields(
        manifest.get("environment"),
        {"platform", "python", "node", "postgresql_evidence_available"},
        "Evidence environment",
    )
    global_errors.extend(environment_errors)
    commit_ok, commit_relation = _commit_relation(manifest.get("tested_commit"))
    if not commit_ok:
        global_errors.append(
            "tested_commit must equal HEAD, or HEAD^ when HEAD is evidence-only."
        )

    records_value = manifest.get("records")
    if not isinstance(records_value, list):
        global_errors.append("Evidence records must be a list.")
        records_value = []
    indexed: dict[str, list[dict[str, Any]]] = {}
    unknown_records: list[str] = []
    malformed_records = False
    for source in records_value:
        if not isinstance(source, dict) or not isinstance(source.get("gate_id"), str):
            malformed_records = True
            continue
        gate_id = source["gate_id"]
        if gate_id not in REQUIRED_GATES:
            unknown_records.append(gate_id)
            continue
        indexed.setdefault(gate_id, []).append(source)
    if malformed_records:
        global_errors.append("A gate record is malformed.")
    if unknown_records:
        global_errors.append(f"Unknown gate IDs: {sorted(unknown_records)}.")

    performance_sources = indexed.get("performance", [])
    raw_performance = None
    if len(performance_sources) == 1:
        raw_performance, _ = _raw_performance(
            performance_sources[0].get("observations")
        )
    gates: dict[str, Any] = {}
    any_failure = bool(global_errors)
    any_missing = False
    for gate in REQUIRED_GATES:
        sources = indexed.get(gate, [])
        if not sources:
            gates[gate] = {"status": "missing", "reason": "Evidence is missing."}
            any_missing = True
            continue
        source = sources[0]
        errors = _exact_fields(
            source, {"gate_id", "commands", "observations"}, "Gate record"
        )
        if len(sources) != 1:
            errors.append("Gate ID is duplicated.")
        commands = source.get("commands")
        expected_specs = GATE_SPECS.get(gate, GATE_SPECS["performance"])
        if not isinstance(commands, list) or len(commands) != len(expected_specs):
            errors.append("Gate command count does not match the allowlist.")
        else:
            errors.extend(
                error
                for command, spec in zip(commands, expected_specs, strict=True)
                for error in _validate_command(command, spec)
            )
        errors.extend(
            _validate_observations(gate, source.get("observations"), raw_performance)
        )
        gates[gate] = {
            "status": "fail" if errors else "pass",
            "commands": commands if isinstance(commands, list) else [],
            "observations": source.get("observations", {}),
        }
        if errors:
            gates[gate]["errors"] = errors
            any_failure = True
    if manifest.get("mode") != "full" and not any_failure:
        any_missing = True
    if any_failure:
        automated = {
            "status": "fail",
            "reason": "Required evidence failed validation.",
            "errors": global_errors,
        }
        exit_status = 1
    elif any_missing:
        automated = {
            "status": "blocked",
            "reason": "Required evidence is missing.",
        }
        exit_status = 2
    else:
        automated = {
            "status": "pass",
            "reason": "All required automated Task20 evidence passed.",
        }
        exit_status = 0
    report: dict[str, Any] = {
        "schema": EVIDENCE_SCHEMA,
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "overall_status": "blocked",
        "automated_acceptance": automated,
        "gates": gates,
        "source": {
            "mode": manifest.get("mode"),
            "tested_commit": manifest.get("tested_commit"),
            "commit_relation": commit_relation,
        },
    }
    reasons = {
        "real_input": "No licensed real input evidence was supplied.",
        "official_rules": "No approved official rule evidence was supplied.",
        "real_model_evaluation": "No real model evaluation or approval was supplied.",
        "public_sharing_permit": "No public-sharing permit was supplied.",
        "shadow_run_20_trading_days": (
            "No evidence for at least 20 real trading days was supplied."
        ),
    }
    for gate in EXTERNAL_GATES:
        report[gate] = {"status": "blocked", "reason": reasons[gate]}
    return report, exit_status


def _load_manifest(path: Path) -> tuple[Any, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return {}, f"Evidence could not be read: {type(error).__name__}."


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("full", "focused", "validate"),
        default="full",
        help=(
            "full is the default acceptance run; focused can only produce "
            "partial evidence."
        ),
    )
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--evidence-dir", type=Path, default=Path(".temp/acceptance"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "validate":
        if args.evidence is None:
            parser.error("--evidence is required with --mode validate")
        manifest, load_error = _load_manifest(args.evidence)
    else:
        if args.evidence is not None:
            parser.error("--evidence is only valid with --mode validate")
        evidence_dir = (
            args.evidence_dir
            if args.evidence_dir.is_absolute()
            else ROOT / args.evidence_dir
        )
        manifest = _run_gates(args.mode, evidence_dir)
        raw_path = evidence_dir / "task20-command-evidence.json"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        load_error = None
    report, exit_status = _validate_manifest(manifest)
    if load_error:
        report["automated_acceptance"] = {"status": "fail", "reason": load_error}
        exit_status = 1
    rendered = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return exit_status


if __name__ == "__main__":
    raise SystemExit(main())
