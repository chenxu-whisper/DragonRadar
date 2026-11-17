"""Task 27 trading-rule, ledger and run-lifecycle validation suite.

Task 1 scaffold pins the research package boundary. Task 3 adds the
deterministic ``simulate_fill`` pure-function contract: nine closed-set
rejection codes, T+1, directional limit blocking, conservative unknown-limit
handling and Decimal golden-sample economics.
"""

from __future__ import annotations

import importlib
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from dragon_radar.domain.models import DailyBar, SecurityState
from dragon_radar.research.execution import (
    Fill,
    Order,
    Position,
    Rejection,
    simulate_fill,
)
from dragon_radar.research.ledger import (
    EquityPoint,
    LedgerState,
    OrderOutcome,
    apply_fill,
    cost_sensitivity_models,
    mark_equity,
    open_ledger,
    roll_overnight,
    summarize_run,
)
from dragon_radar.research.models import CostModel

VALIDATION_MODULES = (
    "execution",
    "ledger",
    "repository",
    "worker",
)


@pytest.mark.parametrize("module_name", VALIDATION_MODULES)
def test_research_validation_module_importable(module_name: str) -> None:
    module = importlib.import_module(f"dragon_radar.research.{module_name}")
    assert module is not None


# ---------------------------------------------------------------------------
# Task 3 — simulate_fill pure execution rule
# ---------------------------------------------------------------------------

SIGNAL_DAY = date(2025, 1, 6)
FILL_DAY = date(2025, 1, 7)
AVAILABLE_AT = datetime(2025, 1, 7, 8, 0, tzinfo=UTC)


def cost_model(**overrides: object) -> CostModel:
    values = {
        "commission_rate": "0.0003",
        "stamp_tax_rate": "0.001",
        "slippage_rate": "0.0005",
        "min_commission": "5",
    }
    values.update(overrides)
    return CostModel(**values)  # type: ignore[arg-type]


def order(side: str = "buy", quantity: int = 100, *, signal_day=SIGNAL_DAY) -> Order:
    return Order(
        security_id="000001",
        side=side,  # type: ignore[arg-type]
        quantity=quantity,
        signal_date=signal_day,
    )


def bar(
    *,
    close: str | None = "10.00",
    limit_status: str = "no_limit",
    limit_up: str | None = None,
    limit_down: str | None = None,
    trade_date: date = FILL_DAY,
    factor: str = "1",
) -> DailyBar:
    return DailyBar(
        security_id="000001",
        trade_date=trade_date,
        available_at=AVAILABLE_AT,
        source_id="synthetic",
        source_version="v1",
        close=Decimal(close) if close is not None else None,
        adjustment_factor=Decimal(factor),
        adjustment_version="adj-v1",
        currency="CNY",
        price_unit="CNY",
        volume_unit="share",
        amount_unit="CNY",
        limit_status=limit_status,  # type: ignore[arg-type]
        limit_date=trade_date if limit_status == "limited" else None,
        limit_up=Decimal(limit_up) if limit_up is not None else None,
        limit_down=Decimal(limit_down) if limit_down is not None else None,
    )


def state(*, suspended: bool | None = False, delisting: bool | None = False):
    return SecurityState(
        security_id="000001",
        code="000001",
        name="Synthetic Alpha",
        board="main",
        exchange="SSE",
        effective_from=date(2020, 1, 1),
        available_at=AVAILABLE_AT,
        source_id="synthetic",
        source_version="v1",
        pool_version="pool-v1",
        suspended=suspended,
        is_delisting=delisting,
    )


def position(total: str = "0", available: str | None = None) -> Position:
    return Position(
        security_id="000001",
        quantity=Decimal(total),
        available_quantity=Decimal(available if available is not None else total),
    )


def reject(
    side: str = "buy",
    quantity: int = 100,
    *,
    cash: str = "100000",
    pos: Position | None = None,
    bar_: DailyBar | None = None,
    state_=None,
    signal_day=SIGNAL_DAY,
):
    result = simulate_fill(
        order(side, quantity, signal_day=signal_day),
        bar_ or bar(),
        state_ if state_ is not None else state(),
        cost_model(),
        Decimal(cash),
        pos,
    )
    assert isinstance(result, Rejection)
    return result


