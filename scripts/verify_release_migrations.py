"""Verify that the packaged Alembic graph matches the frozen release manifest."""

from __future__ import annotations

import argparse
import glob
import hashlib
import importlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

_external_pythonpath = os.environ.pop("PYTHONPATH", None)
if _external_pythonpath:
    external_entries = {
        os.path.normcase(os.path.abspath(entry))
        for entry in _external_pythonpath.split(os.pathsep)
        if entry
    }
    sys.path[:] = [
        entry
        for entry in sys.path
        if os.path.normcase(os.path.abspath(entry)) not in external_entries
    ]
if os.environ.pop("PYTHONHOME", None) is not None:
    raise RuntimeError("PYTHONHOME is not allowed for release migration verification")

command = importlib.import_module("alembic.command")
Config = importlib.import_module("alembic.config").Config
ScriptDirectory = importlib.import_module("alembic.script").ScriptDirectory

MANIFEST_SCHEMA = "dragon-radar.migration-release-manifest"
MANIFEST_SCHEMA_VERSION = 1
AUDIT_SCHEMA = "dragon-radar.release-migration-verification"
AUDIT_SCHEMA_VERSION = 1
RELEASE_HEAD = "0013_query"
ROLLBACK_MODE = "backup_restore_only"
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
    "0011_realtime",
    "0012_lhb",
    "0013_query",
]
MANIFEST_FIELDS = {
    "schema",
    "schema_version",
    "head",
    "rollback_mode",
    "supported_from",
    "revisions",
}
REVISION_FIELDS = {"filename", "revision", "parent", "sha256", "bytes"}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
DEFAULT_MIGRATIONS = (
    Path(__file__).resolve().parents[1] / "src" / "dragon_radar" / "migrations"
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DANGEROUS_GIT_ENVIRONMENT = (
    "GIT_REPLACE_REF_BASE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
)


def _safe_git_environment() -> dict[str, str]:
    for name in DANGEROUS_GIT_ENVIRONMENT:
        if name in os.environ:
            raise ValueError(f"unsafe Git environment: {name}")
    environment = os.environ.copy()
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    return environment


def _git_process(
    repository_root: Path,
    argv: Sequence[str],
    *,
    text: bool = True,
    timeout: float = 30,
) -> subprocess.CompletedProcess:
    return _run_process(
        ["git", "--no-replace-objects", *argv],
        cwd=repository_root,
        env=_safe_git_environment(),
        timeout=timeout,
        text=text,
    )


def _reject_git_replace_refs(repository_root: Path) -> None:
    result = _git_process(
        repository_root,
        ["for-each-ref", "--format=%(refname)", "refs/replace/"],
    )
    if result.returncode != 0:
        raise ValueError("cannot inspect Git replace refs")
    if result.stdout.strip():
        raise ValueError("Git replace refs are not allowed during release verification")


def canonical_migration_bytes(path: Path) -> bytes:
    """Return source bytes with checkout-specific CRLF normalized to LF."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def migration_fingerprint(path: Path) -> tuple[str, int]:
    """Return the canonical source SHA-256 and byte count."""
    content = canonical_migration_bytes(path)
    return hashlib.sha256(content).hexdigest(), len(content)


def migration_sha256(path: Path) -> str:
    """Return the SHA-256 of the file's canonical source bytes."""
    return migration_fingerprint(path)[0]


def _require_exact_fields(
    value: object,
    required: set[str],
    *,
    location: str,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be a JSON object")
    fields = set(value)
    missing = sorted(required - fields)
    if missing:
        raise ValueError(f"{location} missing field: {missing[0]}")
    unexpected = sorted(fields - required)
    if unexpected:
        raise ValueError(f"{location} unexpected field: {unexpected[0]}")
    return value


def _require_string(value: object, *, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{location} must be a non-empty string")
    return value


def _reject_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _validate_revision_entry(
    raw_entry: object,
    *,
    index: int,
) -> dict[str, object]:
    location = f"revision entry {index}"
    entry = _require_exact_fields(raw_entry, REVISION_FIELDS, location=location)
    filename = _require_string(entry["filename"], location=f"{location}.filename")
    if (
        Path(filename).name != filename
        or "/" in filename
        or "\\" in filename
        or not filename.endswith(".py")
    ):
        raise ValueError(f"{location}.filename must be a safe filename")

    revision = _require_string(entry["revision"], location=f"{location}.revision")
    parent = entry["parent"]
    if parent is not None:
        _require_string(parent, location=f"{location}.parent")
    sha256 = _require_string(entry["sha256"], location=f"{location}.sha256")
    if SHA256_PATTERN.fullmatch(sha256) is None:
        raise ValueError(f"{location}.sha256 must be 64 lowercase hex characters")
    byte_count = entry["bytes"]
    if (
        not isinstance(byte_count, int)
        or isinstance(byte_count, bool)
        or byte_count < 0
    ):
        raise ValueError(f"{location}.bytes must be a non-negative integer")
    if Path(filename).stem != revision:
        raise ValueError(f"{location}.filename must match its revision: {revision}")
    return entry


def load_release_manifest(path: Path) -> dict[str, object]:
    """Load and strictly validate a migration release manifest."""
    try:
        raw_manifest = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read release manifest {path}: {exc}") from exc

    manifest = _require_exact_fields(
        raw_manifest,
        MANIFEST_FIELDS,
        location="manifest",
    )
    expected_values = {
        "schema": MANIFEST_SCHEMA,
        "head": RELEASE_HEAD,
        "rollback_mode": ROLLBACK_MODE,
        "supported_from": SUPPORTED_FROM,
    }
    if (
        not isinstance(manifest["schema_version"], int)
        or isinstance(manifest["schema_version"], bool)
        or manifest["schema_version"] != MANIFEST_SCHEMA_VERSION
    ):
        raise ValueError(
            "manifest schema_version must be "
            f"{MANIFEST_SCHEMA_VERSION!r}, got {manifest['schema_version']!r}"
        )
    for field, expected in expected_values.items():
        if manifest[field] != expected:
            raise ValueError(
                f"manifest {field} must be {expected!r}, got {manifest[field]!r}"
            )

    raw_revisions = manifest["revisions"]
    if not isinstance(raw_revisions, list):
        raise ValueError("manifest revisions must be a JSON array")
    revisions = [
        _validate_revision_entry(entry, index=index)
        for index, entry in enumerate(raw_revisions)
    ]
    revision_ids = [str(entry["revision"]) for entry in revisions]
    if len(set(revision_ids)) != len(revision_ids):
        raise ValueError("manifest contains a duplicate revision")
    filenames = [str(entry["filename"]) for entry in revisions]
    if len(set(filenames)) != len(filenames):
        raise ValueError("manifest contains a duplicate filename")
    if revision_ids != sorted(revision_ids):
        raise ValueError("manifest revisions must be sorted by revision")
    return manifest


def _parent_value(down_revision: object) -> str | list[str] | None:
    if down_revision is None or isinstance(down_revision, str):
        return down_revision
    return sorted(str(parent) for parent in down_revision)


def inspect_alembic_graph(root: Path) -> dict[str, object]:
    """Read revision and parent metadata through Alembic's ScriptDirectory."""
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"migration root is not a directory: {root}")
    if not (root / "versions").is_dir():
        raise ValueError(f"migration root has no versions directory: {root}")

    scripts = ScriptDirectory(str(root))
    revisions = []
    for script in scripts.walk_revisions(base="base", head="heads"):
        revisions.append(
            {
                "filename": Path(script.path).name,
                "revision": script.revision,
                "parent": _parent_value(script.down_revision),
            }
        )
    revisions.sort(key=lambda item: str(item["revision"]))

    child_counts: Counter[str] = Counter()
    parents_are_single = True
    for entry in revisions:
        parent = entry["parent"]
        if isinstance(parent, str):
            child_counts[parent] += 1
        elif isinstance(parent, list):
            parents_are_single = False
            child_counts.update(parent)
    revision_ids = {str(entry["revision"]) for entry in revisions}
    bases = sorted(scripts.get_bases())
    heads = sorted(scripts.get_heads())
    linear = (
        len(bases) == 1
        and len(heads) == 1
        and parents_are_single
        and all(
            child_counts[revision] == (0 if revision in heads else 1)
            for revision in revision_ids
        )
    )
    return {
        "bases": bases,
        "heads": heads,
        "linear": linear,
        "revision_count": len(revisions),
        "revisions": revisions,
    }


def _resolve_migration_root(root: Path) -> Path:
    if root.is_symlink() or _is_junction(root):
        raise ValueError("unsafe migration path: root")
    if (root / "release_manifest.json").is_file():
        return root
    package_root = root / "src" / "dragon_radar" / "migrations"
    if package_root.is_symlink() or _is_junction(package_root):
        raise ValueError("unsafe migration path: root")
    if package_root.is_dir():
        return package_root
    return root


def _is_junction(path: Path) -> bool:
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def _validate_migration_files(
    root: Path,
    manifest: dict[str, object],
) -> list[str]:
    errors: list[str] = []
    versions_root = root / "versions"
    if versions_root.is_symlink() or _is_junction(versions_root):
        return ["unsafe migration path: versions"]
    if not versions_root.is_dir():
        return [f"migration root has no versions directory: {root}"]

    manifest_entries = {
        str(entry["filename"]): entry for entry in manifest["revisions"]
    }
    expected_names = set(manifest_entries)
    actual_names = {
        entry.name for entry in versions_root.iterdir() if entry.name.endswith(".py")
    }
    if expected_names != actual_names:
        missing = sorted(expected_names - actual_names)
        unlisted = sorted(actual_names - expected_names)
        return [f"revision set mismatch: missing={missing}, unlisted={unlisted}"]

    canonical_versions_root = versions_root.resolve(strict=True)
    migration_paths: dict[str, Path] = {}
    for filename in sorted(expected_names):
        path = versions_root / filename
        if (
            path.is_symlink()
            or _is_junction(path)
            or path.resolve(strict=False).parent != canonical_versions_root
        ):
            errors.append(f"unsafe migration path: {filename}")
            continue
        if not path.is_file():
            errors.append(f"migration file missing: {filename}")
            continue
        migration_paths[filename] = path
    if errors:
        return errors

    for filename, path in migration_paths.items():
        expected = manifest_entries[filename]
        sha256, byte_count = migration_fingerprint(path)
        if sha256 != expected["sha256"]:
            errors.append(f"SHA-256 mismatch: {filename}")
        if byte_count != expected["bytes"]:
            errors.append(f"byte count mismatch: {filename}")
    return errors


def verify_manifest(root: Path) -> dict[str, object]:
    """Compare the graph and raw migration files with the release manifest."""
    root = _resolve_migration_root(root)
    versions_root = root / "versions"
    if versions_root.is_symlink() or _is_junction(versions_root):
        return {
            "head": None,
            "revision_count": 0,
            "errors": ["unsafe migration path: versions"],
        }
    root = root.resolve()
    manifest = load_release_manifest(root / "release_manifest.json")
    errors = _validate_migration_files(root, manifest)
    if errors:
        return {
            "head": None,
            "revision_count": 0,
            "errors": errors,
        }

    graph = inspect_alembic_graph(root)

    bases = graph["bases"]
    heads = graph["heads"]
    if len(bases) != 1:
        errors.append(f"expected exactly one base, found {bases}")
    if len(heads) != 1:
        errors.append(f"expected exactly one head, found {heads}")
    if not graph["linear"]:
        errors.append("migration graph must be a single linear path")
    if heads != [manifest["head"]]:
        errors.append(
            f"manifest head mismatch: expected {manifest['head']}, found {heads}"
        )

    manifest_entries = {
        str(entry["revision"]): entry for entry in manifest["revisions"]
    }
    graph_entries = {str(entry["revision"]): entry for entry in graph["revisions"]}
    manifest_ids = set(manifest_entries)
    graph_ids = set(graph_entries)
    if manifest_ids != graph_ids:
        missing = sorted(manifest_ids - graph_ids)
        unlisted = sorted(graph_ids - manifest_ids)
        errors.append(f"revision set mismatch: missing={missing}, unlisted={unlisted}")

    for revision in sorted(manifest_ids & graph_ids):
        expected = manifest_entries[revision]
        actual = graph_entries[revision]
        if expected["filename"] != actual["filename"]:
            errors.append(
                f"filename mismatch for {revision}: "
                f"expected {expected['filename']}, found {actual['filename']}"
            )
        if expected["parent"] != actual["parent"]:
            errors.append(
                f"parent mismatch for {revision}: "
                f"expected {expected['parent']}, found {actual['parent']}"
            )

    return {
        "head": heads[0] if len(heads) == 1 else None,
        "revision_count": graph["revision_count"],
        "errors": errors,
    }


def _committed_blob(repository_root: Path, commit: str, path: str) -> bytes:
    result = _git_process(
        repository_root,
        ["cat-file", "blob", f"{commit}:{path}"],
        text=False,
    )
    if result.returncode != 0:
        raise ValueError(f"cannot read committed Git blob: {path}")
    return result.stdout


def verify_repository_commit(
    repository_root: Path,
    audited_commit: str,
) -> dict[str, object]:
    """Verify the complete frozen migration contract from one Git commit."""
    repository_root = Path(repository_root).resolve()
    audited_commit = validate_audited_commit(audited_commit)
    _reject_git_replace_refs(repository_root)
    verify_commit = _git_process(
        repository_root,
        ["cat-file", "-e", f"{audited_commit}^{{commit}}"],
        text=False,
    )
    if verify_commit.returncode != 0:
        raise ValueError("audited commit is not available")
    prefix = "src/dragon_radar/migrations"
    listed = _git_process(
        repository_root,
        [
            "-c",
            "core.quotepath=false",
            "ls-tree",
            "-r",
            "--name-only",
            audited_commit,
            "--",
            prefix,
        ],
    )
    if listed.returncode != 0:
        raise ValueError("cannot enumerate committed migrations")
    paths = [line for line in listed.stdout.splitlines() if line]
    if not paths or any(
        not path.startswith(f"{prefix}/") or "\\" in path or ".." in Path(path).parts
        for path in paths
    ):
        raise ValueError("committed migration paths are invalid")

    release_path = f"{prefix}/release_manifest.json"
    schema_path = "src/dragon_radar/storage/schema_contract.json"
    verifier_path = "scripts/verify_release_migrations.py"
    validator_path = "src/dragon_radar/storage/schema_contract.py"
    release_bytes = _committed_blob(repository_root, audited_commit, release_path)
    schema_bytes = _committed_blob(repository_root, audited_commit, schema_path)
    verifier_bytes = _committed_blob(repository_root, audited_commit, verifier_path)
    validator_bytes = _committed_blob(repository_root, audited_commit, validator_path)
    try:
        schema_contract = json.loads(
            schema_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        validator_globals = {
            "__name__": "_audited_schema_contract",
            "__file__": validator_path,
        }
        exec(
            compile(validator_bytes, validator_path, "exec"),
            validator_globals,
        )
        validate_schema_contract = validator_globals["validate_schema_contract"]
        validate_schema_contract(schema_contract)
    except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("committed schema contract is invalid") from exc

    with tempfile.TemporaryDirectory(
        prefix="dragon-radar-migration-audit-"
    ) as temporary:
        migration_root = Path(temporary) / "migrations"
        for path in paths:
            relative = Path(path).relative_to(prefix)
            target = migration_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_committed_blob(repository_root, audited_commit, path))
        graph = verify_manifest(migration_root)

    return {
        "schema": AUDIT_SCHEMA,
        "schema_version": AUDIT_SCHEMA_VERSION,
        "audited_commit": audited_commit,
        "head": graph["head"],
        "revision_count": graph["revision_count"],
        "release_manifest_sha256": hashlib.sha256(release_bytes).hexdigest(),
        "schema_contract_sha256": hashlib.sha256(schema_bytes).hexdigest(),
        "verifier_sha256": hashlib.sha256(verifier_bytes).hexdigest(),
        "schema_validator_sha256": hashlib.sha256(validator_bytes).hexdigest(),
        "errors": graph["errors"],
    }


def require_clean_migration_tree(repository_root: Path = REPOSITORY_ROOT) -> None:
    """Reject staged or unstaged changes to tracked migration files."""
    result = _git_process(
        repository_root,
        [
            "diff",
            "--quiet",
            "HEAD",
            "--",
            "src/dragon_radar/migrations",
        ],
    )
    if result.returncode != 0:
        raise ValueError("tracked migrations differ from HEAD")


def _validated_postgres_url(database_url: str):
    from sqlalchemy.engine import make_url

    try:
        url = make_url(database_url)
    except Exception:
        raise ValueError("invalid PostgreSQL database URL") from None
    if url.drivername not in ("postgresql", "postgresql+psycopg"):
        raise ValueError("PostgreSQL with psycopg is required")
    return url.set(drivername="postgresql+psycopg")


def _create_admin_engine(url):
    from sqlalchemy import create_engine

    return create_engine(
        url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 3},
    )


def _database_class():
    from dragon_radar.storage.database import Database

    return Database


def _schema_contract_functions():
    from dragon_radar.storage.schema_contract import (
        capture_schema_contract,
        validate_schema_contract,
    )

    return capture_schema_contract, validate_schema_contract


def _new_schema_name() -> str:
    return f"schema_contract_{uuid4().hex}"


def _new_upgrade_schema_name(schema_prefix: str | None = None) -> str:
    if schema_prefix is None:
        return f"release_upgrade_{uuid4().hex}"
    _validate_wheel_schema_prefix(schema_prefix)
    return f"{schema_prefix}{uuid4().hex[:16]}"


def _new_wheel_schema_prefix() -> str:
    return f"release_wheel_{secrets.token_hex(16)}_"


def _validate_wheel_schema_prefix(schema_prefix: str) -> None:
    if re.fullmatch(r"release_wheel_[0-9a-f]{32}_", schema_prefix) is None:
        raise ValueError("unsafe wheel schema prefix")


def _validate_upgrade_schema_name(schema: str, schema_prefix: str | None) -> None:
    pattern = (
        r"release_upgrade_[0-9a-f]{32}"
        if schema_prefix is None
        else re.escape(schema_prefix) + r"[0-9a-f]{16}"
    )
    if re.fullmatch(pattern, schema) is None:
        raise ValueError("unsafe generated schema name")


def _upgrade(connection, revision: str) -> None:
    config = Config()
    config.set_main_option("script_location", "dragon_radar:migrations")
    config.attributes["connection"] = connection
    command.upgrade(config, revision)


def _sentinel_values() -> dict[str, object]:
    token = uuid4().hex
    return {
        "token": token,
        "source_id": f"task21.{token}",
        "owner_id": uuid4(),
        "batch_id": uuid4(),
        "snapshot_id": uuid4(),
        "topic_batch_id": uuid4(),
        "topic_attempt_id": uuid4(),
        "topic_snapshot_id": uuid4(),
        "market_task_id": uuid4(),
        "market_attempt_id": uuid4(),
        "security_id": f"S{token[:12]}",
        "role_run_id": uuid4(),
        "publication_id": uuid4(),
        "analysis_job_id": uuid4(),
        "article_id": uuid4(),
        "article_revision_id": uuid4(),
        "rule_record_id": uuid4(),
        "rule_id": f"R{token[:12]}",
        "event_id": f"E{token[:12]}",
        "regulation_snapshot_id": uuid4(),
        "ai_task_id": uuid4(),
        "realtime_batch_record_id": uuid4(),
        "realtime_batch_id": f"B{token[:12]}",
        "realtime_minute_id": uuid4(),
        "realtime_event_id": f"RT{token[:12]}",
        "lhb_batch_record_id": uuid4(),
        "lhb_batch_id": f"LHB{token[:12]}",
        "lhb_disclosure_record_id": uuid4(),
        "lhb_disclosure_id": f"LD{token[:12]}",
        "lhb_revision_record_id": uuid4(),
        "lhb_seat_record_a": uuid4(),
        "lhb_seat_record_b": uuid4(),
        "lhb_seat_id_a": f"LA{token[:12]}",
        "lhb_seat_id_b": f"LB{token[:12]}",
        "lhb_row_record_a": uuid4(),
        "lhb_row_record_b": uuid4(),
        "query_record_id": uuid4(),
        "hash": hashlib.sha256(token.encode("ascii")).hexdigest(),
    }


def _insert_source(connection, values: dict[str, object]) -> None:
    from sqlalchemy import text

    connection.execute(
        text(
            """
            INSERT INTO source_grants (source_id, "grant", updated_at)
            VALUES (:source_id, CAST(:grant AS jsonb), :now)
            """
        ),
        {
            **values,
            "grant": json.dumps({"source_id": values["source_id"], "approved": True}),
            "now": "2026-01-02T03:04:05+00:00",
        },
    )


def _insert_market_archive(
    connection,
    values: dict[str, object],
    *,
    with_idempotency_hash: bool = False,
) -> None:
    from sqlalchemy import text

    _insert_source(connection, values)
    batch_sql = (
        """
        INSERT INTO import_batches (
            id, idempotency_key, source_id, trade_date, canonical_json,
            payload_hash, quality_report, imported_at, idempotency_hash
        ) VALUES (
            :batch_id, :token, :source_id, DATE '2026-01-02',
            '{"as_of":"2026-01-02T03:04:05+00:00"}', :hash, '{}',
            TIMESTAMPTZ '2026-01-02T03:04:05+00:00', :hash
        )
        """
        if with_idempotency_hash
        else """
        INSERT INTO import_batches (
            id, idempotency_key, source_id, trade_date, canonical_json,
            payload_hash, quality_report, imported_at
        ) VALUES (
            :batch_id, :token, :source_id, DATE '2026-01-02',
            '{"as_of":"2026-01-02T03:04:05+00:00"}', :hash, '{}',
            TIMESTAMPTZ '2026-01-02T03:04:05+00:00'
        )
        """
    )
    connection.execute(
        text(batch_sql),
        values,
    )
    connection.execute(
        text(
            """
            INSERT INTO snapshots (
                id, batch_id, source_id, trade_date, revision, manifest,
                published_at
            ) VALUES (
                :snapshot_id, :batch_id, :source_id, DATE '2026-01-02', 1,
                '{}', TIMESTAMPTZ '2026-01-02T03:04:05+00:00'
            )
            """
        ),
        values,
    )


def _insert_publication(connection, values: dict[str, object]) -> None:
    from sqlalchemy import text

    connection.execute(
        text(
            """
            INSERT INTO publications (
                id, trade_date, version, status, market_snapshot_id, domains,
                coverage, quality_flags, command_json, command_hash, created_at
            ) VALUES (
                :publication_id, DATE '2026-01-02', 1, 'draft', :snapshot_id,
                '{}', '{}', '[]', '{}', :hash,
                TIMESTAMPTZ '2026-01-02T03:04:05+00:00'
            )
            """
        ),
        values,
    )


def _insert_rule(connection, values: dict[str, object]) -> None:
    from sqlalchemy import text

    connection.execute(
        text(
            """
            INSERT INTO regulation_rules (
                id, rule_id, rule_version, effective_from, window_days,
                direction, threshold, warning_threshold, calculation_method,
                review_switch, test_cases, regression_passed, active, created_at
            ) VALUES (
                :rule_record_id, :rule_id, 'v1', DATE '2026-01-01', 5,
                'upper', 10, 8, 'relative_return', 'pending', '[]', false,
                false, TIMESTAMPTZ '2026-01-02T03:04:05+00:00'
            )
            """
        ),
        values,
    )


def _insert_sentinel(
    connection,
    revision: str,
    values: dict[str, object],
) -> tuple[str, dict[str, object]]:
    from sqlalchemy import text

    now = "2026-01-02T03:04:05+00:00"
    if revision == "0001_foundation":
        _insert_market_archive(connection, values)
        audit_id = uuid4()
        connection.execute(
            text(
                """
                INSERT INTO audit_events (
                    id, action, source_id, object_id, details, occurred_at
                ) VALUES (
                    :audit_id, 'task21_upgrade', :source_id, :token, '{}', :now
                )
                """
            ),
            {**values, "audit_id": audit_id, "now": now},
        )
        return (
            "foundation_archive",
            {
                "snapshot_id": values["snapshot_id"],
                "payload_hash": values["hash"],
                "audit_id": audit_id,
            },
        )
    if revision == "0003_private_review":
        audit_id = uuid4()
        connection.execute(
            text(
                """
                INSERT INTO owner_principals (id, local_key, created_at)
                VALUES (:owner_id, NULL, :now)
                """
            ),
            {**values, "audit_id": audit_id, "now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO private_audit_events (
                    id, owner_id, action, object_id, version, occurred_at
                ) VALUES (
                    :audit_id, :owner_id, 'task21_upgrade', :owner_id, 1, :now
                )
                """
            ),
            {**values, "audit_id": audit_id, "now": now},
        )
        return "private_audit", {"id": audit_id, "owner_id": values["owner_id"]}
    if revision == "0004a_topic_attempt_ranges":
        _insert_source(connection, values)
        connection.execute(
            text(
                """
                INSERT INTO topic_import_batches (
                    id, idempotency_key, source_id, trade_date, canonical_json,
                    payload_hash, quality_report, imported_at
                ) VALUES (
                    :topic_batch_id, :token, :source_id, DATE '2026-01-02',
                    '{}', :hash, '{}', :now
                )
                """
            ),
            {**values, "now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO topic_import_attempts (
                    id, batch_id, idempotency_key, payload_hash, source_id,
                    embedded_grant, status, stage, missing_items, details,
                    created_at, request_from, request_to
                ) VALUES (
                    :topic_attempt_id, :topic_batch_id, :token || '.attempt',
                    :hash, :source_id, '{}', 'succeeded', 'complete', '[]',
                    '{}', :now, DATE '2026-01-02', DATE '2026-01-02'
                )
                """
            ),
            {**values, "now": now},
        )
        return (
            "topic_attempt_batch",
            {"id": values["topic_attempt_id"], "payload_hash": values["hash"]},
        )
    if revision == "0006_market_readmodels":
        _insert_market_archive(connection, values, with_idempotency_hash=True)
        connection.execute(
            text(
                """
                INSERT INTO market_import_tasks (
                    id, idempotency_key, source_id, embedded_grant, status,
                    request_from, request_to, created_at, updated_at
                ) VALUES (
                    :market_task_id, :token || '.task', :source_id, '{}',
                    'succeeded', DATE '2026-01-02', DATE '2026-01-02', :now, :now
                )
                """
            ),
            {**values, "now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO market_import_attempts (
                    id, task_id, batch_id, idempotency_key, envelope_hash,
                    source_id, embedded_grant, status, stage, page_manifest,
                    missing_items, request_from, request_to, details, created_at
                ) VALUES (
                    :market_attempt_id, :market_task_id, :batch_id,
                    :token || '.attempt', :hash, :source_id, '{}', 'succeeded',
                    'complete', '{}', '[]', DATE '2026-01-02',
                    DATE '2026-01-02', '{}', :now
                )
                """
            ),
            {**values, "now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO security (
                    security_id, code, name, security_type, exchange, board,
                    list_date, is_st, is_suspended, source, source_version,
                    available_at, source_archive_id
                ) VALUES (
                    :security_id, 'T21', 'Task21 Sentinel', 'stock', 'SSE',
                    'main', DATE '2020-01-01', false, false, :source_id, 'v1',
                    :now, :batch_id
                )
                """
            ),
            {**values, "now": now},
        )
        return (
            "security_market_attempt",
            {"id": values["market_attempt_id"], "hash": values["hash"]},
        )
    if revision == "0007_role_readmodels":
        _insert_market_archive(connection, values, with_idempotency_hash=True)
        connection.execute(
            text(
                """
                INSERT INTO topic_import_batches (
                    id, idempotency_key, source_id, trade_date, canonical_json,
                    payload_hash, quality_report, imported_at
                ) VALUES (
                    :topic_batch_id, :token || '.topic', :source_id,
                    DATE '2026-01-02', '{"topics":[]}', :hash, '{}', :now
                )
                """
            ),
            {**values, "now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO topic_snapshots (
                    id, batch_id, source_id, trade_date, revision, manifest,
                    published_at
                ) VALUES (
                    :topic_snapshot_id, :topic_batch_id, :source_id,
                    DATE '2026-01-02', 1, '{}', :now
                )
                """
            ),
            {**values, "now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO role_runs (
                    id, idempotency_key, payload_hash, market_snapshot_id,
                    topic_snapshot_id, trade_date, knowledge_cutoff,
                    rule_version, rule_json, rule_hash, benchmark_id,
                    visibility_mode, market_source_id, market_source_version,
                    market_pool_version, adjustment_version, calendar_version,
                    topic_source_id, topic_source_version, taxonomy_version,
                    membership_version, status, created_at
                ) VALUES (
                    :role_run_id, :token, :hash, :snapshot_id,
                    :topic_snapshot_id, DATE '2026-01-02', :now, 'v1', '{}',
                    :hash, 'B000300', 'contemporaneous', :source_id, 'v1',
                    'v1', 'v1', 'v1', :source_id, 'v1', 'v1', 'v1', 'draft',
                    :now
                )
                """
            ),
            {**values, "now": now},
        )
        return "role_run_root", {"id": values["role_run_id"], "hash": values["hash"]}
    if revision == "0007c_analysis_jobs":
        _insert_market_archive(connection, values, with_idempotency_hash=True)
        connection.execute(
            text(
                "INSERT INTO owner_principals (id, local_key, created_at) "
                "VALUES (:owner_id, NULL, :now)"
            ),
            {**values, "now": now},
        )
        _insert_publication(connection, values)
        connection.execute(
            text(
                """
                INSERT INTO analysis_jobs (
                    id, owner_id, idempotency_key, request_hash, cache_key,
                    snapshot_id, input_manifest, status, attempt_count,
                    available_at, created_at
                ) VALUES (
                    :analysis_job_id, :owner_id, :token, :hash, :hash,
                    :publication_id, '{}', 'queued', 0, :now, :now
                )
                """
            ),
            {**values, "now": now},
        )
        return (
            "analysis_job",
            {"id": values["analysis_job_id"], "hash": values["hash"]},
        )
    if revision == "0008_news":
        _insert_source(connection, values)
        connection.execute(
            text(
                """
                INSERT INTO news_articles (
                    id, source_id, source_article_id, created_at
                ) VALUES (
                    :article_id, :source_id, :token, :now
                )
                """
            ),
            {**values, "now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO news_article_revisions (
                    id, article_id, revision, source_id, source_version,
                    source_article_id, news_type, title, url, published_at,
                    received_at, available_at, content_scope, is_novel_event,
                    supporting_evidence, opposing_evidence, content_digest,
                    embedded_grant, ingested_at
                ) VALUES (
                    :article_revision_id, :article_id, 1, :source_id, 'v1',
                    :token, 'news', 'Task21 sentinel', 'https://example.test/',
                    :now, :now, :now, 'none', true, '[]', '[]', :hash, '{}',
                    :now
                )
                """
            ),
            {**values, "now": now},
        )
        return (
            "news_article_revision",
            {"id": values["article_revision_id"], "hash": values["hash"]},
        )
    if revision == "0009_regulation":
        _insert_market_archive(connection, values, with_idempotency_hash=True)
        _insert_rule(connection, values)
        connection.execute(
            text(
                """
                INSERT INTO regulation_events (
                    event_id, rule_id, rule_version, security_id, event_type,
                    effective_from, created_at
                ) VALUES (
                    :event_id, :rule_id, 'v1', :security_id, 'monitoring',
                    DATE '2026-01-01', :now
                )
                """
            ),
            {**values, "now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO regulation_snapshots (
                    id, idempotency_key, command_hash, market_snapshot_id,
                    trade_date, as_of, status_sequence_watermark, created_at
                ) VALUES (
                    :regulation_snapshot_id, :token || '.regulation', :hash,
                    :snapshot_id, DATE '2026-01-02', :now, 0, :now
                )
                """
            ),
            {**values, "now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO regulation_snapshot_events (
                    snapshot_id, event_id, position
                ) VALUES (
                    :regulation_snapshot_id, :event_id, 0
                )
                """
            ),
            values,
        )
        return (
            "legacy_regulation_graph",
            {
                "rule_record_id": values["rule_record_id"],
                "event_id": values["event_id"],
                "regulation_snapshot_id": values["regulation_snapshot_id"],
            },
        )
    if revision == "0009d_reg_event_complete_set":
        _insert_source(connection, values)
        _insert_rule(connection, values)
        connection.execute(
            text(
                """
                INSERT INTO regulation_events (
                    event_id, rule_id, rule_version, security_id, event_type,
                    effective_from, source_id, source_version, created_at
                ) VALUES (
                    :event_id, :rule_id, 'v1', :security_id, 'monitoring',
                    DATE '2026-01-01', :source_id, 'v1', :now
                )
                """
            ),
            {**values, "now": now},
        )
        return (
            "regulation_event_provenance",
            {
                "event_id": values["event_id"],
                "source_id": values["source_id"],
            },
        )
    if revision == "0010_ai_tasks":
        _insert_market_archive(connection, values, with_idempotency_hash=True)
        connection.execute(
            text(
                "INSERT INTO owner_principals (id, local_key, created_at) "
                "VALUES (:owner_id, NULL, :now)"
            ),
            {**values, "now": now},
        )
        _insert_publication(connection, values)
        connection.execute(
            text(
                """
                INSERT INTO ai_tasks (
                    id, owner_id, idempotency_key, request_hash, task_type,
                    snapshot_id, as_of, version, input_manifest, status,
                    verification_status, human_review_status, attempt_count,
                    available_at, created_at
                ) VALUES (
                    :ai_task_id, :owner_id, :token, :hash, 'summarize',
                    :publication_id, :now, 'v1', '{}', 'pending', 'pending',
                    'unreviewed', 0, :now, :now
                )
                """
            ),
            {**values, "now": now},
        )
        return "ai_task", {"id": values["ai_task_id"], "hash": values["hash"]}
    if revision == "0011_realtime":
        _insert_source(connection, values)
        ingest_order = connection.scalar(
            text(
                """
                INSERT INTO realtime_quote_batches (
                    id, quote_batch_id, source_id, source_version,
                    source_event_at, received_at, available_at,
                    source_sequence, permission_snapshot, payload_hash,
                    imported_at
                ) VALUES (
                    :realtime_batch_record_id, :realtime_batch_id, :source_id,
                    'v1', :now, :now, :now, 1,
                    '{"permission_id":"task21","state":"authorized"}',
                    :hash, :now
                )
                RETURNING ingest_order
                """
            ),
            {**values, "now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO realtime_minute_bars (
                    id, source_id, batch_id, source_version, security_id,
                    event_at, revision, open, high, low, close, volume, amount,
                    final, source_event_at, received_at, available_at
                ) VALUES (
                    :realtime_minute_id, :source_id, :realtime_batch_id, 'v1',
                    :security_id, :now, 1, 10, 11, 9, 10.5, NULL, NULL, true,
                    :now, :now, :now
                )
                """
            ),
            {**values, "now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO realtime_anomaly_events (
                    event_id, source_id, batch_id, security_id, rule_version,
                    metric, "window", value, threshold, event_at, available_at,
                    quality, cooldown_bucket
                ) VALUES (
                    :realtime_event_id, :source_id, :realtime_batch_id,
                    :security_id, 'return-v1', 'window_return', 3, 0.05, 0.02,
                    :now, :now, 'complete', :now
                )
                """
            ),
            {**values, "now": now},
        )
        return (
            "realtime_evidence",
            {
                "batch_id": values["realtime_batch_id"],
                "ingest_order": ingest_order,
                "minute_id": values["realtime_minute_id"],
                "event_id": values["realtime_event_id"],
            },
        )
    if revision == "0012_lhb":
        _insert_source(connection, values)
        connection.execute(
            text(
                """
                INSERT INTO lhb_import_batches (
                    id, batch_id, source_id, source_version, source_event_at,
                    received_at, available_at, permission_snapshot, payload_hash,
                    status, reason, imported_at
                ) VALUES (
                    :lhb_batch_record_id, :lhb_batch_id, :source_id, 'v1', :now,
                    :now, :now,
                    '{"permission_id":"task21","state":"authorized"}',
                    :hash, 'accepted', NULL, :now
                )
                """
            ),
            {**values, "now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO lhb_disclosures (
                    id, source_id, disclosure_id, security_id, disclosure_date,
                    reason, permission_id
                ) VALUES (
                    :lhb_disclosure_record_id, :source_id, :lhb_disclosure_id,
                    :security_id, DATE '2026-01-02', 'daily price movement',
                    'task21'
                )
                """
            ),
            {**values, "now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO lhb_seats (
                    id, source_id, seat_id, mapping_version, source_seat_name,
                    provenance
                ) VALUES
                    (:lhb_seat_record_a, :source_id, :lhb_seat_id_a,
                     'seat-map-v1', 'task21 branch A', '{}'::jsonb),
                    (:lhb_seat_record_b, :source_id, :lhb_seat_id_b,
                     'seat-map-v1', 'task21 branch B', '{}'::jsonb)
                """
            ),
            {**values, "now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO lhb_disclosure_revisions (
                    id, source_id, disclosure_id, batch_id, revision,
                    source_event_at, received_at, available_at,
                    permission_snapshot, completeness, payload_hash
                ) VALUES (
                    :lhb_revision_record_id, :source_id, :lhb_disclosure_id,
                    :lhb_batch_id, 1, :now, :now, :now,
                    '{"permission_id":"task21","state":"authorized"}',
                    'incomplete', :hash
                )
                """
            ),
            {**values, "now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO lhb_disclosure_seats (
                    id, source_id, disclosure_id, disclosure_date, revision,
                    source_row_id, seat_id, mapping_version, side, "rank",
                    buy_amount, sell_amount, currency, unit, completeness
                ) VALUES
                    (:lhb_row_record_a, :source_id, :lhb_disclosure_id,
                     DATE '2026-01-02', 1, :token || '.lhb-row-a',
                     :lhb_seat_id_a, 'seat-map-v1', 'net', 1,
                     100, 30, 'CNY', 'yuan', 'complete'),
                    (:lhb_row_record_b, :source_id, :lhb_disclosure_id,
                     DATE '2026-01-02', 1, :token || '.lhb-row-b',
                     :lhb_seat_id_b, 'seat-map-v1', NULL, NULL,
                     NULL, 20, 'CNY', 'yuan', 'buy_unknown')
                """
            ),
            {**values, "now": now},
        )
        return (
            "lhb_evidence",
            {
                "batch_id": values["lhb_batch_id"],
                "disclosure_id": values["lhb_disclosure_id"],
                "lhb_seat_id_a": values["lhb_seat_id_a"],
                "lhb_seat_id_b": values["lhb_seat_id_b"],
                "revision": 1,
                "revision_completeness": "incomplete",
                "mapping_version": "seat-map-v1",
                "buy_amount": Decimal("100"),
                "sell_amount": Decimal("30"),
                "row_a_completeness": "complete",
                "unknown_buy": None,
                "row_b_sell": Decimal("20"),
                "row_b_completeness": "buy_unknown",
            },
        )
    if revision == "0013_query":
        _insert_market_archive(connection, values, with_idempotency_hash=True)
        connection.execute(
            text(
                "INSERT INTO owner_principals (id, local_key, created_at) "
                "VALUES (:owner_id, NULL, :now)"
            ),
            {**values, "now": now},
        )
        _insert_publication(connection, values)
        connection.execute(
            text(
                """
                INSERT INTO query_records (
                    id, owner_id, snapshot_id, idempotency_key, request_hash,
                    ast, ast_hash, parser_version, parser_kind, model_version,
                    created_at
                ) VALUES (
                    :query_record_id, :owner_id, :publication_id, :token,
                    :hash, CAST(:ast_json AS jsonb), :hash, 'zh-whitelist-v1',
                    'deterministic', NULL, :now
                )
                """
            ),
            {
                **values,
                "now": now,
                "ast_json": json.dumps(
                    {
                        "version": 1,
                        "predicates": [
                            {"field": "security_id", "op": "eq", "value": "S000001"}
                        ],
                        "sort": None,
                        "limit": 50,
                    }
                ),
            },
        )
        return (
            "query_record",
            {"id": values["query_record_id"], "hash": values["hash"]},
        )
    raise ValueError(f"unsupported upgrade baseline: {revision}")


def _sentinel_preserved(
    connection,
    kind: str,
    expected: dict[str, object],
) -> bool:
    from sqlalchemy import text

    queries = {
        "foundation_archive": (
            """
            SELECT snapshot.id AS snapshot_id, batch.payload_hash,
                   audit.id AS audit_id
            FROM snapshots snapshot
            JOIN import_batches batch ON batch.id = snapshot.batch_id
            JOIN audit_events audit ON audit.id = :audit_id
            WHERE snapshot.id = :snapshot_id
            """,
            expected,
        ),
        "private_audit": (
            "SELECT id, owner_id FROM private_audit_events WHERE id = :id",
            expected,
        ),
        "topic_attempt_batch": (
            """
            SELECT attempt.id, batch.payload_hash
            FROM topic_import_attempts attempt
            JOIN topic_import_batches batch ON batch.id = attempt.batch_id
            WHERE attempt.id = :id
            """,
            expected,
        ),
        "security_market_attempt": (
            """
            SELECT attempt.id, attempt.envelope_hash AS hash
            FROM market_import_attempts attempt
            JOIN security ON security.source_archive_id = attempt.batch_id
            WHERE attempt.id = :id
            """,
            expected,
        ),
        "role_run_root": (
            "SELECT id, payload_hash AS hash FROM role_runs WHERE id = :id",
            expected,
        ),
        "analysis_job": (
            "SELECT id, request_hash AS hash FROM analysis_jobs WHERE id = :id",
            expected,
        ),
        "news_article_revision": (
            """
            SELECT id, content_digest AS hash
            FROM news_article_revisions WHERE id = :id
            """,
            expected,
        ),
        "regulation_rule": (
            "SELECT id, rule_id FROM regulation_rules WHERE id = :id",
            expected,
        ),
        "legacy_regulation_graph": (
            """
            SELECT rule.id AS rule_record_id, event.event_id,
                   snapshot.id AS regulation_snapshot_id
            FROM regulation_rules rule
            JOIN regulation_events event
              ON event.rule_id = rule.rule_id
             AND event.rule_version = rule.rule_version
            JOIN regulation_snapshot_events binding
              ON binding.event_id = event.event_id
            JOIN regulation_snapshots snapshot
              ON snapshot.id = binding.snapshot_id
            WHERE rule.id = :rule_record_id
              AND event.event_id = :event_id
              AND snapshot.id = :regulation_snapshot_id
            """,
            expected,
        ),
        "regulation_event_provenance": (
            """
            SELECT event_id, source_id
            FROM regulation_events WHERE event_id = :event_id
            """,
            expected,
        ),
        "ai_task": (
            "SELECT id, request_hash AS hash FROM ai_tasks WHERE id = :id",
            expected,
        ),
        "query_record": (
            "SELECT id, request_hash AS hash FROM query_records WHERE id = :id",
            expected,
        ),
        "realtime_evidence": (
            """
            SELECT batch.quote_batch_id AS batch_id,
                   batch.ingest_order,
                   minute.id AS minute_id,
                   event.event_id
            FROM realtime_quote_batches batch
            JOIN realtime_minute_bars minute
              ON minute.source_id = batch.source_id
             AND minute.batch_id = batch.quote_batch_id
            JOIN realtime_anomaly_events event
              ON event.source_id = batch.source_id
             AND event.batch_id = batch.quote_batch_id
            WHERE batch.quote_batch_id = :batch_id
              AND minute.id = :minute_id
              AND event.event_id = :event_id
            """,
            expected,
        ),
        "lhb_evidence": (
            """
            SELECT batch.batch_id AS batch_id,
                   disclosure.disclosure_id AS disclosure_id,
                   complete_seat.seat_id AS lhb_seat_id_a,
                   unknown_seat.seat_id AS lhb_seat_id_b,
                   revision.revision AS revision,
                   revision.completeness AS revision_completeness,
                   complete_seat.mapping_version AS mapping_version,
                   complete_seat.buy_amount AS buy_amount,
                   complete_seat.sell_amount AS sell_amount,
                   complete_seat.completeness AS row_a_completeness,
                   unknown_seat.buy_amount AS unknown_buy,
                   unknown_seat.sell_amount AS row_b_sell,
                   unknown_seat.completeness AS row_b_completeness
            FROM lhb_import_batches batch
            JOIN lhb_disclosures disclosure
              ON disclosure.source_id = batch.source_id
            JOIN lhb_disclosure_revisions revision
              ON revision.source_id = batch.source_id
             AND revision.batch_id = batch.batch_id
             AND revision.disclosure_id = disclosure.disclosure_id
            JOIN lhb_disclosure_seats complete_seat
              ON complete_seat.source_id = revision.source_id
             AND complete_seat.disclosure_id = revision.disclosure_id
             AND complete_seat.revision = revision.revision
             AND complete_seat.seat_id = :lhb_seat_id_a
            JOIN lhb_disclosure_seats unknown_seat
              ON unknown_seat.source_id = revision.source_id
             AND unknown_seat.disclosure_id = revision.disclosure_id
             AND unknown_seat.revision = revision.revision
             AND unknown_seat.seat_id = :lhb_seat_id_b
            WHERE batch.batch_id = :batch_id
              AND disclosure.disclosure_id = :disclosure_id
            """,
            expected,
        ),
    }
    statement, parameters = queries[kind]
    row = connection.execute(text(statement), parameters).mappings().one_or_none()
    if row is None:
        return False
    return all(row[key] == value for key, value in expected.items())


def _legacy_regulation_state(
    connection,
    expected: dict[str, object],
    *,
    upgraded: bool,
) -> dict[str, object]:
    from sqlalchemy import text

    provenance = (
        """
        event.source_id, event.source_version,
        snapshot.event_source_completeness,
        snapshot.event_source_id, snapshot.event_source_version
        """
        if upgraded
        else """
        NULL::text AS source_id, NULL::text AS source_version,
        'unknown'::text AS event_source_completeness,
        NULL::text AS event_source_id, NULL::text AS event_source_version
        """
    )
    row = (
        connection.execute(
            text(
                f"""
            SELECT rule.id AS rule_record_id, event.event_id,
                   snapshot.id AS regulation_snapshot_id,
                   binding.snapshot_id AS binding_snapshot_id,
                   binding.event_id AS binding_event_id,
                   {provenance}
            FROM regulation_rules rule
            JOIN regulation_events event
              ON event.rule_id = rule.rule_id
             AND event.rule_version = rule.rule_version
            JOIN regulation_snapshot_events binding
              ON binding.event_id = event.event_id
            JOIN regulation_snapshots snapshot
              ON snapshot.id = binding.snapshot_id
            WHERE rule.id = :rule_record_id
              AND event.event_id = :event_id
              AND snapshot.id = :regulation_snapshot_id
            """
            ),
            expected,
        )
        .mappings()
        .one()
    )
    return {
        key: str(value) if key.endswith("_id") and value is not None else value
        for key, value in row.items()
    }


def exercise_upgrade(
    database_url: str,
    revision: str,
    schema_prefix: str | None = None,
) -> dict[str, object]:
    """Upgrade one declared baseline with representative data in a fresh schema."""
    from sqlalchemy import text

    if revision not in SUPPORTED_FROM:
        raise ValueError(f"unsupported upgrade baseline: {revision}")
    url = _validated_postgres_url(database_url)
    admin_url = url.difference_update_query(["options"])
    admin_engine = _create_admin_engine(admin_url)
    schema = _new_upgrade_schema_name(schema_prefix)
    database = None
    scoped_engine = None
    schema_created = False
    primary_error = None
    cleanup_errors: list[BaseException] = []
    result = None
    sentinel_before = None
    sentinel_after = None
    try:
        _validate_upgrade_schema_name(schema, schema_prefix)
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        schema_created = True
        scoped_url = admin_url.update_query_dict({"options": f"-csearch_path={schema}"})
        scoped_engine = _create_admin_engine(scoped_url)
        values = _sentinel_values()
        with scoped_engine.begin() as connection:
            _upgrade(connection, revision)
            kind, expected = _insert_sentinel(connection, revision, values)
            if kind == "legacy_regulation_graph":
                sentinel_before = _legacy_regulation_state(
                    connection,
                    expected,
                    upgraded=False,
                )
            baseline_revision = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            if baseline_revision != revision:
                raise ValueError(f"baseline upgrade did not reach {revision}")
        with scoped_engine.begin() as connection:
            _upgrade(connection, RELEASE_HEAD)
            final_revision = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            if final_revision != RELEASE_HEAD:
                raise ValueError(f"supported upgrade did not reach {RELEASE_HEAD}")
            preserved = _sentinel_preserved(connection, kind, expected)
            if kind == "legacy_regulation_graph":
                sentinel_after = _legacy_regulation_state(
                    connection,
                    expected,
                    upgraded=True,
                )
                preserved = preserved and sentinel_before == sentinel_after
        database = _database_class()(scoped_url.render_as_string(hide_password=False))
        ready = database.ready()
        if not preserved:
            raise ValueError(f"{revision} sentinel was not preserved")
        if not ready:
            raise ValueError(f"{revision} upgraded schema is not ready")
        result = {
            "from_revision": revision,
            "to_revision": final_revision,
            "sentinel": kind,
            "sentinel_preserved": preserved,
            "ready": ready,
        }
        if sentinel_before is not None and sentinel_after is not None:
            result["sentinel_before"] = sentinel_before
            result["sentinel_after"] = sentinel_after
    except BaseException as exc:
        primary_error = exc
    finally:
        for close in (
            None if database is None else database.close,
            None if scoped_engine is None else scoped_engine.dispose,
        ):
            if close is not None:
                try:
                    close()
                except BaseException as exc:
                    cleanup_errors.append(exc)
        try:
            if schema_created:
                with admin_engine.begin() as connection:
                    connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        except BaseException as exc:
            cleanup_errors.append(exc)
        try:
            admin_engine.dispose()
        except BaseException as exc:
            cleanup_errors.append(exc)
    if primary_error is not None:
        if cleanup_errors:
            raise BaseExceptionGroup(
                "supported upgrade and cleanup failed",
                [primary_error, *cleanup_errors],
            )
        raise primary_error.with_traceback(primary_error.__traceback__)
    if cleanup_errors:
        if len(cleanup_errors) == 1:
            raise cleanup_errors[0]
        raise BaseExceptionGroup("supported upgrade cleanup failed", cleanup_errors)
    if result is None:
        raise RuntimeError("supported upgrade produced no result")
    return result


def exercise_supported_upgrades(
    database_url: str,
    schema_prefix: str | None = None,
) -> list[dict[str, object]]:
    """Exercise every baseline declared by the frozen release policy."""
    return [
        exercise_upgrade(database_url, revision, schema_prefix)
        for revision in SUPPORTED_FROM
    ]


def exercise_fresh_upgrade(
    database_url: str,
    schema_prefix: str | None = None,
) -> dict[str, object]:
    """Create a fresh head schema and require packaged readiness."""
    from sqlalchemy import text

    url = _validated_postgres_url(database_url)
    admin_url = url.difference_update_query(["options"])
    admin_engine = _create_admin_engine(admin_url)
    schema = _new_upgrade_schema_name(schema_prefix)
    database = None
    schema_created = False
    primary_error = None
    cleanup_errors: list[BaseException] = []
    result = None
    try:
        _validate_upgrade_schema_name(schema, schema_prefix)
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        schema_created = True
        scoped_url = admin_url.update_query_dict({"options": f"-csearch_path={schema}"})
        database = _database_class()(scoped_url.render_as_string(hide_password=False))
        database.migrate()
        with database.engine.connect() as connection:
            revision = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
        ready = database.ready()
        if revision != RELEASE_HEAD:
            raise ValueError(f"fresh upgrade did not reach {RELEASE_HEAD}")
        if not ready:
            raise ValueError("fresh upgraded schema is not ready")
        result = {"to_revision": revision, "ready": ready}
    except BaseException as exc:
        primary_error = exc
    finally:
        try:
            if database is not None:
                database.close()
        except BaseException as exc:
            cleanup_errors.append(exc)
        try:
            if schema_created:
                with admin_engine.begin() as connection:
                    connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        except BaseException as exc:
            cleanup_errors.append(exc)
        try:
            admin_engine.dispose()
        except BaseException as exc:
            cleanup_errors.append(exc)
    if primary_error is not None:
        if cleanup_errors:
            raise BaseExceptionGroup(
                "fresh upgrade and cleanup failed",
                [primary_error, *cleanup_errors],
            )
        raise primary_error.with_traceback(primary_error.__traceback__)
    if cleanup_errors:
        if len(cleanup_errors) == 1:
            raise cleanup_errors[0]
        raise BaseExceptionGroup("fresh upgrade cleanup failed", cleanup_errors)
    if result is None:
        raise RuntimeError("fresh upgrade produced no result")
    return result


def resolve_wheel_path(value: Path) -> Path:
    """Resolve a wheel file, directory, or glob to exactly one wheel."""
    raw_value = os.fspath(value)
    if glob.has_magic(raw_value):
        matches = [
            Path(match).resolve()
            for match in glob.glob(raw_value)
            if Path(match).is_file() and Path(match).suffix == ".whl"
        ]
    elif value.is_dir():
        matches = [path.resolve() for path in value.glob("*.whl") if path.is_file()]
    elif value.is_file() and value.suffix == ".whl":
        matches = [value.resolve()]
    else:
        matches = []
    matches = sorted(set(matches))
    if len(matches) != 1:
        raise ValueError(
            f"exactly one wheel is required; found {len(matches)} at {value}"
        )
    return matches[0]


def wheel_sha256(wheel: Path) -> str:
    """Return the SHA-256 of the exact wheel archive bytes."""
    return hashlib.sha256(Path(wheel).read_bytes()).hexdigest()


def validate_audited_commit(
    value: str,
    repository_root: Path | None = None,
) -> str:
    """Validate a full Git object name and match the checkout when available."""
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ValueError("audited commit must be 40 lowercase hex characters")
    if repository_root is None:
        return value
    root = Path(repository_root)
    if not (root / ".git").exists():
        return value
    _reject_git_replace_refs(root)
    result = _git_process(
        root,
        ["rev-parse", "--verify", "HEAD"],
    )
    if result.returncode != 0:
        raise ValueError("cannot verify repository HEAD")
    if result.stdout.strip() != value:
        raise ValueError("audited commit does not match repository HEAD")
    return value


def _clear_output_target(output: Path) -> None:
    output = Path(output)
    if output.exists() and output.is_dir() and not output.is_symlink():
        raise ValueError("evidence output must not be a directory")
    output.unlink(missing_ok=True)


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _wheel_helper_source() -> str:
    return """\
