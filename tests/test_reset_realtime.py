from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from dragon_radar.realtime.anomalies import detect_anomalies
from dragon_radar.realtime.models import (
    AnomalyRule,
    MinuteBarInput,
    MinuteBatchInput,
    PermissionSnapshot,
    QuoteBatchInput,
    QuoteInput,
    QuoteRecord,
    SectorFlowBatchInput,
    SectorFlowPoint,
)

T0 = datetime(2026, 10, 9, 1, 30, tzinfo=UTC)


def permission(state: str = "authorized") -> PermissionSnapshot:
    return PermissionSnapshot(
        permission_id=" permission-1 ",
        state=state,
        captured_at=T0,
    )


def minute_bar(
    close: str = "100",
    *,
    minute: int = 0,
    revision: int = 1,
    volume: str | None = "100",
) -> MinuteBarInput:
    close_value = Decimal(close)
    return MinuteBarInput(
        security_id=" 600000.SH ",
        event_at=T0 + timedelta(minutes=minute),
        received_at=T0 + timedelta(minutes=minute, seconds=1),
        available_at=T0 + timedelta(minutes=minute, seconds=2),
        revision=revision,
        open=close_value,
        high=close_value,
        low=close_value,
        close=close_value,
        volume=None if volume is None else Decimal(volume),
        amount=None,
        final=True,
    )


def minute_bars(
    closes: list[str],
    *,
    volumes: list[str | None] | None = None,
) -> tuple[MinuteBarInput, ...]:
    supplied_volumes = volumes or ["100"] * len(closes)
    return tuple(
        minute_bar(close, minute=index, volume=supplied_volumes[index])
        for index, close in enumerate(closes)
    )


def return_rule(window: int = 3) -> AnomalyRule:
    return AnomalyRule(
        rule_version="return-v1",
        metric="window_return",
        window=window,
        threshold=Decimal("0.02"),
        cooldown_seconds=300,
    )


def test_authorization_model_is_frozen_strict_and_forbids_extra_fields():
    snapshot = permission()

    assert snapshot.permission_id == "permission-1"
    with pytest.raises(ValidationError):
        PermissionSnapshot(
            permission_id="permission-1",
            state="authorized",
            captured_at=T0,
            credential="secret",
        )
    with pytest.raises(ValidationError):
        snapshot.state = "revoked"


def test_authorization_rejects_protected_quote_batch_when_not_authorized():
    with pytest.raises(ValidationError, match="authorized"):
        QuoteBatchInput(
            batch_id="batch-1",
            source_id="licensed-feed",
            source_version="v1",
            source_event_at=T0,
            received_at=T0,
            available_at=T0,
            permission=permission("revoked"),
            quotes=(),
        )


def test_model_timestamps_are_canonical_utc_and_ordered():
    east = timezone(timedelta(hours=8))
    source = datetime(2026, 10, 9, 9, 30, tzinfo=east)
    batch = QuoteBatchInput(
        batch_id="batch-1",
        source_id="licensed-feed",
        source_version="v1",
        source_event_at=source,
        received_at=source + timedelta(seconds=1),
        available_at=source + timedelta(seconds=2),
        permission=permission(),
        quotes=(),
    )

    assert batch.source_event_at == T0
    assert batch.source_event_at.tzinfo is UTC
    with pytest.raises(ValidationError, match="received_at"):
        QuoteBatchInput.model_validate(
            {
                **batch.model_dump(),
                "received_at": batch.source_event_at - timedelta(seconds=1),
            }
        )
    with pytest.raises(ValidationError, match="available_at"):
        QuoteBatchInput.model_validate(
            {
                **batch.model_dump(),
                "available_at": batch.received_at - timedelta(seconds=1),
            }
        )


