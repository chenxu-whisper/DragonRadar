"""Fail-closed local production evidence authentication, not gate approval.

Protocol v1 signs the exact canonical UTF-8 bytes of the entire record, including
the detached signature *reference*. Canonical encoding is json.dumps with sorted
keys, ensure_ascii=False, separators=(',', ':'), allow_nan=False, no BOM/newline.
JSON integers are signed 64-bit; finite floats are supported (not RFC 8785).
RSA-PSS uses SHA-256, MGF1/SHA-256 and exactly 32 salt bytes.

TrustPolicy is supplied by an externally authorized caller, never by evidence.
Key fingerprints are lowercase SHA-256 of exact pinned public PEM file bytes.
All files, including keys/signatures, live below a private local asset root.
Only public key/signature bytes enter temporary files; record bytes go to stdin.
No production identities, signing, tool discovery, network access or gate
semantics are supplied here. Callers must still validate_gate on the result.
"""

import base64
import binascii
import ctypes
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from dragon_radar.owner.files import secure_output

SCHEMA = "dragon-radar-production-evidence/v1"
ALGORITHM = "RSA-PSS-SHA256"
GATE_IDS = frozenset(
    {
        "real_input",
        "official_rules",
        "real_model_evaluation",
        "public_sharing_permit",
        "shadow_run_20_trading_days",
    }
)
CANDIDATE_FIELDS = frozenset(
    {"code", "dataset", "rules", "model", "prompt", "retrieval"}
)
SCOPE_FIELDS = frozenset({"subject", "purpose", "target", "audience"})
_RECORD_FIELDS = frozenset(
    {
        "schema",
        "gate_id",
        "evidence_id",
        "kind",
        "issuer",
        "key_id",
        "approval_id",
        "approver",
        "issued_at",
        "approved_at",
        "valid_from",
        "expires_at",
        "revoked",
        "candidate",
        "scopes",
        "materials",
        "payload",
        "signature",
    }
)


class EvidenceError(Exception):
    """Safe fixed diagnostic; never includes paths, tool output or proof content."""

    code = "evidence_error"
    message = "Evidence verification error"

    def __init__(self):
        super().__init__(self.message)


class MissingEvidence(EvidenceError):
    code = "missing_evidence"
    message = "Required evidence asset is missing"


class InvalidEvidence(EvidenceError):
    code = "invalid_evidence"
    message = "Evidence is invalid or not applicable"


class VerifierUnavailable(EvidenceError):
    code = "verifier_unavailable"
    message = "Configured evidence verifier is unavailable"


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class TrustedKey:
    """Externally pinned exact PEM bytes; path is relative to asset_root."""

    public_key_path: str
    sha256: str
    revoked: bool = False


@dataclass(frozen=True)
class TrustedIssuer:
    """Exact authorities; empty collections authorize nothing, no wildcards."""

    keys: Mapping[str, TrustedKey] = field(default_factory=dict, repr=False)
    gates: frozenset[str] = frozenset()
    scopes: Mapping[str, frozenset[str]] = field(default_factory=dict, repr=False)
    approvers: frozenset[str] = frozenset()
    revoked: bool = False

    def __post_init__(self):
        object.__setattr__(self, "keys", MappingProxyType(dict(self.keys)))
        object.__setattr__(self, "gates", frozenset(self.gates))
        object.__setattr__(
            self,
            "scopes",
            MappingProxyType(
                {key: frozenset(value) for key, value in self.scopes.items()}
            ),
        )
        object.__setattr__(self, "approvers", frozenset(self.approvers))


@dataclass(frozen=True)
class TrustPolicy:
    """External configuration, with hard upper bounds even if misconfigured."""

    openssl_executable: Path | None = None
    issuers: Mapping[str, TrustedIssuer] = field(default_factory=dict, repr=False)
    revoked_approval_ids: frozenset[str] = field(default=frozenset(), repr=False)
    revoked_evidence_ids: frozenset[str] = field(default=frozenset(), repr=False)
    max_record_bytes: int = 1024 * 1024
    max_material_bytes: int = 16 * 1024 * 1024
    max_total_material_bytes: int = 64 * 1024 * 1024
    max_signature_bytes: int = 1024
    max_key_bytes: int = 16384
    max_materials: int = 512
    verifier_timeout_seconds: float = 5.0

    def __post_init__(self):
        object.__setattr__(self, "issuers", MappingProxyType(dict(self.issuers)))
        object.__setattr__(
            self, "revoked_approval_ids", frozenset(self.revoked_approval_ids)
        )
        object.__setattr__(
            self, "revoked_evidence_ids", frozenset(self.revoked_evidence_ids)
        )


