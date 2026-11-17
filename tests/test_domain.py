import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from factories import DAYS, NOW, bar, batch, grant, state
from pydantic import ValidationError


def test_frozen_batch_round_trips_decimal_and_utc():
    data = batch()
    assert type(data).model_validate_json(data.model_dump_json()) == data
    assert isinstance(data.bars, tuple)
    assert '"close":"10"' in data.model_dump_json()
    with pytest.raises(ValidationError, match="frozen"):
        data.source_version = "changed"
    with pytest.raises(ValidationError, match="frozen"):
        data.bars[0].close = Decimal("20")


@pytest.mark.parametrize("value", [10.0, float("nan"), float("inf"), True])
def test_decimal_contract_rejects_float_and_bool(value):
    with pytest.raises(ValidationError, match="Decimal"):
        bar(close=value)


def test_decimal_string_is_lossless_and_optional_unknowns_stay_unknown():
    assert bar(close="10.123456789123456789").close == Decimal("10.123456789123456789")
    unknown = state(suspended=None, is_st=None, listing_date=None)
    assert unknown.suspended is unknown.is_st is unknown.listing_date is None


@pytest.mark.parametrize(
    "factory,field",
    [
        (bar, "available_at"),
        (batch, "as_of"),
        (state, "available_at"),
        (grant, "starts_at"),
        (grant, "expires_at"),
    ],
)
def test_timestamps_reject_naive(factory, field):
    with pytest.raises(ValidationError, match="timezone"):
        factory(**{field: datetime(2026, 9, 9)})


@pytest.mark.parametrize(
    "value",
    [
        1788955200,
        1788955200.25,
        True,
        False,
        "1788955200",
        "2026-09-09T12:00:00",
        b"2026-09-09T12:00:00Z",
        Decimal("1788955200"),
    ],
)
def test_timestamp_contract_rejects_coercible_raw_inputs(value):
    with pytest.raises(ValidationError):
        batch(as_of=value)


@pytest.mark.parametrize("value", [1788955200, 1788955200.25, True, "1788955200"])
def test_timestamp_contract_rejects_numeric_json(value):
    data = batch()
    payload = json.loads(data.model_dump_json())
    payload["as_of"] = value
    with pytest.raises(ValidationError):
        type(data).model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "value",
    [NOW, "2026-09-09T12:00:00Z", "2026-09-09T20:00:00+08:00"],
)
@pytest.mark.parametrize(
    "factory,field",
    [
        (bar, "available_at"),
        (batch, "as_of"),
        (batch, "source_time"),
        (state, "available_at"),
        (grant, "starts_at"),
        (grant, "expires_at"),
    ],
)
def test_timestamp_contract_accepts_explicit_timezones(factory, field, value):
    result = factory(**{field: value})
    assert getattr(result, field) == NOW
    assert getattr(result, field).tzinfo is UTC
    assert type(result).model_validate_json(result.model_dump_json()) == result


def test_grant_defaults_fail_closed():
    from dragon_radar.domain.models import SourceGrant
    from dragon_radar.domain.quality import (
        grant_allows_display,
        grant_allows_storage,
        validate_batch,
    )

    rights = SourceGrant(source_id="synthetic")
    assert not any(
        (
            rights.approved,
            rights.local_storage,
            rights.local_display,
            rights.export,
            rights.ai,
            rights.sharing,
        )
    )
    assert not grant_allows_storage(rights, source_id="synthetic", now=NOW)
    assert not grant_allows_display(rights, source_id="synthetic", now=NOW)
    report = validate_batch(batch(grant=rights), now=NOW)
    assert not report.publishable
    assert "grant_denied" in {i.code for i in report.issues}


def test_storage_and_display_rights_are_independent():
    from dragon_radar.domain.quality import (
        grant_allows_display,
        grant_allows_storage,
    )

    storage_only = grant(local_display=False)
    display_only = grant(local_storage=False)

    assert grant_allows_storage(storage_only, source_id="synthetic", now=NOW)
    assert not grant_allows_display(storage_only, source_id="synthetic", now=NOW)
    assert not grant_allows_storage(display_only, source_id="synthetic", now=NOW)
    assert grant_allows_display(display_only, source_id="synthetic", now=NOW)


