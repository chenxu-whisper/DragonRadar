"""Private archive and recovery tests, with explicit sessions and fake HTTPS."""

import json
import os
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from dragon_radar.domain.models import ExchangeCalendar, SourceGrant
from dragon_radar.ingestion import supplier_collection as collection
from dragon_radar.ingestion import supplier_transport as transport

NOW = datetime(2026, 11, 16, tzinfo=UTC)
ENDPOINT = "https://quantapi.51ifind.com/api/v1/cmd_history"
SESSIONS = (date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6))


def request(**changes):
    fields = dict(
        source_id="ifind",
        source_version="v1",
        profile_id="explicit-profile",
        profile_sha256="a" * 64,
        endpoint=ENDPOINT,
        securities=("EXPLICIT.A", "EXPLICIT.B", "EXPLICIT.C"),
        calendar=ExchangeCalendar(exchange="SSE", version="v1", sessions=SESSIONS),
        sessions=SESSIONS,
        max_codes=2,
        max_sessions=2,
    )
    return collection.CollectionRequest(**(fields | changes))


def grant():
    return SourceGrant(
        source_id="ifind",
        approved=True,
        reference="offline",
        starts_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=1),
        local_storage=True,
    )


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("IFIND_ACCESS_TOKEN", "PRIVATE_TEST_TOKEN")
    if os.name == "nt":
        helper = Path(__file__).parents[1] / "scripts/private-common.ps1"
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                ". $env:SUPPLIER_TEST_HELPER; "
                "Initialize-PrivateDirectory $env:SUPPLIER_TEST_ROOT | Out-Null",
            ],
            env=dict(
                os.environ,
                SUPPLIER_TEST_HELPER=str(helper),
                SUPPLIER_TEST_ROOT=str(tmp_path),
            ),
            check=True,
            capture_output=True,
            timeout=20,
        )
    else:
        tmp_path.chmod(0o700)
    return tmp_path


def client(send):
    return transport.IfindTransport(ENDPOINT, send=send)


def ok(*_):
    return transport.HttpResponse(200, b'{"errorcode":0,"tables":[]}')


def test_collection_exists():
    assert hasattr(collection, "CollectionRequest")


def test_partition_archive_resume_actual_receipt(root):
    calls = []

    def send(req, *_):
        calls.append(json.loads(req.data))
        return ok()

    result = collection.collect(
        request(),
        grant=grant(),
        archive_root=root,
        transport=client(send),
        clock=lambda: NOW,
        sleep=lambda _: None,
    )
    assert len(result.chunks) == 4
    assert calls[0] == {
        "codes": ["EXPLICIT.A", "EXPLICIT.B"],
        "dates": ["2020-01-02", "2020-01-03"],
    }
    assert all(chunk.first_received_at == NOW for chunk in result.chunks)
    assert all(chunk.provenance == "retrospective" for chunk in result.chunks)
    assert len(list(root.glob("*.raw.json"))) == 4
    assert len(list(root.glob("*.done.json"))) == 4
    again = collection.collect(
        request(),
        grant=grant(),
        archive_root=root,
        transport=client(send),
        clock=lambda: NOW,
        sleep=lambda _: None,
    )
    assert again == result
    assert len(calls) == 4
    assert "PRIVATE_TEST_TOKEN" not in "".join(p.read_text() for p in root.iterdir())
    if os.name != "nt":
        assert all(p.stat().st_mode & 0o077 == 0 for p in root.iterdir())


@pytest.mark.parametrize(
    "change",
    [
        {"sessions": (SESSIONS[1], SESSIONS[0])},
        {"sessions": (date(2020, 1, 4),)},
        {"securities": ("x", "x")},
        {"max_codes": True},
        {"payload_json": '{"access_token":"secret"}'},
        {"payload_json": '{"nested":{"password":"secret"}}'},
    ],
)
def test_strict_request(change):
    with pytest.raises((ValidationError, transport.SupplierError)):
        request(**change)


def test_request_is_immutable():
    req = request()
    with pytest.raises(ValidationError):
        req.max_codes = 3
    assert "access_token" not in req.model_dump_json()


@pytest.mark.parametrize("failure", [429, 503, "timeout"])
def test_retry_bounded(root, failure):
    calls, sleeps = [], []

    def send(*_):
        calls.append(1)
        if failure == "timeout":
            raise TimeoutError("PRIVATE_TEST_TOKEN")
        return transport.HttpResponse(failure, b"PRIVATE_TEST_TOKEN")

    with pytest.raises(transport.SupplierError):
        collection.collect(
            request(),
            grant=grant(),
            archive_root=root,
            transport=client(send),
            clock=lambda: NOW,
            sleep=sleeps.append,
            max_attempts=3,
        )
    assert len(calls) == 3
    assert len(sleeps) == 2
    assert not list(root.iterdir())


