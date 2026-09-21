"""Licensed market ingestion attempts and normalized market read models."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0006_market_readmodels"
down_revision = "0005_publications"
branch_labels = None
depends_on = None


def _lineage_columns():
    return (
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("source_version", sa.String(100), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "source_archive_id",
            UUID(as_uuid=True),
            sa.ForeignKey("import_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
    )


def _immutable(table):
    op.execute(f"""
        CREATE TRIGGER {table}_immutable
        BEFORE UPDATE OR DELETE ON {table}
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
        CREATE TRIGGER {table}_no_truncate
        BEFORE TRUNCATE ON {table}
        FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
    """)


def _create_daily_table(name):
    op.create_table(
        name,
        sa.Column(
            "security_id",
            sa.String(32),
            sa.ForeignKey("security.security_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("open", sa.Numeric),
        sa.Column("high", sa.Numeric),
        sa.Column("low", sa.Numeric),
        sa.Column("close", sa.Numeric, nullable=False),
        sa.Column("volume", sa.Numeric),
        sa.Column("amount", sa.Numeric),
        sa.Column("change_ratio", sa.Numeric),
        sa.Column("adj_factor", sa.Numeric, nullable=False),
        sa.Column("limit_up_price", sa.Numeric),
        sa.Column("limit_down_price", sa.Numeric),
        sa.Column("turnover_rate", sa.Numeric),
        sa.Column("float_mv", sa.Numeric),
        sa.Column("total_mv", sa.Numeric),
        sa.Column("currency", sa.String(10), nullable=False),
        sa.Column("volume_unit", sa.String(20), nullable=False),
        *_lineage_columns(),
        sa.UniqueConstraint("security_id", "trade_date", "source", "source_version"),
        sa.CheckConstraint("close > 0"),
        sa.CheckConstraint("volume IS NULL OR volume >= 0"),
        sa.CheckConstraint("amount IS NULL OR amount >= 0"),
        sa.CheckConstraint("turnover_rate IS NULL OR turnover_rate >= 0"),
        sa.CheckConstraint("float_mv IS NULL OR float_mv >= 0"),
        sa.CheckConstraint("total_mv IS NULL OR total_mv >= 0"),
        sa.CheckConstraint("adj_factor > 0"),
    )
    op.create_index(f"{name}_trade_date", name, ["trade_date"])


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "security",
        sa.Column("security_id", sa.String(32), primary_key=True),
        sa.Column("code", sa.String(10), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("security_type", sa.String(30), nullable=False),
        sa.Column("exchange", sa.String(10), nullable=False),
        sa.Column("board", sa.String(20), nullable=False),
        sa.Column("list_date", sa.Date, nullable=False),
        sa.Column("delist_date", sa.Date),
        sa.Column("is_st", sa.Boolean, nullable=False),
        sa.Column("is_suspended", sa.Boolean, nullable=False),
        *_lineage_columns(),
    )
    op.create_index("security_exchange_code", "security", ["exchange", "code"])
    op.create_index("security_exchange_board", "security", ["exchange", "board"])
    op.create_table(
        "security_state_history",
        sa.Column(
            "history_id",
            sa.BigInteger,
            sa.Identity(always=True),
            primary_key=True,
        ),
        sa.Column(
            "security_id",
            sa.String(32),
            sa.ForeignKey("security.security_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("state_type", sa.String(20), nullable=False),
        sa.Column("old_value", sa.String(100)),
        sa.Column("new_value", sa.String(100)),
        sa.Column("valid_from", sa.Date, nullable=False),
        sa.Column("valid_to", sa.Date),
        *_lineage_columns(),
        sa.UniqueConstraint("security_id", "state_type", "valid_from"),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from"),
    )
    op.execute("""
        ALTER TABLE security_state_history
        ADD CONSTRAINT security_state_history_no_overlap
        EXCLUDE USING gist (
            security_id WITH =,
            state_type WITH =,
            daterange(valid_from, valid_to, '[)') WITH &&
        )
    """)
    op.create_index(
        "security_state_history_security_type",
        "security_state_history",
        ["security_id", "state_type"],
    )
    _create_daily_table("daily_kline")
    _create_daily_table("index_daily_kline")
    op.create_table(
        "limit_up_record",
        sa.Column(
            "security_id",
            sa.String(32),
            sa.ForeignKey("security.security_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("trade_date", sa.Date, primary_key=True),
        sa.Column("is_limit_up", sa.Boolean, nullable=False),
        sa.Column("touched_limit", sa.Boolean),
        sa.Column("first_seal_time", sa.Time),
        sa.Column("last_seal_time", sa.Time),
        sa.Column("break_count", sa.Integer),
        sa.Column("seal_amount", sa.Numeric),
        sa.Column("continuous_boards", sa.Integer, nullable=False, server_default="0"),
        *_lineage_columns(),
        sa.CheckConstraint("break_count IS NULL OR break_count >= 0"),
        sa.CheckConstraint("seal_amount IS NULL OR seal_amount >= 0"),
        sa.CheckConstraint("continuous_boards >= 0"),
    )
    op.create_index(
        "limit_up_record_date_boards",
        "limit_up_record",
        ["trade_date", "continuous_boards"],
    )
    op.create_table(
        "market_import_tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        sa.Column(
            "source_id",
            sa.Text,
            sa.ForeignKey("source_grants.source_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("embedded_grant", JSONB, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("request_from", sa.Date, nullable=False),
        sa.Column("request_to", sa.Date, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('quarantined', 'succeeded')"),
    )
    op.create_table(
        "market_import_attempts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "attempt_sequence",
            sa.BigInteger,
            sa.Identity(always=True),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "task_id",
            UUID(as_uuid=True),
            sa.ForeignKey("market_import_tasks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "batch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("import_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column(
            "source_id",
            sa.Text,
            sa.ForeignKey("source_grants.source_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("embedded_grant", JSONB, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(80), nullable=False),
        sa.Column("page_manifest", JSONB, nullable=False),
        sa.Column("missing_items", JSONB, nullable=False),
        sa.Column("failed_cursor", sa.Text),
        sa.Column("request_from", sa.Date, nullable=False),
        sa.Column("request_to", sa.Date, nullable=False),
        sa.Column("retry_from", sa.Date),
        sa.Column("retry_to", sa.Date),
        sa.Column("details", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('quarantined', 'succeeded')"),
    )
    op.create_index(
        "market_import_attempts_listing",
        "market_import_attempts",
        [sa.text("attempt_sequence DESC")],
    )
    for table in (
        "security_state_history",
        "daily_kline",
        "index_daily_kline",
        "limit_up_record",
        "market_import_attempts",
    ):
        _immutable(table)


def downgrade():
    raise RuntimeError("Licensed market history cannot be destructively downgraded")
