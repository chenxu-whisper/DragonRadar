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
from sqlalchemy import func, select, text

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
    SeatIdentity,
    aggregate_seats,
    validate_import_batch,
)

T0 = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)
AS_OF = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
DISCLOSED_ON = date(2026, 9, 18)


@pytest.fixture
def anyio_backend():
    # Pin anyio to asyncio: a CI image with trio installed must not silently
    # double-parametrize the async HTTP route tests under two backends.
    return "asyncio"


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


def _chain_revision(revision, **changes):
    values = {
        "disclosure_id": "D1",
        "revision": revision,
        "source_row_id": f"ROW-{revision}",
        "source_event_at": utc(f"2026-09-18T0{revision + 7}:00:00Z"),
        "received_at": utc(f"2026-09-18T0{revision + 7}:00:01Z"),
        "available_at": utc(f"2026-09-18T0{revision + 7}:00:02Z"),
        "buy_amount": Decimal(f"10{revision}"),
        "sell_amount": Decimal("35"),
    }
    values.update(changes)
    return disclosure(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("disclosure_date", date(2026, 9, 19)),
        ("reason", "换手率达20%的证券"),
        ("security_id", "000001.SZ"),
        (
            "permission",
            PermissionSnapshot(
                permission_id="lhb-permission-2",
                state="authorized",
                captured_at=T0,
            ),
        ),
    ],
)
def test_validate_import_batch_rejects_divergent_identity_across_revisions(
    field, value
):
    first = _chain_revision(1)
    second = _chain_revision(2, **{field: value})
    batch = import_batch().model_copy(update={"disclosures": (first, second)})

    with pytest.raises(ValueError, match="disclosure identity"):
        validate_import_batch(batch)


def test_validate_import_batch_accepts_revision_chain_with_consistent_identity():
    first = _chain_revision(1)
    second = _chain_revision(2)
    batch = import_batch().model_copy(update={"disclosures": (first, second)})

    validated = validate_import_batch(batch)

    assert [item.revision for item in validated.disclosures] == [1, 2]
    assert {item.disclosure_date for item in validated.disclosures} == {DISCLOSED_ON}


@pytest.mark.parametrize(
    ("currency", "unit"),
    [("USD", "yuan"), ("CNY", "dollar"), ("USD", "dollar")],
)
def test_validate_import_batch_rejects_mixed_currency_unit_for_same_seat(
    currency, unit
):
    first = disclosure(
        disclosure_id="D1",
        source_row_id="ROW-1",
        currency="CNY",
        unit="yuan",
        available_at=utc("2026-09-18T08:00:00Z"),
    )
    second = disclosure(
        disclosure_id="D2",
        source_row_id="ROW-2",
        currency=currency,
        unit=unit,
        available_at=utc("2026-09-18T09:00:00Z"),
    )
    batch = import_batch().model_copy(update={"disclosures": (first, second)})

    with pytest.raises(ValueError, match="currency/unit"):
        validate_import_batch(batch)


def test_validate_import_batch_accepts_consistent_currency_unit_across_revisions():
    first = disclosure(
        disclosure_id="D1",
        revision=1,
        source_row_id="ROW-1",
        currency="CNY",
        unit="yuan",
        available_at=utc("2026-09-18T08:00:00Z"),
    )
    second = disclosure(
        disclosure_id="D1",
        revision=2,
        source_row_id="ROW-2",
        currency="CNY",
        unit="yuan",
        buy_amount=Decimal("110"),
        sell_amount=Decimal("35"),
        source_event_at=utc("2026-09-18T09:00:00Z"),
        received_at=utc("2026-09-18T09:00:01Z"),
        available_at=utc("2026-09-18T09:00:02Z"),
    )
    batch = import_batch().model_copy(update={"disclosures": (first, second)})

    validated = validate_import_batch(batch)

    assert [item.revision for item in validated.disclosures] == [1, 2]


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

# (column name, reflected type string, nullable) in ordinal position.
EXPECTED_LHB_COLUMNS = {
    "lhb_import_batches": [
        ("id", "UUID", False),
        ("batch_id", "TEXT", False),
        ("source_id", "TEXT", False),
        ("source_version", "TEXT", False),
        ("source_event_at", "TIMESTAMP", False),
        ("received_at", "TIMESTAMP", False),
        ("available_at", "TIMESTAMP", False),
        ("permission_snapshot", "JSONB", False),
        ("payload_hash", "VARCHAR(64)", False),
        ("status", "VARCHAR(20)", False),
        ("reason", "TEXT", True),
        ("imported_at", "TIMESTAMP", False),
    ],
    "lhb_import_attempts": [
        ("id", "UUID", False),
        ("source_id", "TEXT", False),
        ("batch_id", "TEXT", False),
        ("idempotency_key", "VARCHAR(200)", False),
        ("payload_hash", "VARCHAR(64)", False),
        ("status", "VARCHAR(20)", False),
        ("reason", "TEXT", True),
        ("details", "JSONB", False),
        ("created_at", "TIMESTAMP", False),
    ],
    "lhb_disclosures": [
        ("id", "UUID", False),
        ("source_id", "TEXT", False),
        ("disclosure_id", "TEXT", False),
        ("security_id", "TEXT", False),
        ("disclosure_date", "DATE", False),
        ("reason", "TEXT", False),
        ("permission_id", "TEXT", False),
    ],
    "lhb_disclosure_revisions": [
        ("id", "UUID", False),
        ("source_id", "TEXT", False),
        ("disclosure_id", "TEXT", False),
        ("batch_id", "TEXT", False),
        ("revision", "INTEGER", False),
        ("source_event_at", "TIMESTAMP", False),
        ("received_at", "TIMESTAMP", False),
        ("available_at", "TIMESTAMP", False),
        ("permission_snapshot", "JSONB", False),
        ("completeness", "VARCHAR(20)", False),
        ("payload_hash", "VARCHAR(64)", False),
    ],
    "lhb_seats": [
        ("id", "UUID", False),
        ("source_id", "TEXT", False),
        ("seat_id", "TEXT", False),
        ("mapping_version", "TEXT", False),
        ("source_seat_name", "TEXT", False),
        ("provenance", "JSONB", False),
    ],
    "lhb_disclosure_seats": [
        ("id", "UUID", False),
        ("source_id", "TEXT", False),
        ("disclosure_id", "TEXT", False),
        ("disclosure_date", "DATE", False),
        ("revision", "INTEGER", False),
        ("source_row_id", "TEXT", False),
        ("seat_id", "TEXT", False),
        ("mapping_version", "TEXT", False),
        ("side", "VARCHAR(4)", True),
        ("rank", "INTEGER", True),
        ("buy_amount", "NUMERIC", True),
        ("sell_amount", "NUMERIC", True),
        ("currency", "TEXT", False),
        ("unit", "TEXT", False),
        ("completeness", "VARCHAR(20)", False),
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
        # Every column position is pinned exactly: ordinal name, reflected
        # type string and NOT NULL flag must match EXPECTED_LHB_COLUMNS.
        for table, expected_columns in EXPECTED_LHB_COLUMNS.items():
            columns = inspector.get_columns(table)
            assert [
                (column["name"], str(column["type"]), column["nullable"])
                for column in columns
            ] == expected_columns
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


# ---------------------------------------------------------------------------
# LHB repository (Task 22-2)
#
# Transactional ingestion, point-in-time reads, and read-time permission
# recheck over the immutable six-table LHB schema. Every test in this section
# uses the disposable ``database_url`` schema and skips without
# ``DRAGON_TEST_DATABASE_URL``.
# ---------------------------------------------------------------------------

OPERATION_NOW = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)
LIST_AS_OF = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)


@pytest.fixture
def lhb_repository(database_url):
    from factories import grant as grant_factory

    from dragon_radar.lhb.repository import LHBRepository
    from dragon_radar.storage.repository import Repository

    database = _upgrade_to_lhb(database_url)

    def clock():
        return OPERATION_NOW

    Repository(database, clock=clock).register_source(
        grant_factory(source_id="lhb-source")
    )
    repository = LHBRepository(database, clock=clock)
    try:
        yield repository
    finally:
        database.close()


def repo_batch(batch_id="batch-1", *, disclosures=None, **changes) -> LHBImportBatch:
    values = {
        "batch_id": batch_id,
        "source_id": "lhb-source",
        "source_version": "v1",
        "source_event_at": T0,
        "received_at": T0 + timedelta(seconds=1),
        "available_at": T0 + timedelta(seconds=2),
        "permission": permission(),
        "disclosures": (disclosure(),) if disclosures is None else disclosures,
    }
    values.update(changes)
    return LHBImportBatch(**values)


def lhb_table_counts(engine):
    from dragon_radar.lhb.tables import (
        lhb_disclosure_revisions,
        lhb_disclosure_seats,
        lhb_disclosures,
        lhb_import_attempts,
        lhb_import_batches,
        lhb_seats,
    )

    tables = (
        lhb_import_batches,
        lhb_import_attempts,
        lhb_disclosures,
        lhb_disclosure_revisions,
        lhb_seats,
        lhb_disclosure_seats,
    )
    with engine.connect() as connection:
        return {
            table.name: connection.scalar(select(func.count()).select_from(table))
            for table in tables
        }


def revision_with_earlier_availability(revision_number, **changes):
    return disclosure(
        revision=revision_number,
        source_event_at=T0,
        received_at=T0 + timedelta(milliseconds=400),
        available_at=T0 + timedelta(milliseconds=500),
        **changes,
    )


# --- ingestion: accepted, duplicate, conflict, rejected -------------------


def test_lhb_repo_ingest_accepts_new_batch_and_persists_the_whole_chain(
    lhb_repository,
):
    from dragon_radar.lhb.tables import (
        lhb_disclosure_revisions,
        lhb_disclosure_seats,
        lhb_disclosures,
        lhb_import_attempts,
        lhb_import_batches,
        lhb_seats,
    )

    result = lhb_repository.ingest_batch(repo_batch())

    assert result.status == "accepted"
    assert result.reason is None
    assert result.batch_id == "batch-1"
    assert len(result.payload_hash) == 64
    assert result.imported_at == OPERATION_NOW
    with lhb_repository.database.engine.connect() as connection:
        batch = (
            connection.execute(
                select(lhb_import_batches).where(
                    lhb_import_batches.c.batch_id == "batch-1"
                )
            )
            .mappings()
            .one()
        )
        attempt = connection.execute(select(lhb_import_attempts)).mappings().one()
        disclosure = connection.execute(select(lhb_disclosures)).mappings().one()
        revision = connection.execute(select(lhb_disclosure_revisions)).mappings().one()
        seat = connection.execute(select(lhb_seats)).mappings().one()
        seat_row = connection.execute(select(lhb_disclosure_seats)).mappings().one()
    assert batch["status"] == "accepted"
    assert batch["reason"] is None
    assert batch["payload_hash"] == result.payload_hash == attempt["payload_hash"]
    assert attempt["status"] == "accepted"
    assert attempt["idempotency_key"] == "batch-1"
    assert attempt["details"]["kind"] == "lhb"
    assert attempt["details"]["disclosure_count"] == 1
    assert attempt["details"]["seat_row_count"] == 1
    assert disclosure["security_id"] == "600000.SH"
    assert revision["completeness"] == "complete"
    assert revision["batch_id"] == "batch-1"
    assert seat["source_seat_name"] == "Example branch"
    assert seat["provenance"] == {
        "mapping_version": "seat-map-v1",
        "source_id": "lhb-source",
    }
    assert seat_row["completeness"] == "complete"
    assert seat_row["side"] is None
    assert seat_row["buy_amount"] == Decimal("100")
    assert seat_row["sell_amount"] == Decimal("30")


def test_lhb_repo_identical_replay_is_duplicate_without_new_domain_rows(
    lhb_repository,
):
    from dragon_radar.lhb.tables import (
        lhb_disclosure_revisions,
        lhb_disclosure_seats,
        lhb_disclosures,
        lhb_import_attempts,
        lhb_import_batches,
        lhb_seats,
    )

    batch = repo_batch()
    first = lhb_repository.ingest_batch(batch)
    replay = lhb_repository.ingest_batch(repo_batch())

    assert first.status == "accepted"
    assert replay.status == "duplicate"
    assert replay.reason is None
    assert replay.import_id != first.import_id
    assert replay.batch_id == first.batch_id == "batch-1"
    assert replay.payload_hash == first.payload_hash
    assert replay.imported_at == OPERATION_NOW
    with lhb_repository.database.engine.connect() as connection:
        assert connection.scalars(select(lhb_import_attempts.c.status)).all() == [
            "accepted",
            "duplicate",
        ]
        duplicate = (
            connection.execute(
                select(lhb_import_attempts).where(
                    lhb_import_attempts.c.status == "duplicate"
                )
            )
            .mappings()
            .one()
        )
        assert duplicate["reason"] is None
        assert duplicate["details"]["original_import_id"] == first.import_id
        assert (
            connection.scalar(select(func.count()).select_from(lhb_import_batches)) == 1
        )
        assert connection.scalar(select(func.count()).select_from(lhb_disclosures)) == 1
        assert (
            connection.scalar(
                select(func.count()).select_from(lhb_disclosure_revisions)
            )
            == 1
        )
        assert connection.scalar(select(func.count()).select_from(lhb_seats)) == 1
        assert (
            connection.scalar(select(func.count()).select_from(lhb_disclosure_seats))
            == 1
        )


def test_lhb_repo_same_identity_different_payload_is_conflict_with_attempt(
    lhb_repository,
):
    from dragon_radar.lhb.tables import (
        lhb_disclosure_revisions,
        lhb_disclosure_seats,
        lhb_disclosures,
        lhb_import_attempts,
        lhb_import_batches,
        lhb_seats,
    )
    from dragon_radar.storage.repository import Conflict

    lhb_repository.ingest_batch(repo_batch())
    tampered = repo_batch().model_copy(
        update={
            "disclosures": (
                disclosure(buy_amount=Decimal("101"), sell_amount=Decimal("31")),
            )
        }
    )

    with pytest.raises(Conflict, match="payload"):
        lhb_repository.ingest_batch(tampered)

    with lhb_repository.database.engine.connect() as connection:
        rejected = (
            connection.execute(
                select(lhb_import_attempts).where(
                    lhb_import_attempts.c.status == "rejected"
                )
            )
            .mappings()
            .one()
        )
        assert rejected["reason"] == "batch_identity_conflict"
        assert rejected["details"] == {"kind": "lhb"}
        assert (
            connection.scalar(select(func.count()).select_from(lhb_import_batches)) == 1
        )
        assert connection.scalar(select(func.count()).select_from(lhb_disclosures)) == 1
        assert (
            connection.scalar(
                select(func.count()).select_from(lhb_disclosure_revisions)
            )
            == 1
        )
        assert connection.scalar(select(func.count()).select_from(lhb_seats)) == 1
        assert (
            connection.scalar(select(func.count()).select_from(lhb_disclosure_seats))
            == 1
        )


def test_lhb_repo_validation_failure_returns_rejected_result_without_domain_rows(
    lhb_repository,
):
    from dragon_radar.lhb.tables import (
        lhb_disclosure_revisions,
        lhb_disclosure_seats,
        lhb_disclosures,
        lhb_import_attempts,
        lhb_import_batches,
        lhb_seats,
    )

    tampered_seat = (
        disclosure().seats[0].model_copy(update={"buy_amount": Decimal("-5")})
    )
    tampered_revision = disclosure().model_copy(update={"seats": (tampered_seat,)})
    tampered = repo_batch(disclosures=(tampered_revision,))

    result = lhb_repository.ingest_batch(tampered)

    assert result.status == "rejected"
    assert result.reason == "validation_failed"
    assert result.reason.strip()
    with lhb_repository.database.engine.connect() as connection:
        attempt = connection.execute(select(lhb_import_attempts)).mappings().one()
        assert attempt["status"] == "rejected"
        assert attempt["reason"] == "validation_failed"
        assert attempt["details"]["kind"] == "lhb"
        assert attempt["details"]["error_type"] == "ValidationError"
        assert (
            connection.scalar(select(func.count()).select_from(lhb_import_batches)) == 0
        )
        assert connection.scalar(select(func.count()).select_from(lhb_disclosures)) == 0
        assert (
            connection.scalar(
                select(func.count()).select_from(lhb_disclosure_revisions)
            )
            == 0
        )
        assert connection.scalar(select(func.count()).select_from(lhb_seats)) == 0
        assert (
            connection.scalar(select(func.count()).select_from(lhb_disclosure_seats))
            == 0
        )


def test_lhb_repo_duplicate_source_rows_are_rejected_not_double_counted(
    lhb_repository,
):
    first = disclosure(
        disclosure_id="D1",
        available_at=utc("2026-09-18T08:00:00Z"),
        seats=(seat(source_row_id="ROW-1"),),
    )
    second = disclosure(
        disclosure_id="D2",
        source_row_id="ROW-1",
        available_at=utc("2026-09-18T09:00:00Z"),
        seats=(seat(source_row_id="ROW-1"),),
    )

    result = lhb_repository.ingest_batch(
        repo_batch("batch-dup-rows", disclosures=(first, second))
    )

    assert result.status == "rejected"
    assert result.reason == "validation_failed"
    assert lhb_table_counts(lhb_repository.database.engine) == {
        "lhb_import_batches": 0,
        "lhb_import_attempts": 1,
        "lhb_disclosures": 0,
        "lhb_disclosure_revisions": 0,
        "lhb_seats": 0,
        "lhb_disclosure_seats": 0,
    }


def test_lhb_repo_concurrent_identical_ingests_yield_one_accepted_one_duplicate(
    lhb_repository,
):
    from concurrent.futures import ThreadPoolExecutor

    batch = repo_batch()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(lhb_repository.ingest_batch, batch) for _ in range(2)
        ]
    results = [future.result() for future in futures]

    assert {result.status for result in results} == {"accepted", "duplicate"}
    assert len({result.payload_hash for result in results}) == 1
    counts = lhb_table_counts(lhb_repository.database.engine)
    assert counts["lhb_import_batches"] == 1
    assert counts["lhb_import_attempts"] == 2
    assert counts["lhb_disclosure_revisions"] == 1
    assert counts["lhb_disclosure_seats"] == 1


