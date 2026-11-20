"""Independent private sample verification, without the original collection."""

import importlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from test_supplier_archive_sample_cli import inputs as inputs
from test_supplier_collection import NOW
from test_supplier_collection import root as root
from test_supplier_sample_archive import evaluate, save_raw

from dragon_radar.ingestion import sample_archive
from dragon_radar.owner.files import secure_output
from dragon_radar.production.trust import canonical_json

MODULE = "dragon_radar.ingestion.sample_archive_cli"
ROOT = Path(__file__).resolve().parents[1]


def module():
    assert importlib.util.find_spec(MODULE) is not None, "normalized verifier missing"
    return importlib.import_module(MODULE)


@pytest.fixture
def saved(inputs):
    package = evaluate(inputs).archive
    path = inputs[0]["sample"].parent / "normalized.json"
    digest = sample_archive.write_sample_archive(path, package, clock=lambda: NOW)
    return path, digest, inputs[0]["grant"], package


def run(saved, capsys, monkeypatch, *extras):
    mod = module()
    monkeypatch.setattr(mod, "utc_now", lambda: NOW)
    code = mod.main(
        [
            "--sample",
            str(saved[0]),
            "--sha256",
            saved[1],
            "--grant",
            str(saved[2]),
            *map(str, extras),
        ]
    )
    output = capsys.readouterr()
    assert not output.err
    for private in ("SECRET_VALUE", "PRIVATE_TEST_TOKEN", "600000", str(saved[0])):
        assert private not in output.out
    return code, json.loads(output.out)


def replace(path, raw):
    path.unlink()
    save_raw(path, raw)


def mutate(saved, change):
    value = json.loads(saved[0].read_bytes())
    change(value)
    raw = canonical_json(value)
    replace(saved[0], raw)
    return saved[0], sha256(raw).hexdigest(), *saved[2:]


def test_standalone_pass_reuses_quality_once_without_network_or_database(
    saved, capsys, monkeypatch
):
    original = sample_archive.evaluate_sample
    calls = []

    def quality(request, *, now):
        calls.append(now)
        return original(request, now=now)

    monkeypatch.setattr(sample_archive, "evaluate_sample", quality)
    from dragon_radar.ingestion.supplier_transport import IfindTransport

    monkeypatch.setattr(
        IfindTransport, "__init__", lambda *_a, **_k: pytest.fail("network")
    )
    monkeypatch.delenv("IFIND_ACCESS_TOKEN")
    monkeypatch.delenv("DRAGON_DATABASE_URL", raising=False)
    before = saved[0].read_bytes()
    output = saved[0].with_name("report.json")
    code, report = run(saved, capsys, monkeypatch, "--output", output)
    assert code == 0, report
    assert calls == [NOW]
    assert saved[0].read_bytes() == before
    assert json.loads(output.read_bytes()) == report
    assert report["sample_archive_sha256"] == saved[1]
    assert report["coverage"]["valid_observations"] == 21
    assert report["provenance"] == "retrospective"
    assert report["historical_pit_status"] == "unverified"
    assert report["production_approved"] is False
    assert report["kind"] == "test"
    assert report["test_only"] is True
    assert "reference" not in json.dumps(report)


@pytest.mark.parametrize(
    "change,expected",
    [
        (lambda p: p["sample"]["batch"]["bars"].pop(0), 2),
        (lambda p: p["sample"].update(benchmark_closes={}), 2),
        (lambda p: p["sample"]["batch"].update(complete_pages=False), 2),
        (lambda p: p["sample"]["batch"]["bars"][0].update(close="-1"), 1),
        (lambda p: p.update(production_approved=True), 1),
        (lambda p: p["lineage"]["chunks"][0].update(index=3), 1),
    ],
)
def test_unqualified_or_corrupt_sample_fails_closed(
    saved, capsys, monkeypatch, change, expected
):
    saved = mutate(saved, change)
    code, report = run(saved, capsys, monkeypatch)
    assert code == expected
    assert report["status"] != "pass"
    with pytest.raises(sample_archive.SampleArchiveError):
        sample_archive.read_sample_archive(
            saved[0],
            expected_sha256=saved[1],
            grant=saved[3].sample.batch.grant,
            clock=lambda: NOW,
        )


@pytest.mark.parametrize(
    "kind", ["digest", "canonical", "duplicate", "budget", "empty"]
)
def test_integrity_failures(saved, capsys, monkeypatch, kind):
    if kind == "digest":
        saved = saved[0], "0" * 64, *saved[2:]
    else:
        raw = {
            "canonical": saved[0].read_bytes() + b"\n",
            "duplicate": b'{"secret":"SECRET_VALUE","secret":true}',
            "budget": b" " * (sample_archive.MAX_ARCHIVE_BYTES + 1),
            "empty": b"",
        }[kind]
        replace(saved[0], raw)
        saved = saved[0], sha256(raw).hexdigest(), *saved[2:]
    assert run(saved, capsys, monkeypatch)[0] == 1