@dataclass(frozen=True)
class MaterialRef:
    material_id: str
    path: str
    sha256: str


@dataclass(frozen=True)
class SignatureRef:
    algorithm: str
    path: str


@dataclass(frozen=True)
class EvidenceRecord:
    """Exact v1 fields; nested data is immutable and excluded from repr."""

    schema: str
    gate_id: str
    evidence_id: str
    kind: str
    issuer: str
    key_id: str
    approval_id: str
    approver: str
    issued_at: datetime
    approved_at: datetime
    valid_from: datetime
    expires_at: datetime
    revoked: bool
    candidate: Mapping[str, str] = field(repr=False)
    scopes: Mapping[str, tuple[str, ...]] = field(repr=False)
    materials: tuple[MaterialRef, ...] = field(repr=False)
    payload: Mapping[str, Any] = field(repr=False)
    signature: SignatureRef = field(repr=False)


@dataclass(frozen=True)
class VerifiedEvidence:
    """Authenticated only: gate semantics are a separate mandatory decision.

    materials maps both material_id and relative path to the same bytes.
    Ambiguous aliases are rejected, not silently overwritten.
    """

    record: EvidenceRecord = field(repr=False)
    materials: Mapping[str, bytes] = field(repr=False)
    record_sha256: str


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidEvidence()
        result[key] = value
    return result


def _constant(_value):
    raise InvalidEvidence()


def _json_tree(value, depth=0):
    if depth > 32:
        raise InvalidEvidence()
    if isinstance(value, dict):
        for key, item in value.items():
            _text(key, 4096)
            _json_tree(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _json_tree(item, depth + 1)
    elif isinstance(value, str):
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise InvalidEvidence()
    elif type(value) is float:
        if not math.isfinite(value):
            raise InvalidEvidence()
    elif type(value) is int:
        if not -(2**63) <= value < 2**63:
            raise InvalidEvidence()
    elif value is not None and type(value) is not bool:
        raise InvalidEvidence()


def strict_json(content: bytes) -> dict:
    """Strict bounded UTF-8 JSON object for gate material parsing as well.

    Duplicate keys at any depth, NaN/Infinity, invalid Unicode, excessive nesting
    and non-object roots fail with InvalidEvidence, without echoing content.
    This parser does not impose a gate schema or require canonical input.
    """
    try:
        if type(content) is not bytes or len(content) > 16 * 1024 * 1024:
            raise InvalidEvidence()
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
        )
        if not isinstance(value, dict):
            raise InvalidEvidence()
        _json_tree(value)
        return value
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise InvalidEvidence() from None


def canonical_json(value: Mapping) -> bytes:
    """Encode the fixed v1 protocol, including immutable verified payloads."""

    def thaw(item, depth=0):
        if depth > 32:
            raise InvalidEvidence()
        if isinstance(item, Mapping):
            if any(type(key) is not str for key in item):
                raise InvalidEvidence()
            return {key: thaw(child, depth + 1) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [thaw(child, depth + 1) for child in item]
        return item

    try:
        if not isinstance(value, Mapping):
            raise InvalidEvidence()
        value = thaw(value)
        _json_tree(value)
        result = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(result) > 16 * 1024 * 1024:
            raise InvalidEvidence()
        return result
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise InvalidEvidence() from None


def _text(value, limit=256):
    if (
        type(value) is not str
        or not value
        or len(value) > limit
        or value != value.strip()
        or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value)
    ):
        raise InvalidEvidence()
    return value


def _exact(value, fields):
    if not isinstance(value, Mapping) or set(value) != fields:
        raise InvalidEvidence()
    return value


def _digest(value):
    if type(value) is not str or re.fullmatch("[a-f0-9]{64}", value) is None:
        raise InvalidEvidence()
    return value


def _relative(value):
    _text(value, 1024)
    parts = value.split("/")
    if (
        "\\" in value
        or ":" in value
        or PurePosixPath(value).is_absolute()
        or any(part in ("", ".", "..") or part.endswith((" ", ".")) for part in parts)
        or any(
            re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
            for part in parts
        )
    ):
        raise InvalidEvidence()
    return parts


def _utc(value):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise InvalidEvidence()
    return value.astimezone(UTC)


def _time(value):
    _text(value)
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", value):
        raise InvalidEvidence()
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        raise InvalidEvidence() from None