def test_lhb_repo_unknown_source_is_forbidden_without_any_attempt_row(
    lhb_repository,
):
    from dragon_radar.storage.repository import Forbidden

    with pytest.raises(Forbidden):
        lhb_repository.ingest_batch(repo_batch(source_id="missing-source"))

    assert lhb_table_counts(lhb_repository.database.engine) == {
        "lhb_import_batches": 0,
        "lhb_import_attempts": 0,
        "lhb_disclosures": 0,
        "lhb_disclosure_revisions": 0,
        "lhb_seats": 0,
        "lhb_disclosure_seats": 0,
    }


def test_lhb_repo_ingest_requires_storage_right_and_leaves_no_attempt(
    database_url,
):
    from factories import grant as grant_factory

    from dragon_radar.lhb.repository import LHBRepository
    from dragon_radar.storage.repository import Forbidden, Repository

    database = _upgrade_to_lhb(database_url)
    try:
        Repository(database, clock=lambda: OPERATION_NOW).register_source(
            grant_factory(source_id="display-only", local_storage=False)
        )
        repository = LHBRepository(database, clock=lambda: OPERATION_NOW)

        with pytest.raises(Forbidden):
            repository.ingest_batch(repo_batch(source_id="display-only"))

        assert lhb_table_counts(database.engine)["lhb_import_attempts"] == 0
    finally:
        database.close()


def test_lhb_repo_ingest_rejects_future_captured_permission_without_attempt(
    lhb_repository,
):
    from dragon_radar.storage.repository import Forbidden

    future_permission = PermissionSnapshot(
        permission_id="lhb-permission-1",
        state="authorized",
        captured_at=OPERATION_NOW + timedelta(hours=1),
    )
    with pytest.raises(Forbidden):
        lhb_repository.ingest_batch(repo_batch(permission=future_permission))

    assert lhb_table_counts(lhb_repository.database.engine)["lhb_import_attempts"] == 0


def test_lhb_repo_payload_hash_is_canonical_order_insensitive_and_content_sensitive(
    lhb_repository,
):
    from dragon_radar.lhb.repository import _batch_payload, _canonical_hash

    def batch_hash(batch):
        return _canonical_hash(
            {"kind": "lhb", "batch": _batch_payload(validate_import_batch(batch))}
        )

    first = disclosure(
        disclosure_id="D1",
        available_at=utc("2026-09-18T08:00:00Z"),
        seats=(
            seat(seat_id="SEAT-A", source_row_id="ROW-1", buy_amount=Decimal("1")),
            seat(
                seat_id="SEAT-B",
                source_row_id="ROW-2",
                buy_amount=Decimal("2"),
                sell_amount=Decimal("1"),
            ),
        ),
    )
    reordered = disclosure(
        disclosure_id="D1",
        available_at=utc("2026-09-18T08:00:00Z"),
        seats=(
            seat(
                seat_id="SEAT-B",
                source_row_id="ROW-2",
                buy_amount=Decimal("2"),
                sell_amount=Decimal("1"),
            ),
            seat(seat_id="SEAT-A", source_row_id="ROW-1", buy_amount=Decimal("1")),
        ),
    )
    accepted = lhb_repository.ingest_batch(
        repo_batch("canonical-1", disclosures=(first,))
    )
    replay = lhb_repository.ingest_batch(
        repo_batch("canonical-1", disclosures=(reordered,))
    )
    changed_digit = disclosure(
        disclosure_id="D1",
        available_at=utc("2026-09-18T08:00:00Z"),
        seats=(
            seat(seat_id="SEAT-A", source_row_id="ROW-1", buy_amount=Decimal("2")),
            seat(
                seat_id="SEAT-B",
                source_row_id="ROW-2",
                buy_amount=Decimal("2"),
                sell_amount=Decimal("1"),
            ),
        ),
    )
    changed_date = disclosure(
        disclosure_id="D1",
        available_at=utc("2026-09-18T08:00:01Z"),
        seats=(
            seat(seat_id="SEAT-A", source_row_id="ROW-1", buy_amount=Decimal("1")),
            seat(
                seat_id="SEAT-B",
                source_row_id="ROW-2",
                buy_amount=Decimal("2"),
                sell_amount=Decimal("1"),
            ),
        ),
    )
    changed_null = disclosure(
        disclosure_id="D1",
        available_at=utc("2026-09-18T08:00:00Z"),
        seats=(
            seat(
                seat_id="SEAT-A",
                source_row_id="ROW-1",
                buy_amount=Decimal("1"),
                sell_amount=None,
            ),
            seat(
                seat_id="SEAT-B",
                source_row_id="ROW-2",
                buy_amount=Decimal("2"),
                sell_amount=Decimal("1"),
            ),
        ),
    )

    assert replay.status == "duplicate"
    assert replay.payload_hash == accepted.payload_hash
    # Changed content must hash differently. These variants are never ingested:
    # D1:1 is already bound to the accepted payload, and revision identity is
    # immutable (a real content change has to arrive as revision 2).
    for batch_id, changed in (
        ("canonical-digit", changed_digit),
        ("canonical-date", changed_date),
        ("canonical-null", changed_null),
    ):
        assert (
            batch_hash(repo_batch(batch_id, disclosures=(changed,)))
            != accepted.payload_hash
        )


@pytest.mark.parametrize(
    ("buy_amount", "sell_amount", "seat_completeness"),
    [
        (None, Decimal("30"), "buy_unknown"),
        (Decimal("100"), None, "sell_unknown"),
        (None, None, "both_unknown"),
    ],
)
def test_lhb_repo_null_amount_marks_revision_batch_and_attempt_incomplete(
    lhb_repository, buy_amount, sell_amount, seat_completeness
):
    # Completeness is derived from the payload amounts alone -- no batch-id
    # prefix and no NULL ``side`` involvement.
    from dragon_radar.lhb.tables import (
        lhb_disclosure_revisions,
        lhb_disclosure_seats,
        lhb_import_attempts,
        lhb_import_batches,
    )

    incomplete_revision = disclosure(
        side="net", buy_amount=buy_amount, sell_amount=sell_amount
    )
    batch_id = f"amount-incomplete-{seat_completeness}"
    result = lhb_repository.ingest_batch(
        repo_batch(batch_id, disclosures=(incomplete_revision,))
    )

    assert result.status == "incomplete"
    assert result.reason == "incomplete_revision"
    with lhb_repository.database.engine.connect() as connection:
        batch = (
            connection.execute(
                select(lhb_import_batches).where(
                    lhb_import_batches.c.batch_id == batch_id
                )
            )
            .mappings()
            .one()
        )
        attempt = (
            connection.execute(
                select(lhb_import_attempts).where(
                    lhb_import_attempts.c.status == "incomplete"
                )
            )
            .mappings()
            .one()
        )
        revision = connection.execute(select(lhb_disclosure_revisions)).mappings().one()
        seat_row = connection.execute(select(lhb_disclosure_seats)).mappings().one()
        assert batch["status"] == "incomplete"
        assert batch["reason"] == "incomplete_revision"
        assert attempt["reason"] == "incomplete_revision"
        assert attempt["details"] == {"incomplete_revisions": ["D1:1"]}
        assert revision["completeness"] == "incomplete"
        assert seat_row["completeness"] == seat_completeness

    replay = lhb_repository.ingest_batch(
        repo_batch(batch_id, disclosures=(incomplete_revision,))
    )
    assert replay.status == "duplicate"
    assert replay.reason is None
    assert replay.payload_hash == result.payload_hash


def test_lhb_repo_null_side_with_known_amounts_is_accepted_and_complete(
    lhb_repository,
):
    # ``side`` is display-only metadata: a NULL side with both amounts known
    # stays accepted and complete on both the revision and seat rows.
    from dragon_radar.lhb.tables import (
        lhb_disclosure_revisions,
        lhb_disclosure_seats,
        lhb_import_attempts,
    )

    side_null = disclosure(side=None)
    result = lhb_repository.ingest_batch(
        repo_batch("side-null-accepted-1", disclosures=(side_null,))
    )

    assert result.status == "accepted"
    assert result.reason is None
    with lhb_repository.database.engine.connect() as connection:
        attempt = connection.execute(select(lhb_import_attempts)).mappings().one()
        revision = connection.execute(select(lhb_disclosure_revisions)).mappings().one()
        seat_row = connection.execute(select(lhb_disclosure_seats)).mappings().one()
        assert attempt["status"] == "accepted"
        assert attempt["reason"] is None
        assert attempt["details"] == {
            "kind": "lhb",
            "disclosure_count": 1,
            "seat_row_count": 1,
        }
        assert revision["completeness"] == "complete"
        assert seat_row["side"] is None
        assert seat_row["completeness"] == "complete"

    replay = lhb_repository.ingest_batch(
        repo_batch("side-null-accepted-1", disclosures=(side_null,))
    )
    assert replay.status == "duplicate"
    assert replay.payload_hash == result.payload_hash


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("disclosure_date", date(2026, 9, 19)),
        ("reason", "换手率达20%的证券"),
    ],
)
def test_lhb_repo_divergent_revision_chain_identity_is_rejected_with_zero_rows(
    lhb_repository, field, value
):
    # A divergent same-batch revision chain must be rejected at the trust
    # boundary: one rejected attempt, zero domain rows, no raw IntegrityError.
    from dragon_radar.lhb.tables import lhb_import_attempts

    first = _chain_revision(1)
    second = _chain_revision(2, **{field: value})

    result = lhb_repository.ingest_batch(
        repo_batch("divergent-identity", disclosures=(first, second))
    )

    assert result.status == "rejected"
    assert result.reason == "validation_failed"
    counts = lhb_table_counts(lhb_repository.database.engine)
    assert counts["lhb_import_batches"] == 0
    assert counts["lhb_disclosures"] == 0
    assert counts["lhb_disclosure_revisions"] == 0
    assert counts["lhb_disclosure_seats"] == 0
    assert counts["lhb_seats"] == 0
    assert counts["lhb_import_attempts"] == 1
    with lhb_repository.database.engine.connect() as connection:
        attempt = connection.execute(select(lhb_import_attempts)).mappings().one()
    assert attempt["status"] == "rejected"
    assert attempt["reason"] == "validation_failed"


def test_lhb_repo_currency_unit_conflict_with_stored_history_is_rejected(
    lhb_repository,
):
    # A new payload that changes currency/unit for an already-stored
    # (source_id, seat_id) must fail closed as a structured conflict without
    # polluting the existing rows.
    from dragon_radar.lhb.tables import lhb_import_attempts
    from dragon_radar.storage.repository import Conflict

    first = disclosure(
        disclosure_id="D1",
        source_row_id="ROW-1",
        currency="CNY",
        unit="yuan",
    )
    accepted = lhb_repository.ingest_batch(repo_batch("money-1", disclosures=(first,)))
    assert accepted.status == "accepted"

    changed = disclosure(
        disclosure_id="D2",
        source_row_id="ROW-2",
        currency="USD",
        unit="dollar",
    )
    with pytest.raises(Conflict, match="currency/unit") as conflict:
        lhb_repository.ingest_batch(repo_batch("money-2", disclosures=(changed,)))
    assert "lhb_disclosure_seats" not in str(conflict.value)

    # The original rows stay untouched and the rejected attempt is audited.
    counts = lhb_table_counts(lhb_repository.database.engine)
    assert counts["lhb_import_batches"] == 1
    assert counts["lhb_disclosure_revisions"] == 1
    assert counts["lhb_disclosure_seats"] == 1
    with lhb_repository.database.engine.connect() as connection:
        attempts = (
            connection.execute(
                select(
                    lhb_import_attempts.c.status,
                    lhb_import_attempts.c.reason,
                    lhb_import_attempts.c.details,
                )
            )
            .mappings()
            .all()
        )
    # The fixed clock stamps both attempts with the same created_at and the
    # tie-break id is a random uuid4(), so ordering by (created_at, id) cannot
    # reproduce insertion order. Compare statuses as a multiset and locate the
    # rejected row by its status instead of relying on a positional index.
    assert sorted(row["status"] for row in attempts) == [
        "accepted",
        "rejected",
    ]
    rejected = next(row for row in attempts if row["status"] == "rejected")
    assert rejected["reason"] == "currency_unit_conflict"
    assert rejected["details"] == {"kind": "lhb"}

    # Same currency/unit on a new revision still goes through.
    consistent = disclosure(
        disclosure_id="D3",
        source_row_id="ROW-3",
        currency="CNY",
        unit="yuan",
    )
    follow_up = lhb_repository.ingest_batch(
        repo_batch("money-3", disclosures=(consistent,))
    )
    assert follow_up.status == "accepted"


def test_lhb_repo_integrity_error_becomes_structured_conflict(
    lhb_repository, monkeypatch
):
    # Backstop: when a constraint violation escapes the pre-checks, the raw
    # SQLAlchemy IntegrityError (SQL text / constraint name) must never reach
    # the caller; it becomes a structured Conflict plus a rejected attempt.
    from dragon_radar.lhb.repository import LHBRepository
    from dragon_radar.lhb.tables import lhb_import_attempts
    from dragon_radar.storage.repository import Conflict

    monkeypatch.setattr(
        LHBRepository,
        "_insert_seat_identities",
        staticmethod(lambda connection, batch, digest: None),
    )

    with pytest.raises(Conflict, match="Conflicting LHB batch") as conflict:
        lhb_repository.ingest_batch(repo_batch("integrity-1"))
    message = str(conflict.value)
    assert conflict.value.__cause__ is None
    for leaked in ("INSERT", "lhb_", "psycopg", "foreign key", "violat"):
        assert leaked not in message

    counts = lhb_table_counts(lhb_repository.database.engine)
    assert counts["lhb_import_batches"] == 0
    assert counts["lhb_disclosure_revisions"] == 0
    assert counts["lhb_disclosure_seats"] == 0
    assert counts["lhb_import_attempts"] == 1
    with lhb_repository.database.engine.connect() as connection:
        attempt = connection.execute(select(lhb_import_attempts)).mappings().one()
    assert attempt["status"] == "rejected"
    assert attempt["reason"] == "integrity_error"
    assert attempt["details"] == {"kind": "lhb", "error_type": "IntegrityError"}

    # The engine is still usable after the rollback.
    monkeypatch.undo()
    recovered = lhb_repository.ingest_batch(repo_batch("integrity-2"))
    assert recovered.status == "accepted"


@pytest.mark.parametrize("deauthorization", ["revoked", "expired"])
def test_lhb_repo_empty_history_header_never_leaks_unauthorized_source(
    database_url, deauthorization
):
    import json

    from factories import grant as grant_factory

    from dragon_radar.lhb.repository import LHBRepository
    from dragon_radar.storage.repository import Forbidden, Repository

    database = _upgrade_to_lhb(database_url)
    try:
        sources = Repository(database, clock=lambda: OPERATION_NOW)
        sources.register_source(grant_factory(source_id="lhb-source"))
        sources.register_source(grant_factory(source_id="lhb-other"))
        repository = LHBRepository(database, clock=lambda: OPERATION_NOW)
        # Only the unauthorized source carries this reused seat_id.
        repository.ingest_batch(
            repo_batch(
                "leak-1",
                source_id="lhb-other",
                disclosures=(
                    disclosure(
                        source_id="lhb-other",
                        source_seat_name="Other Hidden Branch",
                        mapping_version="other-map-v1",
                    ),
                ),
            )
        )

        if deauthorization == "revoked":
            sources.revoke_source("lhb-other")
        else:
            sources.register_source(
                grant_factory(
                    source_id="lhb-other",
                    expires_at=OPERATION_NOW - timedelta(hours=1),
                )
            )

        # Before the revision becomes visible there is no anchor activity: the
        # empty envelope's header must be the synthetic placeholder, never the
        # unauthorized source's catalog identity.
        empty = repository.get_seat_history(
            SeatHistorySelection(seat_id="SEAT-A", date=DISCLOSED_ON, as_of=T0)
        )
        assert empty.items == ()
        assert empty.total == 0
        assert empty.seat.source_id == "SEAT-A"
        assert empty.seat.source_seat_name == "SEAT-A"
        assert empty.seat.mapping_version == "unknown"
        rendered = empty.seat.model_dump_json()
        assert "lhb-other" not in rendered
        assert "Other Hidden Branch" not in rendered
        assert "other-map-v1" not in rendered
        assert json.loads(rendered)["seat_id"] == "SEAT-A"

        # Once the revision is visible, the unauthorized source is a hard 403.
        with pytest.raises(Forbidden):
            repository.get_seat_history(
                SeatHistorySelection(
                    seat_id="SEAT-A", date=DISCLOSED_ON, as_of=OPERATION_NOW
                )
            )
    finally:
        database.close()


