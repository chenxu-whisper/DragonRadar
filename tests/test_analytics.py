import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from factories import DAYS, NOW, bar, batch, state
from pydantic import ValidationError


@pytest.mark.parametrize(
    "field", ["interval_return", "maximum_drawdown", "normalized_curve", "value"]
)
@pytest.mark.parametrize(
    "value",
    [
        0.1,
        True,
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        "NaN",
        "Infinity",
        "-Infinity",
    ],
)
def test_result_decimal_contract_rejects_inexact_or_nonfinite(field, value):
    from dragon_radar.analytics.metrics import DailyReturn, IntervalItem

    if field == "value":
        model = DailyReturn
        payload = {
            "security_id": "A",
            "basis": "explicit_previous_close",
            "value": value,
        }
    else:
        model = IntervalItem
        payload = {
            "security_id": "A",
            "interval_return": "0",
            "maximum_drawdown": "0",
            "normalized_curve": ("100",),
        }
        payload[field] = (value,) if field == "normalized_curve" else value
    with pytest.raises(ValidationError, match=field):
        model.model_validate(payload)
    with pytest.raises(ValidationError, match=field):
        model.model_validate_json(json.dumps(payload, default=str))


@pytest.mark.parametrize(
    "value", [Decimal("0.123456789123456789"), "0.123456789123456789", 0]
)
def test_result_decimal_contract_preserves_exact_values_and_json(value):
    from dragon_radar.analytics.metrics import DailyReturn, IntervalItem

    results = (
        DailyReturn(security_id="A", value=value, basis="prior_session_close"),
        IntervalItem(
            security_id="A",
            interval_return=value,
            maximum_drawdown=value,
            normalized_curve=(100, value),
        ),
    )
    expected = Decimal(value)
    assert results[0].value == expected
    assert results[1].interval_return == results[1].maximum_drawdown == expected
    assert results[1].normalized_curve == (Decimal(100), expected)
    for result in results:
        assert type(result).model_validate_json(result.model_dump_json()) == result


def test_calendar_windows_use_only_supplied_sessions():
    from dragon_radar.analytics.calendar import TradingCalendar

    calendar = TradingCalendar(batch().calendar)
    assert calendar.n_day_window(date(2026, 9, 6), 1) == DAYS[:2]
    assert calendar.n_day_window(DAYS[-1], 3) == DAYS
    assert calendar.custom_window(date(2026, 9, 5), DAYS[-1]) == DAYS[2:]
    assert calendar.custom_window(DAYS[1], DAYS[1]) == (DAYS[1],)


@pytest.mark.parametrize("n", [True, False, 0, -1, 251, 1.0, "1", None])
def test_calendar_rejects_non_integer_or_out_of_range_n(n):
    from dragon_radar.analytics.calendar import TradingCalendar

    with pytest.raises(ValueError, match="integer"):
        TradingCalendar(batch().calendar).n_day_window(DAYS[-1], n)


def test_calendar_250_offsets_requires_251_sessions():
    from dragon_radar.analytics.calendar import TradingCalendar
    from dragon_radar.domain.models import ExchangeCalendar

    sessions = tuple(date(2025, 1, 1) + timedelta(days=i * 2) for i in range(251))
    calendar = TradingCalendar(
        ExchangeCalendar(exchange="TEST", version="c1", sessions=sessions)
    )
    assert calendar.n_day_window(sessions[-1], 250) == sessions


@pytest.mark.parametrize(
    "start,end",
    [
        (DAYS[-1], DAYS[0]),
        (date(2026, 9, 5), date(2026, 9, 6)),
        (date(2025, 1, 1), date(2025, 1, 2)),
    ],
)
def test_custom_window_reversed_or_empty_invalid(start, end):
    from dragon_radar.analytics.calendar import TradingCalendar

    with pytest.raises(ValueError, match="window"):
        TradingCalendar(batch().calendar).custom_window(start, end)


