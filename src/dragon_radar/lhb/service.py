"""Pure point-in-time revision selection and null-safe LHB seat aggregation."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)

from dragon_radar.lhb.models import (
    AggregatedSeat,
    AggregationCoverage,
    DisclosureRevisionInput,
    LHBImportBatch,
    SeatAggregation,
    SeatCompleteness,
    SeatContribution,
)


def _decimal_context() -> Context:
    return Context(
        prec=34,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


@dataclass(slots=True)
class _SeatAccumulator:
    seat_id: str
    source_id: str
    source_version: str
    mapping_version: str
    source_seat_name: str
    currency: str
    unit: str
    header_revision: int
    header_available_at: datetime
    buy_amounts: list[Decimal] = field(default_factory=list)
    sell_amounts: list[Decimal] = field(default_factory=list)
    contributions: list[SeatContribution] = field(default_factory=list)
    row_count: int = 0


def validate_import_batch(batch: LHBImportBatch) -> LHBImportBatch:
    """Re-validate a typed batch at the trust boundary before storage.

    Pydantic construction enforces field-level and per-row contracts; this
    boundary adds the cross-row invariants of one import envelope:
    every revision belongs to the envelope's source/version, ``source_row_id``
    is unique across the whole batch (a row can never be double-counted), and
    ``seat_id`` is unique within each revision (the canonical adapter supplies
    one merged buy/sell row per seat per revision).
    """
    if not isinstance(batch, LHBImportBatch):
        raise TypeError("batch must be an LHBImportBatch")
    validated = LHBImportBatch.model_validate(batch.model_dump())
    seen_source_rows: set[str] = set()
    for revision in validated.disclosures:
        if revision.source_id != validated.source_id:
            raise ValueError(
                f"revision {revision.disclosure_id} source_id "
                f"{revision.source_id!r} does not match batch source_id "
                f"{validated.source_id!r}"
            )
        if revision.source_version != validated.source_version:
            raise ValueError(
                f"revision {revision.disclosure_id} source_version "
                f"{revision.source_version!r} does not match batch "
                f"source_version {validated.source_version!r}"
            )
        seat_ids: set[str] = set()
        for row in revision.seats:
            if row.source_row_id in seen_source_rows:
                raise ValueError(
                    f"duplicate source_row_id {row.source_row_id!r} in batch"
                )
            seen_source_rows.add(row.source_row_id)
            if row.seat_id in seat_ids:
                raise ValueError(
                    f"duplicate seat_id {row.seat_id!r} within revision "
                    f"{revision.disclosure_id}"
                )
            seat_ids.add(row.seat_id)
    return validated


def aggregate_seats(
    disclosures: Sequence[DisclosureRevisionInput],
    as_of: datetime,
) -> SeatAggregation:
    """Aggregate the latest visible revision of each disclosure as of ``as_of``.

    Rows with ``available_at > as_of`` are hidden. Later revisions replace older
    ones for the source-scoped key ``(source_id, disclosure_id)``; two sources
    that reuse the same disclosure identifier never replace each other. Within
    one source chain, selection orders by ``(revision, available_at)``; an exact
    tie keeps the first-seen row so duplicate rows never double-count. Callers
    must ensure same-key payloads on an exact tie are consistent (the
    repository enforces payload-hash identity); conflicting tied payloads also
    collapse to the first-seen row rather than being reconciled here. Seat rows
    are grouped by the composite, source-scoped key ``(source_id, seat_id)`` so
    two sources that reuse the same seat identifier never merge.
    """
    visible = [row for row in disclosures if row.available_at <= as_of]
    selected: dict[tuple[str, str], DisclosureRevisionInput] = {}
    for row in visible:
        key = (row.source_id, row.disclosure_id)
        current = selected.get(key)
        if current is None or (row.revision, row.available_at) > (
            current.revision,
            current.available_at,
        ):
            selected[key] = row
    return _aggregate_selected(
        tuple(selected.values()),
        as_of=as_of,
        total_disclosures=len(disclosures),
        visible_disclosures=len(visible),
    )


def _sum_decimals(values: Sequence[Decimal]) -> Decimal:
    with localcontext(_decimal_context()):
        total = Decimal("0")
        for value in values:
            total += value
        return total


def _aggregate_selected(
    disclosures: Sequence[DisclosureRevisionInput],
    *,
    as_of: datetime,
    total_disclosures: int,
    visible_disclosures: int,
) -> SeatAggregation:
    """Group selected revisions into source-scoped seats.

    Groups use the composite key ``(source_id, seat_id)``, never the display
    name. Each contribution keeps its own source seat name, mapping version,
    and captured permission state; branch renames and mapping changes are
    historical facts and never raise. Only currency/unit conflicts raise
    ``ValueError``.

    The header's ``source_seat_name``, ``mapping_version``, and
    ``source_version`` follow wall-clock availability: they come from the
    contributing row of the revision with the greatest ``available_at`` in the
    group (revision numbers across different disclosures belong to independent
    chains and are not comparable), with ``revision`` used only as an exact
    ``available_at`` tiebreak; an exact tie on both keeps the first-seen row.
    """
    accumulators: dict[tuple[str, str], _SeatAccumulator] = {}
    seat_order: list[tuple[str, str]] = []
    for revision in disclosures:
        for row in revision.seats:
            key = (revision.source_id, row.seat_id)
            accumulator = accumulators.get(key)
            wall_clock_key = (revision.available_at, revision.revision)
            if accumulator is None:
                accumulator = _SeatAccumulator(
                    seat_id=row.seat_id,
                    source_id=revision.source_id,
                    source_version=revision.source_version,
                    mapping_version=row.mapping_version,
                    source_seat_name=row.source_seat_name,
                    currency=row.currency,
                    unit=row.unit,
                    header_revision=revision.revision,
                    header_available_at=revision.available_at,
                )
                accumulators[key] = accumulator
                seat_order.append(key)
            elif row.currency != accumulator.currency:
                raise ValueError(
                    f"seat {row.seat_id} from source {revision.source_id} "
                    "mixes incompatible currency values"
                )
            elif row.unit != accumulator.unit:
                raise ValueError(
                    f"seat {row.seat_id} from source {revision.source_id} "
                    "mixes incompatible unit values"
                )
            elif wall_clock_key > (
                accumulator.header_available_at,
                accumulator.header_revision,
            ):
                # Renames and remaps never raise: the header follows the latest
                # available contributing revision (revision numbers of distinct
                # disclosures are not comparable) while every contribution keeps
                # its own values.
                accumulator.source_seat_name = row.source_seat_name
                accumulator.mapping_version = row.mapping_version
                accumulator.source_version = revision.source_version
                accumulator.header_revision = revision.revision
                accumulator.header_available_at = revision.available_at
            accumulator.row_count += 1
            if row.buy_amount is not None:
                accumulator.buy_amounts.append(row.buy_amount)
            if row.sell_amount is not None:
                accumulator.sell_amounts.append(row.sell_amount)
            accumulator.contributions.append(
                SeatContribution(
                    disclosure_id=revision.disclosure_id,
                    security_id=revision.security_id,
                    revision=revision.revision,
                    disclosure_date=revision.disclosure_date,
                    reason=revision.reason,
                    source_row_id=row.source_row_id,
                    source_seat_name=row.source_seat_name,
                    mapping_version=row.mapping_version,
                    permission_state=revision.permission.state,
                )
            )

    seats: list[AggregatedSeat] = []
    complete_seats = 0
    for key in seat_order:
        accumulator = accumulators[key]
        buy_total = (
            _sum_decimals(accumulator.buy_amounts)
            if len(accumulator.buy_amounts) == accumulator.row_count
            else None
        )
        sell_total = (
            _sum_decimals(accumulator.sell_amounts)
            if len(accumulator.sell_amounts) == accumulator.row_count
            else None
        )
        completeness = _completeness(buy_total, sell_total)
        with localcontext(_decimal_context()):
            net_amount = (
                buy_total - sell_total
                if buy_total is not None and sell_total is not None
                else None
            )
        if completeness == "complete":
            complete_seats += 1
        seats.append(
            AggregatedSeat(
                seat_id=accumulator.seat_id,
                source_id=accumulator.source_id,
                source_version=accumulator.source_version,
                mapping_version=accumulator.mapping_version,
                source_seat_name=accumulator.source_seat_name,
                currency=accumulator.currency,
                unit=accumulator.unit,
                buy_amount=buy_total,
                sell_amount=sell_total,
                net_amount=net_amount,
                completeness=completeness,
                contributions=tuple(accumulator.contributions),
            )
        )

    seat_count = len(seats)
    coverage = AggregationCoverage(
        total_disclosures=total_disclosures,
        visible_disclosures=visible_disclosures,
        late_disclosures=total_disclosures - visible_disclosures,
        selected_disclosures=len(disclosures),
        seat_count=seat_count,
        complete_seats=complete_seats,
        unknown_seats=seat_count - complete_seats,
    )
    return SeatAggregation(as_of=as_of, seats=tuple(seats), coverage=coverage)


def _completeness(
    buy_total: Decimal | None,
    sell_total: Decimal | None,
) -> SeatCompleteness:
    if buy_total is None and sell_total is None:
        return "both_unknown"
    if buy_total is None:
        return "buy_unknown"
    if sell_total is None:
        return "sell_unknown"
    return "complete"


__all__ = ["aggregate_seats", "validate_import_batch"]
