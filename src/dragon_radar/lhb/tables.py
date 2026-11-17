"""SQLAlchemy metadata for immutable LHB disclosure evidence.

The six tables mirror the trust-boundary conventions of the realtime package:
UUID record identifiers, source-scoped natural keys, explicit stable constraint
and index names (``lhb_`` prefix), and append-only enforcement through the
shared ``reject_immutable_mutation`` trigger function. Seat catalog rows carry
no natural-person fields; a remap inserts a new ``mapping_version`` row and
never rewrites history.
"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
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

lhb_import_batches = Table(
    "lhb_import_batches",
    metadata,
    Column("id", PGUUID(as_uuid=True), nullable=False),
    Column("batch_id", Text, nullable=False),
    Column("source_id", Text, nullable=False),
    Column("source_version", Text, nullable=False),
    Column("source_event_at", DateTime(timezone=True), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("permission_snapshot", JSONB, nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("status", String(20), nullable=False),
    Column("reason", Text),
    Column("imported_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("id", name="lhb_import_batches_pkey"),
    UniqueConstraint(
        "source_id",
        "batch_id",
        name="lhb_import_batches_source_batch_key",
    ),
    UniqueConstraint(
        "source_id",
        "batch_id",
        "source_version",
        name="lhb_import_batches_source_batch_version_key",
    ),
    ForeignKeyConstraint(
        ["source_id"],
        ["source_grants.source_id"],
        name="lhb_import_batches_source_id_fkey",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "source_event_at <= received_at AND received_at <= available_at",
        name="lhb_import_batches_timestamp_order",
    ),
    CheckConstraint(
        "payload_hash ~ '^[0-9a-f]{64}$'",
        name="lhb_import_batches_payload_hash",
    ),
    CheckConstraint(
        "status IN ('accepted', 'duplicate', 'rejected', 'incomplete') "
        "AND ((status IN ('accepted', 'duplicate') AND reason IS NULL) "
        "OR (status IN ('rejected', 'incomplete') "
        "AND reason IS NOT NULL AND reason !~ '^[[:space:]]*$'))",
        name="lhb_import_batches_status",
    ),
    Index(
        "lhb_import_batches_as_of",
        text("available_at DESC"),
        text("id DESC"),
    ),
    Index(
        "lhb_import_batches_source_as_of",
        "source_id",
        text("available_at DESC"),
        text("id DESC"),
    ),
)

lhb_import_attempts = Table(
    "lhb_import_attempts",
    metadata,
    Column("id", PGUUID(as_uuid=True), nullable=False),
    Column("source_id", Text, nullable=False),
    Column("batch_id", Text, nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("status", String(20), nullable=False),
    Column("reason", Text),
    Column("details", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("id", name="lhb_import_attempts_pkey"),
    ForeignKeyConstraint(
        ["source_id"],
        ["source_grants.source_id"],
        name="lhb_import_attempts_source_id_fkey",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "status IN ('accepted', 'duplicate', 'rejected', 'incomplete') "
        "AND ((status IN ('accepted', 'duplicate') AND reason IS NULL) "
        "OR (status IN ('rejected', 'incomplete') "
        "AND reason IS NOT NULL AND reason !~ '^[[:space:]]*$'))",
        name="lhb_import_attempts_status",
    ),
    CheckConstraint(
        "payload_hash ~ '^[0-9a-f]{64}$'",
        name="lhb_import_attempts_payload_hash",
    ),
    Index(
        "lhb_import_attempts_bound_idempotency_key",
        "source_id",
        "idempotency_key",
        unique=True,
        postgresql_where=text("status IN ('accepted', 'incomplete')"),
    ),
    Index(
        "lhb_import_attempts_listing",
        "source_id",
        text("created_at DESC"),
        text("id DESC"),
    ),
)

lhb_disclosures = Table(
    "lhb_disclosures",
    metadata,
    Column("id", PGUUID(as_uuid=True), nullable=False),
    Column("source_id", Text, nullable=False),
    Column("disclosure_id", Text, nullable=False),
    Column("security_id", Text, nullable=False),
    Column("disclosure_date", Date, nullable=False),
    Column("reason", Text, nullable=False),
    Column("permission_id", Text, nullable=False),
    PrimaryKeyConstraint("id", name="lhb_disclosures_pkey"),
    UniqueConstraint(
        "source_id",
        "disclosure_id",
        name="lhb_disclosures_identity_key",
    ),
    # Unique target for lhb_disclosure_seats' (source, disclosure, date) FK;
    # the denormalized disclosure_date is part of the key.
    UniqueConstraint(
        "source_id",
        "disclosure_id",
        "disclosure_date",
        name="lhb_disclosures_identity_date_key",
    ),
    ForeignKeyConstraint(
        ["source_id"],
        ["source_grants.source_id"],
        name="lhb_disclosures_source_id_fkey",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "security_id !~ '^[[:space:]]*$' "
        "AND reason !~ '^[[:space:]]*$' "
        "AND permission_id !~ '^[[:space:]]*$'",
        name="lhb_disclosures_required_text",
    ),
    Index(
        "lhb_disclosures_listing",
        "disclosure_date",
        "security_id",
        "source_id",
        "disclosure_id",
    ),
)

lhb_disclosure_revisions = Table(
    "lhb_disclosure_revisions",
    metadata,
    Column("id", PGUUID(as_uuid=True), nullable=False),
    Column("source_id", Text, nullable=False),
    Column("disclosure_id", Text, nullable=False),
    Column("batch_id", Text, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("source_event_at", DateTime(timezone=True), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("permission_snapshot", JSONB, nullable=False),
    # Revision-level import state for the whole payload ('complete' |
    # 'incomplete'). The per-seat buy/sell null-state completeness
    # ('complete' | 'buy_unknown' | 'sell_unknown' | 'both_unknown') lives on
    # lhb_disclosure_seats and is derived by the aggregation/service layer.
    Column("completeness", String(20), nullable=False),
    Column("payload_hash", String(64), nullable=False),
    PrimaryKeyConstraint("id", name="lhb_disclosure_revisions_pkey"),
    UniqueConstraint(
        "source_id",
        "disclosure_id",
        "revision",
        name="lhb_disclosure_revisions_revision_key",
    ),
    ForeignKeyConstraint(
        ["source_id", "batch_id"],
        ["lhb_import_batches.source_id", "lhb_import_batches.batch_id"],
        name="lhb_disclosure_revisions_batch_fkey",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["source_id", "disclosure_id"],
        ["lhb_disclosures.source_id", "lhb_disclosures.disclosure_id"],
        name="lhb_disclosure_revisions_disclosure_fkey",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "revision > 0",
        name="lhb_disclosure_revisions_positive_revision",
    ),
    CheckConstraint(
        "source_event_at <= received_at AND received_at <= available_at",
        name="lhb_disclosure_revisions_timestamp_order",
    ),
    CheckConstraint(
        "payload_hash ~ '^[0-9a-f]{64}$'",
        name="lhb_disclosure_revisions_payload_hash",
    ),
    CheckConstraint(
        "completeness IN ('complete', 'incomplete')",
        name="lhb_disclosure_revisions_completeness",
    ),
    Index(
        "lhb_disclosure_revisions_visible",
        "source_id",
        "disclosure_id",
        text("available_at DESC"),
        text("revision DESC"),
        text("id DESC"),
    ),
    Index(
        "lhb_disclosure_revisions_batch",
        "source_id",
        "batch_id",
    ),
)

lhb_seats = Table(
    "lhb_seats",
    metadata,
    Column("id", PGUUID(as_uuid=True), nullable=False),
    Column("source_id", Text, nullable=False),
    Column("seat_id", Text, nullable=False),
    Column("mapping_version", Text, nullable=False),
    Column("source_seat_name", Text, nullable=False),
    Column("provenance", JSONB, nullable=False),
    PrimaryKeyConstraint("id", name="lhb_seats_pkey"),
    UniqueConstraint(
        "source_id",
        "seat_id",
        "mapping_version",
        name="lhb_seats_identity_key",
    ),
    ForeignKeyConstraint(
        ["source_id"],
        ["source_grants.source_id"],
        name="lhb_seats_source_id_fkey",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "seat_id !~ '^[[:space:]]*$' "
        "AND mapping_version !~ '^[[:space:]]*$' "
        "AND source_seat_name !~ '^[[:space:]]*$'",
        name="lhb_seats_required_text",
    ),
)

lhb_disclosure_seats = Table(
    "lhb_disclosure_seats",
    metadata,
    Column("id", PGUUID(as_uuid=True), nullable=False),
    Column("source_id", Text, nullable=False),
    Column("disclosure_id", Text, nullable=False),
    Column("disclosure_date", Date, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("source_row_id", Text, nullable=False),
    Column("seat_id", Text, nullable=False),
    Column("mapping_version", Text, nullable=False),
    Column("side", String(4)),
    Column("rank", Integer),
    Column("buy_amount", Numeric),
    Column("sell_amount", Numeric),
    Column("currency", Text, nullable=False),
    Column("unit", Text, nullable=False),
    Column("completeness", String(20), nullable=False),
    PrimaryKeyConstraint("id", name="lhb_disclosure_seats_pkey"),
    UniqueConstraint(
        "source_id",
        "disclosure_id",
        "revision",
        "source_row_id",
        name="lhb_disclosure_seats_revision_row_key",
    ),
    UniqueConstraint(
        "source_id",
        "disclosure_id",
        "revision",
        "seat_id",
        name="lhb_disclosure_seats_revision_seat_key",
    ),
    ForeignKeyConstraint(
        ["source_id", "disclosure_id", "revision"],
        [
            "lhb_disclosure_revisions.source_id",
            "lhb_disclosure_revisions.disclosure_id",
            "lhb_disclosure_revisions.revision",
        ],
        name="lhb_disclosure_seats_revision_fkey",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["source_id", "seat_id", "mapping_version"],
        [
            "lhb_seats.source_id",
            "lhb_seats.seat_id",
            "lhb_seats.mapping_version",
        ],
        name="lhb_disclosure_seats_seat_fkey",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["source_id", "disclosure_id", "disclosure_date"],
        [
            "lhb_disclosures.source_id",
            "lhb_disclosures.disclosure_id",
            "lhb_disclosures.disclosure_date",
        ],
        name="lhb_disclosure_seats_disclosure_fkey",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "side IS NULL OR side IN ('buy', 'sell', 'net')",
        name="lhb_disclosure_seats_side",
    ),
    CheckConstraint(
        '"rank" IS NULL OR "rank" > 0',
        name="lhb_disclosure_seats_rank",
    ),
    CheckConstraint(
        "("
        "buy_amount IS NULL OR "
        "(buy_amount >= 0 AND buy_amount < 'Infinity'::numeric)"
        ") AND ("
        "sell_amount IS NULL OR "
        "(sell_amount >= 0 AND sell_amount < 'Infinity'::numeric)"
        ")",
        name="lhb_disclosure_seats_nonnegative_amounts",
    ),
    CheckConstraint(
        "currency !~ '^[[:space:]]*$' AND unit !~ '^[[:space:]]*$'",
        name="lhb_disclosure_seats_currency_unit",
    ),
    CheckConstraint(
        "completeness IN ('complete', 'buy_unknown', 'sell_unknown', 'both_unknown')",
        name="lhb_disclosure_seats_completeness",
    ),
    Index(
        "lhb_disclosure_seats_seat_history",
        "source_id",
        "seat_id",
        text("disclosure_date DESC"),
        "disclosure_id",
        text("revision DESC"),
    ),
)

__all__ = [
    "lhb_disclosure_revisions",
    "lhb_disclosure_seats",
    "lhb_disclosures",
    "lhb_import_attempts",
    "lhb_import_batches",
    "lhb_seats",
]
