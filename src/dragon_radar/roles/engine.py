"""Formal deterministic evaluators for trend and core topic roles."""

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import ConfigDict, model_validator

from dragon_radar.domain.models import DailyBar, FrozenModel, Identifier, SecurityState
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
    ROLE_CAPABILITIES,
    RankedCandidate,
    RoleCandidate,
    RoleContext,
    RoleEvaluation,
    RoleName,
    RoleRunStatus,
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


class RelayPredecessorRun(FrozenModel):
    """Immutable Task 6 projection of the run selected by an activation event."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    role_run_id: Identifier
    activation_event_id: Identifier
    trade_date: date
    active_run_count: Literal[1]
    rule_version: Literal["role-v1", "role-test-v1"]
    visibility_mode: Literal["contemporaneous", "retrospective"]
    market_benchmark_definition: Identifier
    topic_id: Identifier
    topic_source_id: Identifier
    topic_source_version: Identifier
    taxonomy_version: Identifier
    membership_version: Identifier
    member_ids: tuple[Identifier, ...]
    trend_evaluation: RoleEvaluation

    @model_validator(mode="after")
    def consistent_trend_identity(self):
        evaluation = self.trend_evaluation
        member_ids = frozenset(self.member_ids)
        if len(member_ids) != len(self.member_ids):
            raise ValueError("member_ids must not contain duplicates")
        if (
            evaluation.topic_status.topic_id != self.topic_id
            or evaluation.topic_status.role != "trend"
            or any(
                row.topic_id != self.topic_id or row.role != "trend"
                for row in evaluation.candidates
            )
        ):
            raise ValueError(
                "trend_evaluation topic and role must match predecessor topic"
            )
        if any(row.security_id not in member_ids for row in evaluation.candidates):
            raise ValueError(
                "trend_evaluation security_id must belong to predecessor member_ids"
            )
        return self


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
        coverage_actual_member_counts: dict[tuple[date, datetime], int | None] = {}
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
                and daily_bar.adjustment_version == context.market.adjustment_version
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
    status: RoleRunStatus,
    expected: int,
    valid: int,
    legal_excluded: int,
    missing: int,
    reasons: Sequence[str],
) -> RoleTopicStatus:
    return RoleTopicStatus(
        topic_id=topic_id,
        role=role,
        capability=ROLE_CAPABILITIES[role],
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
    valid: int = 0,
) -> RoleEvaluation:
    if expected is None:
        expected = _observation_expected_member_count(prepared)
    legal_excluded, missing = _partition_counts(
        expected,
        classifications or {},
        valid=valid,
    )
    return RoleEvaluation(
        topic_status=_role_status(
            topic_id=prepared.topic_id,
            role=role,
            status="insufficient",
            expected=expected,
            valid=valid,
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


def _staged_member_state_eligibility(
    prepared: _PreparedContext,
    security_id: str,
    day: date,
) -> tuple[Eligibility, str | None]:
    security_state = prepared.state_on(security_id, day)
    if (
        security_state is not None
        and security_state.exchange != prepared.context.market.calendar.exchange
    ):
        return "cross_exchange", f"{security_id}: cross_exchange"
    return _state_eligibility(
        prepared,
        security_id,
        day,
        check_listing=False,
    )


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
    valid: int = 0,
) -> RoleEvaluation | None:
    if "cross_exchange" not in classifications.values():
        return None
    return _insufficient(
        prepared,
        role,
        reasons=("cross_exchange", *reasons),
        expected=expected,
        classifications=classifications,
        valid=valid,
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
        ("cross_exchange",) if "cross_exchange" in classifications.values() else ()
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
            prices.append(adjusted_price(daily_bar.close, daily_bar.adjustment_factor))
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
                if factors[0] > 0 and factors[1] > 0 and factors[2] <= topic_mdd
                else "eligible_non_candidate"
            ),
            factors=factors,
            reasons=(
                ("candidate_conditions_met",)
                if factors[0] > 0 and factors[1] > 0 and factors[2] <= topic_mdd
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
        observations = tuple(_amount_on(prepared, security_id, day) for day in sessions)
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

    share_median = decimal_median(tuple(factors[0] for factors in raw_factors.values()))
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


def _staged_preflight(
    context: RoleContext,
    topic_id: str,
    role: RoleName,
) -> tuple[
    _PreparedContext,
    int,
    dict[str, Eligibility],
    list[str],
    RoleEvaluation | None,
]:
    prepared = _PreparedContext.build(context, topic_id)
    expected = _observation_expected_member_count(prepared)
    classifications, reasons = _classify_current_members(prepared)
    global_reasons = _global_reasons(prepared)
    if global_reasons:
        failure = _global_failure_result(
            prepared,
            role,
            expected=expected,
            global_reasons=global_reasons,
            classifications=classifications,
            member_reasons=reasons,
        )
        return prepared, expected, classifications, reasons, failure
    failure = _cross_exchange_result(
        prepared,
        role,
        expected=expected,
        classifications=classifications,
        reasons=reasons,
    )
    return prepared, expected, classifications, reasons, failure


def _complete_coverage_window(
    prepared: _PreparedContext,
    sessions: Sequence[date],
) -> str | None:
    for day in sessions:
        if not _coverage_complete(prepared, day):
            return f"{day.isoformat()}: topic coverage incomplete"
    return None


def _continuous_window_eligibility(
    prepared: _PreparedContext,
    security_id: str,
    sessions: Sequence[date],
    *,
    price_sessions: Sequence[date] | None = None,
) -> tuple[Eligibility, str | None]:
    if any(not prepared.has_member_on(security_id, day) for day in sessions):
        return (
            "missing",
            f"{security_id}: membership is not continuous for {len(sessions)} sessions",
        )
    for day in sessions:
        classification, reason = _state_eligibility(
            prepared,
            security_id,
            day,
            check_listing=False,
        )
        if classification != "valid":
            return classification, reason
    required_price_sessions = sessions if price_sessions is None else price_sessions
    if _prices_for(prepared, security_id, required_price_sessions) is None:
        return (
            "missing",
            f"{security_id}: missing {len(required_price_sessions)} adjusted prices",
        )
    return "valid", None


def _staged_window_failure(
    prepared: _PreparedContext,
    role: RoleName,
    sessions: Sequence[date],
    *,
    expected: int,
    classifications: dict[str, Eligibility],
    reasons: list[str],
    price_sessions: Sequence[date] | None = None,
    fail_on_missing: bool,
) -> RoleEvaluation | None:
    window_incomplete = False
    has_cross_exchange = False
    for day in sessions:
        for security_id in prepared.members_on(day):
            day_classification, reason = _staged_member_state_eligibility(
                prepared,
                security_id,
                day,
            )
            if day_classification not in {"missing", "cross_exchange"}:
                continue
            if day_classification == "missing" and (
                security_id not in classifications
                or classifications[security_id] == "legal_excluded"
            ):
                continue
            window_incomplete = True
            has_cross_exchange = (
                has_cross_exchange or day_classification == "cross_exchange"
            )
            if (
                security_id in classifications
                and classifications[security_id] == "valid"
            ):
                classifications[security_id] = day_classification
            if reason is not None:
                reasons.append(reason)

    for security_id in tuple(classifications):
        if classifications[security_id] != "valid":
            continue
        window_classification, reason = _continuous_window_eligibility(
            prepared,
            security_id,
            sessions,
            price_sessions=price_sessions,
        )
        if window_classification == "valid":
            continue
        window_incomplete = True
        if classifications[security_id] == "valid":
            classifications[security_id] = window_classification
        has_cross_exchange = (
            has_cross_exchange or window_classification == "cross_exchange"
        )
        if reason is not None:
            reasons.append(reason)
    if not has_cross_exchange and (not fail_on_missing or not window_incomplete):
        return None
    failure_reasons = tuple(
        dict.fromkeys(
            (
                *(("cross_exchange",) if has_cross_exchange else ()),
                *reasons,
            )
        )
    )
    valid = sum(value == "valid" for value in classifications.values())
    return _insufficient(
        prepared,
        role,
        reasons=failure_reasons,
        expected=expected,
        classifications=classifications,
        valid=valid,
    )


def _topic_return_median(
    prepared: _PreparedContext,
    sessions: Sequence[date],
) -> Decimal | None:
    returns: list[Decimal] = []
    for security_id in prepared.members_on(prepared.trade_date):
        observation_classification, _ = _state_eligibility(
            prepared,
            security_id,
            prepared.trade_date,
            check_listing=True,
        )
        if observation_classification != "valid":
            continue
        classification, _ = _continuous_window_eligibility(
            prepared,
            security_id,
            sessions,
        )
        if classification != "valid":
            continue
        prices = _prices_for(prepared, security_id, sessions)
        if prices is None:
            continue
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


def _amount_series(
    prepared: _PreparedContext,
    security_id: str,
    sessions: Sequence[date],
) -> tuple[Decimal, ...]:
    observations = tuple(_amount_on(prepared, security_id, day) for day in sessions)
    if any(observation is None for observation in observations):
        raise AnalysisUnavailable("missing daily amount")
    complete = tuple(observation for observation in observations if observation)
    if len({observation[1] for observation in complete}) != 1:
        raise AnalysisUnavailable("currency mismatch across amount window")
    return tuple(observation[0] for observation in complete)


def _amount_change(
    current: Sequence[Decimal],
    prior: Sequence[Decimal],
    *,
    require_current_positive: bool = False,
) -> Decimal:
    current_mean = decimal_mean(current)
    prior_mean = decimal_mean(prior)
    with _decimal34_operation("amount_change") as normalize:
        current_mean, prior_mean, one = normalize(
            current_mean,
            prior_mean,
            Decimal(1),
        )
        if prior_mean <= 0:
            raise AnalysisUnavailable("prior amount mean must be positive")
        if require_current_positive and current_mean <= 0:
            raise AnalysisUnavailable("current amount mean must be positive")
        return current_mean / prior_mean - one


def _daily_topic_amounts(
    prepared: _PreparedContext,
    day: date,
) -> tuple[tuple[Decimal, ...], tuple[str, ...]] | None:
    if not _coverage_complete(prepared, day):
        return None
    amounts: list[Decimal] = []
    currencies: list[str] = []
    for security_id in prepared.members_on(day):
        classification, _ = _state_eligibility(
            prepared,
            security_id,
            day,
            check_listing=True,
        )
        if classification == "legal_excluded":
            continue
        if classification != "valid":
            return None
        observation = _amount_on(prepared, security_id, day)
        if observation is None:
            return None
        amounts.append(observation[0])
        currencies.append(observation[1])
    if not amounts or len(set(currencies)) != 1:
        return None
    try:
        topic_amount_share(
            amounts[0],
            amounts,
            currency=currencies[0],
            eligible_currencies=currencies,
        )
    except AnalysisUnavailable:
        return None
    return tuple(amounts), tuple(currencies)


def _closing_limit_streak(
    prepared: _PreparedContext,
    security_id: str,
) -> tuple[int | None, Eligibility, str | None]:
    sessions = prepared.context.market.calendar.sessions
    try:
        endpoint = sessions.index(prepared.trade_date)
    except ValueError:
        return None, "missing", None
    count = 0
    for day in reversed(sessions[: endpoint + 1]):
        classification, reason = _state_eligibility(
            prepared,
            security_id,
            day,
            check_listing=False,
        )
        state = prepared.state_on(security_id, day)
        if classification == "cross_exchange":
            return None, classification, reason
        if classification == "missing":
            return None, classification, reason
        if state is None:
            raise AssertionError("classified limit streak state became unavailable")
        if state.suspended:
            return count, "valid", None
        if classification != "valid":
            return None, "missing", reason
        daily_bar = prepared.bar_on(security_id, day)
        if (
            daily_bar is None
            or daily_bar.close is None
            or not daily_bar.close.is_finite()
            or daily_bar.close <= 0
        ):
            return None, "missing", None
        if daily_bar.limit_status == "no_limit":
            if any(
                value is not None
                for value in (
                    daily_bar.limit_date,
                    daily_bar.limit_up,
                    daily_bar.limit_down,
                )
            ):
                return None, "missing", None
            return count, "valid", None
        if (
            daily_bar.limit_status != "limited"
            or daily_bar.limit_date != day
            or daily_bar.limit_up is None
            or daily_bar.limit_down is None
            or not daily_bar.limit_up.is_finite()
            or not daily_bar.limit_down.is_finite()
            or daily_bar.limit_down <= 0
            or daily_bar.limit_down >= daily_bar.limit_up
            or not daily_bar.limit_down <= daily_bar.close <= daily_bar.limit_up
        ):
            return None, "missing", None
        if daily_bar.close != daily_bar.limit_up:
            return count, "valid", None
        count += 1
    return None, "missing", None


def _finish_staged(
    *,
    prepared: _PreparedContext,
    role: RoleName,
    expected: int,
    classifications: dict[str, Eligibility],
    reasons: Sequence[str],
    rows: tuple[RankedCandidate, ...],
) -> RoleEvaluation:
    valid = sum(value == "valid" for value in classifications.values())
    legal_excluded, missing = _partition_counts(
        expected,
        classifications,
        valid=valid,
    )
    minimum = RULE_DEFINITIONS[prepared.context.rule_version]["minimum_topic_members"]
    if valid < minimum:
        return RoleEvaluation(
            topic_status=_role_status(
                topic_id=prepared.topic_id,
                role=role,
                status="insufficient",
                expected=expected,
                valid=valid,
                legal_excluded=legal_excluded,
                missing=missing,
                reasons=(*reasons, "minimum_valid_members_not_met"),
            )
        )
    positive_status = {
        "emotion": "simplified_candidate",
        "catchup": "simplified_candidate",
        "follower": "watch",
        "relay": "pending_validation",
    }[role]
    has_positive = any(row.status == positive_status for row in rows)
    return RoleEvaluation(
        topic_status=_role_status(
            topic_id=prepared.topic_id,
            role=role,
            status=ROLE_CAPABILITIES[role] if has_positive else "no_candidate",
            expected=expected,
            valid=valid,
            legal_excluded=legal_excluded,
            missing=missing,
            reasons=reasons,
        ),
        candidates=rows,
    )


def _first_simplified_tie_group(
    rows: tuple[RankedCandidate, ...],
) -> tuple[RankedCandidate, ...]:
    return tuple(
        row
        if row.tie_rank == 1 or row.status != "simplified_candidate"
        else row.model_copy(
            update={
                "status": "eligible_non_candidate",
                "reasons": (*row.reasons, "not_first_tie_group"),
            }
        )
        for row in rows
    )


def evaluate_emotion(context: RoleContext, topic_id: str) -> RoleEvaluation:
    """Evaluate the daily-only simplified emotion role."""
    prepared, expected, classifications, reasons, failure = _staged_preflight(
        context, topic_id, "emotion"
    )
    if failure is not None:
        return failure
    sessions = _window(prepared, 6)
    if len(sessions) != 6:
        return _insufficient(
            prepared,
            "emotion",
            reasons=(*reasons, "market_calendar_missing_6_sessions"),
            expected=expected,
            classifications=classifications,
        )
    coverage_reason = _complete_coverage_window(prepared, sessions)
    if coverage_reason is not None:
        return _insufficient(
            prepared,
            "emotion",
            reasons=(*reasons, coverage_reason),
            expected=expected,
            classifications=classifications,
        )
    window_failure = _staged_window_failure(
        prepared,
        "emotion",
        sessions,
        expected=expected,
        classifications=classifications,
        reasons=reasons,
        fail_on_missing=True,
    )
    if window_failure is not None:
        return window_failure
    daily_topic_amounts = _daily_topic_amounts(prepared, prepared.trade_date)
    if daily_topic_amounts is None:
        return _insufficient(
            prepared,
            "emotion",
            reasons=(*reasons, "daily topic amount coverage unavailable"),
            expected=expected,
            classifications=classifications,
        )
    benchmark = _topic_return_median(prepared, sessions)
    if benchmark is None:
        return _insufficient(
            prepared,
            "emotion",
            reasons=(*reasons, "topic_return_median_unavailable"),
            expected=expected,
            classifications=classifications,
        )

    factors_by_security: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    candidates: dict[str, bool] = {}
    for security_id, classification in tuple(classifications.items()):
        if classification != "valid":
            continue
        amount = _amount_on(prepared, security_id, prepared.trade_date)
        streak, streak_classification, streak_reason = _closing_limit_streak(
            prepared,
            security_id,
        )
        current_bar = prepared.bar_on(security_id, prepared.trade_date)
        if streak_classification != "valid":
            classifications[security_id] = streak_classification
            reasons.append(
                streak_reason or f"{security_id}: unknown required daily limit input"
            )
            continue
        if amount is None or streak is None or current_bar is None:
            classifications[security_id] = "missing"
            reasons.append(f"{security_id}: unknown required daily limit input")
            continue
        prices = _prices_for(prepared, security_id, sessions)
        if prices is None:
            raise AssertionError("validated emotion prices became unavailable")
        try:
            factors_by_security[security_id] = (
                Decimal(streak),
                relative_return(period_return(prices[0], prices[-1]), benchmark),
                topic_amount_share(
                    amount[0],
                    daily_topic_amounts[0],
                    currency=amount[1],
                    eligible_currencies=daily_topic_amounts[1],
                ),
            )
        except AnalysisUnavailable as exc:
            classifications[security_id] = "missing"
            reasons.append(f"{security_id}: {exc}")
            continue
        candidates[security_id] = (
            current_bar.limit_status == "limited"
            and current_bar.limit_date == prepared.trade_date
            and current_bar.limit_up is not None
            and current_bar.close == current_bar.limit_up
            and streak >= 1
        )

    cross_exchange = _cross_exchange_result(
        prepared,
        "emotion",
        expected=expected,
        classifications=classifications,
        reasons=reasons,
        valid=sum(value == "valid" for value in classifications.values()),
    )
    if cross_exchange is not None:
        return cross_exchange

    rows = tuple(
        RoleCandidate(
            topic_id=topic_id,
            security_id=security_id,
            role="emotion",
            status=(
                "simplified_candidate"
                if candidates[security_id]
                else "eligible_non_candidate"
            ),
            factors=factors,
            reasons=(
                (
                    "candidate_conditions_met",
                    "daily_simplified_candidate_only",
                    "first_limit_time_unavailable",
                    "open_reseal_evidence_unavailable",
                    "seal_order_quality_unavailable",
                )
                if candidates[security_id]
                else ("candidate_conditions_not_met",)
            ),
        )
        for security_id, factors in factors_by_security.items()
    )
    try:
        ranked = dense_rank(
            rows,
            key=lambda row: (-row.factors[0], -row.factors[1], -row.factors[2]),
        )
    except AnalysisUnavailable:
        return _insufficient(
            prepared,
            "emotion",
            reasons=(*reasons, "emotion ranking unavailable"),
            expected=expected,
            classifications=classifications,
        )
    return _finish_staged(
        prepared=prepared,
        role="emotion",
        expected=expected,
        classifications=classifications,
        reasons=reasons,
        rows=_first_simplified_tie_group(ranked),
    )


def evaluate_catchup(context: RoleContext, topic_id: str) -> RoleEvaluation:
    """Evaluate the two disjoint-window simplified catchup role."""
    prepared, expected, classifications, reasons, failure = _staged_preflight(
        context, topic_id, "catchup"
    )
    if failure is not None:
        return failure
    sessions = _window(prepared, 26)
    if len(sessions) != 26:
        return _insufficient(
            prepared,
            "catchup",
            reasons=(*reasons, "market_calendar_missing_26_sessions"),
            expected=expected,
            classifications=classifications,
        )
    coverage_reason = _complete_coverage_window(prepared, sessions)
    if coverage_reason is not None:
        return _insufficient(
            prepared,
            "catchup",
            reasons=(*reasons, coverage_reason),
            expected=expected,
            classifications=classifications,
        )
    window_failure = _staged_window_failure(
        prepared,
        "catchup",
        sessions,
        expected=expected,
        classifications=classifications,
        reasons=reasons,
        fail_on_missing=True,
    )
    if window_failure is not None:
        return window_failure
    prior_sessions = sessions[:21]
    current_sessions = sessions[-6:]
    prior_benchmark = _topic_return_median(prepared, prior_sessions)
    current_benchmark = _topic_return_median(prepared, current_sessions)
    if prior_benchmark is None or current_benchmark is None:
        return _insufficient(
            prepared,
            "catchup",
            reasons=(*reasons, "topic_return_median_unavailable"),
            expected=expected,
            classifications=classifications,
        )

    factors_by_security: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    for security_id, classification in tuple(classifications.items()):
        if classification != "valid":
            continue
        prices = _prices_for(prepared, security_id, sessions)
        if prices is None:
            classifications[security_id] = "missing"
            reasons.append(f"{security_id}: missing 26 adjusted prices")
            continue
        try:
            amounts = _amount_series(prepared, security_id, sessions[1:])
            prior_return = period_return(prices[0], prices[20])
            current_return = period_return(prices[20], prices[-1])
            factors_by_security[security_id] = (
                relative_return(current_return, current_benchmark),
                relative_return(prior_benchmark, prior_return),
                _amount_change(
                    amounts[-5:],
                    amounts[:20],
                    require_current_positive=True,
                ),
            )
        except AnalysisUnavailable as exc:
            classifications[security_id] = "missing"
            reasons.append(f"{security_id}: {exc}")

    rows = tuple(
        RoleCandidate(
            topic_id=topic_id,
            security_id=security_id,
            role="catchup",
            status=(
                "simplified_candidate"
                if all(factor > 0 for factor in factors)
                else "eligible_non_candidate"
            ),
            factors=factors,
            reasons=(
                ("candidate_conditions_met", "daily_simplified_candidate_only")
                if all(factor > 0 for factor in factors)
                else ("candidate_conditions_not_met",)
            ),
        )
        for security_id, factors in factors_by_security.items()
    )
    try:
        ranked = dense_rank(
            rows,
            key=lambda row: (-row.factors[0], -row.factors[1], -row.factors[2]),
        )
    except AnalysisUnavailable:
        return _insufficient(
            prepared,
            "catchup",
            reasons=(*reasons, "catchup ranking unavailable"),
            expected=expected,
            classifications=classifications,
        )
    return _finish_staged(
        prepared=prepared,
        role="catchup",
        expected=expected,
        classifications=classifications,
        reasons=reasons,
        rows=_first_simplified_tie_group(ranked),
    )


def _unique_first_trend_candidate(
    evaluation: RoleEvaluation,
) -> RankedCandidate | None:
    candidates = tuple(
        row
        for row in evaluation.candidates
        if row.status == "candidate" and row.tie_rank == 1
    )
    return candidates[0] if len(candidates) == 1 else None


def evaluate_follower(context: RoleContext, topic_id: str) -> RoleEvaluation:
    """Evaluate follower observations against the unique current trend core."""
    prepared, expected, classifications, reasons, failure = _staged_preflight(
        context, topic_id, "follower"
    )
    if failure is not None:
        return failure
    sessions = _window(prepared, 10)
    if len(sessions) != 10:
        return _insufficient(
            prepared,
            "follower",
            reasons=(*reasons, "market_calendar_missing_10_sessions"),
            expected=expected,
            classifications=classifications,
        )
    coverage_reason = _complete_coverage_window(prepared, sessions)
    if coverage_reason is not None:
        return _insufficient(
            prepared,
            "follower",
            reasons=(*reasons, coverage_reason),
            expected=expected,
            classifications=classifications,
        )
    window_failure = _staged_window_failure(
        prepared,
        "follower",
        sessions,
        expected=expected,
        classifications=classifications,
        reasons=reasons,
        price_sessions=sessions[-6:],
        fail_on_missing=False,
    )
    if window_failure is not None:
        return window_failure
    reference = _unique_first_trend_candidate(evaluate_trend(context, topic_id))
    if reference is None:
        return _insufficient(
            prepared,
            "follower",
            reasons=(*reasons, "unique_trend_reference_unavailable"),
            expected=expected,
            classifications=classifications,
        )
    reference_prices = _prices_for(prepared, reference.security_id, sessions[-6:])
    if reference_prices is None:
        return _insufficient(
            prepared,
            "follower",
            reasons=(*reasons, "trend_reference_return5_unavailable"),
            expected=expected,
            classifications=classifications,
        )
    try:
        reference_return = period_return(reference_prices[0], reference_prices[-1])
    except AnalysisUnavailable:
        return _insufficient(
            prepared,
            "follower",
            reasons=(*reasons, "trend_reference_return5_unavailable"),
            expected=expected,
            classifications=classifications,
        )

    factors_by_security: dict[str, tuple[Decimal, Decimal]] = {}
    for security_id, classification in tuple(classifications.items()):
        if classification != "valid":
            continue
        prices = _prices_for(prepared, security_id, sessions[-6:])
        if prices is None:
            classifications[security_id] = "missing"
            reasons.append(f"{security_id}: missing 6 adjusted prices")
            continue
        try:
            amounts = _amount_series(prepared, security_id, sessions)
            factors_by_security[security_id] = (
                period_return(prices[0], prices[-1]),
                _amount_change(amounts[-5:], amounts[:5]),
            )
        except AnalysisUnavailable as exc:
            classifications[security_id] = "missing"
            reasons.append(f"{security_id}: {exc}")

    rows = tuple(
        RoleCandidate(
            topic_id=topic_id,
            security_id=security_id,
            role="follower",
            status="watch",
            factors=factors,
            reasons=(
                "watch_conditions_met",
                "intraday_start_order_unavailable",
                "independent_catalyst_evidence_unavailable",
                "correlation_is_not_causation",
            ),
        )
        for security_id, factors in factors_by_security.items()
        if factors[0] > 0 and factors[0] < reference_return
    )
    try:
        ranked = dense_rank(
            rows,
            key=lambda row: (-row.factors[0], -row.factors[1]),
        )
    except AnalysisUnavailable:
        return _insufficient(
            prepared,
            "follower",
            reasons=(*reasons, "follower ranking unavailable"),
            expected=expected,
            classifications=classifications,
        )
    return _finish_staged(
        prepared=prepared,
        role="follower",
        expected=expected,
        classifications=classifications,
        reasons=reasons,
        rows=ranked,
    )


def _previous_session(prepared: _PreparedContext) -> date | None:
    sessions = prepared.context.market.calendar.sessions
    try:
        index = sessions.index(prepared.trade_date)
    except ValueError:
        return None
    return sessions[index - 1] if index > 0 else None


def _relay_comparability_reasons(
    prepared: _PreparedContext,
    predecessor: RelayPredecessorRun,
) -> tuple[str, ...]:
    context = prepared.context
    benchmark_definition = RULE_DEFINITIONS[context.rule_version]["roles"]["trend"][
        "required_inputs"
    ]["market_benchmark"]["definition"]
    visibility_mode = (
        "retrospective"
        if (
            context.market.provenance == "retrospective"
            or context.topic.provenance == "retrospective"
        )
        else "contemporaneous"
    )
    checks = (
        (predecessor.trade_date == _previous_session(prepared), "not_previous_session"),
        (predecessor.active_run_count == 1, "predecessor_not_unique_active"),
        (predecessor.rule_version == context.rule_version, "rule_version_changed"),
        (
            predecessor.visibility_mode == visibility_mode,
            "visibility_mode_changed",
        ),
        (
            predecessor.market_benchmark_definition == benchmark_definition,
            "market_benchmark_changed",
        ),
        (predecessor.topic_id == prepared.topic_id, "topic_identity_changed"),
        (
            predecessor.topic_source_id == context.topic.source_id,
            "topic_identity_changed",
        ),
        (
            predecessor.topic_source_version == context.topic.source_version,
            "topic_revision_changed",
        ),
        (
            predecessor.taxonomy_version == context.topic.taxonomy_version,
            "topic_identity_changed",
        ),
    )
    return tuple(reason for matches, reason in checks if not matches)


def evaluate_relay(
    context: RoleContext,
    topic_id: str,
    predecessor: RelayPredecessorRun | None,
) -> RoleEvaluation:
    """Evaluate same-day relay evidence without Task 7 validation outcomes."""
    prepared, expected, classifications, reasons, failure = _staged_preflight(
        context, topic_id, "relay"
    )
    if failure is not None:
        return failure
    if predecessor is None:
        return _insufficient(
            prepared,
            "relay",
            reasons=(*reasons, "predecessor_run_unavailable"),
            expected=expected,
            classifications=classifications,
        )
    comparability_reasons = _relay_comparability_reasons(prepared, predecessor)
    if comparability_reasons:
        return _insufficient(
            prepared,
            "relay",
            reasons=(*reasons, *comparability_reasons),
            expected=expected,
            classifications=classifications,
        )
    sessions = _window(prepared, 6)
    daily_sessions = _window(prepared, 2)
    if len(sessions) != 6 or len(daily_sessions) != 2:
        return _insufficient(
            prepared,
            "relay",
            reasons=(*reasons, "market_calendar_missing_relay_window"),
            expected=expected,
            classifications=classifications,
        )
    coverage_reason = _complete_coverage_window(prepared, sessions)
    if coverage_reason is not None:
        return _insufficient(
            prepared,
            "relay",
            reasons=(*reasons, coverage_reason),
            expected=expected,
            classifications=classifications,
        )
    window_failure = _staged_window_failure(
        prepared,
        "relay",
        sessions,
        expected=expected,
        classifications=classifications,
        reasons=reasons,
        fail_on_missing=True,
    )
    if window_failure is not None:
        return window_failure
    old_core = _unique_first_trend_candidate(predecessor.trend_evaluation)
    current_core = _unique_first_trend_candidate(evaluate_trend(context, topic_id))
    if old_core is None or current_core is None:
        return _insufficient(
            prepared,
            "relay",
            reasons=(*reasons, "unique_trend_core_unavailable"),
            expected=expected,
            classifications=classifications,
        )
    if old_core.security_id == current_core.security_id:
        valid = sum(value == "valid" for value in classifications.values())
        legal_excluded, missing = _partition_counts(
            expected, classifications, valid=valid
        )
        return RoleEvaluation(
            topic_status=_role_status(
                topic_id=topic_id,
                role="relay",
                status="no_candidate",
                expected=expected,
                valid=valid,
                legal_excluded=legal_excluded,
                missing=missing,
                reasons=(*reasons, "trend_core_unchanged"),
            )
        )
    topic_return5 = _topic_return_median(prepared, sessions)
    topic_daily_return = _topic_return_median(prepared, daily_sessions)
    old_prices = _prices_for(prepared, old_core.security_id, sessions)
    new_prices = _prices_for(prepared, current_core.security_id, sessions)
    if (
        topic_return5 is None
        or topic_daily_return is None
        or old_prices is None
        or new_prices is None
    ):
        return _insufficient(
            prepared,
            "relay",
            reasons=(*reasons, "relay_factor_input_unavailable"),
            expected=expected,
            classifications=classifications,
        )
    try:
        factors = (
            relative_return(
                period_return(old_prices[0], old_prices[-1]),
                topic_return5,
            ),
            relative_return(
                period_return(new_prices[0], new_prices[-1]),
                topic_return5,
            ),
            topic_daily_return,
        )
    except AnalysisUnavailable:
        return _insufficient(
            prepared,
            "relay",
            reasons=(*reasons, "relay_factor_input_unavailable"),
            expected=expected,
            classifications=classifications,
        )
    membership_changed = (
        predecessor.membership_version != context.topic.membership_version
        or frozenset(predecessor.member_ids)
        != frozenset(prepared.members_on(prepared.trade_date))
    )
    if membership_changed:
        reasons.append("membership_changed")
    rows: tuple[RankedCandidate, ...] = ()
    if factors[0] <= 0 and factors[1] > 0 and factors[2] > 0:
        rows = (
            RankedCandidate(
                topic_id=topic_id,
                security_id=current_core.security_id,
                role="relay",
                status="pending_validation",
                factors=factors,
                reasons=(
                    "pending_conditions_met",
                    "validation_window:T+1..T+5",
                    *(
                        ("membership_changed_not_attributed_to_market",)
                        if membership_changed
                        else ()
                    ),
                ),
                tie_rank=1,
            ),
        )
    return _finish_staged(
        prepared=prepared,
        role="relay",
        expected=expected,
        classifications=classifications,
        reasons=reasons,
        rows=rows,
    )