def test_lhb_repo_concurrent_different_payloads_yield_one_accepted_one_conflict(
    lhb_repository,
):
    import threading

    from dragon_radar.storage.repository import Conflict

    winner = disclosure(
        disclosure_id="D1",
        source_row_id="ROW-1",
        buy_amount=Decimal("100"),
        sell_amount=Decimal("30"),
    )
    loser = disclosure(
        disclosure_id="D1",
        source_row_id="ROW-1",
        buy_amount=Decimal("200"),
        sell_amount=Decimal("60"),
    )
    outcomes = []
    barrier = threading.Barrier(2)

    def worker(payload):
        barrier.wait()
        try:
            result = lhb_repository.ingest_batch(
                repo_batch("race-1", disclosures=(payload,))
            )
            outcomes.append(("result", result.status, result.reason))
        except Conflict as error:
            outcomes.append(("conflict", str(error)))

    threads = [
        threading.Thread(target=worker, args=(winner,)),
        threading.Thread(target=worker, args=(loser,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(outcomes) == 2
    statuses = sorted(outcome[0] for outcome in outcomes)
    assert statuses == ["conflict", "result"]
    result_outcome = next(outcome for outcome in outcomes if outcome[0] == "result")
    assert result_outcome[1:] == ("accepted", None)

    counts = lhb_table_counts(lhb_repository.database.engine)
    assert counts["lhb_import_batches"] == 1
    assert counts["lhb_disclosures"] == 1
    assert counts["lhb_disclosure_revisions"] == 1
    assert counts["lhb_disclosure_seats"] == 1
    with lhb_repository.database.engine.connect() as connection:
        status_counts = dict(
            connection.execute(
                text("SELECT status, count(*) FROM lhb_import_attempts GROUP BY status")
            ).all()
        )
    assert status_counts == {"accepted": 1, "rejected": 1}


def test_lhb_repo_list_forbids_when_grant_expires_after_ingest(lhb_repository):
    from factories import grant as grant_factory

    from dragon_radar.storage.repository import Forbidden, Repository

    lhb_repository.ingest_batch(repo_batch("expiry-1"))

    before = lhb_repository.list_disclosures(
        DisclosureSelection(date=DISCLOSED_ON, as_of=OPERATION_NOW)
    )
    assert before.total == 1

    Repository(lhb_repository.database, clock=lhb_repository.clock).register_source(
        grant_factory(
            source_id="lhb-source",
            expires_at=OPERATION_NOW - timedelta(hours=1),
        )
    )
    with pytest.raises(Forbidden):
        lhb_repository.list_disclosures(
            DisclosureSelection(date=DISCLOSED_ON, as_of=OPERATION_NOW)
        )
    # Before the data exists in the read horizon there is simply nothing to
    # show, regardless of the expired grant.
    empty = lhb_repository.list_disclosures(
        DisclosureSelection(date=DISCLOSED_ON, as_of=T0)
    )
    assert empty.total == 0


def test_lhb_repo_list_forbids_revision_with_future_captured_snapshot(database_url):
    from uuid import uuid4

    from factories import grant as grant_factory
    from sqlalchemy import insert

    from dragon_radar.lhb.repository import LHBRepository
    from dragon_radar.lhb.tables import (
        lhb_disclosure_revisions,
        lhb_disclosure_seats,
        lhb_disclosures,
        lhb_import_batches,
        lhb_seats,
    )
    from dragon_radar.storage.repository import Forbidden, Repository

    # Revision rows are immutable, so a future captured_at snapshot is staged
    # through a raw chain (the same shape a read-time late permission update
    # would present to the read path).
    future_snapshot = PermissionSnapshot(
        permission_id="lhb-permission-future",
        state="authorized",
        captured_at=OPERATION_NOW + timedelta(hours=1),
    ).model_dump(mode="json")

    database = _upgrade_to_lhb(database_url)
    try:
        Repository(database, clock=lambda: OPERATION_NOW).register_source(
            grant_factory(source_id="lhb-source")
        )
        repository = LHBRepository(database, clock=lambda: OPERATION_NOW)
        with database.engine.begin() as connection:
            connection.execute(
                insert(lhb_import_batches).values(
                    id=uuid4(),
                    batch_id="snapshot-future",
                    source_id="lhb-source",
                    source_version="v1",
                    source_event_at=T0,
                    received_at=T0 + timedelta(seconds=1),
                    available_at=T0 + timedelta(seconds=2),
                    permission_snapshot=future_snapshot,
                    payload_hash="c" * 64,
                    status="accepted",
                    reason=None,
                    imported_at=OPERATION_NOW,
                )
            )
            connection.execute(
                insert(lhb_disclosures).values(
                    id=uuid4(),
                    source_id="lhb-source",
                    disclosure_id="D-FUTURE",
                    security_id="600000.SH",
                    disclosure_date=DISCLOSED_ON,
                    reason="future captured snapshot disclosure",
                    permission_id="lhb-permission-future",
                )
            )
            connection.execute(
                insert(lhb_seats).values(
                    id=uuid4(),
                    source_id="lhb-source",
                    seat_id="SEAT-FUTURE",
                    mapping_version="seat-map-v1",
                    source_seat_name="Future snapshot branch",
                    provenance={
                        "mapping_version": "seat-map-v1",
                        "source_id": "lhb-source",
                    },
                )
            )
            connection.execute(
                insert(lhb_disclosure_revisions).values(
                    id=uuid4(),
                    source_id="lhb-source",
                    disclosure_id="D-FUTURE",
                    batch_id="snapshot-future",
                    revision=1,
                    source_event_at=T0,
                    received_at=T0 + timedelta(seconds=1),
                    available_at=T0 + timedelta(seconds=2),
                    permission_snapshot=future_snapshot,
                    completeness="complete",
                    payload_hash="d" * 64,
                )
            )
            connection.execute(
                insert(lhb_disclosure_seats).values(
                    id=uuid4(),
                    source_id="lhb-source",
                    disclosure_id="D-FUTURE",
                    disclosure_date=DISCLOSED_ON,
                    revision=1,
                    source_row_id="ROW-FUTURE",
                    seat_id="SEAT-FUTURE",
                    mapping_version="seat-map-v1",
                    side="net",
                    rank=None,
                    buy_amount=Decimal("1"),
                    sell_amount=Decimal("1"),
                    currency="CNY",
                    unit="yuan",
                    completeness="complete",
                )
            )

        with pytest.raises(Forbidden):
            repository.list_disclosures(
                DisclosureSelection(date=DISCLOSED_ON, as_of=OPERATION_NOW)
            )
    finally:
        database.close()


def test_lhb_repo_revision_append_keeps_immutable_history(lhb_repository):
    from dragon_radar.lhb.tables import (
        lhb_disclosure_revisions,
        lhb_disclosure_seats,
    )

    first = disclosure(
        revision=1,
        side="net",
        buy_amount=Decimal("100"),
        sell_amount=Decimal("30"),
        available_at=utc("2026-09-18T08:00:02Z"),
    )
    second = revision_with_earlier_availability(
        2,
        side="net",
        buy_amount=Decimal("110"),
        sell_amount=Decimal("35"),
    )
    lhb_repository.ingest_batch(repo_batch("append-1", disclosures=(first,)))
    accepted = lhb_repository.ingest_batch(
        repo_batch("append-2", disclosures=(second,))
    )

    assert accepted.status == "accepted"
    with lhb_repository.database.engine.connect() as connection:
        revisions = connection.execute(
            select(
                lhb_disclosure_revisions.c.revision,
                lhb_disclosure_revisions.c.payload_hash,
            ).order_by(lhb_disclosure_revisions.c.revision)
        ).all()
        row_count = (
            connection.execute(
                select(lhb_disclosure_seats).where(lhb_disclosure_seats.c.revision == 1)
            )
            .mappings()
            .all()
        )
    assert [revision for revision, _ in revisions] == [1, 2]
    assert len({digest for _, digest in revisions}) == 2
    assert len(row_count) == 1
    assert row_count[0]["buy_amount"] == Decimal("100")

    # Visibility follows each revision's own available_at only; the carrying
    # batch's availability never gates it. rev2 announces at T0+0.5s, so before
    # that cut nothing is visible, at T0+1s only rev2 is visible and wins by
    # revision number, and once rev1 appears later rev2 still stays selected.
    early = lhb_repository.list_disclosures(
        DisclosureSelection(date=DISCLOSED_ON, as_of=T0 + timedelta(milliseconds=400))
    )
    assert early.items == ()
    revision_two_announces_first = lhb_repository.list_disclosures(
        DisclosureSelection(date=DISCLOSED_ON, as_of=T0 + timedelta(seconds=1))
    )
    assert revision_two_announces_first.items[0].revision.revision == 2
    assert revision_two_announces_first.items[0].revision.seats[
        0
    ].buy_amount == Decimal("110")
    latest = lhb_repository.list_disclosures(
        DisclosureSelection(date=DISCLOSED_ON, as_of=LIST_AS_OF)
    )
    assert latest.items[0].revision.revision == 2
    assert latest.items[0].revision.seats[0].buy_amount == Decimal("110")


def test_lhb_repo_latest_revision_number_wins_even_with_earlier_availability(
    lhb_repository,
):
    # Mandatory regression: revision is the authoritative correction sequence.
    # rev2 becomes available EARLIER than rev1; once both are visible rev2
    # wins, and rev2 stays hidden before its (earlier) available_at.
    rev1 = disclosure(
        revision=1,
        side="net",
        buy_amount=Decimal("100"),
        available_at=utc("2026-09-18T08:00:02Z"),
    )
    rev2 = revision_with_earlier_availability(2, side="net", buy_amount=Decimal("110"))
    lhb_repository.ingest_batch(repo_batch("order-1", disclosures=(rev1,)))
    lhb_repository.ingest_batch(repo_batch("order-2", disclosures=(rev2,)))

    hidden = lhb_repository.list_disclosures(
        DisclosureSelection(date=DISCLOSED_ON, as_of=utc("2026-09-18T08:00:00Z"))
    )
    assert hidden.items == ()
    both_visible = lhb_repository.list_disclosures(
        DisclosureSelection(date=DISCLOSED_ON, as_of=LIST_AS_OF)
    )
    assert len(both_visible.items) == 1
    selected = both_visible.items[0].revision
    assert selected.revision == 2
    assert selected.seats[0].buy_amount == Decimal("110")


def test_lhb_repo_same_name_distinct_seat_ids_are_both_persisted(lhb_repository):
    from dragon_radar.lhb.tables import lhb_disclosure_seats

    named = disclosure(
        seats=(
            seat(seat_id="SEAT-A", source_row_id="ROW-1"),
            seat(
                seat_id="SEAT-B",
                source_row_id="ROW-2",
                buy_amount=Decimal("5"),
                sell_amount=Decimal("1"),
            ),
        )
    )
    lhb_repository.ingest_batch(repo_batch("named-1", disclosures=(named,)))

    page = lhb_repository.list_disclosures(
        DisclosureSelection(date=DISCLOSED_ON, as_of=LIST_AS_OF)
    )
    seats = page.items[0].revision.seats
    assert [row.seat_id for row in seats] == ["SEAT-A", "SEAT-B"]
    assert {row.source_seat_name for row in seats} == {"Example branch"}
    with lhb_repository.database.engine.connect() as connection:
        assert len(connection.execute(select(lhb_disclosure_seats)).all()) == 2


def test_lhb_repo_shared_disclosure_identity_conflict_is_fail_closed(
    lhb_repository,
):
    from dragon_radar.storage.repository import Conflict

    lhb_repository.ingest_batch(repo_batch("identity-1"))
    divergent = disclosure(revision=2, reason="different reason text")
    with pytest.raises(Conflict, match="disclosure identity"):
        lhb_repository.ingest_batch(repo_batch("identity-2", disclosures=(divergent,)))
    # The rejected batch leaves no revision rows behind.
    counts = lhb_table_counts(lhb_repository.database.engine)
    assert counts["lhb_import_batches"] == 1
    assert counts["lhb_disclosure_revisions"] == 1
    assert counts["lhb_import_attempts"] == 2


def test_lhb_repo_shared_disclosure_identity_is_reused_when_identical(
    lhb_repository,
):
    from dragon_radar.lhb.tables import lhb_disclosures

    another = disclosure(
        disclosure_id="D2",
        source_row_id="ROW-2",
        buy_amount=Decimal("5"),
        sell_amount=Decimal("1"),
    )
    lhb_repository.ingest_batch(repo_batch("shared-1"))
    lhb_repository.ingest_batch(repo_batch("shared-2", disclosures=(another,)))

    with lhb_repository.database.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(lhb_disclosures)) == 2


def test_lhb_repo_seat_catalog_same_key_same_name_is_idempotent(lhb_repository):
    from dragon_radar.lhb.tables import lhb_seats

    second_day = disclosure(
        disclosure_id="D2",
        disclosure_date=date(2026, 9, 19),
        source_row_id="ROW-2",
        available_at=utc("2026-09-19T08:00:00Z"),
    )
    lhb_repository.ingest_batch(repo_batch("seat-1"))
    lhb_repository.ingest_batch(repo_batch("seat-2", disclosures=(second_day,)))

    with lhb_repository.database.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(lhb_seats)) == 1


def test_lhb_repo_seat_catalog_same_key_different_name_conflicts(lhb_repository):
    from dragon_radar.storage.repository import Conflict

    renamed = disclosure(
        disclosure_id="D2",
        disclosure_date=date(2026, 9, 19),
        source_row_id="ROW-2",
        source_seat_name="Renamed branch",
        available_at=utc("2026-09-19T08:00:00Z"),
    )
    lhb_repository.ingest_batch(repo_batch("seat-rename-1"))

    with pytest.raises(Conflict, match="seat identity"):
        lhb_repository.ingest_batch(repo_batch("seat-rename-2", disclosures=(renamed,)))
    counts = lhb_table_counts(lhb_repository.database.engine)
    assert counts["lhb_seats"] == 1
    assert counts["lhb_disclosure_seats"] == 1


def test_lhb_repo_same_revision_number_with_different_hash_conflicts(
    lhb_repository,
):
    from dragon_radar.storage.repository import Conflict

    recarried = disclosure(
        revision=1, buy_amount=Decimal("999"), sell_amount=Decimal("1")
    )
    lhb_repository.ingest_batch(repo_batch("recarry-1"))

    with pytest.raises(Conflict, match="revision identity"):
        lhb_repository.ingest_batch(repo_batch("recarry-2", disclosures=(recarried,)))


def test_lhb_repo_identical_recarried_revision_is_idempotent_and_adds_disclosure(
    lhb_repository,
):
    from dragon_radar.lhb.tables import lhb_disclosure_revisions

    same_revision = disclosure()
    second = disclosure(
        disclosure_id="D2",
        source_row_id="ROW-2",
        buy_amount=Decimal("5"),
        sell_amount=Decimal("1"),
    )
    lhb_repository.ingest_batch(repo_batch("recarry-ok-1"))
    lhb_repository.ingest_batch(
        repo_batch("recarry-ok-2", disclosures=(same_revision, second))
    )

    with lhb_repository.database.engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(lhb_disclosure_revisions)
            )
            == 2
        )


def test_lhb_repo_revoked_grant_after_import_forbids_both_reads(lhb_repository):
    from dragon_radar.storage.repository import Forbidden, Repository

    lhb_repository.ingest_batch(repo_batch())
    Repository(lhb_repository.database, clock=lhb_repository.clock).revoke_source(
        "lhb-source"
    )

    with pytest.raises(Forbidden):
        lhb_repository.list_disclosures(
            DisclosureSelection(date=DISCLOSED_ON, as_of=LIST_AS_OF)
        )
    with pytest.raises(Forbidden):
        lhb_repository.get_seat_history(
            SeatHistorySelection(seat_id="SEAT-A", date=DISCLOSED_ON)
        )


def test_lhb_repo_read_and_ingest_require_typed_models(lhb_repository):
    with pytest.raises(TypeError, match="LHBImportBatch"):
        lhb_repository.ingest_batch(repo_batch().model_dump())
    with pytest.raises(TypeError, match="LHBImportBatch"):
        lhb_repository.ingest_batch(disclosure())
    with pytest.raises(TypeError, match="DisclosureSelection"):
        lhb_repository.list_disclosures({"date": DISCLOSED_ON})
    with pytest.raises(TypeError, match="SeatHistorySelection"):
        lhb_repository.get_seat_history({"seat_id": "SEAT-A", "date": DISCLOSED_ON})


def test_lhb_repo_null_zero_and_decimal_scale_round_trip(lhb_repository):
    precise_revision = disclosure(
        seats=(
            seat(
                seat_id="SEAT-A",
                source_row_id="ROW-1",
                side="net",
                rank=3,
                buy_amount=None,
                sell_amount=Decimal("12345678901234567890.123456789012345678"),
            ),
        )
    )
    lhb_repository.ingest_batch(
        repo_batch("precision-1", disclosures=(precise_revision,))
    )

    page = lhb_repository.list_disclosures(
        DisclosureSelection(date=DISCLOSED_ON, as_of=LIST_AS_OF)
    )
    row = page.items[0].revision.seats[0]
    assert row.buy_amount is None
    assert row.sell_amount == Decimal("12345678901234567890.123456789012345678")
    assert isinstance(row.sell_amount, Decimal)
    assert row.rank == 3
    assert row.side == "net"


def test_lhb_repo_zero_amount_stays_zero(lhb_repository):
    lhb_repository.ingest_batch(
        repo_batch(
            "zero-1",
            disclosures=(disclosure(side="net", buy_amount=Decimal("0")),),
        )
    )

    page = lhb_repository.list_disclosures(
        DisclosureSelection(date=DISCLOSED_ON, as_of=LIST_AS_OF)
    )
    row = page.items[0].revision.seats[0]
    assert row.buy_amount == Decimal("0")
    assert row.sell_amount == Decimal("30")


# --- read semantics -------------------------------------------------------


def test_lhb_repo_list_disclosures_matches_date_and_security_with_order(
    lhb_repository,
):
    other_security = disclosure(
        disclosure_id="D2",
        source_row_id="ROW-2",
        security_id="000001.SZ",
        buy_amount=Decimal("5"),
        sell_amount=Decimal("1"),
    )
    other_day = disclosure(
        disclosure_id="D3",
        disclosure_date=date(2026, 9, 19),
        source_row_id="ROW-3",
        security_id="000001.SZ",
        available_at=utc("2026-09-19T08:00:00Z"),
    )
    lhb_repository.ingest_batch(
        repo_batch("list-1", disclosures=(disclosure(), other_security, other_day))
    )

    page = lhb_repository.list_disclosures(
        DisclosureSelection(date=DISCLOSED_ON, as_of=LIST_AS_OF)
    )
    assert page.total == 2
    assert [item.revision.disclosure_id for item in page.items] == ["D1", "D2"]
    assert page.as_of == LIST_AS_OF

    scoped = lhb_repository.list_disclosures(
        DisclosureSelection(
            date=DISCLOSED_ON, security_id="000001.SZ", as_of=LIST_AS_OF
        )
    )
    assert scoped.total == 1
    assert scoped.items[0].revision.disclosure_id == "D2"

    # A disclosure dated on the 19th announced at 08:00 stays hidden to a read
    # taken at 10:00 on the 18th, even when querying its own calendar date.
    other_date_hidden = lhb_repository.list_disclosures(
        DisclosureSelection(date=date(2026, 9, 19), as_of=LIST_AS_OF)
    )
    assert other_date_hidden.total == 0
    assert other_date_hidden.items == ()
    other_date = lhb_repository.list_disclosures(
        DisclosureSelection(date=date(2026, 9, 19), as_of=utc("2026-09-19T09:00:00Z"))
    )
    assert other_date.total == 1
    assert other_date.items[0].revision.disclosure_id == "D3"


def test_lhb_repo_list_disclosures_paginates_deterministically_with_stable_total(
    lhb_repository,
):
    disclosures = tuple(
        disclosure(
            disclosure_id=f"D{index}",
            source_row_id=f"ROW-{index}",
            seat_id=f"SEAT-{index}",
            buy_amount=Decimal(index),
            sell_amount=Decimal("1"),
        )
        for index in range(1, 6)
    )
    lhb_repository.ingest_batch(repo_batch("page-1", disclosures=disclosures))

    pages = [
        lhb_repository.list_disclosures(
            DisclosureSelection(date=DISCLOSED_ON, page=page, size=2, as_of=LIST_AS_OF)
        )
        for page in (1, 2, 3)
    ]
    assert [page.total for page in pages] == [5, 5, 5]
    assert [[item.revision.disclosure_id for item in page.items] for page in pages] == [
        ["D1", "D2"],
        ["D3", "D4"],
        ["D5"],
    ]
    assert all(page.size == 2 for page in pages[:2])
    assert pages[2].page == 3


