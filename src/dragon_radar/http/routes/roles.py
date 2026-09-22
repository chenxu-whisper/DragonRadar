"""Published limit-up ladder and role evidence routes."""

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request

from dragon_radar.http.contracts import (
    archived_coverage,
    empty_envelope,
    snapshot_envelope,
)
from dragon_radar.http.errors import DataUnavailable, StorageUnavailable
from dragon_radar.http.routes.market import _published_payload
from dragon_radar.http.selection import query_date, query_values, select_snapshot
from dragon_radar.http.serialization import DecimalJSONResponse
from dragon_radar.publication.models import PublishedSnapshot
from dragon_radar.roles.adapters import adapt_role_evaluation
from dragon_radar.roles.models import (
    RankedCandidate,
    RoleEvaluation,
    RoleTopicStatus,
)
from dragon_radar.roles.rules import RESET_APPROVED_RULE_VERSIONS, ROLE_NAMES
from dragon_radar.storage.repository import NotFound

router = APIRouter()

_EXTERNAL_ROLES = frozenset(ROLE_NAMES.values())
_INTERNAL_ROLE = {external: internal for internal, external in ROLE_NAMES.items()}
_ROLE_ORDER = {role: position for position, role in enumerate(ROLE_NAMES.values())}


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


def _role_coverage(result) -> dict[str, int]:
    return archived_coverage(dict(result.coverage))


def _response(
    snapshot: PublishedSnapshot,
    role_result,
    role_payload,
    data,
    coverage,
    *,
    quality_flags=(),
):
    payload = snapshot_envelope(snapshot, data, unit="ratio", coverage=coverage)
    payload["source"] = "role_materialization"
    payload["rule_version"] = (
        role_payload["rule_version"] if role_payload is not None else None
    )
    payload["basis"]["price_type"] = "adjusted"
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
    for topic in _selected_topics(role_payload, sector):
        topic_id = topic["topic_id"]
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
                or boards < 1
                or security_id not in names
            ):
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
    return _response(
        snapshot,
        role_result,
        role_payload,
        data,
        _role_coverage(role_result),
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
        median = market_payload["overview"]["sentiment"][
            "yesterday_limit_up_median_return"
        ]
    except (KeyError, TypeError):
        raise DataUnavailable from None
    data = {
        "promote_rate": promote,
        "yesterday_limit_up_median_return": median,
        "tradable_count": promote["tradable"],
    }
    return _response(
        snapshot,
        role_result,
        role_payload,
        data,
        _role_coverage(role_result),
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
    quality_flags = set()
    for topic in selected_topics:
        evaluation = _evaluation(topic, internal_role, role_payload["rule_version"])
        if evaluation is None:
            continue
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
        for evidence in adapted:
            if "rule_version_not_approved" in evidence.reasons:
                quality_flags.add("rule_version_not_approved")
            if evidence.status != "candidate" or evidence.security_id is None:
                continue
            ladder_row = next(
                (
                    row
                    for row in topic.get("ladder", ())
                    if row.get("security_id") == evidence.security_id
                ),
                None,
            )
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
        {"role": external_role, "candidates": output, "total": len(output)},
        coverage,
        quality_flags=quality_flags,
    )


def _factor_projection(item):
    reasons = list(item.get("reasons", ()))
    metadata = item.get("metadata") or {}
    source_time = next(
        (
            metadata[key]
            for key in ("observation_at", "knowledge_cutoff", "source_time")
            if metadata.get(key) is not None
        ),
        None,
    )
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
        "source_time": source_time,
    }


def _evidence_reasons(status, result, factors, evidence_status):
    persisted = [
        *status.get("reasons", ()),
        *(result.get("reasons", ()) if result is not None else ()),
    ]
    factor_reasons = [
        reason for factor in factors for reason in factor.get("reasons", ())
    ]
    missing = [
        reason
        for reason in (*persisted, *factor_reasons)
        if any(
            marker in reason for marker in ("missing", "unavailable", "insufficient")
        )
    ]
    nonmissing = [reason for reason in persisted if reason not in missing]
    if evidence_status in {"candidate", "waiting_verify"}:
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
            None,
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
            status, result, factor_rows, evidence_status
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
    coverage = {
        "expected": len(topic.get("member_ids", ())),
        "valid": 1,
        "excluded": 0,
        "missing": max(len(topic.get("member_ids", ())) - 1, 0),
    }
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
