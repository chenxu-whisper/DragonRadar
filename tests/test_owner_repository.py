"""Owner contracts against disposable PostgreSQL schemas."""

import hashlib
import secrets
import subprocess
import sys
from base64 import urlsafe_b64decode
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from importlib.util import find_spec
from queue import Queue
from threading import Barrier
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, insert, inspect, select, text
from sqlalchemy.exc import DBAPIError

NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)
ORIGIN = "https://localhost:8030"


def owner_repository(database, clock=lambda: NOW):
    assert find_spec("dragon_radar.owner") is not None, "owner module is missing"
    from dragon_radar.owner import OwnerRepository

    return OwnerRepository(database, clock=clock)


def test_initialize_is_stable_after_restart(database, database_url):
    from dragon_radar.storage.database import Database

    repo = owner_repository(database)
    owner_id = repo.initialize()
    assert isinstance(owner_id, UUID)
    assert repo.initialize() == owner_id
    restarted = Database(database_url)
    try:
        assert owner_repository(restarted).initialize() == owner_id
    finally:
        restarted.close()


def test_concurrent_initialize_has_one_local_principal(database):
    barrier = Barrier(6)

    def initialize(_):
        repo = owner_repository(database)
        barrier.wait(timeout=10)
        return repo.initialize()

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert len(set(pool.map(initialize, range(6)))) == 1
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM owner_principals")) == 1


def test_owner_migration_uses_shared_metadata_and_no_private_market_audit(database):
    repo = owner_repository(database)
    from dragon_radar.owner import models
    from dragon_radar.storage.database import SCHEMA_REVISION
    from dragon_radar.storage.models import metadata

    repo.initialize()
    names = {
        "owner_principals",
        "owner_pairings",
        "owner_sessions",
        "owner_pairing_limits",
    }
    assert names <= set(inspect(database.engine).get_table_names())
    assert all(getattr(models, name).metadata is metadata for name in names)
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            SCHEMA_REVISION
        )
        assert connection.scalar(text("SELECT count(*) FROM audit_events")) == 0
    database.migrate()
    assert database.ready()


def test_readiness_checks_owner_tables(database):
    with database.engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS owner_sessions"))
    assert not database.ready()


@pytest.fixture
def owners(database):
    repo = owner_repository(database)
    repo.initialize()
    return repo


def issue(repo, origin=ORIGIN):
    return repo.exchange_pairing(repo.issue_pairing(origin), origin)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def test_pairing_single_use_and_detached_identity(owners):
    from dragon_radar.owner import InvalidPairing

    code = owners.issue_pairing(ORIGIN)
    issued = owners.exchange_pairing(code, ORIGIN)
    identity = owners.authenticate(issued.token)
    assert identity.owner_id == issued.owner_id == owners.initialize()
    assert identity.session_id == issued.session_id
    assert identity.expires_at == issued.expires_at == NOW + timedelta(hours=8)
    assert identity.origin == issued.origin == ORIGIN
    assert not hasattr(identity, "token")
    assert not hasattr(identity, "csrf")
    assert issued.token not in repr(issued)
    assert issued.csrf not in repr(issued)
    with pytest.raises(FrozenInstanceError):
        identity.owner_id = uuid4()
    with pytest.raises(InvalidPairing):
        owners.exchange_pairing(code, ORIGIN)


def test_secrets_are_random_and_only_sha256_is_persisted(owners, database):
    code = owners.issue_pairing(ORIGIN)
    issued = owners.exchange_pairing(code, ORIGIN)
    for value in (code, issued.token, issued.csrf):
        assert len(urlsafe_b64decode(value + "=" * (-len(value) % 4))) >= 32
    assert len({code, issued.token, issued.csrf}) == 3
    from dragon_radar.owner.models import owner_pairings, owner_sessions

    with database.engine.connect() as connection:
        pairing = connection.execute(select(owner_pairings)).mappings().one()
        session = connection.execute(select(owner_sessions)).mappings().one()
        assert pairing["code_hash"] == digest(code)
        assert pairing["expires_at"] == NOW + timedelta(minutes=5)
        assert pairing["consumed_at"] == NOW
        assert session["token_hash"] == digest(issued.token)
        assert session["csrf_hash"] == digest(issued.csrf)
        assert pairing["origin"] == session["origin"] == ORIGIN
        stored = repr((dict(pairing), dict(session)))
        assert all(value not in stored for value in (code, issued.token, issued.csrf))
        assert connection.scalar(text("SELECT count(*) FROM audit_events")) == 0
    other = issue(owners)
    assert other.token != issued.token and other.csrf != issued.csrf


