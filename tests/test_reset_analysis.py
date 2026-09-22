"""Pure publication-time analysis and stock read-model contracts."""

import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_DOWN, Decimal, Inexact, localcontext
from uuid import uuid4

import pytest
from factories import bar, batch, state
from httpx import ASGITransport, AsyncClient


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
    projected = result["windows"]["20"]["adjustments"]["raw"]
    exclusion = next(
        item for item in projected["exclusions"] if item["security_id"] == "A"
    )

    assert all(item["security_id"] != "A" for item in projected["ranking"])
    assert "amount_change_unavailable" in exclusion["reasons"]


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
    assert "C" not in rows
    assert adjusted["exclusions"] == [
        {"security_id": "C", "reasons": ["missing_price"]}
    ]
    assert adjusted["curves"]["C"]["normalized"] == [None] * 21
    assert adjusted["curves"]["C"]["drawdown"] == [None] * 21
    assert [row["security_id"] for row in adjusted["ranking"]] == ["E", "A", "D"]
    assert [row["rank"] for row in adjusted["ranking"]] == [1, 2, 3]


def test_analysis_keeps_fixed_baseline_curves_across_middle_gap():
    from dragon_radar.readmodels.analysis import materialize_analysis

    source = _analysis_batch()
    gap_day = _sessions()[2]
    source = source.model_copy(
        update={
            "bars": tuple(
                item
                for item in source.bars
                if not (item.security_id == "D" and item.trade_date == gap_day)
            )
        }
    )

    projected = materialize_analysis(source, windows=(20,))["windows"]["20"][
        "adjustments"
    ]["raw"]
    curve = projected["curves"]["D"]

    assert all(item["security_id"] != "D" for item in projected["ranking"])
    assert {"security_id": "D", "reasons": ["missing_price"]} in projected["exclusions"]
    assert curve["normalized"][:4] == [
        Decimal("100"),
        Decimal("120"),
        None,
        Decimal("90"),
    ]
    assert curve["drawdown"][:4] == [
        Decimal("0"),
        Decimal("0"),
        None,
        Decimal("0.25"),
    ]
    assert "missing_price" in curve["reasons"]


def test_analysis_gives_equal_returns_equal_ranks_with_stable_security_order():
    from dragon_radar.readmodels.analysis import materialize_analysis

    result = materialize_analysis(_analysis_batch(), windows=(20,))
    rows = result["windows"]["20"]["adjustments"]["raw"]["ranking"]

    assert [row["security_id"] for row in rows] == ["A", "E", "D"]
    assert [row["rank"] for row in rows] == [1, 1, 3]


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
        projected = result["windows"]["20"]["adjustments"][adjustment]
        assert {row["security_id"] for row in projected["ranking"]} == {
            "A",
            "D",
            "E",
        }
        assert [item["security_id"] for item in projected["exclusions"]] == ["C"]


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
            "D",
            "E",
        }
        assert [item["security_id"] for item in projected["exclusions"]] == ["C"]
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


def _limit_up_event(source, **changes):
    from dragon_radar.domain.models import LimitUpEvent

    return LimitUpEvent(
        **{
            "security_id": "A",
            "trade_date": source.trade_date,
            "available_at": source.as_of - timedelta(minutes=1),
            "source_id": source.source_id,
            "source_version": source.source_version,
            "is_limit_up": True,
            "first_seal_time": time(9, 35),
            "last_seal_time": time(14, 55),
            "break_count": 2,
            "seal_amount": Decimal("123456.78"),
            "seal_amount_unit": "currency",
            "continuous_boards": 3,
            **changes,
        }
    )


def test_stock_materialization_projects_unique_current_trusted_seal():
    from dragon_radar.readmodels.stock import materialize_stocks

    source = _analysis_batch()
    source = source.model_copy(
        update={
            "limit_up_events": (_limit_up_event(source, touched_limit=True),),
        }
    )

    assert materialize_stocks(source)["stocks"]["A"]["seal"] == {
        "is_limit_up": True,
        "touched_limit": True,
        "first_seal_time": "09:35:00",
        "last_seal_time": "14:55:00",
        "break_count": 2,
        "seal_amount": Decimal("123456.78"),
        "seal_amount_unit": "currency",
        "continuous_boards": 3,
        "reason": None,
    }


