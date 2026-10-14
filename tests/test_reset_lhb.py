"""Unit and schema tests for strict LHB disclosure models.

The pure tests above the migration section never touch PostgreSQL: evidence is
built from the strict ``dragon_radar.lhb`` models and aggregated with pure
functions. The ``0012_lhb`` migration section below exercises PostgreSQL and
skips cleanly when ``DRAGON_TEST_DATABASE_URL`` is not provided.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from dragon_radar.lhb import (
    AggregatedSeat,
    AggregationCoverage,
    DisclosureRevisionInput,
    DisclosureSeatInput,
    DisclosureSelection,
    LHBImportBatch,
    LHBImportResult,
    PermissionSnapshot,
    SeatAggregation,
    SeatHistorySelection,
    aggregate_seats,
    validate_import_batch,
)

T0 = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)
AS_OF = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
DISCLOSED_ON = date(2026, 9, 18)


def utc(value: str) -> datetime:
    """Parse an ISO-8601 timestamp ending in ``Z`` into an aware datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def permission(state: str = "authorized") -> PermissionSnapshot:
    return PermissionSnapshot(
        permission_id="lhb-permission-1",
        state=state,
        captured_at=T0,
    )


_default_permission = permission


def seat(
    *,
    seat_id: str = "SEAT-A",
    source_row_id: str = "ROW-1",
    source_seat_name: str = "Example branch",
    mapping_version: str = "seat-map-v1",
    buy_amount: Decimal | None = Decimal("100"),
    sell_amount: Decimal | None = Decimal("30"),
    currency: str = "CNY",
    unit: str = "yuan",
    rank: int | None = None,
    side: str | None = None,
) -> DisclosureSeatInput:
    return DisclosureSeatInput(
        source_row_id=source_row_id,
        seat_id=seat_id,
        source_seat_name=source_seat_name,
        mapping_version=mapping_version,
        buy_amount=buy_amount,
        sell_amount=sell_amount,
        currency=currency,
        unit=unit,
        rank=rank,
        side=side,
    )


def disclosure(
    *,
    disclosure_id: str = "D1",
    revision: int = 1,
    source_id: str = "lhb-source",
    source_version: str = "v1",
    security_id: str = "600000.SH",
    disclosure_date: date = DISCLOSED_ON,
    reason: str = "日涨幅偏离值达7%",
    source_event_at: datetime | None = None,
    received_at: datetime | None = None,
    available_at: datetime | None = None,
    permission: PermissionSnapshot | None = None,
    seats: tuple[DisclosureSeatInput, ...] | None = None,
    seat_id: str = "SEAT-A",
    source_row_id: str = "ROW-1",
    source_seat_name: str = "Example branch",
    mapping_version: str = "seat-map-v1",
    buy_amount: Decimal | None = Decimal("100"),
    sell_amount: Decimal | None = Decimal("30"),
    currency: str = "CNY",
    unit: str = "yuan",
    rank: int | None = None,
    side: str | None = None,
) -> DisclosureRevisionInput:
    if seats is None:
        seats = (
            seat(
                seat_id=seat_id,
                source_row_id=source_row_id,
                source_seat_name=source_seat_name,
                mapping_version=mapping_version,
                buy_amount=buy_amount,
                sell_amount=sell_amount,
                currency=currency,
                unit=unit,
                rank=rank,
                side=side,
            ),
        )
    if available_at is None:
        available_at = T0 + timedelta(seconds=2)
    if received_at is None:
        received_at = available_at - timedelta(seconds=1)
    if source_event_at is None:
        source_event_at = received_at - timedelta(seconds=1)
    return DisclosureRevisionInput(
        disclosure_id=disclosure_id,
        revision=revision,
        source_id=source_id,
        source_version=source_version,
        security_id=security_id,
        disclosure_date=disclosure_date,
        reason=reason,
        source_event_at=source_event_at,
        received_at=received_at,
        available_at=available_at,
        permission=(permission if permission is not None else _default_permission()),
        seats=seats,
    )


def test_aggregate_seats_computes_known_net_amount():
    result = aggregate_seats(
        disclosures=[
            disclosure(
                disclosure_id="D1",
                revision=1,
                seat_id="SEAT-A",
                source_seat_name="Example branch",
                buy_amount=Decimal("100"),
                sell_amount=Decimal("30"),
            )
        ],
        as_of=utc("2026-09-18T12:00:00Z"),
    )

    assert isinstance(result, SeatAggregation)
    assert isinstance(result.seats[0], AggregatedSeat)
    assert result.seats[0].buy_amount == Decimal("100")
    assert result.seats[0].sell_amount == Decimal("30")
    assert result.seats[0].net_amount == Decimal("70")
    assert result.seats[0].completeness == "complete"


def test_unknown_amount_is_not_coerced_to_zero():
    result = aggregate_seats(
        disclosures=[
            disclosure(
                disclosure_id="D1",
                revision=1,
                seat_id="SEAT-A",
                buy_amount=None,
                sell_amount=Decimal("0"),
            )
        ],
        as_of=utc("2026-09-18T12:00:00Z"),
    )

    assert result.seats[0].buy_amount is None
    assert result.seats[0].sell_amount == Decimal("0")
    assert result.seats[0].net_amount is None
    assert result.seats[0].completeness == "buy_unknown"


def test_amount_zero_remains_a_distinct_value():
    result = aggregate_seats(
        disclosures=[disclosure(buy_amount=Decimal("0"), sell_amount=Decimal("0"))],
        as_of=AS_OF,
    )

    assert result.seats[0].buy_amount == Decimal("0")
    assert result.seats[0].sell_amount == Decimal("0")
    assert result.seats[0].net_amount == Decimal("0")
    assert result.seats[0].completeness == "complete"


def test_aggregate_empty_disclosures_returns_zeroed_coverage():
    result = aggregate_seats(disclosures=(), as_of=AS_OF)

    assert isinstance(result, SeatAggregation)
    assert result.seats == ()
    assert result.as_of == AS_OF
    assert result.coverage == AggregationCoverage(
        total_disclosures=0,
        visible_disclosures=0,
        late_disclosures=0,
        selected_disclosures=0,
        seat_count=0,
        complete_seats=0,
        unknown_seats=0,
    )


def test_aggregate_accepts_negative_net_when_sell_exceeds_buy():
    result = aggregate_seats(
        disclosures=[disclosure(buy_amount=Decimal("10"), sell_amount=Decimal("30"))],
        as_of=AS_OF,
    )

    aggregated = result.seats[0]
    assert aggregated.buy_amount == Decimal("10")
    assert aggregated.sell_amount == Decimal("30")
    assert aggregated.net_amount == Decimal("-20")
    assert aggregated.completeness == "complete"


@pytest.mark.parametrize(
    ("buy_amount", "sell_amount", "completeness"),
    [
        (Decimal("10"), None, "sell_unknown"),
        (None, None, "both_unknown"),
    ],
)
def test_aggregate_completeness_when_sell_or_both_unknown(
    buy_amount, sell_amount, completeness
):
    result = aggregate_seats(
        disclosures=[disclosure(buy_amount=buy_amount, sell_amount=sell_amount)],
        as_of=AS_OF,
    )

    aggregated = result.seats[0]
    assert aggregated.net_amount is None
    assert aggregated.completeness == completeness


def test_aggregate_duplicate_disclosure_rows_are_not_double_counted():
    row = disclosure(
        disclosure_id="D1",
        revision=1,
        buy_amount=Decimal("100"),
        sell_amount=Decimal("30"),
    )

    result = aggregate_seats(disclosures=[row, row], as_of=AS_OF)

    assert len(result.seats) == 1
    assert result.seats[0].buy_amount == Decimal("100")
    assert result.seats[0].sell_amount == Decimal("30")
    assert result.seats[0].net_amount == Decimal("70")
    assert len(result.seats[0].contributions) == 1
    assert result.coverage.total_disclosures == 2
    assert result.coverage.selected_disclosures == 1


def test_aggregate_later_revision_replaces_earlier():
    first = disclosure(
        revision=1,
        buy_amount=Decimal("100"),
        sell_amount=Decimal("30"),
        available_at=utc("2026-09-18T08:00:00Z"),
    )
    second = disclosure(
        revision=2,
        buy_amount=Decimal("110"),
        sell_amount=Decimal("35"),
        available_at=utc("2026-09-18T09:00:00Z"),
    )

    result = aggregate_seats(disclosures=[first, second], as_of=AS_OF)

    assert len(result.seats) == 1
    assert result.seats[0].buy_amount == Decimal("110")
    assert result.seats[0].sell_amount == Decimal("35")
    assert result.seats[0].net_amount == Decimal("75")
    revisions = [
        contribution.revision for contribution in result.seats[0].contributions
    ]
    assert revisions == [2]


def test_aggregate_sums_known_amounts_across_disclosures():
    first = disclosure(
        disclosure_id="D1",
        buy_amount=Decimal("100"),
        sell_amount=Decimal("30"),
        available_at=utc("2026-09-18T08:00:00Z"),
    )
    second = disclosure(
        disclosure_id="D2",
        source_row_id="ROW-2",
        buy_amount=Decimal("50"),
        sell_amount=Decimal("20"),
        available_at=utc("2026-09-18T09:00:00Z"),
    )

    result = aggregate_seats(disclosures=[first, second], as_of=AS_OF)

    assert result.seats[0].buy_amount == Decimal("150")
    assert result.seats[0].sell_amount == Decimal("50")
    assert result.seats[0].net_amount == Decimal("100")
    assert {
        (contribution.disclosure_id, contribution.revision)
        for contribution in result.seats[0].contributions
    } == {("D1", 1), ("D2", 1)}


