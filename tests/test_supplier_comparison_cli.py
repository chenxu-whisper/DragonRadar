"""Private offline CLI tests; all evidence is explicitly synthetic."""

import importlib
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from test_supplier_collection import root as root
from test_supplier_comparison import (
    CHECK_TIME,
    contracts,
    make_pair,
    private_json,
)
from test_supplier_comparison import (
    private_workspace as private_workspace,
)

from dragon_radar.production.trust import canonical_json


def cli():
    name = "dragon_radar.ingestion.comparison_cli"
    assert importlib.util.find_spec(name) is not None, "Q03 CLI is missing"
    return importlib.import_module(name)


@pytest.fixture
def files(tmp_path, monkeypatch):
    pair = make_pair(tmp_path)
    values = {
        "left": pair["left"].model_dump(mode="json", by_alias=True),
        "right": pair["right"].model_dump(mode="json", by_alias=True),
        "plan": pair["plan"],
        "identity-map": pair["mapping"],
        "left-grant": pair["left"].sample.batch.grant.model_dump(mode="json"),
        "right-grant": pair["right"].sample.batch.grant.model_dump(mode="json"),
    }
    paths = {}
    args = []
    for key, value in values.items():
        paths[key] = tmp_path / f"{key}.json"
        private_json(paths[key], value)
        args.extend([f"--{key}", str(paths[key])])
    return pair, paths, args


def run(files, capsys, monkeypatch, extra=(), **kwargs):
    mod = cli()
    monkeypatch.setattr(mod, "utc_now", lambda: CHECK_TIME)
    code = mod.main([*files[2], *extra], **kwargs)
    output = capsys.readouterr()
    assert not output.err
    report = json.loads(output.out)
    contracts().ComparisonReport.model_validate_json(canonical_json(report))
    assert "PRIVATE_TEST_SECRET" not in output.out
    return code, report


def test_private_report_matches_stdout(files, capsys, monkeypatch, tmp_path):
    output = tmp_path / "report.json"
    code, report = run(files, capsys, monkeypatch, ["--output", str(output)])
    assert code == 0
    assert report["status"] == "pass"
    assert report == json.loads(output.read_bytes())


@pytest.mark.parametrize(
    "extra",
    [
        ["--now", "2099-01-01"],
        ["--lef", "x"],
        ["--unknown", "PRIVATE_TEST_SECRET"],
        ["--left", "PRIVATE_TEST_SECRET"],
        ["--left=PRIVATE_TEST_SECRET"],
        ["--output", "a", "--output", "b"],
    ],
)
def test_invalid_arguments_are_safe_typed_reports(files, capsys, monkeypatch, extra):
    code, report = run(files, capsys, monkeypatch, extra)
    assert code == 1
    assert report["issues"][0]["code"] == "arguments"
    assert not report["cells"]


def test_missing_arguments_are_typed(capsys):
    assert cli().main([]) == 1
    assert json.loads(capsys.readouterr().out)["issues"][0]["code"] == "arguments"


def test_output_alias_never_changes_input(files, capsys, monkeypatch):
    target = files[1]["left"]
    before = target.read_bytes()
    code, report = run(files, capsys, monkeypatch, ["--output", str(target)])
    assert code == 1
    assert report["issues"][0]["code"] == "output_conflict"
    assert target.read_bytes() == before


def test_existing_output_is_reserved_before_reads(files, capsys, monkeypatch, tmp_path):
    target = tmp_path / "existing.json"
    private_json(target, {"keep": True})
    files[1]["plan"].unlink()
    code, report = run(files, capsys, monkeypatch, ["--output", str(target)])
    assert code == 1
    assert report["issues"][0]["code"] == "output_conflict"
    assert json.loads(target.read_bytes()) == {"keep": True}


@pytest.mark.parametrize(
    "name", ["left", "right", "plan", "identity-map", "left-grant"]
)
def test_duplicate_json_keys_never_reach_comparison(files, capsys, monkeypatch, name):
    target = files[1][name]
    target.unlink()
    from dragon_radar.owner.files import secure_output

    with secure_output(target) as stream:
        stream.write(b'{"PRIVATE_TEST_SECRET":1,"PRIVATE_TEST_SECRET":2}')
    code, report = run(files, capsys, monkeypatch)
    assert code == 1
    assert not report["cells"]


def test_missing_evidence_is_blocked(files, capsys, monkeypatch):
    proof = files[0]["plan"]["independence_evidence_sha256"]
    (files[0]["root"] / f"{proof}.json").unlink()
    code, report = run(files, capsys, monkeypatch)
    assert code == 2
    assert report["status"] == "blocked"