import json
import os
import runpy
import sys
from pathlib import Path

verifier = runpy.run_path(str(Path(__file__).with_name("verifier.py")))
import dragon_radar

package_file = Path(dragon_radar.__file__).resolve()
source_root = Path(sys.argv[1]).resolve()
output = Path(sys.argv[2]).resolve()
schema_prefix = sys.argv[3]
if package_file.is_relative_to(source_root):
    raise RuntimeError("wheel verification imported dragon_radar from source")

migrations = package_file.parent / "migrations"
manifest = verifier["verify_manifest"](migrations)
if manifest["errors"]:
    raise RuntimeError("installed migration manifest verification failed")

database_url = os.environ["DRAGON_TEST_DATABASE_URL"]
fresh = verifier["exercise_fresh_upgrade"](database_url, schema_prefix)
upgrades = verifier["exercise_supported_upgrades"](database_url, schema_prefix)
evidence = {
    "schema": "dragon-radar.installed-wheel-migrations",
    "schema_version": 1,
    "imported_from": str(package_file),
    "fresh": fresh,
    "supported_upgrades": upgrades,
}
verifier["write_contract_atomically"](evidence, output)
print(f"dragon_radar imported from {package_file}")
print(f"fresh -> {fresh['to_revision']}: ready={str(fresh['ready']).lower()}")
for item in upgrades:
    print(
        f"{item['from_revision']} -> {item['to_revision']}: "
        f"sentinel={item['sentinel']} "
        f"preserved={str(item['sentinel_preserved']).lower()} "
        f"ready={str(item['ready']).lower()}"
    )