def test_aggregate_keeps_same_name_distinct_seats_separate():
    result = aggregate_seats(
        disclosures=[
            disclosure(
                seats=(
                    seat(seat_id="SEAT-A", source_seat_name="Example branch"),
                    seat(
                        seat_id="SEAT-B",
                        source_row_id="ROW-2",
                        source_seat_name="Example branch",
                        buy_amount=Decimal("5"),
                        sell_amount=Decimal("1"),
                    ),
                )
            )
        ],
        as_of=AS_OF,
    )

    assert [aggregated.seat_id for aggregated in result.seats] == [
        "SEAT-A",
        "SEAT-B",
    ]
    assert {aggregated.source_seat_name for aggregated in result.seats} == {
        "Example branch"
    }
    assert result.seats[0].net_amount == Decimal("70")
    assert result.seats[1].net_amount == Decimal("4")


def test_aggregate_seat_order_follows_first_appearance():
    result = aggregate_seats(
        disclosures=[
            disclosure(
                disclosure_id="D1",
                available_at=utc("2026-09-18T08:00:00Z"),
                seats=(seat(seat_id="SEAT-B"),),
            ),
            disclosure(
                disclosure_id="D2",
                source_row_id="ROW-2",
                available_at=utc("2026-09-18T09:00:00Z"),
                seats=(
                    seat(seat_id="SEAT-A"),
                    seat(
                        seat_id="SEAT-B",
                        source_row_id="ROW-2",
                    ),
                ),
            ),
        ],
        as_of=AS_OF,
    )

    assert [aggregated.seat_id for aggregated in result.seats] == [
        "SEAT-B",
        "SEAT-A",
    ]


def test_aggregate_retains_source_and_contributing_disclosure_identities():
    result = aggregate_seats(
        disclosures=[disclosure()],
        as_of=AS_OF,
    )

    aggregated = result.seats[0]
    assert aggregated.seat_id == "SEAT-A"
    assert aggregated.source_id == "lhb-source"
    assert aggregated.source_version == "v1"
    assert aggregated.mapping_version == "seat-map-v1"
    assert aggregated.currency == "CNY"
    assert aggregated.unit == "yuan"
    contribution = aggregated.contributions[0]
    assert contribution.disclosure_id == "D1"
    assert contribution.security_id == "600000.SH"
    assert contribution.revision == 1
    assert contribution.disclosure_date == DISCLOSED_ON
    assert contribution.reason == "日涨幅偏离值达7%"
    assert contribution.source_row_id == "ROW-1"
    assert contribution.permission_state == "authorized"
    assert result.as_of == AS_OF


def test_aggregate_propagates_captured_permission_state_without_revalidation():
    valid = disclosure()
    revoked_permission = PermissionSnapshot.model_construct(
        permission_id="lhb-permission-1",
        state="revoked",
        captured_at=T0,
    )
    # model_construct bypasses validators on purpose: aggregation consumes
    # captured import-time evidence and must copy the snapshot's state
    # verbatim rather than assume "authorized" or re-run import validation.
    captured_revision = DisclosureRevisionInput.model_construct(
        **{
            **valid.model_dump(),
            "permission": revoked_permission,
            "seats": valid.seats,
        }
    )
    assert captured_revision.permission.state == "revoked"

    result = aggregate_seats(disclosures=[captured_revision], as_of=AS_OF)

    contribution = result.seats[0].contributions[0]
    assert contribution.permission_state == "revoked"


def test_late_disclosure_hidden_until_available_at():
    result = aggregate_seats(
        disclosures=[
            disclosure(
                available_at=utc("2026-09-18T12:00:01Z"),
            )
        ],
        as_of=AS_OF,
    )

    assert result.seats == ()
    assert result.coverage.total_disclosures == 1
    assert result.coverage.visible_disclosures == 0
    assert result.coverage.late_disclosures == 1
    assert result.coverage.selected_disclosures == 0


def test_late_disclosure_visible_when_available_at_equals_as_of():
    result = aggregate_seats(
        disclosures=[
            disclosure(
                available_at=utc("2026-09-18T12:00:00Z"),
            )
        ],
        as_of=AS_OF,
    )

    assert [aggregated.seat_id for aggregated in result.seats] == ["SEAT-A"]
    assert result.coverage.visible_disclosures == 1
    assert result.coverage.late_disclosures == 0


def test_aggregate_conflicting_currency_or_unit_raises():
    first = disclosure(
        disclosure_id="D1",
        currency="CNY",
        available_at=utc("2026-09-18T08:00:00Z"),
    )
    second = disclosure(
        disclosure_id="D2",
        source_row_id="ROW-2",
        currency="USD",
        available_at=utc("2026-09-18T09:00:00Z"),
    )

    with pytest.raises(ValueError, match=r"SEAT-A.*lhb-source.*currency"):
        aggregate_seats(disclosures=[first, second], as_of=AS_OF)


def test_aggregate_conflicting_unit_raises_value_error():
    first = disclosure(
        disclosure_id="D1",
        unit="yuan",
        available_at=utc("2026-09-18T08:00:00Z"),
    )
    second = disclosure(
        disclosure_id="D2",
        source_row_id="ROW-2",
        unit="shares",
        available_at=utc("2026-09-18T09:00:00Z"),
    )

    with pytest.raises(ValueError, match=r"SEAT-A.*lhb-source.*unit"):
        aggregate_seats(disclosures=[first, second], as_of=AS_OF)


def test_aggregate_separates_same_seat_id_from_different_sources():
    first = disclosure(
        disclosure_id="D1",
        source_id="SRC-1",
        available_at=utc("2026-09-18T08:00:00Z"),
        seats=(seat(seat_id="SEAT-X"),),
    )
    second = disclosure(
        disclosure_id="D2",
        source_id="SRC-2",
        available_at=utc("2026-09-18T09:00:00Z"),
        seats=(seat(seat_id="SEAT-X", source_row_id="ROW-2"),),
    )

    result = aggregate_seats(disclosures=[first, second], as_of=AS_OF)

    assert len(result.seats) == 2
    assert {
        (aggregated.source_id, aggregated.seat_id) for aggregated in result.seats
    } == {("SRC-1", "SEAT-X"), ("SRC-2", "SEAT-X")}
    assert {aggregated.net_amount for aggregated in result.seats} == {Decimal("70")}
    assert [len(aggregated.contributions) for aggregated in result.seats] == [1, 1]


def test_aggregate_revision_selection_is_scoped_to_source_and_disclosure():
    first_source = disclosure(
        disclosure_id="D1",
        revision=1,
        source_id="SRC-1",
        available_at=utc("2026-09-18T08:00:00Z"),
        seats=(
            seat(
                seat_id="SEAT-A",
                source_row_id="ROW-1",
                buy_amount=Decimal("100"),
                sell_amount=Decimal("30"),
            ),
        ),
    )
    second_source = disclosure(
        disclosure_id="D1",
        revision=1,
        source_id="SRC-2",
        available_at=utc("2026-09-18T09:00:00Z"),
        seats=(
            seat(
                seat_id="SEAT-A",
                source_row_id="ROW-2",
                buy_amount=Decimal("50"),
                sell_amount=Decimal("20"),
            ),
        ),
    )

    result = aggregate_seats(
        disclosures=[first_source, second_source],
        as_of=AS_OF,
    )

    assert result.coverage.total_disclosures == 2
    assert result.coverage.visible_disclosures == 2
    assert result.coverage.selected_disclosures == 2
    assert len(result.seats) == 2
    totals = {
        (aggregated.source_id, aggregated.buy_amount, aggregated.sell_amount)
        for aggregated in result.seats
    }
    assert totals == {
        ("SRC-1", Decimal("100"), Decimal("30")),
        ("SRC-2", Decimal("50"), Decimal("20")),
    }


def test_aggregate_same_source_revision_chain_still_replaces_earlier_revision():
    first = disclosure(
        disclosure_id="D1",
        revision=1,
        source_id="SRC-1",
        buy_amount=Decimal("100"),
        sell_amount=Decimal("30"),
        available_at=utc("2026-09-18T08:00:00Z"),
    )
    second = disclosure(
        disclosure_id="D1",
        revision=2,
        source_id="SRC-1",
        buy_amount=Decimal("110"),
        sell_amount=Decimal("35"),
        available_at=utc("2026-09-18T09:00:00Z"),
    )

    result = aggregate_seats(disclosures=[first, second], as_of=AS_OF)

    assert result.coverage.selected_disclosures == 1
    assert result.seats[0].buy_amount == Decimal("110")
    assert result.seats[0].sell_amount == Decimal("35")


def test_aggregate_same_source_seat_id_still_merges_across_disclosures():
    result = aggregate_seats(
        disclosures=[
            disclosure(
                disclosure_id="D1",
                available_at=utc("2026-09-18T08:00:00Z"),
            ),
            disclosure(
                disclosure_id="D2",
                source_row_id="ROW-2",
                available_at=utc("2026-09-18T09:00:00Z"),
            ),
        ],
        as_of=AS_OF,
    )

    assert len(result.seats) == 1
    assert result.seats[0].net_amount == Decimal("140")
    assert len(result.seats[0].contributions) == 2


