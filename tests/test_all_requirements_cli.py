"""Offline command namespaces must reach their own validators before storage."""

import json
import os
import subprocess
import sys


def invoke(*arguments):
    environment = dict(os.environ)
    environment["DRAGON_DATABASE_URL"] = "invalid-private-password-marker"
    return subprocess.run(
        [sys.executable, "-m", "dragon_radar", *arguments],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
        timeout=30,
    )


def test_central_help_lists_new_offline_namespaces():
    result = invoke("--help")
    assert result.returncode == 0
    assert "supplier-compare" in result.stdout
    assert "ai-output" in result.stdout


def test_supplier_compare_rejects_arguments_without_opening_database():
    result = invoke("supplier-compare", "--unexpected=1")
    assert result.returncode == 1
    assert "private-password-marker" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert json.loads(result.stdout)["issues"][0]["code"] == "arguments"


def test_summary_selection_validation_remains_offline(tmp_path):
    document = tmp_path / "summary-selections.json"
    document.write_text("[]", encoding="utf-8")
    result = invoke(
        "ai-output", "validate-summary-selections", "--document", str(document)
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["summary_selections"] == []
    assert "private-password-marker" not in result.stdout + result.stderr
