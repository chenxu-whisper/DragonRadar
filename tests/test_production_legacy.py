import importlib
import subprocess
import sys
from pathlib import Path


def test_external_passes_do_not_upgrade_integrity_only_automation():
    module = importlib.import_module("dragon_radar.production.report")
    assert hasattr(module, "attach_external"), "legacy opt-in adapter missing"
    legacy = {
        "overall_status": "blocked",
        "automated_acceptance": {"status": "unverified"},
        "source": {"trust": "INTEGRITY_ONLY"},
    }
    external = module.blocked_report()
    external["gates"]["public_sharing_permit"]["status"] = "pass"
    module.attach_external(legacy, external)
    assert legacy["public_sharing_permit"]["status"] == "pass"
    assert legacy["automated_acceptance"]["status"] == "unverified"
    assert legacy["source"]["trust"] == "INTEGRITY_ONLY"
    assert legacy["overall_status"] == "blocked"


def test_legacy_clis_expose_explicit_proof_opt_in():
    root = Path(__file__).parents[1]
    for script, flag in (
        ("verify_reset.py", "--production-evidence"),
        ("audit_reset_release.py", "--production-evidence-output"),
    ):
        result = subprocess.run(
            [sys.executable, str(root / "scripts" / script), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0
        assert flag in result.stdout