def test_lhb_repo_list_disclosures_empty_window_is_normal_empty_envelope(
    lhb_repository,
):
    lhb_repository.ingest_batch(repo_batch())

    page = lhb_repository.list_disclosures(
        DisclosureSelection(date=date(2026, 9, 1), as_of=LIST_AS_OF)
    )
    assert page.items == ()
    assert page.total == 0
    assert page.coverage == AggregationCoverage(
        total_disclosures=0,
        visible_disclosures=0,
        late_disclosures=0,
        selected_disclosures=0,
        seat_count=0,
        complete_seats=0,
        unknown_seats=0,
    )


def test_lhb_repo_list_disclosures_coverage_counts_are_deterministic(
    lhb_repository,
):
    late = disclosure(
        disclosure_id="D-late",
        source_row_id="ROW-late",
        seat_id="SEAT-LATE",
        available_at=LIST_AS_OF + timedelta(seconds=1),
    )
    unknown = disclosure(
        disclosure_id="D-unknown",
        source_row_id="ROW-unknown",
        seat_id="SEAT-U",
        buy_amount=None,
        sell_amount=Decimal("2"),
    )
    known = disclosure(
        disclosure_id="D-known",
        source_row_id="ROW-known",
        seat_id="SEAT-K",
        side="net",
        buy_amount=Decimal("9"),
        sell_amount=Decimal("4"),
    )
    lhb_repository.ingest_batch(
        repo_batch("coverage-1", disclosures=(late, unknown, known))
    )

    page = lhb_repository.list_disclosures(
        DisclosureSelection(date=DISCLOSED_ON, as_of=LIST_AS_OF, size=100)
    )
    assert page.total == 2
    assert page.coverage.total_disclosures == 3
    assert page.coverage.visible_disclosures == 2
    assert page.coverage.late_disclosures == 1
    assert page.coverage.selected_disclosures == 2
    assert page.coverage.seat_count == 2
    assert page.coverage.complete_seats == 1
    assert page.coverage.unknown_seats == 1


def test_lhb_repo_list_disclosures_defaults_as_of_to_clock(lhb_repository):
    lhb_repository.ingest_batch(repo_batch())

    page = lhb_repository.list_disclosures(DisclosureSelection(date=DISCLOSED_ON))
    assert page.as_of == OPERATION_NOW
    assert page.total == 1


def test_lhb_repo_list_disclosures_filters_individually_unauthorized_source(
    database_url,
):
    from factories import grant as grant_factory

    from dragon_radar.lhb.repository import LHBRepository
    from dragon_radar.storage.repository import Repository

    database = _upgrade_to_lhb(database_url)
    try:
        sources = Repository(database, clock=lambda: OPERATION_NOW)
        sources.register_source(grant_factory(source_id="lhb-source"))
        sources.register_source(grant_factory(source_id="lhb-other"))
        repository = LHBRepository(database, clock=lambda: OPERATION_NOW)
        repository.ingest_batch(repo_batch("multi-1"))
        repository.ingest_batch(
            repo_batch(
                "multi-2",
                source_id="lhb-other",
                source_version="v1",
                disclosures=(disclosure(source_id="lhb-other"),),
            )
        )

        both = repository.list_disclosures(
            DisclosureSelection(date=DISCLOSED_ON, as_of=OPERATION_NOW)
        )
        assert both.total == 2
        assert {item.revision.source_id for item in both.items} == {
            "lhb-other",
            "lhb-source",
        }

        sources.register_source(
            grant_factory(source_id="lhb-other", local_display=False)
        )
        filtered = repository.list_disclosures(
            DisclosureSelection(date=DISCLOSED_ON, as_of=OPERATION_NOW)
        )
        assert filtered.total == 1
        assert filtered.items[0].revision.source_id == "lhb-source"
        assert filtered.coverage.total_disclosures == 1
    finally:
        database.close()


def test_lhb_repo_list_disclosures_filters_revision_with_revoked_snapshot(
    database_url,
):
    from uuid import uuid4

    from factories import grant as grant_factory
    from sqlalchemy import insert

    from dragon_radar.lhb.repository import LHBRepository
    from dragon_radar.lhb.tables import (
        lhb_disclosure_revisions,
        lhb_disclosure_seats,
        lhb_disclosures,
        lhb_import_batches,
        lhb_seats,
    )
    from dragon_radar.storage.repository import Repository

    database = _upgrade_to_lhb(database_url)
    try:
        Repository(database, clock=lambda: OPERATION_NOW).register_source(
            grant_factory(source_id="lhb-source")
        )
        repository = LHBRepository(database, clock=lambda: OPERATION_NOW)
        repository.ingest_batch(repo_batch("snapshot-1"))
        revoked_snapshot = (
            permission().model_copy(update={"state": "revoked"}).model_dump(mode="json")
        )
        with database.engine.begin() as connection:
            connection.execute(
                insert(lhb_import_batches).values(
                    id=uuid4(),
                    batch_id="snapshot-raw",
                    source_id="lhb-source",
                    source_version="v1",
                    source_event_at=T0,
                    received_at=T0 + timedelta(seconds=1),
                    available_at=T0 + timedelta(seconds=2),
                    permission_snapshot=revoked_snapshot,
                    payload_hash="b" * 64,
                    status="accepted",
                    reason=None,
                    imported_at=OPERATION_NOW,
                )
            )
            connection.execute(
                insert(lhb_disclosures).values(
                    id=uuid4(),
                    source_id="lhb-source",
                    disclosure_id="D-REVOKED",
                    security_id="600000.SH",
                    disclosure_date=DISCLOSED_ON,
                    reason="revoked snapshot disclosure",
                    permission_id="lhb-permission-1",
                )
            )
            connection.execute(
                insert(lhb_seats).values(
                    id=uuid4(),
                    source_id="lhb-source",
                    seat_id="SEAT-REVOKED",
                    mapping_version="seat-map-v1",
                    source_seat_name="Revoked branch",
                    provenance={
                        "mapping_version": "seat-map-v1",
                        "source_id": "lhb-source",
                    },
                )
            )
            connection.execute(
                insert(lhb_disclosure_revisions).values(
                    id=uuid4(),
                    source_id="lhb-source",
                    disclosure_id="D-REVOKED",
                    batch_id="snapshot-raw",
                    revision=1,
                    source_event_at=T0,
                    received_at=T0 + timedelta(seconds=1),
                    available_at=T0 + timedelta(seconds=2),
                    permission_snapshot=revoked_snapshot,
                    completeness="complete",
                    payload_hash="a" * 64,
                )
            )
            connection.execute(
                insert(lhb_disclosure_seats).values(
                    id=uuid4(),
                    source_id="lhb-source",
                    disclosure_id="D-REVOKED",
                    disclosure_date=DISCLOSED_ON,
                    revision=1,
                    source_row_id="ROW-REVOKED",
                    seat_id="SEAT-REVOKED",
                    mapping_version="seat-map-v1",
                    side="net",
                    rank=None,
                    buy_amount=Decimal("1"),
                    sell_amount=Decimal("1"),
                    currency="CNY",
                    unit="yuan",
                    completeness="complete",
                )
            )

        page = repository.list_disclosures(
            DisclosureSelection(date=DISCLOSED_ON, as_of=OPERATION_NOW)
        )
        assert page.total == 1
        assert page.items[0].revision.disclosure_id == "D1"
    finally:
        database.close()


def test_lhb_repo_reads_reconstruct_through_strict_pydantic_models(lhb_repository):
    from dragon_radar.lhb.models import (
        DisclosureRevisionInput,
        DisclosureSeatInput,
        PermissionSnapshot,
        SelectedDisclosure,
    )

    lhb_repository.ingest_batch(repo_batch())

    page = lhb_repository.list_disclosures(
        DisclosureSelection(date=DISCLOSED_ON, as_of=LIST_AS_OF)
    )
    item = page.items[0]
    assert type(item) is SelectedDisclosure
    assert type(item.revision) is DisclosureRevisionInput
    assert type(item.revision.seats[0]) is DisclosureSeatInput
    assert type(item.revision.permission) is PermissionSnapshot
    assert item.revision.source_version == "v1"
    assert item.revision.security_id == "600000.SH"
    assert item.revision.reason == "日涨幅偏离值达7%"
    assert item.revision.permission.state == "authorized"


def test_lhb_repo_reads_are_strictly_read_only(lhb_repository):
    lhb_repository.ingest_batch(repo_batch())
    lhb_repository.ingest_batch(
        repo_batch(
            "history-seed",
            disclosures=(
                disclosure(
                    disclosure_id="D2",
                    disclosure_date=date(2026, 9, 17),
                    source_row_id="ROW-2",
                    available_at=utc("2026-09-17T08:00:00Z"),
                ),
            ),
        )
    )
    before = lhb_table_counts(lhb_repository.database.engine)

    list_page = lhb_repository.list_disclosures(
        DisclosureSelection(date=DISCLOSED_ON, as_of=LIST_AS_OF)
    )
    history = lhb_repository.get_seat_history(
        SeatHistorySelection(seat_id="SEAT-A", date=DISCLOSED_ON, as_of=LIST_AS_OF)
    )
    assert list_page.total == 1
    assert history.total == 2

    assert lhb_table_counts(lhb_repository.database.engine) == before


def test_lhb_repo_seat_history_groups_per_source_and_day_with_pure_parity(
    lhb_repository,
):
    from dragon_radar.lhb.service import aggregate_seats

    monday = disclosure(
        disclosure_id="D-MON",
        disclosure_date=date(2026, 9, 14),
        source_row_id="ROW-MON",
        side="net",
        buy_amount=Decimal("40"),
        sell_amount=Decimal("10"),
        available_at=utc("2026-09-14T10:00:00Z"),
    )
    tuesday = disclosure(
        disclosure_id="D-TUE",
        disclosure_date=date(2026, 9, 15),
        source_row_id="ROW-TUE",
        side="net",
        buy_amount=Decimal("50"),
        sell_amount=Decimal("20"),
        available_at=utc("2026-09-15T10:00:00Z"),
    )
    friday = disclosure(
        disclosure_id="D-FRI",
        disclosure_date=DISCLOSED_ON,
        source_row_id="ROW-FRI",
        side="net",
        buy_amount=Decimal("100"),
        sell_amount=Decimal("30"),
        available_at=utc("2026-09-18T08:00:00Z"),
    )
    lhb_repository.ingest_batch(
        repo_batch("hist-1", disclosures=(monday, tuesday, friday))
    )

    history = lhb_repository.get_seat_history(
        SeatHistorySelection(
            seat_id="SEAT-A", date=DISCLOSED_ON, days=30, as_of=LIST_AS_OF
        )
    )
    assert history.total == 3
    assert [item.buy_amount for item in history.items] == [
        Decimal("100"),
        Decimal("50"),
        Decimal("40"),
    ]
    assert [item.net_amount for item in history.items] == [
        Decimal("70"),
        Decimal("30"),
        Decimal("30"),
    ]
    by_date = {item.contributions[0].disclosure_date: item for item in history.items}
    for revision in (monday, tuesday, friday):
        expected = aggregate_seats([revision], as_of=LIST_AS_OF).seats[0]
        assert by_date[revision.disclosure_date] == expected
    assert history.seat == SeatIdentity(
        source_id="lhb-source",
        seat_id="SEAT-A",
        source_seat_name="Example branch",
        mapping_version="seat-map-v1",
    )
    assert history.coverage.total_disclosures == 3
    assert history.coverage.visible_disclosures == 3
    assert history.coverage.late_disclosures == 0
    assert history.coverage.selected_disclosures == 3
    assert history.coverage.seat_count == 3
    assert history.coverage.complete_seats == 3
    assert history.coverage.unknown_seats == 0


def test_lhb_repo_sql_matches_pure_aggregate_across_visibility_cuts(
    lhb_repository,
):
    # SQL<->pure parity regression: the same fixture revisions are fed to the
    # pure aggregate_seats and to the SQL-backed list/history reads. At every
    # as_of cut both paths must select the same revision per (source_id,
    # disclosure_id) and produce identical aggregated seats (buy/sell/net/
    # completeness) and wall-clock headers (name/mapping/source_version).
    thursday = date(2026, 9, 17)
    d1r1 = disclosure(
        disclosure_id="D1",
        revision=1,
        source_row_id="ROW-1",
        side="net",
        buy_amount=Decimal("100"),
        sell_amount=Decimal("30"),
        available_at=utc("2026-09-18T08:00:00Z"),
    )
    d1r2 = disclosure(
        disclosure_id="D1",
        revision=2,
        source_version="v2",
        source_row_id="ROW-1",
        source_seat_name="Corrected branch",
        mapping_version="seat-map-v2",
        side="net",
        buy_amount=Decimal("110"),
        sell_amount=Decimal("35"),
        available_at=utc("2026-09-18T12:00:00Z"),
    )
    d2r1 = disclosure(
        disclosure_id="D2",
        revision=1,
        source_row_id="ROW-2",
        side="net",
        buy_amount=Decimal("50"),
        sell_amount=Decimal("20"),
        available_at=utc("2026-09-18T09:30:00Z"),
    )
    d3r1 = disclosure(
        disclosure_id="D3",
        revision=1,
        disclosure_date=thursday,
        source_row_id="ROW-3",
        side="net",
        buy_amount=Decimal("200"),
        sell_amount=Decimal("80"),
        source_event_at=utc("2026-09-17T08:59:58Z"),
        received_at=utc("2026-09-17T08:59:59Z"),
        available_at=utc("2026-09-17T09:00:00Z"),
    )
    d3r2 = disclosure(
        disclosure_id="D3",
        revision=2,
        disclosure_date=thursday,
        source_row_id="ROW-3",
        side="net",
        buy_amount=Decimal("210"),
        sell_amount=Decimal("85"),
        source_event_at=utc("2026-09-16T05:59:58Z"),
        received_at=utc("2026-09-16T05:59:59Z"),
        available_at=utc("2026-09-16T06:00:00Z"),
    )
    lhb_repository.ingest_batch(repo_batch("parity-1", disclosures=(d1r1, d2r1, d3r1)))
    lhb_repository.ingest_batch(
        repo_batch("parity-2", source_version="v2", disclosures=(d1r2,))
    )
    lhb_repository.ingest_batch(repo_batch("parity-3", disclosures=(d3r2,)))

    friday_candidates = (d1r1, d1r2, d2r1)
    thursday_candidates = (d3r1, d3r2)
    all_candidates = (d1r1, d1r2, d2r1, d3r1, d3r2)

    cut_before_anything = utc("2026-09-16T05:00:00Z")
    cut_early = utc("2026-09-18T08:30:00Z")
    cut_same_day_before = utc("2026-09-18T09:00:00Z")
    cut_same_day_boundary = utc("2026-09-18T09:30:00Z")
    cut_future_rev2 = utc("2026-09-18T10:00:00Z")
    cut_latest = utc("2026-09-18T13:00:00Z")

    empty_list = lhb_repository.list_disclosures(
        DisclosureSelection(date=DISCLOSED_ON, as_of=cut_before_anything, size=100)
    )
    assert empty_list.items == ()
    assert empty_list.total == 0
    empty_history = lhb_repository.get_seat_history(
        SeatHistorySelection(
            seat_id="SEAT-A",
            date=DISCLOSED_ON,
            days=30,
            as_of=cut_before_anything,
        )
    )
    assert empty_history.items == ()
    assert empty_history.total == 0

    # as_of cut -> expected selected revision per Friday disclosure id.
    list_expectations = {
        cut_early: {"D1": 1},
        cut_same_day_before: {"D1": 1},
        cut_same_day_boundary: {"D1": 1, "D2": 1},
        cut_future_rev2: {"D1": 1, "D2": 1},
        cut_latest: {"D1": 2, "D2": 1},
    }
    for cut, expected_selection in list_expectations.items():
        page = lhb_repository.list_disclosures(
            DisclosureSelection(date=DISCLOSED_ON, as_of=cut, size=100)
        )
        sql_selection = {
            item.revision.disclosure_id: item.revision.revision for item in page.items
        }
        assert sql_selection == expected_selection, cut
        pure = aggregate_seats(friday_candidates, as_of=cut)
        pure_selection = {
            contribution.disclosure_id: contribution.revision
            for contribution in pure.seats[0].contributions
        }
        assert pure_selection == expected_selection, cut
        sql_aggregated = aggregate_seats(
            tuple(item.revision for item in page.items), as_of=cut
        )
        assert sql_aggregated.seats == pure.seats, cut
        assert page.coverage.total_disclosures == 2
        assert page.coverage.visible_disclosures == len(expected_selection)
        assert page.coverage.late_disclosures == 2 - len(expected_selection)

    # (i) A future rev2 hides itself but must not discard the still-visible
    # rev1 -- neither in list nor in history.
    friday_page = lhb_repository.list_disclosures(
        DisclosureSelection(date=DISCLOSED_ON, as_of=cut_future_rev2, size=100)
    )
    d1_selected = next(
        item.revision
        for item in friday_page.items
        if item.revision.disclosure_id == "D1"
    )
    assert d1_selected.revision == 1
    assert d1_selected.seats[0].buy_amount == Decimal("100")

    # (ii) Same-day late announcement: hidden at 09:00, visible at the exact
    # 09:30 boundary (<= as_of).
    before = lhb_repository.get_seat_history(
        SeatHistorySelection(
            seat_id="SEAT-A", date=DISCLOSED_ON, days=30, as_of=cut_same_day_before
        )
    )
    friday_item_before = next(
        item
        for item in before.items
        if item.contributions[0].disclosure_date == DISCLOSED_ON
    )
    assert {
        (entry.disclosure_id, entry.revision)
        for entry in friday_item_before.contributions
    } == {("D1", 1)}
    at_boundary = lhb_repository.get_seat_history(
        SeatHistorySelection(
            seat_id="SEAT-A",
            date=DISCLOSED_ON,
            days=30,
            as_of=cut_same_day_boundary,
        )
    )
    friday_item_at_boundary = next(
        item
        for item in at_boundary.items
        if item.contributions[0].disclosure_date == DISCLOSED_ON
    )
    assert {
        (entry.disclosure_id, entry.revision)
        for entry in friday_item_at_boundary.contributions
    } == {("D1", 1), ("D2", 1)}

    # History parity at every cut: one item per (source, date), source ascending
    # then date descending (Friday group before Thursday group).
    for cut in (
        cut_early,
        cut_same_day_before,
        cut_same_day_boundary,
        cut_future_rev2,
        cut_latest,
    ):
        history = lhb_repository.get_seat_history(
            SeatHistorySelection(
                seat_id="SEAT-A", date=DISCLOSED_ON, days=30, as_of=cut
            )
        )
        assert len(history.items) == 2, cut
        assert [item.contributions[0].disclosure_date for item in history.items] == [
            DISCLOSED_ON,
            thursday,
        ], cut
        friday_pure = aggregate_seats(friday_candidates, as_of=cut)
        thursday_pure = aggregate_seats(thursday_candidates, as_of=cut)
        assert history.items[0] == friday_pure.seats[0], cut
        assert history.items[1] == thursday_pure.seats[0], cut
        header_pure = aggregate_seats(all_candidates, as_of=cut).seats[0]
        assert history.seat.source_id == header_pure.source_id
        assert history.seat.source_seat_name == header_pure.source_seat_name
        assert history.seat.mapping_version == header_pure.mapping_version
        assert history.total == 2, cut

    # (i) continued: at the 10:00 cut the Friday history item still carries
    # rev1's amounts while rev2 (12:00) is hidden.
    future_cut_history = lhb_repository.get_seat_history(
        SeatHistorySelection(
            seat_id="SEAT-A", date=DISCLOSED_ON, days=30, as_of=cut_future_rev2
        )
    )
    friday_visible = future_cut_history.items[0]
    assert friday_visible.buy_amount == Decimal("150")
    assert friday_visible.sell_amount == Decimal("50")
    assert friday_visible.net_amount == Decimal("100")

    # (iii) rev2 of D3 is available EARLIER than rev1; the revision number is
    # authoritative and wins at every cut, so Thursday carries rev2's amounts.
    early_history = lhb_repository.get_seat_history(
        SeatHistorySelection(
            seat_id="SEAT-A", date=DISCLOSED_ON, days=30, as_of=cut_early
        )
    )
    thursday_item = early_history.items[1]
    assert thursday_item.contributions[0].revision == 2
    assert thursday_item.buy_amount == Decimal("210")
    assert thursday_item.sell_amount == Decimal("85")
    assert thursday_item.net_amount == Decimal("125")
    assert thursday_item.completeness == "complete"

    latest_history = lhb_repository.get_seat_history(
        SeatHistorySelection(
            seat_id="SEAT-A", date=DISCLOSED_ON, days=30, as_of=cut_latest
        )
    )
    assert latest_history.items[0].buy_amount == Decimal("160")
    assert latest_history.items[0].sell_amount == Decimal("55")
    assert latest_history.items[0].net_amount == Decimal("105")
    # Header follows wall-clock availability across every selected revision:
    # rev2 of D1 (12:00, v2 rename) is newest at the latest cut; at the early
    # cut rev1 of D1 (08:00, v1 name) owns it.
    assert latest_history.seat == SeatIdentity(
        source_id="lhb-source",
        seat_id="SEAT-A",
        source_seat_name="Corrected branch",
        mapping_version="seat-map-v2",
    )
    assert early_history.seat == SeatIdentity(
        source_id="lhb-source",
        seat_id="SEAT-A",
        source_seat_name="Example branch",
        mapping_version="seat-map-v1",
    )
    assert latest_history.items[0].source_version == "v2"
    assert latest_history.coverage.total_disclosures == 3
    assert latest_history.coverage.visible_disclosures == 3
    assert early_history.coverage.visible_disclosures == 2
    assert early_history.coverage.late_disclosures == 1


