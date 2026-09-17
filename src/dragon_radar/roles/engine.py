"""Formal deterministic evaluators for trend and core topic roles."""

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from dragon_radar.domain.models import DailyBar, SecurityState
from dragon_radar.domain.quality import grant_allows_local
from dragon_radar.roles.factors import (
    AnalysisUnavailable,
    _decimal34_operation,
    adjusted_price,
    decimal_mean,
    decimal_median,
    maximum_drawdown,
    mean_absolute_deviation_rate,
    period_return,
    relative_return,
    topic_amount_share,
)
from dragon_radar.roles.models import (
    RankedCandidate,
    RoleCandidate,
    RoleContext,
    RoleEvaluation,
    RoleName,
    RoleTopicStatus,
)
from dragon_radar.roles.rules import RULE_DEFINITIONS
from dragon_radar.topics.models import (
    TopicCoverageCell,
    TopicDefinition,
    TopicMembership,
)

SortKey = tuple[Decimal, ...]
Eligibility = Literal["valid", "legal_excluded", "missing", "cross_exchange"]


def dense_rank(
    rows: Sequence[RoleCandidate],
    key: Callable[[RoleCandidate], SortKey],
) -> tuple[RankedCandidate, ...]:
    """Sort by an exact business key and assign one-based dense ranks."""
    keyed_rows: list[tuple[SortKey, RoleCandidate]] = []
    with _decimal34_operation("dense_rank") as normalize:
        for row in rows:
            business_key = key(row)
            if any(
                not isinstance(value, Decimal) or not value.is_finite()
                for value in business_key
            ):
                raise AnalysisUnavailable(
                    "dense_rank key items must be finite Decimals"
                )
            keyed_rows.append((normalize(*business_key), row))
    keyed = tuple(keyed_rows)
    ordered = sorted(keyed, key=lambda item: (item[0], item[1].security_id))
    result: list[RankedCandidate] = []
    previous_key: SortKey | None = None
    rank = 0
    for business_key, row in ordered:
        if previous_key is None or business_key != previous_key:
            rank += 1
            previous_key = business_key
        result.append(
            RankedCandidate(
                topic_id=row.topic_id,
                security_id=row.security_id,
                role=row.role,
                status=row.status,
                factors=row.factors,
                reasons=row.reasons,
                tie_rank=rank,
            )
        )
    return tuple(result)


def _first_tie_group_candidates(
    rows: tuple[RankedCandidate, ...],
) -> tuple[RankedCandidate, ...]:
    return tuple(
        row
        if row.tie_rank == 1 or row.status != "candidate"
        else row.model_copy(
            update={
                "status": "eligible_non_candidate",
                "reasons": (*row.reasons, "not_first_tie_group"),
            }
        )
        for row in rows
    )


