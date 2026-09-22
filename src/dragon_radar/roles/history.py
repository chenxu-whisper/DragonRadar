"""Pure materialization of limit, role-change, and relay-validation evidence."""

from bisect import bisect_left
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from dragon_radar.domain.models import DailyBar, SecurityState
from dragon_radar.domain.quality import bar_issues, grant_allows_local
from dragon_radar.roles.engine import (
    _effective,
    _global_reasons,
    _PreparedContext,
    _state_eligibility,
)
from dragon_radar.roles.factors import (
    AnalysisUnavailable,
    adjusted_price,
    decimal_median,
    period_return,
)
from dragon_radar.roles.models import (
    LimitRow,
    PendingRelayValidation,
    RankedCandidate,
    RoleChange,
    RoleContext,
    RoleName,
    RoleRunEvidence,
    RoleValidation,
)

_POSITIVE_STATUSES = frozenset(
    {"candidate", "simplified_candidate", "watch", "pending_validation"}
)


def _limit_observation(
    prepared: _PreparedContext,
    security_id: str,
    day: date,
) -> tuple[bool | None, bool | None, str | None]:
    state = prepared.state_on(security_id, day)
    if state is None or state.suspended is None:
        return None, None, "security_state_unavailable"
    if state.exchange != prepared.context.market.calendar.exchange:
        return None, None, "cross_exchange"
    if state.suspended:
        return False, False, None

    daily_bar = prepared.bar_on(security_id, day)
    if daily_bar is None:
        return None, None, "daily_bar_unavailable"
    if daily_bar.limit_status == "no_limit":
        if any(
            value is not None
            for value in (
                daily_bar.limit_date,
                daily_bar.limit_up,
                daily_bar.limit_down,
            )
        ):
            return None, None, "limit_status_unavailable"
        return False, False, None
    if daily_bar.limit_status != "limited":
        return None, None, "limit_status_unavailable"
    if (
        daily_bar.limit_date != day
        or daily_bar.limit_up is None
        or daily_bar.limit_down is None
        or daily_bar.close is None
        or any(
            not value.is_finite()
            for value in (
                daily_bar.limit_up,
                daily_bar.limit_down,
                daily_bar.close,
            )
        )
        or daily_bar.limit_down <= 0
        or daily_bar.limit_down >= daily_bar.limit_up
        or not daily_bar.limit_down <= daily_bar.close <= daily_bar.limit_up
    ):
        return None, None, "limit_status_unavailable"

    closed_limit = daily_bar.close == daily_bar.limit_up
    ohlc = (daily_bar.open, daily_bar.high, daily_bar.low, daily_bar.close)
    touch_quality_ok = (
        all(value is not None and value.is_finite() for value in ohlc)
        and daily_bar.high is not None
        and daily_bar.high <= daily_bar.limit_up
        and not bar_issues(daily_bar, prepared.context.market)
    )
    if not touch_quality_ok:
        return closed_limit, None, "touch_quality_gate_failed"
    return (
        closed_limit,
        daily_bar.high == daily_bar.limit_up and daily_bar.close < daily_bar.limit_up,
        None,
    )


