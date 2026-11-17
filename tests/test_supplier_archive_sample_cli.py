"""End-to-end offline archive diagnostics with explicit empty sample context."""

import importlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from test_supplier_collection import NOW, client, grant
from test_supplier_collection import request as collection_request
from test_supplier_collection import root as root
from test_supplier_collection_cli import write_private
from test_supplier_sample import sample_payload

from dragon_radar.domain.models import ExchangeCalendar, SourceGrant
from dragon_radar.ingestion.supplier_collection import collect
from dragon_radar.ingestion.supplier_transport import HttpResponse

MODULE = "dragon_radar.ingestion.archive_sample_cli"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def inputs(root, request):
    archive = root / "archive"
    archive.mkdir(mode=0o700)
    if os.name == "nt":
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                ". $env:SUPPLIER_TEST_HELPER; "
                "Initialize-PrivateDirectory $env:SUPPLIER_TEST_ROOT | Out-Null",
            ],
            env=dict(
                os.environ,
                SUPPLIER_TEST_HELPER=str(ROOT / "scripts/private-common.ps1"),
                SUPPLIER_TEST_ROOT=str(archive),
            ),
            check=True,
            capture_output=True,
            timeout=20,
        )
    sample = sample_payload()
    sample["batch"].update(
        source_id="ifind",
        source_version="v1",
        provenance="retrospective",
        as_of=NOW.isoformat(),
        grant=grant().model_dump(mode="json") | {"local_display": True},
    )
    bars = sample["batch"].pop("bars")
    states = sample["batch"].pop("security_states")
    for row in (*bars, *states):
        row.pop("source_id")
        row.pop("source_version")
        row.pop("available_at")
    profile = {
        "schema": "dragon-radar-supplier-mapping/v1",
        "profile_id": "explicit-profile",
        "source_id": "ifind",
        "source_version": "v1",
        "bar_fields": {key: key for key in bars[0]},
        "state_fields": {key: key for key in states[0]},
    }
    profile_raw = json.dumps(profile).encode()
    calendar = ExchangeCalendar.model_validate(sample["batch"]["calendar"])
    scenario = getattr(request, "param", None)
    plan = collection_request(
        profile_sha256=sha256(profile_raw).hexdigest(),
        calendar=calendar,
        sessions=calendar.sessions,
        securities=("600000.SH",),
        max_sessions=10 if scenario == "multi_chunk" else 100,
    )
    if scenario == "bad_row":
        bars[0]["close"] = "SECRET_VALUE"
    if scenario == "missing_history":
        bars.pop(0)
    receipt = NOW
    if scenario == "multi_chunk":
        bars[0]["close"] = "10.00000000000000000000000001"
        sample["batch"]["as_of"] = (NOW + timedelta(seconds=3)).isoformat()

    def send(http_request, *_):
        nonlocal receipt
        selected_bars, selected_states = bars, states
        if scenario == "multi_chunk":
            dates = json.loads(http_request.data)["dates"]
            selected_bars = [row for row in bars if row["trade_date"] in dates]
            selected_states = states if receipt == NOW else []
            receipt += timedelta(seconds=1)
        return HttpResponse(
            200,
            json.dumps(
                {
                    "errorcode": 0,
                    "data": {"bars": selected_bars, "states": selected_states},
                }
            ).encode(),
        )

    result = collect(
        plan,
        grant=SourceGrant.model_validate(sample["batch"]["grant"]),
        archive_root=archive,
        clock=lambda: receipt,
        transport=client(send),
    )
    sample["batch"].update(bars=[], security_states=[], limit_up_events=[])
    paths = {"archive-root": archive}
    for key, value in (
        ("request", plan.model_dump(mode="json")),
        ("grant", sample["batch"]["grant"]),
        ("mapping", profile),
        (
            "extraction",
            {"bar_path": ["data", "bars"], "state_path": ["data", "states"]},
        ),
        ("sample", sample),
    ):
        paths[key] = root / f"{key}.json"
        write_private(paths[key], value)
    return paths, result


def update(inputs, key, change):
    path = inputs[0][key]
    value = json.loads(path.read_bytes())
    change(value)
    path.unlink()
    write_private(path, value)


