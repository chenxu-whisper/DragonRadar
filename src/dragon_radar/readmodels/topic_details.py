"""Optional publication-time topic facts and fail-closed archive validation."""

from collections import defaultdict
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_serializer,
    model_validator,
)

from dragon_radar.domain.models import ExactDecimal, FrozenModel, UTCDateTime
from dragon_radar.domain.quality import state_at
from dragon_radar.topics.models import (
    SafeReference,
    TopicDefinition,
    TopicMembership,
)

TOPIC_DETAILS_SCHEMA = "dragon-radar.topic-details"
TOPIC_DETAILS_SCHEMA_VERSION = 1
Count = Annotated[StrictInt, Field(ge=0)]
Text = Annotated[StrictStr, Field(min_length=1)]


class _ArchiveModel(FrozenModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class _Coverage(_ArchiveModel):
    expected: Count
    valid: Count
    excluded: Count
    missing: Count

    @model_validator(mode="after")
    def counts(self):
        if self.expected != self.valid + self.excluded + self.missing:
            raise ValueError("Invalid aggregate coverage")
        return self


class _Ratio(_ArchiveModel):
    numerator: Count
    denominator: Count
    value: ExactDecimal | None
    reason: StrictStr | None

    @model_validator(mode="after")
    def counts(self):
        if self.numerator > self.denominator:
            raise ValueError("Invalid aggregate ratio")
        if self.value is not None and not 0 <= self.value <= 1:
            raise ValueError("Invalid aggregate ratio value")
        return self


class _Sector(_ArchiveModel):
    sector_id: Text
    name: Text
    member_count: Count
    valid_returns: Count
    up_ratio: _Ratio
    limit_up_ratio: _Ratio
    median_return: ExactDecimal | None
    change_ratio: ExactDecimal | None
    limit_up_count: Count | None
    net_inflow: ExactDecimal | None
    net_inflow_reason: StrictStr | None
    reason: StrictStr | None
    quality_flags: tuple[StrictStr, ...]
    coverage: _Coverage


class _Basis(_ArchiveModel):
    source: Text
    source_version: Text
    as_of: UTCDateTime
    source_time: UTCDateTime | None
    cutoff: UTCDateTime
    market_snapshot_id: UUID
    topic_snapshot_id: UUID
    taxonomy_version: Text
    membership_version: Text

    @field_serializer("market_snapshot_id", "topic_snapshot_id")
    def snapshot_ids(self, value):
        return str(value)


class _TopicMetadata(_ArchiveModel):
    snapshot_id: UUID
    batch_id: UUID
    source_version: Text
    taxonomy_version: Text
    membership_version: Text


class _Definition(_ArchiveModel):
    effective_from: date
    effective_to: date | None
    available_at: UTCDateTime
    source_reference: SafeReference


class _Member(_ArchiveModel):
    security_id: Text
    code: StrictStr | None
    name: StrictStr | None
    effective_from: date
    effective_to: date | None
    available_at: UTCDateTime
    evidence_refs: Annotated[tuple[SafeReference, ...], Field(min_length=1)]


class _Identity(_ArchiveModel):
    security_id: Text
    code: StrictStr | None
    name: Text
    exchange: Text


class _MembershipCoverage(_ArchiveModel):
    expected: Count | None
    received: Count | None
    known: Count
    status: Literal["complete", "unavailable"]
    reason: StrictStr | None
    complete_as_of: UTCDateTime | None

    @model_validator(mode="after")
    def counts(self):
        if self.complete_as_of is None:
            if self.expected is not None or self.received is not None:
                raise ValueError("Missing coverage must not invent counts")
        elif self.expected is None or self.received is None:
            raise ValueError("Coverage cell counts are required")
        if self.expected is not None and (
            self.received > self.expected or self.known > self.expected
        ):
            raise ValueError("Invalid membership coverage counts")
        if self.status == "complete":
            if (
                self.complete_as_of is None
                or not self.expected == self.received == self.known
                or self.reason is not None
            ):
                raise ValueError("Incomplete membership cannot claim completeness")
        elif not self.reason:
            raise ValueError("Unavailable membership needs a reason")
        return self


class _Detail(_ArchiveModel):
    sector: _Sector
    topic_basis: _Basis
    definition: _Definition
    membership_coverage: _MembershipCoverage
    members: tuple[_Member, ...]
    total: Count

    @model_validator(mode="after")
    def counts(self):
        ids = [member.security_id for member in self.members]
        if ids != sorted(set(ids)):
            raise ValueError("Members must be unique and deterministically ordered")
        if not (
            self.total
            == len(ids)
            == self.membership_coverage.known
            == self.sector.member_count
        ):
            raise ValueError("Member counts disagree")
        return self


class _Block(_ArchiveModel):
    schema_name: Literal["dragon-radar.topic-details"] = Field(alias="schema")
    schema_version: Annotated[StrictInt, Field(ge=1, le=1)]
    trade_date: date
    sectors: dict[Text, _Detail]


def _visible(fact, trade_date, cutoff):
    return (
        fact.available_at <= cutoff
        and fact.effective_from <= trade_date
        and (fact.effective_to is None or trade_date < fact.effective_to)
    )


def materialize_topic_details(
    batch,
    topic_batch,
    market_id,
    topic_id,
    sectors,
    archived_identities,
):
    """Freeze effective membership independently of returns or role readiness."""
    cutoff = min(batch.as_of, topic_batch.as_of)
    definitions = {
        fact.topic_id: fact
        for fact in topic_batch.topics
        if _visible(fact, batch.trade_date, cutoff)
    }
    memberships = defaultdict(list)
    for fact in topic_batch.memberships:
        if fact.topic_id in definitions and _visible(fact, batch.trade_date, cutoff):
            memberships[fact.topic_id].append(fact)
    cells = {
        cell.topic_id: cell
        for cell in topic_batch.coverage_cells
        if cell.trade_date == batch.trade_date and cell.complete_as_of <= cutoff
    }
    basis = {
        "source": topic_batch.source_id,
        "source_version": topic_batch.source_version,
        "as_of": topic_batch.as_of,
        "source_time": topic_batch.source_time,
        "cutoff": cutoff,
        "market_snapshot_id": market_id,
        "topic_snapshot_id": topic_id,
        "taxonomy_version": topic_batch.taxonomy_version,
        "membership_version": topic_batch.membership_version,
    }
    details = {}
    identity_ids = {item["security_id"] for item in archived_identities}
    for sector in sectors:
        topic = sector["sector_id"]
        definition = definitions[topic]
        members = []
        for fact in sorted(memberships[topic], key=lambda item: item.security_id):
            identity = state_at(batch, fact.security_id, batch.trade_date)
            if identity is not None and (
                identity.available_at > cutoff or fact.security_id not in identity_ids
            ):
                identity = None
            members.append(
                {
                    **fact.model_dump(exclude={"topic_id"}),
                    "code": identity.code if identity is not None else None,
                    "name": identity.name if identity is not None else None,
                }
            )
        cell = cells.get(topic)
        complete = (
            cell is not None
            and cell.status == "complete"
            and cell.expected_member_count == cell.received_member_count == len(members)
        )
        details[topic] = {
            "sector": sector,
            "topic_basis": basis,
            "definition": definition.model_dump(exclude={"topic_id", "name"}),
            "membership_coverage": {
                "expected": cell.expected_member_count if cell is not None else None,
                "received": cell.received_member_count if cell is not None else None,
                "known": len(members),
                "status": "complete" if complete else "unavailable",
                "reason": (
                    None
                    if complete
                    else (
                        cell.reason
                        if cell is not None and cell.reason
                        else "topic_coverage_unavailable"
                    )
                ),
                "complete_as_of": cell.complete_as_of if cell is not None else None,
            },
            "members": members,
            "total": len(members),
        }
    return _Block.model_validate(
        {
            "schema": TOPIC_DETAILS_SCHEMA,
            "schema_version": TOPIC_DETAILS_SCHEMA_VERSION,
            "trade_date": batch.trade_date,
            "sectors": details,
        }
    ).model_dump(by_alias=True)


def parse_archived_topic_details(payload, snapshot):
    """Validate frozen facts and publication bindings; never load topic inputs."""
    block = _Block.model_validate(payload["topic_details"])
    publication = snapshot.publication
    result = snapshot.result("market")
    topic_results = [item for item in snapshot.results if item.domain == "topic"]
    if len(topic_results) != 1:
        raise ValueError("Topic input metadata is missing or ambiguous")
    topic_result = topic_results[0]
    topic_metadata = _TopicMetadata.model_validate(topic_result.payload)
    if (
        block.trade_date != snapshot.trade_date
        or result.publication_id != publication.id
        or result.input_id != publication.market_snapshot_id
        or snapshot.market_snapshot.id != publication.market_snapshot_id
        or publication.topic_snapshot_id is None
        or topic_result.status != "ready"
        or topic_result.publication_id != publication.id
        or topic_result.input_id != publication.topic_snapshot_id
        or topic_metadata.snapshot_id != publication.topic_snapshot_id
    ):
        raise ValueError("Topic detail publication binding mismatch")
    aggregates = [
        _Sector.model_validate(item) for item in payload["sector_flow"]["sectors"]
    ]
    by_id = {sector.sector_id: sector for sector in aggregates}
    if len(by_id) != len(aggregates) or set(by_id) != set(block.sectors):
        raise ValueError("Topic detail sector set mismatch")
    identities = [
        _Identity.model_validate(item) for item in payload["search"]["securities"]
    ]
    by_security = {identity.security_id: identity for identity in identities}
    if len(by_security) != len(identities):
        raise ValueError("Archived market identities are ambiguous")
    archived_basis = payload["sector_flow"]["topic_basis"]
    for topic, detail in block.sectors.items():
        basis = detail.topic_basis
        if (
            detail.sector != by_id[topic]
            or basis.market_snapshot_id != publication.market_snapshot_id
            or basis.topic_snapshot_id != publication.topic_snapshot_id
            or basis.cutoff != min(snapshot.batch.as_of, basis.as_of)
        ):
            raise ValueError("Topic detail input binding mismatch")
        # Compare archived descriptors, not reconstructed supplier facts.
        descriptor = _Basis.model_validate(
            {
                **archived_basis,
                "market_snapshot_id": publication.market_snapshot_id,
                "topic_snapshot_id": publication.topic_snapshot_id,
                "taxonomy_version": topic_metadata.taxonomy_version,
                "membership_version": topic_metadata.membership_version,
            }
        )
        if descriptor != basis or basis.source_version != topic_metadata.source_version:
            raise ValueError("Topic descriptor mismatch")
        definition = TopicDefinition.model_validate(
            {
                **detail.definition.model_dump(),
                "topic_id": topic,
                "name": detail.sector.name,
            }
        )
        if not _visible(definition, block.trade_date, basis.cutoff):
            raise ValueError("Topic definition is not visible")
        coverage = detail.membership_coverage
        if (
            coverage.complete_as_of is not None
            and coverage.complete_as_of > basis.cutoff
        ):
            raise ValueError("Topic coverage is not visible")
        for member in detail.members:
            fact = TopicMembership.model_validate(
                {
                    **member.model_dump(exclude={"code", "name"}),
                    "topic_id": topic,
                }
            )
            if not _visible(fact, block.trade_date, basis.cutoff):
                raise ValueError("Topic member is not visible")
            if member.code is not None or member.name is not None:
                identity = by_security.get(member.security_id)
                if identity is None or (
                    member.code != identity.code or member.name != identity.name
                ):
                    raise ValueError("Topic identity disagrees with archived market")
    return block.model_dump()["sectors"]