def test_calendar_does_not_shift_insufficient_start():
    from dragon_radar.analytics.calendar import TradingCalendar

    with pytest.raises(ValueError, match="insufficient"):
        TradingCalendar(batch().calendar).n_day_window(DAYS[0], 1)


@pytest.mark.parametrize("i", range(100))
def test_100_independent_decimal_returns(i):
    from dragon_radar.analytics.metrics import interval_return

    # Construct an independent exact oracle from a prescribed basis-point change.
    start = Decimal(125 * (i + 1))
    expected = Decimal(i - 50) / Decimal(10000)
    end = start + start * expected
    assert interval_return((start, end)) == expected


def test_drawdown_running_peak_and_normalization():
    from dragon_radar.analytics.metrics import maximum_drawdown, normalized_curve

    prices = tuple(map(Decimal, ("100", "125", "100", "150", "90")))
    assert maximum_drawdown(prices) == Decimal("0.4")
    assert normalized_curve(prices) == tuple(
        map(Decimal, ("100", "125", "100", "150", "90"))
    )
    assert maximum_drawdown((Decimal("1"),)) == 0
    assert maximum_drawdown((Decimal("1"), Decimal("2"))) == 0
    assert normalized_curve((Decimal("20"), Decimal("30"))) == (
        Decimal("100"),
        Decimal("150"),
    )


@pytest.mark.parametrize(
    "prices",
    [
        (),
        None,
        (None,),
        (Decimal("0"),),
        (Decimal("-1"),),
        (10.0,),
        (Decimal("NaN"),),
        (Decimal("Infinity"),),
        (True,),
        (1,),
        (Decimal("1"), Decimal("0"), Decimal("2")),
    ],
)
@pytest.mark.parametrize(
    "function", ["interval_return", "maximum_drawdown", "normalized_curve"]
)
def test_metrics_validate_all_prices(function, prices):
    from dragon_radar.analytics import metrics

    with pytest.raises(ValueError, match="price"):
        getattr(metrics, function)(prices)


def test_interval_fixed_endpoints_adjusted_close_and_metadata():
    from dragon_radar.analytics.metrics import analyze_interval

    data = batch(
        bars=tuple(
            bar(trade_date=day, close=Decimal(10 + i), adjustment_factor=Decimal("2"))
            for i, day in enumerate(DAYS)
        )
    )
    result = analyze_interval(data, DAYS[0], DAYS[-1])
    assert result.sessions == DAYS
    assert result.exclusions == ()
    assert result.items[0].security_id == "A"
    assert result.items[0].interval_return == Decimal("0.3")
    assert result.items[0].normalized_curve == tuple(
        map(Decimal, ("100", "110", "120", "130"))
    )
    assert result.items[0].maximum_drawdown == 0
    assert result.provenance == "contemporaneous"
    assert result.as_of == NOW
    assert result.source_id == "synthetic"
    assert result.basis == "adjusted_close"


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"close": None}, "missing_price"),
        ({"close": Decimal("0")}, "invalid_price"),
        ({"source_id": "other"}, "source_mismatch"),
        ({"source_version": "s2"}, "source_mismatch"),
        ({"adjustment_version": "a2"}, "adjustment_mismatch"),
        ({"available_at": NOW + timedelta(seconds=1)}, "late_bar"),
        ({"currency": "USD"}, "unit_mismatch"),
    ],
)
def test_interval_excludes_bad_middle_record_without_shifting(changes, reason):
    from dragon_radar.analytics.metrics import analyze_interval

    bars = tuple(
        bar(trade_date=day, **(changes if i == 1 else {})) for i, day in enumerate(DAYS)
    )
    result = analyze_interval(batch(bars=bars), DAYS[0], DAYS[-1])
    assert result.sessions == DAYS
    assert not result.items
    assert reason in result.exclusions[0].reasons