"""


def _load_wheel_child_evidence(path: Path) -> dict[str, object]:
    try:
        raw_evidence = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("cannot read installed wheel evidence") from exc
    evidence = _require_exact_fields(
        raw_evidence,
        {
            "schema",
            "schema_version",
            "imported_from",
            "fresh",
            "supported_upgrades",
        },
        location="installed wheel evidence",
    )
    if evidence["schema"] != "dragon-radar.installed-wheel-migrations":
        raise ValueError("installed wheel evidence schema is invalid")
    if evidence["schema_version"] != 1:
        raise ValueError("installed wheel evidence schema_version is invalid")
    _require_string(
        evidence["imported_from"],
        location="installed wheel evidence.imported_from",
    )
    fresh = _require_exact_fields(
        evidence["fresh"],
        {"to_revision", "ready"},
        location="installed wheel evidence.fresh",
    )
    if fresh != {"to_revision": RELEASE_HEAD, "ready": True}:
        raise ValueError("installed wheel fresh migration evidence is invalid")

    upgrades = evidence["supported_upgrades"]
    if not isinstance(upgrades, list) or len(upgrades) != len(SUPPORTED_FROM):
        raise ValueError("installed wheel supported_upgrades is invalid")
    legacy_state_fields = {
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
    for index, (raw_upgrade, revision) in enumerate(
        zip(upgrades, SUPPORTED_FROM, strict=True)
    ):
        fields = {
            "from_revision",
            "to_revision",
            "sentinel",
            "sentinel_preserved",
            "ready",
        }
        if revision == "0009_regulation":
            fields.update({"sentinel_before", "sentinel_after"})
        upgrade = _require_exact_fields(
            raw_upgrade,
            fields,
            location=f"installed wheel evidence.supported_upgrades[{index}]",
        )
        if (
            upgrade["from_revision"] != revision
            or upgrade["to_revision"] != RELEASE_HEAD
            or upgrade["sentinel_preserved"] is not True
            or upgrade["ready"] is not True
        ):
            raise ValueError("installed wheel supported upgrade evidence is invalid")
        _require_string(
            upgrade["sentinel"],
            location=f"installed wheel evidence.supported_upgrades[{index}].sentinel",
        )
        if revision == "0009_regulation":
            before = _require_exact_fields(
                upgrade["sentinel_before"],
                legacy_state_fields,
                location="installed wheel evidence legacy sentinel_before",
            )
            after = _require_exact_fields(
                upgrade["sentinel_after"],
                legacy_state_fields,
                location="installed wheel evidence legacy sentinel_after",
            )
            if before != after:
                raise ValueError("installed wheel legacy sentinel changed")
    return evidence


def _run_process(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float,
    text: bool = True,
) -> subprocess.CompletedProcess:
    process_kwargs: dict[str, object] = {
        "cwd": cwd,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": text,
    }
    if text:
        process_kwargs.update(encoding="utf-8", errors="replace")
    if os.name == "nt":
        process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        process_kwargs["start_new_session"] = True
    process = subprocess.Popen(
        list(argv),
        **process_kwargs,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process, force=False)
        wait = getattr(process, "wait", None)
        if os.name == "nt":
            for stream_name in ("stdout", "stderr"):
                stream = getattr(process, stream_name, None)
                if stream is not None:
                    stream.close()
            try:
                if wait is not None:
                    wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                _terminate_process_tree(process, force=True)
                try:
                    wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
            raise exc
        grace_deadline = time.monotonic() + 0.5
        try:
            if wait is not None:
                wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        remaining_grace = grace_deadline - time.monotonic()
        if remaining_grace > 0:
            time.sleep(remaining_grace)
        _terminate_process_tree(process, force=True)
        try:
            process.communicate(timeout=0.5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            for stream_name in ("stdout", "stderr"):
                stream = getattr(process, stream_name, None)
                if stream is not None:
                    stream.close()
            try:
                if wait is not None:
                    wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
        raise exc
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def _terminate_process_tree(
    process: subprocess.Popen,
    *,
    force: bool,
) -> None:
    if os.name == "nt" and getattr(process, "pid", None) is not None:
        try:
            tree_killer = subprocess.Popen(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
            )
            try:
                tree_killer.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                tree_killer.kill()
                try:
                    tree_killer.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    pass
            if tree_killer.returncode == 0:
                return
        except OSError:
            pass
    elif getattr(process, "pid", None) is not None:
        try:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            return
        except ProcessLookupError:
            return
        except OSError:
            pass
    try:
        process.kill() if force else process.terminate()
    except OSError:
        pass


def _require_process_success(
    result: subprocess.CompletedProcess[str],
    *,
    stage: str,
) -> None:
    if result.returncode != 0:
        detail = result.stderr.strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"{stage} failed with exit code {result.returncode}{suffix}")


def validate_rollback_request(requested: str) -> str:
    """Reject unsupported production rollback mechanisms."""
    if requested != ROLLBACK_MODE:
        raise ValueError(
            {
                "code": "unsupported_release_rollback",
                "requested": requested,
                "rollback_mode": ROLLBACK_MODE,
            }
        )
    return requested


def validate_restore_container_name(name: str) -> str:
    """Require a Docker container name that cannot be parsed as an option."""
    if (
        not isinstance(name, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name) is None
    ):
        raise ValueError("unsafe restore container name")
    return name


def _new_restore_token() -> str:
    return secrets.token_hex(16)


def _new_restore_drill_resources() -> dict[str, str]:
    token = _new_restore_token()
    restore_database = f"task21_restore_{token}"
    return {
        "role": f"task21_role_{token}",
        "source_database": f"task21_source_{token}",
        "restore_database": restore_database,
        "dump_path": f"/tmp/{restore_database}.dump",
        "password": secrets.token_urlsafe(32),
    }


def _hash_database_state(state: dict[str, object]) -> str:
    encoded = json.dumps(
        state,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _database_fingerprint(database_url: str) -> str:
    """Hash public table rows and sequence state in one stable snapshot."""
    from sqlalchemy import create_engine, text

    engine = create_engine(
        _validated_postgres_url(database_url),
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 3},
    )
    state: dict[str, list[dict[str, object]]] = {
        "tables": [],
        "sequences": [],
    }
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            preparer = connection.dialect.identifier_preparer
            tables = list(
                connection.execute(
                    text(
                        """
                        SELECT tablename
                        FROM pg_tables
                        WHERE schemaname = 'public'
                        ORDER BY tablename
                        """
                    )
                ).scalars()
            )
            for table in tables:
                qualified = f"{preparer.quote_schema('public')}.{preparer.quote(table)}"
                row = connection.execute(
                    text(
                        "SELECT count(*) AS row_count, "
                        "md5(COALESCE(string_agg("
                        "md5(row_to_json(row_value)::text), '' "
                        "ORDER BY md5(row_to_json(row_value)::text)), '')) "
                        f"AS fingerprint FROM {qualified} AS row_value"
                    )
                ).one()
                state["tables"].append(
                    {
                        "qualified_name": f"public.{table}",
                        "rows": row.row_count,
                        "fingerprint": row.fingerprint,
                    }
                )
            sequences = connection.execute(
                text(
                    """
                    SELECT sequencename, increment_by, cycle
                    FROM pg_sequences
                    WHERE schemaname = 'public'
                    ORDER BY sequencename
                    """
                )
            ).mappings()
            for sequence in sequences:
                name = str(sequence["sequencename"])
                qualified = f"{preparer.quote_schema('public')}.{preparer.quote(name)}"
                runtime = (
                    connection.execute(
                        text(f"SELECT last_value, is_called FROM {qualified}")
                    )
                    .mappings()
                    .one()
                )
                state["sequences"].append(
                    {
                        "qualified_name": f"public.{name}",
                        "last_value": runtime["last_value"],
                        "is_called": runtime["is_called"],
                        "increment": sequence["increment_by"],
                        "cycle": sequence["cycle"],
                    }
                )
            transaction.commit()
    finally:
        engine.dispose()
    return _hash_database_state(state)


def _restore_sentinel_counts(connection, values: dict[str, object]) -> dict[str, int]:
    from sqlalchemy import text

    queries = {
        "source_grants": (
            "SELECT count(*) FROM source_grants WHERE source_id = :source_id"
        ),
        "import_batches": ("SELECT count(*) FROM import_batches WHERE id = :batch_id"),
        "snapshots": "SELECT count(*) FROM snapshots WHERE id = :snapshot_id",
        "audit_events": "SELECT count(*) FROM audit_events WHERE id = :audit_id",
    }
    return {
        table: int(connection.scalar(text(statement), values))
        for table, statement in queries.items()
    }


def _prepare_restore_source(database_url: str) -> dict[str, object]:
    """Migrate and seed the dedicated source database before quiescence."""
    from sqlalchemy import text

    database = _database_class()(database_url)
    values = _sentinel_values()
    permission_sentinel = {
        "source_id": values["source_id"],
        "approved": True,
        "local_storage": True,
        "local_display": True,
        "export": False,
        "ai": False,
        "sharing": False,
    }
    try:
        database.migrate()
        with database.engine.begin() as connection:
            _insert_market_archive(
                connection,
                values,
                with_idempotency_hash=True,
            )
            audit_id = uuid4()
            values["audit_id"] = audit_id
            connection.execute(
                text(
                    """
                    INSERT INTO audit_events (
                        id, action, source_id, object_id, details, occurred_at
                    ) VALUES (
                        :audit_id, 'task21_restore', :source_id, :token, '{}',
                        TIMESTAMPTZ '2026-01-02T03:04:05+00:00'
                    )
                    """
                ),
                values,
            )
            kind = "foundation_archive"
            expected = {
                "snapshot_id": values["snapshot_id"],
                "payload_hash": values["hash"],
                "audit_id": audit_id,
            }
            connection.execute(
                text(
                    """
                    UPDATE source_grants
                    SET "grant" = CAST(:grant AS jsonb)
                    WHERE source_id = :source_id
                    """
                ),
                {
                    "source_id": values["source_id"],
                    "grant": json.dumps(permission_sentinel),
                },
            )
            source_counts = _restore_sentinel_counts(connection, values)
    finally:
        database.close()
    return {
        "sentinel_kind": kind,
        "sentinel_expected": expected,
        "sentinel_values": values,
        "source_counts": source_counts,
        "permission_sentinel": permission_sentinel,
    }


def _restored_schema_contracts(
    connection,
) -> tuple[dict[str, object], dict[str, object]]:
    from dragon_radar.storage.schema_contract import (
        capture_schema_contract,
        load_packaged_schema_contract,
    )

    return load_packaged_schema_contract(), capture_schema_contract(connection)


def _restore_schema_metadata():
    import importlib

    modules = (
        "dragon_radar.owner.models",
        "dragon_radar.review.tables",
        "dragon_radar.topics.tables",
        "dragon_radar.readmodels.tables",
        "dragon_radar.roles.tables",
        "dragon_radar.publication.tables",
        "dragon_radar.analysis_jobs.models",
        "dragon_radar.news.tables",
        "dragon_radar.regulation.tables",
        "dragon_radar.ai.tables",
        "dragon_radar.realtime.tables",
        "dragon_radar.lhb.tables",
    )
    for module in modules:
        importlib.import_module(module)
    from dragon_radar.storage.models import metadata

    return metadata


def _contract_objects(
    contract: dict[str, object],
    collection: str,
) -> dict[tuple[str, str], dict[str, object]]:
    return {
        (str(item["table"]), str(item["name"])): item for item in contract[collection]
    }


def _metadata_check_constraint(table, expected: dict[str, object]):
    from sqlalchemy import CheckConstraint

    def literals(expression: str) -> tuple[str, ...]:
        return tuple(
            value[1:-1].replace("''", "'")
            for value in re.findall(r"'(?:''|[^'])*'", expression)
        )

    expected_name = str(expected["name"])
    columns = [str(column) for column in expected["columns"]]
    expected_literals = literals(str(expected["expression"]))
    candidates = []
    for constraint in table.constraints:
        if not isinstance(constraint, CheckConstraint):
            continue
        if constraint.name == expected_name:
            return constraint
        expression = str(constraint.sqltext)
        if constraint.name is None and all(column in expression for column in columns):
            candidates.append(constraint)
    if expected_literals:
        literal_matches = [
            constraint
            for constraint in candidates
            if literals(str(constraint.sqltext)) == expected_literals
        ]
        if len(literal_matches) == 1:
            return literal_matches[0]
    if len(candidates) != 1:
        raise ValueError(
            f"cannot resolve restored check constraint metadata: {expected_name}"
        )
    return candidates[0]


def _canonicalize_restored_schema(connection) -> int:
    """Recreate only expressions changed by PostgreSQL's dump/reparse cycle."""
    from sqlalchemy import text
    from sqlalchemy.schema import AddConstraint, CreateIndex, DropIndex

    expected, actual = _restored_schema_contracts(connection)
    expected_constraints = _contract_objects(expected, "constraints")
    actual_constraints = _contract_objects(actual, "constraints")
    expected_indexes = _contract_objects(expected, "indexes")
    actual_indexes = _contract_objects(actual, "indexes")
    metadata = _restore_schema_metadata()
    changed = 0

    for key, expected_constraint in expected_constraints.items():
        actual_constraint = actual_constraints.get(key)
        if (
            actual_constraint is None
            or expected_constraint == actual_constraint
            or expected_constraint["kind"] != "check"
        ):
            continue
        table_name, constraint_name = key
        if (
            re.fullmatch(r"[a-z][a-z0-9_]*", table_name) is None
            or re.fullmatch(r"[a-z][a-z0-9_]*", constraint_name) is None
            or table_name not in metadata.tables
        ):
            raise ValueError("unsafe restored check constraint identity")
        constraint = _metadata_check_constraint(
            metadata.tables[table_name],
            expected_constraint,
        )
        connection.execute(
            text(f'ALTER TABLE "{table_name}" DROP CONSTRAINT "{constraint_name}"')
        )
        connection.execute(AddConstraint(constraint))
        changed += 1

    metadata_indexes = {
        (table.name, index.name): index
        for table in metadata.tables.values()
        for index in table.indexes
        if index.name is not None
    }
    for key, expected_index in expected_indexes.items():
        actual_index = actual_indexes.get(key)
        if (
            actual_index is None
            or expected_index == actual_index
            or expected_index["predicate"] is None
        ):
            continue
        index = metadata_indexes.get(key)
        if index is None:
            raise ValueError(f"cannot resolve restored index metadata: {key[1]}")
        connection.execute(DropIndex(index))
        connection.execute(CreateIndex(index))
        changed += 1
    return changed


