"""Pure materialization of limit, role-change, and relay-validation evidence."""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from dragon_radar.domain.models import DailyBar, SecurityState
from dragon_radar.domain.quality import bar_issues, grant_allows_local
from dragon_radar.roles.engine import _effective, _PreparedContext
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
    prepared = _PreparedContext.build(context, topic_id)
    sessions = context.market.calendar.sessions
    try:
        endpoint = sessions.index(context.market.trade_date)
    except ValueError:
        return ()
    window = sessions[max(0, endpoint - 9) : endpoint + 1]
    global_reasons = tuple(
        reason
        for reason in (
            (
                None
                if grant_allows_local(
                    context.market.grant,
                    source_id=context.market.source_id,
                    now=context.knowledge_cutoff,
                )
                else "market_source_not_materializable"
            ),
            (
                None
                if grant_allows_local(
                    context.topic.grant,
                    source_id=context.topic.source_id,
                    now=context.knowledge_cutoff,
                )
                else "topic_source_not_materializable"
            ),
        )
        if reason is not None
    )
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
        if global_reasons:
            rows.append(
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
            )
            continue

        observations = {
            day: _limit_observation(prepared, security_id, day) for day in window
        }
        current = observations[context.market.trade_date]
        reasons: list[str] = []
        if current[2] is not None:
            reasons.append(current[2])

        if len(window) != 10 or any(
            observation[0] is None for observation in observations.values()
        ):
            hit_count = None
        else:
            hit_count = sum(
                observation[0] is True for observation in observations.values()
            )

        streak: int | None = 0
        for day in reversed(sessions[: endpoint + 1]):
            closed_limit, _, reason = _limit_observation(prepared, security_id, day)
            if closed_limit is None:
                streak = None
                if reason is not None and day == context.market.trade_date:
                    reasons.append(reason)
                break
            if not closed_limit:
                break
            streak += 1

        broken: bool | None = False
        if endpoint == 0:
            broken = None
        else:
            previous = _limit_observation(
                prepared,
                security_id,
                sessions[endpoint - 1],
            )[0]
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
        current.context.market.expected_security_ids,
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
        previous.context.market.expected_security_ids,
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
    current_rows = _positive_rows(current)
    previous_rows = _positive_rows(previous)
    keys = tuple(sorted(current_rows.keys() | previous_rows.keys()))
    reasons = _comparison_reasons(current, previous)
    membership_changed = (
        current.membership_version != previous.membership_version
        or frozenset(current.member_ids) != frozenset(previous.member_ids)
    )
    result: list[RoleChange] = []
    for role, security_id in keys:
        change_type = None
        change_reasons = reasons
        if not reasons:
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
                comparison_status=("not_comparable" if reasons else "comparable"),
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
    runs: Sequence[RoleRunEvidence],
    reasons: Sequence[str],
) -> tuple[RoleValidation, ...]:
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
            input_run_ids=tuple(run.role_run_id for run in runs),
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
) -> DailyBar | None:
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
    return matches[0] if len(matches) == 1 else None


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
            run.context.market.expected_security_ids == pending.expected_security_ids,
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
            run.context.market.calendar.sessions == run.context.topic.calendar.sessions
            and run.trade_date in run.context.market.calendar.sessions,
            "calendar_sessions_changed",
        ),
    )
    return tuple(reason for matches, reason in checks if not matches)


def validate_relay(
    pending: PendingRelayValidation,
    future_runs: Sequence[RoleRunEvidence],
) -> tuple[RoleValidation, ...]:
    """Validate a mature pending relay against fixed daily-visible run inputs."""
    pending = PendingRelayValidation.model_validate(pending.model_dump())
    if pending.current_validation is not None:
        return (pending.current_validation,)
    ordered = tuple(
        sorted(
            (run for run in future_runs if run.trade_date > pending.observation_date),
            key=lambda run: (run.trade_date, run.role_run_id),
        )
    )
    if not ordered:
        return ()
    validation_sessions = pending.validation_sessions
    if max(run.trade_date for run in ordered) < validation_sessions[-1]:
        return ()

    by_day: dict[date, list[RoleRunEvidence]] = {}
    for run in ordered:
        if run.trade_date in validation_sessions:
            by_day.setdefault(run.trade_date, []).append(run)
    selected = tuple(
        runs[0] for day in validation_sessions if len(runs := by_day.get(day, [])) == 1
    )
    if len(selected) != 5:
        return _unavailable_validation(
            pending,
            sessions=validation_sessions,
            runs=selected,
            reasons=("window_incomplete",),
        )
    for run in selected:
        if run.verification_status != "passed" or not any(
            not character.isspace() for character in run.activation_event_id
        ):
            RoleRunEvidence.model_validate(run.model_dump())

    identity_reasons = tuple(
        reason for run in selected for reason in _run_identity_reasons(pending, run)
    )
    if identity_reasons:
        return _unavailable_validation(
            pending,
            sessions=validation_sessions,
            runs=selected,
            reasons=("topic_incomparable", *identity_reasons),
        )

    validation_cutoff = selected[-1].context.knowledge_cutoff
    if any(
        not grant_allows_local(
            run.context.market.grant,
            source_id=run.context.market.source_id,
            now=validation_cutoff,
        )
        or not grant_allows_local(
            run.context.topic.grant,
            source_id=run.context.topic.source_id,
            now=validation_cutoff,
        )
        for run in selected
    ):
        return _unavailable_validation(
            pending,
            sessions=validation_sessions,
            runs=selected,
            reasons=("authorization_unavailable",),
        )

    baseline = frozenset(pending.baseline_member_ids)
    if any(frozenset(run.member_ids) != baseline for run in selected):
        return _unavailable_validation(
            pending,
            sessions=validation_sessions,
            runs=selected,
            reasons=("membership_changed",),
        )

    if any(
        _members_on(run, day) != baseline
        for day, run in zip(validation_sessions, selected, strict=True)
    ):
        return _unavailable_validation(
            pending,
            sessions=validation_sessions,
            runs=selected,
            reasons=("membership_changed",),
        )
    prices: dict[str, list[Decimal]] = {
        price.security_id: [price.adjusted_price_raw]
        for price in pending.baseline_prices
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
            daily_bar = _bar_for(run, security_id, day)
            if daily_bar is None or daily_bar.close is None:
                failures.append(f"{security_id}: missing_price")
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
