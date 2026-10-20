"""Published limit-up ladder and role evidence routes."""

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request

from dragon_radar.http.contracts import (
    coverage_view,
    empty_envelope,
    snapshot_envelope,
)
from dragon_radar.http.errors import DataUnavailable, StorageUnavailable
from dragon_radar.http.routes.market import _published_payload
from dragon_radar.http.selection import (
    query_date,
    query_integer,
    query_values,
    select_snapshot,
)
from dragon_radar.http.serialization import DecimalJSONResponse
from dragon_radar.publication.models import PublishedSnapshot
from dragon_radar.roles.adapters import adapt_role_evaluation
from dragon_radar.roles.models import (
    RankedCandidate,
    RoleEvaluation,
    RoleTopicStatus,
)
from dragon_radar.roles.rotation import ROTATION_REASONS
from dragon_radar.roles.rules import RESET_APPROVED_RULE_VERSIONS, ROLE_NAMES
from dragon_radar.storage.repository import NotFound

router = APIRouter()

_EXTERNAL_ROLES = frozenset(ROLE_NAMES.values())
_INTERNAL_ROLE = {external: internal for internal, external in ROLE_NAMES.items()}
_ROLE_ORDER = {role: position for position, role in enumerate(ROLE_NAMES.values())}
_ADAPTER_MISSING_REASONS = frozenset(
    {
        "rule_version_not_approved",
        "historical_research_only",
        "capability_not_formal_candidate",
        "pending_validation",
    }
)


def _repository(request: Request):
    repository = request.app.state.repository
    if repository is None:
        raise StorageUnavailable
    return repository


def _required_text(value: str | None) -> str:
    if value is None or not value.strip() or len(value) > 100:
        raise ValueError("Invalid query")
    return value


def _selection(request: Request, required: set[str], optional: set[str]):
    query = query_values(request, required | optional | {"date", "snapshot_id"})
    if any(key not in query for key in required) or "date" not in query:
        raise ValueError("Invalid query")
    trade_date = query_date(query["date"])
    snapshot_id = UUID(query["snapshot_id"]) if "snapshot_id" in query else None
    for key in required | optional:
        if key in query:
            query[key] = _required_text(query[key])
    return query, trade_date, snapshot_id


def _select_publication(request, trade_date, snapshot_id):
    snapshot = select_snapshot(_repository(request), trade_date, snapshot_id)
    if snapshot is None or not isinstance(snapshot, PublishedSnapshot):
        return None
    return snapshot


def _role_payload(snapshot: PublishedSnapshot):
    result = snapshot.result("role")
    if result.status != "ready":
        return None, result
    payload = dict(result.payload)
    if (
        not {
            "role_run_id",
            "trade_date",
            "rule_version",
            "benchmark_id",
            "topics",
        }
        <= payload.keys()
    ):
        raise DataUnavailable
    if payload["trade_date"] != snapshot.trade_date.isoformat():
        raise DataUnavailable
    return payload, result


def _response(
    snapshot: PublishedSnapshot,
    role_result,
    role_payload,
    data,
    coverage,
    *,
    price_type="adjusted",
    quality_flags=(),
):
    payload = snapshot_envelope(snapshot, data, unit="ratio", coverage=coverage)
    payload["source"] = "role_materialization"
    payload["rule_version"] = (
        role_payload["rule_version"] if role_payload is not None else None
    )
    payload["basis"]["price_type"] = price_type
    payload["basis"]["benchmark"] = (
        role_payload["benchmark_id"] if role_payload is not None else None
    )
    payload["quality_flags"] = sorted(
        {
            *payload["quality_flags"],
            *role_result.quality_flags,
            *quality_flags,
        }
    )
    return DecimalJSONResponse(payload)


def _domain_unavailable(snapshot, role_result):
    payload = snapshot_envelope(snapshot, None, unit=None)
    payload["source"] = "role_materialization"
    payload["coverage"] = None
    payload["rule_version"] = None
    payload["basis"] = {
        "unit": None,
        "currency": None,
        "price_type": None,
        "benchmark": None,
    }
    payload["quality_flags"] = sorted(
        {
            *payload["quality_flags"],
            *role_result.quality_flags,
            f"role_{role_result.status}",
        }
    )
    return DecimalJSONResponse(payload)


