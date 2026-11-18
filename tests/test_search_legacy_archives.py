"""Exact Task09 publication shapes, without database access or recomputation."""

import json
from uuid import uuid4

import pytest
from factories import batch, state
from test_reset_ig11_search import ArchiveRepository, _request, _with_results
from test_topic_details import _materialized, _selected

from dragon_radar.http.errors import DataUnavailable
from dragon_radar.http.routes.market import search
from dragon_radar.http.serialization import restore_decimal_fields
from dragon_radar.publication.models import (
    DOMAIN_NAMES,
    DomainMaterialization,
    PublicationResult,
)
from dragon_radar.readmodels.search import archived_search


@pytest.fixture
def task09_archive():
    market = batch(
        security_states=(state(code="000001", name="Synthetic Alpha"),),
    )
    selected = _selected(*_materialized(market, None))
    # PublicationService persists a result for every domain, including disabled
    # domains with no input, payload, or coverage. Round-trip the JSONB shape.
    disabled = DomainMaterialization(status="disabled").model_dump(mode="json")
    results = [
        selected.result("market"),
        *(
            PublicationResult.model_validate(
                {
                    "publication_id": str(selected.id),
                    "domain": domain,
                    "input_id": None,
                    **disabled,
                }
            )
            for domain in DOMAIN_NAMES
            if domain != "market"
        ),
    ]
    return _with_results(
        selected,
        results,
        topic_snapshot_id=None,
        domains={
            domain: item.status
            for domain, item in zip(DOMAIN_NAMES, results, strict=True)
        },
    )


def _news_state(selected, status):
    return _with_results(
        selected,
        [
            PublicationResult(**{**item.model_dump(), "status": status})
            if item.domain == "news"
            else item
            for item in selected.results
        ],
        domains={**selected.publication.domains, "news": status},
    )


def _legacy(selected):
    payload = {
        "advancing": 1,
        "declining": 1,
        "unchanged": 1,
        "returns": [
            {
                "security_id": "A",
                "value": "0.1000",
                "basis": "explicit_previous_close",
                "warnings": [],
            }
        ],
        "exclusions": [],
    }
    return _with_results(
        selected,
        [
            PublicationResult(**{**item.model_dump(), "payload": payload})
            if item.domain == "market"
            else item
            for item in selected.results
        ],
    )


def _unavailable(group):
    return {
        "status": "unavailable",
        "total": None,
        "reason": f"{group}_materialization_unavailable",
    }


@pytest.mark.parametrize("status", ["disabled", "missing"])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("entry", ["helper", "latest", "historical"])
@pytest.mark.parametrize("term,match_field", [("000001", "code"), ("Alpha", "name")])
def test_search_nonready_news_with_empty_coverage(
    task09_archive, monkeypatch, status, legacy, entry, term, match_field
):
    selected = _news_state(task09_archive, status)
    if legacy:
        selected = _legacy(selected)
    assert dict(selected.result("news").coverage) == {}
    assert dict(selected.result("news").payload) == {}

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Search must not recompute or load current identities")

    monkeypatch.setattr("dragon_radar.readmodels.market.materialize_market", forbidden)
    monkeypatch.setattr("dragon_radar.http.routes.market.market_breadth", forbidden)
    monkeypatch.setattr("dragon_radar.readmodels.market.state_at", forbidden)
    before = [item.model_dump(mode="json") for item in selected.results]
    if entry == "helper":
        data = archived_search(
            restore_decimal_fields(selected.result("market").payload),
            selected,
            term,
            1,
            50,
        )
    else:
        repository = ArchiveRepository(selected)
        params = {"q": f" {term} "}
        if entry == "historical":
            params.update(date=str(selected.trade_date), snapshot_id=str(selected.id))
        response = search(_request(repository, params))
        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["snapshot_id"] == str(selected.id)
        assert body["trade_date"] == str(selected.trade_date)
        data = body["data"]
        assert repository.calls == (
            [("get", selected.id)]
            if entry == "historical"
            else [("list", {"limit": 1})]
        )
    assert data == {
        "query": term,
        "groups": {
            "securities": []
            if legacy
            else [
                {
                    "security_id": "A",
                    "code": "000001",
                    "name": "Synthetic Alpha",
                    "exchange": "TEST",
                    "match_field": match_field,
                }
            ],
            "topics": [],
            "messages": [],
        },
        "group_status": {
            "securities": _unavailable("securities")
            if legacy
            else {"status": "ready", "total": 1, "reason": None},
            "topics": _unavailable("topics"),
            "messages": _unavailable("messages"),
        },
        "total": 0 if legacy else 1,
        "page": 1,
        "size": 50,
    }
    assert [item.model_dump(mode="json") for item in selected.results] == before


@pytest.mark.parametrize("status", ["disabled", "missing"])
@pytest.mark.parametrize("change", ["publication", "input", "duplicate"])
def test_search_nonready_news_still_fails_closed_on_binding(
    task09_archive, status, change
):
    selected = _news_state(task09_archive, status)
    results = list(selected.results)
    news = selected.result("news")
    if change == "duplicate":
        results.append(news)
    else:
        field = "publication_id" if change == "publication" else "input_id"
        results = [
            PublicationResult(**{**item.model_dump(), field: uuid4()})
            if item.domain == "news"
            else item
            for item in results
        ]
    selected = _with_results(selected, results)
    with pytest.raises(DataUnavailable):
        search(_request(ArchiveRepository(selected), {"q": "000001"}))


def test_search_ready_news_empty_archive_is_not_treated_as_unavailable(task09_archive):
    selected = _news_state(task09_archive, "ready")
    with pytest.raises(DataUnavailable):
        search(_request(ArchiveRepository(selected), {"q": "000001"}))