@pytest.mark.parametrize(
    "changes",
    [
        {"approved": False},
        {"reference": None},
        {"local_display": False},
        {"starts_at": NOW + timedelta(seconds=1)},
        {"expires_at": NOW},
        {"source_id": "other"},
    ],
)
def test_grant_must_be_valid_at_operation_time(changes):
    from dragon_radar.domain.quality import validate_batch

    result = validate_batch(batch(grant=grant(**changes)), now=NOW)
    assert not result.publishable
    assert "grant_denied" in {i.code for i in result.issues}


def test_grant_start_inclusive_expiry_exclusive():
    from dragon_radar.domain.quality import validate_batch

    data = batch(grant=grant(starts_at=NOW, expires_at=NOW + timedelta(seconds=1)))
    assert validate_batch(data, now=NOW).publishable
    assert not validate_batch(data, now=NOW + timedelta(seconds=1)).publishable
    with pytest.raises(ValueError, match="timezone"):
        validate_batch(data, now=NOW.replace(tzinfo=None))


@pytest.mark.parametrize("sessions", [(), DAYS[::-1], (DAYS[0], DAYS[0])])
def test_calendar_is_explicit_sorted_unique(sessions):
    from dragon_radar.domain.models import ExchangeCalendar

    with pytest.raises(ValidationError, match="sessions"):
        ExchangeCalendar(exchange="TEST", version="c1", sessions=sessions)


def test_quality_report_public_schema_and_json_remain_market_specific():
    from dragon_radar.domain.models import MarketBatch
    from dragon_radar.domain.quality import QualityReport, validate_batch

    schema = QualityReport.model_json_schema()
    assert schema["properties"]["batch"] == {"$ref": "#/$defs/MarketBatch"}

    report = validate_batch(batch(), now=NOW)
    restored = QualityReport.model_validate_json(report.model_dump_json())
    assert isinstance(restored.batch, MarketBatch)
    assert restored == report


def test_clean_batch_coverage():
    from dragon_radar.domain.quality import QualityReport, validate_batch

    data = batch()
    result = validate_batch(data, now=NOW)
    assert isinstance(result, QualityReport)
    assert result.batch is data
    assert set(result.model_dump()) == {"batch", "issues", "coverage"}
    assert type(result).model_validate_json(result.model_dump_json()) == result
    assert result.publishable
    assert result.coverage.expected == result.coverage.valid == 1
    assert result.coverage.missing == result.coverage.legal_excluded == 0


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("close", None, "missing_price"),
        ("close", Decimal("0"), "invalid_price"),
        ("close", Decimal("-1"), "invalid_price"),
        ("close", Decimal("NaN"), "nonfinite"),
        ("close", Decimal("Infinity"), "nonfinite"),
        ("volume", Decimal("-1"), "negative_volume"),
        ("amount", Decimal("-1"), "negative_amount"),
        ("adjustment_factor", Decimal("0"), "invalid_factor"),
        ("adjustment_factor", Decimal("NaN"), "nonfinite"),
        ("source_id", "other", "source_mismatch"),
        ("source_version", "s2", "source_mismatch"),
        ("adjustment_version", "a2", "adjustment_mismatch"),
        ("available_at", NOW + timedelta(seconds=1), "late_bar"),
        ("available_at", datetime(2026, 9, 1, tzinfo=UTC), "bar_time"),
        ("trade_date", DAYS[-1] + timedelta(days=20), "bar_date"),
    ],
)
def test_data_errors_are_archivable_issues(field, value, code):
    from dragon_radar.domain.quality import validate_batch

    data = batch(bars=(bar(**{field: value}),))
    result = validate_batch(data, now=NOW)
    assert result.batch is data
    assert not result.publishable
    assert code in {i.code for i in result.issues}
    archived = data.model_dump_json()
    assert type(data).model_validate_json(archived).model_dump_json() == archived