@pytest.mark.parametrize("kind", ("missing", "late", "source_mismatch", "conflict"))
def test_stock_materialization_nulls_untrusted_or_conflicting_seal(kind):
    from dragon_radar.readmodels.stock import materialize_stocks

    source = _analysis_batch()
    event = _limit_up_event(source)
    if kind == "missing":
        events = ()
    elif kind == "late":
        events = (
            event.model_copy(update={"available_at": source.as_of + timedelta(1)}),
        )
    elif kind == "source_mismatch":
        events = (event.model_copy(update={"source_id": "other"}),)
    else:
        events = (event, event.model_copy(update={"break_count": 4}))
    source = source.model_copy(update={"limit_up_events": events})

    seal = materialize_stocks(source)["stocks"]["A"]["seal"]

    assert all(
        seal[field] is None
        for field in (
            "is_limit_up",
            "touched_limit",
            "first_seal_time",
            "last_seal_time",
            "break_count",
            "seal_amount",
            "seal_amount_unit",
            "continuous_boards",
        )
    )
    assert seal["reason"] == (
        "seal_event_conflict" if kind == "conflict" else "seal_event_missing"
    )


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


def test_stock_quote_reports_missing_baseline_on_first_calendar_session():
    from dragon_radar.readmodels.stock import materialize_stocks

    source = _analysis_batch()
    source = source.model_copy(
        update={
            "calendar": source.calendar.model_copy(
                update={"sessions": (source.trade_date,)}
            ),
            "bars": tuple(
                item for item in source.bars if item.trade_date == source.trade_date
            ),
        }
    )

    quote = materialize_stocks(source)["stocks"]["A"]["quote"]

    assert quote["previous_close"] is None
    assert quote["change_ratio"] is None
    assert "missing_previous_close" in quote["reasons"]


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


def _published_analysis_snapshot(
    *,
    role_ready: bool = False,
    role_status: str | None = None,
    source=None,
):
    from dragon_radar.publication.models import (
        Publication,
        PublicationResult,
        PublishedSnapshot,
    )
    from dragon_radar.publication.service import _stored_json
    from dragon_radar.readmodels.analysis import materialize_analysis
    from dragon_radar.readmodels.market import materialize_market
    from dragon_radar.readmodels.stock import materialize_stocks
    from dragon_radar.storage.models import Snapshot

    source = source or _analysis_batch()
    publication_id = uuid4()
    market_snapshot_id = uuid4()
    market_payload = materialize_market(source, None)
    market_payload["analysis"] = materialize_analysis(source, None)
    market_payload["stocks"] = materialize_stocks(source)
    role_payload = {
        "role_run_id": str(uuid4()),
        "trade_date": source.trade_date.isoformat(),
        "rule_version": "role-v1",
        "benchmark_id": "B000300",
        "topics": [
            {
                "topic_id": "topic-a",
                "member_ids": ["A"],
                "statuses": [
                    {
                        "role": "trend",
                        "capability": "ready",
                        "status": "ready",
                        "expected": 1,
                        "valid": 1,
                        "legal_excluded": 0,
                        "missing": 0,
                        "reasons": [],
                    }
                ],
                "results": [
                    {
                        "security_id": "A",
                        "role": "trend",
                        "internal_status": "candidate",
                        "tie_rank": 1,
                        "reasons": [],
                    }
                ],
                "ladder": [
                    {
                        "security_id": "A",
                        "trade_date": source.trade_date.isoformat(),
                        "closing_limit_streak": 2,
                    }
                ],
            }
        ],
    }
    effective_role_status = role_status or ("ready" if role_ready else "disabled")
    publication = Publication(
        id=publication_id,
        trade_date=source.trade_date,
        version=1,
        supersedes=None,
        status="published",
        market_snapshot_id=market_snapshot_id,
        topic_snapshot_id=None,
        news_input_id=None,
        regulation_input_id=None,
        role_input_id=uuid4() if effective_role_status == "ready" else None,
        domains={
            "market": "ready",
            "topic": "disabled",
            "news": "disabled",
            "regulation": "disabled",
            "role": effective_role_status,
        },
        coverage={},
        quality_flags=(),
        command_json="{}",
        published_at=source.as_of,
    )
    coverage = {
        "expected": 4,
        "valid": 4,
        "legal_excluded": 0,
        "missing": 0,
    }
    snapshot = Snapshot(
        id=market_snapshot_id,
        batch_id=uuid4(),
        revision=1,
        supersedes=None,
        published_at=source.as_of,
        manifest={"rule_version": "market-v1"},
        batch=source,
        quality_report={"issues": [], "coverage": coverage},
    )
    results = (
        PublicationResult(
            publication_id=publication_id,
            domain="market",
            input_id=market_snapshot_id,
            status="ready",
            payload=_stored_json(market_payload),
            coverage=coverage,
            quality_flags=(),
        ),
        PublicationResult(
            publication_id=publication_id,
            domain="role",
            input_id=publication.role_input_id,
            status=effective_role_status,
            payload=role_payload if effective_role_status == "ready" else {},
            coverage={},
            quality_flags=(),
        ),
        PublicationResult(
            publication_id=publication_id,
            domain="regulation",
            input_id=None,
            status="disabled",
            payload={},
            coverage={},
            quality_flags=(),
        ),
    )
    return PublishedSnapshot(publication, snapshot, results)