def _policy_limits(policy):
    if not isinstance(policy, TrustPolicy):
        raise InvalidEvidence()
    for name, ceiling in (
        ("max_record_bytes", 1024 * 1024),
        ("max_material_bytes", 16 * 1024 * 1024),
        ("max_total_material_bytes", 64 * 1024 * 1024),
        ("max_key_bytes", 16384),
        ("max_signature_bytes", 1024),
        ("max_materials", 512),
    ):
        value = getattr(policy, name)
        if type(value) is not int or not 1 <= value <= ceiling:
            raise InvalidEvidence()
    timeout = policy.verifier_timeout_seconds
    if type(timeout) not in (int, float) or not 0 < timeout <= 30:
        raise InvalidEvidence()


def _record(value, policy, candidate, context, now):
    _exact(value, _RECORD_FIELDS)
    if value["schema"] != SCHEMA or value["gate_id"] not in GATE_IDS:
        raise InvalidEvidence()
    if value["kind"] != "real" or value["revoked"] is not False:
        raise InvalidEvidence()
    for name in ("evidence_id", "issuer", "key_id", "approval_id", "approver"):
        _text(value[name])
    if (
        value["approval_id"] in policy.revoked_approval_ids
        or value["evidence_id"] in policy.revoked_evidence_ids
    ):
        raise InvalidEvidence()
    for mapping in (candidate, value["candidate"]):
        _exact(mapping, CANDIDATE_FIELDS)
        for item in mapping.values():
            _text(item)
    if dict(candidate) != value["candidate"]:
        raise InvalidEvidence()
    _exact(context, SCOPE_FIELDS)
    _exact(value["scopes"], SCOPE_FIELDS)
    issuer = policy.issuers.get(value["issuer"])
    if (
        not isinstance(issuer, TrustedIssuer)
        or issuer.revoked is not False
        or value["gate_id"] not in issuer.gates
        or value["approver"] not in issuer.approvers
    ):
        raise InvalidEvidence()
    key = issuer.keys.get(value["key_id"])
    if not isinstance(key, TrustedKey) or key.revoked is not False:
        raise InvalidEvidence()
    _digest(key.sha256)
    _relative(key.public_key_path)
    for name in SCOPE_FIELDS:
        _text(context[name])
        scope = value["scopes"][name]
        if type(scope) is not list or not 1 <= len(scope) <= 128:
            raise InvalidEvidence()
        for item in scope:
            _text(item)
        if (
            len(set(scope)) != len(scope)
            or context[name] not in scope
            or not set(scope) <= issuer.scopes.get(name, frozenset())
        ):
            raise InvalidEvidence()
    times = {
        name: _time(value[name])
        for name in ("issued_at", "approved_at", "valid_from", "expires_at")
    }
    if not (
        times["issued_at"]
        <= times["approved_at"]
        <= times["valid_from"]
        <= now
        < times["expires_at"]
    ):
        raise InvalidEvidence()
    signature = _exact(value["signature"], {"algorithm", "path"})
    if signature["algorithm"] != ALGORITHM:
        raise InvalidEvidence()
    _relative(signature["path"])
    raw_materials = value["materials"]
    if (
        type(raw_materials) is not list
        or not 1 <= len(raw_materials) <= policy.max_materials
    ):
        raise InvalidEvidence()
    refs, aliases, ids, paths = [], set(), set(), set()
    for raw in raw_materials:
        _exact(raw, {"material_id", "path", "sha256"})
        identity = _text(raw["material_id"])
        _relative(raw["path"])
        _digest(raw["sha256"])
        current = {identity, raw["path"]}
        if identity in ids or raw["path"] in paths or current & aliases:
            raise InvalidEvidence()
        ids.add(identity)
        paths.add(raw["path"])
        aliases.update(current)
        refs.append(MaterialRef(**raw))
    if not isinstance(value["payload"], dict):
        raise InvalidEvidence()
    record = EvidenceRecord(
        **{
            name: value[name]
            for name in (
                "schema",
                "gate_id",
                "evidence_id",
                "kind",
                "issuer",
                "key_id",
                "approval_id",
                "approver",
                "revoked",
            )
        },
        **times,
        candidate=_freeze(value["candidate"]),
        scopes=_freeze(value["scopes"]),
        materials=tuple(refs),
        payload=_freeze(value["payload"]),
        signature=SignatureRef(**signature),
    )
    return record, key


