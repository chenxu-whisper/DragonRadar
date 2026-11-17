"""Owner-scoped immutable whitelist query records (parse persistence, execute replay).

Every public method authenticates the caller inside one READ COMMITTED
transaction and re-authorizes the referenced publication before any
idempotency lookup or replay, mirroring ``AnalysisJobRepository``. Parse
outcomes marked ``unsupported`` are business results and are never persisted;
successful deterministic parses become immutable ``query_records`` rows.
"""

import hashlib
import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import insert, select, text
from sqlalchemy.engine import Connection

from dragon_radar.publication.models import PublishedSnapshot
from dragon_radar.publication.repository import PublicationRepository
from dragon_radar.publication.tables import publications
from dragon_radar.query.models import (
    PARSER_VERSION,
    Field,
    Op,
    Predicate,
    QueryAst,
    SortDirection,
    SortSpec,
    query_records,
)
from dragon_radar.query.parser import (
    QueryUnsupported,
    UnsupportedItem,
    parse_query,
)
from dragon_radar.storage.repository import (
    Conflict,
    Forbidden,
    NotFound,
    StoredRecordCorrupt,
    _identifier,
    _uuid,
)

_TEXT_LIMIT = 500


def _request_hash(text_value: str, snapshot_id: UUID) -> str:
    canonical = json.dumps(
        {"snapshot_id": str(snapshot_id), "text": text_value},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StoredQuery:
    """One immutable parse record hydrated from ``query_records``."""

    id: UUID
    owner_id: UUID
    snapshot_id: UUID
    ast: QueryAst
    ast_hash: str
    parser_kind: str
    model_version: str | None
    created_at: datetime


@dataclass(frozen=True)
class ParsedQuery:
    """Parse result: either a stored record or a non-persisted unsupported outcome."""

    record: StoredQuery | None
    snapshot: PublishedSnapshot | None
    unsupported: tuple[UnsupportedItem, ...]

    @property
    def supported(self) -> bool:
        return self.record is not None


class QueryRepository:
    def __init__(
        self,
        database,
        owner_repository,
        publication_repository: PublicationRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.database = database
        self.owners = owner_repository
        self.publications = publication_repository
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Query clock must be aware")
        return value.astimezone(UTC)

    @staticmethod
    def _lock(connection: Connection, key: str) -> None:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        )

    def _authorized_input(self, connection: Connection, snapshot_id: UUID):
        row = (
            connection.execute(
                select(publications).where(publications.c.id == snapshot_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound("Publication not found")
        if row["status"] != "published":
            raise Forbidden("Publication is not available")
        return self.publications._authorized_records(connection, [row])[0]

    @staticmethod
    def _hydrate_ast(stored: dict) -> QueryAst:
        """Rebuild an AST from immutable JSONB storage.

        The write side validated the AST through the strict models; JSONB only
        retains JSON-native tokens (enum *values*, tuples as lists, Decimal as
        canonical strings), so the strict validators cannot consume it
        directly. Reconstruct enums/tuples/Decimals explicitly and still run
        every model validator on the rebuilt AST.

        A row that cannot be rehydrated (missing keys, wrong token shapes,
        failed validation) is a tampered/corrupt immutable record, never a
        client-side 400: surface it as :class:`StoredRecordCorrupt`.
        """

        try:

            def value(field_name: str, raw: object) -> object:
                if field_name == Field.RETURN_RATIO.value:
                    return raw if isinstance(raw, Decimal) else Decimal(str(raw))
                if isinstance(raw, list):
                    return tuple(raw)
                return raw

            predicates = tuple(
                Predicate(
                    field=Field(item["field"]),
                    op=Op(item["op"]),
                    value=value(item["field"], item["value"]),
                )
                for item in stored["predicates"]
            )
            sort = None
            if stored.get("sort") is not None:
                sort = SortSpec(
                    field=Field(stored["sort"]["field"]),
                    direction=SortDirection(stored["sort"]["direction"]),
                )
            return QueryAst(
                version=stored["version"],
                predicates=predicates,
                sort=sort,
                limit=stored["limit"],
            )
        except (ArithmeticError, KeyError, TypeError, ValueError) as error:
            # pydantic.ValidationError is a ValueError subclass; a null return
            # ratio value reaches Decimal(str(None)) -> decimal.InvalidOperation,
            # an ArithmeticError rather than a ValueError, and is normalized
            # here so every unreadable immutable AST surfaces as corrupt.
            raise StoredRecordCorrupt(
                "Stored whitelist query AST is corrupt"
            ) from error

    @staticmethod
    def _record(row) -> StoredQuery:
        return StoredQuery(
            id=row["id"],
            owner_id=row["owner_id"],
            snapshot_id=row["snapshot_id"],
            ast=QueryRepository._hydrate_ast(row["ast"]),
            ast_hash=row["ast_hash"],
            parser_kind=row["parser_kind"],
            model_version=row["model_version"],
            created_at=row["created_at"],
        )

    def parse(
        self,
        token: str,
        csrf: str,
        *,
        text_value: str,
        snapshot_id: UUID | str,
        key: str,
    ) -> ParsedQuery:
        """Authorize token and CSRF, then parse/persist in that same transaction."""
        self._require_write(csrf)
        with self.database.engine.begin() as connection:
            identity = self.owners.authorize_connection(connection, token, csrf)
            return self.parse_for_owner(
                connection,
                identity.owner_id,
                text_value=text_value,
                snapshot_id=snapshot_id,
                key=key,
            )

    def parse_for_owner(
        self,
        connection: Connection,
        owner_id: UUID,
        *,
        text_value: str,
        snapshot_id: UUID | str,
        key: str,
    ) -> ParsedQuery:
        """Parse after the caller locked/authorized principal in the same transaction.

        This internal entry point never authenticates a caller. HTTP handlers
        must call :meth:`parse` and must never call this method directly.
        """
        self._require_transaction(connection)
        _identifier(key)
        if not isinstance(owner_id, UUID):
            raise ValueError("Invalid owner ID")
        if (
            not isinstance(text_value, str)
            or len(text_value) == 0
            or len(text_value) > _TEXT_LIMIT
        ):
            raise ValueError("Query text must be a 1..500 character string")
        snapshot_uuid = _uuid(snapshot_id)

        # Authorization precedes every idempotency replay or parser call.
        snapshot = self._authorized_input(connection, snapshot_uuid)
        request_hash = _request_hash(text_value, snapshot_uuid)
        self._lock(connection, f"query:{owner_id}:{key}")
        existing = (
            connection.execute(
                select(query_records).where(
                    query_records.c.owner_id == owner_id,
                    query_records.c.idempotency_key == key,
                )
            )
            .mappings()
            .first()
        )
        if existing is not None:
            if not hmac.compare_digest(existing["request_hash"], request_hash):
                raise Conflict("Idempotency key already belongs to another request")
            return ParsedQuery(self._record(existing), snapshot, ())

        outcome = parse_query(text_value)
        if isinstance(outcome, QueryUnsupported):
            # Business outcome, never persisted; the key stays free for a later
            # corrected request under the same idempotency identifier.
            return ParsedQuery(None, snapshot, tuple(outcome.unsupported))

        now = self._now()
        row = (
            connection.execute(
                insert(query_records)
                .values(
                    id=uuid4(),
                    owner_id=owner_id,
                    snapshot_id=snapshot_uuid,
                    idempotency_key=key,
                    request_hash=request_hash,
                    ast=outcome.ast.model_dump(mode="json"),
                    ast_hash=outcome.ast_hash,
                    parser_version=PARSER_VERSION,
                    parser_kind=outcome.parser_kind.value,
                    model_version=outcome.model_version,
                    created_at=now,
                )
                .returning(query_records)
            )
            .mappings()
            .one()
        )
        return ParsedQuery(self._record(row), snapshot, ())

    def execute(
        self,
        token: str,
        csrf: str,
        *,
        query_id: UUID | str,
        confirmed_ast_hash: str,
        snapshot_id: UUID | str,
        key: str,
    ) -> tuple[StoredQuery, PublishedSnapshot]:
        """Authorize and re-check publication/hash ownership, then return the record."""
        self._require_write(csrf)
        with self.database.engine.begin() as connection:
            identity = self.owners.authorize_connection(connection, token, csrf)
            return self.execute_for_owner(
                connection,
                identity.owner_id,
                query_id=query_id,
                confirmed_ast_hash=confirmed_ast_hash,
                snapshot_id=snapshot_id,
                key=key,
            )

    def execute_for_owner(
        self,
        connection: Connection,
        owner_id: UUID,
        *,
        query_id: UUID | str,
        confirmed_ast_hash: str,
        snapshot_id: UUID | str,
        key: str,
    ) -> tuple[StoredQuery, PublishedSnapshot]:
        """Execute after the caller locked/authorized principal in the same transaction.

        This internal entry point never authenticates a caller. HTTP handlers
        must call :meth:`execute` and must never call this method directly.
        """
        self._require_transaction(connection)
        _identifier(key)
        if not isinstance(owner_id, UUID):
            raise ValueError("Invalid owner ID")
        if not isinstance(confirmed_ast_hash, str) or not confirmed_ast_hash:
            raise ValueError("Invalid confirmed AST hash")
        query_uuid = _uuid(query_id)
        snapshot_uuid = _uuid(snapshot_id)

        row = (
            connection.execute(
                select(query_records).where(
                    query_records.c.id == query_uuid,
                    query_records.c.owner_id == owner_id,
                )
            )
            .mappings()
            .first()
        )
        # A missing record or one owned by another principal are indistinguishable.
        if row is None:
            raise NotFound("Query record not found")

        # Re-authorization precedes every conflict comparison.
        snapshot = self._authorized_input(connection, snapshot_uuid)
        if row["snapshot_id"] != snapshot_uuid:
            raise Conflict("Query belongs to another snapshot")
        if not hmac.compare_digest(row["ast_hash"], confirmed_ast_hash):
            raise Conflict("Confirmed AST hash does not match the stored query")
        return self._record(row), snapshot

    def _require_write(self, csrf: str) -> None:
        if self.owners is None:
            raise Forbidden("Owner access is unavailable")
        if not isinstance(csrf, str) or not csrf:
            from dragon_radar.owner import CsrfForbidden

            raise CsrfForbidden()

    @staticmethod
    def _require_transaction(connection: Connection) -> None:
        if not connection.in_transaction():
            raise ValueError("Query operation requires a caller transaction")
        if (
            connection.get_execution_options().get("isolation_level") == "AUTOCOMMIT"
            or connection.get_isolation_level() != "READ COMMITTED"
        ):
            raise ValueError("Query operation requires READ COMMITTED")


__all__ = ["ParsedQuery", "QueryRepository", "StoredQuery"]
