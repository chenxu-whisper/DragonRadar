"""Point-in-time visible-information set assembly (Task 5).

Pure functions only: given an anchored, already-immutable publication
sequence, each decision day sees exclusively the data that was visible at that
day's publication cutoff. There is no database or clock access; the IO layer
(Task 11/13) loads frames through ``list_published_window`` and passes them in.

Two independent anti-look-ahead gates apply on every decision day:

1. ``publication_sequence`` — every frame must be at or below the anchored
   publication's sequence. A later revision (a v2 of an older trade date, or a
   brand-new frame) can never flow back into an anchored window.
2. ``available_at`` — within a frame, states/bars are accepted only when the
   shared quality gate accepts them at the batch cutoff, and topic memberships
   only when their own ``available_at`` is at or before the topic cutoff. A
   backdated constituent published late therefore stays out of history until
   its actual availability day (survivorship defense).

All failure modes are closed: non-published anchor/frame, cross-sequence
frame, unordered or duplicated trade dates, mismatched batch trade dates and
windows above 250 sessions raise ``ValueError`` rather than truncating.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field

from dragon_radar.domain.models import Identifier, MarketBatch
from dragon_radar.domain.quality import bar_issues, state_at
from dragon_radar.research.models import MAX_PERIOD_SESSIONS, StrictFrozenModel
from dragon_radar.topics.models import TopicBatch

PublicationStatus = Literal["published", "revoked"]
PositiveSequence = Annotated[int, Field(ge=1)]

# Kernel-level exclusion codes plus the closed set emitted by the shared
# record-local quality gate (domain.quality.bar_issues).
ExclusionCode = Literal[
    "unobservable_state",
    "st_excluded",
    "suspended_excluded",
    "late_topic_member",
    "not_topic_member",
    "missing_decision_bar",
    "duplicate_decision_bar",
    "source_mismatch",
    "adjustment_mismatch",
    "late_bar",
    "bar_time",
    "bar_date",
    "nonfinite",
    "invalid_price",
    "missing_price",
    "invalid_factor",
    "negative_volume",
    "negative_amount",
    "negative_turnover_rate",
    "negative_float_market_cap",
    "negative_total_market_cap",
    "unit_contract",
    "invalid_ohlc",
    "invalid_limits",
]
DegradationCode = Literal["topic_domain_unavailable"]


class PitAnchor(StrictFrozenModel):
    """The publication the whole window is pinned to."""

    publication_sequence: PositiveSequence
    trade_date: date
    status: PublicationStatus


class PitFrame(StrictFrozenModel):
    """One immutable publication projected onto its loaded domain batches."""

    trade_date: date
    publication_sequence: PositiveSequence
    status: PublicationStatus = "published"
    market: MarketBatch
    topic: TopicBatch | None = None


class UniversePolicy(StrictFrozenModel):
    """Configurable pool rules; defaults are conservative A-share exclusions."""

    exclude_st: bool = True
    exclude_suspended: bool = True
    topic_ids: tuple[Identifier, ...] = ()


class PitExclusion(StrictFrozenModel):
    security_id: Identifier
    code: ExclusionCode


class VisibleSecurity(StrictFrozenModel):
    """One tradable candidate at the decision day, under the then-current data."""

    security_id: Identifier
    close: Decimal
    adjustment_factor: Decimal
    adjustment_version: Identifier
    is_st: bool
    suspended: bool
    is_delisting: bool
    topic_ids: tuple[Identifier, ...]


class VisibleDay(StrictFrozenModel):
    trade_date: date
    publication_sequence: PositiveSequence
    securities: tuple[VisibleSecurity, ...]
    exclusions: tuple[PitExclusion, ...]
    degradations: tuple[DegradationCode, ...]


class PitWindow(StrictFrozenModel):
    anchor_sequence: PositiveSequence
    days: tuple[VisibleDay, ...]


def _validate_frames(
    frames: Sequence[PitFrame], anchor: PitAnchor
) -> tuple[PitFrame, ...]:
    if anchor.status != "published":
        raise ValueError("anchor publication must be published")
    if not frames:
        raise ValueError("PIT window requires at least one frame")
    if len(frames) > MAX_PERIOD_SESSIONS:
        raise ValueError(
            f"PIT window covers {len(frames)} sessions, above the "
            f"{MAX_PERIOD_SESSIONS} session cap"
        )

    ordered: list[PitFrame] = []
    previous_date: date | None = None
    for frame in frames:
        if frame.status != "published":
            raise ValueError(
                f"frame for {frame.trade_date} must be published, got {frame.status}"
            )
        if frame.publication_sequence > anchor.publication_sequence:
            raise ValueError(
                f"frame sequence {frame.publication_sequence} exceeds anchor "
                f"sequence {anchor.publication_sequence}"
            )
        if frame.trade_date > anchor.trade_date:
            raise ValueError(
                f"frame trade_date {frame.trade_date} is after anchor trade_date "
                f"{anchor.trade_date}"
            )
        if frame.market.trade_date != frame.trade_date:
            raise ValueError(
                f"market batch trade_date {frame.market.trade_date} does not "
                f"match frame trade_date {frame.trade_date}"
            )
        if frame.topic is not None and frame.topic.trade_date != frame.trade_date:
            raise ValueError(
                f"topic batch trade_date {frame.topic.trade_date} does not "
                f"match frame trade_date {frame.trade_date}"
            )
        if previous_date is not None and frame.trade_date <= previous_date:
            if frame.trade_date == previous_date:
                raise ValueError(f"duplicate frame for trade_date {frame.trade_date}")
            raise ValueError("PIT frames must be in ascending trade_date order")
        previous_date = frame.trade_date
        ordered.append(frame)
    return tuple(ordered)


def _membership_view(
    frame: PitFrame, policy: UniversePolicy
) -> tuple[dict[str, set[str]], set[str], tuple[DegradationCode, ...]]:
    """Return visible members, late-known members and domain degradations."""
    if not policy.topic_ids:
        return {}, set(), ()
    if frame.topic is None:
        # A missing optional domain degrades pool scoping, never the run.
        return {}, set(), ("topic_domain_unavailable",)

    wanted = set(policy.topic_ids)
    day = frame.trade_date
    visible: dict[str, set[str]] = {}
    late: set[str] = set()
    for membership in frame.topic.memberships:
        if membership.topic_id not in wanted:
            continue
        if membership.effective_from > day:
            continue
        if membership.effective_to is not None and day >= membership.effective_to:
            continue
        if membership.available_at <= frame.topic.as_of:
            visible.setdefault(membership.security_id, set()).add(membership.topic_id)
        else:
            # Effective interval claims history, but the row was published
            # only after this frame's cutoff: survivorship leak, excluded.
            late.add(membership.security_id)
    return visible, late, ()


def _assemble_day(frame: PitFrame, policy: UniversePolicy) -> VisibleDay:
    market = frame.market
    visible_members, late_members, degradations = _membership_view(frame, policy)
    topic_scoped = bool(policy.topic_ids) and not degradations

    securities: list[VisibleSecurity] = []
    exclusions: list[PitExclusion] = []

    for security_id in dict.fromkeys(market.expected_security_ids):
        state = state_at(market, security_id, frame.trade_date)
        if state is None:
            exclusions.append(
                PitExclusion(security_id=security_id, code="unobservable_state")
            )
            continue
        if policy.exclude_st and state.is_st is True:
            exclusions.append(PitExclusion(security_id=security_id, code="st_excluded"))
            continue
        if policy.exclude_suspended and state.suspended is True:
            exclusions.append(
                PitExclusion(security_id=security_id, code="suspended_excluded")
            )
            continue

        if topic_scoped:
            topic_ids = visible_members.get(security_id)
            if topic_ids is None:
                code = (
                    "late_topic_member"
                    if security_id in late_members
                    else "not_topic_member"
                )
                exclusions.append(PitExclusion(security_id=security_id, code=code))
                continue
        else:
            topic_ids = set()

        day_bars = tuple(
            bar
            for bar in market.bars
            if bar.security_id == security_id and bar.trade_date == frame.trade_date
        )
        if len(day_bars) == 0:
            exclusions.append(
                PitExclusion(security_id=security_id, code="missing_decision_bar")
            )
            continue
        if len(day_bars) > 1:
            exclusions.append(
                PitExclusion(security_id=security_id, code="duplicate_decision_bar")
            )
            continue

        bar = day_bars[0]
        issues = bar_issues(bar, market)
        if issues:
            exclusions.append(
                PitExclusion(security_id=security_id, code=issues[0].code)
            )
            continue

        securities.append(
            VisibleSecurity(
                security_id=security_id,
                close=bar.close,
                adjustment_factor=bar.adjustment_factor,
                adjustment_version=bar.adjustment_version,
                is_st=state.is_st is True,
                suspended=state.suspended is True,
                is_delisting=state.is_delisting is True,
                topic_ids=tuple(sorted(topic_ids)),
            )
        )

    securities.sort(key=lambda security: security.security_id)
    exclusions.sort(key=lambda exclusion: (exclusion.security_id, exclusion.code))
    return VisibleDay(
        trade_date=frame.trade_date,
        publication_sequence=frame.publication_sequence,
        securities=tuple(securities),
        exclusions=tuple(exclusions),
        degradations=degradations,
    )


def assemble_pit_window(
    frames: Sequence[PitFrame],
    *,
    anchor: PitAnchor,
    policy: UniversePolicy | None = None,
) -> PitWindow:
    """Assemble deterministic per-day visible sets from anchored publications."""
    chosen_policy = policy or UniversePolicy()
    ordered = _validate_frames(frames, anchor)
    return PitWindow(
        anchor_sequence=anchor.publication_sequence,
        days=tuple(_assemble_day(frame, chosen_policy) for frame in ordered),
    )


__all__ = [
    "ExclusionCode",
    "PitAnchor",
    "PitFrame",
    "PitWindow",
    "PitExclusion",
    "UniversePolicy",
    "VisibleDay",
    "VisibleSecurity",
    "assemble_pit_window",
]