@dataclass(frozen=True)
class _PreparedContext:
    context: RoleContext
    topic_id: str
    topic_definition: TopicDefinition | None
    pool: frozenset[str]
    topic_definitions: tuple[TopicDefinition, ...]
    memberships: tuple[TopicMembership, ...]
    coverage_by_session: dict[date, tuple[TopicCoverageCell, ...]]
    coverage_actual_member_counts: dict[tuple[date, datetime], int | None]
    members_by_session: dict[date, tuple[str, ...]]
    member_ids_by_session: dict[date, frozenset[str]]
    states: dict[str, tuple[SecurityState, ...]]
    bars: dict[tuple[str, date], tuple[DailyBar, ...]]

    @classmethod
    def build(cls, context: RoleContext, topic_id: str) -> "_PreparedContext":
        cutoff = context.knowledge_cutoff
        topic_definitions = tuple(
            definition
            for definition in context.topic.topics
            if definition.topic_id == topic_id and definition.available_at <= cutoff
        )
        definitions = tuple(
            definition
            for definition in topic_definitions
            if _effective(
                definition.effective_from,
                definition.effective_to,
                context.market.trade_date,
            )
        )
        topic_definition = definitions[0] if len(definitions) == 1 else None
        memberships = tuple(
            membership
            for membership in context.topic.memberships
            if membership.topic_id == topic_id and membership.available_at <= cutoff
        )
        sessions = context.market.calendar.sessions
        endpoint = bisect_right(sessions, context.market.trade_date)
        indexed_days = sessions[max(0, endpoint - 61) : endpoint]
        if context.market.trade_date not in indexed_days:
            indexed_days = (*indexed_days[-60:], context.market.trade_date)
        indexed_day_set = frozenset(indexed_days)
        coverage_groups: dict[date, list[TopicCoverageCell]] = {}
        for cell in context.topic.coverage_cells:
            if (
                cell.topic_id == topic_id
                and cell.complete_as_of <= cutoff
                and cell.trade_date in indexed_day_set
            ):
                coverage_groups.setdefault(cell.trade_date, []).append(cell)
        coverage_by_session = {
            day: tuple(cells) for day, cells in coverage_groups.items()
        }
        coverage_actual_member_counts: dict[
            tuple[date, datetime], int | None
        ] = {}
        for day, cells in coverage_by_session.items():
            for complete_as_of in {cell.complete_as_of for cell in cells}:
                visible_definitions = tuple(
                    definition
                    for definition in topic_definitions
                    if definition.available_at <= complete_as_of
                    and _effective(
                        definition.effective_from,
                        definition.effective_to,
                        day,
                    )
                )
                actual_count = None
                if len(visible_definitions) == 1:
                    actual_count = len(
                        {
                            membership.security_id
                            for membership in memberships
                            if membership.available_at <= complete_as_of
                            and _effective(
                                membership.effective_from,
                                membership.effective_to,
                                day,
                            )
                        }
                    )
                coverage_actual_member_counts[(day, complete_as_of)] = actual_count
        members_by_session: dict[date, tuple[str, ...]] = {}
        member_ids_by_session: dict[date, frozenset[str]] = {}
        for day in indexed_days:
            members = (
                tuple(
                    sorted(
                        {
                            membership.security_id
                            for membership in memberships
                            if _effective(
                                membership.effective_from,
                                membership.effective_to,
                                day,
                            )
                        }
                    )
                )
                if topic_definition is not None
                and _effective(
                    topic_definition.effective_from,
                    topic_definition.effective_to,
                    day,
                )
                else ()
            )
            members_by_session[day] = members
            member_ids_by_session[day] = frozenset(members)
        state_groups: dict[str, list[SecurityState]] = {}
        for security_state in context.market.security_states:
            if (
                security_state.available_at <= cutoff
                and security_state.source_id == context.market.source_id
                and security_state.source_version == context.market.source_version
                and security_state.pool_version == context.market.pool_version
            ):
                state_groups.setdefault(security_state.security_id, []).append(
                    security_state
                )
        bar_groups: dict[tuple[str, date], list[DailyBar]] = {}
        for daily_bar in context.market.bars:
            if (
                daily_bar.available_at <= cutoff
                and daily_bar.source_id == context.market.source_id
                and daily_bar.source_version == context.market.source_version
                and daily_bar.adjustment_version
                == context.market.adjustment_version
            ):
                bar_groups.setdefault(
                    (daily_bar.security_id, daily_bar.trade_date), []
                ).append(daily_bar)
        return cls(
            context=context,
            topic_id=topic_id,
            topic_definition=topic_definition,
            pool=frozenset(context.market.expected_security_ids),
            topic_definitions=topic_definitions,
            memberships=memberships,
            coverage_by_session=coverage_by_session,
            coverage_actual_member_counts=coverage_actual_member_counts,
            members_by_session=members_by_session,
            member_ids_by_session=member_ids_by_session,
            states={
                security_id: tuple(values)
                for security_id, values in state_groups.items()
            },
            bars={key: tuple(values) for key, values in bar_groups.items()},
        )

    @property
    def trade_date(self) -> date:
        return self.context.market.trade_date

    def members_on(self, day: date) -> tuple[str, ...]:
        return self.members_by_session.get(day, ())

    def has_member_on(self, security_id: str, day: date) -> bool:
        return security_id in self.member_ids_by_session.get(day, frozenset())

    def state_on(self, security_id: str, day: date) -> SecurityState | None:
        matches = tuple(
            security_state
            for security_state in self.states.get(security_id, ())
            if _effective(
                security_state.effective_from,
                security_state.effective_to,
                day,
            )
        )
        return matches[0] if len(matches) == 1 else None

    def bar_on(self, security_id: str, day: date) -> DailyBar | None:
        matches = self.bars.get((security_id, day), ())
        return matches[0] if len(matches) == 1 else None