@pytest.mark.parametrize(
    ("first_buy", "first_sell", "second_buy", "second_sell", "completeness"),
    [
        (None, Decimal("10"), Decimal("50"), Decimal("20"), "buy_unknown"),
        (Decimal("50"), None, Decimal("50"), Decimal("20"), "sell_unknown"),
        (None, Decimal("10"), Decimal("50"), None, "both_unknown"),
    ],
)
def test_aggregate_unknown_amount_in_one_row_blanks_the_whole_total(
    first_buy, first_sell, second_buy, second_sell, completeness
):
    result = aggregate_seats(
        disclosures=[
            disclosure(
                disclosure_id="D1",
                buy_amount=first_buy,
                sell_amount=first_sell,
                available_at=utc("2026-09-18T08:00:00Z"),
            ),
            disclosure(
                disclosure_id="D2",
                source_row_id="ROW-2",
                buy_amount=second_buy,
                sell_amount=second_sell,
                available_at=utc("2026-09-18T09:00:00Z"),
            ),
        ],
        as_of=AS_OF,
    )

    aggregated = result.seats[0]
    assert aggregated.net_amount is None
    assert aggregated.completeness == completeness
    if completeness == "buy_unknown":
        assert aggregated.buy_amount is None
        assert aggregated.sell_amount == Decimal("30")
    elif completeness == "sell_unknown":
        assert aggregated.buy_amount == Decimal("100")
        assert aggregated.sell_amount is None
    else:
        assert aggregated.buy_amount is None
        assert aggregated.sell_amount is None


def test_aggregate_exact_tie_distinct_identical_payloads_collapse_once():
    first = disclosure(
        disclosure_id="D1",
        revision=1,
        buy_amount=Decimal("100"),
        sell_amount=Decimal("30"),
        available_at=utc("2026-09-18T08:00:00Z"),
    )
    second = DisclosureRevisionInput.model_validate(first.model_dump())
    assert first is not second
    assert first == second

    result = aggregate_seats(disclosures=[first, second], as_of=AS_OF)

    assert len(result.seats) == 1
    assert result.seats[0].buy_amount == Decimal("100")
    assert result.seats[0].sell_amount == Decimal("30")
    assert result.seats[0].net_amount == Decimal("70")
    assert len(result.seats[0].contributions) == 1
    assert result.coverage.total_disclosures == 2
    assert result.coverage.visible_disclosures == 2
    assert result.coverage.selected_disclosures == 1


def test_aggregate_header_uses_latest_revision_name_and_mapping_per_row():
    older = disclosure(
        disclosure_id="D1",
        revision=1,
        available_at=utc("2026-09-18T08:00:00Z"),
        seats=(
            seat(
                source_seat_name="Old branch",
                mapping_version="seat-map-v1",
            ),
        ),
    )
    newer = disclosure(
        disclosure_id="D2",
        revision=1,
        source_row_id="ROW-2",
        available_at=utc("2026-09-18T09:00:00Z"),
        seats=(
            seat(
                source_row_id="ROW-2",
                source_seat_name="New branch",
                mapping_version="seat-map-v2",
            ),
        ),
    )

    result = aggregate_seats(disclosures=[older, newer], as_of=AS_OF)

    aggregated = result.seats[0]
    assert aggregated.source_seat_name == "New branch"
    assert aggregated.mapping_version == "seat-map-v2"
    assert [
        contribution.source_seat_name for contribution in aggregated.contributions
    ] == ["Old branch", "New branch"]
    assert [
        contribution.mapping_version for contribution in aggregated.contributions
    ] == ["seat-map-v1", "seat-map-v2"]


def test_aggregate_header_follows_wall_clock_availability_across_chain_revisions():
    monday = disclosure(
        disclosure_id="D1",
        revision=5,
        source_version="v1",
        disclosure_date=date(2026, 9, 14),
        available_at=utc("2026-09-14T18:00:00Z"),
        seats=(seat(source_seat_name="Old branch", mapping_version="seat-map-v1"),),
    )
    tuesday = disclosure(
        disclosure_id="D2",
        revision=1,
        source_row_id="ROW-2",
        source_version="v2",
        disclosure_date=date(2026, 9, 15),
        available_at=utc("2026-09-15T18:00:00Z"),
        seats=(
            seat(
                source_row_id="ROW-2",
                source_seat_name="New branch",
                mapping_version="seat-map-v2",
            ),
        ),
    )

    result = aggregate_seats(disclosures=[monday, tuesday], as_of=AS_OF)

    aggregated = result.seats[0]
    assert aggregated.source_seat_name == "New branch"
    assert aggregated.mapping_version == "seat-map-v2"
    assert aggregated.source_version == "v2"
    assert [
        contribution.source_seat_name for contribution in aggregated.contributions
    ] == ["Old branch", "New branch"]
    assert [
        contribution.mapping_version for contribution in aggregated.contributions
    ] == ["seat-map-v1", "seat-map-v2"]


def test_aggregate_header_exact_availability_tie_keeps_first_seen():
    earlier_in_input = disclosure(
        disclosure_id="D1",
        revision=1,
        available_at=utc("2026-09-18T08:00:00Z"),
        seats=(seat(source_seat_name="First seen branch", mapping_version="map-v1"),),
    )
    later_in_input = disclosure(
        disclosure_id="D2",
        revision=1,
        source_row_id="ROW-2",
        available_at=utc("2026-09-18T08:00:00Z"),
        seats=(
            seat(
                source_row_id="ROW-2",
                source_seat_name="Tied branch",
                mapping_version="map-v2",
            ),
        ),
    )

    result = aggregate_seats(
        disclosures=[earlier_in_input, later_in_input],
        as_of=AS_OF,
    )

    aggregated = result.seats[0]
    assert aggregated.source_seat_name == "First seen branch"
    assert aggregated.mapping_version == "map-v1"
    assert [
        contribution.source_seat_name for contribution in aggregated.contributions
    ] == ["First seen branch", "Tied branch"]


def test_aggregate_selection_exact_tie_with_conflicting_payload_keeps_first_seen():
    first = disclosure(
        disclosure_id="D1",
        revision=1,
        buy_amount=Decimal("100"),
        sell_amount=Decimal("30"),
        available_at=utc("2026-09-18T08:00:00Z"),
    )
    conflicting = disclosure(
        disclosure_id="D1",
        revision=1,
        source_row_id="ROW-2",
        buy_amount=Decimal("999"),
        sell_amount=Decimal("1"),
        available_at=utc("2026-09-18T08:00:00Z"),
    )

    result = aggregate_seats(disclosures=[first, conflicting], as_of=AS_OF)

    assert result.coverage.selected_disclosures == 1
    assert result.seats[0].buy_amount == Decimal("100")
    assert result.seats[0].sell_amount == Decimal("30")
    assert result.seats[0].net_amount == Decimal("70")
    assert len(result.seats[0].contributions) == 1


