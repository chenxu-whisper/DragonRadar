"""Contract checks exercise the actual PRD, with one mutation per rejection."""

import io
import re
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_prd_contracts.py"
PRD = ROOT / "docs" / "DragonRadar_\u5f00\u53d1\u9700\u6c42PRD.md"


@pytest.fixture
def checker():
    assert SCRIPT.exists(), "Task01 contract checker has not been implemented"
    return runpy.run_path(str(SCRIPT))


@pytest.fixture
def document():
    return PRD.read_text(encoding="utf-8")


def test_actual_prd_contracts(checker, document):
    report = checker["check_prd"](document)
    assert report["errors"] == []
    assert report["req_count"] == 57
    assert report["endpoint_count"] == 29
    assert report["example_count"] == 29
    assert report["json_count"] >= 30


@pytest.mark.parametrize(
    ("old", "new", "diagnostic"),
    [
        ("| G01 |", "| G02 |", "REQ"),
        ("| G01 |", "| Z99 |", "REQ"),
        ("| 01 | GET |", "| 02 | GET |", "endpoint"),
        ("| 01 | GET |", "| 01 | POST |", "endpoint 01"),
        ("/api/market/overview?date=", "/api/market/wrong?date=", "endpoint 01"),
        ('"trade_date": "2026-09-18",', '"trade_date": "2026-09-18"!', "JSON"),
        ('"expected": 5, "valid": 5', '"expected": 6, "valid": 5', "coverage"),
        ('"expected": 5, "valid": 5', '"expected": true, "valid": 5', "coverage"),
        ('"excluded": 0', '"excluded": -1', "coverage"),
        ('"excluded": 0, ', "", "coverage"),
        ('"valid": 5', '"valid": NaN', "JSON"),
        ('"valid": 5', '"valid": 5, "valid": 5', "JSON"),
        ('"up": 3, "down": 1', '"up": 4, "down": 1', "breadth"),
        ('"valid_total": 5', '"valid_total": 6', "breadth"),
        ('"total": 2,\n    "cells"', '"total": 3,\n    "cells"', "ladder"),
        (
            '"boards": 1, "count": 1, "stocks"',
            '"boards": 1, "count": 2, "stocks"',
            "ladder",
        ),
        (
            '"boards": 2, "count": 1, "stocks"',
            '"boards": 3, "count": 1, "stocks"',
            "ladder",
        ),
        ('"boards": 2, "count": 1}', '"boards": 2, "count": 2}', "heatmap"),
        ('"total": 1\n  }\n}', '"total": 2\n  }\n}', "candidates"),
        ('"value": 0.6667', '"value": 0.6666', "seal_rate"),
        ('"tradable": 2, "rate": 0.5', '"tradable": 2, "rate": 0.7', "promote_rate"),
    ],
)
def test_minimal_mutation_is_rejected(checker, document, old, new, diagnostic):
    assert old in document, f"Mutation anchor missing: {old}"
    mutated = document.replace(old, new, 1)
    errors = checker["check_prd"](mutated)["errors"]
    assert any(diagnostic in error for error in errors), errors


def test_missing_example_and_duplicate_number_are_rejected(checker, document):
    marker = "#### \u7aef\u70b901 "
    assert marker in document
    mutated = document.replace(marker, "#### \u7aef\u70b902 ", 1)
    assert any("example" in e for e in checker["check_prd"](mutated)["errors"])


def test_missing_json_example_is_rejected(checker, document):
    start = document.index("#### \u7aef\u70b901 ")
    end = document.index("#### \u7aef\u70b902 ", start)
    section = re.sub(r"```json\n.*?\n```", "", document[start:end], flags=re.S)
    mutated = document[:start] + section + document[end:]
    assert any("example 01" in e for e in checker["check_prd"](mutated)["errors"])


def test_empty_document_is_not_success(checker):
    report = checker["check_prd"]("")
    assert report["errors"]
    assert report["req_count"] == 0


def test_cli_stdin_mutation_returns_nonzero(checker, document, monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(document.replace("| G01 |", "| G02 |"))
    )
    assert checker["main"](["--stdin"]) == 1
    assert "REQ" in capsys.readouterr().err


def test_cli_missing_path_reports_error_without_traceback(checker, capsys):
    assert checker["main"]([str(ROOT / "missing-prd-for-contract-test.md")]) == 1
    output = capsys.readouterr()
    assert "read" in output.err
    assert "Traceback" not in output.err


def test_cli_default_reads_workspace_prd_from_any_cwd(checker):
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT / "tests",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "REQ=57 endpoints=29 examples=29" in result.stdout
