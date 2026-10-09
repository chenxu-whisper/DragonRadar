"""Release migration manifest tests freeze the Task 00-20 Alembic graph."""

import hashlib
import json
import os
import re
import runpy
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

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


@pytest.fixture(scope="session")
def built_wheel(tmp_path_factory):
    output = tmp_path_factory.mktemp("task21-wheel-dist")
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(output)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    wheels = list(output.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


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


def audited_head():
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip()
    assert re.fullmatch(r"[0-9a-f]{40}", head)
    return head


def append_import_marker(path, marker):
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\nfrom pathlib import Path\n"
        + f"Path({marker.as_posix()!r}).write_text('imported')\n",
        encoding="utf-8",
    )


def refresh_manifest_fingerprint(root, filename):
    path = root / "versions" / filename
    content = path.read_bytes().replace(b"\r\n", b"\n")
    manifest = read_manifest(root)
    entry = next(
        entry for entry in manifest["revisions"] if entry["filename"] == filename
    )
    entry["sha256"] = hashlib.sha256(content).hexdigest()
    entry["bytes"] = len(content)
    write_manifest(root, manifest)


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


def test_release_manifest_hashes_match_committed_git_blobs():
    manifest = read_manifest()

    for entry in manifest["revisions"]:
        relative_path = (MIGRATIONS / "versions" / entry["filename"]).relative_to(ROOT)
        committed_bytes = subprocess.check_output(
            ["git", "cat-file", "blob", f"HEAD:{relative_path.as_posix()}"],
            cwd=ROOT,
        )

        assert b"\r\n" not in committed_bytes
        assert entry["sha256"] == hashlib.sha256(committed_bytes).hexdigest()
        assert entry["bytes"] == len(committed_bytes)


def test_migration_fingerprint_normalizes_crlf_but_not_bare_cr(verifier, tmp_path):
    lf_path = tmp_path / "lf.py"
    crlf_path = tmp_path / "crlf.py"
    bare_cr_path = tmp_path / "bare-cr.py"
    lf_path.write_bytes(b"first\nsecond\n")
    crlf_path.write_bytes(b"first\r\nsecond\r\n")
    bare_cr_path.write_bytes(b"first\rsecond\r")

    migration_fingerprint = verifier["migration_fingerprint"]

    assert migration_fingerprint(lf_path) == migration_fingerprint(crlf_path)
    assert migration_fingerprint(lf_path)[1] == len(b"first\nsecond\n")
    assert migration_fingerprint(lf_path) != migration_fingerprint(bare_cr_path)


def test_release_manifest_accepts_lf_and_crlf_checkouts(verifier, copied_migrations):
    manifest = read_manifest(copied_migrations)
    for entry in manifest["revisions"]:
        path = copied_migrations / "versions" / entry["filename"]
        canonical_bytes = path.read_bytes().replace(b"\r\n", b"\n")
        path.write_bytes(canonical_bytes)
        entry["sha256"] = hashlib.sha256(canonical_bytes).hexdigest()
        entry["bytes"] = len(canonical_bytes)
    write_manifest(copied_migrations, manifest)

    assert verifier["verify_manifest"](copied_migrations)["errors"] == []

    for entry in manifest["revisions"]:
        path = copied_migrations / "versions" / entry["filename"]
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    assert verifier["verify_manifest"](copied_migrations)["errors"] == []


def test_release_manifest_rejects_raw_hash_tamper(verifier, copied_migrations):
    path = copied_migrations / "versions" / "0001_foundation.py"
    path.write_bytes(path.read_bytes() + b"# changed\r\n")

    errors = verifier["verify_manifest"](copied_migrations)["errors"]

    assert "SHA-256 mismatch: 0001_foundation.py" in errors
    assert "byte count mismatch: 0001_foundation.py" in errors


def test_release_manifest_rejects_hash_tamper_before_import(
    verifier, copied_migrations, tmp_path
):
    marker = tmp_path / "tampered-imported"
    path = copied_migrations / "versions" / "0001_foundation.py"
    append_import_marker(path, marker)

    errors = verifier["verify_manifest"](copied_migrations)["errors"]

    assert "SHA-256 mismatch: 0001_foundation.py" in errors
    assert not marker.exists()


def test_release_manifest_rejects_unlisted_revision(verifier, copied_migrations):
    marker = copied_migrations.parent / "unlisted-imported"
    add_revision(
        copied_migrations,
        revision="0011_unlisted",
        down_revision="0010_ai_tasks",
    )
    append_import_marker(
        copied_migrations / "versions" / "0011_unlisted.py",
        marker,
    )

    errors = verifier["verify_manifest"](copied_migrations)["errors"]

    assert any("revision set mismatch" in error for error in errors)
    assert any("0011_unlisted" in error for error in errors)
    assert not marker.exists()


def test_release_manifest_rejects_symlink_escape_before_import(
    verifier, copied_migrations, tmp_path
):
    marker = tmp_path / "symlink-imported"
    path = copied_migrations / "versions" / "0001_foundation.py"
    target = tmp_path / "outside.py"
    shutil.copy2(path, target)
    append_import_marker(target, marker)
    path.unlink()
    try:
        path.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"file symlinks are unavailable: {exc}")

    errors = verifier["verify_manifest"](copied_migrations)["errors"]

    assert "unsafe migration path: 0001_foundation.py" in errors
    assert not marker.exists()


def test_release_manifest_rejects_symlink_root_before_import(
    verifier, copied_migrations, tmp_path
):
    marker = tmp_path / "root-symlink-imported"
    append_import_marker(
        copied_migrations / "versions" / "0001_foundation.py",
        marker,
    )
    refresh_manifest_fingerprint(copied_migrations, "0001_foundation.py")
    linked_root = tmp_path / "linked-migrations"
    try:
        linked_root.symlink_to(copied_migrations, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    with pytest.raises(ValueError, match="unsafe migration path.*root"):
        verifier["verify_manifest"](linked_root)

    assert not marker.exists()


@pytest.mark.skipif(
    sys.platform != "win32" or not hasattr(os.path, "isjunction"),
    reason="Windows junctions require os.path.isjunction",
)
def test_release_manifest_rejects_junction_before_import(
    verifier, copied_migrations, tmp_path
):
    marker = tmp_path / "junction-imported"
    versions = copied_migrations / "versions"
    target = tmp_path / "outside-versions"
    versions.rename(target)
    append_import_marker(target / "0001_foundation.py", marker)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(versions), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"directory junctions are unavailable: {result.stderr}")
    assert os.path.isjunction(versions)

    errors = verifier["verify_manifest"](copied_migrations)["errors"]

    assert any("unsafe migration path" in error for error in errors)
    assert not marker.exists()


@pytest.mark.skipif(
    sys.platform != "win32" or not hasattr(os.path, "isjunction"),
    reason="Windows junctions require os.path.isjunction",
)
def test_release_manifest_rejects_junction_root_before_import(
    verifier, copied_migrations, tmp_path
):
    marker = tmp_path / "root-junction-imported"
    append_import_marker(
        copied_migrations / "versions" / "0001_foundation.py",
        marker,
    )
    refresh_manifest_fingerprint(copied_migrations, "0001_foundation.py")
    linked_root = tmp_path / "linked-migrations"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(linked_root), str(copied_migrations)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"directory junctions are unavailable: {result.stderr}")
    assert os.path.isjunction(linked_root)

    with pytest.raises(ValueError, match="unsafe migration path.*root"):
        verifier["verify_manifest"](linked_root)

    assert not marker.exists()


def test_release_manifest_rejects_branch_and_multiple_heads(
    verifier, copied_migrations
):
    revision = "fork"
    add_revision(
        copied_migrations,
        revision=revision,
        down_revision="0009d_reg_event_complete_set",
    )
    path = copied_migrations / "versions" / f"{revision}.py"
    sha256, byte_count = verifier["migration_fingerprint"](path)
    manifest = read_manifest(copied_migrations)
    manifest["revisions"].append(
        {
            "filename": path.name,
            "revision": revision,
            "parent": "0009d_reg_event_complete_set",
            "sha256": sha256,
            "bytes": byte_count,
        }
    )
    write_manifest(copied_migrations, manifest)

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


@pytest.mark.parametrize(
    ("location", "field"),
    [
        ("manifest", "schema_version"),
        ("revision", "bytes"),
    ],
)
def test_release_manifest_rejects_boolean_integer_fields(
    verifier, copied_migrations, location, field
):
    manifest = read_manifest(copied_migrations)
    target = manifest if location == "manifest" else manifest["revisions"][0]
    target[field] = True
    write_manifest(copied_migrations, manifest)

    with pytest.raises(ValueError, match=field):
        verifier["load_release_manifest"](copied_migrations / "release_manifest.json")


@pytest.mark.parametrize(
    ("needle", "duplicate"),
    [
        (
            '"schema": "dragon-radar.migration-release-manifest",',
            '"schema": "dragon-radar.migration-release-manifest",\n'
            '  "schema": "dragon-radar.migration-release-manifest",',
        ),
        (
            '"revision": "0001_foundation",',
            '"revision": "0001_foundation",\n      "revision": "0001_foundation",',
        ),
    ],
)
def test_release_manifest_rejects_duplicate_json_keys(
    verifier, copied_migrations, needle, duplicate
):
    path = copied_migrations / "release_manifest.json"
    path.write_text(
        path.read_text(encoding="utf-8").replace(needle, duplicate, 1),
        encoding="utf-8",
    )

    field = "schema" if '"schema"' in needle else "revision"
    with pytest.raises(ValueError, match=rf"duplicate JSON key.*{field}"):
        verifier["load_release_manifest"](path)


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


def test_cli_emits_machine_readable_verification_for_audited_commit(tmp_path):
    commit = audited_head()
    verifier_bytes = subprocess.check_output(
        [
            "git",
            "--no-replace-objects",
            "cat-file",
            "blob",
            f"{commit}:scripts/verify_release_migrations.py",
        ],
        cwd=ROOT,
    )
    schema_validator_bytes = subprocess.check_output(
        [
            "git",
            "--no-replace-objects",
            "cat-file",
            "blob",
            f"{commit}:src/dragon_radar/storage/schema_contract.py",
        ],
        cwd=ROOT,
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repository-root",
            str(ROOT),
            "--audited-commit",
            commit,
            "--json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "schema": "dragon-radar.release-migration-verification",
        "schema_version": 1,
        "audited_commit": commit,
        "head": "0010_ai_tasks",
        "revision_count": 18,
        "release_manifest_sha256": hashlib.sha256(
            subprocess.check_output(
                [
                    "git",
                    "cat-file",
                    "blob",
                    f"{commit}:src/dragon_radar/migrations/release_manifest.json",
                ],
                cwd=ROOT,
            )
        ).hexdigest(),
        "schema_contract_sha256": hashlib.sha256(
            subprocess.check_output(
                [
                    "git",
                    "--no-replace-objects",
                    "cat-file",
                    "blob",
                    f"{commit}:src/dragon_radar/storage/schema_contract.json",
                ],
                cwd=ROOT,
            )
        ).hexdigest(),
        "verifier_sha256": hashlib.sha256(verifier_bytes).hexdigest(),
        "schema_validator_sha256": hashlib.sha256(schema_validator_bytes).hexdigest(),
        "errors": [],
    }