def test_aggregate_name_or_mapping_divergence_does_not_raise():
    renamed = disclosure(
        disclosure_id="D1",
        available_at=utc("2026-09-18T08:00:00Z"),
        seats=(seat(source_seat_name="Old branch", mapping_version="seat-map-v1"),),
    )
    remapped = disclosure(
        disclosure_id="D2",
        source_row_id="ROW-2",
        available_at=utc("2026-09-18T09:00:00Z"),
        seats=(
            seat(
                source_row_id="ROW-2",
                source_seat_name="New branch",
                mapping_version="seat-map-v2",
            ),
        ),
    )

    result = aggregate_seats(disclosures=[renamed, remapped], as_of=AS_OF)

    assert len(result.seats) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("buy_amount", Decimal("NaN")),
        ("buy_amount", Decimal("Infinity")),
        ("sell_amount", Decimal("-Infinity")),
        ("buy_amount", Decimal("-1")),
        ("sell_amount", Decimal("-0.01")),
        ("buy_amount", True),
        ("sell_amount", False),
    ],
)
def test_model_rejects_non_finite_or_negative_amount(field, value):
    payload = {
        "source_row_id": "ROW-1",
        "seat_id": "SEAT-A",
        "source_seat_name": "Example branch",
        "mapping_version": "seat-map-v1",
        "buy_amount": Decimal("100"),
        "sell_amount": Decimal("30"),
        "currency": "CNY",
        "unit": "yuan",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        DisclosureSeatInput.model_validate(payload)


@pytest.mark.parametrize("field", ["buy_amount", "sell_amount"])
def test_model_rejects_float_decimal_amount_inputs(field):
    payload = {
        "source_row_id": "ROW-1",
        "seat_id": "SEAT-A",
        "source_seat_name": "Example branch",
        "mapping_version": "seat-map-v1",
        "buy_amount": Decimal("100"),
        "sell_amount": Decimal("30"),
        "currency": "CNY",
        "unit": "yuan",
    }
    payload[field] = 1.5

    with pytest.raises(ValidationError, match="float"):
        DisclosureSeatInput.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("currency", " "),
        ("unit", "\t"),
        ("currency", None),
    ],
)
def test_model_rejects_missing_or_blank_currency_and_unit(field, value):
    payload = {
        "source_row_id": "ROW-1",
        "seat_id": "SEAT-A",
        "source_seat_name": "Example branch",
        "mapping_version": "seat-map-v1",
        "buy_amount": None,
        "sell_amount": None,
        "currency": "CNY",
        "unit": "yuan",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        DisclosureSeatInput.model_validate(payload)


@pytest.mark.parametrize("rank", [0, -1])
def test_model_rejects_rank_below_one(rank):
    with pytest.raises(ValidationError, match="rank"):
        seat(rank=rank)


def test_model_side_metadata_does_not_change_unknown_amounts():
    result = aggregate_seats(
        disclosures=[
            disclosure(
                buy_amount=None,
                sell_amount=Decimal("0"),
                side="buy",
                rank=1,
            )
        ],
        as_of=AS_OF,
    )

    assert result.seats[0].buy_amount is None
    assert result.seats[0].net_amount is None
    assert result.seats[0].completeness == "buy_unknown"


@pytest.mark.parametrize("state", ["revoked", "unavailable"])
def test_model_rejects_revoked_and_unavailable_permission(state):
    with pytest.raises(ValidationError, match="authorized"):
        disclosure(permission=permission(state))


@pytest.mark.parametrize("state", ["revoked", "unavailable"])
def test_model_import_batch_requires_authorized_permission(state):
    # The revision bypasses its own constructor via model_copy so the batch
    # validator itself is what must reject a revoked/unavailable envelope.
    revoked_revision = disclosure().model_copy(update={"permission": permission(state)})

    with pytest.raises(ValidationError, match="authorized"):
        LHBImportBatch(
            batch_id="batch-1",
            source_id="lhb-source",
            source_version="v1",
            source_event_at=T0,
            received_at=T0,
            available_at=T0,
            permission=permission(state),
            disclosures=(revoked_revision,),
        )


def test_model_rejects_revision_without_seat_rows():
    payload = disclosure().model_dump()
    payload["seats"] = ()

    with pytest.raises(ValidationError, match="seats"):
        DisclosureRevisionInput.model_validate(payload)


def test_model_rejects_batch_without_disclosures():
    with pytest.raises(ValidationError, match="disclosures"):
        LHBImportBatch(
            batch_id="batch-1",
            source_id="lhb-source",
            source_version="v1",
            source_event_at=T0,
            received_at=T0,
            available_at=T0,
            permission=permission(),
            disclosures=(),
        )


def test_model_import_batch_accepts_authorized_disclosures():
    batch = LHBImportBatch(
        batch_id="batch-1",
        source_id="lhb-source",
        source_version="v1",
        source_event_at=T0,
        received_at=T0 + timedelta(seconds=1),
        available_at=T0 + timedelta(seconds=2),
        permission=permission(),
        disclosures=(disclosure(),),
    )

    assert batch.batch_id == "batch-1"
    assert batch.disclosures[0].disclosure_id == "D1"


def import_batch() -> LHBImportBatch:
    return LHBImportBatch(
        batch_id="batch-1",
        source_id="lhb-source",
        source_version="v1",
        source_event_at=T0,
        received_at=T0 + timedelta(seconds=1),
        available_at=T0 + timedelta(seconds=2),
        permission=permission(),
        disclosures=(disclosure(),),
    )


def test_validate_import_batch_round_trips_valid_batch():
    batch = import_batch()

    validated = validate_import_batch(batch)

    assert validated == batch
    assert validated is not batch


def test_validate_import_batch_rejects_revoked_permission_in_constructed_input():
    tampered_permission = PermissionSnapshot.model_construct(
        permission_id="lhb-permission-1",
        state="revoked",
        captured_at=T0,
    )
    tampered = import_batch().model_copy(update={"permission": tampered_permission})
    assert tampered.permission.state == "revoked"

    with pytest.raises(ValidationError, match="authorized"):
        validate_import_batch(tampered)


def test_validate_import_batch_rejects_tampered_negative_amount():
    batch = import_batch()
    tampered_seat = (
        batch.disclosures[0].seats[0].model_copy(update={"buy_amount": Decimal("-5")})
    )
    tampered_revision = batch.disclosures[0].model_copy(
        update={"seats": (tampered_seat,)}
    )
    tampered = batch.model_copy(update={"disclosures": (tampered_revision,)})

    with pytest.raises(ValidationError, match="nonnegative|finite"):
        validate_import_batch(tampered)


def test_validate_import_batch_rejects_revision_from_another_source():
    foreign_revision = disclosure(disclosure_id="D2", source_id="other-source")
    batch = import_batch().model_copy(update={"disclosures": (foreign_revision,)})

    with pytest.raises(ValueError, match="source_id"):
        validate_import_batch(batch)


def test_validate_import_batch_rejects_revision_with_other_source_version():
    foreign_revision = disclosure(disclosure_id="D2", source_version="v9")
    batch = import_batch().model_copy(update={"disclosures": (foreign_revision,)})

    with pytest.raises(ValueError, match="source_version"):
        validate_import_batch(batch)


def test_validate_import_batch_rejects_duplicate_source_row_across_batch():
    first = disclosure(
        disclosure_id="D1",
        available_at=utc("2026-09-18T08:00:00Z"),
        seats=(seat(source_row_id="ROW-1"),),
    )
    second = disclosure(
        disclosure_id="D2",
        available_at=utc("2026-09-18T09:00:00Z"),
        seats=(seat(source_row_id="ROW-1"),),
    )
    batch = import_batch().model_copy(update={"disclosures": (first, second)})

    with pytest.raises(ValueError, match="source_row_id"):
        validate_import_batch(batch)


def test_validate_import_batch_rejects_duplicate_seat_within_revision():
    merged_violation = disclosure(
        seats=(
            seat(seat_id="SEAT-A", source_row_id="ROW-1"),
            seat(
                seat_id="SEAT-A",
                source_row_id="ROW-2",
                buy_amount=Decimal("5"),
                sell_amount=Decimal("1"),
            ),
        )
    )
    batch = import_batch().model_copy(update={"disclosures": (merged_violation,)})

    with pytest.raises(ValueError, match="seat_id"):
        validate_import_batch(batch)


def test_validate_import_batch_accepts_two_disclosures_with_distinct_rows():
    first = disclosure(
        disclosure_id="D1",
        available_at=utc("2026-09-18T08:00:00Z"),
        seats=(seat(source_row_id="ROW-1"),),
    )
    second = disclosure(
        disclosure_id="D2",
        available_at=utc("2026-09-18T09:00:00Z"),
        seats=(seat(source_row_id="ROW-2"),),
    )
    batch = import_batch().model_copy(update={"disclosures": (first, second)})

    validated = validate_import_batch(batch)

    assert [item.disclosure_id for item in validated.disclosures] == ["D1", "D2"]


@pytest.mark.parametrize("name", ["Zhang San", "张三", "John Smith Jr.", "  李四  "])
def test_model_seat_name_accepts_person_like_plain_string(name):
    model = seat(source_seat_name=name)

    assert model.source_seat_name == name.strip()
    assert not hasattr(model, "person_name")
    assert not hasattr(model, "legal_person")
    assert not hasattr(model, "fund_name")


@pytest.mark.parametrize(
    "extra",
    [
        {"person_name": "张三"},
        {"legal_person": "张三"},
        {"fund_name": "某基金"},
    ],
)
def test_model_seat_input_rejects_person_attribution_keys(extra):
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DisclosureSeatInput(
            source_row_id="ROW-1",
            seat_id="SEAT-A",
            source_seat_name="张三 专用席位",
            mapping_version="seat-map-v1",
            buy_amount=Decimal("100"),
            sell_amount=Decimal("30"),
            currency="CNY",
            unit="yuan",
            **extra,
        )


def test_model_contracts_are_frozen_strict_and_forbid_extra_fields():
    model = seat()

    with pytest.raises(ValidationError, match="frozen"):
        model.buy_amount = Decimal("1")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DisclosureSeatInput.model_validate(
            {
                **seat().model_dump(),
                "person_name": "not allowed",
            }
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"received_at": T0 - timedelta(seconds=1)},
        {"available_at": T0 - timedelta(seconds=1)},
    ],
)
def test_model_enforces_timestamp_order(changes):
    values = {
        "source_event_at": T0,
        "received_at": T0,
        "available_at": T0 + timedelta(seconds=2),
    }
    values.update(changes)

    with pytest.raises(ValidationError, match="received_at|available_at"):
        disclosure(**values)


@pytest.mark.parametrize("field", ["source_event_at", "received_at", "available_at"])
def test_model_rejects_naive_timestamps(field):
    values = {
        "source_event_at": T0,
        "received_at": T0 + timedelta(seconds=1),
        "available_at": T0 + timedelta(seconds=2),
    }
    values[field] = values[field].replace(tzinfo=None)

    with pytest.raises(ValidationError, match="timezone|aware"):
        disclosure(**values)


@pytest.mark.parametrize("revision", [0, -1])
def test_model_rejects_non_positive_revision(revision):
    with pytest.raises(ValidationError, match="revision"):
        disclosure(revision=revision)


def test_model_disclosure_selection_enforces_paging_bounds():
    with pytest.raises(ValidationError, match="size"):
        DisclosureSelection(date=DISCLOSED_ON, size=101)
    with pytest.raises(ValidationError, match="page"):
        DisclosureSelection(date=DISCLOSED_ON, page=0)

    selection = DisclosureSelection(
        date=DISCLOSED_ON,
        security_id="600000.SH",
        page=2,
        size=100,
        as_of=AS_OF,
    )
    assert selection.date == DISCLOSED_ON
    assert selection.security_id == "600000.SH"
    assert selection.page == 2
    assert selection.size == 100


