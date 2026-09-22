"""Pure publication-time analysis and stock read-model contracts."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, Inexact, localcontext

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
    assert "benchmark_unavailable" in rows[0]["reasons"]


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
