import json
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from threading import Event, current_thread
from time import perf_counter
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from dragon_radar.realtime.anomalies import detect_anomalies
from dragon_radar.realtime.models import (
    AnomalyEvent,
    AnomalyResult,
    AnomalyRule,
    MinuteBarInput,
    MinuteBatchInput,
    PermissionSnapshot,
    QuoteBatchInput,
    QuoteInput,
    QuoteRecord,
    SectorFlowBatchInput,
    SectorFlowPoint,
    SourceCompleteness,
)

T0 = datetime(2026, 10, 9, 1, 30, tzinfo=UTC)


def permission(state: str = "authorized") -> PermissionSnapshot:
    return PermissionSnapshot(
        permission_id="permission-1",
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
        security_id="600000.SH",
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


@pytest.mark.parametrize(
    "value",
    (
        "",
        " 600000.SH ",
        "é",
        "\x1f",
        "-leading",
        ".leading",
        "A" * 201,
    ),
)
def test_model_rejects_noncanonical_persisted_child_identifiers(value):
    model_payloads = (
        (
            QuoteInput,
            {
                "security_id": value,
                "last": Decimal("10"),
                "order_book_status": "disabled",
                "order_book_unavailable_reason": "provider policy unavailable",
            },
        ),
        (
            MinuteBarInput,
            {
                **minute_bar().model_dump(),
                "security_id": value,
            },
        ),
        (
            SectorFlowPoint,
            {
                "sector_id": value,
                "event_at": T0,
                "net_inflow": None,
                "unit": "currency/share",
                "unavailable_reason": "供应商 暂未授权",
            },
        ),
        (
            AnomalyRule,
            {
                "rule_version": value,
                "metric": "window_return",
                "window": 2,
                "threshold": Decimal("0.02"),
                "cooldown_seconds": 300,
            },
        ),
    )

    for model_type, payload in model_payloads:
        with pytest.raises(ValidationError):
            model_type.model_validate(payload)


def test_model_preserves_nonidentity_unit_and_human_reasons():
    quote = QuoteInput(
        security_id="600000.SH",
        last=None,
        order_book_status="unavailable",
        order_book_unavailable_reason="provider policy unavailable",
    )
    point = SectorFlowPoint(
        sector_id="sector-1",
        event_at=T0,
        net_inflow=None,
        unit="currency/share",
        unavailable_reason="供应商 暂未授权",
    )

    assert quote.order_book_unavailable_reason == "provider policy unavailable"
    assert point.unit == "currency/share"
    assert point.unavailable_reason == "供应商 暂未授权"


def test_model_rejects_blank_nonidentity_text():
    with pytest.raises(ValidationError):
        QuoteInput(
            security_id="600000.SH",
            last=None,
            order_book_status="unavailable",
            order_book_unavailable_reason="  ",
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


@pytest.mark.parametrize(
    "values",
    [
        {"status": "complete", "expected_count": None, "received_count": 0},
        {"status": "complete", "expected_count": 2, "received_count": 1},
        {
            "status": "incomplete",
            "expected_count": 2,
            "received_count": 2,
            "reason": "stream_gap",
        },
    ],
)
def test_source_completeness_rejects_status_count_contradictions(values):
    with pytest.raises(ValidationError, match="complete|expected_count"):
        SourceCompleteness(**values)


@pytest.mark.parametrize(
    "values",
    [
        {"status": "complete", "expected_count": 2, "received_count": 2},
        {
            "status": "delayed",
            "expected_count": 2,
            "received_count": 2,
            "reason": "stale_stream",
        },
        {
            "status": "incomplete",
            "expected_count": 2,
            "received_count": 1,
            "reason": "stream_gap",
        },
        {
            "status": "unavailable",
            "expected_count": None,
            "received_count": 0,
            "reason": "source_unavailable",
        },
    ],
)
def test_source_completeness_accepts_consistent_enum_states(values):
    completeness = SourceCompleteness(**values)

    assert completeness.status == values["status"]


def test_source_completeness_rejects_partial_outside_canonical_enum():
    with pytest.raises(ValidationError, match="literal_error"):
        SourceCompleteness(
            status="partial",
            expected_count=2,
            received_count=1,
            reason="stream_gap",
        )


def test_anomaly_event_rejects_availability_before_event_time():
    with pytest.raises(ValidationError, match="available_at"):
        AnomalyEvent(
            event_id="event-1",
            batch_id="batch-1",
            security_id="600000.SH",
            rule_version="return-v1",
            event_at=T0,
            available_at=T0 - timedelta(microseconds=1),
            result=AnomalyResult(
                triggered=True,
                metric="window_return",
                window=3,
                value=Decimal("0.03"),
                threshold=Decimal("0.02"),
                reason=None,
            ),
            quality="complete",
        )


def test_anomaly_event_accepts_equal_event_and_availability_times():
    event = AnomalyEvent(
        event_id="event-1",
        batch_id="batch-1",
        security_id="600000.SH",
        rule_version="return-v1",
        event_at=T0,
        available_at=T0,
        result=AnomalyResult(
            triggered=True,
            metric="window_return",
            window=3,
            value=Decimal("0.03"),
            threshold=Decimal("0.02"),
            reason=None,
        ),
        quality="complete",
    )

    assert event.event_at == event.available_at


def test_three_minute_return_triggers_at_two_percent():
    result = detect_anomalies(
        bars=minute_bars(["100", "101", "103"]),
        rule=return_rule(),
    )

    assert result.value == Decimal("0.03")
    assert result.triggered is True
    assert result.reason is None


def test_anomaly_decimal34_result_is_independent_of_global_precision():
    results = []
    for precision in (6, 28):
        with localcontext() as context:
            context.prec = precision
            results.append(
                detect_anomalies(
                    bars=minute_bars(["3", "10"]),
                    rule=return_rule(window=2),
                ).value
            )

    assert results == [
        Decimal("2.333333333333333333333333333333333"),
        Decimal("2.333333333333333333333333333333333"),
    ]


def test_relative_volume_decimal34_result_is_independent_of_global_precision():
    rule = AnomalyRule(
        rule_version="volume-v1",
        metric="relative_volume",
        window=2,
        threshold=Decimal("3"),
        cooldown_seconds=300,
    )
    results = []
    for precision in (6, 28):
        with localcontext() as context:
            context.prec = precision
            results.append(
                detect_anomalies(
                    bars=minute_bars(["100", "101"], volumes=["3", "10"]),
                    rule=rule,
                ).value
            )

    assert results == [
        Decimal("3.333333333333333333333333333333333"),
        Decimal("3.333333333333333333333333333333333"),
    ]


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

    assert result.value == Decimal("0.019801980198019801980198019801980")


def test_old_revision_cannot_fill_anomaly_window():
    bars = (
        minute_bar("100", minute=0),
        minute_bar("101", minute=1, revision=1),
        minute_bar("102", minute=1, revision=2),
    )

    result = detect_anomalies(bars=bars, rule=return_rule(window=3))

    assert result.value is None
    assert result.reason == "insufficient_window"


@pytest.mark.parametrize(
    "revisions",
    [
        (2, 1, 1),
        (1, 1, 2),
        (1, 2, 1),
    ],
)
def test_anomaly_window_rejects_duplicate_revisions_in_any_order(revisions):
    bars = tuple(
        minute_bar(str(100 + index), minute=1, revision=revision)
        for index, revision in enumerate(revisions)
    )

    with pytest.raises(ValueError, match="duplicate minute bar revision"):
        detect_anomalies(bars=bars, rule=return_rule(window=2))


def test_anomaly_window_rejects_mixed_security_ids():
    other_security = minute_bar("101", minute=1).model_copy(
        update={"security_id": "600001.SH"}
    )

    with pytest.raises(ValueError, match="same security_id"):
        detect_anomalies(
            bars=(minute_bar("100", minute=0), other_security),
            rule=return_rule(window=2),
        )


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
        sector_id="sector-1",
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


EXPECTED_REALTIME_TABLES = {
    "realtime_quote_batches",
    "realtime_quotes",
    "realtime_minute_bars",
    "realtime_anomaly_events",
    "realtime_sector_flow_points",
    "realtime_import_attempts",
}

EXPECTED_REALTIME_COLUMNS = {
    "realtime_quote_batches": [
        "id",
        "ingest_order",
        "quote_batch_id",
        "source_id",
        "source_version",
        "source_event_at",
        "received_at",
        "available_at",
        "source_sequence",
        "permission_snapshot",
        "payload_hash",
        "imported_at",
    ],
    "realtime_quotes": [
        "id",
        "source_id",
        "batch_id",
        "security_id",
        "last",
        "bid",
        "ask",
        "bid_size",
        "ask_size",
        "volume",
        "amount",
        "order_book_status",
        "order_book_unavailable_reason",
    ],
    "realtime_minute_bars": [
        "id",
        "source_id",
        "batch_id",
        "source_version",
        "security_id",
        "event_at",
        "revision",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "final",
        "source_event_at",
        "received_at",
        "available_at",
    ],
    "realtime_anomaly_events": [
        "event_id",
        "source_id",
        "batch_id",
        "security_id",
        "rule_version",
        "metric",
        "window",
        "value",
        "threshold",
        "event_at",
        "available_at",
        "quality",
        "cooldown_bucket",
    ],
    "realtime_sector_flow_points": [
        "id",
        "source_id",
        "batch_id",
        "sector_id",
        "event_at",
        "net_inflow",
        "unit",
        "unavailable_reason",
    ],
    "realtime_import_attempts": [
        "id",
        "source_id",
        "batch_id",
        "idempotency_key",
        "payload_hash",
        "status",
        "reason",
        "details",
        "created_at",
    ],
}

EXPECTED_REALTIME_CONSTRAINTS = {
    "realtime_quote_batches": {
        "realtime_quote_batches_pkey",
        "realtime_quote_batches_ingest_order_key",
        "realtime_quote_batches_source_batch_key",
        "realtime_quote_batches_source_batch_version_key",
        "realtime_quote_batches_source_id_fkey",
        "realtime_quote_batches_timestamp_order",
        "realtime_quote_batches_source_sequence",
        "realtime_quote_batches_payload_hash",
    },
    "realtime_quotes": {
        "realtime_quotes_pkey",
        "realtime_quotes_batch_fkey",
        "realtime_quotes_batch_security_key",
        "realtime_quotes_nonnegative_values",
        "realtime_quotes_order_book_status",
        "realtime_quotes_order_book_availability",
    },
    "realtime_minute_bars": {
        "realtime_minute_bars_pkey",
        "realtime_minute_bars_batch_fkey",
        "realtime_minute_bars_revision_key",
        "realtime_minute_bars_positive_revision",
        "realtime_minute_bars_nonnegative_values",
        "realtime_minute_bars_ohlc",
        "realtime_minute_bars_optional_values",
        "realtime_minute_bars_timestamp_order",
    },
    "realtime_anomaly_events": {
        "realtime_anomaly_events_pkey",
        "realtime_anomaly_events_batch_fkey",
        "realtime_anomaly_events_cooldown_key",
        "realtime_anomaly_events_metric",
        "realtime_anomaly_events_window",
        "realtime_anomaly_events_quality",
        "realtime_anomaly_events_timestamp_order",
    },
    "realtime_sector_flow_points": {
        "realtime_sector_flow_points_pkey",
        "realtime_sector_flow_points_batch_fkey",
        "realtime_sector_flow_points_identity_key",
        "realtime_sector_flow_points_availability",
    },
    "realtime_import_attempts": {
        "realtime_import_attempts_pkey",
        "realtime_import_attempts_source_id_fkey",
        "realtime_import_attempts_status",
        "realtime_import_attempts_payload_hash",
    },
}

EXPECTED_REALTIME_INDEXES = {
    "realtime_quote_batches_as_of",
    "realtime_quote_batches_source_as_of",
    "realtime_quotes_security",
    "realtime_minute_bars_as_of",
    "realtime_minute_bars_batch",
    "realtime_anomaly_events_listing",
    "realtime_anomaly_events_security",
    "realtime_sector_flow_points_event",
    "realtime_import_attempts_listing",
}


def _upgrade_realtime(database_url):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from dragon_radar.storage.database import Database

    database = Database(database_url)
    config = Config()
    config.set_main_option("script_location", "dragon_radar:migrations")
    with database.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0010_ai_tasks")
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0010_ai_tasks"
        )
        command.upgrade(config, "0011_realtime")
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0011_realtime"
        )
    return database


def test_0011_realtime_extends_0010_with_six_exact_tables(database_url):
    from sqlalchemy import inspect

    database = _upgrade_realtime(database_url)
    try:
        inspector = inspect(database.engine)
        assert EXPECTED_REALTIME_TABLES <= set(inspector.get_table_names())
        for table, expected_columns in EXPECTED_REALTIME_COLUMNS.items():
            columns = inspector.get_columns(table)
            assert [column["name"] for column in columns] == expected_columns
        ingest_order = next(
            column
            for column in inspector.get_columns("realtime_quote_batches")
            if column["name"] == "ingest_order"
        )
        assert ingest_order["type"].python_type is int
        assert ingest_order["identity"]["always"] is True
    finally:
        database.close()


def test_realtime_schema_has_exact_named_constraints_indexes_and_triggers(
    database_url,
):
    from sqlalchemy import text

    database = _upgrade_realtime(database_url)
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
                {"tables": sorted(EXPECTED_REALTIME_TABLES)},
            )
            actual_constraints = {table: set() for table in EXPECTED_REALTIME_TABLES}
            for table, name in constraints:
                actual_constraints[table].add(name)
            assert actual_constraints == EXPECTED_REALTIME_CONSTRAINTS

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
                    {"tables": sorted(EXPECTED_REALTIME_TABLES)},
                ).scalars()
            )
            assert indexes == EXPECTED_REALTIME_INDEXES
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
                            "realtime_quote_batches_as_of",
                            "realtime_quote_batches_source_as_of",
                            "realtime_quotes_security",
                        ]
                    },
                ).all()
            )
            assert index_definitions["realtime_quote_batches_as_of"].endswith(
                "USING btree (available_at DESC, ingest_order DESC, id DESC)"
            )
            assert index_definitions["realtime_quote_batches_source_as_of"].endswith(
                "USING btree (source_id, available_at DESC, ingest_order DESC, id DESC)"
            )
            assert index_definitions["realtime_quotes_security"].endswith(
                "USING btree (security_id, source_id, batch_id)"
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
                    {"tables": sorted(EXPECTED_REALTIME_TABLES)},
                )
            )
        assert len(triggers) == 12
        for table in EXPECTED_REALTIME_TABLES:
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