def test_model_seat_history_selection_enforces_days_bounds():
    with pytest.raises(ValidationError, match="days"):
        SeatHistorySelection(seat_id="SEAT-A", date=DISCLOSED_ON, days=0)
    with pytest.raises(ValidationError, match="days"):
        SeatHistorySelection(seat_id="SEAT-A", date=DISCLOSED_ON, days=251)

    history = SeatHistorySelection(
        seat_id="SEAT-A",
        date=DISCLOSED_ON,
        days=250,
        as_of=AS_OF,
    )
    assert history.days == 250


def test_model_import_result_requires_sha256_payload_hash():
    with pytest.raises(ValidationError, match="payload_hash"):
        LHBImportResult(
            import_id="import-1",
            batch_id="batch-1",
            status="accepted",
            payload_hash="not-a-hash",
            imported_at=T0,
        )

    result = LHBImportResult(
        import_id="import-1",
        batch_id="batch-1",
        status="accepted",
        payload_hash="a" * 64,
        imported_at=T0,
    )
    assert result.status == "accepted"


# ---------------------------------------------------------------------------
# 0012_lhb immutable PostgreSQL schema (Task 22-2)
#
# Every test in this section uses the disposable ``database_url`` schema and
# therefore skips without ``DRAGON_TEST_DATABASE_URL``.
# ---------------------------------------------------------------------------

EXPECTED_LHB_TABLES = {
    "lhb_import_batches",
    "lhb_import_attempts",
    "lhb_disclosures",
    "lhb_disclosure_revisions",
    "lhb_seats",
    "lhb_disclosure_seats",
}

EXPECTED_LHB_COLUMNS = {
    "lhb_import_batches": [
        ("id", "UUID"),
        ("batch_id", "TEXT"),
        ("source_id", "TEXT"),
        ("source_version", "TEXT"),
        ("source_event_at", "TIMESTAMP"),
        ("received_at", "TIMESTAMP"),
        ("available_at", "TIMESTAMP"),
        ("permission_snapshot", "JSONB"),
        ("payload_hash", "VARCHAR(64)"),
        ("status", "VARCHAR(20)"),
        ("reason", "TEXT"),
        ("imported_at", "TIMESTAMP"),
    ],
    "lhb_import_attempts": [
        ("id", "UUID"),
        ("source_id", "TEXT"),
        ("batch_id", "TEXT"),
        ("idempotency_key", "VARCHAR(200)"),
        ("payload_hash", "VARCHAR(64)"),
        ("status", "VARCHAR(20)"),
        ("reason", "TEXT"),
        ("details", "JSONB"),
        ("created_at", "TIMESTAMP"),
    ],
    "lhb_disclosures": [
        ("id", "UUID"),
        ("source_id", "TEXT"),
        ("disclosure_id", "TEXT"),
        ("security_id", "TEXT"),
        ("disclosure_date", "DATE"),
        ("reason", "TEXT"),
        ("permission_id", "TEXT"),
    ],
    "lhb_disclosure_revisions": [
        ("id", "UUID"),
        ("source_id", "TEXT"),
        ("disclosure_id", "TEXT"),
        ("batch_id", "TEXT"),
        ("revision", "INTEGER"),
        ("source_event_at", "TIMESTAMP"),
        ("received_at", "TIMESTAMP"),
        ("available_at", "TIMESTAMP"),
        ("permission_snapshot", "JSONB"),
        ("completeness", "VARCHAR(20)"),
        ("payload_hash", "VARCHAR(64)"),
    ],
    "lhb_seats": [
        ("id", "UUID"),
        ("source_id", "TEXT"),
        ("seat_id", "TEXT"),
        ("mapping_version", "TEXT"),
        ("source_seat_name", "TEXT"),
        ("provenance", "JSONB"),
    ],
    "lhb_disclosure_seats": [
        ("id", "UUID"),
        ("source_id", "TEXT"),
        ("disclosure_id", "TEXT"),
        ("disclosure_date", "DATE"),
        ("revision", "INTEGER"),
        ("source_row_id", "TEXT"),
        ("seat_id", "TEXT"),
        ("mapping_version", "TEXT"),
        ("side", "VARCHAR(4)"),
        ("rank", "INTEGER"),
        ("buy_amount", "NUMERIC"),
        ("sell_amount", "NUMERIC"),
        ("currency", "TEXT"),
        ("unit", "TEXT"),
        ("completeness", "VARCHAR(20)"),
    ],
}

EXPECTED_LHB_CONSTRAINTS = {
    "lhb_import_batches": {
        "lhb_import_batches_pkey",
        "lhb_import_batches_source_batch_key",
        "lhb_import_batches_source_batch_version_key",
        "lhb_import_batches_source_id_fkey",
        "lhb_import_batches_timestamp_order",
        "lhb_import_batches_payload_hash",
        "lhb_import_batches_status",
    },
    "lhb_import_attempts": {
        "lhb_import_attempts_pkey",
        "lhb_import_attempts_source_id_fkey",
        "lhb_import_attempts_status",
        "lhb_import_attempts_payload_hash",
    },
    "lhb_disclosures": {
        "lhb_disclosures_pkey",
        "lhb_disclosures_identity_key",
        "lhb_disclosures_identity_date_key",
        "lhb_disclosures_source_id_fkey",
        "lhb_disclosures_required_text",
    },
    "lhb_disclosure_revisions": {
        "lhb_disclosure_revisions_pkey",
        "lhb_disclosure_revisions_revision_key",
        "lhb_disclosure_revisions_batch_fkey",
        "lhb_disclosure_revisions_disclosure_fkey",
        "lhb_disclosure_revisions_positive_revision",
        "lhb_disclosure_revisions_timestamp_order",
        "lhb_disclosure_revisions_payload_hash",
        "lhb_disclosure_revisions_completeness",
    },
    "lhb_seats": {
        "lhb_seats_pkey",
        "lhb_seats_identity_key",
        "lhb_seats_source_id_fkey",
        "lhb_seats_required_text",
    },
    "lhb_disclosure_seats": {
        "lhb_disclosure_seats_pkey",
        "lhb_disclosure_seats_revision_row_key",
        "lhb_disclosure_seats_revision_seat_key",
        "lhb_disclosure_seats_revision_fkey",
        "lhb_disclosure_seats_seat_fkey",
        "lhb_disclosure_seats_disclosure_fkey",
        "lhb_disclosure_seats_side",
        "lhb_disclosure_seats_rank",
        "lhb_disclosure_seats_nonnegative_amounts",
        "lhb_disclosure_seats_currency_unit",
        "lhb_disclosure_seats_completeness",
    },
}

EXPECTED_LHB_INDEXES = {
    "lhb_import_batches_as_of",
    "lhb_import_batches_source_as_of",
    "lhb_import_attempts_listing",
    "lhb_disclosures_listing",
    "lhb_disclosure_revisions_visible",
    "lhb_disclosure_revisions_batch",
    "lhb_seats_catalog",
    "lhb_disclosure_seats_seat_history",
}

LHB_PAYLOAD_HASH = "c" * 64
LHB_PERMISSION_SNAPSHOT = (
    '{"permission_id":"lhb-permission-1","state":"authorized",'
    '"captured_at":"2026-09-18T08:00:00+00:00"}'
)


def _upgrade_to_lhb(database_url):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from dragon_radar.storage.database import Database

    database = Database(database_url)
    config = Config()
    config.set_main_option("script_location", "dragon_radar:migrations")
    with database.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0011_realtime")
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0011_realtime"
        )
        command.upgrade(config, "0012_lhb")
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0012_lhb"
        )
    return database