def module():
    assert importlib.util.find_spec(MODULE) is not None, (
        "offline archive sample CLI missing"
    )
    return importlib.import_module(MODULE)


def run(inputs, capsys, *extras):
    args = [item for key, path in inputs[0].items() for item in (f"--{key}", str(path))]
    code = module().main(args + ["--now", NOW.isoformat(), *map(str, extras)])
    output = capsys.readouterr()
    assert not output.err
    assert "SECRET_VALUE" not in output.out
    assert "PRIVATE_TEST_TOKEN" not in output.out
    return code, json.loads(output.out)


def issue_codes(report):
    return {item["code"] for item in report["issues"]}


def test_complete_archive_passes_engineering_without_network_or_mutation(
    inputs, capsys, monkeypatch
):
    from dragon_radar.ingestion import supplier_transport

    monkeypatch.delenv("IFIND_ACCESS_TOKEN")
    monkeypatch.setattr(
        supplier_transport.IfindTransport,
        "__init__",
        lambda *_a, **_k: pytest.fail("transport constructed"),
    )
    before = {p.name: p.read_bytes() for p in inputs[0]["archive-root"].iterdir()}
    code, report = run(inputs, capsys)
    assert code == 0, report
    assert report["coverage"]["valid_observations"] == 21
    assert report["collection_fingerprint"] == inputs[1].fingerprint
    assert report["chunks"] == 1
    assert report["provenance"] == "retrospective"
    assert report["historical_pit_status"] == "unverified"
    assert report["production_approved"] is False
    assert all(
        len(report[f"{key}_sha256"]) == 64
        for key in ("request", "grant", "profile", "sample_file", "extraction")
    )
    assert before == {
        p.name: p.read_bytes() for p in inputs[0]["archive-root"].iterdir()
    }
    assert "600000" not in json.dumps(report)


@pytest.mark.parametrize("inputs", ["missing_history"], indirect=True)
def test_archive_missing_history_is_blocked_not_partial_success(inputs, capsys):
    code, report = run(inputs, capsys)
    assert code == 2
    assert "missing_history" in issue_codes(report)


@pytest.mark.parametrize("inputs", ["bad_row"], indirect=True)
def test_bad_archive_row_retains_issues_and_never_calls_acceptance(
    inputs, capsys, monkeypatch
):
    monkeypatch.setattr(
        importlib.import_module("dragon_radar.ingestion.archive_sample"),
        "evaluate_sample",
        lambda *_a, **_k: pytest.fail("bad rows reached acceptance"),
    )
    code, report = run(inputs, capsys)
    assert code == 1
    assert "normalization_failed" in issue_codes(report)
    assert report["normalization_issues"][0]["row_index"] == 0


@pytest.mark.parametrize("field", ["source_id", "source_version", "calendar_version"])
def test_sample_identity_cannot_override_collection(inputs, capsys, field):
    update(inputs, "sample", lambda p: p["batch"].update({field: "SECRET_VALUE"}))
    code, report = run(inputs, capsys)
    assert code == 1
    assert "sample_binding_mismatch" in issue_codes(report)


def test_sample_grant_cannot_replace_archived_authorization(inputs, capsys):
    update(
        inputs, "sample", lambda p: p["batch"]["grant"].update(reference="SECRET_VALUE")
    )
    code, report = run(inputs, capsys)
    assert code == 1
    assert "sample_binding_mismatch" in issue_codes(report)


def test_sample_calendar_must_equal_request_not_just_version(inputs, capsys):
    update(inputs, "sample", lambda p: p["batch"]["calendar"]["sessions"].pop(0))
    code, report = run(inputs, capsys)
    assert code == 1
    assert "sample_binding_mismatch" in issue_codes(report)


def test_sample_trade_date_must_be_collection_last_session(inputs, capsys):
    update(
        inputs,
        "sample",
        lambda p: p["batch"].update(trade_date=p["batch"]["calendar"]["sessions"][-2]),
    )
    assert run(inputs, capsys)[0] == 1