def _decimal(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return Decimal(value)
    except InvalidOperation:
        return value


def _names(market_payload) -> dict[str, str]:
    try:
        return {
            item["security_id"]: item["name"]
            for item in market_payload["search"]["securities"]
        }
    except (KeyError, TypeError):
        raise DataUnavailable from None


def _evaluation(topic, internal_role: str, rule_version: str):
    status = next(
        (
            item
            for item in topic.get("statuses", ())
            if item.get("role") == internal_role
        ),
        None,
    )
    if status is None:
        return None
    try:
        topic_status = RoleTopicStatus(
            topic_id=topic["topic_id"],
            role=internal_role,
            capability=status["capability"],
            status=status["status"],
            expected=status["expected"],
            valid=status["valid"],
            legal_excluded=status["legal_excluded"],
            missing=status["missing"],
            reasons=tuple(status.get("reasons", ())),
        )
        candidates = tuple(
            RankedCandidate(
                topic_id=topic["topic_id"],
                security_id=item["security_id"],
                role=internal_role,
                status=item["internal_status"],
                factors=tuple(_decimal(value) for value in item.get("factors", ())),
                reasons=tuple(item.get("reasons", ())),
                tie_rank=item["tie_rank"],
            )
            for item in topic.get("results", ())
            if item.get("role") == internal_role
        )
        return RoleEvaluation(topic_status=topic_status, candidates=candidates)
    except (KeyError, TypeError, ValueError):
        raise DataUnavailable from None


def _adapted(topic, internal_role: str, rule_version: str):
    evaluation = _evaluation(topic, internal_role, rule_version)
    if evaluation is None:
        return ()
    return adapt_role_evaluation(
        evaluation,
        rule_version=rule_version,
        approved_rule_versions=RESET_APPROVED_RULE_VERSIONS,
    )


def _selected_topics(payload, sector):
    topics = list(payload["topics"])
    if sector is not None:
        topics = [topic for topic in topics if topic.get("topic_id") == sector]
    return topics


def _promote_rate(market_payload):
    try:
        stored = market_payload["overview"]["sentiment"]["promote_rate"]
        return {
            "yesterday_ge2": stored["yesterday_ge2"],
            "today_sealed": stored["numerator"],
            "tradable": stored["denominator"],
            "rate": stored["value"],
        }
    except (KeyError, TypeError):
        raise DataUnavailable from None


def _promote_coverage(market_payload):
    try:
        stored = market_payload["overview"]["sentiment"]["promote_rate"]
        valid = stored["denominator"]
        excluded = stored["excluded"]
        missing = stored["missing"]
        return coverage_view(
            valid + excluded + missing,
            valid,
            excluded,
            missing,
        )
    except (KeyError, TypeError, ValueError):
        raise DataUnavailable from None


def _quality_reasons(reason):
    if reason is None:
        return []
    if not isinstance(reason, str) or not reason:
        raise DataUnavailable
    return [reason]


def _feedback_quality(market_payload, median):
    try:
        sentiment = market_payload["overview"]["sentiment"]
        stored = sentiment["yesterday_limit_up_return_coverage"]
        coverage = coverage_view(
            stored["expected"],
            stored["valid"],
            stored["excluded"],
            stored["missing"],
        )
        reasons = _quality_reasons(sentiment["yesterday_limit_up_return_reason"])
    except (KeyError, TypeError, ValueError):
        raise DataUnavailable from None
    return {
        "status": (
            "missing" if median is None else "degraded" if reasons else "available"
        ),
        "coverage": coverage,
        "reasons": reasons,
    }


def _combined_coverage(*coverages):
    return coverage_view(
        *(
            sum(coverage[key] for coverage in coverages)
            for key in (
                "expected",
                "valid",
                "excluded",
                "missing",
            )
        )
    )


@router.get("/api/limit-up/ladder")
def ladder(request: Request):
    query, trade_date, snapshot_id = _selection(request, set(), {"sector"})
    snapshot = _select_publication(request, trade_date, snapshot_id)
    if snapshot is None:
        return DecimalJSONResponse(empty_envelope(trade_date))
    role_payload, role_result = _role_payload(snapshot)
    if role_payload is None:
        return _domain_unavailable(snapshot, role_result)
    market_payload, _market_coverage, materialized = _published_payload(snapshot)
    if not materialized:
        raise DataUnavailable

    sector = query.get("sector")
    names = _names(market_payload)
    by_security = {}
    zero_streak_members = set()
    selected_topics = _selected_topics(role_payload, sector)
    member_ids = {
        security_id
        for topic in selected_topics
        for security_id in topic.get("member_ids", ())
    }
    for topic in selected_topics:
        topic_id = topic["topic_id"]
        topic_members = set(topic.get("member_ids", ()))
        formal_roles = defaultdict(list)
        for internal_role in ROLE_NAMES:
            for evidence in _adapted(
                topic, internal_role, role_payload["rule_version"]
            ):
                if evidence.security_id is not None and evidence.status == "candidate":
                    formal_roles[evidence.security_id].append(evidence.role)
        for row in topic.get("ladder", ()):
            boards = row.get("closing_limit_streak")
            security_id = row.get("security_id")
            if (
                row.get("trade_date") != snapshot.trade_date.isoformat()
                or type(boards) is not int
                or boards < 0
                or security_id not in topic_members
            ):
                continue
            if boards == 0:
                zero_streak_members.add(security_id)
                continue
            if security_id not in names:
                continue
            existing = by_security.get(security_id)
            if existing is not None and existing["boards"] != boards:
                raise DataUnavailable
            candidate = {
                "security_id": security_id,
                "name": names[security_id],
                "roles": sorted(
                    set(formal_roles[security_id]), key=_ROLE_ORDER.__getitem__
                ),
                "sector_id": topic_id,
                "boards": boards,
            }
            if existing is None or topic_id < existing["sector_id"]:
                by_security[security_id] = candidate

    grouped = defaultdict(list)
    for stock in by_security.values():
        grouped[stock.pop("boards")].append(stock)
    cells = [
        {
            "boards": boards,
            "count": len(stocks),
            "stocks": sorted(stocks, key=lambda item: item["security_id"]),
        }
        for boards, stocks in sorted(grouped.items())
    ]
    total = len(by_security)
    if sector is None:
        try:
            heatmap = [
                cell
                for cell in market_payload["heatmap"]["cells"]
                if cell["trade_date"] == snapshot.trade_date.isoformat()
            ]
            expected = {
                cell["boards"]: cell["count"] for cell in heatmap if cell["count"] > 0
            }
            actual = {cell["boards"]: cell["count"] for cell in cells}
            overview_total = market_payload["overview"]["sentiment"]["limit_up_count"]
        except (KeyError, TypeError):
            raise DataUnavailable from None
        if actual != expected or total != overview_total:
            raise DataUnavailable
    data = {
        "columns": [cell["boards"] for cell in cells],
        "total": total,
        "cells": cells,
        "promote_rate": _promote_rate(market_payload),
    }
    coverage = coverage_view(
        len(member_ids),
        total,
        len(zero_streak_members - by_security.keys()),
        len(member_ids) - total - len(zero_streak_members - by_security.keys()),
    )
    return _response(
        snapshot,
        role_result,
        role_payload,
        data,
        coverage,
        price_type="raw",
    )


@router.get("/api/limit-up/continuity")
def continuity(request: Request):
    _query, trade_date, snapshot_id = _selection(request, set(), set())
    snapshot = _select_publication(request, trade_date, snapshot_id)
    if snapshot is None:
        return DecimalJSONResponse(empty_envelope(trade_date))
    role_payload, role_result = _role_payload(snapshot)
    if role_payload is None:
        return _domain_unavailable(snapshot, role_result)
    market_payload, _market_coverage, materialized = _published_payload(snapshot)
    if not materialized:
        raise DataUnavailable
    promote = _promote_rate(market_payload)
    try:
        sentiment = market_payload["overview"]["sentiment"]
        median = sentiment["yesterday_limit_up_median_return"]
    except (KeyError, TypeError):
        raise DataUnavailable from None
    promote_coverage = _promote_coverage(market_payload)
    promote_quality = {
        "coverage": promote_coverage,
        "reasons": _quality_reasons(sentiment["promote_rate"].get("reason")),
    }
    feedback_quality = _feedback_quality(market_payload, median)
    data = {
        "promote_rate": promote,
        "promote_rate_quality": promote_quality,
        "yesterday_limit_up_median_return": median,
        "yesterday_limit_up_feedback": feedback_quality,
        "tradable_count": promote["tradable"],
    }
    return _response(
        snapshot,
        role_result,
        role_payload,
        data,
        _combined_coverage(promote_coverage, feedback_quality["coverage"]),
        price_type="raw",
    )


@router.get("/api/role/candidates")
def candidates(request: Request):
    query, trade_date, snapshot_id = _selection(request, {"role"}, {"sector"})
    external_role = query["role"]
    if external_role not in _EXTERNAL_ROLES:
        raise ValueError("Invalid role")
    snapshot = _select_publication(request, trade_date, snapshot_id)
    if snapshot is None:
        return DecimalJSONResponse(empty_envelope(trade_date))
    role_payload, role_result = _role_payload(snapshot)
    if role_payload is None:
        return _domain_unavailable(snapshot, role_result)
    market_payload, _market_coverage, materialized = _published_payload(snapshot)
    if not materialized:
        raise DataUnavailable

    internal_role = _INTERNAL_ROLE[external_role]
    names = _names(market_payload)
    selected_topics = _selected_topics(role_payload, query.get("sector"))
    coverage = {"expected": 0, "valid": 0, "excluded": 0, "missing": 0}
    output = []
    statuses = []
    change_summaries = []
    role_changes = []
    quality_flags = set()
    for topic in selected_topics:
        evaluation = _evaluation(topic, internal_role, role_payload["rule_version"])
        if evaluation is None:
            continue
        ladder_by_security = {
            row.get("security_id"): row for row in topic.get("ladder", ())
        }
        change_summaries.extend(
            {"sector_id": topic["topic_id"], **item}
            for item in topic.get("change_summaries", ())
            if item.get("role") == internal_role
        )
        role_changes.extend(
            {"sector_id": topic["topic_id"], **item}
            for item in topic.get("role_changes", ())
            if item.get("role") == internal_role
        )
        status = evaluation.topic_status
        for target, source in (
            ("expected", "expected"),
            ("valid", "valid"),
            ("excluded", "legal_excluded"),
            ("missing", "missing"),
        ):
            coverage[target] += getattr(status, source)
        adapted = adapt_role_evaluation(
            evaluation,
            rule_version=role_payload["rule_version"],
            approved_rule_versions=RESET_APPROVED_RULE_VERSIONS,
        )
        status_reasons = list(status.reasons)
        if role_payload["rule_version"] not in RESET_APPROVED_RULE_VERSIONS:
            for reason in (
                "rule_version_not_approved",
                "historical_research_only",
            ):
                if reason not in status_reasons:
                    status_reasons.append(reason)
            quality_flags.add("rule_version_not_approved")
        statuses.append(
            {
                "sector_id": topic["topic_id"],
                "status": status.status,
                "capability": status.capability,
                "reasons": status_reasons,
            }
        )
        for evidence in adapted:
            if "rule_version_not_approved" in evidence.reasons:
                quality_flags.add("rule_version_not_approved")
            if evidence.status != "candidate" or evidence.security_id is None:
                continue
            ladder_row = ladder_by_security.get(evidence.security_id)
            output.append(
                {
                    "security_id": evidence.security_id,
                    "name": names.get(evidence.security_id, evidence.security_id),
                    "rank": evidence.rank,
                    "evidence_status": evidence.status,
                    "sector_id": topic["topic_id"],
                    "boards": (
                        ladder_row.get("closing_limit_streak")
                        if ladder_row is not None
                        else None
                    ),
                }
            )
    output.sort(key=lambda item: (item["rank"], item["sector_id"], item["security_id"]))
    return _response(
        snapshot,
        role_result,
        role_payload,
        {
            "role": external_role,
            "statuses": statuses,
            "candidates": output,
            "change_summaries": change_summaries,
            "role_changes": role_changes,
            "total": len(output),
        },
        coverage,
        quality_flags=quality_flags,
    )


def _factor_projection(item):
    reasons = list(item.get("reasons", ()))
    metadata = item.get("metadata") or {}
    return {
        "factor_id": item["factor_id"],
        "metric": item.get("metric"),
        "value": _decimal(item.get("value")),
        "status": (
            "computed"
            if item.get("value") is not None and not reasons
            else "unavailable"
        ),
        "unit": item.get("unit"),
        "window": item.get("window"),
        "coverage": dict(item.get("coverage") or {}),
        "reasons": reasons,
        "source_time": metadata.get("source_time"),
        "knowledge_cutoff": item.get(
            "knowledge_cutoff", metadata.get("knowledge_cutoff")
        ),
    }


def _evidence_reasons(status, result, factors, adapted):
    persisted = list(
        dict.fromkeys(
            [
                *status.get("reasons", ()),
                *(result.get("reasons", ()) if result is not None else ()),
            ]
        )
    )
    factor_reasons = list(
        dict.fromkeys(
            reason for factor in factors for reason in factor.get("reasons", ())
        )
    )
    adapter_reasons = (
        [reason for reason in adapted.reasons if reason in _ADAPTER_MISSING_REASONS]
        if adapted is not None
        else []
    )
    missing = list(
        dict.fromkeys(
            reason
            for reason in (*persisted, *factor_reasons, *adapter_reasons)
            if any(
                marker in reason
                for marker in ("missing", "unavailable", "insufficient")
            )
            or reason in adapter_reasons
        )
    )
    nonmissing = [reason for reason in persisted if reason not in missing]
    if result is not None and result.get("internal_status") in {
        "candidate",
        "pending_validation",
    }:
        return nonmissing, [], missing
    return [], nonmissing, missing


@router.get("/api/role/evidence")
def evidence(request: Request):
    query, trade_date, snapshot_id = _selection(
        request, {"security_id", "sector"}, set()
    )
    snapshot = _select_publication(request, trade_date, snapshot_id)
    if snapshot is None:
        return DecimalJSONResponse(empty_envelope(trade_date))
    role_payload, role_result = _role_payload(snapshot)
    if role_payload is None:
        return _domain_unavailable(snapshot, role_result)
    topic = next(
        (
            item
            for item in role_payload["topics"]
            if item.get("topic_id") == query["sector"]
        ),
        None,
    )
    if topic is None or query["security_id"] not in topic.get("member_ids", ()):
        raise NotFound("Role evidence entity not found")

    roles = []
    factor_rows = [
        item
        for item in topic.get("factors", ())
        if item.get("security_id") == query["security_id"]
    ]
    projected_factors = [_factor_projection(item) for item in factor_rows]
    for internal_role, external_role in ROLE_NAMES.items():
        status = next(
            (
                item
                for item in topic.get("statuses", ())
                if item.get("role") == internal_role
            ),
            None,
        )
        if status is None:
            continue
        evaluation = _evaluation(topic, internal_role, role_payload["rule_version"])
        adapted = (
            adapt_role_evaluation(
                evaluation,
                rule_version=role_payload["rule_version"],
                approved_rule_versions=RESET_APPROVED_RULE_VERSIONS,
            )
            if evaluation is not None
            else ()
        )
        selected = next(
            (item for item in adapted if item.security_id == query["security_id"]),
            next((item for item in adapted if item.security_id is None), None),
        )
        result = next(
            (
                item
                for item in topic.get("results", ())
                if item.get("role") == internal_role
                and item.get("security_id") == query["security_id"]
            ),
            None,
        )
        evidence_status = selected.status if selected is not None else "insufficient"
        supporting, opposing, missing = _evidence_reasons(
            status, result, factor_rows, selected
        )
        roles.append(
            {
                "role_type": external_role,
                "evidence_status": evidence_status,
                "factors": projected_factors,
                "supporting_evidence": supporting,
                "opposing_evidence": opposing,
                "missing_evidence": missing,
            }
        )
    security_results = [
        item
        for item in topic.get("results", ())
        if item.get("security_id") == query["security_id"]
    ]
    factor_available = any(
        (item.get("coverage") or {}).get("valid", 0) > 0 for item in factor_rows
    )
    valid = int(
        factor_available
        or any(item.get("internal_status") != "excluded" for item in security_results)
    )
    excluded = int(
        not valid
        and bool(security_results)
        and all(item.get("internal_status") == "excluded" for item in security_results)
    )
    coverage = coverage_view(1, valid, excluded, 1 - valid - excluded)
    return _response(
        snapshot,
        role_result,
        role_payload,
        {
            "security_id": query["security_id"],
            "sector_id": query["sector"],
            "roles": roles,
        },
        coverage,
    )


# ---------------------------------------------------------------------------
# EX11: sector rotation timeline. Read-side assembly only: per-sector event
# blocks are materialized at publication time and archived inside the role
# payload; the endpoint never reruns a role algorithm. Archived blocks are
# validated fail-closed against the frozen seven-key contract (at, sector_id,
# core_ids, change, comparable, reasons, input_manifest); that block and
# rotation_version are never altered. The wire projection is an eight-key,
# read-only view: the internal reasons tuple collapses to one reason token
# and the serving publication id is added as snapshot_id so clients can drill
# into evidence at the exact historical publication rather than the latest one.
# ---------------------------------------------------------------------------

_ROTATION_BLOCK_KEYS = frozenset(
    {
        "at",
        "sector_id",
        "core_ids",
        "change",
        "comparable",
        "reasons",
        "input_manifest",
    }
)
_ROTATION_MANIFEST_KEYS = frozenset(
    {
        "run_id",
        "market_snapshot_id",
        "topic_snapshot_id",
        "rule_version",
        "membership_version",
    }
)
_ROTATION_CHANGES = frozenset(
    {"segment_start", "unchanged", "expanded", "contracted", "reorganized"}
)
# Only payloads carrying exactly this frozen version enter block validation;
# a bool True compares equal to int 1 in Python, so the type is pinned too.
_ROTATION_PAYLOAD_VERSION = 1


def _rotation_is_version_one(payload: dict[str, Any]) -> bool:
    version = payload.get("rotation_version")
    return type(version) is int and version == _ROTATION_PAYLOAD_VERSION


def _rotation_payload(snapshot: PublishedSnapshot):
    """Return the JSON-normalized ready role payload, or ``(None, result)``.

    A missing or non-ready role result is a day the caller may skip; a ready
    payload missing the frozen identity keys is corruption and fails closed.
    """
    result = next(
        (item for item in snapshot.results if item.domain == "role"),
        None,
    )
    if result is None or result.status != "ready":
        return None, result
    # model_dump(mode="json") is required (rather than reading ``result.payload``
    # directly): ImmutableJsonObject hydrates stored JSON arrays as tuples, and
    # the frozen-block contract below validates list-specific shapes. JSON mode
    # converts those tuples back to plain lists exactly as the wire response
    # does, so validation sees the same shape clients receive.
    payload = result.model_dump(mode="json")["payload"]
    if (
        not isinstance(payload, dict)
        or not {
            "role_run_id",
            "trade_date",
            "rule_version",
            "benchmark_id",
            "topics",
        }
        <= payload.keys()
    ):
        raise DataUnavailable
    if not isinstance(payload["role_run_id"], str) or not payload["role_run_id"]:
        raise DataUnavailable
    if payload["trade_date"] != snapshot.trade_date.isoformat():
        raise DataUnavailable
    return payload, result


def _identifier_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and bool(item) for item in value)
        and len(set(value)) == len(value)
    )