@pytest.mark.parametrize("phase", ["before", "during", "changed"])
def test_authorization_is_current_and_identical(saved, capsys, monkeypatch, phase):
    mod = module()
    if phase == "changed":
        value = json.loads(saved[2].read_bytes())
        value["reference"] = "SECRET_VALUE"
        replace(saved[2], canonical_json(value))
        assert run(saved, capsys, monkeypatch)[0] == 1
        return
    times = iter((NOW, NOW + timedelta(days=2)))
    monkeypatch.setattr(
        mod,
        "utc_now",
        (lambda: NOW + timedelta(days=2)) if phase == "before" else lambda: next(times),
    )
    code = mod.main(
        [
            "--sample",
            str(saved[0]),
            "--sha256",
            saved[1],
            "--grant",
            str(saved[2]),
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert report["issues"][0]["code"] == "storage_denied"


def test_existing_output_precedes_any_input_read(saved, capsys, monkeypatch):
    mod = module()
    monkeypatch.setattr(
        mod, "read_asset", lambda *_a, **_k: pytest.fail("read before reservation")
    )
    output = saved[0].with_name("exists.json")
    save_raw(output, b"preserved")
    assert run(saved, capsys, monkeypatch, "--output", output)[0] == 1
    assert output.read_bytes() == b"preserved"


@pytest.mark.parametrize("index", [0, 2])
def test_output_input_conflict(saved, capsys, monkeypatch, index):
    before = saved[index].read_bytes()
    code, report = run(saved, capsys, monkeypatch, "--output", saved[index])
    assert code == 1
    assert report["issues"][0]["code"] == "output_conflict"
    assert saved[index].read_bytes() == before


@pytest.mark.parametrize("failure", ["write", "fsync"])
def test_output_failure_removes_success_report(saved, capsys, monkeypatch, failure):
    mod = module()
    if failure == "fsync":
        monkeypatch.setattr(
            os, "fsync", lambda *_: (_ for _ in ()).throw(OSError("SECRET_VALUE"))
        )
    else:

        @contextmanager
        def broken(path, **kwargs):
            with secure_output(path, **kwargs):

                class Stream:
                    def write(self, _content):
                        raise OSError("SECRET_VALUE")

                yield Stream()

        monkeypatch.setattr(mod, "secure_output", broken)
    output = saved[0].with_name("report.json")
    code, report = run(saved, capsys, monkeypatch, "--output", output)
    assert code == 1
    assert report["issues"][0]["code"] == "output_unavailable"
    assert not output.exists()


@pytest.mark.parametrize(
    "extras",
    [
        ("--sha256", "A" * 64),
        ("--sha256", "SECRET_VALUE"),
        ("--now", NOW.isoformat()),
        ("--execute",),
        ("--sam", "SECRET_VALUE"),
    ],
)
def test_argument_errors_are_safe(saved, capsys, monkeypatch, extras):
    assert run(saved, capsys, monkeypatch, *extras)[0] == 1


@pytest.mark.parametrize("which", [0, 2])
@pytest.mark.parametrize("kind", ["hardlink", "symlink", "public", "missing"])
def test_unsafe_inputs_are_rejected(saved, capsys, monkeypatch, which, kind):
    path = saved[which]
    if kind == "hardlink":
        os.link(path, path.with_name("alias.json"))
    elif kind == "symlink":
        alias = path.with_name("alias.json")
        if os.name == "nt":
            linked = path.parent / "linked"
            subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "New-Item -ItemType Junction -Path $env:SUPPLIER_LINK "
                    "-Target $env:SUPPLIER_TARGET | Out-Null",
                ],
                env=dict(
                    os.environ,
                    SUPPLIER_LINK=str(linked),
                    SUPPLIER_TARGET=str(path.parent),
                ),
                check=True,
                capture_output=True,
                timeout=20,
            )
            alias = linked / path.name
        else:
            os.symlink(path, alias)
        saved = tuple(
            alias if index == which else item for index, item in enumerate(saved)
        )
    elif kind == "missing":
        path.unlink()
    elif os.name == "nt":
        subprocess.run(
            ["icacls.exe", str(path), "/grant", "*S-1-1-0:(R)"],
            check=True,
            capture_output=True,
            timeout=20,
        )
    else:
        path.chmod(0o644)
    assert run(saved, capsys, monkeypatch)[0] == 1


@pytest.mark.parametrize(
    "raw",
    [
        b'{"reference":"SECRET_VALUE","reference":true}',
        b'{"reference":"SECRET_VALUE"}',
        b" " * (4 * 1024 * 1024 + 1),
    ],
    ids=["duplicate", "invalid", "budget"],
)
def test_grant_parse_and_budget_failures(saved, capsys, monkeypatch, raw):
    replace(saved[2], raw)
    assert run(saved, capsys, monkeypatch)[0] == 1


def test_blocked_report_is_durable(saved, capsys, monkeypatch):
    saved = mutate(saved, lambda p: p["sample"].update(benchmark_closes={}))
    output = saved[0].with_name("blocked.json")
    code, report = run(saved, capsys, monkeypatch, "--output", output)
    assert code == 2
    assert "missing_benchmark" in {i["code"] for i in report["issues"]}
    assert json.loads(output.read_bytes()) == report