def test_lhb_repo_seat_history_window_excludes_old_and_late_rows(lhb_repository):
    old = disclosure(
        disclosure_id="D-OLD",
        disclosure_date=date(2026, 8, 1),
        source_row_id="ROW-OLD",
        available_at=utc("2026-08-01T08:00:00Z"),
    )
    recent = disclosure(
        disclosure_id="D-RECENT",
        disclosure_date=date(2026, 9, 17),
        source_row_id="ROW-RECENT",
        available_at=utc("2026-09-17T08:00:00Z"),
    )
    late = disclosure(
        disclosure_id="D-LATE-H",
        disclosure_date=DISCLOSED_ON,
        source_row_id="ROW-LATE-H",
        available_at=LIST_AS_OF + timedelta(minutes=1),
    )
    lhb_repository.ingest_batch(
        repo_batch("hist-window-1", disclosures=(old, recent, late))
    )

    history = lhb_repository.get_seat_history(
        SeatHistorySelection(
            seat_id="SEAT-A", date=DISCLOSED_ON, days=30, as_of=LIST_AS_OF
        )
    )
    assert [item.contributions[0].disclosure_date for item in history.items] == [
        date(2026, 9, 17)
    ]
    assert history.total == 1
    assert history.coverage.total_disclosures == 2
    assert history.coverage.visible_disclosures == 1
    assert history.coverage.late_disclosures == 1


def test_lhb_repo_seat_history_unknown_seat_is_not_found(lhb_repository):
    from dragon_radar.storage.repository import NotFound

    lhb_repository.ingest_batch(repo_batch())

    with pytest.raises(NotFound):
        lhb_repository.get_seat_history(
            SeatHistorySelection(
                seat_id="SEAT-MISSING", date=DISCLOSED_ON, as_of=LIST_AS_OF
            )
        )


def _statement_count(engine, callable_):
    """Run ``callable_`` while counting every cursor statement exactly."""
    from sqlalchemy import event

    statements = []

    def count_statement(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        statements.append(statement)

    event.listen(engine, "after_cursor_execute", count_statement)
    try:
        result = callable_()
    finally:
        event.remove(engine, "after_cursor_execute", count_statement)
    return statements, result


def test_lhb_repo_empty_window_carrier_and_coverage_share_one_read(
    lhb_repository,
):
    # EX09 cold path: the visibility-scoped probe finds nothing (the only
    # in-window evidence is late and the rest is outside the window) and the
    # anchor date has no visible activity, so the envelope stays empty rather
    # than 404. The quiet carrier resolution and the empty-window coverage
    # fetch must be a single round trip: probe, anchor, merged carrier/
    # coverage read, scoped catalog lookup -- exactly four statements.
    old = disclosure(
        disclosure_id="D-OLD-COLD",
        disclosure_date=date(2026, 8, 1),
        source_row_id="ROW-OLD-COLD",
        available_at=utc("2026-08-01T08:00:00Z"),
    )
    late = disclosure(
        disclosure_id="D-LATE-COLD",
        disclosure_date=DISCLOSED_ON,
        source_row_id="ROW-LATE-COLD",
        available_at=LIST_AS_OF + timedelta(minutes=1),
    )
    lhb_repository.ingest_batch(repo_batch("cold-window-1", disclosures=(old, late)))

    statements, history = _statement_count(
        lhb_repository.database.engine,
        lambda: lhb_repository.get_seat_history(
            SeatHistorySelection(
                seat_id="SEAT-A", date=DISCLOSED_ON, days=30, as_of=LIST_AS_OF
            )
        ),
    )

    assert len(statements) == 4
    # The merge must not do less work: the late in-window row still counts in
    # total/late coverage and the carrier still anchors the catalog identity.
    assert history.items == ()
    assert history.total == 0
    assert history.identity_state == "known"
    assert history.seat == SeatIdentity(
        source_id="lhb-source",
        seat_id="SEAT-A",
        source_seat_name="Example branch",
        mapping_version="seat-map-v1",
    )
    assert history.coverage == AggregationCoverage(
        total_disclosures=1,
        visible_disclosures=0,
        late_disclosures=1,
        selected_disclosures=0,
        seat_count=0,
        complete_seats=0,
        unknown_seats=0,
    )


def test_lhb_repo_empty_window_without_carrier_skips_catalog_lookup(
    lhb_repository,
):
    # No source carries the queried seat at all: the merged read returns no
    # carrier rows, so coverage is zeroed and the header goes synthetic
    # without the scoped catalog lookup -- probe, anchor, merged read only.
    late = disclosure(
        disclosure_id="D-LATE-NOANCHOR",
        disclosure_date=DISCLOSED_ON,
        source_row_id="ROW-LATE-NOANCHOR",
        available_at=LIST_AS_OF + timedelta(minutes=1),
    )
    lhb_repository.ingest_batch(repo_batch("cold-window-2", disclosures=(late,)))

    statements, history = _statement_count(
        lhb_repository.database.engine,
        lambda: lhb_repository.get_seat_history(
            SeatHistorySelection(
                seat_id="SEAT-MISSING",
                date=DISCLOSED_ON,
                days=30,
                as_of=LIST_AS_OF,
            )
        ),
    )

    assert len(statements) == 3
    assert history.items == ()
    assert history.total == 0
    assert history.identity_state == "unknown"
    assert history.seat == SeatIdentity(
        source_id="SEAT-MISSING",
        seat_id="SEAT-MISSING",
        source_seat_name="SEAT-MISSING",
        mapping_version="unknown",
    )
    assert history.coverage == AggregationCoverage(
        total_disclosures=0,
        visible_disclosures=0,
        late_disclosures=0,
        selected_disclosures=0,
        seat_count=0,
        complete_seats=0,
        unknown_seats=0,
    )


@pytest.mark.parametrize("other_grant", ["expired", "valid"])
def test_lhb_repo_empty_window_merged_read_applies_grant_gate_per_source(
    database_url, other_grant
):
    from factories import grant as grant_factory

    from dragon_radar.lhb.repository import LHBRepository
    from dragon_radar.storage.repository import Repository

    database = _upgrade_to_lhb(database_url)
    try:
        sources = Repository(database, clock=lambda: OPERATION_NOW)
        sources.register_source(grant_factory(source_id="lhb-source"))
        sources.register_source(grant_factory(source_id="lhb-other"))
        repository = LHBRepository(database, clock=lambda: OPERATION_NOW)
        late = disclosure(
            disclosure_id="D-LATE-COLD3",
            disclosure_date=DISCLOSED_ON,
            source_row_id="ROW-LATE-COLD3",
            available_at=LIST_AS_OF + timedelta(minutes=1),
        )
        other_old = disclosure(
            source_id="lhb-other",
            disclosure_id="D-OTHER-OLD",
            disclosure_date=date(2026, 8, 2),
            source_row_id="ROW-OTHER-OLD",
            source_seat_name="Other Hidden Branch",
            mapping_version="other-map-v1",
            available_at=utc("2026-08-02T08:00:00Z"),
        )
        other_disclosures = (other_old,)
        if other_grant == "expired":
            # An in-window, not-yet-visible row under lhb-other's own distinct
            # catalog identity. With the grant expired the Python gate must
            # exclude it from coverage exactly as it excludes the out-of-window
            # row; merely failing the identity resolution would not prove this.
            # The valid branch keeps its original single out-of-window row.
            other_late = disclosure(
                source_id="lhb-other",
                disclosure_id="D-OTHER-LATE",
                disclosure_date=DISCLOSED_ON,
                source_row_id="ROW-OTHER-LATE",
                source_seat_name="Other Hidden Branch",
                mapping_version="other-map-v1",
                available_at=LIST_AS_OF + timedelta(minutes=1),
            )
            other_disclosures = (other_old, other_late)
        repository.ingest_batch(repo_batch("cold-window-3", disclosures=(late,)))
        repository.ingest_batch(
            repo_batch(
                "cold-window-4",
                source_id="lhb-other",
                disclosures=other_disclosures,
            )
        )
        # The merged SQL is snapshot-gated only; the grant provenance gate is
        # re-checked in Python, one memoized verdict per source.
        if other_grant == "expired":
            sources.register_source(
                grant_factory(
                    source_id="lhb-other",
                    expires_at=OPERATION_NOW - timedelta(hours=1),
                )
            )

        statements, history = _statement_count(
            database.engine,
            lambda: repository.get_seat_history(
                SeatHistorySelection(
                    seat_id="SEAT-A",
                    date=DISCLOSED_ON,
                    days=30,
                    as_of=LIST_AS_OF,
                )
            ),
        )

        # Exactly four cold-path statements (probe, anchor, merged carrier/
        # coverage read, scoped catalog lookup); the extra evidence row adds
        # no round trip.
        assert len(statements) == 4
        assert history.items == ()
        assert history.total == 0
        # In the expired branch lhb-other owns an in-window late row, but the
        # grant gate must drop the whole source: coverage stays at the single
        # lhb-source row instead of growing to total=2/late=2.
        assert history.coverage == AggregationCoverage(
            total_disclosures=1,
            visible_disclosures=0,
            late_disclosures=1,
            selected_disclosures=0,
            seat_count=0,
            complete_seats=0,
            unknown_seats=0,
        )
        if other_grant == "expired":
            # The Python grant gate rejects lhb-other: its in-window late row
            # neither anchors the catalog identity nor enters coverage.
            assert history.identity_state == "known"
            assert history.seat == SeatIdentity(
                source_id="lhb-source",
                seat_id="SEAT-A",
                source_seat_name="Example branch",
                mapping_version="seat-map-v1",
            )
            rendered = history.seat.model_dump_json()
            assert "lhb-other" not in rendered
            assert "Other Hidden Branch" not in rendered
            assert "other-map-v1" not in rendered
        else:
            # Two carriers with distinct catalog identities fail closed to the
            # synthetic placeholder; the out-of-window evidence stays out of
            # coverage even though both sources are carriers.
            assert history.identity_state == "unknown"
            assert history.seat == SeatIdentity(
                source_id="SEAT-A",
                seat_id="SEAT-A",
                source_seat_name="SEAT-A",
                mapping_version="unknown",
            )
    finally:
        database.close()


def test_lhb_repo_seat_history_candidates_all_unauthorized_is_forbidden(
    lhb_repository,
):
    from dragon_radar.storage.repository import Forbidden, Repository

    lhb_repository.ingest_batch(repo_batch())
    Repository(lhb_repository.database, clock=lhb_repository.clock).revoke_source(
        "lhb-source"
    )

    with pytest.raises(Forbidden):
        lhb_repository.get_seat_history(
            SeatHistorySelection(seat_id="SEAT-A", date=DISCLOSED_ON, as_of=LIST_AS_OF)
        )


def test_lhb_repo_seat_history_is_source_scoped_for_reused_seat_id(
    database_url,
):
    from factories import grant as grant_factory

    from dragon_radar.lhb.repository import LHBRepository
    from dragon_radar.storage.repository import Repository

    database = _upgrade_to_lhb(database_url)
    try:
        sources = Repository(database, clock=lambda: OPERATION_NOW)
        sources.register_source(grant_factory(source_id="lhb-source"))
        sources.register_source(grant_factory(source_id="lhb-other"))
        repository = LHBRepository(database, clock=lambda: OPERATION_NOW)
        repository.ingest_batch(repo_batch("scope-1"))
        repository.ingest_batch(
            repo_batch(
                "scope-2",
                source_id="lhb-other",
                disclosures=(disclosure(source_id="lhb-other"),),
            )
        )

        history = repository.get_seat_history(
            SeatHistorySelection(
                seat_id="SEAT-A", date=DISCLOSED_ON, as_of=OPERATION_NOW
            )
        )
        assert history.total == 2
        assert {item.source_id for item in history.items} == {
            "lhb-other",
            "lhb-source",
        }
        # One item per (source, date): source_id ascending primary key.
        assert [item.source_id for item in history.items] == [
            "lhb-other",
            "lhb-source",
        ]
    finally:
        database.close()


def test_lhb_repo_seat_history_merges_same_day_disclosures(lhb_repository):
    first = disclosure(
        disclosure_id="D1",
        side="net",
        buy_amount=Decimal("100"),
        sell_amount=Decimal("30"),
    )
    second = disclosure(
        disclosure_id="D2",
        source_row_id="ROW-2",
        side="net",
        buy_amount=Decimal("50"),
        sell_amount=Decimal("20"),
    )
    lhb_repository.ingest_batch(repo_batch("day-merge", disclosures=(first, second)))

    history = lhb_repository.get_seat_history(
        SeatHistorySelection(seat_id="SEAT-A", date=DISCLOSED_ON, as_of=LIST_AS_OF)
    )
    assert history.total == 1
    item = history.items[0]
    assert item.buy_amount == Decimal("150")
    assert item.sell_amount == Decimal("50")
    assert item.net_amount == Decimal("100")
    assert {contribution.disclosure_id for contribution in item.contributions} == {
        "D1",
        "D2",
    }


# --- raw-SQL CHECK boundary and partial unique index ---------------------


def _raw_attempt(connection, *, key, status, reason, payload_hash="a" * 64):
    from uuid import uuid4

    connection.execute(
        text(
            """
            INSERT INTO lhb_import_attempts (
                id, source_id, batch_id, idempotency_key, payload_hash,
                status, reason, details, created_at
            ) VALUES (
                :id, 'lhb-checks-source', :batch_id, :key, :payload_hash,
                :status, :reason, '{}'::jsonb, :now
            )
            """
        ),
        {
            "id": uuid4(),
            "batch_id": f"batch-{key}",
            "key": key,
            "payload_hash": payload_hash,
            "status": status,
            "reason": reason,
            "now": T0,
        },
    )


def test_lhb_repo_attempt_and_batch_status_reason_matrix_enforced(database_url):
    from sqlalchemy.exc import DBAPIError

    database = _upgrade_to_lhb(database_url)
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO source_grants (source_id, "grant", updated_at)
                    VALUES ('lhb-checks-source', '{}', :now)
                    """
                ),
                {"now": T0},
            )

        def raw_attempt(**kwargs):
            with database.engine.begin() as connection:
                _raw_attempt(connection, **kwargs)

        raw_attempt(key="accepted-null", status="accepted", reason=None)
        raw_attempt(key="duplicate-null", status="duplicate", reason=None)
        raw_attempt(key="rejected-reason", status="rejected", reason="some reason")
        raw_attempt(
            key="incomplete-reason",
            status="incomplete",
            reason="incomplete_revision",
        )
        for key, status, reason in (
            ("rejected-null", "rejected", None),
            ("incomplete-null", "incomplete", None),
            ("rejected-blank", "rejected", "   "),
            ("accepted-reason", "accepted", "not allowed"),
            ("duplicate-reason", "duplicate", "not allowed"),
        ):
            with pytest.raises(DBAPIError) as exc_info:
                raw_attempt(key=key, status=status, reason=reason)
            assert exc_info.value.orig.sqlstate == "23514"
            assert (
                exc_info.value.orig.diag.constraint_name == "lhb_import_attempts_status"
            )
    finally:
        database.close()


def test_lhb_repo_payload_hash_shape_enforced(database_url):
    from sqlalchemy.exc import DBAPIError

    database = _upgrade_to_lhb(database_url)
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO source_grants (source_id, "grant", updated_at)
                    VALUES ('lhb-checks-source', '{}', :now)
                    """
                ),
                {"now": T0},
            )
        for bad_hash in ("A" * 64, "f" * 63, "g" * 64):
            with pytest.raises(DBAPIError) as exc_info:
                with database.engine.begin() as connection:
                    _raw_attempt(
                        connection,
                        key=f"hash-{bad_hash[0]}",
                        status="rejected",
                        reason="bad hash",
                        payload_hash=bad_hash,
                    )
            assert exc_info.value.orig.sqlstate == "23514"
            assert (
                exc_info.value.orig.diag.constraint_name
                == "lhb_import_attempts_payload_hash"
            )
    finally:
        database.close()