def _effective(start: date, end: date | None, day: date) -> bool:
    return start <= day and (end is None or day < end)


def _listed_session_count(
    sessions: Sequence[date],
    *,
    listing_date: date,
    day: date,
) -> int:
    return bisect_right(sessions, day) - bisect_left(sessions, listing_date)


def _role_status(
    *,
    topic_id: str,
    role: RoleName,
    status: Literal["ready", "insufficient", "no_candidate"],
    expected: int,
    valid: int,
    legal_excluded: int,
    missing: int,
    reasons: Sequence[str],
) -> RoleTopicStatus:
    return RoleTopicStatus(
        topic_id=topic_id,
        role=role,
        capability="ready",
        status=status,
        expected=expected,
        valid=valid,
        legal_excluded=legal_excluded,
        missing=missing,
        reasons=tuple(reasons),
    )


def _insufficient(
    prepared: _PreparedContext,
    role: RoleName,
    *,
    reasons: Sequence[str],
    expected: int | None = None,
    classifications: dict[str, Eligibility] | None = None,
) -> RoleEvaluation:
    if expected is None:
        expected = _observation_expected_member_count(prepared)
    legal_excluded, missing = _partition_counts(
        expected,
        classifications or {},
        valid=0,
    )
    return RoleEvaluation(
        topic_status=_role_status(
            topic_id=prepared.topic_id,
            role=role,
            status="insufficient",
            expected=expected,
            valid=0,
            legal_excluded=legal_excluded,
            missing=missing,
            reasons=reasons,
        )
    )


def _observation_expected_member_count(prepared: _PreparedContext) -> int:
    known_member_count = len(prepared.members_on(prepared.trade_date))
    visible_cells = prepared.coverage_by_session.get(prepared.trade_date, ())
    if len(visible_cells) != 1:
        return known_member_count
    return max(known_member_count, visible_cells[0].expected_member_count)


def _partition_counts(
    expected: int,
    classifications: dict[str, Eligibility],
    *,
    valid: int,
) -> tuple[int, int]:
    legal_excluded = sum(
        classification == "legal_excluded"
        for classification in classifications.values()
    )
    known_missing = sum(
        classification == "missing" for classification in classifications.values()
    )
    unresolved_known = len(classifications) - valid - legal_excluded - known_missing
    unreceived = max(expected - len(classifications), 0)
    return legal_excluded, known_missing + unresolved_known + unreceived


def _global_reasons(prepared: _PreparedContext) -> tuple[str, ...]:
    context = prepared.context
    reasons: list[str] = []
    if prepared.topic_definition is None:
        reasons.append("topic_unavailable")
    if (
        context.market.calendar.exchange != context.topic.exchange
        or context.topic.calendar.exchange != context.topic.exchange
    ):
        reasons.append("cross_exchange")
    if context.market.calendar_version != context.market.calendar.version:
        reasons.append("market_calendar_version_mismatch")
    if context.topic.calendar_version != context.topic.calendar.version:
        reasons.append("topic_calendar_version_mismatch")
    if context.market.calendar.version != context.topic.calendar.version:
        reasons.append("market_topic_calendar_version_mismatch")
    if context.market.calendar.sessions != context.topic.calendar.sessions:
        reasons.append("market_topic_calendar_sessions_mismatch")
    for source, grant, source_id in (
        ("market", context.market.grant, context.market.source_id),
        ("topic", context.topic.grant, context.topic.source_id),
    ):
        if not grant_allows_local(
            grant,
            source_id=source_id,
            now=context.knowledge_cutoff,
        ):
            reasons.append(f"{source}_source_not_materializable")
    return tuple(reasons)


