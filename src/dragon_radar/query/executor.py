"""EX13 pure in-memory execution of a stored whitelist query on one publication.

The executor never reruns an algorithm: every value comes from the immutable
materialized payloads archived with the publication. The repository has
already re-authorized the publication and confirmed the AST hash, so this
module is deliberately free of IO, authentication and clock/network access.

Two-layer unavailability contract (spec 3.4):

* Declarative unavailability (the materialized window honestly reports
  ``available: false`` with a materialized reason, or the raw adjustment
  layer is declaratively unavailable) is a normal business result: HTTP 200,
  ``rows: []`` and one ``return_ratio_unavailable`` item in ``data.unavailable``.
* Structural corruption (a window claims availability but its closed sets do
  not partition the eligible universe, sessions drift from the frozen
  calendar, the benchmark is not B000300, or per-security board streaks
  disagree across topics) fails closed with :class:`DataUnavailable`, which
  the route maps to HTTP 503.

``RESET_APPROVED_RULE_VERSIONS`` is imported as a module attribute on
purpose: tests pin the approval seam at
``dragon_radar.query.executor.RESET_APPROVED_RULE_VERSIONS``.
"""

from collections import defaultdict
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from dragon_radar.analytics.calendar import TradingCalendar
from dragon_radar.http.contracts import coverage_view, snapshot_envelope
from dragon_radar.http.errors import DataUnavailable
from dragon_radar.http.routes.analysis import (
    _exclusion_ids,
    _published_eligible_ids,
    _ranking_ids,
)
from dragon_radar.http.routes.market import _published_payload
from dragon_radar.http.routes.roles import (
    _ROLE_ORDER,
    _evaluation,
    _names,
    _role_payload,
)
from dragon_radar.http.routes.stock import _market_extensions
from dragon_radar.publication.models import PublishedSnapshot
from dragon_radar.query.models import Field, Op, QueryAst, SortDirection
from dragon_radar.roles.adapters import adapt_role_evaluation
from dragon_radar.roles.rules import RESET_APPROVED_RULE_VERSIONS, ROLE_NAMES

_BENCHMARK_ID = "B000300"
_WINDOW_OFFSETS = 20
_WINDOW_KEY = str(_WINDOW_OFFSETS)
_ROUNDING = "ROUND_HALF_UP"
_RETURN_RATIO_UNAVAILABLE = "return_ratio_unavailable"
_ROLE_MATERIALIZATION_UNAVAILABLE = "role_materialization_unavailable"
_PROJECTION_KEYS = frozenset(
    {
        "ranking",
        "exclusions",
        "curves",
        "benchmark_return_ratio",
        "benchmark_reasons",
    }
)

_FIELDS: tuple[dict[str, str], ...] = (
    {"key": "security_id", "label": "证券代码"},
    {"key": "name", "label": "名称"},
    {"key": "sector_ids", "label": "所属题材"},
    {"key": "roles", "label": "角色"},
    {"key": "boards", "label": "连板数"},
    {
        "key": "return_ratio",
        "label": "20日涨幅",
        "basis": "20 交易日 · 未复权 raw_close · 基准 B000300",
    },
)

_INTERNAL_ROLE = {external: internal for internal, external in ROLE_NAMES.items()}


# ---------------------------------------------------------------------------
# Predicate plan
# ---------------------------------------------------------------------------


def _literal_set(value: object) -> frozenset:
    if isinstance(value, tuple):
        return frozenset(value)
    return frozenset({value})


def _numeric_target(op: Op, value: object) -> tuple[Op, Any]:
    if op is Op.IN:
        return op, frozenset(value)
    return op, value


def _compare(op: Op, target: Any, value: Any) -> bool:
    if op is Op.EQ:
        return target == value
    if op is Op.IN:
        return target in value
    if op is Op.GT:
        return target > value
    if op is Op.GTE:
        return target >= value
    if op is Op.LT:
        return target < value
    return target <= value