class _PublishedRepository:
    database = object()

    def __init__(self, snapshot=None, *additional):
        self.snapshots = tuple(
            item for item in (snapshot, *additional) if item is not None
        )
        self.accesses = 0
        self.list_calls = []
        self.get_calls = []

    def get_snapshot(self, snapshot_id):
        self.accesses += 1
        self.get_calls.append(snapshot_id)
        return next(
            (item for item in self.snapshots if snapshot_id == item.id),
            None,
        )

    def list_snapshots(self, *, trade_date=None, through_date=None, limit=50, **kwargs):
        self.accesses += 1
        self.list_calls.append(
            {
                "trade_date": trade_date,
                "through_date": through_date,
                "limit": limit,
                **kwargs,
            }
        )
        selected = [
            item
            for item in self.snapshots
            if (trade_date is None or trade_date == item.trade_date)
            and (through_date is None or item.trade_date <= through_date)
        ]
        return sorted(selected, key=lambda item: item.trade_date, reverse=True)[:limit]


def test_publication_market_payload_adds_versioned_analysis_and_stocks():
    from dragon_radar.publication.service import PublicationService

    source = _analysis_batch()
    result = PublicationService._market_materialization(
        (
            None,
            {"quality_report": {"issues": []}},
            source,
        ),
    )

    assert result.payload["schema"] == "dragon-radar.market-read-model"
    assert result.payload["analysis"]["schema"] == "dragon-radar.analysis"
    assert result.payload["analysis"]["schema_version"] == 1
    assert result.payload["stocks"]["schema"] == "dragon-radar.stocks"
    assert result.payload["stocks"]["schema_version"] == 1
    assert (
        result.payload["analysis"]["windows"]["20"]["adjustments"]["raw"]["ranking"][0][
            "return_ratio"
        ]
        == "0.1"
    )
    assert result.payload["stocks"]["stocks"]["A"]["quote"]["last"] == "110"