def _seed_lhb_chain(connection, *, suffix: str = "seed"):
    """Insert one authorized batch, disclosure, two seats, revision, and rows."""
    from uuid import uuid4

    from sqlalchemy import text

    ids = {
        "source_id": f"lhb-{suffix}-source",
        "batch_id": f"lhb-{suffix}-batch",
        "disclosure_id": f"lhb-{suffix}-disclosure",
        "security_id": f"lhb-{suffix}-security",
        "seat_a": f"lhb-{suffix}-seat-a",
        "seat_b": f"lhb-{suffix}-seat-b",
        "batch_record_id": uuid4(),
        "disclosure_record_id": uuid4(),
        "revision_record_id": uuid4(),
        "seat_record_a": uuid4(),
        "seat_record_b": uuid4(),
        "row_record_a": uuid4(),
        "row_record_b": uuid4(),
    }
    connection.execute(
        text(
            """
            INSERT INTO source_grants (source_id, "grant", updated_at)
            VALUES (:source_id, '{}', :t0)
            """
        ),
        {"source_id": ids["source_id"], "t0": T0},
    )
    connection.execute(
        text(
            """
            INSERT INTO lhb_import_batches (
                id, batch_id, source_id, source_version, source_event_at,
                received_at, available_at, permission_snapshot, payload_hash,
                status, reason, imported_at
            ) VALUES (
                :batch_record_id, :batch_id, :source_id, 'v1', :t0,
                :t1, :t2, CAST(:permission AS jsonb), :hash,
                'accepted', NULL, :t3
            )
            """
        ),
        {
            **ids,
            "permission": LHB_PERMISSION_SNAPSHOT,
            "hash": LHB_PAYLOAD_HASH,
            "t0": T0,
            "t1": T0 + timedelta(seconds=1),
            "t2": T0 + timedelta(seconds=2),
            "t3": T0 + timedelta(seconds=3),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO lhb_disclosures (
                id, source_id, disclosure_id, security_id, disclosure_date,
                reason, permission_id
            ) VALUES (
                :disclosure_record_id, :source_id, :disclosure_id,
                :security_id, DATE '2026-09-18', 'daily price movement',
                'lhb-permission-1'
            )
            """
        ),
        ids,
    )
    connection.execute(
        text(
            """
            INSERT INTO lhb_seats (
                id, source_id, seat_id, mapping_version, source_seat_name,
                provenance
            ) VALUES
                (:seat_record_a, :source_id, :seat_a, 'seat-map-v1',
                 'Example branch A', '{}'::jsonb),
                (:seat_record_b, :source_id, :seat_b, 'seat-map-v1',
                 'Example branch B', '{}'::jsonb)
            """
        ),
        ids,
    )
    connection.execute(
        text(
            """
            INSERT INTO lhb_disclosure_revisions (
                id, source_id, disclosure_id, batch_id, revision,
                source_event_at, received_at, available_at,
                permission_snapshot, completeness, payload_hash
            ) VALUES (
                :revision_record_id, :source_id, :disclosure_id, :batch_id, 1,
                :t0, :t1, :t2, CAST(:permission AS jsonb), 'incomplete', :hash
            )
            """
        ),
        {
            **ids,
            "permission": LHB_PERMISSION_SNAPSHOT,
            "hash": LHB_PAYLOAD_HASH,
            "t0": T0,
            "t1": T0 + timedelta(seconds=1),
            "t2": T0 + timedelta(seconds=2),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO lhb_disclosure_seats (
                id, source_id, disclosure_id, disclosure_date, revision,
                source_row_id, seat_id, mapping_version, side, "rank",
                buy_amount, sell_amount, currency, unit, completeness
            ) VALUES
                (:row_record_a, :source_id, :disclosure_id, DATE '2026-09-18',
                 1, :row_a, :seat_a, 'seat-map-v1', 'net', 1,
                 100, 30, 'CNY', 'yuan', 'complete'),
                (:row_record_b, :source_id, :disclosure_id, DATE '2026-09-18',
                 1, :row_b, :seat_b, 'seat-map-v1', NULL, NULL,
                 NULL, 20, 'CNY', 'yuan', 'buy_unknown')
            """
        ),
        {
            **ids,
            "row_a": f"{ids['batch_id']}-row-a",
            "row_b": f"{ids['batch_id']}-row-b",
        },
    )
    return ids


def test_0012_lhb_extends_0011_as_linear_head_with_exact_six_tables(database_url):
    from sqlalchemy import inspect

    database = _upgrade_to_lhb(database_url)
    try:
        inspector = inspect(database.engine)
        lhb_tables = {
            table for table in inspector.get_table_names() if table.startswith("lhb_")
        }
        assert lhb_tables == EXPECTED_LHB_TABLES
        for table, expected_columns in EXPECTED_LHB_COLUMNS.items():
            columns = inspector.get_columns(table)
            assert [(column["name"], str(column["type"])) for column in columns] == (
                expected_columns
            )
            assert all(column["nullable"] is False for column in columns[:1])
        nullable_columns = {
            table: {
                column["name"]
                for column in inspector.get_columns(table)
                if column["nullable"]
            }
            for table in EXPECTED_LHB_TABLES
        }
        assert nullable_columns == {
            "lhb_import_batches": {"reason"},
            "lhb_import_attempts": {"reason"},
            "lhb_disclosures": set(),
            "lhb_disclosure_revisions": set(),
            "lhb_seats": set(),
            "lhb_disclosure_seats": {"side", "rank", "buy_amount", "sell_amount"},
        }
    finally:
        database.close()


def test_lhb_schema_has_exact_named_constraints_indexes_and_triggers(database_url):
    from sqlalchemy import text

    database = _upgrade_to_lhb(database_url)
    try:
        with database.engine.connect() as connection:
            constraints = connection.execute(
                text(
                    """
                    SELECT relation.relname, constraint_record.conname
                    FROM pg_constraint constraint_record
                    JOIN pg_class relation
                      ON relation.oid = constraint_record.conrelid
                    JOIN pg_namespace namespace
                      ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = current_schema()
                      AND relation.relname = ANY(:tables)
                    """
                ),
                {"tables": sorted(EXPECTED_LHB_TABLES)},
            )
            actual_constraints = {table: set() for table in EXPECTED_LHB_TABLES}
            for table, name in constraints:
                actual_constraints[table].add(name)
            assert actual_constraints == EXPECTED_LHB_CONSTRAINTS

            indexes = set(
                connection.execute(
                    text(
                        """
                        SELECT index_relation.relname
                        FROM pg_index index_record
                        JOIN pg_class relation
                          ON relation.oid = index_record.indrelid
                        JOIN pg_namespace namespace
                          ON namespace.oid = relation.relnamespace
                        JOIN pg_class index_relation
                          ON index_relation.oid = index_record.indexrelid
                        WHERE namespace.nspname = current_schema()
                          AND relation.relname = ANY(:tables)
                          AND NOT index_record.indisunique
                        """
                    ),
                    {"tables": sorted(EXPECTED_LHB_TABLES)},
                ).scalars()
            )
            assert indexes == EXPECTED_LHB_INDEXES
            index_definitions = dict(
                connection.execute(
                    text(
                        """
                        SELECT index_relation.relname,
                               pg_get_indexdef(index_relation.oid)
                        FROM pg_class index_relation
                        JOIN pg_namespace namespace
                          ON namespace.oid = index_relation.relnamespace
                        WHERE namespace.nspname = current_schema()
                          AND index_relation.relname = ANY(:indexes)
                        """
                    ),
                    {
                        "indexes": [
                            "lhb_disclosures_listing",
                            "lhb_disclosure_revisions_visible",
                            "lhb_disclosure_seats_seat_history",
                        ]
                    },
                ).all()
            )
            assert index_definitions["lhb_disclosures_listing"].endswith(
                "USING btree (disclosure_date, security_id, source_id, disclosure_id)"
            )
            assert index_definitions["lhb_disclosure_revisions_visible"].endswith(
                "USING btree (source_id, disclosure_id, available_at DESC, "
                "revision DESC, id DESC)"
            )
            assert index_definitions["lhb_disclosure_seats_seat_history"].endswith(
                "USING btree (source_id, seat_id, disclosure_date DESC, "
                "disclosure_id, revision DESC)"
            )

            triggers = set(
                connection.execute(
                    text(
                        """
                        SELECT relation.relname, trigger.tgname,
                               function.proname,
                               pg_get_triggerdef(trigger.oid, false)
                        FROM pg_trigger trigger
                        JOIN pg_class relation ON relation.oid = trigger.tgrelid
                        JOIN pg_namespace namespace
                          ON namespace.oid = relation.relnamespace
                        JOIN pg_proc function ON function.oid = trigger.tgfoid
                        WHERE namespace.nspname = current_schema()
                          AND relation.relname = ANY(:tables)
                          AND NOT trigger.tgisinternal
                        """
                    ),
                    {"tables": sorted(EXPECTED_LHB_TABLES)},
                )
            )
        assert len(triggers) == 12
        for table in EXPECTED_LHB_TABLES:
            table_triggers = {
                (name, function, definition)
                for trigger_table, name, function, definition in triggers
                if trigger_table == table
            }
            assert {
                (name, function) for name, function, _definition in table_triggers
            } == {
                ("immutable_rows", "reject_immutable_mutation"),
                ("immutable_truncate", "reject_immutable_mutation"),
            }
            definitions = " ".join(
                definition for _name, _function, definition in table_triggers
            )
            assert "UPDATE" in definitions
            assert "DELETE" in definitions
            assert "TRUNCATE" in definitions
    finally:
        database.close()


def test_lhb_bound_attempt_index_is_partial_and_unique(database_url):
    from sqlalchemy import inspect, text

    database = _upgrade_to_lhb(database_url)
    try:
        attempt_index = next(
            index
            for index in inspect(database.engine).get_indexes("lhb_import_attempts")
            if index["name"] == "lhb_import_attempts_bound_idempotency_key"
        )
        assert attempt_index["unique"] is True
        assert attempt_index["column_names"] == ["source_id", "idempotency_key"]
        predicate = str(attempt_index["dialect_options"]["postgresql_where"])
        assert "accepted" in predicate and "incomplete" in predicate
        with database.engine.connect() as connection:
            definition = connection.execute(
                text(
                    """
                    SELECT pg_get_indexdef(index_relation.oid)
                    FROM pg_class index_relation
                    JOIN pg_namespace namespace
                      ON namespace.oid = index_relation.relnamespace
                    WHERE namespace.nspname = current_schema()
                      AND index_relation.relname
                        = 'lhb_import_attempts_bound_idempotency_key'
                    """
                )
            ).scalar_one()
        assert " WHERE " in definition
        assert "accepted" in definition and "incomplete" in definition
    finally:
        database.close()


