import importlib
import json
from hashlib import sha256

import pytest
from test_production_trust import protect


def module():
    assert importlib.util.find_spec("dragon_radar.production.cli"), (
        "production CLI missing"
    )
    return importlib.import_module("dragon_radar.production.cli")


def test_missing_user_paths_write_blocked_diagnostics(tmp_path, capsys):
    output = tmp_path / "report.json"
    result = module().main(["--workspace", str(tmp_path), "--output", str(output)])
    assert result == 2
    report = json.loads(output.read_text())
    assert report["input_availability"]["suppliers"] == "missing"
    assert report["input_availability"]["proofs"] == "missing"
    assert all(x["status"] == "blocked" for x in report["gates"].values())
    assert str(tmp_path) not in capsys.readouterr().out


def test_evidence_directory_cannot_install_its_own_trust(tmp_path):
    proofs = tmp_path / "docs" / "proofs"
    proofs.mkdir(parents=True)
    (proofs / "trust-policy.json").write_text('{"issuers":{"self":{}}}')
    output = tmp_path / "report.json"
    assert module().main(["--workspace", str(tmp_path), "--output", str(output)]) == 2
    report = json.loads(output.read_text())
    assert report["input_availability"]["trust_policy"] == "not_configured"
    assert all(x["status"] == "blocked" for x in report["gates"].values())


def test_source_fingerprint_changes_when_kernel_changes(tmp_path):
    source = tmp_path / "src" / "dragon_radar"
    source.mkdir(parents=True)
    file = source / "kernel.py"
    file.write_text("FACTOR_METHOD = 'v1'\n")
    first = module().source_identity(tmp_path)
    file.write_text("FACTOR_METHOD = 'v2'\n")
    assert first != module().source_identity(tmp_path)


def test_source_fingerprint_does_not_bind_generated_reports(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "kernel.py").write_text("METHOD = 'v1'\n")
    first = module().source_identity(tmp_path)
    (tmp_path / "report.json").write_text('{"status":"pass"}')
    assert first == module().source_identity(tmp_path)


@pytest.mark.parametrize(
    "name", ["public.Dockerfile", "private.Caddyfile", "routing.conf"]
)
def test_source_fingerprint_binds_deployment_inputs(tmp_path, name):
    deployment = tmp_path / "deploy"
    deployment.mkdir()
    file = deployment / name
    file.write_text("configuration-v1\n")
    first = module().source_identity(tmp_path)
    file.write_text("configuration-v2\n")
    assert first != module().source_identity(tmp_path)


@pytest.mark.parametrize(
    "name",
    [
        ".dockerignore",
        ".python-version",
        ".gitattributes",
        "compose.reset.yaml",
        "compose.dev.yaml",
        "compose.workbench.yaml",
    ],
)
def test_source_fingerprint_binds_root_build_configuration(tmp_path, name):
    file = tmp_path / name
    file.write_text("configuration-v1\n")
    first = module().source_identity(tmp_path)
    file.write_text("configuration-v2\n")
    assert first != module().source_identity(tmp_path)


def test_current_source_identity_rejects_installed_layout_even_with_partial_inputs(
    tmp_path, monkeypatch
):
    library = tmp_path / "Lib"
    installed = library / "site-packages" / "dragon_radar" / "production" / "cli.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("installed candidate code\n")
    (library / "deploy").mkdir()
    (library / "deploy" / "public.Dockerfile").write_text("partial inventory\n")
    monkeypatch.setattr(module(), "__file__", str(installed))
    with pytest.raises(ValueError, match="source layout"):
        module().current_source_identity(library)


def test_current_source_identity_binds_the_loaded_checkout():
    assert module().current_source_identity(module().CODE_ROOT) == (
        module().source_identity(module().CODE_ROOT)
    )


def test_current_source_identity_requires_complete_build_and_test_inputs(
    tmp_path, monkeypatch
):
    source = tmp_path / "src" / "dragon_radar" / "production" / "cli.py"
    source.parent.mkdir(parents=True)
    source.write_text("source without build or test inputs\n")
    monkeypatch.setattr(module(), "__file__", str(source))
    with pytest.raises(ValueError, match="source layout"):
        module().current_source_identity(tmp_path)


@pytest.mark.parametrize("scopes", [[], None, "scope"])
def test_malformed_policy_scope_returns_safe_configuration_failure(tmp_path, scopes):
    policy = {
        "schema": "dragon-radar-production-trust/v1",
        "issuers": {
            "test-only": {
                "keys": {},
                "gates": ["real_input"],
                "scopes": scopes,
                "approvers": ["independent"],
                "revoked": False,
            }
        },
        "revoked_approval_ids": [],
        "revoked_evidence_ids": [],
    }
    raw = json.dumps(policy).encode()
    path = tmp_path / "policy.json"
    path.write_bytes(raw)
    protect(tmp_path)
    output = tmp_path / "report.json"
    assert (
        module().main(
            [
                "--workspace",
                str(tmp_path),
                "--trust-policy",
                str(path),
                "--trust-policy-sha256",
                sha256(raw).hexdigest(),
                "--output",
                str(output),
            ]
        )
        == 1
    )
    report = json.loads(output.read_bytes())
    assert report["overall_status"] == "fail"
    assert all(
        entry["code"] == "invalid_configuration" for entry in report["gates"].values()
    )


def test_cli_source_mismatch_returns_invalid_evidence_exit(tmp_path):
    policy = {
        "schema": "dragon-radar-production-trust/v1",
        "issuers": {},
        "revoked_approval_ids": [],
        "revoked_evidence_ids": [],
    }
    raw = json.dumps(policy).encode()
    assets = {
        "policy.json": raw,
        "candidate.json": json.dumps(
            {
                "versions": {
                    "code": "source-sha256:" + "f" * 64,
                    "dataset": "d1",
                    "rules": "r1",
                    "model": "m1",
                    "prompt": "p1",
                    "retrieval": "r1",
                },
                "methods": {"FAC-R01": "v1"},
                "regulation_rules": [],
                "rule_scope": {
                    "exchange": "TEST",
                    "board": "main",
                    "security_status": "regular",
                    "trade_date": "2026-11-16",
                },
                "official_source_hosts": ["exchange.example"],
            }
        ).encode(),
        "context.json": json.dumps(
            {
                "subject": "owner",
                "purpose": "research",
                "target": "local",
                "audience": "owner",
                "content": ["overview"],
                "data": ["market"],
            }
        ).encode(),
        "index.json": json.dumps(
            {
                "schema": "dragon-radar-production-index/v1",
                "evidence": {},
            }
        ).encode(),
    }
    for name, content in assets.items():
        (tmp_path / name).write_bytes(content)
    protect(tmp_path)
    output = tmp_path / "report.json"
    assert (
        module().main(
            [
                "--workspace",
                str(tmp_path),
                "--proofs",
                str(tmp_path),
                "--trust-policy",
                str(tmp_path / "policy.json"),
                "--trust-policy-sha256",
                sha256(raw).hexdigest(),
                "--output",
                str(output),
            ]
        )
        == 1
    )
    report = json.loads(output.read_bytes())
    assert report["overall_status"] == "fail"
    assert all(
        entry["code"] == "candidate_source_mismatch"
        for entry in report["gates"].values()
    )