def test_supplier_kind_is_preserved(saved, capsys, monkeypatch):
    saved = mutate(saved, lambda p: p["sample"].update(kind="supplier_sample"))
    code, report = run(saved, capsys, monkeypatch)
    assert code == 0
    assert report["kind"] == "supplier_sample"
    assert report["test_only"] is False


def test_invalid_internal_clock_fails_safely(saved, capsys, monkeypatch):
    mod = module()
    monkeypatch.setattr(mod, "utc_now", lambda: datetime(2026, 11, 17))
    code = mod.main(
        ["--sample", str(saved[0]), "--sha256", saved[1], "--grant", str(saved[2])]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["issues"][0]["code"] == "operation_time"


def test_missing_required_arguments_produce_safe_json(capsys):
    assert module().main([]) == 1
    output = capsys.readouterr()
    assert not output.err
    assert json.loads(output.out)["issues"][0]["code"] == "arguments"


def test_expiry_during_report_write_rolls_back(saved, capsys, monkeypatch):
    mod = module()
    expired = False

    @contextmanager
    def output(path, **kwargs):
        with secure_output(path, **kwargs) as stream:

            class Stream:
                def write(self, content):
                    nonlocal expired
                    stream.write(content)
                    expired = True

            yield Stream()

    monkeypatch.setattr(mod, "secure_output", output)
    monkeypatch.setattr(
        mod, "utc_now", lambda: NOW + timedelta(days=2) if expired else NOW
    )
    target = saved[0].with_name("expired.json")
    code = mod.main(
        [
            "--sample",
            str(saved[0]),
            "--sha256",
            saved[1],
            "--grant",
            str(saved[2]),
            "--output",
            str(target),
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert report["issues"][0]["code"] == "storage_denied"
    assert not target.exists()


@pytest.mark.parametrize("phase", ["fsync", "failed_diagnostic"])
def test_expiry_at_durable_boundary_removes_any_report(
    saved, capsys, monkeypatch, phase
):
    mod = module()
    expired = False
    if phase == "fsync":
        original = os.fsync

        def expire_fsync(fd):
            nonlocal expired
            original(fd)
            expired = True

        monkeypatch.setattr(os, "fsync", expire_fsync)
    else:
        saved = saved[0], "0" * 64, *saved[2:]

        @contextmanager
        def output(path, **kwargs):
            with secure_output(path, **kwargs) as stream:

                class Stream:
                    def write(self, content):
                        nonlocal expired
                        stream.write(content)
                        expired = True

                yield Stream()

        monkeypatch.setattr(mod, "secure_output", output)
    monkeypatch.setattr(
        mod, "utc_now", lambda: NOW + timedelta(days=2) if expired else NOW
    )
    target = saved[0].with_name("expired-at-boundary.json")
    code = mod.main(
        [
            "--sample",
            str(saved[0]),
            "--sha256",
            saved[1],
            "--grant",
            str(saved[2]),
            "--output",
            str(target),
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 2, report
    assert report["issues"][0]["code"] == "storage_denied"
    assert not target.exists()


def test_current_grant_for_other_source_is_storage_denied(saved, capsys, monkeypatch):
    value = json.loads(saved[2].read_bytes())
    value["source_id"] = "other_supplier"
    replace(saved[2], canonical_json(value))
    code, report = run(saved, capsys, monkeypatch)
    assert code == 2
    assert report["issues"][0]["code"] == "storage_denied"


@pytest.mark.parametrize("entry", ["module", "script"])
def test_actual_entry_uses_current_authorization_without_original_archive(
    saved, inputs, entry
):
    module()
    now = datetime.now(UTC)
    value = saved[3].model_dump(mode="json", by_alias=True)
    value["sample"]["batch"]["grant"]["expires_at"] = (
        now + timedelta(days=1)
    ).isoformat()
    package = sample_archive.SampleArchive.model_validate_json(canonical_json(value))
    raw = sample_archive.archive_bytes(package, clock=lambda: now)
    replace(saved[0], raw)
    replace(
        saved[2], canonical_json(package.sample.batch.grant.model_dump(mode="json"))
    )
    # Delete original collection/configuration, leaving only normalized inputs.
    for path in inputs[0]["archive-root"].iterdir():
        path.unlink()
    inputs[0]["archive-root"].rmdir()
    for key in ("request", "mapping", "extraction", "sample"):
        inputs[0][key].unlink()
    command = (
        ["-m", MODULE]
        if entry == "module"
        else [str(ROOT / "scripts/verify_normalized_sample.py")]
    )
    result = subprocess.run(
        [
            sys.executable,
            *command,
            "--sample",
            str(saved[0]),
            "--sha256",
            sha256(raw).hexdigest(),
            "--grant",
            str(saved[2]),
        ],
        cwd=ROOT,
        env={
            k: v
            for k, v in os.environ.items()
            if k not in {"DRAGON_DATABASE_URL", "IFIND_ACCESS_TOKEN"}
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "pass"
    assert report["test_only"] is True
    assert saved[0].read_bytes() == raw
