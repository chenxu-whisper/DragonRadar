"""Static contracts for deterministic Task20 CI acceptance setup."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SETUP_SCRIPT = ROOT / "scripts" / "task20-private-tls.sh"


def test_task20_workflow_prepares_tls_before_verifier_and_always_tears_down():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    task20 = workflow[
        workflow.index("  task20_acceptance:") : workflow.index(
            "\n  web_quality:", workflow.index("  task20_acceptance:")
        )
    ]

    setup = task20.index("name: Prepare Task20 private TLS gateway")
    verifier = task20.index("scripts/verify_reset.py --mode full")
    teardown = task20.index("name: Tear down Task20 private TLS gateway")
    upload = task20.index("uses: actions/upload-artifact@")

    assert setup < verifier < teardown < upload
    assert "run: ./scripts/task20-private-tls.sh setup" in task20
    assert "run: ./scripts/task20-private-tls.sh teardown" in task20
    teardown_step = task20[teardown:upload]
    assert "if: always()" in teardown_step


def test_task20_tls_script_uses_fixed_isolated_compose_lifecycle_and_exports_ca():
    script = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert 'PRIVATE_PORT="8030"' in script
    assert "compose.yaml" in script
    assert "compose.private.yaml" in script
    assert "DRAGON_PRIVATE_PUBLISHED_PORT" in script
    assert "openssl rand -hex 32" in script
    assert "DRAGON_DB_PASSWORD=" in script
    assert "DRAGON_TEST_PRIVATE_CA=" in script
    assert "GITHUB_ENV" in script
    assert "pki/authorities/local/root.crt" in script
    assert "openssl x509" in script
    assert "--cacert" in script

    build = script.index("build")
    database = script.index("up --detach --wait db")
    migration = script.index("run --rm api migrate")
    owner = script.index("run --rm api owner-init")
    gateway = script.index("up --detach --wait web api")
    certificate = script.index("pki/authorities/local/root.crt")
    assert build < database < migration < owner < gateway < certificate

    assert "down --volumes --remove-orphans" in script
    assert 'rm -rf "${STATE_DIR}"' in script


def test_readme_documents_integrity_only_cli_without_acceptance_claim():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(readme.lower().split())

    assert "--validate-existing <path>" in readme
    assert "exit 3" in normalized
    assert "integrity-only" in normalized
    assert "cannot satisfy automated acceptance" in normalized
    assert "--mode validate" not in readme
    assert "--evidence <path>" not in readme
