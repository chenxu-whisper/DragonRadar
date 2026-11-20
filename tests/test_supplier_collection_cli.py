"""Collection entry point uses synthetic private configs and offline HTTPS."""

import importlib
import json
import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest
from test_supplier_collection import ENDPOINT, NOW, grant, request
from test_supplier_collection import root as root

from dragon_radar.ingestion.supplier_transport import (
    HttpResponse,
    IfindTransport,
    SupplierError,
)
from dragon_radar.owner.files import secure_output


def write_private(path, payload):
    content = json.dumps(payload).encode("utf-8")
    with secure_output(path) as stream:
        stream.write(content)
    return sha256(content).hexdigest()


@pytest.fixture
def configs(root):
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
                SUPPLIER_TEST_HELPER=str(
                    Path(__file__).parents[1] / "scripts/private-common.ps1"
                ),
                SUPPLIER_TEST_ROOT=str(archive),
            ),
            capture_output=True,
            check=True,
            timeout=20,
        )
    profile = {
        "schema": "dragon-radar-supplier-mapping/v1",
        "profile_id": "explicit-profile",
        "source_id": "ifind",
        "source_version": "v1",
    }
    profile_path = root / "mapping.json"
    profile_hash = write_private(profile_path, profile)
    request_path, grant_path = root / "request.json", root / "grant.json"
    write_private(
        request_path,
        request(profile_sha256=profile_hash).model_dump(mode="json"),
    )
    write_private(grant_path, grant().model_dump(mode="json"))
    return {
        "request": request_path,
        "grant": grant_path,
        "mapping": profile_path,
        "archive-root": archive,
    }


@pytest.fixture
def api(monkeypatch):
    name = "dragon_radar.ingestion.collection_cli"
    calls = []

    def send(req, *_):
        calls.append(req)
        return HttpResponse(200, b'{"errorcode":0,"tables":[]}')

    class Entry:
        def main(self, args):
            assert importlib.util.find_spec(name) is not None, (
                "collection CLI is missing"
            )
            module = importlib.import_module(name)
            monkeypatch.setattr(module, "utc_now", lambda: NOW)
            monkeypatch.setattr(
                module,
                "IfindTransport",
                lambda endpoint, **options: IfindTransport(
                    endpoint, send=send, **options
                ),
            )
            return module.main(args)

    return Entry(), calls


def run_cli(api, configs, capsys, *extras):
    args = []
    for key, value in configs.items():
        args.extend([f"--{key}", str(value)])
    code = api[0].main(args + list(map(str, extras)))
    captured = capsys.readouterr()
    assert not captured.err
    assert "PRIVATE_TEST_TOKEN" not in captured.out
    assert "SECRET_VALUE" not in captured.out
    return code, json.loads(captured.out)


def test_default_preflight_never_sends_or_creates_archives(api, configs, capsys):
    code, report = run_cli(api, configs, capsys)
    assert code == 0, report
    assert report["mode"] == "preflight"
    assert report["checkpoint_status"] == "not_checked"
    assert report["planned_chunks"] == 4
    assert report["production_approved"] is False
    assert report["historical_pit_status"] == "unverified"
    assert not api[1]
    assert not list(configs["archive-root"].iterdir())


def test_execute_collects_and_resumes_without_duplicate_requests(api, configs, capsys):
    code, report = run_cli(api, configs, capsys, "--execute", "--request-interval", 0)
    assert code == 0
    assert report["mode"] == "collect"
    assert report["chunks"] == 4
    assert len(api[1]) == 4
    code, again = run_cli(api, configs, capsys, "--execute", "--request-interval", 0)
    assert code == 0
    assert len(api[1]) == 4
    assert again["collection_fingerprint"] == report["collection_fingerprint"]


@pytest.mark.parametrize(
    "field", ["profile_sha256", "profile_id", "source_id", "source_version"]
)
def test_profile_binding_mismatch_fails_before_send(api, configs, capsys, field):
    payload = json.loads(configs["request"].read_bytes())
    payload[field] = "b" * 64 if field == "profile_sha256" else "SECRET_VALUE"
    configs["request"].unlink()
    write_private(configs["request"], payload)
    code, report = run_cli(api, configs, capsys, "--execute")
    assert code == 1
    assert report["code"] == "mapping_binding_mismatch"
    assert not api[1]


def test_expired_storage_grant_blocks_even_preflight(api, configs, capsys):
    payload = grant().model_dump(mode="json")
    payload["expires_at"] = NOW.isoformat()
    configs["grant"].unlink()
    write_private(configs["grant"], payload)
    code, report = run_cli(api, configs, capsys)
    assert code == 2
    assert report["code"] == "storage_denied"
    assert not api[1]


def test_missing_credentials_block_execution(api, configs, capsys, monkeypatch):
    monkeypatch.delenv("IFIND_ACCESS_TOKEN")
    code, report = run_cli(api, configs, capsys, "--execute")
    assert code == 2
    assert report["code"] == "missing_credentials"
    assert not api[1]


