"""SQLAlchemy metadata for immutable realtime market evidence."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from dragon_radar.storage.models import metadata, source_grants  # noqa: F401

realtime_quote_batches = Table(
    "realtime_quote_batches",
    metadata,
    Column("id", PGUUID(as_uuid=True), nullable=False),
    Column("quote_batch_id", Text, nullable=False),
    Column("source_id", Text, nullable=False),
    Column("source_version", Text, nullable=False),
    Column("source_event_at", DateTime(timezone=True), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("source_sequence", BigInteger),
    Column("permission_snapshot", JSONB, nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("imported_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("id", name="realtime_quote_batches_pkey"),
    UniqueConstraint(
        "source_id",
        "quote_batch_id",
        name="realtime_quote_batches_source_batch_key",
    ),
    UniqueConstraint(
        "source_id",
        "quote_batch_id",
        "source_version",
        name="realtime_quote_batches_source_batch_version_key",
    ),
    ForeignKeyConstraint(
        ["source_id"],
        ["source_grants.source_id"],
        name="realtime_quote_batches_source_id_fkey",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "source_event_at <= received_at AND received_at <= available_at",
        name="realtime_quote_batches_timestamp_order",
    ),
    CheckConstraint(
        "source_sequence IS NULL OR source_sequence >= 0",
        name="realtime_quote_batches_source_sequence",
    ),
    CheckConstraint(
        "payload_hash ~ '^[0-9a-f]{64}$'",
        name="realtime_quote_batches_payload_hash",
    ),
    Index(
        "realtime_quote_batches_as_of",
        "source_id",
        text("available_at DESC"),
        text("source_event_at DESC"),
        text("id DESC"),
    ),
)

realtime_quotes = Table(
    "realtime_quotes",
    metadata,
    Column("id", PGUUID(as_uuid=True), nullable=False),
    Column("source_id", Text, nullable=False),
    Column("batch_id", Text, nullable=False),
    Column("security_id", Text, nullable=False),
    Column("last", Numeric),
    Column("bid", Numeric),
    Column("ask", Numeric),
    Column("bid_size", Numeric),
    Column("ask_size", Numeric),
    Column("volume", Numeric),
    Column("amount", Numeric),
    Column("order_book_status", String(20), nullable=False),
    Column("order_book_unavailable_reason", Text),
    PrimaryKeyConstraint("id", name="realtime_quotes_pkey"),
    ForeignKeyConstraint(
        ["source_id", "batch_id"],
        [
            "realtime_quote_batches.source_id",
            "realtime_quote_batches.quote_batch_id",
        ],
        name="realtime_quotes_batch_fkey",
        ondelete="RESTRICT",
    ),
    UniqueConstraint(
        "source_id",
        "batch_id",
        "security_id",
        name="realtime_quotes_batch_security_key",
    ),
    CheckConstraint(
        "("
        "last IS NULL OR (last >= 0 AND last < 'Infinity'::numeric)"
        ") AND ("
        "bid IS NULL OR (bid >= 0 AND bid < 'Infinity'::numeric)"
        ") AND ("
        "ask IS NULL OR (ask >= 0 AND ask < 'Infinity'::numeric)"
        ") AND ("
        "bid_size IS NULL OR (bid_size >= 0 AND bid_size < 'Infinity'::numeric)"
        ") AND ("
        "ask_size IS NULL OR (ask_size >= 0 AND ask_size < 'Infinity'::numeric)"
        ") AND ("
        "volume IS NULL OR (volume >= 0 AND volume < 'Infinity'::numeric)"
        ") AND ("
        "amount IS NULL OR (amount >= 0 AND amount < 'Infinity'::numeric)"
        ")",
        name="realtime_quotes_nonnegative_values",
    ),
    CheckConstraint(
        "order_book_status IN ('available', 'disabled', 'unavailable')",
        name="realtime_quotes_order_book_status",
    ),
    CheckConstraint(
        "("
        "order_book_status = 'available' "
        "AND bid IS NOT NULL AND ask IS NOT NULL "
        "AND bid_size IS NOT NULL AND ask_size IS NOT NULL "
        "AND order_book_unavailable_reason IS NULL"
        ") OR ("
        "order_book_status IN ('disabled', 'unavailable') "
        "AND bid IS NULL AND ask IS NULL "
        "AND bid_size IS NULL AND ask_size IS NULL "
        "AND order_book_unavailable_reason IS NOT NULL "
        "AND btrim(order_book_unavailable_reason) <> ''"
        ")",
        name="realtime_quotes_order_book_availability",
    ),
    Index("realtime_quotes_security", "security_id", "batch_id"),
)

realtime_minute_bars = Table(
    "realtime_minute_bars",
    metadata,
    Column("id", PGUUID(as_uuid=True), nullable=False),
    Column("source_id", Text, nullable=False),
    Column("batch_id", Text, nullable=False),
    Column("source_version", Text, nullable=False),
    Column("security_id", Text, nullable=False),
    Column("event_at", DateTime(timezone=True), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("open", Numeric, nullable=False),
    Column("high", Numeric, nullable=False),
    Column("low", Numeric, nullable=False),
    Column("close", Numeric, nullable=False),
    Column("volume", Numeric),
    Column("amount", Numeric),
    Column("final", Boolean, nullable=False),
    Column("source_event_at", DateTime(timezone=True), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("id", name="realtime_minute_bars_pkey"),
    ForeignKeyConstraint(
        ["source_id", "batch_id", "source_version"],
        [
            "realtime_quote_batches.source_id",
            "realtime_quote_batches.quote_batch_id",
            "realtime_quote_batches.source_version",
        ],
        name="realtime_minute_bars_batch_fkey",
        ondelete="RESTRICT",
    ),
    UniqueConstraint(
        "source_id",
        "security_id",
        "event_at",
        "revision",
        name="realtime_minute_bars_revision_key",
    ),
    CheckConstraint(
        "revision > 0",
        name="realtime_minute_bars_positive_revision",
    ),
    CheckConstraint(
        "open >= 0 AND open < 'Infinity'::numeric "
        "AND high >= 0 AND high < 'Infinity'::numeric "
        "AND low >= 0 AND low < 'Infinity'::numeric "
        "AND close >= 0 AND close < 'Infinity'::numeric",
        name="realtime_minute_bars_nonnegative_values",
    ),
    CheckConstraint(
        "high >= open AND high >= close AND high >= low "
        "AND low <= open AND low <= close AND low <= high",
        name="realtime_minute_bars_ohlc",
    ),
    CheckConstraint(
        "("
        "volume IS NULL OR (volume >= 0 AND volume < 'Infinity'::numeric)"
        ") AND ("
        "amount IS NULL OR (amount >= 0 AND amount < 'Infinity'::numeric)"
        ")",
        name="realtime_minute_bars_optional_values",
    ),
    CheckConstraint(
        "source_event_at <= received_at "
        "AND event_at <= received_at "
        "AND received_at <= available_at",
        name="realtime_minute_bars_timestamp_order",
    ),
    Index(
        "realtime_minute_bars_as_of",
        "security_id",
        "event_at",
        "available_at",
        text("revision DESC"),
        text("id DESC"),
    ),
    Index("realtime_minute_bars_batch", "source_id", "batch_id"),
)

realtime_anomaly_events = Table(
    "realtime_anomaly_events",
    metadata,
    Column("event_id", Text, nullable=False),
    Column("source_id", Text, nullable=False),
    Column("batch_id", Text, nullable=False),
    Column("security_id", Text, nullable=False),
    Column("rule_version", Text, nullable=False),
    Column("metric", String(30), nullable=False),
    Column("window", Integer, nullable=False),
    Column("value", Numeric),
    Column("threshold", Numeric, nullable=False),
    Column("event_at", DateTime(timezone=True), nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("quality", String(20), nullable=False),
    Column("cooldown_bucket", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("event_id", name="realtime_anomaly_events_pkey"),
    ForeignKeyConstraint(
        ["source_id", "batch_id"],
        [
            "realtime_quote_batches.source_id",
            "realtime_quote_batches.quote_batch_id",
        ],
        name="realtime_anomaly_events_batch_fkey",
        ondelete="RESTRICT",
    ),
    UniqueConstraint(
        "source_id",
        "batch_id",
        "security_id",
        "rule_version",
        "metric",
        "window",
        "cooldown_bucket",
        name="realtime_anomaly_events_cooldown_key",
    ),
    CheckConstraint(
        "metric IN ('window_return', 'relative_volume', 'order_book_imbalance') "
        "AND (value IS NULL OR "
        "(value > '-Infinity'::numeric AND value < 'Infinity'::numeric)) "
        "AND threshold > '-Infinity'::numeric "
        "AND threshold < 'Infinity'::numeric",
        name="realtime_anomaly_events_metric",
    ),
    CheckConstraint(
        '"window" >= 2 AND "window" <= 120',
        name="realtime_anomaly_events_window",
    ),
    CheckConstraint(
        "quality IN ('complete', 'delayed', 'incomplete')",
        name="realtime_anomaly_events_quality",
    ),
    CheckConstraint(
        "available_at >= event_at AND cooldown_bucket <= event_at",
        name="realtime_anomaly_events_timestamp_order",
    ),
    Index(
        "realtime_anomaly_events_listing",
        text("available_at DESC"),
        text("event_id DESC"),
    ),
    Index(
        "realtime_anomaly_events_security",
        "security_id",
        text("event_at DESC"),
    ),
)

realtime_sector_flow_points = Table(
    "realtime_sector_flow_points",
    metadata,
    Column("id", PGUUID(as_uuid=True), nullable=False),
    Column("source_id", Text, nullable=False),
    Column("batch_id", Text, nullable=False),
    Column("sector_id", Text, nullable=False),
    Column("event_at", DateTime(timezone=True), nullable=False),
    Column("net_inflow", Numeric),
    Column("unit", Text, nullable=False),
    Column("unavailable_reason", Text),
    PrimaryKeyConstraint("id", name="realtime_sector_flow_points_pkey"),
    ForeignKeyConstraint(
        ["source_id", "batch_id"],
        [
            "realtime_quote_batches.source_id",
            "realtime_quote_batches.quote_batch_id",
        ],
        name="realtime_sector_flow_points_batch_fkey",
        ondelete="RESTRICT",
    ),
    UniqueConstraint(
        "source_id",
        "batch_id",
        "sector_id",
        "event_at",
        name="realtime_sector_flow_points_identity_key",
    ),
    CheckConstraint(
        "("
        "net_inflow IS NOT NULL "
        "AND net_inflow > '-Infinity'::numeric "
        "AND net_inflow < 'Infinity'::numeric "
        "AND unavailable_reason IS NULL"
        ") OR ("
        "net_inflow IS NULL AND unavailable_reason IS NOT NULL "
        "AND btrim(unavailable_reason) <> ''"
        ")",
        name="realtime_sector_flow_points_availability",
    ),
    Index(
        "realtime_sector_flow_points_event",
        "sector_id",
        text("event_at DESC"),
    ),
)

realtime_import_attempts = Table(
    "realtime_import_attempts",
    metadata,
    Column("id", PGUUID(as_uuid=True), nullable=False),
    Column(
        "source_id",
        Text,
        ForeignKey(
            "source_grants.source_id",
            name="realtime_import_attempts_source_id_fkey",
            ondelete="RESTRICT",
        ),
        nullable=False,
    ),
    Column("batch_id", Text, nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("status", String(20), nullable=False),
    Column("reason", Text),
    Column("details", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("id", name="realtime_import_attempts_pkey"),
    UniqueConstraint(
        "source_id",
        "idempotency_key",
        name="realtime_import_attempts_idempotency_key",
    ),
    CheckConstraint(
        "status IN ('accepted', 'duplicate', 'rejected', 'incomplete') "
        "AND ((status IN ('accepted', 'duplicate') AND reason IS NULL) "
        "OR (status IN ('rejected', 'incomplete') "
        "AND reason IS NOT NULL AND btrim(reason) <> ''))",
        name="realtime_import_attempts_status",
    ),
    CheckConstraint(
        "payload_hash ~ '^[0-9a-f]{64}$'",
        name="realtime_import_attempts_payload_hash",
    ),
    Index(
        "realtime_import_attempts_listing",
        "source_id",
        text("created_at DESC"),
        text("id DESC"),
    ),
)

__all__ = [
    "realtime_anomaly_events",
    "realtime_import_attempts",
    "realtime_minute_bars",
    "realtime_quote_batches",
    "realtime_quotes",
    "realtime_sector_flow_points",
]
