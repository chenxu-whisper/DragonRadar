"""Pure quality gate. Storage owns durable archiving and authorization policy.

validate_batch returns the original frozen payload, including invalid records.
It never persists or repairs data. A denied storage grant must also prevent
storage of the payload; the caller can retain only permitted audit metadata.
Re-evaluate rights with current operation time on import, publication and reads.
"""

from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from dragon_radar.domain.models import (
    DailyBar,
    FrozenModel,
    LimitUpEvent,
    MarketBatch,
    SecurityState,
    SourceGrant,
)


class QualityIssue(FrozenModel):
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"
    security_id: str | None = None
    trade_date: date | None = None


class Coverage(FrozenModel):
    """Counts partition unique expected IDs; explicit suspension is legal exclusion."""

    expected: int
    valid: int
    legal_excluded: int
    missing: int


class QualityReport(FrozenModel):
    batch: MarketBatch
    issues: tuple[QualityIssue, ...]
    coverage: Coverage

    @property
    def publishable(self) -> bool:
        return not self.issues


def _grant_is_current(grant: SourceGrant, *, source_id: str, now: datetime) -> bool:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("operation time requires a timezone")
    return bool(
        grant.source_id == source_id
        and grant.approved
        and grant.reference
        and grant.starts_at is not None
        and grant.expires_at is not None
        and grant.starts_at <= now < grant.expires_at
    )


def grant_allows_storage(grant: SourceGrant, *, source_id: str, now: datetime) -> bool:
    """Check provenance, time bounds, and the explicit local-storage right."""
    return bool(
        _grant_is_current(grant, source_id=source_id, now=now) and grant.local_storage
    )


def grant_allows_display(grant: SourceGrant, *, source_id: str, now: datetime) -> bool:
    """Check provenance, time bounds, and the explicit local-display right."""
    return bool(
        _grant_is_current(grant, source_id=source_id, now=now) and grant.local_display
    )


def grant_allows_local(grant: SourceGrant, *, source_id: str, now: datetime) -> bool:
    """Compatibility predicate for operations that require both local rights."""
    return bool(
        grant_allows_storage(grant, source_id=source_id, now=now)
        and grant_allows_display(grant, source_id=source_id, now=now)
    )


def state_at(batch: MarketBatch, security_id: str, day: date) -> SecurityState | None:
    """Return a unique effective, known, correctly versioned state, else None."""
    states = [
        s
        for s in batch.security_states
        if s.security_id == security_id
        and s.effective_from <= day
        and (s.effective_to is None or day < s.effective_to)
    ]
    if len(states) != 1:
        return None
    state = states[0]
    if (
        state.available_at > batch.as_of
        or state.source_id != batch.source_id
        or state.source_version != batch.source_version
        or state.pool_version != batch.pool_version
        or state.exchange != batch.calendar.exchange
        or state.listing_date is None
        or state.listing_date > day
        or state.suspended is None
    ):
        return None
    return state


