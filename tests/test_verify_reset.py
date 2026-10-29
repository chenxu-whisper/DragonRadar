import hashlib
import json
import runpy
import subprocess
import sys
from copy import deepcopy
from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "verify_reset.py"
VERIFIER = runpy.run_path(str(SCRIPT))
REQUIRED_GATES = tuple(VERIFIER["REQUIRED_GATES"])
GATE_SPECS = VERIFIER["GATE_SPECS"]
RECOVERY_DRILLS = tuple(VERIFIER["RECOVERY_DRILLS"])


@dataclass(frozen=True)
class FakeRunResult:
    started_at: datetime
    ended_at: datetime
    exit_status: int
    stdout: bytes
    stderr: bytes = b""
    timed_out: bool = False


class RecordingRunner:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def run(self, *, argv, cwd, environment, timeout_seconds):
        self.calls.append(
            {
                "argv": argv,
                "cwd": cwd,
                "environment": environment,
                "timeout_seconds": timeout_seconds,
            }
        )
        return next(self.results)


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
    buffer_counts = {
        "shared_hit_blocks": 2,
        "shared_read_blocks": 0,
        "shared_dirtied_blocks": 0,
        "shared_written_blocks": 0,
        "local_hit_blocks": 0,
        "local_read_blocks": 0,
        "local_dirtied_blocks": 0,
        "local_written_blocks": 0,
        "temp_read_blocks": 0,
        "temp_written_blocks": 0,
    }
    paths = (
        "/api/snapshots/{id}",
        "/api/market/overview",
        "/api/rank/range",
        "/api/stock/{id}",
    )
    normalized_sql = (
        "SELECT publications.id FROM publications WHERE publications.id = %(id)s",
        "SELECT payload FROM publication_results WHERE publication_id = %(id)s",
        "SELECT source_id FROM source_dependencies WHERE publication_id = %(id)s",
        "SELECT id FROM withdrawal_records WHERE source_id = %(source_id)s",
        "SELECT withdrawal_id FROM withdrawal_dependencies "
        "WHERE archive_id = %(archive_id)s",
    )
    shape_hashes = {
        statement: hashlib.sha256(statement.encode("utf-8")).hexdigest()
        for statement in normalized_sql
    }
    generic_lookup_hash = shape_hashes[normalized_sql[0]]
    projection_hashes = sorted(set(shape_hashes.values()) - {generic_lookup_hash})
    runs = [
        {
            "round": index,
            "cache_condition": "post_warmup_caches_uncontrolled",
            "request_count": 20,
            "concurrency": 20,
            "successful": 20,
            "error_rate": 0.0,
            "p50_seconds": 0.08,
            "p95_seconds": 0.15,
            "max_seconds": 0.25,
            "wall_seconds": 0.35,
            "process_cpu_seconds": 0.15,
            "peak_memory_bytes": 100,
            "rss_bytes": 100,
            "memory_measurement": "test",
            "sql_statement_count": 100,
        }
        for index in range(1, 4)
    ]
    return {
        "fixture": {
            "kind": "synthetic",
            "counts": {"securities": 100, "trading_sessions": 61},
            "data_volume": {"market_archive_bytes": 1},
        },
        "environment": {
            "postgresql": "17",
            "os_cold_cache_controlled": False,
            "cache_claim": "warmup_then_measured_caches_uncontrolled",
        },
        "warmup": {
            "paths": [
                "/api/snapshots/{id}",
                "/api/market/overview",
                "/api/rank/range",
                "/api/stock/{id}",
            ],
            "request_count": 20,
            "concurrency": 20,
            "successful": 20,
        },
        "runs": runs,
        "aggregate": {
            "round_count": 3,
            "request_count": 60,
            "successful": 60,
            "error_rate": 0.0,
            "p50_seconds": 0.08,
            "p95_seconds": 0.15,
            "max_seconds": 0.25,
            "wall_seconds": 1.05,
            "process_cpu_seconds": 0.45,
        },
        "async_work": {
            "synthetic": True,
            "label": "controlled_worker_delay",
            "submission_seconds": 0.1,
            "submitted_status": "queued",
            "worker_elapsed_seconds": 5.1,
            "status": "succeeded",
        },
        "observed_query_shapes": {
            shape_hashes[statement]: {
                "normalized_sql": statement,
                "verb": "SELECT",
                "relations": [
                    statement.split(" FROM ", maxsplit=1)[1].split(maxsplit=1)[0]
                ],
                "plan_role": (
                    "generic_publication_lookup"
                    if shape_hashes[statement] == generic_lookup_hash
                    else "payload_projection_read"
                ),
                "endpoint_observations": {
                    path: {
                        "count": 15,
                        "total_seconds": 0.15,
                        "min_seconds": 0.005,
                        "max_seconds": 0.02,
                    }
                    for path in paths
                },
            }
            for statement in normalized_sql
        },
        "endpoint_shape_hashes": {
            path: sorted(shape_hashes.values()) for path in paths
        },
        "query_attribution_groups": [
            {
                "scope": "shared_repository_projection",
                "endpoints": sorted(paths),
                "sql_hashes": sorted(shape_hashes.values()),
            }
        ],
        "application_state_before": {"table": {"rows": 1, "fingerprint": "a"}},
        "application_state_after": {"table": {"rows": 1, "fingerprint": "a"}},
        "query_plan_evidence": {
            digest: {
                "status": "explained",
                "sql_relations": [
                    statement.split(" FROM ", maxsplit=1)[1].split(maxsplit=1)[0]
                ],
                "plan": {
                    "relations": [
                        statement.split(" FROM ", maxsplit=1)[1].split(maxsplit=1)[0]
                    ],
                    "indexes": [f"{relation}_test_idx"],
                    "nodes": ["Index Scan"],
                    "node_count": 1,
                    "buffers": buffer_counts,
                },
            }
            for statement, digest in shape_hashes.items()
            for relation in [
                statement.split(" FROM ", maxsplit=1)[1].split(maxsplit=1)[0]
            ]
            if digest in projection_hashes
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
                "warmup": raw["warmup"],
                "runs": raw["runs"],
                "aggregate": raw["aggregate"],
                "fixture": raw["fixture"],
                "environment": raw["environment"],
            }
        elif gate == "public_no_write":
            observations = {
                "sql_statement_count": sum(
                    timing["count"]
                    for shape in raw["observed_query_shapes"].values()
                    for timing in shape["endpoint_observations"].values()
                ),
                "sql_write_count": 0,
                "fingerprints_equal": True,
                "application_table_count": 1,
                "endpoint_shape_hashes": raw["endpoint_shape_hashes"],
                "query_attribution_groups": raw["query_attribution_groups"],
                "query_plan_evidence": raw["query_plan_evidence"],
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
        "schema_version": 3,
        "mode": "full",
        "generated_at": "2026-10-08T00:00:00+00:00",
        "tested_commit": git_head(),
        "environment": {
            "platform": "test",
            "python": "3.12",
            "node": "22",
            "postgresql_evidence_available": True,
        },
        "repository": {
            "before": {
                "head": git_head(),
                "tracked_clean": True,
                "disallowed_changes": [],
            },
            "after": {
                "head": git_head(),
                "tracked_clean": True,
                "disallowed_changes": [],
            },
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


def test_count_parser_maps_node_test_tap_block_without_key_error():
    # `npm test` ends with the node:test gates, whose TAP summary emits
    # "# pass" / "# fail" rather than pytest's "passed" / "failed"; the
    # parser must normalize both aliases (regression: KeyError "pass"
    # crashed the harness after the frontend_tests gate).
    count_output = VERIFIER["_counts"]
    output = "\n".join(
        [
            "Test Files  32 passed",
            "Tests  1025 passed",
            "1..10",
            "# tests 10",
            "# pass 10",
            "# fail 0",
            "# cancelled 0",
            "# skipped 0",
            "# todo 0",
            "# duration_ms 88.294201",
        ]
    )

    counts = count_output(output, 0, fallback_from_exit=False)

    assert counts["passed"] == 1025
    assert counts["failed"] == 0
    assert counts["skipped"] == 0


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("passed", {"passed": 1, "failed": 0}),
        ("failed", {"passed": 0, "failed": 1}),
    ],
)
def test_count_parser_uses_machine_readable_status(status, expected):
    count_output = VERIFIER["_counts"]

    counts = count_output(
        json.dumps({"project": "task20", "status": status}),
        0,
        fallback_from_exit=False,
    )

    assert counts["passed"] == expected["passed"]
    assert counts["failed"] == expected["failed"]


def test_allowlisted_command_graph_is_immutable():
    with pytest.raises(TypeError):
        GATE_SPECS["full_backend"] = ()
    with pytest.raises(FrozenInstanceError):
        GATE_SPECS["full_backend"][0].timeout_seconds = 1
    assert isinstance(GATE_SPECS["full_backend"][0]["command"], tuple)


@pytest.mark.parametrize(
    ("status_lines", "expected_changes"),
    [
        ([], []),
        (["?? .temp/acceptance/result.json"], []),
        (["?? .temp/result.json", "?? docs/untracked.md"], ["?? docs/untracked.md"]),
        ([" M src/dragon_radar/api.py"], [" M src/dragon_radar/api.py"]),
        ([" M .temp/tracked.json"], [" M .temp/tracked.json"]),
    ],
)
def test_repository_state_ignores_only_untracked_temp(status_lines, expected_changes):
    classify = VERIFIER["_repository_state_from_status"]

    state = classify("1" * 40, status_lines)

    assert state["tracked_clean"] is (not expected_changes)
    assert state["disallowed_changes"] == expected_changes


def test_repository_evidence_requires_stable_clean_head():
    errors = VERIFIER["_repository_errors"]
    clean = {
        "head": "1" * 40,
        "tracked_clean": True,
        "disallowed_changes": [],
    }

    assert errors(clean, clean) == []
    assert any(
        "HEAD changed" in error for error in errors(clean, {**clean, "head": "2" * 40})
    )
    assert any(
        "not clean" in error
        for error in errors(
            clean,
            {
                **clean,
                "tracked_clean": False,
                "disallowed_changes": [" M README.md"],
            },
        )
    )


def test_execution_refuses_dirty_repository_before_running(tmp_path, monkeypatch):
    run_gates = VERIFIER["_run_gates"]
    monkeypatch.setitem(
        run_gates.__globals__,
        "_repository_state",
        lambda: {
            "head": git_head(),
            "tracked_clean": False,
            "disallowed_changes": [" M README.md"],
        },
    )
    runner = RecordingRunner([])

    with pytest.raises(RuntimeError, match="preflight.*not clean"):
        run_gates("focused", tmp_path, runner)

    assert runner.calls == []


def test_subprocess_runner_resolves_windows_command_shim(monkeypatch, tmp_path):
    runner = VERIFIER["SubprocessRunner"]()
    run = VERIFIER["SubprocessRunner"].run
    calls = []

    monkeypatch.setattr(
        run.__globals__["shutil"],
        "which",
        lambda command: "C:/Program Files/nodejs/npm.cmd",
    )

    def completed(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, b"ok", b"")

    monkeypatch.setattr(run.__globals__["subprocess"], "run", completed)

    result = runner.run(
        argv=("npm", "--version"),
        cwd=tmp_path,
        environment={},
        timeout_seconds=5,
    )

    assert calls[0][0] == ("C:/Program Files/nodejs/npm.cmd", "--version")
    assert calls[0][1]["shell"] is False
    assert result.exit_status == 0


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


def test_performance_contract_requires_realistic_warmed_multi_round_fixture():
    errors = VERIFIER["_performance_errors"]
    valid = performance_raw()

    assert errors(valid) == []

    undersized = deepcopy(valid)
    undersized["fixture"]["counts"]["securities"] = 99
    assert any("100 securities" in error for error in errors(undersized))

    false_cold = deepcopy(valid)
    false_cold["runs"][0]["cache_condition"] = "cold_process"
    assert any("cold" in error.lower() for error in errors(false_cold))

    no_aggregate = deepcopy(valid)
    del no_aggregate["aggregate"]
    assert any("aggregate" in error.lower() for error in errors(no_aggregate))


def test_performance_contract_rejects_endpoint_map_not_matching_observed_shapes():
    errors = VERIFIER["_performance_errors"]
    valid = performance_raw()

    hard_coded = deepcopy(valid)
    hard_coded["endpoint_shape_hashes"]["/api/market/overview"] = [
        next(iter(valid["observed_query_shapes"]))
    ]

    assert any("observed shape set" in error.lower() for error in errors(hard_coded))


def test_performance_contract_rejects_legacy_distinct_endpoint_representatives():
    errors = VERIFIER["_performance_errors"]
    legacy = performance_raw()
    hashes = iter(legacy["observed_query_shapes"])
    legacy["measured_path_explains"] = {
        path: {"sql_sha256": next(hashes)} for path in legacy["endpoint_shape_hashes"]
    }

    assert any("legacy" in error.lower() for error in errors(legacy))


def test_performance_contract_rejects_endpoint_plan_representatives():
    errors = VERIFIER["_performance_errors"]
    fabricated = performance_raw()
    hashes = iter(fabricated["observed_query_shapes"])
    fabricated["endpoint_plans"] = {
        path: {"sql_sha256": next(hashes)}
        for path in fabricated["endpoint_shape_hashes"]
    }

    assert any("field" in error.lower() for error in errors(fabricated))


def test_performance_contract_rejects_tampered_attribution_group():
    errors = VERIFIER["_performance_errors"]
    tampered = performance_raw()
    tampered["query_attribution_groups"][0]["endpoints"].pop()

    assert any("attribution groups" in error.lower() for error in errors(tampered))


def test_performance_contract_rejects_missing_shared_shape_plan():
    errors = VERIFIER["_performance_errors"]
    missing = performance_raw()
    missing["query_plan_evidence"].pop(next(iter(missing["query_plan_evidence"])))

    assert any("plan coverage" in error.lower() for error in errors(missing))


def test_performance_contract_rejects_uncaptured_plan_hash():
    errors = VERIFIER["_performance_errors"]
    uncaptured = performance_raw()
    plan = deepcopy(next(iter(uncaptured["query_plan_evidence"].values())))
    uncaptured["query_plan_evidence"]["f" * 64] = plan

    plan_errors = errors(uncaptured)
    assert any("plan coverage" in error.lower() for error in plan_errors)
    assert any("captured payload projection" in error.lower() for error in plan_errors)


def test_performance_contract_rejects_extra_normalized_plan_relation():
    errors = VERIFIER["_performance_errors"]
    fabricated = performance_raw()
    plan = next(iter(fabricated["query_plan_evidence"].values()))["plan"]
    plan["relations"].append("fabricated_relation")
    plan["relations"].sort()

    assert any("plan relations" in error.lower() for error in errors(fabricated))


def test_performance_contract_rejects_endpoint_without_observed_sql():
    errors = VERIFIER["_performance_errors"]
    fabricated = performance_raw()
    missing_path = "/api/stock/{id}"
    reassigned_path = "/api/snapshots/{id}"
    for shape in fabricated["observed_query_shapes"].values():
        shape["endpoint_observations"].pop(missing_path)
        timing = shape["endpoint_observations"][reassigned_path]
        timing["count"] *= 2
        timing["total_seconds"] *= 2
    fabricated["endpoint_shape_hashes"][missing_path] = []
    fabricated["query_attribution_groups"] = VERIFIER["_attribution_groups"](
        {
            path: set(hashes)
            for path, hashes in fabricated["endpoint_shape_hashes"].items()
        }
    )

    assert any("no observed sql" in error.lower() for error in errors(fabricated))


def test_performance_contract_rejects_fabricated_distinct_endpoint_mappings():
    errors = VERIFIER["_performance_errors"]
    fabricated = performance_raw()
    hashes = iter(fabricated["observed_query_shapes"])
    fabricated["endpoint_shape_hashes"] = {
        path: [next(hashes)] for path in fabricated["endpoint_shape_hashes"]
    }

    mapping_errors = errors(fabricated)
    assert (
        len(
            [error for error in mapping_errors if "observed shape set" in error.lower()]
        )
        == 4
    )


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
        "--validate-existing",
        str(evidence),
    )

    assert result.returncode != 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["overall_status"] == "blocked"
    assert report["automated_acceptance"]["status"] == "unverified"
    assert report["integrity_validation"]["status"] == "fail"
    assert report["source"]["trust"] == "INTEGRITY_ONLY"
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
        "--validate-existing",
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