class _Plan:
    def __init__(self, ast: QueryAst):
        self.security: list[frozenset[str]] = []
        self.sector: list[frozenset[str]] = []
        self.role: list[frozenset[str]] = []
        self.boards: list[tuple[Op, Any]] = []
        self.ratio: list[tuple[Op, Any]] = []
        selected_topic_ids: set[str] = set()
        for predicate in ast.predicates:
            field = predicate.field
            if field is Field.SECURITY_ID:
                self.security.append(_literal_set(predicate.value))
            elif field is Field.SECTOR_ID:
                wanted = _literal_set(predicate.value)
                self.sector.append(wanted)
                selected_topic_ids.update(wanted)
            elif field is Field.ROLE:
                self.role.append(_literal_set(predicate.value))
            elif field is Field.BOARDS:
                self.boards.append(_numeric_target(predicate.op, predicate.value))
            else:
                self.ratio.append(_numeric_target(predicate.op, predicate.value))
        self.selected_topic_ids = frozenset(selected_topic_ids)
        self.sort = ast.sort
        self.limit = ast.limit
        self.needs_topics = bool(self.sector or self.role or self.boards)
        # A reference is a dependency (spec 3.4): an explicit return-ratio
        # sort loads the 20-day window even without a ratio predicate, so a
        # declarative window gap is still surfaced via ``unavailable``.
        self.needs_ratio = bool(self.ratio) or (
            self.sort is not None and self.sort.field is Field.RETURN_RATIO
        )


# ---------------------------------------------------------------------------
# Role payload projections
# ---------------------------------------------------------------------------


def _members(topic: dict) -> list[str]:
    members = topic.get("member_ids")
    if (
        not isinstance(members, list)
        or not members
        or any(not isinstance(item, str) or not item for item in members)
    ):
        raise DataUnavailable
    return members


def _thaw(value: object) -> object:
    """Project an immutable, recursively-frozen JSON value back to plain JSON.

    Archived role payloads are :class:`ImmutableJsonObject` values: dicts are
    stored behind ``MappingProxyType`` and arrays as tuples. Unlike
    ``restore_decimal_fields`` this is a pure container walk -- role statuses
    and ladder rows carry only int/str/bool tokens, and the Decimal financial
    fields are restored exclusively through the market payload seam.
    """

    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


def _role_context(snapshot: PublishedSnapshot):
    """Return ``((topics, rule_version), result)`` for a ready role result.

    A non-ready (``disabled``/``missing``) role domain returns
    ``(None, result)`` so the caller treats the domain as declaratively
    unavailable instead of failing closed. A ready result with a malformed
    payload is structural corruption and raises :class:`DataUnavailable`.
    """

    payload, result = _role_payload(snapshot)
    if payload is None:
        return None, result
    plain = _thaw(payload)
    topics = plain.get("topics")
    rule_version = plain.get("rule_version")
    if (
        not isinstance(topics, list)
        or any(not isinstance(topic, dict) for topic in topics)
        or not isinstance(rule_version, str)
        or not rule_version
    ):
        raise DataUnavailable
    for topic in topics:
        topic_id = topic.get("topic_id")
        if not isinstance(topic_id, str) or not topic_id:
            raise DataUnavailable
    return (topics, rule_version), result


def _candidate_roles(topics, rule_version: str) -> dict[str, set[str]]:
    roles: dict[str, set[str]] = defaultdict(set)
    for topic in topics:
        for internal_role in ROLE_NAMES:
            evaluation = _evaluation(topic, internal_role, rule_version)
            if evaluation is None:
                continue
            for evidence in adapt_role_evaluation(
                evaluation,
                rule_version=rule_version,
                approved_rule_versions=RESET_APPROVED_RULE_VERSIONS,
            ):
                if evidence.status == "candidate" and evidence.security_id is not None:
                    roles[evidence.security_id].add(evidence.role)
    return roles


def _boards(snapshot: PublishedSnapshot, topics) -> dict[str, int]:
    """Today's closing-limit streak per security over the given topics.

    Mirrors the ladder route's validation: only same-trade-date rows with a
    non-negative integer streak for a topic member count. A security seen
    with two different streaks (within one topic or across topics) is
    publication corruption and fails closed (D4).
    """

    trade_date = snapshot.trade_date.isoformat()
    boards: dict[str, int] = {}
    for topic in topics:
        members = set(_members(topic))
        per_topic: dict[str, int] = {}
        ladder = topic.get("ladder", ())
        if not isinstance(ladder, list):
            raise DataUnavailable
        for row in ladder:
            if not isinstance(row, dict):
                raise DataUnavailable
            streak = row.get("closing_limit_streak")
            security_id = row.get("security_id")
            if (
                row.get("trade_date") != trade_date
                or type(streak) is not int
                or not isinstance(security_id, str)
                or security_id not in members
            ):
                continue
            # The wire domain for closing-limit streaks is int 0..99 (spec 3.2);
            # a qualifying same-date row outside the domain is corruption.
            if streak < 0 or streak > 99:
                raise DataUnavailable
            if security_id in per_topic and per_topic[security_id] != streak:
                raise DataUnavailable
            per_topic[security_id] = streak
        for security_id, streak in per_topic.items():
            if security_id in boards and boards[security_id] != streak:
                raise DataUnavailable
            boards.setdefault(security_id, streak)
    return boards