def test_realtime_schema_preserves_nulls_decimal_precision_and_source_time(
    database_url,
):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    database = _upgrade_realtime(database_url)
    source_id = "licensed-feed"
    batch_id = "batch-null-preservation"
    batch_record_id = uuid4()
    quote_id = uuid4()
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO source_grants (source_id, "grant", updated_at)
                    VALUES (:source_id, '{}', :now)
                    """
                ),
                {"source_id": source_id, "now": T0},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO realtime_quote_batches (
                        id, quote_batch_id, source_id, source_version,
                        source_event_at, received_at, available_at,
                        source_sequence, permission_snapshot, payload_hash,
                        imported_at
                    ) VALUES (
                        :id, :batch_id, :source_id, 'v1', :source_event_at,
                        :received_at, :available_at, NULL, '{}', :payload_hash,
                        :imported_at
                    )
                    """
                ),
                {
                    "id": batch_record_id,
                    "batch_id": batch_id,
                    "source_id": source_id,
                    "source_event_at": T0,
                    "received_at": T0 + timedelta(seconds=1),
                    "available_at": T0 + timedelta(seconds=2),
                    "payload_hash": "a" * 64,
                    "imported_at": T0 + timedelta(seconds=3),
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO realtime_quotes (
                        id, source_id, batch_id, security_id, last, bid, ask,
                        bid_size, ask_size, volume, amount, order_book_status,
                        order_book_unavailable_reason
                    ) VALUES (
                        :id, :source_id, :batch_id, '600000.SH',
                        :last, NULL, NULL, NULL, NULL, NULL, NULL, 'disabled',
                        'order_book_not_authorized'
                    )
                    """
                ),
                {
                    "id": quote_id,
                    "source_id": source_id,
                    "batch_id": batch_id,
                    "last": Decimal("12345678901234567890.123456789012345678"),
                },
            )
        with database.engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT batch.source_event_at, batch.received_at,
                           batch.available_at, batch.source_sequence,
                           quote.last, quote.bid, quote.volume, quote.amount
                    FROM realtime_quote_batches batch
                    JOIN realtime_quotes quote
                      ON quote.source_id = batch.source_id
                     AND quote.batch_id = batch.quote_batch_id
                    WHERE quote.id = :id
                    """
                ),
                {"id": quote_id},
            ).one()
        assert row._tuple() == (
            T0,
            T0 + timedelta(seconds=1),
            T0 + timedelta(seconds=2),
            None,
            Decimal("12345678901234567890.123456789012345678"),
            None,
            None,
            None,
        )

        with pytest.raises(DBAPIError), database.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO realtime_quotes (
                        id, source_id, batch_id, security_id, last,
                        order_book_status, order_book_unavailable_reason
                    ) VALUES (
                        :id, :source_id, :batch_id, 'bad-negative', -1,
                        'disabled', 'not_authorized'
                    )
                    """
                ),
                {"id": uuid4(), "source_id": source_id, "batch_id": batch_id},
            )
    finally:
        database.close()


def test_realtime_immutable_tables_reject_update_delete_and_truncate(database_url):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    database = _upgrade_realtime(database_url)
    source_id = "immutable-feed"
    batch_id = "immutable-batch"
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO source_grants (source_id, "grant", updated_at)
                    VALUES (:source_id, '{}', :now)
                    """
                ),
                {"source_id": source_id, "now": T0},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO realtime_quote_batches (
                        id, quote_batch_id, source_id, source_version,
                        source_event_at, received_at, available_at,
                        permission_snapshot, payload_hash, imported_at
                    ) VALUES (
                        :id, :batch_id, :source_id, 'v1', :now, :now, :now,
                        '{}', :payload_hash, :now
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "batch_id": batch_id,
                    "source_id": source_id,
                    "payload_hash": "b" * 64,
                    "now": T0,
                },
            )

        statements = (
            (
                "UPDATE realtime_quote_batches SET source_version = 'v2' "
                "WHERE quote_batch_id = :batch_id"
            ),
            ("DELETE FROM realtime_quote_batches WHERE quote_batch_id = :batch_id"),
            "TRUNCATE TABLE realtime_quote_batches",
        )
        for statement in statements:
            with pytest.raises(DBAPIError), database.engine.begin() as connection:
                connection.execute(text(statement), {"batch_id": batch_id})
    finally:
        database.close()


def test_realtime_source_version_catalog_binds_only_versioned_children(database_url):
    from sqlalchemy import inspect

    database = _upgrade_realtime(database_url)
    try:
        inspector = inspect(database.engine)
        unique_constraints = {
            (constraint["name"], tuple(constraint["column_names"]))
            for constraint in inspector.get_unique_constraints("realtime_quote_batches")
        }
        assert unique_constraints == {
            (
                "realtime_quote_batches_ingest_order_key",
                ("ingest_order",),
            ),
            (
                "realtime_quote_batches_source_batch_key",
                ("source_id", "quote_batch_id"),
            ),
            (
                "realtime_quote_batches_source_batch_version_key",
                ("source_id", "quote_batch_id", "source_version"),
            ),
        }
        attempt_index = next(
            index
            for index in inspector.get_indexes("realtime_import_attempts")
            if index["name"] == "realtime_import_attempts_bound_idempotency_key"
        )
        assert attempt_index["unique"] is True
        assert attempt_index["column_names"] == ["source_id", "idempotency_key"]
        predicate = str(attempt_index["dialect_options"]["postgresql_where"])
        assert "accepted" in predicate and "incomplete" in predicate

        minute_batch_fk = next(
            constraint
            for constraint in inspector.get_foreign_keys("realtime_minute_bars")
            if constraint["name"] == "realtime_minute_bars_batch_fkey"
        )
        assert minute_batch_fk["constrained_columns"] == [
            "source_id",
            "batch_id",
            "source_version",
        ]
        assert minute_batch_fk["referred_columns"] == [
            "source_id",
            "quote_batch_id",
            "source_version",
        ]

        for table in (
            "realtime_quotes",
            "realtime_anomaly_events",
            "realtime_sector_flow_points",
        ):
            assert "source_version" not in {
                column["name"] for column in inspector.get_columns(table)
            }
    finally:
        database.close()


def test_realtime_key_index_binds_incomplete_but_keeps_audit_attempts(database_url):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    database = _upgrade_realtime(database_url)
    source_id = "incomplete-index-feed"
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO source_grants (source_id, "grant", updated_at)
                    VALUES (:source_id, '{}', :now)
                    """
                ),
                {"source_id": source_id, "now": T0},
            )
            for status, reason in (
                ("incomplete", "stream_gap"),
                ("duplicate", None),
                ("rejected", "idempotency_key_conflict"),
            ):
                connection.execute(
                    text(
                        """
                        INSERT INTO realtime_import_attempts (
                            id, source_id, batch_id, idempotency_key,
                            payload_hash, status, reason, details, created_at
                        ) VALUES (
                            :id, :source_id, :batch_id, 'occupied-key',
                            :payload_hash, :status, :reason, '{}', :now
                        )
                        """
                    ),
                    {
                        "id": uuid4(),
                        "source_id": source_id,
                        "batch_id": f"{status}-batch",
                        "payload_hash": "a" * 64,
                        "status": status,
                        "reason": reason,
                        "now": T0,
                    },
                )

        with pytest.raises(DBAPIError), database.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO realtime_import_attempts (
                        id, source_id, batch_id, idempotency_key,
                        payload_hash, status, reason, details, created_at
                    ) VALUES (
                        :id, :source_id, 'second-incomplete', 'occupied-key',
                        :payload_hash, 'incomplete', 'stream_gap', '{}', :now
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "source_id": source_id,
                    "payload_hash": "b" * 64,
                    "now": T0,
                },
            )
    finally:
        database.close()


def test_minute_bar_rejects_parent_source_version_mismatch(database_url):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    database = _upgrade_realtime(database_url)
    source_id = "versioned-feed"
    batch_id = "versioned-batch"
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO source_grants (source_id, "grant", updated_at)
                    VALUES (:source_id, '{}', :now)
                    """
                ),
                {"source_id": source_id, "now": T0},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO realtime_quote_batches (
                        id, quote_batch_id, source_id, source_version,
                        source_event_at, received_at, available_at,
                        permission_snapshot, payload_hash, imported_at
                    ) VALUES (
                        :id, :batch_id, :source_id, 'v1', :now, :now, :now,
                        '{}', :payload_hash, :now
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "batch_id": batch_id,
                    "source_id": source_id,
                    "payload_hash": "c" * 64,
                    "now": T0,
                },
            )

        with pytest.raises(DBAPIError), database.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO realtime_minute_bars (
                        id, source_id, batch_id, source_version, security_id,
                        event_at, revision, open, high, low, close, final,
                        source_event_at, received_at, available_at
                    ) VALUES (
                        :id, :source_id, :batch_id, 'v2', '600000.SH',
                        :now, 1, 10, 10, 10, 10, true, :now, :now, :now
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "source_id": source_id,
                    "batch_id": batch_id,
                    "now": T0,
                },
            )
    finally:
        database.close()


@pytest.mark.parametrize(
    "statement",
    [
        """
        INSERT INTO realtime_quotes (
            id, source_id, batch_id, security_id, order_book_status,
            order_book_unavailable_reason
        ) VALUES (
            :id, :source_id, :batch_id, '600000.SH', 'disabled', :reason
        )
        """,
        """
        INSERT INTO realtime_sector_flow_points (
            id, source_id, batch_id, sector_id, event_at, net_inflow, unit,
            unavailable_reason
        ) VALUES (
            :id, :source_id, :batch_id, 'sector-1', :now, NULL, 'CNY', :reason
        )
        """,
        """
        INSERT INTO realtime_import_attempts (
            id, source_id, batch_id, idempotency_key, payload_hash, status,
            reason, details, created_at
        ) VALUES (
            :id, :source_id, :batch_id, 'empty-reason', :payload_hash,
            'rejected', :reason, '{}', :now
        )
        """,
    ],
    ids=("order-book", "sector-flow", "import-attempt"),
)
@pytest.mark.parametrize(
    "reason",
    ["", " ", "\t", "\n", " \t\n "],
    ids=("empty", "space", "tab", "newline", "mixed-whitespace"),
)
def test_realtime_required_reasons_reject_blank_strings(
    database_url, statement, reason
):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    database = _upgrade_realtime(database_url)
    source_id = "reason-feed"
    batch_id = "reason-batch"
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO source_grants (source_id, "grant", updated_at)
                    VALUES (:source_id, '{}', :now)
                    """
                ),
                {"source_id": source_id, "now": T0},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO realtime_quote_batches (
                        id, quote_batch_id, source_id, source_version,
                        source_event_at, received_at, available_at,
                        permission_snapshot, payload_hash, imported_at
                    ) VALUES (
                        :id, :batch_id, :source_id, 'v1', :now, :now, :now,
                        '{}', :payload_hash, :now
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "batch_id": batch_id,
                    "source_id": source_id,
                    "payload_hash": "d" * 64,
                    "now": T0,
                },
            )

        with pytest.raises(DBAPIError), database.engine.begin() as connection:
            connection.execute(
                text(statement),
                {
                    "id": uuid4(),
                    "source_id": source_id,
                    "batch_id": batch_id,
                    "payload_hash": "e" * 64,
                    "now": T0,
                    "reason": reason,
                },
            )
    finally:
        database.close()


@pytest.mark.parametrize(
    ("statement", "select_reason"),
    [
        (
            """
            INSERT INTO realtime_quotes (
                id, source_id, batch_id, security_id, order_book_status,
                order_book_unavailable_reason
            ) VALUES (
                :id, :source_id, :batch_id, '600000.SH', 'disabled', :reason
            )
            """,
            """
            SELECT order_book_unavailable_reason
            FROM realtime_quotes
            WHERE id = :id
            """,
        ),
        (
            """
            INSERT INTO realtime_sector_flow_points (
                id, source_id, batch_id, sector_id, event_at, net_inflow, unit,
                unavailable_reason
            ) VALUES (
                :id, :source_id, :batch_id, 'sector-1', :now, NULL, 'CNY', :reason
            )
            """,
            """
            SELECT unavailable_reason
            FROM realtime_sector_flow_points
            WHERE id = :id
            """,
        ),
        (
            """
            INSERT INTO realtime_import_attempts (
                id, source_id, batch_id, idempotency_key, payload_hash, status,
                reason, details, created_at
            ) VALUES (
                :id, :source_id, :batch_id, 'valid-reason', :payload_hash,
                'rejected', :reason, '{}', :now
            )
            """,
            """
            SELECT reason
            FROM realtime_import_attempts
            WHERE id = :id
            """,
        ),
    ],
    ids=("order-book", "sector-flow", "import-attempt"),
)
def test_realtime_required_reasons_accept_nonblank_string(
    database_url, statement, select_reason
):
    from sqlalchemy import text

    database = _upgrade_realtime(database_url)
    source_id = "reason-feed"
    batch_id = "reason-batch"
    row_id = uuid4()
    reason = "source unavailable"
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO source_grants (source_id, "grant", updated_at)
                    VALUES (:source_id, '{}', :now)
                    """
                ),
                {"source_id": source_id, "now": T0},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO realtime_quote_batches (
                        id, quote_batch_id, source_id, source_version,
                        source_event_at, received_at, available_at,
                        permission_snapshot, payload_hash, imported_at
                    ) VALUES (
                        :id, :batch_id, :source_id, 'v1', :now, :now, :now,
                        '{}', :payload_hash, :now
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "batch_id": batch_id,
                    "source_id": source_id,
                    "payload_hash": "d" * 64,
                    "now": T0,
                },
            )
            parameters = {
                "id": row_id,
                "source_id": source_id,
                "batch_id": batch_id,
                "payload_hash": "e" * 64,
                "now": T0,
                "reason": reason,
            }
            connection.execute(text(statement), parameters)
            stored_reason = connection.scalar(text(select_reason), {"id": row_id})

        assert stored_reason == reason
    finally:
        database.close()


def test_0011_realtime_downgrade_is_forbidden(database_url):
    from alembic import command
    from alembic.config import Config

    database = _upgrade_realtime(database_url)
    config = Config()
    config.set_main_option("script_location", "dragon_radar:migrations")
    try:
        with (
            pytest.raises(
                RuntimeError,
                match="Realtime evidence history cannot be downgraded",
            ),
            database.engine.begin() as connection,
        ):
            config.attributes["connection"] = connection
            command.downgrade(config, "0010_ai_tasks")
    finally:
        database.close()


def _quote(security_id: str = "600000.SH", last: str = "10") -> QuoteInput:
    return QuoteInput(
        security_id=security_id,
        last=Decimal(last),
        order_book_status="disabled",
        order_book_unavailable_reason="order_book_not_authorized",
    )


def _quote_batch(
    batch_id: str,
    *,
    minute: int = 0,
    last: str = "10",
    source_id: str = "licensed-feed",
) -> QuoteBatchInput:
    source_event_at = T0 + timedelta(minutes=minute)
    return QuoteBatchInput(
        batch_id=batch_id,
        source_id=source_id,
        source_version="v1",
        source_event_at=source_event_at,
        received_at=source_event_at + timedelta(seconds=1),
        available_at=source_event_at + timedelta(seconds=2),
        permission=permission(),
        quotes=(_quote(last=last),),
        source_sequence=minute,
    )


def _minute_batch(
    batch_id: str,
    bars: tuple[MinuteBarInput, ...],
    *,
    source_id: str = "licensed-feed",
) -> MinuteBatchInput:
    latest_available = max(bar.available_at for bar in bars)
    return MinuteBatchInput(
        batch_id=batch_id,
        source_id=source_id,
        source_version="v1",
        source_event_at=min(bar.event_at for bar in bars),
        received_at=latest_available,
        available_at=latest_available,
        permission=permission(),
        session_start=T0,
        session_end=T0 + timedelta(minutes=4),
        bars=bars,
    )


def _flow_batch(
    batch_id: str,
    *,
    minute: int = 0,
    sector_id: str = "sector-1",
    source_id: str = "licensed-feed",
    net_inflow: Decimal | None = Decimal("12.5"),
    reason: str | None = None,
) -> SectorFlowBatchInput:
    source_event_at = T0 + timedelta(minutes=minute)
    return SectorFlowBatchInput(
        batch_id=batch_id,
        source_id=source_id,
        source_version="v1",
        source_event_at=source_event_at,
        received_at=source_event_at + timedelta(seconds=1),
        available_at=source_event_at + timedelta(seconds=2),
        permission=permission(),
        session_start=T0,
        session_end=T0 + timedelta(minutes=minute + 4),
        points=(
            SectorFlowPoint(
                sector_id=sector_id,
                event_at=source_event_at,
                net_inflow=net_inflow,
                unit="CNY",
                unavailable_reason=reason,
            ),
        ),
    )


@pytest.fixture
def realtime_repository(database):
    from factories import grant

    from dragon_radar.realtime.repository import RealtimeRepository
    from dragon_radar.storage.repository import Repository

    def clock():
        return T0 + timedelta(hours=1)

    Repository(database, clock=clock).register_source(grant(source_id="licensed-feed"))
    return RealtimeRepository(database, clock=clock)


def test_ingestion_revalidates_persisted_child_identifiers(realtime_repository):
    malformed_quote = _quote().model_copy(update={"security_id": "é"})
    malformed_batch = _quote_batch("malformed-child").model_copy(
        update={"quotes": (malformed_quote,)}
    )

    with pytest.raises(ValidationError):
        realtime_repository.ingest_quote_batch(
            malformed_batch,
            key="malformed-child",
        )


def test_task21r_duplicate_import_records_attempt_and_preserves_accepted_result(
    realtime_repository,
):
    from sqlalchemy import func, select

    from dragon_radar.realtime.tables import (
        realtime_import_attempts,
        realtime_quote_batches,
        realtime_quotes,
    )

    batch = _quote_batch("quote-duplicate", last="10.00")
    first = realtime_repository.ingest_quote_batch(batch, key="quote-key-1")
    replay = realtime_repository.ingest_quote_batch(batch, key="quote-key-2")

    assert first.status == "accepted"
    assert replay.status == "duplicate"
    assert replay.import_id != first.import_id
    assert replay.batch_id == first.batch_id
    assert replay.payload_hash == first.payload_hash
    with realtime_repository.database.engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(realtime_quote_batches))
            == 1
        )
        assert connection.scalar(select(func.count()).select_from(realtime_quotes)) == 1
        assert (
            connection.scalar(
                select(func.count()).select_from(realtime_import_attempts)
            )
            == 2
        )
        attempts = {
            row.status: (str(row.id), row.idempotency_key)
            for row in connection.execute(
                select(
                    realtime_import_attempts.c.id,
                    realtime_import_attempts.c.idempotency_key,
                    realtime_import_attempts.c.status,
                )
            )
        }
    assert attempts == {
        "accepted": (first.import_id, "quote-key-1"),
        "duplicate": (replay.import_id, "quote-key-2"),
    }


