"""SQLAlchemy mappings for immutable role and validation evidence."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
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

role_runs = Table(
    "role_runs",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("idempotency_key", String(200), nullable=False, unique=True),
    Column("payload_hash", String(64), nullable=False),
    Column(
        "market_snapshot_id",
        PGUUID(as_uuid=True),
        ForeignKey("snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column(
        "topic_snapshot_id",
        PGUUID(as_uuid=True),
        ForeignKey("topic_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("trade_date", Date, nullable=False),
    Column("knowledge_cutoff", DateTime(timezone=True), nullable=False),
    Column("rule_version", String(100), nullable=False),
    Column("rule_json", Text, nullable=False),
    Column("rule_hash", String(64), nullable=False),
    Column("benchmark_id", String(100), nullable=False),
    Column("visibility_mode", String(30), nullable=False),
    Column("market_source_id", Text, nullable=False),
    Column("market_source_version", String(100), nullable=False),
    Column("market_pool_version", String(100), nullable=False),
    Column("adjustment_version", String(100), nullable=False),
    Column("calendar_version", String(100), nullable=False),
    Column("topic_source_id", Text, nullable=False),
    Column("topic_source_version", String(100), nullable=False),
    Column("taxonomy_version", String(100), nullable=False),
    Column("membership_version", String(100), nullable=False),
    Column(
        "previous_comparable_run_id",
        PGUUID(as_uuid=True),
        ForeignKey("role_runs.id", ondelete="RESTRICT"),
    ),
    Column("status", String(20), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("materialized_at", DateTime(timezone=True)),
    CheckConstraint("status IN ('draft', 'materialized')"),
    CheckConstraint(
        "(status = 'draft' AND materialized_at IS NULL) OR "
        "(status = 'materialized' AND materialized_at IS NOT NULL)",
        name="role_runs_status_materialized_at",
    ),
    CheckConstraint("visibility_mode IN ('contemporaneous', 'retrospective')"),
)

role_run_topics = Table(
    "role_run_topics",
    metadata,
    Column(
        "role_run_id",
        PGUUID(as_uuid=True),
        ForeignKey("role_runs.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("topic_id", String(100), primary_key=True),
    Column("member_ids", JSONB, nullable=False),
    Column("member_identity", String(64), nullable=False),
)

role_topic_statuses = Table(
    "role_topic_statuses",
    metadata,
    Column(
        "role_run_id",
        PGUUID(as_uuid=True),
        ForeignKey("role_runs.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("topic_id", String(100), primary_key=True),
    Column("role", String(30), primary_key=True),
    Column("capability", String(30), nullable=False),
    Column("status", String(30), nullable=False),
    Column("expected", Integer, nullable=False),
    Column("valid", Integer, nullable=False),
    Column("legal_excluded", Integer, nullable=False),
    Column("missing", Integer, nullable=False),
    Column("reasons", JSONB, nullable=False),
    CheckConstraint("expected = valid + legal_excluded + missing"),
)

role_results = Table(
    "role_results",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "role_run_id",
        PGUUID(as_uuid=True),
        ForeignKey("role_runs.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("topic_id", String(100), nullable=False),
    Column("security_id", String(100), nullable=False),
    Column("observation_at", DateTime(timezone=True), nullable=False),
    Column("role", String(30), nullable=False),
    Column("rule_version", String(100), nullable=False),
    Column("internal_status", String(40), nullable=False),
    Column("tie_rank", Integer, nullable=False),
    Column("factors", JSONB, nullable=False),
    Column("reasons", JSONB, nullable=False),
    UniqueConstraint(
        "role_run_id",
        "topic_id",
        "security_id",
        "observation_at",
        "role",
        "rule_version",
        name="role_results_complete_business_key",
    ),
)

role_factor_observations = Table(
    "role_factor_observations",
    metadata,
    Column(
        "role_run_id",
        PGUUID(as_uuid=True),
        ForeignKey("role_runs.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("topic_id", String(100), primary_key=True),
    Column("security_id", String(100), primary_key=True),
    Column("factor_id", String(30), primary_key=True),
    Column("metric", String(100), primary_key=True),
    Column("window", String(100), primary_key=True),
    Column("value", JSONB),
    Column("unit", String(40), nullable=False),
    Column("coverage", JSONB, nullable=False),
    Column("reasons", JSONB, nullable=False),
    Column("metadata", JSONB, nullable=False),
)

role_limit_rows = Table(
    "role_limit_rows",
    metadata,
    Column(
        "role_run_id",
        PGUUID(as_uuid=True),
        ForeignKey("role_runs.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("topic_id", String(100), primary_key=True),
    Column("security_id", String(100), primary_key=True),
    Column("trade_date", Date, nullable=False),
    Column("closing_limit_streak", Integer),
    Column("limit_hits_10", Integer),
    Column("broken_board", Boolean),
    Column("touched_not_sealed", Boolean),
    Column("reasons", JSONB, nullable=False),
)

role_change_summaries = Table(
    "role_change_summaries",
    metadata,
    Column(
        "role_run_id",
        PGUUID(as_uuid=True),
        ForeignKey("role_runs.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("topic_id", String(100), primary_key=True),
    Column("role", String(30), primary_key=True),
    Column(
        "previous_role_run_id",
        PGUUID(as_uuid=True),
        ForeignKey("role_runs.id", ondelete="RESTRICT"),
    ),
    Column("comparison_status", String(30), nullable=False),
    Column("membership_changed", Boolean, nullable=False),
    Column("reasons", JSONB, nullable=False),
)

role_changes = Table(
    "role_changes",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "role_run_id",
        PGUUID(as_uuid=True),
        ForeignKey("role_runs.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("topic_id", String(100), nullable=False),
    Column("security_id", String(100), nullable=False),
    Column("role", String(30), nullable=False),
    Column("comparison_status", String(30), nullable=False),
    Column("change_type", String(30)),
    Column("membership_changed", Boolean, nullable=False),
    Column("reasons", JSONB, nullable=False),
    UniqueConstraint("role_run_id", "topic_id", "security_id", "role"),
)

role_pending_validations = Table(
    "role_pending_validations",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "pending_result_id",
        PGUUID(as_uuid=True),
        ForeignKey("role_results.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    ),
    Column(
        "observation_run_id",
        PGUUID(as_uuid=True),
        ForeignKey("role_runs.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("topic_id", String(100), nullable=False),
    Column("observation_date", Date, nullable=False),
    Column("validation_sessions", JSONB, nullable=False),
    Column("baseline_evidence", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

role_validations = Table(
    "role_validations",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column(
        "pending_validation_id",
        PGUUID(as_uuid=True),
        ForeignKey("role_pending_validations.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("revision", Integer, nullable=False),
    Column(
        "supersedes",
        PGUUID(as_uuid=True),
        ForeignKey("role_validations.id", ondelete="RESTRICT"),
    ),
    Column("outcome", String(30), nullable=False),
    Column("window_start", Date, nullable=False),
    Column("window_end", Date, nullable=False),
    Column("return_start", Date, nullable=False),
    Column("return_end", Date, nullable=False),
    Column("old_return_raw", Numeric),
    Column("new_return_raw", Numeric),
    Column("topic_median_raw", Numeric),
    Column("reasons", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("pending_validation_id", "revision"),
)

role_validation_inputs = Table(
    "role_validation_inputs",
    metadata,
    Column(
        "validation_id",
        PGUUID(as_uuid=True),
        ForeignKey("role_validations.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("position", Integer, primary_key=True),
    Column(
        "input_role_run_id",
        PGUUID(as_uuid=True),
        ForeignKey("role_runs.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    UniqueConstraint("validation_id", "input_role_run_id"),
)

role_materialization_attempts = Table(
    "role_materialization_attempts",
    metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("idempotency_key", String(200), nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column(
        "role_run_id",
        PGUUID(as_uuid=True),
        ForeignKey("role_runs.id", ondelete="RESTRICT"),
    ),
    Column("status", String(20), nullable=False),
    Column("stage", String(80), nullable=False),
    Column("details", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("status IN ('failed', 'materialized')"),
    CheckConstraint(
        "(status = 'materialized' AND role_run_id IS NOT NULL) OR "
        "(status = 'failed' AND role_run_id IS NULL)"
    ),
)

__all__ = [
    "role_change_summaries",
    "role_changes",
    "role_factor_observations",
    "role_limit_rows",
    "role_materialization_attempts",
    "role_pending_validations",
    "role_results",
    "role_run_topics",
    "role_runs",
    "role_topic_statuses",
    "role_validation_inputs",
    "role_validations",
]
