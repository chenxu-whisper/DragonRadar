"""Local owner initialization and transactional credential operations."""

import hashlib
import hmac
import re
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from dragon_radar.owner.errors import (
    CsrfForbidden,
    InvalidPairing,
    PairingRateLimited,
    SessionUnauthorized,
)
from dragon_radar.owner.models import (
    IssuedSession,
    OwnerIdentity,
    owner_pairing_limits,
    owner_pairings,
    owner_principals,
    owner_sessions,
)
from dragon_radar.storage.database import Database

DEFAULT_ORIGIN = "https://localhost:8030"
PAIRING_LIFETIME = timedelta(minutes=5)
SESSION_LIFETIME = timedelta(hours=8)
PAIRING_WINDOW = timedelta(seconds=60)


def _valid_origin(origin: str) -> bool:
    if not isinstance(origin, str):
        return False
    match = re.fullmatch(r"https://localhost:([1-9][0-9]{0,4})", origin)
    return match is not None and int(match[1]) <= 65535


def _hash(value: str) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 1024:
        return None
    try:
        return hashlib.sha256(value.encode("ascii")).hexdigest()
    except UnicodeEncodeError:
        return None


class OwnerRepository:
    def __init__(self, database: Database, clock: Callable[[], datetime] | None = None):
        self.database = database
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Owner clock must return an aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _local_owner(connection: Connection) -> UUID | None:
        return connection.scalar(
            select(owner_principals.c.id).where(owner_principals.c.local_key == "local")
        )

    @staticmethod
    def _lock_principal(connection: Connection, owner_id: UUID) -> None:
        # All revocations and private writes lock the principal first, then
        # command/idempotency keys, then resources. Keep this lock until commit.
        if (
            connection.scalar(
                select(owner_principals.c.id)
                .where(owner_principals.c.id == owner_id)
                .with_for_update()
            )
            is None
        ):
            raise SessionUnauthorized()

    def initialize(self) -> UUID:
        with self.database.engine.begin() as connection:
            connection.execute(
                pg_insert(owner_principals)
                .values(id=uuid4(), local_key="local", created_at=self._now())
                .on_conflict_do_nothing(index_elements=[owner_principals.c.local_key])
            )
            return connection.execute(
                select(owner_principals.c.id).where(
                    owner_principals.c.local_key == "local"
                )
            ).scalar_one()

    def issue_pairing(self, origin: str = DEFAULT_ORIGIN) -> str:
        if not _valid_origin(origin):
            raise ValueError("Invalid owner origin")
        with self.database.engine.begin() as connection:
            owner_id = self._local_owner(connection)
            if owner_id is None:
                raise SessionUnauthorized()
            self._lock_principal(connection, owner_id)
            now = self._now()
            connection.execute(
                update(owner_pairings)
                .where(
                    owner_pairings.c.owner_id == owner_id,
                    owner_pairings.c.consumed_at.is_(None),
                    owner_pairings.c.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
            code = secrets.token_urlsafe(32)
            connection.execute(
                insert(owner_pairings).values(
                    id=uuid4(),
                    owner_id=owner_id,
                    code_hash=_hash(code),
                    origin=origin,
                    created_at=now,
                    expires_at=now + PAIRING_LIFETIME,
                )
            )
        return code

    def exchange_pairing(self, code: str, origin: str) -> IssuedSession:
        code_hash = _hash(code)
        error = None
        with self.database.engine.begin() as connection:
            owner_id = connection.scalar(
                select(owner_pairings.c.owner_id).where(
                    owner_pairings.c.code_hash == code_hash
                )
            )
            owner_id = owner_id or self._local_owner(connection)
            if owner_id is not None:
                self._lock_principal(connection, owner_id)
            limit = (
                connection.execute(
                    select(owner_pairing_limits)
                    .where(owner_pairing_limits.c.id == 1)
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            now = self._now()
            window = limit["window_started_at"]
            failures = limit["failures"]
            if window is None or now >= window + PAIRING_WINDOW:
                window, failures = now, 0
            if failures >= 5:
                error = PairingRateLimited()
            else:
                pairing = (
                    connection.execute(
                        select(owner_pairings).where(
                            owner_pairings.c.code_hash == code_hash,
                            owner_pairings.c.owner_id == owner_id,
                        )
                    )
                    .mappings()
                    .first()
                )
                if (
                    pairing is None
                    or not _valid_origin(origin)
                    or pairing["origin"] != origin
                    or pairing["consumed_at"] is not None
                    or pairing["revoked_at"] is not None
                    or now >= pairing["expires_at"]
                ):
                    connection.execute(
                        update(owner_pairing_limits)
                        .where(owner_pairing_limits.c.id == 1)
                        .values(window_started_at=window, failures=failures + 1)
                    )
                    error = InvalidPairing()
                else:
                    issued = IssuedSession(
                        owner_id=owner_id,
                        session_id=uuid4(),
                        expires_at=now + SESSION_LIFETIME,
                        origin=origin,
                        token=secrets.token_urlsafe(32),
                        csrf=secrets.token_urlsafe(32),
                    )
                    connection.execute(
                        update(owner_pairings)
                        .where(owner_pairings.c.id == pairing["id"])
                        .values(consumed_at=now)
                    )
                    connection.execute(
                        insert(owner_sessions).values(
                            id=issued.session_id,
                            owner_id=issued.owner_id,
                            token_hash=_hash(issued.token),
                            csrf_hash=_hash(issued.csrf),
                            origin=origin,
                            created_at=now,
                            expires_at=issued.expires_at,
                        )
                    )
        # Failed attempts commit independently of any caller's business rollback.
        # A storage failure, however, rolls back consumption and session creation.
        if error is not None:
            raise error
        return issued

    def authorize_connection(
        self, connection: Connection, token: str, csrf: str | None = None
    ) -> OwnerIdentity:
        """Reauthorize in the caller's transaction; never commit it here.

        With csrf=None this is read authorization. Private writes must supply
        the request's CSRF value (including an empty value when missing).
        The returned origin must also match the HTTP application's exact origin.
        Requires an active READ COMMITTED transaction so the post-lock read
        sees revocations committed while waiting, and the lock survives return.
        """
        if not connection.in_transaction():
            raise ValueError("Owner authorization requires a caller transaction")
        if (
            connection.get_execution_options().get("isolation_level") == "AUTOCOMMIT"
            or connection.get_isolation_level() != "READ COMMITTED"
        ):
            raise ValueError("Owner authorization requires READ COMMITTED")
        token_hash = _hash(token)
        owner_id = connection.scalar(
            select(owner_sessions.c.owner_id).where(
                owner_sessions.c.token_hash == token_hash
            )
        )
        if owner_id is None:
            raise SessionUnauthorized()
        self._lock_principal(connection, owner_id)
        session = (
            connection.execute(
                select(owner_sessions).where(
                    owner_sessions.c.token_hash == token_hash,
                    owner_sessions.c.owner_id == owner_id,
                )
            )
            .mappings()
            .one()
        )
        now = self._now()
        if session["revoked_at"] is not None or now >= session["expires_at"]:
            raise SessionUnauthorized()
        if csrf is not None and not hmac.compare_digest(
            _hash(csrf) or "", session["csrf_hash"]
        ):
            raise CsrfForbidden()
        return OwnerIdentity(
            owner_id=owner_id,
            session_id=session["id"],
            expires_at=session["expires_at"].astimezone(UTC),
            origin=session["origin"],
        )

    def authenticate(self, token: str) -> OwnerIdentity:
        with self.database.engine.begin() as connection:
            return self.authorize_connection(connection, token)

    def logout(self, token: str) -> None:
        token_hash = _hash(token)
        with self.database.engine.begin() as connection:
            owner_id = connection.scalar(
                select(owner_sessions.c.owner_id).where(
                    owner_sessions.c.token_hash == token_hash
                )
            )
            if owner_id is None:
                return
            self._lock_principal(connection, owner_id)
            connection.execute(
                update(owner_sessions)
                .where(
                    owner_sessions.c.owner_id == owner_id,
                    owner_sessions.c.token_hash == token_hash,
                    owner_sessions.c.revoked_at.is_(None),
                )
                .values(revoked_at=self._now())
            )

    def revoke_all(self) -> None:
        with self.database.engine.begin() as connection:
            owner_id = self._local_owner(connection)
            if owner_id is None:
                return
            self._lock_principal(connection, owner_id)
            now = self._now()
            connection.execute(
                update(owner_sessions)
                .where(
                    owner_sessions.c.owner_id == owner_id,
                    owner_sessions.c.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
            connection.execute(
                update(owner_pairings)
                .where(
                    owner_pairings.c.owner_id == owner_id,
                    owner_pairings.c.consumed_at.is_(None),
                    owner_pairings.c.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
