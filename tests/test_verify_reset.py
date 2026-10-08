import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "verify_reset.py"
REQUIRED_GATES = {
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
}


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


def command_record(tmp_path, gate, *, skipped=0, exit_status=0):
    log = tmp_path / f"{gate}.log"
    log.write_text(f"{gate}: independently generated output\n", encoding="utf-8")
    observations = {"gate": gate}
    if gate == "performance":
        observations["runs"] = [
            {"cache_condition": "cold", "p95_seconds": 0.2},
            {"cache_condition": "warm", "p95_seconds": 0.1},
        ]
    elif gate == "public_no_write":
        observations.update(
            sql_write_count=0,
            fingerprints_equal=True,
            application_table_count=42,
        )
    elif gate == "async_over_five_seconds":
        observations.update(
            worker_elapsed_seconds=5.1,
            submission_seconds=0.1,
            status="succeeded",
            synthetic=True,
        )
    elif gate == "recovery_drills":
        observations["drills"] = [
            "snapshot_revision_and_pinned_rollback",
            "withdrawal_and_revocation",
            "source_failure",
            "local_backup_restore",
            "owner_session_expiry_and_revoke",
            "ai_off",
        ]
    return {
        "status": "pass",
        "commands": [
            {
                "command": ["recorded", gate],
                "cwd": ".",
                "exit_status": exit_status,
                "timed_out": False,
                "observed_counts": {
                    "passed": 1,
                    "failed": int(exit_status != 0),
                    "skipped": skipped,
                    "output_lines": 1,
                },
                "artifact": {
                    "path": str(log),
                    "sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
                    "bytes": log.stat().st_size,
                },
                "environment": {
                    "platform": "test",
                    "python": "3.12",
                    "node": "22",
                    "postgresql": "17",
                },
            }
        ],
        "observations": observations,
    }


def evidence_manifest(tmp_path):
    records = {gate: command_record(tmp_path, gate) for gate in sorted(REQUIRED_GATES)}
    return {
        "schema": "dragon-radar.task20-evidence.v1",
        "mode": "full",
        "commit": {"value": "98e1142969ba2bb96bc44d386cf6ec4ad31babb8"},
        "records": records,
    }


def write_manifest(tmp_path, manifest):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_count_parser_uses_test_count_from_ansi_colored_output():
    count_output = runpy.run_path(str(SCRIPT))["_counts"]
    output = (
        "\x1b[32m1 passed\x1b[39m\n"
        "\x1b[2mTests\x1b[22m \x1b[1m\x1b[32m14 passed\x1b[39m"
    )

    assert count_output(output, 0)["passed"] == 14


def test_evidence_artifact_snapshot_is_immutable_and_content_addressed(tmp_path):
    snapshot_artifact = runpy.run_path(str(SCRIPT))["_snapshot_artifact"]
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
    manifest["records"].pop("full_backend")
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
    assert set(report["gates"]) == REQUIRED_GATES


def test_validate_mode_rejects_skips_failures_missing_fields_and_bad_artifact_hash(
    tmp_path,
):
    mutations = (
        lambda record: record["commands"][0]["observed_counts"].update(skipped=1),
        lambda record: record["commands"][0].update(exit_status=1),
        lambda record: record["commands"][0].pop("environment"),
        lambda record: record["commands"][0]["artifact"].update(sha256="0" * 64),
    )
    for index, mutate in enumerate(mutations):
        case = tmp_path / str(index)
        case.mkdir()
        manifest = evidence_manifest(case)
        mutate(manifest["records"]["full_backend"])
        evidence = write_manifest(case, manifest)

        result, output = invoke(
            case,
            "--mode",
            "validate",
            "--evidence",
            str(evidence),
        )

        assert result.returncode == 1
        report = json.loads(output.read_text(encoding="utf-8"))
        assert report["gates"]["full_backend"]["status"] == "fail"
        assert report["automated_acceptance"]["status"] == "fail"


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
        assert command["command"]
        assert command["exit_status"] == 0
        assert command["observed_counts"]["skipped"] == 0
        assert command["artifact"]["sha256"]
        assert command["environment"]
    assert "postgresql+psycopg://" not in result.stdout
