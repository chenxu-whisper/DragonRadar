"""CLI runs without database or supplier credentials; payloads are synthetic."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_supplier_sample import sample_payload

ROOT = Path(__file__).resolve().parents[1]


def invoke(path, *args):
    environment = {
        key: value for key, value in os.environ.items() if key != "DRAGON_DATABASE_URL"
    }
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "dragon_radar.ingestion.sample_cli",
            str(path),
            "--now",
            "2026-10-02T12:00:00+00:00",
            *map(str, args),
        ],
        capture_output=True,
        text=True,
        env=environment,
        cwd=ROOT,
        check=False,
    )


def write_sample(tmp_path, payload=None):
    path = tmp_path / "sample.json"
    path.write_text(json.dumps(payload or sample_payload()), encoding="utf-8")
    return path


def test_offline_cli_passes_without_database_or_claiming_production(tmp_path):
    result = invoke(write_sample(tmp_path))
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "pass"
    assert report["test_only"] is True
    assert report["production_approved"] is False


def test_missing_sample_facts_return_two(tmp_path):
    payload = sample_payload()
    payload["benchmark_closes"] = {}
    result = invoke(write_sample(tmp_path, payload))
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "blocked"


@pytest.mark.parametrize(
    "text",
    [
        '{"schema":"x","schema":"SECRET_TOKEN_SAMPLE"}',
        '{"schema":"x","payload":NaN,"secret":"SECRET_TOKEN_SAMPLE"}',
        '{"schema":"x","secret":"SECRET_TOKEN_SAMPLE"}',
    ],
)
def test_bad_json_fails_without_echoing_secrets(tmp_path, text):
    path = tmp_path / "invalid.json"
    path.write_text(text, encoding="utf-8")
    result = invoke(path)
    assert result.returncode == 1
    assert "SECRET_TOKEN_SAMPLE" not in result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "fail"


def test_report_output_never_overwrites_existing_file(tmp_path):
    source = write_sample(tmp_path)
    target = tmp_path / "report.json"
    target.write_text("existing", encoding="utf-8")
    result = invoke(source, "--output", target)
    assert result.returncode == 1
    assert target.read_text(encoding="utf-8") == "existing"
    assert json.loads(result.stdout)["status"] == "fail"


def test_report_cannot_replace_input(tmp_path):
    source = write_sample(tmp_path)
    original = source.read_bytes()
    result = invoke(source, "--output", source)
    assert result.returncode == 1
    assert source.read_bytes() == original


def test_incomplete_mapping_arguments_fail_safely(tmp_path):
    source = write_sample(tmp_path)
    result = invoke(source, "--bar-rows", source)
    assert result.returncode == 1
    assert json.loads(result.stdout)["issues"][0]["code"] == "mapping_arguments"


def test_script_entry_runs_same_checks(tmp_path):
    source = write_sample(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/verify_supplier_sample.py"),
            str(source),
            "--now",
            "2026-10-02T12:00:00+00:00",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["production_approved"] is False


def test_excessively_large_input_fails_before_parse(tmp_path):
    source = tmp_path / "large.json"
    with source.open("wb") as stream:
        stream.truncate(16 * 1024 * 1024 + 1)
    result = invoke(source)
    assert result.returncode == 1
    assert "input_unavailable" in result.stdout


def test_invalid_operation_time_is_safe_json_error(tmp_path):
    result = invoke(write_sample(tmp_path), "--now", "SECRET_TOKEN_SAMPLE")
    assert result.returncode == 1
    assert "SECRET_TOKEN_SAMPLE" not in result.stdout + result.stderr
    assert json.loads(result.stdout)["issues"][0]["code"] == "operation_time"


def write_mapping_inputs(tmp_path):
    payload = sample_payload()
    bars = payload["batch"]["bars"]
    states = payload["batch"]["security_states"]
    profile = {
        "schema": "dragon-radar-supplier-mapping/v1",
        "profile_id": "offline-canonical-test",
        "source_id": "synthetic",
        "source_version": "s1",
        "bar_fields": {field: field for field in bars[0]},
        "state_fields": {field: field for field in states[0]},
        "bar_constants": {},
        "state_constants": {},
        "value_maps": {},
        "decimal_scales": {},
    }
    paths = []
    for name, data in (
        ("profile", profile),
        ("bars", bars),
        ("states", states),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        paths.append(path)
    return paths


def test_cli_maps_rows_and_reports_input_hashes(tmp_path):
    source = write_sample(tmp_path)
    profile, bars, states = write_mapping_inputs(tmp_path)
    result = invoke(
        source,
        "--mapping",
        profile,
        "--bar-rows",
        bars,
        "--state-rows",
        states,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert all(
        len(report[key]) == 64
        for key in ("profile_sha256", "bar_rows_sha256", "state_rows_sha256")
    )
    assert report["production_approved"] is False


def test_cli_mapping_failure_is_not_hidden_by_valid_canonical_sample(tmp_path):
    source = write_sample(tmp_path)
    profile, bars, states = write_mapping_inputs(tmp_path)
    rows = json.loads(bars.read_text(encoding="utf-8"))
    rows[0]["close"] = "SECRET_BAD_PRICE"
    bars.write_text(json.dumps(rows), encoding="utf-8")
    result = invoke(
        source,
        "--mapping",
        profile,
        "--bar-rows",
        bars,
        "--state-rows",
        states,
    )
    assert result.returncode == 1
    assert "SECRET_BAD_PRICE" not in result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "fail"
    assert report["normalization_issues"]


def test_cli_rejects_mapping_source_mismatch(tmp_path):
    source = write_sample(tmp_path)
    profile, bars, states = write_mapping_inputs(tmp_path)
    data = json.loads(profile.read_text(encoding="utf-8"))
    data["source_version"] = "other-version"
    profile.write_text(json.dumps(data), encoding="utf-8")
    result = invoke(
        source,
        "--mapping",
        profile,
        "--bar-rows",
        bars,
        "--state-rows",
        states,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["issues"][0]["code"] == "mapping_source_mismatch"


def test_cli_argument_errors_never_echo_input_values(tmp_path):
    result = invoke(write_sample(tmp_path), "--unknown", "SECRET_ARGUMENT_VALUE")
    assert result.returncode == 1
    assert "SECRET_ARGUMENT_VALUE" not in result.stdout + result.stderr
    assert json.loads(result.stdout)["issues"][0]["code"] == "arguments"
