"""Pure unit tests for strict LHB disclosure models and null-safe aggregation.

These tests never touch PostgreSQL: all evidence is built from the strict
``dragon_radar.lhb`` models and aggregated with pure functions.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from dragon_radar.lhb import (
    AggregatedSeat,
    DisclosureRevisionInput,
    DisclosureSeatInput,
    DisclosureSelection,
    LHBImportBatch,
    LHBImportResult,
    PermissionSnapshot,
    SeatAggregation,
    SeatHistorySelection,
    aggregate_seats,
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
    assert result.as_of == AS_OF


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

    with pytest.raises(ValueError, match="currency|unit"):
        aggregate_seats(disclosures=[first, second], as_of=AS_OF)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("buy_amount", Decimal("NaN")),
        ("buy_amount", Decimal("Infinity")),
        ("sell_amount", Decimal("-Infinity")),
        ("buy_amount", Decimal("-1")),
        ("sell_amount", Decimal("-0.01")),
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
    with pytest.raises(ValidationError, match="authorized"):
        LHBImportBatch(
            batch_id="batch-1",
            source_id="lhb-source",
            source_version="v1",
            source_event_at=T0,
            received_at=T0,
            available_at=T0,
            permission=permission(state),
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
