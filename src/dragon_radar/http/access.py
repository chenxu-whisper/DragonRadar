"""Fail-closed public access to explicitly shared unified publications."""

from datetime import datetime

from sqlalchemy import select

from dragon_radar.domain.models import SourceGrant
from dragon_radar.publication.repository import (
    PublicationRepository,
    _publication,
)
from dragon_radar.publication.tables import publications
from dragon_radar.storage.repository import Forbidden, Repository, _uuid


def grant_allows_public_sharing(
    grant: SourceGrant,
    *,
    source_id: str,
    now: datetime,
) -> bool:
    """Require current provenance approval and the independent sharing right."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("operation time requires a timezone")
    return bool(
        grant.source_id == source_id
        and grant.approved
        and grant.reference
        and grant.starts_at is not None
        and grant.expires_at is not None
        and grant.starts_at <= now < grant.expires_at
        and grant.sharing
    )


class PublicPublicationRepository(PublicationRepository):
    """Published read facade with current sharing checks and no legacy fallback."""

    def __init__(
        self,
        database,
        market_repository: Repository | None = None,
        *,
        clock=None,
    ):
        super().__init__(database, market_repository, clock=clock)

    def _authorized_snapshot(self, record, dependencies, grants, **kwargs):
        source_ids = {dependency["source_id"] for dependency in dependencies}
        operation_now = self.clock()
        if not source_ids or not all(
            grant_allows_public_sharing(
                grants[source_id],
                source_id=source_id,
                now=operation_now,
            )
            for source_id in source_ids
        ):
            raise Forbidden("Publication is not authorized for public sharing")
        return super()._authorized_snapshot(
            record,
            dependencies,
            grants,
            require_local_display=False,
            **kwargs,
        )

    def get_snapshot(self, snapshot_id):
        snapshot_id = _uuid(snapshot_id)
        with self.database.engine.begin() as connection:
            row = (
                connection.execute(
                    select(publications).where(publications.c.id == snapshot_id)
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            record = _publication(row)
            if record.status != "published":
                raise Forbidden("Publication is not available")
            return self._authorized_records(connection, [row])[0]