# ---------------------------------------------------------------------------
# Return-ratio window (D1 two-layer unavailability)
# ---------------------------------------------------------------------------


def _declared_unavailable(contract: object) -> str | None:
    if not isinstance(contract, dict) or type(contract.get("available")) is not bool:
        raise DataUnavailable
    if contract["available"]:
        return None
    reason = contract.get("reason")
    if not isinstance(reason, str) or not reason:
        raise DataUnavailable
    return reason


def _window_sessions(snapshot: PublishedSnapshot) -> list[str]:
    calendar = TradingCalendar(snapshot.batch.calendar)
    try:
        sessions = calendar.n_day_window(snapshot.trade_date, _WINDOW_OFFSETS)
    except ValueError as error:
        # An available window contradicting an insufficient calendar is
        # structural corruption; callers only invoke this after confirming
        # the materialized window declares availability.
        raise DataUnavailable from error
    return [day.isoformat() for day in sessions]


def _top_raw_contract(analysis: dict):
    """The top-level ``analysis.adjustments.raw`` declaration, or ``None``.

    ``None`` covers a missing adjustments object or a missing raw flag.
    """

    adjustments = analysis.get("adjustments")
    if not isinstance(adjustments, dict):
        return None
    raw = adjustments.get("raw")
    return raw if isinstance(raw, dict) else None


def _return_ratios(
    snapshot: PublishedSnapshot,
    analysis: dict,
    eligible: set[str],
) -> tuple[dict[str, Decimal], list[dict], str | None]:
    """Return (ratios, ranking rows, declarative reason).

    ``eligible`` is the materialized eligible universe, computed once by the
    caller and reused here for the projection closed-set checks.

    D1(a) declaration ordering -- the window stub decides first:

    1. A ``windows["20"]`` object whose ``available`` is ``false`` with a
       non-empty reason short-circuits immediately; the unrelated top-level
       raw flag is never inspected.
    2. With no ``"20"`` window (missing or JSON null), an honest top-level
       ``raw.available=false`` declaration carries the business reason; any
       other shape fails closed.
    3. Only a window declaring ``available=true`` is cross-checked against
       the raw flag and the frozen projection closed sets.
    """

    windows = analysis.get("windows")
    if not isinstance(windows, dict):
        raise DataUnavailable
    candidate = windows.get(_WINDOW_KEY)

    if isinstance(candidate, dict):
        window_reason = _declared_unavailable(candidate)
        if window_reason is not None:
            return {}, [], window_reason
    elif candidate is None:
        raw = _top_raw_contract(analysis)
        if raw is not None:
            raw_reason = _declared_unavailable(raw)
            if raw_reason is not None:
                return {}, [], raw_reason
        raise DataUnavailable
    else:
        raise DataUnavailable

    # The window claims availability: the raw layer must still be honestly
    # declared before any projection is consumed.
    raw = _top_raw_contract(analysis)
    if raw is None:
        raise DataUnavailable
    raw_reason = _declared_unavailable(raw)
    if raw_reason is not None:
        return {}, [], raw_reason

    stored_sessions = candidate.get("sessions")
    if (
        candidate.get("benchmark_id") != _BENCHMARK_ID
        or not isinstance(stored_sessions, list)
        or stored_sessions != _window_sessions(snapshot)
    ):
        raise DataUnavailable
    layers = candidate.get("adjustments")
    if not isinstance(layers, dict):
        raise DataUnavailable
    projected = layers.get("raw")
    if not isinstance(projected, dict) or set(projected) != _PROJECTION_KEYS:
        raise DataUnavailable
    ranking = projected["ranking"]
    exclusions = projected["exclusions"]
    # The closed-set helpers iterate both layers. The key-set check above
    # proves presence only, so a JSON null/bool/number makes ``for row in
    # rows`` raise TypeError, which bypasses the DATA_UNAVAILABLE mapping and
    # surfaces as the wrong-code 503 STORAGE_UNAVAILABLE; a tuple is
    # corruption too -- the hydration walks (``_thaw`` /
    # restore_decimal_fields) normalize every archived array to a list. Fail
    # closed before either is consumed.
    if not isinstance(ranking, list) or not isinstance(exclusions, list):
        raise DataUnavailable
    curves = projected["curves"]
    benchmark_return = projected["benchmark_return_ratio"]
    benchmark_reasons = projected["benchmark_reasons"]
    if (
        not isinstance(curves, dict)
        or not isinstance(benchmark_reasons, list)
        or any(not isinstance(item, str) for item in benchmark_reasons)
    ):
        raise DataUnavailable
    if benchmark_return is not None and (
        not isinstance(benchmark_return, Decimal) or not benchmark_return.is_finite()
    ):
        raise DataUnavailable
    ranking_ids = _ranking_ids(ranking, eligible)
    exclusion_ids = _exclusion_ids(exclusions, eligible)
    if ranking_ids & exclusion_ids or ranking_ids | exclusion_ids != eligible:
        raise DataUnavailable
    ratios = {row["security_id"]: row["return_ratio"] for row in ranking}
    return ratios, ranking, None