@pytest.mark.anyio
async def test_published_analysis_gets_only_project_archived_jsonb(monkeypatch):
    from dragon_radar.http.app import create_workbench_app

    monkeypatch.setattr(
        "dragon_radar.http.routes.stock.RESET_APPROVED_RULE_VERSIONS",
        frozenset({"role-v1"}),
    )
    snapshot = _published_analysis_snapshot(role_ready=True)
    repository = _PublishedRepository(snapshot)

    def recomputation_is_forbidden(*_args, **_kwargs):
        raise AssertionError("published read model was recomputed")

    monkeypatch.setattr(
        "dragon_radar.readmodels.analysis.materialize_analysis",
        recomputation_is_forbidden,
    )
    monkeypatch.setattr(
        "dragon_radar.readmodels.stock.materialize_stocks",
        recomputation_is_forbidden,
    )
    app = create_workbench_app(repository=repository)
    start, end = _sessions()[0], _sessions()[-1]
    common = {"snapshot_id": str(snapshot.id), "start": str(start), "end": str(end)}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        stock_response = await client.get(
            "/api/stock/A", params={"snapshot_id": str(snapshot.id)}
        )
        raw_kline = await client.get(
            "/api/stock/A/kline", params={**common, "adj": "raw"}
        )
        qfq_kline = await client.get("/api/stock/A/kline", params=common)
        raw_rank = await client.get(
            "/api/rank/range",
            params={**common, "adj": "raw", "benchmark": "B000300"},
        )
        qfq_rank = await client.get("/api/rank/range", params=common)
        curves = await client.get(
            "/api/compare/curves",
            params={**common, "symbols": "A,D", "adj": "raw"},
        )
        qfq_curves = await client.get(
            "/api/compare/curves",
            params={**common, "symbols": "A,D"},
        )
        uncomputed = await client.get(
            "/api/rank/range",
            params={
                **common,
                "start": str(_sessions()[1]),
                "adj": "raw",
            },
        )
        adjusted = await client.get(
            "/api/rank/range",
            params={
                **common,
                "start": str(start - timedelta(days=1)),
                "end": str(end + timedelta(days=1)),
                "adj": "raw",
            },
        )
        unpinned_adjusted = await client.get(
            "/api/rank/range",
            params={
                "start": str(start - timedelta(days=1)),
                "end": str(end + timedelta(days=1)),
                "adj": "raw",
            },
        )

    assert stock_response.status_code == 200
    stock = stock_response.json()["data"]
    assert set(stock) >= {
        "security_id",
        "name",
        "quote",
        "roles",
        "seal",
        "regulation",
    }
    assert stock["security_id"] == "A"
    assert stock["name"] == "Synthetic A"
    assert stock["roles"] == [
        {
            "role_type": "trend_leader",
            "evidence_status": "candidate",
            "sector_id": "topic-a",
        }
    ]
    assert set(stock["roles"][0]) == {
        "role_type",
        "evidence_status",
        "sector_id",
    }
    assert "role_reason" not in stock
    assert stock["seal"] == {
        "is_limit_up": None,
        "touched_limit": None,
        "first_seal_time": None,
        "last_seal_time": None,
        "break_count": None,
        "seal_amount": None,
        "seal_amount_unit": None,
        "continuous_boards": None,
        "reason": "seal_event_missing",
    }
    assert stock["regulation"] == {
        "active_events": None,
        "reason": "regulation_disabled",
    }

    assert raw_kline.status_code == 200
    raw_kline_data = raw_kline.json()["data"]
    assert raw_kline_data["adjustment"] == "raw"
    assert raw_kline_data["adjusted_start"] == str(start)
    assert raw_kline_data["adjusted_end"] == str(end)
    assert raw_kline_data["points"][-1]["open"] == 110
    assert qfq_kline.status_code == 200
    assert qfq_kline.json()["data"] is None
    assert qfq_kline.json()["reason"] == (
        "qfq_semantics_unproven_from_generic_adjustment_factor"
    )
    assert "adjustment_unavailable" in qfq_kline.json()["quality_flags"]

    assert raw_rank.status_code == 200
    rank_body = json.loads(raw_rank.content, parse_float=Decimal)
    assert rank_body["data"]["window_days"] == 20
    assert rank_body["data"]["adjusted_start"] == str(start)
    assert rank_body["data"]["adjusted_end"] == str(end)
    assert rank_body["data"]["benchmark_return"] == Decimal("0.02")
    assert rank_body["basis"]["benchmark"] == "B000300"
    assert rank_body["rule_version"] == "analysis-v1-decimal34"
    assert rank_body["data"]["exclusions"] == [
        {"security_id": "C", "reasons": ["missing_price"]}
    ]
    assert rank_body["coverage"] == {
        "expected": 4,
        "valid": 3,
        "excluded": 0,
        "missing": 1,
    }
    by_id = {item["security_id"]: item for item in rank_body["data"]["ranking"]}
    assert by_id["A"]["return_ratio"] == Decimal("0.1")
    assert by_id["A"]["excess_return_ratio"] == Decimal("0.08")
    assert by_id["D"]["max_drawdown_ratio"] == Decimal("0.25")
    assert b'"return_ratio":0.1' in raw_rank.content
    assert qfq_rank.status_code == 200
    assert qfq_rank.json()["data"] is None
    assert qfq_rank.json()["reason"] == (
        "qfq_semantics_unproven_from_generic_adjustment_factor"
    )

    assert curves.status_code == 200
    curve_data = curves.json()["data"]
    assert curve_data["adjusted_start"] == str(start)
    assert curve_data["adjusted_end"] == str(end)
    assert [item["security_id"] for item in curve_data["curves"]] == ["A", "D"]
    assert curve_data["curves"][0]["points"][-1] == {
        "trade_date": str(end),
        "normalized": 110,
        "drawdown": 0,
    }
    assert curve_data["curves"][1]["points"][-1]["drawdown"] == 0.25
    assert curve_data["exclusions"] == []
    assert qfq_curves.status_code == 200
    assert qfq_curves.json()["data"] is None
    assert "adjustment_unavailable" in qfq_curves.json()["quality_flags"]
    assert uncomputed.status_code == 200
    assert uncomputed.json()["data"] is None
    assert uncomputed.json()["reason"] == "analysis_not_precomputed"
    assert "analysis_not_precomputed" in uncomputed.json()["quality_flags"]
    assert adjusted.status_code == 200
    assert adjusted.json()["data"]["adjusted_start"] == str(start)
    assert adjusted.json()["data"]["adjusted_end"] == str(end)
    assert unpinned_adjusted.status_code == 200
    assert unpinned_adjusted.json()["snapshot_id"] == str(snapshot.id)
    assert unpinned_adjusted.json()["data"]["adjusted_end"] == str(end)


