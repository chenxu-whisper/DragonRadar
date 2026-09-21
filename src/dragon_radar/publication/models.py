"""Contracts for versioned workbench publications."""

import json
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue, StrictBool, field_validator, model_validator

from dragon_radar.domain.models import FrozenModel
from dragon_radar.storage.models import Snapshot
from dragon_radar.topics.models import ImmutableJsonObject

DOMAIN_NAMES = ("market", "topic", "news", "regulation", "role")
DomainName = Literal["market", "topic", "news", "regulation", "role"]
DomainState = Literal["ready", "missing", "disabled"]


class PublicationCommand(FrozenModel):
    trade_date: date
    market_snapshot_id: UUID
    topic_snapshot_id: UUID | None = None
    news_input_id: UUID | None = None
    regulation_input_id: UUID | None = None
    role_input_id: UUID | None = None
    disabled_domains: tuple[DomainName, ...] = ()

    @field_validator("disabled_domains")
    @classmethod
    def unique_disabled_domains(
        cls, value: tuple[DomainName, ...]
    ) -> tuple[DomainName, ...]:
        if len(set(value)) != len(value) or "market" in value:
            raise ValueError("Invalid disabled domains")
        return tuple(sorted(value, key=DOMAIN_NAMES.index))

    @model_validator(mode="after")
    def disabled_domains_have_no_input(self):
        references = self.input_references()
        if any(references[domain] is not None for domain in self.disabled_domains):
            raise ValueError("Disabled domains cannot have input references")
        return self

    def input_references(self) -> dict[DomainName, UUID | None]:
        return {
            "market": self.market_snapshot_id,
            "topic": self.topic_snapshot_id,
            "news": self.news_input_id,
            "regulation": self.regulation_input_id,
            "role": self.role_input_id,
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )


class DomainMaterialization(FrozenModel):
    status: DomainState = "ready"
    payload: ImmutableJsonObject = {}
    coverage: ImmutableJsonObject = {}
    quality_flags: tuple[str, ...] = ()


class PublicationResult(FrozenModel):
    publication_id: UUID
    domain: DomainName
    input_id: UUID | None
    status: DomainState
    payload: ImmutableJsonObject
    coverage: ImmutableJsonObject
    quality_flags: tuple[str, ...]


class Publication(FrozenModel):
    id: UUID
    trade_date: date
    version: Annotated[int, Field(ge=1)]
    supersedes: UUID | None
    status: Literal["published", "revoked"]
    market_snapshot_id: UUID
    topic_snapshot_id: UUID | None
    news_input_id: UUID | None
    regulation_input_id: UUID | None
    role_input_id: UUID | None
    domains: dict[DomainName, DomainState]
    coverage: ImmutableJsonObject
    quality_flags: tuple[str, ...]
    command_json: str
    published_at: datetime


class RetentionPolicy(FrozenModel):
    reference: Annotated[str, Field(min_length=1, max_length=200)]
    approved: StrictBool
    action: Literal["deidentify", "delete"]


class WithdrawalRecord(FrozenModel):
    id: UUID
    sequence: Annotated[int, Field(ge=1)]
    source_id: str
    scope: Literal["display", "storage"]
    action: Literal["blocked", "deidentified", "deleted", "replayed", "restored"]
    policy_reference: str | None
    cutoff_dependency_sequence: Annotated[int, Field(ge=0)] | None
    details: ImmutableJsonObject
    occurred_at: datetime


class PublishedSnapshot:
    """Legacy market projection selected by a unified publication ID."""

    def __init__(
        self,
        publication: Publication,
        market_snapshot: Snapshot,
        results: tuple[PublicationResult, ...],
    ):
        self.publication = publication
        self.market_snapshot = market_snapshot
        self.results = results

    def result(self, domain: DomainName) -> PublicationResult:
        return next(result for result in self.results if result.domain == domain)

    @property
    def id(self) -> UUID:
        return self.publication.id

    @property
    def batch_id(self) -> UUID:
        return self.market_snapshot.batch_id

    @property
    def revision(self) -> int:
        return self.publication.version

    @property
    def supersedes(self) -> UUID | None:
        return self.publication.supersedes

    @property
    def published_at(self) -> datetime:
        return self.publication.published_at

    @property
    def manifest(self) -> dict[str, JsonValue]:
        return self.market_snapshot.manifest

    @property
    def batch(self):
        return self.market_snapshot.batch

    @property
    def quality_report(self) -> dict:
        return self.market_snapshot.quality_report

    @property
    def trade_date(self) -> date:
        return self.publication.trade_date