def test_lhb_repo_row_level_enums_rank_text_and_infinity_enforced(database_url):
    from uuid import uuid4

    from sqlalchemy.exc import DBAPIError

    database = _upgrade_to_lhb(database_url)
    try:
        with database.engine.begin() as connection:
            ids = _seed_lhb_chain(connection, suffix="rawchecks")

        def expect_check(label, sql, parameters, constraint):
            with pytest.raises(DBAPIError) as exc_info:
                with database.engine.begin() as connection:
                    connection.execute(text(sql), parameters)
            assert exc_info.value.orig.sqlstate == "23514", label
            assert exc_info.value.orig.diag.constraint_name == constraint, label

        base_params = {**ids, "id": uuid4()}
        expect_check(
            "bad revision completeness",
            """
            INSERT INTO lhb_disclosure_revisions (
                id, source_id, disclosure_id, batch_id, revision,
                source_event_at, received_at, available_at,
                permission_snapshot, completeness, payload_hash
            ) VALUES (
                :id, :source_id, :disclosure_id, :batch_id, 3,
                :t0, :t1, :t2, CAST(:permission AS jsonb),
                'bogus', :hash
            )
            """,
            {
                **base_params,
                "permission": LHB_PERMISSION_SNAPSHOT,
                "hash": LHB_PAYLOAD_HASH,
                "t0": T0,
                "t1": T0 + timedelta(seconds=1),
                "t2": T0 + timedelta(seconds=2),
            },
            "lhb_disclosure_revisions_completeness",
        )
        expect_check(
            "bad side enum",
            """
            INSERT INTO lhb_disclosure_seats (
                id, source_id, disclosure_id, disclosure_date, revision,
                source_row_id, seat_id, mapping_version, side, "rank",
                buy_amount, sell_amount, currency, unit, completeness
            ) VALUES (
                :id, :source_id, :disclosure_id, DATE '2026-09-18', 1,
                'side-row', :seat_a, 'seat-map-v1', 'buyx', NULL,
                1, 1, 'CNY', 'yuan', 'complete'
            )
            """,
            base_params,
            "lhb_disclosure_seats_side",
        )
        expect_check(
            "rank zero",
            """
            INSERT INTO lhb_disclosure_seats (
                id, source_id, disclosure_id, disclosure_date, revision,
                source_row_id, seat_id, mapping_version, side, "rank",
                buy_amount, sell_amount, currency, unit, completeness
            ) VALUES (
                :id, :source_id, :disclosure_id, DATE '2026-09-18', 1,
                'rank-row', :seat_a, 'seat-map-v1', 'net', 0,
                1, 1, 'CNY', 'yuan', 'complete'
            )
            """,
            base_params,
            "lhb_disclosure_seats_rank",
        )
        expect_check(
            "bad row completeness enum",
            """
            INSERT INTO lhb_disclosure_seats (
                id, source_id, disclosure_id, disclosure_date, revision,
                source_row_id, seat_id, mapping_version, side, "rank",
                buy_amount, sell_amount, currency, unit, completeness
            ) VALUES (
                :id, :source_id, :disclosure_id, DATE '2026-09-18', 1,
                'enum-row', :seat_a, 'seat-map-v1', NULL, NULL,
                1, 1, 'CNY', 'yuan', 'bogus'
            )
            """,
            base_params,
            "lhb_disclosure_seats_completeness",
        )
        expect_check(
            "infinite amount",
            """
            INSERT INTO lhb_disclosure_seats (
                id, source_id, disclosure_id, disclosure_date, revision,
                source_row_id, seat_id, mapping_version, side, "rank",
                buy_amount, sell_amount, currency, unit, completeness
            ) VALUES (
                :id, :source_id, :disclosure_id, DATE '2026-09-18', 1,
                'inf-row', :seat_a, 'seat-map-v1', NULL, NULL,
                'Infinity', 1, 'CNY', 'yuan', 'complete'
            )
            """,
            base_params,
            "lhb_disclosure_seats_nonnegative_amounts",
        )
        expect_check(
            "whitespace currency",
            """
            INSERT INTO lhb_disclosure_seats (
                id, source_id, disclosure_id, disclosure_date, revision,
                source_row_id, seat_id, mapping_version, side, "rank",
                buy_amount, sell_amount, currency, unit, completeness
            ) VALUES (
                :id, :source_id, :disclosure_id, DATE '2026-09-18', 1,
                'ws-row', :seat_a, 'seat-map-v1', NULL, NULL,
                1, 1, ' ', 'yuan', 'complete'
            )
            """,
            base_params,
            "lhb_disclosure_seats_currency_unit",
        )
        expect_check(
            "whitespace disclosure reason",
            """
            INSERT INTO lhb_disclosures (
                id, source_id, disclosure_id, security_id, disclosure_date,
                reason, permission_id
            ) VALUES (
                :id, :source_id, 'whitespace-disclosure', '600000.SH',
                DATE '2026-09-18', '  ', 'lhb-permission-1'
            )
            """,
            base_params,
            "lhb_disclosures_required_text",
        )
        expect_check(
            "whitespace seat name",
            """
            INSERT INTO lhb_seats (
                id, source_id, seat_id, mapping_version,
                source_seat_name, provenance
            ) VALUES (
                :id, :source_id, 'whitespace-seat', 'seat-map-v1',
                ' ', '{}'::jsonb
            )
            """,
            base_params,
            "lhb_seats_required_text",
        )
    finally:
        database.close()


def test_lhb_repo_nan_amount_is_rejected_at_repository_boundary(lhb_repository):
    valid = disclosure()
    nan_seat = valid.seats[0].model_copy(update={"buy_amount": Decimal("NaN")})
    tampered = valid.model_copy(update={"seats": (nan_seat,)})
    batch = repo_batch("nan-1", disclosures=(tampered,))

    result = lhb_repository.ingest_batch(batch)

    assert result.status == "rejected"
    assert result.reason == "validation_failed"
    assert lhb_table_counts(lhb_repository.database.engine)["lhb_import_batches"] == 0