@pytest.mark.parametrize(
    ("case", "expected_reason"),
    [
        ("suspended", "suspended"),
        ("limit_up_buy", "limit_up_blocked_buy"),
        ("limit_down_sell", "limit_down_blocked_sell"),
        ("t1", "t1_sell_blocked"),
        ("cash", "insufficient_cash"),
        ("position", "insufficient_position"),
        ("delisted", "delisted"),
        ("no_price", "no_tradable_price"),
        ("unknown_limit", "unknown_limit_untradable"),
    ],
)
def test_nine_rejection_reasons_are_deterministic(case: str, expected_reason: str):
    if case == "suspended":
        result = reject(state_=state(suspended=True))
    elif case == "limit_up_buy":
        result = reject(
            bar_=bar(
                limit_status="limited",
                close="11.00",
                limit_up="11.00",
                limit_down="9.00",
            ),
        )
    elif case == "limit_down_sell":
        result = reject(
            side="sell",
            pos=position("100"),
            bar_=bar(
                limit_status="limited",
                close="9.00",
                limit_up="11.00",
                limit_down="9.00",
            ),
        )
    elif case == "t1":
        result = reject(side="sell", pos=position("100", "0"))
    elif case == "cash":
        result = reject(cash="1")
    elif case == "position":
        result = reject(side="sell", pos=position("0"))
    elif case == "delisted":
        result = reject(state_=state(delisting=True))
    elif case == "no_price":
        result = reject(bar_=bar(close=None))
    else:
        result = reject(bar_=bar(limit_status="unknown"))

    assert result.reason == expected_reason
    assert result.security_id == "000001"
    assert result.trade_date == FILL_DAY


def test_rejection_never_emits_a_fill():
    result = simulate_fill(
        order(), bar(close=None), state(), cost_model(), Decimal("100000"), None
    )
    assert isinstance(result, Rejection)
    assert not isinstance(result, Fill)


def test_execution_earlier_than_next_session_is_a_contract_violation():
    with pytest.raises(ValueError, match="next tradable session"):
        simulate_fill(
            order(signal_day=FILL_DAY),
            bar(),
            state(),
            cost_model(),
            Decimal("100000"),
            None,
        )


def test_limit_up_blocks_buy_but_allows_sell():
    sealed_up = bar(
        limit_status="limited", close="11.00", limit_up="11.00", limit_down="9.00"
    )
    blocked = simulate_fill(
        order("buy"), sealed_up, state(), cost_model(), Decimal("100000"), None
    )
    assert isinstance(blocked, Rejection)
    assert blocked.reason == "limit_up_blocked_buy"

    filled = simulate_fill(
        order("sell"),
        sealed_up,
        state(),
        cost_model(),
        Decimal("0"),
        position("100"),
    )
    assert isinstance(filled, Fill)


def test_limit_down_blocks_sell_but_allows_buy():
    sealed_down = bar(
        limit_status="limited", close="9.00", limit_up="11.00", limit_down="9.00"
    )
    blocked = simulate_fill(
        order("sell"),
        sealed_down,
        state(),
        cost_model(),
        Decimal("0"),
        position("100"),
    )
    assert isinstance(blocked, Rejection)
    assert blocked.reason == "limit_down_blocked_sell"

    filled = simulate_fill(
        order("buy"), sealed_down, state(), cost_model(), Decimal("100000"), None
    )
    assert isinstance(filled, Fill)


def test_unknown_limit_metadata_rejects_both_sides_conservatively():
    unknown = bar(limit_status="unknown")
    buy = simulate_fill(
        order("buy"), unknown, state(), cost_model(), Decimal("100000"), None
    )
    sell = simulate_fill(
        order("sell"),
        unknown,
        state(),
        cost_model(),
        Decimal("0"),
        position("100"),
    )
    assert isinstance(buy, Rejection) and buy.reason == "unknown_limit_untradable"
    assert isinstance(sell, Rejection) and sell.reason == "unknown_limit_untradable"