@pytest.mark.parametrize(
    "kind", ["gap", "duplicate", "suspension", "late_state", "unknown"]
)
def test_interval_requires_every_known_state_and_session(kind):
    from dragon_radar.analytics.metrics import analyze_interval

    bars = tuple(bar(trade_date=day) for day in DAYS)
    states = (state(),)
    ids = ("A",)
    if kind == "gap":
        bars = bars[:1] + bars[2:]
    elif kind == "duplicate":
        bars += (bars[1],)
    elif kind in {"suspension", "late_state"}:
        states = (
            state(effective_to=DAYS[1]),
            state(
                effective_from=DAYS[1],
                effective_to=DAYS[2],
                **(
                    {"suspended": True}
                    if kind == "suspension"
                    else {"available_at": NOW + timedelta(seconds=1)}
                ),
            ),
            state(effective_from=DAYS[2]),
        )
    else:
        ids = ("UNKNOWN",)
    result = analyze_interval(
        batch(bars=bars, security_states=states), DAYS[0], DAYS[-1], ids
    )
    assert not result.items
    assert result.exclusions
    assert result.sessions == DAYS


def test_same_session_zero_requires_eligible_price():
    from dragon_radar.analytics.metrics import analyze_interval

    assert analyze_interval(batch(), DAYS[-1], DAYS[-1]).items[0].interval_return == 0
    assert not analyze_interval(batch(bars=()), DAYS[-1], DAYS[-1]).items


@pytest.mark.parametrize("ids", [(), ("A", "A"), ("A", "B", "C", "D", "E", "F")])
def test_comparison_rejects_empty_duplicate_or_sixth_member(ids):
    from dragon_radar.analytics.metrics import compare_interval

    with pytest.raises(ValueError, match="unique"):
        compare_interval(batch(), DAYS[0], DAYS[-1], ids)


def test_comparison_common_window_and_exclusions():
    from dragon_radar.analytics.metrics import compare_interval

    data = batch(
        expected_security_ids=("A", "B"),
        security_states=(state("A"), state("B")),
    )
    result = compare_interval(data, DAYS[0], DAYS[-1], ("A", "B"))
    assert result.sessions == DAYS
    assert tuple(item.security_id for item in result.items) == ("A",)
    assert result.exclusions[0].security_id == "B"


def test_breadth_uses_raw_reference_not_adjusted_return():
    from dragon_radar.analytics.metrics import market_breadth

    data = batch(
        bars=(
            bar(
                close=Decimal("12"),
                previous_close=Decimal("10"),
                adjustment_factor=Decimal("0.5"),
            ),
        )
    )
    result = market_breadth(data)
    assert result.advancing == 1
    assert result.declining == result.unchanged == 0
    assert result.returns[0].value == Decimal("0.2")
    assert result.returns[0].basis == "explicit_previous_close"
    assert result.coverage.expected == result.coverage.valid == 1
    assert result.basis == "raw_daily_return"
    assert result.source_time is None
    assert result.available_at == data.bars[0].available_at


def test_breadth_fallback_flags_corporate_action_caution():
    from dragon_radar.analytics.metrics import market_breadth

    result = market_breadth(batch())
    assert result.unchanged == 1
    assert result.returns[0].basis == "prior_session_close"
    assert "corporate_action_caution" in result.returns[0].warnings


def test_breadth_denominator_includes_missing_and_suspended():
    from dragon_radar.analytics.metrics import market_breadth

    data = batch(
        expected_security_ids=("A", "B", "C"),
        security_states=(state(), state("B", suspended=True), state("C")),
    )
    result = market_breadth(data)
    assert result.coverage.expected == 3
    assert (
        result.coverage.valid
        == result.coverage.legal_excluded
        == result.coverage.missing
        == 1
    )


def test_breadth_does_not_reach_over_missing_previous_session():
    from dragon_radar.analytics.metrics import market_breadth

    result = market_breadth(batch(bars=(bar(trade_date=DAYS[0]), bar())))
    assert result.coverage.valid == 0
    assert result.coverage.missing == 1