def _verify_restored_database(
    restore_url: str,
    source_state: dict[str, object],
) -> dict[str, object]:
    """Verify revision, schema, sentinel data, permissions, and full data hash."""
    from sqlalchemy import text

    database = _database_class()(restore_url)
    try:
        ready_before_canonicalization = database.ready()
        with database.engine.begin() as connection:
            app_role, app_is_superuser = connection.execute(
                text(
                    """
                    SELECT current_user, role_record.rolsuper
                    FROM pg_roles role_record
                    WHERE role_record.rolname = current_user
                    """
                )
            ).one()
            connection.execute(
                text("CREATE TEMP TABLE task21_app_semantics(value integer)")
            )
            connection.execute(
                text("INSERT INTO task21_app_semantics(value) VALUES (1)")
            )
            if (
                connection.scalar(text("SELECT count(*) FROM task21_app_semantics"))
                != 1
            ):
                raise ValueError("restored app write semantics failed")
            canonicalized_schema_objects = _canonicalize_restored_schema(connection)
            revision = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            sentinel_preserved = _sentinel_preserved(
                connection,
                str(source_state["sentinel_kind"]),
                source_state["sentinel_expected"],
            )
            permission = connection.scalar(
                text(
                    """
                    SELECT "grant" FROM source_grants
                    WHERE source_id = :source_id
                    """
                ),
                {
                    "source_id": source_state["permission_sentinel"]["source_id"],
                },
            )
            restored_counts = _restore_sentinel_counts(
                connection,
                source_state["sentinel_values"],
            )
        ready = database.ready()
    finally:
        database.close()
    restore_fingerprint = _database_fingerprint(restore_url)
    if revision != RELEASE_HEAD:
        raise ValueError(f"restored database revision is not {RELEASE_HEAD}")
    if not ready:
        raise ValueError("restored database schema is not ready")
    if not sentinel_preserved:
        raise ValueError("restored database sentinel was not preserved")
    if permission != source_state["permission_sentinel"]:
        raise ValueError("restored database permission sentinel changed")
    if restored_counts != source_state["source_counts"]:
        raise ValueError("restored database sentinel counts changed")
    if restore_fingerprint != source_state["fingerprint"]:
        raise ValueError("restored database fingerprint changed")
    if app_is_superuser or app_role != source_state["app_role"]:
        raise ValueError("restored database did not use the non-superuser app role")
    return {
        "revision": revision,
        "restored_ready": ready,
        "sentinel_preserved": sentinel_preserved,
        "permissions_preserved": True,
        "restore_fingerprint": restore_fingerprint,
        "restored_counts": restored_counts,
        "ready_before_canonicalization": ready_before_canonicalization,
        "canonicalized_schema_objects": canonicalized_schema_objects,
        "app_semantics_verified": True,
    }