def test_quote_record_accepts_ordered_timestamps():
    record = QuoteRecord(
        batch_id="batch-1",
        source_id="licensed-feed",
        source_version="v1",
        source_event_at=T0,
        received_at=T0 + timedelta(seconds=1),
        available_at=T0 + timedelta(seconds=2),
        quote=QuoteInput(
            security_id="600000.SH",
            last=Decimal("10"),
            order_book_status="disabled",
            order_book_unavailable_reason="order_book_not_authorized",
        ),
    )

    assert record.source_event_at <= record.received_at <= record.available_at


@pytest.mark.parametrize(
    "changes",
    [
        {"received_at": T0 - timedelta(seconds=1)},
        {"available_at": T0 - timedelta(seconds=1)},
    ],
)
def test_quote_record_rejects_reversed_timestamps(changes):
    values = {
        "batch_id": "batch-1",
        "source_id": "licensed-feed",
        "source_version": "v1",
        "source_event_at": T0,
        "received_at": T0,
        "available_at": T0,
        "quote": QuoteInput(
            security_id="600000.SH",
            last=Decimal("10"),
            order_book_status="disabled",
            order_book_unavailable_reason="order_book_not_authorized",
        ),
    }
    values.update(changes)

    with pytest.raises(ValidationError, match="received_at|available_at"):
        QuoteRecord(**values)


@pytest.mark.parametrize("field", ["source_event_at", "received_at", "available_at"])
def test_model_rejects_naive_timestamps(field):
    values = {
        "batch_id": "batch-1",
        "source_id": "licensed-feed",
        "source_version": "v1",
        "source_event_at": T0,
        "received_at": T0,
        "available_at": T0,
        "permission": permission(),
        "quotes": (),
    }
    values[field] = T0.replace(tzinfo=None)

    with pytest.raises(ValidationError, match="timezone|aware"):
        QuoteBatchInput(**values)


def test_model_trims_stable_security_id_and_rejects_blank_ids():
    quote = QuoteInput(
        security_id=" 600000.SH ",
        last=Decimal("10"),
        order_book_status="disabled",
        order_book_unavailable_reason="order_book_not_authorized",
    )

    assert quote.security_id == "600000.SH"
    with pytest.raises(ValidationError):
        QuoteInput(
            security_id="  ",
            last=None,
            order_book_status="unavailable",
            order_book_unavailable_reason="source_unavailable",
        )


def test_quote_models_keep_last_independent_from_disabled_order_book():
    quote = QuoteInput(
        security_id="600000.SH",
        last=Decimal("10"),
        order_book_status="disabled",
        order_book_unavailable_reason="order_book_not_authorized",
    )
    record = QuoteRecord(
        batch_id="batch-1",
        source_id="licensed-feed",
        source_version="v1",
        source_event_at=T0,
        received_at=T0,
        available_at=T0,
        quote=quote,
    )

    assert quote.last == Decimal("10")
    assert quote.bid is None
    assert quote.bid_size is None
    assert quote.order_book_status == "disabled"
    assert quote.order_book_unavailable_reason == "order_book_not_authorized"
    assert record.quote == quote


def test_quote_model_accepts_complete_order_book_without_last():
    quote = QuoteInput(
        security_id="600000.SH",
        last=None,
        bid=Decimal("9.99"),
        ask=Decimal("10.01"),
        bid_size=Decimal("100"),
        ask_size=Decimal("200"),
        order_book_status="available",
        order_book_unavailable_reason=None,
    )

    assert quote.last is None
    assert (quote.bid, quote.ask, quote.bid_size, quote.ask_size) == (
        Decimal("9.99"),
        Decimal("10.01"),
        Decimal("100"),
        Decimal("200"),
    )


@pytest.mark.parametrize("missing_field", ["bid", "ask", "bid_size", "ask_size"])
def test_available_order_book_requires_every_bid_ask_pair_field(missing_field):
    values = {
        "security_id": "600000.SH",
        "last": None,
        "bid": Decimal("9.99"),
        "ask": Decimal("10.01"),
        "bid_size": Decimal("100"),
        "ask_size": Decimal("200"),
        "order_book_status": "available",
        "order_book_unavailable_reason": None,
    }
    values[missing_field] = None

    with pytest.raises(ValidationError, match="available order book"):
        QuoteInput(**values)


