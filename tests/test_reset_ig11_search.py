"""IG11 RED contracts. Synthetic frozen archives; no database or live inputs."""

import json
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from factories import NOW, batch, state
from starlette.requests import Request
from test_topic_details import _materialized, _selected
from topic_factories import topic_batch

from dragon_radar.http.errors import DataUnavailable
from dragon_radar.http.routes.market import search
from dragon_radar.http.routes.news import _payload as validated_news_payload
from dragon_radar.news.service import (
    NEWS_RULE_VERSION,
    NEWS_SCHEMA,
    NEWS_SCHEMA_VERSION,
)
from dragon_radar.publication.models import (
    Publication,
    PublicationResult,
    PublishedSnapshot,
)
from dragon_radar.readmodels.topic_details import parse_archived_topic_details
from dragon_radar.storage.repository import Forbidden


def _result_copy(result, **changes):
    return PublicationResult(**{**result.model_dump(mode="json"), **changes})


def _with_results(selected, results, **publication_changes):
    publication = Publication(
        **{**selected.publication.model_dump(), **publication_changes}
    )
    return PublishedSnapshot(publication, selected.market_snapshot, tuple(results))


def _with_market_payload(selected, change):
    payload = selected.result("market").model_dump(mode="json")["payload"]
    change(payload)
    return _with_results(
        selected,
        [
            _result_copy(result, payload=payload)
            if result.domain == "market"
            else result
            for result in selected.results
        ],
    )


@pytest.fixture
def archive():
    identities = ("A", *(f"S{index:03d}" for index in range(55)))
    market = batch(
        expected_security_ids=identities,
        security_states=tuple(
            state(identity, code=f"{index:06d}", name=f"Synthetic {identity}")
            for index, identity in enumerate(identities)
        ),
    )
    selected = _selected(*_materialized(market, topic_batch()))
    articles = [
        {
            "article_id": str(uuid4()),
            "revision_id": str(uuid4()),
            "revision": 1,
            "event_id": str(uuid4()),
            "type": "announcement",
            "source": {"id": "synthetic", "version": "news-v1"},
            "title": f"Synthetic archived article {index}",
            "url": f"https://example.test/ig11/{index}",
            "published_at": NOW.isoformat(),
            "received_at": NOW.isoformat(),
            "available_at": NOW.isoformat(),
            "content_scope": "summary",
            "summary": "Frozen licensed summary",
            "content": None,
            "ai_summary": None,
            "is_novel_event": True,
            "supporting_evidence": [],
            "opposing_evidence": [],
            "securities": [],
            "topics": [],
        }
        for index in range(2)
    ]
    news_id = uuid4()
    news = PublicationResult(
        publication_id=selected.id,
        domain="news",
        input_id=news_id,
        status="ready",
        payload={
            "schema": NEWS_SCHEMA,
            "schema_version": NEWS_SCHEMA_VERSION,
            "rule_version": NEWS_RULE_VERSION,
            "articles": articles,
            "sources": ["synthetic"],
            "event_count": 2,
        },
        coverage={"expected": 2, "valid": 2, "legal_excluded": 0, "missing": 0},
        quality_flags=(),
    )
    selected = _with_results(
        selected,
        [*selected.results, news],
        news_input_id=news_id,
        domains={**selected.publication.domains, "news": "ready"},
    )
    # Both independent validators must accept the fixture before testing search.
    payload = selected.result("market").model_dump(mode="json")["payload"]
    assert list(parse_archived_topic_details(payload, selected)) == ["topic-a"]
    assert validated_news_payload(selected)[0]["articles"] == articles
    return selected


class ArchiveRepository:
    def __init__(self, selected, *, denied=False):
        self.selected = selected
        self.denied = denied
        self.calls = []

    def get_snapshot(self, snapshot_id):
        self.calls.append(("get", snapshot_id))
        if self.denied:
            raise Forbidden("Original publication source access denied")
        return self.selected if snapshot_id == self.selected.id else None

    def list_snapshots(self, **kwargs):
        self.calls.append(("list", kwargs))
        if self.denied:
            raise Forbidden("Original publication source access denied")
        return [self.selected]

    def __getattr__(self, name):
        raise AssertionError(f"Search must not load current inputs: {name}")


def _request(repository, params):
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/search",
            "query_string": urlencode(params, doseq=True).encode(),
            "headers": [],
            "app": SimpleNamespace(state=SimpleNamespace(repository=repository)),
        }
    )


def _search(selected, *, q="Synthetic", historical=True, **params):
    repository = ArchiveRepository(selected)
    query = {"q": q, **params}
    if historical:
        query.update(date=str(selected.trade_date), snapshot_id=str(selected.id))
    try:
        response = search(_request(repository, query))
    except ValueError as error:
        pytest.fail(f"Valid IG11 search parameters were rejected: {error}")
    return json.loads(response.body), repository