@pytest.mark.parametrize("seconds", [300, 301])
def test_pairing_expiry_boundary(owners, seconds):
    from dragon_radar.owner import InvalidPairing

    code = owners.issue_pairing(ORIGIN)
    owners.clock = lambda: NOW + timedelta(seconds=seconds)
    with pytest.raises(InvalidPairing):
        owners.exchange_pairing(code, ORIGIN)


def test_pairing_is_valid_just_before_expiry(owners):
    code = owners.issue_pairing(ORIGIN)
    owners.clock = lambda: NOW + timedelta(minutes=5, microseconds=-1)
    assert owners.exchange_pairing(code, ORIGIN).owner_id == owners.initialize()


def test_new_pairing_revokes_pending_but_not_existing_session(owners):
    from dragon_radar.owner import InvalidPairing

    session = issue(owners)
    old = owners.issue_pairing(ORIGIN)
    new = owners.issue_pairing(ORIGIN)
    with pytest.raises(InvalidPairing):
        owners.exchange_pairing(old, ORIGIN)
    owners.exchange_pairing(new, ORIGIN)
    owners.authenticate(session.token)


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:8030",
        "https://localhost",
        "https://127.0.0.1:8030",
        "https://[::1]:8030",
        "https://LOCALHOST:8030",
        "HTTPS://localhost:8030",
        "https://localhost.:8030",
        "https://localhost:08030",
        "https://localhost:0",
        "https://localhost:65536",
        "https://localhost:8030/",
        "https://localhost:8030?a=1",
        "https://localhost:8030#fragment",
        "https://user@localhost:8030",
        "https://localhost:8030\n",
        " https://localhost:8030",
        "null",
        "",
        None,
    ],
)
def test_repository_rejects_noncanonical_origins(owners, origin):
    from dragon_radar.owner import InvalidPairing

    with pytest.raises(ValueError, match="Invalid owner origin"):
        owners.issue_pairing(origin)
    code = owners.issue_pairing(ORIGIN)
    with pytest.raises(InvalidPairing):
        owners.exchange_pairing(code, origin)
    owners.exchange_pairing(code, ORIGIN)


def test_exact_origin_is_bound_and_alternate_explicit_port_supported(owners):
    from dragon_radar.owner import InvalidPairing

    alternate = "https://localhost:8443"
    code = owners.issue_pairing(alternate)
    with pytest.raises(InvalidPairing):
        owners.exchange_pairing(code, ORIGIN)
    issued = owners.exchange_pairing(code, alternate)
    assert owners.authenticate(issued.token).origin == alternate


def test_concurrent_pairing_only_one_session(owners, database):
    from dragon_radar.owner import InvalidPairing

    code = owners.issue_pairing(ORIGIN)
    barrier = Barrier(6)

    def exchange(_):
        barrier.wait(timeout=10)
        try:
            return owners.exchange_pairing(code, ORIGIN)
        except InvalidPairing:
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(result is not None for result in pool.map(exchange, range(6))) == 1
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM owner_sessions")) == 1
        assert connection.scalar(text("SELECT failures FROM owner_pairing_limits")) == 5


def test_session_creation_failure_rolls_back_code_consumption(owners, database):
    code = owners.issue_pairing(ORIGIN)
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE owner_sessions ADD CONSTRAINT reject_insert CHECK (false)"
            )
        )
    with pytest.raises(DBAPIError):
        owners.exchange_pairing(code, ORIGIN)
    with database.engine.begin() as connection:
        assert connection.scalar(text("SELECT consumed_at FROM owner_pairings")) is None
        connection.execute(
            text("ALTER TABLE owner_sessions DROP CONSTRAINT reject_insert")
        )
    owners.exchange_pairing(code, ORIGIN)


@pytest.mark.parametrize("seconds", [28800, 28801])
def test_session_expiry_is_absolute(owners, seconds):
    from dragon_radar.owner import SessionUnauthorized

    issued = issue(owners)
    owners.clock = lambda: NOW + timedelta(hours=7)
    assert owners.authenticate(issued.token).expires_at == issued.expires_at
    owners.clock = lambda: NOW + timedelta(seconds=seconds)
    with pytest.raises(SessionUnauthorized):
        owners.authenticate(issued.token)