@pytest.mark.parametrize("owner_status", ("accepted", "incomplete"))
def test_task21r_key_owner_is_checked_before_existing_batch(
    realtime_repository,
    owner_status,
):
    from sqlalchemy import func, select

    from dragon_radar.realtime.tables import (
        realtime_import_attempts,
        realtime_quote_batches,
    )
    from dragon_radar.storage.repository import Conflict

    if owner_status == "accepted":
        batch_a = _quote_batch("owned-key-batch-a", last="10")
        batch_b = _quote_batch("owned-key-batch-b", minute=1, last="11")
        ingest = realtime_repository.ingest_quote_batch
    else:
        batch_a = _minute_batch(
            "owned-key-batch-a",
            (minute_bar("100", minute=0),),
        )
        batch_b = _minute_batch(
            "owned-key-batch-b",
            (minute_bar("101", minute=1),),
        )
        ingest = realtime_repository.ingest_minute_batch

    owner = ingest(batch_a, key="owned-key")
    existing = ingest(batch_b, key="batch-b-key")
    assert owner.status == owner_status
    assert existing.status == owner_status

    with realtime_repository.database.engine.connect() as connection:
        parent_count = connection.scalar(
            select(func.count()).select_from(realtime_quote_batches)
        )
        attempt_count = connection.scalar(
            select(func.count()).select_from(realtime_import_attempts)
        )

    with pytest.raises(Conflict, match="idempotency key"):
        ingest(batch_b, key="owned-key")

    with realtime_repository.database.engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(realtime_quote_batches))
            == parent_count
        )
        assert (
            connection.scalar(
                select(func.count()).select_from(realtime_import_attempts)
            )
            == attempt_count + 1
        )
        rejected = (
            connection.execute(
                select(
                    realtime_import_attempts.c.reason,
                    realtime_import_attempts.c.details,
                ).where(realtime_import_attempts.c.status == "rejected")
            )
            .mappings()
            .one()
        )
    assert rejected == {
        "reason": "idempotency_key_conflict",
        "details": {"kind": "quote" if owner_status == "accepted" else "minute"},
    }

    replay = ingest(batch_a, key="owned-key")
    assert replay.status == "duplicate"
    assert replay.batch_id == owner.batch_id
    assert replay.payload_hash == owner.payload_hash


def test_task21r_same_key_and_hash_replays_incomplete_parent(
    realtime_repository,
):
    from sqlalchemy import func, select

    from dragon_radar.realtime.tables import (
        realtime_import_attempts,
        realtime_quote_batches,
    )

    batch = _minute_batch(
        "incomplete-key-replay",
        (minute_bar("100", minute=0),),
    )
    first = realtime_repository.ingest_minute_batch(
        batch,
        key="incomplete-key",
    )
    replay = realtime_repository.ingest_minute_batch(
        batch,
        key="incomplete-key",
    )

    assert first.status == "incomplete"
    assert replay.status == "duplicate"
    assert replay.batch_id == first.batch_id
    assert replay.payload_hash == first.payload_hash
    with realtime_repository.database.engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(realtime_quote_batches))
            == 1
        )
        attempts = connection.execute(
            select(
                realtime_import_attempts.c.id,
                realtime_import_attempts.c.status,
                realtime_import_attempts.c.details,
            )
        ).mappings()
        attempts = list(attempts)
    assert {attempt["status"] for attempt in attempts} == {"incomplete", "duplicate"}
    duplicate = next(
        attempt for attempt in attempts if attempt["status"] == "duplicate"
    )
    assert duplicate["details"]["original_import_id"] == first.import_id


def test_task21r_incomplete_parent_owns_key_for_conflicting_hash(
    realtime_repository,
):
    from sqlalchemy import func, select

    from dragon_radar.realtime.tables import (
        realtime_import_attempts,
        realtime_quote_batches,
    )
    from dragon_radar.storage.repository import Conflict

    first = realtime_repository.ingest_minute_batch(
        _minute_batch(
            "incomplete-key-owner",
            (minute_bar("100", minute=0),),
        ),
        key="incomplete-owned-key",
    )

    with pytest.raises(Conflict, match="idempotency key"):
        realtime_repository.ingest_minute_batch(
            _minute_batch(
                "incomplete-key-conflict",
                (minute_bar("101", minute=1),),
            ),
            key="incomplete-owned-key",
        )

    assert first.status == "incomplete"
    with realtime_repository.database.engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(realtime_quote_batches))
            == 1
        )
        attempts = set(connection.scalars(select(realtime_import_attempts.c.status)))
    assert attempts == {"incomplete", "rejected"}


def test_task21r_concurrent_incomplete_imports_bind_one_key_and_parent(
    realtime_repository,
):
    from sqlalchemy import func, select

    from dragon_radar.realtime.tables import (
        realtime_import_attempts,
        realtime_quote_batches,
    )
    from dragon_radar.storage.repository import Conflict

    batches = (
        _minute_batch(
            "concurrent-incomplete-first",
            (minute_bar("100", minute=0),),
        ),
        _minute_batch(
            "concurrent-incomplete-second",
            (minute_bar("101", minute=1),),
        ),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                realtime_repository.ingest_minute_batch,
                batch,
                key="concurrent-incomplete-key",
            )
            for batch in batches
        ]
    outcomes = []
    for future in futures:
        try:
            outcomes.append(future.result())
        except Conflict as error:
            outcomes.append(error)

    assert sum(isinstance(outcome, Conflict) for outcome in outcomes) == 1
    result = next(outcome for outcome in outcomes if not isinstance(outcome, Conflict))
    assert result.status == "incomplete"
    with realtime_repository.database.engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(realtime_quote_batches))
            == 1
        )
        attempts = (
            connection.execute(
                select(
                    realtime_import_attempts.c.status,
                    realtime_import_attempts.c.reason,
                    realtime_import_attempts.c.details,
                )
            )
            .mappings()
            .all()
        )
    assert {(attempt["status"], attempt["reason"]) for attempt in attempts} == {
        ("incomplete", "stream_gap"),
        ("rejected", "idempotency_key_conflict"),
    }
    assert next(
        attempt["details"] for attempt in attempts if attempt["status"] == "rejected"
    ) == {"kind": "minute"}


def test_task21r_accepted_and_incomplete_attempt_statuses_are_immutable(
    realtime_repository,
):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    realtime_repository.ingest_quote_batch(
        _quote_batch("immutable-accepted"),
        key="immutable-accepted",
    )
    realtime_repository.ingest_minute_batch(
        _minute_batch(
            "immutable-incomplete",
            (minute_bar("100", minute=0),),
        ),
        key="immutable-incomplete",
    )

    for current, replacement in (
        ("accepted", "incomplete"),
        ("incomplete", "accepted"),
    ):
        with (
            pytest.raises(DBAPIError),
            realtime_repository.database.engine.begin() as connection,
        ):
            connection.execute(
                text(
                    """
                    UPDATE realtime_import_attempts
                    SET status = :replacement,
                        reason = CASE
                            WHEN :replacement = 'incomplete' THEN 'stream_gap'
                            ELSE NULL
                        END
                    WHERE status = :current
                    """
                ),
                {"current": current, "replacement": replacement},
            )


def test_task21r_cross_source_commit_order_serializes_ingest_watermarks(
    realtime_repository,
):
    from factories import grant
    from sqlalchemy import event, select

    from dragon_radar.realtime.tables import realtime_quote_batches
    from dragon_radar.storage.repository import Repository

    Repository(
        realtime_repository.database,
        clock=realtime_repository.clock,
    ).register_source(grant(source_id="backup-feed"))
    first_inserted = Event()
    second_at_lock = Event()
    release_first = Event()

    def observe_second_lock(
        _connection, _cursor, statement, parameters, _context, _executemany
    ):
        if (
            current_thread().name.startswith("cursor-order-second")
            and "pg_advisory_xact_lock" in statement
            and parameters.get("key") == "dragon-radar:realtime:cursor-order:v1"
        ):
            second_at_lock.set()

    def pause_first_parent_insert(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        if current_thread().name.startswith(
            "cursor-order-first"
        ) and statement.startswith("INSERT INTO realtime_quote_batches"):
            first_inserted.set()
            assert release_first.wait(5)

    event.listen(
        realtime_repository.database.engine,
        "after_cursor_execute",
        pause_first_parent_insert,
    )
    event.listen(
        realtime_repository.database.engine,
        "before_cursor_execute",
        observe_second_lock,
    )
    try:
        with (
            ThreadPoolExecutor(
                max_workers=2,
                thread_name_prefix="cursor-order-first",
            ) as first_executor,
            ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="cursor-order-second",
            ) as second_executor,
        ):
            first = first_executor.submit(
                realtime_repository.ingest_quote_batch,
                _quote_batch("commit-order-first"),
                key="commit-order-first",
            )
            assert first_inserted.wait(5)
            second = second_executor.submit(
                realtime_repository.ingest_quote_batch,
                _quote_batch(
                    "commit-order-second",
                    source_id="backup-feed",
                ),
                key="commit-order-second",
            )
            assert second_at_lock.wait(5)
            assert not second.done()
            release_first.set()
            assert first.result(timeout=5).status == "accepted"
            assert second.result(timeout=5).status == "accepted"
    finally:
        release_first.set()
        event.remove(
            realtime_repository.database.engine,
            "after_cursor_execute",
            pause_first_parent_insert,
        )
        event.remove(
            realtime_repository.database.engine,
            "before_cursor_execute",
            observe_second_lock,
        )

    with realtime_repository.database.engine.connect() as connection:
        orders = dict(
            connection.execute(
                select(
                    realtime_quote_batches.c.quote_batch_id,
                    realtime_quote_batches.c.ingest_order,
                ).where(
                    realtime_quote_batches.c.quote_batch_id.in_(
                        ("commit-order-first", "commit-order-second")
                    )
                )
            ).all()
        )
    assert orders["commit-order-first"] < orders["commit-order-second"]


def test_task21r_cross_source_rollback_releases_watermark_with_gap(
    realtime_repository,
):
    from factories import grant
    from sqlalchemy import event, select

    from dragon_radar.realtime.tables import realtime_quote_batches
    from dragon_radar.storage.repository import Repository

    Repository(
        realtime_repository.database,
        clock=realtime_repository.clock,
    ).register_source(grant(source_id="backup-feed"))
    first_inserted = Event()
    second_at_lock = Event()
    release_first = Event()

    def observe_second_lock(
        _connection, _cursor, statement, parameters, _context, _executemany
    ):
        if (
            current_thread().name.startswith("cursor-rollback-second")
            and "pg_advisory_xact_lock" in statement
            and parameters.get("key") == "dragon-radar:realtime:cursor-order:v1"
        ):
            second_at_lock.set()

    def roll_back_first_parent_insert(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        if current_thread().name.startswith(
            "cursor-rollback-first"
        ) and statement.startswith("INSERT INTO realtime_quote_batches"):
            first_inserted.set()
            assert release_first.wait(5)
            raise RuntimeError("force first realtime transaction rollback")

    event.listen(
        realtime_repository.database.engine,
        "after_cursor_execute",
        roll_back_first_parent_insert,
    )
    event.listen(
        realtime_repository.database.engine,
        "before_cursor_execute",
        observe_second_lock,
    )
    try:
        with (
            ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="cursor-rollback-first",
            ) as first_executor,
            ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="cursor-rollback-second",
            ) as second_executor,
        ):
            first = first_executor.submit(
                realtime_repository.ingest_quote_batch,
                _quote_batch("rollback-order-first"),
                key="rollback-order-first",
            )
            assert first_inserted.wait(5)
            second = second_executor.submit(
                realtime_repository.ingest_quote_batch,
                _quote_batch(
                    "rollback-order-second",
                    source_id="backup-feed",
                ),
                key="rollback-order-second",
            )
            assert second_at_lock.wait(5)
            assert not second.done()
            release_first.set()
            with pytest.raises(
                RuntimeError,
                match="force first realtime transaction rollback",
            ):
                first.result(timeout=5)
            assert second.result(timeout=5).status == "accepted"
    finally:
        release_first.set()
        event.remove(
            realtime_repository.database.engine,
            "after_cursor_execute",
            roll_back_first_parent_insert,
        )
        event.remove(
            realtime_repository.database.engine,
            "before_cursor_execute",
            observe_second_lock,
        )

    with realtime_repository.database.engine.connect() as connection:
        rows = connection.execute(
            select(
                realtime_quote_batches.c.quote_batch_id,
                realtime_quote_batches.c.ingest_order,
            ).where(
                realtime_quote_batches.c.quote_batch_id.in_(
                    ("rollback-order-first", "rollback-order-second")
                )
            )
        ).all()
    assert rows == [("rollback-order-second", 2)]


def test_task21r_anomalies_require_visible_parent_batch(realtime_repository):
    batch = _minute_batch(
        "future-parent-anomaly",
        (minute_bar("100", minute=0), minute_bar("103", minute=1)),
    ).model_copy(
        update={
            "received_at": T0 + timedelta(minutes=30),
            "available_at": T0 + timedelta(minutes=30),
        }
    )
    realtime_repository.ingest_minute_batch(
        MinuteBatchInput.model_validate(batch.model_dump()),
        key="future-parent-anomaly",
        rules=(return_rule(window=2),),
    )

    before_parent = realtime_repository.list_anomalies(
        date=T0.date(),
        as_of=T0 + timedelta(minutes=10),
        limit=100,
    )
    at_parent = realtime_repository.list_anomalies(
        date=T0.date(),
        as_of=T0 + timedelta(minutes=30),
        limit=100,
    )

    assert before_parent.items == ()
    assert before_parent.completeness.received_count == 0
    assert len(at_parent.items) == 1


def test_anomalies_without_visible_parent_batch_are_unavailable(realtime_repository):
    result = realtime_repository.list_anomalies(
        date=T0.date(),
        as_of=T0 + timedelta(minutes=10),
        limit=100,
    )

    assert result.items == ()
    assert result.next_cursor is None
    assert result.completeness.status == "unavailable"
    assert result.completeness.expected_count is None
    assert result.completeness.received_count == 0
    assert result.completeness.reason == "no_data"


def test_anomalies_with_visible_batch_and_zero_events_are_complete(
    realtime_repository,
):
    realtime_repository.ingest_minute_batch(
        _minute_batch(
            "zero-anomaly-events",
            (minute_bar("100", minute=0), minute_bar("101", minute=1)),
        ),
        key="zero-anomaly-events",
        rules=(return_rule(window=2),),
    )

    result = realtime_repository.list_anomalies(
        date=T0.date(),
        as_of=T0 + timedelta(hours=1),
        limit=100,
    )

    assert result.items == ()
    assert result.next_cursor is None
    assert result.completeness.status == "complete"
    assert result.completeness.expected_count == 0
    assert result.completeness.received_count == 0
    assert result.completeness.reason is None


