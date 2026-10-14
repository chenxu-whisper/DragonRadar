"""Pure point-in-time revision selection and null-safe LHB seat aggregation."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from dragon_radar.lhb.models import (
    AggregatedSeat,
    AggregationCoverage,
    DisclosureRevisionInput,
    LHBImportBatch,
    SeatAggregation,
    SeatCompleteness,
    SeatContribution,
)

_DECIMAL_CONTEXT = Context(
    prec=34,
    rounding=ROUND_HALF_EVEN,
    Emin=-999999,
    Emax=999999,
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
    buy_amounts: list[Decimal] = field(default_factory=list)
    sell_amounts: list[Decimal] = field(default_factory=list)
    contributions: list[SeatContribution] = field(default_factory=list)
    row_count: int = 0


def validate_import_batch(batch: LHBImportBatch) -> LHBImportBatch:
    """Re-validate a typed batch at the trust boundary before storage."""
    if not isinstance(batch, LHBImportBatch):
        raise TypeError("batch must be an LHBImportBatch")
    return LHBImportBatch.model_validate(batch.model_dump())


def aggregate_seats(
    disclosures: Sequence[DisclosureRevisionInput],
    as_of: datetime,
) -> SeatAggregation:
    """Aggregate the latest visible revision of each disclosure as of ``as_of``.

    Rows with ``available_at > as_of`` are hidden. Later revisions replace older
    ones for the same ``disclosure_id``; ties on ``(revision, available_at)``
    keep the first-seen row so duplicate rows never double-count.
    """
    visible = [row for row in disclosures if row.available_at <= as_of]
    selected: dict[str, DisclosureRevisionInput] = {}
    for row in visible:
        current = selected.get(row.disclosure_id)
        if current is None or (row.revision, row.available_at) > (
            current.revision,
            current.available_at,
        ):
            selected[row.disclosure_id] = row
    return _aggregate_selected(
        tuple(selected.values()),
        as_of=as_of,
        total_disclosures=len(disclosures),
        visible_disclosures=len(visible),
    )


def _sum_decimals(values: Sequence[Decimal]) -> Decimal:
    with localcontext(_DECIMAL_CONTEXT):
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
    accumulators: dict[str, _SeatAccumulator] = {}
    seat_order: list[str] = []
    for revision in disclosures:
        for row in revision.seats:
            accumulator = accumulators.get(row.seat_id)
            if accumulator is None:
                accumulator = _SeatAccumulator(
                    seat_id=row.seat_id,
                    source_id=revision.source_id,
                    source_version=revision.source_version,
                    mapping_version=row.mapping_version,
                    source_seat_name=row.source_seat_name,
                    currency=row.currency,
                    unit=row.unit,
                )
                accumulators[row.seat_id] = accumulator
                seat_order.append(row.seat_id)
            elif row.currency != accumulator.currency:
                raise ValueError(
                    f"seat {row.seat_id} mixes incompatible currency values"
                )
            elif row.unit != accumulator.unit:
                raise ValueError(f"seat {row.seat_id} mixes incompatible unit values")
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
                )
            )

    seats: list[AggregatedSeat] = []
    complete_seats = 0
    for seat_id in seat_order:
        accumulator = accumulators[seat_id]
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
        with localcontext(_DECIMAL_CONTEXT):
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
