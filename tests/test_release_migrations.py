"""Release migration manifest tests freeze the Task 00-20 Alembic graph."""

import json
import runpy
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_release_migrations.py"
MIGRATIONS = ROOT / "src" / "dragon_radar" / "migrations"
SUPPORTED_FROM = [
    "0001_foundation",
    "0003_private_review",
    "0004a_topic_attempt_ranges",
    "0006_market_readmodels",
    "0007_role_readmodels",
    "0007c_analysis_jobs",
    "0008_news",
    "0009_regulation",
    "0009d_reg_event_complete_set",
    "0010_ai_tasks",
]


@pytest.fixture
def verifier():
    assert SCRIPT.exists(), "Task 21 migration verifier has not been implemented"
    return runpy.run_path(str(SCRIPT))


@pytest.fixture
def copied_migrations(tmp_path):
    target = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, target)
    return target


def read_manifest(root=MIGRATIONS):
    return json.loads((root / "release_manifest.json").read_text(encoding="utf-8"))


def write_manifest(root, manifest):
    (root / "release_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )


def add_revision(root, *, revision, down_revision):
    (root / "versions" / f"{revision}.py").write_text(
        "\n".join(
            [
                f'revision = "{revision}"',
                f'down_revision = "{down_revision}"',
                "branch_labels = None",
                "depends_on = None",
                "",
                "def upgrade():",
                "    pass",
                "",
                "def downgrade():",
                "    pass",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_release_manifest_matches_single_linear_graph(verifier):
    manifest = verifier["load_release_manifest"](MIGRATIONS / "release_manifest.json")
    graph = verifier["inspect_alembic_graph"](MIGRATIONS)
    result = verifier["verify_manifest"](MIGRATIONS)

    assert manifest["schema"] == "dragon-radar.migration-release-manifest"
    assert manifest["schema_version"] == 1
    assert manifest["rollback_mode"] == "backup_restore_only"
    assert manifest["supported_from"] == SUPPORTED_FROM
    assert graph["bases"] == ["0001_foundation"]
    assert graph["heads"] == ["0010_ai_tasks"]
    assert graph["revision_count"] == 18
    assert result == {
        "head": "0010_ai_tasks",
        "revision_count": 18,
        "errors": [],
    }


def test_release_manifest_accepts_repository_root(verifier):
    assert verifier["verify_manifest"](ROOT)["errors"] == []


def test_release_manifest_rejects_raw_hash_tamper(verifier, copied_migrations):
    path = copied_migrations / "versions" / "0001_foundation.py"
    path.write_bytes(path.read_bytes() + b"# changed\r\n")

    errors = verifier["verify_manifest"](copied_migrations)["errors"]

    assert "SHA-256 mismatch: 0001_foundation.py" in errors
    assert "byte count mismatch: 0001_foundation.py" in errors


def test_release_manifest_rejects_unlisted_revision(verifier, copied_migrations):
    add_revision(
        copied_migrations,
        revision="0011_unlisted",
        down_revision="0010_ai_tasks",
    )

    errors = verifier["verify_manifest"](copied_migrations)["errors"]

    assert any("revision set mismatch" in error for error in errors)
    assert any("0011_unlisted" in error for error in errors)


def test_release_manifest_rejects_branch_and_multiple_heads(
    verifier, copied_migrations
):
    add_revision(
        copied_migrations,
        revision="fork",
        down_revision="0009d_reg_event_complete_set",
    )

    errors = verifier["verify_manifest"](copied_migrations)["errors"]

    assert any("exactly one head" in error for error in errors)
    assert any("linear" in error for error in errors)


@pytest.mark.parametrize(
    ("location", "field"),
    [
        ("manifest", "unexpected"),
        ("manifest", "status"),
        ("manifest", "passed"),
        ("revision", "unexpected"),
        ("revision", "status"),
        ("revision", "pass"),
    ],
)
def test_release_manifest_rejects_unknown_and_caller_status_fields(
    verifier, copied_migrations, location, field
):
    manifest = read_manifest(copied_migrations)
    target = manifest if location == "manifest" else manifest["revisions"][0]
    target[field] = "GO" if field in {"status", "pass", "passed"} else True
    write_manifest(copied_migrations, manifest)

    with pytest.raises(ValueError, match=rf"unexpected field.*{field}"):
        verifier["load_release_manifest"](copied_migrations / "release_manifest.json")


@pytest.mark.parametrize(
    ("location", "field"),
    [
        ("manifest", "schema"),
        ("manifest", "schema_version"),
        ("manifest", "head"),
        ("manifest", "rollback_mode"),
        ("manifest", "supported_from"),
        ("manifest", "revisions"),
        ("revision", "filename"),
        ("revision", "revision"),
        ("revision", "parent"),
        ("revision", "sha256"),
        ("revision", "bytes"),
    ],
)
def test_release_manifest_requires_every_declared_field(
    verifier, copied_migrations, location, field
):
    manifest = read_manifest(copied_migrations)
    target = manifest if location == "manifest" else manifest["revisions"][0]
    del target[field]
    write_manifest(copied_migrations, manifest)

    with pytest.raises(ValueError, match=rf"missing field.*{field}"):
        verifier["load_release_manifest"](copied_migrations / "release_manifest.json")


@pytest.mark.parametrize("filename", ["../0001_foundation.py", "nested/revision.py"])
def test_release_manifest_rejects_unsafe_revision_paths(
    verifier, copied_migrations, filename
):
    manifest = read_manifest(copied_migrations)
    manifest["revisions"][0]["filename"] = filename
    write_manifest(copied_migrations, manifest)

    with pytest.raises(ValueError, match="safe filename"):
        verifier["load_release_manifest"](copied_migrations / "release_manifest.json")


def test_release_manifest_requires_exact_policy_and_sorted_revisions(
    verifier, copied_migrations
):
    manifest = read_manifest(copied_migrations)
    wrong_policy = deepcopy(manifest)
    wrong_policy["supported_from"] = wrong_policy["supported_from"][:-1]
    write_manifest(copied_migrations, wrong_policy)
    with pytest.raises(ValueError, match="supported_from"):
        verifier["load_release_manifest"](copied_migrations / "release_manifest.json")

    manifest["revisions"][0], manifest["revisions"][1] = (
        manifest["revisions"][1],
        manifest["revisions"][0],
    )
    write_manifest(copied_migrations, manifest)
    with pytest.raises(ValueError, match="sorted"):
        verifier["load_release_manifest"](copied_migrations / "release_manifest.json")


def test_release_manifest_rejects_duplicate_revision(verifier, copied_migrations):
    manifest = read_manifest(copied_migrations)
    manifest["revisions"].append(deepcopy(manifest["revisions"][-1]))
    write_manifest(copied_migrations, manifest)

    with pytest.raises(ValueError, match="duplicate revision"):
        verifier["load_release_manifest"](copied_migrations / "release_manifest.json")


def test_release_manifest_rejects_boolean_schema_version(verifier, copied_migrations):
    manifest = read_manifest(copied_migrations)
    manifest["schema_version"] = True
    write_manifest(copied_migrations, manifest)

    with pytest.raises(ValueError, match="schema_version"):
        verifier["load_release_manifest"](copied_migrations / "release_manifest.json")


def test_cli_validates_default_manifest_from_any_working_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "0010_ai_tasks" in result.stdout
    assert "18 revisions" in result.stdout


def test_cli_returns_nonzero_with_actionable_errors(
    verifier, copied_migrations, capsys
):
    path = copied_migrations / "versions" / "0010_ai_tasks.py"
    path.write_bytes(path.read_bytes() + b"# changed\n")

    assert verifier["main"](["--root", str(copied_migrations)]) == 1
    assert "SHA-256 mismatch: 0010_ai_tasks.py" in capsys.readouterr().err