@pytest.mark.anyio
async def test_rank_keeps_return_valid_rows_with_null_sort_value_last():
    from dragon_radar.http.app import create_workbench_app

    source = _analysis_batch()
    current_day = source.trade_date
    source = source.model_copy(
        update={
            "bars": tuple(
                item.model_copy(update={"amount": None})
                if item.security_id == "A" and item.trade_date == current_day
                else item
                for item in source.bars
            )
        }
    )
    snapshot = _published_analysis_snapshot(source=source)
    app = create_workbench_app(repository=_PublishedRepository(snapshot))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/rank/range",
            params={
                "snapshot_id": str(snapshot.id),
                "start": str(_sessions()[0]),
                "end": str(_sessions()[-1]),
                "benchmark": "B000300",
                "sort": "amount_change",
                "adj": "raw",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert [item["security_id"] for item in body["data"]["ranking"]] == [
        "D",
        "E",
        "A",
    ]
    assert body["data"]["ranking"][-1]["return_ratio"] == 0.1
    assert body["data"]["ranking"][-1]["amount_change_ratio"] is None
    assert body["data"]["exclusions"] == [
        {"security_id": "C", "reasons": ["missing_price"]}
    ]
    assert body["coverage"] == {
        "expected": 4,
        "valid": 2,
        "excluded": 0,
        "missing": 2,
    }


@pytest.mark.anyio
async def test_stock_roles_fail_closed_without_leaking_internal_fields():
    from dragon_radar.http.app import create_workbench_app

    snapshot = _published_analysis_snapshot(role_ready=True)
    app = create_workbench_app(repository=_PublishedRepository(snapshot))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/stock/A",
            params={"snapshot_id": str(snapshot.id)},
        )

    assert response.status_code == 200
    assert response.json()["data"]["roles"] == [
        {
            "role_type": "trend_leader",
            "evidence_status": "insufficient",
            "sector_id": "topic-a",
        }
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("role_status", ("disabled", "missing"))
async def test_stock_roles_are_always_an_array_when_domain_is_unavailable(role_status):
    from dragon_radar.http.app import create_workbench_app

    snapshot = _published_analysis_snapshot(role_status=role_status)
    app = create_workbench_app(repository=_PublishedRepository(snapshot))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/stock/A",
            params={"snapshot_id": str(snapshot.id)},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["roles"] == []
    assert data["role_reason"] == f"role_{role_status}"
    assert data["regulation"] == {
        "active_events": None,
        "reason": "regulation_disabled",
    }


@pytest.mark.anyio
async def test_range_routes_select_latest_publication_not_after_requested_end():
    from dragon_radar.http.app import create_workbench_app
    from dragon_radar.publication.models import PublishedSnapshot

    selected = _published_analysis_snapshot()
    end = selected.trade_date
    future = PublishedSnapshot(
        selected.publication.model_copy(
            update={"id": uuid4(), "trade_date": end + timedelta(days=1)}
        ),
        selected.market_snapshot,
        selected.results,
    )
    repository = _PublishedRepository(future, selected)
    app = create_workbench_app(repository=repository)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for path, extra in (
            ("/api/stock/A/kline", {}),
            ("/api/rank/range", {"benchmark": "B000300"}),
            ("/api/compare/curves", {"symbols": "A"}),
        ):
            response = await client.get(
                path,
                params={
                    "start": str(_sessions()[0]),
                    "end": str(end),
                    "adj": "raw",
                    **extra,
                },
            )
            assert response.status_code == 200
            assert response.json()["snapshot_id"] == str(selected.id)

    assert len(repository.list_calls) == 3
    assert all(
        call
        == {
            "trade_date": None,
            "through_date": end,
            "limit": 1,
        }
        for call in repository.list_calls
    )


@pytest.mark.anyio
async def test_explicit_snapshot_is_authorized_on_every_range_request():
    from dragon_radar.http.app import create_workbench_app

    snapshot = _published_analysis_snapshot()
    repository = _PublishedRepository(snapshot)
    app = create_workbench_app(repository=repository)
    common = {
        "snapshot_id": str(snapshot.id),
        "start": str(_sessions()[0]),
        "end": str(snapshot.trade_date),
        "adj": "raw",
    }

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        first = await client.get("/api/rank/range", params=common)
        second = await client.get(
            "/api/compare/curves",
            params={**common, "symbols": "A"},
        )

    assert first.status_code == second.status_code == 200
    assert repository.get_calls == [snapshot.id, snapshot.id]
    assert repository.list_calls == []


@pytest.mark.anyio
async def test_unpinned_explicit_date_must_equal_adjusted_end():
    from dragon_radar.http.app import create_workbench_app

    snapshot = _published_analysis_snapshot()
    repository = _PublishedRepository(snapshot)
    app = create_workbench_app(repository=repository)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/rank/range",
            params={
                "start": str(_sessions()[0]),
                "end": str(snapshot.trade_date + timedelta(days=1)),
                "date": str(_sessions()[0]),
                "adj": "raw",
            },
        )

    assert response.status_code == 400
    assert repository.list_calls == [
        {
            "trade_date": None,
            "through_date": snapshot.trade_date + timedelta(days=1),
            "limit": 1,
        }
    ]