# ---------------------------------------------------------------------------
# Row evaluation
# ---------------------------------------------------------------------------


def _verdict(
    security_id: str,
    plan: _Plan,
    sectors: dict[str, set[str]],
    all_roles: dict[str, set[str]],
    boards: dict[str, int],
    ratios: dict[str, Decimal],
    *,
    topics_available: bool,
) -> str:
    """Three-way classification (spec 3.4).

    Every predicate is evaluated so an unknown field outranks an explicit
    falsehood elsewhere in the conjunction: any unknowable predicate makes
    the row ``missing``; otherwise any false predicate makes it ``excluded``.
    When the role domain is declaratively unavailable, its sector/role/boards
    predicates are unknowable for every security.
    """

    unknown = False
    failed = False
    for literals in plan.security:
        if security_id not in literals:
            failed = True
    if plan.sector or plan.role or plan.boards:
        if not topics_available:
            unknown = True
        else:
            for wanted in plan.sector:
                if not (sectors.get(security_id, set()) & wanted):
                    failed = True
            for wanted in plan.role:
                if not (all_roles.get(security_id, set()) & wanted):
                    failed = True
            for op, value in plan.boards:
                target = boards.get(security_id)
                if target is None:
                    unknown = True
                elif not _compare(op, target, value):
                    failed = True
    for op, value in plan.ratio:
        target = ratios.get(security_id)
        if target is None:
            unknown = True
        elif not _compare(op, target, value):
            failed = True
    if unknown:
        return "missing"
    return "excluded" if failed else "valid"