def _root(asset_root):
    try:
        root = Path(asset_root)
        if not root.is_absolute() or ".." in root.parts:
            raise InvalidEvidence()
        if os.name == "nt" and (
            len(root.drive) != 2
            or root.drive[1] != ":"
            or any(":" in p or p.endswith((" ", ".")) for p in root.parts[1:])
        ):
            raise InvalidEvidence()
        return root
    except (TypeError, ValueError):
        raise InvalidEvidence() from None


def _private_posix(info, directory=False):
    if (
        info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
        or (not directory and info.st_nlink != 1)
    ):
        raise InvalidEvidence()


def _read_posix(root, parts, maximum):
    with ExitStack() as stack:
        parent = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, parent)
        dirs = root.parts[1:] + tuple(parts[:-1])
        for index, part in enumerate(dirs):
            parent = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
            )
            stack.callback(os.close, parent)
            if index >= len(root.parts) - 2:
                _private_posix(os.fstat(parent), directory=True)
        fd = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                raise InvalidEvidence()
            _private_posix(info)
            data = stream.read(maximum + 1)
            if len(data) > maximum:
                raise InvalidEvidence()
            return data


def _win_private(handle):
    """Read-only equivalent of scripts/private-common.ps1 Assert-PrivateFile."""
    from ctypes import wintypes as w

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.GetSecurityInfo.argtypes = [
        w.HANDLE,
        ctypes.c_int,
        w.DWORD,
        ctypes.POINTER(w.LPVOID),
        ctypes.POINTER(w.LPVOID),
        ctypes.POINTER(w.LPVOID),
        ctypes.POINTER(w.LPVOID),
        ctypes.POINTER(w.LPVOID),
    ]
    advapi.GetSecurityInfo.restype = w.DWORD
    advapi.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    advapi.GetTokenInformation.argtypes = [
        w.HANDLE,
        ctypes.c_int,
        w.LPVOID,
        w.DWORD,
        ctypes.POINTER(w.DWORD),
    ]
    advapi.ConvertStringSidToSidW.argtypes = [w.LPCWSTR, ctypes.POINTER(w.LPVOID)]
    advapi.EqualSid.argtypes = [w.LPVOID, w.LPVOID]
    advapi.GetAce.argtypes = [w.LPVOID, w.DWORD, ctypes.POINTER(w.LPVOID)]
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes = [w.LPVOID]
    owner, acl, descriptor = w.LPVOID(), w.LPVOID(), w.LPVOID()
    with ExitStack() as stack:
        if advapi.GetSecurityInfo(
            handle,
            1,
            5,
            ctypes.byref(owner),
            None,
            ctypes.byref(acl),
            None,
            ctypes.byref(descriptor),
        ):
            raise InvalidEvidence()
        stack.callback(kernel.LocalFree, descriptor)
        if not owner.value or not acl.value:
            raise InvalidEvidence()
        token = w.HANDLE()
        if not advapi.OpenProcessToken(
            kernel.GetCurrentProcess(), 8, ctypes.byref(token)
        ):
            raise InvalidEvidence()
        stack.callback(kernel.CloseHandle, token)
        size = w.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not 0 < size.value <= 65536:
            raise InvalidEvidence()
        user = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, user, size, ctypes.byref(size)):
            raise InvalidEvidence()
        user_sid = ctypes.cast(user, ctypes.POINTER(w.LPVOID))[0]
        system = w.LPVOID()
        if not advapi.ConvertStringSidToSidW("S-1-5-18", ctypes.byref(system)):
            raise InvalidEvidence()
        stack.callback(kernel.LocalFree, system)

        def allowed(sid):
            return advapi.EqualSid(sid, user_sid) or advapi.EqualSid(sid, system)

        if not allowed(owner):
            raise InvalidEvidence()
        # ACL header: revision, padding, size, ACE count, padding.
        count = ctypes.c_ushort.from_address(acl.value + 4).value
        for index in range(count):
            ace = w.LPVOID()
            if not advapi.GetAce(acl, index, ctypes.byref(ace)):
                raise InvalidEvidence()
            ace_type = ctypes.c_ubyte.from_address(ace.value).value
            if ace_type == 0:
                if not allowed(ace.value + 8):
                    raise InvalidEvidence()
            elif ace_type != 1:
                # Fail closed for object/callback/unknown ACEs.
                raise InvalidEvidence()


