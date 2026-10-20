"""Pure sector-rotation and core-replacement timeline (Task 24 / EX11).

This module is a synchronous, dependency-free pure function over archived
role-run projections. It performs no database access, no HTTP concerns, no
clock reads and no logging; publication-time materialization lives in the
role repository (which assembles the loaded rows into the mapping inputs
defined here) and the ``GET /api/topics/rotation`` parameter validation and
window assembly live in the HTTP layer.

Slice 2 adds the publication-side adapters that stay pure:
:func:`trend_core_ids` extracts the archived core set from role-result-shaped
mapping rows, :func:`materialize_run_rotation` turns the already-loaded rows
of one run (and its optional immediate predecessor run) into frozen per-topic
event payloads, and :func:`rotation_event_payload` projects one event to the
JSON-safe archive shape. Database row-to-mapping assembly belongs to the
repository; this module never imports SQLAlchemy.

The timeline describes **set algebra only**. ``change`` compares consecutive
visible core sets (the trend rank-1, ``status="candidate"`` securities
materialized for each run) as ``unchanged`` / ``expanded`` / ``contracted`` /
``reorganized``. It never asserts causation: labels such as succession,
relay or replacement are deliberately absent.

History is never backfilled from current members. A sector with no visible
run for a date simply emits no event for that date; when the calculation
basis between two consecutive visible runs is incomparable (rule/topic/
taxonomy/membership versions, the member identity pool, an explicit
predecessor pointer, or a non-adjacent supplied session calendar), the
current run starts a fresh ``segment_start`` and later comparisons never
reach across that break. Runs with ``available_at > as_of`` are invisible
even when their trade date is inside the observation window.

Identifier ordering contract: ``member_ids`` and ``core_ids`` are
canonicalized once, at the model boundary, as ``tuple(sorted(set(...)))`` so
timeline output is independent of input ordering and duplicates.

Every supplied run is first validated as a :class:`RotationRun` and only
then filtered by ``as_of``/``sector`` visibility, so an invalid run can
raise even when it would be invisible; duplicate ``(sector, date)`` is
judged only across the visible set that survives filtering.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import ConfigDict, field_validator, model_validator

from dragon_radar.domain.models import FrozenModel, Identifier, UTCDateTime

ChangeKind = Literal[
    "segment_start",
    "unchanged",
    "expanded",
    "contracted",
    "reorganized",
]

# Fixed emission order for segment-break reasons; deduplicated with
# dict.fromkeys so the reason tuple is itself a stable contract.
_FIRST_OBSERVATION = "first_observation"
_NO_CORE_CANDIDATE = "no_core_candidate"

# Single authoritative vocabulary of every reason token the v1 rotation
# contract may ever archive or serve. Frozen alongside tokenizer-style
# versioned contracts: a new reason requires bumping the rotation payload
# version, never an ad-hoc string. Both the model boundary and the HTTP
# frozen-block validation import this constant.
ROTATION_REASONS = frozenset(
    {
        _FIRST_OBSERVATION,
        "rule_version_changed",
        "topic_revision_changed",
        "taxonomy_version_changed",
        "membership_version_changed",
        "member_pool_changed",
        "previous_comparable_run_mismatch",
        "not_previous_comparable_session",
        _NO_CORE_CANDIDATE,
    }
)

# Set-algebra changes are the only changes allowed for a comparable event.
_SET_ALGEBRA_CHANGES = frozenset({"unchanged", "expanded", "contracted", "reorganized"})


class _StrictFrozenRotationModel(FrozenModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class RotationInputManifest(_StrictFrozenRotationModel):
    """Frozen calculation binding echoed with every timeline event."""

    run_id: Identifier
    market_snapshot_id: UUID
    topic_snapshot_id: UUID
    rule_version: Identifier
    membership_version: Identifier


class RotationRun(_StrictFrozenRotationModel):
    """One published, visible role-run projection for one sector and date."""

    run_id: Identifier
    sector_id: Identifier
    trade_date: date
    available_at: UTCDateTime
    rule_version: Identifier
    topic_source_version: Identifier
    taxonomy_version: Identifier
    membership_version: Identifier
    member_ids: tuple[Identifier, ...]
    core_ids: tuple[Identifier, ...]
    market_snapshot_id: UUID
    topic_snapshot_id: UUID
    previous_comparable_run_id: Identifier | None = None

    @field_validator("member_ids", "core_ids", mode="after")
    @classmethod
    def _canonicalize_identifiers(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        # Single normalization point for the identifier ordering contract.
        return tuple(sorted(set(values)))

    @model_validator(mode="after")
    def validate_core_pool_membership(self) -> Self:
        if not set(self.core_ids).issubset(self.member_ids):
            raise ValueError("core_ids must be a subset of member_ids")
        return self


class RotationEvent(_StrictFrozenRotationModel):
    """One visible observation and its comparison against the prior event."""

    at: Identifier
    sector_id: Identifier
    core_ids: tuple[Identifier, ...]
    change: ChangeKind
    comparable: bool
    reasons: tuple[Identifier, ...]
    input_manifest: RotationInputManifest

    @field_validator("reasons", mode="after")
    @classmethod
    def _validate_reason_vocabulary(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        # Boundary of the frozen reason contract: publication-time
        # materialization fails closed on any token outside ROTATION_REASONS,
        # so a fabricated reason can never be archived and later served.
        unknown = [value for value in values if value not in ROTATION_REASONS]
        if unknown:
            raise ValueError(f"unknown rotation reason token(s): {unknown}")
        return values

    @model_validator(mode="after")
    def validate_comparison_result(self) -> Self:
        is_segment_start = self.change == "segment_start"
        # segment_start and comparable=False must describe the same state.
        if is_segment_start == self.comparable:
            raise ValueError("segment_start must agree with comparable being False")
        if not self.comparable and not self.reasons:
            raise ValueError("not comparable events must carry reasons")
        if self.comparable and self.change not in _SET_ALGEBRA_CHANGES:
            raise ValueError("comparable events must describe set algebra")
        return self


class RotationTimeline(_StrictFrozenRotationModel):
    """Immutable, globally ordered rotation events."""

    events: tuple[RotationEvent, ...]


def _is_previous_session(
    previous_date: date,
    current_date: date,
    sessions: Sequence[date],
) -> bool:
    try:
        current_index = tuple(sessions).index(current_date)
    except ValueError:
        return False
    # A previous date absent from the calendar can never equal the immediate
    # predecessor session, so this also fails closed on that case.
    return current_index > 0 and sessions[current_index - 1] == previous_date


def _segment_break_reasons(
    previous: RotationRun,
    current: RotationRun,
    sessions: Sequence[date] | None,
) -> tuple[str, ...]:
    checks = (
        (
            current.rule_version == previous.rule_version,
            "rule_version_changed",
        ),
        (
            current.topic_source_version == previous.topic_source_version,
            "topic_revision_changed",
        ),
        (
            current.taxonomy_version == previous.taxonomy_version,
            "taxonomy_version_changed",
        ),
        (
            current.membership_version == previous.membership_version,
            "membership_version_changed",
        ),
        (
            frozenset(current.member_ids) == frozenset(previous.member_ids),
            "member_pool_changed",
        ),
        (
            current.previous_comparable_run_id is None
            or current.previous_comparable_run_id == previous.run_id,
            "previous_comparable_run_mismatch",
        ),
        (
            sessions is None
            or _is_previous_session(previous.trade_date, current.trade_date, sessions),
            "not_previous_comparable_session",
        ),
    )
    return tuple(dict.fromkeys(reason for matches, reason in checks if not matches))


def _set_change(
    previous_cores: frozenset[str],
    current_cores: frozenset[str],
) -> ChangeKind:
    if current_cores == previous_cores:
        return "unchanged"
    if previous_cores < current_cores:
        return "expanded"
    if current_cores < previous_cores:
        return "contracted"
    return "reorganized"


def build_rotation(
    published_runs: Iterable[RotationRun],
    *,
    as_of: datetime | None = None,
    sector: str | None = None,
    sessions: Sequence[date] | None = None,
) -> RotationTimeline:
    """Build the visible core-set timeline from published role runs.

    ``as_of`` hides every run with ``available_at > as_of`` (a run visible
    exactly at the cutoff stays visible); ``None`` means all supplied runs
    are visible. An aware, non-UTC ``as_of`` is normalized to its UTC instant
    before comparison; a naive ``as_of`` raises ``ValueError``. ``sector``
    restricts output to one sector and must be a nonempty, non-whitespace
    identifier when supplied. When ``sessions`` is supplied it must be a
    nonempty, sorted, unique trading calendar and consecutive events must
    occupy adjacent sessions; without it no session adjacency check is made.

    Two visible runs sharing ``(sector_id, trade_date)`` raise
    ``ValueError``; so do empty, unsorted or duplicate ``sessions``. Runs are
    compared strictly within a sector in trade-date order (``run_id`` is the
    deterministic tiebreaker, though same-date duplicates are rejected
    first). The first observation of each segment is never comparable and
    carries ``first_observation``. Events are returned globally sorted by
    ``(trade_date, sector_id)``.
    """
    if as_of is not None:
        if (
            not isinstance(as_of, datetime)
            or as_of.tzinfo is None
            or (as_of.utcoffset() is None)
        ):
            raise ValueError("as_of must be a timezone-aware datetime")
        as_of = as_of.astimezone(UTC)
    if sector is not None and not sector.strip():
        raise ValueError("sector must be a nonempty, non-whitespace identifier")

    session_days: tuple[date, ...] | None = None
    if sessions is not None:
        session_days = tuple(sessions)
        if not session_days or session_days != tuple(sorted(set(session_days))):
            raise ValueError("sessions must be nonempty, sorted and unique trade dates")

    visible_runs: list[RotationRun] = []
    seen_keys: set[tuple[str, date]] = set()
    for item in published_runs:
        run = RotationRun.model_validate(item)
        if as_of is not None and run.available_at > as_of:
            continue
        if sector is not None and run.sector_id != sector:
            continue
        key = (run.sector_id, run.trade_date)
        if key in seen_keys:
            raise ValueError(
                f"duplicate rotation run for sector {run.sector_id!r} "
                f"on {run.trade_date.isoformat()}"
            )
        seen_keys.add(key)
        visible_runs.append(run)

    runs_by_sector: dict[str, list[RotationRun]] = {}
    for run in visible_runs:
        runs_by_sector.setdefault(run.sector_id, []).append(run)

    # Carry the trade date alongside each event so the global ordering never
    # re-parses the ISO ``at`` string.
    dated_events: list[tuple[date, RotationEvent]] = []
    for sector_id in sorted(runs_by_sector):
        ordered = sorted(
            runs_by_sector[sector_id],
            key=lambda run: (run.trade_date, run.run_id),
        )
        previous: RotationRun | None = None
        for run in ordered:
            if previous is None:
                break_reasons = (_FIRST_OBSERVATION,)
            else:
                break_reasons = _segment_break_reasons(previous, run, session_days)
            if break_reasons:
                change: ChangeKind = "segment_start"
                comparable = False
                reasons = break_reasons
            else:
                assert previous is not None
                change = _set_change(
                    frozenset(previous.core_ids),
                    frozenset(run.core_ids),
                )
                comparable = True
                reasons = (_NO_CORE_CANDIDATE,) if not run.core_ids else ()
            dated_events.append(
                (
                    run.trade_date,
                    RotationEvent(
                        at=run.trade_date.isoformat(),
                        sector_id=run.sector_id,
                        core_ids=run.core_ids,
                        change=change,
                        comparable=comparable,
                        reasons=reasons,
                        input_manifest=RotationInputManifest(
                            run_id=run.run_id,
                            market_snapshot_id=run.market_snapshot_id,
                            topic_snapshot_id=run.topic_snapshot_id,
                            rule_version=run.rule_version,
                            membership_version=run.membership_version,
                        ),
                    ),
                )
            )
            previous = run

    dated_events.sort(key=lambda item: (item[0], item[1].sector_id))
    return RotationTimeline(events=tuple(event for _, event in dated_events))


def trend_core_ids(
    result_rows: Iterable[Mapping[str, Any]],
) -> tuple[Identifier, ...]:
    """Extract the archived trend core set from role-result mapping rows.

    A security is a core member only when its trend result row is an
    internal ``candidate`` at ``tie_rank == 1`` (the same caliber used by
    the role relay materializer). Identifiers are canonicalized as
    ``tuple(sorted(set(...)))`` so the output is independent of row order
    and duplicates.
    """
    return tuple(
        sorted(
            {
                row["security_id"]
                for row in result_rows
                if row["role"] == "trend"
                and row["internal_status"] == "candidate"
                and row["tie_rank"] == 1
            }
        )
    )


def rotation_event_payload(event: RotationEvent) -> dict[str, Any]:
    """Project one rotation event to its JSON-safe archived block shape."""
    manifest = event.input_manifest
    return {
        "at": event.at,
        "sector_id": event.sector_id,
        "core_ids": list(event.core_ids),
        "change": event.change,
        "comparable": event.comparable,
        "reasons": list(event.reasons),
        "input_manifest": {
            "run_id": manifest.run_id,
            "market_snapshot_id": str(manifest.market_snapshot_id),
            "topic_snapshot_id": str(manifest.topic_snapshot_id),
            "rule_version": manifest.rule_version,
            "membership_version": manifest.membership_version,
        },
    }


def _run_projection(
    run_row: Mapping[str, Any],
    topic_row: Mapping[str, Any],
    core_ids: Sequence[Identifier],
) -> dict[str, Any]:
    previous_id = run_row["previous_comparable_run_id"]
    return {
        "run_id": str(run_row["id"]),
        "sector_id": topic_row["topic_id"],
        "trade_date": run_row["trade_date"],
        "available_at": run_row["materialized_at"],
        "rule_version": run_row["rule_version"],
        "topic_source_version": run_row["topic_source_version"],
        "taxonomy_version": run_row["taxonomy_version"],
        "membership_version": run_row["membership_version"],
        "member_ids": tuple(topic_row["member_ids"]),
        "core_ids": tuple(core_ids),
        "market_snapshot_id": run_row["market_snapshot_id"],
        "topic_snapshot_id": run_row["topic_snapshot_id"],
        "previous_comparable_run_id": (
            str(previous_id) if previous_id is not None else None
        ),
    }


def _results_by_topic(
    result_rows: Iterable[Mapping[str, Any]] | None,
) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in result_rows or ():
        grouped.setdefault(row["topic_id"], []).append(row)
    return grouped


def materialize_run_rotation(
    *,
    run_row: Mapping[str, Any],
    topic_rows: Sequence[Mapping[str, Any]],
    result_rows: Iterable[Mapping[str, Any]],
    sessions: Sequence[date],
    predecessor_row: Mapping[str, Any] | None = None,
    predecessor_topic_rows: Sequence[Mapping[str, Any]] | None = None,
    predecessor_result_rows: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build frozen per-topic rotation blocks for one published role run.

    Inputs are the already-loaded run row, ``role_run_topics`` rows and
    ``role_results`` rows of the current run and, when the run has an
    immediate comparable predecessor run, the same three row sets for that
    predecessor. ``sessions`` is the deterministic market calendar (no clock
    read). Returns ``{topic_id: rotation event block}`` for every current
    topic; a sector absent from the predecessor restarts with
    ``first_observation`` and history is never backfilled.

    Every run is validated through :class:`RotationRun` first, so corrupted
    rows fail the publication transaction instead of producing a silent gap.
    """
    predecessor_topics = (
        {row["topic_id"]: row for row in predecessor_topic_rows}
        if predecessor_row is not None and predecessor_topic_rows is not None
        else {}
    )
    current_results = _results_by_topic(result_rows)
    predecessor_results = _results_by_topic(predecessor_result_rows)
    current_date = run_row["trade_date"]
    blocks: dict[str, dict[str, Any]] = {}
    for topic_row in topic_rows:
        topic_id = topic_row["topic_id"]
        runs: list[RotationRun] = []
        previous_topic = predecessor_topics.get(topic_id)
        if predecessor_row is not None and previous_topic is not None:
            runs.append(
                RotationRun.model_validate(
                    _run_projection(
                        predecessor_row,
                        previous_topic,
                        trend_core_ids(predecessor_results.get(topic_id, ())),
                    )
                )
            )
        runs.append(
            RotationRun.model_validate(
                _run_projection(
                    run_row,
                    topic_row,
                    trend_core_ids(current_results.get(topic_id, ())),
                )
            )
        )
        timeline = build_rotation(runs, sessions=sessions)
        current_events = [
            event
            for event in timeline.events
            if event.sector_id == topic_id and event.at == current_date.isoformat()
        ]
        # Invariant: the current run is always one of the ``runs`` fed to
        # build_rotation for exactly this topic row (both sector_id and date
        # are projected from the same run/topic mappings), so its event is
        # present exactly once. A miss or duplicate signals corrupt row
        # assembly above rather than a data gap; fail the publication.
        if len(current_events) != 1:
            raise ValueError(
                "rotation materialization invariant violated: expected exactly "
                f"one event for sector {topic_id!r} at "
                f"{current_date.isoformat()}, got {len(current_events)}"
            )
        blocks[topic_id] = rotation_event_payload(current_events[0])
    return blocks


__all__ = [
    "ChangeKind",
    "ROTATION_REASONS",
    "RotationEvent",
    "RotationInputManifest",
    "RotationRun",
    "RotationTimeline",
    "build_rotation",
    "materialize_run_rotation",
    "rotation_event_payload",
    "trend_core_ids",
]