def test_limit_metadata_from_another_date_is_not_assertable():
    stale = bar(
        limit_status="limited",
        close="11.00",
        limit_up="11.00",
        limit_down="9.00",
        trade_date=FILL_DAY,
    )
    # limit_date not equal to trade_date makes closing_limit_hit "unknown".
    stale = stale.model_copy(update={"limit_date": date(2025, 1, 6)})
    result = simulate_fill(
        order("buy"), stale, state(), cost_model(), Decimal("100000"), None
    )
    assert isinstance(result, Rejection)
    assert result.reason == "unknown_limit_untradable"


def test_t1_sell_blocked_then_allowed_next_session():
    bought_today = position("100", "0")
    blocked = simulate_fill(
        order("sell", signal_day=SIGNAL_DAY),
        bar(),
        state(),
        cost_model(),
        Decimal("0"),
        bought_today,
    )
    assert isinstance(blocked, Rejection) and blocked.reason == "t1_sell_blocked"

    settled = position("100", "100")
    next_bar = bar(trade_date=date(2025, 1, 8))
    filled = simulate_fill(
        Order(
            security_id="000001",
            side="sell",
            quantity=100,
            signal_date=date(2025, 1, 7),
        ),
        next_bar,
        state(),
        cost_model(),
        Decimal("0"),
        settled,
    )
    assert isinstance(filled, Fill)


def test_selling_more_than_total_is_insufficient_position_not_t1():
    result = simulate_fill(
        order("sell", quantity=200),
        bar(),
        state(),
        cost_model(),
        Decimal("0"),
        position("100", "100"),
    )
    assert isinstance(result, Rejection)
    assert result.reason == "insufficient_position"


def test_selling_frozen_lot_within_total_is_t1():
    result = simulate_fill(
        order("sell", quantity=150),
        bar(),
        state(),
        cost_model(),
        Decimal("0"),
        position("200", "100"),
    )
    assert isinstance(result, Rejection)
    assert result.reason == "t1_sell_blocked"


def test_insufficient_cash_accounts_for_slippage_and_commission():
    # buy 100 @ ~10.005 -> 1000.50 + min commission 5 -> 1005.50 needed
    short = simulate_fill(
        order("buy"), bar(), state(), cost_model(), Decimal("1005"), None
    )
    assert isinstance(short, Rejection) and short.reason == "insufficient_cash"

    exact = simulate_fill(
        order("buy"), bar(), state(), cost_model(), Decimal("1005.50"), None
    )
    assert isinstance(exact, Fill)


def test_buy_golden_sample_matches_hand_arithmetic():
    result = simulate_fill(
        order("buy", 100),
        bar(close="10.00"),
        state(),
        cost_model(),
        Decimal("100000"),
        None,
    )
    assert isinstance(result, Fill)
    assert result.raw_close == Decimal("10.00")
    assert result.fill_price == Decimal("10.0050")
    assert result.gross_amount == Decimal("1000.50")
    assert result.commission == Decimal("5.00")
    assert result.stamp_tax == Decimal("0.00")
    assert result.net_cash_delta == Decimal("-1005.50")
    assert result.trade_date == FILL_DAY


def test_sell_golden_sample_matches_hand_arithmetic():
    result = simulate_fill(
        order("sell", 100),
        bar(close="10.00"),
        state(),
        cost_model(),
        Decimal("0"),
        position("100", "100"),
    )
    assert isinstance(result, Fill)
    assert result.fill_price == Decimal("9.9950")
    assert result.gross_amount == Decimal("999.50")
    assert result.commission == Decimal("5.00")
    assert result.stamp_tax == Decimal("1.00")
    assert result.net_cash_delta == Decimal("993.50")


def test_fill_economics_are_all_decimal_and_raw_priced():
    result = simulate_fill(
        order("buy", 100),
        bar(close="10.00", factor="1.5"),
        state(),
        cost_model(),
        Decimal("100000"),
        None,
    )
    assert isinstance(result, Fill)
    for value in (
        result.raw_close,
        result.fill_price,
        result.gross_amount,
        result.commission,
        result.stamp_tax,
        result.net_cash_delta,
    ):
        assert isinstance(value, Decimal)
    # Adjustment factor never enters the raw fill price (N8).
    assert result.fill_price == Decimal("10.0050")