def test_external_grant_mismatch_is_blocked(files, capsys, monkeypatch):
    path = files[1]["right-grant"]
    grant = json.loads(path.read_bytes())
    grant["reference"] = "different-authority"
    path.unlink()
    private_json(path, grant)
    code, report = run(files, capsys, monkeypatch)
    assert code == 2
    assert not report["cells"]


def test_metadata_budget_is_shared(files, capsys, monkeypatch):
    mod = cli()
    monkeypatch.setattr(mod, "MAX_METADATA_BYTES", 300)
    code, report = run(files, capsys, monkeypatch)
    assert code == 1
    assert report["issues"][0]["code"] == "budget"


def test_serialized_report_budget_never_leaves_output(
    files, capsys, monkeypatch, tmp_path
):
    mod = cli()
    monkeypatch.setattr(mod, "MAX_REPORT_BYTES", 10)
    output = tmp_path / "too-big.json"
    code, report = run(files, capsys, monkeypatch, ["--output", str(output)])
    assert code == 1
    assert report["issues"][0]["code"] == "budget"
    assert not output.exists()


def test_durable_expiry_rolls_back_output(files, capsys, monkeypatch, tmp_path):
    mod = cli()
    from dragon_radar.owner.files import secure_output

    def expiring_output(path, *, on_durable):
        def expire():
            monkeypatch.setattr(
                mod, "utc_now", lambda: CHECK_TIME + timedelta(days=1000)
            )
            on_durable()

        return secure_output(path, on_durable=expire)

    monkeypatch.setattr(mod, "secure_output", expiring_output)
    output = tmp_path / "expired.json"
    code, report = run(files, capsys, monkeypatch, ["--output", str(output)])
    assert code == 2
    assert report["issues"][0]["code"] == "storage_denied"
    assert not output.exists()
    assert not report["cells"]


def test_actual_module_subprocess_safe_failure(files):
    cli()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dragon_radar.ingestion.comparison_cli",
            "--now",
            "PRIVATE_TEST_SECRET",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        timeout=30,
    )
    assert result.returncode == 1
    assert not result.stderr
    report = json.loads(result.stdout)
    assert report["issues"][0]["code"] == "arguments"
    assert "PRIVATE_TEST_SECRET" not in result.stdout


def test_fsync_failure_rolls_back_without_secrets(files, capsys, monkeypatch, tmp_path):
    import os

    def fail(fd):
        raise OSError("PRIVATE_TEST_SECRET")

    monkeypatch.setattr(os, "fsync", fail)
    output = tmp_path / "fsync.json"
    code, report = run(files, capsys, monkeypatch, ["--output", str(output)])
    assert code == 1
    assert report["issues"][0]["code"] == "output_unavailable"
    assert not output.exists()


def test_archive_row_budget_precedes_verifier(files, capsys, monkeypatch):
    monkeypatch.setattr(cli(), "MAX_ROWS", 1)
    code, report = run(files, capsys, monkeypatch)
    assert code == 1
    assert report["issues"][0]["code"] == "budget"
    assert not report["cells"]


def test_hardlinked_input_is_not_loaded(files, capsys, monkeypatch, tmp_path):
    import os

    os.link(files[1]["plan"], tmp_path / "hardlink.json")
    code, report = run(files, capsys, monkeypatch)
    assert code == 1
    assert not report["cells"]


def test_output_cannot_alias_digest_addressed_evidence(files, capsys, monkeypatch):
    evidence = (
        files[0]["root"] / f"{files[0]['plan']['independence_evidence_sha256']}.json"
    )
    before = evidence.read_bytes()
    code, report = run(files, capsys, monkeypatch, ["--output", str(evidence)])
    assert code == 1
    assert report["issues"][0]["code"] == "output_conflict"
    assert evidence.read_bytes() == before


def test_input_parent_traversal_is_rejected(files, capsys, monkeypatch):
    path = files[1]["plan"]
    files[2][5] = str(path.parent / ".." / path.parent.name / path.name)
    code, report = run(files, capsys, monkeypatch)
    assert code == 1
    assert not report["cells"]


def test_actual_module_subprocess_valid_test_bundle(files, tmp_path):
    output = tmp_path / "actual-report.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dragon_radar.ingestion.comparison_cli",
            *files[2],
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        timeout=30,
    )
    assert result.returncode == 0
    assert not result.stderr
    report = json.loads(result.stdout)
    assert report["test_only"] is True
    assert report["production_approved"] is False
    assert report == json.loads(output.read_bytes())