def _window(prepared: _PreparedContext, observations: int) -> tuple[date, ...]:
    sessions = prepared.context.market.calendar.sessions
    try:
        endpoint = sessions.index(prepared.trade_date)
    except ValueError:
        return ()
    start = endpoint - observations + 1
    if start < 0:
        return ()
    return sessions[start : endpoint + 1]


def _state_eligibility(
    prepared: _PreparedContext,
    security_id: str,
    day: date,
    *,
    check_listing: bool,
) -> tuple[Eligibility, str | None]:
    if security_id not in prepared.pool:
        return "missing", f"{security_id}: outside declared market pool"
    security_state = prepared.state_on(security_id, day)
    if security_state is None:
        return "missing", f"{security_id}: missing effective security state"
    if security_state.exchange != prepared.context.market.calendar.exchange:
        return "cross_exchange", f"{security_id}: cross_exchange"
    if (
        security_state.security_type is None
        or security_state.board_category is None
        or security_state.is_delisting is None
        or security_state.suspended is None
        or security_state.is_st is None
    ):
        return "missing", f"{security_id}: missing required security state"
    if security_state.listing_date is None:
        return "missing", f"{security_id}: missing listing date"
    if security_state.listing_date > day:
        return "missing", f"{security_id}: listing date is after observation day"
    allowed_boards = RULE_DEFINITIONS[prepared.context.rule_version]["allowed_boards"]
    if (
        security_state.security_type != "common_stock"
        or security_state.board_category not in allowed_boards
        or security_state.is_st
        or security_state.is_delisting
        or security_state.suspended
    ):
        return "legal_excluded", f"{security_id}: ineligible security state"
    if not check_listing:
        return "valid", None
    sessions = prepared.context.market.calendar.sessions
    minimum = RULE_DEFINITIONS[prepared.context.rule_version][
        "minimum_listing_sessions"
    ]
    if security_state.listing_date < sessions[0]:
        return "missing", f"{security_id}: incomplete listing calendar"
    listed_sessions = _listed_session_count(
        sessions,
        listing_date=security_state.listing_date,
        day=day,
    )
    if listed_sessions >= minimum:
        return "valid", None
    return "legal_excluded", f"{security_id}: insufficient listing history"


def _classify_current_members(
    prepared: _PreparedContext,
) -> tuple[dict[str, Eligibility], list[str]]:
    classifications: dict[str, Eligibility] = {}
    reasons: list[str] = []
    for security_id in prepared.members_on(prepared.trade_date):
        classification, reason = _state_eligibility(
            prepared,
            security_id,
            prepared.trade_date,
            check_listing=True,
        )
        classifications[security_id] = classification
        if reason is not None:
            reasons.append(reason)
    return classifications, reasons


def _cross_exchange_result(
    prepared: _PreparedContext,
    role: RoleName,
    *,
    expected: int,
    classifications: dict[str, Eligibility],
    reasons: Sequence[str],
) -> RoleEvaluation | None:
    if "cross_exchange" not in classifications.values():
        return None
    return _insufficient(
        prepared,
        role,
        reasons=("cross_exchange", *reasons),
        expected=expected,
        classifications=classifications,
    )