def _read_windows(root, parts, maximum):
    import msvcrt
    from ctypes import wintypes as w

    from dragon_radar.owner.windows_files import FileInformation

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        w.LPCWSTR,
        w.DWORD,
        w.DWORD,
        w.LPVOID,
        w.DWORD,
        w.DWORD,
        w.HANDLE,
    ]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.GetFileInformationByHandle.argtypes = [
        w.HANDLE,
        ctypes.POINTER(FileInformation),
    ]
    path = root.joinpath(*parts)
    with ExitStack() as stack:
        for name in (*reversed(path.parents), path):
            is_file = name == path
            handle = kernel.CreateFileW(
                "\\\\?\\" + str(name),
                0x20000 | (0x80000000 if is_file else 0),
                1 if is_file else 3,
                None,
                3,
                0x02200000,
                None,
            )
            if handle == w.HANDLE(-1).value:
                if ctypes.get_last_error() in (2, 3):
                    raise MissingEvidence()
                raise InvalidEvidence()
            stack.callback(kernel.CloseHandle, handle)
            info = FileInformation()
            if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
                raise InvalidEvidence()
            if info.attributes & 0x400 or bool(info.attributes & 0x10) == is_file:
                raise InvalidEvidence()
            if name == root or root in name.parents:
                _win_private(handle)
            if is_file:
                if info.links != 1 or (info.size_high << 32 | info.size_low) > maximum:
                    raise InvalidEvidence()
                # Duplicate into CRT ownership; keep the original pinned handle.
                kernel.DuplicateHandle.argtypes = [
                    w.HANDLE,
                    w.HANDLE,
                    w.HANDLE,
                    ctypes.POINTER(w.HANDLE),
                    w.DWORD,
                    w.BOOL,
                    w.DWORD,
                ]
                kernel.GetCurrentProcess.restype = w.HANDLE
                process = kernel.GetCurrentProcess()
                duplicate = w.HANDLE()
                if not kernel.DuplicateHandle(
                    process, handle, process, ctypes.byref(duplicate), 0, False, 2
                ):
                    raise InvalidEvidence()
                fd = msvcrt.open_osfhandle(duplicate.value, os.O_RDONLY | os.O_BINARY)
                with os.fdopen(fd, "rb") as stream:
                    data = stream.read(maximum + 1)
                if len(data) > maximum:
                    raise InvalidEvidence()
                return data
    raise InvalidEvidence()


def _read(root, relative, maximum):
    parts = _relative(relative)
    try:
        return (
            _read_windows(root, parts, maximum)
            if os.name == "nt"
            else _read_posix(root, parts, maximum)
        )
    except FileNotFoundError:
        raise MissingEvidence() from None
    except (OSError, ValueError, OverflowError):
        raise InvalidEvidence() from None


def read_asset(asset_root: Path, relative: str, maximum: int) -> bytes:
    """Read a private bounded regular asset, pinning non-reparse ancestors.

    This authenticates neither an index nor a policy. Trust configuration must
    still originate in an externally authorized channel. Hard maximum: 16 MiB.
    Missing/unsafe files use the same safe typed errors as verify_evidence.
    """
    if type(maximum) is not int or not 1 <= maximum <= 16 * 1024 * 1024:
        raise InvalidEvidence()
    return _read(_root(asset_root), relative, maximum)


def _rsa_signature_size(public_key):
    """Parse only DER rsaEncryption SubjectPublicKeyInfo, not arbitrary ASN.1."""
    try:
        lines = public_key.decode("ascii").splitlines()
        if (
            not lines
            or lines[0] != "-----BEGIN PUBLIC KEY-----"
            or lines[-1] != "-----END PUBLIC KEY-----"
        ):
            raise InvalidEvidence()
        der = base64.b64decode("".join(lines[1:-1]), validate=True)

        def element(data, tag):
            if len(data) < 2 or data[0] != tag:
                raise InvalidEvidence()
            length, offset = data[1], 2
            if length & 128:
                count = length & 127
                if not 1 <= count <= 2 or len(data) < offset + count:
                    raise InvalidEvidence()
                raw = data[offset : offset + count]
                length = int.from_bytes(raw, "big")
                offset += count
                if raw[0] == 0 or length < 128:
                    raise InvalidEvidence()
            end = offset + length
            if end > len(data):
                raise InvalidEvidence()
            return data[offset:end], data[end:]

        spki, trailing = element(der, 0x30)
        algorithm, rest = element(spki, 0x30)
        if trailing or algorithm != bytes.fromhex("06092a864886f70d0101010500"):
            raise InvalidEvidence()
        bit_string, trailing = element(rest, 0x03)
        if trailing or not bit_string or bit_string[0] != 0:
            raise InvalidEvidence()
        rsa, trailing = element(bit_string[1:], 0x30)
        modulus, rest = element(rsa, 0x02)
        exponent, extra = element(rest, 0x02)
        if (
            trailing
            or extra
            or not modulus
            or not exponent
            or modulus[0] != 0
            or len(modulus) < 2
            or modulus[1] < 128
            or len(exponent) > 4
            or exponent[0] == 0
            or exponent[0] & 128
        ):
            raise InvalidEvidence()
        bits = int.from_bytes(modulus, "big").bit_length()
        e = int.from_bytes(exponent, "big")
        if not 2048 <= bits <= 8192 or e < 3 or e % 2 != 1:
            raise InvalidEvidence()
        return (bits + 7) // 8
    except (UnicodeError, ValueError, binascii.Error):
        raise InvalidEvidence() from None


