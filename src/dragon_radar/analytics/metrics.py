"""Pure Decimal analytics, with no provider, clock, storage, or model calls.

These APIs do not authorize publication/read access: callers must check current
grants and use a quality-gated snapshot. They nevertheless exclude unavailable,
invalid or ambiguous observations on their own. Results retain batch provenance.
Ratios are fractions, curves start at 100, and drawdown is a nonnegative fraction.
Decimal operations use a fixed 34-digit half-even context; no float conversion.
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import Literal

from pydantic import ConfigDict

from dragon_radar.analytics.calendar import TradingCalendar
from dragon_radar.domain.models import (
    DailyBar,
    ExactDecimal,
    FrozenModel,
    MarketBatch,
    Provenance,
    UTCDateTime,
)
from dragon_radar.domain.quality import (
    Coverage,
    bar_issues,
    state_at,
)


def _decimal_context() -> Context:
    return Context(
        prec=34,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


def _prices(prices: Sequence[Decimal]) -> tuple[Decimal, ...]:
    if not isinstance(prices, Sequence) or not prices:
        raise ValueError(
            "prices must be a nonempty sequence of positive Decimal prices"
        )
    values = tuple(prices)
    if any(not isinstance(p, Decimal) or not p.is_finite() or p <= 0 for p in values):
        raise ValueError("every price must be a finite positive Decimal")
    return values


def interval_return(prices: Sequence[Decimal]) -> Decimal:
    """Last/first - 1, validating every supplied price; one price returns zero."""
    values = _prices(prices)
    with localcontext(_decimal_context()):
        return values[-1] / values[0] - 1


def maximum_drawdown(prices: Sequence[Decimal]) -> Decimal:
    """Maximum 1 - price/running_peak; never use the initial price as every peak."""
    values = _prices(prices)
    peak = values[0]
    worst = Decimal(0)
    with localcontext(_decimal_context()):
        for price in values:
            peak = max(peak, price)
            worst = max(worst, 1 - price / peak)
    return worst


def normalized_curve(prices: Sequence[Decimal]) -> tuple[Decimal, ...]:
    """100 * price/first for every input point; no gap filling."""
    values = _prices(prices)
    with localcontext(_decimal_context()):
        return tuple(Decimal(100) * price / values[0] for price in values)


class AnalysisMetadata(FrozenModel):
    """Source metadata is copied from input, never derived from request time."""

    trade_date: date
    as_of: UTCDateTime
    source_time: UTCDateTime | None
    available_at: UTCDateTime | None
    source_id: str
    source_version: str
    adjustment_version: str
    calendar_version: str
    pool_version: str
    provenance: Provenance
    basis: str
    rule_version: str = "analytics-v1-decimal34-half-even"


class Exclusion(FrozenModel):
    security_id: str
    reasons: tuple[str, ...]


class IntervalItem(FrozenModel):
    model_config = ConfigDict(allow_inf_nan=False)

    security_id: str
    interval_return: ExactDecimal
    maximum_drawdown: ExactDecimal
    normalized_curve: tuple[ExactDecimal, ...]


class IntervalAnalysis(AnalysisMetadata):
    sessions: tuple[date, ...]
    items: tuple[IntervalItem, ...]
    exclusions: tuple[Exclusion, ...]


class DailyReturn(FrozenModel):
    model_config = ConfigDict(allow_inf_nan=False)

    security_id: str
    value: ExactDecimal
    basis: Literal["explicit_previous_close", "prior_session_close"]
    warnings: tuple[str, ...] = ()


class BreadthAnalysis(AnalysisMetadata):
    coverage: Coverage
    advancing: int
    declining: int
    unchanged: int
    returns: tuple[DailyReturn, ...]
    exclusions: tuple[Exclusion, ...]


LimitHit = Literal["up", "down", "none", "unknown"]


class LimitStreak(AnalysisMetadata):
    security_id: str
    end: date
    direction: Literal["up", "down"]
    count: int | None
    known: bool
    reason: str | None


def _metadata(batch: MarketBatch, basis: str) -> dict:
    return {
        "trade_date": batch.trade_date,
        "as_of": batch.as_of,
        "source_time": batch.source_time,
        "available_at": max(
            (b.available_at for b in batch.bars if b.available_at <= batch.as_of),
            default=None,
        ),
        "source_id": batch.source_id,
        "source_version": batch.source_version,
        "adjustment_version": batch.adjustment_version,
        "calendar_version": batch.calendar_version,
        "pool_version": batch.pool_version,
        "provenance": batch.provenance,
        "basis": basis,
    }


def _index(batch: MarketBatch) -> dict[tuple[str, date], list[DailyBar]]:
    index: dict[tuple[str, date], list[DailyBar]] = defaultdict(list)
    for bar in batch.bars:
        index[(bar.security_id, bar.trade_date)].append(bar)
    return index


def _observation(
    batch: MarketBatch,
    index: dict[tuple[str, date], list[DailyBar]],
    sid: str,
    day: date,
) -> tuple[DailyBar | None, tuple[str, ...]]:
    state = state_at(batch, sid, day)
    if state is None:
        return None, ("effective_state",)
    if state.suspended:
        return None, ("suspension",)
    records = index.get((sid, day), [])
    if len(records) != 1:
        return None, ("duplicate_bar" if records else "missing_price",)
    bar = records[0]
    errors = bar_issues(bar, batch)
    if errors:
        return None, tuple(sorted({e.code for e in errors}))
    return bar, ()


def _batch_reasons(batch: MarketBatch) -> tuple[str, ...]:
    reasons = []
    if not batch.complete_pages:
        reasons.append("incomplete_pages")
    if batch.calendar_version != batch.calendar.version:
        reasons.append("calendar_version")
    if len(set(batch.expected_security_ids)) != len(batch.expected_security_ids):
        reasons.append("duplicate_expected_id")
    return tuple(reasons)


def analyze_interval(
    batch: MarketBatch,
    start: date,
    end: date,
    security_ids: Sequence[str] | None = None,
) -> IntervalAnalysis:
    """Analyze fixed common endpoints; exclude ANY invalid or missing session.

    None selects the declared expected pool. Explicit IDs must be unique and
    nonempty. Unknown/non-pool IDs are returned as exclusions, not fabricated.
    Use compare_interval for the max-five comparison constraint.
    """
    sessions = TradingCalendar(batch.calendar).custom_window(start, end)
    if sessions[-1] > batch.trade_date:
        raise ValueError("window exceeds batch trade date")
    ids = tuple(batch.expected_security_ids if security_ids is None else security_ids)
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("security IDs must be nonempty and unique")
    index = _index(batch)
    items: list[IntervalItem] = []
    exclusions: list[Exclusion] = []
    for sid in ids:
        reasons = set(_batch_reasons(batch))
        if sid not in batch.expected_security_ids:
            reasons.add("unknown_id")
        records = []
        for day in sessions:
            bar, errors = _observation(batch, index, sid, day)
            reasons.update(errors)
            if bar is not None:
                records.append(bar)
        if len({(b.currency, b.price_unit) for b in records}) > 1:
            reasons.add("unit_mismatch")
        if reasons:
            exclusions.append(
                Exclusion(security_id=sid, reasons=tuple(sorted(reasons)))
            )
            continue
        with localcontext(_decimal_context()):
            prices = tuple(b.close * b.adjustment_factor for b in records)
        items.append(
            IntervalItem(
                security_id=sid,
                interval_return=interval_return(prices),
                maximum_drawdown=maximum_drawdown(prices),
                normalized_curve=normalized_curve(prices),
            )
        )
    return IntervalAnalysis(
        **_metadata(batch, "adjusted_close"),
        sessions=sessions,
        items=tuple(items),
        exclusions=tuple(exclusions),
    )


def compare_interval(
    batch: MarketBatch, start: date, end: date, security_ids: Sequence[str]
) -> IntervalAnalysis:
    """One common window, at most five unique requested IDs, explicit exclusions."""
    if not 1 <= len(security_ids) <= 5 or len(set(security_ids)) != len(security_ids):
        raise ValueError("comparison requires one to five unique security IDs")
    return analyze_interval(batch, start, end, security_ids)


def market_breadth(batch: MarketBatch) -> BreadthAnalysis:
    """Raw daily returns over the full expected denominator.

    Explicit previous_close takes precedence. Otherwise only the immediately
    preceding supplied session is usable, with a corporate-action caution.
    Suspension is the sole legal exclusion; missing/unknown inputs stay missing.
    """
    index = _index(batch)
    sessions = batch.calendar.sessions
    position = sessions.index(batch.trade_date) if batch.trade_date in sessions else -1
    previous_day = sessions[position - 1] if position > 0 else None
    returns: list[DailyReturn] = []
    exclusions: list[Exclusion] = []
    legal_excluded = 0
    for sid in dict.fromkeys(batch.expected_security_ids):
        reasons = set(_batch_reasons(batch))
        state = state_at(batch, sid, batch.trade_date)
        if state is not None and state.suspended and not reasons:
            legal_excluded += 1
            continue
        current, errors = _observation(batch, index, sid, batch.trade_date)
        reasons.update(errors)
        reference = current.previous_close if current is not None else None
        basis = "explicit_previous_close"
        warnings: tuple[str, ...] = ()
        if current is not None and reference is None:
            basis = "prior_session_close"
            warnings = ("corporate_action_caution",)
            if previous_day is None:
                reasons.add("missing_previous_close")
            else:
                previous, errors = _observation(batch, index, sid, previous_day)
                reasons.update(errors)
                if previous is not None:
                    if (current.currency, current.price_unit) != (
                        previous.currency,
                        previous.price_unit,
                    ):
                        reasons.add("unit_mismatch")
                    reference = previous.close
        if reasons:
            exclusions.append(
                Exclusion(security_id=sid, reasons=tuple(sorted(reasons)))
            )
        else:
            returns.append(
                DailyReturn(
                    security_id=sid,
                    value=interval_return((reference, current.close)),
                    basis=basis,
                    warnings=warnings,
                )
            )
    return BreadthAnalysis(
        **_metadata(batch, "raw_daily_return"),
        coverage=Coverage(
            expected=len(set(batch.expected_security_ids)),
            valid=len(returns),
            legal_excluded=legal_excluded,
            missing=len(exclusions),
        ),
        advancing=sum(r.value > 0 for r in returns),
        declining=sum(r.value < 0 for r in returns),
        unchanged=sum(r.value == 0 for r in returns),
        returns=tuple(returns),
        exclusions=tuple(exclusions),
    )


def closing_limit_hit(bar: DailyBar) -> LimitHit:
    """Exact RAW close equality to dated supplied limits; unknown != no_limit.

    This record-local helper does not check state, cutoff or source. Batch callers
    must use the quality gate, or closing_limit_streak which checks those itself.
    """
    if bar.close is None or not bar.close.is_finite() or bar.close <= 0:
        return "unknown"
    if bar.limit_status == "no_limit":
        return (
            "none"
            if all(v is None for v in (bar.limit_date, bar.limit_up, bar.limit_down))
            else "unknown"
        )
    if (
        bar.limit_status != "limited"
        or bar.limit_date != bar.trade_date
        or any(
            v is None or not v.is_finite() or v <= 0
            for v in (bar.limit_down, bar.limit_up)
        )
        or not bar.limit_down < bar.limit_up
        or not bar.limit_down <= bar.close <= bar.limit_up
    ):
        return "unknown"
    if bar.close == bar.limit_up:
        return "up"
    if bar.close == bar.limit_down:
        return "down"
    return "none"


def closing_limit_streak(
    batch: MarketBatch,
    security_id: str,
    end: date,
    direction: Literal["up", "down"] = "up",
) -> LimitStreak:
    """Consecutive closing hits ending at rolled-back end.

    A suspension or known non-hit resets the run. Missing state/price/limit or
    exhausted history before a reset makes count None, not zero or a lower bound.
    """
    if direction not in ("up", "down"):
        raise ValueError("direction must be up or down")
    sessions = TradingCalendar(batch.calendar).custom_window(
        batch.calendar.sessions[0], end
    )
    if sessions[-1] > batch.trade_date:
        raise ValueError("window exceeds batch trade date")
    index = _index(batch)
    count = 0
    reason = "insufficient_history"
    known = False
    for day in reversed(sessions):
        if _batch_reasons(batch):
            reason = _batch_reasons(batch)[0]
            break
        state = state_at(batch, security_id, day)
        if state is None:
            reason = "effective_state"
            break
        if state.suspended:
            known = True
            break
        bar, errors = _observation(batch, index, security_id, day)
        if errors:
            reason = errors[0]
            break
        hit = closing_limit_hit(bar)
        if hit == "unknown":
            reason = "unknown_limit"
            break
        if hit != direction:
            known = True
            break
        count += 1
    return LimitStreak(
        **_metadata(batch, "raw_closing_limit"),
        security_id=security_id,
        end=sessions[-1],
        direction=direction,
        count=count if known else None,
        known=known,
        reason=None if known else reason,
    )