def _global_failure_result(
    prepared: _PreparedContext,
    role: RoleName,
    *,
    expected: int,
    global_reasons: Sequence[str],
    classifications: dict[str, Eligibility],
    member_reasons: Sequence[str],
) -> RoleEvaluation:
    cross_exchange_reasons = (
        ("cross_exchange",)
        if "cross_exchange" in classifications.values()
        else ()
    )
    reasons = tuple(
        dict.fromkeys((*global_reasons, *cross_exchange_reasons, *member_reasons))
    )
    return _insufficient(
        prepared,
        role,
        reasons=reasons,
        expected=expected,
        classifications=classifications,
    )


def _prices_for(
    prepared: _PreparedContext,
    security_id: str,
    sessions: Sequence[date],
) -> tuple[Decimal, ...] | None:
    prices: list[Decimal] = []
    for day in sessions:
        daily_bar = prepared.bar_on(security_id, day)
        if daily_bar is None or daily_bar.close is None:
            return None
        try:
            prices.append(
                adjusted_price(daily_bar.close, daily_bar.adjustment_factor)
            )
        except AnalysisUnavailable:
            return None
    return tuple(prices)


def _continuous_trend_eligibility(
    prepared: _PreparedContext,
    security_id: str,
    sessions: Sequence[date],
    *,
    require_membership: bool,
) -> tuple[Eligibility, str | None]:
    if require_membership and any(
        not prepared.has_member_on(security_id, day) for day in sessions
    ):
        return "missing", f"{security_id}: membership is not continuous for 61 sessions"
    for day in sessions:
        classification, reason = _state_eligibility(
            prepared,
            security_id,
            day,
            check_listing=False,
        )
        if classification != "valid":
            return classification, reason
    if _prices_for(prepared, security_id, sessions) is None:
        return "missing", f"{security_id}: missing 61 adjusted prices"
    return "valid", None


def _market_return_median(
    prepared: _PreparedContext,
    sessions: Sequence[date],
) -> Decimal | None:
    returns: list[Decimal] = []
    for security_id in sorted(prepared.pool):
        current_classification, _ = _state_eligibility(
            prepared,
            security_id,
            prepared.trade_date,
            check_listing=True,
        )
        if current_classification != "valid":
            continue
        classification, _ = _continuous_trend_eligibility(
            prepared,
            security_id,
            sessions,
            require_membership=False,
        )
        if classification != "valid":
            continue
        prices = _prices_for(prepared, security_id, sessions)
        if prices is not None:
            try:
                returns.append(period_return(prices[0], prices[-1]))
            except AnalysisUnavailable:
                continue
    if not returns:
        return None
    try:
        return decimal_median(returns)
    except AnalysisUnavailable:
        return None


