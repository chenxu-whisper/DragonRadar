"""Test-only RSA identities never configure production trust."""

import copy
import hashlib
import importlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

try:
    trust = importlib.import_module("dragon_radar.production.trust")
except ModuleNotFoundError:
    trust = None

NOW = datetime(2026, 11, 16, 12, tzinfo=UTC)
CANDIDATE = dict.fromkeys(
    ("code", "dataset", "rules", "model", "prompt", "retrieval"), "test-version"
)
CONTEXT = {
    "subject": "test-owner",
    "purpose": "research",
    "target": "local",
    "audience": "owner",
}
OPENSSL = shutil.which("openssl") or (
    r"C:\Program Files\Git\usr\bin\openssl.exe" if os.name == "nt" else None
)


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, timeout=20).stdout


def protect(root):
    if os.name == "nt":
        helper = Path(__file__).parents[1] / "scripts/private-common.ps1"
        env = dict(os.environ, TRUST_TEST_ROOT=str(root), TRUST_TEST_HELPER=str(helper))
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                ". $env:TRUST_TEST_HELPER; "
                "Initialize-PrivateDirectory $env:TRUST_TEST_ROOT | Out-Null; "
                "Get-ChildItem -LiteralPath $env:TRUST_TEST_ROOT -Recurse -File | "
                "ForEach-Object { Set-PrivateFileAcl $_.FullName | Out-Null }",
            ],
            env=env,
            check=True,
            capture_output=True,
            timeout=20,
        )
    else:
        root.chmod(0o700)
        for file in root.rglob("*"):
            file.chmod(0o700 if file.is_dir() else 0o600)


@pytest.fixture(scope="session")
def rsa_keys(tmp_path_factory):
    assert trust is not None, "production trust module has not been implemented"
    if not OPENSSL or not Path(OPENSSL).is_file():
        pytest.fail("Real OpenSSL required for trust tests")
    root = tmp_path_factory.mktemp("test-only-keys")
    private = root / "private.pem"
    run(
        OPENSSL,
        "genpkey",
        "-algorithm",
        "RSA",
        "-pkeyopt",
        "rsa_keygen_bits:2048",
        "-out",
        str(private),
    )
    public = run(OPENSSL, "pkey", "-in", str(private), "-pubout")
    return private, public


@pytest.fixture
def bundle(tmp_path, rsa_keys):
    private, public = rsa_keys
    root = tmp_path / "assets"
    root.mkdir()
    (root / "key.pem").write_bytes(public)
    material = b"test-only proof, not real production evidence"
    (root / "proof.txt").write_bytes(material)
    record = {
        "schema": "dragon-radar-production-evidence/v1",
        "gate_id": "real_input",
        "evidence_id": "test-only-evidence",
        "kind": "real",
        "issuer": "test-only-issuer",
        "key_id": "test-key",
        "approval_id": "test-only-approval",
        "approver": "test-only-reviewer",
        "issued_at": "2026-11-15T00:00:00Z",
        "approved_at": "2026-11-15T01:00:00Z",
        "valid_from": "2026-11-15T02:00:00Z",
        "expires_at": "2026-11-17T00:00:00Z",
        "revoked": False,
        "candidate": CANDIDATE.copy(),
        "scopes": {k: [v] for k, v in CONTEXT.items()},
        "materials": [
            {
                "material_id": "proof",
                "path": "proof.txt",
                "sha256": hashlib.sha256(material).hexdigest(),
            }
        ],
        "payload": {"gate_data": {"report": "proof", "metric": 0.95}},
        "signature": {"algorithm": "RSA-PSS-SHA256", "path": "record.sig"},
    }
    key = trust.TrustedKey(
        public_key_path="key.pem", sha256=hashlib.sha256(public).hexdigest()
    )
    issuer = trust.TrustedIssuer(
        keys={"test-key": key},
        gates=frozenset(trust.GATE_IDS),
        scopes={k: frozenset([v]) for k, v in CONTEXT.items()},
        approvers=frozenset(["test-only-reviewer"]),
    )
    policy = trust.TrustPolicy(
        openssl_executable=Path(OPENSSL), issuers={"test-only-issuer": issuer}
    )

    def save(value=record, signer=private):
        data = canonical(value)
        (root / "record.json").write_bytes(data)
        run(
            OPENSSL,
            "dgst",
            "-sha256",
            "-sign",
            str(signer),
            "-sigopt",
            "rsa_padding_mode:pss",
            "-sigopt",
            "rsa_pss_saltlen:32",
            "-sigopt",
            "rsa_mgf1_md:sha256",
            "-out",
            str(root / "record.sig"),
            str(root / "record.json"),
        )
        protect(root)

    save()
    return root, record, policy, save


