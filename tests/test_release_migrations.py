"""Release migration manifest tests freeze the Task 00-20 Alembic graph."""

import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
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
        "expression": None,
        "key_expressions": [
            "security_id",
            "state_type",
            "daterange(valid_from, valid_to, '[)'::text)",
        ],
        "kind": "exclusion",
        "name": "security_state_history_no_overlap",
        "operators": ["=", "=", "&&"],
        "referenced_columns": [],
        "referenced_table": None,
        "table": "security_state_history",
    }
    assert database.ready()


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

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    verifier["require_clean_migration_tree"](ROOT)

    assert observed["argv"] == [
        "git",
        "diff",
        "--quiet",
        "HEAD",
        "--",
        "src/dragon_radar/migrations",
    ]
    assert observed["kwargs"]["cwd"] == ROOT
    assert observed["kwargs"]["shell"] is False


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