def test_available_order_book_rejects_unavailable_reason():
    with pytest.raises(ValidationError, match="available order book"):
        QuoteInput(
            security_id="600000.SH",
            bid=Decimal("9.99"),
            ask=Decimal("10.01"),
            bid_size=Decimal("100"),
            ask_size=Decimal("200"),
            order_book_status="available",
            order_book_unavailable_reason="not_really_available",
        )


@pytest.mark.parametrize("order_book_status", ["disabled", "unavailable"])
@pytest.mark.parametrize("field", ["bid", "ask", "bid_size", "ask_size"])
def test_nonavailable_order_book_rejects_any_book_value(order_book_status, field):
    values = {
        "security_id": "600000.SH",
        "last": Decimal("10"),
        "order_book_status": order_book_status,
        "order_book_unavailable_reason": "order_book_not_authorized",
        field: Decimal("1"),
    }

    with pytest.raises(ValidationError, match="must not contain"):
        QuoteInput(**values)


@pytest.mark.parametrize("order_book_status", ["disabled", "unavailable"])
def test_nonavailable_order_book_requires_reason(order_book_status):
    with pytest.raises(ValidationError, match="reason"):
        QuoteInput(
            security_id="600000.SH",
            last=Decimal("10"),
            order_book_status=order_book_status,
            order_book_unavailable_reason=None,
        )


@pytest.mark.parametrize(
    "extra",
    [
        {"status": "available"},
        {"unavailable_reason": "legacy"},
        {"daily_close": Decimal("10")},
    ],
)
def test_quote_model_forbids_legacy_and_daily_order_book_fallback_fields(extra):
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        QuoteInput(
            security_id="600000.SH",
            last=Decimal("10"),
            order_book_status="disabled",
            order_book_unavailable_reason="order_book_not_authorized",
            **extra,
        )


def test_quote_model_remains_frozen():
    quote = QuoteInput(
        security_id="600000.SH",
        last=Decimal("10"),
        order_book_status="disabled",
        order_book_unavailable_reason="order_book_not_authorized",
    )

    with pytest.raises(ValidationError, match="frozen"):
        quote.order_book_status = "available"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("close", Decimal("NaN")),
        ("high", Decimal("Infinity")),
        ("volume", Decimal("-1")),
        ("amount", Decimal("-0.01")),
    ],
)
def test_model_rejects_nonfinite_or_negative_price_volume(field, value):
    values = minute_bar().model_dump()
    values[field] = value

    with pytest.raises(ValidationError):
        MinuteBarInput.model_validate(values)


@pytest.mark.parametrize(
    "changes",
    [
        {"open": Decimal("101"), "high": Decimal("100")},
        {"close": Decimal("99"), "low": Decimal("100")},
        {"high": Decimal("99"), "low": Decimal("100")},
    ],
)
def test_model_enforces_ohlc_constraints(changes):
    values = minute_bar().model_dump()
    values.update(changes)

    with pytest.raises(ValidationError, match="OHLC|high|low"):
        MinuteBarInput.model_validate(values)


@pytest.mark.parametrize("revision", [0, -1])
def test_model_requires_positive_revision(revision):
    values = minute_bar().model_dump()
    values["revision"] = revision

    with pytest.raises(ValidationError, match="revision"):
        MinuteBarInput.model_validate(values)


def test_session_model_rejects_bar_outside_declared_trading_session():
    with pytest.raises(ValidationError, match="trading session"):
        MinuteBatchInput(
            batch_id="minute-batch-1",
            source_id="licensed-feed",
            source_version="v1",
            source_event_at=T0,
            received_at=T0,
            available_at=T0,
            permission=permission(),
            session_start=T0,
            session_end=T0 + timedelta(minutes=2),
            bars=(minute_bar(minute=2),),
        )


