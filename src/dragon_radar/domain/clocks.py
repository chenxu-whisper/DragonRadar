"""Immutable clock semantics, independent of daily payload and PIT contracts."""

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictStr,
    model_validator,
)

CLOCK_NAMES = (
    "source_event_at",
    "provider_published_at",
    "first_received_at",
    "computed_at",
    "snapshot_published_at",
    "page_updated_at",
)
DAILY_DOMAINS = ("market", "topic", "news", "regulation", "role")
ClockReason = Literal[
    "legacy_clock_unavailable",
    "input_clock_unavailable",
    "domain_missing",
    "domain_disabled",
    "browser_clock",
    "domain_clock_not_applicable",
]


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Clock timezone is required")
    return value.astimezone(UTC)


def timestamp_input(value):
    if isinstance(value, str):
        return utc(datetime.fromisoformat(value))
    if not isinstance(value, (datetime, str)):
        raise ValueError("Clock requires an explicit timezone-aware timestamp")
    return value


Timestamp = Annotated[datetime, BeforeValidator(timestamp_input), AfterValidator(utc)]
Digest = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")]


class ClockModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class ClockCell(ClockModel):
    state: Literal["known", "mixed", "unknown", "not_applicable"]
    at: Timestamp | None
    first_at: Timestamp | None
    last_at: Timestamp | None
    reason: ClockReason | None

    @model_validator(mode="after")
    def coherent(self):
        if (self.first_at is None) != (self.last_at is None):
            raise ValueError("Clock range endpoints must agree")
        if self.first_at is not None and self.first_at > self.last_at:
            raise ValueError("Clock range is reversed")
        if self.state == "known":
            if self.at is None or not self.at == self.first_at == self.last_at:
                raise ValueError("Known clock requires identical timestamps")
        elif self.at is not None:
            raise ValueError("Non-scalar clock cannot have at")
        if self.state == "mixed":
            if self.first_at is None or self.first_at == self.last_at:
                raise ValueError("Mixed clock requires a real range")
        if self.state == "not_applicable" and self.first_at is not None:
            raise ValueError("Not applicable clock cannot have timestamps")
        if (self.state in ("known", "mixed")) != (self.reason is None):
            raise ValueError("Clock reason does not match state")
        return self


class InputClockReference(ClockModel):
    source_id: Identifier
    archive_type: Identifier
    archive_id: Identifier
    revision_key: Identifier
    source_version: Identifier
    input_sha256: Digest
    evidence_sha256: Digest | None
    source_event_at: Timestamp | None
    provider_published_at: Timestamp | None
    first_received_at: Timestamp | None

    def key(self) -> tuple[str, str, str, str]:
        return self.source_id, self.archive_type, self.archive_id, self.revision_key

    @model_validator(mode="after")
    def evidenced(self):
        if self.evidence_sha256 is None and any(
            value is not None
            for value in (
                self.source_event_at,
                self.provider_published_at,
                self.first_received_at,
            )
        ):
            raise ValueError("Observed input clocks require evidence")
        if (
            self.provider_published_at is not None
            and self.first_received_at is not None
            and self.first_received_at < self.provider_published_at
        ):
            raise ValueError("Receipt precedes attested provider publication")
        return self


class InputClockEvidence(InputClockReference):
    evidence_sha256: Digest

    @model_validator(mode="after")
    def observed(self):
        if all(
            value is None
            for value in (
                self.source_event_at,
                self.provider_published_at,
                self.first_received_at,
            )
        ):
            raise ValueError("Input evidence requires an actual observation")
        return self


