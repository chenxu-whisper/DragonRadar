import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "verify_reset.py"


def performance_evidence():
    state = {"publications": {"rows": 1, "row_versions": 1}}
    run = {
        "cache_condition": "warm_process_and_warmed_database",
        "request_count": 20,
        "concurrency": 20,
        "successful": 20,
        "error_rate": 0,
        "p50_seconds": 0.1,
        "p95_seconds": 0.2,
        "max_seconds": 0.3,
        "process_cpu_seconds": 0.1,
        "peak_memory_bytes": 1000,
        "sql_statement_count": 40,
    }
    return {
        "fixture": {
            "kind": "synthetic",
            "counts": {"securities": 1, "trading_sessions": 4, "publications": 1},
            "data_volume": {
                "market_archive_bytes": 100,
                "publication_payload_bytes": 200,
            },
            "projected_owner_work_seconds": ">5",
            "owner_execution": "queued_async",
        },
        "environment": {
            "python": "3.12",
            "postgresql": "17",
            "os_cold_cache_controlled": False,
        },
        "runs": [
            {
                **run,
                "cache_condition": (
                    "cold_process_first_concurrent_run_db_cache_uncontrolled"
                ),
            },
            run,
        ],
        "public_state_before": state,
        "public_state_after": state,
        "explain_analyze_buffers": [{"Plan": {"Node Type": "Index Scan"}}],
    }


def invoke(tmp_path, evidence=None):
    evidence_path = tmp_path / "performance.json"
    if evidence is not None:
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    output = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--performance-evidence",
            str(evidence_path),
            "--output",
            str(output),
        ],
        cwd=SCRIPT.parents[1],
        text=True,
        capture_output=True,
        check=False,
    )
    return result, output


def test_verifier_emits_machine_report_and_honest_blocked_external_gates(tmp_path):
    result, output = invoke(tmp_path, performance_evidence())

    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert report["development_complete"]["status"] == "pass"
    assert report["synthetic_regression"]["status"] == "pass"
    assert report["real_input"]["status"] == "not_run"
    assert report["official_rules"]["status"] == "not_run"
    assert report["real_model_evaluation"]["status"] == "blocked"
    assert report["public_sharing_permit"]["status"] == "blocked"
    assert report["shadow_run_20_trading_days"]["status"] == "blocked"
    assert report["overall_status"] == "blocked"
    assert report["performance"]["runs"][0]["request_count"] == 20
    assert report["performance"]["no_public_writes"] is True
    assert report["record"]["command"] == [
        "python",
        "scripts/verify_reset.py",
        "--performance-evidence",
        "<path>",
        "--output",
        "<path>",
    ]
    assert "postgresql+psycopg://" not in result.stdout
    assert (
        "Synthetic checks passed; external acceptance remains blocked." in result.stderr
    )


def test_verifier_never_turns_missing_or_skipped_database_evidence_into_pass(tmp_path):
    stale = tmp_path / "report.json"
    stale.write_text('{"overall_status":"pass"}', encoding="utf-8")
    missing, output = invoke(tmp_path)
    assert missing.returncode == 2
    assert not output.exists()
    assert json.loads(missing.stdout)["synthetic_regression"]["status"] == "missing"

    evidence = performance_evidence()
    evidence["runs"][0]["successful"] = 0
    evidence["runs"][0]["error_rate"] = 1
    failed, _output = invoke(tmp_path, evidence)
    assert failed.returncode == 1
    assert json.loads(failed.stdout)["synthetic_regression"]["status"] == "fail"


def test_verifier_rejects_changed_state_bad_latency_and_incomplete_metrics(tmp_path):
    for mutate in (
        lambda evidence: evidence.update(public_state_after={}),
        lambda evidence: evidence["runs"][1].update(p95_seconds=2.01),
        lambda evidence: evidence["runs"][1].pop("peak_memory_bytes"),
        lambda evidence: evidence.update(explain_analyze_buffers=[]),
    ):
        evidence = performance_evidence()
        mutate(evidence)
        result, _output = invoke(tmp_path, evidence)
        assert result.returncode == 1
        assert json.loads(result.stdout)["synthetic_regression"]["status"] == "fail"


def test_verifier_fails_closed_for_malformed_shapes_and_metric_types(tmp_path):
    for evidence in (
        {"fixture": "bad", "runs": "bad"},
        {
            **performance_evidence(),
            "public_state_before": None,
            "public_state_after": None,
        },
        {
            **performance_evidence(),
            "runs": [
                performance_evidence()["runs"][0],
                {
                    **performance_evidence()["runs"][1],
                    "p95_seconds": "fast",
                },
            ],
        },
    ):
        result, _output = invoke(tmp_path, evidence)
        assert result.returncode == 1
        assert json.loads(result.stdout)["synthetic_regression"]["status"] == "fail"
