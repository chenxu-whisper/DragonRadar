"""Verify that the packaged Alembic graph matches the frozen release manifest."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

MANIFEST_SCHEMA = "dragon-radar.migration-release-manifest"
MANIFEST_SCHEMA_VERSION = 1
RELEASE_HEAD = "0010_ai_tasks"
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


def require_clean_migration_tree(repository_root: Path = REPOSITORY_ROOT) -> None:
    """Reject staged or unstaged changes to tracked migration files."""
    result = subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            "HEAD",
            "--",
            "src/dragon_radar/migrations",
        ],
        cwd=repository_root,
        shell=False,
        check=False,
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


def _new_upgrade_schema_name() -> str:
    return f"release_upgrade_{uuid4().hex}"


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
        "ai_task_id": uuid4(),
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
        _insert_rule(connection, values)
        return (
            "regulation_rule",
            {"id": values["rule_record_id"], "rule_id": values["rule_id"]},
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
    }
    statement, parameters = queries[kind]
    row = connection.execute(text(statement), parameters).mappings().one_or_none()
    if row is None:
        return False
    return all(row[key] == value for key, value in expected.items())


def exercise_upgrade(database_url: str, revision: str) -> dict[str, object]:
    """Upgrade one declared baseline with representative data in a fresh schema."""
    from sqlalchemy import text

    if revision not in SUPPORTED_FROM:
        raise ValueError(f"unsupported upgrade baseline: {revision}")
    url = _validated_postgres_url(database_url)
    admin_url = url.difference_update_query(["options"])
    admin_engine = _create_admin_engine(admin_url)
    schema = _new_upgrade_schema_name()
    database = None
    scoped_engine = None
    schema_created = False
    primary_error = None
    cleanup_errors: list[BaseException] = []
    result = None
    try:
        if re.fullmatch(r"release_upgrade_[0-9a-f]{32}", schema) is None:
            raise ValueError("unsafe generated schema name")
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        schema_created = True
        scoped_url = admin_url.update_query_dict({"options": f"-csearch_path={schema}"})
        scoped_engine = _create_admin_engine(scoped_url)
        values = _sentinel_values()
        with scoped_engine.begin() as connection:
            _upgrade(connection, revision)
            kind, expected = _insert_sentinel(connection, revision, values)
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
                raise ValueError("supported upgrade did not reach 0010_ai_tasks")
            preserved = _sentinel_preserved(connection, kind, expected)
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


def exercise_supported_upgrades(database_url: str) -> list[dict[str, object]]:
    """Exercise every baseline declared by the frozen release policy."""
    return [exercise_upgrade(database_url, revision) for revision in SUPPORTED_FROM]


def exercise_fresh_upgrade(database_url: str) -> dict[str, object]:
    """Create a fresh head schema and require packaged readiness."""
    from sqlalchemy import text

    url = _validated_postgres_url(database_url)
    admin_url = url.difference_update_query(["options"])
    admin_engine = _create_admin_engine(admin_url)
    schema = _new_upgrade_schema_name()
    database = None
    schema_created = False
    primary_error = None
    cleanup_errors: list[BaseException] = []
    result = None
    try:
        if re.fullmatch(r"release_upgrade_[0-9a-f]{32}", schema) is None:
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
        ready = database.ready()
        if revision != RELEASE_HEAD:
            raise ValueError("fresh upgrade did not reach 0010_ai_tasks")
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
if package_file.is_relative_to(source_root):
    raise RuntimeError("wheel verification imported dragon_radar from source")

migrations = package_file.parent / "migrations"
manifest = verifier["verify_manifest"](migrations)
if manifest["errors"]:
    raise RuntimeError("installed migration manifest verification failed")

database_url = os.environ["DRAGON_TEST_DATABASE_URL"]
fresh = verifier["exercise_fresh_upgrade"](database_url)
upgrades = verifier["exercise_supported_upgrades"](database_url)
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


def verify_installed_wheel(
    database_url: str,
    wheel: Path,
    output: Path,
) -> None:
    """Install one wheel and execute migration checks outside the source tree."""
    wheel = resolve_wheel_path(wheel)
    temp_root = Path(tempfile.gettempdir()) / "dragon-radar-task21" / ".temp"
    venv = temp_root / "task21-wheel-venv"
    run_root = temp_root / "task21-wheel-run"
    shutil.rmtree(venv, ignore_errors=True)
    shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)
    helper = run_root / "wheel_helper.py"
    copied_verifier = run_root / "verifier.py"
    helper.write_text(_wheel_helper_source(), encoding="utf-8")
    shutil.copy2(Path(__file__).resolve(), copied_verifier)
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["DRAGON_TEST_DATABASE_URL"] = database_url
    try:
        subprocess.run(
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
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            check=True,
        )
        python = _venv_python(venv)
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), str(wheel)],
            cwd=run_root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
            check=True,
        )
        result = subprocess.run(
            [
                str(python),
                str(helper),
                str(REPOSITORY_ROOT),
                str(Path(output).resolve()),
            ],
            cwd=run_root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "installed wheel migration verification failed: "
                f"{result.stderr.strip()}"
            )
        print(result.stdout, end="")
    finally:
        shutil.rmtree(venv, ignore_errors=True)
        shutil.rmtree(run_root, ignore_errors=True)


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
                raise ValueError("generated database revision is not 0010_ai_tasks")
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
    args = parser.parse_args(argv)
    if args.wheel is not None:
        database_url = args.database_url or os.environ.get("DRAGON_TEST_DATABASE_URL")
        if not database_url:
            print("database URL is required", file=sys.stderr)
            return 1
        if args.output is None:
            print("--output is required with --wheel", file=sys.stderr)
            return 1
        try:
            verify_installed_wheel(database_url, args.wheel, args.output)
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