@pytest.mark.parametrize(
    "changes",
    [
        {"open": Decimal("10")},
        {"open": Decimal("10"), "high": Decimal("9"), "low": Decimal("8")},
        {"open": Decimal("10"), "high": Decimal("11"), "low": Decimal("10.5")},
        {"open": Decimal("10"), "high": Decimal("11"), "low": Decimal("0")},
    ],
)
def test_invalid_ohlc_is_issue(changes):
    from dragon_radar.domain.quality import validate_batch

    result = validate_batch(batch(bars=(bar(**changes),)), now=NOW)
    assert not result.publishable
    assert {i.code for i in result.issues} & {"invalid_ohlc", "invalid_price"}


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"expected_security_ids": ("A", "A")}, "duplicate_expected_id"),
        ({"expected_security_ids": ("A", "UNKNOWN")}, "unknown_id"),
        ({"bars": (bar(), bar())}, "duplicate_bar"),
        ({"bars": (bar(security_id="UNKNOWN"),)}, "unknown_id"),
        ({"security_states": (state(), state())}, "overlapping_state"),
        ({"security_states": (state(effective_to=DAYS[0]),)}, "state_interval"),
        (
            {"security_states": (state(available_at=NOW + timedelta(seconds=1)),)},
            "late_state",
        ),
        ({"security_states": (state(source_version="s2"),)}, "state_version"),
        ({"security_states": (state(pool_version="p2"),)}, "state_version"),
        ({"security_states": (state(exchange="OTHER"),)}, "state_exchange"),
        ({"calendar_version": "c2"}, "calendar_version"),
        ({"trade_date": DAYS[-1] + timedelta(days=5)}, "trade_date"),
        ({"complete_pages": False}, "incomplete_pages"),
        ({"bars": ()}, "insufficient_coverage"),
        ({"expected_security_ids": ()}, "empty_pool"),
    ],
)
def test_batch_quality_violations(changes, code):
    from dragon_radar.domain.quality import validate_batch

    result = validate_batch(batch(**changes), now=NOW)
    assert not result.publishable
    assert code in {i.code for i in result.issues}


def test_explicit_suspension_counts_without_fabricated_bar():
    from dragon_radar.domain.quality import validate_batch

    result = validate_batch(
        batch(security_states=(state(suspended=True),), bars=()), now=NOW
    )
    assert result.publishable
    assert result.coverage.legal_excluded == 1
    assert result.coverage.valid == result.coverage.missing == 0
    assert result.batch.bars == ()


def test_unsupported_security_type_is_a_quality_issue():
    from dragon_radar.domain.quality import validate_batch

    report = validate_batch(
        batch(security_states=(state(security_type="fund"),)),
        now=NOW,
    )

    assert "unsupported_security_type" in {issue.code for issue in report.issues}
    assert not report.publishable


@pytest.mark.parametrize(
    ("state_changes", "expected_code"),
    [
        ({"effective_to": DAYS[-1]}, "effective_limit_up_state"),
        ({"available_at": NOW + timedelta(seconds=1)}, "effective_limit_up_state"),
        ({"source_version": "s2"}, "effective_limit_up_state"),
        ({"pool_version": "p2"}, "effective_limit_up_state"),
        ({"suspended": True}, "suspended_limit_up_event"),
    ],
)
def test_limit_up_event_requires_usable_trading_identity(state_changes, expected_code):
    from dragon_radar.domain.models import LimitUpEvent
    from dragon_radar.domain.quality import validate_batch

    event = LimitUpEvent(
        security_id="A",
        trade_date=DAYS[-1],
        available_at=NOW - timedelta(hours=1),
        source_id="synthetic",
        source_version="s1",
        is_limit_up=True,
    )
    identity = state(**state_changes)
    payload = batch(
        security_states=(identity,),
        bars=() if identity.suspended else (bar(),),
        limit_up_events=(event,),
    )

    report = validate_batch(payload, now=NOW)

    assert expected_code in {issue.code for issue in report.issues}
    assert not report.publishable


@pytest.mark.parametrize(
    "changes",
    [
        {"suspended": None},
        {"listing_date": None},
        {"listing_date": DAYS[-1] + timedelta(days=1)},
    ],
)
def test_unknown_or_unlisted_state_cannot_satisfy_coverage(changes):
    from dragon_radar.domain.quality import validate_batch

    result = validate_batch(batch(security_states=(state(**changes),)), now=NOW)
    assert not result.publishable
    assert result.coverage.missing == 1


def test_retrospective_label_is_preserved_and_does_not_override_cutoff():
    from dragon_radar.domain.quality import validate_batch

    data = batch(
        provenance="retrospective", bars=(bar(available_at=NOW + timedelta(days=1)),)
    )
    result = validate_batch(data, now=NOW)
    assert result.batch.provenance == "retrospective"
    assert not result.publishable
    assert "late_bar" in {i.code for i in result.issues}


