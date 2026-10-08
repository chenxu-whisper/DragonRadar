"""Access policies for reads of immutable unified publications."""

from collections.abc import Callable
from datetime import datetime
from typing import Literal, Protocol

from dragon_radar.domain.models import SourceGrant
from dragon_radar.domain.quality import (
    grant_allows_display,
    grant_allows_local,
)

DependencyKind = Literal["market", "topic", "news", "regulation"]


def grant_allows_public_sharing(
    grant: SourceGrant,
    *,
    source_id: str,
    now: datetime,
) -> bool:
    """Require provenance approval, a bounded grant, and explicit sharing."""
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


class PublicationAccessPolicy(Protocol):
    """Authorize the current and immutable grant for one dependency."""

    def allows(
        self,
        kind: DependencyKind,
        *,
        source_id: str,
        current: SourceGrant,
        embedded: SourceGrant | None,
    ) -> bool: ...


class LocalPublicationAccessPolicy:
    """Preserve the existing local publication-read predicates."""

    def __init__(self, clock: Callable[[], datetime]):
        self.clock = clock

    def allows(
        self,
        kind: DependencyKind,
        *,
        source_id: str,
        current: SourceGrant,
        embedded: SourceGrant | None,
    ) -> bool:
        now = self.clock()
        if kind == "regulation":
            return grant_allows_display(current, source_id=source_id, now=now)
        if embedded is None:
            return False
        predicate = grant_allows_local if kind == "topic" else grant_allows_display
        return all(
            predicate(grant, source_id=source_id, now=now)
            for grant in (current, embedded)
        )


class PublicSharingAccessPolicy:
    """Fail closed unless current and embedded grants both permit sharing."""

    def __init__(self, clock: Callable[[], datetime]):
        self.clock = clock

    def allows(
        self,
        kind: DependencyKind,
        *,
        source_id: str,
        current: SourceGrant,
        embedded: SourceGrant | None,
    ) -> bool:
        if embedded is None:
            return False
        now = self.clock()
        return all(
            grant_allows_public_sharing(grant, source_id=source_id, now=now)
            for grant in (current, embedded)
        )