@pytest.mark.parametrize(
    "flag,value",
    [
        ("--timeout", "0"),
        ("--timeout", "nan"),
        ("--max-attempts", "6"),
        ("--retry-delay", "-1"),
        ("--request-interval", "inf"),
        ("--token-env", "SECRET_VALUE=bad"),
        ("--now", "SECRET_VALUE"),
        ("--token", "SECRET_VALUE"),
    ],
)
def test_invalid_control_arguments_are_safe_and_never_send(
    api, configs, capsys, flag, value
):
    code, report = run_cli(api, configs, capsys, flag, value)
    assert code == 1
    assert report["status"] == "fail"
    assert not api[1]


def test_duplicate_json_keys_are_not_silently_accepted(api, configs, capsys):
    path = configs["request"]
    path.unlink()
    with secure_output(path) as stream:
        stream.write(b'{"source_id":"x","source_id":"SECRET_VALUE"}')
    code, report = run_cli(api, configs, capsys, "--execute")
    assert code == 1
    assert report["code"] == "invalid_configuration"
    assert not api[1]


@pytest.mark.parametrize("kind", ["existing", "input", "archive"])
def test_output_conflicts_fail_before_send(api, configs, root, capsys, kind):
    if kind == "input":
        target = configs["grant"]
    elif kind == "archive":
        target = configs["archive-root"] / "report.json"
    else:
        target = root / "report.json"
        write_private(target, {"existing": True})
    original = target.read_bytes() if target.exists() else None
    code, _ = run_cli(api, configs, capsys, "--execute", "--output", target)
    assert code == 1
    assert not api[1]
    assert not target.exists() if original is None else target.read_bytes() == original


def test_report_is_private_and_omits_input_values(api, configs, root, capsys):
    target = root / "report.json"
    code, report = run_cli(api, configs, capsys, "--output", target)
    assert code == 0
    assert json.loads(target.read_bytes()) == report
    assert ENDPOINT not in target.read_text(encoding="utf-8")
    assert "EXPLICIT.A" not in target.read_text(encoding="utf-8")
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600


def test_nonprivate_input_rejected_before_send(api, configs, capsys):
    path = configs["grant"]
    if os.name == "nt":
        subprocess.run(
            ["icacls.exe", str(path), "/grant", "*S-1-1-0:(R)"],
            capture_output=True,
            check=True,
            timeout=20,
        )
    else:
        path.chmod(0o644)
    code, report = run_cli(api, configs, capsys, "--execute")
    assert code == 1
    assert report["code"] == "input_unavailable"
    assert not api[1]


def test_missing_output_parent_blocks_before_send(api, configs, root, capsys):
    code, report = run_cli(
        api, configs, capsys, "--execute", "--output", root / "missing" / "report.json"
    )
    assert code == 1
    assert report["code"] == "output_unavailable"
    assert not api[1]


def test_missing_archive_root_blocks_preflight(api, configs, capsys):
    configs["archive-root"].rmdir()
    code, report = run_cli(api, configs, capsys)
    assert code == 1
    assert report["code"] == "archive_error"
    assert not api[1]


def test_missing_private_input_is_safe(api, configs, capsys):
    configs["mapping"].unlink()
    code, report = run_cli(api, configs, capsys)
    assert code == 1
    assert report["code"] == "input_unavailable"
    assert not api[1]


def test_corrupt_checkpoint_fails_before_new_send(api, configs, capsys):
    code, _ = run_cli(api, configs, capsys, "--execute", "--request-interval", 0)
    assert code == 0
    marker = configs["archive-root"] / "chunk-00003.done.json"
    marker.unlink()
    write_private(marker, {"corrupted": "SECRET_VALUE"})
    code, report = run_cli(api, configs, capsys, "--execute", "--request-interval", 0)
    assert code == 1
    assert report["code"] == "checkpoint_mismatch"
    assert len(api[1]) == 4


@pytest.mark.parametrize(
    "error,expected",
    [
        ("authentication_failed", 2),
        ("rate_limited", 2),
        ("server_error", 2),
        ("timeout", 2),
        ("network_error", 2),
        ("invalid_response", 1),
        ("archive_error", 1),
        ("checkpoint_mismatch", 1),
    ],
)
def test_safe_collection_failure_is_written_to_reserved_report(
    api, configs, root, capsys, monkeypatch, error, expected
):
    module = importlib.import_module("dragon_radar.ingestion.collection_cli")

    def fail(*_, **__):
        raise SupplierError(error)

    monkeypatch.setattr(module, "collect", fail)
    target = root / "report.json"
    code, report = run_cli(api, configs, capsys, "--execute", "--output", target)
    assert code == expected
    assert report["code"] == error
    assert json.loads(target.read_bytes()) == report
    assert not api[1]


def test_script_entry_reports_errors_without_database_or_credentials(tmp_path):
    entry = Path(__file__).resolve().parents[1] / "scripts/collect_supplier_sample.py"
    assert entry.exists(), "collection script is missing"
    result = subprocess.run(
        [sys.executable, str(entry), "--token", "SECRET_VALUE"],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
        env={k: v for k, v in os.environ.items() if k != "DRAGON_DATABASE_URL"},
    )
    assert result.returncode == 1
    assert not result.stderr
    assert "SECRET_VALUE" not in result.stdout
    assert json.loads(result.stdout)["code"] == "arguments"