def build_limit_ladder(
    context: RoleContext,
    topic_id: str,
) -> tuple[LimitRow, ...]:
    """Build one conservative limit-ladder row per current topic member."""
    context = RoleContext.model_validate(context.model_dump())
    prepared = _PreparedContext.build(context, topic_id)
    global_reasons = (
        *_global_reasons(prepared),
        *(
            ("trade_date_not_in_calendar_sessions",)
            if context.market.trade_date not in context.market.calendar.sessions
            or context.topic.trade_date not in context.topic.calendar.sessions
            else ()
        ),
    )
    if global_reasons:
        return tuple(
            LimitRow(
                trade_date=context.market.trade_date,
                topic_id=topic_id,
                security_id=security_id,
                closing_limit_streak=None,
                limit_hits_10=None,
                broken_board=None,
                touched_not_sealed=None,
                reasons=global_reasons,
            )
            for security_id in prepared.members_on(context.market.trade_date)
        )

    sessions = context.market.calendar.sessions
    try:
        endpoint = sessions.index(context.market.trade_date)
    except ValueError:
        return ()
    window = sessions[max(0, endpoint - 9) : endpoint + 1]
    rows: list[LimitRow] = []
    market_pool = frozenset(context.market.expected_security_ids)
    for security_id in prepared.members_on(context.market.trade_date):
        if security_id not in market_pool:
            rows.append(
                LimitRow(
                    trade_date=context.market.trade_date,
                    topic_id=topic_id,
                    security_id=security_id,
                    closing_limit_streak=None,
                    limit_hits_10=None,
                    broken_board=None,
                    touched_not_sealed=None,
                    reasons=("member_not_in_market_pool",),
                )
            )
            continue

        current_state = prepared.state_on(security_id, context.market.trade_date)
        listing_boundary: int | None = None
        if (
            current_state is not None
            and current_state.listing_date is not None
            and sessions[0] <= current_state.listing_date <= context.market.trade_date
        ):
            first_listing_index = bisect_left(
                sessions,
                current_state.listing_date,
                hi=endpoint + 1,
            )
            first_listing_session = sessions[first_listing_index]
            first_listing_state = prepared.state_on(
                security_id,
                first_listing_session,
            )
            if (
                first_listing_state is not None
                and first_listing_state.listing_date == current_state.listing_date
            ):
                listing_boundary = first_listing_index

        window_start = endpoint - len(window) + 1
        observations = {
            day: (
                (False, False, None)
                if listing_boundary is not None
                and window_start + offset < listing_boundary
                else _limit_observation(prepared, security_id, day)
            )
            for offset, day in enumerate(window)
        }
        current = observations[context.market.trade_date]
        reasons: list[str] = []
        if current[2] is not None:
            reasons.append(current[2])
        reasons.extend(
            f"{day.isoformat()}: {observation[2]}"
            for day, observation in observations.items()
            if day != context.market.trade_date
            and observation[0] is None
            and observation[2] is not None
        )

        if len(window) != 10 or any(
            observation[0] is None for observation in observations.values()
        ):
            hit_count = None
        else:
            hit_count = sum(
                observation[0] is True for observation in observations.values()
            )

        streak: int | None = 0
        scan_start = listing_boundary if listing_boundary is not None else 0
        for day in reversed(sessions[scan_start : endpoint + 1]):
            closed_limit, _, reason = _limit_observation(prepared, security_id, day)
            if closed_limit is None:
                streak = None
                if reason is not None and day == context.market.trade_date:
                    reasons.append(reason)
                elif reason is not None:
                    reasons.append(f"{day.isoformat()}: {reason}")
                break
            if not closed_limit:
                break
            streak += 1
        else:
            if listing_boundary is None:
                streak = None
                reasons.append("history_boundary_unknown")

        broken: bool | None = False
        if endpoint == 0:
            broken = None
        else:
            previous_day = sessions[endpoint - 1]
            previous = (
                observations[previous_day][0]
                if previous_day in observations
                else _limit_observation(prepared, security_id, previous_day)[0]
            )
            if previous is None or current[0] is None:
                broken = None
            else:
                broken = previous and not current[0]

        rows.append(
            LimitRow(
                trade_date=context.market.trade_date,
                topic_id=topic_id,
                security_id=security_id,
                closing_limit_streak=streak,
                limit_hits_10=hit_count,
                broken_board=broken,
                touched_not_sealed=current[1],
                reasons=tuple(dict.fromkeys(reasons)),
            )
        )
    return tuple(rows)


def _positive_rows(run: RoleRunEvidence) -> dict[tuple[RoleName, str], RankedCandidate]:
    return {
        (row.role, row.security_id): row
        for evaluation in run.evaluations
        for row in evaluation.candidates
        if row.status in _POSITIVE_STATUSES
    }


def _tie_group(
    rows: dict[tuple[RoleName, str], RankedCandidate],
    row: RankedCandidate,
) -> frozenset[str]:
    return frozenset(
        security_id
        for (role, security_id), candidate in rows.items()
        if role == row.role and candidate.tie_rank == row.tie_rank
    )