@pytest.mark.parametrize("field", ["bars", "security_states", "limit_up_events"])
def test_nonempty_sample_records_are_rejected(inputs, capsys, field):
    source = sample_payload()["batch"]
    row = (
        source[field][0]
        if source[field]
        else {
            "security_id": "A",
            "trade_date": source["trade_date"],
            "available_at": NOW.isoformat(),
            "source_id": "ifind",
            "source_version": "v1",
            "is_limit_up": True,
        }
    )
    update(inputs, "sample", lambda p: p["batch"].update({field: [row]}))
    code, report = run(inputs, capsys)
    assert code == 1
    assert "sample_records_not_empty" in issue_codes(report)


def test_contemporaneous_provenance_cannot_be_claimed(inputs, capsys):
    update(inputs, "sample", lambda p: p["batch"].update(provenance="contemporaneous"))
    code, report = run(inputs, capsys)
    assert code == 1
    assert "sample_binding_mismatch" in issue_codes(report)


def test_as_of_is_not_backdated_or_silently_rewritten(inputs, capsys):
    update(
        inputs,
        "sample",
        lambda p: p["batch"].update(as_of=(NOW - timedelta(seconds=1)).isoformat()),
    )
    code, report = run(inputs, capsys)
    assert code == 1
    assert "receipt_after_as_of" in issue_codes(report)


def test_complete_archive_does_not_imply_complete_supplier_pages(inputs, capsys):
    update(inputs, "sample", lambda p: p["batch"].update(complete_pages=False))
    code, report = run(inputs, capsys)
    assert code == 2
    assert "incomplete_pages" in issue_codes(report)


def test_missing_benchmark_is_blocked(inputs, capsys):
    update(inputs, "sample", lambda p: p.update(benchmark_closes={}))
    code, report = run(inputs, capsys)
    assert code == 2
    assert "missing_benchmark" in issue_codes(report)


def test_expired_grant_is_blocked(inputs, capsys):
    code, report = run(inputs, capsys, "--now", (NOW + timedelta(days=2)).isoformat())
    assert code == 2
    assert "storage_denied" in issue_codes(report)


def test_corrupt_archive_is_safe_failure(inputs, capsys):
    (inputs[0]["archive-root"] / inputs[1].chunks[0].raw_path).unlink()
    code, report = run(inputs, capsys)
    assert code == 1
    assert "checkpoint_mismatch" in issue_codes(report)


def test_profile_actual_bytes_are_bound(inputs, capsys):
    path = inputs[0]["mapping"]
    raw = path.read_bytes()
    path.unlink()
    from dragon_radar.owner.files import secure_output

    with secure_output(path) as stream:
        stream.write(raw + b"\n")
    code, report = run(inputs, capsys)
    assert code == 1
    assert "mapping_binding_mismatch" in issue_codes(report)


@pytest.mark.parametrize("key", ["request", "grant", "mapping", "sample", "extraction"])
def test_duplicate_json_keys_never_echo_input(inputs, capsys, key):
    path = inputs[0][key]
    path.unlink()
    from dragon_radar.owner.files import secure_output

    with secure_output(path) as stream:
        stream.write(b'{"secret":"SECRET_VALUE","secret":true}')
    code, report = run(inputs, capsys)
    assert code == 1
    assert "invalid_configuration" in issue_codes(report)


@pytest.mark.parametrize("path", [[], ["*"], ["data", True], ["missing"]])
def test_invalid_or_missing_extraction_path_fails(inputs, capsys, path):
    update(inputs, "extraction", lambda p: p.update(bar_path=path))
    assert run(inputs, capsys)[0] == 1


def test_output_is_exclusive_and_matches_stdout(inputs, capsys):
    output = inputs[0]["sample"].parent / "report.json"
    code, report = run(inputs, capsys, "--output", output)
    assert code == 0
    assert json.loads(output.read_bytes()) == report
    original = output.read_bytes()
    code, report = run(inputs, capsys, "--output", output)
    assert code == 1
    assert "output_unavailable" in issue_codes(report)
    assert output.read_bytes() == original


@pytest.mark.parametrize("target", ["sample", "archive-root"])
def test_output_conflicts_are_rejected(inputs, capsys, target):
    path = inputs[0][target]
    if target == "archive-root":
        path = path / "report.json"
    code, report = run(inputs, capsys, "--output", path)
    assert code == 1
    assert "output_conflict" in issue_codes(report)