def test_lhb_immutable_tables_reject_update_delete_and_truncate(database_url):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    database = _upgrade_to_lhb(database_url)
    try:
        with database.engine.begin() as connection:
            ids = _seed_lhb_chain(connection)
        statements = (
            (
                "lhb_import_batches",
                "UPDATE lhb_import_batches SET status = 'rejected' "
                "WHERE batch_id = :batch_id",
            ),
            (
                "lhb_import_batches",
                "DELETE FROM lhb_import_batches WHERE batch_id = :batch_id",
            ),
            (
                "lhb_import_batches",
                "TRUNCATE TABLE lhb_import_batches, lhb_import_attempts, "
                "lhb_disclosures, lhb_disclosure_revisions, lhb_seats, "
                "lhb_disclosure_seats",
            ),
            (
                "lhb_disclosures",
                "UPDATE lhb_disclosures SET reason = 'changed' "
                "WHERE disclosure_id = :disclosure_id",
            ),
            (
                "lhb_disclosures",
                "DELETE FROM lhb_disclosures WHERE disclosure_id = :disclosure_id",
            ),
            (
                "lhb_disclosures",
                "TRUNCATE TABLE lhb_disclosures, lhb_import_batches, "
                "lhb_import_attempts, lhb_disclosure_revisions, lhb_seats, "
                "lhb_disclosure_seats",
            ),
            (
                "lhb_disclosure_revisions",
                "UPDATE lhb_disclosure_revisions SET completeness = 'complete' "
                "WHERE disclosure_id = :disclosure_id",
            ),
            (
                "lhb_disclosure_revisions",
                "DELETE FROM lhb_disclosure_revisions "
                "WHERE disclosure_id = :disclosure_id",
            ),
            (
                "lhb_disclosure_revisions",
                "TRUNCATE TABLE lhb_disclosure_revisions, lhb_import_batches, "
                "lhb_import_attempts, lhb_disclosures, lhb_seats, "
                "lhb_disclosure_seats",
            ),
            (
                "lhb_seats",
                "UPDATE lhb_seats SET source_seat_name = 'renamed' "
                "WHERE seat_id = :seat_a",
            ),
            ("lhb_seats", "DELETE FROM lhb_seats WHERE seat_id = :seat_a"),
            (
                "lhb_seats",
                "TRUNCATE TABLE lhb_seats, lhb_import_batches, "
                "lhb_import_attempts, lhb_disclosures, lhb_disclosure_revisions, "
                "lhb_disclosure_seats",
            ),
            (
                "lhb_disclosure_seats",
                "UPDATE lhb_disclosure_seats SET side = 'buy' "
                "WHERE source_row_id LIKE :row_prefix",
            ),
            (
                "lhb_disclosure_seats",
                "DELETE FROM lhb_disclosure_seats WHERE source_row_id LIKE :row_prefix",
            ),
            ("lhb_disclosure_seats", "TRUNCATE TABLE lhb_disclosure_seats"),
        )
        parameters = {
            "batch_id": ids["batch_id"],
            "disclosure_id": ids["disclosure_id"],
            "seat_a": ids["seat_a"],
            "row_prefix": f"{ids['batch_id']}-row-%",
        }
        for table_name, statement in statements:
            with pytest.raises(DBAPIError) as exc_info:
                with database.engine.begin() as connection:
                    connection.execute(text(statement), parameters)
            assert "immutable records cannot be changed" in str(exc_info.value), (
                table_name,
                statement,
            )
    finally:
        database.close()


def test_lhb_import_attempts_are_immutable(database_url):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    database = _upgrade_to_lhb(database_url)
    try:
        with database.engine.begin() as connection:
            ids = _seed_lhb_chain(connection)
            connection.execute(
                text(
                    """
                    INSERT INTO lhb_import_attempts (
                        id, source_id, batch_id, idempotency_key, payload_hash,
                        status, reason, details, created_at
                    ) VALUES (
                        gen_random_uuid(), :source_id, :batch_id, :key, :hash,
                        'accepted', NULL, '{}'::jsonb, :now
                    )
                    """
                ),
                {
                    "source_id": ids["source_id"],
                    "batch_id": ids["batch_id"],
                    "key": f"{ids['batch_id']}-key",
                    "hash": LHB_PAYLOAD_HASH,
                    "now": T0,
                },
            )
        for statement in (
            "UPDATE lhb_import_attempts SET status = 'rejected' "
            "WHERE idempotency_key = :key",
            "DELETE FROM lhb_import_attempts WHERE idempotency_key = :key",
            "TRUNCATE TABLE lhb_import_attempts",
        ):
            with pytest.raises(DBAPIError) as exc_info:
                with database.engine.begin() as connection:
                    connection.execute(
                        text(statement), {"key": f"{ids['batch_id']}-key"}
                    )
            assert "immutable records cannot be changed" in str(exc_info.value)
    finally:
        database.close()


def test_lhb_check_and_not_null_constraints_enforced(database_url):
    from uuid import uuid4

    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    database = _upgrade_to_lhb(database_url)
    timestamps = {
        "t0": T0,
        "t1": T0 + timedelta(seconds=1),
        "t2": T0 + timedelta(seconds=2),
    }
    try:
        with database.engine.begin() as connection:
            ids = _seed_lhb_chain(connection, suffix="checks")

        def expect_violation(label, sql, parameters, fragment):
            with pytest.raises(DBAPIError) as exc_info:
                with database.engine.begin() as connection:
                    connection.execute(text(sql), parameters)
            assert fragment in str(exc_info.value), label

        expect_violation(
            "revision zero",
            """
            INSERT INTO lhb_disclosure_revisions (
                id, source_id, disclosure_id, batch_id, revision,
                source_event_at, received_at, available_at,
                permission_snapshot, completeness, payload_hash
            ) VALUES (
                :id, :source_id, :disclosure_id, :batch_id, 0,
                :t0, :t1, :t2, CAST(:permission AS jsonb), 'incomplete', :hash
            )
            """,
            {
                **ids,
                "id": uuid4(),
                "permission": LHB_PERMISSION_SNAPSHOT,
                "hash": LHB_PAYLOAD_HASH,
                **timestamps,
            },
            "lhb_disclosure_revisions_positive_revision",
        )
        expect_violation(
            "negative buy amount",
            """
            INSERT INTO lhb_disclosure_seats (
                id, source_id, disclosure_id, disclosure_date, revision,
                source_row_id, seat_id, mapping_version, side, "rank",
                buy_amount, sell_amount, currency, unit, completeness
            ) VALUES (
                :id, :source_id, :disclosure_id, DATE '2026-09-18', 1,
                :row_id, :seat_b, 'seat-map-v1', NULL, NULL,
                -1, NULL, 'CNY', 'yuan', 'sell_unknown'
            )
            """,
            {**ids, "id": uuid4(), "row_id": "negative-row"},
            "lhb_disclosure_seats_nonnegative_amounts",
        )
        expect_violation(
            "revision received before source event",
            """
            INSERT INTO lhb_disclosure_revisions (
                id, source_id, disclosure_id, batch_id, revision,
                source_event_at, received_at, available_at,
                permission_snapshot, completeness, payload_hash
            ) VALUES (
                :id, :source_id, :disclosure_id, :batch_id, 2,
                :t1, :t0, :t2, CAST(:permission AS jsonb), 'incomplete', :hash
            )
            """,
            {
                **ids,
                "id": uuid4(),
                "permission": LHB_PERMISSION_SNAPSHOT,
                "hash": LHB_PAYLOAD_HASH,
                **timestamps,
            },
            "lhb_disclosure_revisions_timestamp_order",
        )
        expect_violation(
            "batch available before received",
            """
            INSERT INTO lhb_import_batches (
                id, batch_id, source_id, source_version, source_event_at,
                received_at, available_at, permission_snapshot, payload_hash,
                status, reason, imported_at
            ) VALUES (
                :id, :batch_id, :source_id, 'v1', :t0, :t2, :t1,
                CAST(:permission AS jsonb), :hash, 'accepted', NULL, :t2
            )
            """,
            {
                **ids,
                "id": uuid4(),
                "batch_id": "reversed-batch",
                "permission": LHB_PERMISSION_SNAPSHOT,
                "hash": LHB_PAYLOAD_HASH,
                **timestamps,
            },
            "lhb_import_batches_timestamp_order",
        )
        expect_violation(
            "null currency",
            """
            INSERT INTO lhb_disclosure_seats (
                id, source_id, disclosure_id, disclosure_date, revision,
                source_row_id, seat_id, mapping_version, side, "rank",
                buy_amount, sell_amount, currency, unit, completeness
            ) VALUES (
                :id, :source_id, :disclosure_id, DATE '2026-09-18', 1,
                :row_id, :seat_b, 'seat-map-v1', NULL, NULL,
                1, NULL, NULL, 'yuan', 'complete'
            )
            """,
            {**ids, "id": uuid4(), "row_id": "null-currency-row"},
            'null value in column "currency"',
        )
        expect_violation(
            "null unit",
            """
            INSERT INTO lhb_disclosure_seats (
                id, source_id, disclosure_id, disclosure_date, revision,
                source_row_id, seat_id, mapping_version, side, "rank",
                buy_amount, sell_amount, currency, unit, completeness
            ) VALUES (
                :id, :source_id, :disclosure_id, DATE '2026-09-18', 1,
                :row_id, :seat_b, 'seat-map-v1', NULL, NULL,
                1, NULL, 'CNY', NULL, 'complete'
            )
            """,
            {**ids, "id": uuid4(), "row_id": "null-unit-row"},
            'null value in column "unit"',
        )
    finally:
        database.close()