def bar_issues(bar: DailyBar, batch: MarketBatch) -> tuple[QualityIssue, ...]:
    """Record-local validation, shared by the gate and pure analytics."""
    issues: list[QualityIssue] = []

    def add(code: str, message: str) -> None:
        issues.append(
            QualityIssue(
                code=code,
                message=message,
                security_id=bar.security_id,
                trade_date=bar.trade_date,
            )
        )

    if (bar.source_id, bar.source_version) != (batch.source_id, batch.source_version):
        add("source_mismatch", "Bar source/version differs from batch")
    if bar.adjustment_version != batch.adjustment_version:
        add("adjustment_mismatch", "Bar factor version differs from batch")
    if bar.available_at > batch.as_of:
        add("late_bar", "Bar was not known at the batch cutoff")
    if bar.available_at.astimezone(ZoneInfo("Asia/Shanghai")).date() < bar.trade_date:
        add("bar_time", "Bar availability precedes its trading date")
    if (
        bar.trade_date not in batch.calendar.sessions
        or bar.trade_date > batch.trade_date
    ):
        add("bar_date", "Bar is outside the supplied calendar or batch history")
    numeric_fields = (
        "close",
        "open",
        "high",
        "low",
        "previous_close",
        "adjustment_factor",
        "volume",
        "amount",
        "turnover_rate",
        "float_mv",
        "total_mv",
        "limit_up",
        "limit_down",
    )
    nonfinite = {
        name
        for name in numeric_fields
        if (value := getattr(bar, name)) is not None and not value.is_finite()
    }
    for name in sorted(nonfinite):
        add("nonfinite", f"{name} must be finite")
    for name in ("close", "open", "high", "low", "previous_close"):
        value = getattr(bar, name)
        if value is not None and name not in nonfinite and value <= 0:
            add("invalid_price", f"{name} must be positive")
    if bar.close is None:
        add("missing_price", "Close is missing")
    if "adjustment_factor" not in nonfinite and bar.adjustment_factor <= 0:
        add("invalid_factor", "Adjustment factor must be positive")
    nonnegative_fields = (
        ("volume", "negative_volume"),
        ("amount", "negative_amount"),
        ("turnover_rate", "negative_turnover_rate"),
        ("float_mv", "negative_float_market_cap"),
        ("total_mv", "negative_total_market_cap"),
    )
    for name, code in nonnegative_fields:
        value = getattr(bar, name)
        if value is not None and name not in nonfinite and value < 0:
            add(code, f"{name} must be nonnegative")
    if (
        bar.price_unit != "currency/share"
        or bar.volume_unit not in {"share", "lot"}
        or bar.amount_unit != "currency"
        or bar.turnover_rate_unit != "fraction"
        or bar.market_cap_unit != "currency"
    ):
        add("unit_contract", "Bar units do not match the canonical unit contract")
    ohlc = (bar.open, bar.high, bar.low)
    if any(v is not None for v in ohlc):
        if any(v is None for v in (*ohlc, bar.close)):
            add("invalid_ohlc", "Supply complete OHLC or close-only")
        elif not nonfinite.intersection({"open", "high", "low", "close"}):
            if not (
                bar.low
                <= min(bar.open, bar.close)
                <= max(bar.open, bar.close)
                <= bar.high
            ):
                add("invalid_ohlc", "OHLC must lie within low/high")
    limits = (bar.limit_down, bar.limit_up)
    if bar.limit_status == "limited":
        if (
            bar.limit_date != bar.trade_date
            or any(v is None or not v.is_finite() or v <= 0 for v in limits)
            or bar.limit_down >= bar.limit_up
        ):
            add("invalid_limits", "Limited session needs dated positive ordered limits")
        elif bar.close is not None and bar.close.is_finite():
            if not bar.limit_down <= bar.close <= bar.limit_up:
                add("invalid_limits", "Raw close falls outside supplied limits")
    elif any(v is not None for v in limits) or bar.limit_date is not None:
        add("invalid_limits", "Only a limited session may supply limit prices/date")
    return tuple(issues)


def limit_up_event_issues(
    event: LimitUpEvent, batch: MarketBatch
) -> tuple[QualityIssue, ...]:
    issues: list[QualityIssue] = []

    def add(code: str, message: str) -> None:
        issues.append(
            QualityIssue(
                code=code,
                message=message,
                security_id=event.security_id,
                trade_date=event.trade_date,
            )
        )

    if (event.source_id, event.source_version) != (
        batch.source_id,
        batch.source_version,
    ):
        add("source_mismatch", "Limit-up source/version differs from batch")
    if event.available_at > batch.as_of:
        add("late_limit_up_event", "Limit-up event was not known at the batch cutoff")
    if (
        event.trade_date not in batch.calendar.sessions
        or event.trade_date > batch.trade_date
    ):
        add("limit_up_date", "Limit-up event is outside the supplied batch history")
    for name in ("break_count", "continuous_boards"):
        value = getattr(event, name)
        if value is not None and value < 0:
            add(f"negative_{name}", f"{name} must be nonnegative")
    if event.seal_amount is not None:
        if not event.seal_amount.is_finite():
            add("nonfinite", "seal_amount must be finite")
        elif event.seal_amount < 0:
            add("negative_seal_amount", "seal_amount must be nonnegative")
    if event.seal_amount_unit != "currency":
        add("unit_contract", "Seal amount unit must be currency")
    if (
        event.first_seal_time is not None
        and event.last_seal_time is not None
        and event.last_seal_time < event.first_seal_time
    ):
        add("invalid_seal_times", "Last seal time precedes first seal time")
    return tuple(issues)