def _docker_restore_command(
    container: str,
    *arguments: str,
    stage: str,
) -> subprocess.CompletedProcess[str]:
    result = _run_process(
        ["docker", "exec", container, *arguments],
        timeout=300,
    )
    _require_process_success(result, stage=stage)
    return result


def _validated_database_identifier(name: str) -> str:
    safe_identifier = r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,62}"
    if re.fullmatch(safe_identifier, name) is None:
        raise ValueError("unsafe database identifier")
    return f'"{name}"'


def _database_literal(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def _psql_restore_command(
    container: str,
    username: str,
    database: str,
    sql: str,
    *,
    stage: str,
) -> subprocess.CompletedProcess[str]:
    return _docker_restore_command(
        container,
        "psql",
        "--username",
        username,
        "--dbname",
        database,
        "--tuples-only",
        "--no-align",
        "--set=ON_ERROR_STOP=1",
        "--command",
        sql,
        stage=stage,
    )


def _validate_restore_drill_target(container: str, database_url: str):
    from sqlalchemy import create_engine, text

    label = _run_process(
        [
            "docker",
            "inspect",
            "--format",
            '{{ index .Config.Labels "dragon-radar.task21-release" }}',
            container,
        ],
        timeout=30,
    )
    _require_process_success(label, stage="restore container inspection")
    if label.stdout.strip() != "true":
        raise ValueError("restore container must have dragon-radar.task21-release=true")

    url = _validated_postgres_url(database_url).difference_update_query(["options"])
    if url.database != "postgres":
        raise ValueError("restore drill requires maintenance database postgres")
    username = url.username
    if (
        username is None
        or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,62}", username) is None
    ):
        raise ValueError("maintenance URL must contain a safe admin user")

    engine = create_engine(
        url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 3},
    )
    try:
        with engine.connect() as connection:
            database, is_superuser = connection.execute(
                text(
                    """
                    SELECT current_database(), role_record.rolsuper
                    FROM pg_roles role_record
                    WHERE role_record.rolname = current_user
                    """
                )
            ).one()
    finally:
        engine.dispose()
    if database != "postgres" or is_superuser is not True:
        raise ValueError("maintenance URL must identify a postgres admin")
    return url, username