def test_retry_then_success(root):
    calls = []

    def send(*_):
        calls.append(1)
        return transport.HttpResponse(503, b"secret") if len(calls) == 1 else ok()

    result = collection.collect(
        request(),
        grant=grant(),
        archive_root=root,
        transport=client(send),
        clock=lambda: NOW,
        sleep=lambda _: None,
    )
    assert len(result.chunks) == 4
    assert len(calls) == 5


@pytest.mark.parametrize("status,body", [(401, b"secret"), (200, b"bad")])
def test_no_retry_on_auth_or_data(root, status, body):
    calls = []

    def send(*_):
        calls.append(1)
        return transport.HttpResponse(status, body)

    with pytest.raises(transport.SupplierError):
        collection.collect(
            request(),
            grant=grant(),
            archive_root=root,
            transport=client(send),
            clock=lambda: NOW,
            sleep=lambda _: None,
        )
    assert len(calls) == 1


def test_expiry_at_receipt_prevents_persistence(root):
    current = [NOW]

    def send(*_):
        current[0] = grant().expires_at
        return ok()

    with pytest.raises(transport.SupplierError, match="storage_denied"):
        collection.collect(
            request(),
            grant=grant(),
            archive_root=root,
            transport=client(send),
            clock=lambda: current[0],
            sleep=lambda _: None,
        )
    assert not list(root.iterdir())


def test_expiry_before_marker_leaves_recoverable_orphan(root, monkeypatch):
    current = [NOW]
    original = collection._write_once

    def write(*args, **kwargs):
        original(*args, **kwargs)
        current[0] = grant().expires_at

    monkeypatch.setattr(collection, "_write_once", write)
    with pytest.raises(transport.SupplierError, match="storage_denied"):
        collection.collect(
            request(),
            grant=grant(),
            archive_root=root,
            transport=client(ok),
            clock=lambda: current[0],
            sleep=lambda _: None,
        )
    assert len(list(root.glob("*.raw.json"))) == 1
    assert not list(root.glob("*.done.json"))
    monkeypatch.setattr(collection, "_write_once", original)
    calls = []

    def send(*_):
        calls.append(1)
        return ok()

    collection.collect(
        request(),
        grant=grant(),
        archive_root=root,
        transport=client(send),
        clock=lambda: NOW,
        sleep=lambda _: None,
    )
    assert len(calls) == 3


@pytest.mark.parametrize("kind", ["raw", "done"])
def test_tamper_fails_closed(root, kind):
    collection.collect(
        request(),
        grant=grant(),
        archive_root=root,
        transport=client(ok),
        clock=lambda: NOW,
        sleep=lambda _: None,
    )
    path = next(root.glob(f"*.{kind}.json"))
    path.write_bytes(b'{"tampered":"PRIVATE_TEST_TOKEN"}')
    with pytest.raises(transport.SupplierError, match="checkpoint_mismatch"):
        collection.collect(
            request(),
            grant=grant(),
            archive_root=root,
            transport=client(ok),
            clock=lambda: NOW,
            sleep=lambda _: None,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"profile_id": "changed"},
        {"endpoint": "https://quantapi.10jqka.com.cn/api/v1/cmd_history"},
        {"payload_json": '{"indicator":"changed"}'},
    ],
)
def test_resume_identity_mismatch(root, changes):
    collection.collect(
        request(),
        grant=grant(),
        archive_root=root,
        transport=client(ok),
        clock=lambda: NOW,
        sleep=lambda _: None,
    )
    with pytest.raises(transport.SupplierError, match="checkpoint_mismatch"):
        collection.collect(
            request(**changes),
            grant=grant(),
            archive_root=root,
            transport=transport.IfindTransport(request(**changes).endpoint, send=ok),
            clock=lambda: NOW,
            sleep=lambda _: None,
        )


def test_orphan_receipt_time_tamper_is_detected(root):
    collection.collect(
        request(),
        grant=grant(),
        archive_root=root,
        transport=client(ok),
        clock=lambda: NOW,
        sleep=lambda _: None,
    )
    next(root.glob("*.done.json")).unlink()
    raw_path = next(root.glob("*.raw.json"))
    value = json.loads(raw_path.read_bytes())
    value["first_received_at"] = (NOW - timedelta(hours=1)).isoformat()
    raw_path.write_bytes(collection.canonical_json(value))
    with pytest.raises(transport.SupplierError, match="checkpoint_mismatch"):
        collection.collect(
            request(),
            grant=grant(),
            archive_root=root,
            transport=client(ok),
            clock=lambda: NOW,
            sleep=lambda _: None,
        )


