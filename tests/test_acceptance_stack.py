"""Real Bash lifecycle regressions; Docker/TLS processes are local test doubles."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

FAKE_DOCKER = r"""#!/usr/bin/env python3
import json, os, pathlib, sys
a = sys.argv[1:]
root = pathlib.Path(os.environ["FAKE_ROOT"])
with (root / "calls.jsonl").open("a") as f:
    f.write(json.dumps(a) + "\n")
fault = os.environ.get("FAKE_FAULT", "")
if a[0] == "compose":
    p = a[a.index("--project-name") + 1]
    env = dict(line.split("=", 1) for line in
               pathlib.Path(a[a.index("--env-file") + 1]).read_text().splitlines())
    override = pathlib.Path(a[a.index("-f", a.index("-f") + 1) + 1])
    assert override.name == "compose.override.yaml"
    action = a[a.index("-f", a.index("-f") + 1) + 2:]
    if action[:1] == ["config"]:
        url = ("postgresql+psycopg://dragon_radar:" + env["DRAGON_DB_PASSWORD"]
               + "@db:5432/dragon_test")
        services = {s: {"environment": {"DRAGON_DATABASE_URL": url}}
                    for s in
                    ("public-api", "owner-api", "analysis-worker", "ai-worker")}
        services["db"] = {"environment": {"POSTGRES_DB": "dragon_test",
            "POSTGRES_USER": "dragon_radar",
            "POSTGRES_PASSWORD": env["DRAGON_DB_PASSWORD"]},
            "healthcheck": {"test": ["CMD-SHELL",
                "pg_isready -U dragon_radar -d dragon_test"]}}
        authority = "localhost:" + env["DRAGON_OWNER_PORT"]
        services["owner-caddy"] = {
            "environment": {"DRAGON_OWNER_AUTHORITY": authority},
            "ports": [{"host_ip": "127.0.0.1",
                      "published": env["DRAGON_OWNER_PORT"], "target": 8443}]}
        services["owner-api"]["environment"]["DRAGON_OWNER_AUTHORITY"] = authority
        services["owner-api"]["command"] = [
            "serve-owner", "--origin", "https://" + authority]
        services["ai-worker"]["environment"]["DRAGON_AI_TOKEN"] = ""
        services["public-caddy"] = {"ports": [
            {"host_ip": "127.0.0.1", "published": "0", "target": 8080}]}
        for value in services.values():
            value["labels"] = {"dragon-radar.acceptance-run": p}
        if fault == "mapping":
            del services["ai-worker"]["environment"]["DRAGON_DATABASE_URL"]
        if fault == "bind":
            services["public-caddy"]["ports"][0]["host_ip"] = "0.0.0.0"
        if fault == "configsecret":
            services["ai-worker"]["environment"]["DRAGON_AI_TOKEN"] = "live-secret"
        print(json.dumps({"services": services, "volumes": {}, "networks": {}}))
    elif action[:1] == ["up"]:
        for kind in ("container", "volume", "network"):
            (root / ("resource-" + kind)).write_text(p)
        if fault == "setup":
            sys.exit(37)
    elif action[:1] == ["down"]:
        if fault in ("down", "remove", "residual"):
            sys.exit(41)
        for kind in ("container", "volume", "network"):
            (root / ("resource-" + kind)).unlink(missing_ok=True)
    elif action[:1] == ["build"] and fault == "build":
        sys.exit(36)
    elif action[:1] == ["exec"]:
        print("FAKE CA")
elif a[0] in ("ps", "volume", "network") and ("ls" in a or a[0] == "ps"):
    kind = "container" if a[0] == "ps" else a[0]
    if (root / ("resource-" + kind)).exists():
        print("owned-" + kind)
elif len(a) > 1 and a[1] == "inspect":
    kind = a[0]
    p = (root / ("resource-" + kind)).read_text()
    if fault == "unknown" and kind == "container":
        p = "daily-project"
    labels = {"com.docker.compose.project": p, "dragon-radar.acceptance-run": p}
    print(json.dumps([{"Id": "owned-" + kind,
                      "Config": {"Labels": labels}, "Labels": labels}]))