def _comparison_reasons(
    current: RoleRunEvidence,
    previous: RoleRunEvidence,
) -> tuple[str, ...]:
    calculation_basis_matches = (
        current.context.market.source_id,
        current.context.market.adjustment_version,
        current.context.market.pool_version,
        frozenset(current.context.market.expected_security_ids),
        current.context.market.calendar_version,
        current.context.market.calendar.exchange,
        current.context.market.calendar.sessions,
        current.context.topic.calendar_version,
        current.context.topic.exchange,
        current.context.topic.calendar.sessions,
    ) == (
        previous.context.market.source_id,
        previous.context.market.adjustment_version,
        previous.context.market.pool_version,
        frozenset(previous.context.market.expected_security_ids),
        previous.context.market.calendar_version,
        previous.context.market.calendar.exchange,
        previous.context.market.calendar.sessions,
        previous.context.topic.calendar_version,
        previous.context.topic.exchange,
        previous.context.topic.calendar.sessions,
    )
    is_previous_session = True
    if calculation_basis_matches:
        sessions = current.context.market.calendar.sessions
        try:
            current_index = sessions.index(current.trade_date)
        except ValueError:
            is_previous_session = False
        else:
            is_previous_session = (
                current_index > 0 and sessions[current_index - 1] == previous.trade_date
            )
    checks = (
        (
            current.previous_comparable_run_id == previous.role_run_id,
            "previous_comparable_run_mismatch",
        ),
        (is_previous_session, "not_previous_comparable_session"),
        (current.rule_version == previous.rule_version, "rule_version_changed"),
        (
            current.visibility_mode == previous.visibility_mode,
            "visibility_mode_changed",
        ),
        (
            current.market_benchmark_definition == previous.market_benchmark_definition,
            "market_benchmark_changed",
        ),
        (current.topic_id == previous.topic_id, "topic_identity_changed"),
        (
            current.topic_source_id == previous.topic_source_id,
            "topic_identity_changed",
        ),
        (
            current.topic_source_version == previous.topic_source_version,
            "topic_revision_changed",
        ),
        (
            current.taxonomy_version == previous.taxonomy_version,
            "taxonomy_version_changed",
        ),
        (calculation_basis_matches, "calculation_basis_changed"),
    )
    return tuple(dict.fromkeys(reason for matches, reason in checks if not matches))


def compare_runs(
    current: RoleRunEvidence,
    previous: RoleRunEvidence,
) -> tuple[RoleChange, ...]:
    """Compare positive role assignments only against the selected predecessor."""
    current = RoleRunEvidence.model_validate(current.model_dump())
    previous = RoleRunEvidence.model_validate(previous.model_dump())
    if (
        previous.verification_status != "passed"
        or previous.activation_event_id is None
        or previous.active_run_count != 1
    ):
        raise ValueError("previous run must be passed and active")
    current_rows = _positive_rows(current)
    previous_rows = _positive_rows(previous)
    keys = tuple(sorted(current_rows.keys() | previous_rows.keys()))
    reasons = _comparison_reasons(current, previous)
    current_evaluations = {
        evaluation.topic_status.role: evaluation for evaluation in current.evaluations
    }
    previous_evaluations = {
        evaluation.topic_status.role: evaluation for evaluation in previous.evaluations
    }
    membership_changed = (
        current.membership_version != previous.membership_version
        or frozenset(current.member_ids) != frozenset(previous.member_ids)
    )
    result: list[RoleChange] = []
    for role, security_id in keys:
        change_type = None
        role_inputs = (
            current_evaluations.get(role),
            previous_evaluations.get(role),
        )
        role_reasons = (
            ("role_input_insufficient",)
            if any(
                evaluation is None or evaluation.topic_status.status == "insufficient"
                for evaluation in role_inputs
            )
            else ()
        )
        comparison_reasons = tuple(dict.fromkeys((*reasons, *role_reasons)))
        change_reasons = comparison_reasons
        if not comparison_reasons:
            current_row = current_rows.get((role, security_id))
            previous_row = previous_rows.get((role, security_id))
            if previous_row is None:
                change_type = "added"
            elif current_row is None:
                change_type = "exited"
            elif current_row.tie_rank != previous_row.tie_rank or _tie_group(
                current_rows, current_row
            ) != _tie_group(previous_rows, previous_row):
                change_type = "tie_changed"
            else:
                change_type = "unchanged"
            change_reasons = ("membership_changed",) if membership_changed else ()
        result.append(
            RoleChange(
                current_run_id=current.role_run_id,
                previous_comparable_run_id=current.previous_comparable_run_id,
                topic_id=current.topic_id,
                security_id=security_id,
                role=role,
                comparison_status=(
                    "not_comparable" if comparison_reasons else "comparable"
                ),
                change_type=change_type,
                membership_changed=membership_changed,
                reasons=change_reasons,
            )
        )
    return tuple(result)


