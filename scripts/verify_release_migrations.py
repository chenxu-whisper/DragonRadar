"""Verify that the packaged Alembic graph matches the frozen release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

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
    root = root.resolve()
    if (root / "release_manifest.json").is_file():
        return root
    package_root = root / "src" / "dragon_radar" / "migrations"
    if package_root.is_dir():
        return package_root.resolve()
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_MIGRATIONS,
        help="migration package root (defaults to the repository package)",
    )
    args = parser.parse_args(argv)
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