@pytest.mark.anyio
async def test_compare_curve_returns_middle_nulls_and_reasons():
    from dragon_radar.http.app import create_workbench_app

    source = _analysis_batch()
    gap_day = _sessions()[2]
    source = source.model_copy(
        update={
            "bars": tuple(
                item
                for item in source.bars
                if not (item.security_id == "D" and item.trade_date == gap_day)
            )
        }
    )
    snapshot = _published_analysis_snapshot(source=source)
    app = create_workbench_app(repository=_PublishedRepository(snapshot))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/compare/curves",
            params={
                "snapshot_id": str(snapshot.id),
                "start": str(_sessions()[0]),
                "end": str(_sessions()[-1]),
                "symbols": "D",
                "adj": "raw",
            },
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["exclusions"] == []
    assert data["curves"][0]["reasons"] == ["missing_price"]
    assert data["curves"][0]["points"][2] == {
        "trade_date": str(gap_day),
        "normalized": None,
        "drawdown": None,
    }
    assert data["curves"][0]["points"][3]["normalized"] == 90
    assert data["curves"][0]["points"][3]["drawdown"] == 0.25


@pytest.mark.anyio
async def test_stock_detail_prefers_complete_published_stock_seal():
    from dragon_radar.http.app import create_workbench_app

    source = _analysis_batch()
    source = source.model_copy(
        update={
            "limit_up_events": (_limit_up_event(source, touched_limit=True),),
        }
    )
    snapshot = _published_analysis_snapshot(role_ready=True, source=source)
    app = create_workbench_app(repository=_PublishedRepository(snapshot))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/stock/A",
            params={"snapshot_id": str(snapshot.id)},
        )

    assert response.status_code == 200
    assert response.json()["data"]["seal"] == {
        "is_limit_up": True,
        "touched_limit": True,
        "first_seal_time": "09:35:00",
        "last_seal_time": "14:55:00",
        "break_count": 2,
        "seal_amount": 123456.78,
        "seal_amount_unit": "currency",
        "continuous_boards": 3,
        "reason": None,
    }


