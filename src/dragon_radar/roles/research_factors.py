"""Traceable reset research factors independent of historical role thresholds."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from dragon_radar.domain.models import (
    DailyBar,
    ExactDecimal,
    FrozenModel,
    Identifier,
    MarketBatch,
)
from dragon_radar.roles.factors import (
    AnalysisUnavailable,
    _decimal34_operation,
    adjusted_price,
    decimal_mean,
    maximum_drawdown,
    mean_absolute_deviation_rate,
    period_return,
    relative_return,
    topic_amount_share,
)
from dragon_radar.topics.models import TopicBatch

FactorValue = ExactDecimal | bool | None


class FactorCoverage(FrozenModel):
    """A strict partition of every input position required by one metric."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    expected: int = Field(ge=0)
    valid: int = Field(ge=0)
    excluded: int = Field(ge=0)
    missing: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_partition(self) -> Self:
        if self.expected != self.valid + self.excluded + self.missing:
            raise ValueError("coverage must be a complete partition")
        return self


class FactorObservation(FrozenModel):
    """One independently computable research metric with source context."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    factor_id: Literal["FAC-R01", "FAC-R02", "FAC-R03", "FAC-C01", "FAC-C02"]
    metric: Identifier
    value: FactorValue
    unit: Identifier
    window: Identifier
    coverage: FactorCoverage
    reasons: tuple[str, ...] = ()
    metadata: Mapping[str, str] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def freeze_metadata(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        return MappingProxyType(dict(value))

    @model_validator(mode="after")
    def validate_value_reason(self) -> Self:
        if self.value is None and not self.reasons:
            raise ValueError("unavailable factor observations require reasons")
        if self.value is not None and self.reasons:
            raise ValueError("computed factor observations must not have reasons")
        return self


def _effective(start: date, end: date | None, day: date) -> bool:
    return start <= day and (end is None or day < end)


class _ResearchInput:
    def __init__(
        self,
        market: MarketBatch,
        *,
        cutoff: datetime,
    ) -> None:
        self.market = market
        self.cutoff = cutoff
        self._bars: dict[tuple[str, date], list[DailyBar]] = {}
        for item in market.bars:
            if (
                item.available_at <= cutoff
                and item.source_id == market.source_id
                and item.source_version == market.source_version
                and item.adjustment_version == market.adjustment_version
            ):
                self._bars.setdefault((item.security_id, item.trade_date), []).append(
                    item
                )

    def identity_available(self, security_id: str, *, index: bool) -> bool:
        if not index and security_id not in self.market.expected_security_ids:
            return False
        matches = tuple(
            item
            for item in self.market.security_states
            if item.security_id == security_id
            and item.available_at <= self.cutoff
            and item.source_id == self.market.source_id
            and item.source_version == self.market.source_version
            and item.pool_version == self.market.pool_version
            and _effective(
                item.effective_from,
                item.effective_to,
                self.market.trade_date,
            )
        )
        if len(matches) != 1:
            return False
        expected_type = "index" if index else "common_stock"
        return matches[0].security_type == expected_type

    def bar(self, security_id: str, day: date) -> DailyBar | None:
        matches = self._bars.get((security_id, day), ())
        return matches[0] if len(matches) == 1 else None

    def close(self, security_id: str, day: date) -> Decimal | None:
        item = self.bar(security_id, day)
        if item is None or item.close is None:
            return None
        try:
            return adjusted_price(item.close, item.adjustment_factor)
        except AnalysisUnavailable:
            return None

    def nonnegative(
        self,
        security_id: str,
        day: date,
        field: Literal["amount", "turnover_rate", "float_mv"],
    ) -> Decimal | None:
        item = self.bar(security_id, day)
        value = getattr(item, field) if item is not None else None
        if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
            return None
        return value


def _window(market: MarketBatch, count: int) -> tuple[date, ...]:
    try:
        endpoint = market.calendar.sessions.index(market.trade_date)
    except ValueError:
        return ()
    start = endpoint - count + 1
    if start < 0:
        return ()
    return market.calendar.sessions[start : endpoint + 1]


def _coverage(expected: int, valid: int, *, excluded: int = 0) -> FactorCoverage:
    valid = max(min(valid, expected - excluded), 0)
    return FactorCoverage(
        expected=expected,
        valid=valid,
        excluded=excluded,
        missing=expected - valid - excluded,
    )


def _metadata(
    market: MarketBatch,
    benchmark_id: str,
    *,
    cutoff: datetime,
    topic: TopicBatch | None,
) -> dict[str, str]:
    result = {
        "market_source_id": market.source_id,
        "market_source_version": market.source_version,
        "adjustment_version": market.adjustment_version,
        "calendar_version": market.calendar_version,
        "benchmark_id": benchmark_id,
        "trade_date": market.trade_date.isoformat(),
        "knowledge_cutoff": cutoff.isoformat(),
    }
    if topic is not None:
        result.update(
            {
                "topic_source_id": topic.source_id,
                "topic_source_version": topic.source_version,
                "taxonomy_version": topic.taxonomy_version,
                "membership_version": topic.membership_version,
            }
        )
    return result


def _observation(
    factor_id: Literal["FAC-R01", "FAC-R02", "FAC-R03", "FAC-C01", "FAC-C02"],
    metric: str,
    value: Decimal | bool | None,
    unit: str,
    window: str,
    coverage: FactorCoverage,
    *,
    reasons: tuple[str, ...] = (),
    metadata: Mapping[str, str],
) -> FactorObservation:
    return FactorObservation(
        factor_id=factor_id,
        metric=metric,
        value=value,
        unit=unit,
        window=window,
        coverage=coverage,
        reasons=reasons,
        metadata=metadata,
    )


def _complete_closes(
    inputs: _ResearchInput,
    security_id: str,
    days: Sequence[date],
) -> tuple[tuple[Decimal, ...] | None, int]:
    values = tuple(inputs.close(security_id, day) for day in days)
    valid = sum(value is not None for value in values)
    if valid != len(days):
        return None, valid
    return tuple(value for value in values if value is not None), valid


def _complete_nonnegative(
    inputs: _ResearchInput,
    security_id: str,
    days: Sequence[date],
    field: Literal["amount", "turnover_rate", "float_mv"],
) -> tuple[tuple[Decimal, ...] | None, int]:
    values = tuple(inputs.nonnegative(security_id, day, field) for day in days)
    valid = sum(value is not None for value in values)
    if valid != len(days):
        return None, valid
    return tuple(value for value in values if value is not None), valid


def _ratio(numerator: Decimal, denominator: Decimal, operation: str) -> Decimal:
    with _decimal34_operation(operation) as normalize:
        numerator, denominator, one = normalize(
            numerator,
            denominator,
            Decimal(1),
        )
        if denominator <= 0:
            raise AnalysisUnavailable(f"{operation} denominator must be positive")
        return numerator / denominator - one


def _relative_strength(
    inputs: _ResearchInput,
    security_id: str,
    benchmark_id: str,
    window: int,
    metadata: Mapping[str, str],
) -> FactorObservation:
    expected = window + 1
    days = _window(inputs.market, expected)
    reason: str | None = None
    if not inputs.identity_available(security_id, index=False):
        reason = "security_identity_unavailable"
    elif not inputs.identity_available(benchmark_id, index=True):
        reason = "benchmark_identity_unavailable"
    if len(days) != expected:
        reason = reason or "window_incomplete"
    if reason is not None:
        return _observation(
            "FAC-R01",
            f"relative_strength_{window}",
            None,
            "ratio",
            f"T-{window}..T",
            _coverage(expected, 0),
            reasons=(reason,),
            metadata=metadata,
        )
    security, _ = _complete_closes(inputs, security_id, days)
    benchmark, _ = _complete_closes(inputs, benchmark_id, days)
    paired_valid = sum(
        inputs.close(security_id, day) is not None
        and inputs.close(benchmark_id, day) is not None
        for day in days
    )
    if security is None or benchmark is None:
        return _observation(
            "FAC-R01",
            f"relative_strength_{window}",
            None,
            "ratio",
            f"T-{window}..T",
            _coverage(expected, paired_valid),
            reasons=("exact_window_incomplete",),
            metadata=metadata,
        )
    try:
        value = relative_return(
            period_return(security[0], security[-1]),
            period_return(benchmark[0], benchmark[-1]),
        )
    except AnalysisUnavailable:
        return _observation(
            "FAC-R01",
            f"relative_strength_{window}",
            None,
            "ratio",
            f"T-{window}..T",
            _coverage(expected, paired_valid),
            reasons=("factor_arithmetic_unavailable",),
            metadata=metadata,
        )
    return _observation(
        "FAC-R01",
        f"relative_strength_{window}",
        value,
        "ratio",
        f"T-{window}..T",
        _coverage(expected, expected),
        metadata=metadata,
    )


def _moving_average_observations(
    inputs: _ResearchInput,
    security_id: str,
    window: int,
    metadata: Mapping[str, str],
) -> tuple[FactorObservation, FactorObservation]:
    current_days = _window(inputs.market, window)
    current, current_valid = _complete_closes(inputs, security_id, current_days)
    current_value = None
    current_reasons = ("exact_window_incomplete",)
    if len(current_days) == window and current is not None:
        try:
            current_value = _ratio(
                current[-1],
                decimal_mean(current),
                f"close_vs_ma{window}",
            )
            current_reasons = ()
        except AnalysisUnavailable:
            current_reasons = ("factor_arithmetic_unavailable",)
    close_vs_ma = _observation(
        "FAC-R02",
        f"close_vs_ma{window}",
        current_value,
        "ratio",
        f"T-{window - 1}..T",
        _coverage(window, current_valid if len(current_days) == window else 0),
        reasons=current_reasons,
        metadata=metadata,
    )

    slope_expected = window + 5
    slope_days = _window(inputs.market, slope_expected)
    slope_prices, slope_valid = _complete_closes(inputs, security_id, slope_days)
    slope_value = None
    slope_reasons = ("exact_window_incomplete",)
    if len(slope_days) == slope_expected and slope_prices is not None:
        try:
            slope_value = _ratio(
                decimal_mean(slope_prices[-window:]),
                decimal_mean(slope_prices[:window]),
                f"ma{window}_slope_5",
            )
            slope_reasons = ()
        except AnalysisUnavailable:
            slope_reasons = ("factor_arithmetic_unavailable",)
    slope = _observation(
        "FAC-R02",
        f"ma{window}_slope_5",
        slope_value,
        "ratio",
        f"MA{window}(T)/MA{window}(T-5)-1",
        _coverage(
            slope_expected,
            slope_valid if len(slope_days) == slope_expected else 0,
        ),
        reasons=slope_reasons,
        metadata=metadata,
    )
    return close_vs_ma, slope


def _new_high(
    inputs: _ResearchInput,
    security_id: str,
    window: int,
    metadata: Mapping[str, str],
) -> FactorObservation:
    expected = window + 1
    days = _window(inputs.market, expected)
    prices, valid = _complete_closes(inputs, security_id, days)
    if len(days) != expected or prices is None:
        return _observation(
            "FAC-R03",
            f"new_high_{window}",
            None,
            "boolean",
            f"T vs T-{window}..T-1",
            _coverage(expected, valid if len(days) == expected else 0),
            reasons=("exact_window_incomplete",),
            metadata=metadata,
        )
    return _observation(
        "FAC-R03",
        f"new_high_{window}",
        prices[-1] > max(prices[:-1]),
        "boolean",
        f"T vs T-{window}..T-1",
        _coverage(expected, expected),
        metadata=metadata,
    )


def _drawdown(
    inputs: _ResearchInput,
    security_id: str,
    window: int,
    metadata: Mapping[str, str],
) -> FactorObservation:
    days = _window(inputs.market, window)
    prices, valid = _complete_closes(inputs, security_id, days)
    if len(days) != window or prices is None:
        return _observation(
            "FAC-R03",
            f"maximum_drawdown_{window}",
            None,
            "ratio",
            f"T-{window - 1}..T",
            _coverage(window, valid if len(days) == window else 0),
            reasons=("exact_window_incomplete",),
            metadata=metadata,
        )
    try:
        value = maximum_drawdown(prices)
    except AnalysisUnavailable:
        return _observation(
            "FAC-R03",
            f"maximum_drawdown_{window}",
            None,
            "ratio",
            f"T-{window - 1}..T",
            _coverage(window, window),
            reasons=("factor_arithmetic_unavailable",),
            metadata=metadata,
        )
    return _observation(
        "FAC-R03",
        f"maximum_drawdown_{window}",
        value,
        "ratio",
        f"T-{window - 1}..T",
        _coverage(window, window),
        metadata=metadata,
    )


def _series_observation(
    inputs: _ResearchInput,
    security_id: str,
    *,
    metric: str,
    field: Literal["amount", "turnover_rate"],
    unit: str,
    unavailable_reason: str,
    operation,
    metadata: Mapping[str, str],
) -> FactorObservation:
    days = _window(inputs.market, 20)
    values, valid = _complete_nonnegative(inputs, security_id, days, field)
    value = None
    reasons = (unavailable_reason,)
    if len(days) == 20 and values is not None:
        try:
            value = operation(values)
            reasons = ()
        except AnalysisUnavailable:
            reasons = ("factor_arithmetic_unavailable",)
    return _observation(
        "FAC-C01",
        metric,
        value,
        unit,
        "T-19..T",
        _coverage(20, valid if len(days) == 20 else 0),
        reasons=reasons,
        metadata=metadata,
    )


def _topic_share(
    inputs: _ResearchInput,
    security_id: str,
    topic: TopicBatch | None,
    topic_id: str | None,
    metadata: Mapping[str, str],
) -> FactorObservation:
    if topic is None or topic_id is None:
        return _observation(
            "FAC-C01",
            "topic_amount_share",
            None,
            "ratio",
            "T",
            _coverage(1, 0),
            reasons=("topic_batch_unavailable",),
            metadata=metadata,
        )
    day = inputs.market.trade_date
    definitions = tuple(
        item
        for item in topic.topics
        if item.topic_id == topic_id
        and item.available_at <= inputs.cutoff
        and _effective(item.effective_from, item.effective_to, day)
    )
    members = tuple(
        sorted(
            item.security_id
            for item in topic.memberships
            if item.topic_id == topic_id
            and item.available_at <= inputs.cutoff
            and _effective(item.effective_from, item.effective_to, day)
        )
    )
    cells = tuple(
        item
        for item in topic.coverage_cells
        if item.topic_id == topic_id
        and item.trade_date == day
        and item.complete_as_of <= inputs.cutoff
    )
    valid_contract = (
        topic.trade_date == day
        and topic.calendar.sessions == inputs.market.calendar.sessions
        and topic.calendar_version == inputs.market.calendar_version
        and len(definitions) == 1
        and len(cells) == 1
        and cells[0].status == "complete"
        and cells[0].expected_member_count
        == cells[0].received_member_count
        == len(members)
        and security_id in members
    )
    if not valid_contract:
        return _observation(
            "FAC-C01",
            "topic_amount_share",
            None,
            "ratio",
            "T",
            _coverage(max(len(members), 1), 0),
            reasons=("topic_coverage_incomplete",),
            metadata=metadata,
        )
    valid_identities = sum(
        inputs.identity_available(member_id, index=False) for member_id in members
    )
    if valid_identities != len(members):
        return _observation(
            "FAC-C01",
            "topic_amount_share",
            None,
            "ratio",
            "T",
            _coverage(len(members), valid_identities),
            reasons=("topic_member_identity_unavailable",),
            metadata=metadata,
        )
    member_bars = tuple(inputs.bar(member_id, day) for member_id in members)
    valid = sum(
        item is not None
        and item.amount is not None
        and item.amount.is_finite()
        and item.amount >= 0
        for item in member_bars
    )
    if valid != len(members):
        return _observation(
            "FAC-C01",
            "topic_amount_share",
            None,
            "ratio",
            "T",
            _coverage(len(members), valid),
            reasons=("topic_amount_coverage_incomplete",),
            metadata=metadata,
        )
    complete = tuple(item for item in member_bars if item is not None)
    currencies = tuple(item.currency for item in complete)
    amounts = tuple(item.amount for item in complete if item.amount is not None)
    subject_index = members.index(security_id)
    try:
        value = topic_amount_share(
            amounts[subject_index],
            amounts,
            currency=currencies[subject_index],
            eligible_currencies=currencies,
        )
    except AnalysisUnavailable:
        return _observation(
            "FAC-C01",
            "topic_amount_share",
            None,
            "ratio",
            "T",
            _coverage(len(members), len(members)),
            reasons=("topic_amounts_incompatible",),
            metadata=metadata,
        )
    return _observation(
        "FAC-C01",
        "topic_amount_share",
        value,
        "ratio",
        "T",
        _coverage(len(members), len(members)),
        metadata=metadata,
    )


def _float_market_cap(
    inputs: _ResearchInput,
    security_id: str,
    metadata: Mapping[str, str],
) -> FactorObservation:
    value = inputs.nonnegative(security_id, inputs.market.trade_date, "float_mv")
    return _observation(
        "FAC-C01",
        "float_mv",
        value,
        "currency",
        "T",
        _coverage(1, int(value is not None)),
        reasons=() if value is not None else ("float_mv_unavailable",),
        metadata=metadata,
    )


def _amount_change(
    inputs: _ResearchInput,
    security_id: str,
    metadata: Mapping[str, str],
) -> FactorObservation:
    days = _window(inputs.market, 6)
    amounts, valid = _complete_nonnegative(inputs, security_id, days, "amount")
    value = None
    reasons = ("amount_window_incomplete",)
    if len(days) == 6 and amounts is not None:
        try:
            value = _ratio(
                amounts[-1],
                decimal_mean(amounts[:-1]),
                "amount_change_1_vs_prior5",
            )
            reasons = ()
        except AnalysisUnavailable:
            reasons = ("prior_amount_mean_nonpositive",)
    return _observation(
        "FAC-C02",
        "amount_change_1_vs_prior5",
        value,
        "ratio",
        "T vs T-5..T-1",
        _coverage(6, valid if len(days) == 6 else 0),
        reasons=reasons,
        metadata={**metadata, "baseline": "T-5..T-1"},
    )


def _directional_amounts(
    inputs: _ResearchInput,
    security_id: str,
    metadata: Mapping[str, str],
) -> tuple[FactorObservation, FactorObservation]:
    days = _window(inputs.market, 21)
    closes, close_valid = _complete_closes(inputs, security_id, days)
    amount_days = days[1:] if len(days) == 21 else ()
    amounts, amount_valid = _complete_nonnegative(
        inputs,
        security_id,
        amount_days,
        "amount",
    )
    valid = min(max(close_valid - 1, 0), amount_valid)
    groups: dict[str, list[Decimal]] = {"up": [], "down": []}
    if len(days) == 21 and closes is not None and amounts is not None:
        for previous, current, amount in zip(
            closes[:-1],
            closes[1:],
            amounts,
            strict=True,
        ):
            if current > previous:
                groups["up"].append(amount)
            elif current < previous:
                groups["down"].append(amount)
    observations = []
    for direction in ("up", "down"):
        sample = groups[direction]
        if len(days) != 21 or closes is None or amounts is None:
            value = None
            reasons = ("directional_amount_window_incomplete",)
        elif not sample:
            value = None
            reasons = ("not_applicable",)
        else:
            value = decimal_mean(sample)
            reasons = ()
        observations.append(
            _observation(
                "FAC-C02",
                f"{direction}_day_average_amount",
                value,
                "currency",
                "20 return sessions through T",
                _coverage(20, valid if len(days) == 21 else 0),
                reasons=reasons,
                metadata={**metadata, "direction_basis": "adjusted_close_change"},
            )
        )
    return observations[0], observations[1]


def research_role_factors(
    market: MarketBatch,
    security_id: str,
    benchmark_id: str,
    *,
    topic: TopicBatch | None = None,
    topic_id: str | None = None,
) -> tuple[FactorObservation, ...]:
    """Compute reset research observations without applying candidate thresholds."""
    cutoff = min(market.as_of, topic.as_of) if topic is not None else market.as_of
    inputs = _ResearchInput(market, cutoff=cutoff)
    metadata = _metadata(
        market,
        benchmark_id,
        cutoff=cutoff,
        topic=topic,
    )
    result: list[FactorObservation] = []
    result.extend(
        _relative_strength(inputs, security_id, benchmark_id, window, metadata)
        for window in (20, 60, 120)
    )
    for window in (20, 60):
        result.extend(
            _moving_average_observations(
                inputs,
                security_id,
                window,
                metadata,
            )
        )
    result.extend(
        _new_high(inputs, security_id, window, metadata) for window in (60, 120)
    )
    result.extend(
        _drawdown(inputs, security_id, window, metadata) for window in (20, 60)
    )
    result.append(
        _series_observation(
            inputs,
            security_id,
            metric="average_amount_20",
            field="amount",
            unit="currency",
            unavailable_reason="amount_window_incomplete",
            operation=decimal_mean,
            metadata=metadata,
        )
    )
    result.append(_topic_share(inputs, security_id, topic, topic_id, metadata))
    result.append(
        _series_observation(
            inputs,
            security_id,
            metric="turnover_stability_20",
            field="turnover_rate",
            unit="ratio",
            unavailable_reason="turnover_rate_unavailable",
            operation=mean_absolute_deviation_rate,
            metadata=metadata,
        )
    )
    result.append(_float_market_cap(inputs, security_id, metadata))
    result.append(_amount_change(inputs, security_id, metadata))
    result.extend(_directional_amounts(inputs, security_id, metadata))
    if not inputs.identity_available(security_id, index=False):
        result = [
            FactorObservation(
                factor_id=item.factor_id,
                metric=item.metric,
                value=None,
                unit=item.unit,
                window=item.window,
                coverage=_coverage(item.coverage.expected, 0),
                reasons=("security_identity_unavailable",),
                metadata=item.metadata,
            )
            for item in result
        ]
    return tuple(result)


__all__ = [
    "FactorCoverage",
    "FactorObservation",
    "research_role_factors",
]
