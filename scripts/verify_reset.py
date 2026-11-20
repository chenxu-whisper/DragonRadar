"""Run or validate bounded Task20 acceptance evidence without self-certification."""

from __future__ import annotations

import argparse
import base64
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
    "prd_contracts",
    "csv_units",
    # The performance trio must run before the browser-heavy Playwright gates:
    # their residual CPU/IO load perturbs the P95 measurement. Derived gates
    # reuse performance's commands, so performance stays first of the three.
    "performance",
    "public_no_write",
    "async_over_five_seconds",
    "reset_acceptance_e2e",
    "recovery_drills",
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
    "frontend_five_builds",
    "frontend_contract_c2",
    "frontend_contract_c3",
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
# Opt-in marker for the Task20 acceptance job: while the isolated gates (P95
# performance measurement, full backend suite) run, the private compose stack
# left running by the TLS setup step is stopped, then resumed for the later
# private browser gates. The marker is harness-only and is stripped from gate
# subprocess environments.
PERF_ISOLATION_ENV = "DRAGON_TASK20_PERF_ISOLATION"
TASK20_COMPOSE_PROJECT_ENV = "DRAGON_TASK20_COMPOSE_PROJECT"
TASK20_STACK_ENV_FILE = ROOT / ".temp" / "acceptance-stack" / "compose.env"
TASK20_COMPOSE_FILES = (
    "compose.yaml",
    ".temp/acceptance-stack/compose.override.yaml",
)
PERF_ISOLATION_ACTIVE = frozenset({"1", "true", "yes", "on"})
# Allowlisted commands marked suspend_private_stack run with the private
# compose stack fully stopped on the Task20 runner: the P95 measurement
# (residual container IO perturbs the sample) and the ~30-minute full backend
# bulk suite (the resumed web/api edge contended against the suite and its
# PostgreSQL and pushed it past the 2400s budget on the pinned 24.04 runner).
# Live TLS gateway checks cannot run stopped, so full_backend runs them as a
# separate stack-up command first and deselects them from the suspended bulk
# command. The stack resumes immediately afterwards for the private browser
# gates.
MEASURED_PATHS = {
    "/api/snapshots/{id}",
    "/api/market/overview",
    "/api/rank/range",
    "/api/stock/{id}",
}
# Performance measurement methodology. Shared CI runners exhibit scheduling
# stalls that a single warmup wave cannot absorb: measured rounds run only
# after multi-wave steady-state warmup and with cyclic garbage collection
# fenced (forced collection, then disabled for the measured window). The P95
# budget itself is unchanged at 2.0 seconds per 20-concurrent-request round.
PERFORMANCE_WARMUP_WAVES = 3
PERFORMANCE_CONCURRENCY = 20
PERFORMANCE_RUN_CACHE_CONDITION = "steady_state_warmup_gc_fenced_caches_uncontrolled"
PERFORMANCE_CACHE_CLAIM = (
    "multi_wave_warmup_then_gc_fenced_measured_caches_uncontrolled"
)
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
    suspend_private_stack: bool = False

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
    suspend_private_stack: bool = False,
) -> CommandSpec:
    return CommandSpec(
        command=tuple(command),
        cwd=cwd,
        timeout_seconds=timeout,
        environment_class=environment_class,
        environment_overrides=tuple(sorted((environment_overrides or {}).items())),
        requires_test_counts=requires_test_counts,
        suspend_private_stack=suspend_private_stack,
    )


_GATE_SPECS: dict[str, list[CommandSpec]] = {
    "prd_contracts": [
        _spec(
            ["uv", "run", "python", "scripts/check_prd_contracts.py"],
            requires_test_counts=False,
        )
    ],
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
            suspend_private_stack=True,
        )
    ],
    "full_backend": [
        # Live TLS gateway checks must reach the running private edge: run
        # them first while the stack resumed by the performance window is up,
        # then deselect them from the suspended bulk command below.
        _spec(
            [
                "uv",
                "run",
                "pytest",
                "-q",
                "-rs",
                "tests/test_private_https.py",
            ],
            timeout=300,
        ),
        _spec(
            [
                "uv",
                "run",
                "pytest",
                "-q",
                "-rs",
                # Measured by the dedicated performance gate; avoid a second,
                # load-perturbed measurement inside the 30+ minute suite.
                "--deselect",
                "tests/performance/test_reset_queries.py",
                # Executed against the live edge by the stack-up command above;
                # the bulk window runs with the private stack stopped.
                "--deselect",
                "tests/test_private_https.py",
            ],
            timeout=2400,
            suspend_private_stack=True,
        ),
    ],
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
        _spec(
            ["npm", "run", "build", "--", "--manifest"],
            cwd="web",
            requires_test_counts=False,
        )
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
CONTRACT_GATES = frozenset(
    {
        "frontend_five_builds",
        "frontend_contract_c2",
        "frontend_contract_c3",
    }
)
BUILD_GATES = frozenset(
    {
        "frontend_builds",
        "frontend_private_build",
        "frontend_bundle",
    }
)
_GATE_SPECS.update(
    {
        "frontend_five_builds": [
            _spec(
                ["node", "web/scripts/test-five-builds.mjs"],
                timeout=7200,
                requires_test_counts=False,
            )
        ],
        "frontend_contract_c2": [
            _spec(
                ["npm", "run", "test:contract-c2"],
                cwd="web",
                timeout=3600,
                requires_test_counts=False,
            )
        ],
        "frontend_contract_c3": [
            _spec(
                ["npm", "run", "test:contract-c3"],
                cwd="web",
                timeout=3600,
                requires_test_counts=False,
            )
        ],
    }
)
GATE_SPECS: Mapping[str, tuple[CommandSpec, ...]] = MappingProxyType(
    {gate: tuple(specs) for gate, specs in _GATE_SPECS.items()}
)
del _GATE_SPECS
FOCUSED_GATES = {
    "prd_contracts",
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


class PrivateStackIsolation:
    """Stop the Task20 private compose stack around isolated command windows.

    Idle db/api/web containers (and their residual checkpoint/IO and
    PostgreSQL polling) are removed from the P95 measurement window and the
    full backend bulk-suite window, then the stack is resumed so later
    private browser gates still reach the TLS gateway. Commands that must
    reach the live gateway (the test_private_https checks) run in a separate
    stack-up window and are never suspended. This deliberately bypasses the
    frozen gate Runner: the allowlisted gate command graph must not gain
    docker entries.
    """

    def __init__(
        self,
        *,
        project: str,
        env_file: Path,
        invoke: Any = None,
        suspend_timeout_seconds: int = 120,
        resume_timeout_seconds: int = 300,
    ) -> None:
        self._project = project
        self._env_file = env_file
        self._invoke = invoke or self._default_invoke
        self._suspend_timeout = suspend_timeout_seconds
        self._resume_timeout = resume_timeout_seconds

    @staticmethod
    def _default_invoke(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout: int,
    ) -> Any:
        return subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(env),
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=timeout,
        )

    def _compose_argv(self, *action: str) -> tuple[str, ...]:
        prefix = (
            "docker",
            "compose",
            "--project-name",
            self._project,
            "--project-directory",
            str(ROOT),
            "--env-file",
            str(self._env_file),
        )
        files = tuple(
            part for name in TASK20_COMPOSE_FILES for part in ("-f", str(ROOT / name))
        )
        return prefix + files + action

    def _run(self, label: str, action: tuple[str, ...], timeout_seconds: int) -> None:
        argv = self._compose_argv(*action)
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(
                (
                    "COMPOSE_",
                    "DRAGON_AI_",
                    "DRAGON_OWNER_",
                    "DRAGON_PUBLIC_",
                    "DRAGON_PRIVATE_",
                )
            )
            and key
            not in {"DRAGON_DB_PASSWORD", "DRAGON_WEB_PORT", "DRAGON_E2E_STATE_DIR"}
        }
        print(
            f"[stack-isolation] {label} private stack '{self._project}'",
            file=sys.stderr,
            flush=True,
        )
        try:
            result = self._invoke(
                argv,
                cwd=ROOT,
                env=environment,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                f"private stack isolation {label} timed out after {timeout_seconds}s"
            ) from error
        except OSError as error:
            raise RuntimeError(
                f"private stack isolation {label} failed to invoke docker: "
                f"{type(error).__name__}: {error}"
            ) from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(
                f"private stack isolation {label} failed with exit "
                f"{result.returncode}: {detail[:500]}"
            )

    def suspend(self) -> None:
        self._run("suspending", ("stop",), self._suspend_timeout)

    def resume(self) -> None:
        self._run(
            "resuming",
            ("up", "--detach", "--wait"),
            self._resume_timeout,
        )