def _sort_rows(rows: list[dict], plan: _Plan) -> list[dict]:
    rows.sort(key=lambda row: row["security_id"])
    sort = plan.sort
    if sort is None:
        if plan.ratio:
            field, direction = Field.RETURN_RATIO, SortDirection.DESC
        elif plan.boards:
            field, direction = Field.BOARDS, SortDirection.DESC
        else:
            return rows
    else:
        field, direction = sort.field, sort.direction
    descending = direction is SortDirection.DESC
    if field is Field.SECURITY_ID:
        if descending:
            rows.reverse()
        return rows
    if field is Field.SECTOR_ID:
        rows.sort(
            key=lambda row: tuple(row["sector_ids"]),
            reverse=descending,
        )
    elif field is Field.ROLE:
        rows.sort(
            key=lambda row: tuple(_ROLE_ORDER[role] for role in row["roles"]),
            reverse=descending,
        )
    else:
        key_name = "boards" if field is Field.BOARDS else "return_ratio"

        def numeric_key(row):
            value = row[key_name]
            if descending:
                return (value is None, -value if value is not None else 0)
            return (value is None, value)

        rows.sort(key=numeric_key)
    return rows


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def execute_query(snapshot: PublishedSnapshot, ast: QueryAst) -> dict:
    """Execute a confirmed AST against one authorized published snapshot.

    Returns the frozen EX13 envelope (a plain JSON-friendly dict containing
    :class:`~decimal.Decimal` values for the serializer). Raises
    :class:`DataUnavailable` for structural corruption only; declarative
    unavailability stays a 200 envelope with empty rows.
    """

    market_payload, _market_coverage, materialized = _published_payload(snapshot)
    if not materialized:
        raise DataUnavailable
    analysis, stocks, _coverage = _market_extensions(snapshot)
    adjustment_version = analysis.get("adjustment_version")
    if not isinstance(adjustment_version, str) or not adjustment_version:
        raise DataUnavailable

    plan = _Plan(ast)
    # The materialized eligible universe is the fallback pool whenever the
    # role domain does not provide topic membership.
    eligible = set(_published_eligible_ids(analysis, stocks))

    sectors: dict[str, set[str]] = defaultdict(set)
    all_roles: dict[str, set[str]] = {}
    boards: dict[str, int] = {}
    unavailable: list[dict[str, str]] = []
    pool: set[str] = set()
    topics_available = False
    if plan.needs_topics:
        context, role_result = _role_context(snapshot)
        if context is None:
            # Declarative domain unavailability (disabled/missing role result)
            # is a business result, recorded before any ratio item so the
            # unavailable order mirrors predicate-source order.
            unavailable.append(
                {
                    "code": _ROLE_MATERIALIZATION_UNAVAILABLE,
                    "reason": f"role_{role_result.status}",
                }
            )
        else:
            topics, rule_version = context
            selected = (
                [
                    topic
                    for topic in topics
                    if topic.get("topic_id") in plan.selected_topic_ids
                ]
                if plan.selected_topic_ids
                else topics
            )
            for topic in topics:
                for security_id in _members(topic):
                    sectors[security_id].add(topic["topic_id"])
            # Role and boards predicates are satisfied in ANY topic, so they
            # always evaluate over every published topic (D4) -- even topics
            # outside a sector selection -- and a cross-topic streak
            # disagreement anywhere is corruption (spec 3.4).
            all_roles = _candidate_roles(topics, rule_version)
            boards = _boards(snapshot, topics)
            pool = {
                security_id for topic in selected for security_id in _members(topic)
            }
            topics_available = True
    if not topics_available:
        if plan.security:
            pool = set().union(*plan.security)
        else:
            pool = set(eligible)

    ratios: dict[str, Decimal] = {}
    ranking_names: dict[str, str] = {}
    if plan.needs_ratio:
        ratios, ranking, reason = _return_ratios(snapshot, analysis, eligible)
        if reason is not None:
            unavailable.append({"code": _RETURN_RATIO_UNAVAILABLE, "reason": reason})
        else:
            ranking_names = {row["security_id"]: row.get("name") for row in ranking}

    # Search names are a display-only projection: a malformed search block
    # degrades to null names, never fails a boards/role/sector query (spec
    # 3.4: "still unavailable -> name=null").
    try:
        names = _names(market_payload)
    except DataUnavailable:
        names = {}

    # Every candidate keeps its three-way bucket even when one source is
    # declaratively unavailable: rows from other sources still form.
    valid_rows: list[dict] = []
    missing = 0
    excluded = 0
    for security_id in pool:
        verdict = _verdict(
            security_id,
            plan,
            sectors,
            all_roles,
            boards,
            ratios,
            topics_available=topics_available,
        )
        if verdict == "missing":
            missing += 1
            continue
        if verdict == "excluded":
            excluded += 1
            continue
        ranked_name = ranking_names.get(security_id)
        valid_rows.append(
            {
                "security_id": security_id,
                "name": (
                    ranked_name
                    if isinstance(ranked_name, str) and ranked_name
                    else names.get(security_id)
                ),
                "sector_ids": sorted(sectors.get(security_id, set())),
                "roles": sorted(
                    all_roles.get(security_id, set()),
                    key=_ROLE_ORDER.__getitem__,
                ),
                "boards": boards.get(security_id),
                "return_ratio": ratios.get(security_id),
            }
        )

    total = len(valid_rows)
    rows = _sort_rows(valid_rows, plan)[: plan.limit]
    data = {
        "fields": list(_FIELDS),
        "rows": rows,
        "total": total,
        "unavailable": unavailable,
    }
    coverage = coverage_view(len(pool), total, excluded, missing)
    envelope = snapshot_envelope(
        snapshot,
        data,
        unit="ratio",
        coverage=coverage,
    )
    envelope["rule_version"] = analysis["rule_version"]
    basis = envelope["basis"]
    basis["price_type"] = "raw_close"
    basis["benchmark"] = _BENCHMARK_ID
    basis["adjustment_version"] = adjustment_version
    basis["rounding"] = _ROUNDING
    return envelope


__all__ = ["RESET_APPROVED_RULE_VERSIONS", "execute_query"]