def verify(bundle, **kwargs):
    root, _, policy, _ = bundle
    return trust.verify_evidence(
        kwargs.pop("record_path", "record.json"),
        root,
        kwargs.pop("trust_policy", policy),
        kwargs.pop("candidate", CANDIDATE),
        kwargs.pop("context", CONTEXT),
        kwargs.pop("now", NOW),
        **kwargs,
    )


def test_public_api_exists():
    assert trust is not None, "production trust module has not been implemented"
    for name in (
        "TrustPolicy",
        "TrustedIssuer",
        "TrustedKey",
        "EvidenceRecord",
        "VerifiedEvidence",
        "MissingEvidence",
        "InvalidEvidence",
        "VerifierUnavailable",
        "verify_evidence",
    ):
        assert hasattr(trust, name)


def test_canonical_json_public_api():
    assert hasattr(trust, "canonical_json"), "canonical_json public API is missing"
    value = {"z": [True, None, 0.95], "a": "\u00e9"}
    assert trust.canonical_json(value) == canonical(value)
    assert trust.strict_json(trust.canonical_json(value)) == value


def test_canonical_json_accepts_frozen_payload(bundle):
    assert hasattr(trust, "canonical_json"), "canonical_json public API is missing"
    result = verify(bundle)
    assert (
        trust.strict_json(trust.canonical_json(result.record.payload))
        == (bundle[1]["payload"])
    )


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"a": float("nan")},
        {"a": float("inf")},
        {"a": b"not-json"},
        {"a": "\ud800"},
        {"a": 2**63},
    ],
)
def test_canonical_json_rejects_invalid_json(value):
    assert hasattr(trust, "canonical_json"), "canonical_json public API is missing"
    with pytest.raises(trust.InvalidEvidence):
        trust.canonical_json(value)


def test_public_read_asset(bundle):
    assert hasattr(trust, "read_asset"), "read_asset public API is missing"
    assert (
        trust.read_asset(bundle[0], "proof.txt", 1024)
        == (bundle[0] / "proof.txt").read_bytes()
    )


@pytest.mark.parametrize(
    "path,maximum",
    [
        ("../escape", 1024),
        ("proof.txt", 1),
        ("proof.txt", 0),
        ("proof.txt", 16 * 1024 * 1024 + 1),
    ],
)
def test_public_read_asset_rejects_escape_or_invalid_bounds(bundle, path, maximum):
    assert hasattr(trust, "read_asset"), "read_asset public API is missing"
    with pytest.raises(trust.InvalidEvidence):
        trust.read_asset(bundle[0], path, maximum)


def test_real_signature_returns_bound_envelope_and_materials(bundle):
    result = verify(bundle)
    assert isinstance(result, trust.VerifiedEvidence)
    assert isinstance(result.record, trust.EvidenceRecord)
    assert result.record.gate_id == "real_input"
    assert result.record.payload["gate_data"]["report"] == "proof"
    assert result.materials["proof"] == (bundle[0] / "proof.txt").read_bytes()
    assert (
        result.record_sha256
        == hashlib.sha256((bundle[0] / "record.json").read_bytes()).hexdigest()
    )
    assert "test-only proof" not in repr(result)
    with pytest.raises(TypeError):
        result.record.payload["gate_data"]["report"] = "changed"


@pytest.mark.parametrize(
    "gate",
    [
        "real_input",
        "official_rules",
        "real_model_evaluation",
        "public_sharing_permit",
        "shadow_run_20_trading_days",
    ],
)
def test_five_canonical_gates(bundle, gate):
    bundle[1]["gate_id"] = gate
    bundle[3]()
    assert verify(bundle).record.gate_id == gate


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(kind="synthetic"),
        lambda r: r.update(revoked=True),
        lambda r: r.update(revoked="false"),
        lambda r: r.update(expires_at="2026-11-16T12:00:00Z"),
        lambda r: r.update(issued_at="2026-11-16T13:00:00Z"),
        lambda r: r.update(approved_at="2026-11-14T00:00:00Z"),
        lambda r: r.update(valid_from="2026-11-16T13:00:00Z"),
        lambda r: r.update(approved_at="2026-11-15T01:00:00"),
        lambda r: r.update(gate_id="assistant-approved"),
        lambda r: r.update(approver="assistant"),
        lambda r: r.update(issuer="self"),
        lambda r: r.update(key_id="other-key"),
        lambda r: r.update(command="anything"),
        lambda r: r["candidate"].update(code="drift"),
        lambda r: r["candidate"].update(extra="unknown"),
        lambda r: r["scopes"].update(target=["elsewhere"]),
        lambda r: r["scopes"].update(extra=["unknown"]),
        lambda r: r["signature"].update(algorithm="RSA-SHA256"),
        lambda r: r["signature"].update(command="openssl"),
        lambda r: r["materials"][0].update(sha256="0" * 64),
        lambda r: r["materials"][0].update(extra="unknown"),
        lambda r: r["materials"].append(copy.deepcopy(r["materials"][0])),
        lambda r: r.update(materials=[]),
        lambda r: r.update(payload=[]),
    ],
)
def test_signed_invalid_content_is_rejected(bundle, mutation):
    mutation(bundle[1])
    bundle[3]()
    with pytest.raises(trust.InvalidEvidence):
        verify(bundle)