def test_expiry_during_write_prevents_publication(root, monkeypatch):
    current = [NOW]
    original = collection.secure_output

    from contextlib import contextmanager

    @contextmanager
    def delayed(path):
        with original(path) as stream:
            yield stream
        current[0] = grant().expires_at

    monkeypatch.setattr(collection, "secure_output", delayed)
    with pytest.raises(transport.SupplierError, match="storage_denied"):
        collection.collect(
            request(),
            grant=grant(),
            archive_root=root,
            transport=client(ok),
            clock=lambda: current[0],
            sleep=lambda _: None,
        )
    assert not list(root.iterdir())


def test_expiry_before_retry_never_sends_again(root):
    current, calls = [NOW], []

    def send(*_):
        calls.append(1)
        return transport.HttpResponse(429, b"")

    def sleep(_):
        current[0] = grant().expires_at

    with pytest.raises(transport.SupplierError, match="storage_denied"):
        collection.collect(
            request(),
            grant=grant(),
            archive_root=root,
            transport=client(send),
            clock=lambda: current[0],
            sleep=sleep,
        )
    assert len(calls) == 1


def test_write_collision_is_idempotent_not_overwrite(root):
    collection._write_once(root, "collision.json", b"first")
    collection._write_once(root, "collision.json", b"first")
    with pytest.raises(transport.SupplierError, match="checkpoint_mismatch"):
        collection._write_once(root, "collision.json", b"second")
    assert (root / "collision.json").read_bytes() == b"first"


def test_source_mismatch_never_sends(root):
    calls = []
    with pytest.raises(transport.SupplierError, match="storage_denied"):
        collection.collect(
            request(source_id="other"),
            grant=grant(),
            archive_root=root,
            transport=client(lambda *_: calls.append(1)),
            clock=lambda: NOW,
        )
    assert not calls


@pytest.mark.parametrize("unsafe", ["missing", "public"])
def test_directory_failure_is_before_any_send(root, unsafe):
    if unsafe == "missing":
        directory = root / "not-created"
    else:
        directory = root
        if os.name == "nt":
            subprocess.run(
                ["icacls.exe", str(directory), "/grant", "*S-1-1-0:(R)"],
                check=True,
                capture_output=True,
                timeout=20,
            )
        else:
            directory.chmod(0o755)
    calls = []
    with pytest.raises(transport.SupplierError):
        collection.collect(
            request(),
            grant=grant(),
            archive_root=directory,
            transport=client(lambda *_: calls.append(1)),
            clock=lambda: NOW,
            sleep=lambda _: None,
        )
    assert not calls


@pytest.mark.parametrize("kind,index", [("done", 4), ("done", 99), ("raw", 99)])
def test_extra_sparse_assets_fail_before_any_send(root, kind, index):
    name = (
        f"chunk-{index:05d}.done.json"
        if kind == "done"
        else f"chunk-{index:05d}-{'a' * 64}.raw.json"
    )
    collection._write_once(root, name, b"{}")
    calls = []

    def send(*_):
        calls.append(1)
        return ok()

    with pytest.raises(transport.SupplierError, match="checkpoint_mismatch"):
        collection.collect(
            request(),
            grant=grant(),
            archive_root=root,
            transport=client(send),
            clock=lambda: NOW,
            sleep=lambda _: None,
        )
    assert not calls


def test_later_chunk_identity_conflict_fails_before_any_send(root):
    collection.collect(
        request(),
        grant=grant(),
        archive_root=root,
        transport=client(ok),
        clock=lambda: NOW,
        sleep=lambda _: None,
    )
    for path in root.glob("chunk-0000[012]*"):
        path.unlink()
    calls = []

    def send(*_):
        calls.append(1)
        return ok()

    with pytest.raises(transport.SupplierError, match="checkpoint_mismatch"):
        collection.collect(
            request(profile_id="different"),
            grant=grant(),
            archive_root=root,
            transport=client(send),
            clock=lambda: NOW,
            sleep=lambda _: None,
        )
    assert not calls


def test_active_credential_cannot_enter_archive_identity(root):
    req = request(profile_id="PRIVATE_TEST_TOKEN")
    calls = []
    assert "PRIVATE_TEST_TOKEN" not in repr(req)
    with pytest.raises(transport.SupplierError, match="invalid_request"):
        collection.collect(
            req,
            grant=grant(),
            archive_root=root,
            transport=client(lambda *_: calls.append(1)),
            clock=lambda: NOW,
            sleep=lambda _: None,
        )
    assert not calls
    assert not list(root.iterdir())