def validate_batch(batch: MarketBatch, *, now: datetime) -> QualityReport:
    """Validate for import/publication at explicit operation time.

    All current expected IDs must be valid or explicitly suspended. Missing
    history remains archived; interval eligibility requires every window session.
    Late observations are blocked even on explicitly retrospective payloads.
    """
    issues: list[QualityIssue] = []

    def add(
        code: str, message: str, sid: str | None = None, day: date | None = None
    ) -> None:
        issues.append(
            QualityIssue(code=code, message=message, security_id=sid, trade_date=day)
        )

    if not grant_allows_display(batch.grant, source_id=batch.source_id, now=now):
        add("grant_denied", "Embedded grant does not authorize local display")
    if (
        batch.as_of > now
        or batch.trade_date > batch.as_of.astimezone(ZoneInfo("Asia/Shanghai")).date()
    ):
        add("batch_time", "Batch cutoff/date is in the future")
    if batch.source_time is not None and (
        batch.source_time > batch.as_of or batch.source_time > now
    ):
        add("source_time", "Source timestamp exceeds batch cutoff or operation time")
    if batch.calendar_version != batch.calendar.version:
        add("calendar_version", "Calendar version differs from batch")
    if batch.trade_date not in batch.calendar.sessions:
        add("trade_date", "Trade date is not a supplied session")
    if not batch.complete_pages:
        add("incomplete_pages", "Source pagination is incomplete")
    expected = set(batch.expected_security_ids)
    if not expected:
        add("empty_pool", "Expected security pool is empty")
    for sid, count in Counter(batch.expected_security_ids).items():
        if count > 1:
            add("duplicate_expected_id", "Expected IDs must be unique", sid)
    known = {state.security_id for state in batch.security_states}
    for sid in sorted(expected - known):
        add("unknown_id", "Expected ID has no security state", sid)

    grouped_states: dict[str, list[SecurityState]] = defaultdict(list)
    for state in batch.security_states:
        sid = state.security_id
        grouped_states[sid].append(state)
        if state.security_type not in {"common_stock", "index"}:
            add(
                "unsupported_security_type",
                "Security type is not supported for normalized market data",
                sid,
            )
        if (
            state.effective_to is not None
            and state.effective_to <= state.effective_from
        ):
            add("state_interval", "State effective interval must be nonempty", sid)
        if state.available_at > batch.as_of:
            add("late_state", "Security state was not known at cutoff", sid)
        if (state.source_id, state.source_version, state.pool_version) != (
            batch.source_id,
            batch.source_version,
            batch.pool_version,
        ):
            add("state_version", "Security state source/pool version differs", sid)
        if state.exchange != batch.calendar.exchange:
            add("state_exchange", "Security exchange differs from calendar", sid)
    for sid, states in grouped_states.items():
        ordered = sorted(states, key=lambda s: s.effective_from)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if (
                previous.effective_to is None
                or current.effective_from < previous.effective_to
            ):
                add("overlapping_state", "Security state intervals overlap", sid)

    grouped_bars: dict[tuple[str, date], list[DailyBar]] = defaultdict(list)
    valid_keys: set[tuple[str, date]] = set()
    for bar in batch.bars:
        key = (bar.security_id, bar.trade_date)
        grouped_bars[key].append(bar)
        errors = bar_issues(bar, batch)
        issues.extend(errors)
        if bar.security_id not in known:
            add("unknown_id", "Bar ID has no security state", *key)
        state = state_at(batch, *key)
        if state is None:
            add("effective_state", "Bar lacks a usable effective state", *key)
        elif state.suspended:
            add("suspended_bar", "Suspension cannot supply a trading bar", *key)
        elif not errors:
            valid_keys.add(key)
    for key, bars in grouped_bars.items():
        if len(bars) > 1:
            add("duplicate_bar", "Duplicate security/date bars", *key)
            valid_keys.discard(key)

    event_keys: set[tuple[str, date]] = set()
    for event in batch.limit_up_events:
        key = (event.security_id, event.trade_date)
        if key in event_keys:
            add("duplicate_limit_up_event", "Duplicate security/date event", *key)
        event_keys.add(key)
        issues.extend(limit_up_event_issues(event, batch))
        if event.security_id not in known:
            add("unknown_id", "Limit-up event ID has no security state", *key)
        event_state = state_at(batch, *key)
        if event_state is None:
            add(
                "effective_limit_up_state",
                "Limit-up event lacks a usable effective state",
                *key,
            )
        elif event_state.suspended:
            add(
                "suspended_limit_up_event",
                "Suspended security cannot supply a limit-up event",
                *key,
            )

    valid = legal_excluded = missing = 0
    for sid in sorted(expected):
        state = state_at(batch, sid, batch.trade_date)
        if state is not None and state.suspended:
            legal_excluded += 1
        elif state is not None and (sid, batch.trade_date) in valid_keys:
            valid += 1
        else:
            missing += 1
            add(
                "missing_current",
                "Expected current record is missing or invalid",
                sid,
                batch.trade_date,
            )
    if missing:
        add(
            "insufficient_coverage",
            "Current coverage must account for every expected ID",
        )
    return QualityReport(
        batch=batch,
        issues=tuple(issues),
        coverage=Coverage(
            expected=len(expected),
            valid=valid,
            legal_excluded=legal_excluded,
            missing=missing,
        ),
    )
