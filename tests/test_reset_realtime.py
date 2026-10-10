from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from uuid import uuid4

import pytest
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
        "realtime_import_attempts_idempotency_key",
        "realtime_import_attempts_status",
        "realtime_import_attempts_payload_hash",
    },
}

EXPECTED_REALTIME_INDEXES = {
    "realtime_quote_batches_as_of",
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
            assert [
                column["name"] for column in inspector.get_columns(table)
            ] == expected_columns
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
                "realtime_quote_batches_source_batch_key",
                ("source_id", "quote_batch_id"),
            ),
            (
                "realtime_quote_batches_source_batch_version_key",
                ("source_id", "quote_batch_id", "source_version"),
            ),
        }

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