def _validate_rotation_block(
    block: object, *, at: date, sector_id: str, member_ids: list[str], run_id: str
) -> None:
    """Validate one archived rotation block against the frozen contract.

    Any mismatch raises :class:`DataUnavailable`; the timeline endpoint is
    fail-closed and never projects a corrupted block.
    """
    if not isinstance(block, dict) or set(block) != _ROTATION_BLOCK_KEYS:
        raise DataUnavailable
    if block.get("at") != at.isoformat() or block.get("sector_id") != sector_id:
        raise DataUnavailable
    core_ids = block.get("core_ids")
    if not _identifier_list(core_ids) or not set(core_ids) <= set(member_ids):
        raise DataUnavailable
    change = block.get("change")
    comparable = block.get("comparable")
    if change not in _ROTATION_CHANGES or type(comparable) is not bool:
        raise DataUnavailable
    # segment_start and comparable=False describe the same state.
    if (change == "segment_start") == comparable:
        raise DataUnavailable
    reasons = block.get("reasons")
    if not _identifier_list(reasons):
        raise DataUnavailable
    # The reason vocabulary is frozen with the v1 rotation contract; an
    # archived token outside the shared pure-module allowlist is corruption.
    if any(reason not in ROTATION_REASONS for reason in reasons):
        raise DataUnavailable
    if comparable:
        if reasons not in ([], ["no_core_candidate"]):
            raise DataUnavailable
    elif not reasons:
        raise DataUnavailable
    manifest = block.get("input_manifest")
    if not isinstance(manifest, dict) or set(manifest) != _ROTATION_MANIFEST_KEYS:
        raise DataUnavailable
    if manifest.get("run_id") != run_id:
        raise DataUnavailable
    try:
        UUID(manifest.get("market_snapshot_id"))
        UUID(manifest.get("topic_snapshot_id"))
    except (AttributeError, TypeError, ValueError):
        raise DataUnavailable from None
    if any(
        not isinstance(manifest.get(key), str) or not manifest.get(key)
        for key in ("rule_version", "membership_version")
    ):
        raise DataUnavailable