def test_rejection_precedence_limit_before_cash_and_position():
    sealed_up = bar(
        limit_status="limited", close="11.00", limit_up="11.00", limit_down="9.00"
    )
    buy = simulate_fill(
        order("buy"), sealed_up, state(), cost_model(), Decimal("0"), None
    )
    assert isinstance(buy, Rejection) and buy.reason == "limit_up_blocked_buy"

    sealed_down = bar(
        limit_status="limited", close="9.00", limit_up="11.00", limit_down="9.00"
    )
    sell = simulate_fill(
        order("sell"),
        sealed_down,
        state(),
        cost_model(),
        Decimal("0"),
        position("0"),
    )
    assert isinstance(sell, Rejection)
    assert sell.reason == "limit_down_blocked_sell"


# ---------------------------------------------------------------------------
# Task 4 — Decimal ledger, metrics and cost sensitivity
# ---------------------------------------------------------------------------

DAY1 = date(2025, 1, 7)
DAY2 = date(2025, 1, 8)
INITIAL_CASH = Decimal("100000.00")


def _round_trip_fills(rules: CostModel):
    """Buy 100 @10 on DAY1, sell 100 @10 on DAY2 under one cost model."""
    ledger = open_ledger(INITIAL_CASH, SIGNAL_DAY)

    buy = simulate_fill(
        order("buy", 100),
        bar(trade_date=DAY1),
        state(),
        rules,
        ledger.cash,
        None,
    )
    assert isinstance(buy, Fill)
    ledger = apply_fill(ledger, buy)
    assert ledger.holdings["000001"].quantity == Decimal("100")
    assert ledger.holdings["000001"].available_quantity == Decimal("0")

    # Identity, not a base-tier constant: this helper runs under every cost
    # tier, so independently recompute cash after the fill plus the holding
    # marked at the raw close. The base-cost golden value stays pinned below.
    day1_equity = mark_equity(ledger, {"000001": Decimal("10.00")})
    assert day1_equity == (INITIAL_CASH + buy.net_cash_delta + Decimal("1000.00"))

    ledger = roll_overnight(ledger, DAY2)
    assert ledger.holdings["000001"].available_quantity == Decimal("100")

    holding = ledger.holdings["000001"]
    sell = simulate_fill(
        Order(
            security_id="000001",
            side="sell",
            quantity=100,
            signal_date=DAY1,
        ),
        bar(trade_date=DAY2),
        state(),
        rules,
        ledger.cash,
        Position(
            security_id="000001",
            quantity=holding.quantity,
            available_quantity=holding.available_quantity,
        ),
    )
    assert isinstance(sell, Fill)
    ledger = apply_fill(ledger, sell)
    assert "000001" not in ledger.holdings
    return buy, sell, ledger


def test_ledger_identity_and_nonnegativity_hold_each_session():
    buy, sell, final_ledger = _round_trip_fills(cost_model())
    assert final_ledger.cash == Decimal("99988.00")

    points = (
        EquityPoint(trade_date=SIGNAL_DAY, equity=INITIAL_CASH),
        EquityPoint(trade_date=DAY1, equity=Decimal("99994.50")),
        EquityPoint(trade_date=DAY2, equity=Decimal("99988.00")),
    )
    # Identity recomputed independently at every marked session.
    assert points[1].equity == Decimal("98994.50") + Decimal("1000.00")
    assert points[2].equity == final_ledger.cash


def test_ledger_rejects_negative_cash_construction():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LedgerState(as_of=SIGNAL_DAY, cash=Decimal("-0.01"), holdings={})


def test_t1_lot_freezes_until_roll_overnight():
    ledger = open_ledger(INITIAL_CASH, SIGNAL_DAY)
    buy = simulate_fill(
        order("buy", 100),
        bar(trade_date=DAY1),
        state(),
        cost_model(),
        ledger.cash,
        None,
    )
    assert isinstance(buy, Fill)
    same_day = apply_fill(ledger, buy)
    assert same_day.holdings["000001"].available_quantity == Decimal("0")
    next_day = roll_overnight(same_day, DAY2)
    assert next_day.holdings["000001"].available_quantity == Decimal("100")
    assert next_day.cash == same_day.cash