def test_logout_revokes_only_one_session_and_is_idempotent(owners):
    from dragon_radar.owner import SessionUnauthorized

    first, second = issue(owners), issue(owners)
    owners.logout(first.token)
    owners.logout(first.token)
    owners.logout("unknown")
    with pytest.raises(SessionUnauthorized):
        owners.authenticate(first.token)
    owners.authenticate(second.token)


def test_revoke_all_includes_pending_pairings_and_all_local_sessions(owners):
    from dragon_radar.owner import InvalidPairing, SessionUnauthorized

    first, second = issue(owners), issue(owners)
    pending = owners.issue_pairing(ORIGIN)
    owners.revoke_all()
    owners.revoke_all()
    for session in (first, second):
        with pytest.raises(SessionUnauthorized):
            owners.authenticate(session.token)
    with pytest.raises(InvalidPairing):
        owners.exchange_pairing(pending, ORIGIN)
    assert issue(owners).owner_id == first.owner_id


def test_failures_persist_across_codes_success_and_business_rollback(owners, database):
    from dragon_radar.owner import InvalidPairing, PairingRateLimited

    used = owners.issue_pairing(ORIGIN)
    owners.exchange_pairing(used, ORIGIN)
    expired = owners.issue_pairing(ORIGIN)
    owners.clock = lambda: NOW + timedelta(minutes=5)
    attempts = ["wrong", used, expired]
    for code in attempts:
        with pytest.raises(InvalidPairing):
            owners.exchange_pairing(code, ORIGIN)
    issue(owners)  # Success and issuing new codes must not reset the window.
    for _ in range(2):
        with pytest.raises(InvalidPairing), database.engine.begin() as connection:
            connection.execute(text("SELECT 1"))
            owners.exchange_pairing("wrong", ORIGIN)
    code = owners.issue_pairing(ORIGIN)
    with pytest.raises(PairingRateLimited):
        owners.exchange_pairing(code, ORIGIN)
    owners.clock = lambda: NOW + timedelta(minutes=6, microseconds=-1)
    with pytest.raises(PairingRateLimited):
        owners.exchange_pairing(code, ORIGIN)
    owners.clock = lambda: NOW + timedelta(minutes=6)
    owners.exchange_pairing(code, ORIGIN)