def _sequence(data):
    return [
        (
            group,
            item[
                "security_id"
                if group == "securities"
                else "sector_id"
                if group == "topics"
                else "article_id"
            ],
        )
        for group in ("securities", "topics", "messages")
        for item in data["groups"][group]
    ]


def test_search_legacy_keys_preserve_original_security_identity(archive):
    body, repository = _search(archive, q="000000", historical=False)
    original = archive.result("market").payload["search"]["securities"][0]
    assert body["snapshot_id"] == str(archive.id)
    assert body["trade_date"] == str(archive.trade_date)
    assert body["data"]["groups"]["securities"] == [{**original, "match_field": "code"}]
    assert repository.calls == [("list", {"limit": 1})]


def test_search_three_groups_share_one_pub_and_real_total(archive):
    body, repository = _search(archive, size=100, page=1)
    data = body["data"]
    assert body["snapshot_id"] == str(archive.id)
    assert body["trade_date"] == str(archive.trade_date)
    assert data["query"] == "Synthetic"
    assert data["total"] == 59
    assert (data["page"], data["size"]) == (1, 100)
    assert data["group_status"] == {
        "securities": {"status": "ready", "total": 56, "reason": None},
        "topics": {"status": "ready", "total": 1, "reason": None},
        "messages": {"status": "ready", "total": 2, "reason": None},
    }
    assert data["groups"]["topics"] == [
        {"sector_id": "topic-a", "name": "Synthetic Topic A", "match_field": "name"}
    ]
    assert {item["article_id"] for item in data["groups"]["messages"]} == {
        item["article_id"] for item in archive.result("news").payload["articles"]
    }
    assert all("content" not in item for item in data["groups"]["messages"])
    assert repository.calls == [("get", archive.id)]


