"""Published regulation completeness, calculation, and formal-event reads."""

from collections.abc import Mapping
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import ValidationError

from dragon_radar.http.contracts import (
    archived_coverage,
    empty_envelope,
    snapshot_envelope,
)
from dragon_radar.http.errors import DataUnavailable, StorageUnavailable
from dragon_radar.http.selection import query_date, query_values, select_snapshot
from dragon_radar.http.serialization import DecimalJSONResponse
from dragon_radar.publication.models import PublishedSnapshot
from dragon_radar.regulation.models import (
    RegulationCalculation,
    RegulationEvent,
    RegulationRule,
)
from dragon_radar.regulation.repository import (
    REGULATION_SCHEMA,
    REGULATION_SCHEMA_VERSION,
)

router = APIRouter()

_EVENT_DISPLAY_STATES = {
    "abnormal": "disclosed",
    "serious_abnormal": "disclosed",
    "monitoring": "monitoring",
    "restriction": "restricted",
}


def _repository(request: Request):
    repository = request.app.state.repository
    if repository is None:
        raise StorageUnavailable
    return repository


def _selection(request: Request, *, allow_security: bool = False):
    allowed = {"date", "snapshot_id"}
    if allow_security:
        allowed.add("security_id")
    query = query_values(request, allowed)
    trade_date = query_date(query.get("date"))
    if trade_date is None:
        raise ValueError("Regulation date is required")
    snapshot_id = UUID(query["snapshot_id"]) if "snapshot_id" in query else None
    security_id = query.get("security_id")
    if security_id is not None and (not security_id.strip() or len(security_id) > 200):
        raise ValueError("Invalid security")
    return trade_date, snapshot_id, security_id


def _selected_snapshot(request: Request, trade_date: date, snapshot_id: UUID | None):
    snapshot = select_snapshot(_repository(request), trade_date, snapshot_id)
    if snapshot is None or not isinstance(snapshot, PublishedSnapshot):
        return None
    return snapshot


def _models(value: object, model):
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, Mapping) for item in value
    ):
        raise DataUnavailable
    try:
        return tuple(
            model.model_validate(
                {key: item[key] for key in model.model_fields if key in item}
            )
            for item in value
        )
    except (KeyError, ValidationError):
        raise DataUnavailable from None


def _regulation_payload(snapshot: PublishedSnapshot):
    result = snapshot.result("regulation")
    if result.status != "ready":
        return None, result
    payload = dict(result.payload)
    event_source_completeness = payload.get("event_source_completeness", "unknown")
    event_source_id = payload.get("event_source_id")
    event_source_version = payload.get("event_source_version")
    event_source_is_valid = (
        event_source_completeness == "unknown"
        and event_source_id is None
        and event_source_version is None
    ) or (
        event_source_completeness == "complete"
        and isinstance(event_source_id, str)
        and bool(event_source_id)
        and isinstance(event_source_version, str)
        and bool(event_source_version)
    )
    if (
        payload.get("schema") != REGULATION_SCHEMA
        or payload.get("schema_version") != REGULATION_SCHEMA_VERSION
        or payload.get("snapshot_id") != str(result.input_id)
        or payload.get("trade_date") != snapshot.trade_date.isoformat()
        or not event_source_is_valid
        or not isinstance(payload.get("completeness"), Mapping)
    ):
        raise DataUnavailable
    coverage = archived_coverage(dict(payload["completeness"]))
    if coverage != archived_coverage(dict(result.coverage)):
        raise DataUnavailable
    rules = _models(payload.get("rules"), RegulationRule)
    calculations = _models(payload.get("calculations"), RegulationCalculation)
    events = _models(payload.get("events"), RegulationEvent)
    identities = {(rule.rule_id, rule.rule_version) for rule in rules}
    if (
        len(identities) != len(rules)
        or any(
            (item.rule_id, item.rule_version) not in identities
            or item.trade_date != snapshot.trade_date
            for item in calculations
        )
        or any((item.rule_id, item.rule_version) not in identities for item in events)
    ):
        raise DataUnavailable
    return {
        "coverage": coverage,
        "rules": rules,
        "calculations": calculations,
        "events": events,
        "event_source_completeness": event_source_completeness,
        "stored_rules": tuple(payload["rules"]),
    }, result