@pytest.mark.anyio
async def test_analysis_routes_validate_before_storage_and_bind_snapshot_date():
    from dragon_radar.http.app import create_workbench_app

    snapshot = _published_analysis_snapshot()
    repository = _PublishedRepository(snapshot)
    app = create_workbench_app(repository=repository)
    start, end = _sessions()[0], _sessions()[-1]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for symbols in ("A,B,C,D,E,F", "A,A", "A,,B", ""):
            response = await client.get(
                "/api/compare/curves",
                params={"symbols": symbols, "start": str(start), "end": str(end)},
            )
            assert response.status_code == 400
        assert repository.accesses == 0

        mismatch = await client.get(
            "/api/rank/range",
            params={
                "start": str(start),
                "end": str(end),
                "date": str(start),
                "snapshot_id": str(snapshot.id),
                "adj": "raw",
            },
        )
        assert mismatch.status_code == 400

        reversed_window = await client.get(
            "/api/stock/A/kline",
            params={
                "start": str(end),
                "end": str(start),
                "snapshot_id": str(snapshot.id),
                "adj": "raw",
            },
        )
        assert reversed_window.status_code == 400

        missing_stock = await client.get(
            "/api/stock/UNKNOWN", params={"snapshot_id": str(snapshot.id)}
        )
        assert missing_stock.status_code == 404
        missing_roles = await client.get(
            "/api/stock/A", params={"snapshot_id": str(snapshot.id)}
        )
        assert missing_roles.json()["data"]["roles"] == []
        assert missing_roles.json()["data"]["role_reason"] == "role_disabled"
        assert missing_roles.json()["data"]["seal"] == {
            "is_limit_up": None,
            "touched_limit": None,
            "first_seal_time": None,
            "last_seal_time": None,
            "break_count": None,
            "seal_amount": None,
            "seal_amount_unit": None,
            "continuous_boards": None,
            "reason": "seal_event_missing",
        }

    missing_repository = _PublishedRepository()
    missing_app = create_workbench_app(repository=missing_repository)
    async with AsyncClient(
        transport=ASGITransport(app=missing_app), base_url="http://testserver"
    ) as client:
        missing = await client.get(
            "/api/rank/range",
            params={
                "start": str(start),
                "end": str(end),
                "snapshot_id": str(uuid4()),
                "adj": "raw",
            },
        )
    assert missing.status_code == 200
    assert missing.json()["data"] is None


def _replace_market_payload(snapshot, payload):
    from dragon_radar.publication.models import PublishedSnapshot

    results = tuple(
        result.model_copy(update={"payload": payload})
        if result.domain == "market"
        else result
        for result in snapshot.results
    )
    return PublishedSnapshot(snapshot.publication, snapshot.market_snapshot, results)


def _mutable_market_payload(snapshot):
    return snapshot.result("market").model_dump(mode="json")["payload"]


def _corrupt_market_extension(snapshot, extension, field, value):
    payload = _mutable_market_payload(snapshot)
    payload[extension][field] = value
    return _replace_market_payload(snapshot, payload)


@pytest.mark.anyio
@pytest.mark.parametrize("extension", ("analysis", "stocks"))
async def test_analysis_routes_reject_extension_trade_date_mismatch(extension):
    from dragon_radar.http.app import create_workbench_app

    snapshot = _corrupt_market_extension(
        _published_analysis_snapshot(),
        extension,
        "trade_date",
        "1999-01-01",
    )
    app = create_workbench_app(repository=_PublishedRepository(snapshot))

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/rank/range",
            params={
                "snapshot_id": str(snapshot.id),
                "start": str(_sessions()[0]),
                "end": str(_sessions()[-1]),
                "adj": "raw",
            },
        )

    assert response.status_code == 503
    assert response.json() == {
        "code": "DATA_UNAVAILABLE",
        "message": "Data unavailable",
    }


