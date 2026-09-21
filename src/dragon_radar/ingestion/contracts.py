"""Contracts for explicitly licensed, paginated local market JSON."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictStr

from dragon_radar.domain.models import FrozenModel, Identifier, MarketBatch, UTCDateTime

SHA256 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


class MarketManifestPage(FrozenModel):
    page_id: Identifier
    request_cursor: str | None
    next_cursor: str | None
    page_hash: SHA256
    received_row_count: Annotated[int, Field(ge=0)]


class MarketPageManifest(FrozenModel):
    request_from: date
    request_to: date
    pages: tuple[MarketManifestPage, ...]
    declared_total: Annotated[int, Field(ge=0)]
    terminal_cursor: str | None


class LicensedMarketBatch(FrozenModel):
    """Local-only envelope; the embedded grant is still rechecked at operation time."""

    batch: MarketBatch
    page_manifest: MarketPageManifest


class MarketImportAttempt(FrozenModel):
    id: UUID
    task_id: UUID
    batch_id: UUID
    status: Literal["quarantined", "succeeded"]
    stage: Identifier
    missing_items: tuple[str, ...] = ()
    failed_cursor: str | None = None
    request_from: date
    request_to: date
    retry_from: date | None = None
    retry_to: date | None = None
    created_at: UTCDateTime


class MarketImportTask(FrozenModel):
    id: UUID
    status: Literal["quarantined", "succeeded"]
    source_id: Identifier
    request_from: date
    request_to: date
    created_at: UTCDateTime


class MarketIngestionResult(FrozenModel):
    task_id: UUID
    attempt_id: UUID
    batch_id: UUID
    status: Literal["quarantined", "succeeded"]