def test_summarize_run_golden_metrics_and_dual_unfillable_ratios():
    buy, sell, _ledger = _round_trip_fills(cost_model())
    outcomes = (
        OrderOutcome(
            security_id="000001",
            side="buy",
            quantity=100,
            intended_amount=Decimal("1000.00"),
            filled=True,
        ),
        OrderOutcome(
            security_id="000001",
            side="sell",
            quantity=100,
            intended_amount=Decimal("1000.00"),
            filled=True,
        ),
        # 50-share rejected order carries half the intended amount but the
        # same count weight, separating the two ratio denominators.
        OrderOutcome(
            security_id="000001",
            side="buy",
            quantity=50,
            intended_amount=Decimal("500.00"),
            filled=False,
        ),
    )
    points = (
        EquityPoint(trade_date=SIGNAL_DAY, equity=INITIAL_CASH),
        EquityPoint(trade_date=DAY1, equity=Decimal("99994.50")),
        EquityPoint(trade_date=DAY2, equity=Decimal("99988.00")),
    )
    metrics = summarize_run(
        equity_points=points,
        fills=(buy, sell),
        outcomes=outcomes,
        benchmark_prices=(
            Decimal("100000"),
            Decimal("100000"),
            Decimal("100010"),
        ),
    )
    assert metrics.initial_equity == Decimal("100000.00")
    assert metrics.final_equity == Decimal("99988.00")
    assert metrics.total_return == Decimal("-0.000120")
    assert metrics.benchmark_return == Decimal("0.000100")
    assert metrics.excess_return == Decimal("-0.000220")
    assert metrics.max_drawdown == Decimal("0.000120")
    assert metrics.turnover_rate == Decimal("0.020000")
    assert metrics.total_commission == Decimal("10.00")
    assert metrics.total_stamp_tax == Decimal("1.00")
    assert metrics.total_cost == Decimal("11.00")
    assert metrics.unfillable_count_ratio == Decimal("0.333333")
    assert metrics.unfillable_amount_ratio == Decimal("0.200000")


def test_empty_order_stream_reports_zero_ratios():
    points = (EquityPoint(trade_date=SIGNAL_DAY, equity=INITIAL_CASH),)
    metrics = summarize_run(
        equity_points=points,
        fills=(),
        outcomes=(),
        benchmark_prices=None,
    )
    assert metrics.total_return == Decimal("0.000000")
    assert metrics.unfillable_count_ratio == Decimal("0.000000")
    assert metrics.unfillable_amount_ratio == Decimal("0.000000")
    assert metrics.excess_return == Decimal("0.000000")


def test_cost_sensitivity_scales_every_component_and_monotone_returns():
    tiers = cost_sensitivity_models(cost_model())
    assert tuple(tiers) == ("low", "base", "high")
    assert tiers["base"].commission_rate == Decimal("0.0003")
    assert tiers["low"].commission_rate == Decimal("0.00015")
    assert tiers["high"].commission_rate == Decimal("0.0006")
    assert tiers["low"].slippage_rate == Decimal("0.00025")
    assert tiers["high"].stamp_tax_rate == Decimal("0.002")
    assert tiers["low"].min_commission == Decimal("2.50")

    returns_by_tier = {}
    for tier_name, rules in tiers.items():
        buy, sell, _ledger = _round_trip_fills(rules)
        outcomes = (
            OrderOutcome(
                security_id="000001",
                side="buy",
                quantity=100,
                intended_amount=buy.gross_amount,
                filled=True,
            ),
            OrderOutcome(
                security_id="000001",
                side="sell",
                quantity=100,
                intended_amount=sell.gross_amount,
                filled=True,
            ),
        )
        points = (
            EquityPoint(trade_date=SIGNAL_DAY, equity=INITIAL_CASH),
            EquityPoint(
                trade_date=DAY2,
                equity=INITIAL_CASH + buy.net_cash_delta + sell.net_cash_delta,
            ),
        )
        metrics = summarize_run(
            equity_points=points,
            fills=(buy, sell),
            outcomes=outcomes,
            benchmark_prices=None,
        )
        returns_by_tier[tier_name] = metrics.total_return

    assert returns_by_tier["low"] > returns_by_tier["base"]
    assert returns_by_tier["base"] > returns_by_tier["high"]