def _create_restore_drill_resources(
    container: str,
    admin_username: str,
    resources: dict[str, str],
) -> None:
    role = _validated_database_identifier(resources["role"])
    source_database = _validated_database_identifier(resources["source_database"])
    password = _database_literal(resources["password"])
    _psql_restore_command(
        container,
        admin_username,
        "postgres",
        (
            f"CREATE ROLE {role} LOGIN PASSWORD {password} "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
        ),
        stage="app role creation",
    )
    _psql_restore_command(
        container,
        admin_username,
        "postgres",
        f"CREATE DATABASE {source_database} OWNER {role}",
        stage="source database creation",
    )
    attributes = _psql_restore_command(
        container,
        admin_username,
        "postgres",
        (
            "SELECT rolsuper::int || ',' || rolcreatedb::int || ',' || "
            "rolcreaterole::int || ',' || rolreplication::int "
            f"FROM pg_roles WHERE rolname = {_database_literal(resources['role'])}"
        ),
        stage="app role verification",
    )
    if attributes.stdout.strip() != "0,0,0,0":
        raise RuntimeError("generated app role is privileged")


def _app_database_url(admin_url, resources: dict[str, str], database: str) -> str:
    return admin_url.set(
        username=resources["role"],
        password=resources["password"],
        database=database,
    ).render_as_string(hide_password=False)


