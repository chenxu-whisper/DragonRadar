"""SQLAlchemy metadata for owner-scoped research backtest runs.

The ledger mirrors the trust-boundary conventions of
:mod:`dragon_radar.analysis_jobs.models`: UUID record identifiers, an
owner/idempotency uniqueness pair, explicit stable constraint and index
names (``research_backtest_`` prefix), and a five-state lifecycle guarded
by one mutually-exclusive CHECK constraint. Identity and request inputs are
frozen by a row-level trigger; only scheduling, lease, lifecycle and result
columns may change.

The module is deliberately separate from :mod:`dragon_radar.research.models`
so the pure, SQLAlchemy-free computational kernel never imports IO layers.
"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from dragon_radar.owner.models import owner_principals  # noqa: F401
from dragon_radar.publication.tables import publications  # noqa: F401
from dragon_radar.storage.models import metadata

research_backtest_cache = Table(
    "research_backtest_cache",
    metadata,
    Column("id", PGUUID(as_uuid=True), nullable=False),
    Column("config_hash", String(64), nullable=False),
    Column("engine_version", String(100), nullable=False),
    Column("input_manifest", JSONB, nullable=False),
    Column("result", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("id", name="research_backtest_cache_pkey"),
    UniqueConstraint(
        "config_hash",
        name="research_backtest_cache_config_hash_key",
    ),
    CheckConstraint(
        "config_hash ~ '^[0-9a-f]{64}$'",
        name="research_backtest_cache_config_hash",
    ),
)

research_backtest_runs = Table(
    "research_backtest_runs",
    metadata,
    Column("id", PGUUID(as_uuid=True), nullable=False),
    Column("owner_id", PGUUID(as_uuid=True), nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("config_hash", String(64), nullable=False),
    Column("snapshot_id", PGUUID(as_uuid=True), nullable=False),
    Column("input_manifest", JSONB, nullable=False),
    Column("status", String(20), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    # Conservative wall-clock budget captured at submission time. The O4
    # threshold itself is supplied by the repository/worker (Tasks 11/13);
    # the schema only enforces a non-negative bound, never a hard-coded value.
    Column("max_wall_clock_ms", BigInteger, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("lease_token", PGUUID(as_uuid=True)),
    Column("lease_owner", String(100)),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("result", JSONB(none_as_null=True)),
    Column("failure", JSONB(none_as_null=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("completed_at", DateTime(timezone=True)),
    PrimaryKeyConstraint("id", name="research_backtest_runs_pkey"),
    UniqueConstraint(
        "owner_id",
        "idempotency_key",
        name="research_backtest_runs_owner_idempotency_key",
    ),
    ForeignKeyConstraint(
        ["owner_id"],
        ["owner_principals.id"],
        name="research_backtest_runs_owner_id_fkey",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["snapshot_id"],
        ["publications.id"],
        name="research_backtest_runs_snapshot_id_fkey",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "attempt_count >= 0",
        name="research_backtest_runs_attempt_count",
    ),
    CheckConstraint(
        "max_wall_clock_ms >= 0",
        name="research_backtest_runs_max_wall_clock_ms",
    ),
    CheckConstraint(
        "request_hash ~ '^[0-9a-f]{64}$'",
        name="research_backtest_runs_request_hash",
    ),
    CheckConstraint(
        "config_hash ~ '^[0-9a-f]{64}$'",
        name="research_backtest_runs_config_hash",
    ),
    CheckConstraint(
        "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
        name="research_backtest_runs_status",
    ),
    CheckConstraint(
        "(status = 'queued' AND lease_token IS NULL "
        "AND lease_owner IS NULL AND lease_expires_at IS NULL "
        "AND result IS NULL AND failure IS NULL AND completed_at IS NULL) OR "
        "(status = 'running' AND lease_token IS NOT NULL "
        "AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
        "AND result IS NULL AND failure IS NULL AND completed_at IS NULL) OR "
        "(status = 'succeeded' AND lease_token IS NULL "
        "AND lease_owner IS NULL AND lease_expires_at IS NULL "
        "AND result IS NOT NULL AND failure IS NULL AND completed_at IS NOT NULL) OR "
        "(status = 'failed' AND lease_token IS NULL "
        "AND lease_owner IS NULL AND lease_expires_at IS NULL "
        "AND result IS NULL AND failure IS NOT NULL AND completed_at IS NOT NULL) OR "
        "(status = 'cancelled' AND lease_token IS NULL "
        "AND lease_owner IS NULL AND lease_expires_at IS NULL "
        "AND result IS NULL AND failure IS NULL AND completed_at IS NOT NULL)",
        name="research_backtest_runs_state",
    ),
)

Index(
    "research_backtest_runs_claim",
    research_backtest_runs.c.available_at,
    research_backtest_runs.c.created_at,
    postgresql_where=research_backtest_runs.c.status.in_(("queued", "running")),
)

__all__ = [
    "research_backtest_cache",
    "research_backtest_runs",
]