def _rotation_topic_events(
    snapshot: PublishedSnapshot, payload: dict[str, Any], sector: str | None
) -> list[dict[str, Any]]:
    """Project frozen rotation blocks into the eight-key wire event shape.

    The archived block keeps its seven-key contract (with the internal
    ``reasons`` tuple); each projected event collapses that tuple to one
    nullable reason and carries ``snapshot_id``: the id of the publication
    serving this event, so evidence drill-down stays pinned to the historical
    published version.
    """
    topics = payload["topics"]
    if not isinstance(topics, list):
        raise DataUnavailable
    events: list[dict[str, Any]] = []
    for topic in topics:
        if not isinstance(topic, dict):
            raise DataUnavailable
        topic_id = topic.get("topic_id")
        member_ids = topic.get("member_ids")
        if not isinstance(topic_id, str) or not topic_id:
            raise DataUnavailable
        if not _identifier_list(member_ids):
            raise DataUnavailable
        block = topic.get("rotation")
        _validate_rotation_block(
            block,
            at=snapshot.trade_date,
            sector_id=topic_id,
            member_ids=member_ids,
            run_id=payload["role_run_id"],
        )
        if sector is not None and topic_id != sector:
            continue
        if block["comparable"]:
            reason = (
                "no_core_candidate"
                if block["reasons"] == ["no_core_candidate"]
                else None
            )
        else:
            reason = block["reasons"][0]
        events.append(
            {
                "at": block["at"],
                "sector_id": topic_id,
                "core_ids": block["core_ids"],
                "change": block["change"],
                "comparable": block["comparable"],
                "reason": reason,
                "input_manifest": block["input_manifest"],
                # Read-side projection only: the publication serving this
                # event, so clients drill into evidence at this version
                # instead of the latest publication. Never written back to
                # the frozen block; absent from rotation_version's contract.
                "snapshot_id": str(snapshot.id),
            }
        )
    return events


