"""Use the real retention orchestration for sidecar payload erasure."""

from uuid import UUID

import pytest
from factories import NOW
from sqlalchemy import select
from test_b3_daily_ai_integration import accepted
from test_b3_daily_ai_integration import setup as setup

from dragon_radar.public_daily_ai.retention import TOMBSTONE
from dragon_radar.public_daily_ai.tables import public_daily_ai_archives
from dragon_radar.publication.models import RetentionPolicy
from dragon_radar.publication.retention import RetentionService
from dragon_radar.publication.tables import source_dependencies


@pytest.mark.parametrize("action", ["delete", "deidentify"])
def test_real_retention_dispatch_erases_daily_sidecar_without_rewriting_hash(
    setup,
    action,
):
    tasks, _service, session, _publication, promotion = setup
    task, command = accepted(setup)
    receipt = promotion.publish_daily(session.token, session.csrf, task.id, command)
    with tasks.database.engine.connect() as connection:
        source = connection.scalar(
            select(source_dependencies.c.source_id).where(
                source_dependencies.c.derived_id == UUID(receipt["archive_id"])
            )
        )
    retention = RetentionService(
        tasks.database,
        promotion.publications.market,
        clock=lambda: NOW,
    )
    withdrawal = retention.withdraw_storage(
        source,
        policy=RetentionPolicy(
            reference="synthetic-retention-policy",
            approved=True,
            action=action,
        ),
    )
    assert withdrawal.action == ("deleted" if action == "delete" else "deidentified")
    with tasks.database.engine.connect() as connection:
        row = (
            connection.execute(
                select(public_daily_ai_archives).where(
                    public_daily_ai_archives.c.id == UUID(receipt["archive_id"])
                )
            )
            .mappings()
            .one()
        )
    assert row["safe_payload"] == TOMBSTONE
    assert row["content_hash"] == receipt["content_hash"]
