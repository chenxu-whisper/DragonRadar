"""Local production proof importer with explicitly pinned external trust."""

import argparse
import hashlib
import json
import os
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from dragon_radar.production.gates import exact
from dragon_radar.production.report import blocked_report, evaluate_production
from dragon_radar.production.trust import (
    SCOPE_FIELDS,
    EvidenceError,
    InvalidEvidence,
    MissingEvidence,
    TrustedIssuer,
    TrustedKey,
    TrustPolicy,
    read_asset,
    strict_json,
)

CODE_ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIRECTORIES = ("src", "scripts", "web", "tests", ".github", "containers")
ROOT_INPUTS = (
    "pyproject.toml",
    "uv.lock",
    "compose.yaml",
    "Dockerfile",
    "alembic.ini",
    ".dockerignore",
    ".python-version",
    ".gitattributes",
)
SOURCE_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".mjs",
    ".js",
    ".css",
    ".html",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".lock",
    ".sh",
    ".ps1",
}
GENERATED = {
    "__pycache__",
    "node_modules",
    "dist",
    ".temp",
    ".venv",
    "test-results",
    "playwright-report",
    ".git",
}


def source_identity(root: Path) -> str:
    """Bind current code and test/toolchain inputs, including uncommitted files."""
    root = Path(root).absolute()
    paths = []
    for directory in SOURCE_DIRECTORIES:
        folder = root / directory
        if not folder.exists():
            continue
        for current, directories, files in os.walk(folder, followlinks=False):
            directories[:] = sorted(set(directories) - GENERATED)
            for name in directories + files:
                path = Path(current) / name
                info = path.lstat()
                if (
                    stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & 0x400
                ):
                    raise ValueError("source links forbidden")
            for name in files:
                path = Path(current) / name
                if directory == "containers" or path.suffix in SOURCE_SUFFIXES:
                    paths.append(path)
            if len(paths) > 20000:
                raise ValueError("source inventory exceeds bound")
    paths.extend(root / name for name in ROOT_INPUTS if (root / name).is_file())
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.relative_to(root).as_posix()):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 32 * 1024 * 1024:
            raise ValueError("source file invalid")
        relative = path.relative_to(root).as_posix().encode()
        content_hash = hashlib.sha256(path.read_bytes()).digest()
        digest.update(len(relative).to_bytes(4, "big") + relative + content_hash)
    return "source-sha256:" + digest.hexdigest()


def load_json(path: Path):
    path = Path(path).absolute()
    return strict_json(read_asset(path.parent, path.name, 16 * 1024 * 1024))


def current_source_identity(root: Path) -> str:
    """Refuse layouts that cannot bind the loaded verifier to a source checkout."""
    root = Path(root).absolute()
    loaded = Path(__file__).resolve()
    expected = root / "src/dragon_radar/production/cli.py"
    if (
        not expected.is_file()
        or expected.resolve() != loaded
        or any(not (root / name).is_dir() for name in SOURCE_DIRECTORIES)
        or any(not (root / name).is_file() for name in ROOT_INPUTS)
    ):
        raise ValueError("current source layout unavailable")
    return source_identity(root)


def _policy_strings(value):
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        raise InvalidEvidence()
    return frozenset(value)


def load_policy(path: Path, expected_sha256: str, openssl: Path | None):
    """A proof directory is never allowed to install its own authority."""
    raw = read_asset(path.absolute().parent, path.name, 1024 * 1024)
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or hashlib.sha256(raw).hexdigest() != expected_sha256
    ):
        raise InvalidEvidence()
    value = strict_json(raw)
    exact(value, {"schema", "issuers", "revoked_approval_ids", "revoked_evidence_ids"})
    if value["schema"] != "dragon-radar-production-trust/v1":
        raise InvalidEvidence()
    if not isinstance(value["issuers"], dict) or len(value["issuers"]) > 128:
        raise InvalidEvidence()
    issuers = {}
    for identity, issuer in value["issuers"].items():
        exact(issuer, {"keys", "gates", "scopes", "approvers", "revoked"})
        if not isinstance(issuer["keys"], dict) or len(issuer["keys"]) > 128:
            raise InvalidEvidence()
        scopes = exact(issuer["scopes"], SCOPE_FIELDS)
        keys = {}
        for key_id, key in issuer["keys"].items():
            exact(key, {"public_key_path", "sha256", "revoked"})
            keys[key_id] = TrustedKey(**key)
        issuers[identity] = TrustedIssuer(
            keys=keys,
            gates=_policy_strings(issuer["gates"]),
            scopes={key: _policy_strings(items) for key, items in scopes.items()},
            approvers=_policy_strings(issuer["approvers"]),
            revoked=issuer["revoked"],
        )
    for field in ("revoked_approval_ids", "revoked_evidence_ids"):
        if (
            not isinstance(value[field], list)
            or any(not isinstance(x, str) or not x for x in value[field])
            or len(set(value[field])) != len(value[field])
        ):
            raise InvalidEvidence()
    return TrustPolicy(
        openssl_executable=openssl,
        issuers=issuers,
        revoked_approval_ids=frozenset(value["revoked_approval_ids"]),
        revoked_evidence_ids=frozenset(value["revoked_evidence_ids"]),
    )