def _rotation_domain_unavailable(snapshot: PublishedSnapshot, role_result):
    payload = snapshot_envelope(snapshot, None, unit=None)
    payload["source"] = "role_materialization"
    payload["coverage"] = None
    payload["rule_version"] = None
    payload["basis"] = {
        "unit": None,
        "currency": None,
        "price_type": None,
        "benchmark": None,
    }
    flags = set(payload["quality_flags"])
    if role_result is not None:
        flags.update(role_result.quality_flags)
        flags.add(f"role_{role_result.status}")
    else:
        flags.add("role_missing")
    payload["quality_flags"] = sorted(flags)
    return DecimalJSONResponse(payload)


@router.get("/api/topics/rotation")
def topic_rotation(request: Request):
    query = query_values(request, {"date", "days", "sector", "snapshot_id"})
    trade_date = query_date(query.get("date"))
    if trade_date is None:
        raise ValueError("Rotation date is required")
    days = query_integer(query.get("days", "30"), 1, 250)
    sector = query.get("sector")
    if sector is not None:
        sector = _required_text(sector)
    snapshot_id = UUID(query["snapshot_id"]) if "snapshot_id" in query else None

    repository = _repository(request)
    snapshot = _select_publication(request, trade_date, snapshot_id)
    if snapshot is None:
        return DecimalJSONResponse(empty_envelope(trade_date))
    anchor_payload, anchor_result = _rotation_payload(snapshot)
    if anchor_payload is None:
        return _rotation_domain_unavailable(snapshot, anchor_result)
    if not _rotation_is_version_one(anchor_payload):
        raise DataUnavailable

    sessions = snapshot.batch.calendar.sessions
    try:
        anchor_index = sessions.index(snapshot.trade_date)
    except ValueError:
        raise DataUnavailable from None
    window_start = sessions[max(0, anchor_index - days + 1)]
    window_end = snapshot.trade_date
    history = repository.list_published_window(snapshot.id, window_start, window_end)

    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    quality_flags = set()
    for daily_snapshot in sorted(
        [*history, snapshot], key=lambda item: item.trade_date
    ):
        if daily_snapshot is snapshot:
            payload, role_result = anchor_payload, anchor_result
        else:
            payload, role_result = _rotation_payload(daily_snapshot)
            if payload is None or not _rotation_is_version_one(payload):
                # Older publications predate rotation materialization or
                # carry a different frozen payload version; history is never
                # backfilled and their blocks are never validated. Such a
                # day is intentionally asymmetric with the anchor: it does
                # not participate, so its quality flags are not merged.
                continue
        quality_flags.update(role_result.quality_flags)
        for event in _rotation_topic_events(daily_snapshot, payload, sector):
            key = (event["at"], event["sector_id"])
            # Each (at, sector) comes from a distinct publication and trade
            # date, so a cross-snapshot duplicate cannot occur structurally
            # (the watermark query is DISTINCT ON trade_date). The practical
            # defense this guards is two topics carrying the same topic_id
            # inside one frozen payload; fail closed either way.
            if key in seen:
                raise DataUnavailable
            seen.add(key)
            events.append(event)
    events.sort(key=lambda event: (event["at"], event["sector_id"]))

    data = {
        "events": events,
        "days": days,
        "sector": sector,
        "window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
        },
    }
    payload = snapshot_envelope(snapshot, data, unit=None)
    payload["source"] = "role_materialization"
    payload["rule_version"] = anchor_payload["rule_version"]
    payload["basis"]["benchmark"] = anchor_payload["benchmark_id"]
    payload["quality_flags"] = sorted({*payload["quality_flags"], *quality_flags})
    return DecimalJSONResponse(payload)
