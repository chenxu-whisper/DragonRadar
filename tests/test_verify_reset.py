import hashlib
import json
import runpy
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "verify_reset.py"
VERIFIER = runpy.run_path(str(SCRIPT))
REQUIRED_GATES = tuple(VERIFIER["REQUIRED_GATES"])
GATE_SPECS = VERIFIER["GATE_SPECS"]
RECOVERY_DRILLS = tuple(VERIFIER["RECOVERY_DRILLS"])


def invoke(tmp_path, *args):
    output = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--output", str(output)],
        cwd=SCRIPT.parents[1],
        text=True,
        capture_output=True,
        check=False,
    )
    return result, output


def git_head():
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=SCRIPT.parents[1],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def artifact(path):
    content = path.read_bytes()
    return {
        "path": str(path),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def command_record(tmp_path, gate, index, spec, *, skipped=0, exit_status=0):
    log = tmp_path / f"{gate}-{index}.log"
    log.write_text(f"{gate}: independently generated output\n", encoding="utf-8")
    return {
        "argv": list(spec["command"]),
        "cwd": spec["cwd"],
        "environment_class": spec.get("environment_class", "default"),
        "exit_status": exit_status,
        "timed_out": False,
        "observed_counts": {
            "passed": int(exit_status == 0),
            "failed": int(exit_status != 0),
            "skipped": skipped,
            "output_lines": 1,
        },
        "artifact": artifact(log),
    }


def performance_raw():
    runs = [
        {
            "cache_condition": (
                "cold_process_first_concurrent_run_db_cache_uncontrolled"
            ),
            "request_count": 20,
            "concurrency": 20,
            "successful": 20,
            "error_rate": 0.0,
            "p50_seconds": 0.1,
            "p95_seconds": 0.2,
            "max_seconds": 0.3,
            "wall_seconds": 0.4,
            "process_cpu_seconds": 0.2,
            "peak_memory_bytes": 100,
            "rss_bytes": 100,
            "memory_measurement": "test",
            "sql_statement_count": 2,
        },
        {
            "cache_condition": "warm_process_and_warmed_database",
            "request_count": 20,
            "concurrency": 20,
            "successful": 20,
            "error_rate": 0.0,
            "p50_seconds": 0.08,
            "p95_seconds": 0.15,
            "max_seconds": 0.25,
            "wall_seconds": 0.35,
            "process_cpu_seconds": 0.15,
            "peak_memory_bytes": 90,
            "rss_bytes": 90,
            "memory_measurement": "test",
            "sql_statement_count": 2,
        },
    ]
    return {
        "fixture": {
            "kind": "synthetic",
            "counts": {"securities": 1},
            "data_volume": {"market_archive_bytes": 1},
        },
        "environment": {"postgresql": "17"},
        "runs": runs,
        "async_work": {
            "synthetic": True,
            "label": "controlled_worker_delay",
            "submission_seconds": 0.1,
            "submitted_status": "queued",
            "worker_elapsed_seconds": 5.1,
            "status": "succeeded",
        },
        "public_sql_statements": [
            {"path": "/api/snapshots/list", "verb": "SELECT"},
            {"path": "/api/snapshots/{id}", "verb": "SELECT"},
            {"path": "/api/snapshots/list", "verb": "SELECT"},
            {"path": "/api/snapshots/{id}", "verb": "SELECT"},
        ],
        "application_state_before": {"table": {"rows": 1, "fingerprint": "a"}},
        "application_state_after": {"table": {"rows": 1, "fingerprint": "a"}},
        "measured_path_explains": {
            "/api/snapshots/list": {"plan": ["index"]},
            "/api/snapshots/{id}": {"plan": ["index"]},
        },
    }


def evidence_manifest(tmp_path):
    raw_path = tmp_path / "performance.json"
    raw = performance_raw()
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    performance_commands = [
        command_record(tmp_path, "performance", index, spec)
        for index, spec in enumerate(GATE_SPECS["performance"], start=1)
    ]
    records = []
    for gate in REQUIRED_GATES:
        specs = GATE_SPECS.get(gate, GATE_SPECS["performance"])
        commands = (
            deepcopy(performance_commands)
            if gate in {"public_no_write", "async_over_five_seconds"}
            else [
                command_record(tmp_path, gate, index, spec)
                for index, spec in enumerate(specs, start=1)
            ]
        )
        observations = {}
        if gate == "performance":
            observations = {
                "raw_artifact": artifact(raw_path),
                "runs": raw["runs"],
                "fixture": raw["fixture"],
                "environment": raw["environment"],
            }
        elif gate == "public_no_write":
            observations = {
                "sql_statement_count": 4,
                "sql_write_count": 0,
                "fingerprints_equal": True,
                "application_table_count": 1,
                "measured_path_explains": raw["measured_path_explains"],
            }
        elif gate == "async_over_five_seconds":
            observations = raw["async_work"]
        elif gate == "recovery_drills":
            observations = {"drills": list(RECOVERY_DRILLS)}
        records.append(
            {"gate_id": gate, "commands": commands, "observations": observations}
        )
    return {
        "schema": "dragon-radar.task20-evidence",
        "schema_version": 2,
        "mode": "full",
        "generated_at": "2026-10-08T00:00:00+00:00",
        "tested_commit": git_head(),
        "environment": {
            "platform": "test",
            "python": "3.12",
            "node": "22",
            "postgresql_evidence_available": True,
        },
        "records": records,
    }


def write_manifest(tmp_path, manifest):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_count_parser_uses_test_count_from_ansi_colored_output():
    count_output = VERIFIER["_counts"]
    output = (
        "\x1b[32m1 passed\x1b[39m\n"
        "\x1b[2mTests\x1b[22m \x1b[1m\x1b[32m14 passed\x1b[39m"
    )

    assert count_output(output, 0)["passed"] == 14


def test_evidence_artifact_snapshot_is_immutable_and_content_addressed(tmp_path):
    snapshot_artifact = VERIFIER["_snapshot_artifact"]
    source = tmp_path / "performance.json"
    source.write_bytes(b'{"run":"first"}')

    recorded = snapshot_artifact(source, tmp_path / "artifacts")
    source.write_bytes(b'{"run":"overwritten"}')
    snapshot = Path(recorded["path"])

    assert snapshot.read_bytes() == b'{"run":"first"}'
    assert snapshot.name == f"reset-performance-{recorded['sha256']}.json"
    assert recorded["bytes"] == len(b'{"run":"first"}')


def test_default_full_mode_cannot_self_certify_from_performance_json(tmp_path):
    performance = tmp_path / "performance.json"
    performance.write_text('{"runs":[]}', encoding="utf-8")

    result, output = invoke(tmp_path, "--performance-evidence", str(performance))

    assert result.returncode != 0
    assert not output.exists()
    assert "--performance-evidence" in result.stderr


def test_validate_mode_requires_every_task20_gate_and_never_marks_development_pass(
    tmp_path,
):
    manifest = evidence_manifest(tmp_path)
    manifest["records"] = [
        record for record in manifest["records"] if record["gate_id"] != "full_backend"
    ]
    evidence = write_manifest(tmp_path, manifest)

    result, output = invoke(
        tmp_path,
        "--mode",
        "validate",
        "--evidence",
        str(evidence),
    )

    assert result.returncode == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["overall_status"] == "blocked"
    assert report["automated_acceptance"]["status"] == "blocked"
    assert "development_complete" not in report
    assert report["gates"]["full_backend"]["status"] == "missing"
    assert set(report["gates"]) == set(REQUIRED_GATES)


def record(manifest, gate):
    return next(item for item in manifest["records"] if item["gate_id"] == gate)


@pytest.mark.parametrize(
    ("mutation", "gate"),
    [
        (lambda manifest: manifest.update(schema="wrong"), None),
        (lambda manifest: manifest.update(schema_version=1), None),
        (lambda manifest: manifest.update(mode="focused"), None),
        (lambda manifest: manifest.update(tested_commit="0" * 40), None),
        (
            lambda manifest: record(manifest, "full_backend")["commands"][0].update(
                argv=["python", "-c", "print('fabricated')"]
            ),
            "full_backend",
        ),
        (
            lambda manifest: record(manifest, "full_backend")["commands"][0].update(
                cwd="web"
            ),
            "full_backend",
        ),
        (
            lambda manifest: record(manifest, "full_backend")["commands"][0].update(
                environment_class="private-review"
            ),
            "full_backend",
        ),
        (
            lambda manifest: record(manifest, "full_backend")["commands"][0][
                "observed_counts"
            ].update(failed=1),
            "full_backend",
        ),
        (
            lambda manifest: record(manifest, "full_backend")["commands"][0][
                "observed_counts"
            ].update(passed=0),
            "full_backend",
        ),
        (
            lambda manifest: record(manifest, "full_backend")["commands"][0][
                "observed_counts"
            ].update(passed=2),
            "full_backend",
        ),
        (
            lambda manifest: record(manifest, "full_backend")["commands"][0][
                "observed_counts"
            ].update(skipped=1),
            "full_backend",
        ),
        (
            lambda manifest: record(manifest, "full_backend")["commands"][0][
                "artifact"
            ].update(sha256="0" * 64),
            "full_backend",
        ),
        (
            lambda manifest: record(manifest, "full_backend")["commands"][0][
                "artifact"
            ].update(sha256=123),
            "full_backend",
        ),
        (
            lambda manifest: Path(
                record(manifest, "full_backend")["commands"][0]["artifact"]["path"]
            ).unlink(),
            "full_backend",
        ),
        (
            lambda manifest: record(manifest, "performance")["observations"]["runs"][
                0
            ].update(p95_seconds=0.9),
            "performance",
        ),
        (
            lambda manifest: record(manifest, "performance")["observations"]["runs"][
                0
            ].update(error_rate=0.1),
            "performance",
        ),
        (
            lambda manifest: record(manifest, "full_backend").update(status="pass"),
            "full_backend",
        ),
        (
            lambda manifest: manifest["records"].append(
                deepcopy(record(manifest, "full_backend"))
            ),
            "full_backend",
        ),
    ],
)
def test_validate_mode_rejects_adversarial_evidence(tmp_path, mutation, gate):
    manifest = evidence_manifest(tmp_path)
    mutation(manifest)
    evidence = write_manifest(tmp_path, manifest)

    result, output = invoke(
        tmp_path,
        "--mode",
        "validate",
        "--evidence",
        str(evidence),
    )

    assert result.returncode != 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["automated_acceptance"]["status"] != "pass"
    if gate:
        assert report["gates"][gate]["status"] == "fail"


def test_parent_commit_is_allowed_only_for_strict_evidence_only_head():
    approved = VERIFIER["_approved_tested_commit"]
    parent = "1" * 40
    head = "2" * 40

    assert approved(
        parent,
        head=head,
        parent=parent,
        changed_paths=["docs/acceptance/reset-base.md"],
    ) == (True, "HEAD^")
    assert approved(
        parent,
        head=head,
        parent=parent,
        changed_paths=["README.md", "docs/acceptance/reset-base.md"],
    ) == (False, None)


def test_complete_hashed_evidence_passes_automation_but_external_gates_block(
    tmp_path,
):
    evidence = write_manifest(tmp_path, evidence_manifest(tmp_path))

    result, output = invoke(
        tmp_path,
        "--mode",
        "validate",
        "--evidence",
        str(evidence),
    )

    assert result.returncode == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["automated_acceptance"]["status"] == "pass"
    assert report["overall_status"] == "blocked"
    assert report["real_input"]["status"] == "blocked"
    assert report["official_rules"]["status"] == "blocked"
    assert report["real_model_evaluation"]["status"] == "blocked"
    assert report["public_sharing_permit"]["status"] == "blocked"
    assert report["shadow_run_20_trading_days"]["status"] == "blocked"
    for gate in REQUIRED_GATES:
        assert report["gates"][gate]["status"] == "pass"
        command = report["gates"][gate]["commands"][0]
        assert command["argv"]
        assert command["exit_status"] == 0
        assert command["observed_counts"]["skipped"] == 0
        assert command["artifact"]["sha256"]
        assert command["environment_class"]
    assert report["source"]["tested_commit"] == git_head()
    assert report["source"]["commit_relation"] == "HEAD"
    assert "postgresql+psycopg://" not in result.stdout
