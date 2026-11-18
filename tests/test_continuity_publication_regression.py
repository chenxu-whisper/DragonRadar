"""Distinguish valid continuity archives from stale summary-only fixtures."""

import json
from decimal import Decimal

import pytest
from test_reset_role_api import (
    _client,
    _update_domain_payload,
)
from test_reset_role_api import (
    role_publication as _role_publication,
)

from dragon_radar.publication.service import _stored_json
from dragon_radar.readmodels.market import materialize_market

pytestmark = pytest.mark.anyio
role_publication = _role_publication


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def read_continuity(publication, persisted):
    async with await _client(persisted) as client:
        return await client.get(
            "/api/limit-up/continuity",
            params={
                "date": str(publication.trade_date),
                "snapshot_id": str(publication.id),
            },
        )


async def test_current_materialization_survives_postgres_archive_round_trip(
    role_publication, database, monkeypatch
):
    publication, persisted, _repository = role_publication
    snapshot = persisted.get_snapshot(publication.id)
    actual = _stored_json(materialize_market(snapshot.batch, None))
    _update_domain_payload(
        database, publication, "market", lambda payload: payload.update(actual)
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("GET must not rematerialize publication inputs")

    monkeypatch.setattr("dragon_radar.readmodels.market.materialize_market", forbidden)
    response = await read_continuity(publication, persisted)
    assert response.status_code == 200, response.text
    body = json.loads(response.content, parse_float=Decimal)
    assert body["snapshot_id"] == str(publication.id)
    assert body["basis"]["price_type"] == "raw"
    assert body["data"]["details"]["status"] == "available"
    detail = body["data"]["details"]["data"]
    assert detail["promotion"]["coverage"]["expected"] == 1
    assert detail["feedback"]["coverage"]["expected"] == 1
    assert body["data"]["yesterday_limit_up_median_return"] == Decimal("0")


async def test_stale_summary_cannot_be_served_as_current_v1_detail(
    role_publication, database
):
    publication, persisted, _repository = role_publication
    snapshot = persisted.get_snapshot(publication.id)
    current_sentiment = _stored_json(materialize_market(snapshot.batch, None))[
        "overview"
    ]["sentiment"]

    def contradict_summary(payload):
        payload["overview"]["sentiment"].update(
            continuity_detail=current_sentiment["continuity_detail"],
            continuity_detail_version=current_sentiment["continuity_detail_version"],
        )

    _update_domain_payload(database, publication, "market", contradict_summary)
    sentiment = (
        persisted.get_snapshot(publication.id)
        .result("market")
        .payload["overview"]["sentiment"]
    )
    detail = sentiment["continuity_detail"]
    assert sentiment["promote_rate"]["yesterday_ge2"] == 2
    assert detail["promotion"]["coverage"]["expected"] == 1
    assert sentiment["yesterday_limit_up_return_coverage"]["expected"] == 2
    assert detail["feedback"]["coverage"]["expected"] == 1
    assert Decimal(sentiment["yesterday_limit_up_median_return"]) == Decimal("0.0120")
    assert Decimal(detail["feedback"]["members"][0]["return"]) == Decimal("0")
    response = await read_continuity(publication, persisted)
    assert response.status_code == 503
    assert response.json()["code"] == "DATA_UNAVAILABLE"


@pytest.mark.parametrize("missing_pool", [None, "promotion", "feedback"])
async def test_legacy_summary_preserves_decimal_and_independent_quality(
    role_publication, database, missing_pool
):
    publication, persisted, _repository = role_publication

    def legacy(payload):
        sentiment = payload["overview"]["sentiment"]
        sentiment.pop("continuity_detail", None)
        sentiment.pop("continuity_detail_version", None)
        if missing_pool == "promotion":
            sentiment["promote_rate"].update(
                denominator=0,
                value=None,
                missing=2,
                reason="promotion_observation_missing",
            )
        elif missing_pool == "feedback":
            sentiment["yesterday_limit_up_median_return"] = None
            sentiment["yesterday_limit_up_return_coverage"].update(valid=0, missing=2)
            sentiment["yesterday_limit_up_return_reason"] = (
                "no_valid_returns_for_yesterday_limit_up_members"
            )

    _update_domain_payload(database, publication, "market", legacy)
    response = await read_continuity(publication, persisted)
    assert response.status_code == 200, response.text
    body = json.loads(response.content, parse_float=Decimal)
    data = body["data"]
    assert body["basis"]["price_type"] == "raw"
    assert data["details"] == {
        "status": "unavailable",
        "version": None,
        "data": None,
        "reasons": ["continuity_detail_unavailable"],
    }
    assert data["promote_rate"]["rate"] == (
        None if missing_pool == "promotion" else Decimal("0.5000")
    )
    assert data["yesterday_limit_up_median_return"] == (
        None if missing_pool == "feedback" else Decimal("0.0120")
    )
    promotion = data["promote_rate_quality"]
    feedback = data["yesterday_limit_up_feedback"]
    assert promotion["reasons"] == (
        ["promotion_observation_missing"] if missing_pool == "promotion" else []
    )
    assert feedback["status"] == (
        "missing" if missing_pool == "feedback" else "available"
    )
    assert feedback["coverage"]["missing"] == (2 if missing_pool == "feedback" else 0)
    assert body["coverage"]["expected"] == sum(
        body["coverage"][key] for key in ("valid", "excluded", "missing")
    )