@pytest.mark.parametrize(
    "close,up,down,expected",
    [
        ("12", "12", "8", "up"),
        ("8", "12", "8", "down"),
        ("11", "12", "8", "none"),
        ("10.9999", "11", "9", "none"),
    ],
)
def test_closing_limits_only_exact_raw_price(close, up, down, expected):
    from dragon_radar.analytics.metrics import closing_limit_hit

    record = bar(
        close=Decimal(close),
        limit_status="limited",
        limit_date=DAYS[-1],
        limit_up=Decimal(up),
        limit_down=Decimal(down),
    )
    assert closing_limit_hit(record) == expected


def test_no_limit_and_unknown_are_distinct():
    from dragon_radar.analytics.metrics import closing_limit_hit

    assert closing_limit_hit(bar(close=Decimal("11"))) == "none"
    assert closing_limit_hit(bar(limit_status="unknown")) == "unknown"
    assert closing_limit_hit(bar(limit_status="limited")) == "unknown"
    assert closing_limit_hit(bar(close=None)) == "unknown"


def hit(day):
    return bar(
        trade_date=day,
        close=Decimal("12"),
        limit_status="limited",
        limit_date=day,
        limit_up=Decimal("12"),
        limit_down=Decimal("8"),
    )


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("reset", 3),
        ("gap", None),
        ("unknown_limit", None),
        ("late_state", None),
        ("suspension", 2),
        ("unbounded_history", None),
    ],
)
def test_limit_streak_resets_or_becomes_unknown(kind, expected):
    from dragon_radar.analytics.metrics import closing_limit_streak

    bars = (bar(trade_date=DAYS[0]),) + tuple(hit(day) for day in DAYS[1:])
    states = (state(),)
    if kind == "gap":
        bars = bars[:1] + bars[2:]
    elif kind == "unknown_limit":
        bars = (bar(trade_date=DAYS[0], limit_status="unknown"),) + bars[1:]
    elif kind == "unbounded_history":
        bars = tuple(hit(day) for day in DAYS)
    elif kind in {"suspension", "late_state"}:
        states = (
            state(effective_to=DAYS[1]),
            state(
                effective_from=DAYS[1],
                effective_to=DAYS[2],
                **(
                    {"suspended": True}
                    if kind == "suspension"
                    else {"available_at": NOW + timedelta(seconds=1)}
                ),
            ),
            state(effective_from=DAYS[2]),
        )
        if kind == "suspension":
            bars = bars[:1] + bars[2:]
    result = closing_limit_streak(
        batch(bars=bars, security_states=states), "A", DAYS[-1]
    )
    assert result.count == expected
    assert result.known is (expected is not None)


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"complete_pages": False}, "incomplete_pages"),
        ({"calendar_version": "other"}, "calendar_version"),
    ],
)
def test_incomplete_batch_cannot_enter_any_batch_analysis(changes, reason):
    from dragon_radar.analytics.metrics import (
        analyze_interval,
        closing_limit_streak,
        market_breadth,
    )

    data = batch(**changes)
    assert reason in analyze_interval(data, DAYS[0], DAYS[-1]).exclusions[0].reasons
    assert market_breadth(data).coverage.missing == 1
    assert closing_limit_streak(data, "A", DAYS[-1]).count is None


def test_duplicate_expected_ids_are_not_silently_counted():
    from dragon_radar.analytics.metrics import analyze_interval, market_breadth

    data = batch(expected_security_ids=("A", "A"))
    with pytest.raises(ValueError, match="unique"):
        analyze_interval(data, DAYS[0], DAYS[-1])
    assert market_breadth(data).coverage.missing == 1


def test_future_window_is_invalid_even_when_calendar_contains_it():
    from dragon_radar.analytics.metrics import analyze_interval, closing_limit_streak

    data = batch(trade_date=DAYS[1])
    with pytest.raises(ValueError, match="window"):
        analyze_interval(data, DAYS[0], DAYS[-1])
    with pytest.raises(ValueError, match="window"):
        closing_limit_streak(data, "A", DAYS[-1])
    with pytest.raises(ValueError, match="direction"):
        closing_limit_streak(batch(), "A", DAYS[-1], "sideways")