def test_lhb_foreign_keys_enforced(database_url):
    from uuid import uuid4

    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    database = _upgrade_to_lhb(database_url)
    timestamps = {
        "t0": T0,
        "t1": T0 + timedelta(seconds=1),
        "t2": T0 + timedelta(seconds=2),
    }
    try:
        with database.engine.begin() as connection:
            ids = _seed_lhb_chain(connection, suffix="fk")

        def expect_fk(label, sql, parameters, fragment):
            with pytest.raises(DBAPIError) as exc_info:
                with database.engine.begin() as connection:
                    connection.execute(text(sql), parameters)
            assert fragment in str(exc_info.value), label

        expect_fk(
            "revision without disclosure",
            """
            INSERT INTO lhb_disclosure_revisions (
                id, source_id, disclosure_id, batch_id, revision,
                source_event_at, received_at, available_at,
                permission_snapshot, completeness, payload_hash
            ) VALUES (
                :id, :source_id, 'missing-disclosure', :batch_id, 1,
                :t0, :t1, :t2, CAST(:permission AS jsonb), 'incomplete', :hash
            )
            """,
            {
                **ids,
                "id": uuid4(),
                "permission": LHB_PERMISSION_SNAPSHOT,
                "hash": LHB_PAYLOAD_HASH,
                **timestamps,
            },
            "lhb_disclosure_revisions_disclosure_fkey",
        )
        expect_fk(
            "revision without batch",
            """
            INSERT INTO lhb_disclosure_revisions (
                id, source_id, disclosure_id, batch_id, revision,
                source_event_at, received_at, available_at,
                permission_snapshot, completeness, payload_hash
            ) VALUES (
                :id, :source_id, :disclosure_id, 'missing-batch', 2,
                :t0, :t1, :t2, CAST(:permission AS jsonb), 'incomplete', :hash
            )
            """,
            {
                **ids,
                "id": uuid4(),
                "permission": LHB_PERMISSION_SNAPSHOT,
                "hash": LHB_PAYLOAD_HASH,
                **timestamps,
            },
            "lhb_disclosure_revisions_batch_fkey",
        )
        expect_fk(
            "seat row without seat catalog entry",
            """
            INSERT INTO lhb_disclosure_seats (
                id, source_id, disclosure_id, disclosure_date, revision,
                source_row_id, seat_id, mapping_version, side, "rank",
                buy_amount, sell_amount, currency, unit, completeness
            ) VALUES (
                :id, :source_id, :disclosure_id, DATE '2026-09-18', 1,
                :row_id, 'missing-seat', 'seat-map-v9', NULL, NULL,
                1, 2, 'CNY', 'yuan', 'complete'
            )
            """,
            {**ids, "id": uuid4(), "row_id": "orphan-seat-row"},
            "lhb_disclosure_seats_seat_fkey",
        )
        expect_fk(
            "attempt without authorized source",
            """
            INSERT INTO lhb_import_attempts (
                id, source_id, batch_id, idempotency_key, payload_hash,
                status, reason, details, created_at
            ) VALUES (
                :id, 'missing-source', 'batch-x', 'key-x', :hash,
                'rejected', 'not authorized', '{}'::jsonb, :t0
            )
            """,
            {"id": uuid4(), "hash": LHB_PAYLOAD_HASH, "t0": T0},
            "lhb_import_attempts_source_id_fkey",
        )
    finally:
        database.close()


def test_lhb_source_scoped_natural_keys_reject_duplicates(database_url):
    from uuid import uuid4

    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    database = _upgrade_to_lhb(database_url)
    timestamps = {
        "t0": T0,
        "t1": T0 + timedelta(seconds=1),
        "t2": T0 + timedelta(seconds=2),
    }
    try:
        with database.engine.begin() as connection:
            ids = _seed_lhb_chain(connection, suffix="unique")

        def expect_unique(label, sql, parameters, fragment):
            with pytest.raises(DBAPIError) as exc_info:
                with database.engine.begin() as connection:
                    connection.execute(text(sql), parameters)
            assert fragment in str(exc_info.value), label

        expect_unique(
            "duplicate disclosure",
            """
            INSERT INTO lhb_disclosures (
                id, source_id, disclosure_id, security_id, disclosure_date,
                reason, permission_id
            ) VALUES (
                :id, :source_id, :disclosure_id, 'other-security',
                DATE '2026-09-19', 'other reason', 'lhb-permission-1'
            )
            """,
            {**ids, "id": uuid4()},
            "lhb_disclosures_identity_key",
        )
        expect_unique(
            "duplicate revision number",
            """
            INSERT INTO lhb_disclosure_revisions (
                id, source_id, disclosure_id, batch_id, revision,
                source_event_at, received_at, available_at,
                permission_snapshot, completeness, payload_hash
            ) VALUES (
                :id, :source_id, :disclosure_id, :batch_id, 1,
                :t0, :t1, :t2, CAST(:permission AS jsonb), 'incomplete', :hash
            )
            """,
            {
                **ids,
                "id": uuid4(),
                "permission": LHB_PERMISSION_SNAPSHOT,
                "hash": LHB_PAYLOAD_HASH,
                **timestamps,
            },
            "lhb_disclosure_revisions_revision_key",
        )
        expect_unique(
            "duplicate seat mapping version",
            """
            INSERT INTO lhb_seats (
                id, source_id, seat_id, mapping_version, source_seat_name,
                provenance
            ) VALUES (
                :id, :source_id, :seat_a, 'seat-map-v1', 'renamed branch',
                '{}'::jsonb
            )
            """,
            {**ids, "id": uuid4()},
            "lhb_seats_identity_key",
        )
        expect_unique(
            "duplicate source row in revision",
            """
            INSERT INTO lhb_disclosure_seats (
                id, source_id, disclosure_id, disclosure_date, revision,
                source_row_id, seat_id, mapping_version, side, "rank",
                buy_amount, sell_amount, currency, unit, completeness
            ) VALUES (
                :id, :source_id, :disclosure_id, DATE '2026-09-18', 1,
                :row_a, :seat_b, 'seat-map-v1', NULL, NULL,
                1, 2, 'CNY', 'yuan', 'complete'
            )
            """,
            {**ids, "id": uuid4(), "row_a": f"{ids['batch_id']}-row-a"},
            "lhb_disclosure_seats_revision_row_key",
        )
    finally:
        database.close()


def test_lhb_schema_preserves_null_zero_and_decimal_precision(database_url):
    from uuid import uuid4

    from sqlalchemy import text

    database = _upgrade_to_lhb(database_url)
    precise = Decimal("12345678901234567890.123456789012345678")
    try:
        with database.engine.begin() as connection:
            ids = _seed_lhb_chain(connection, suffix="precision")
            row_id = uuid4()
            connection.execute(
                text(
                    """
                    INSERT INTO lhb_seats (
                        id, source_id, seat_id, mapping_version,
                        source_seat_name, provenance
                    ) VALUES (
                        :seat_record_c, :source_id, :seat_c, 'seat-map-v1',
                        'Example branch C', '{}'::jsonb
                    )
                    """
                ),
                {
                    **ids,
                    "seat_record_c": uuid4(),
                    "seat_c": f"{ids['source_id']}-seat-c",
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO lhb_disclosure_seats (
                        id, source_id, disclosure_id, disclosure_date, revision,
                        source_row_id, seat_id, mapping_version, side, "rank",
                        buy_amount, sell_amount, currency, unit, completeness
                    ) VALUES (
                        :id, :source_id, :disclosure_id, DATE '2026-09-18', 1,
                        'precision-row', :seat_c, 'seat-map-v1', NULL, NULL,
                        :precise, 0, 'CNY', 'yuan', 'complete'
                    )
                    """
                ),
                {
                    **ids,
                    "id": row_id,
                    "seat_c": f"{ids['source_id']}-seat-c",
                    "precise": precise,
                },
            )
        with database.engine.connect() as connection:
            precise_buy, zero_sell, unknown_buy, unknown_sell = connection.execute(
                text(
                    """
                    SELECT precise_row.buy_amount, precise_row.sell_amount,
                           unknown_row.buy_amount, unknown_row.sell_amount
                    FROM lhb_disclosure_seats precise_row
                    JOIN lhb_disclosure_seats unknown_row
                      ON unknown_row.seat_id = :seat_b
                     AND unknown_row.source_row_id = :row_b
                    WHERE precise_row.id = :id
                    """
                ),
                {
                    "id": row_id,
                    "seat_b": ids["seat_b"],
                    "row_b": f"{ids['batch_id']}-row-b",
                },
            ).one()
        assert precise_buy == precise
        assert zero_sell == Decimal("0")
        assert unknown_buy is None
        assert unknown_sell == Decimal("20")
    finally:
        database.close()


def test_lhb_schema_is_ready_at_0012_head(database):
    from dragon_radar.storage.database import SCHEMA_REVISION

    assert SCHEMA_REVISION == "0012_lhb"
    assert database.ready()


def test_0012_lhb_downgrade_is_forbidden(database_url):
    from alembic import command
    from alembic.config import Config

    database = _upgrade_to_lhb(database_url)
    config = Config()
    config.set_main_option("script_location", "dragon_radar:migrations")
    try:
        with (
            pytest.raises(
                RuntimeError,
                match="LHB disclosure history cannot be downgraded",
            ),
            database.engine.begin() as connection,
        ):
            config.attributes["connection"] = connection
            command.downgrade(config, "0011_realtime")
    finally:
        database.close()