elif a[0] == "rm" or a[1] == "rm":
    kind = "container" if a[0] == "rm" else a[0]
    if fault in ("remove", "residual") and kind == "container":
        sys.exit(43)
    (root / ("resource-" + kind)).unlink(missing_ok=True)
"""


@pytest.fixture
def harness(tmp_path):
    if os.name == "nt":
        pytest.skip("Real Bash fault suite runs in the isolated Linux tools runner")
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copyfile(
        ROOT / "scripts/acceptance-stack.sh", root / "scripts/acceptance-stack.sh"
    )
    shutil.copyfile(
        ROOT / "scripts/acceptance_stack.py", root / "scripts/acceptance_stack.py"
    )
    shutil.copyfile(ROOT / "compose.yaml", root / "compose.yaml")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in {
        "docker": FAKE_DOCKER,
        "openssl": "#!/bin/sh\nexit 0\n",
        "curl": "#!/bin/sh\nexit 0\n",
    }.items():
        path = bin_dir / name
        path.write_text(text)
        path.chmod(0o700)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DRAGON_", "DAILY_", "COMPOSE_", "AI_"))
    }
    env.update(PATH=f"{bin_dir}:{env['PATH']}", FAKE_ROOT=str(root))

    class Harness:
        state = root / ".temp/acceptance-stack"

        def run(self, *args, **extra):
            return subprocess.run(
                ["bash", str(root / "scripts/acceptance-stack.sh"), *args],
                env=env | extra,
                capture_output=True,
                text=True,
                timeout=20,
            )

        def calls(self):
            path = root / "calls.jsonl"
            return (
                [json.loads(line) for line in path.read_text().splitlines()]
                if path.exists()
                else []
            )

        def exports(self):
            return dict(
                line.split("=", 1)
                for line in (root / "github.env").read_text().splitlines()
            )

        def build(self):
            result = self.run("build", GITHUB_ENV=str(root / "github.env"))
            assert result.returncode == 0, result.stderr
            return self.exports()

    return Harness()


@pytest.mark.parametrize(
    "key,value",
    [
        ("DRAGON_AI_TOKEN", "live"),
        ("DRAGON_DATABASE_URL", "postgresql://daily/db"),
        ("DRAGON_DB_PASSWORD", "live"),
        ("COMPOSE_FILE", "daily.yaml"),
        ("COMPOSE_PROJECT_NAME", "daily"),
        ("DAILY_DATABASE_URL", "daily"),
        ("AI_TOKEN", "live"),
        ("DRAGON_TASK20_COMPOSE_PROJECT", "daily"),
    ],
)
def test_rejects_inherited_before_docker(harness, key, value):
    result = harness.run("build", **{key: value})
    assert result.returncode != 0
    assert harness.calls() == []


@pytest.mark.parametrize("mode", ["missing", "malformed", "permissions", "symlink"])
def test_refuses_unvalidated_state_without_docker(harness, mode):
    harness.state.mkdir(parents=True, mode=0o700)
    state = harness.state / "compose.env"
    if mode != "missing":
        state.write_text("$(touch injected)\nCOMPOSE_PROJECT_NAME=daily\n")
        state.chmod(0o600 if mode != "permissions" else 0o644)
    if mode == "symlink":
        target = harness.state / "unknown"
        state.rename(target)
        state.symlink_to(target)
    result = harness.run("setup", "--skip-build")
    assert result.returncode != 0
    assert harness.calls() == []
    assert harness.state.exists()


def test_build_setup_reuses_exact_validated_exports(harness):
    exports = harness.build()
    before = (harness.state / "compose.env").read_bytes()
    assert len(exports["DRAGON_DB_PASSWORD"]) == 64
    assert exports["DRAGON_OWNER_PORT"] not in ("8030", "8031")
    assert harness.run("setup", "--skip-build", **exports).returncode == 0
    assert (harness.state / "compose.env").read_bytes() == before
    calls = harness.calls()
    assert sum("build" in call for call in calls) == 1
    assert (
        len(
            {
                call[call.index("--project-name") + 1]
                for call in calls
                if call[0] == "compose"
            }
        )
        == 1
    )
    override = (harness.state / "compose.override.yaml").read_text()
    assert "POSTGRES_DB: dragon_test" in override
    assert override.count("@db:5432/dragon_test") == 4
    assert "pg_isready -U dragon_radar -d dragon_test" in override
    facts = (harness.state / "config-facts.json").read_text()
    assert exports["DRAGON_DB_PASSWORD"] not in facts
    assert "postgresql" not in facts
    assert harness.run("teardown", **exports).returncode == 0
    assert not (harness.state / "compose.env").exists()
    assert (harness.state / "cleanup.json").exists()
    archived = harness.state.parent / "acceptance/acceptance-stack-cleanup.json"
    assert archived.read_bytes() == (harness.state / "cleanup.json").read_bytes()


def test_rejects_tampered_prior_ci_exports(harness):
    exports = harness.build()
    calls = len(harness.calls())
    exports["DRAGON_OWNER_PORT"] = "8030"
    assert harness.run("setup", "--skip-build", **exports).returncode != 0
    assert len(harness.calls()) == calls


@pytest.mark.parametrize("fault", ["mapping", "bind", "configsecret"])
def test_rejects_unsafe_config_before_start(harness, fault):
    harness.build()
    result = harness.run("setup", "--skip-build", FAKE_FAULT=fault)
    assert result.returncode != 0
    assert not any("up" in call for call in harness.calls())
    evidence = (harness.state / "cleanup.json").read_text()
    assert "live-secret" not in evidence


@pytest.mark.parametrize("fault,code", [("build", 36), ("setup", 37)])
def test_primary_failure_preserved_and_cleanup_attempted(harness, fault, code):
    result = harness.run("setup", FAKE_FAULT=fault)
    assert result.returncode == code
    assert any("down" in call for call in harness.calls())
    assert (harness.state / "cleanup.json").exists()
    assert not (harness.state / "compose.env").exists()


@pytest.mark.parametrize("fault", ["down", "remove", "residual", "unknown"])
def test_teardown_collects_failures_and_never_deletes_unknown(harness, fault):
    exports = harness.build()
    assert harness.run("setup", "--skip-build", **exports).returncode == 0
    result = harness.run("teardown", FAKE_FAULT=fault)
    assert result.returncode != 0
    evidence = json.loads((harness.state / "cleanup.json").read_text())
    assert evidence["errors"]
    assert "residuals" in evidence
    assert not (harness.state / "compose.env").exists()
    calls = harness.calls()
    if fault == "unknown":
        assert not any("down" in call or call[0] == "rm" for call in calls)
        assert evidence["residuals"]
        assert any(call[:2] == ["volume", "rm"] for call in calls)
        assert any(call[:2] == ["network", "rm"] for call in calls)
    else:
        assert any(call[:2] == ["network", "ls"] for call in calls)
        assert any(call[:2] == ["volume", "ls"] for call in calls)
        assert any(call[:2] == ["volume", "rm"] for call in calls)
        assert any(call[:2] == ["network", "rm"] for call in calls)


def test_missing_state_teardown_refuses_export_identity(harness):
    result = harness.run(
        "teardown", DRAGON_TASK20_COMPOSE_PROJECT="dragon-radar-task20-123"
    )
    assert result.returncode != 0
    assert harness.calls() == []


def test_unknown_private_file_is_retained_and_cleanup_fails(harness):
    exports = harness.build()
    unknown = harness.state / "owner-secrets/unknown-pairing.txt"
    unknown.write_text("do-not-delete-or-archive")
    result = harness.run("teardown", **exports)
    assert result.returncode != 0
    assert unknown.read_text() == "do-not-delete-or-archive"
    receipt = (harness.state / "cleanup.json").read_text()
    assert "remove-owned-secrets" in receipt
    assert "do-not-delete-or-archive" not in receipt