class DomainClockArchive(ClockModel):
    schema_name: Literal["dragon-radar.domain-clocks/v1"] = Field(
        default="dragon-radar.domain-clocks/v1", alias="schema"
    )
    domain: Literal["market", "topic", "news", "regulation", "role"]
    input_id: UUID | None
    as_of: Timestamp | None
    status: Literal["ready", "missing", "disabled"]
    inputs: Annotated[tuple[InputClockReference, ...], Field(max_length=10000)]
    computed_at: Timestamp | None

    @model_validator(mode="after")
    def coherent(self):
        keys = [item.key() for item in self.inputs]
        if keys != sorted(set(keys)):
            raise ValueError("Clock inputs must be sorted and unique")
        if self.status != "ready" and (
            self.input_id is not None or self.inputs or self.computed_at is not None
        ):
            raise ValueError("Missing/disabled domain cannot claim computation")
        if self.status == "ready" and self.input_id is None:
            raise ValueError("Ready domain requires its actual input")
        if self.computed_at is not None and any(
            item.first_received_at is not None
            and item.first_received_at > self.computed_at
            for item in self.inputs
        ):
            raise ValueError("Completion precedes attested receipt")
        return self


def aggregate_clock(
    values: Iterable[datetime | None], *, reason: ClockReason
) -> ClockCell:
    values = tuple(utc(item) if item is not None else None for item in values)
    known = tuple(item for item in values if item is not None)
    first, last = (min(known), max(known)) if known else (None, None)
    if not values or len(known) != len(values):
        return ClockCell(
            state="unknown", at=None, first_at=first, last_at=last, reason=reason
        )
    return ClockCell(
        state="known" if first == last else "mixed",
        at=first if first == last else None,
        first_at=first,
        last_at=last,
        reason=None,
    )


def scalar_clock(
    value: datetime | None, *, reason: ClockReason = "legacy_clock_unavailable"
) -> ClockCell:
    return aggregate_clock((value,), reason=reason)


def not_applicable(reason: ClockReason) -> ClockCell:
    return ClockCell(
        state="not_applicable", at=None, first_at=None, last_at=None, reason=reason
    )


def domain_cells(archive: DomainClockArchive, *, published_at: datetime) -> dict:
    reason: ClockReason = (
        "input_clock_unavailable"
        if archive.status == "ready"
        else "domain_" + archive.status
    )
    cells = {
        name: aggregate_clock(
            (getattr(item, name) for item in archive.inputs), reason=reason
        )
        for name in CLOCK_NAMES[:3]
    }
    cells["computed_at"] = scalar_clock(
        archive.computed_at,
        reason="legacy_clock_unavailable" if archive.status == "ready" else reason,
    )
    cells["snapshot_published_at"] = scalar_clock(published_at)
    cells["page_updated_at"] = not_applicable("browser_clock")
    return cells


def validate_publication_time(
    completions: Iterable[datetime | None], published_at: datetime
) -> datetime:
    published_at = utc(published_at)
    if any(utc(item) > published_at for item in completions if item is not None):
        raise ValueError("Backward publication clock")
    return published_at


def clock_delays(cells: Mapping[str, ClockCell]) -> dict:
    pairs = {
        "provider_minus_event": ("source_event_at", "provider_published_at"),
        "receipt_minus_provider": ("provider_published_at", "first_received_at"),
        "completion_minus_receipt": ("first_received_at", "computed_at"),
        "publication_minus_completion": ("computed_at", "snapshot_published_at"),
    }
    result = {}
    for name, (start, end) in pairs.items():
        left, right = cells[start], cells[end]
        if left.state not in ("known", "mixed") or right.state != "known":
            result[name] = {"milliseconds": None, "reason": "clock_unavailable"}
            continue
        # Mixed source ranges do not prove per-object supplier causal delays.
        if left.state == "mixed" and name != "completion_minus_receipt":
            result[name] = {"milliseconds": None, "reason": "mixed_clock"}
            continue
        delta = right.at - left.last_at
        microseconds = (
            delta.days * 86400 + delta.seconds
        ) * 1_000_000 + delta.microseconds
        result[name] = {
            "milliseconds": microseconds // 1000 if microseconds >= 0 else None,
            "reason": None if microseconds >= 0 else "negative_delay",
        }
    return result