def _admin_database_url(admin_url, database: str) -> str:
    return admin_url.set(database=database).render_as_string(hide_password=False)


def _fence_restore_source(
    container: str,
    admin_username: str,
    source_database: str,
    app_role: str,
    app_password: str,
) -> None:
    """Permanently fence the disposable source before its backup."""
    quoted_database = _validated_database_identifier(source_database)
    database_literal = _database_literal(source_database)
    quoted_role = _validated_database_identifier(app_role)
    _psql_restore_command(
        container,
        admin_username,
        "postgres",
        (f"ALTER DATABASE {quoted_database} SET default_transaction_read_only = on"),
        stage="source write freeze",
    )
    _psql_restore_command(
        container,
        admin_username,
        "postgres",
        f"REVOKE CONNECT ON DATABASE {quoted_database} FROM PUBLIC",
        stage="public connect revocation",
    )
    _psql_restore_command(
        container,
        admin_username,
        "postgres",
        f"REVOKE CONNECT ON DATABASE {quoted_database} FROM {quoted_role}",
        stage="app connect revocation",
    )
    _psql_restore_command(
        container,
        admin_username,
        "postgres",
        (
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = {database_literal} AND pid <> pg_backend_pid()"
        ),
        stage="source session termination",
    )
    sessions = _psql_restore_command(
        container,
        admin_username,
        "postgres",
        (
            "SELECT count(*) FROM pg_stat_activity "
            f"WHERE datname = {database_literal} AND pid <> pg_backend_pid()"
        ),
        stage="source session verification",
    )
    if sessions.stdout.strip() != "0":
        raise RuntimeError("source database still has non-drill sessions")

    denied = _run_process(
        [
            "docker",
            "exec",
            "--env",
            "PGPASSWORD",
            container,
            "psql",
            "--host",
            "localhost",
            "--username",
            app_role,
            "--dbname",
            source_database,
            "--set=ON_ERROR_STOP=1",
            "--command",
            "CREATE TABLE task21_write_denied(value integer)",
        ],
        env={**os.environ, "PGPASSWORD": app_password},
        timeout=30,
    )
    if (
        denied.returncode == 0
        or "permission denied for database" not in denied.stderr.lower()
    ):
        raise RuntimeError("fresh app connection/write denial was not proven")


def _restore_app_access(
    container: str,
    admin_username: str,
    restore_database: str,
    app_role: str,
) -> None:
    quoted_database = _validated_database_identifier(restore_database)
    quoted_role = _validated_database_identifier(app_role)
    _psql_restore_command(
        container,
        admin_username,
        "postgres",
        (
            f"ALTER DATABASE {quoted_database} OWNER TO {quoted_role}; "
            f"ALTER DATABASE {quoted_database} "
            "SET default_transaction_read_only = off; "
            f"GRANT CONNECT ON DATABASE {quoted_database} TO {quoted_role}"
        ),
        stage="restore app access",
    )


def _parse_backup_sha256(stdout: str, dump_path: str) -> str:
    match = re.fullmatch(
        rf"([0-9a-f]{{64}})  {re.escape(dump_path)}\r?\n?",
        stdout,
    )
    if match is None:
        raise ValueError("backup SHA-256 output is invalid")
    return match.group(1)


def exercise_backup_restore(
    restore_container: str,
    database_url: str,
    output: Path,
    *,
    audited_commit: str | None = None,
) -> dict[str, object]:
    """Run an isolated custom-format backup and restore verification drill."""
    _clear_output_target(output)
    if audited_commit is None:
        head = _git_process(
            REPOSITORY_ROOT,
            ["rev-parse", "--verify", "HEAD"],
        )
        if head.returncode != 0:
            raise ValueError("cannot derive audited commit from repository HEAD")
        audited_commit = head.stdout.strip()
    audited_commit = validate_audited_commit(audited_commit, REPOSITORY_ROOT)
    container = validate_restore_container_name(restore_container)
    admin_url, admin_username = _validate_restore_drill_target(
        container,
        database_url,
    )
    resources = _new_restore_drill_resources()
    source_database = resources["source_database"]
    restore_database = resources["restore_database"]
    dump_path = resources["dump_path"]
    source_app_url = _app_database_url(admin_url, resources, source_database)
    source_admin_url = _admin_database_url(admin_url, source_database)
    restore_app_url = _app_database_url(admin_url, resources, restore_database)
    started = time.monotonic()
    primary_error = None
    cleanup_errors: list[BaseException] = []
    source_state = None
    verification = None
    backup_sha256 = None
    freeze_started = None
    freeze_ended = None
    quiescence_enforced = False
    try:
        _create_restore_drill_resources(
            container,
            admin_username,
            resources,
        )
        source_state = _prepare_restore_source(source_app_url)
        source_state["app_role"] = resources["role"]
        freeze_started = time.monotonic()
        _fence_restore_source(
            container,
            admin_username,
            source_database,
            resources["role"],
            resources["password"],
        )
        quiescence_enforced = True
        source_state["fingerprint"] = _database_fingerprint(source_admin_url)
        _docker_restore_command(
            container,
            "pg_dump",
            "--username",
            admin_username,
            "--dbname",
            source_database,
            "--format=custom",
            f"--file={dump_path}",
            stage="pg_dump",
        )
        if _database_fingerprint(source_admin_url) != source_state["fingerprint"]:
            raise ValueError("source database fingerprint changed")
        hash_result = _docker_restore_command(
            container,
            "sha256sum",
            dump_path,
            stage="backup SHA-256",
        )
        backup_sha256 = _parse_backup_sha256(hash_result.stdout, dump_path)
        _psql_restore_command(
            container,
            admin_username,
            "postgres",
            (
                f"CREATE DATABASE {_validated_database_identifier(restore_database)} "
                f"OWNER {_validated_database_identifier(resources['role'])}"
            ),
            stage="restore database creation",
        )
        _docker_restore_command(
            container,
            "pg_restore",
            "--username",
            admin_username,
            "--exit-on-error",
            "--dbname",
            restore_database,
            dump_path,
            stage="pg_restore",
        )
        _restore_app_access(
            container,
            admin_username,
            restore_database,
            resources["role"],
        )
        verification = _verify_restored_database(restore_app_url, source_state)
        if _database_fingerprint(source_admin_url) != source_state["fingerprint"]:
            raise ValueError("source database fingerprint changed")
        freeze_ended = time.monotonic()
    except BaseException as exc:
        primary_error = exc
    finally:
        for sql, stage in (
            (
                "DROP DATABASE IF EXISTS "
                f"{_validated_database_identifier(restore_database)} WITH (FORCE)",
                "restore database cleanup",
            ),
            (
                "DROP DATABASE IF EXISTS "
                f"{_validated_database_identifier(source_database)} WITH (FORCE)",
                "source database cleanup",
            ),
            (
                "DROP ROLE IF EXISTS "
                f"{_validated_database_identifier(resources['role'])}",
                "app role cleanup",
            ),
        ):
            try:
                _psql_restore_command(
                    container,
                    admin_username,
                    "postgres",
                    sql,
                    stage=stage,
                )
            except BaseException as exc:
                cleanup_errors.append(exc)
        try:
            _docker_restore_command(
                container,
                "rm",
                "-f",
                dump_path,
                stage="backup file cleanup",
            )
        except BaseException as exc:
            cleanup_errors.append(exc)

    if primary_error is not None:
        if cleanup_errors:
            raise BaseExceptionGroup(
                "backup restore drill and cleanup failed",
                [primary_error, *cleanup_errors],
            )
        raise primary_error.with_traceback(primary_error.__traceback__)
    if cleanup_errors:
        if len(cleanup_errors) == 1:
            raise cleanup_errors[0]
        raise BaseExceptionGroup("backup restore cleanup failed", cleanup_errors)
    if source_state is None or verification is None or backup_sha256 is None:
        raise RuntimeError("backup restore drill produced no evidence")
    if (
        not quiescence_enforced
        or freeze_started is None
        or freeze_ended is None
        or verification["restore_fingerprint"] != source_state["fingerprint"]
    ):
        raise RuntimeError("backup restore drill produced no pass evidence")

    elapsed = max(time.monotonic() - started, 0.000001)
    write_freeze_seconds = max(freeze_ended - freeze_started, 0.000001)
    evidence = {
        "schema": "dragon-radar.backup-restore-drill",
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "audited_commit": audited_commit,
        "rollback_mode": ROLLBACK_MODE,
        "backup_format": "custom",
        "backup_sha256": backup_sha256,
        "source_fingerprint": source_state["fingerprint"],
        "restore_fingerprint": verification["restore_fingerprint"],
        "source_unchanged": True,
        "quiescence_enforced": quiescence_enforced,
        "measured_data_loss_rows": 0,
        "data_loss_window_seconds": 0.0,
        "write_freeze_seconds": write_freeze_seconds,
        "elapsed_seconds": elapsed,
        "revision": verification["revision"],
        "restored_ready": verification["restored_ready"],
        "sentinel_preserved": verification["sentinel_preserved"],
        "permissions_preserved": verification["permissions_preserved"],
        "app_semantics_verified": verification["app_semantics_verified"],
        "ready_before_canonicalization": verification.get(
            "ready_before_canonicalization",
            True,
        ),
        "canonicalized_schema_objects": verification.get(
            "canonicalized_schema_objects",
            0,
        ),
        "source_counts": source_state["source_counts"],
        "restored_counts": verification["restored_counts"],
        "cleanup": {
            "source_database_removed": True,
            "restore_database_removed": True,
            "app_role_removed": True,
            "backup_file_removed": True,
        },
        "disposable": {
            "role": resources["role"],
            "source_database": source_database,
            "restore_database": restore_database,
            "dump_path": dump_path,
        },
        "targets": {
            "rpo_seconds": 3600,
            "rto_seconds": 14400,
            "approval_status": "suggested_unapproved",
        },
    }
    write_contract_atomically(evidence, output)
    return evidence