def test_lhb_repo_bound_idempotency_index_sequences(database_url):
    from sqlalchemy.exc import DBAPIError

    database = _upgrade_to_lhb(database_url)
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO source_grants (source_id, "grant", updated_at)
                    VALUES ('lhb-checks-source', '{}', :now)
                    """
                ),
                {"now": T0},
            )

        def attempt(key, status, reason=None):
            with database.engine.begin() as connection:
                _raw_attempt(connection, key=key, status=status, reason=reason)

        attempt("bound-key", "accepted")
        with pytest.raises(DBAPIError) as exc_info:
            attempt("bound-key", "accepted")
        assert exc_info.value.orig.sqlstate == "23505"
        assert (
            exc_info.value.orig.diag.constraint_name
            == "lhb_import_attempts_bound_idempotency_key"
        )

        attempt("rejected-key", "rejected", reason="first failure")
        attempt("rejected-key", "accepted")
        attempt("duplicate-key", "duplicate")
        attempt("duplicate-key", "accepted")
        attempt("incomplete-key", "incomplete", reason="incomplete_revision")
        with pytest.raises(DBAPIError) as exc_info:
            attempt("incomplete-key", "accepted")
        assert exc_info.value.orig.sqlstate == "23505"
        assert (
            exc_info.value.orig.diag.constraint_name
            == "lhb_import_attempts_bound_idempotency_key"
        )
    finally:
        database.close()


# --- authorization probe budget ------------------------------------------


def _insert_miss_chains(connection, *, source_ids, start, stop):
    from uuid import uuid4

    from sqlalchemy import insert

    from dragon_radar.lhb.tables import (
        lhb_disclosure_revisions,
        lhb_disclosure_seats,
        lhb_disclosures,
        lhb_import_batches,
        lhb_seats,
    )

    batches, disclosures, revisions, seats, rows = [], [], [], [], []
    miss_date = date(2026, 9, 1)
    for source_id in source_ids:
        for index in range(start, stop):
            batch_id = f"{source_id}-miss-{index}"
            available_at = T0 + timedelta(seconds=2, microseconds=index)
            batches.append(
                {
                    "id": uuid4(),
                    "batch_id": batch_id,
                    "source_id": source_id,
                    "source_version": "v1",
                    "source_event_at": T0,
                    "received_at": T0 + timedelta(seconds=1),
                    "available_at": available_at,
                    "permission_snapshot": permission().model_dump(mode="json"),
                    "payload_hash": f"{index:064x}",
                    "status": "accepted",
                    "reason": None,
                    "imported_at": available_at,
                }
            )
            disclosures.append(
                {
                    "id": uuid4(),
                    "source_id": source_id,
                    "disclosure_id": f"D-miss-{index}",
                    "security_id": "600000.SH",
                    "disclosure_date": miss_date,
                    "reason": "miss window",
                    "permission_id": "lhb-permission-1",
                }
            )
            revisions.append(
                {
                    "id": uuid4(),
                    "source_id": source_id,
                    "disclosure_id": f"D-miss-{index}",
                    "batch_id": batch_id,
                    "revision": 1,
                    "source_event_at": T0,
                    "received_at": T0 + timedelta(seconds=1),
                    "available_at": available_at,
                    "permission_snapshot": permission().model_dump(mode="json"),
                    "completeness": "complete",
                    "payload_hash": f"{index + 1:064x}",
                }
            )
            seats.append(
                {
                    "id": uuid4(),
                    "source_id": source_id,
                    "seat_id": f"SEAT-MISS-{index}",
                    "mapping_version": "seat-map-v1",
                    "source_seat_name": "Miss branch",
                    "provenance": {
                        "mapping_version": "seat-map-v1",
                        "source_id": source_id,
                    },
                }
            )
            rows.append(
                {
                    "id": uuid4(),
                    "source_id": source_id,
                    "disclosure_id": f"D-miss-{index}",
                    "disclosure_date": miss_date,
                    "revision": 1,
                    "source_row_id": f"ROW-MISS-{index}",
                    "seat_id": f"SEAT-MISS-{index}",
                    "mapping_version": "seat-map-v1",
                    "side": "net",
                    "rank": None,
                    "buy_amount": Decimal("1"),
                    "sell_amount": Decimal("1"),
                    "currency": "CNY",
                    "unit": "yuan",
                    "completeness": "complete",
                }
            )
    connection.execute(insert(lhb_import_batches), batches)
    connection.execute(insert(lhb_disclosures), disclosures)
    connection.execute(insert(lhb_disclosure_revisions), revisions)
    connection.execute(insert(lhb_seats), seats)
    connection.execute(insert(lhb_disclosure_seats), rows)


def test_lhb_repo_authorization_reads_stay_linear_on_filter_misses(
    database_url,
):
    from factories import grant as grant_factory
    from sqlalchemy import event

    from dragon_radar.lhb.repository import LHBRepository
    from dragon_radar.storage.repository import Repository

    database = _upgrade_to_lhb(database_url)
    source_ids = ("lhb-source", "lhb-other")
    try:
        sources = Repository(database, clock=lambda: OPERATION_NOW)
        for source_id in source_ids:
            sources.register_source(grant_factory(source_id=source_id))
        repository = LHBRepository(database, clock=lambda: OPERATION_NOW)

        authorization_reads = []

        def record_authorization_read(
            _connection,
            cursor,
            statement,
            parameters,
            _context,
            _executemany,
        ):
            if "lhb_source_authorization" in statement:
                authorization_reads.append((statement, parameters, cursor.rowcount))

        event.listen(
            database.engine,
            "after_cursor_execute",
            record_authorization_read,
        )

        def plan_nodes(node):
            yield node
            for child in node.get("Plans", ()):
                yield from plan_nodes(child)

        history_relations = {
            "lhb_import_batches",
            "lhb_disclosure_revisions",
            "lhb_disclosures",
            "lhb_disclosure_seats",
            "lhb_seats",
        }

        def summarize(plan):
            summary = []
            for node in plan_nodes(plan):
                if node.get("Relation Name") not in history_relations:
                    continue
                touches = (
                    node.get("Actual Rows", 0)
                    + node.get("Rows Removed by Filter", 0)
                    + node.get("Rows Removed by Index Recheck", 0)
                ) * node.get("Actual Loops", 0)
                summary.append(
                    {
                        "node": node["Node Type"],
                        "relation": node["Relation Name"],
                        "loops": node.get("Actual Loops"),
                        "touches": touches,
                    }
                )
            return summary

        round_summaries = []
        raw_connection = database.engine.raw_connection()
        try:
            inserted = 0
            for batches_per_source in (8, 200):
                with database.engine.begin() as connection:
                    _insert_miss_chains(
                        connection,
                        source_ids=source_ids,
                        start=inserted + 1,
                        stop=batches_per_source + 1,
                    )
                inserted = batches_per_source
                reads_before = len(authorization_reads)
                # All stored disclosures are dated 2026-09-01 and carry
                # SEAT-MISS-* rows: the list window and history window both
                # miss completely. Such a window-wide miss is "no data", never
                # Forbidden, and must not rescanned per candidate batch.
                page = repository.list_disclosures(
                    DisclosureSelection(date=DISCLOSED_ON, as_of=OPERATION_NOW)
                )
                history = repository.get_seat_history(
                    SeatHistorySelection(
                        seat_id="SEAT-A",
                        date=DISCLOSED_ON,
                        days=30,
                        as_of=OPERATION_NOW,
                    )
                )
                assert page.items == ()
                assert page.total == 0
                assert history.total == 0
                cursor = raw_connection.cursor()
                summaries = []
                for statement, parameters, _ in authorization_reads[reads_before:]:
                    cursor.execute(
                        f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {statement}",
                        parameters,
                    )
                    plan = cursor.fetchone()[0][0]["Plan"]
                    summaries.append(summarize(plan))
                chain_rows = len(source_ids) * batches_per_source
                round_summaries.append(
                    {
                        "batches_per_source": batches_per_source,
                        # Five rows per chain (batch, disclosure, revision,
                        # seat, seat row).
                        "child_rows": chain_rows * 5,
                        "summaries": summaries,
                    }
                )
        finally:
            raw_connection.rollback()
            raw_connection.close()
            event.remove(
                database.engine,
                "after_cursor_execute",
                record_authorization_read,
            )

        assert [read[2] for read in authorization_reads] == [0, 0, 0, 0]
        for statement, _, _ in authorization_reads:
            assert "/* lhb_source_authorization */" in statement
            assert "FROM source_grants" in statement
            assert "FOR SHARE OF source_grants" in statement
            assert "EXISTS (SELECT" in statement
            # User values stay bound parameters: the engine hides parameters
            # and the literal user values never appear in SQL text.
            assert "SEAT-A" not in statement
            assert "2026-09-18" not in statement

        # Structural bounds (statistics-independent): the uncorrelated
        # horizon is evaluated at most once per statement and every per-source
        # LATERAL probe is skipped behind the One-Time Filter on a full miss,
        # so no history relation node may loop per candidate batch.
        for round_info in round_summaries:
            for summary in round_info["summaries"]:
                assert all(node["loops"] <= 1 for node in summary), round_info
                assert (
                    sum(node["touches"] for node in summary) <= round_info["child_rows"]
                ), round_info

        # Linear-slope budget with 3/2 slack around the row-count growth
        # (400 vs 16 chains = 25x): any per-batch rescan grows quadratically
        # and fails this.
        small, large = round_summaries
        growth = large["child_rows"] // small["child_rows"]
        for index in (0, 1):
            small_touches = sum(node["touches"] for node in small["summaries"][index])
            large_touches = sum(node["touches"] for node in large["summaries"][index])
            assert 2 * large_touches <= 3 * small_touches * growth + 1, (
                small_touches,
                large_touches,
            )
    finally:
        database.close()


def test_lhb_repo_seat_identity_lookup_is_bounded_to_batch_keys(lhb_repository):
    # Write-path bound: the seat-identity dedupe probe reads only the
    # (seat_id, mapping_version) composite keys present in the batch (scoped
    # by source_id), never the source's full seat catalog. A 400-row catalog
    # of unrelated seats must stay invisible to a 2-key batch: the captured
    # SELECT carries the composite IN predicates as bound parameters and an
    # EXPLAIN ANALYZE replay touches no more lhb_seats rows than batch keys.
    from uuid import uuid4

    from sqlalchemy import event, insert

    from dragon_radar.lhb.tables import lhb_seats

    catalog_size = 400
    batch_key_count = 2
    catalog_rows = [
        {
            "id": uuid4(),
            "source_id": "lhb-source",
            "seat_id": f"SEAT-OTHER-{index}",
            "mapping_version": "seat-map-v1",
            "source_seat_name": f"Other branch {index}",
            "provenance": {
                "mapping_version": "seat-map-v1",
                "source_id": "lhb-source",
            },
        }
        for index in range(catalog_size)
    ]
    engine = lhb_repository.database.engine
    with engine.begin() as connection:
        connection.execute(insert(lhb_seats), catalog_rows)
        connection.execute(text("ANALYZE lhb_seats"))

    batch_disclosure = disclosure(
        disclosure_id="D-BATCH",
        seats=(
            seat(
                seat_id="SEAT-BATCH-1",
                source_row_id="ROW-BATCH-1",
                source_seat_name="Batch branch 1",
            ),
            seat(
                seat_id="SEAT-BATCH-2",
                source_row_id="ROW-BATCH-2",
                source_seat_name="Batch branch 2",
                buy_amount=Decimal("7"),
                sell_amount=Decimal("4"),
            ),
        ),
    )

    captured = []

    def record_statement(
        _connection,
        _cursor,
        statement,
        parameters,
        _context,
        _executemany,
    ):
        captured.append((statement, parameters))

    event.listen(engine, "after_cursor_execute", record_statement)
    try:
        result = lhb_repository.ingest_batch(
            repo_batch("seat-bounded-1", disclosures=(batch_disclosure,))
        )
    finally:
        event.remove(engine, "after_cursor_execute", record_statement)

    seat_lookups = [
        (statement, parameters)
        for statement, parameters in captured
        if statement.lstrip().upper().startswith("SELECT")
        and "lhb_seats" in statement
        and "lhb_disclosure_seats" not in statement
    ]
    assert len(seat_lookups) == 1
    statement, parameters = seat_lookups[0]
    where_clause = statement[statement.upper().index("WHERE") :]
    # Structural shape (statistics-independent): the predicate pairs both
    # composite-key columns through an IN list instead of source_id alone.
    assert "seat_id" in where_clause, where_clause
    assert "mapping_version" in where_clause, where_clause
    assert "IN (" in where_clause.upper(), where_clause
    # User values stay bound parameters, never inlined into SQL text.
    assert "lhb-source" not in statement
    assert "SEAT-OTHER" not in statement
    assert "SEAT-BATCH" not in statement

    # Replay the exact captured probe after unhooking the listener: the
    # lhb_seats_identity_key unique index on (source_id, seat_id,
    # mapping_version) backs the predicate, so the scan touches only the two
    # batch keys even though the source catalog holds catalog_size rows.
    def plan_nodes(node):
        yield node
        for child in node.get("Plans", ()):
            yield from plan_nodes(child)

    touches = 0
    node_types = []
    index_names = []
    raw_connection = engine.raw_connection()
    try:
        cursor = raw_connection.cursor()
        cursor.execute(
            f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {statement}",
            parameters,
        )
        plan = cursor.fetchone()[0][0]["Plan"]
        for node in plan_nodes(plan):
            if node.get("Relation Name") == "lhb_seats":
                node_types.append(node["Node Type"])
                touches += (
                    node.get("Actual Rows", 0)
                    + node.get("Rows Removed by Filter", 0)
                    + node.get("Rows Removed by Index Recheck", 0)
                ) * node.get("Actual Loops", 0)
            if str(node.get("Index Name", "")).startswith("lhb_seats"):
                index_names.append(node["Index Name"])
    finally:
        raw_connection.rollback()
        raw_connection.close()

    assert index_names, node_types
    assert "Seq Scan" not in node_types, node_types
    assert touches <= batch_key_count, (touches, node_types, index_names)
    assert catalog_size >= touches * 10

    # Business behavior is unchanged: accepted attempt, missing identities
    # inserted, unrelated catalog rows untouched.
    assert result.status == "accepted"
    counts = lhb_table_counts(engine)
    assert counts["lhb_seats"] == catalog_size + batch_key_count
    assert counts["lhb_import_batches"] == 1
    assert counts["lhb_import_attempts"] == 1
    with engine.connect() as connection:
        rows = connection.execute(
            select(lhb_seats.c.seat_id, lhb_seats.c.source_seat_name)
        ).all()
    identities = dict(rows)
    assert len(identities) == catalog_size + batch_key_count
    assert identities["SEAT-BATCH-1"] == "Batch branch 1"
    assert identities["SEAT-BATCH-2"] == "Batch branch 2"
    assert identities["SEAT-OTHER-0"] == "Other branch 0"
    assert identities[f"SEAT-OTHER-{catalog_size - 1}"] == (
        f"Other branch {catalog_size - 1}"
    )


# --- EX08/EX09: read-only HTTP contracts ----------------------------------
#
# The pure validation tests below run without PostgreSQL: parameter rejection
# happens before any repository access. The history tests depend on the
# ``lhb_repository`` fixture and skip cleanly without DRAGON_TEST_DATABASE_URL.

_LHB_HTTP_DATE = "2026-09-18"
_LHB_HTTP_EARLY = "2026-09-18T10:00:00Z"
_LHB_HTTP_LATE = "2026-09-18T13:00:00Z"

_LHB_TOP_KEYS = {
    "date",
    "as_of",
    "source",
    "source_version",
    "quality_flags",
    "basis",
    "coverage",
    "data",
}
_LHB_COVERAGE_KEYS = {
    "total_disclosures",
    "visible_disclosures",
    "late_disclosures",
    "selected_disclosures",
    "seat_count",
    "complete_seats",
    "unknown_seats",
}
_LHB_DISCLOSURE_ITEM_KEYS = {
    "disclosure_id",
    "security_id",
    "disclosure_date",
    "reason",
    "revision",
    "source_id",
    "source_version",
    "source_event_at",
    "received_at",
    "available_at",
    "seats",
}
_LHB_SEAT_ROW_KEYS = {
    "source_row_id",
    "seat_id",
    "source_seat_name",
    "mapping_version",
    "side",
    "rank",
    "buy_amount",
    "sell_amount",
    "currency",
    "unit",
    "completeness",
    "net_amount",
}
_LHB_SEAT_IDENTITY_KEYS = {
    "source_id",
    "seat_id",
    "source_seat_name",
    "mapping_version",
}
_LHB_AGGREGATED_SEAT_KEYS = {
    "seat_id",
    "source_id",
    "source_version",
    "mapping_version",
    "source_seat_name",
    "currency",
    "unit",
    "buy_amount",
    "sell_amount",
    "net_amount",
    "completeness",
    "contributions",
}
_LHB_CONTRIBUTION_KEYS = {
    "disclosure_id",
    "security_id",
    "revision",
    "disclosure_date",
    "reason",
    "source_row_id",
    "source_seat_name",
    "mapping_version",
    "permission_state",
}


class _LHBHTTPWorkbenchRepository:
    """Stand-in owner repository: LHB tests inject the repository explicitly."""

    database = None


async def _lhb_http_client(lhb_repository=None):
    from httpx import ASGITransport, AsyncClient

    from dragon_radar.http.app import create_workbench_app

    app = create_workbench_app(
        repository=_LHBHTTPWorkbenchRepository(),
        lhb_repository=lhb_repository,
    )
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    return app, client


def _assert_http_error(response, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.json()["code"] == code


def _collect_json_keys(value, found: set[str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            found.add(key)
            _collect_json_keys(nested, found)
    elif isinstance(value, list):
        for nested in value:
            _collect_json_keys(nested, found)


# --- pure parameter validation (no database) ------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    "params",
    [
        {"date": _LHB_HTTP_DATE, "size": "101"},
        {"date": _LHB_HTTP_DATE, "size": "0"},
        {"date": _LHB_HTTP_DATE, "size": "x"},
        {"date": _LHB_HTTP_DATE, "page": "0"},
        {"date": _LHB_HTTP_DATE, "page": "-1"},
        {"date": _LHB_HTTP_DATE, "page": "x"},
    ],
)
async def test_ex08_lhb_list_rejects_invalid_paging(params):
    _, client = await _lhb_http_client()
    async with client:
        response = await client.get("/api/lhb", params=params)
    _assert_http_error(response, 400, "INVALID_PARAM")


@pytest.mark.anyio
@pytest.mark.parametrize("days", ["0", "251", "x", "-1"])
async def test_ex09_lhb_seats_rejects_days_outside_bounds(days):
    _, client = await _lhb_http_client()
    async with client:
        response = await client.get(
            "/api/lhb/seats/SEAT-A",
            params={"date": _LHB_HTTP_DATE, "days": days},
        )
    _assert_http_error(response, 400, "INVALID_PARAM")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path,params",
    [
        ("/api/lhb", {"date": _LHB_HTTP_DATE, "unknown": "1"}),
        (
            "/api/lhb",
            [("date", _LHB_HTTP_DATE), ("date", _LHB_HTTP_DATE)],
        ),
        (
            "/api/lhb/seats/SEAT-A",
            {"date": _LHB_HTTP_DATE, "unknown": "1"},
        ),
        (
            "/api/lhb/seats/SEAT-A",
            [("date", _LHB_HTTP_DATE), ("date", _LHB_HTTP_DATE)],
        ),
    ],
)
async def test_ex08_ex09_reject_unknown_and_duplicate_query_keys(path, params):
    _, client = await _lhb_http_client()
    async with client:
        response = await client.get(path, params=params)
    _assert_http_error(response, 400, "INVALID_PARAM")


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/api/lhb", "/api/lhb/seats/SEAT-A"])
@pytest.mark.parametrize(
    "params",
    [
        {},
        {"date": "2026-9-18"},
        {"date": "not-a-date"},
        {"date": "2026-09-31"},
    ],
)
async def test_ex08_ex09_require_strict_iso_date(path, params):
    _, client = await _lhb_http_client()
    async with client:
        response = await client.get(path, params=params)
    _assert_http_error(response, 400, "INVALID_PARAM")


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/api/lhb", "/api/lhb/seats/SEAT-A"])
@pytest.mark.parametrize(
    "as_of",
    [
        "2026-09-18T10:00:00",
        "not-a-date",
        "20260918T100000Z",
        "2026-09-18 10:00:00+00:00",
    ],
)
async def test_ex08_ex09_require_strict_timezone_aware_as_of(path, as_of):
    _, client = await _lhb_http_client()
    async with client:
        response = await client.get(
            path, params={"date": _LHB_HTTP_DATE, "as_of": as_of}
        )
    _assert_http_error(response, 400, "INVALID_PARAM")


@pytest.mark.anyio
@pytest.mark.parametrize("security_id", ["BAD ID", "-LEAD", "bad$", ""])
async def test_ex08_lhb_list_rejects_invalid_security_identifier(security_id):
    _, client = await _lhb_http_client()
    async with client:
        response = await client.get(
            "/api/lhb",
            params={"date": _LHB_HTTP_DATE, "security_id": security_id},
        )
    _assert_http_error(response, 400, "INVALID_PARAM")


@pytest.mark.anyio
@pytest.mark.parametrize("seat_id", ["BAD ID", "-LEAD", "bad$"])
async def test_ex09_lhb_seats_rejects_invalid_path_identifier(seat_id):
    _, client = await _lhb_http_client()
    async with client:
        response = await client.get(
            f"/api/lhb/seats/{seat_id}",
            params={"date": _LHB_HTTP_DATE},
        )
    _assert_http_error(response, 400, "INVALID_PARAM")


@pytest.mark.anyio
async def test_ex08_lhb_post_is_forbidden_by_read_only_middleware():
    _, client = await _lhb_http_client()
    async with client:
        response = await client.post("/api/lhb", json={})
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "FORBIDDEN"
    assert body["message"] == "Read-only API"


@pytest.mark.anyio
async def test_ex08_ex09_without_repository_report_storage_unavailable():
    app, client = await _lhb_http_client()
    assert app.state.lhb_repository is None
    async with client:
        listing = await client.get("/api/lhb", params={"date": _LHB_HTTP_DATE})
        history = await client.get(
            "/api/lhb/seats/SEAT-A", params={"date": _LHB_HTTP_DATE}
        )
    _assert_http_error(listing, 503, "STORAGE_UNAVAILABLE")
    _assert_http_error(history, 503, "STORAGE_UNAVAILABLE")


def test_ex08_ex09_serializers_reject_plain_dict_instead_of_models():
    from dragon_radar.http.routes.lhb import (
        serialize_disclosure_page,
        serialize_seat_history,
    )

    with pytest.raises(TypeError):
        serialize_disclosure_page({}, date=DISCLOSED_ON)
    with pytest.raises(TypeError):
        serialize_seat_history({}, date=DISCLOSED_ON)


# --- database-backed route behaviour --------------------------------------


@pytest.mark.anyio
async def test_ex08_lhb_list_point_in_time_visibility(lhb_repository):
    on_time = disclosure(disclosure_id="D1", source_row_id="ROW-1", seat_id="SEAT-A")
    late = disclosure(
        disclosure_id="D2",
        source_row_id="ROW-2",
        seat_id="SEAT-B",
        available_at=LIST_AS_OF + timedelta(minutes=1),
    )
    lhb_repository.ingest_batch(
        repo_batch("http-visibility", disclosures=(on_time, late))
    )

    _, client = await _lhb_http_client(lhb_repository)
    async with client:
        early = await client.get(
            "/api/lhb",
            params={"date": _LHB_HTTP_DATE, "as_of": _LHB_HTTP_EARLY},
        )
        late_response = await client.get(
            "/api/lhb",
            params={"date": _LHB_HTTP_DATE, "as_of": _LHB_HTTP_LATE},
        )

    assert early.status_code == 200
    early_payload = early.json()
    assert [item["disclosure_id"] for item in early_payload["data"]["items"]] == ["D1"]
    assert early_payload["data"]["total"] == 1
    assert early_payload["as_of"] == "2026-09-18T10:00:00+00:00"

    assert late_response.status_code == 200
    late_payload = late_response.json()
    assert [item["disclosure_id"] for item in late_payload["data"]["items"]] == [
        "D1",
        "D2",
    ]
    assert late_payload["data"]["total"] == 2


@pytest.mark.anyio
async def test_ex08_lhb_list_null_zero_and_net_amount_raw_bytes(lhb_repository):
    import json

    mixed = disclosure(
        disclosure_id="D-null-zero",
        seats=(
            seat(
                source_row_id="ROW-1",
                seat_id="SEAT-A",
                buy_amount=None,
                sell_amount=Decimal("5"),
            ),
            seat(
                source_row_id="ROW-2",
                seat_id="SEAT-B",
                buy_amount=Decimal("0"),
                sell_amount=Decimal("0"),
            ),
        ),
    )
    lhb_repository.ingest_batch(repo_batch("http-null-zero", disclosures=(mixed,)))

    _, client = await _lhb_http_client(lhb_repository)
    async with client:
        response = await client.get(
            "/api/lhb",
            params={"date": _LHB_HTTP_DATE, "as_of": _LHB_HTTP_EARLY},
        )

    assert response.status_code == 200
    raw = response.content
    assert b'"buy_amount":null' in raw
    assert b'"net_amount":null' in raw
    assert b'"completeness":"buy_unknown"' in raw
    assert b'"buy_amount":0' in raw
    assert b'"sell_amount":0' in raw
    assert b'"net_amount":0' in raw
    assert b'"completeness":"complete"' in raw

    payload = json.loads(raw)
    rows = {
        row["seat_id"]: row
        for item in payload["data"]["items"]
        for row in item["seats"]
    }
    unknown = rows["SEAT-A"]
    assert unknown["buy_amount"] is None
    assert unknown["sell_amount"] == 5
    assert unknown["net_amount"] is None
    zero = rows["SEAT-B"]
    assert zero["buy_amount"] == 0
    assert zero["sell_amount"] == 0
    assert zero["net_amount"] == 0


@pytest.mark.anyio
async def test_ex08_lhb_list_keeps_large_decimal_precision(lhb_repository):
    import json

    precise = disclosure(
        disclosure_id="D-precision",
        seats=(
            seat(
                source_row_id="ROW-P",
                seat_id="SEAT-P",
                side="net",
                rank=3,
                buy_amount=None,
                sell_amount=Decimal("12345678901234567890.123456789012345678"),
            ),
        ),
    )
    lhb_repository.ingest_batch(repo_batch("http-precision", disclosures=(precise,)))

    _, client = await _lhb_http_client(lhb_repository)
    async with client:
        response = await client.get(
            "/api/lhb",
            params={"date": _LHB_HTTP_DATE, "as_of": _LHB_HTTP_EARLY},
        )

    assert response.status_code == 200
    assert b'"sell_amount":12345678901234567890.123456789012345678' in response.content
    payload = json.loads(response.content, parse_float=Decimal)
    row = payload["data"]["items"][0]["seats"][0]
    assert row["sell_amount"] == Decimal("12345678901234567890.123456789012345678")
    assert row["buy_amount"] is None
    assert row["rank"] == 3
    assert row["side"] == "net"


@pytest.mark.anyio
async def test_ex08_ex09_revoked_source_returns_403(lhb_repository):
    from dragon_radar.storage.repository import Repository

    lhb_repository.ingest_batch(repo_batch())
    Repository(lhb_repository.database, clock=lhb_repository.clock).revoke_source(
        "lhb-source"
    )

    _, client = await _lhb_http_client(lhb_repository)
    async with client:
        listing = await client.get(
            "/api/lhb",
            params={"date": _LHB_HTTP_DATE, "as_of": _LHB_HTTP_EARLY},
        )
        history = await client.get(
            "/api/lhb/seats/SEAT-A",
            params={"date": _LHB_HTTP_DATE, "as_of": _LHB_HTTP_EARLY},
        )
    _assert_http_error(listing, 403, "FORBIDDEN")
    _assert_http_error(history, 403, "FORBIDDEN")


@pytest.mark.anyio
async def test_ex09_lhb_unknown_seat_with_anchor_activity_returns_404(
    lhb_repository,
):
    lhb_repository.ingest_batch(repo_batch())

    _, client = await _lhb_http_client(lhb_repository)
    async with client:
        response = await client.get(
            "/api/lhb/seats/SEAT-MISSING",
            params={"date": _LHB_HTTP_DATE, "as_of": _LHB_HTTP_EARLY},
        )
    _assert_http_error(response, 404, "NOT_FOUND")


@pytest.mark.anyio
async def test_ex08_ex09_empty_window_returns_200_no_data_envelope(
    lhb_repository,
):
    import json

    lhb_repository.ingest_batch(repo_batch())

    _, client = await _lhb_http_client(lhb_repository)
    async with client:
        listing = await client.get(
            "/api/lhb",
            params={"date": "2026-09-01", "as_of": _LHB_HTTP_EARLY},
        )
        history = await client.get(
            "/api/lhb/seats/SEAT-A",
            params={
                "date": "2026-09-01",
                "days": "30",
                "as_of": _LHB_HTTP_EARLY,
            },
        )

    assert listing.status_code == 200
    page = json.loads(listing.content)
    assert set(page) == _LHB_TOP_KEYS
    assert page["date"] == "2026-09-01"
    assert page["quality_flags"] == ["no_data"]
    assert page["source"] is None
    assert page["source_version"] is None
    assert page["basis"] == {"unit": None, "currency": None}
    assert page["coverage"] == {
        "total_disclosures": 0,
        "visible_disclosures": 0,
        "late_disclosures": 0,
        "selected_disclosures": 0,
        "seat_count": 0,
        "complete_seats": 0,
        "unknown_seats": 0,
    }
    assert page["data"] == {
        "items": [],
        "total": 0,
        "page": 1,
        "size": 20,
    }

    assert history.status_code == 200
    seat_page = json.loads(history.content)
    assert set(seat_page) == _LHB_TOP_KEYS
    assert seat_page["quality_flags"] == ["no_data"]
    assert seat_page["source"] is None
    assert seat_page["source_version"] is None
    assert seat_page["basis"] == {"unit": None, "currency": None}
    assert set(seat_page["data"]) == {"seat", "identity_state", "items", "total"}
    assert seat_page["data"]["items"] == []
    assert seat_page["data"]["total"] == 0
    assert set(seat_page["data"]["seat"]) == _LHB_SEAT_IDENTITY_KEYS
    assert seat_page["data"]["seat"]["seat_id"] == "SEAT-A"
    # The disclosure sits outside the read window, but its authorized source
    # still owns a real catalog identity: the empty envelope names it
    # explicitly instead of guessing from a synthetic placeholder.
    assert seat_page["data"]["identity_state"] == "known"
    assert seat_page["data"]["seat"]["source_id"] == "lhb-source"
    assert seat_page["data"]["seat"]["mapping_version"] == "seat-map-v1"


@pytest.mark.anyio
async def test_ex08_lhb_list_fields_identity_basis_coverage_and_paging(
    lhb_repository,
):
    disclosures = tuple(
        disclosure(
            disclosure_id=f"D{index}",
            source_row_id=f"ROW-{index}",
            seat_id=f"SEAT-{index}",
            buy_amount=Decimal(index * 10),
            sell_amount=Decimal(index),
        )
        for index in range(1, 4)
    )
    lhb_repository.ingest_batch(repo_batch("http-fields", disclosures=disclosures))

    _, client = await _lhb_http_client(lhb_repository)
    async with client:
        first = await client.get(
            "/api/lhb",
            params={
                "date": _LHB_HTTP_DATE,
                "as_of": _LHB_HTTP_EARLY,
                "page": "1",
                "size": "2",
            },
        )
        second = await client.get(
            "/api/lhb",
            params={
                "date": _LHB_HTTP_DATE,
                "as_of": _LHB_HTTP_EARLY,
                "page": "2",
                "size": "2",
            },
        )
        defaults = await client.get("/api/lhb", params={"date": _LHB_HTTP_DATE})

    assert first.status_code == 200
    payload = first.json()
    assert payload["date"] == _LHB_HTTP_DATE
    assert payload["source"] == "lhb-source"
    assert payload["source_version"] == "v1"
    assert payload["quality_flags"] == []
    assert payload["basis"] == {"unit": "yuan", "currency": "CNY"}
    assert payload["coverage"] == {
        "total_disclosures": 3,
        "visible_disclosures": 3,
        "late_disclosures": 0,
        "selected_disclosures": 3,
        "seat_count": 3,
        "complete_seats": 3,
        "unknown_seats": 0,
    }
    assert payload["data"]["total"] == 3
    assert payload["data"]["page"] == 1
    assert payload["data"]["size"] == 2
    assert [item["disclosure_id"] for item in payload["data"]["items"]] == ["D1", "D2"]
    first_item = payload["data"]["items"][0]
    assert first_item["security_id"] == "600000.SH"
    assert first_item["disclosure_date"] == _LHB_HTTP_DATE
    assert first_item["revision"] == 1
    row = first_item["seats"][0]
    assert row["mapping_version"] == "seat-map-v1"
    assert row["source_row_id"] == "ROW-1"
    assert row["rank"] is None
    assert row["side"] is None
    assert row["completeness"] == "complete"
    assert row["net_amount"] == 9  # 10 - 1

    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["data"]["page"] == 2
    assert [item["disclosure_id"] for item in second_payload["data"]["items"]] == ["D3"]

    assert defaults.status_code == 200
    default_payload = defaults.json()
    assert default_payload["data"]["page"] == 1
    assert default_payload["data"]["size"] == 20
    # Without as_of the repository clock fixes the point in time.
    assert default_payload["as_of"] == "2026-09-18T13:00:00+00:00"
    assert default_payload["data"]["total"] == 3


@pytest.mark.anyio
async def test_ex08_lhb_list_basis_blank_when_currency_or_unit_diverges(
    lhb_repository,
):
    mixed = disclosure(
        disclosure_id="D-mixed-basis",
        seats=(
            seat(
                source_row_id="ROW-1",
                seat_id="SEAT-A",
                currency="CNY",
                unit="yuan",
            ),
            seat(
                source_row_id="ROW-2",
                seat_id="SEAT-B",
                currency="HKD",
                unit="dollar",
            ),
        ),
    )
    lhb_repository.ingest_batch(repo_batch("http-basis", disclosures=(mixed,)))

    _, client = await _lhb_http_client(lhb_repository)
    async with client:
        response = await client.get(
            "/api/lhb",
            params={"date": _LHB_HTTP_DATE, "as_of": _LHB_HTTP_EARLY},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "lhb-source"
    assert payload["basis"] == {"unit": None, "currency": None}


@pytest.mark.anyio
async def test_ex08_lhb_list_exact_key_sets_without_attribution_keys(
    lhb_repository,
):
    lhb_repository.ingest_batch(
        repo_batch(
            "http-keys",
            disclosures=(
                disclosure(
                    disclosure_id="D1",
                    source_row_id="ROW-1",
                    seat_id="SEAT-A",
                    rank=1,
                    side="buy",
                ),
                disclosure(
                    disclosure_id="D2",
                    source_row_id="ROW-2",
                    seat_id="SEAT-B",
                ),
            ),
        )
    )

    _, client = await _lhb_http_client(lhb_repository)
    async with client:
        response = await client.get(
            "/api/lhb",
            params={
                "date": _LHB_HTTP_DATE,
                "as_of": _LHB_HTTP_EARLY,
                "size": "100",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == _LHB_TOP_KEYS
    assert set(payload["data"]) == {"items", "total", "page", "size"}
    assert set(payload["basis"]) == {"unit", "currency"}
    assert set(payload["coverage"]) == _LHB_COVERAGE_KEYS
    for item in payload["data"]["items"]:
        assert set(item) == _LHB_DISCLOSURE_ITEM_KEYS
        for row in item["seats"]:
            assert set(row) == _LHB_SEAT_ROW_KEYS

    found = set()
    _collect_json_keys(payload, found)
    assert not {
        key for key in found if "person" in key or "fund" in key or "holding" in key
    }
    # The disclosure endpoint never exposes the permission snapshot or raw
    # credential/upstream evidence.
    assert "permission" not in found
    assert "permission_id" not in found
    assert "credential" not in found


@pytest.mark.anyio
async def test_ex09_lhb_seat_history_fields_and_exact_key_sets(
    lhb_repository,
):
    import json

    lhb_repository.ingest_batch(repo_batch())

    _, client = await _lhb_http_client(lhb_repository)
    async with client:
        explicit = await client.get(
            "/api/lhb/seats/SEAT-A",
            params={
                "date": _LHB_HTTP_DATE,
                "days": "30",
                "as_of": _LHB_HTTP_EARLY,
            },
        )
        default_days = await client.get(
            "/api/lhb/seats/SEAT-A",
            params={"date": _LHB_HTTP_DATE, "as_of": _LHB_HTTP_EARLY},
        )

    assert explicit.status_code == 200
    payload = json.loads(explicit.content, parse_float=Decimal)
    assert set(payload) == _LHB_TOP_KEYS
    assert payload["date"] == _LHB_HTTP_DATE
    assert payload["as_of"] == "2026-09-18T10:00:00+00:00"
    assert payload["source"] == "lhb-source"
    assert payload["source_version"] == "v1"
    assert payload["quality_flags"] == []
    assert payload["basis"] == {"unit": "yuan", "currency": "CNY"}
    assert set(payload["coverage"]) == _LHB_COVERAGE_KEYS
    assert payload["coverage"] == {
        "total_disclosures": 1,
        "visible_disclosures": 1,
        "late_disclosures": 0,
        "selected_disclosures": 1,
        "seat_count": 1,
        "complete_seats": 1,
        "unknown_seats": 0,
    }

    data = payload["data"]
    assert set(data) == {"seat", "identity_state", "items", "total"}
    assert data["identity_state"] == "known"
    assert data["total"] == 1
    assert set(data["seat"]) == _LHB_SEAT_IDENTITY_KEYS
    assert data["seat"] == {
        "source_id": "lhb-source",
        "seat_id": "SEAT-A",
        "source_seat_name": "Example branch",
        "mapping_version": "seat-map-v1",
    }

    aggregated = data["items"][0]
    assert set(aggregated) == _LHB_AGGREGATED_SEAT_KEYS
    assert aggregated["buy_amount"] == Decimal("100")
    assert aggregated["sell_amount"] == Decimal("30")
    assert aggregated["net_amount"] == Decimal("70")
    assert aggregated["completeness"] == "complete"
    contribution = aggregated["contributions"][0]
    assert set(contribution) == _LHB_CONTRIBUTION_KEYS
    assert contribution["disclosure_id"] == "D1"
    assert contribution["permission_state"] == "authorized"

    assert default_days.status_code == 200
    assert default_days.json()["data"]["total"] == 1

    found = set()
    _collect_json_keys(payload, found)
    assert not {
        key for key in found if "person" in key or "fund" in key or "holding" in key
    }
    assert "credential" not in found


# --- EX09 identity provenance (I-1), late coverage (N-4), paging (N-3) ----


@pytest.mark.anyio
async def test_ex09_identity_unknown_without_any_catalog_entry(lhb_repository):
    # (a) Empty window and no authorized source carries the seat at all: the
    # header is the synthetic placeholder with an explicit "unknown" state.
    history = lhb_repository.get_seat_history(
        SeatHistorySelection(seat_id="SEAT-A", date=DISCLOSED_ON, as_of=LIST_AS_OF)
    )
    assert history.identity_state == "unknown"
    assert history.seat.source_id == "SEAT-A"
    assert history.seat.source_seat_name == "SEAT-A"
    assert history.seat.mapping_version == "unknown"

    _, client = await _lhb_http_client(lhb_repository)
    async with client:
        response = await client.get(
            "/api/lhb/seats/SEAT-A",
            params={"date": _LHB_HTTP_DATE, "as_of": _LHB_HTTP_EARLY},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["identity_state"] == "unknown"
    assert data["seat"] == {
        "source_id": "SEAT-A",
        "seat_id": "SEAT-A",
        "source_seat_name": "SEAT-A",
        "mapping_version": "unknown",
    }
    assert data["items"] == []
    assert data["total"] == 0


@pytest.mark.anyio
async def test_ex09_identity_known_from_catalog_when_data_outside_window(
    lhb_repository,
):
    # (b) Nothing visible in the window, but the authorized source's catalog
    # holds the real seat identity (evidence landed outside the window): the
    # state is "known" and the header is the catalog identity, not a seat_id
    # echo or a mapping_version=="unknown" guess.
    lhb_repository.ingest_batch(repo_batch())

    _, client = await _lhb_http_client(lhb_repository)
    async with client:
        response = await client.get(
            "/api/lhb/seats/SEAT-A",
            params={
                "date": "2026-09-01",
                "days": "30",
                "as_of": _LHB_HTTP_EARLY,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    data = payload["data"]
    assert data["identity_state"] == "known"
    assert data["seat"] == {
        "source_id": "lhb-source",
        "seat_id": "SEAT-A",
        "source_seat_name": "Example branch",
        "mapping_version": "seat-map-v1",
    }
    assert data["items"] == []
    assert payload["quality_flags"] == ["no_data"]
    assert payload["coverage"] == {
        "total_disclosures": 0,
        "visible_disclosures": 0,
        "late_disclosures": 0,
        "selected_disclosures": 0,
        "seat_count": 0,
        "complete_seats": 0,
        "unknown_seats": 0,
    }


@pytest.mark.anyio
async def test_ex09_identity_unknown_when_two_authorized_sources_reuse_seat(
    database_url,
):
    # (c) Two authorized sources reuse the same seat_id with distinct catalog
    # identities and no record is visible in the window. The header must fail
    # closed to the synthetic placeholder: never silently attribute the seat
    # to either source via ORDER BY ... LIMIT 1.
    from factories import grant as grant_factory

    from dragon_radar.lhb.repository import LHBRepository
    from dragon_radar.storage.repository import Repository

    database = _upgrade_to_lhb(database_url)
    try:
        sources = Repository(database, clock=lambda: OPERATION_NOW)
        sources.register_source(grant_factory(source_id="lhb-source"))
        sources.register_source(grant_factory(source_id="lhb-other"))
        repository = LHBRepository(database, clock=lambda: OPERATION_NOW)
        repository.ingest_batch(repo_batch("prov-1"))
        repository.ingest_batch(
            repo_batch(
                "prov-2",
                source_id="lhb-other",
                disclosures=(
                    disclosure(
                        source_id="lhb-other",
                        source_seat_name="Other Hidden Branch",
                        mapping_version="other-map-v1",
                    ),
                ),
            )
        )

        _, client = await _lhb_http_client(repository)
        async with client:
            response = await client.get(
                "/api/lhb/seats/SEAT-A",
                params={
                    "date": "2026-09-01",
                    "days": "30",
                    "as_of": _LHB_HTTP_LATE,
                },
            )

        assert response.status_code == 200
        payload = response.json()
        data = payload["data"]
        assert data["identity_state"] == "unknown"
        assert data["seat"] == {
            "source_id": "SEAT-A",
            "seat_id": "SEAT-A",
            "source_seat_name": "SEAT-A",
            "mapping_version": "unknown",
        }
        assert data["items"] == []
        raw = response.text
        assert "Example branch" not in raw
        assert "Other Hidden Branch" not in raw
        assert "seat-map-v1" not in raw
        assert "other-map-v1" not in raw
    finally:
        database.close()


@pytest.mark.anyio
async def test_ex09_identity_known_on_normal_items_path(lhb_repository):
    # (d) Visible aggregated rows always carry an explicit "known" identity.
    lhb_repository.ingest_batch(repo_batch())

    history = lhb_repository.get_seat_history(
        SeatHistorySelection(seat_id="SEAT-A", date=DISCLOSED_ON, as_of=LIST_AS_OF)
    )
    assert history.identity_state == "known"

    _, client = await _lhb_http_client(lhb_repository)
    async with client:
        response = await client.get(
            "/api/lhb/seats/SEAT-A",
            params={"date": _LHB_HTTP_DATE, "as_of": _LHB_HTTP_EARLY},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["identity_state"] == "known"
    assert data["seat"]["source_id"] == "lhb-source"
    assert data["total"] == 1
    assert len(data["items"]) == 1


@pytest.mark.anyio
async def test_ex09_all_late_candidates_count_in_coverage(lhb_repository):
    # N-4: an authorized source whose only in-window revision is not yet
    # available returns an empty envelope, but coverage still reports the
    # late candidate -- consistent with the EX08 late counting.
    late = disclosure(
        disclosure_id="D-late-http",
        source_row_id="ROW-late-http",
        available_at=LIST_AS_OF + timedelta(minutes=1),
    )
    lhb_repository.ingest_batch(repo_batch("http-all-late", disclosures=(late,)))

    _, client = await _lhb_http_client(lhb_repository)
    async with client:
        response = await client.get(
            "/api/lhb/seats/SEAT-A",
            params={"date": _LHB_HTTP_DATE, "as_of": _LHB_HTTP_EARLY},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["items"] == []
    assert payload["data"]["total"] == 0
    assert payload["quality_flags"] == ["no_data"]
    assert payload["coverage"]["total_disclosures"] >= 1
    assert payload["coverage"]["visible_disclosures"] == 0
    assert payload["coverage"]["late_disclosures"] >= 1
    assert payload["coverage"]["selected_disclosures"] == 0
    assert payload["coverage"]["seat_count"] == 0
    assert payload["data"]["identity_state"] == "known"


@pytest.mark.anyio
async def test_ex08_out_of_range_page_is_empty_page_without_no_data_flag(
    lhb_repository,
):
    # N-3: a page past the last one is an empty slice of a non-empty result,
    # not a "no_data" window: total stays the full count and no_data is absent.
    disclosures = tuple(
        disclosure(
            disclosure_id=f"D{index}",
            source_row_id=f"ROW-{index}",
            seat_id=f"SEAT-{index}",
        )
        for index in range(1, 4)
    )
    lhb_repository.ingest_batch(
        repo_batch("http-page-past-end", disclosures=disclosures)
    )

    _, client = await _lhb_http_client(lhb_repository)
    async with client:
        response = await client.get(
            "/api/lhb",
            params={
                "date": _LHB_HTTP_DATE,
                "as_of": _LHB_HTTP_EARLY,
                "page": "3",
                "size": "2",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["total"] == 3
    assert payload["data"]["page"] == 3
    assert payload["data"]["size"] == 2
    assert payload["data"]["items"] == []
    assert payload["quality_flags"] == []
    assert "no_data" not in payload["quality_flags"]


@pytest.mark.anyio
async def test_ex08_row_net_uses_34_digit_decimal_context(lhb_repository):
    # #3: the route-level difference must run in the same prec=34 context the
    # pure aggregator uses; a 30-significant-digit difference must survive.
    import json

    big_buy = Decimal("9" * 30)
    # Exact 999...998 (30 significant digits), built from a string so the
    # test process's default 28-digit context cannot round the expectation.
    exact_net = Decimal("9" * 29 + "8")
    precise = disclosure(
        disclosure_id="D-ctx-34",
        seats=(
            seat(
                source_row_id="ROW-1",
                seat_id="SEAT-A",
                buy_amount=big_buy,
                sell_amount=Decimal("1"),
            ),
        ),
    )
    lhb_repository.ingest_batch(repo_batch("http-ctx-34", disclosures=(precise,)))

    _, client = await _lhb_http_client(lhb_repository)
    async with client:
        response = await client.get(
            "/api/lhb",
            params={"date": _LHB_HTTP_DATE, "as_of": _LHB_HTTP_EARLY},
        )

    assert response.status_code == 200
    assert f'"net_amount":{exact_net}'.encode() in response.content
    payload = json.loads(response.content, parse_float=Decimal)
    row = payload["data"]["items"][0]["seats"][0]
    assert row["buy_amount"] == big_buy
    assert row["net_amount"] == exact_net