def _unavailable_validation(
    pending: PendingRelayValidation,
    *,
    sessions: Sequence[date],
    runs: Sequence[RoleRunEvidence] = (),
    input_run_ids: Sequence[str] | None = None,
    reasons: Sequence[str],
) -> tuple[RoleValidation, ...]:
    bound_input_run_ids = (
        tuple(input_run_ids)
        if input_run_ids is not None
        else tuple(dict.fromkeys(run.role_run_id for run in runs))
    )
    return (
        RoleValidation(
            pending_result_id=pending.pending_result_id,
            observation_run_id=pending.observation_run_id,
            topic_id=pending.topic_id,
            outcome="unavailable",
            window_start=sessions[0],
            window_end=sessions[-1],
            return_start=pending.observation_date,
            return_end=sessions[-1],
            old_security_id=pending.old_security_id,
            new_security_id=pending.new_security_id,
            input_run_ids=bound_input_run_ids,
            reasons=tuple(dict.fromkeys(reasons)),
        ),
    )


def _state_for(
    run: RoleRunEvidence,
    security_id: str,
    day: date,
) -> SecurityState | None:
    context = run.context
    matches = tuple(
        security_state
        for security_state in context.market.security_states
        if security_state.security_id == security_id
        and security_state.available_at <= context.knowledge_cutoff
        and security_state.source_id == context.market.source_id
        and security_state.source_version == context.market.source_version
        and security_state.pool_version == context.market.pool_version
        and security_state.exchange == context.market.calendar.exchange
        and security_state.listing_date is not None
        and security_state.listing_date <= day
        and _effective(
            security_state.effective_from,
            security_state.effective_to,
            day,
        )
    )
    return matches[0] if len(matches) == 1 else None


def _identity_state_for(
    run: RoleRunEvidence,
    security_id: str,
    day: date,
) -> SecurityState | None:
    context = run.context
    matches = tuple(
        security_state
        for security_state in context.market.security_states
        if security_state.security_id == security_id
        and security_state.available_at <= context.knowledge_cutoff
        and _effective(
            security_state.effective_from,
            security_state.effective_to,
            day,
        )
    )
    return matches[0] if len(matches) == 1 else None


def _bar_for(
    run: RoleRunEvidence,
    security_id: str,
    day: date,
) -> tuple[DailyBar | None, str | None]:
    context = run.context
    matches = tuple(
        daily_bar
        for daily_bar in context.market.bars
        if daily_bar.security_id == security_id
        and daily_bar.trade_date == day
        and daily_bar.available_at <= context.knowledge_cutoff
        and daily_bar.source_id == context.market.source_id
        and daily_bar.source_version == context.market.source_version
        and daily_bar.adjustment_version == context.market.adjustment_version
    )
    if len(matches) != 1:
        return None, "missing_price"
    daily_bar = matches[0]
    if bar_issues(daily_bar, context.market):
        return None, "bar_quality_unavailable"
    return daily_bar, None


def _is_member(
    run: RoleRunEvidence,
    security_id: str,
    day: date,
) -> bool:
    context = run.context
    matches = tuple(
        membership
        for membership in context.topic.memberships
        if membership.topic_id == run.topic_id
        and membership.security_id == security_id
        and membership.available_at <= context.knowledge_cutoff
        and _effective(membership.effective_from, membership.effective_to, day)
    )
    return len(matches) == 1


def _members_on(run: RoleRunEvidence, day: date) -> frozenset[str]:
    context = run.context
    return frozenset(
        membership.security_id
        for membership in context.topic.memberships
        if membership.topic_id == run.topic_id
        and membership.available_at <= context.knowledge_cutoff
        and _effective(membership.effective_from, membership.effective_to, day)
    )