@pytest.mark.parametrize(
    "component", ["record.json", "proof.txt", "record.sig", "key.pem"]
)
def test_tamper_rejected(bundle, component):
    path = bundle[0] / component
    if component == "record.json":
        record = copy.deepcopy(bundle[1])
        record["payload"]["gate_data"]["metric"] = 1
        path.write_bytes(canonical(record))
    else:
        path.write_bytes(path.read_bytes() + b"tamper")
    with pytest.raises(trust.InvalidEvidence):
        verify(bundle)


def test_default_empty_policy_rejects_self_signed_evidence(bundle):
    with pytest.raises(trust.InvalidEvidence):
        verify(bundle, trust_policy=trust.TrustPolicy(openssl_executable=Path(OPENSSL)))


@pytest.mark.parametrize("field", ["gates", "scopes", "approvers", "revoked"])
def test_external_authority_restricts_issuer(bundle, field):
    from dataclasses import replace

    policy = bundle[2]
    issuer = policy.issuers["test-only-issuer"]
    value = {
        "gates": frozenset(["official_rules"]),
        "scopes": {**issuer.scopes, "target": frozenset(["elsewhere"])},
        "approvers": frozenset(["different-human"]),
        "revoked": True,
    }[field]
    policy = replace(
        policy,
        issuers={
            "test-only-issuer": replace(issuer, **{field: value}),
        },
    )
    with pytest.raises(trust.InvalidEvidence):
        verify(bundle, trust_policy=policy)


def test_external_key_revocation(bundle):
    from dataclasses import replace

    issuer = bundle[2].issuers["test-only-issuer"]
    issuer = replace(
        issuer, keys={"test-key": replace(issuer.keys["test-key"], revoked=True)}
    )
    with pytest.raises(trust.InvalidEvidence):
        verify(
            bundle,
            trust_policy=replace(bundle[2], issuers={"test-only-issuer": issuer}),
        )


@pytest.mark.parametrize(
    "field,identity",
    [
        ("revoked_approval_ids", "test-only-approval"),
        ("revoked_evidence_ids", "test-only-evidence"),
    ],
)
def test_external_revocation_rejects_old_signed_record(bundle, field, identity):
    from dataclasses import replace

    assert hasattr(bundle[2], field), "External revocation list API is missing"
    original = (bundle[0] / "record.json").read_bytes()
    policy = replace(bundle[2], **{field: frozenset([identity])})
    with pytest.raises(trust.InvalidEvidence):
        verify(bundle, trust_policy=policy)
    assert (bundle[0] / "record.json").read_bytes() == original


def test_external_revocation_defaults_empty():
    policy = trust.TrustPolicy()
    assert hasattr(policy, "revoked_approval_ids"), "Revocation API is missing"
    assert policy.revoked_approval_ids == frozenset()
    assert policy.revoked_evidence_ids == frozenset()


@pytest.mark.parametrize("location", ["record", "material", "signature", "key"])
@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "/absolute",
        "C:/absolute",
        "dir/../proof.txt",
        "dir\\proof.txt",
        "file:stream",
        "./record.json",
    ],
)
def test_path_escapes_rejected(bundle, location, path):
    from dataclasses import replace

    kwargs = {}
    if location == "record":
        kwargs["record_path"] = path
    elif location == "material":
        bundle[1]["materials"][0]["path"] = path
        bundle[3]()
    elif location == "signature":
        bundle[1]["signature"]["path"] = path
        bundle[3]()
    else:
        issuer = bundle[2].issuers["test-only-issuer"]
        kwargs["trust_policy"] = replace(
            bundle[2],
            issuers={
                "test-only-issuer": replace(
                    issuer,
                    keys={
                        "test-key": replace(
                            issuer.keys["test-key"], public_key_path=path
                        ),
                    },
                ),
            },
        )
    with pytest.raises(trust.InvalidEvidence):
        verify(bundle, **kwargs)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema":"x","schema":"y"}',
        b'{"payload":{"a":1,"a":2}}',
        b'{"payload":NaN}',
        b'{"payload":Infinity}',
        b"\xff",
    ],
)
def test_strict_json_rejected_before_verifier(bundle, raw):
    from dataclasses import replace

    (bundle[0] / "record.json").write_bytes(raw)
    with pytest.raises(trust.InvalidEvidence):
        verify(bundle, trust_policy=replace(bundle[2], openssl_executable=None))