def evaluate_trend(context: RoleContext, topic_id: str) -> RoleEvaluation:
    """Evaluate the formal trend role for one topic."""
    prepared = _PreparedContext.build(context, topic_id)
    expected = _observation_expected_member_count(prepared)
    classifications, reasons = _classify_current_members(prepared)
    global_reasons = _global_reasons(prepared)
    if global_reasons:
        return _global_failure_result(
            prepared,
            "trend",
            expected=expected,
            global_reasons=global_reasons,
            classifications=classifications,
            member_reasons=reasons,
        )
    cross_exchange = _cross_exchange_result(
        prepared,
        "trend",
        expected=expected,
        classifications=classifications,
        reasons=reasons,
    )
    if cross_exchange is not None:
        return cross_exchange
    sessions = _window(prepared, 61)
    if len(sessions) != 61:
        return _insufficient(
            prepared,
            "trend",
            reasons=(*reasons, "market_calendar_missing_61_sessions"),
            expected=expected,
            classifications=classifications,
        )

    for day in sessions:
        if not _coverage_complete(prepared, day):
            return _insufficient(
                prepared,
                "trend",
                reasons=(
                    *reasons,
                    f"{day.isoformat()}: topic coverage incomplete",
                ),
                expected=expected,
                classifications=classifications,
            )

    for security_id, classification in tuple(classifications.items()):
        if classification != "valid":
            continue
        classification, reason = _continuous_trend_eligibility(
            prepared,
            security_id,
            sessions,
            require_membership=True,
        )
        classifications[security_id] = classification
        if reason is not None:
            reasons.append(reason)

    cross_exchange = _cross_exchange_result(
        prepared,
        "trend",
        expected=expected,
        classifications=classifications,
        reasons=reasons,
    )
    if cross_exchange is not None:
        return cross_exchange

    benchmark60 = _market_return_median(prepared, sessions)
    benchmark20 = _market_return_median(prepared, sessions[-21:])
    valid_ids = tuple(
        sorted(
            security_id
            for security_id, classification in classifications.items()
            if classification == "valid"
        )
    )
    minimum = RULE_DEFINITIONS[context.rule_version]["minimum_topic_members"]
    if benchmark60 is None or benchmark20 is None:
        return _insufficient(
            prepared,
            "trend",
            reasons=(*reasons, "market_benchmark_unavailable"),
            expected=expected,
            classifications=classifications,
        )

    raw_factors: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    for security_id in valid_ids:
        prices = _prices_for(prepared, security_id, sessions)
        if prices is None:
            raise AssertionError("validated trend prices became unavailable")
        try:
            raw_factors[security_id] = (
                relative_return(
                    period_return(prices[0], prices[-1]),
                    benchmark60,
                ),
                relative_return(
                    period_return(prices[-21], prices[-1]),
                    benchmark20,
                ),
                maximum_drawdown(prices),
            )
        except AnalysisUnavailable as exc:
            classifications[security_id] = "missing"
            reasons.append(f"{security_id}: {exc}")

    valid_ids = tuple(sorted(raw_factors))
    legal_excluded, missing = _partition_counts(
        expected,
        classifications,
        valid=len(valid_ids),
    )
    if len(valid_ids) < minimum:
        return RoleEvaluation(
            topic_status=_role_status(
                topic_id=topic_id,
                role="trend",
                status="insufficient",
                expected=expected,
                valid=len(valid_ids),
                legal_excluded=legal_excluded,
                missing=missing,
                reasons=(*reasons, "minimum_valid_members_not_met"),
            )
        )
    try:
        topic_mdd = decimal_median(
            tuple(factors[2] for factors in raw_factors.values())
        )
    except AnalysisUnavailable:
        return _insufficient(
            prepared,
            "trend",
            reasons=(*reasons, "topic_mdd_median_unavailable"),
            expected=expected,
            classifications=classifications,
        )
    rows = tuple(
        RoleCandidate(
            topic_id=topic_id,
            security_id=security_id,
            role="trend",
            status=(
                "candidate"
                if factors[0] > 0
                and factors[1] > 0
                and factors[2] <= topic_mdd
                else "eligible_non_candidate"
            ),
            factors=factors,
            reasons=(
                ("candidate_conditions_met",)
                if factors[0] > 0
                and factors[1] > 0
                and factors[2] <= topic_mdd
                else ("candidate_conditions_not_met",)
            ),
        )
        for security_id, factors in raw_factors.items()
    )
    try:
        ranked = dense_rank(
            rows,
            key=lambda row: (-row.factors[0], -row.factors[1], row.factors[2]),
        )
    except AnalysisUnavailable:
        return _insufficient(
            prepared,
            "trend",
            reasons=(*reasons, "trend ranking unavailable"),
            expected=expected,
            classifications=classifications,
        )
    ranked = _first_tie_group_candidates(ranked)
    has_candidate = any(row.status == "candidate" for row in ranked)
    return RoleEvaluation(
        topic_status=_role_status(
            topic_id=topic_id,
            role="trend",
            status="ready" if has_candidate else "no_candidate",
            expected=expected,
            valid=len(valid_ids),
            legal_excluded=legal_excluded,
            missing=missing,
            reasons=reasons,
        ),
        candidates=ranked,
    )


