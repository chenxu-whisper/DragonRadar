"""Verify the committed Task20 evidence bundle without executing acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "dragon-radar.task20-committed-evidence"
SHA256 = re.compile(r"[0-9a-f]{64}")


def _tracked_paths(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return {
        path.decode("utf-8").replace("\\", "/")
        for path in result.stdout.split(b"\0")
        if path
    }


def _exact_fields(value: Any, expected: set[str], label: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{label} is not an object."]
    actual = set(value)
    if actual == expected:
        return []
    return [
        f"{label} fields differ: missing={sorted(expected - actual)}, "
        f"extra={sorted(actual - expected)}."
    ]


def validate_bundle(
    root: Path,
    manifest_path: Path,
    tracked_paths: set[str],
) -> list[str]:
    root = root.resolve()
    manifest_path = manifest_path.resolve()
    errors = []
    try:
        manifest_relative = manifest_path.relative_to(root).as_posix()
    except ValueError:
        return ["Evidence manifest must be inside the repository."]
    if manifest_relative not in tracked_paths:
        errors.append("Evidence manifest is not Git-tracked.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return [*errors, f"Evidence manifest is unreadable: {type(error).__name__}."]
    errors.extend(
        _exact_fields(
            manifest,
            {
                "schema",
                "schema_version",
                "implementation_commit",
                "execution_provenance",
                "files",
            },
            "Evidence manifest",
        )
    )
    if errors:
        return errors
    if manifest["schema"] != SCHEMA or manifest["schema_version"] != 1:
        errors.append("Evidence manifest schema is unsupported.")
    if not isinstance(manifest["implementation_commit"], str) or not re.fullmatch(
        r"[0-9a-f]{40}",
        manifest["implementation_commit"],
    ):
        errors.append("Evidence implementation commit is invalid.")
    if (
        not isinstance(manifest["execution_provenance"], str)
        or not manifest["execution_provenance"].strip()
    ):
        errors.append("Evidence execution provenance is missing.")
    files = manifest["files"]
    if not isinstance(files, list) or not files:
        return [*errors, "Evidence file list is empty."]
    listed_paths = [item.get("path") for item in files if isinstance(item, dict)]
    if len(listed_paths) != len(files) or listed_paths != sorted(set(listed_paths)):
        errors.append("Evidence file paths must be unique and sorted.")
    for index, item in enumerate(files):
        label = f"Evidence file {index}"
        item_errors = _exact_fields(item, {"path", "sha256", "bytes"}, label)
        if item_errors:
            errors.extend(item_errors)
            continue
        path_value = item["path"]
        relative = PurePosixPath(path_value) if isinstance(path_value, str) else None
        if (
            relative is None
            or relative.is_absolute()
            or ".." in relative.parts
            or "\\" in path_value
            or str(relative) in {"", "."}
        ):
            errors.append(f"{label} path must be a normalized relative path.")
            continue
        artifact_path = manifest_path.parent.joinpath(*relative.parts)
        try:
            repository_path = artifact_path.resolve().relative_to(root).as_posix()
        except ValueError:
            errors.append(f"{label} path must remain inside the repository.")
            continue
        if repository_path not in tracked_paths:
            errors.append(f"{label} is not Git-tracked.")
        try:
            content = artifact_path.read_bytes()
        except OSError as error:
            errors.append(f"{label} is unreadable: {type(error).__name__}.")
            continue
        if (
            not isinstance(item["sha256"], str)
            or not SHA256.fullmatch(item["sha256"])
            or hashlib.sha256(content).hexdigest() != item["sha256"]
        ):
            errors.append(f"{label} SHA-256 does not match.")
        if type(item["bytes"]) is not int or item["bytes"] != len(content):
            errors.append(f"{label} byte count does not match.")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest",
        type=Path,
        nargs="?",
        default=Path("docs/acceptance/evidence/task20/manifest.json"),
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = args.manifest if args.manifest.is_absolute() else root / args.manifest
    errors = validate_bundle(root, manifest, _tracked_paths(root))
    if errors:
        for error in errors:
            print(error)
        return 1
    print(
        f"Task20 committed evidence verified: {manifest.relative_to(root).as_posix()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