def test_task21r_cursor_uses_database_ingest_order_watermark(database):
    from factories import grant
    from sqlalchemy import insert

    from dragon_radar.realtime.repository import RealtimeRepository
    from dragon_radar.realtime.tables import (
        realtime_anomaly_events,
        realtime_quote_batches,
    )
    from dragon_radar.storage.repository import Repository

    operation_now = T0 + timedelta(hours=1)
    Repository(database, clock=lambda: operation_now).register_source(
        grant(source_id="licensed-feed")
    )
    repository = RealtimeRepository(database, clock=lambda: operation_now)
    rule = return_rule(window=2)
    for revision in range(1, 4):
        repository.ingest_minute_batch(
            _minute_batch(
                f"watermark-batch-{revision}",
                (
                    minute_bar("100", minute=0, revision=revision),
                    minute_bar(str(102 + revision), minute=1, revision=revision),
                ),
            ),
            key=f"watermark-key-{revision}",
            rules=(rule.model_copy(update={"rule_version": f"return-v{revision}"}),),
        )

    expected = repository.list_anomalies(
        date=T0.date(),
        as_of=operation_now,
        limit=100,
    )
    first_page = repository.list_anomalies(
        date=T0.date(),
        as_of=operation_now,
        limit=1,
    )
    assert first_page.next_cursor is not None

    with database.engine.begin() as connection:
        connection.execute(
            insert(realtime_quote_batches).values(
                id=uuid4(),
                quote_batch_id="late-commit-old-client-time",
                source_id="licensed-feed",
                source_version="v1",
                source_event_at=T0,
                received_at=T0,
                available_at=T0,
                source_sequence=None,
                permission_snapshot=permission().model_dump(mode="json"),
                payload_hash="f" * 64,
                imported_at=T0,
            )
        )
        connection.execute(
            insert(realtime_anomaly_events).values(
                event_id="0" * 64,
                source_id="licensed-feed",
                batch_id="late-commit-old-client-time",
                security_id="600000.SH",
                rule_version="return-late",
                metric="window_return",
                window=2,
                value=Decimal("0.03"),
                threshold=Decimal("0.02"),
                event_at=T0,
                available_at=T0,
                quality="complete",
                cooldown_bucket=T0,
            )
        )

    seen = [first_page.items[0].event_id]
    cursor = first_page.next_cursor
    while cursor is not None:
        page = repository.list_anomalies(
            date=T0.date(),
            as_of=operation_now,
            limit=1,
            cursor=cursor,
        )
        seen.extend(item.event_id for item in page.items)
        cursor = page.next_cursor

    assert seen == [item.event_id for item in expected.items]


def test_task21r_cursor_chain_excludes_batches_committed_after_first_page(
    realtime_repository,
):
    from factories import grant
    from sqlalchemy import event, select

    from dragon_radar.realtime.tables import realtime_anomaly_events
    from dragon_radar.storage.repository import Repository

    storage = Repository(
        realtime_repository.database,
        clock=realtime_repository.clock,
    )
    storage.register_source(grant(source_id="backup-feed"))
    storage.register_source(grant(source_id="delayed-feed"))
    rule = return_rule(window=2)
    for revision in range(1, 4):
        realtime_repository.ingest_minute_batch(
            _minute_batch(
                f"cursor-seed-{revision}",
                (
                    minute_bar("100", minute=2, revision=revision),
                    minute_bar(str(102 + revision), minute=3, revision=revision),
                ),
            ),
            key=f"cursor-seed-{revision}",
            rules=(rule.model_copy(update={"rule_version": f"seed-v{revision}"}),),
        )

    first_inserted = Event()
    second_at_lock = Event()
    release_first = Event()

    def observe_second_lock(
        _connection, _cursor, statement, parameters, _context, _executemany
    ):
        if (
            current_thread().name.startswith("cursor-chain-second")
            and "pg_advisory_xact_lock" in statement
            and parameters.get("key") == "dragon-radar:realtime:cursor-order:v1"
        ):
            second_at_lock.set()

    def pause_first_parent_insert(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        if current_thread().name.startswith(
            "cursor-chain-first"
        ) and statement.startswith("INSERT INTO realtime_quote_batches"):
            first_inserted.set()
            assert release_first.wait(5)

    event.listen(
        realtime_repository.database.engine,
        "after_cursor_execute",
        pause_first_parent_insert,
    )
    event.listen(
        realtime_repository.database.engine,
        "before_cursor_execute",
        observe_second_lock,
    )
    try:
        with (
            ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="cursor-chain-first",
            ) as first_executor,
            ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="cursor-chain-second",
            ) as second_executor,
        ):
            first = first_executor.submit(
                realtime_repository.ingest_minute_batch,
                _minute_batch(
                    "cursor-late-first",
                    (
                        minute_bar("100", minute=0),
                        minute_bar("110", minute=1),
                    ),
                    source_id="backup-feed",
                ),
                key="cursor-late-first",
                rules=(rule.model_copy(update={"rule_version": "late-first"}),),
            )
            assert first_inserted.wait(5)
            second = second_executor.submit(
                realtime_repository.ingest_minute_batch,
                _minute_batch(
                    "cursor-second",
                    (
                        minute_bar("100", minute=2),
                        minute_bar("109", minute=3),
                    ),
                    source_id="delayed-feed",
                ),
                key="cursor-second",
                rules=(rule.model_copy(update={"rule_version": "second"}),),
            )
            assert second_at_lock.wait(5)
            assert not second.done()

            first_page = realtime_repository.list_anomalies(
                date=T0.date(),
                as_of=T0 + timedelta(hours=1),
                limit=1,
            )
            assert first_page.next_cursor is not None
            release_first.set()
            assert first.result(timeout=5).status == "incomplete"
            assert second.result(timeout=5).status == "incomplete"
    finally:
        release_first.set()
        event.remove(
            realtime_repository.database.engine,
            "after_cursor_execute",
            pause_first_parent_insert,
        )
        event.remove(
            realtime_repository.database.engine,
            "before_cursor_execute",
            observe_second_lock,
        )

    with realtime_repository.database.engine.connect() as connection:
        late_event_id = connection.scalar(
            select(realtime_anomaly_events.c.event_id).where(
                realtime_anomaly_events.c.source_id == "backup-feed",
                realtime_anomaly_events.c.batch_id == "cursor-late-first",
            )
        )
    assert late_event_id is not None

    seen = [item.event_id for item in first_page.items]
    cursor = first_page.next_cursor
    while cursor is not None:
        page = realtime_repository.list_anomalies(
            date=T0.date(),
            as_of=T0 + timedelta(hours=1),
            limit=1,
            cursor=cursor,
        )
        seen.extend(item.event_id for item in page.items)
        cursor = page.next_cursor
    assert late_event_id not in seen


def test_task21r_exact_batch_selection_requires_complete_source_identity(
    realtime_repository,
):
    from factories import grant

    from dragon_radar.storage.repository import Repository

    Repository(
        realtime_repository.database,
        clock=realtime_repository.clock,
    ).register_source(grant(source_id="backup-feed"))
    realtime_repository.ingest_quote_batch(
        _quote_batch("shared-batch", last="10", source_id="licensed-feed"),
        key="shared-primary",
    )
    realtime_repository.ingest_quote_batch(
        _quote_batch("shared-batch", last="20", source_id="backup-feed"),
        key="shared-backup",
    )

    with pytest.raises(ValueError, match="source_id"):
        realtime_repository.list_quotes(
            security_ids=("600000.SH",),
            as_of=T0 + timedelta(hours=1),
            batch_id="shared-batch",
        )

    selected = realtime_repository.list_quotes(
        security_ids=("600000.SH",),
        as_of=T0 + timedelta(hours=1),
        batch_id="shared-batch",
        source_id="backup-feed",
    )
    assert selected.items[0].source_id == "backup-feed"
    assert selected.items[0].quote.last == Decimal("20")


def test_task21r_duplicate_rule_identity_rejects_before_any_write(
    realtime_repository,
):
    from sqlalchemy import func, select

    from dragon_radar.realtime.tables import (
        realtime_anomaly_events,
        realtime_import_attempts,
        realtime_minute_bars,
        realtime_quote_batches,
    )

    rule = return_rule(window=2)
    with pytest.raises(ValueError, match="duplicate.*rule"):
        realtime_repository.ingest_minute_batch(
            _minute_batch(
                "duplicate-rule",
                (minute_bar("100", minute=0), minute_bar("103", minute=1)),
            ),
            key="duplicate-rule",
            rules=(rule, rule),
        )

    with realtime_repository.database.engine.connect() as connection:
        counts = tuple(
            connection.scalar(select(func.count()).select_from(table))
            for table in (
                realtime_quote_batches,
                realtime_minute_bars,
                realtime_anomaly_events,
                realtime_import_attempts,
            )
        )
    assert counts == (0, 0, 0, 1)
    with realtime_repository.database.engine.connect() as connection:
        rejected = (
            connection.execute(
                select(
                    realtime_import_attempts.c.reason,
                    realtime_import_attempts.c.details,
                )
            )
            .mappings()
            .one()
        )
    assert rejected == {
        "reason": "duplicate_rule_identity",
        "details": {"kind": "minute"},
    }


def test_rt05_same_identity_different_payload_conflicts_without_partial_write(
    realtime_repository,
):
    from sqlalchemy import func, select

    from dragon_radar.realtime.tables import (
        realtime_import_attempts,
        realtime_quote_batches,
        realtime_quotes,
    )
    from dragon_radar.storage.repository import Conflict

    realtime_repository.ingest_quote_batch(
        _quote_batch("quote-conflict", last="10"),
        key="quote-conflict-1",
    )

    with pytest.raises(Conflict, match="payload"):
        realtime_repository.ingest_quote_batch(
            _quote_batch("quote-conflict", last="11"),
            key="quote-conflict-2",
        )

    with realtime_repository.database.engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(realtime_quote_batches))
            == 1
        )
        assert connection.scalar(select(func.count()).select_from(realtime_quotes)) == 1
        assert (
            connection.scalar(
                select(func.count()).select_from(realtime_import_attempts)
            )
            == 2
        )
        assert set(connection.scalars(select(realtime_import_attempts.c.status))) == {
            "accepted",
            "rejected",
        }
        rejected = (
            connection.execute(
                select(
                    realtime_import_attempts.c.reason,
                    realtime_import_attempts.c.details,
                ).where(realtime_import_attempts.c.status == "rejected")
            )
            .mappings()
            .one()
        )
    assert rejected == {
        "reason": "batch_identity_conflict",
        "details": {"kind": "quote"},
    }


def test_duplicate_quote_identity_is_rejected_without_partial_data(
    realtime_repository,
):
    from sqlalchemy import func, select

    from dragon_radar.realtime.models import QuoteBatchInput
    from dragon_radar.realtime.tables import (
        realtime_import_attempts,
        realtime_quote_batches,
        realtime_quotes,
    )

    batch = _quote_batch("quote-child-conflict").model_copy(
        update={"quotes": (_quote(), _quote())}
    )

    with pytest.raises(ValueError, match="duplicate.*identity"):
        realtime_repository.ingest_quote_batch(
            QuoteBatchInput.model_validate(batch.model_dump()),
            key="quote-child-conflict",
        )

    with realtime_repository.database.engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(realtime_quote_batches))
            == 0
        )
        assert connection.scalar(select(func.count()).select_from(realtime_quotes)) == 0
        rejected = (
            connection.execute(
                select(
                    realtime_import_attempts.c.reason,
                    realtime_import_attempts.c.details,
                )
            )
            .mappings()
            .one()
        )
    assert rejected == {
        "reason": "duplicate_quote_identity",
        "details": {"kind": "quote"},
    }


@pytest.mark.parametrize("kind", ("minute", "sector_flow"))
def test_duplicate_relationship_identity_is_rejected_before_data_write(
    realtime_repository,
    kind,
):
    from sqlalchemy import func, select

    from dragon_radar.realtime.tables import (
        realtime_import_attempts,
        realtime_minute_bars,
        realtime_quote_batches,
        realtime_sector_flow_points,
    )

    if kind == "minute":
        item = minute_bar()
        batch = _minute_batch("duplicate-minute", (item, item))
        ingest = realtime_repository.ingest_minute_batch
        child_table = realtime_minute_bars
    else:
        item = _flow_batch("duplicate-flow").points[0]
        batch = _flow_batch("duplicate-flow").model_copy(
            update={"points": (item, item)}
        )
        ingest = realtime_repository.ingest_sector_flow_batch
        child_table = realtime_sector_flow_points

    with pytest.raises(ValueError, match="duplicate.*identity"):
        ingest(batch, key=f"duplicate-{kind}")

    with realtime_repository.database.engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(realtime_quote_batches))
            == 0
        )
        assert connection.scalar(select(func.count()).select_from(child_table)) == 0
        rejected = (
            connection.execute(
                select(
                    realtime_import_attempts.c.reason,
                    realtime_import_attempts.c.details,
                )
            )
            .mappings()
            .one()
        )
    assert rejected == {
        "reason": f"duplicate_{kind}_identity",
        "details": {"kind": kind},
    }


def test_child_integrity_error_rolls_back_data_then_records_redacted_rejection(
    realtime_repository,
):
    from sqlalchemy import func, select

    from dragon_radar.realtime.tables import (
        realtime_import_attempts,
        realtime_minute_bars,
        realtime_quote_batches,
    )
    from dragon_radar.storage.repository import Conflict

    first = _minute_batch("integrity-first", (minute_bar(),))
    second = _minute_batch("integrity-second", (minute_bar(),))
    realtime_repository.ingest_minute_batch(first, key="integrity-first")

    with pytest.raises(Conflict, match="minute batch or revision"):
        realtime_repository.ingest_minute_batch(second, key="integrity-second")

    with realtime_repository.database.engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(realtime_quote_batches))
            == 1
        )
        minute_count = connection.scalar(
            select(func.count()).select_from(realtime_minute_bars)
        )
        assert minute_count == 1
        rejected = (
            connection.execute(
                select(
                    realtime_import_attempts.c.reason,
                    realtime_import_attempts.c.details,
                ).where(realtime_import_attempts.c.status == "rejected")
            )
            .mappings()
            .one()
        )
    assert rejected == {
        "reason": "integrity_error",
        "details": {"error_type": "IntegrityError", "kind": "minute"},
    }
    assert "integrity-first" not in str(rejected)


def test_rejected_attempt_failure_preserves_primary_error_and_safe_cleanup_context(
    realtime_repository,
):
    from sqlalchemy import event

    from dragon_radar.storage.repository import Conflict

    realtime_repository.ingest_quote_batch(
        _quote_batch("cleanup-context", last="10"),
        key="cleanup-context-first",
    )

    def fail_rejected_attempt(
        _connection, _cursor, statement, parameters, _context, _executemany
    ):
        if (
            statement.startswith("INSERT INTO realtime_import_attempts")
            and parameters.get("status") == "rejected"
        ):
            raise RuntimeError("database-password-secret")

    event.listen(
        realtime_repository.database.engine,
        "before_cursor_execute",
        fail_rejected_attempt,
    )
    try:
        with pytest.raises(Conflict, match="batch identity") as captured:
            realtime_repository.ingest_quote_batch(
                _quote_batch("cleanup-context", last="11"),
                key="cleanup-context-second",
            )
    finally:
        event.remove(
            realtime_repository.database.engine,
            "before_cursor_execute",
            fail_rejected_attempt,
        )

    assert captured.value.__notes__ == [
        "Rejected attempt persistence failed (RuntimeError)"
    ]
    assert "database-password-secret" not in str(captured.value)
    assert "database-password-secret" not in repr(captured.value.__notes__)


def test_rt06_out_of_order_batches_remain_exactly_queryable(realtime_repository):
    later = _quote_batch("quote-later", minute=2, last="12")
    earlier = _quote_batch("quote-earlier", minute=1, last="11")
    realtime_repository.ingest_quote_batch(later, key="quote-later")
    realtime_repository.ingest_quote_batch(earlier, key="quote-earlier")

    exact = realtime_repository.list_quotes(
        security_ids=("600000.SH",),
        as_of=T0 + timedelta(minutes=3),
        batch_id="quote-earlier",
        source_id="licensed-feed",
    )
    historical = realtime_repository.list_quotes(
        security_ids=("600000.SH",),
        as_of=T0 + timedelta(minutes=1, seconds=2),
    )
    current = realtime_repository.list_quotes(
        security_ids=("600000.SH",),
        as_of=T0 + timedelta(minutes=3),
    )

    assert exact.batch_id == historical.batch_id == "quote-earlier"
    assert exact.items[0].quote.last == historical.items[0].quote.last == 11
    assert current.batch_id == "quote-later"
    assert current.items[0].quote.last == 12


def test_latest_quote_selection_ignores_newer_non_quote_batches(
    realtime_repository,
):
    realtime_repository.ingest_quote_batch(
        _quote_batch("quote-before-minute", minute=0, last="10"),
        key="quote-before-minute",
    )
    realtime_repository.ingest_minute_batch(
        _minute_batch(
            "newer-minute-only",
            (minute_bar("11", minute=1),),
        ),
        key="newer-minute-only",
    )

    result = realtime_repository.list_quotes(
        security_ids=("600000.SH",),
        as_of=T0 + timedelta(hours=1),
    )

    assert result.batch_id == "quote-before-minute"
    assert result.items[0].quote.last == Decimal("10")


