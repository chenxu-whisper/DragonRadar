"""Release migration manifest tests freeze the Task 00-20 Alembic graph."""

import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

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


def test_cli_returns_nonzero_with_actionable_errors(
    verifier, copied_migrations, capsys
):
    path = copied_migrations / "versions" / "0010_ai_tasks.py"
    path.write_bytes(path.read_bytes() + b"# changed\n")

    assert verifier["main"](["--root", str(copied_migrations)]) == 1
    assert "SHA-256 mismatch: 0010_ai_tasks.py" in capsys.readouterr().err


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
    assert expected["schema_version"] == 1
    for collection in ("tables", "constraints", "indexes", "triggers"):
        assert expected[collection] == sorted(
            expected[collection],
            key=lambda item: json.dumps(item, sort_keys=True),
        )
    assert database.ready()


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