def _drop_wheel_schemas(database_url: str, schema_prefix: str) -> None:
    from sqlalchemy import text

    _validate_wheel_schema_prefix(schema_prefix)
    url = _validated_postgres_url(database_url)
    admin_url = url.difference_update_query(["options"])
    admin_engine = _create_admin_engine(admin_url)
    primary_error = None
    cleanup_errors: list[BaseException] = []
    try:
        with admin_engine.begin() as connection:
            schemas = list(
                connection.execute(
                    text(
                        """
                        SELECT nspname
                        FROM pg_namespace
                        WHERE left(nspname, length(:prefix)) = :prefix
                        ORDER BY nspname
                        """
                    ),
                    {"prefix": schema_prefix},
                ).scalars()
            )
            for schema in schemas:
                if (
                    re.fullmatch(
                        re.escape(schema_prefix) + r"[0-9a-f]{16}",
                        schema,
                    )
                    is None
                ):
                    raise ValueError("unsafe prefixed schema name")
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    except BaseException as exc:
        primary_error = exc
    finally:
        try:
            admin_engine.dispose()
        except BaseException as exc:
            cleanup_errors.append(exc)
    if primary_error is not None:
        if cleanup_errors:
            raise BaseExceptionGroup(
                "wheel schema cleanup failed",
                [primary_error, *cleanup_errors],
            )
        raise primary_error.with_traceback(primary_error.__traceback__)
    if cleanup_errors:
        raise cleanup_errors[0]


def verify_installed_wheel(
    database_url: str,
    wheel: Path,
    output: Path,
    audited_commit: str,
) -> None:
    """Install one wheel and execute migration checks outside the source tree."""
    _clear_output_target(output)
    audited_commit = validate_audited_commit(audited_commit, REPOSITORY_ROOT)
    wheel = resolve_wheel_path(wheel)
    wheel_digest = wheel_sha256(wheel)
    schema_prefix = _new_wheel_schema_prefix()
    _validate_wheel_schema_prefix(schema_prefix)
    temp_root = Path(tempfile.gettempdir()) / "dragon-radar-task21" / ".temp"
    run_token = schema_prefix.removeprefix("release_wheel_").removesuffix("_")
    venv = temp_root / f"task21-wheel-venv-{run_token}"
    run_root = temp_root / f"task21-wheel-run-{run_token}"
    shutil.rmtree(venv, ignore_errors=True)
    shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)
    helper = run_root / "wheel_helper.py"
    copied_verifier = run_root / "verifier.py"
    child_output = run_root / "child-evidence.json"
    helper.write_text(_wheel_helper_source(), encoding="utf-8")
    shutil.copy2(Path(__file__).resolve(), copied_verifier)
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["DRAGON_TEST_DATABASE_URL"] = database_url
    primary_error = None
    cleanup_errors: list[BaseException] = []
    child_evidence = None
    helper_stdout = ""
    try:
        result = _run_process(
            [
                "uv",
                "venv",
                "--python",
                sys.executable,
                "--clear",
                str(venv),
            ],
            cwd=run_root,
            env=environment,
            timeout=120,
        )
        _require_process_success(result, stage="wheel virtual environment creation")
        python = _venv_python(venv)
        result = _run_process(
            ["uv", "pip", "install", "--python", str(python), str(wheel)],
            cwd=run_root,
            env=environment,
            timeout=180,
        )
        _require_process_success(result, stage="wheel installation")
        result = _run_process(
            [
                str(python),
                str(helper),
                str(REPOSITORY_ROOT),
                str(child_output),
                schema_prefix,
            ],
            cwd=run_root,
            env=environment,
            timeout=300,
        )
        _require_process_success(
            result,
            stage="installed wheel migration verification",
        )
        child_evidence = _load_wheel_child_evidence(child_output)
        helper_stdout = result.stdout
    except BaseException as exc:
        primary_error = exc
    finally:
        shutil.rmtree(venv, ignore_errors=True)
        shutil.rmtree(run_root, ignore_errors=True)
        try:
            _drop_wheel_schemas(database_url, schema_prefix)
        except BaseException as exc:
            cleanup_errors.append(exc)
    if primary_error is not None:
        if cleanup_errors:
            raise BaseExceptionGroup(
                "installed wheel verification and cleanup failed",
                [primary_error, *cleanup_errors],
            )
        raise primary_error.with_traceback(primary_error.__traceback__)
    if cleanup_errors:
        raise cleanup_errors[0]
    if child_evidence is None:
        raise RuntimeError("installed wheel verification produced no evidence")
    evidence = {
        "schema": child_evidence["schema"],
        "schema_version": child_evidence["schema_version"],
        "generated_at": datetime.now(UTC).isoformat(),
        "wheel_filename": wheel.name,
        "wheel_sha256": wheel_digest,
        "audited_commit": audited_commit,
        "imported_from": child_evidence["imported_from"],
        "fresh": child_evidence["fresh"],
        "supported_upgrades": child_evidence["supported_upgrades"],
    }
    write_contract_atomically(evidence, output)
    print(helper_stdout, end="")


def write_contract_atomically(
    contract: dict[str, object],
    output_path: Path,
) -> None:
    """Write a validated contract without exposing a partial target file."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(contract, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _run_generation_lifecycle(database_url: str) -> dict[str, object]:
    from sqlalchemy import text

    url = _validated_postgres_url(database_url)
    admin_url = url.difference_update_query(["options"])
    admin_engine = _create_admin_engine(admin_url)
    schema = _new_schema_name()
    database = None
    schema_created = False
    contract = None
    primary_error = None
    cleanup_errors = []
    try:
        if re.fullmatch(r"schema_contract_[0-9a-f]{32}", schema) is None:
            raise ValueError("unsafe generated schema name")
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        schema_created = True
        scoped_url = admin_url.update_query_dict({"options": f"-csearch_path={schema}"})
        database = _database_class()(scoped_url.render_as_string(hide_password=False))
        database.migrate()
        with database.engine.connect() as connection:
            revision = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            if revision != RELEASE_HEAD:
                raise ValueError(f"generated database revision is not {RELEASE_HEAD}")
            capture_schema_contract, validate_schema_contract = (
                _schema_contract_functions()
            )
            contract = capture_schema_contract(connection)
            validate_schema_contract(contract)
    except BaseException as exc:
        primary_error = exc
    finally:
        try:
            if database is not None:
                database.close()
        except BaseException as exc:
            cleanup_errors.append(exc)
        try:
            if schema_created:
                with admin_engine.begin() as connection:
                    connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        except BaseException as exc:
            cleanup_errors.append(exc)
        try:
            admin_engine.dispose()
        except BaseException as exc:
            cleanup_errors.append(exc)

    if primary_error is not None:
        if cleanup_errors:
            raise BaseExceptionGroup(
                "schema contract generation and cleanup failed",
                [primary_error, *cleanup_errors],
            )
        raise primary_error.with_traceback(primary_error.__traceback__)
    if cleanup_errors:
        if len(cleanup_errors) == 1:
            cleanup_error = cleanup_errors[0]
            raise cleanup_error.with_traceback(cleanup_error.__traceback__)
        raise BaseExceptionGroup(
            "schema contract cleanup failed",
            cleanup_errors,
        )
    if contract is None:
        raise RuntimeError("schema contract generation produced no contract")
    return contract


def generate_schema_contract(
    output_path: Path,
    database_url: str,
) -> dict[str, object]:
    """Generate a schema contract in a disposable PostgreSQL schema."""
    contract = _run_generation_lifecycle(database_url)
    write_contract_atomically(contract, output_path)
    return contract


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_MIGRATIONS,
        help="migration package root (defaults to the repository package)",
    )
    parser.add_argument(
        "--write-schema-contract",
        type=Path,
        metavar="PATH",
        help="generate the current schema contract at PATH",
    )
    parser.add_argument(
        "--database-url",
        help="PostgreSQL URL for disposable schema generation",
    )
    parser.add_argument(
        "--wheel",
        type=Path,
        help="wheel file, directory, or glob to install and verify",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write installed-wheel migration evidence to PATH",
    )
    parser.add_argument(
        "--audited-commit",
        help="full lowercase Git commit audited by installed-wheel verification",
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        help="repository containing the audited commit",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit stable machine-readable commit verification",
    )
    parser.add_argument(
        "--restore-container",
        metavar="NAME",
        help="run the backup/restore drill in a named PostgreSQL container",
    )
    args = parser.parse_args(argv)
    if args.json or args.repository_root is not None:
        if not args.json or args.repository_root is None or args.audited_commit is None:
            print(
                "--json, --repository-root, and --audited-commit are required together",
                file=sys.stderr,
            )
            return 1
        try:
            result = verify_repository_commit(
                args.repository_root,
                args.audited_commit,
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            print("release migration commit verification failed", file=sys.stderr)
            return 1
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if not result["errors"] else 1
    if args.restore_container is not None:
        if args.output is None:
            print("--output is required with --restore-container", file=sys.stderr)
            return 1
        database_url = args.database_url or os.environ.get("DRAGON_TEST_DATABASE_URL")
        if not database_url:
            try:
                _clear_output_target(args.output)
            except (OSError, ValueError):
                pass
            print("database URL is required", file=sys.stderr)
            return 1
        try:
            result = exercise_backup_restore(
                args.restore_container,
                database_url,
                args.output,
                audited_commit=args.audited_commit,
            )
        except Exception:
            print("backup restore drill failed", file=sys.stderr)
            return 1
        print(
            "backup restore verified: "
            f"sha256={result['backup_sha256']} "
            f"elapsed_seconds={result['elapsed_seconds']:.6f} "
            "cleanup=complete"
        )
        return 0
    if args.wheel is not None:
        if args.output is None:
            print("--output is required with --wheel", file=sys.stderr)
            return 1
        try:
            _clear_output_target(args.output)
        except (OSError, ValueError):
            print("installed wheel migration verification failed", file=sys.stderr)
            return 1
        if args.audited_commit is None:
            print("--audited-commit is required with --wheel", file=sys.stderr)
            return 1
        database_url = args.database_url or os.environ.get("DRAGON_TEST_DATABASE_URL")
        if not database_url:
            print("database URL is required", file=sys.stderr)
            return 1
        try:
            verify_installed_wheel(
                database_url,
                args.wheel,
                args.output,
                args.audited_commit,
            )
        except Exception:
            print("installed wheel migration verification failed", file=sys.stderr)
            return 1
        return 0
    if args.write_schema_contract is not None:
        database_url = args.database_url or os.environ.get("DRAGON_TEST_DATABASE_URL")
        if not database_url:
            print("database URL is required", file=sys.stderr)
            return 1
        try:
            result = verify_manifest(DEFAULT_MIGRATIONS)
            if result["errors"]:
                for error in result["errors"]:
                    print(error, file=sys.stderr)
                return 1
            require_clean_migration_tree()
            contract = generate_schema_contract(
                args.write_schema_contract,
                database_url,
            )
        except Exception:
            print("schema contract generation failed", file=sys.stderr)
            return 1
        print(
            f"{len(contract['tables'])} tables, "
            f"{len(contract['constraints'])} constraints, "
            f"{len(contract['indexes'])} indexes, "
            f"{len(contract['triggers'])} triggers written to "
            f"{args.write_schema_contract}"
        )
        return 0
    try:
        result = verify_manifest(args.root)
    except (OSError, ValueError) as exc:
        print(f"release migration verification failed: {exc}", file=sys.stderr)
        return 1
    if result["errors"]:
        for error in result["errors"]:
            print(error, file=sys.stderr)
        return 1
    print(f"{result['head']}: {result['revision_count']} revisions verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
