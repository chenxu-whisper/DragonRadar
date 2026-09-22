"""Pure publication-time analysis and stock read-model contracts."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, Inexact, localcontext

import pytest
from factories import bar, batch, state


def _sessions(count: int = 21) -> tuple[date, ...]:
    sessions = []
    current = date(2026, 9, 8)
    while len(sessions) < count:
        if current.weekday() < 5:
            sessions.append(current)
        current -= timedelta(days=1)
    return tuple(reversed(sessions))


def _analysis_batch():
    from dragon_radar.domain.models import ExchangeCalendar

    sessions = _sessions()
    stock_ids = ("A", "C", "D", "E")
    states = tuple(
        state(
            security_id,
            name=f"Synthetic {security_id}",
            effective_from=sessions[0],
            available_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        for security_id in stock_ids
    ) + (
        state(
            "B000300",
            name="Synthetic Benchmark",
            security_type="index",
            effective_from=sessions[0],
            available_at=datetime(2026, 8, 1, tzinfo=UTC),
        ),
    )
    bars = []
    for offset, day in enumerate(sessions):
        prices = {
            "A": Decimal("110") if offset == 20 else Decimal("100"),
            "C": None if offset == 0 else Decimal("110"),
            "D": (
                Decimal("100")
                if offset == 0
                else Decimal("120")
                if offset == 1
                else Decimal("90")
            ),
            "E": Decimal("110") if offset == 20 else Decimal("100"),
            "B000300": Decimal("102") if offset == 20 else Decimal("100"),
        }
        for security_id, close in prices.items():
            ohlc = (
                {"open": close, "high": close, "low": close}
                if security_id == "A" and close is not None
                else {}
            )
            bars.append(
                bar(
                    security_id,
                    trade_date=day,
                    close=close,
                    adjustment_factor=(
                        Decimal("2")
                        if security_id == "E" and offset == 20
                        else Decimal("1")
                    ),
                    amount=(
                        Decimal("1200")
                        if security_id == "A" and offset == 20
                        else Decimal("1000")
                    ),
                    **ohlc,
                )
            )
    return batch(
        trade_date=sessions[-1],
        calendar=ExchangeCalendar(exchange="TEST", version="c1", sessions=sessions),
        expected_security_ids=stock_ids,
        security_states=states,
        bars=tuple(bars),
    )


def test_decimal34_analysis_primitives_cover_frozen_examples():
    from dragon_radar.analytics import metrics

    assert metrics.interval_return((Decimal("100"), Decimal("110"))) == Decimal("0.1")
    assert metrics.maximum_drawdown(
        (Decimal("100"), Decimal("120"), Decimal("90"))
    ) == Decimal("0.25")
    assert metrics.interval_return((Decimal("100"),)) == Decimal("0")
    assert metrics.drawdown_curve((Decimal("100"), Decimal("120"), Decimal("90"))) == (
        Decimal("0"),
        Decimal("0"),
        Decimal("0.25"),
    )
    assert metrics.excess_return(Decimal("0.1"), Decimal("0.02")) == Decimal("0.08")

    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        assert metrics.excess_return(
            Decimal("0.1234567891234567891234567891234567"),
            Decimal("0.02"),
        ) == Decimal("0.1034567891234567891234567891234567")


def test_amount_change_uses_current_against_prior_five_session_average():
    from dragon_radar.readmodels.analysis import _amount_change

    amounts = ("100", "10", "20", "30", "40", "50", "90")
    bars = tuple(bar(amount=Decimal(amount)) for amount in amounts)

    assert _amount_change(bars) == (Decimal("2"), None)


@pytest.mark.parametrize(
    "amounts",
    (
        ("1", "1", "1", "1", "1"),
        ("1", "1", None, "1", "1", "1"),
        ("1", "1", "NaN", "1", "1", "1"),
        ("1", "1", "-1", "1", "1", "1"),
        ("0", "0", "0", "0", "0", "1"),
    ),
)
def test_amount_change_is_unavailable_without_six_valid_positions_or_nonzero_basis(
    amounts,
):
    from dragon_radar.readmodels.analysis import _amount_change

    bars = tuple(
        bar(amount=None if amount is None else Decimal(amount)) for amount in amounts
    )

    assert _amount_change(bars) == (None, "amount_change_unavailable")


@pytest.mark.parametrize("invalid_amount", (Decimal("-1"), Decimal("NaN")))
def test_analysis_marks_invalid_required_amount_unavailable(invalid_amount):
    from dragon_radar.readmodels.analysis import materialize_analysis

    source = _analysis_batch()
    invalid_day = _sessions()[-2]
    bars = tuple(
        item.model_copy(update={"amount": invalid_amount})
        if item.security_id == "A" and item.trade_date == invalid_day
        else item
        for item in source.bars
    )

    result = materialize_analysis(
        source.model_copy(update={"bars": bars}),
        windows=(20,),
    )
    rows = result["windows"]["20"]["adjustments"]["raw"]["ranking"]
    row = next(item for item in rows if item["security_id"] == "A")

    assert row["amount_change_ratio"] is None
    assert "amount_change_unavailable" in row["reasons"]


def test_analysis_uses_exact_n_plus_one_positions_and_never_shifts_gaps():
    from dragon_radar.readmodels.analysis import materialize_analysis

    result = materialize_analysis(_analysis_batch(), windows=(20,))
    window = result["windows"]["20"]
    assert len(window["sessions"]) == 21
    assert window["sessions"][0] == _sessions()[0].isoformat()
    adjusted = window["adjustments"]["adjusted"]
    rows = {item["security_id"]: item for item in adjusted["ranking"]}
    assert rows["A"]["return_ratio"] == Decimal("0.1")
    assert rows["A"]["excess_return_ratio"] == Decimal("0.08")
    assert rows["D"]["max_drawdown_ratio"] == Decimal("0.25")
    assert rows["A"]["amount_change_ratio"] == Decimal("0.2")
    assert rows["C"]["return_ratio"] is None
    assert "missing_price" in rows["C"]["reasons"]
    assert adjusted["curves"]["C"]["normalized"] == [None] * 21
    assert [row["security_id"] for row in adjusted["ranking"]] == [
        "E",
        "A",
        "D",
        "C",
    ]
    assert [row["rank"] for row in adjusted["ranking"]] == [1, 2, 3, None]


def test_analysis_gives_equal_returns_equal_ranks_with_stable_security_order():
    from dragon_radar.readmodels.analysis import materialize_analysis

    result = materialize_analysis(_analysis_batch(), windows=(20,))
    rows = result["windows"]["20"]["adjustments"]["raw"]["ranking"]

    assert [row["security_id"] for row in rows] == ["A", "E", "D", "C"]
    assert [row["rank"] for row in rows] == [1, 1, 3, None]


def test_analysis_ranking_excludes_effective_indexes_and_benchmark():
    from dragon_radar.readmodels.analysis import materialize_analysis

    source = _analysis_batch()
    index_state = state(
        "INDEX",
        security_type="index",
        effective_from=_sessions()[0],
        available_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    index_bars = tuple(
        bar("INDEX", trade_date=day, close=Decimal("100")) for day in _sessions()
    )
    source = source.model_copy(
        update={
            "expected_security_ids": (
                *source.expected_security_ids,
                "INDEX",
                "B000300",
            ),
            "security_states": (*source.security_states, index_state),
            "bars": (*source.bars, *index_bars),
        }
    )

    result = materialize_analysis(source, windows=(20,))

    for adjustment in ("raw", "adjusted"):
        ranking = result["windows"]["20"]["adjustments"][adjustment]["ranking"]
        assert {row["security_id"] for row in ranking} == {"A", "C", "D", "E"}


@pytest.mark.parametrize(
    ("topic_changes", "expected_reason"),
    (
        (None, "topic_batch_unavailable"),
        ({"trade_date": _sessions()[-2]}, "topic_binding_mismatch"),
        ({"exchange": "OTHER"}, "topic_binding_mismatch"),
    ),
)
def test_analysis_marks_missing_or_mismatched_topic_batch_unavailable(
    topic_changes,
    expected_reason,
):
    from topic_factories import topic_batch

    from dragon_radar.readmodels.analysis import materialize_analysis

    topics = (
        None
        if topic_changes is None
        else topic_batch().model_copy(update=topic_changes)
    )

    result = materialize_analysis(_analysis_batch(), topics, windows=(20,))
    rows = result["windows"]["20"]["adjustments"]["raw"]["ranking"]

    assert all(row["topics"] == [] for row in rows)
    assert all(row["topic_status"] == "unavailable" for row in rows)
    assert all(row["topic_reasons"] == [expected_reason] for row in rows)


@pytest.mark.parametrize(
    "coverage_change",
    ("missing", "duplicate", "unavailable", "count_mismatch", "member_mismatch"),
)
def test_analysis_hides_topic_memberships_without_exact_complete_coverage(
    coverage_change,
):
    from topic_factories import topic_batch, topic_membership

    from dragon_radar.readmodels.analysis import materialize_analysis

    topics = topic_batch()
    current_cells = tuple(
        cell for cell in topics.coverage_cells if cell.trade_date == topics.trade_date
    )
    if coverage_change == "missing":
        cells = tuple(
            cell
            for cell in topics.coverage_cells
            if cell.trade_date != topics.trade_date
        )
    elif coverage_change == "duplicate":
        cells = (*topics.coverage_cells, current_cells[0])
    elif coverage_change == "unavailable":
        cells = tuple(
            cell.model_copy(
                update={
                    "status": "unavailable",
                    "received_member_count": 0,
                    "reason": "provider_gap",
                }
            )
            if cell.trade_date == topics.trade_date
            else cell
            for cell in topics.coverage_cells
        )
    elif coverage_change == "count_mismatch":
        cells = tuple(
            cell.model_copy(update={"expected_member_count": 2})
            if cell.trade_date == topics.trade_date
            else cell
            for cell in topics.coverage_cells
        )
    else:
        cells = topics.coverage_cells
        topics = topics.model_copy(
            update={
                "memberships": (
                    *topics.memberships,
                    type(topics.memberships[0]).model_validate(
                        topic_membership(security_id="C")
                    ),
                )
            }
        )
    topics = topics.model_copy(update={"coverage_cells": cells})

    result = materialize_analysis(_analysis_batch(), topics, windows=(20,))
    rows = result["windows"]["20"]["adjustments"]["raw"]["ranking"]

    assert all(row["topics"] == [] for row in rows)
    assert all(row["topic_status"] == "unavailable" for row in rows)
    assert all(row["topic_reasons"] == ["topic_coverage_unavailable"] for row in rows)
    stock_a = next(row for row in rows if row["security_id"] == "A")
    assert stock_a["return_ratio"] == Decimal("0.1")
    assert "topic_coverage_unavailable" not in stock_a["reasons"]


def test_analysis_projects_memberships_only_with_exact_complete_coverage():
    from topic_factories import topic_batch

    from dragon_radar.readmodels.analysis import materialize_analysis

    result = materialize_analysis(
        _analysis_batch(),
        topic_batch(),
        windows=(20,),
    )
    rows = result["windows"]["20"]["adjustments"]["raw"]["ranking"]
    by_id = {row["security_id"]: row for row in rows}

    assert by_id["A"]["topics"] == [
        {"topic_id": "topic-a", "name": "Synthetic Topic A"}
    ]
    assert all(row["topic_status"] == "available" for row in rows)
    assert all(row["topic_reasons"] == [] for row in rows)


def test_analysis_default_windows_report_short_history_independently():
    from dragon_radar.readmodels.analysis import materialize_analysis

    result = materialize_analysis(_analysis_batch())

    assert result["windows"]["20"]["available"] is True
    assert result["windows"]["20"]["reason"] is None
    assert result["windows"]["20"]["sessions"] == [
        day.isoformat() for day in _sessions()
    ]
    for offsets in (60, 120):
        assert result["windows"][str(offsets)] == {
            "requested_offsets": offsets,
            "available": False,
            "reason": "insufficient_calendar_history",
        }


def test_analysis_preserves_stock_return_when_benchmark_is_unavailable():
    from dragon_radar.readmodels.analysis import materialize_analysis

    result = materialize_analysis(
        _analysis_batch(), windows=(20,), benchmark_id="MISSING"
    )
    rows = result["windows"]["20"]["adjustments"]["raw"]["ranking"]
    assert rows[0]["return_ratio"] is not None
    assert rows[0]["excess_return_ratio"] is None
    assert "benchmark_identity_invalid" in rows[0]["reasons"]


def test_analysis_keeps_common_stock_benchmark_in_ranking_but_rejects_its_identity():
    from dragon_radar.readmodels.analysis import materialize_analysis

    result = materialize_analysis(
        _analysis_batch(),
        windows=(20,),
        benchmark_id="A",
    )

    for adjustment in ("raw", "adjusted"):
        projected = result["windows"]["20"]["adjustments"][adjustment]
        assert {row["security_id"] for row in projected["ranking"]} == {
            "A",
            "C",
            "D",
            "E",
        }
        assert projected["benchmark_return_ratio"] is None
        assert projected["benchmark_reasons"] == ["benchmark_identity_invalid"]
        assert all(
            "benchmark_identity_invalid" in row["reasons"]
            for row in projected["ranking"]
        )


def test_analysis_exposes_only_supported_adjustment_semantics_and_is_storable():
    from dragon_radar.readmodels.analysis import materialize_analysis

    result = materialize_analysis(_analysis_batch(), windows=(20,))
    assert result["schema"] == "dragon-radar.analysis"
    assert result["schema_version"] == 1
    assert result["adjustments"] == {
        "raw": {"available": True, "price_basis": "raw_close", "reason": None},
        "adjusted": {
            "available": True,
            "price_basis": "close_times_adjustment_factor",
            "reason": None,
        },
        "qfq": {
            "available": False,
            "price_basis": None,
            "reason": "qfq_semantics_unproven_from_generic_adjustment_factor",
        },
        "hfq": {
            "available": False,
            "price_basis": None,
            "reason": "hfq_semantics_unproven_from_generic_adjustment_factor",
        },
    }
    window = result["windows"]["20"]["adjustments"]
    assert window["raw"]["ranking"][0]["security_id"] == "A"
    assert window["adjusted"]["ranking"][0]["security_id"] == "E"
    assert "qfq" not in window
    assert "hfq" not in window
    json.dumps(result, default=str, allow_nan=False)


def test_stock_materialization_preserves_real_ohlc_and_selects_chart_type():
    from dragon_radar.readmodels.stock import materialize_stocks

    result = materialize_stocks(_analysis_batch())
    assert result["schema"] == "dragon-radar.stocks"
    assert result["schema_version"] == 1

    full = result["stocks"]["A"]
    assert full["quote"]["last"] == Decimal("110")
    assert full["quote"]["amount"] == Decimal("1200")
    assert full["chart_type"] == "candlestick"
    assert full["history"][-1]["open"] == Decimal("110")

    close_only = result["stocks"]["D"]
    assert close_only["chart_type"] == "line"
    assert close_only["history"][-1]["close"] == Decimal("90")
    assert close_only["history"][-1]["open"] is None
    assert close_only["history"][-1]["high"] is None
    assert close_only["history"][-1]["low"] is None
    json.dumps(result, default=str, allow_nan=False)


def test_stock_materialization_preserves_per_bar_units_and_nulls_missing_bar_units():
    from dragon_radar.readmodels.stock import materialize_stocks

    source = _analysis_batch()
    changed_day = _sessions()[-2]
    bars = tuple(
        item.model_copy(update={"currency": "USD", "volume_unit": "lot"})
        if item.security_id == "A" and item.trade_date == changed_day
        else item
        for item in source.bars
        if not (item.security_id == "D" and item.trade_date == changed_day)
    )

    stocks = materialize_stocks(source.model_copy(update={"bars": bars}))["stocks"]

    assert {
        key: stocks["A"]["history"][-2][key]
        for key in ("currency", "price_unit", "volume_unit", "amount_unit")
    } == {
        "currency": "USD",
        "price_unit": "currency/share",
        "volume_unit": "lot",
        "amount_unit": "currency",
    }
    assert {
        key: stocks["A"]["quote"][key]
        for key in ("currency", "price_unit", "volume_unit", "amount_unit")
    } == {
        "currency": "CNY",
        "price_unit": "currency/share",
        "volume_unit": "share",
        "amount_unit": "currency",
    }
    assert all(
        stocks["D"]["history"][-2][key] is None
        for key in ("currency", "price_unit", "volume_unit", "amount_unit")
    )


@pytest.mark.parametrize(
    ("previous_change", "expected_reason"),
    (
        (None, "missing_price"),
        ({"source_id": "other"}, "source_mismatch"),
        ({"currency": "USD"}, "unit_mismatch"),
    ),
)
def test_stock_quote_rejects_unusable_previous_session_observation(
    previous_change,
    expected_reason,
):
    from dragon_radar.readmodels.stock import materialize_stocks

    source = _analysis_batch()
    previous_day = _sessions()[-2]
    bars = []
    for item in source.bars:
        if item.security_id != "A" or item.trade_date != previous_day:
            bars.append(item)
        elif previous_change is not None:
            bars.append(item.model_copy(update=previous_change))
    source = source.model_copy(update={"bars": tuple(bars)})

    quote = materialize_stocks(source)["stocks"]["A"]["quote"]

    assert quote["previous_close"] is None
    assert quote["change_ratio"] is None
    assert expected_reason in quote["reasons"]


def test_market_projection_caches_security_scoped_batches_and_observations():
    from dragon_radar.readmodels.projection import MarketProjection

    source = _analysis_batch()
    projection = MarketProjection(source)

    first_batch = projection.security_batch("A")
    second_batch = projection.security_batch("A")
    first_observation = projection.observation("A", source.trade_date)
    second_observation = projection.observation("A", source.trade_date)

    assert first_batch is second_batch
    assert {item.security_id for item in first_batch.security_states} == {"A"}
    assert len(first_batch.security_states) < len(source.security_states)
    assert first_observation is second_observation