def test_fabricated_all_pass_json_is_integrity_only_and_cannot_pass_acceptance(
    tmp_path,
):
    evidence = write_manifest(tmp_path, evidence_manifest(tmp_path))

    result, output = invoke(
        tmp_path,
        "--validate-existing",
        str(evidence),
    )

    assert result.returncode != 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["automated_acceptance"]["status"] == "unverified"
    assert report["integrity_validation"]["status"] == "pass"
    assert report["source"]["trust"] == "INTEGRITY_ONLY"
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


def test_self_hashed_logs_and_fake_exit_zero_remain_unverified(tmp_path):
    manifest = evidence_manifest(tmp_path)
    for item in manifest["records"]:
        for command in item["commands"]:
            command["exit_status"] = 0
            command["timed_out"] = False
            command["observed_counts"] = {
                "passed": 1,
                "failed": 0,
                "skipped": 0,
                "output_lines": 1,
            }
    evidence = write_manifest(tmp_path, manifest)

    result, output = invoke(tmp_path, "--validate-existing", str(evidence))

    assert result.returncode != 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["automated_acceptance"]["status"] == "unverified"
    assert report["source"]["trust"] == "INTEGRITY_ONLY"


def test_execution_mode_invokes_exact_allowlisted_graph_and_uses_runner_results(
    tmp_path,
):
    execute = VERIFIER["_execute_gates"]
    specs = [
        spec
        for gate in REQUIRED_GATES
        if gate not in {"public_no_write", "async_over_five_seconds"}
        for spec in GATE_SPECS[gate]
    ]
    started = datetime(2026, 10, 8, tzinfo=UTC)
    results = [
        FakeRunResult(
            started_at=started + timedelta(seconds=index),
            ended_at=started + timedelta(seconds=index + 1),
            exit_status=0,
            stdout=b"1 passed\n",
            stderr=f"runner-stderr-{index}\n".encode(),
        )
        for index in range(len(specs))
    ]
    runner = RecordingRunner(results)

    records = execute("full", tmp_path, runner)

    assert [call["argv"] for call in runner.calls] == [
        tuple(spec["command"]) for spec in specs
    ]
    assert len(records) == len(REQUIRED_GATES)
    first = records[0]["commands"][0]
    assert first["started_at"] == started.isoformat()
    assert first["ended_at"] == (started + timedelta(seconds=1)).isoformat()
    assert Path(first["stdout_artifact"]["path"]).read_bytes() == b"1 passed\n"
    assert Path(first["stderr_artifact"]["path"]).read_bytes() == b"runner-stderr-0\n"


