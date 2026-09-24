"""Published news reads and separately installed owner association review."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError

from dragon_radar.http.contracts import (
    archived_coverage,
    empty_envelope,
    snapshot_envelope,
)
from dragon_radar.http.errors import DataUnavailable, StorageUnavailable, error_response
from dragon_radar.http.selection import (
    query_date,
    query_integer,
    query_values,
    select_snapshot,
)
from dragon_radar.http.serialization import DecimalJSONResponse
from dragon_radar.news.repository import NewsRepository
from dragon_radar.news.service import (
    NEWS_RULE_VERSION,
    NEWS_SCHEMA,
    NEWS_SCHEMA_VERSION,
)
from dragon_radar.owner.security import (
    BoundaryError,
    OwnerWrite,
    invalid_request,
    json_object,
)
from dragon_radar.publication.models import PublishedSnapshot
from dragon_radar.storage.repository import Conflict, Forbidden, NotFound

router = APIRouter()
_NEWS_TYPES = frozenset({"news", "announcement", "research"})


def _repository(request: Request):
    repository = request.app.state.repository
    if repository is None:
        raise StorageUnavailable
    return repository


def _selection(request: Request, *, detail: bool = False):
    allowed = {"date", "snapshot_id"}
    if not detail:
        allowed |= {"type", "source", "q", "security_id", "page", "size"}
    query = query_values(request, allowed)
    trade_date = query_date(query.get("date"))
    if trade_date is None:
        raise ValueError("News date is required")
    snapshot_id = UUID(query["snapshot_id"]) if "snapshot_id" in query else None
    return query, trade_date, snapshot_id


def _article(value) -> bool:
    if not isinstance(value, dict):
        return False
    required = {
        "article_id",
        "revision_id",
        "revision",
        "event_id",
        "type",
        "source",
        "title",
        "url",
        "published_at",
        "received_at",
        "available_at",
        "content_scope",
        "summary",
        "content",
        "securities",
        "topics",
    }
    if set(value) != required:
        return False
    try:
        UUID(value["article_id"])
        UUID(value["revision_id"])
        UUID(value["event_id"])
    except (TypeError, ValueError):
        return False
    if (
        type(value["revision"]) is not int
        or value["revision"] < 1
        or value["type"] not in _NEWS_TYPES
        or not isinstance(value["source"], dict)
        or set(value["source"]) != {"id", "version"}
        or not all(
            isinstance(value["source"][field], str) and value["source"][field]
            for field in ("id", "version")
        )
        or not all(
            isinstance(value[field], str) and value[field]
            for field in (
                "title",
                "url",
                "published_at",
                "received_at",
                "available_at",
                "content_scope",
            )
        )
        or value["content_scope"] not in {"none", "summary", "full"}
        or value["summary"] is not None
        and not isinstance(value["summary"], str)
        or value["content"] is not None
        and not isinstance(value["content"], str)
        or not isinstance(value["securities"], list)
        or not isinstance(value["topics"], list)
    ):
        return False
    return all(_association(item, "security") for item in value["securities"]) and all(
        _association(item, "topic") for item in value["topics"]
    )


def _association(value, target_type: str) -> bool:
    if not isinstance(value, dict):
        return False
    target_field = f"{target_type}_id"
    if set(value) != {
        "association_id",
        target_field,
        "method",
        "status",
        "evidence_snippets",
        "effective",
        "pending_topic_publication",
    }:
        return False
    try:
        UUID(value["association_id"])
    except (TypeError, ValueError):
        return False
    return (
        isinstance(value[target_field], str)
        and bool(value[target_field])
        and value["method"] in {"direct", "manual", "ai"}
        and value["status"] in {"accepted", "unreviewed", "rejected"}
        and isinstance(value["evidence_snippets"], list)
        and bool(value["evidence_snippets"])
        and all(
            isinstance(item, str) and bool(item.strip())
            for item in value["evidence_snippets"]
        )
        and type(value["effective"]) is bool
        and type(value["pending_topic_publication"]) is bool
    )


def _payload(snapshot: PublishedSnapshot):
    result = snapshot.result("news")
    if result.status != "ready":
        return None, archived_coverage(dict(result.coverage)), f"news_{result.status}"
    value = result.payload
    if (
        not isinstance(value, dict)
        or value.get("schema") != NEWS_SCHEMA
        or value.get("schema_version") != NEWS_SCHEMA_VERSION
        or value.get("rule_version") != NEWS_RULE_VERSION
        or not isinstance(value.get("articles"), list)
        or not all(_article(article) for article in value["articles"])
        or not isinstance(value.get("sources"), list)
        or not all(isinstance(source, str) and source for source in value["sources"])
        or type(value.get("event_count")) is not int
        or value["event_count"] < 0
    ):
        raise DataUnavailable
    return value, archived_coverage(dict(result.coverage)), None


def _response(snapshot, data, coverage, *, reason=None):
    payload = snapshot_envelope(snapshot, data, unit=None, coverage=coverage)
    payload["source"] = "news_materialization"
    payload["source_version"] = NEWS_RULE_VERSION
    payload["rule_version"] = NEWS_RULE_VERSION
    payload["basis"] = {
        "unit": None,
        "currency": None,
        "price_type": None,
        "benchmark": None,
    }
    if reason is not None:
        payload["reason"] = reason
        payload["quality_flags"] = sorted({*payload["quality_flags"], reason})
    return DecimalJSONResponse(payload)


def _selected_snapshot(request: Request, trade_date: date, snapshot_id: UUID | None):
    snapshot = select_snapshot(_repository(request), trade_date, snapshot_id)
    if snapshot is None or not isinstance(snapshot, PublishedSnapshot):
        return None
    return snapshot


@router.get("/api/news")
def list_news(request: Request):
    query, trade_date, snapshot_id = _selection(request)
    news_type = query.get("type", "")
    if news_type and news_type not in _NEWS_TYPES:
        raise ValueError("Invalid news type")
    source = query.get("source", "")
    term = query.get("q", "").strip()
    security_id = query.get("security_id", "")
    if any(len(value) > 200 for value in (source, security_id)) or len(term) > 100:
        raise ValueError("Invalid news filter")
    page = query_integer(query.get("page", "1"), 1, 100000)
    size = query_integer(query.get("size", "20"), 1, 100)
    snapshot = _selected_snapshot(request, trade_date, snapshot_id)
    if snapshot is None:
        return DecimalJSONResponse(empty_envelope(trade_date))
    payload, coverage, reason = _payload(snapshot)
    if payload is None:
        return _response(snapshot, None, coverage, reason=reason)
    folded = term.casefold()
    items = [
        article
        for article in payload["articles"]
        if (not news_type or article["type"] == news_type)
        and (not source or article["source"]["id"] == source)
        and (
            not security_id
            or any(item["security_id"] == security_id for item in article["securities"])
        )
        and (
            not folded
            or folded
            in " ".join(filter(None, (article["title"], article["summary"]))).casefold()
        )
    ]
    items.sort(key=lambda item: item["article_id"])
    items.sort(key=lambda item: item["available_at"], reverse=True)
    total = len(items)
    start = (page - 1) * size
    selected = items[start : start + size]
    data = {
        "items": selected,
        "page": page,
        "size": size,
        "total": total,
        "sources": sorted({item["source"]["id"] for item in items}),
        "event_count": len({item["event_id"] for item in items}),
    }
    return _response(snapshot, data, coverage)


@router.get("/api/news/{article_id}")
def news_detail(article_id: UUID, request: Request):
    _query, trade_date, snapshot_id = _selection(request, detail=True)
    snapshot = _selected_snapshot(request, trade_date, snapshot_id)
    if snapshot is None:
        return DecimalJSONResponse(empty_envelope(trade_date))
    payload, coverage, reason = _payload(snapshot)
    if payload is None:
        return _response(snapshot, None, coverage, reason=reason)
    matches = [
        article
        for article in payload["articles"]
        if article["article_id"] == str(article_id)
    ]
    if len(matches) != 1:
        raise NotFound("News article not found")
    return _response(snapshot, matches[0], coverage)


class _NewsReviewRoute(APIRoute):
    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await route_handler(request)
            except RequestValidationError:
                return error_response(400, "INVALID_PARAM", "Invalid request")
            except Forbidden:
                return error_response(403, "FORBIDDEN", "Source access denied")
            except NotFound:
                return error_response(404, "NOT_FOUND", "Resource not found")
            except Conflict:
                return error_response(409, "VERSION_CONFLICT", "Version conflict")
            except (SQLAlchemyError, StorageUnavailable):
                return error_response(503, "STORAGE_UNAVAILABLE", "Storage unavailable")
            except (BoundaryError, ValueError):
                return error_response(400, "INVALID_PARAM", "Invalid request")

        return handler


def install_news_review_routes(app: FastAPI) -> None:
    owner_router = APIRouter(route_class=_NewsReviewRoute)

    @owner_router.post("/api/news/{article_id}/associations/review")
    def review_association(article_id: UUID, request: Request, owner: OwnerWrite):
        if request.query_params:
            raise invalid_request()
        body = json_object(
            request,
            {
                "association_id",
                "decision",
                "expected_revision",
                "idempotency_key",
            },
        )
        try:
            association_id = UUID(body["association_id"])
            decision = body["decision"]
            expected_revision = body["expected_revision"]
            key = body["idempotency_key"]
        except (KeyError, TypeError, ValueError):
            raise invalid_request() from None
        if (
            decision not in {"accept", "reject"}
            or type(expected_revision) is not int
            or not isinstance(key, str)
        ):
            raise invalid_request()
        repository = request.app.state.news_repository
        if not isinstance(repository, NewsRepository):
            raise StorageUnavailable
        assert owner.csrf is not None
        outcome = repository.review(
            owner.token,
            owner.csrf,
            article_id=article_id,
            association_id=association_id,
            decision=decision,
            expected_revision=expected_revision,
            key=key,
        )
        return DecimalJSONResponse(
            outcome.model_dump(
                mode="json",
                exclude={"reviewed_at"},
            )
        )

    app.router.routes.extend(owner_router.routes)


__all__ = ["install_news_review_routes", "router"]