def test_session_model_keeps_intraday_batch_separate_from_daily_snapshot():
    batch = MinuteBatchInput(
        batch_id="minute-batch-1",
        source_id="licensed-feed",
        source_version="v1",
        source_event_at=T0,
        received_at=T0,
        available_at=T0,
        permission=permission(),
        session_start=T0,
        session_end=T0 + timedelta(minutes=3),
        bars=minute_bars(["100", "101", "103"]),
    )

    assert batch.batch_id == "minute-batch-1"
    assert not hasattr(batch, "market_snapshot_id")


def test_three_minute_return_triggers_at_two_percent():
    result = detect_anomalies(
        bars=minute_bars(["100", "101", "103"]),
        rule=return_rule(),
    )

    assert result.value == Decimal("0.03")
    assert result.triggered is True
    assert result.reason is None


def test_insufficient_window_returns_reason_without_event():
    result = detect_anomalies(
        bars=minute_bars(["100", "103"]),
        rule=return_rule(window=3),
    )

    assert result.triggered is False
    assert result.value is None
    assert result.reason == "insufficient_window"


def test_window_return_uses_stable_event_time_revision_order():
    bars = (
        minute_bar("103", minute=2),
        minute_bar("99", minute=1, revision=1),
        minute_bar("101", minute=1, revision=2),
        minute_bar("100", minute=0),
    )

    result = detect_anomalies(bars=bars, rule=return_rule(window=2))

    assert result.value == Decimal("103") / Decimal("101") - Decimal(1)


def test_window_return_zero_denominator_has_explicit_reason():
    result = detect_anomalies(
        bars=minute_bars(["0", "1", "2"]),
        rule=return_rule(),
    )

    assert result.triggered is False
    assert result.value is None
    assert result.reason == "zero_denominator"


def test_relative_volume_missing_volume_has_explicit_reason():
    result = detect_anomalies(
        bars=minute_bars(["100", "101", "102"], volumes=["100", "110", None]),
        rule=AnomalyRule(
            rule_version="volume-v1",
            metric="relative_volume",
            window=3,
            threshold=Decimal("1.5"),
            cooldown_seconds=300,
        ),
    )

    assert result.triggered is False
    assert result.value is None
    assert result.reason == "volume_unavailable"


def test_relative_volume_zero_denominator_has_explicit_reason():
    result = detect_anomalies(
        bars=minute_bars(["100", "101", "102"], volumes=["0", "110", "200"]),
        rule=AnomalyRule(
            rule_version="volume-v1",
            metric="relative_volume",
            window=3,
            threshold=Decimal("1.5"),
            cooldown_seconds=300,
        ),
    )

    assert result.triggered is False
    assert result.value is None
    assert result.reason == "zero_denominator"


def test_order_book_rule_without_order_book_input_is_disabled():
    result = detect_anomalies(
        bars=minute_bars(["100", "101", "102"]),
        rule=AnomalyRule(
            rule_version="book-v1",
            metric="order_book_imbalance",
            window=3,
            threshold=Decimal("0.2"),
            cooldown_seconds=300,
        ),
    )

    assert result.triggered is False
    assert result.value is None
    assert result.reason == "order_book_unavailable"


def test_model_sector_flow_requires_explicit_unavailable_reason():
    with pytest.raises(ValidationError, match="unavailable_reason"):
        SectorFlowPoint(
            sector_id="sector-1",
            event_at=T0,
            net_inflow=None,
            unit="CNY",
        )

    point = SectorFlowPoint(
        sector_id=" sector-1 ",
        event_at=T0,
        net_inflow=None,
        unit="CNY",
        unavailable_reason="field_not_authorized",
    )
    batch = SectorFlowBatchInput(
        batch_id="flow-batch-1",
        source_id="licensed-feed",
        source_version="v1",
        source_event_at=T0,
        received_at=T0,
        available_at=T0,
        permission=permission(),
        session_start=T0,
        session_end=T0 + timedelta(minutes=1),
        points=(point,),
    )

    assert batch.points[0].sector_id == "sector-1"
    assert batch.points[0].net_inflow is None