def test_output_reservation_failure_precedes_archive_read(inputs, capsys, monkeypatch):
    monkeypatch.setattr(
        importlib.import_module("dragon_radar.ingestion.archive_sample"),
        "normalize_collection",
        lambda *_a, **_k: pytest.fail("archive read before output reservation"),
    )
    output = inputs[0]["sample"].parent / "exists.json"
    write_private(output, {})
    assert run(inputs, capsys, "--output", output)[0] == 1


@pytest.mark.parametrize(
    "extra",
    [
        ("--execute", "SECRET_VALUE"),
        ("--now", "SECRET_VALUE"),
        ("--now", "2026-11-16"),
    ],
)
def test_argument_and_clock_failures_are_safe_json(inputs, capsys, extra):
    assert run(inputs, capsys, *extra)[0] == 1


@pytest.mark.parametrize("entry", ["module", "script"])
def test_actual_entry_runs_without_db_or_token(inputs, entry):
    module()
    args = [item for key, path in inputs[0].items() for item in (f"--{key}", str(path))]
    command = (
        ["-m", MODULE]
        if entry == "module"
        else [str(ROOT / "scripts/verify_supplier_archive.py")]
    )
    result = subprocess.run(
        [sys.executable, *command, *args, "--now", NOW.isoformat()],
        cwd=ROOT,
        env={
            k: v
            for k, v in os.environ.items()
            if k not in {"DRAGON_DATABASE_URL", "IFIND_ACCESS_TOKEN"}
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout)["production_approved"] is False


def test_default_clock_rechecks_authorization_during_offline_work(
    inputs, capsys, monkeypatch
):
    times = iter((NOW, NOW + timedelta(days=2)))
    monkeypatch.setattr(module(), "utc_now", lambda: next(times))
    args = [item for key, path in inputs[0].items() for item in (f"--{key}", str(path))]
    code = module().main(args)
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "storage_denied" in issue_codes(report)


def test_explicit_sample_output_roundtrips_and_matches_safe_report(inputs, capsys):
    from dragon_radar.ingestion.sample_archive import read_sample_archive

    target = inputs[0]["sample"].parent / "normalized.json"
    report_path = target.with_name("report.json")
    code, report = run(
        inputs, capsys, "--sample-output", target, "--output", report_path
    )
    assert code == 0, report
    assert report["sample_archive_sha256"] == sha256(target.read_bytes()).hexdigest()
    assert report == json.loads(report_path.read_bytes())
    grant = SourceGrant.model_validate_json(inputs[0]["grant"].read_bytes())
    loaded = read_sample_archive(
        target,
        expected_sha256=report["sample_archive_sha256"],
        grant=grant,
        clock=lambda: NOW,
    )
    assert len(loaded.sample.batch.bars) == 21
    assert (
        report["input_sha256"]
        == sha256(
            json.dumps(
                loaded.sample.model_dump(mode="json", by_alias=True),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        ).hexdigest()
    )
    assert "reference" not in json.dumps(report)


@pytest.mark.parametrize("inputs", ["missing_history", "bad_row"], indirect=True)
def test_unqualified_samples_leave_diagnostic_but_no_sample(inputs, capsys):
    target = inputs[0]["sample"].parent / "normalized.json"
    report_path = target.with_name("report.json")
    code, report = run(
        inputs, capsys, "--sample-output", target, "--output", report_path
    )
    assert code != 0
    assert not target.exists()
    assert json.loads(report_path.read_bytes()) == report
    assert "sample_archive_sha256" not in report


@pytest.mark.parametrize("target_kind", ["sample", "archive", "report"])
def test_sample_output_conflicts_rejected(inputs, capsys, target_kind):
    target = inputs[0]["sample"]
    extras = []
    if target_kind == "archive":
        target = inputs[0]["archive-root"] / "normalized.json"
    elif target_kind == "report":
        target = target.with_name("same.json")
        extras = ["--output", target]
    code, report = run(inputs, capsys, "--sample-output", target, *extras)
    assert code == 1
    assert "output_conflict" in issue_codes(report)


@pytest.mark.parametrize("existing", ["sample", "report"])
def test_both_outputs_reserved_before_any_input_read(
    inputs, capsys, monkeypatch, existing
):
    target = inputs[0]["sample"].parent / "normalized.json"
    report_path = target.with_name("report.json")
    preserved = target if existing == "sample" else report_path
    write_private(preserved, {"original": True})
    before = preserved.read_bytes()
    monkeypatch.setattr(
        module(),
        "_read",
        lambda *_a, **_k: pytest.fail("input read before both reservations"),
    )
    code, report = run(
        inputs, capsys, "--sample-output", target, "--output", report_path
    )
    assert code == 1
    assert "output_unavailable" in issue_codes(report)
    assert preserved.read_bytes() == before
    if existing == "report":
        assert not target.exists()
    else:
        assert not report_path.exists()


@pytest.mark.parametrize("failure_at", [1, 2])
def test_report_or_sample_fsync_failure_removes_sample(
    inputs, capsys, monkeypatch, failure_at
):
    from dragon_radar.owner import files

    original = files.os.fsync
    calls = 0

    def fail_fsync(fd):
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise OSError("SECRET_VALUE")
        return original(fd)

    monkeypatch.setattr(files.os, "fsync", fail_fsync)
    target = inputs[0]["sample"].parent / "normalized.json"
    report_path = target.with_name("report.json")
    code, report = run(
        inputs, capsys, "--sample-output", target, "--output", report_path
    )
    assert code == 1
    assert "output_unavailable" in issue_codes(report)
    assert not target.exists()
    if failure_at == 1:
        assert not report_path.exists()


def test_authorization_expiring_during_sample_write_rolls_back(
    inputs, capsys, monkeypatch
):
    from dragon_radar.owner.files import secure_output

    expired = False
    target = inputs[0]["sample"].parent / "normalized.json"

    @contextmanager
    def output(path, **kwargs):
        with secure_output(path, **kwargs) as stream:

            class ExpiringStream:
                def write(self, content):
                    nonlocal expired
                    count = stream.write(content)
                    if path == target:
                        expired = True
                    return count

            yield ExpiringStream()

    monkeypatch.setattr(module(), "secure_output", output)
    monkeypatch.setattr(
        module(), "utc_now", lambda: NOW + timedelta(days=2) if expired else NOW
    )
    args = [item for key, path in inputs[0].items() for item in (f"--{key}", str(path))]
    code = module().main(args + ["--sample-output", str(target)])
    report = json.loads(capsys.readouterr().out)
    assert code == 2, report
    assert not target.exists()
    assert "storage_denied" in issue_codes(report)


@pytest.mark.parametrize(
    "mode,expire_at",
    [
        ("report", 1),
        ("sample", 1),
        ("both", 1),
        ("both", 2),
    ],
)
def test_current_authorization_covers_final_fsync(
    inputs, capsys, monkeypatch, mode, expire_at
):
    original = os.fsync
    calls = 0
    expired = False

    def expire(fd):
        nonlocal calls, expired
        original(fd)
        calls += 1
        if calls == expire_at:
            expired = True

    monkeypatch.setattr(os, "fsync", expire)
    monkeypatch.setattr(
        module(), "utc_now", lambda: NOW + timedelta(days=2) if expired else NOW
    )
    target = inputs[0]["sample"].parent / "normalized-durable.json"
    report_path = target.with_name("durable-report.json")
    args = [item for key, path in inputs[0].items() for item in (f"--{key}", str(path))]
    if mode in {"sample", "both"}:
        args += ["--sample-output", str(target)]
    if mode in {"report", "both"}:
        args += ["--output", str(report_path)]
    code = module().main(args)
    output = capsys.readouterr()
    assert not output.err
    report = json.loads(output.out)
    assert code == 2, report
    assert issue_codes(report) == {"storage_denied"}
    assert not target.exists()
    if mode == "both" and expire_at == 2:
        # The prior durable report is historical diagnostics, not a completion flag.
        prior = json.loads(report_path.read_bytes())
        assert prior["status"] == "pass"
        assert "sample_archive_sha256" in prior
    else:
        assert not report_path.exists()