def test_machine_verifier_ignores_external_pythonpath(tmp_path):
    package = tmp_path / "polluted" / "dragon_radar" / "storage"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    marker = tmp_path / "external-validator-imported"
    (package / "schema_contract.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')\n"
        "raise RuntimeError('external validator imported')\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(tmp_path / "polluted")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repository-root",
            str(ROOT),
            "--audited-commit",
            audited_head(),
            "--json",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    assert (
        json.loads(result.stdout)["schema_validator_sha256"]
        == hashlib.sha256(
            subprocess.check_output(
                [
                    "git",
                    "--no-replace-objects",
                    "cat-file",
                    "blob",
                    f"{audited_head()}:src/dragon_radar/storage/schema_contract.py",
                ],
                cwd=ROOT,
            )
        ).hexdigest()
    )


def test_machine_verifier_removes_pythonpath_before_importing_alembic(tmp_path):
    package = tmp_path / "polluted" / "alembic"
    package.mkdir(parents=True)
    marker = tmp_path / "external-alembic-imported"
    (package / "__init__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')\n"
        "raise RuntimeError('external alembic imported')\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(tmp_path / "polluted")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(MIGRATIONS)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_machine_verifier_rejects_pythonhome_before_third_party_import(
    monkeypatch,
):
    monkeypatch.setenv("PYTHONHOME", "caller-controlled")

    with pytest.raises(RuntimeError, match="PYTHONHOME"):
        runpy.run_path(str(SCRIPT))


def test_cli_returns_nonzero_with_actionable_errors(
    verifier, copied_migrations, capsys
):
    path = copied_migrations / "versions" / "0010_ai_tasks.py"
    path.write_bytes(path.read_bytes() + b"# changed\n")

    assert verifier["main"](["--root", str(copied_migrations)]) == 1
    assert "SHA-256 mismatch: 0010_ai_tasks.py" in capsys.readouterr().err


@pytest.mark.parametrize("revision", SUPPORTED_FROM)
def test_supported_revision_upgrades_with_sentinel(
    verifier,
    database_url,
    revision,
):
    manifest = verifier["load_release_manifest"](MIGRATIONS / "release_manifest.json")

    assert manifest["supported_from"] == SUPPORTED_FROM
    result = verifier["exercise_upgrade"](database_url, revision)

    assert result["from_revision"] == revision
    assert result["to_revision"] == "0010_ai_tasks"
    assert result["sentinel_preserved"] is True
    assert result["ready"] is True


def test_legacy_0009_upgrade_preserves_event_snapshot_and_binding(
    verifier,
    database_url,
):
    result = verifier["exercise_upgrade"](database_url, "0009_regulation")

    assert result["sentinel"] == "legacy_regulation_graph"
    before = result["sentinel_before"]
    after = result["sentinel_after"]
    assert before == after
    assert set(before) == {
        "rule_record_id",
        "event_id",
        "regulation_snapshot_id",
        "binding_snapshot_id",
        "binding_event_id",
        "source_id",
        "source_version",
        "event_source_completeness",
        "event_source_id",
        "event_source_version",
    }
    assert before["rule_record_id"]
    assert before["event_id"]
    assert before["regulation_snapshot_id"]
    assert before["binding_snapshot_id"] == before["regulation_snapshot_id"]
    assert before["binding_event_id"] == before["event_id"]
    assert before["source_id"] is None
    assert before["source_version"] is None
    assert before["event_source_completeness"] == "unknown"
    assert before["event_source_id"] is None
    assert before["event_source_version"] is None


def test_built_wheel_contains_all_release_migration_resources(built_wheel):
    with zipfile.ZipFile(built_wheel) as archive:
        names = set(archive.namelist())

    migration_files = {
        name
        for name in names
        if name.startswith("dragon_radar/migrations/versions/") and name.endswith(".py")
    }
    assert migration_files == {
        f"dragon_radar/migrations/versions/{path.name}"
        for path in (MIGRATIONS / "versions").glob("*.py")
    }
    assert len(migration_files) == 18
    assert "dragon_radar/migrations/release_manifest.json" in names
    assert "dragon_radar/storage/schema_contract.json" in names


def test_wheel_resolution_requires_exactly_one_wheel(verifier, tmp_path):
    first = tmp_path / "first.whl"
    second = tmp_path / "second.whl"

    with pytest.raises(ValueError, match="exactly one wheel.*found 0"):
        verifier["resolve_wheel_path"](tmp_path)

    first.touch()
    assert verifier["resolve_wheel_path"](tmp_path) == first.resolve()
    assert verifier["resolve_wheel_path"](tmp_path / "*.whl") == first.resolve()

    second.touch()
    with pytest.raises(ValueError, match="exactly one wheel.*found 2"):
        verifier["resolve_wheel_path"](tmp_path)
    with pytest.raises(ValueError, match="exactly one wheel.*found 2"):
        verifier["resolve_wheel_path"](tmp_path / "*.whl")


@pytest.mark.parametrize("failure", ["timeout", "abnormal-exit"])
def test_wheel_parent_cleans_exact_run_prefix_after_helper_failure(
    verifier,
    database_url,
    tmp_path,
    monkeypatch,
    failure,
):
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    wheel = tmp_path / "dragon_radar.whl"
    wheel.write_bytes(b"wheel")
    output = tmp_path / "migration-evidence.json"
    calls = []
    created_prefix = None
    admin_url = make_url(database_url).difference_update_query(["options"])
    admin = create_engine(admin_url)
    verifier_globals = verifier["verify_installed_wheel"].__globals__

    def run_process(argv, **kwargs):
        nonlocal created_prefix
        calls.append((argv, kwargs))
        if argv[:3] == ["git", "--no-replace-objects", "rev-parse"]:
            return subprocess.CompletedProcess(argv, 0, audited_head() + "\n", "")
        if Path(argv[0]).name.startswith("python") and "wheel_helper.py" in argv[1]:
            created_prefix = argv[-1]
            schema = f"{created_prefix}{'f' * 16}"
            with admin.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            if failure == "timeout":
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            return subprocess.CompletedProcess(argv, 17, "", "forced helper exit")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setitem(verifier_globals, "_run_process", run_process)
    try:
        with pytest.raises((subprocess.TimeoutExpired, RuntimeError)):
            verifier["verify_installed_wheel"](
                database_url,
                wheel,
                output,
                audited_head(),
            )

        assert created_prefix is not None
        assert re.fullmatch(r"release_wheel_[0-9a-f]{32}_", created_prefix)
        with admin.connect() as connection:
            remaining = connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM pg_namespace
                    WHERE left(nspname, length(:prefix)) = :prefix
                    """
                ),
                {"prefix": created_prefix},
            )
        assert remaining == 0
        assert len(calls) == 5
        assert calls[1][0] == [
            "git",
            "--no-replace-objects",
            "rev-parse",
            "--verify",
            "HEAD",
        ]
    finally:
        if created_prefix is not None:
            with admin.begin() as connection:
                schemas = connection.execute(
                    text(
                        """
                        SELECT nspname
                        FROM pg_namespace
                        WHERE left(nspname, length(:prefix)) = :prefix
                        """
                    ),
                    {"prefix": created_prefix},
                ).scalars()
                for schema in schemas:
                    connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_run_process_timeout_terminates_and_waits(verifier, monkeypatch):
    events = []

    class FakeProcess:
        returncode = None

        def communicate(self, timeout=None):
            events.append(("communicate", timeout))
            if len(events) == 1:
                raise subprocess.TimeoutExpired(["helper"], timeout)
            self.returncode = -15
            return "", ""

        def terminate(self):
            events.append(("terminate", None))

        def kill(self):
            events.append(("kill", None))

        def wait(self, timeout=None):
            events.append(("wait", timeout))
            self.returncode = -15
            return self.returncode

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    with pytest.raises(subprocess.TimeoutExpired):
        verifier["_run_process"](["helper"], timeout=0.01)

    if os.name == "nt":
        assert events == [
            ("communicate", 0.01),
            ("terminate", None),
            ("wait", 0.5),
        ]
    else:
        assert events == [
            ("communicate", 0.01),
            ("terminate", None),
            ("wait", 0.5),
            ("kill", None),
            ("communicate", 0.5),
        ]


def _process_exists(pid):
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _process_group_exists(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX process groups")
def test_run_process_timeout_kills_sigterm_ignoring_grandchild_after_parent_exits(
    verifier,
    tmp_path,
):
    process_file = tmp_path / "sigterm-ignoring-grandchild.txt"
    grandchild = """
import os
import pathlib
import signal
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path(sys.argv[1]).write_text(f"{os.getpid()} {os.getpgrp()}")
time.sleep(60)
"""
    parent = """
import subprocess
import sys
import time

subprocess.Popen([sys.executable, "-c", sys.argv[2], sys.argv[1]])
time.sleep(60)
"""
    started = time.perf_counter()
    pid = None
    pgid = None
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            verifier["_run_process"](
                [
                    sys.executable,
                    "-c",
                    parent,
                    str(process_file),
                    grandchild,
                ],
                timeout=0.5,
            )

        elapsed = time.perf_counter() - started
        assert elapsed < 2
        assert process_file.exists()
        pid, pgid = map(int, process_file.read_text().split())
        deadline = time.monotonic() + 1
        while (
            _process_exists(pid) or _process_group_exists(pgid)
        ) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not _process_exists(pid)
        assert not _process_group_exists(pgid)
    finally:
        if pgid is not None and _process_group_exists(pgid):
            os.killpg(pgid, signal.SIGKILL)


def test_run_process_timeout_kills_pipe_inheriting_process_tree(
    verifier,
    tmp_path,
):
    pid_file = tmp_path / "grandchild.pid"
    parent = (
        "import pathlib,subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(4)']);"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid));"
        "time.sleep(60)"
    )
    started = time.perf_counter()

    with pytest.raises(subprocess.TimeoutExpired):
        verifier["_run_process"](
            [sys.executable, "-c", parent, str(pid_file)],
            timeout=0.2,
        )

    elapsed = time.perf_counter() - started
    assert elapsed < 3
    assert pid_file.exists()
    grandchild_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 1
    while _process_exists(grandchild_pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not _process_exists(grandchild_pid)


@pytest.mark.parametrize(
    "failure",
    ["validation", "install", "helper"],
)
def test_wheel_failure_removes_stale_evidence_before_work(
    verifier,
    tmp_path,
    monkeypatch,
    failure,
):
    output = tmp_path / "migration-evidence.json"
    output.write_text('{"stale":"success"}\n', encoding="utf-8")
    wheel = tmp_path / "dragon_radar.whl"
    if failure != "validation":
        wheel.write_bytes(b"wheel")
    calls = 0
    verifier_globals = verifier["verify_installed_wheel"].__globals__

    def run_process(argv, **_kwargs):
        nonlocal calls
        calls += 1
        if failure == "install" and calls == 2:
            return subprocess.CompletedProcess(argv, 9, "", "install failed")
        if failure == "helper" and calls == 3:
            child_output = Path(argv[3])
            assert child_output != output.resolve()
            child_output.write_text('{"stale":"child"}\n', encoding="utf-8")
            return subprocess.CompletedProcess(argv, 13, "", "helper failed")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setitem(verifier_globals, "_run_process", run_process)
    monkeypatch.setitem(verifier_globals, "_drop_wheel_schemas", lambda *_: None)

    with pytest.raises((ValueError, RuntimeError)):
        verifier["verify_installed_wheel"](
            "postgresql://user:never-print@localhost/dragon_test",
            wheel,
            output,
            audited_head(),
        )

    assert not output.exists()


@pytest.mark.parametrize(
    "value",
    [
        "",
        "183587e",
        "A" * 40,
        "g" * 40,
        "0" * 39,
        "0" * 41,
    ],
)
def test_audited_commit_requires_exact_lowercase_git_identity(verifier, value):
    with pytest.raises(ValueError, match="40 lowercase hex"):
        verifier["validate_audited_commit"](value, ROOT)


def test_audited_commit_must_match_checkout_when_checkable(verifier):
    head = audited_head()

    assert verifier["validate_audited_commit"](head, ROOT) == head
    mismatch = "0" * 40 if head != "0" * 40 else "1" * 40
    with pytest.raises(ValueError, match="does not match"):
        verifier["validate_audited_commit"](mismatch, ROOT)


def test_audited_commit_fails_closed_when_checkout_identity_cannot_be_read(
    verifier,
    tmp_path,
    monkeypatch,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    globals_ = verifier["validate_audited_commit"].__globals__
    monkeypatch.setitem(globals_, "_reject_git_replace_refs", lambda *_: None)
    monkeypatch.setitem(
        globals_,
        "_git_process",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=128,
            stdout="",
        ),
    )

    with pytest.raises(ValueError, match="cannot verify repository HEAD"):
        verifier["validate_audited_commit"]("0" * 40, repository)


def test_wheel_sha256_binds_raw_wheel_bytes(verifier, tmp_path):
    wheel = tmp_path / "dragon_radar.whl"
    wheel.write_bytes(b"first wheel bytes")
    first = verifier["wheel_sha256"](wheel)

    wheel.write_bytes(b"first wheel bytes plus mutation")
    second = verifier["wheel_sha256"](wheel)

    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert re.fullmatch(r"[0-9a-f]{64}", second)
    assert first != second


def test_wheel_cli_requires_audited_commit_and_removes_stale_output(
    verifier,
    tmp_path,
    monkeypatch,
    capsys,
):
    wheel = tmp_path / "dragon_radar.whl"
    wheel.write_bytes(b"wheel")
    output = tmp_path / "migration-evidence.json"
    output.write_text('{"stale":"success"}\n', encoding="utf-8")
    monkeypatch.setitem(
        verifier["main"].__globals__,
        "verify_installed_wheel",
        lambda *_: pytest.fail("verification ran without an audited commit"),
    )

    result = verifier["main"](
        [
            "--database-url",
            "postgresql://user:never-print@localhost/dragon_test",
            "--wheel",
            str(wheel),
            "--output",
            str(output),
        ]
    )

    assert result == 1
    assert "--audited-commit is required with --wheel" in capsys.readouterr().err
    assert not output.exists()


def test_installed_wheel_runs_fresh_and_supported_upgrades(
    built_wheel,
    database_url,
    tmp_path,
):
    output = tmp_path / "migration-evidence.json"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = ""

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--database-url",
            database_url,
            "--wheel",
            str(built_wheel.parent / "*.whl"),
            "--output",
            str(output),
            "--audited-commit",
            audited_head(),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert set(evidence) == {
        "schema",
        "schema_version",
        "generated_at",
        "wheel_filename",
        "wheel_sha256",
        "audited_commit",
        "imported_from",
        "fresh",
        "supported_upgrades",
    }
    assert evidence["schema"] == "dragon-radar.installed-wheel-migrations"
    assert evidence["schema_version"] == 1
    assert evidence["generated_at"].endswith("+00:00")
    assert evidence["wheel_filename"] == built_wheel.name
    assert (
        evidence["wheel_sha256"] == hashlib.sha256(built_wheel.read_bytes()).hexdigest()
    )
    assert evidence["audited_commit"] == audited_head()
    imported_from = Path(evidence["imported_from"]).resolve()
    assert f"dragon_radar imported from {imported_from}" in result.stdout
    assert not imported_from.is_relative_to(ROOT)
    assert evidence["fresh"] == {
        "to_revision": "0010_ai_tasks",
        "ready": True,
    }
    assert [item["from_revision"] for item in evidence["supported_upgrades"]] == (
        SUPPORTED_FROM
    )
    assert all(item["sentinel_preserved"] for item in evidence["supported_upgrades"])
    assert all(item["ready"] for item in evidence["supported_upgrades"])


def test_ci_runs_installed_wheel_migration_gate_after_build():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    verify_job = workflow[
        workflow.index("  verify:") : workflow.index(
            "\n  reset_performance:",
            workflow.index("  verify:"),
        )
    ]

    build = "uv build --out-dir .temp/dist"
    gate = "name: Verify installed wheel migrations"
    assert verify_job.index(build) < verify_job.index(gate)
    assert '--database-url "$DRAGON_TEST_DATABASE_URL"' in verify_job
    assert "--wheel .temp/dist" in verify_job
    assert "--output .temp/acceptance/task21-migrations.json" in verify_job
    assert "--audited-commit '${{ github.sha }}'" in verify_job
    assert "dragon_radar-0.1.0-py3-none-any.whl" not in verify_job


def test_wheel_cli_never_reveals_database_password(
    verifier,
    tmp_path,
    monkeypatch,
    capsys,
):
    secret = "never-print-wheel-password"
    wheel = tmp_path / "dragon_radar.whl"
    wheel.touch()
    monkeypatch.setitem(
        verifier["main"].__globals__,
        "verify_installed_wheel",
        lambda *_: (_ for _ in ()).throw(RuntimeError(f"failed at {secret}")),
    )

    result = verifier["main"](
        [
            "--database-url",
            f"postgresql://user:{secret}@localhost/dragon_test",
            "--wheel",
            str(wheel),
            "--output",
            str(tmp_path / "evidence.json"),
            "--audited-commit",
            audited_head(),
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert secret not in captured.out + captured.err


def test_fresh_head_schema_contract_is_strict_sorted_and_ready(database):
    from dragon_radar.storage.schema_contract import (
        capture_schema_contract,
        load_packaged_schema_contract,
    )

    expected = load_packaged_schema_contract()
    with database.engine.connect() as connection:
        actual = capture_schema_contract(connection)

    assert expected == actual
    assert expected["schema"] == "dragon-radar.postgresql-schema-contract"
    assert expected["schema_version"] == 4
    assert expected["postgresql_major"] == 17
    for collection in ("tables", "constraints", "indexes", "triggers"):
        assert expected[collection] == sorted(
            expected[collection],
            key=lambda item: json.dumps(item, sort_keys=True),
        )
    exclusion = next(
        constraint
        for constraint in expected["constraints"]
        if constraint["name"] == "security_state_history_no_overlap"
    )
    assert exclusion == {
        "columns": [],
        "deferrable": False,
        "expression": None,
        "initially_deferred": False,
        "key_expressions": [
            "security_id",
            "state_type",
            "daterange(valid_from, valid_to, '[)'::text)",
        ],
        "kind": "exclusion",
        "match_type": None,
        "name": "security_state_history_no_overlap",
        "on_delete": None,
        "on_update": None,
        "operators": ["=", "=", "&&"],
        "referenced_columns": [],
        "referenced_schema": None,
        "referenced_table": None,
        "table": "security_state_history",
    }
    foreign_key = next(
        constraint
        for constraint in expected["constraints"]
        if constraint["name"] == "news_article_revisions_article_id_fkey"
    )
    assert {
        field: foreign_key[field]
        for field in (
            "on_update",
            "on_delete",
            "match_type",
            "deferrable",
            "initially_deferred",
        )
    } == {
        "on_update": "no_action",
        "on_delete": "restrict",
        "match_type": "simple",
        "deferrable": False,
        "initially_deferred": False,
    }
    assert foreign_key["referenced_schema"] == "current_schema"
    listing_index = next(
        index for index in expected["indexes"] if index["name"] == "snapshots_listing"
    )
    assert listing_index["access_method"] == "btree"
    trigger = next(
        trigger
        for trigger in expected["triggers"]
        if trigger["name"] == "immutable_rows" and trigger["table"] == "snapshots"
    )
    assert trigger["function_schema"] == "current_schema"
    assert trigger["function_name"] == "reject_immutable_mutation"
    assert re.fullmatch(r"[0-9a-f]{64}", trigger["function_definition_sha256"])
    assert trigger["deferrable"] is False
    assert trigger["initially_deferred"] is False
    assert trigger["constraint"] is None
    assert trigger["update_of_columns"] == []
    assert trigger["when"] is None
    assert trigger["level"] == "ROW"
    assert trigger["arguments"] == ""
    assert database.ready()


def test_readiness_rejects_trigger_function_definition_drift(database):
    from dragon_radar.storage.schema_contract import capture_schema_contract

    assert database.ready()
    with database.engine.connect() as connection:
        before = capture_schema_contract(connection)
    expected_trigger = next(
        trigger
        for trigger in before["triggers"]
        if trigger["name"] == "immutable_rows" and trigger["table"] == "snapshots"
    )

    with database.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE OR REPLACE FUNCTION reject_immutable_mutation() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN RETURN NEW; END $$"
            )
        )

    with database.engine.connect() as connection:
        after = capture_schema_contract(connection)
    actual_trigger = next(
        trigger
        for trigger in after["triggers"]
        if trigger["name"] == "immutable_rows" and trigger["table"] == "snapshots"
    )
    assert (
        actual_trigger["function_definition_sha256"]
        != expected_trigger["function_definition_sha256"]
    )
    assert not database.ready()


def test_readiness_rejects_constraint_trigger_deferral_drift(database):
    from dragon_radar.storage.schema_contract import capture_schema_contract

    assert database.ready()
    with database.engine.begin() as connection:
        connection.execute(
            text("DROP TRIGGER withdrawal_records_binding_sealed ON withdrawal_records")
        )
        connection.execute(
            text(
                "CREATE CONSTRAINT TRIGGER withdrawal_records_binding_sealed "
                "AFTER INSERT ON withdrawal_records "
                "DEFERRABLE INITIALLY IMMEDIATE FOR EACH ROW "
                "EXECUTE FUNCTION require_withdrawal_binding_seal()"
            )
        )

    with database.engine.connect() as connection:
        actual = capture_schema_contract(connection)
    trigger = next(
        trigger
        for trigger in actual["triggers"]
        if trigger["name"] == "withdrawal_records_binding_sealed"
        and trigger["table"] == "withdrawal_records"
    )
    assert trigger["deferrable"] is True
    assert trigger["initially_deferred"] is False
    assert trigger["constraint"] == {
        "schema": "current_schema",
        "table": "withdrawal_records",
        "name": "withdrawal_records_binding_sealed",
    }
    assert not database.ready()


def test_readiness_rejects_trigger_update_of_columns_drift(database):
    from dragon_radar.storage.schema_contract import capture_schema_contract

    assert database.ready()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE OR REPLACE TRIGGER analysis_jobs_immutable_inputs "
                "BEFORE UPDATE OF status ON analysis_jobs FOR EACH ROW "
                "EXECUTE FUNCTION guard_analysis_job_inputs()"
            )
        )

    with database.engine.connect() as connection:
        actual = capture_schema_contract(connection)
    trigger = next(
        trigger
        for trigger in actual["triggers"]
        if trigger["name"] == "analysis_jobs_immutable_inputs"
        and trigger["table"] == "analysis_jobs"
    )
    assert trigger["update_of_columns"] == ["status"]
    assert not database.ready()


def test_readiness_rejects_foreign_key_action_drift(database):
    assert database.ready()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE news_article_revisions DROP CONSTRAINT "
                "news_article_revisions_article_id_fkey"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE news_article_revisions "
                "ADD CONSTRAINT news_article_revisions_article_id_fkey "
                "FOREIGN KEY (article_id) REFERENCES news_articles(id) "
                "ON DELETE CASCADE"
            )
        )

    assert not database.ready()


def test_readiness_rejects_foreign_key_rebound_to_same_table_in_other_schema(
    database,
):
    assert database.ready()
    with database.engine.begin() as connection:
        current_schema = connection.scalar(text("SELECT current_schema()"))
        alternate_schema = f"{current_schema}_fk"
        connection.execute(text(f'CREATE SCHEMA "{alternate_schema}"'))
        connection.execute(
            text(
                f'CREATE TABLE "{alternate_schema}".news_articles '
                "(LIKE news_articles INCLUDING ALL)"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE news_article_revisions DROP CONSTRAINT "
                "news_article_revisions_article_id_fkey"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE news_article_revisions "
                "ADD CONSTRAINT news_article_revisions_article_id_fkey "
                f'FOREIGN KEY (article_id) REFERENCES "{alternate_schema}".'
                "news_articles(id) ON DELETE RESTRICT"
            )
        )

    try:
        assert not database.ready()
    finally:
        with database.engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{alternate_schema}" CASCADE'))


def test_readiness_rejects_trigger_function_schema_drift(database):
    assert database.ready()
    with database.engine.begin() as connection:
        current_schema = connection.scalar(text("SELECT current_schema()"))
        alternate_schema = f"{current_schema}_trigger"
        connection.execute(text(f'CREATE SCHEMA "{alternate_schema}"'))
        connection.execute(
            text(
                f'CREATE FUNCTION "{alternate_schema}".reject_immutable_mutation() '
                "RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN RAISE EXCEPTION 'immutable table'; END $$"
            )
        )
        connection.execute(
            text(
                "CREATE OR REPLACE TRIGGER immutable_rows "
                "BEFORE UPDATE OR DELETE ON snapshots "
                "FOR EACH ROW EXECUTE FUNCTION "
                f'"{alternate_schema}".reject_immutable_mutation()'
            )
        )

    try:
        from dragon_radar.storage.schema_contract import capture_schema_contract

        with database.engine.connect() as connection:
            actual = capture_schema_contract(connection)
        trigger = next(
            trigger
            for trigger in actual["triggers"]
            if trigger["name"] == "immutable_rows" and trigger["table"] == "snapshots"
        )
        assert trigger["function_schema"] == f"schema:{alternate_schema}"
        assert not database.ready()
    finally:
        with database.engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{alternate_schema}" CASCADE'))


def test_readiness_rejects_trigger_when_condition_drift(database):
    assert database.ready()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE OR REPLACE TRIGGER immutable_rows "
                "BEFORE UPDATE OR DELETE ON snapshots "
                "FOR EACH ROW WHEN (false) "
                "EXECUTE FUNCTION reject_immutable_mutation()"
            )
        )

    from dragon_radar.storage.schema_contract import capture_schema_contract

    with database.engine.connect() as connection:
        actual = capture_schema_contract(connection)
    trigger = next(
        trigger
        for trigger in actual["triggers"]
        if trigger["name"] == "immutable_rows" and trigger["table"] == "snapshots"
    )
    assert trigger["when"] == "false"
    assert not database.ready()


def test_readiness_rejects_index_access_method_drift(database):
    assert database.ready()
    with database.engine.begin() as connection:
        connection.execute(text("DROP INDEX market_import_attempts_listing"))
        connection.execute(
            text(
                "CREATE INDEX market_import_attempts_listing "
                "ON market_import_attempts USING hash (attempt_sequence)"
            )
        )

    assert not database.ready()


def test_readiness_rejects_exclusion_replaced_by_equivalent_named_index(database):
    assert database.ready()
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE security_state_history DROP CONSTRAINT "
                "security_state_history_no_overlap"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX security_state_history_no_overlap "
                "ON security_state_history USING gist "
                "(security_id, state_type, "
                "daterange(valid_from, valid_to, '[)'::text))"
            )
        )
    assert not database.ready()


def test_readiness_rejects_partial_index_without_required_predicate(database):
    assert database.ready()
    with database.engine.begin() as connection:
        index_definition = connection.scalar(
            text(
                """
                SELECT pg_get_indexdef(index_record.indexrelid)
                FROM pg_index index_record
                JOIN pg_class relation
                  ON relation.oid = index_record.indrelid
                JOIN pg_namespace namespace
                  ON namespace.oid = relation.relnamespace
                JOIN pg_class index_relation
                  ON index_relation.oid = index_record.indexrelid
                WHERE namespace.nspname = current_schema()
                  AND relation.relname = 'publication_attempts'
                  AND index_relation.relname =
                      'publication_attempts_published_key'
                """
            )
        )

        assert index_definition is not None
        create_without_predicate, separator, predicate = index_definition.partition(
            " WHERE "
        )
        assert separator
        assert "status" in predicate
        assert "published" in predicate
        connection.execute(text("DROP INDEX publication_attempts_published_key"))
        connection.execute(text(create_without_predicate))

    assert not database.ready()


@pytest.mark.parametrize(
    ("catalog_query", "catalog_parameters", "ddl"),
    [
        (
            """
            SELECT count(*)
            FROM pg_trigger trigger
            JOIN pg_class relation ON relation.oid = trigger.tgrelid
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = current_schema()
              AND relation.relname = 'snapshots'
              AND trigger.tgname = 'immutable_rows'
              AND NOT trigger.tgisinternal
            """,
            {},
            "DROP TRIGGER immutable_rows ON snapshots",
        ),
        (
            """
            SELECT count(*)
            FROM pg_constraint constraint_record
            JOIN pg_class relation ON relation.oid = constraint_record.conrelid
            JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = current_schema()
              AND relation.relname = 'snapshots'
              AND constraint_record.contype = 'u'
              AND (
                  SELECT array_agg(attribute.attname ORDER BY key.ordinality)
                  FROM unnest(constraint_record.conkey)
                      WITH ORDINALITY AS key(attnum, ordinality)
                  JOIN pg_attribute attribute
                    ON attribute.attrelid = relation.oid
                   AND attribute.attnum = key.attnum
              ) = ARRAY['source_id', 'trade_date', 'revision']::name[]
            """,
            {},
            (
                "ALTER TABLE snapshots DROP CONSTRAINT "
                "snapshots_source_id_trade_date_revision_key"
            ),
        ),
        (
            """
            SELECT count(*)
            FROM pg_class index_record
            JOIN pg_namespace namespace ON namespace.oid = index_record.relnamespace
            WHERE namespace.nspname = current_schema()
              AND index_record.relname = 'snapshots_listing'
              AND index_record.relkind = 'i'
            """,
            {},
            "DROP INDEX snapshots_listing",
        ),
        (
            """
            SELECT count(*)
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'import_batches'
              AND column_name = 'payload_hash'
            """,
            {},
            "ALTER TABLE import_batches DROP COLUMN payload_hash",
        ),
    ],
    ids=(
        "immutable-trigger",
        "snapshot-unique-constraint",
        "listing-index",
        "payload-hash-column",
    ),
)
def test_readiness_rejects_required_schema_drift(
    database,
    catalog_query,
    catalog_parameters,
    ddl,
):
    assert database.ready()
    with database.engine.begin() as connection:
        assert connection.scalar(text(catalog_query), catalog_parameters) == 1
        connection.execute(text(ddl))
    assert not database.ready()


def test_schema_contract_loader_rejects_unknown_fields_and_unsorted_data(tmp_path):
    from dragon_radar.storage.schema_contract import (
        load_packaged_schema_contract,
        load_schema_contract,
    )

    contract = load_packaged_schema_contract()
    contract["status"] = "GO"
    path = tmp_path / "unknown.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected field.*status"):
        load_schema_contract(path)

    del contract["status"]
    contract["tables"] = list(reversed(contract["tables"]))
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(ValueError, match="tables.*sorted"):
        load_schema_contract(path)


def test_schema_contract_requires_exact_index_predicate_field(tmp_path):
    from dragon_radar.storage.schema_contract import (
        load_packaged_schema_contract,
        load_schema_contract,
    )

    contract = load_packaged_schema_contract()
    del contract["indexes"][0]["predicate"]
    path = tmp_path / "missing-index-predicate.json"
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(ValueError, match=r"indexes\[0\] missing field: predicate"):
        load_schema_contract(path)


def test_schema_contract_requires_index_access_method(tmp_path):
    from dragon_radar.storage.schema_contract import (
        load_packaged_schema_contract,
        load_schema_contract,
    )

    contract = load_packaged_schema_contract()
    del contract["indexes"][0]["access_method"]
    path = tmp_path / "missing-index-access-method.json"
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(ValueError, match=r"indexes\[0\] missing field: access_method"):
        load_schema_contract(path)


def test_schema_contract_requires_exact_foreign_key_behavior_fields(tmp_path):
    from dragon_radar.storage.schema_contract import (
        load_packaged_schema_contract,
        load_schema_contract,
    )

    contract = load_packaged_schema_contract()
    foreign_key = next(
        constraint
        for constraint in contract["constraints"]
        if constraint["kind"] == "foreign_key"
    )
    del foreign_key["on_delete"]
    path = tmp_path / "missing-foreign-key-action.json"
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(
        ValueError, match=r"constraints\[\d+\] missing field: on_delete"
    ):
        load_schema_contract(path)


def test_schema_contract_requires_foreign_key_referenced_schema(tmp_path):
    from dragon_radar.storage.schema_contract import (
        load_packaged_schema_contract,
        load_schema_contract,
    )

    contract = load_packaged_schema_contract()
    foreign_key = next(
        constraint
        for constraint in contract["constraints"]
        if constraint["kind"] == "foreign_key"
    )
    del foreign_key["referenced_schema"]
    path = tmp_path / "missing-foreign-key-schema.json"
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match=r"constraints\[\d+\] missing field: referenced_schema",
    ):
        load_schema_contract(path)


def test_schema_contract_rejects_invalid_foreign_key_behavior(tmp_path):
    from dragon_radar.storage.schema_contract import (
        load_packaged_schema_contract,
        load_schema_contract,
    )

    contract = load_packaged_schema_contract()
    foreign_key = next(
        constraint
        for constraint in contract["constraints"]
        if constraint["kind"] == "foreign_key"
    )
    foreign_key["on_update"] = "ignore"
    contract["constraints"] = sorted(
        contract["constraints"],
        key=lambda item: json.dumps(
            item,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ),
    )
    path = tmp_path / "invalid-foreign-key-action.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(ValueError, match=r"constraints\[\d+\]\.on_update.*one of"):
        load_schema_contract(path)

    contract = load_packaged_schema_contract()
    contract["constraints"][0]["initially_deferred"] = True
    contract["constraints"] = sorted(
        contract["constraints"],
        key=lambda item: json.dumps(
            item,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ),
    )
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(ValueError, match="initially_deferred requires"):
        load_schema_contract(path)


def test_schema_contract_requires_trigger_function_schema(tmp_path):
    from dragon_radar.storage.schema_contract import (
        load_packaged_schema_contract,
        load_schema_contract,
    )

    contract = load_packaged_schema_contract()
    del contract["triggers"][0]["function_schema"]
    path = tmp_path / "missing-trigger-function-schema.json"
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match=r"triggers\[0\] missing field: function_schema",
    ):
        load_schema_contract(path)


@pytest.mark.parametrize(
    "field",
    [
        "when",
        "level",
        "arguments",
        "function_definition_sha256",
        "deferrable",
        "initially_deferred",
        "constraint",
        "update_of_columns",
    ],
)
def test_schema_contract_requires_trigger_behavior_fields(tmp_path, field):
    from dragon_radar.storage.schema_contract import (
        load_packaged_schema_contract,
        load_schema_contract,
    )

    contract = load_packaged_schema_contract()
    defaults = {
        "function_definition_sha256": "0" * 64,
        "deferrable": False,
        "initially_deferred": False,
        "constraint": None,
        "update_of_columns": [],
    }
    for trigger in contract["triggers"]:
        for key, value in defaults.items():
            trigger.setdefault(key, deepcopy(value))
    del contract["triggers"][0][field]
    contract["triggers"] = sorted(
        contract["triggers"],
        key=lambda item: json.dumps(
            item,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ),
    )
    path = tmp_path / f"missing-trigger-{field}.json"
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match=rf"triggers\[\d+\] missing field: {field}",
    ):
        load_schema_contract(path)


@pytest.mark.parametrize("predicate", ["", True, 1, [], {}])
def test_schema_contract_rejects_invalid_index_predicate(tmp_path, predicate):
    from dragon_radar.storage.schema_contract import (
        load_packaged_schema_contract,
        load_schema_contract,
    )

    contract = load_packaged_schema_contract()
    contract["indexes"][0]["predicate"] = predicate
    contract["indexes"] = sorted(
        contract["indexes"],
        key=lambda item: json.dumps(
            item,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ),
    )
    path = tmp_path / "invalid-index-predicate.json"
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(ValueError, match=r"indexes\[\d+\]\.predicate.*string"):
        load_schema_contract(path)


@pytest.mark.parametrize("major", [16, 18, True, "17"])
def test_schema_contract_requires_exact_supported_postgresql_major(tmp_path, major):
    from dragon_radar.storage.schema_contract import (
        load_packaged_schema_contract,
        load_schema_contract,
    )

    contract = load_packaged_schema_contract()
    contract["postgresql_major"] = major
    path = tmp_path / "wrong-major.json"
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(ValueError, match="postgresql_major.*17"):
        load_schema_contract(path)


def test_capture_uses_stable_non_pretty_expressions_and_server_major():
    from dragon_radar.storage.schema_contract import (
        CONSTRAINTS_SQL,
        INDEXES_SQL,
        POSTGRESQL_MAJOR_SQL,
    )

    assert POSTGRESQL_MAJOR_SQL.strip() == "SHOW server_version_num"
    assert "constraint_record.contype IN ('p', 'u', 'f', 'c', 'x')" in CONSTRAINTS_SQL
    assert "constraint_record.conindid" in CONSTRAINTS_SQL
    assert "constraint_record.conexclop" in CONSTRAINTS_SQL
    assert "constraint_record.conrelid,\n            false" in CONSTRAINTS_SQL
    assert "index_record.indexrelid,\n            key_number,\n            false" in (
        INDEXES_SQL
    )
    assert (
        "index_record.indpred,\n        index_record.indrelid,\n        false"
        in INDEXES_SQL
    )


def test_readiness_rejects_postgresql_major_mismatch(database, monkeypatch):
    import dragon_radar.storage.database as database_module
    from dragon_radar.storage.schema_contract import load_packaged_schema_contract

    actual = load_packaged_schema_contract()
    actual["postgresql_major"] = 18
    monkeypatch.setattr(
        database_module,
        "capture_schema_contract",
        lambda _connection: actual,
    )

    assert not database.ready()


def test_schema_contract_loader_rejects_duplicate_keys_and_wrong_version(tmp_path):
    from dragon_radar.storage.schema_contract import load_schema_contract

    path = tmp_path / "tampered.json"
    path.write_text(
        """
        {
          "schema": "dragon-radar.postgresql-schema-contract",
          "schema": "dragon-radar.postgresql-schema-contract",
          "schema_version": 2,
          "tables": [],
          "constraints": [],
          "indexes": [],
          "triggers": []
        }
        """,
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate JSON key.*schema"):
        load_schema_contract(path)

    path.write_text(
        json.dumps(
            {
                "schema": "dragon-radar.postgresql-schema-contract",
                "schema_version": 2,
                "postgresql_major": 17,
                "tables": [],
                "constraints": [],
                "indexes": [],
                "triggers": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="schema_version"):
        load_schema_contract(path)


def test_schema_contract_loader_rejects_malformed_json_and_wrong_schema(tmp_path):
    from dragon_radar.storage.schema_contract import load_schema_contract

    path = tmp_path / "tampered.json"
    path.write_text('{"schema":', encoding="utf-8")
    with pytest.raises(ValueError, match="cannot read schema contract"):
        load_schema_contract(path)

    path.write_text(
        json.dumps(
            {
                "schema": "not-dragon-radar",
                "schema_version": 1,
                "postgresql_major": 17,
                "tables": [],
                "constraints": [],
                "indexes": [],
                "triggers": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="contract schema"):
        load_schema_contract(path)


def test_readiness_fails_closed_when_contract_processing_raises(
    database,
    monkeypatch,
):
    import dragon_radar.storage.database as database_module

    monkeypatch.setattr(
        database_module,
        "load_packaged_schema_contract",
        lambda: (_ for _ in ()).throw(RuntimeError("contract failure")),
    )

    assert not database.ready()


def test_schema_contract_comparison_rejects_tampered_expected_contract(database):
    from dragon_radar.storage.schema_contract import (
        capture_schema_contract,
        compare_schema_contract,
        load_packaged_schema_contract,
    )

    expected = load_packaged_schema_contract()
    expected["triggers"] = expected["triggers"][1:]
    with database.engine.connect() as connection:
        actual = capture_schema_contract(connection)

    errors = compare_schema_contract(expected, actual)

    assert errors
    assert any("triggers" in error for error in errors)


def test_schema_contract_writer_is_atomic_and_contains_no_credentials(
    database,
    tmp_path,
):
    from dragon_radar.storage.schema_contract import (
        load_schema_contract,
        write_schema_contract,
    )

    output = tmp_path / "schema-contract.json"
    with database.engine.connect() as connection:
        contract = write_schema_contract(connection, output)

    assert load_schema_contract(output) == contract
    serialized = output.read_text(encoding="utf-8")
    assert contract["tables"]
    assert "postgresql+psycopg://" not in serialized
    assert "dragon-test-local-only" not in serialized
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


def test_write_schema_contract_cli_requires_database_url(
    verifier,
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.delenv("DRAGON_TEST_DATABASE_URL", raising=False)
    output = tmp_path / "contract.json"

    assert verifier["main"](["--write-schema-contract", str(output)]) == 1

    captured = capsys.readouterr()
    assert "database URL is required" in captured.err
    assert not output.exists()


def test_write_schema_contract_verifies_exact_package_before_database(
    verifier,
    tmp_path,
    monkeypatch,
    capsys,
):
    calls = []
    verifier_globals = verifier["main"].__globals__

    def reject_manifest(root):
        calls.append(root)
        return {"head": None, "revision_count": 0, "errors": ["hash failure"]}

    monkeypatch.setitem(verifier_globals, "verify_manifest", reject_manifest)
    monkeypatch.setitem(
        verifier_globals,
        "require_clean_migration_tree",
        lambda: pytest.fail("Git check ran after a failed manifest"),
    )
    monkeypatch.setitem(
        verifier_globals,
        "generate_schema_contract",
        lambda *_: pytest.fail("database code ran after a failed manifest"),
    )

    result = verifier["main"](
        [
            "--write-schema-contract",
            str(tmp_path / "contract.json"),
            "--database-url",
            "postgresql://user:top-secret@localhost/dragon_test",
        ]
    )

    assert result == 1
    assert calls == [MIGRATIONS]
    captured = capsys.readouterr()
    assert "hash failure" in captured.err
    assert "top-secret" not in captured.err + captured.out


@pytest.mark.parametrize("staged", [False, True], ids=["unstaged", "staged"])
def test_clean_migration_gate_rejects_any_tracked_change(
    verifier,
    tmp_path,
    staged,
):
    repository = tmp_path / "repository"
    migration = repository / "src" / "dragon_radar" / "migrations" / "version.py"
    migration.parent.mkdir(parents=True)
    migration.write_text("revision = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    migration.write_text("revision = 'changed'\n", encoding="utf-8")
    if staged:
        subprocess.run(["git", "add", str(migration)], cwd=repository, check=True)

    with pytest.raises(ValueError, match="tracked migrations differ from HEAD"):
        verifier["require_clean_migration_tree"](repository)


def test_clean_migration_gate_uses_strict_git_argv(
    verifier,
    monkeypatch,
):
    observed = {}

    def fake_run(root, argv, **kwargs):
        observed["root"] = root
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setitem(
        verifier["require_clean_migration_tree"].__globals__,
        "_git_process",
        fake_run,
    )

    verifier["require_clean_migration_tree"](ROOT)

    assert observed["argv"] == [
        "diff",
        "--quiet",
        "HEAD",
        "--",
        "src/dragon_radar/migrations",
    ]
    assert observed["root"] == ROOT


def test_atomic_contract_output_preserves_target_and_removes_temp_on_failure(
    verifier,
    tmp_path,
    monkeypatch,
):
    output = tmp_path / "contract.json"
    output.write_text("original\n", encoding="utf-8")

    def reject_replace(_source, _target):
        raise OSError("write failed")

    monkeypatch.setattr(os, "replace", reject_replace)

    with pytest.raises(OSError, match="write failed"):
        verifier["write_contract_atomically"](
            {"schema": "test", "tables": []},
            output,
        )

    assert output.read_text(encoding="utf-8") == "original\n"
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


def test_generation_asserts_revision_writes_contract_and_always_cleans_up(
    verifier,
    tmp_path,
    monkeypatch,
):
    events = []
    verifier_globals = verifier["generate_schema_contract"].__globals__
    schema = "schema_contract_" + ("a" * 32)
    contract = {
        "schema": "dragon-radar.postgresql-schema-contract",
        "schema_version": 1,
        "tables": [{"name": "example", "columns": []}],
        "constraints": [],
        "indexes": [],
        "triggers": [],
    }

    class FakeUrl:
        def difference_update_query(self, keys):
            assert keys == ["options"]
            return self

        def update_query_dict(self, query):
            assert query == {"options": f"-csearch_path={schema}"}
            return self

        def render_as_string(self, *, hide_password):
            assert not hide_password
            return "postgresql+psycopg://user:secret@localhost/dragon_test"

    class FakeConnection:
        def execute(self, statement):
            events.append(str(statement))

        def scalar(self, statement):
            events.append(str(statement))
            return "0010_ai_tasks"

    class FakeEngine:
        @contextmanager
        def begin(self):
            yield FakeConnection()

        @contextmanager
        def connect(self):
            yield FakeConnection()

        def dispose(self):
            events.append("admin disposed")

    class FakeDatabase:
        def __init__(self, url):
            assert "secret" in url
            self.engine = FakeEngine()

        def migrate(self):
            events.append("migrated")

        def close(self):
            events.append("database closed")

    monkeypatch.setitem(
        verifier_globals, "_validated_postgres_url", lambda _: FakeUrl()
    )
    monkeypatch.setitem(
        verifier_globals, "_create_admin_engine", lambda _: FakeEngine()
    )
    monkeypatch.setitem(verifier_globals, "_database_class", lambda: FakeDatabase)
    monkeypatch.setitem(
        verifier_globals,
        "_schema_contract_functions",
        lambda: (lambda _connection: contract, lambda value: value),
    )
    monkeypatch.setitem(verifier_globals, "_new_schema_name", lambda: schema)
    output = tmp_path / "contract.json"

    result = verifier["generate_schema_contract"](
        output,
        "postgresql://user:secret@localhost/dragon_test",
    )

    assert result == contract
    assert json.loads(output.read_text(encoding="utf-8")) == contract
    assert events.index("migrated") < next(
        index for index, event in enumerate(events) if "alembic_version" in event
    )
    assert events[-3:] == [
        "database closed",
        f'DROP SCHEMA "{schema}" CASCADE',
        "admin disposed",
    ]
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


@pytest.mark.parametrize(
    ("revision", "close_fails", "expected_error"),
    [
        ("0010_ai_tasks", False, "capture failed"),
        ("wrong_revision", False, "generated database revision"),
    ],
    ids=["capture", "revision"],
)
def test_generation_failure_leaves_no_output_or_schema(
    verifier,
    tmp_path,
    monkeypatch,
    revision,
    close_fails,
    expected_error,
):
    output = tmp_path / "contract.json"
    verifier_globals = verifier["generate_schema_contract"].__globals__
    events = []
    schema = "schema_contract_" + ("b" * 32)

    class FakeUrl:
        def difference_update_query(self, keys):
            return self

        def update_query_dict(self, query):
            return self

        def render_as_string(self, *, hide_password):
            return "postgresql+psycopg://user:secret@localhost/dragon_test"

    class FakeConnection:
        def execute(self, statement):
            events.append(str(statement))

        def scalar(self, statement):
            return revision

    class FakeEngine:
        @contextmanager
        def begin(self):
            yield FakeConnection()

        @contextmanager
        def connect(self):
            yield FakeConnection()

        def dispose(self):
            events.append("admin disposed")

    class FakeDatabase:
        def __init__(self, url):
            self.engine = FakeEngine()

        def migrate(self):
            events.append("migrated")

        def close(self):
            events.append("database closed")
            if close_fails:
                raise RuntimeError("close failed")

    monkeypatch.setitem(
        verifier_globals,
        "_validated_postgres_url",
        lambda _: FakeUrl(),
    )
    monkeypatch.setitem(
        verifier_globals,
        "_create_admin_engine",
        lambda _: FakeEngine(),
    )
    monkeypatch.setitem(verifier_globals, "_database_class", lambda: FakeDatabase)
    monkeypatch.setitem(verifier_globals, "_new_schema_name", lambda: schema)
    monkeypatch.setitem(
        verifier_globals,
        "_schema_contract_functions",
        lambda: (
            lambda _connection: (_ for _ in ()).throw(RuntimeError("capture failed")),
            lambda value: value,
        ),
    )
    monkeypatch.setitem(
        verifier_globals,
        "write_contract_atomically",
        lambda *_: pytest.fail("write attempted after capture failed"),
    )

    with pytest.raises((RuntimeError, ValueError), match=expected_error):
        verifier["generate_schema_contract"](
            output,
            "postgresql://user:secret@localhost/dragon_test",
        )

    assert f'DROP SCHEMA "{schema}" CASCADE' in events
    assert events[-1] == "admin disposed"
    assert not output.exists()
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


def _configure_generation_failures(
    verifier,
    monkeypatch,
    *,
    primary=False,
    close=False,
    drop=False,
    dispose=False,
):
    verifier_globals = verifier["_run_generation_lifecycle"].__globals__
    schema = "schema_contract_" + ("c" * 32)
    events = []
    contract = {
        "schema": "dragon-radar.postgresql-schema-contract",
        "schema_version": 1,
        "postgresql_major": 17,
        "tables": [],
        "constraints": [],
        "indexes": [],
        "triggers": [],
    }

    class FakeUrl:
        def difference_update_query(self, _keys):
            return self

        def update_query_dict(self, _query):
            return self

        def render_as_string(self, *, hide_password):
            assert not hide_password
            return "postgresql+psycopg://user:never-print@localhost/dragon_test"

    class FakeConnection:
        def execute(self, statement):
            sql = str(statement)
            events.append(sql)
            if sql.startswith("DROP SCHEMA") and drop:
                raise RuntimeError("drop failed")

        def scalar(self, _statement):
            return "0010_ai_tasks"

    class FakeEngine:
        @contextmanager
        def begin(self):
            yield FakeConnection()

        @contextmanager
        def connect(self):
            yield FakeConnection()

        def dispose(self):
            events.append("admin disposed")
            if dispose:
                raise RuntimeError("dispose failed")

    class FakeDatabase:
        def __init__(self, _url):
            self.engine = FakeEngine()

        def migrate(self):
            events.append("migrated")

        def close(self):
            events.append("database closed")
            if close:
                raise RuntimeError("close failed")

    def capture(_connection):
        if primary:
            raise RuntimeError("primary generation failed")
        return contract

    monkeypatch.setitem(
        verifier_globals, "_validated_postgres_url", lambda _: FakeUrl()
    )
    monkeypatch.setitem(
        verifier_globals,
        "_create_admin_engine",
        lambda _: FakeEngine(),
    )
    monkeypatch.setitem(verifier_globals, "_database_class", lambda: FakeDatabase)
    monkeypatch.setitem(verifier_globals, "_new_schema_name", lambda: schema)
    monkeypatch.setitem(
        verifier_globals,
        "_schema_contract_functions",
        lambda: (capture, lambda value: value),
    )
    return events


def test_generation_preserves_primary_and_all_cleanup_failures(
    verifier,
    monkeypatch,
):
    events = _configure_generation_failures(
        verifier,
        monkeypatch,
        primary=True,
        close=True,
        drop=True,
        dispose=True,
    )

    with pytest.raises(ExceptionGroup) as raised:
        verifier["_run_generation_lifecycle"](
            "postgresql://user:never-print@localhost/dragon_test"
        )

    errors = raised.value.exceptions
    assert [str(error) for error in errors] == [
        "primary generation failed",
        "close failed",
        "drop failed",
        "dispose failed",
    ]
    assert events[-2:] == [
        'DROP SCHEMA "schema_contract_cccccccccccccccccccccccccccccccc" CASCADE',
        "admin disposed",
    ]
    assert "never-print" not in repr(raised.value)


@pytest.mark.parametrize("failure", ["close", "drop", "dispose"])
def test_generation_raises_cleanup_only_failure(verifier, monkeypatch, failure):
    _configure_generation_failures(
        verifier,
        monkeypatch,
        **{failure: True},
    )

    with pytest.raises(RuntimeError, match=f"{failure} failed"):
        verifier["_run_generation_lifecycle"](
            "postgresql://user:never-print@localhost/dragon_test"
        )


def test_write_cli_never_reveals_database_password(
    verifier,
    tmp_path,
    monkeypatch,
    capsys,
):
    secret = "never-print-this-password"
    verifier_globals = verifier["main"].__globals__
    monkeypatch.setitem(
        verifier_globals,
        "verify_manifest",
        lambda root: {
            "head": "0010_ai_tasks",
            "revision_count": 18,
            "errors": [],
        },
    )
    monkeypatch.setitem(verifier_globals, "require_clean_migration_tree", lambda: None)
    monkeypatch.setitem(
        verifier_globals,
        "generate_schema_contract",
        lambda *_: (_ for _ in ()).throw(RuntimeError(f"failed at {secret}")),
    )

    result = verifier["main"](
        [
            "--write-schema-contract",
            str(tmp_path / "contract.json"),
            "--database-url",
            f"postgresql://user:{secret}@localhost/dragon_test",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert secret not in captured.out + captured.err


def test_actual_generation_matches_committed_contract_and_drops_schema(
    verifier,
    tmp_path,
    monkeypatch,
):
    value = os.environ.get("DRAGON_TEST_DATABASE_URL")
    if not value:
        pytest.skip("DRAGON_TEST_DATABASE_URL is required for PostgreSQL integration")
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    schema = "schema_contract_" + os.urandom(16).hex()
    verifier_globals = verifier["generate_schema_contract"].__globals__
    monkeypatch.setitem(verifier_globals, "_new_schema_name", lambda: schema)
    output = tmp_path / "schema_contract.json"

    verifier["generate_schema_contract"](output, value)

    committed = json.loads(
        (ROOT / "src/dragon_radar/storage/schema_contract.json").read_text(
            encoding="utf-8"
        )
    )
    assert json.loads(output.read_text(encoding="utf-8")) == committed
    admin = create_engine(make_url(value).difference_update_query(["options"]))
    try:
        with admin.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM pg_namespace WHERE nspname = :schema"),
                    {"schema": schema},
                )
                == 0
            )
    finally:
        admin.dispose()


def test_release_policy_rejects_alembic_downgrade(verifier):
    manifest = verifier["load_release_manifest"](MIGRATIONS / "release_manifest.json")

    assert manifest["rollback_mode"] == "backup_restore_only"
    with pytest.raises(ValueError) as raised:
        verifier["validate_rollback_request"]("alembic_downgrade")

    assert raised.value.args == (
        {
            "code": "unsupported_release_rollback",
            "requested": "alembic_downgrade",
            "rollback_mode": "backup_restore_only",
        },
    )


def test_release_tooling_never_imports_or_calls_alembic_downgrade():
    import ast

    for path in (ROOT / "scripts").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            assert not (
                isinstance(node, ast.ImportFrom)
                and node.module == "alembic.command"
                and any(alias.name == "downgrade" for alias in node.names)
            ), path
            assert not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "downgrade"
            ), path


@pytest.mark.parametrize(
    "name",
    [
        "",
        "-starts-with-dash",
        "contains space",
        "name;rm",
        "name/child",
        "a" * 129,
    ],
)
def test_restore_container_name_must_be_safe(verifier, name):
    with pytest.raises(ValueError, match="unsafe restore container name"):
        verifier["validate_restore_container_name"](name)


def _configure_fake_restore_drill(
    verifier,
    monkeypatch,
    *,
    failure=None,
    hash_stdout=None,
    source_fingerprints=None,
):
    from sqlalchemy.engine import make_url

    globals_ = verifier["exercise_backup_restore"].__globals__
    events = []
    fingerprint_calls = 0
    source_fingerprint = "a" * 64
    backup_sha256 = "b" * 64
    fingerprints = iter(source_fingerprints or [source_fingerprint] * 3)

    def prepare(database_url):
        events.append(("prepare", database_url))
        if failure == "prepare":
            raise RuntimeError("prepare failed")
        return {
            "sentinel_kind": "foundation_archive",
            "sentinel_expected": {"snapshot_id": "sentinel"},
            "sentinel_values": {"source_id": "task21.sentinel"},
            "source_counts": {
                "source_grants": 1,
                "import_batches": 1,
                "snapshots": 1,
                "audit_events": 1,
            },
            "permission_sentinel": {
                "source_id": "task21.sentinel",
                "approved": True,
                "local_storage": True,
                "local_display": True,
            },
        }

    def fingerprint(database_url):
        nonlocal fingerprint_calls
        fingerprint_calls += 1
        events.append(("fingerprint", database_url))
        return next(fingerprints)

    resources = {
        "role": "task21_role_" + ("c" * 32),
        "source_database": "task21_source_" + ("c" * 32),
        "restore_database": "task21_restore_" + ("c" * 32),
        "dump_path": "/tmp/task21_restore_" + ("c" * 32) + ".dump",
        "password": "unreported-password",
    }

    def create_resources(container, username, actual):
        assert actual == resources
        events.append(("create", container, username))
        if failure == "prepare":
            raise RuntimeError("prepare failed")

    def enforce_quiescence(
        container,
        username,
        source_database,
        app_role,
        app_password,
    ):
        events.append(
            ("freeze", container, username, source_database, app_role, app_password)
        )
        if failure == "freeze":
            raise RuntimeError("freeze failed")

    def restore_access(container, username, restore_database, app_role):
        events.append(
            ("restore_access", container, username, restore_database, app_role)
        )

    def verify(restore_url, source_state):
        events.append(("verify", restore_url, source_state))
        if failure == "verify":
            raise RuntimeError("verify failed")
        return {
            "revision": "0010_ai_tasks",
            "restored_ready": True,
            "sentinel_preserved": True,
            "permissions_preserved": True,
            "restore_fingerprint": source_fingerprint,
            "restored_counts": source_state["source_counts"],
            "app_semantics_verified": True,
        }

    def run_process(argv, **kwargs):
        events.append(("process", list(argv), kwargs))
        command = next(
            (item for item in ("pg_dump", "pg_restore", "sha256sum") if item in argv),
            None,
        )
        if argv[-1].startswith("CREATE DATABASE"):
            command = "createdb"
        if failure is not None and failure == command:
            return subprocess.CompletedProcess(argv, 23, "", f"{command} failed")
        if failure == "cleanup" and (argv[-1].startswith("DROP ") or "rm" in argv):
            return subprocess.CompletedProcess(argv, 24, "", "cleanup failed")
        stdout = ""
        if command == "sha256sum":
            stdout = (
                hash_stdout
                if hash_stdout is not None
                else f"{backup_sha256}  {argv[-1]}\n"
            )
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setitem(
        globals_,
        "_validate_restore_drill_target",
        lambda *_: (
            make_url("postgresql+psycopg://dragon:secret@localhost:5432/postgres"),
            "dragon",
        ),
    )
    monkeypatch.setitem(globals_, "_new_restore_drill_resources", lambda: resources)
    monkeypatch.setitem(globals_, "_create_restore_drill_resources", create_resources)
    monkeypatch.setitem(globals_, "_prepare_restore_source", prepare)
    monkeypatch.setitem(globals_, "_database_fingerprint", fingerprint)
    monkeypatch.setitem(globals_, "_fence_restore_source", enforce_quiescence)
    monkeypatch.setitem(globals_, "_restore_app_access", restore_access)
    monkeypatch.setitem(globals_, "_verify_restored_database", verify)
    monkeypatch.setitem(globals_, "_run_process", run_process)
    monkeypatch.setitem(
        globals_,
        "_git_process",
        lambda _root, argv, **_kwargs: subprocess.CompletedProcess(
            argv,
            0,
            "" if argv[0] == "for-each-ref" else audited_head() + "\n",
            "",
        ),
    )
    return events, source_fingerprint, backup_sha256, lambda: fingerprint_calls


def test_backup_restore_drill_uses_argv_and_writes_atomic_evidence(
    verifier,
    tmp_path,
    monkeypatch,
):
    events, source_fingerprint, backup_sha256, fingerprint_calls = (
        _configure_fake_restore_drill(verifier, monkeypatch)
    )
    output = tmp_path / "restore.json"
    database_url = "postgresql+psycopg://dragon:secret@localhost:5432/postgres"

    result = verifier["exercise_backup_restore"](
        "task21-postgres",
        database_url,
        output,
        audited_commit=audited_head(),
    )

    assert json.loads(output.read_text(encoding="utf-8")) == result
    assert result["audited_commit"] == audited_head()
    assert result["generated_at"].endswith("+00:00")
    assert result["backup_sha256"] == backup_sha256
    assert result["source_fingerprint"] == source_fingerprint
    assert result["source_unchanged"] is True
    assert result["restored_ready"] is True
    assert result["sentinel_preserved"] is True
    assert result["permissions_preserved"] is True
    assert result["app_semantics_verified"] is True
    assert result["quiescence_enforced"] is True
    assert result["measured_data_loss_rows"] == 0
    assert result["data_loss_window_seconds"] == 0.0
    assert result["write_freeze_seconds"] > 0
    assert "quiesced" not in result
    assert "measured_data_loss_window_seconds" not in result
    assert result["elapsed_seconds"] > 0
    assert "secret" not in output.read_text(encoding="utf-8")
    assert "unreported-password" not in output.read_text(encoding="utf-8")
    assert fingerprint_calls() == 3
    assert [event[0] for event in events].index("freeze") < [
        event[0] for event in events
    ].index("fingerprint")
    cleanup_index = next(
        index
        for index, event in enumerate(events)
        if event[0] == "process" and event[1][-1].startswith("DROP ")
    )
    assert [event[0] for event in events].index("verify") < cleanup_index
    assert "thaw" not in [event[0] for event in events]

    process_events = [event for event in events if event[0] == "process"]
    argv = [event[1] for event in process_events]
    assert all(event[2]["timeout"] > 0 for event in process_events)
    assert all("shell" not in event[2] for event in process_events)
    assert sum("pg_dump" in command for command in argv) == 1
    assert sum("sha256sum" in command for command in argv) == 1
    assert sum("pg_restore" in command for command in argv) == 1
    cleanup_sql = [command[-1] for command in argv if command[-1].startswith("DROP ")]
    assert cleanup_sql == [
        'DROP DATABASE IF EXISTS "task21_restore_' + ("c" * 32) + '" WITH (FORCE)',
        'DROP DATABASE IF EXISTS "task21_source_' + ("c" * 32) + '" WITH (FORCE)',
        'DROP ROLE IF EXISTS "task21_role_' + ("c" * 32) + '"',
    ]
    assert argv[-1][-3:] == [
        "rm",
        "-f",
        "/tmp/task21_restore_" + ("c" * 32) + ".dump",
    ]


def test_source_fence_never_thaws_and_proves_app_denial(
    verifier,
    monkeypatch,
):
    globals_ = verifier["exercise_backup_restore"].__globals__
    calls = []

    def run_process(argv, **kwargs):
        calls.append((argv, kwargs))
        sql = argv[-1]
        if "SELECT count(*)" in sql:
            stdout = "0\n"
        elif "task21_write_denied" in sql:
            return subprocess.CompletedProcess(
                argv,
                2,
                "",
                "FATAL: permission denied for database source-db",
            )
        else:
            stdout = ""
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setitem(globals_, "_run_process", run_process)

    verifier["_fence_restore_source"](
        "task21-postgres",
        "dragon",
        "source-db",
        "task21-role",
        "unreported-password",
    )

    argv = [call[0] for call in calls]
    assert all(call[1]["timeout"] > 0 for call in calls)
    assert (
        'ALTER DATABASE "source-db" SET default_transaction_read_only = on'
        in (argv[0][-1])
    )
    assert "REVOKE CONNECT" in argv[1][-1]
    assert "REVOKE CONNECT" in argv[2][-1]
    assert "pg_terminate_backend" in argv[3][-1]
    assert "SELECT count(*)" in argv[4][-1]
    assert "CREATE TABLE task21_write_denied" in argv[5][-1]
    assert not any(
        "default_transaction_read_only = off" in command[-1] for command in argv
    )


@pytest.mark.parametrize(
    "hash_stdout",
    [
        "",
        "not-a-sha256  /tmp/task21.dump\n",
        ("b" * 64) + "\n",
        ("B" * 64) + "  /tmp/task21.dump\n",
        ("b" * 64) + "  /tmp/wrong.dump\n",
        ("b" * 64) + " */tmp/task21.dump\n",
    ],
)
def test_backup_sha256_malformed_output_fails_closed(
    verifier,
    tmp_path,
    monkeypatch,
    hash_stdout,
):
    events, _, _, _ = _configure_fake_restore_drill(
        verifier,
        monkeypatch,
        hash_stdout=hash_stdout,
    )
    output = tmp_path / "restore.json"
    output.write_text('{"stale":"success"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256 output is invalid"):
        verifier["exercise_backup_restore"](
            "task21-postgres",
            "postgresql://dragon:secret@localhost/postgres",
            output,
        )

    assert not output.exists()
    cleanup = [
        event[1]
        for event in events
        if event[0] == "process"
        and (event[1][-1].startswith("DROP ") or "rm" in event[1])
    ]
    assert len(cleanup) == 4
    assert "thaw" not in [event[0] for event in events]


def test_source_mutation_after_dump_fails_closed(
    verifier,
    tmp_path,
    monkeypatch,
):
    source = "a" * 64
    mutated = "c" * 64
    events, _, _, fingerprint_calls = _configure_fake_restore_drill(
        verifier,
        monkeypatch,
        source_fingerprints=[source, mutated, mutated],
    )
    output = tmp_path / "restore.json"

    with pytest.raises(ValueError, match="source database fingerprint changed"):
        verifier["exercise_backup_restore"](
            "task21-postgres",
            "postgresql://dragon:secret@localhost/postgres",
            output,
        )

    assert not output.exists()
    assert fingerprint_calls() >= 2
    assert "thaw" not in [event[0] for event in events]


def test_quiescence_failure_emits_no_pass_evidence_and_keeps_source_frozen(
    verifier,
    tmp_path,
    monkeypatch,
):
    events, _, _, fingerprint_calls = _configure_fake_restore_drill(
        verifier,
        monkeypatch,
        failure="freeze",
    )
    output = tmp_path / "restore.json"

    with pytest.raises(RuntimeError, match="freeze failed"):
        verifier["exercise_backup_restore"](
            "task21-postgres",
            "postgresql://dragon:secret@localhost/postgres",
            output,
        )

    assert not output.exists()
    assert fingerprint_calls() == 0
    assert "thaw" not in [event[0] for event in events]


def test_restore_schema_canonicalization_recreates_only_round_trip_mismatches(
    verifier,
    monkeypatch,
):
    from sqlalchemy import CheckConstraint, Column, Index, MetaData, String, Table

    statements = []
    metadata = MetaData()
    example = Table(
        "example",
        metadata,
        Column("status", String),
        CheckConstraint("status IN ('queued', 'running')", name="example_status"),
    )
    Index(
        "example_claim",
        example.c.status,
        postgresql_where=example.c.status.in_(("queued", "running")),
    )
    expected = {
        "constraints": [
            {
                "table": "example",
                "name": "example_status",
                "kind": "check",
                "expression": "expected",
                "columns": ["status"],
            }
        ],
        "indexes": [
            {
                "table": "example",
                "name": "example_claim",
                "predicate": "expected",
            }
        ],
    }
    actual = {
        "constraints": [
            {
                "table": "example",
                "name": "example_status",
                "kind": "check",
                "expression": "round-trip",
                "columns": ["status"],
            }
        ],
        "indexes": [
            {
                "table": "example",
                "name": "example_claim",
                "predicate": "round-trip",
            }
        ],
    }
    globals_ = verifier["_canonicalize_restored_schema"].__globals__
    monkeypatch.setitem(
        globals_,
        "_restored_schema_contracts",
        lambda _connection: (expected, actual),
    )
    monkeypatch.setitem(globals_, "_restore_schema_metadata", lambda: metadata)

    class FakeConnection:
        def execute(self, statement):
            statements.append(str(statement))

    count = verifier["_canonicalize_restored_schema"](FakeConnection())

    assert count == 2
    combined = "\n".join(statements)
    for object_name in ("example_status", "example_claim"):
        assert combined.count(object_name) == 2


def test_restore_constraint_resolution_uses_literals_for_unnamed_checks(verifier):
    from sqlalchemy import CheckConstraint, Column, MetaData, String, Table

    metadata = MetaData()
    table = Table(
        "example",
        metadata,
        Column("state", String),
        Column("reason", String),
        CheckConstraint("state IN ('pending', 'idle')"),
        CheckConstraint("(state = 'pending' AND reason IS NULL)"),
    )
    expected = {
        "name": "example_state_check",
        "columns": ["state"],
        "expression": (
            "((state)::text = ANY ((ARRAY['pending'::character varying, "
            "'idle'::character varying])::text[]))"
        ),
    }

    constraint = verifier["_metadata_check_constraint"](table, expected)

    assert str(constraint.sqltext) == "state IN ('pending', 'idle')"


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("pg_dump", "pg_dump failed"),
        ("sha256sum", "backup SHA-256 failed"),
        ("createdb", "createdb failed"),
        ("pg_restore", "pg_restore failed"),
        ("verify", "verify failed"),
    ],
)
def test_backup_restore_failure_invalidates_output_and_cleans_exactly(
    verifier,
    tmp_path,
    monkeypatch,
    failure,
    message,
):
    events, _, _, fingerprint_calls = _configure_fake_restore_drill(
        verifier,
        monkeypatch,
        failure=failure,
    )
    output = tmp_path / "restore.json"
    output.write_text('{"stale":"success"}\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match=message):
        verifier["exercise_backup_restore"](
            "task21-postgres",
            "postgresql://dragon:secret@localhost/postgres",
            output,
        )

    assert not output.exists()
    argv = [event[1] for event in events if event[0] == "process"]
    cleanup = [
        command
        for command in argv
        if command[-1].startswith("DROP ") or "rm" in command
    ]
    assert len(cleanup) == 4
    assert cleanup[0][-1].startswith("DROP DATABASE")
    assert "rm" in cleanup[-1]
    assert fingerprint_calls() >= 1
    assert "thaw" not in [event[0] for event in events]


def test_backup_restore_preserves_primary_and_all_cleanup_failures(
    verifier,
    tmp_path,
    monkeypatch,
):
    _configure_fake_restore_drill(verifier, monkeypatch, failure="cleanup")
    globals_ = verifier["exercise_backup_restore"].__globals__
    monkeypatch.setitem(
        globals_,
        "_verify_restored_database",
        lambda *_: (_ for _ in ()).throw(RuntimeError("verify failed")),
    )

    with pytest.raises(BaseExceptionGroup) as raised:
        verifier["exercise_backup_restore"](
            "task21-postgres",
            "postgresql://dragon:secret@localhost/postgres",
            tmp_path / "restore.json",
        )

    assert [str(error) for error in raised.value.exceptions] == [
        "verify failed",
        "restore database cleanup failed with exit code 24: cleanup failed",
        "source database cleanup failed with exit code 24: cleanup failed",
        "app role cleanup failed with exit code 24: cleanup failed",
        "backup file cleanup failed with exit code 24: cleanup failed",
    ]
    assert "secret" not in repr(raised.value)


def test_backup_restore_has_no_source_thaw_path():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "_restore_source_writable" not in source


def test_actual_named_container_backup_restore_drill(verifier, tmp_path):
    container = os.environ.get("DRAGON_TEST_POSTGRES_CONTAINER")
    database_url = os.environ.get("DRAGON_TEST_DATABASE_URL")
    if not container or not database_url:
        pytest.skip(
            "DRAGON_TEST_POSTGRES_CONTAINER and DRAGON_TEST_DATABASE_URL "
            "are required for the restore integration"
        )
    output = tmp_path / "restore.json"

    result = verifier["exercise_backup_restore"](
        container,
        database_url,
        output,
    )

    assert result["source_unchanged"] is True
    assert result["quiescence_enforced"] is True
    assert result["measured_data_loss_rows"] == 0
    assert result["data_loss_window_seconds"] == 0.0
    assert result["write_freeze_seconds"] > 0
    assert result["restored_ready"] is True
    assert result["sentinel_preserved"] is True
    assert result["permissions_preserved"] is True
    assert (
        result["backup_sha256"]
        == json.loads(output.read_text(encoding="utf-8"))["backup_sha256"]
    )
    assert result["cleanup"] == {
        "source_database_removed": True,
        "restore_database_removed": True,
        "app_role_removed": True,
        "backup_file_removed": True,
    }


def test_backup_restore_runbook_is_restore_only_and_operational():
    runbook = (ROOT / "docs" / "operations" / "database-backup-restore.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "stop writes",
        "custom",
        "distinct restore",
        "Database.ready",
        "sentinel",
        "switch",
        "abort",
        "cleanup",
        "audit",
        "RPO <= 1 hour",
        "RTO <= 4 hours",
        "suggested",
        "unapproved",
        "NEVER",
        "alembic downgrade",
    ):
        assert required.lower() in runbook.lower()
    assert "down -v" not in runbook


def test_backup_restore_runbook_never_drops_the_active_restore_after_switch():
    runbook = (ROOT / "docs" / "operations" / "database-backup-restore.md").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(runbook.lower().split())

    assert "retain the newly active restored database" in normalized
    assert "never drop the active restored database" in normalized
    assert "retain the old source database for the rollback window" in normalized
    assert "remove the dump according to the retention policy" in normalized
    assert "abort before switch" in normalized
    assert "may drop the failed restore database" in normalized


def test_ci_has_isolated_task21_migration_release_contract():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    job = workflow[workflow.index("  task21_migration_release:") :]
    next_job = re.search(r"\n  [a-zA-Z0-9_]+:\n", job[1:])
    if next_job is not None:
        job = job[: next_job.start() + 1]

    assert "postgres:17-alpine@sha256:" in job
    assert "--name dragon-radar-task21-migration-release" in job
    assert "-p 5432" not in job
    assert "openssl rand" in job
    assert "pg_isready" in job
    assert job.index("uv sync --locked") < job.index("uv build")
    assert "--wheel .temp/dist" in job
    assert "--restore-container dragon-radar-task21-migration-release" in job
    assert "if: always()" in job
    assert "docker rm -f dragon-radar-task21-migration-release" in job
    assert "actions/upload-artifact@v4" in job
    assert ".temp/acceptance/" in job


def test_focused_verifier_rejects_unlabeled_container_before_mutation(
    verifier,
    tmp_path,
    monkeypatch,
):
    globals_ = verifier["exercise_backup_restore"].__globals__
    events = []

    def run_process(argv, **_kwargs):
        events.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "false\n", "")

    monkeypatch.setitem(globals_, "_run_process", run_process)
    monkeypatch.setitem(
        globals_,
        "_git_process",
        lambda _root, argv, **_kwargs: subprocess.CompletedProcess(
            argv,
            0,
            "" if argv[0] == "for-each-ref" else audited_head() + "\n",
            "",
        ),
    )
    monkeypatch.setitem(
        globals_,
        "_create_restore_drill_resources",
        lambda *_args, **_kwargs: pytest.fail("mutation preceded label validation"),
    )

    with pytest.raises(ValueError, match="dragon-radar.task21-release=true"):
        verifier["exercise_backup_restore"](
            "task21-postgres",
            "postgresql://admin:secret@localhost/postgres",
            tmp_path / "restore.json",
        )

    assert events == [
        [
            "docker",
            "inspect",
            "--format",
            '{{ index .Config.Labels "dragon-radar.task21-release" }}',
            "task21-postgres",
        ]
    ]


def test_focused_verifier_rejects_nonmaintenance_url_before_mutation(
    verifier,
    tmp_path,
    monkeypatch,
):
    globals_ = verifier["exercise_backup_restore"].__globals__
    mutated = False

    def mutate(*_args, **_kwargs):
        nonlocal mutated
        mutated = True

    monkeypatch.setitem(
        globals_,
        "_run_process",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "true\n", ""),
    )
    monkeypatch.setitem(
        globals_,
        "_git_process",
        lambda _root, argv, **_kwargs: subprocess.CompletedProcess(
            argv,
            0,
            "" if argv[0] == "for-each-ref" else audited_head() + "\n",
            "",
        ),
    )
    monkeypatch.setitem(globals_, "_create_restore_drill_resources", mutate)

    with pytest.raises(ValueError, match="maintenance database postgres"):
        verifier["exercise_backup_restore"](
            "task21-postgres",
            "postgresql://admin:secret@localhost/arbitrary",
            tmp_path / "restore.json",
        )

    assert mutated is False


def test_focused_verifier_generates_unique_disposable_identities(verifier):
    first = verifier["_new_restore_drill_resources"]()
    second = verifier["_new_restore_drill_resources"]()

    assert first.keys() == {
        "role",
        "source_database",
        "restore_database",
        "dump_path",
        "password",
    }
    assert second.keys() == first.keys()
    assert first != second
    for identity in ("role", "source_database", "restore_database", "dump_path"):
        assert first[identity] != second[identity]
    assert re.fullmatch(r"task21_role_[0-9a-f]{32}", first["role"])
    assert re.fullmatch(r"task21_source_[0-9a-f]{32}", first["source_database"])
    assert re.fullmatch(r"task21_restore_[0-9a-f]{32}", first["restore_database"])
    assert first["dump_path"] == f"/tmp/{first['restore_database']}.dump"
    assert first["password"] not in json.dumps(
        {key: value for key, value in first.items() if key != "password"}
    )


def test_focused_verifier_fence_orders_revocation_and_proves_app_denial(
    verifier,
    monkeypatch,
):
    globals_ = verifier["_fence_restore_source"].__globals__
    calls = []

    def run_process(argv, **kwargs):
        calls.append((list(argv), kwargs))
        sql = argv[-1]
        if "SELECT count(*) FROM pg_stat_activity" in sql:
            return subprocess.CompletedProcess(argv, 0, "0\n", "")
        if "--username" in argv and argv[argv.index("--username") + 1].startswith(
            "task21_role_"
        ):
            return subprocess.CompletedProcess(
                argv,
                2,
                "",
                "FATAL: permission denied for database",
            )
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setitem(globals_, "_run_process", run_process)
    verifier["_fence_restore_source"](
        "task21-postgres",
        "admin",
        "task21_source_" + ("a" * 32),
        "task21_role_" + ("b" * 32),
        "unreported-password",
    )

    sql = [argv[-1] for argv, _kwargs in calls]
    assert "default_transaction_read_only = on" in sql[0]
    assert sql[1].startswith("REVOKE CONNECT ON DATABASE")
    assert "FROM PUBLIC" in sql[1]
    assert sql[2].startswith("REVOKE CONNECT ON DATABASE")
    assert 'FROM "task21_role_' in sql[2]
    assert "pg_terminate_backend" in sql[3]
    assert "SELECT count(*) FROM pg_stat_activity" in sql[4]
    assert "CREATE TABLE task21_write_denied" in sql[5]
    assert calls[5][1]["env"]["PGPASSWORD"] == "unreported-password"


def test_focused_verifier_fingerprint_contract_includes_sequences_and_snapshot(
    verifier,
):
    source = SCRIPT.read_text(encoding="utf-8")
    function = source[
        source.index("def _database_fingerprint(") : source.index(
            "\ndef _restore_sentinel_counts",
            source.index("def _database_fingerprint("),
        )
    ]

    assert "REPEATABLE READ READ ONLY" in function
    for required in (
        "public",
        "last_value",
        "is_called",
        "increment",
        "cycle",
    ):
        assert required in function

    base = {
        "tables": [{"qualified_name": "public.example", "rows": 0}],
        "sequences": [
            {
                "qualified_name": "public.example_id_seq",
                "last_value": 1,
                "is_called": False,
                "increment": 1,
                "cycle": False,
            }
        ],
    }
    drifted = deepcopy(base)
    drifted["sequences"][0]["last_value"] = 2
    assert verifier["_hash_database_state"](base) != verifier["_hash_database_state"](
        drifted
    )


def test_focused_verifier_detects_sequence_only_drift(
    verifier,
    database_url,
):
    from sqlalchemy import create_engine, text

    sequence = "task21_fingerprint_" + os.urandom(8).hex()
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SEQUENCE public."{sequence}"'))
        before = verifier["_database_fingerprint"](database_url)
        with engine.begin() as connection:
            connection.execute(text(f"SELECT nextval('public.\"{sequence}\"')"))
        after = verifier["_database_fingerprint"](database_url)
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SEQUENCE IF EXISTS public."{sequence}"'))
        engine.dispose()

    assert before != after


def test_focused_verifier_docs_and_ci_preserve_production_frozen_state():
    runbook = (ROOT / "docs/operations/database-backup-restore.md").read_text(
        encoding="utf-8"
    )
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    normalized = " ".join(runbook.lower().split())
    job = workflow[workflow.index("  task21_migration_release:") :]

    assert "disposable drill cleanup" in normalized
    assert "production failure stays frozen" in normalized
    assert "explicit approved thaw" in normalized
    assert "retain the newly active restored database" in normalized
    assert "retain the old source database for the rollback window" in normalized
    assert "--label dragon-radar.task21-release=true" in job
    assert re.search(
        r"DRAGON_TEST_DATABASE_URL=.*?/postgres(?:\"|$)",
        job,
        flags=re.MULTILINE,
    )