def _completeness(coverage: dict[str, int], source_check: str):
    expected = coverage["expected"]
    accounted = coverage["valid"] + coverage["excluded"]
    if coverage["missing"] == 0:
        status = "complete"
    elif accounted == 0:
        status = "missing"
    else:
        status = "partial"
    return {
        "status": status,
        "coverage_ratio": accounted / expected if expected else 1,
        "missing_count": coverage["missing"],
        "source_check": source_check,
        **coverage,
    }


def _event(item: RegulationEvent):
    return {
        **item.model_dump(),
        "display_state": _EVENT_DISPLAY_STATES[item.event_type],
    }


def _calculation(item: RegulationCalculation, stored_rule: Mapping):
    return {
        **item.model_dump(),
        "official_source_url": stored_rule["official_source_url"],
        "effective_from": stored_rule["effective_from"],
        "effective_to": stored_rule["effective_to"],
        "production_enabled": stored_rule["production_enabled"],
        "production_gate_reasons": list(stored_rule["production_gate_reasons"]),
    }


def _response(
    snapshot,
    result,
    data,
    coverage,
    *,
    reason=None,
    coverage_unknown=False,
):
    payload = snapshot_envelope(snapshot, data, unit="ratio", coverage=coverage)
    if coverage_unknown:
        payload["coverage"] = None
    payload["source"] = "regulation_materialization"
    payload["source_version"] = str(REGULATION_SCHEMA_VERSION)
    payload["rule_version"] = None
    payload["quality_flags"] = sorted(
        {
            *payload["quality_flags"],
            *result.quality_flags,
            *((reason,) if reason else ()),
        }
    )
    if reason is not None:
        payload["reason"] = reason
    return DecimalJSONResponse(payload)


def _unknown_response(snapshot, result, *, status: bool):
    reason = f"regulation_{result.status}"
    if status:
        data = {
            "region1_completeness": {
                "status": "missing",
                "coverage_ratio": None,
                "missing_count": None,
                "source_check": "unknown",
                "expected": None,
                "valid": None,
                "excluded": None,
                "missing": None,
            },
            "region2_calc_state": [],
            "region3_events": None,
        }
    else:
        data = {
            "events": None,
            "listed_count": None,
            "total": None,
            "source_check": "unknown",
        }
    return _response(
        snapshot,
        result,
        data,
        None,
        reason=reason,
        coverage_unknown=True,
    )


@router.get("/api/regulation/status")
def regulation_status(request: Request):
    trade_date, snapshot_id, _security_id = _selection(request)
    snapshot = _selected_snapshot(request, trade_date, snapshot_id)
    if snapshot is None:
        return DecimalJSONResponse(empty_envelope(trade_date))
    payload, result = _regulation_payload(snapshot)
    if payload is None:
        return _unknown_response(snapshot, result, status=True)
    stored_rules = {
        (item["rule_id"], item["rule_version"]): item
        for item in payload["stored_rules"]
    }
    calculations = [
        _calculation(
            item,
            stored_rules[(item.rule_id, item.rule_version)],
        )
        for item in payload["calculations"]
    ]
    calculations.sort(
        key=lambda item: (
            item["rule_id"],
            item["rule_version"],
            item["security_id"],
        )
    )
    events = [_event(item) for item in payload["events"]]
    return _response(
        snapshot,
        result,
        {
            "region1_completeness": _completeness(payload["coverage"], "pass"),
            "region2_calc_state": calculations,
            "region3_events": events,
        },
        payload["coverage"],
    )


@router.get("/api/regulation/events")
def regulation_events(request: Request):
    trade_date, snapshot_id, security_id = _selection(
        request,
        allow_security=True,
    )
    snapshot = _selected_snapshot(request, trade_date, snapshot_id)
    if snapshot is None:
        return DecimalJSONResponse(empty_envelope(trade_date))
    payload, result = _regulation_payload(snapshot)
    if payload is None:
        return _unknown_response(snapshot, result, status=False)
    events = [
        _event(item)
        for item in payload["events"]
        if security_id is None or item.security_id == security_id
    ]
    return _response(
        snapshot,
        result,
        {
            "events": events,
            "listed_count": len(events),
            "total": (
                len(events)
                if payload["event_source_completeness"] == "complete"
                else None
            ),
            "source_check": (
                "pass"
                if payload["event_source_completeness"] == "complete"
                else "unknown"
            ),
        },
        payload["coverage"],
        reason=(
            None
            if payload["event_source_completeness"] == "complete"
            else "regulation_event_source_unknown"
        ),
    )


__all__ = ["_regulation_payload", "router"]
