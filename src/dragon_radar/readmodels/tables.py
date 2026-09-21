"""Normalized market read-model mappings with archive lineage."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    Time,
    UniqueConstraint,
    func,
    literal_column,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from dragon_radar.storage.models import metadata

security = Table(
    "security",
    metadata,
    Column("security_id", String(32), primary_key=True),
    Column("code", String(10), nullable=False),
    Column("name", String(50), nullable=False),
    Column("security_type", String(30), nullable=False),
    Column("exchange", String(10), nullable=False),
    Column("board", String(20), nullable=False),
    Column("list_date", Date, nullable=False),
    Column("delist_date", Date),
    Column("is_st", Boolean, nullable=False),
    Column("is_suspended", Boolean, nullable=False),
    Column("source", String(50), nullable=False),
    Column("source_version", String(100), nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column(
        "source_archive_id",
        PGUUID(as_uuid=True),
        ForeignKey("import_batches.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Index("security_exchange_code", "exchange", "code"),
    Index("security_exchange_board", "exchange", "board"),
)

security_state_history = Table(
    "security_state_history",
    metadata,
    Column("history_id", BigInteger, Identity(always=True), primary_key=True),
    Column(
        "security_id",
        String(32),
        ForeignKey("security.security_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("state_type", String(20), nullable=False),
    Column("old_value", String(100)),
    Column("new_value", String(100)),
    Column("valid_from", Date, nullable=False),
    Column("valid_to", Date),
    Column("source", String(50), nullable=False),
    Column("source_version", String(100), nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column(
        "source_archive_id",
        PGUUID(as_uuid=True),
        ForeignKey("import_batches.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    UniqueConstraint("security_id", "state_type", "valid_from"),
    CheckConstraint("valid_to IS NULL OR valid_to > valid_from"),
    Index("security_state_history_security_type", "security_id", "state_type"),
)
security_state_history.append_constraint(
    ExcludeConstraint(
        (security_state_history.c.security_id, "="),
        (security_state_history.c.state_type, "="),
        (
            func.daterange(
                security_state_history.c.valid_from,
                security_state_history.c.valid_to,
                literal_column("'[)'"),
            ),
            "&&",
        ),
        name="security_state_history_no_overlap",
        using="gist",
    )
)


def _daily_table(name: str) -> Table:
    return Table(
        name,
        metadata,
        Column(
            "security_id",
            String(32),
            ForeignKey("security.security_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        Column("trade_date", Date, nullable=False),
        Column("open", Numeric, nullable=True),
        Column("high", Numeric, nullable=True),
        Column("low", Numeric, nullable=True),
        Column("close", Numeric, nullable=False),
        Column("volume", Numeric, nullable=True),
        Column("amount", Numeric, nullable=True),
        Column("change_ratio", Numeric, nullable=True),
        Column("adj_factor", Numeric, nullable=False),
        Column("limit_up_price", Numeric),
        Column("limit_down_price", Numeric),
        Column("turnover_rate", Numeric),
        Column("float_mv", Numeric),
        Column("total_mv", Numeric),
        Column("currency", String(10), nullable=False),
        Column("volume_unit", String(20), nullable=False),
        Column("source", String(50), nullable=False),
        Column("source_version", String(100), nullable=False),
        Column("available_at", DateTime(timezone=True), nullable=False),
        Column(
            "source_archive_id",
            PGUUID(as_uuid=True),
            ForeignKey("import_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        UniqueConstraint("security_id", "trade_date", "source", "source_version"),
        Index(f"{name}_trade_date", "trade_date"),
        CheckConstraint("close > 0"),
        CheckConstraint("volume IS NULL OR volume >= 0"),
        CheckConstraint("amount IS NULL OR amount >= 0"),
        CheckConstraint("turnover_rate IS NULL OR turnover_rate >= 0"),
        CheckConstraint("float_mv IS NULL OR float_mv >= 0"),
        CheckConstraint("total_mv IS NULL OR total_mv >= 0"),
        CheckConstraint("adj_factor > 0"),
    )


daily_kline = _daily_table("daily_kline")
index_daily_kline = _daily_table("index_daily_kline")

limit_up_record = Table(
    "limit_up_record",
    metadata,
    Column(
        "security_id",
        String(32),
        ForeignKey("security.security_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("trade_date", Date, nullable=False),
    Column("is_limit_up", Boolean, nullable=False),
    Column("touched_limit", Boolean),
    Column("first_seal_time", Time),
    Column("last_seal_time", Time),
    Column("break_count", Integer),
    Column("seal_amount", Numeric),
    Column("continuous_boards", Integer, nullable=False, server_default="0"),
    Column("source", String(50), nullable=False),
    Column("source_version", String(100), nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column(
        "source_archive_id",
        PGUUID(as_uuid=True),
        ForeignKey("import_batches.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    UniqueConstraint("security_id", "trade_date", "source", "source_version"),
    CheckConstraint("break_count IS NULL OR break_count >= 0"),
    CheckConstraint("seal_amount IS NULL OR seal_amount >= 0"),
    CheckConstraint("continuous_boards >= 0"),
    Index("limit_up_record_date_boards", "trade_date", "continuous_boards"),
)

market_import_tasks = Table(
    "market_import_tasks",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column(
        "source_id",
        Text,
        ForeignKey("source_grants.source_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("embedded_grant", JSONB, nullable=False),
    Column("status", String(20), nullable=False),
    Column("request_from", Date, nullable=False),
    Column("request_to", Date, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("status IN ('quarantined', 'succeeded')"),
)

market_import_attempts = Table(
    "market_import_attempts",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "attempt_sequence",
        BigInteger,
        Identity(always=True),
        nullable=False,
        unique=True,
    ),
    Column(
        "task_id",
        PGUUID(as_uuid=True),
        ForeignKey("market_import_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "batch_id",
        PGUUID(as_uuid=True),
        ForeignKey("import_batches.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("envelope_hash", String(64), nullable=False),
    Column(
        "source_id",
        Text,
        ForeignKey("source_grants.source_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("embedded_grant", JSONB, nullable=False),
    Column("status", String(20), nullable=False),
    Column("stage", String(80), nullable=False),
    Column("page_manifest", JSONB, nullable=False),
    Column("missing_items", JSONB, nullable=False),
    Column("failed_cursor", Text),
    Column("request_from", Date, nullable=False),
    Column("request_to", Date, nullable=False),
    Column("retry_from", Date),
    Column("retry_to", Date),
    Column("details", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("status IN ('quarantined', 'succeeded')"),
    Index("market_import_attempts_listing", "attempt_sequence"),
)