def test_latest_quote_selection_skips_newer_currently_revoked_source(
    realtime_repository,
):
    from factories import grant

    from dragon_radar.storage.repository import Forbidden, Repository

    sources = Repository(
        realtime_repository.database,
        clock=realtime_repository.clock,
    )
    sources.register_source(grant(source_id="backup-feed"))
    realtime_repository.ingest_quote_batch(
        _quote_batch(
            "older-authorized",
            minute=1,
            last="11",
            source_id="backup-feed",
        ),
        key="older-authorized",
    )
    realtime_repository.ingest_quote_batch(
        _quote_batch("newer-revoked", minute=2, last="12"),
        key="newer-revoked",
    )
    sources.revoke_source("licensed-feed")

    latest = realtime_repository.list_quotes(
        security_ids=("600000.SH",),
        as_of=T0 + timedelta(hours=1),
    )

    assert latest.batch_id == "older-authorized"
    assert latest.items[0].source_id == "backup-feed"
    assert latest.items[0].quote.last == Decimal("11")
    with pytest.raises(Forbidden):
        realtime_repository.list_quotes(
            security_ids=("600000.SH",),
            as_of=T0 + timedelta(hours=1),
            source_id="licensed-feed",
            batch_id="newer-revoked",
        )
    with pytest.raises(Forbidden):
        realtime_repository.list_quotes(
            security_ids=("600000.SH",),
            as_of=T0 + timedelta(hours=1),
            source_id="licensed-feed",
        )


def test_rt07_point_in_time_minutes_select_visible_revision_without_fall_forward(
    realtime_repository,
):
    revision_1 = minute_bar("10", minute=1, revision=1)
    revision_2 = minute_bar("11", minute=1, revision=2).model_copy(
        update={
            "received_at": T0 + timedelta(minutes=2),
            "available_at": T0 + timedelta(minutes=2, seconds=1),
        }
    )
    realtime_repository.ingest_minute_batch(
        _minute_batch("minute-revision-1", (revision_1,)),
        key="minute-revision-1",
    )
    realtime_repository.ingest_minute_batch(
        _minute_batch("minute-revision-2", (revision_2,)),
        key="minute-revision-2",
    )

    before_revision = realtime_repository.list_minutes(
        security_id="600000.SH",
        session_start=T0,
        session_end=T0 + timedelta(minutes=4),
        as_of=T0 + timedelta(minutes=1, seconds=2),
    )
    after_revision = realtime_repository.list_minutes(
        security_id="600000.SH",
        session_start=T0,
        session_end=T0 + timedelta(minutes=4),
        as_of=T0 + timedelta(minutes=3),
    )
    exact_old_batch = realtime_repository.list_minutes(
        security_id="600000.SH",
        session_start=T0,
        session_end=T0 + timedelta(minutes=4),
        as_of=T0 + timedelta(minutes=3),
        batch_id="minute-revision-1",
        source_id="licensed-feed",
    )

    assert [(item.bar.revision, item.bar.close) for item in before_revision.items] == [
        (1, Decimal("10"))
    ]
    assert [(item.bar.revision, item.bar.close) for item in after_revision.items] == [
        (2, Decimal("11"))
    ]
    assert exact_old_batch.batch_id == "minute-revision-1"
    assert exact_old_batch.items[0].bar.revision == 1


def test_minute_completeness_counts_each_authorized_source(realtime_repository):
    from factories import grant

    from dragon_radar.storage.repository import Repository

    Repository(
        realtime_repository.database,
        clock=realtime_repository.clock,
    ).register_source(grant(source_id="backup-feed"))
    bars = tuple(minute_bar(str(10 + minute), minute=minute) for minute in range(4))
    realtime_repository.ingest_minute_batch(
        _minute_batch("primary-complete", bars),
        key="primary-complete",
    )
    realtime_repository.ingest_minute_batch(
        _minute_batch("backup-complete", bars, source_id="backup-feed"),
        key="backup-complete",
    )

    result = realtime_repository.list_minutes(
        security_id="600000.SH",
        session_start=T0,
        session_end=T0 + timedelta(minutes=4),
        as_of=T0 + timedelta(hours=1),
    )

    assert len(result.items) == 8
    assert result.completeness.status == "complete"
    assert result.completeness.expected_count == 8
    assert result.completeness.received_count == 8


def test_minute_completeness_counts_unique_trading_minutes(realtime_repository):
    second_event = MinuteBarInput.model_validate(
        {
            **minute_bar("10.5").model_dump(),
            "event_at": T0 + timedelta(seconds=30),
            "received_at": T0 + timedelta(seconds=31),
            "available_at": T0 + timedelta(seconds=32),
        }
    )
    bars = (
        minute_bar("10", minute=0),
        second_event,
        minute_bar("11", minute=1),
        minute_bar("12", minute=2),
    )
    ingest = realtime_repository.ingest_minute_batch(
        _minute_batch("multiple-events-per-minute", bars),
        key="multiple-events-per-minute",
    )

    result = realtime_repository.list_minutes(
        security_id="600000.SH",
        session_start=T0,
        session_end=T0 + timedelta(minutes=4),
        as_of=T0 + timedelta(hours=1),
        batch_id="multiple-events-per-minute",
        source_id="licensed-feed",
    )

    assert ingest.status == "incomplete"
    assert ingest.reason == "stream_gap"
    assert len(result.items) == 4
    assert result.completeness.status == "incomplete"
    assert result.completeness.expected_count == 4
    assert result.completeness.received_count == 3
    assert result.completeness.reason == ingest.reason


def test_point_in_time_minutes_do_not_leak_rows_from_future_atomic_batch(
    realtime_repository,
):
    realtime_repository.ingest_minute_batch(
        _minute_batch(
            "visible-atomic-minute",
            (minute_bar("9", minute=0, revision=1),),
        ),
        key="visible-atomic-minute",
    )
    batch = _minute_batch(
        "future-atomic-minute",
        (
            minute_bar("10", minute=0, revision=2),
            minute_bar("11", minute=2, revision=2),
        ),
    )
    realtime_repository.ingest_minute_batch(batch, key="future-atomic-minute")

    before_batch_available = realtime_repository.list_minutes(
        security_id="600000.SH",
        session_start=T0,
        session_end=T0 + timedelta(minutes=4),
        as_of=T0 + timedelta(seconds=2),
    )
    at_batch_available = realtime_repository.list_minutes(
        security_id="600000.SH",
        session_start=T0,
        session_end=T0 + timedelta(minutes=4),
        as_of=batch.available_at,
    )

    assert [
        (item.bar.revision, item.bar.close) for item in before_batch_available.items
    ] == [(1, Decimal("9"))]
    assert at_batch_available.items[0].bar.event_at == T0


def test_rt08_missing_minutes_remain_absent_and_gap_is_incomplete(
    realtime_repository,
):
    batch = _minute_batch(
        "minute-gap",
        (minute_bar("10", minute=0), minute_bar("12", minute=2)),
    )
    realtime_repository.ingest_minute_batch(batch, key="minute-gap")

    result = realtime_repository.list_minutes(
        security_id="600000.SH",
        session_start=T0,
        session_end=T0 + timedelta(minutes=4),
        as_of=T0 + timedelta(hours=1),
        batch_id="minute-gap",
        source_id="licensed-feed",
    )

    assert [item.bar.event_at for item in result.items] == [
        T0,
        T0 + timedelta(minutes=2),
    ]
    assert result.completeness.status == "incomplete"
    assert result.completeness.reason == "stream_gap"
    assert result.completeness.expected_count == 4
    assert result.completeness.received_count == 2
    assert result.items[1].bar.received_at == T0 + timedelta(minutes=2, seconds=1)
    assert result.items[1].bar.available_at == T0 + timedelta(minutes=2, seconds=2)


def _trading_day_minute_batch(
    batch_id: str,
    *,
    missing_minute: int | None = None,
    include_lunch: bool = False,
) -> MinuteBatchInput:
    minute_offsets = [*range(120), *range(210, 330)]
    if missing_minute is not None:
        minute_offsets.remove(missing_minute)
    if include_lunch:
        minute_offsets.append(150)
    bars = tuple(
        minute_bar(str(100 + minute), minute=minute) for minute in minute_offsets
    )
    latest_available = max(bar.available_at for bar in bars)
    return MinuteBatchInput(
        batch_id=batch_id,
        source_id="licensed-feed",
        source_version="v1",
        source_event_at=T0,
        received_at=latest_available,
        available_at=latest_available,
        permission=permission(),
        session_start=T0,
        session_end=T0 + timedelta(minutes=330),
        bars=bars,
    )


def test_minute_session_windows_exclude_lunch_and_expect_240(
    realtime_repository,
):
    batch = _trading_day_minute_batch(
        "trading-day-complete",
        include_lunch=True,
    )
    ingest = realtime_repository.ingest_minute_batch(
        batch,
        key="trading-day-complete",
    )
    windows = (
        (T0, T0 + timedelta(minutes=120)),
        (T0 + timedelta(minutes=210), T0 + timedelta(minutes=330)),
    )

    result = realtime_repository.list_minutes(
        security_id="600000.SH",
        session_start=windows[0][0],
        session_end=windows[-1][1],
        session_windows=windows,
        as_of=T0 + timedelta(hours=6),
        batch_id="trading-day-complete",
        source_id="licensed-feed",
    )

    assert len(result.items) == 240
    assert T0 + timedelta(minutes=150) not in {
        item.bar.event_at for item in result.items
    }
    assert ingest.status == "accepted"
    assert ingest.reason is None
    assert result.completeness.status == "complete"
    assert result.completeness.expected_count == 240
    assert result.completeness.received_count == 240


def test_minute_session_windows_report_one_missing_minute_as_stream_gap(
    realtime_repository,
):
    batch = _trading_day_minute_batch(
        "trading-day-gap",
        missing_minute=329,
    )
    realtime_repository.ingest_minute_batch(batch, key="trading-day-gap")
    windows = (
        (T0, T0 + timedelta(minutes=120)),
        (T0 + timedelta(minutes=210), T0 + timedelta(minutes=330)),
    )

    result = realtime_repository.list_minutes(
        security_id="600000.SH",
        session_start=windows[0][0],
        session_end=windows[-1][1],
        session_windows=windows,
        as_of=T0 + timedelta(hours=6),
        batch_id="trading-day-gap",
        source_id="licensed-feed",
    )

    assert len(result.items) == 239
    assert result.completeness.status == "incomplete"
    assert result.completeness.reason == "stream_gap"
    assert result.completeness.expected_count == 240
    assert result.completeness.received_count == 239


def test_rt09_sector_flow_preserves_null_and_reason(realtime_repository):
    realtime_repository.ingest_sector_flow_batch(
        _flow_batch(
            "flow-unavailable",
            net_inflow=None,
            reason="field_not_authorized",
        ),
        key="flow-unavailable",
    )

    result = realtime_repository.list_sector_flow(
        sector_id="sector-1",
        session_start=T0,
        session_end=T0 + timedelta(minutes=4),
        as_of=T0 + timedelta(hours=1),
        batch_id="flow-unavailable",
        source_id="licensed-feed",
    )

    assert result.items[0].point.net_inflow is None
    assert result.items[0].point.unavailable_reason == "field_not_authorized"


def test_sector_flow_session_windows_exclude_lunch_without_minute_expectation(
    realtime_repository,
):
    points = tuple(
        SectorFlowPoint(
            sector_id="sector-1",
            event_at=T0 + timedelta(minutes=minute),
            net_inflow=Decimal(str(minute)),
            unit="CNY",
        )
        for minute in (0, 150, 210)
    )
    latest_available = T0 + timedelta(minutes=210, seconds=2)
    realtime_repository.ingest_sector_flow_batch(
        SectorFlowBatchInput(
            batch_id="flow-trading-windows",
            source_id="licensed-feed",
            source_version="v1",
            source_event_at=T0,
            received_at=latest_available,
            available_at=latest_available,
            permission=permission(),
            session_start=T0,
            session_end=T0 + timedelta(minutes=330),
            points=points,
        ),
        key="flow-trading-windows",
    )
    windows = (
        (T0, T0 + timedelta(minutes=120)),
        (T0 + timedelta(minutes=210), T0 + timedelta(minutes=330)),
    )

    result = realtime_repository.list_sector_flow(
        sector_id="sector-1",
        session_start=windows[0][0],
        session_end=windows[-1][1],
        session_windows=windows,
        as_of=T0 + timedelta(hours=6),
        batch_id="flow-trading-windows",
        source_id="licensed-feed",
    )

    assert [item.point.event_at for item in result.items] == [T0, windows[1][0]]
    assert result.completeness.status == "complete"
    assert result.completeness.expected_count == 2
    assert result.completeness.received_count == 2


def test_rt11_event_id_and_cooldown_are_bound_to_rule_input_and_security(
    realtime_repository,
):
    from sqlalchemy import func, select

    from dragon_radar.realtime.tables import realtime_anomaly_events

    bars = (minute_bar("100", minute=0), minute_bar("103", minute=1))
    batch = _minute_batch("minute-event-1", bars)
    rule = return_rule(window=2)
    first = realtime_repository.ingest_minute_batch(
        batch,
        key="minute-event-1",
        rules=(rule,),
    )
    replay = realtime_repository.ingest_minute_batch(
        batch,
        key="minute-event-replay",
        rules=(rule,),
    )
    realtime_repository.ingest_minute_batch(
        _minute_batch(
            "minute-event-2",
            (
                minute_bar("100", minute=0, revision=2),
                minute_bar("104", minute=1, revision=2),
            ),
        ),
        key="minute-event-2",
        rules=(rule.model_copy(update={"rule_version": "return-v2"}),),
    )

    events = realtime_repository.list_anomalies(
        date=T0.date(),
        as_of=T0 + timedelta(hours=1),
        limit=100,
    )
    assert first.status == "incomplete"
    assert replay.status == "duplicate"
    assert replay.payload_hash == first.payload_hash
    assert len(events.items) == 2
    assert len({event.event_id for event in events.items}) == 2
    with realtime_repository.database.engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(realtime_anomaly_events))
            == 2
        )


def test_rt11_anomaly_event_preserves_incomplete_input_quality(
    realtime_repository,
):
    realtime_repository.ingest_minute_batch(
        _minute_batch(
            "minute-incomplete-event",
            (minute_bar("100", minute=0), minute_bar("103", minute=1)),
        ),
        key="minute-incomplete-event",
        rules=(return_rule(window=2),),
    )

    events = realtime_repository.list_anomalies(
        date=T0.date(),
        as_of=T0 + timedelta(hours=1),
        limit=100,
    )

    assert events.items[0].quality == "incomplete"


def test_rt12_fixed_batch_replay_is_decimal_and_datetime_deterministic(
    realtime_repository,
):
    east = timezone(timedelta(hours=8))
    first_batch = _quote_batch("quote-canonical", last="10.00")
    equivalent = QuoteBatchInput.model_validate(
        {
            **first_batch.model_dump(),
            "source_event_at": first_batch.source_event_at.astimezone(east),
            "received_at": first_batch.received_at.astimezone(east),
            "available_at": first_batch.available_at.astimezone(east),
            "quotes": (
                first_batch.quotes[0].model_copy(update={"last": Decimal("10.0")}),
            ),
        }
    )

    first = realtime_repository.ingest_quote_batch(first_batch, key="canonical-1")
    replay = realtime_repository.ingest_quote_batch(equivalent, key="canonical-2")

    assert first.status == "accepted"
    assert replay.status == "duplicate"
    assert replay.payload_hash == first.payload_hash


def test_rt12_canonical_hash_is_independent_of_decimal_context(
    realtime_repository,
):
    batch = _quote_batch(
        "quote-decimal-context",
        last="123456789.123456789",
    )
    first = realtime_repository.ingest_quote_batch(batch, key="decimal-context-1")

    with localcontext() as context:
        context.prec = 2
        replay = realtime_repository.ingest_quote_batch(
            batch,
            key="decimal-context-2",
        )

    assert first.status == "accepted"
    assert replay.status == "duplicate"
    assert replay.payload_hash == first.payload_hash


def test_decimal_canonical_value_is_bounded_by_coefficient_not_exponent():
    from dragon_radar.realtime.repository import _canonical_value

    tracemalloc.start()
    started = perf_counter()
    try:
        canonical = _canonical_value(Decimal("1e10000000"))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert perf_counter() - started < 2
    assert peak < 1_000_000
    assert len(json.dumps(canonical)) < 100


def test_decimal_canonical_hash_is_numeric_and_context_independent():
    from dragon_radar.realtime.repository import _canonical_hash

    equivalent = {
        _canonical_hash({"value": Decimal(value)}) for value in ("1", "1.0", "1.00")
    }
    distinct = {
        _canonical_hash({"value": Decimal(value)})
        for value in ("1", "1.01", "10", "-1")
    }
    with localcontext() as context:
        context.prec = 2
        low_precision = _canonical_hash(
            {"value": Decimal("123456789.123456789e100000")}
        )
    with localcontext() as context:
        context.prec = 50
        high_precision = _canonical_hash(
            {"value": Decimal("123456789.123456789e100000")}
        )

    assert len(equivalent) == 1
    assert len(distinct) == 4
    assert low_precision == high_precision


