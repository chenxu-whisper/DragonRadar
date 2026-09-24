"""SQLAlchemy mappings for append-only regulation evidence."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from dragon_radar.storage.models import metadata

regulation_rules = Table(
    "regulation_rules",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("rule_id", String(30), nullable=False),
    Column("rule_version", String(20), nullable=False),
    Column("applicable_exchange", String(100)),
    Column("applicable_board", String(100)),
    Column("security_status", String(100)),
    Column("effective_from", Date, nullable=False),
    Column("effective_to", Date),
    Column("window_days", Integer, nullable=False),
    Column("direction", String(10), nullable=False),
    Column("threshold", Numeric, nullable=False),
    Column("warning_threshold", Numeric, nullable=False),
    Column("benchmark_index", String(100)),
    Column("normal_anomaly_count_mode", String(100)),
    Column("recompute_condition", String(100)),
    Column("calculation_method", String(100), nullable=False),
    Column("official_source_url", Text),
    Column("review_switch", String(20), nullable=False),
    Column("review_record", JSONB),
    Column("test_cases", JSONB, nullable=False),
    Column("regression_passed", Boolean, nullable=False),
    Column("active", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("rule_id", "rule_version"),
    CheckConstraint("window_days > 0"),
    CheckConstraint("direction IN ('upper', 'lower')"),
    CheckConstraint("review_switch IN ('pending', 'approved', 'rejected')"),
    CheckConstraint(
        "(direction = 'upper' AND warning_threshold < threshold) "
        "OR (direction = 'lower' AND warning_threshold > threshold)",
        name="regulation_rules_warning_order",
    ),
    Index(
        "regulation_rules_effective",
        "effective_from",
        "effective_to",
        "rule_id",
    ),
)

regulation_rule_statuses = Table(
    "regulation_rule_statuses",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "sequence",
        BigInteger,
        Identity(always=True),
        nullable=False,
        unique=True,
    ),
    Column(
        "rule_record_id",
        PGUUID(as_uuid=True),
        ForeignKey("regulation_rules.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("status", String(20), nullable=False),
    Column("reason", Text, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("status IN ('active', 'withdrawn')"),
    Index("regulation_rule_statuses_rule", "rule_record_id", "sequence"),
)

regulation_events = Table(
    "regulation_events",
    metadata,
    Column("event_id", String(32), primary_key=True),
    Column("rule_id", String(30), nullable=False),
    Column("rule_version", String(20), nullable=False),
    Column("security_id", String(32), nullable=False),
    Column("event_type", String(20), nullable=False),
    Column("announcement_id", String(50)),
    Column("announcement_url", Text),
    Column("effective_from", Date, nullable=False),
    Column("effective_to", Date),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["rule_id", "rule_version"],
        ["regulation_rules.rule_id", "regulation_rules.rule_version"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("event_id", "announcement_id"),
    CheckConstraint(
        "event_type IN ('abnormal', 'serious_abnormal', 'monitoring', 'restriction')"
    ),
    Index(
        "regulation_events_security_effective",
        "security_id",
        "effective_from",
    ),
)

regulation_snapshots = Table(
    "regulation_snapshots",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("command_hash", String(64), nullable=False),
    Column(
        "market_snapshot_id",
        PGUUID(as_uuid=True),
        ForeignKey("snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("trade_date", Date, nullable=False),
    Column("as_of", DateTime(timezone=True), nullable=False),
    Column("status_sequence_watermark", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("regulation_snapshots_market", "market_snapshot_id"),
)

regulation_snapshot_rules = Table(
    "regulation_snapshot_rules",
    metadata,
    Column(
        "snapshot_id",
        PGUUID(as_uuid=True),
        ForeignKey("regulation_snapshots.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column(
        "rule_record_id",
        PGUUID(as_uuid=True),
        ForeignKey("regulation_rules.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("position", Integer, nullable=False),
    Column("active", Boolean, nullable=False),
    UniqueConstraint("snapshot_id", "position"),
    CheckConstraint("position >= 0"),
)

regulation_snapshot_inputs = Table(
    "regulation_snapshot_inputs",
    metadata,
    Column(
        "snapshot_id",
        PGUUID(as_uuid=True),
        ForeignKey("regulation_snapshots.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column(
        "rule_record_id",
        PGUUID(as_uuid=True),
        ForeignKey("regulation_rules.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("security_id", String(100), primary_key=True),
    Column("trade_date", Date, nullable=False),
    Column("exchange", String(100), nullable=False),
    Column("board", String(100), nullable=False),
    Column("security_status", String(100), nullable=False),
    Column("computed_value", Numeric),
    Column("input_state", String(20), nullable=False),
    CheckConstraint("input_state IN ('complete', 'missing', 'conflict')"),
)

regulation_calculations = Table(
    "regulation_calculations",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "snapshot_id",
        PGUUID(as_uuid=True),
        ForeignKey("regulation_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "rule_record_id",
        PGUUID(as_uuid=True),
        ForeignKey("regulation_rules.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("security_id", String(100), nullable=False),
    Column("trade_date", Date, nullable=False),
    Column("computed_value", Numeric),
    Column("input_state", String(20), nullable=False),
    Column("state", String(30), nullable=False),
    Column("reasons", JSONB, nullable=False),
    UniqueConstraint("snapshot_id", "rule_record_id", "security_id"),
    ForeignKeyConstraint(
        ["snapshot_id", "rule_record_id", "security_id"],
        [
            "regulation_snapshot_inputs.snapshot_id",
            "regulation_snapshot_inputs.rule_record_id",
            "regulation_snapshot_inputs.security_id",
        ],
        ondelete="RESTRICT",
    ),
    CheckConstraint("input_state IN ('complete', 'missing', 'conflict')"),
    CheckConstraint("state IN ('pending', 'idle', 'approaching_alert', 'triggered')"),
    CheckConstraint(
        "(state = 'pending' AND jsonb_array_length(reasons) > 0) "
        "OR (state <> 'pending' AND jsonb_array_length(reasons) = 0)",
        name="regulation_calculations_pending_reason",
    ),
    Index("regulation_calculations_snapshot", "snapshot_id", "security_id"),
)

regulation_snapshot_events = Table(
    "regulation_snapshot_events",
    metadata,
    Column(
        "snapshot_id",
        PGUUID(as_uuid=True),
        ForeignKey("regulation_snapshots.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column(
        "event_id",
        String(32),
        ForeignKey("regulation_events.event_id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("position", Integer, nullable=False),
    UniqueConstraint("snapshot_id", "position"),
    CheckConstraint("position >= 0"),
)

regulation_commands = Table(
    "regulation_commands",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("payload_hash", String(64), nullable=False),
    Column("result_type", String(30), nullable=False),
    Column("result_id", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "result_type IN ('rule', 'event', 'status', 'snapshot')",
        name="regulation_commands_result_type",
    ),
)

__all__ = [
    "regulation_calculations",
    "regulation_commands",
    "regulation_events",
    "regulation_rule_statuses",
    "regulation_rules",
    "regulation_snapshot_events",
    "regulation_snapshot_inputs",
    "regulation_snapshot_rules",
    "regulation_snapshots",
]