def _performance_isolation(
    environment: Mapping[str, str],
) -> PrivateStackIsolation | None:
    if (
        environment.get(PERF_ISOLATION_ENV, "").strip().lower()
        not in PERF_ISOLATION_ACTIVE
    ):
        return None
    project = environment.get(TASK20_COMPOSE_PROJECT_ENV, "").strip()
    if not project:
        raise RuntimeError(
            f"{PERF_ISOLATION_ENV} is enabled but "
            f"{TASK20_COMPOSE_PROJECT_ENV} is unset; the Task20 private stack "
            "must be identifiable before performance isolation can run."
        )
    if not TASK20_STACK_ENV_FILE.is_file():
        raise RuntimeError(
            f"{PERF_ISOLATION_ENV} is enabled but compose state is missing "
            f"({TASK20_STACK_ENV_FILE}); run acceptance-stack.sh "
            "build/setup before the verifier."
        )
    return PrivateStackIsolation(
        project=project,
        env_file=TASK20_STACK_ENV_FILE,
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
        normalized = {"pass": "passed", "fail": "failed", "skipped": "skipped"}[label]
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
    # The isolation marker is harness-only: gate subprocesses (including the
    # full_backend pytest run that executes these tests) must never perform
    # their own private-stack suspend/resume.
    process_environment.pop(PERF_ISOLATION_ENV, None)
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
        "observed_counts": {
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "output_lines": len(output.splitlines()),
        }
        if gate in CONTRACT_GATES | BUILD_GATES
        else _counts(
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
    *,
    require_plans: bool = True,
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
                or not 0 <= minimum <= maximum < float("inf")
                or not total < float("inf")
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
                or not all(isinstance(item, str) for item in hashes)
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
    if not require_plans:
        return errors, statement_count, write_count
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


def _research_load_errors(value: Any) -> list[str]:
    label = "Concurrent research"
    fields = {
        "synthetic",
        "label",
        "worker",
        "submitted_status",
        "status",
        "config_hash",
        "result_config_hash",
        "engine_iterations",
        "checkpoint_count",
        "securities",
        "trading_sessions",
        "runs",
        "sql_attribution",
        "worker_sql_context",
        "stable_state_excluded_tables",
        "application_state_before",
        "application_state_after",
    }
    errors = _exact_fields(value, fields, label)
    if errors:
        return errors
    if (
        value["sql_attribution"] != "CURRENT_PUBLIC_PATH"
        or value["worker_sql_context"] is not None
    ):
        errors.append(f"{label} SQL attribution must exclude only non-GET context.")
    excluded = ["research_backtest_cache", "research_backtest_runs"]
    if value["stable_state_excluded_tables"] != excluded:
        errors.append(f"{label} stable-state exclusions are not the research tables.")
    states = []
    for phase in ("before", "after"):
        state = value[f"application_state_{phase}"]
        if not isinstance(state, dict) or not set(excluded) <= set(state):
            errors.append(f"{label} application state {phase} is incomplete.")
            states.append({})
            continue
        for table, item in state.items():
            item_errors = _exact_fields(
                item, {"rows", "fingerprint"}, f"{label} {phase} table {table}"
            )
            errors.extend(item_errors)
            if not item_errors and (
                type(item["rows"]) is not int
                or item["rows"] < 0
                or not isinstance(item["fingerprint"], str)
                or not re.fullmatch(r"[0-9a-f]{32}", item["fingerprint"])
            ):
                errors.append(f"{label} {phase} table {table} fingerprint is invalid.")
        states.append(
            {table: item for table, item in state.items() if table not in excluded}
        )
    if len(states[0]) <= 8 or states[0] != states[1]:
        errors.append(
            f"{label} stable application table fingerprints changed or are incomplete."
        )
    if (
        value["synthetic"] is not True
        or value["label"] != "real_engine_repeated_until_public_rounds_finish"
        or value["worker"] != "ResearchBacktestWorker"
        or value["submitted_status"] != "queued"
        or value["status"] != "succeeded"
    ):
        errors.append(f"{label} must use the real worker/engine and succeed.")
    digest = value["config_hash"]
    if (
        not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or value["result_config_hash"] != digest
    ):
        errors.append(f"{label} result is not bound to the submitted config.")
    for field, minimum in (
        ("engine_iterations", 1),
        ("checkpoint_count", 1),
        ("securities", 100),
        ("trading_sessions", 60),
    ):
        if type(value[field]) is not int or value[field] < minimum:
            errors.append(f"{label} lacks valid {field}.")
    runs = value["runs"]
    if not isinstance(runs, list) or len(runs) != 3:
        return [*errors, f"{label} requires three measured rounds."]
    previous = 0
    for index, run in enumerate(runs, 1):
        if not isinstance(run, dict):
            errors.append(f"{label} round {index} is malformed.")
            continue
        before = run.get("checkpoint_count_before")
        after = run.get("checkpoint_count_after")
        if (
            run.get("worker_active_before") is not True
            or run.get("worker_active_after") is not True
            or type(before) is not int
            or type(after) is not int
            or not previous <= before < after
            or type(value["checkpoint_count"]) is not int
            or after > value["checkpoint_count"]
        ):
            errors.append(f"{label} round {index} did not overlap engine progress.")
        if type(after) is int:
            previous = after
        if (
            any(
                type(run.get(field)) is not int
                for field in ("round", "concurrency", "request_count", "successful")
            )
            or type(run.get("error_rate")) not in (int, float)
            or run.get("round") != index
            or run.get("concurrency") != PERFORMANCE_CONCURRENCY
            or run.get("request_count") != PERFORMANCE_CONCURRENCY
            or run.get("successful") != PERFORMANCE_CONCURRENCY
            or run.get("error_rate") != 0
            or run.get("cache_condition") != PERFORMANCE_RUN_CACHE_CONDITION
        ):
            errors.append(f"{label} round {index} is not 20/20 successful.")
        metrics = run.get("path_metrics")
        if not isinstance(metrics, dict) or set(metrics) != MEASURED_PATHS:
            errors.append(f"{label} round {index} lacks ordinary GET coverage.")
            metrics = {}
        for path, metric in [("all GETs", run), *metrics.items()]:
            if not isinstance(metric, dict):
                errors.append(f"{label} round {index} {path} is malformed.")
                continue
            p50, p95, maximum = (
                metric.get(field)
                for field in ("p50_seconds", "p95_seconds", "max_seconds")
            )
            if (
                not all(type(item) in (int, float) for item in (p50, p95, maximum))
                or not 0 <= p50 <= p95 <= maximum < float("inf")
                or p95 > 2
            ):
                errors.append(f"{label} round {index} {path} exceeds P95 budget.")
            if path != "all GETs" and (
                type(metric.get("request_count")) is not int
                or type(metric.get("successful")) is not int
                or metric.get("request_count") != PERFORMANCE_CONCURRENCY // 4
                or metric.get("successful") != PERFORMANCE_CONCURRENCY // 4
            ):
                errors.append(f"{label} round {index} {path} is not 5/5 successful.")
            samples = metric.get("latencies_seconds")
            sample_count = (
                PERFORMANCE_CONCURRENCY
                if path == "all GETs"
                else PERFORMANCE_CONCURRENCY // 4
            )
            if (
                not isinstance(samples, list)
                or len(samples) != sample_count
                or any(
                    type(sample) not in (int, float) or not 0 <= sample < float("inf")
                    for sample in samples
                )
            ):
                errors.append(f"{label} round {index} {path} lacks raw latencies.")
                continue
            ordered = sorted(samples)
            if (p50, p95, maximum) != (
                ordered[(len(ordered) + 1) // 2 - 1],
                ordered[(95 * len(ordered) + 99) // 100 - 1],
                ordered[-1],
            ):
                errors.append(
                    f"{label} round {index} {path} latency summaries "
                    "differ from samples."
                )
        samples = run.get("latencies_seconds")
        path_samples = [
            metric.get("latencies_seconds")
            for metric in metrics.values()
            if isinstance(metric, dict)
        ]
        if (
            isinstance(samples, list)
            and len(path_samples) == len(MEASURED_PATHS)
            and all(isinstance(items, list) for items in path_samples)
            and all(type(item) in (int, float) for item in samples)
            and all(
                type(item) in (int, float) for items in path_samples for item in items
            )
            and sorted(samples)
            != sorted(item for items in path_samples for item in items)
        ):
            errors.append(
                f"{label} round {index} path latencies do not cover all GETs."
            )
        query_errors, statement_count, write_count = _query_evidence_errors(
            run.get("observed_query_shapes"),
            run.get("endpoint_shape_hashes"),
            run.get("query_attribution_groups"),
            None,
            require_plans=False,
        )
        errors.extend(f"{label} round {index}: {error}" for error in query_errors)
        if (
            type(run.get("sql_statement_count")) is not int
            or run["sql_statement_count"] <= 0
            or run["sql_statement_count"] != statement_count
        ):
            errors.append(
                f"{label} round {index} SQL statement counts are inconsistent."
            )
        if (
            type(run.get("sql_write_count")) is not int
            or run["sql_write_count"] != write_count
            or write_count != 0
        ):
            errors.append(
                f"{label} round {index} public write SQL was observed or misreported."
            )
        shapes = run.get("observed_query_shapes")
        if not query_errors and isinstance(shapes, dict):
            for path in MEASURED_PATHS:
                count = sum(
                    shape["endpoint_observations"].get(path, {}).get("count", 0)
                    for shape in shapes.values()
                )
                if count < PERFORMANCE_CONCURRENCY // 4:
                    errors.append(
                        f"{label} round {index} {path} lacks GET SQL coverage."
                    )
    return errors


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
                "research_concurrent_load",
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
    errors.extend(_research_load_errors(value.get("research_concurrent_load")))
    research = value.get("research_concurrent_load")
    if isinstance(research, dict):
        baseline = value.get("application_state_after")
        excluded = {"research_backtest_cache", "research_backtest_runs"}
        for phase in ("before", "after"):
            concurrent = research.get(f"application_state_{phase}")
            if (
                not isinstance(baseline, dict)
                or not isinstance(concurrent, dict)
                or set(concurrent) != set(baseline)
                or {
                    key: item for key, item in concurrent.items() if key not in excluded
                }
                != {key: item for key, item in baseline.items() if key not in excluded}
            ):
                errors.append(
                    "Concurrent research stable state does not match "
                    "the full public baseline."
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
                or run.get("cache_condition") != PERFORMANCE_RUN_CACHE_CONDITION
            ):
                errors.append(
                    f"Performance run {index} has an unsupported cold/cache claim."
                )
            if (
                run.get("request_count") != PERFORMANCE_CONCURRENCY
                or run.get("concurrency") != PERFORMANCE_CONCURRENCY
                or run.get("successful") != PERFORMANCE_CONCURRENCY
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
        or environment.get("cache_claim") != PERFORMANCE_CACHE_CLAIM
    ):
        errors.append("Performance environment makes an unsupported cache claim.")
    if not isinstance(environment, dict) or environment.get("gc_fenced") is not True:
        errors.append(
            "Performance environment does not fence cyclic GC during measurement."
        )
    warmup = value.get("warmup")
    warmup_valid = False
    if isinstance(warmup, dict):
        wave_count = warmup.get("wave_count")
        concurrency = warmup.get("concurrency")
        warmup_requests = warmup.get("request_count")
        warmup_successful = warmup.get("successful")
        warmup_valid = (
            set(warmup.get("paths", [])) == MEASURED_PATHS
            and wave_count == PERFORMANCE_WARMUP_WAVES
            and concurrency == PERFORMANCE_CONCURRENCY
            and type(warmup_requests) is int
            and warmup_requests == wave_count * concurrency
            and warmup_successful == warmup_requests
        )
    if not warmup_valid:
        errors.append(
            "Performance warmup must run three 20-concurrent waves covering "
            "every measured path, all successful."
        )
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
        "research_concurrent_load": value["research_concurrent_load"],
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


SOURCE_EXCLUSIONS = frozenset(
    {
        ".git",
        ".temp",
        ".venv",
        "node_modules",
        "dist",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "coverage",
        "test-results",
        "playwright-report",
        ".coverage",
    }
)


def _source_inventory() -> list[dict[str, Any]]:
    files = []
    for directory, names, filenames in os.walk(ROOT, followlinks=False):
        names[:] = [name for name in names if name not in SOURCE_EXCLUSIONS]
        for name in [*names, *filenames]:
            path = Path(directory) / name
            if name in SOURCE_EXCLUSIONS:
                continue
            if path.is_symlink():
                raise ValueError("Linked source inputs are forbidden.")
            if path.is_file():
                data = path.read_bytes()
                files.append(
                    {
                        "path": path.relative_to(ROOT).as_posix(),
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
    return sorted(files, key=lambda item: item["path"])


def _contract_parent(gate: str) -> Path:
    if gate == "frontend_five_builds":
        return ROOT / ".temp" / "five-builds"
    if gate == "frontend_contract_c2":
        return ROOT.parent
    evidence_parent = ROOT.parent
    if evidence_parent.name != ".temp":
        evidence_parent = evidence_parent / ".temp"
    return evidence_parent / "reset-contract-runtime"


def _contract_runs(parent: Path) -> set[str]:
    return (
        {str(path) for path in parent.iterdir() if path.is_dir()}
        if parent.is_dir()
        else set()
    )


def _new_contract_run(parent: Path, before: set[str]) -> Path:
    added = _contract_runs(parent) - before
    if len(added) != 1:
        raise ValueError("A unique new native contract run is required.")
    path = Path(added.pop())
    if path.is_symlink() or path.resolve().parent != parent.resolve():
        raise ValueError("Linked native run is forbidden.")
    return path


def _json_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _safe_relative(path: Any) -> bool:
    return (
        isinstance(path, str)
        and bool(path)
        and "\\" not in path
        and ":" not in path
        and not path.startswith("/")
        and all(part not in {"", ".", ".."} for part in path.split("/"))
    )


def _file_bindings(
    files: Any,
    content: Mapping[str, bytes],
    *,
    prefix: str = "",
    size_key: str = "size",
) -> list[str]:
    if not isinstance(files, list):
        return ["Native file inventory is missing."]
    errors = []
    seen = set()
    for item in files:
        if _exact_fields(item, {"path", size_key, "sha256"}, "Native file"):
            errors.append("Native file schema is invalid.")
            continue
        path = item["path"]
        if not _safe_relative(path) or path in seen:
            errors.append("Native file path is unsafe or duplicated.")
            continue
        seen.add(path)
        data = content.get(prefix + path)
        if (
            not isinstance(data, bytes)
            or type(item[size_key]) is not int
            or len(data) != item[size_key]
            or hashlib.sha256(data).hexdigest() != item["sha256"]
        ):
            errors.append("Native file size/SHA256 binding differs.")
    return errors


def _native_contract_counts(commands: Any) -> dict[str, int]:
    result = {"passed": 0, "failed": 0, "skipped": 0, "output_lines": 0}
    if not isinstance(commands, list):
        return result
    # Each subprocess has its own native summary; wrapper JSON/exit zero is not a test.
    for command in commands:
        if not isinstance(command, dict):
            continue
        text = ANSI_ESCAPE.sub(
            "", str(command.get("stdout", "")) + str(command.get("stderr", ""))
        )
        counts = {"passed": 0, "failed": 0, "skipped": 0}
        for count, kind in SUMMARY_COUNT.findall(text):
            counts[kind] = max(counts[kind], int(count))
        for kind, count in TAP_COUNT.findall(text):
            key = {"pass": "passed", "fail": "failed", "skipped": "skipped"}[kind]
            counts[key] = max(counts[key], int(count))
        for key, count in counts.items():
            result[key] += count
        result["output_lines"] += len(text.splitlines())
    return result


def _native_commands(gate: str, files: Mapping[str, bytes]) -> list[dict[str, Any]]:
    if gate == "frontend_contract_c2":
        return json.loads(files["commands.json"])
    if gate == "frontend_contract_c3":
        return [
            json.loads(line)
            for line in files["commands.jsonl"].decode().splitlines()
            if line.strip()
        ]
    return []


def _node_contract_check(
    gate: str, raw: dict[str, Any], files: Mapping[str, bytes]
) -> list[str]:
    # Reuse native matrix/static-closure validators without builds or browsers.
    with tempfile.TemporaryDirectory(prefix="c5-native-") as temporary:
        directory = Path(temporary)
        if gate == "frontend_five_builds":
            for path, data in files.items():
                if not _safe_relative(path):
                    return ["Unsafe native archive path."]
                target = directory / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            module = (ROOT / "web/scripts/check-default-bundle.mjs").as_uri()
            program = (
                f"import {{inspectBundle}} from {json.dumps(module)};"
                "import fs from 'node:fs'; import assert from 'node:assert/strict';"
                "const x=JSON.parse(fs.readFileSync(0,'utf8'));"
                "for(const b of x.raw.builds) assert.deepEqual("
                "inspectBundle({dist:x.dir+'/'+b.name+'/dist',env:b.flags,requireManifest:true}),b.bundle);"
            )
        else:
            module = (ROOT / "web/scripts/contract-c2-support.mjs").as_uri()
            program = (
                f"import {{assertRunEvidence}} from {json.dumps(module)};"
                "import fs from 'node:fs';"
                "const x=JSON.parse(fs.readFileSync(0,'utf8'));"
                "const readBytes=p=>Buffer.from(x.files[p],'base64');"
                "const load=p=>JSON.parse(readBytes(p));"
                "assertRunEvidence({source:load('source.json'),builds:load('builds.json'),"
                "tools:load('tools.json'),cells:load('matrix.json'),browser:load('zoom-smoke.json').browser,"
                "lockPackages:load('web/package-lock.json').packages,readBytes});"
            )
        payload = {
            "raw": raw,
            "dir": str(directory),
            "files": {
                path: base64.b64encode(data).decode() for path, data in files.items()
            },
        }
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", program],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            cwd=ROOT,
            timeout=60,
            check=False,
            env={
                key: value
                for key, value in os.environ.items()
                if key.upper()
                in {
                    "PATH",
                    "SYSTEMROOT",
                    "WINDIR",
                    "TEMP",
                    "TMP",
                    "HOME",
                    "USERPROFILE",
                }
            },
        )
        return (
            []
            if completed.returncode == 0
            else ["Native matrix/bundle validation failed."]
        )


def _native_contract_errors(
    gate: str, raw: Any, files: Mapping[str, bytes], source: list[dict[str, Any]]
) -> list[str]:
    try:
        return _check_native_contract(gate, raw, files, source)
    except (
        KeyError,
        IndexError,
        StopIteration,
        TypeError,
        ValueError,
        AttributeError,
        OSError,
        subprocess.SubprocessError,
    ):
        return ["Native contract archive is incomplete or malformed."]


def _check_native_contract(
    gate: str, raw: Any, files: Mapping[str, bytes], source: list[dict[str, Any]]
) -> list[str]:
    errors: list[str] = []
    if gate == "frontend_five_builds":
        errors.extend(
            _exact_fields(
                raw,
                {
                    "schema",
                    "status",
                    "runRoot",
                    "root",
                    "startedAt",
                    "finishedAt",
                    "source",
                    "builds",
                },
                "Five-build archive",
            )
        )
        if errors:
            return errors
        if (
            raw["schema"] != "contract-c4-five-builds-v1"
            or raw["status"] != "passed"
            or Path(raw["root"]).resolve() != ROOT
        ):
            errors.append("Five-build identity/status is invalid.")
        binding = raw["source"]
        errors.extend(
            _exact_fields(
                binding, {"algorithm", "sha256", "files"}, "Five-build source"
            )
        )
        if binding["algorithm"] != "sha256" or binding["sha256"] != _json_digest(
            binding["files"]
        ):
            errors.append("Five-build source digest is invalid.")
        if sorted(binding["files"], key=lambda item: item["path"]) != source:
            errors.append("Five-build inputs differ from captured source.")
        expected = [
            ("default", "0000"),
            ("sharing", "0100"),
            ("reset", "1000"),
            ("private", "0010"),
            ("owner", "1011"),
        ]
        if [b["name"] for b in raw["builds"]] != [name for name, _ in expected]:
            return [*errors, "Exactly five independent combinations are required."]
        for build, (name, bits) in zip(raw["builds"], expected, strict=True):
            errors.extend(
                _exact_fields(
                    build,
                    {
                        "name",
                        "flags",
                        "status",
                        "commands",
                        "source",
                        "files",
                        "bundle",
                        "manifestSha256",
                    },
                    "Five-build combination",
                )
            )
            flags = dict(
                zip(
                    [
                        "VITE_RESET_WORKBENCH",
                        "VITE_PUBLIC_SHARING",
                        "VITE_PRIVATE_REVIEW",
                        "VITE_PRIVATE_AI",
                    ],
                    bits,
                    strict=True,
                )
            )
            if (
                build["flags"] != flags
                or build["source"] != binding
                or build["status"] != "passed"
            ):
                errors.append("Five-build flags/source/status differs.")
            inventory = build["files"]
            errors.extend(_file_bindings(inventory, files, prefix=f"{name}/dist/"))
            if {f"{name}/dist/{item['path']}" for item in inventory} != {
                path for path in files if path.startswith(f"{name}/dist/")
            }:
                errors.append("Five-build archive inventory is incomplete.")
            manifest = next(
                item for item in inventory if item["path"] == ".vite/manifest.json"
            )
            if manifest["sha256"] != build["manifestSha256"]:
                errors.append("Five-build manifest digest differs.")
            if len(build["commands"]) != 2:
                errors.append("Five-build dual command evidence is incomplete.")
                continue
            for command, stage, tail in zip(
                build["commands"],
                ["build", "check"],
                [["run", "build", "--", "--manifest"], ["run", "check:bundle"]],
                strict=True,
            ):
                errors.extend(
                    _exact_fields(
                        command,
                        {
                            "stage",
                            "command",
                            "args",
                            "exitCode",
                            "signal",
                            "durationMs",
                            "stdout",
                            "stderr",
                        },
                        "Five-build command",
                    )
                )
                args = command["args"]
                if (
                    command["stage"] != stage
                    or Path(command["command"]).name not in {"node", "node.exe"}
                    or len(args) != len(tail) + 1
                    or Path(args[0]).name != "npm-cli.js"
                    or args[1:] != tail
                    or type(command["exitCode"]) is not int
                    or command["exitCode"] != 0
                    or command["signal"] is not None
                    or type(command["durationMs"]) not in (int, float)
                    or not 0 <= command["durationMs"] <= 600000
                ):
                    errors.append("Five-build command/budget is invalid.")
                for stream in ("stdout", "stderr"):
                    log = command[stream]
                    if log["path"] != f"{name}/{stage}.{stream}.log":
                        errors.append("Five-build log path differs.")
                    errors.extend(_file_bindings([log], files))
        if not errors:
            errors.extend(_node_contract_check(gate, raw, files))
    elif gate == "frontend_contract_c2":
        errors.extend(
            _exact_fields(
                raw,
                {
                    "schema",
                    "runId",
                    "argv",
                    "startedAt",
                    "finishedAt",
                    "scope",
                    "cells",
                    "fullMatrix",
                    "outcome",
                    "cleanup",
                },
                "C2 result",
            )
        )
        if errors:
            return errors
        if (
            raw["schema"] != "contract-c5-native-c2-v1"
            or raw["argv"] != []
            or raw["fullMatrix"] is not True
            or type(raw["cells"]) is not int
            or raw["cells"] != 320
            or raw["scope"] != "C2 matrix and lifecycle (mocked HTTP, not C1 TLS or C4)"
            or raw["outcome"] != "passed"
        ):
            errors.append("C2 full matrix invocation/status is invalid.")
        cleanup = json.loads(files["cleanup.json"])
        errors.extend(
            _exact_fields(cleanup, {"runId", "id", "containerRemoved"}, "C2 cleanup")
        )
        if (
            cleanup != raw["cleanup"]
            or cleanup["runId"] != raw["runId"]
            or cleanup["containerRemoved"] is not True
            or not re.fullmatch("[a-f0-9]{64}", cleanup["id"])
        ):
            errors.append("C2 owned container cleanup is invalid.")
        matrix = json.loads(files["matrix-manifest.json"])
        errors.extend(
            _exact_fields(
                matrix,
                {
                    "runId",
                    "sourceSha256",
                    "buildsSha256",
                    "matrixSha256",
                    "source",
                    "builds",
                    "matrix",
                },
                "C2 matrix manifest",
            )
        )
        if matrix["runId"] != raw["runId"]:
            errors.append("C2 matrix run identity differs.")
        for key in ("source", "builds", "matrix"):
            if (
                matrix[key] != key + ".json"
                or hashlib.sha256(files[key + ".json"]).hexdigest()
                != matrix[key + "Sha256"]
            ):
                errors.append("C2 matrix manifest binding differs.")
        inputs = json.loads(files["source.json"])
        if sorted(inputs, key=lambda item: item["path"]) != source:
            errors.append("C2 input source inventory differs.")
        errors.extend(_file_bindings(inputs, files))
        for build in json.loads(files["builds.json"]):
            errors.extend(
                _exact_fields(
                    build,
                    {
                        "variant",
                        "flags",
                        "assets",
                        "assetsSha256",
                        "sourceSha256",
                        "lockSha256",
                        "tools",
                    },
                    "C2 build",
                )
            )
            errors.extend(_file_bindings(build["assets"], files))
            if any(
                not item["path"].startswith(f"builds/{build['variant']}/")
                for item in build["assets"]
            ):
                errors.append("C2 assets are not from the current run.")
        if "build-reuse.json" in files:
            errors.append("C2 build reuse cannot satisfy a fresh full run.")
        if (
            json.loads(files["zoom-smoke.json"])["outcome"] != "passed"
            or json.loads(files["public-review-boundary.json"])["outcome"] != "passed"
        ):
            errors.append("C2 zoom/public boundary evidence failed.")
        smoke = json.loads(files["zoom-smoke.json"])
        if (
            smoke["zoom"]["requested"] != 2
            or smoke["zoom"]["actual"] != 2
            or type(smoke["zoom"]["tabId"]) is not int
            or smoke["zoom"]["tabId"] <= 0
            or smoke["native"]["innerWidth"] != 768
            or smoke["changed"]["innerWidth"] != 384
            or smoke["changed"]["innerHeight"] != 512
            or smoke["native"]["outerWidth"] != smoke["changed"]["outerWidth"]
            or smoke["cleanup"]
            != {"restored": 1, "closed": True, "profileRemoved": True}
        ):
            errors.append("C2 actual zoom smoke/cleanup proof is incomplete.")
        if not errors:
            errors.extend(_node_contract_check(gate, raw, files))
    elif gate == "frontend_contract_c3":
        errors.extend(
            _exact_fields(
                raw,
                {
                    "schema",
                    "argv",
                    "startedAt",
                    "finishedAt",
                    "runId",
                    "project",
                    "status",
                    "scope",
                    "database",
                    "source",
                    "browserMode",
                    "tools",
                    "inputs",
                    "cleanup",
                    "secretsRemoved",
                    "permissions",
                    "environment",
                    "result",
                },
                "C3 fixture manifest",
            )
        )
        if errors:
            return errors
        if (
            raw["schema"] != "contract-c5-native-c3-v1"
            or raw["argv"] != ["--c3"]
            or raw["status"] != "passed"
            or raw["browserMode"] != "browser"
            or raw["database"] != {"name": "dragon_test", "host": "db"}
            or raw["cleanup"]["status"] != "removed"
            or raw["secretsRemoved"] is not True
        ):
            errors.append("C3 invocation/database/cleanup is invalid.")
        errors.extend(
            _exact_fields(raw["cleanup"], {"status", "before", "after"}, "C3 cleanup")
        )
        for phase in ("before", "after"):
            resources = raw["cleanup"][phase]
            errors.extend(
                _exact_fields(
                    resources,
                    {"containers", "volumes", "networks"},
                    "C3 cleanup resources",
                )
            )
            if any(not isinstance(ids, list) for ids in resources.values()):
                errors.append("C3 cleanup resource inventory is invalid.")
        if any(raw["cleanup"]["after"].values()):
            errors.append("C3 owned resources remain after cleanup.")
        binding = raw["source"]
        errors.extend(
            _exact_fields(
                binding, {"head", "files", "dirtyRawDigest", "lockfiles"}, "C3 source"
            )
        )
        normalized = [
            {"path": item["path"], "size": item["bytes"], "sha256": item["sha256"]}
            for item in binding["files"]
        ]
        if sorted(normalized, key=lambda item: item["path"]) != source or binding[
            "dirtyRawDigest"
        ] != _json_digest(binding["files"]):
            errors.append("C3 input source binding differs.")
        errors.extend(_file_bindings(binding["files"], files, size_key="bytes"))
        errors.extend(_file_bindings(binding["lockfiles"], files, size_key="bytes"))
        result = raw["result"]
        errors.extend(
            _exact_fields(
                result, {"c1", "ownerTransport", "project", "public"}, "C3 result"
            )
        )
        proof = result["c1"]
        errors.extend(
            _exact_fields(
                proof,
                {"scope", "ai", "realtime", "ownerRealtime", "database", "browser"},
                "C3 proof",
            )
        )
        for key, path in [
            ("ai", "c3-ai.json"),
            ("realtime", "c3-realtime-inputs.json"),
            ("ownerRealtime", "c3-owner-realtime.json"),
            ("database", "c3-database.json"),
            ("browser", "c3-browser/evidence.json"),
        ]:
            if proof[key] != json.loads(files[path]):
                errors.append("C3 result differs from its retained native object.")
        http = proof["ai"]["actual_http"]
        if (
            http["requests"] != 6
            or http["peak"] != 2
            or http["active"] != 0
            or not re.fullmatch(r"http://127\.0\.0\.1:\d+/.+", http["approved_target"])
            or len(http["exact_results"]) != 2
        ):
            errors.append("C3 actual approved HTTP provider proof is incomplete.")
        if (
            http["forbidden"] != 0
            or http["rendezvous_timeouts"] != 0
            or len(http["receipts"]) != 6
        ):
            errors.append("C3 provider admission/concurrency receipts differ.")
        for item in http["exact_results"]:
            if (
                item["status"] != "succeeded"
                or item["verification_status"] != "passed"
                or item["human_review_status"] != "unreviewed"
                or item["token_usage"] != 10
                or item["decimal_cost"] != "0.1"
                or not all(
                    item[key]
                    for key in (
                        "task_id",
                        "snapshot_id",
                        "version",
                        "provider",
                        "model_version",
                        "prompt_version",
                    )
                )
            ):
                errors.append("C3 actual provider exact result/budget differs.")
        if len(proof["ai"]["results"]) != 7 or {
            item["case"] for item in proof["ai"]["results"]
        } != {"token-over", "cost-over", "error", "redirect", "forbidden-target"}:
            errors.append("C3 provider failure/redirect cases are incomplete.")
        for item in proof["ai"]["results"]:
            if item["case"] == "forbidden-target":
                if item["status"] != "failed":
                    errors.append("C3 forbidden provider target was admitted.")
                continue
            if (
                item["status"] != "failed"
                or item["failure"]
                != (
                    "result_limit_exceeded"
                    if item["case"].endswith("over")
                    else "provider_failed"
                )
                or len(item["requests"]) != 1
            ):
                errors.append("C3 provider denial receipt differs.")
        realtime = proof["realtime"]
        if (
            len(realtime["event_ids"]) != 111
            or len(set(realtime["event_ids"])) != 111
            or not all(
                realtime[key]
                for key in ("source", "quote", "minute", "flow", "pit", "gates")
            )
            or realtime["duplicate_anomaly_replay"]["unique_events_after_replay"] != 111
            or realtime["duplicate_anomaly_replay"]["same_payload_hash"] is not True
        ):
            errors.append("C3 actual realtime storage proof is incomplete.")
        browser = proof["browser"]
        if (
            browser["synthetic"] is not True
            or browser["actualContainerGateway"] is not True
            or browser["noRouteMocks"] is not True
            or browser["uniqueAnomalies"] != 111
            or not browser["responses"]
        ):
            errors.append("C3 actual gateway browser proof is incomplete.")
        if proof["database"]["database"] != "dragon_test":
            errors.append("C3 actual database proof differs.")
        tools = raw["tools"]
        if (
            not isinstance(tools["docker"], dict)
            or not all(tools["docker"].values())
            or not tools["compose"]
            or tools["chromium"]["status"] != "recorded"
            or tools["chromium"]["version"] != browser["chromium"]
        ):
            errors.append("C3 native tool/browser identities are incomplete.")
        owner = proof["ownerRealtime"]
        errors.extend(
            _exact_fields(
                owner,
                {
                    "authority",
                    "connectPort",
                    "caSha256",
                    "requests",
                    "resources",
                    "caddySha256",
                    "session",
                    "status",
                },
                "C3 owner proof",
            )
        )
        if (
            owner["status"] != "passed"
            or owner["authority"] != "https://localhost:8443"
            or {item["service"] for item in owner["resources"]}
            != {"owner-api", "owner-caddy"}
            or len(owner["resources"]) != 2
            or len(owner["requests"]) != 20
            or not re.fullmatch("[a-f0-9]{64}", owner["caSha256"])
            or not re.fullmatch("[a-f0-9]{64}", owner["caddySha256"])
        ):
            errors.append("C3 independent owner TLS/resources are incomplete.")
        for item in owner["resources"]:
            errors.extend(
                _exact_fields(
                    item, {"container", "image", "service"}, "C3 owner resource"
                )
            )
            if not re.fullmatch("[a-f0-9]{64}", item["container"]) or not re.fullmatch(
                "sha256:[a-f0-9]{64}", item["image"]
            ):
                errors.append("C3 owner resource identities are invalid.")
        for path in [
            "/api/realtime/quotes",
            "/api/realtime/minutes",
            "/api/realtime/anomalies",
            "/api/market/sector-flow/intraday",
        ]:
            receipts = [
                item for item in owner["requests"] if item["path"].split("?")[0] == path
            ]
            if (
                {(item["method"], item["status"]) for item in receipts}
                != {("GET", 200), ("POST", 404), ("HEAD", 404)}
                or len(receipts) != 3
                or any(
                    not item["paired"] or item["host"] != "localhost:8443"
                    for item in receipts
                )
                or any(item["body"] != "" for item in receipts if item["status"] == 404)
                or json.loads(
                    next(item["body"] for item in receipts if item["method"] == "GET")
                )["data"]
                is None
                or not any(
                    item["path"].split("?")[0] == path + "/extra"
                    and item["status"] == 404
                    and item["body"] == ""
                    for item in owner["requests"]
                )
            ):
                errors.append(
                    "C3 exact owner GET-only realtime receipts are incomplete."
                )
        if [(item["method"], item["status"]) for item in owner["requests"][-2:]] != [
            ("DELETE", 200),
            ("GET", 401),
        ]:
            errors.append("C3 independent owner session cleanup is missing.")
    else:
        return ["Unknown native contract gate."]
    for key in ("startedAt", "finishedAt"):
        stamp = datetime.fromisoformat(raw[key].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            errors.append("Native run timestamp lacks timezone.")
    if datetime.fromisoformat(raw["finishedAt"]) < datetime.fromisoformat(
        raw["startedAt"]
    ):
        errors.append("Native run timestamps are reversed.")
    if gate != "frontend_five_builds":
        commands = _native_commands(gate, files)
        if not isinstance(commands, list) or not commands:
            errors.append("Native subprocess logs are missing.")
        else:
            for command in commands:
                errors.extend(
                    _exact_fields(
                        command,
                        {
                            "command",
                            "args",
                            "status",
                            "durationMs",
                            "stdout",
                            "stderr",
                        },
                        "Native subprocess",
                    )
                )
                if (
                    not isinstance(command.get("stdout"), str)
                    or not isinstance(command.get("stderr"), str)
                    or type(command.get("status")) is not int
                    or command["status"] != 0
                    or type(command.get("durationMs")) not in (int, float)
                    or command["durationMs"] < 0
                    or not isinstance(command.get("command"), str)
                    or not isinstance(command.get("args"), list)
                ):
                    errors.append("Native dual stream command evidence failed.")
                if any(
                    arg in {"--grep", "--smoke", "--host-native", "--builds-from"}
                    for arg in command.get("args", [])
                ):
                    errors.append("Native partial execution is forbidden.")
            tests = [
                command
                for command in commands
                if "test" in command.get("args", [])
                and (
                    "playwright.contract-c2.config.ts"
                    if gate.endswith("c2")
                    else "playwright.contract-c3.config.ts"
                )
                in command["args"]
            ]
            counts = _native_contract_counts(tests)
            if counts["passed"] <= 0 or counts["failed"] or counts["skipped"]:
                errors.append(
                    "Native browser test counts are absent or failed/skipped."
                )
    return errors


def _collect_contract_observations(
    gate: str,
    before: set[str],
    source: list[dict[str, Any]],
    evidence_dir: Path,
    *,
    commands: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    observation = {
        "run_path": "",
        "raw_artifact": None,
        "files": {},
        "source_before": source,
        "source_after": [],
        "source_artifacts": {},
        "observed_counts": _native_contract_counts([]),
        "execution_window": (
            {
                "started_at": commands[0]["started_at"],
                "ended_at": commands[-1]["ended_at"],
            }
            if commands
            else {}
        ),
        "native_logs": [],
        "errors": [],
    }
    try:
        run = _new_contract_run(_contract_parent(gate), before)
        observation["run_path"] = str(run)
        raw_name = {
            "frontend_five_builds": "run.json",
            "frontend_contract_c2": "result.json",
            "frontend_contract_c3": "manifest.json",
        }[gate]
        raw_path = run / raw_name
        raw = json.loads(raw_path.read_bytes())
        observation["raw_artifact"] = _snapshot_artifact(
            raw_path, evidence_dir / gate / "raw"
        )
        content = {}
        for path in run.rglob("*"):
            if path.is_symlink():
                raise ValueError("Linked native archive is forbidden.")
            if not path.is_file():
                continue
            relative = path.relative_to(run).as_posix()
            # Admit public evidence/assets, never env/CA/session secrets.
            if (
                "/dist/" not in relative
                and not relative.startswith("builds/")
                and path.suffix not in {".json", ".jsonl", ".log"}
            ):
                continue
            if any(
                part in {"home", "npm-cache", "secrets"}
                for part in path.relative_to(run).parts
            ):
                continue
            content[relative] = path.read_bytes()
            observation["files"][relative] = _snapshot_artifact(
                path, evidence_dir / gate / "files" / path.relative_to(run).parent
            )
        observation["source_after"] = _source_inventory()
        for item in source:
            path = ROOT / item["path"]
            content[item["path"]] = path.read_bytes()
            observation["source_artifacts"][item["path"]] = _snapshot_artifact(
                path, evidence_dir / gate / "source" / Path(item["path"]).parent
            )
        if observation["source_after"] != source:
            observation["errors"].append("Source inputs changed during the native run.")
        if gate == "frontend_five_builds":
            if Path(raw["runRoot"]).resolve() != run.resolve():
                observation["errors"].append("Five-build native run identity differs.")
        elif raw["runId"] != run.name:
            observation["errors"].append("Native run identity differs.")
        observation["errors"].extend(
            _native_contract_errors(gate, raw, content, source)
        )
        native_commands = _native_commands(gate, content)
        observation["observed_counts"] = _native_contract_counts(native_commands)
        for index, command in enumerate(native_commands):
            logs = {}
            for stream in ("stdout", "stderr"):
                path = evidence_dir / gate / "native-logs" / f"{index}.{stream}.log"
                _atomic_write(path, command[stream].encode())
                logs[stream] = _artifact(path)
            observation["native_logs"].append(logs)
    except (KeyError, TypeError, ValueError, OSError):
        observation["errors"].append(
            "A unique new complete native run could not be collected."
        )
    return observation


def _contract_observation_errors(gate: str, observations: Any) -> list[str]:
    errors = _exact_fields(
        observations,
        {
            "run_path",
            "raw_artifact",
            "files",
            "source_before",
            "source_after",
            "source_artifacts",
            "observed_counts",
            "execution_window",
            "native_logs",
            "errors",
        },
        "Native contract observations",
    )
    if errors:
        return errors
    if observations["errors"] != []:
        return ["Native run collection failed."]
    try:
        data, failures = _artifact_value(
            observations["raw_artifact"], "Native raw artifact"
        )
        errors.extend(failures)
        raw = json.loads(data)
        content = {}
        for group in ("files", "source_artifacts"):
            if not isinstance(observations[group], dict):
                raise ValueError
            for path, artifact in observations[group].items():
                if not _safe_relative(path) or path in content:
                    raise ValueError
                data, failures = _artifact_value(artifact, "Native archived file")
                errors.extend(failures)
                content[path] = data
        source = observations["source_before"]
        if source != observations["source_after"]:
            errors.append("Native source inputs changed.")
        errors.extend(_file_bindings(source, content))
        if {item["path"] for item in source} != set(observations["source_artifacts"]):
            errors.append("Native input snapshot is incomplete.")
        raw_name = {
            "frontend_five_builds": "run.json",
            "frontend_contract_c2": "result.json",
            "frontend_contract_c3": "manifest.json",
        }[gate]
        if json.loads(content[raw_name]) != raw:
            errors.append("Native primary artifact differs from the archive.")
        run = Path(observations["run_path"])
        if gate == "frontend_five_builds":
            identity = Path(raw["runRoot"]).resolve() == run.resolve()
        else:
            identity = raw["runId"] == run.name
        if not identity or run.parent.resolve() != _contract_parent(gate).resolve():
            errors.append("Native run identity/path differs.")
        window = observations["execution_window"]
        errors.extend(
            _exact_fields(window, {"started_at", "ended_at"}, "Native execution window")
        )
        start = datetime.fromisoformat(window["started_at"])
        end = datetime.fromisoformat(window["ended_at"])
        native_start = datetime.fromisoformat(raw["startedAt"])
        native_end = datetime.fromisoformat(raw["finishedAt"])
        if not (
            start.tzinfo
            and end.tzinfo
            and native_start.tzinfo
            and native_end.tzinfo
            and start <= native_start <= native_end <= end
        ):
            errors.append(
                "Native archive is not bound to this command execution window."
            )
        errors.extend(_native_contract_errors(gate, raw, content, source))
        native_commands = _native_commands(gate, content)
        if observations["observed_counts"] != _native_contract_counts(native_commands):
            errors.append("Native observed counts differ from archived logs.")
        logs = observations["native_logs"]
        if not isinstance(logs, list) or len(logs) != len(native_commands):
            errors.append("Native dual stream log inventory is incomplete.")
        else:
            for command, log in zip(native_commands, logs, strict=True):
                errors.extend(
                    _exact_fields(log, {"stdout", "stderr"}, "Native dual stream logs")
                )
                for stream in ("stdout", "stderr"):
                    data, failures = _artifact_value(
                        log[stream], "Native subprocess stream"
                    )
                    errors.extend(failures)
                    if data != command[stream].encode():
                        errors.append(
                            "Native subprocess stream differs from its command archive."
                        )
    except (KeyError, TypeError, ValueError, OSError):
        errors.append("Native observations are incomplete or malformed.")
    return errors


def _execute_gates(
    mode: str,
    evidence_dir: Path,
    runner: Runner,
    *,
    isolation: PrivateStackIsolation | None = None,
) -> list[dict[str, Any]]:
    selected = set(REQUIRED_GATES) if mode == "full" else FOCUSED_GATES
    records: list[dict[str, Any]] = []
    performance_commands: list[dict[str, Any]] | None = None
    performance_value: dict[str, Any] | None = None
    performance_errors: list[str] = []
    for gate in REQUIRED_GATES:
        if gate not in selected:
            continue
        native_before = (
            _contract_runs(_contract_parent(gate)) if gate in CONTRACT_GATES else set()
        )
        native_source = _source_inventory() if gate in CONTRACT_GATES else []
        if gate in {"public_no_write", "async_over_five_seconds"}:
            if performance_commands is None:
                continue
            commands = performance_commands
        else:
            commands = []
            for index, spec in enumerate(GATE_SPECS[gate], start=1):
                if isolation is not None and spec.get("suspend_private_stack", False):
                    # Deterministic resource window: suspend the residual
                    # private stack before the command and always resume
                    # afterwards, even on runner failure, so later private
                    # gates keep their gateway. Commands without the marker
                    # (e.g. the live TLS gateway checks) run stack-up.
                    isolation.suspend()
                    try:
                        command = _run_command(gate, index, spec, evidence_dir, runner)
                    finally:
                        isolation.resume()
                else:
                    command = _run_command(gate, index, spec, evidence_dir, runner)
                commands.append(command)
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
        elif gate in CONTRACT_GATES:
            record["observations"] = _collect_contract_observations(
                gate,
                native_before,
                native_source,
                evidence_dir,
                commands=commands,
            )
        records.append(record)
    return records


_AUTO_ISOLATION = object()


def _run_gates(
    mode: str,
    evidence_dir: Path,
    runner: Runner | None = None,
    *,
    isolation: Any = _AUTO_ISOLATION,
) -> ExecutionEvidence:
    repository_before = _repository_state()
    preflight_errors = _repository_errors(repository_before, repository_before)
    if preflight_errors:
        raise RuntimeError("Repository preflight failed: " + " ".join(preflight_errors))
    environment = _environment()
    if isolation is _AUTO_ISOLATION:
        isolation = _performance_isolation(os.environ)
    records = _execute_gates(
        mode,
        evidence_dir,
        runner or SubprocessRunner(),
        isolation=isolation,
    )
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
    native_wrapper = any(spec in GATE_SPECS[gate] for gate in CONTRACT_GATES)
    no_test_counts = native_wrapper or any(
        spec in GATE_SPECS[gate] for gate in BUILD_GATES
    )
    if native_wrapper and required == legacy_required:
        errors.append(
            "Native contract wrappers require captured dual stream Runner evidence."
        )
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
            output = content.decode("utf-8")
            derived_counts = (
                {
                    "passed": 0,
                    "failed": 0,
                    "skipped": 0,
                    "output_lines": len(output.splitlines()),
                }
                if no_test_counts
                else _counts(output, value["exit_status"])
            )
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
            "research_concurrent_load",
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
        for field in (
            "warmup",
            "runs",
            "aggregate",
            "fixture",
            "environment",
            "research_concurrent_load",
        ):
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
    if gate in CONTRACT_GATES:
        return _contract_observation_errors(gate, observations)
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
        if gate in CONTRACT_GATES and isinstance(commands, list) and commands:
            observations = source.get("observations")
            if isinstance(observations, dict) and observations.get(
                "execution_window"
            ) != {
                "started_at": commands[0].get("started_at"),
                "ended_at": commands[-1].get("ended_at"),
            }:
                errors.append(
                    "Native execution window differs from the captured gate command."
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
    from dragon_radar.production.cli import add_arguments, evaluate_arguments
    from dragon_radar.production.report import attach_external

    parser = argparse.ArgumentParser()
    parser.add_argument("--production-evidence", action="store_true")
    add_arguments(parser)
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
    if args.production_evidence:
        attach_external(report, evaluate_arguments(args, code_root=ROOT))
    rendered = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    _atomic_write(args.output, rendered.encode())
    sys.stdout.write(rendered)
    return exit_status


if __name__ == "__main__":
    raise SystemExit(main())