@pytest.mark.parametrize("size", [20, 50, 100])
def test_search_combined_sequence_paginates_once_not_once_per_group(archive, size):
    whole, _ = _search(archive, page=1, size=100)
    expected = _sequence(whole["data"])
    combined = []
    for page in range(1, (len(expected) + size - 1) // size + 1):
        body, _ = _search(archive, page=page, size=size)
        assert body["data"]["total"] == 59
        assert (body["data"]["page"], body["data"]["size"]) == (page, size)
        items = _sequence(body["data"])
        assert items == expected[(page - 1) * size : page * size]
        combined.extend(items)
    assert combined == expected
    assert len(set(combined)) == len(combined)
    assert expected[:56] == [
        ("securities", identity)
        for identity in ("A", *(f"S{index:03d}" for index in range(55)))
    ]
    assert expected[56] == ("topics", "topic-a")
    assert expected[57:] == sorted(expected[57:])


def test_search_default_page_is_bounded_and_out_of_range_keeps_total(archive):
    first, _ = _search(archive)
    assert (first["data"]["page"], first["data"]["size"]) == (1, 50)
    assert len(_sequence(first["data"])) == 50
    last, _ = _search(archive, page=100000, size=20)
    assert last["data"]["total"] == 59
    assert _sequence(last["data"]) == []


def test_search_no_match_is_ready_zero_not_unavailable(archive):
    body, _ = _search(archive, q="no-synthetic-match")
    assert body["data"]["total"] == 0
    assert body["data"]["groups"] == {
        "securities": [],
        "topics": [],
        "messages": [],
    }
    assert body["data"]["group_status"] == {
        group: {"status": "ready", "total": 0, "reason": None}
        for group in ("securities", "topics", "messages")
    }


def _without_market_search(archive, missing):
    if missing in {"legacy", "empty", "search", "securities"}:

        def change(payload):
            if missing == "legacy":
                payload.clear()
                payload.update(
                    advancing=0, declining=0, unchanged=0, returns=[], exclusions=[]
                )
            elif missing == "empty":
                payload.clear()
            else:
                payload.pop("topic_details")
                if missing == "search":
                    payload.pop("search")
                else:
                    payload["search"].pop("securities")

        return _with_market_payload(archive, change)
    return _with_results(
        archive,
        [
            _result_copy(item, status="missing", payload={})
            if item.domain == "market"
            else item
            for item in archive.results
            if missing != "result" or item.domain != "market"
        ],
    )


@pytest.mark.parametrize(
    "missing", ["legacy", "empty", "search", "securities", "not_ready", "result"]
)
@pytest.mark.parametrize("historical", [False, True])
def test_search_missing_securities_still_searches_ready_archived_news(
    archive, missing, historical
):
    selected = _without_market_search(archive, missing)
    body, repository = _search(selected, historical=historical, size=20)
    assert body["snapshot_id"] == str(archive.id)
    assert body["trade_date"] == str(archive.trade_date)
    data = body["data"]
    assert data is not None
    assert data["total"] == 2
    assert (data["page"], data["size"]) == (1, 20)
    assert data["groups"]["securities"] == data["groups"]["topics"] == []
    assert data["group_status"] == {
        **{
            group: {
                "status": "unavailable",
                "total": None,
                "reason": f"{group}_materialization_unavailable",
            }
            for group in ("securities", "topics")
        },
        "messages": {"status": "ready", "total": 2, "reason": None},
    }
    expected_articles = sorted(
        archive.result("news").payload["articles"], key=lambda item: item["article_id"]
    )
    assert [
        (item["article_id"], item["title"], item["match_field"])
        for item in data["groups"]["messages"]
    ] == [(item["article_id"], item["title"], "title") for item in expected_articles]
    assert repository.calls == (
        [("get", archive.id)] if historical else [("list", {"limit": 1})]
    )


@pytest.mark.parametrize(
    "q,page,total,count",
    [("Synthetic", 2, 2, 0), ("no-synthetic-match", 1, 0, 0)],
)
def test_search_news_only_keeps_ready_totals_without_market_matches(
    archive, q, page, total, count
):
    body, _ = _search(_without_market_search(archive, "legacy"), q=q, page=page)
    data = body["data"]
    assert data["total"] == total
    assert len(data["groups"]["messages"]) == count
    assert data["group_status"]["messages"] == {
        "status": "ready",
        "total": total,
        "reason": None,
    }
    assert data["group_status"]["securities"]["total"] is None


@pytest.mark.parametrize("historical", [False, True])
def test_search_stock_archive_does_not_require_other_market_views(archive, historical):
    def change(payload):
        for field in (
            "overview",
            "sector_flow",
            "sentiment",
            "heatmap",
            "topic_details",
        ):
            payload.pop(field)

    selected = _with_market_payload(archive, change)
    body, _ = _search(selected, q="000000", historical=historical)
    original = archive.result("market").payload["search"]["securities"][0]
    assert body["data"]["groups"]["securities"] == [{**original, "match_field": "code"}]
    assert body["data"]["group_status"]["topics"]["status"] == "unavailable"


@pytest.mark.parametrize(
    "change",
    [
        "search_null",
        "securities_null",
        "securities_shape",
        "duplicate",
        "schema",
        "version",
        "market_publication",
        "market_input",
        "market_duplicate",
    ],
)
def test_search_corrupt_present_market_archive_fails_whole_news_query(archive, change):
    def corrupt(payload):
        payload.pop("topic_details")
        if change == "search_null":
            payload["search"] = None
        elif change == "securities_null":
            payload["search"]["securities"] = None
        elif change == "securities_shape":
            del payload["search"]["securities"][0]["exchange"]
        elif change == "duplicate":
            payload["search"]["securities"].append(payload["search"]["securities"][0])
        elif change == "schema":
            payload["schema"] = "wrong"
        elif change == "version":
            payload["schema_version"] = 999

    selected = _with_market_payload(archive, corrupt)
    if change in {"market_publication", "market_input"}:
        changes = {
            "publication_id" if change == "market_publication" else "input_id": uuid4()
        }
        selected = _with_results(
            selected,
            [
                _result_copy(item, **changes) if item.domain == "market" else item
                for item in selected.results
            ],
        )
    elif change == "market_duplicate":
        selected = _with_results(
            selected, [*selected.results, selected.result("market")]
        )
    with pytest.raises(DataUnavailable):
        search(_request(ArchiveRepository(selected), {"q": "archived article"}))


def test_search_missing_securities_still_requires_exact_pub_date(archive):
    selected = _without_market_search(archive, "legacy")
    repository = ArchiveRepository(selected)
    with pytest.raises(ValueError, match="Snapshot date mismatch"):
        search(
            _request(
                repository,
                {
                    "q": "Synthetic",
                    "date": "2026-09-07",
                    "snapshot_id": str(selected.id),
                },
            )
        )
    assert repository.calls == [("get", selected.id)]


@pytest.mark.parametrize("missing", ["topic_details", "topic_metadata", "news_result"])
def test_search_missing_group_materialization_is_explicit_not_false_empty(
    archive, missing
):
    selected = archive
    group = "messages" if missing == "news_result" else "topics"
    if missing == "topic_details":
        selected = _with_market_payload(
            selected, lambda payload: payload.pop("topic_details")
        )
    else:
        domain = "news" if missing == "news_result" else "topic"
        selected = _with_results(
            selected, [item for item in selected.results if item.domain != domain]
        )
    body, _ = _search(selected)
    unavailable = body["data"]["group_status"][group]
    assert unavailable["status"] == "unavailable"
    assert unavailable["total"] is None
    assert isinstance(unavailable["reason"], str) and unavailable["reason"]
    assert body["data"]["groups"][group] == []
    assert body["data"]["total"] == (57 if group == "messages" else 58)


@pytest.mark.parametrize(
    "change",
    ["date", "topic_id", "market_id", "taxonomy", "member_name", "null", "extra"],
)
def test_search_corrupt_topic_block_fails_closed_even_for_security_only_query(
    archive, change
):
    def corrupt(payload):
        detail = payload["topic_details"]["sectors"]["topic-a"]
        if change == "date":
            payload["topic_details"]["trade_date"] = "2026-09-07"
        elif change in {"topic_id", "market_id"}:
            field = change.replace("_id", "_snapshot_id")
            detail["topic_basis"][field] = str(uuid4())
        elif change == "taxonomy":
            detail["topic_basis"]["taxonomy_version"] = "invented"
        elif change == "member_name":
            detail["members"][0]["name"] = "Invented identity"
        elif change == "null":
            payload["topic_details"] = None
        else:
            detail["untrusted"] = True

    selected = _with_market_payload(archive, corrupt)
    with pytest.raises(DataUnavailable):
        search(_request(ArchiveRepository(selected), {"q": "000000"}))


def test_search_topic_metadata_is_validated_independently_not_derived_from_block(
    archive,
):
    topic = archive.result("topic")
    payload = topic.model_dump(mode="json")["payload"]
    payload["membership_version"] = "independently-wrong"
    selected = _with_results(
        archive,
        [
            _result_copy(item, payload=payload) if item.domain == "topic" else item
            for item in archive.results
        ],
    )
    with pytest.raises(DataUnavailable):
        search(_request(ArchiveRepository(selected), {"q": "Synthetic"}))


@pytest.mark.parametrize("change", ["schema", "article_shape", "publication", "input"])
@pytest.mark.parametrize("missing_market", [False, True])
def test_search_corrupt_news_fails_closed_before_returning_any_group(
    archive, change, missing_market
):
    if missing_market:
        archive = _without_market_search(archive, "legacy")
    news = archive.result("news")
    payload = news.model_dump(mode="json")["payload"]
    changes = {"payload": deepcopy(payload)}
    if change == "schema":
        changes["payload"]["schema"] = "wrong"
    elif change == "article_shape":
        del changes["payload"]["articles"][0]["revision_id"]
    elif change == "publication":
        changes["publication_id"] = uuid4()
    else:
        changes["input_id"] = uuid4()
    selected = _with_results(
        archive,
        [
            _result_copy(item, **changes) if item.domain == "news" else item
            for item in archive.results
        ],
    )
    with pytest.raises(DataUnavailable):
        search(_request(ArchiveRepository(selected), {"q": "000000"}))


@pytest.mark.parametrize("name", [None, "", "   "])
def test_search_preserves_empty_security_names_without_current_lookup(archive, name):
    def change(payload):
        payload.pop("topic_details")
        payload["search"]["securities"][0]["name"] = name

    body, _ = _search(_with_market_payload(archive, change), q="000000")
    assert body["data"]["groups"]["securities"][0]["name"] == name
    assert body["data"]["groups"]["securities"][0]["security_id"] == "A"


@pytest.mark.parametrize(
    "params",
    [
        {"page": "0"},
        {"page": "1.5"},
        {"page": "100001"},
        {"size": "0"},
        {"size": "101"},
        {"size": "21"},
        {"page": ["1", "2"]},
        {"q": ["A", "B"]},
        {"unknown": "1"},
    ],
)
def test_search_invalid_page_query_rejected_before_original_pub_access(archive, params):
    repository = ArchiveRepository(archive)
    with pytest.raises(ValueError):
        search(_request(repository, {"q": "Synthetic", **params}))
    assert repository.calls == []


@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize("missing", ["topic_details", "legacy", "result"])
def test_search_cannot_bypass_original_pub_authorization_when_groups_unavailable(
    archive, historical, missing
):
    selected = (
        _with_market_payload(archive, lambda payload: payload.pop("topic_details"))
        if missing == "topic_details"
        else _without_market_search(archive, missing)
    )
    repository = ArchiveRepository(selected, denied=True)
    params = {"q": "Synthetic"}
    if historical:
        params.update(snapshot_id=str(selected.id), date=str(selected.trade_date))
    with pytest.raises(Forbidden, match="Original publication"):
        search(_request(repository, params))
    assert len(repository.calls) == 1