@pytest.mark.anyio
async def test_analysis_routes_reject_non_object_stocks_extension():
    from dragon_radar.http.app import create_workbench_app

    snapshot = _published_analysis_snapshot()
    payload = _mutable_market_payload(snapshot)
    payload["stocks"] = []
    snapshot = _replace_market_payload(snapshot, payload)
    app = create_workbench_app(repository=_PublishedRepository(snapshot))

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/stock/A",
            params={"snapshot_id": str(snapshot.id)},
        )

    assert response.status_code == 503
    assert response.json() == {
        "code": "DATA_UNAVAILABLE",
        "message": "Data unavailable",
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "value"),
    (
        (None, []),
        (None, "bad-stock"),
        ("quote", []),
        ("quote", "bad-quote"),
        ("history", {}),
        ("history", ["bad-point"]),
        ("chart_type", []),
        ("chart_type", "area"),
    ),
)
@pytest.mark.parametrize("path", ("/api/stock/A", "/api/stock/A/kline"))
async def test_stock_routes_reject_malformed_published_stock_payload(
    field, value, path
):
    from dragon_radar.http.app import create_workbench_app

    snapshot = _published_analysis_snapshot()
    payload = _mutable_market_payload(snapshot)
    if field is None:
        payload["stocks"]["stocks"]["A"] = value
    else:
        payload["stocks"]["stocks"]["A"][field] = value
    snapshot = _replace_market_payload(snapshot, payload)
    app = create_workbench_app(repository=_PublishedRepository(snapshot))
    params = {"snapshot_id": str(snapshot.id)}
    if path.endswith("/kline"):
        params.update(
            {
                "start": str(_sessions()[0]),
                "end": str(_sessions()[-1]),
                "adj": "raw",
            }
        )

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        response = await client.get(path, params=params)

    assert response.status_code == 503
    assert response.json() == {
        "code": "DATA_UNAVAILABLE",
        "message": "Data unavailable",
    }


@pytest.mark.anyio
async def test_analysis_routes_reject_bad_published_session_date():
    from dragon_radar.http.app import create_workbench_app

    snapshot = _published_analysis_snapshot()
    payload = _mutable_market_payload(snapshot)
    payload["analysis"]["windows"]["20"]["sessions"][0] = "not-a-date"
    snapshot = _replace_market_payload(snapshot, payload)
    app = create_workbench_app(repository=_PublishedRepository(snapshot))

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/rank/range",
            params={
                "snapshot_id": str(snapshot.id),
                "start": str(_sessions()[0]),
                "end": str(_sessions()[-1]),
                "adj": "raw",
            },
        )

    assert response.status_code == 503
    assert response.json() == {
        "code": "DATA_UNAVAILABLE",
        "message": "Data unavailable",
    }


@pytest.mark.anyio
@pytest.mark.parametrize("value", ("not-a-decimal", ["bad-row"]))
async def test_analysis_routes_reject_bad_published_ranking_decimal(value):
    from dragon_radar.http.app import create_workbench_app

    snapshot = _published_analysis_snapshot()
    payload = _mutable_market_payload(snapshot)
    ranking = payload["analysis"]["windows"]["20"]["adjustments"]["raw"]["ranking"]
    if isinstance(value, list):
        ranking[0] = value
    else:
        ranking[0]["return_ratio"] = value
    snapshot = _replace_market_payload(snapshot, payload)
    app = create_workbench_app(repository=_PublishedRepository(snapshot))

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/rank/range",
            params={
                "snapshot_id": str(snapshot.id),
                "start": str(_sessions()[0]),
                "end": str(_sessions()[-1]),
                "adj": "raw",
            },
        )

    assert response.status_code == 503
    assert response.json() == {
        "code": "DATA_UNAVAILABLE",
        "message": "Data unavailable",
    }