def test_large_exponent_ingestion_hash_is_stable(realtime_repository):
    first_batch = _quote_batch("large-exponent", last="1e100000")
    equivalent = first_batch.model_copy(
        update={
            "quotes": (
                first_batch.quotes[0].model_copy(update={"last": Decimal("10e99999")}),
            )
        }
    )

    first = realtime_repository.ingest_quote_batch(
        first_batch,
        key="large-exponent-first",
    )
    replay = realtime_repository.ingest_quote_batch(
        equivalent,
        key="large-exponent-replay",
    )

    assert first.status == "accepted"
    assert replay.status == "duplicate"
    assert replay.payload_hash == first.payload_hash


def test_canonical_hash_is_independent_of_relationship_collection_order(
    realtime_repository,
):
    quote_batch = _quote_batch("ordered-quotes").model_copy(
        update={
            "quotes": (
                _quote(security_id="600000.SH", last="10"),
                _quote(security_id="000001.SZ", last="11"),
            )
        }
    )
    quote_first = realtime_repository.ingest_quote_batch(
        quote_batch,
        key="ordered-quotes-first",
    )
    quote_replay = realtime_repository.ingest_quote_batch(
        quote_batch.model_copy(update={"quotes": tuple(reversed(quote_batch.quotes))}),
        key="ordered-quotes-replay",
    )

    minute_batch = _minute_batch(
        "ordered-minutes",
        (minute_bar("100", minute=0), minute_bar("103", minute=1)),
    )
    rules = (
        return_rule(window=2),
        AnomalyRule(
            rule_version="volume-v1",
            metric="relative_volume",
            window=2,
            threshold=Decimal("1.1"),
            cooldown_seconds=300,
        ),
    )
    minute_first = realtime_repository.ingest_minute_batch(
        minute_batch,
        key="ordered-minutes-first",
        rules=rules,
    )
    minute_replay = realtime_repository.ingest_minute_batch(
        minute_batch.model_copy(update={"bars": tuple(reversed(minute_batch.bars))}),
        key="ordered-minutes-replay",
        rules=tuple(reversed(rules)),
    )

    flow_batch = _flow_batch("ordered-flow").model_copy(
        update={
            "points": (
                _flow_batch("ignored", sector_id="sector-1").points[0],
                _flow_batch("ignored", sector_id="sector-2").points[0],
            )
        }
    )
    flow_first = realtime_repository.ingest_sector_flow_batch(
        flow_batch,
        key="ordered-flow-first",
    )
    flow_replay = realtime_repository.ingest_sector_flow_batch(
        flow_batch.model_copy(update={"points": tuple(reversed(flow_batch.points))}),
        key="ordered-flow-replay",
    )

    for first, replay in (
        (quote_first, quote_replay),
        (minute_first, minute_replay),
        (flow_first, flow_replay),
    ):
        assert first.status in {"accepted", "incomplete"}
        assert replay.status == "duplicate"
        assert replay.payload_hash == first.payload_hash


def test_anomaly_cursor_binds_signature_filters_expiry_watermark_and_last(
    database,
):
    from factories import grant

    from dragon_radar.realtime.repository import RealtimeRepository
    from dragon_radar.storage.repository import Repository

    operation_time = [T0 + timedelta(hours=1)]

    def clock():
        return operation_time[0]

    Repository(database, clock=clock).register_source(grant(source_id="licensed-feed"))
    repository = RealtimeRepository(database, clock=clock)
    rule = return_rule(window=2)
    for revision in range(1, 4):
        repository.ingest_minute_batch(
            _minute_batch(
                f"cursor-batch-{revision}",
                (
                    minute_bar("100", minute=0, revision=revision),
                    minute_bar(str(102 + revision), minute=1, revision=revision),
                ),
            ),
            key=f"cursor-key-{revision}",
            rules=(rule.model_copy(update={"rule_version": f"return-v{revision}"}),),
        )

    expected = repository.list_anomalies(
        date=T0.date(),
        as_of=operation_time[0],
        security_id="600000.SH",
        limit=100,
    )
    first_page = repository.list_anomalies(
        date=T0.date(),
        as_of=operation_time[0],
        security_id="600000.SH",
        limit=1,
    )
    assert len(first_page.items) == 1
    assert first_page.next_cursor is not None

    with pytest.raises(ValueError, match="cursor"):
        repository.list_anomalies(
            date=T0.date(),
            as_of=operation_time[0],
            security_id="600001.SH",
            limit=1,
            cursor=first_page.next_cursor,
        )
    tampered = first_page.next_cursor[:-1] + (
        "A" if first_page.next_cursor[-1] != "A" else "B"
    )
    with pytest.raises(ValueError, match="cursor"):
        repository.list_anomalies(
            date=T0.date(),
            as_of=operation_time[0],
            security_id="600000.SH",
            limit=1,
            cursor=tampered,
        )

    operation_time[0] += timedelta(minutes=1)
    repository.ingest_minute_batch(
        _minute_batch(
            "cursor-batch-4",
            (
                minute_bar("100", minute=0, revision=4),
                minute_bar("106", minute=1, revision=4),
            ),
        ),
        key="cursor-key-4",
        rules=(rule.model_copy(update={"rule_version": "return-v4"}),),
    )
    seen_ids = [first_page.items[0].event_id]
    cursor = first_page.next_cursor
    while cursor is not None:
        page = repository.list_anomalies(
            date=T0.date(),
            as_of=T0 + timedelta(hours=1),
            security_id="600000.SH",
            limit=1,
            cursor=cursor,
        )
        seen_ids.extend(event.event_id for event in page.items)
        cursor = page.next_cursor
    assert seen_ids == [event.event_id for event in expected.items]

    operation_time[0] = T0 + timedelta(hours=1, minutes=16)
    with pytest.raises(ValueError, match="cursor"):
        repository.list_anomalies(
            date=T0.date(),
            as_of=T0 + timedelta(hours=1),
            security_id="600000.SH",
            limit=1,
            cursor=first_page.next_cursor,
        )