def test_breadth_first_session_without_reference_and_mismatched_currency():
    from dragon_radar.analytics.metrics import market_breadth

    first = batch(trade_date=DAYS[0], bars=(bar(trade_date=DAYS[0]),))
    assert market_breadth(first).coverage.missing == 1
    mixed = batch(bars=(bar(trade_date=DAYS[-2], currency="USD"), bar()))
    assert market_breadth(mixed).coverage.missing == 1


def test_late_retrospective_observations_do_not_enter_breadth_or_streak():
    from dragon_radar.analytics.metrics import closing_limit_streak, market_breadth

    data = batch(
        provenance="retrospective",
        bars=(
            bar(available_at=NOW + timedelta(seconds=1), previous_close=Decimal("9")),
        ),
    )
    result = market_breadth(data)
    assert result.provenance == "retrospective"
    assert result.source_time is None
    assert result.coverage.missing == 1
    assert closing_limit_streak(data, "A", DAYS[-1]).count is None


def test_five_eligible_comparisons_use_identical_windows():
    from dragon_radar.analytics.metrics import compare_interval

    ids = ("A", "B", "C", "D", "E")
    data = batch(
        expected_security_ids=ids,
        security_states=tuple(state(s) for s in ids),
        bars=tuple(bar(s, day) for s in ids for day in DAYS),
    )
    result = compare_interval(data, DAYS[0], DAYS[-1], ids)
    assert len(result.items) == 5
    assert result.sessions == DAYS
    assert not result.exclusions


def test_breadth_decline_and_down_streak():
    from dragon_radar.analytics.metrics import closing_limit_streak, market_breadth

    data = batch(
        bars=(
            bar(trade_date=DAYS[-2]),
            bar(
                close=Decimal("8"),
                previous_close=Decimal("10"),
                limit_status="limited",
                limit_date=DAYS[-1],
                limit_up=Decimal("12"),
                limit_down=Decimal("8"),
            ),
        )
    )
    assert market_breadth(data).declining == 1
    assert closing_limit_streak(data, "A", DAYS[-1], "down").count == 1


def test_explicit_source_time_is_preserved_without_inference():
    from dragon_radar.analytics.metrics import analyze_interval, market_breadth

    timestamp = NOW - timedelta(days=1)
    data = batch(source_time=timestamp)
    for result in (market_breadth(data), analyze_interval(data, DAYS[0], DAYS[-1])):
        assert result.source_time == timestamp
        assert result.as_of == NOW
        assert result.available_at == data.bars[-1].available_at


def test_decimal_context_is_fixed_and_does_not_mutate_caller():
    from decimal import ROUND_DOWN, Inexact, localcontext

    from dragon_radar.analytics.metrics import (
        analyze_interval,
        interval_return,
        maximum_drawdown,
        normalized_curve,
    )

    prices = (Decimal("3"), Decimal("1"))
    expected = Decimal("-0.6666666666666666666666666666666667")
    data = batch(
        bars=tuple(
            bar(
                trade_date=day,
                close=Decimal("3.123456789") + Decimal(i),
                adjustment_factor=Decimal("1.123456789"),
            )
            for i, day in enumerate(DAYS)
        )
    )
    baseline = analyze_interval(data, DAYS[0], DAYS[-1])
    with localcontext() as ctx:
        ctx.prec = 6
        ctx.rounding = ROUND_DOWN
        ctx.traps[Inexact] = True
        assert interval_return(prices) == expected
        assert maximum_drawdown(prices) == Decimal(
            "0.6666666666666666666666666666666667"
        )
        assert normalized_curve(prices)[-1] == Decimal(
            "33.33333333333333333333333333333333"
        )
        assert analyze_interval(data, DAYS[0], DAYS[-1]) == baseline
        assert ctx.prec == 6 and ctx.rounding == ROUND_DOWN and ctx.traps[Inexact]
