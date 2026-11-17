"""Narrow public facade over publication-layer sharing authorization."""

from collections.abc import Callable
from datetime import UTC, datetime

from dragon_radar.publication.access import (
    PublicSharingAccessPolicy,
    grant_allows_public_sharing,
)
from dragon_radar.publication.repository import PublicationRepository
from dragon_radar.storage.repository import Repository

__all__ = [
    "PublicPublicationRepository",
    "grant_allows_public_sharing",
    "public_publication_repository",
]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PublicPublicationRepository:
    """Expose published reads without the legacy snapshot fallback."""

    def __init__(
        self,
        database,
        market_repository: Repository | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        repository_clock = clock or _utc_now
        self._repository = PublicationRepository(
            database,
            market_repository,
            clock=repository_clock,
            access_policy=PublicSharingAccessPolicy(repository_clock),
        )
        self.database = database

    def get_published_snapshot(self, snapshot_id):
        return self._repository.get_published_snapshot(snapshot_id)

    def get_snapshot(self, snapshot_id):
        return self.get_published_snapshot(snapshot_id)

    def list_snapshots(self, **filters):
        return self._repository.list_snapshots(**filters)

    def list_results(self, publication_id):
        return self._repository.list_results(publication_id)


def public_publication_repository(
    database,
    market_repository: Repository | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
) -> PublicPublicationRepository:
    return PublicPublicationRepository(
        database,
        market_repository,
        clock=clock,
    )
