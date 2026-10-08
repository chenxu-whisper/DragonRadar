"""Validate Reset base evidence without upgrading missing external gates to pass."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

COMMAND_RECORD = [
    "python",
    "scripts/verify_reset.py",
    "--performance-evidence",
    "<path>",
    "--output",
    "<path>",
]
REQUIRED_RUN_FIELDS = {
    "cache_condition",
    "request_count",
    "concurrency",
    "successful",
    "error_rate",
    "p50_seconds",
    "p95_seconds",
    "max_seconds",
    "process_cpu_seconds",
    "peak_memory_bytes",
    "sql_statement_count",
}


def _gate(status: str, reason: str) -> dict[str, str]:
    return {"status": status, "reason": reason}


def _commit() -> dict[str, Any]:
    process = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "command": ["git", "rev-parse", "HEAD"],
        "exit_code": process.returncode,
        "value": process.stdout.strip() if process.returncode == 0 else None,
    }


def _base_report(performance_path: Path, output_path: Path) -> dict[str, Any]:
    return {
        "overall_status": "blocked",
        "development_complete": _gate("missing", "Synthetic evidence not validated."),
        "synthetic_regression": _gate("missing", "Performance evidence is missing."),
        "real_input": _gate("not_run", "No real input evidence was supplied."),
        "official_rules": _gate(
            "not_run", "No approved official rule evidence was supplied."
        ),
        "real_model_evaluation": _gate(
            "blocked", "No real model evaluation evidence or approval was supplied."
        ),
        "public_sharing_permit": _gate(
            "blocked", "No permit for real public sharing was supplied."
        ),
        "shadow_run_20_trading_days": _gate(
            "blocked", "No external 20-trading-day shadow-run records were supplied."
        ),
        "record": {
            "command": COMMAND_RECORD,
            "exit_code": None,
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "database_url_present": bool(
                    os.environ.get("DRAGON_TEST_DATABASE_URL")
                ),
            },
            "commit": _commit(),
            "artifact_paths": {
                "performance_evidence": str(performance_path),
                "report": str(output_path),
            },
        },
    }


def _valid_performance(value: Any) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return False, ["Evidence root must be an object."]
    fixture = value.get("fixture")
    if not isinstance(fixture, dict) or fixture.get("kind") != "synthetic":
        errors.append("Fixture must be explicitly labelled synthetic.")
    else:
        counts = fixture.get("counts")
        volume = fixture.get("data_volume")
        if not isinstance(counts, dict) or not counts:
            errors.append("Fixture counts are missing.")
        if (
            not isinstance(volume, dict)
            or not volume
            or any(type(item) is not int or item <= 0 for item in volume.values())
        ):
            errors.append("Positive archive and payload byte counts are required.")
        if fixture.get("projected_owner_work_seconds") != ">5":
            errors.append("Projected owner work threshold is not recorded.")
        if fixture.get("owner_execution") != "queued_async":
            errors.append("Owner custom analysis is not recorded as queued async.")
    environment = value.get("environment")
    if not isinstance(environment, dict) or not environment.get("postgresql"):
        errors.append("PostgreSQL version is missing.")
    runs = value.get("runs")
    if not isinstance(runs, list) or len(runs) != 2:
        errors.append("Exactly cold-labelled and warm runs are required.")
    else:
        labels = {run.get("cache_condition") for run in runs if isinstance(run, dict)}
        if not any(str(label).startswith("cold_process") for label in labels):
            errors.append("Honest cold-process cache labelling is missing.")
        if "warm_process_and_warmed_database" not in labels:
            errors.append("Warm database run is missing.")
        for index, run in enumerate(runs):
            if not isinstance(run, dict) or not REQUIRED_RUN_FIELDS <= run.keys():
                errors.append(f"Run {index} metrics are incomplete.")
                continue
            integer_metrics = (
                run["request_count"],
                run["concurrency"],
                run["successful"],
                run["peak_memory_bytes"],
                run["sql_statement_count"],
            )
            numeric_metrics = (
                run["error_rate"],
                run["p50_seconds"],
                run["p95_seconds"],
                run["max_seconds"],
                run["process_cpu_seconds"],
            )
            if any(type(metric) is not int for metric in integer_metrics) or any(
                type(metric) not in (int, float) for metric in numeric_metrics
            ):
                errors.append(f"Run {index} metric types are invalid.")
                continue
            if (
                run["request_count"] != 20
                or run["concurrency"] != 20
                or run["successful"] != 20
                or run["error_rate"] != 0
            ):
                errors.append(
                    f"Run {index} did not complete exactly 20 successful requests."
                )
            if (
                not isinstance(run["p95_seconds"], (int, float))
                or run["p95_seconds"] > 2
            ):
                errors.append(f"Run {index} exceeded the two-second P95 budget.")
            if run["peak_memory_bytes"] <= 0 or run["process_cpu_seconds"] < 0:
                errors.append(f"Run {index} resource metrics are invalid.")
    state_before = value.get("public_state_before")
    state_after = value.get("public_state_after")
    if not isinstance(state_before, dict) or not state_before:
        errors.append("Public pre-request state is missing.")
    if not isinstance(state_after, dict) or not state_after:
        errors.append("Public post-request state is missing.")
    if state_before != state_after:
        errors.append("Public request phase changed write-sensitive table state.")
    if not value.get("explain_analyze_buffers"):
        errors.append("EXPLAIN ANALYZE BUFFERS evidence is missing.")
    return not errors, errors


def verify(performance_path: Path, output_path: Path) -> tuple[dict[str, Any], int]:
    report = _base_report(performance_path, output_path)
    if not performance_path.is_file():
        report["record"]["exit_code"] = 2
        return report, 2
    try:
        performance = json.loads(performance_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        report["synthetic_regression"] = _gate(
            "fail", f"Performance evidence could not be parsed: {type(error).__name__}."
        )
        report["development_complete"] = _gate(
            "fail", "Evidence validation did not complete."
        )
        report["record"]["exit_code"] = 1
        return report, 1
    valid, errors = _valid_performance(performance)
    fixture = performance.get("fixture")
    fixture = fixture if isinstance(fixture, dict) else {}
    runs = performance.get("runs")
    runs = runs if isinstance(runs, list) else []
    state_before = performance.get("public_state_before")
    state_after = performance.get("public_state_after")
    report["performance"] = {
        "fixture_counts": fixture.get("counts"),
        "data_volume": fixture.get("data_volume"),
        "runs": runs,
        "no_public_writes": isinstance(state_before, dict)
        and bool(state_before)
        and state_before == state_after,
        "cache_conditions": [
            run.get("cache_condition") for run in runs if isinstance(run, dict)
        ],
        "artifact_path": str(performance_path),
    }
    if not valid:
        report["synthetic_regression"] = _gate("fail", " ".join(errors))
        report["development_complete"] = _gate(
            "fail", "Synthetic acceptance evidence is invalid."
        )
        report["record"]["exit_code"] = 1
        return report, 1
    report["synthetic_regression"] = _gate(
        "pass", "Deterministic PostgreSQL performance and no-write evidence validated."
    )
    report["development_complete"] = _gate(
        "pass", "Task20 automated development evidence is complete."
    )
    report["record"]["exit_code"] = 0
    return report, 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--performance-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() != args.performance_evidence.resolve():
        args.output.unlink(missing_ok=True)
    report, exit_code = verify(args.performance_evidence, args.output)
    rendered = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    sys.stdout.write(rendered)
    if exit_code != 2:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    if exit_code == 0:
        print(
            "Synthetic checks passed; external acceptance remains blocked.",
            file=sys.stderr,
        )
    elif exit_code == 2:
        print("Performance evidence is missing; no pass was recorded.", file=sys.stderr)
    else:
        print("Synthetic evidence validation failed.", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