def add_arguments(parser):
    parser.add_argument("--workspace", type=Path, default=CODE_ROOT)
    parser.add_argument("--proofs", type=Path)
    parser.add_argument("--suppliers", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--context", type=Path)
    parser.add_argument("--trust-policy", type=Path)
    parser.add_argument("--trust-policy-sha256")
    parser.add_argument("--openssl", type=Path)


def evaluate_arguments(args, *, code_root=CODE_ROOT):
    workspace = args.workspace.absolute()
    proofs = args.proofs or workspace / "docs" / "proofs"
    suppliers = args.suppliers or workspace / "data" / "suppliers.json"
    availability = {
        "proofs": "present" if proofs.exists() else "missing",
        "suppliers": "present" if suppliers.is_file() else "missing",
        "trust_policy": "configured" if args.trust_policy else "not_configured",
    }
    report = blocked_report()
    report["input_availability"] = availability
    if args.trust_policy is None:
        report["diagnostic"] = (
            "External issuer policy and its approved SHA256 required."
        )
        return report
    try:
        policy = load_policy(args.trust_policy, args.trust_policy_sha256, args.openssl)
        candidate = load_json(args.candidate or proofs / "candidate.json")
        exact(
            candidate,
            {
                "versions",
                "methods",
                "regulation_rules",
                "rule_scope",
                "official_source_hosts",
            },
        )
        context = load_json(args.context or proofs / "context.json")
        exact(context, {"subject", "purpose", "target", "audience", "content", "data"})
        index = load_json(proofs / "index.json")
        exact(index, {"schema", "evidence"})
        if index["schema"] != "dragon-radar-production-index/v1":
            raise InvalidEvidence()
        report = evaluate_production(
            proofs,
            policy,
            candidate["versions"],
            context,
            index["evidence"],
            datetime.now(UTC),
            expected_code=current_source_identity(code_root),
            semantic_candidate={
                "methods": candidate["methods"],
                "regulation_rules": candidate["regulation_rules"],
                "rule_scope": candidate["rule_scope"],
                "official_source_hosts": candidate["official_source_hosts"],
            },
        )
    except MissingEvidence:
        report = blocked_report()
        report["diagnostic"] = "Required configured proof asset is missing."
    except (EvidenceError, ValueError, TypeError, KeyError, OSError):
        report = blocked_report()
        for entry in report["gates"].values():
            entry.update(
                status="fail",
                code="invalid_configuration",
                reason="Supplied configuration is invalid or inapplicable.",
            )
        report["overall_status"] = "fail"
    # Supplier absence cannot overwrite valid independent permits or evaluations.
    if report["gates"]["real_input"]["status"] == "pass":
        try:
            from dragon_radar.production.trust import verify_evidence

            configured = read_asset(
                suppliers.absolute().parent, suppliers.name, 16 * 1024 * 1024
            )
            bound = verify_evidence(
                index["evidence"]["real_input"],
                proofs,
                policy,
                candidate["versions"],
                {k: context[k] for k in ("subject", "purpose", "target", "audience")},
                datetime.now(UTC),
            )
            material = bound.materials[bound.record.payload["supplier"]]
            if hashlib.sha256(configured).digest() != hashlib.sha256(material).digest():
                raise InvalidEvidence()
        except MissingEvidence:
            report["gates"]["real_input"] = {
                "status": "blocked",
                "code": "missing_supplier_configuration",
                "reason": "The configured supplier asset is missing.",
            }
        except (EvidenceError, ValueError, TypeError, KeyError, OSError):
            report["gates"]["real_input"] = {
                "status": "fail",
                "code": "supplier_configuration_mismatch",
                "reason": "Configured supplier differs from approved evidence.",
            }
        states = {item["status"] for item in report["gates"].values()}
        report["overall_status"] = (
            "fail" if "fail" in states else "pass" if states == {"pass"} else "blocked"
        )
    report["input_availability"] = availability
    return report


def write_report(path, report):
    """Atomic safe summary output; proof assets are never copied to workspace."""
    path = Path(path).absolute()
    if not path.parent.is_dir():
        raise ValueError("report parent directory missing")
    fd, temporary = tempfile.mkstemp(prefix=".production-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, indent=2, sort_keys=True, ensure_ascii=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Verify five external production gates"
    )
    add_arguments(parser)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = evaluate_arguments(args)
    write_report(args.output, report)
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return {"pass": 0, "fail": 1, "blocked": 2}[report["overall_status"]]


if __name__ == "__main__":
    raise SystemExit(main())