def test_realtime_public_read_methods_execute_no_dml_and_have_bounded_queries(
    realtime_repository,
):
    from sqlalchemy import event

    realtime_repository.ingest_quote_batch(
        _quote_batch("read-only-quote"),
        key="read-only-quote",
    )
    realtime_repository.ingest_minute_batch(
        _minute_batch("read-only-minute", (minute_bar(),)),
        key="read-only-minute",
    )
    realtime_repository.ingest_sector_flow_batch(
        _flow_batch("read-only-flow"),
        key="read-only-flow",
    )
    statements = []

    def record_statement(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        statements.append(statement)
        verb = statement.lstrip().split(maxsplit=1)[0].upper()
        assert verb not in {"INSERT", "UPDATE", "DELETE"}

    event.listen(
        realtime_repository.database.engine,
        "before_cursor_execute",
        record_statement,
    )
    try:
        calls = (
            lambda: realtime_repository.list_quotes(
                security_ids=("600000.SH",),
                as_of=T0 + timedelta(hours=1),
            ),
            lambda: realtime_repository.list_minutes(
                security_id="600000.SH",
                session_start=T0,
                session_end=T0 + timedelta(minutes=4),
                as_of=T0 + timedelta(hours=1),
            ),
            lambda: realtime_repository.list_anomalies(
                date=T0.date(),
                as_of=T0 + timedelta(hours=1),
                limit=10,
            ),
            lambda: realtime_repository.list_sector_flow(
                sector_id="sector-1",
                session_start=T0,
                session_end=T0 + timedelta(minutes=4),
                as_of=T0 + timedelta(hours=1),
            ),
        )
        query_counts = []
        for call in calls:
            before = len(statements)
            call()
            query_counts.append(len(statements) - before)
    finally:
        event.remove(
            realtime_repository.database.engine,
            "before_cursor_execute",
            record_statement,
        )

    assert max(query_counts) <= 5


def test_realtime_multibatch_permission_checks_have_constant_query_counts(
    realtime_repository,
):
    from factories import grant
    from sqlalchemy import event

    from dragon_radar.storage.repository import Repository

    source_repository = Repository(
        realtime_repository.database,
        clock=realtime_repository.clock,
    )
    source_ids = tuple(f"bounded-feed-{index}" for index in range(8))
    for index, source_id in enumerate(source_ids):
        source_repository.register_source(grant(source_id=source_id))
        snapshot = permission().model_copy(
            update={"permission_id": f"permission-{index}"}
        )
        realtime_repository.ingest_quote_batch(
            _quote_batch(
                f"bounded-quote-{index}",
                minute=index,
                source_id=source_id,
            ).model_copy(update={"permission": snapshot}),
            key=f"bounded-quote-{index}",
        )
        realtime_repository.ingest_minute_batch(
            _minute_batch(
                f"bounded-minute-{index}",
                minute_bars(["100", "103"]),
                source_id=source_id,
            ).model_copy(update={"permission": snapshot}),
            key=f"bounded-minute-{index}",
            rules=(
                return_rule(window=2).model_copy(
                    update={"rule_version": f"bounded-return-{index}"}
                ),
            ),
        )
        realtime_repository.ingest_sector_flow_batch(
            _flow_batch(
                f"bounded-flow-{index}",
                minute=index,
                source_id=source_id,
            ).model_copy(update={"permission": snapshot}),
            key=f"bounded-flow-{index}",
        )

    statements = []

    def record_statement(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        statements.append(statement)

    event.listen(
        realtime_repository.database.engine,
        "before_cursor_execute",
        record_statement,
    )
    try:
        calls = {
            "quotes": lambda: realtime_repository.list_quotes(
                security_ids=("600000.SH",),
                as_of=T0 + timedelta(hours=1),
            ),
            "minutes": lambda: realtime_repository.list_minutes(
                security_id="600000.SH",
                session_start=T0,
                session_end=T0 + timedelta(minutes=4),
                as_of=T0 + timedelta(hours=1),
            ),
            "anomalies": lambda: realtime_repository.list_anomalies(
                date=T0.date(),
                as_of=T0 + timedelta(hours=1),
                limit=100,
            ),
            "flow": lambda: realtime_repository.list_sector_flow(
                sector_id="sector-1",
                session_start=T0,
                session_end=T0 + timedelta(minutes=12),
                as_of=T0 + timedelta(hours=1),
            ),
        }
        query_counts = {}
        grant_query_counts = {}
        for name, call in calls.items():
            before = len(statements)
            call()
            executed = statements[before:]
            query_counts[name] = len(executed)
            grant_query_counts[name] = sum(
                "FROM source_grants" in statement for statement in executed
            )
    finally:
        event.remove(
            realtime_repository.database.engine,
            "before_cursor_execute",
            record_statement,
        )

    assert query_counts == {
        "quotes": 3,
        "minutes": 3,
        "anomalies": 4,
        "flow": 3,
    }
    assert grant_query_counts == {
        "quotes": 1,
        "minutes": 1,
        "anomalies": 1,
        "flow": 1,
    }


def test_latest_quote_and_flow_batch_selection_is_limited_and_child_filtered(
    realtime_repository,
):
    from sqlalchemy import event

    history_size = 64
    for index in range(history_size):
        realtime_repository.ingest_quote_batch(
            _quote_batch(
                f"history-quote-{index}",
                minute=index,
                last=str(index),
            ),
            key=f"history-quote-{index}",
        )
        realtime_repository.ingest_sector_flow_batch(
            _flow_batch(
                f"history-flow-{index}",
                minute=index,
                net_inflow=Decimal(index),
            ),
            key=f"history-flow-{index}",
        )
    realtime_repository.ingest_quote_batch(
        _quote_batch(
            "newer-unrelated-quote",
            minute=history_size,
        ).model_copy(update={"quotes": (_quote(security_id="000001.SZ"),)}),
        key="newer-unrelated-quote",
    )
    realtime_repository.ingest_sector_flow_batch(
        _flow_batch(
            "newer-unrelated-flow",
            minute=history_size,
            sector_id="sector-2",
        ),
        key="newer-unrelated-flow",
    )
    statements = []

    def record_statement(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        statements.append(statement)

    event.listen(
        realtime_repository.database.engine,
        "before_cursor_execute",
        record_statement,
    )
    try:
        quotes = realtime_repository.list_quotes(
            security_ids=("600000.SH",),
            as_of=T0 + timedelta(hours=2),
        )
        flow = realtime_repository.list_sector_flow(
            sector_id="sector-1",
            session_start=T0,
            session_end=T0 + timedelta(hours=2),
            as_of=T0 + timedelta(hours=2),
        )
    finally:
        event.remove(
            realtime_repository.database.engine,
            "before_cursor_execute",
            record_statement,
        )

    candidate_statements = [
        statement
        for statement in statements
        if "FROM realtime_quote_batches" in statement
        and "ORDER BY realtime_quote_batches.available_at DESC" in statement
    ]
    assert quotes.batch_id == f"history-quote-{history_size - 1}"
    assert quotes.items[0].quote.last == Decimal(history_size - 1)
    assert flow.batch_id == f"history-flow-{history_size - 1}"
    assert flow.items[0].point.net_inflow == Decimal(history_size - 1)
    assert len(candidate_statements) == 2
    for statement in candidate_statements:
        assert "EXISTS (SELECT" in statement
        assert "realtime_quote_batches.ingest_order DESC" in statement
        assert "LIMIT" in statement
    assert "JOIN realtime_quotes" not in candidate_statements[0]
    assert "realtime_quotes.security_id IN" in candidate_statements[0]
    assert "JOIN realtime_sector_flow_points" not in candidate_statements[1]
    assert "realtime_sector_flow_points.sector_id =" in candidate_statements[1]


def test_latest_quote_pg17_plan_uses_matching_indexes_with_optional_source(database):
    from factories import grant
    from sqlalchemy import text

    from dragon_radar.storage.repository import Repository

    Repository(database, clock=lambda: T0 + timedelta(hours=1)).register_source(
        grant(source_id="plan-feed")
    )
    with database.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO realtime_quote_batches (
                    id, quote_batch_id, source_id, source_version,
                    source_event_at, received_at, available_at,
                    permission_snapshot, payload_hash, imported_at
                )
                SELECT
                    md5('plan-parent-' || value::text)::uuid,
                    'plan-batch-' || value::text,
                    'plan-feed',
                    'v1',
                    :base + value * interval '1 millisecond',
                    :base + value * interval '1 millisecond',
                    :base + value * interval '1 millisecond',
                    '{}',
                    repeat('a', 64),
                    :base
                FROM generate_series(1, 20000) AS value
                """
            ),
            {"base": T0},
        )
        connection.execute(
            text(
                """
                INSERT INTO realtime_quotes (
                    id, source_id, batch_id, security_id, last,
                    order_book_status, order_book_unavailable_reason
                )
                SELECT
                    md5('plan-child-' || value::text)::uuid,
                    'plan-feed',
                    'plan-batch-' || value::text,
                    CASE WHEN value = 19999 THEN '600000.SH' ELSE '000001.SZ' END,
                    value,
                    'disabled',
                    'not_provided'
                FROM generate_series(1, 20000) AS value
                """
            )
        )
        connection.execute(text("ANALYZE realtime_quote_batches"))
        connection.execute(text("ANALYZE realtime_quotes"))

        plans = []
        for source_clause in ("", "AND parent.source_id = 'plan-feed'"):
            plans.append(
                connection.scalar(
                    text(
                        f"""
                        EXPLAIN (ANALYZE, FORMAT JSON)
                        SELECT parent.*
                        FROM realtime_quote_batches AS parent
                        WHERE parent.available_at <= :as_of
                          {source_clause}
                          AND EXISTS (
                              SELECT 1
                              FROM realtime_quotes AS child
                              WHERE child.source_id = parent.source_id
                                AND child.batch_id = parent.quote_batch_id
                                AND child.security_id = '600000.SH'
                              OFFSET 0
                          )
                        ORDER BY parent.available_at DESC,
                                 parent.ingest_order DESC,
                                 parent.id DESC
                        LIMIT 1
                        """
                    ),
                    {"as_of": T0 + timedelta(minutes=1)},
                )[0]["Plan"]
            )

    def nodes(plan):
        yield plan
        for child in plan.get("Plans", ()):
            yield from nodes(child)

    for plan in plans:
        flattened = tuple(nodes(plan))
        assert not any(node["Node Type"] == "Sort" for node in flattened)
        assert not any(
            node["Node Type"] == "Seq Scan"
            and node.get("Relation Name")
            in {"realtime_quote_batches", "realtime_quotes"}
            for node in flattened
        )
        used_indexes = {
            node.get("Index Name")
            for node in flattened
            if node.get("Index Name") is not None
        }
        assert used_indexes & {
            "realtime_quote_batches_as_of",
            "realtime_quote_batches_source_as_of",
        }
        assert "realtime_quotes_security" in used_indexes


def test_same_batch_concurrency_persists_one_result(realtime_repository):
    from sqlalchemy import func, select

    from dragon_radar.realtime.tables import (
        realtime_import_attempts,
        realtime_quote_batches,
    )

    batch = _quote_batch("quote-concurrent")
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                realtime_repository.ingest_quote_batch,
                batch,
                key=f"concurrent-{index}",
            )
            for index in range(2)
        ]
    results = [future.result() for future in futures]

    assert {result.status for result in results} == {"accepted", "duplicate"}
    assert len({result.payload_hash for result in results}) == 1
    with realtime_repository.database.engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(realtime_quote_batches))
            == 1
        )
        assert (
            connection.scalar(
                select(func.count()).select_from(realtime_import_attempts)
            )
            == 2
        )


def test_conflicting_batch_concurrency_fails_closed(realtime_repository):
    from sqlalchemy import func, select

    from dragon_radar.realtime.tables import (
        realtime_import_attempts,
        realtime_quote_batches,
        realtime_quotes,
    )
    from dragon_radar.storage.repository import Conflict

    batches = (
        _quote_batch("quote-concurrent-conflict", last="10"),
        _quote_batch("quote-concurrent-conflict", last="11"),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                realtime_repository.ingest_quote_batch,
                batch,
                key=f"concurrent-conflict-{index}",
            )
            for index, batch in enumerate(batches)
        ]
    outcomes = []
    for future in futures:
        try:
            outcomes.append(future.result())
        except Conflict as error:
            outcomes.append(error)

    assert sum(isinstance(item, Conflict) for item in outcomes) == 1
    with realtime_repository.database.engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(realtime_quote_batches))
            == 1
        )
        assert connection.scalar(select(func.count()).select_from(realtime_quotes)) == 1
        assert (
            connection.scalar(
                select(func.count()).select_from(realtime_import_attempts)
            )
            == 2
        )
        assert set(connection.scalars(select(realtime_import_attempts.c.status))) == {
            "accepted",
            "rejected",
        }


def test_realtime_ingestion_validates_typed_models_and_maps_storage_conflict(
    realtime_repository,
):
    from dragon_radar.realtime.ingestion import (
        RealtimeIngestion,
        RealtimeIngestionConflict,
    )

    ingestion = RealtimeIngestion(realtime_repository)
    batch = _quote_batch("service-conflict", last="10")
    ingestion.ingest_quote_batch(batch, key="service-conflict-1")

    with pytest.raises(TypeError, match="QuoteBatchInput"):
        ingestion.ingest_quote_batch(batch.model_dump(), key="raw-dict")
    with pytest.raises(RealtimeIngestionConflict):
        ingestion.ingest_quote_batch(
            _quote_batch("service-conflict", last="11"),
            key="service-conflict-2",
        )


def test_realtime_import_and_read_recheck_current_permissions(database):
    from factories import grant

    from dragon_radar.realtime.repository import RealtimeRepository
    from dragon_radar.storage.repository import Forbidden, Repository

    def clock():
        return T0 + timedelta(hours=1)

    source_repository = Repository(database, clock=clock)
    source_repository.register_source(
        grant(source_id="licensed-feed", local_storage=False)
    )
    realtime = RealtimeRepository(database, clock=clock)
    with pytest.raises(Forbidden):
        realtime.ingest_quote_batch(
            _quote_batch("permission-denied"),
            key="permission-denied",
        )

    source_repository.register_source(grant(source_id="licensed-feed"))
    realtime.ingest_quote_batch(
        _quote_batch("permission-revoked"),
        key="permission-revoked",
    )
    source_repository.register_source(
        grant(source_id="licensed-feed", local_display=False)
    )
    with pytest.raises(Forbidden):
        realtime.list_quotes(
            security_ids=("600000.SH",),
            as_of=T0 + timedelta(hours=1),
            batch_id="permission-revoked",
            source_id="licensed-feed",
        )


def test_permission_readers_share_locks_and_permission_update_times_out(
    realtime_repository,
):
    from sqlalchemy import event, text
    from sqlalchemy.exc import OperationalError

    batch = _quote_batch("permission-share-lock")
    realtime_repository.ingest_quote_batch(batch, key="permission-share-lock")
    reader_one_locked = Event()
    reader_two_locked = Event()
    release_readers = Event()

    def hold_permission_readers(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        if "FROM source_grants" not in statement or "FOR SHARE" not in statement:
            return
        if current_thread().name.startswith("permission-reader-one"):
            reader_one_locked.set()
        elif current_thread().name.startswith("permission-reader-two"):
            reader_two_locked.set()
        else:
            return
        assert release_readers.wait(5)

    event.listen(
        realtime_repository.database.engine,
        "after_cursor_execute",
        hold_permission_readers,
    )
    try:
        with (
            ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="permission-reader-one",
            ) as first_executor,
            ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="permission-reader-two",
            ) as second_executor,
        ):
            first = first_executor.submit(
                realtime_repository.list_quotes,
                security_ids=("600000.SH",),
                as_of=T0 + timedelta(hours=1),
            )
            second = second_executor.submit(
                realtime_repository.list_quotes,
                security_ids=("600000.SH",),
                as_of=T0 + timedelta(hours=1),
            )
            assert reader_one_locked.wait(5)
            assert reader_two_locked.wait(5)

            with (
                pytest.raises(OperationalError, match="lock timeout"),
                realtime_repository.database.engine.begin() as connection,
            ):
                connection.execute(text("SET LOCAL lock_timeout = '200ms'"))
                connection.execute(
                    text(
                        """
                        UPDATE source_grants
                        SET updated_at = updated_at
                        WHERE source_id = 'licensed-feed'
                        """
                    )
                )

            release_readers.set()
            assert first.result(timeout=5).batch_id == batch.batch_id
            assert second.result(timeout=5).batch_id == batch.batch_id
    finally:
        release_readers.set()
        event.remove(
            realtime_repository.database.engine,
            "after_cursor_execute",
            hold_permission_readers,
        )


class _HTTPRealtimeRepository:
    def __init__(self):
        from dragon_radar.realtime.models import (
            AnomalyReadResult,
            MinuteBarRecord,
            MinuteReadResult,
            QuoteReadResult,
            SectorFlowReadResult,
            SectorFlowRecord,
        )

        completeness = SourceCompleteness(
            status="complete",
            expected_count=1,
            received_count=1,
        )
        self.clock = lambda: T0 + timedelta(hours=1)
        self.ingestion_calls = 0
        self.calls = []
        self.quotes = QuoteReadResult(
            as_of=T0 + timedelta(minutes=1),
            batch_id="quote-http",
            items=(
                QuoteRecord(
                    batch_id="quote-http",
                    source_id="licensed-feed",
                    source_version="v1",
                    source_event_at=T0,
                    received_at=T0 + timedelta(milliseconds=1250),
                    available_at=T0 + timedelta(seconds=2),
                    quote=QuoteInput(
                        security_id="600000.SH",
                        last=Decimal("9007199254740993.01"),
                        order_book_status="disabled",
                        order_book_unavailable_reason="not_authorized",
                    ),
                ),
            ),
            completeness=completeness,
        )
        self.minutes = MinuteReadResult(
            as_of=T0 + timedelta(hours=1),
            batch_id="minute-http",
            items=(
                MinuteBarRecord(
                    batch_id="minute-http",
                    source_id="licensed-feed",
                    source_version="v1",
                    bar=minute_bar("10", minute=0),
                ),
            ),
            completeness=completeness,
        )
        self.anomalies = AnomalyReadResult(
            as_of=T0 + timedelta(hours=1),
            items=(
                AnomalyEvent(
                    event_id="event-http",
                    batch_id="minute-http",
                    security_id="600000.SH",
                    rule_version="return-v1",
                    event_at=T0,
                    available_at=T0 + timedelta(seconds=2),
                    result=AnomalyResult(
                        triggered=True,
                        metric="window_return",
                        window=2,
                        value=Decimal("0.03"),
                        threshold=Decimal("0.02"),
                        reason=None,
                    ),
                    quality="complete",
                ),
            ),
            next_cursor="next-http",
            completeness=completeness,
        )
        self.flow = SectorFlowReadResult(
            as_of=T0 + timedelta(hours=1),
            batch_id="flow-http",
            items=(
                SectorFlowRecord(
                    batch_id="flow-http",
                    source_id="licensed-feed",
                    source_version="v1",
                    available_at=T0 + timedelta(seconds=2),
                    point=SectorFlowPoint(
                        sector_id="sector-1",
                        event_at=T0,
                        net_inflow=Decimal("12.5"),
                        unit="CNY",
                    ),
                ),
            ),
            completeness=completeness,
        )

    def list_quotes(self, **values):
        self.calls.append(("quotes", values))
        return self.quotes

    def list_minutes(self, **values):
        self.calls.append(("minutes", values))
        return self.minutes

    def list_anomalies(self, **values):
        self.calls.append(("anomalies", values))
        if values.get("cursor") == "bad":
            raise ValueError("Invalid realtime anomaly cursor")
        return self.anomalies

    def list_sector_flow(self, **values):
        self.calls.append(("flow", values))
        return self.flow


class _HTTPWorkbenchRepository:
    database = None


async def _http_client(realtime_repository):
    from dragon_radar.http.app import create_workbench_app

    app = create_workbench_app(
        repository=_HTTPWorkbenchRepository(),
        realtime_repository=realtime_repository,
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.mark.anyio
async def test_ex04_quotes_has_exact_decimal_timestamp_and_delay_contract():
    repository = _HTTPRealtimeRepository()
    async with await _http_client(repository) as client:
        response = await client.get(
            "/api/realtime/quotes",
            params={
                "security_ids": "600000.SH",
                "as_of": "2026-10-09T09:31:00+08:00",
            },
        )

    assert response.status_code == 200
    assert b'"last":9007199254740993.01' in response.content
    payload = json.loads(response.content, parse_float=Decimal)
    assert set(payload) == {
        "trade_date",
        "as_of",
        "snapshot_id",
        "source",
        "source_version",
        "coverage",
        "quality_flags",
        "rule_version",
        "basis",
        "data",
    }
    assert set(payload["data"]) == {
        "quote_batch_id",
        "source_event_at",
        "received_at",
        "delay_ms",
        "status",
        "quotes",
    }
    assert payload["data"]["delay_ms"] == 1250
    assert payload["data"]["status"] == "complete"
    assert payload["data"]["quotes"][0]["last"] == Decimal("9007199254740993.01")
    assert repository.calls == [
        (
            "quotes",
            {
                "security_ids": ("600000.SH",),
                "as_of": T0 + timedelta(minutes=1),
                "source_id": None,
                "batch_id": None,
            },
        )
    ]


@pytest.mark.anyio
async def test_ex04_delay_uses_exact_integer_millisecond_floor():
    repository = _HTTPRealtimeRepository()
    quote = repository.quotes.items[0].model_copy(
        update={"received_at": T0 + timedelta(milliseconds=1001)}
    )
    repository.quotes = repository.quotes.model_copy(update={"items": (quote,)})

    async with await _http_client(repository) as client:
        response = await client.get(
            "/api/realtime/quotes",
            params={"security_ids": "600000.SH"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["delay_ms"] == 1001


@pytest.mark.anyio
async def test_ex05_minutes_has_exact_bar_contract_without_gap_fill():
    repository = _HTTPRealtimeRepository()
    async with await _http_client(repository) as client:
        response = await client.get(
            "/api/realtime/minutes",
            params={"security_id": "600000.SH", "date": "2026-10-09"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload["data"]) == {"status", "bars"}
    assert len(payload["data"]["bars"]) == 1
    assert set(payload["data"]["bars"][0]) == {
        "batch_id",
        "source_id",
        "source_version",
        "security_id",
        "event_at",
        "received_at",
        "available_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "revision",
        "final",
    }
    assert repository.calls == [
        (
            "minutes",
            {
                "security_id": "600000.SH",
                "session_start": datetime(2026, 10, 9, 1, 30, tzinfo=UTC),
                "session_end": datetime(2026, 10, 9, 7, 0, tzinfo=UTC),
                "session_windows": (
                    (
                        datetime(2026, 10, 9, 1, 30, tzinfo=UTC),
                        datetime(2026, 10, 9, 3, 30, tzinfo=UTC),
                    ),
                    (
                        datetime(2026, 10, 9, 5, 0, tzinfo=UTC),
                        datetime(2026, 10, 9, 7, 0, tzinfo=UTC),
                    ),
                ),
                "as_of": T0 + timedelta(hours=1),
                "source_id": None,
                "batch_id": None,
            },
        )
    ]


@pytest.mark.anyio
async def test_ex06_anomalies_has_transparent_rule_input_quality_and_cursor():
    repository = _HTTPRealtimeRepository()
    async with await _http_client(repository) as client:
        response = await client.get(
            "/api/realtime/anomalies",
            params={"date": "2026-10-09", "limit": "1"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload["data"]) == {"status", "events", "next_cursor"}
    assert payload["data"]["next_cursor"] == "next-http"
    assert set(payload["data"]["events"][0]) == {
        "event_id",
        "batch_id",
        "security_id",
        "rule_version",
        "event_at",
        "available_at",
        "metric",
        "window",
        "value",
        "threshold",
        "quality",
    }


@pytest.mark.anyio
async def test_ex06_accepts_repository_signed_cursor_longer_than_identifier_limit(
    realtime_repository,
):
    for revision, close in ((1, "103"), (2, "104")):
        realtime_repository.ingest_minute_batch(
            _minute_batch(
                f"http-cursor-{revision}",
                (
                    minute_bar("100", minute=0, revision=revision),
                    minute_bar(close, minute=1, revision=revision),
                ),
            ),
            key=f"http-cursor-{revision}",
            rules=(
                return_rule(window=2).model_copy(
                    update={"rule_version": f"http-return-v{revision}"}
                ),
            ),
        )

    async with await _http_client(realtime_repository) as client:
        first = await client.get(
            "/api/realtime/anomalies",
            params={"date": "2026-10-09", "limit": "1"},
        )
        cursor = first.json()["data"]["next_cursor"]
        second = await client.get(
            "/api/realtime/anomalies",
            params={"date": "2026-10-09", "limit": "1", "cursor": cursor},
        )

    assert first.status_code == 200
    assert 400 <= len(cursor) <= 8192
    assert second.status_code == 200
    assert len(second.json()["data"]["events"]) == 1


@pytest.mark.anyio
async def test_ex06_visible_batch_with_zero_events_returns_complete_empty_data():
    from dragon_radar.realtime.models import AnomalyReadResult

    repository = _HTTPRealtimeRepository()
    repository.anomalies = AnomalyReadResult(
        as_of=T0 + timedelta(hours=1),
        items=(),
        next_cursor=None,
        completeness=SourceCompleteness(
            status="complete",
            expected_count=0,
            received_count=0,
        ),
    )

    async with await _http_client(repository) as client:
        response = await client.get(
            "/api/realtime/anomalies",
            params={"date": "2026-10-09"},
        )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "status": "complete",
        "events": [],
        "next_cursor": None,
    }
    assert response.json()["coverage"] == {
        "expected": 0,
        "valid": 0,
        "excluded": 0,
        "missing": 0,
    }
    assert response.json()["quality_flags"] == []


@pytest.mark.anyio
async def test_ex06_without_visible_batch_returns_null_data_with_no_data_flag():
    from dragon_radar.realtime.models import AnomalyReadResult

    repository = _HTTPRealtimeRepository()
    repository.anomalies = AnomalyReadResult(
        as_of=T0 + timedelta(hours=1),
        items=(),
        next_cursor=None,
        completeness=SourceCompleteness(
            status="unavailable",
            expected_count=None,
            received_count=0,
            reason="no_data",
        ),
    )

    async with await _http_client(repository) as client:
        response = await client.get(
            "/api/realtime/anomalies",
            params={"date": "2026-10-09"},
        )

    assert response.status_code == 200
    assert response.json()["data"] is None
    assert response.json()["coverage"] is None
    assert response.json()["quality_flags"] == ["no_data"]


@pytest.mark.anyio
async def test_ex07_sector_flow_preserves_source_value_unit_and_null_reason():
    repository = _HTTPRealtimeRepository()
    unavailable = repository.flow.items[0].model_copy(
        update={
            "point": repository.flow.items[0].point.model_copy(
                update={
                    "net_inflow": None,
                    "unavailable_reason": "field_not_authorized",
                }
            )
        }
    )
    repository.flow = repository.flow.model_copy(update={"items": (unavailable,)})
    async with await _http_client(repository) as client:
        response = await client.get(
            "/api/market/sector-flow/intraday",
            params={"sector": "sector-1", "date": "2026-10-09"},
        )

    assert response.status_code == 200
    point = response.json()["data"]["points"][0]
    assert set(point) == {
        "batch_id",
        "source_id",
        "source_version",
        "sector",
        "event_at",
        "available_at",
        "net_inflow",
        "unit",
        "unavailable_reason",
    }
    assert point["net_inflow"] is None
    assert point["unavailable_reason"] == "field_not_authorized"
    assert "turnover" not in point
    assert repository.calls == [
        (
            "flow",
            {
                "sector_id": "sector-1",
                "session_start": datetime(2026, 10, 9, 1, 30, tzinfo=UTC),
                "session_end": datetime(2026, 10, 9, 7, 0, tzinfo=UTC),
                "session_windows": (
                    (
                        datetime(2026, 10, 9, 1, 30, tzinfo=UTC),
                        datetime(2026, 10, 9, 3, 30, tzinfo=UTC),
                    ),
                    (
                        datetime(2026, 10, 9, 5, 0, tzinfo=UTC),
                        datetime(2026, 10, 9, 7, 0, tzinfo=UTC),
                    ),
                ),
                "as_of": T0 + timedelta(hours=1),
                "source_id": None,
                "batch_id": None,
            },
        )
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "query"),
    [
        ("/api/realtime/quotes", "security_ids=600000.SH&unknown=1"),
        (
            "/api/realtime/quotes",
            "security_ids=600000.SH&security_ids=000001.SZ",
        ),
        ("/api/realtime/quotes", "security_ids=600000.SH,600000.SH"),
        ("/api/realtime/quotes", "security_ids=600000.SH,%20000001.SZ"),
        ("/api/realtime/minutes", "security_id=600000.SH"),
        ("/api/realtime/minutes", "security_id=600000.SH&date=2026-02-30"),
        ("/api/realtime/anomalies", "date=2026-10-09&limit=0"),
        ("/api/realtime/anomalies", "date=2026-10-09&limit=101"),
        (
            "/api/market/sector-flow/intraday",
            "sector=sector-1&date=2026-10-09&date=2026-10-10",
        ),
    ],
)
async def test_realtime_routes_reject_invalid_queries_before_repository(path, query):
    repository = _HTTPRealtimeRepository()
    async with await _http_client(repository) as client:
        response = await client.get(f"{path}?{query}")

    assert response.status_code == 400
    assert response.json() == {
        "code": "INVALID_PARAM",
        "message": "Invalid request",
    }
    assert repository.calls == []


@pytest.mark.parametrize(
    "value",
    (
        "\ufffd",
        "%ZZ",
        "\x00",
        "\x1f",
        " leading",
        "trailing ",
        "Ａ00001.SH",
        "-leading",
        ".leading",
    ),
)
def test_realtime_identifier_rejects_noncanonical_or_nonascii_values(value):
    from dragon_radar.http.routes.realtime import _identifier

    with pytest.raises(ValueError, match="Invalid security_id"):
        _identifier(value, "security_id")


@pytest.mark.parametrize(
    "value",
    (
        "600000.SH",
        "source:v1-test_name",
        "550e8400-e29b-41d4-a716-446655440000",
    ),
)
def test_realtime_identifier_accepts_stable_ascii_ids(value):
    from dragon_radar.http.routes.realtime import _identifier

    assert _identifier(value, "security_id") == value


@pytest.mark.anyio
@pytest.mark.parametrize(
    "query",
    (
        "security_id=%FF&date=2026-10-09",
        "security_id=%ZZ&date=2026-10-09",
        "security_id=%00&date=2026-10-09",
    ),
)
async def test_realtime_routes_reject_malformed_encoded_identifiers(query):
    repository = _HTTPRealtimeRepository()
    async with await _http_client(repository) as client:
        response = await client.get(f"/api/realtime/minutes?{query}")

    assert response.status_code == 400
    assert repository.calls == []


def test_realtime_security_ids_checks_total_length_before_split():
    from dragon_radar.http.routes.realtime import _security_ids

    class MustNotSplit(str):
        def split(self, *_args, **_kwargs):
            raise AssertionError("oversized security_ids was split")

    with pytest.raises(ValueError, match="security_ids"):
        _security_ids(MustNotSplit("A" * 20100))


def test_realtime_security_ids_accepts_exact_total_length_boundary():
    from dragon_radar.http.routes.realtime import _security_ids

    values = tuple(f"A{index:02d}{'x' * 197}" for index in range(100))
    wire_value = ",".join(values)

    assert len(wire_value) == 20099
    assert _security_ids(wire_value) == values


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "batch_name"),
    [
        ("/api/realtime/quotes?security_ids=600000.SH", "quote_batch_id"),
        (
            "/api/realtime/minutes?security_id=600000.SH&date=2026-10-09",
            "batch_id",
        ),
        (
            "/api/market/sector-flow/intraday?sector=sector-1&date=2026-10-09",
            "batch_id",
        ),
    ],
)
async def test_realtime_explicit_batch_requires_source(path, batch_name):
    repository = _HTTPRealtimeRepository()
    async with await _http_client(repository) as client:
        response = await client.get(f"{path}&{batch_name}=batch-1")

    assert response.status_code == 400
    assert repository.calls == []


@pytest.mark.anyio
async def test_realtime_no_authorized_batch_is_200_null_data_with_flags():
    from dragon_radar.realtime.models import QuoteReadResult

    repository = _HTTPRealtimeRepository()
    repository.quotes = QuoteReadResult(
        as_of=T0,
        batch_id=None,
        items=(),
        completeness=SourceCompleteness(
            status="unavailable",
            expected_count=1,
            received_count=0,
            reason="no_data",
        ),
    )
    async with await _http_client(repository) as client:
        response = await client.get(
            "/api/realtime/quotes",
            params={"security_ids": "600000.SH"},
        )

    assert response.status_code == 200
    assert response.json()["data"] is None
    assert response.json()["quality_flags"] == ["no_data"]


@pytest.mark.anyio
async def test_realtime_explicit_missing_batch_is_404_without_fallback():
    from dragon_radar.realtime.models import QuoteReadResult

    repository = _HTTPRealtimeRepository()
    repository.quotes = QuoteReadResult(
        as_of=T0,
        batch_id=None,
        items=(),
        completeness=SourceCompleteness(
            status="unavailable",
            expected_count=1,
            received_count=0,
            reason="no_data",
        ),
    )
    async with await _http_client(repository) as client:
        response = await client.get(
            "/api/realtime/quotes",
            params={
                "security_ids": "600000.SH",
                "source_id": "licensed-feed",
                "quote_batch_id": "missing-batch",
            },
        )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


@pytest.mark.anyio
async def test_realtime_revoked_permission_returns_403_without_payload():
    from dragon_radar.storage.repository import Forbidden

    repository = _HTTPRealtimeRepository()

    def denied(**_values):
        raise Forbidden("protected source and token must not leak")

    repository.list_quotes = denied
    async with await _http_client(repository) as client:
        response = await client.get(
            "/api/realtime/quotes",
            params={"security_ids": "600000.SH"},
        )

    assert response.status_code == 403
    assert response.json() == {
        "code": "FORBIDDEN",
        "message": "Source access denied",
    }
    assert b"protected source" not in response.content
    assert b"token" not in response.content


@pytest.mark.anyio
async def test_realtime_unknown_entity_is_structured_404():
    from dragon_radar.storage.repository import NotFound

    repository = _HTTPRealtimeRepository()

    def missing(**_values):
        raise NotFound("protected entity name")

    repository.list_sector_flow = missing
    async with await _http_client(repository) as client:
        response = await client.get(
            "/api/market/sector-flow/intraday",
            params={"sector": "unknown", "date": "2026-10-09"},
        )

    assert response.status_code == 404
    assert response.json() == {
        "code": "NOT_FOUND",
        "message": "Resource not found",
    }
    assert b"protected entity" not in response.content


@pytest.mark.anyio
async def test_realtime_invalid_cursor_is_structured_400():
    repository = _HTTPRealtimeRepository()
    async with await _http_client(repository) as client:
        response = await client.get(
            "/api/realtime/anomalies",
            params={"date": "2026-10-09", "cursor": "bad"},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_PARAM"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "cursor",
    (
        "",
        " ",
        "abc\n.def",
        "\u00e9.def",
        "abc",
        "abc.def",
        "abc..def",
        "abc.def=",
        f"{'a' * 4053}.{'b' * 43}",
    ),
)
async def test_realtime_cursor_parser_rejects_invalid_wire_values(cursor):
    repository = _HTTPRealtimeRepository()
    async with await _http_client(repository) as client:
        response = await client.get(
            "/api/realtime/anomalies",
            params={"date": "2026-10-09", "cursor": cursor},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_PARAM"
    assert repository.calls == []


@pytest.mark.anyio
async def test_realtime_cursor_parser_accepts_large_base64url_shape():
    repository = _HTTPRealtimeRepository()
    cursor = f"{'a' * 443}.{'b' * 43}"
    async with await _http_client(repository) as client:
        response = await client.get(
            "/api/realtime/anomalies",
            params={"date": "2026-10-09", "cursor": cursor},
        )

    assert len(cursor) == 487
    assert response.status_code == 200
    assert repository.calls[-1][1]["cursor"] == cursor


@pytest.mark.parametrize("cursor", ("é", "a" * 4097))
def test_realtime_cursor_checks_ascii_and_length_before_regex(monkeypatch, cursor):
    from dragon_radar.http.routes import realtime

    class MustNotMatch:
        def fullmatch(self, _value):
            raise AssertionError("invalid cursor reached regex")

    monkeypatch.setattr(realtime, "_SIGNED_CURSOR", MustNotMatch())
    with pytest.raises(ValueError, match="Invalid cursor"):
        realtime._cursor(cursor)


def test_realtime_cursor_accepts_exact_length_boundary():
    from dragon_radar.http.routes.realtime import _cursor

    cursor = f"{'a' * 4052}.{'b' * 43}"

    assert len(cursor) == 4096
    assert _cursor(cursor) == cursor


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    (
        "/api/realtime/minutes?security_id=600000.SH&date=9999-12-31",
        "/api/realtime/anomalies?date=9999-12-31",
        "/api/market/sector-flow/intraday?sector=sector-1&date=9999-12-31",
    ),
)
async def test_realtime_date_range_overflow_is_invalid_request(path):
    repository = _HTTPRealtimeRepository()
    async with await _http_client(repository) as client:
        response = await client.get(path)

    assert response.status_code == 400
    assert response.json() == {
        "code": "INVALID_PARAM",
        "message": "Invalid request",
    }
    assert repository.calls == []


@pytest.mark.anyio
async def test_realtime_public_get_never_starts_ingestion_or_writes():
    repository = _HTTPRealtimeRepository()
    async with await _http_client(repository) as client:
        responses = (
            await client.get(
                "/api/realtime/quotes",
                params={"security_ids": "600000.SH"},
            ),
            await client.get(
                "/api/realtime/minutes",
                params={"security_id": "600000.SH", "date": "2026-10-09"},
            ),
            await client.get(
                "/api/realtime/anomalies",
                params={"date": "2026-10-09"},
            ),
            await client.get(
                "/api/market/sector-flow/intraday",
                params={"sector": "sector-1", "date": "2026-10-09"},
            ),
        )

    assert {response.status_code for response in responses} == {200}
    assert repository.ingestion_calls == 0


@pytest.mark.anyio
async def test_realtime_routes_without_repository_return_storage_unavailable():
    from dragon_radar.http.app import create_workbench_app

    app = create_workbench_app(repository=_HTTPWorkbenchRepository())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/realtime/quotes",
            params={"security_ids": "600000.SH"},
        )

    assert response.status_code == 503
    assert response.json()["code"] == "STORAGE_UNAVAILABLE"


@pytest.mark.anyio
async def test_realtime_http_gets_execute_no_dml(
    realtime_repository,
):
    from sqlalchemy import event

    realtime_repository.ingest_quote_batch(
        _quote_batch("http-read-only-quote"),
        key="http-read-only-quote",
    )
    realtime_repository.ingest_minute_batch(
        _minute_batch(
            "http-read-only-minute",
            (minute_bar("100", minute=0), minute_bar("103", minute=1)),
        ),
        key="http-read-only-minute",
        rules=(return_rule(window=2),),
    )
    realtime_repository.ingest_sector_flow_batch(
        _flow_batch("http-read-only-flow"),
        key="http-read-only-flow",
    )
    statements = []

    def reject_write(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        statements.append(statement)
        assert statement.lstrip().split(maxsplit=1)[0].upper() not in {
            "INSERT",
            "UPDATE",
            "DELETE",
        }

    event.listen(
        realtime_repository.database.engine,
        "before_cursor_execute",
        reject_write,
    )
    try:
        async with await _http_client(realtime_repository) as client:
            responses = (
                await client.get(
                    "/api/realtime/quotes",
                    params={"security_ids": "600000.SH"},
                ),
                await client.get(
                    "/api/realtime/minutes",
                    params={"security_id": "600000.SH", "date": "2026-10-09"},
                ),
                await client.get(
                    "/api/realtime/anomalies",
                    params={"date": "2026-10-09"},
                ),
                await client.get(
                    "/api/market/sector-flow/intraday",
                    params={"sector": "sector-1", "date": "2026-10-09"},
                ),
            )
    finally:
        event.remove(
            realtime_repository.database.engine,
            "before_cursor_execute",
            reject_write,
        )

    assert {response.status_code for response in responses} == {200}
    assert statements


@pytest.mark.anyio
async def test_realtime_explicit_unknown_batch_is_404_without_latest_fallback(
    realtime_repository,
):
    realtime_repository.ingest_quote_batch(
        _quote_batch("available-http-batch"),
        key="available-http-batch",
    )
    async with await _http_client(realtime_repository) as client:
        response = await client.get(
            "/api/realtime/quotes",
            params={
                "security_ids": "600000.SH",
                "source_id": "licensed-feed",
                "quote_batch_id": "missing-http-batch",
            },
        )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert b"available-http-batch" not in response.content


@pytest.mark.anyio
async def test_realtime_anomalies_revoked_permission_is_403_without_payload(
    realtime_repository,
):
    from dragon_radar.storage.repository import Repository

    realtime_repository.ingest_minute_batch(
        _minute_batch(
            "revoked-http-anomaly",
            (minute_bar("100", minute=0), minute_bar("103", minute=1)),
        ),
        key="revoked-http-anomaly",
        rules=(return_rule(window=2),),
    )
    Repository(
        realtime_repository.database,
        clock=realtime_repository.clock,
    ).revoke_source("licensed-feed")

    async with await _http_client(realtime_repository) as client:
        response = await client.get(
            "/api/realtime/anomalies",
            params={"date": "2026-10-09"},
        )

    assert response.status_code == 403
    assert response.json() == {
        "code": "FORBIDDEN",
        "message": "Source access denied",
    }
    assert b"revoked-http-anomaly" not in response.content


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "params"),
    [
        (
            "/api/realtime/quotes",
            {"security_ids": "000001.SZ"},
        ),
        (
            "/api/realtime/minutes",
            {"security_id": "000001.SZ", "date": "2026-10-09"},
        ),
        (
            "/api/market/sector-flow/intraday",
            {"sector": "unknown-sector", "date": "2026-10-09"},
        ),
    ],
)
async def test_realtime_unknown_entity_in_available_store_is_404(
    realtime_repository,
    path,
    params,
):
    realtime_repository.ingest_quote_batch(
        _quote_batch("known-http-quote"),
        key="known-http-quote",
    )
    realtime_repository.ingest_minute_batch(
        _minute_batch("known-http-minute", (minute_bar(),)),
        key="known-http-minute",
    )
    realtime_repository.ingest_sector_flow_batch(
        _flow_batch("known-http-flow"),
        key="known-http-flow",
    )
    async with await _http_client(realtime_repository) as client:
        response = await client.get(path, params=params)

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