def _run_identity_reasons(
    pending: PendingRelayValidation,
    run: RoleRunEvidence,
) -> tuple[str, ...]:
    checks = (
        (run.rule_version == pending.rule_version, "rule_version_changed"),
        (
            run.visibility_mode == pending.visibility_mode,
            "visibility_mode_changed",
        ),
        (
            run.market_benchmark_definition == pending.market_benchmark_definition,
            "market_benchmark_changed",
        ),
        (run.topic_id == pending.topic_id, "topic_identity_changed"),
        (
            run.topic_source_id == pending.topic_source_id,
            "topic_identity_changed",
        ),
        (
            run.topic_source_version == pending.topic_source_version,
            "topic_revision_changed",
        ),
        (
            run.taxonomy_version == pending.taxonomy_version,
            "taxonomy_version_changed",
        ),
        (
            run.membership_version == pending.membership_version,
            "membership_version_changed",
        ),
        (
            run.context.market.source_id == pending.market_source_id,
            "market_source_changed",
        ),
        (
            run.context.market.adjustment_version == pending.adjustment_version,
            "adjustment_version_changed",
        ),
        (
            run.context.market.pool_version == pending.pool_version,
            "pool_version_changed",
        ),
        (
            frozenset(run.context.market.expected_security_ids)
            == frozenset(pending.expected_security_ids),
            "market_pool_changed",
        ),
        (
            run.context.market.calendar.exchange == pending.exchange
            and run.context.topic.exchange == pending.exchange,
            "exchange_changed",
        ),
        (
            run.context.market.calendar_version == pending.calendar_version
            and run.context.topic.calendar_version == pending.calendar_version,
            "calendar_version_changed",
        ),
        (
            run.context.market.calendar.sessions == pending.calendar_sessions
            and run.context.topic.calendar.sessions == pending.calendar_sessions
            and run.trade_date in run.context.market.calendar.sessions,
            "calendar_sessions_changed",
        ),
    )
    return tuple(reason for matches, reason in checks if not matches)


def _observation_proof_reasons(
    pending: PendingRelayValidation,
) -> tuple[str, ...]:
    run = pending.observation_run
    prepared = _PreparedContext.build(run.context, run.topic_id)
    observation_day = pending.observation_date
    reasons: list[str] = []
    actual_members = frozenset(prepared.members_on(observation_day))
    if actual_members != frozenset(pending.observation_member_ids):
        reasons.append("observation_members_mismatch")

    projected_members = {
        member.security_id: member for member in pending.observation_eligibility
    }
    actual_eligible_ids: set[str] = set()
    identity_fields = (
        "security_type",
        "board_category",
        "listing_date",
        "suspended",
        "is_st",
        "is_delisting",
        "exchange",
        "source_id",
        "pool_version",
    )
    for security_id in sorted(actual_members):
        projected = projected_members.get(security_id)
        state = prepared.state_on(security_id, observation_day)
        if projected is None or state is None:
            reasons.append(f"{security_id}: observation_eligibility_unavailable")
            continue
        if any(
            getattr(projected, field) != getattr(state, field)
            for field in identity_fields
        ):
            reasons.append(f"{security_id}: observation_identity_mismatch")
            continue
        missing_identity = any(
            getattr(state, field) is None
            for field in (
                "security_type",
                "board_category",
                "listing_date",
                "suspended",
                "is_st",
                "is_delisting",
            )
        )
        if missing_identity:
            expected_eligible = False
            expected_reason = "missing_identity_evidence"
        else:
            eligibility, detail = _state_eligibility(
                prepared,
                security_id,
                observation_day,
                check_listing=True,
            )
            if eligibility == "valid":
                expected_eligible = True
                expected_reason = None
            elif eligibility == "legal_excluded" and detail is not None:
                expected_eligible = False
                expected_reason = (
                    "insufficient_listing_history"
                    if detail.endswith("insufficient listing history")
                    else "ineligible_security_state"
                )
            else:
                reasons.append(f"{security_id}: observation_eligibility_unavailable")
                continue
        if (
            projected.eligible is not expected_eligible
            or projected.eligibility_reason != expected_reason
        ):
            reasons.append(f"{security_id}: observation_eligibility_mismatch")
            continue
        if expected_eligible:
            actual_eligible_ids.add(security_id)

    if actual_eligible_ids != set(pending.baseline_member_ids):
        reasons.append("observation_eligible_members_mismatch")

    projected_prices = {
        price.security_id: price.adjusted_price_raw for price in pending.baseline_prices
    }
    for security_id in sorted(actual_eligible_ids):
        daily_bar, bar_failure = _bar_for(run, security_id, observation_day)
        if daily_bar is None:
            reasons.append(
                f"{security_id}: observation_{bar_failure or 'price_unavailable'}"
            )
            continue
        try:
            actual_price = adjusted_price(
                daily_bar.close,
                daily_bar.adjustment_factor,
            )
        except AnalysisUnavailable:
            reasons.append(f"{security_id}: observation_adjusted_price_unavailable")
            continue
        if projected_prices.get(security_id) != actual_price:
            reasons.append(f"{security_id}: observation_adjusted_price_mismatch")
    return tuple(dict.fromkeys(reasons))