def test_pair_limit_survives_another_process(owners, database_url):
    from dragon_radar.owner import InvalidPairing, PairingRateLimited

    for _ in range(4):
        with pytest.raises(InvalidPairing):
            owners.exchange_pairing("wrong", ORIGIN)
    script = """
import sys
from datetime import datetime
from dragon_radar.owner import OwnerRepository, InvalidPairing
from dragon_radar.storage.database import Database
db = Database(sys.stdin.readline().strip())
try:
    now = datetime.fromisoformat(sys.stdin.readline().strip())
    repo = OwnerRepository(db, clock=lambda: now)
    try:
        repo.exchange_pairing('wrong', 'https://localhost:8030')
    except InvalidPairing:
        pass
    else:
        raise AssertionError('must fail')
finally:
    db.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=database_url + "\n" + NOW.isoformat() + "\n",
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    with pytest.raises(PairingRateLimited):
        owners.exchange_pairing("wrong", ORIGIN)


def test_concurrent_failures_are_installation_wide(owners, database):
    from dragon_radar.owner import InvalidPairing, PairingRateLimited

    barrier = Barrier(10)

    def fail(_):
        barrier.wait(timeout=10)
        try:
            owners.exchange_pairing("wrong", ORIGIN)
        except (InvalidPairing, PairingRateLimited) as error:
            return type(error)

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(fail, range(10)))
    assert results.count(InvalidPairing) == 5
    assert results.count(PairingRateLimited) == 5
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT failures FROM owner_pairing_limits")) == 5


def test_error_contracts_are_generic(owners):
    from dragon_radar.owner import (
        CsrfForbidden,
        InvalidPairing,
        PairingRateLimited,
        SessionUnauthorized,
    )

    for error, status, code in (
        (InvalidPairing, 401, "invalid_pairing"),
        (SessionUnauthorized, 401, "session_unauthorized"),
        (CsrfForbidden, 403, "csrf_forbidden"),
        (PairingRateLimited, 429, "pairing_rate_limited"),
    ):
        assert error.status_code == status
        assert error.code == code
        assert str(error()) == error.message
    secret = "must-not-appear"
    with pytest.raises(InvalidPairing) as caught:
        owners.exchange_pairing(secret, ORIGIN)
    assert secret not in str(caught.value)
    with pytest.raises(SessionUnauthorized) as caught:
        owners.authenticate(secret)
    assert secret not in str(caught.value)


def test_csrf_is_session_bound_and_compared_in_constant_time(
    owners, database, monkeypatch
):
    import hmac

    from dragon_radar.owner import CsrfForbidden

    first, second = issue(owners), issue(owners)
    original = hmac.compare_digest
    calls = []

    def compare(a, b):
        calls.append((a, b))
        return original(a, b)

    monkeypatch.setattr(hmac, "compare_digest", compare)
    with database.engine.begin() as connection:
        assert owners.authorize_connection(connection, first.token, first.csrf).owner_id
    assert calls == [(digest(first.csrf), digest(first.csrf))]
    for csrf in ("", second.csrf, "invalid", "\ud800"):
        with pytest.raises(CsrfForbidden), database.engine.begin() as connection:
            owners.authorize_connection(connection, first.token, csrf)
    with database.engine.begin() as connection:
        owners.authorize_connection(connection, first.token)


def test_reauthorize_in_caller_transaction_rejects_stale_identity(owners, database):
    from dragon_radar.owner import SessionUnauthorized

    issued = issue(owners)
    owners.authenticate(issued.token)
    owners.revoke_all()
    with pytest.raises(SessionUnauthorized), database.engine.begin() as connection:
        owners.authorize_connection(connection, issued.token, issued.csrf)


def test_caller_rollback_remains_under_callers_control(owners, database):
    issued = issue(owners)
    extra_id = uuid4()
    from dragon_radar.owner.models import owner_principals

    with pytest.raises(RuntimeError), database.engine.begin() as connection:
        owners.authorize_connection(connection, issued.token, issued.csrf)
        connection.execute(insert(owner_principals).values(id=extra_id, created_at=NOW))
        raise RuntimeError("business failure")
    with database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(owner_principals.c.id).where(owner_principals.c.id == extra_id)
            )
            is None
        )
    owners.authenticate(issued.token)


def test_second_principal_fixture_is_not_local_selector(owners, database):
    from dragon_radar.owner.models import (
        owner_pairings,
        owner_principals,
        owner_sessions,
    )

    second_id = uuid4()
    token, csrf, code = (secrets.token_urlsafe(32) for _ in range(3))
    with database.engine.begin() as connection:
        connection.execute(
            insert(owner_principals).values(id=second_id, created_at=NOW)
        )
        connection.execute(
            insert(owner_sessions).values(
                id=uuid4(),
                owner_id=second_id,
                token_hash=digest(token),
                csrf_hash=digest(csrf),
                origin=ORIGIN,
                created_at=NOW,
                expires_at=NOW + timedelta(hours=8),
            )
        )
        connection.execute(
            insert(owner_pairings).values(
                id=uuid4(),
                owner_id=second_id,
                code_hash=digest(code),
                origin=ORIGIN,
                created_at=NOW,
                expires_at=NOW + timedelta(minutes=5),
            )
        )
    local_code = owners.issue_pairing(ORIGIN)
    assert owners.exchange_pairing(local_code, ORIGIN).owner_id == owners.initialize()
    owners.revoke_all()
    assert owners.authenticate(token).owner_id == second_id
    assert owners.exchange_pairing(code, ORIGIN).owner_id == second_id
    with database.engine.begin() as connection:
        assert (
            owners.authorize_connection(connection, token, csrf).owner_id == second_id
        )


def test_aware_clock_normalized_to_utc_and_naive_clock_rejected(database):
    offset = timezone(timedelta(hours=8))
    repo = owner_repository(database, clock=lambda: NOW.astimezone(offset))
    repo.initialize()
    assert callable(getattr(repo, "issue_pairing", None)), "pairing is missing"
    assert issue(repo).expires_at.tzinfo is UTC
    repo.clock = lambda: NOW.replace(tzinfo=None)
    with pytest.raises(ValueError, match="aware"):
        repo.issue_pairing(ORIGIN)


@pytest.mark.parametrize("token", [None, "", "\ud800", "unknown"])
def test_malformed_credentials_are_generic_unauthorized(owners, token):
    from dragon_radar.owner import InvalidPairing, SessionUnauthorized

    with pytest.raises(SessionUnauthorized):
        owners.authenticate(token)
    with pytest.raises(InvalidPairing):
        owners.exchange_pairing(token, ORIGIN)


@pytest.mark.parametrize("isolation", ["AUTOCOMMIT", "REPEATABLE READ", "SERIALIZABLE"])
def test_authorization_rejects_unsafe_transaction_modes(owners, database, isolation):
    issued = issue(owners)
    with database.engine.connect().execution_options(
        isolation_level=isolation
    ) as connection:
        with connection.begin(), pytest.raises(ValueError, match="READ COMMITTED"):
            owners.authorize_connection(connection, issued.token, issued.csrf)


def test_authorization_requires_caller_transaction(owners, database):
    issued = issue(owners)
    with database.engine.connect() as connection:
        with pytest.raises(ValueError, match="transaction"):
            owners.authorize_connection(connection, issued.token, issued.csrf)


def wait_until_blocked(database, pid):
    deadline = monotonic() + 5
    with database.engine.connect() as connection:
        while monotonic() < deadline:
            if connection.scalar(
                text("SELECT cardinality(pg_blocking_pids(:pid))"), {"pid": pid}
            ):
                return
            sleep(0.01)
    pytest.fail("worker did not wait on the principal lock")


@pytest.mark.parametrize("operation", ["revoke_all", "logout"])
def test_private_transaction_holds_principal_lock_until_commit(
    owners, database, operation
):
    from dragon_radar.owner import SessionUnauthorized

    issued = issue(owners)
    pids = Queue()

    def observe(connection, cursor, statement, parameters, context, executemany):
        if "owner_principals" in statement and "FOR UPDATE" in statement:
            pids.put(connection.connection.driver_connection.info.backend_pid)

    def revoke():
        if operation == "logout":
            owners.logout(issued.token)
        else:
            owners.revoke_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.engine.begin() as connection:
            owners.authorize_connection(connection, issued.token, issued.csrf)
            event.listen(database.engine, "before_cursor_execute", observe)
            try:
                future = pool.submit(revoke)
                wait_until_blocked(database, pids.get(timeout=5))
                assert not future.done()
                # A private write may commit before the waiting revocation.
                connection.execute(text("SELECT 1"))
            finally:
                event.remove(database.engine, "before_cursor_execute", observe)
        future.result(timeout=5)
    with pytest.raises(SessionUnauthorized):
        owners.authenticate(issued.token)


@pytest.mark.parametrize("change", ["expire", "revoke"])
def test_waiting_authorization_rechecks_after_principal_lock(owners, database, change):
    from dragon_radar.owner import SessionUnauthorized
    from dragon_radar.owner.models import owner_principals, owner_sessions

    issued = issue(owners)
    pids = Queue()

    def authorize():
        with database.engine.begin() as connection:
            pids.put(connection.connection.driver_connection.info.backend_pid)
            owners.authorize_connection(connection, issued.token, issued.csrf)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.engine.begin() as connection:
            connection.execute(
                select(owner_principals)
                .where(owner_principals.c.id == issued.owner_id)
                .with_for_update()
            )
            future = pool.submit(authorize)
            wait_until_blocked(database, pids.get(timeout=5))
            if change == "expire":
                owners.clock = lambda: NOW + timedelta(hours=8)
            else:
                connection.execute(
                    owner_sessions.update()
                    .where(owner_sessions.c.id == issued.session_id)
                    .values(revoked_at=NOW)
                )
        with pytest.raises(SessionUnauthorized):
            future.result(timeout=5)


@pytest.mark.parametrize("name", ["owner_pairings", "owner_sessions"])
def test_migration_indexes_match_shared_metadata(database, name):
    from dragon_radar.owner import models

    expected = {index.name for index in getattr(models, name).indexes}
    actual = {
        index["name"]
        for index in inspect(database.engine).get_indexes(name)
        if not index.get("duplicates_constraint")
    }
    assert actual == expected