def test_execution_rejects_omitted_command_result(tmp_path):
    execute = VERIFIER["_execute_gates"]
    runner = RecordingRunner([])

    with pytest.raises(RuntimeError, match="did not execute"):
        execute("full", tmp_path, runner)


def test_execution_rejects_exit_zero_with_failed_native_output(tmp_path):
    validate_result = VERIFIER["_command_result_errors"]
    result = FakeRunResult(
        started_at=datetime(2026, 10, 8, tzinfo=UTC),
        ended_at=datetime(2026, 10, 8, 0, 0, 1, tzinfo=UTC),
        exit_status=0,
        stdout=b"1 failed\n",
    )

    errors = validate_result(result, requires_test_counts=True)

    assert any("native output" in error for error in errors)


def test_execution_rejects_exit_zero_without_native_output():
    validate_result = VERIFIER["_command_result_errors"]
    result = FakeRunResult(
        started_at=datetime(2026, 10, 8, tzinfo=UTC),
        ended_at=datetime(2026, 10, 8, 0, 0, 1, tzinfo=UTC),
        exit_status=0,
        stdout=b"",
    )

    errors = validate_result(result, requires_test_counts=True)

    assert any("native output" in error for error in errors)


def test_full_execution_manifest_passes_only_in_execution_context(
    tmp_path, monkeypatch
):
    run_gates = VERIFIER["_run_gates"]
    validate_execution = VERIFIER["_validate_execution"]
    performance_path = tmp_path / "reset-performance.json"
    performance_path.write_text(json.dumps(performance_raw()), encoding="utf-8")
    monkeypatch.setitem(
        run_gates.__globals__,
        "PERFORMANCE_ARTIFACT",
        performance_path,
    )
    clean_repository = {
        "head": git_head(),
        "tracked_clean": True,
        "disallowed_changes": [],
    }
    monkeypatch.setitem(
        run_gates.__globals__,
        "_repository_state",
        lambda: deepcopy(clean_repository),
    )
    command_count = sum(
        len(GATE_SPECS[gate])
        for gate in REQUIRED_GATES
        if gate not in {"public_no_write", "async_over_five_seconds"}
    )
    started = datetime(2026, 10, 8, tzinfo=UTC)
    runner = RecordingRunner(
        [
            FakeRunResult(
                started_at=started + timedelta(seconds=index),
                ended_at=started + timedelta(seconds=index + 1),
                exit_status=0,
                stdout=b"1 passed\n",
            )
            for index in range(command_count)
        ]
    )

    execution = run_gates("full", tmp_path, runner)
    report, exit_status = validate_execution(execution)

    assert exit_status == 0
    assert report["automated_acceptance"]["status"] == "pass"
    assert report["source"]["trust"] == "EXECUTED"
    assert all(item["status"] == "pass" for item in report["gates"].values())