def _authorization_available(
    pending: PendingRelayValidation,
    runs: Sequence[RoleRunEvidence],
) -> bool:
    operation_at = pending.operation_at
    current_grants = {grant.source_id: grant for grant in pending.current_grants}
    for run in (pending.observation_run, *runs):
        embedded_grants = (
            (run.context.market.source_id, run.context.market.grant),
            (run.context.topic.source_id, run.context.topic.grant),
        )
        for source_id, embedded_grant in embedded_grants:
            current_grant = current_grants.get(source_id)
            if current_grant is None or not grant_allows_local(
                current_grant,
                source_id=source_id,
                now=operation_at,
            ):
                return False
            if not grant_allows_local(
                embedded_grant,
                source_id=source_id,
                now=operation_at,
            ):
                return False
    return True


def validate_relay(
    pending: PendingRelayValidation,
    future_runs: Sequence[RoleRunEvidence],
) -> tuple[RoleValidation, ...]:
    """Validate a mature pending relay against fixed daily-visible run inputs."""
    pending = PendingRelayValidation.model_validate(pending.model_dump())
    if pending.current_validation is not None:
        input_run_ids = pending.current_validation.input_run_ids
        future_runs_by_id: dict[str, RoleRunEvidence] = {}
        for run in future_runs:
            if run.role_run_id in future_runs_by_id:
                raise ValueError("duplicate future run ID")
            future_runs_by_id[run.role_run_id] = run
        referenced_runs = {
            run_id: run
            for run_id, run in future_runs_by_id.items()
            if run_id in input_run_ids
        }
        if set(referenced_runs) != set(input_run_ids):
            return _unavailable_validation(
                pending,
                sessions=pending.validation_sessions,
                input_run_ids=input_run_ids,
                reasons=("authorization_unavailable",),
            )
        current_window_runs = tuple(
            RoleRunEvidence.model_validate(referenced_runs[run_id].model_dump())
            for run_id in input_run_ids
        )
        if (
            pending.current_validation.outcome == "unavailable"
            and len(input_run_ids) < 5
        ):
            if not _authorization_available(pending, current_window_runs):
                return _unavailable_validation(
                    pending,
                    sessions=pending.validation_sessions,
                    input_run_ids=input_run_ids,
                    reasons=("authorization_unavailable",),
                )
            observation_reasons = _observation_proof_reasons(pending)
            if observation_reasons:
                return _unavailable_validation(
                    pending,
                    sessions=pending.validation_sessions,
                    input_run_ids=input_run_ids,
                    reasons=observation_reasons,
                )
            return (pending.current_validation,)
        if any(
            run.verification_status != "passed"
            or run.activation_event_id is None
            or run.active_run_count != 1
            for run in current_window_runs
        ):
            raise ValueError("relay validation runs must be passed and active")
        binding_reasons = tuple(
            reason
            for session, run in zip(
                pending.validation_sessions,
                current_window_runs,
                strict=True,
            )
            for reason in (
                ("validation_session_mismatch",)
                if run.trade_date != session
                else _run_identity_reasons(pending, run)
            )
        )
        if binding_reasons:
            return _unavailable_validation(
                pending,
                sessions=pending.validation_sessions,
                input_run_ids=input_run_ids,
                reasons=("validation_input_mismatch", *binding_reasons),
            )
        if not _authorization_available(
            pending,
            current_window_runs,
        ):
            return _unavailable_validation(
                pending,
                sessions=pending.validation_sessions,
                input_run_ids=input_run_ids,
                reasons=("authorization_unavailable",),
            )
        observation_reasons = _observation_proof_reasons(pending)
        if observation_reasons:
            return _unavailable_validation(
                pending,
                sessions=pending.validation_sessions,
                input_run_ids=input_run_ids,
                reasons=observation_reasons,
            )
        return (pending.current_validation,)
    future_runs = tuple(
        RoleRunEvidence.model_validate(run.model_dump()) for run in future_runs
    )
    if any(
        run.verification_status != "passed"
        or run.activation_event_id is None
        or run.active_run_count != 1
        for run in future_runs
    ):
        raise ValueError("relay validation runs must be passed and active")
    strict_future = tuple(
        sorted(
            (run for run in future_runs if run.trade_date > pending.observation_date),
            key=lambda run: (run.trade_date, run.role_run_id),
        )
    )
    if not strict_future:
        return ()
    maturity_runs = tuple(
        run
        for run in strict_future
        if run.context.market.calendar.exchange == pending.exchange
        and run.context.topic.exchange == pending.exchange
        and run.context.market.calendar_version == pending.calendar_version
        and run.context.topic.calendar_version == pending.calendar_version
        and run.context.market.calendar.sessions == pending.calendar_sessions
        and run.context.topic.calendar.sessions == pending.calendar_sessions
        and run.trade_date in run.context.market.calendar.sessions
        and run.trade_date in run.context.topic.calendar.sessions
    )
    validation_sessions = pending.validation_sessions
    if (
        not maturity_runs
        or max(run.trade_date for run in maturity_runs) < validation_sessions[-1]
    ):
        return ()
    ordered = tuple(
        run
        for run in strict_future
        if run.topic_id == pending.topic_id
        and run.context.market.calendar.exchange == pending.exchange
        and run.context.topic.exchange == pending.exchange
    )

    by_day: dict[date, list[RoleRunEvidence]] = {}
    drift_by_day: dict[date, list[tuple[RoleRunEvidence, tuple[str, ...]]]] = {}
    for run in ordered:
        if run.trade_date in validation_sessions:
            identity_reasons = _run_identity_reasons(pending, run)
            if identity_reasons:
                drift_by_day.setdefault(run.trade_date, []).append(
                    (run, identity_reasons)
                )
            else:
                by_day.setdefault(run.trade_date, []).append(run)
    selected = tuple(
        runs[0] for day in validation_sessions if (runs := by_day.get(day, []))
    )
    if any(len(by_day.get(day, ())) > 1 for day in validation_sessions):
        if len({run.role_run_id for run in selected}) != len(selected):
            raise ValueError("duplicate_input_run_id")
        return _unavailable_validation(
            pending,
            sessions=validation_sessions,
            runs=selected,
            reasons=("multiple_active_topic_runs",),
        )
    if len(selected) != 5:
        bound_runs: list[RoleRunEvidence] = []
        identity_reasons: list[str] = []
        missing = False
        for day in validation_sessions:
            matching = by_day.get(day, ())
            drift = drift_by_day.get(day, ())
            if matching:
                bound_runs.append(matching[0])
            elif len(drift) == 1:
                run, reasons = drift[0]
                bound_runs.append(run)
                identity_reasons.extend(reasons)
            else:
                missing = True
        selected = tuple(bound_runs)
        if len({run.role_run_id for run in selected}) != len(selected):
            raise ValueError("duplicate_input_run_id")
        return _unavailable_validation(
            pending,
            sessions=validation_sessions,
            runs=selected,
            reasons=(
                ("validation_runs_missing", *identity_reasons)
                if missing
                else ("topic_incomparable", *identity_reasons)
            ),
        )

    if len({run.role_run_id for run in selected}) != len(selected):
        raise ValueError("duplicate_input_run_id")

    if not _authorization_available(pending, selected):
        return _unavailable_validation(
            pending,
            sessions=validation_sessions,
            runs=selected,
            reasons=("authorization_unavailable",),
        )
    observation_reasons = _observation_proof_reasons(pending)
    if observation_reasons:
        return _unavailable_validation(
            pending,
            sessions=validation_sessions,
            runs=selected,
            reasons=observation_reasons,
        )

    observation_members = frozenset(pending.observation_member_ids)
    if any(frozenset(run.member_ids) != observation_members for run in selected):
        return _unavailable_validation(
            pending,
            sessions=validation_sessions,
            runs=selected,
            reasons=("membership_changed",),
        )

    if any(
        _members_on(run, day) != observation_members
        for day, run in zip(validation_sessions, selected, strict=True)
    ):
        return _unavailable_validation(
            pending,
            sessions=validation_sessions,
            runs=selected,
            reasons=("membership_changed",),
        )
    baseline = frozenset(pending.baseline_member_ids)
    frozen_members = {
        member.security_id: member
        for member in pending.observation_eligibility
        if member.security_id in baseline
    }
    identity_fields = (
        "security_type",
        "board_category",
        "listing_date",
        "is_st",
        "is_delisting",
        "exchange",
        "source_id",
        "pool_version",
    )
    identity_failures: list[str] = []
    for security_id in sorted(baseline):
        frozen = frozen_members[security_id]
        for day, run in zip(validation_sessions, selected, strict=True):
            security_state = _identity_state_for(run, security_id, day)
            if security_state is None:
                identity_failures.append(f"{security_id}: identity_unavailable")
                break
            changed_field = next(
                (
                    field
                    for field in identity_fields
                    if getattr(security_state, field) != getattr(frozen, field)
                ),
                None,
            )
            if changed_field is not None:
                identity_failures.append(
                    f"{security_id}: identity_changed:{changed_field}"
                )
                break
    if identity_failures:
        return _unavailable_validation(
            pending,
            sessions=validation_sessions,
            runs=selected,
            reasons=identity_failures,
        )
    prices: dict[str, list[Decimal]] = {
        price.security_id: [price.adjusted_price_raw]
        for price in sorted(
            pending.baseline_prices,
            key=lambda price: price.security_id,
        )
    }
    failures: list[str] = []
    for security_id in prices:
        for day, run in zip(validation_sessions, selected, strict=True):
            if not _is_member(run, security_id, day):
                failures.append(f"{security_id}: membership_discontinuous")
                break
            security_state = _state_for(run, security_id, day)
            if security_state is None:
                failures.append(f"{security_id}: security_state_unavailable")
                break
            if security_state.suspended is None:
                failures.append(f"{security_id}: suspension_status_unknown")
                break
            if security_state.suspended:
                failures.append(f"{security_id}: suspended")
                break
            daily_bar, bar_failure = _bar_for(run, security_id, day)
            if daily_bar is None:
                failures.append(f"{security_id}: {bar_failure}")
                break
            try:
                prices[security_id].append(
                    adjusted_price(
                        daily_bar.close,
                        daily_bar.adjustment_factor,
                    )
                )
            except AnalysisUnavailable:
                failures.append(f"{security_id}: missing_price")
                break
    if failures:
        return _unavailable_validation(
            pending,
            sessions=validation_sessions,
            runs=selected,
            reasons=failures,
        )

    try:
        returns = {
            security_id: period_return(values[0], values[-1])
            for security_id, values in prices.items()
        }
        topic_median = decimal_median(
            tuple(returns[security_id] for security_id in sorted(baseline))
        )
    except AnalysisUnavailable:
        return _unavailable_validation(
            pending,
            sessions=validation_sessions,
            runs=selected,
            reasons=("return_unavailable",),
        )
    old_return = returns[pending.old_security_id]
    new_return = returns[pending.new_security_id]
    if new_return <= old_return:
        outcome = "refuted"
    elif new_return > topic_median:
        outcome = "supported"
    else:
        outcome = "weakened"
    return (
        RoleValidation(
            pending_result_id=pending.pending_result_id,
            observation_run_id=pending.observation_run_id,
            topic_id=pending.topic_id,
            outcome=outcome,
            window_start=validation_sessions[0],
            window_end=validation_sessions[-1],
            return_start=pending.observation_date,
            return_end=validation_sessions[-1],
            old_security_id=pending.old_security_id,
            new_security_id=pending.new_security_id,
            old_return_raw=old_return,
            new_return_raw=new_return,
            topic_median_raw=topic_median,
            input_run_ids=tuple(run.role_run_id for run in selected),
            reasons=(f"outcome:{outcome}",),
        ),
    )