def _cryptography(data, signature, public_key, policy):
    if len(signature) != _rsa_signature_size(public_key):
        raise InvalidEvidence()
    executable = policy.openssl_executable
    if executable is None:
        raise VerifierUnavailable()
    try:
        executable = Path(executable)
        if not executable.is_absolute() or not executable.is_file():
            raise VerifierUnavailable()
        # The record is only piped to the fixed read-only verifier. These two
        # scratch files contain no private proof content or credentials.
        with tempfile.TemporaryDirectory(prefix="dragon-evidence-") as directory:
            key_path = Path(directory) / "public.pem"
            signature_path = Path(directory) / "signature.bin"
            for path, content in ((key_path, public_key), (signature_path, signature)):
                with secure_output(path) as stream:
                    stream.write(content)
            result = subprocess.run(
                [
                    str(executable),
                    "dgst",
                    "-sha256",
                    "-verify",
                    str(key_path),
                    "-signature",
                    str(signature_path),
                    "-sigopt",
                    "rsa_padding_mode:pss",
                    "-sigopt",
                    "rsa_pss_saltlen:32",
                    "-sigopt",
                    "rsa_mgf1_md:sha256",
                ],
                input=data,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=policy.verifier_timeout_seconds,
                check=False,
                shell=False,
            )
            if result.returncode != 0:
                raise InvalidEvidence()
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        raise VerifierUnavailable() from None


def verify_evidence(
    record_path: str,
    asset_root: Path,
    trust_policy: TrustPolicy,
    candidate: Mapping[str, str],
    context: Mapping[str, str],
    now: datetime,
) -> VerifiedEvidence:
    """Verify exact schema, authority, scope/time/version and assets, THEN signature.

    candidate must contain exactly code/dataset/rules/model/prompt/retrieval.
    context must contain exactly subject/purpose/target/audience. Evidence scopes
    are nonempty lists of exact strings, each within the issuer's external scope.
    now is explicitly supplied aware UTC; validity is [valid_from, expires_at).
    Payload is JSON, opaque here; semantic gates validate its specific schema.
    MissingEvidence means blocked; VerifierUnavailable means blocked;
    InvalidEvidence means fail. All exception diagnostics are fixed safe text.
    """
    try:
        _policy_limits(trust_policy)
        now = _utc(now)
        root = _root(asset_root)
        data = _read(root, record_path, trust_policy.max_record_bytes)
        value = strict_json(data)
        canonical = canonical_json(value)
        if data != canonical:
            raise InvalidEvidence()
        record, key = _record(value, trust_policy, candidate, context, now)
        materials, total = {}, 0
        for ref in record.materials:
            content = _read(root, ref.path, trust_policy.max_material_bytes)
            total += len(content)
            if total > trust_policy.max_total_material_bytes:
                raise InvalidEvidence()
            if hashlib.sha256(content).hexdigest() != ref.sha256:
                raise InvalidEvidence()
            materials[ref.material_id] = content
            materials[ref.path] = content
        public_key = _read(root, key.public_key_path, trust_policy.max_key_bytes)
        if hashlib.sha256(public_key).hexdigest() != key.sha256:
            raise InvalidEvidence()
        signature = _read(root, record.signature.path, trust_policy.max_signature_bytes)
        _cryptography(data, signature, public_key, trust_policy)
        return VerifiedEvidence(
            record, MappingProxyType(materials), hashlib.sha256(data).hexdigest()
        )
    except (
        TypeError,
        ValueError,
        KeyError,
        UnicodeError,
        RecursionError,
        OverflowError,
    ):
        raise InvalidEvidence() from None