def _coverage_complete(
    prepared: _PreparedContext,
    day: date,
) -> bool:
    topic = prepared.context.topic
    if (
        not topic.coverage_from <= day <= topic.coverage_to
        or day not in topic.calendar.sessions
    ):
        return False
    matches = prepared.coverage_by_session.get(day, ())
    if len(matches) != 1:
        return False
    cell = matches[0]
    if (
        cell.status != "complete"
        or cell.received_member_count != cell.expected_member_count
    ):
        return False
    actual_member_count = prepared.coverage_actual_member_counts.get(
        (day, cell.complete_as_of)
    )
    return (
        actual_member_count is not None
        and cell.received_member_count == actual_member_count
    )


def _amount_on(
    prepared: _PreparedContext,
    security_id: str,
    day: date,
) -> tuple[Decimal, str] | None:
    daily_bar = prepared.bar_on(security_id, day)
    if daily_bar is None or daily_bar.amount is None:
        return None
    if not daily_bar.amount.is_finite() or daily_bar.amount < 0:
        return None
    return daily_bar.amount, daily_bar.currency


def evaluate_core(context: RoleContext, topic_id: str) -> RoleEvaluation:
    """Evaluate the formal core role for one topic."""
    prepared = _PreparedContext.build(context, topic_id)
    expected = _observation_expected_member_count(prepared)
    classifications, reasons = _classify_current_members(prepared)
    global_reasons = _global_reasons(prepared)
    if global_reasons:
        return _global_failure_result(
            prepared,
            "core",
            expected=expected,
            global_reasons=global_reasons,
            classifications=classifications,
            member_reasons=reasons,
        )
    cross_exchange = _cross_exchange_result(
        prepared,
        "core",
        expected=expected,
        classifications=classifications,
        reasons=reasons,
    )
    if cross_exchange is not None:
        return cross_exchange
    sessions = _window(prepared, 20)
    if len(sessions) != 20:
        return _insufficient(
            prepared,
            "core",
            reasons=(*reasons, "market_calendar_missing_20_sessions"),
            expected=expected,
            classifications=classifications,
        )

    daily_denominators: dict[date, tuple[tuple[Decimal, ...], tuple[str, ...]]] = {}
    denominator_failure: str | None = None
    for day in sessions:
        if not _coverage_complete(prepared, day):
            denominator_failure = f"{day.isoformat()}: topic coverage incomplete"
            break
        daily_amounts: list[Decimal] = []
        daily_currencies: list[str] = []
        for security_id in prepared.members_on(day):
            classification, eligibility_reason = _state_eligibility(
                prepared,
                security_id,
                day,
                check_listing=True,
            )
            if classification == "legal_excluded":
                continue
            if classification != "valid":
                denominator_failure = (
                    f"{eligibility_reason or f'{security_id}: ineligible'} "
                    f"on {day.isoformat()}"
                )
                break
            amount = _amount_on(prepared, security_id, day)
            if amount is None:
                denominator_failure = (
                    f"{security_id}: incomplete topic amount coverage on "
                    f"{day.isoformat()}"
                )
                break
            daily_amounts.append(amount[0])
            daily_currencies.append(amount[1])
        if denominator_failure is not None:
            break
        if not daily_amounts:
            denominator_failure = (
                f"{day.isoformat()}: topic amount denominator unavailable/empty"
            )
            break
        if len(set(daily_currencies)) != 1:
            denominator_failure = f"{day.isoformat()}: currency mismatch"
            break
        try:
            topic_amount_share(
                daily_amounts[0],
                daily_amounts,
                currency=daily_currencies[0],
                eligible_currencies=daily_currencies,
            )
        except AnalysisUnavailable:
            denominator_failure = (
                f"{day.isoformat()}: "
                "topic amount denominator nonpositive or unavailable"
            )
            break
        daily_denominators[day] = (tuple(daily_amounts), tuple(daily_currencies))
    if denominator_failure is not None:
        return _insufficient(
            prepared,
            "core",
            reasons=(*reasons, denominator_failure),
            expected=expected,
            classifications=classifications,
        )

    raw_factors: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    for security_id, classification in tuple(classifications.items()):
        if classification != "valid":
            continue
        share_sessions = sessions[-5:]
        if any(
            not prepared.has_member_on(security_id, day)
            or _state_eligibility(
                prepared,
                security_id,
                day,
                check_listing=True,
            )[0]
            != "valid"
            for day in share_sessions
        ):
            classifications[security_id] = "missing"
            reasons.append(
                f"{security_id}: incomplete membership in 5-session share window"
            )
            continue
        observations = tuple(
            _amount_on(prepared, security_id, day) for day in sessions
        )
        if any(observation is None for observation in observations):
            classifications[security_id] = "missing"
            reasons.append(f"{security_id}: missing 20 daily amounts")
            continue
        complete = tuple(observation for observation in observations if observation)
        currencies = tuple(observation[1] for observation in complete)
        if len(set(currencies)) != 1:
            classifications[security_id] = "missing"
            reasons.append(f"{security_id}: currency mismatch across 20 sessions")
            continue
        values = tuple(observation[0] for observation in complete)
        try:
            shares = tuple(
                topic_amount_share(
                    values[index],
                    daily_denominators[day][0],
                    currency=currencies[index],
                    eligible_currencies=daily_denominators[day][1],
                )
                for index, day in enumerate(share_sessions, start=15)
            )
            raw_factors[security_id] = (
                decimal_mean(shares),
                decimal_mean(values),
                mean_absolute_deviation_rate(values),
            )
        except AnalysisUnavailable as exc:
            classifications[security_id] = "missing"
            reasons.append(f"{security_id}: {exc}")

    valid_ids = tuple(sorted(raw_factors))
    minimum = RULE_DEFINITIONS[context.rule_version]["minimum_topic_members"]
    legal_excluded, missing = _partition_counts(
        expected,
        classifications,
        valid=len(valid_ids),
    )
    if len(valid_ids) < minimum:
        return RoleEvaluation(
            topic_status=_role_status(
                topic_id=topic_id,
                role="core",
                status="insufficient",
                expected=expected,
                valid=len(valid_ids),
                legal_excluded=legal_excluded,
                missing=missing,
                reasons=(*reasons, "minimum_valid_members_not_met"),
            )
        )

    share_median = decimal_median(
        tuple(factors[0] for factors in raw_factors.values())
    )
    amount_median = decimal_median(
        tuple(factors[1] for factors in raw_factors.values())
    )
    rows = tuple(
        RoleCandidate(
            topic_id=topic_id,
            security_id=security_id,
            role="core",
            status=(
                "candidate"
                if factors[0] >= share_median and factors[1] >= amount_median
                else "eligible_non_candidate"
            ),
            factors=factors,
            reasons=(
                ("candidate_conditions_met",)
                if factors[0] >= share_median and factors[1] >= amount_median
                else ("candidate_conditions_not_met",)
            ),
        )
        for security_id, factors in raw_factors.items()
    )
    try:
        ranked = dense_rank(
            rows,
            key=lambda row: (-row.factors[0], -row.factors[1], row.factors[2]),
        )
    except AnalysisUnavailable:
        return _insufficient(
            prepared,
            "core",
            reasons=(*reasons, "core ranking unavailable"),
            expected=expected,
            classifications=classifications,
        )
    ranked = _first_tie_group_candidates(ranked)
    has_candidate = any(row.status == "candidate" for row in ranked)
    return RoleEvaluation(
        topic_status=_role_status(
            topic_id=topic_id,
            role="core",
            status="ready" if has_candidate else "no_candidate",
            expected=expected,
            valid=len(valid_ids),
            legal_excluded=legal_excluded,
            missing=missing,
            reasons=reasons,
        ),
        candidates=ranked,
    )