def test_noncanonical_json_rejected(bundle):
    (bundle[0] / "record.json").write_text(json.dumps(bundle[1]), encoding="utf-8")
    with pytest.raises(trust.InvalidEvidence):
        verify(bundle)


@pytest.mark.parametrize(
    "component", ["record.json", "record.sig", "proof.txt", "key.pem"]
)
def test_missing_assets_have_safe_typed_error(bundle, component):
    (bundle[0] / component).unlink()
    with pytest.raises(trust.MissingEvidence) as error:
        verify(bundle)
    assert str(bundle[0]) not in str(error.value)


def test_missing_verifier_is_distinct(bundle):
    from dataclasses import replace

    with pytest.raises(trust.VerifierUnavailable):
        verify(bundle, trust_policy=replace(bundle[2], openssl_executable=None))


def test_verifier_timeout_is_distinct(bundle, monkeypatch):
    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired("sensitive-command", 1)

    monkeypatch.setattr(trust.subprocess, "run", timed_out)
    with pytest.raises(trust.VerifierUnavailable) as error:
        verify(bundle)
    assert "sensitive-command" not in str(error.value)


@pytest.mark.parametrize(
    "limit",
    [
        "max_record_bytes",
        "max_material_bytes",
        "max_total_material_bytes",
        "max_signature_bytes",
        "max_key_bytes",
    ],
)
def test_bounded_sizes(bundle, limit):
    from dataclasses import replace

    with pytest.raises(trust.InvalidEvidence):
        verify(bundle, trust_policy=replace(bundle[2], **{limit: 1}))


@pytest.mark.parametrize("mapping", ["candidate", "context"])
def test_missing_or_extra_context_is_not_wildcard(bundle, mapping):
    value = (CANDIDATE if mapping == "candidate" else CONTEXT).copy()
    value.pop(next(iter(value)))
    with pytest.raises(trust.InvalidEvidence):
        verify(bundle, **{mapping: value})


def test_now_must_be_explicit_utc(bundle):
    with pytest.raises(trust.InvalidEvidence):
        verify(bundle, now=NOW.replace(tzinfo=None))


def test_private_acl_required(bundle):
    path = bundle[0] / "proof.txt"
    if os.name == "nt":
        run("icacls.exe", str(path), "/grant", "*S-1-1-0:(R)")
    else:
        path.chmod(0o644)
    with pytest.raises(trust.InvalidEvidence):
        verify(bundle)


@pytest.mark.parametrize("location", ["root", "ancestor", "material"])
def test_symlink_or_junction_traversal_rejected(bundle, tmp_path, location):
    root = bundle[0]
    link = tmp_path / "linked"
    if os.name == "nt":
        # Junction creation does not require Windows symlink privilege.
        env = dict(os.environ, TRUST_LINK=str(link), TRUST_TARGET=str(root))
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "New-Item -ItemType Junction -Path $env:TRUST_LINK "
                "-Target $env:TRUST_TARGET | Out-Null",
            ],
            check=True,
            capture_output=True,
            env=env,
            timeout=10,
        )
    else:
        link.symlink_to(root, target_is_directory=True)
    try:
        if location == "material":
            bundle[1]["materials"][0]["path"] = "linked/proof.txt"
            bundle[3]()
            shutil.move(str(link), str(root / "linked"))
            with pytest.raises(trust.InvalidEvidence):
                verify(bundle)
        else:
            asset_root = link if location == "root" else link / "child"
            with pytest.raises(trust.InvalidEvidence):
                trust.verify_evidence(
                    "record.json", asset_root, bundle[2], CANDIDATE, CONTEXT, NOW
                )
    finally:
        actual_link = root / "linked" if location == "material" else link
        if os.name == "nt":
            os.rmdir(actual_link)
        else:
            actual_link.unlink()
