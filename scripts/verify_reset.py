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
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_SCHEMA = "dragon-radar.task20-evidence"
EVIDENCE_SCHEMA_VERSION = 3
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
MEASURED_PATHS = {
    "/api/snapshots/{id}",
    "/api/market/overview",
    "/api/rank/range",
    "/api/stock/{id}",
}
WRITE_VERBS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "CREATE",
    "ALTER",
    "DROP",
    "TRUNCATE",
}
PLAN_BUFFER_FIELDS = {
    "shared_hit_blocks",
    "shared_read_blocks",
    "shared_dirtied_blocks",
    "shared_written_blocks",
    "local_hit_blocks",
    "local_read_blocks",
    "local_dirtied_blocks",
    "local_written_blocks",
    "temp_read_blocks",
    "temp_written_blocks",
}
PAYLOAD_PROJECTION_RELATIONS = {
    "publication_results",
    "source_dependencies",
    "withdrawal_dependencies",
    "withdrawal_records",
}
URL_CREDENTIAL = re.compile(r"(://[^:/\s]+:)[^@\s]+(@)")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SUMMARY_COUNT = re.compile(r"(?<![\w-])(\d+)\s+(passed|failed|skipped)\b")
TAP_COUNT = re.compile(r"^#\s+(pass|fail|skipped)\s+(\d+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class CommandSpec:
    command: tuple[str, ...]
    cwd: str
    timeout_seconds: int
    environment_class: str
    environment_overrides: tuple[tuple[str, str], ...] = ()
    requires_test_counts: bool = True

    def __getitem__(self, key: str) -> Any:
        if key == "environment_overrides":
            return dict(self.environment_overrides)
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except AttributeError:
            return default


def _spec(
    command: list[str],
    *,
    cwd: str = ".",
    timeout: int = 900,
    environment_class: str = "default",
    environment_overrides: Mapping[str, str] | None = None,
    requires_test_counts: bool = True,
) -> CommandSpec:
    return CommandSpec(
        command=tuple(command),
        cwd=cwd,
        timeout_seconds=timeout,
        environment_class=environment_class,
        environment_overrides=tuple(sorted((environment_overrides or {}).items())),
        requires_test_counts=requires_test_counts,
    )


_GATE_SPECS: dict[str, list[CommandSpec]] = {
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
    "ruff_lint": [
        _spec(["uv", "run", "ruff", "check", "."], requires_test_counts=False)
    ],
    "ruff_format": [
        _spec(
            ["uv", "run", "ruff", "format", "--check", "."],
            requires_test_counts=False,
        )
    ],
    "compileall": [
        _spec(
            ["uv", "run", "python", "-m", "compileall", "-q", "src", "tests"],
            requires_test_counts=False,
        )
    ],
    "frontend_tests": [_spec(["npm", "test"], cwd="web", timeout=1200)],
    "frontend_builds": [
        _spec(["npm", "run", "build"], cwd="web", requires_test_counts=False)
    ],
    "frontend_bundle": [
        _spec(
            ["npm", "run", "check:bundle"],
            cwd="web",
            requires_test_counts=False,
        )
    ],
    "frontend_private_build": [
        _spec(
            ["npm", "run", "build"],
            cwd="web",
            environment_class="private-review",
            environment_overrides={"VITE_PRIVATE_REVIEW": "1"},
            requires_test_counts=False,
        )
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
        _spec(
            ["npm", "run", "test:contract-runtime"],
            cwd="web",
            timeout=1800,
        )
    ],
}
GATE_SPECS: Mapping[str, tuple[CommandSpec, ...]] = MappingProxyType(
    {gate: tuple(specs) for gate, specs in _GATE_SPECS.items()}
)
del _GATE_SPECS
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


@dataclass(frozen=True)
class RunResult:
    started_at: datetime
    ended_at: datetime
    exit_status: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False


@dataclass(frozen=True)
class ExecutionEvidence:
    manifest: dict[str, Any]


class Runner(Protocol):
    def run(
        self,
        *,
        argv: tuple[str, ...],
        cwd: Path,
        environment: Mapping[str, str],
        timeout_seconds: int,
    ) -> RunResult: ...


class SubprocessRunner:
    def run(
        self,
        *,
        argv: tuple[str, ...],
        cwd: Path,
        environment: Mapping[str, str],
        timeout_seconds: int,
    ) -> RunResult:
        started_at = datetime.now(UTC)
        try:
            executable = shutil.which(argv[0])
            process_argv = (executable, *argv[1:]) if executable is not None else argv
            completed = subprocess.run(
                process_argv,
                cwd=cwd,
                env=dict(environment),
                capture_output=True,
                check=False,
                shell=False,
                timeout=timeout_seconds,
            )
            return RunResult(
                started_at=started_at,
                ended_at=datetime.now(UTC),
                exit_status=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout if isinstance(error.stdout, bytes) else b""
            stderr = error.stderr if isinstance(error.stderr, bytes) else b""
            return RunResult(
                started_at=started_at,
                ended_at=datetime.now(UTC),
                exit_status=124,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
            )
        except OSError as error:
            return RunResult(
                started_at=started_at,
                ended_at=datetime.now(UTC),
                exit_status=127,
                stdout=b"",
                stderr=f"{type(error).__name__}: {error}\n".encode(),
            )


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


def _repository_state_from_status(
    head: str | None,
    status_lines: list[str],
) -> dict[str, Any]:
    disallowed = [
        line
        for line in status_lines
        if not line.replace("\\", "/").startswith("?? .temp/")
    ]
    return {
        "head": head,
        "tracked_clean": not disallowed,
        "disallowed_changes": disallowed,
    }


def _repository_state() -> dict[str, Any]:
    head = _git_value("rev-parse", "HEAD")
    status = _git_value("status", "--porcelain=v1", "--untracked-files=all")
    if status is None:
        return _repository_state_from_status(
            head,
            ["git status failed"],
        )
    return _repository_state_from_status(head, status.splitlines())


def _repository_errors(before: Any, after: Any) -> list[str]:
    expected = {"head", "tracked_clean", "disallowed_changes"}
    errors = [
        *_exact_fields(before, expected, "Repository preflight"),
        *_exact_fields(after, expected, "Repository postflight"),
    ]
    if errors:
        return errors
    for label, state in (("preflight", before), ("postflight", after)):
        if not isinstance(state["head"], str) or not re.fullmatch(
            r"[0-9a-f]{40}", state["head"]
        ):
            errors.append(f"Repository {label} HEAD is invalid.")
        if state["tracked_clean"] is not True:
            errors.append(f"Repository {label} is not clean.")
        if not isinstance(state["disallowed_changes"], list) or any(
            not isinstance(item, str) for item in state["disallowed_changes"]
        ):
            errors.append(f"Repository {label} changes are invalid.")
        elif state["disallowed_changes"]:
            errors.append(f"Repository {label} has disallowed changes.")
    if before["head"] != after["head"]:
        errors.append("Repository HEAD changed during verification.")
    return errors


def _counts(
    output: str,
    exit_status: int,
    *,
    fallback_from_exit: bool = True,
) -> dict[str, int]:
    output = ANSI_ESCAPE.sub("", output)
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for number, label in SUMMARY_COUNT.findall(output):
        counts[label] = max(counts[label], int(number))
    for label, number in TAP_COUNT.findall(output):
        normalized = "failed" if label == "fail" else label
        counts[normalized] = max(counts[normalized], int(number))
    for line in output.splitlines():
        try:
            native = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(native, dict) and native.get("status") in {"passed", "failed"}:
            counts[native["status"]] = max(counts[native["status"]], 1)
    if fallback_from_exit and not any(counts.values()):
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


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _snapshot_artifact(source: Path, destination_dir: Path) -> dict[str, Any]:
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    destination = destination_dir / f"reset-performance-{digest}.json"
    if destination.is_file() and destination.read_bytes() != content:
        raise RuntimeError("Content-addressed evidence artifact collision")
    if not destination.is_file():
        _atomic_write(destination, content)
    return _artifact(destination)


def _command_result_errors(
    result: RunResult,
    *,
    requires_test_counts: bool,
) -> list[str]:
    errors: list[str] = []
    if result.ended_at < result.started_at:
        errors.append("Command end time precedes its start time.")
    if result.exit_status != 0:
        errors.append("Command exit status is non-zero.")
    if result.timed_out:
        errors.append("Command timed out.")
    if requires_test_counts:
        output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        counts = _counts(
            output,
            result.exit_status,
            fallback_from_exit=False,
        )
        if counts["passed"] <= 0 or counts["failed"] != 0 or counts["skipped"] != 0:
            errors.append(
                "Command-native output requires positive passes with zero "
                "failures/skips."
            )
    return errors


def _run_command(
    gate: str,
    index: int,
    spec: dict[str, Any],
    evidence_dir: Path,
    runner: Runner,
) -> dict[str, Any]:
    cwd = ROOT / spec["cwd"]
    command = tuple(spec["command"])
    process_environment = os.environ.copy()
    process_environment.update(spec.get("environment_overrides", {}))
    try:
        result = runner.run(
            argv=command,
            cwd=cwd,
            environment=process_environment,
            timeout_seconds=spec["timeout_seconds"],
        )
    except (StopIteration, AssertionError) as error:
        raise RuntimeError(
            f"Runner did not execute allowlisted command {gate}[{index}]."
        ) from error
    stdout_path = evidence_dir / "logs" / f"{gate}-{index}.stdout.log"
    stderr_path = evidence_dir / "logs" / f"{gate}-{index}.stderr.log"
    _atomic_write(stdout_path, result.stdout)
    _atomic_write(stderr_path, result.stderr)
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    requires_test_counts = spec.get("requires_test_counts", True)
    errors = _command_result_errors(
        result,
        requires_test_counts=requires_test_counts,
    )
    return {
        "argv": list(command),
        "cwd": spec["cwd"],
        "environment_class": spec["environment_class"],
        "started_at": result.started_at.isoformat(),
        "ended_at": result.ended_at.isoformat(),
        "exit_status": result.exit_status,
        "timed_out": result.timed_out,
        "observed_counts": _counts(
            output,
            result.exit_status,
            fallback_from_exit=not requires_test_counts,
        ),
        "stdout_artifact": _artifact(stdout_path),
        "stderr_artifact": _artifact(stderr_path),
        "errors": errors,
    }


def _normalized_sql(statement: str) -> str:
    return " ".join(statement.split())


def _statement_verb(statement: str) -> str:
    normalized = statement.lstrip().upper()
    first = normalized.split(maxsplit=1)[0] if normalized else "UNKNOWN"
    if first == "WITH":
        for verb in WRITE_VERBS:
            if re.search(rf"\b{verb}\b", normalized):
                return verb
        return "SELECT"
    return first


def _sql_relations(statement: str) -> list[str]:
    return sorted(
        {
            match.group(1).lower()
            for match in re.finditer(
                r'\b(?:FROM|JOIN)\s+(?:(?:"?[\w]+"?)\.)?"?([\w]+)"?',
                statement,
                flags=re.IGNORECASE,
            )
        }
    )


def _plan_role(statement: str, relations: list[str]) -> str | None:
    if relations == ["publications"] and re.search(
        r"\bWHERE\s+publications\.id\s*=", statement, flags=re.IGNORECASE
    ):
        return "generic_publication_lookup"
    if set(relations) & PAYLOAD_PROJECTION_RELATIONS:
        return "payload_projection_read"
    return None


def _attribution_groups(
    endpoint_hashes: dict[str, set[str]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[str]] = {}
    for path, hashes in endpoint_hashes.items():
        grouped.setdefault(tuple(sorted(hashes)), []).append(path)
    groups = [
        {
            "scope": (
                "shared_repository_projection"
                if len(endpoints) > 1
                else "endpoint_repository_projection"
            ),
            "endpoints": sorted(endpoints),
            "sql_hashes": list(hashes),
        }
        for hashes, endpoints in grouped.items()
    ]
    return sorted(groups, key=lambda item: item["endpoints"])


def _plan_errors(plan: Any, sql_relations: list[str], label: str) -> list[str]:
    errors: list[str] = []
    field_errors = _exact_fields(
        plan,
        {"relations", "indexes", "nodes", "node_count", "buffers"},
        f"{label} normalized plan",
    )
    if field_errors:
        return field_errors
    relations = plan["relations"]
    indexes = plan["indexes"]
    nodes = plan["nodes"]
    if (
        not isinstance(relations, list)
        or relations != sorted(set(relations))
        or relations != sql_relations
    ):
        errors.append(f"{label} plan relations do not match its SQL relations.")
    if not isinstance(indexes, list) or indexes != sorted(set(indexes)):
        errors.append(f"{label} plan indexes are not normalized.")
    if (
        not isinstance(nodes, list)
        or nodes != sorted(set(nodes))
        or not nodes
        or type(plan["node_count"]) is not int
        or plan["node_count"] < len(nodes)
    ):
        errors.append(f"{label} plan is nontrivial only when nodes are recorded.")
    buffers = plan["buffers"]
    if (
        not isinstance(buffers, dict)
        or set(buffers) != PLAN_BUFFER_FIELDS
        or any(
            type(count) not in (int, float) or count < 0 for count in buffers.values()
        )
    ):
        errors.append(f"{label} plan buffers are invalid.")
    return errors


def _query_evidence_errors(
    shapes: Any,
    endpoint_hashes: Any,
    attribution_groups: Any,
    plans: Any,
) -> tuple[list[str], int, int]:
    errors: list[str] = []
    statement_count = 0
    write_count = 0
    derived_endpoint_hashes = {path: set() for path in MEASURED_PATHS}
    relevant_read_hashes = set()
    shape_relations: dict[str, list[str]] = {}
    if not isinstance(shapes, dict) or not shapes:
        return ["Observed normalized SQL shapes are missing."], 0, 0
    for digest, shape in shapes.items():
        label = f"Observed SQL shape {digest}"
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            errors.append(f"{label} hash key is invalid.")
            continue
        field_errors = _exact_fields(
            shape,
            {
                "normalized_sql",
                "verb",
                "relations",
                "plan_role",
                "endpoint_observations",
            },
            label,
        )
        if field_errors:
            errors.extend(field_errors)
            continue
        statement = shape["normalized_sql"]
        if not isinstance(statement, str) or not statement:
            errors.append(f"{label} normalized SQL is missing.")
            continue
        if statement != _normalized_sql(statement):
            errors.append(f"{label} SQL is not normalized.")
        if hashlib.sha256(statement.encode("utf-8")).hexdigest() != digest:
            errors.append(f"{label} SHA-256 does not match normalized SQL.")
        verb = shape["verb"]
        if verb != _statement_verb(statement):
            errors.append(f"{label} verb does not match normalized SQL.")
        relations = shape["relations"]
        if relations != _sql_relations(statement):
            errors.append(f"{label} relations do not match normalized SQL.")
            continue
        shape_relations[digest] = relations
        expected_role = _plan_role(statement, relations) if verb == "SELECT" else None
        if shape["plan_role"] != expected_role:
            errors.append(f"{label} plan role does not match its SQL shape.")
        if expected_role == "payload_projection_read":
            relevant_read_hashes.add(digest)
        elif verb == "SELECT" and relations and expected_role is None:
            errors.append(f"{label} relational read has no explicit plan role.")
        observations = shape["endpoint_observations"]
        if not isinstance(observations, dict) or not observations:
            errors.append(f"{label} endpoint observations are missing.")
            continue
        for path, timing in observations.items():
            timing_label = f"{label} endpoint {path}"
            if path not in MEASURED_PATHS:
                errors.append(f"{timing_label} is not a measured endpoint.")
                continue
            timing_errors = _exact_fields(
                timing,
                {"count", "total_seconds", "min_seconds", "max_seconds"},
                timing_label,
            )
            if timing_errors:
                errors.extend(timing_errors)
                continue
            count = timing["count"]
            total = timing["total_seconds"]
            minimum = timing["min_seconds"]
            maximum = timing["max_seconds"]
            if (
                type(count) is not int
                or count <= 0
                or not all(
                    type(value) in (int, float) for value in (total, minimum, maximum)
                )
                or not 0 <= minimum <= maximum
                or not minimum * count <= total + 1e-12
                or not total <= maximum * count + 1e-12
            ):
                errors.append(f"{timing_label} counts or timings are invalid.")
                continue
            statement_count += count
            if verb in WRITE_VERBS:
                write_count += count
            derived_endpoint_hashes[path].add(digest)
    if not isinstance(endpoint_hashes, dict) or set(endpoint_hashes) != MEASURED_PATHS:
        errors.append("Endpoint shape-hash mapping endpoints are not exact.")
    else:
        for path in sorted(MEASURED_PATHS):
            hashes = endpoint_hashes[path]
            if not derived_endpoint_hashes[path]:
                errors.append(f"Endpoint {path} has no observed SQL.")
            if (
                not isinstance(hashes, list)
                or hashes != sorted(set(hashes))
                or set(hashes) != derived_endpoint_hashes[path]
            ):
                errors.append(
                    f"Endpoint {path} mapping does not match its observed shape set."
                )
    expected_groups = _attribution_groups(derived_endpoint_hashes)
    if attribution_groups != expected_groups:
        errors.append(
            "Query attribution groups do not match the captured endpoint matrix."
        )
    if not isinstance(plans, dict) or set(plans) != relevant_read_hashes:
        errors.append("Payload projection read plan coverage is incomplete.")
        plans = plans if isinstance(plans, dict) else {}
    for digest, item in plans.items():
        label = f"Query plan {digest}"
        if digest not in relevant_read_hashes:
            errors.append(
                f"{label} does not identify a captured payload projection read."
            )
            continue
        if not isinstance(item, dict):
            errors.append(f"{label} is malformed.")
            continue
        sql_relations = shape_relations.get(digest, [])
        status = item.get("status")
        expected_fields = {"status", "sql_relations", "plan"}
        field_errors = _exact_fields(item, expected_fields, label)
        if field_errors:
            errors.extend(field_errors)
            continue
        if item["sql_relations"] != sql_relations:
            errors.append(f"{label} relations do not match its observed SQL shape.")
        if status == "explained":
            errors.extend(_plan_errors(item["plan"], sql_relations, label))
        else:
            errors.append(
                f"{label} required payload projection plan was not explained."
            )
    return errors, statement_count, write_count


def _performance_errors(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["Performance artifact is not an object."]
    errors.extend(
        _exact_fields(
            value,
            {
                "fixture",
                "environment",
                "warmup",
                "runs",
                "aggregate",
                "async_work",
                "observed_query_shapes",
                "endpoint_shape_hashes",
                "query_attribution_groups",
                "application_state_before",
                "application_state_after",
                "query_plan_evidence",
            },
            "Performance artifact",
        )
    )
    legacy_fields = {"measured_path_explains", "public_sql_statements"} & set(value)
    if legacy_fields:
        errors.append(
            "Legacy endpoint-representative SQL evidence is forbidden: "
            + ", ".join(sorted(legacy_fields))
            + "."
        )
    runs = value.get("runs")
    if not isinstance(runs, list) or len(runs) < 2:
        errors.append("Multiple measured performance rounds are required.")
    else:
        for index, run in enumerate(runs):
            if not isinstance(run, dict):
                errors.append(f"Performance run {index} is malformed.")
                continue
            if (
                run.get("round") != index + 1
                or "cold" in str(run.get("cache_condition", "")).lower()
                or run.get("cache_condition") != "post_warmup_caches_uncontrolled"
            ):
                errors.append(
                    f"Performance run {index} has an unsupported cold/cache claim."
                )
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
    else:
        counts = fixture["counts"]
        if (
            not isinstance(counts, dict)
            or type(counts.get("securities")) is not int
            or counts["securities"] < 100
            or type(counts.get("trading_sessions")) is not int
            or counts["trading_sessions"] < 60
        ):
            errors.append(
                "Performance fixture requires at least 100 securities and "
                "60 trading sessions."
            )
    environment = value.get("environment")
    if (
        not isinstance(environment, dict)
        or environment.get("os_cold_cache_controlled") is not False
        or environment.get("cache_claim") != "warmup_then_measured_caches_uncontrolled"
    ):
        errors.append("Performance environment makes an unsupported cache claim.")
    warmup = value.get("warmup")
    if (
        not isinstance(warmup, dict)
        or set(warmup.get("paths", [])) != MEASURED_PATHS
        or warmup.get("request_count") != 20
        or warmup.get("concurrency") != 20
        or warmup.get("successful") != 20
    ):
        errors.append("Performance warmup does not cover every measured path.")
    aggregate = value.get("aggregate")
    if not isinstance(aggregate, dict):
        errors.append("Aggregate performance metrics are missing.")
    elif isinstance(runs, list) and all(isinstance(run, dict) for run in runs):
        expected_requests = sum(run.get("request_count", 0) for run in runs)
        expected_successful = sum(run.get("successful", 0) for run in runs)
        if (
            aggregate.get("round_count") != len(runs)
            or aggregate.get("request_count") != expected_requests
            or aggregate.get("successful") != expected_successful
            or aggregate.get("error_rate")
            != (
                (expected_requests - expected_successful) / expected_requests
                if expected_requests
                else None
            )
        ):
            errors.append("Aggregate performance counts are inconsistent.")
        p50 = aggregate.get("p50_seconds")
        p95 = aggregate.get("p95_seconds")
        maximum = aggregate.get("max_seconds")
        if (
            not all(type(item) in (int, float) for item in (p50, p95, maximum))
            or not 0 <= p50 <= p95 <= maximum
            or p95 > 2
        ):
            errors.append("Aggregate performance exceeds the P95 budget.")
        for field in ("wall_seconds", "process_cpu_seconds"):
            if type(aggregate.get(field)) not in (int, float) or aggregate[field] < 0:
                errors.append(f"Aggregate performance lacks {field}.")
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
    query_errors, statement_count, write_count = _query_evidence_errors(
        value.get("observed_query_shapes"),
        value.get("endpoint_shape_hashes"),
        value.get("query_attribution_groups"),
        value.get("query_plan_evidence"),
    )
    errors.extend(query_errors)
    if write_count:
        errors.append("A public request executed write or DDL SQL.")
    before = value.get("application_state_before")
    after = value.get("application_state_after")
    if not isinstance(before, dict) or not before or before != after:
        errors.append("Comprehensive application table fingerprints changed.")
    if isinstance(runs, list) and all(
        isinstance(run, dict) and type(run.get("sql_statement_count")) is int
        for run in runs
    ):
        if sum(run["sql_statement_count"] for run in runs) != statement_count:
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
        "warmup": value["warmup"],
        "runs": value["runs"],
        "aggregate": value["aggregate"],
        "fixture": value["fixture"],
        "environment": value.get("environment"),
    }


def _public_observations(value: dict[str, Any]) -> dict[str, Any]:
    shapes = value["observed_query_shapes"]
    return {
        "sql_statement_count": sum(
            timing["count"]
            for shape in shapes.values()
            for timing in shape["endpoint_observations"].values()
        ),
        "sql_write_count": sum(
            timing["count"]
            for shape in shapes.values()
            if shape["verb"] in WRITE_VERBS
            for timing in shape["endpoint_observations"].values()
        ),
        "fingerprints_equal": (
            value["application_state_before"] == value["application_state_after"]
        ),
        "application_table_count": len(value["application_state_before"]),
        "endpoint_shape_hashes": value["endpoint_shape_hashes"],
        "query_attribution_groups": value["query_attribution_groups"],
        "query_plan_evidence": value["query_plan_evidence"],
    }


def _async_observations(value: dict[str, Any]) -> dict[str, Any]:
    return dict(value["async_work"])


def _execute_gates(
    mode: str,
    evidence_dir: Path,
    runner: Runner,
) -> list[dict[str, Any]]:
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
                _run_command(gate, index, spec, evidence_dir, runner)
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
        elif (
            gate == "public_no_write"
            and performance_value is not None
            and not performance_errors
        ):
            record["observations"] = _public_observations(performance_value)
        elif (
            gate == "async_over_five_seconds"
            and performance_value is not None
            and not performance_errors
        ):
            record["observations"] = _async_observations(performance_value)
        elif gate == "recovery_drills":
            record["observations"] = {"drills": list(RECOVERY_DRILLS)}
        records.append(record)
    return records


def _run_gates(
    mode: str,
    evidence_dir: Path,
    runner: Runner | None = None,
) -> ExecutionEvidence:
    repository_before = _repository_state()
    preflight_errors = _repository_errors(repository_before, repository_before)
    if preflight_errors:
        raise RuntimeError("Repository preflight failed: " + " ".join(preflight_errors))
    environment = _environment()
    records = _execute_gates(mode, evidence_dir, runner or SubprocessRunner())
    commit = _commit()
    repository_after = _repository_state()
    return ExecutionEvidence(
        {
            "schema": EVIDENCE_SCHEMA,
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "mode": mode,
            "generated_at": datetime.now(UTC).isoformat(),
            "tested_commit": commit["tested_commit"],
            "environment": environment,
            "repository": {
                "before": repository_before,
                "after": repository_after,
            },
            "records": records,
        }
    )


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
    legacy_required = {
        "argv",
        "cwd",
        "environment_class",
        "exit_status",
        "timed_out",
        "observed_counts",
        "artifact",
    }
    executed_required = {
        "argv",
        "cwd",
        "environment_class",
        "started_at",
        "ended_at",
        "exit_status",
        "timed_out",
        "observed_counts",
        "stdout_artifact",
        "stderr_artifact",
        "errors",
    }
    actual_fields = set(value) if isinstance(value, dict) else set()
    required = (
        legacy_required if actual_fields == legacy_required else executed_required
    )
    errors = _exact_fields(value, required, "Command evidence")
    if errors:
        return errors
    if value["argv"] != list(spec["command"]):
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
    requires_test_counts = spec.get("requires_test_counts", True)
    if (
        requires_test_counts
        and not count_errors
        and (counts["passed"] <= 0 or counts["failed"] != 0 or counts["skipped"] != 0)
    ):
        errors.append("Command requires positive passes with zero failures/skips.")
    if required == legacy_required:
        content, artifact_errors = _artifact_value(
            value["artifact"], "Command artifact"
        )
        errors.extend(artifact_errors)
    else:
        stdout, stdout_errors = _artifact_value(
            value["stdout_artifact"], "Command stdout artifact"
        )
        stderr, stderr_errors = _artifact_value(
            value["stderr_artifact"], "Command stderr artifact"
        )
        errors.extend(stdout_errors)
        errors.extend(stderr_errors)
        content = (
            stdout + stderr
            if isinstance(stdout, bytes) and isinstance(stderr, bytes)
            else None
        )
        try:
            started_at = datetime.fromisoformat(value["started_at"])
            ended_at = datetime.fromisoformat(value["ended_at"])
            if started_at.tzinfo is None or ended_at.tzinfo is None:
                raise ValueError
        except (TypeError, ValueError):
            errors.append("Command timestamps are invalid.")
        else:
            if content is not None:
                derived_errors = _command_result_errors(
                    RunResult(
                        started_at=started_at,
                        ended_at=ended_at,
                        exit_status=value["exit_status"],
                        stdout=stdout,
                        stderr=stderr,
                        timed_out=value["timed_out"],
                    ),
                    requires_test_counts=requires_test_counts,
                )
                if value["errors"] != derived_errors:
                    errors.append(
                        "Command errors do not match captured Runner results."
                    )
                errors.extend(derived_errors)
        if not isinstance(value["errors"], list) or any(
            not isinstance(item, str) for item in value["errors"]
        ):
            errors.append("Command errors are invalid.")
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
        {
            "raw_artifact",
            "warmup",
            "runs",
            "aggregate",
            "fixture",
            "environment",
        },
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
        for field in ("warmup", "runs", "aggregate", "fixture", "environment"):
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


def _validate_manifest(
    value: Any,
    *,
    trust: str = "INTEGRITY_ONLY",
) -> tuple[dict[str, Any], int]:
    manifest_fields = {
        "schema",
        "schema_version",
        "mode",
        "generated_at",
        "tested_commit",
        "environment",
        "repository",
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
    repository = manifest.get("repository")
    repository_errors = _exact_fields(
        repository,
        {"before", "after"},
        "Repository evidence",
    )
    global_errors.extend(repository_errors)
    if not repository_errors:
        global_errors.extend(
            _repository_errors(repository["before"], repository["after"])
        )
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
            "trust": trust,
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


def _validate_execution(
    evidence: ExecutionEvidence,
) -> tuple[dict[str, Any], int]:
    if type(evidence) is not ExecutionEvidence:
        raise TypeError("Automated acceptance requires in-process execution evidence.")
    return _validate_manifest(evidence.manifest, trust="EXECUTED")


def _load_manifest(path: Path) -> tuple[Any, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return {}, f"Evidence could not be read: {type(error).__name__}."


def _integrity_only_report(
    report: dict[str, Any],
    validation_exit_status: int,
    load_error: str | None,
) -> tuple[dict[str, Any], int]:
    integrity_passed = validation_exit_status == 0 and load_error is None
    report["integrity_validation"] = {
        "status": "pass" if integrity_passed else "fail",
        "reason": (
            "Existing evidence is internally consistent but has no execution "
            "provenance."
            if integrity_passed
            else load_error or "Existing evidence failed integrity validation."
        ),
    }
    report["automated_acceptance"] = {
        "status": "unverified",
        "reason": (
            "UNVERIFIED/INTEGRITY_ONLY evidence cannot satisfy automated acceptance."
        ),
    }
    report.setdefault("source", {})["trust"] = "INTEGRITY_ONLY"
    return report, 3 if integrity_passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("full", "focused"),
        default="full",
        help=(
            "full is the default acceptance run; focused can only produce "
            "partial evidence."
        ),
    )
    parser.add_argument(
        "--validate-existing",
        type=Path,
        help=(
            "Check existing evidence integrity without granting automated acceptance."
        ),
    )
    parser.add_argument("--evidence-dir", type=Path, default=Path(".temp/acceptance"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    integrity_only = args.validate_existing is not None
    if integrity_only:
        if args.mode != "full":
            parser.error("--mode cannot be combined with --validate-existing")
        manifest, load_error = _load_manifest(args.validate_existing)
        report, exit_status = _validate_manifest(manifest)
        report, exit_status = _integrity_only_report(report, exit_status, load_error)
    else:
        evidence_dir = (
            args.evidence_dir
            if args.evidence_dir.is_absolute()
            else ROOT / args.evidence_dir
        )
        execution = _run_gates(args.mode, evidence_dir)
        raw_path = evidence_dir / "task20-command-evidence.json"
        _atomic_write(
            raw_path,
            (
                json.dumps(
                    execution.manifest,
                    ensure_ascii=True,
                    indent=2,
                )
                + "\n"
            ).encode(),
        )
        report, exit_status = _validate_execution(execution)
    rendered = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    _atomic_write(args.output, rendered.encode())
    sys.stdout.write(rendered)
    return exit_status


if __name__ == "__main__":
    raise SystemExit(main())
