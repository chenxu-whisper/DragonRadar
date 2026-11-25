"""Explicit sidecar erasure dispatch after cutoff binding and seal completion."""

from sqlalchemy import select, update

from dragon_radar.public_daily_ai.tables import (
    promoted_news_summaries,
    public_daily_ai_archives,
)
from dragon_radar.publication.tables import (
    source_dependencies,
    withdrawal_binding_seals,
    withdrawal_dependencies,
    withdrawal_records,
)
from dragon_radar.storage.repository import Forbidden

TOMBSTONE = {"schema": "dragon-radar.ai-tombstone/v1", "erased": True}


def erase_artifact(connection, *, withdrawal_id, artifact_id, derived_type):
    """Parent retention calls in its existing transaction; never opens a database."""
    tables = {
        "daily_ai": public_daily_ai_archives,
        "news_summary": promoted_news_summaries,
    }
    if derived_type not in tables or not connection.in_transaction():
        raise ValueError("Invalid AI retention dispatch")
    bound = connection.scalar(
        select(withdrawal_records.c.id)
        .join(
            withdrawal_dependencies,
            withdrawal_dependencies.c.withdrawal_id == withdrawal_records.c.id,
        )
        .join(
            source_dependencies,
            source_dependencies.c.id == withdrawal_dependencies.c.source_dependency_id,
        )
        .join(
            withdrawal_binding_seals,
            withdrawal_binding_seals.c.withdrawal_id == withdrawal_records.c.id,
        )
        .where(
            withdrawal_records.c.id == withdrawal_id,
            withdrawal_records.c.scope == "storage",
            withdrawal_records.c.action.in_(("deleted", "deidentified")),
            source_dependencies.c.dependency_sequence
            <= withdrawal_records.c.cutoff_dependency_sequence,
            source_dependencies.c.derived_id == artifact_id,
            source_dependencies.c.derived_type == derived_type,
        )
        .limit(1)
    )
    if bound is None:
        raise Forbidden("Storage erasure requires a sealed cutoff binding")
    table = tables[derived_type]
    connection.execute(
        update(table).where(table.c.id == artifact_id).values(safe_payload=TOMBSTONE)
    )