@pytest.mark.parametrize(
    "changes",
    [
        {"limit_status": "limited"},
        {
            "limit_status": "limited",
            "limit_up": Decimal("12"),
            "limit_down": Decimal("8"),
        },
        {
            "limit_status": "limited",
            "limit_date": DAYS[-1],
            "limit_up": Decimal("8"),
            "limit_down": Decimal("12"),
        },
        {"limit_status": "no_limit", "limit_up": Decimal("12")},
    ],
)
def test_invalid_limit_metadata_is_issue(changes):
    from dragon_radar.domain.quality import validate_batch

    result = validate_batch(batch(bars=(bar(**changes),)), now=NOW)
    assert "invalid_limits" in {i.code for i in result.issues}
    assert not result.publishable


def test_future_cutoff_is_blocked():
    from dragon_radar.domain.quality import validate_batch

    result = validate_batch(batch(as_of=NOW + timedelta(seconds=1)), now=NOW)
    assert not result.publishable
    assert "batch_time" in {i.code for i in result.issues}


@pytest.mark.parametrize("provenance", ["contemporaneous", "retrospective"])
@pytest.mark.parametrize(
    "cutoff,source_time",
    [
        (NOW - timedelta(seconds=2), NOW - timedelta(seconds=1)),
        (NOW, NOW + timedelta(seconds=1)),
    ],
)
def test_future_source_time_blocks_publication(cutoff, source_time, provenance):
    from dragon_radar.domain.quality import validate_batch

    data = batch(as_of=cutoff, source_time=source_time, provenance=provenance)
    result = validate_batch(data, now=NOW)
    assert result.batch is data
    assert not result.publishable
    assert "source_time" in {issue.code for issue in result.issues}


@pytest.mark.parametrize("source_time", [None, NOW - timedelta(seconds=1), NOW])
def test_source_time_missing_past_or_at_cutoff_is_valid(source_time):
    from dragon_radar.domain.quality import validate_batch

    assert validate_batch(batch(source_time=source_time), now=NOW).publishable


def test_trade_date_cutoff_uses_shanghai_not_utc_calendar_day():
    from dragon_radar.domain.quality import validate_batch

    cutoff = datetime(2026, 9, 7, 17, tzinfo=UTC)  # Sep 8 01:00 in Shanghai
    data = batch(as_of=cutoff, security_states=(state(suspended=True),), bars=())
    assert validate_batch(data, now=NOW).publishable
    too_early = batch(
        as_of=datetime(2026, 9, 7, 15, tzinfo=UTC),
        security_states=(state(suspended=True),),
        bars=(),
    )
    assert not validate_batch(too_early, now=NOW).publishable


def test_bar_availability_date_uses_shanghai():
    from dragon_radar.domain.quality import validate_batch

    data = batch(bars=(bar(available_at=datetime(2026, 9, 7, 17, tzinfo=UTC)),))
    assert validate_batch(data, now=NOW).publishable


def test_equal_limits_are_ambiguous_and_block_publication():
    from dragon_radar.domain.quality import validate_batch

    record = bar(
        limit_status="limited",
        limit_date=DAYS[-1],
        limit_up=Decimal("10"),
        limit_down=Decimal("10"),
    )
    result = validate_batch(batch(bars=(record,)), now=NOW)
    assert not result.publishable
    assert "invalid_limits" in {i.code for i in result.issues}


@pytest.mark.parametrize(
    "field",
    [
        "open",
        "high",
        "low",
        "previous_close",
        "amount",
        "volume",
        "limit_up",
        "limit_down",
    ],
)
def test_every_numeric_field_rejects_nonfinite_in_quality(field):
    from dragon_radar.domain.quality import validate_batch

    result = validate_batch(batch(bars=(bar(**{field: Decimal("NaN")}),)), now=NOW)
    assert not result.publishable
    assert "nonfinite" in {i.code for i in result.issues}


def test_valid_ohlc_and_adjacent_states():
    from dragon_radar.domain.quality import validate_batch

    data = batch(
        security_states=(
            state(effective_to=DAYS[2]),
            state(name="Renamed", effective_from=DAYS[2]),
        ),
        bars=(bar(open=Decimal("9"), low=Decimal("8"), high=Decimal("11")),),
    )
    assert validate_batch(data, now=NOW).publishable


def test_suspended_bar_and_outside_limit_close_block():
    from dragon_radar.domain.quality import validate_batch

    suspended = validate_batch(batch(security_states=(state(suspended=True),)), now=NOW)
    assert "suspended_bar" in {i.code for i in suspended.issues}
    outside = bar(
        limit_status="limited",
        limit_date=DAYS[-1],
        limit_up=Decimal("9"),
        limit_down=Decimal("8"),
    )
    assert not validate_batch(batch(bars=(outside,)), now=NOW).publishable
