"""HTTP integration tests against isolated real PostgreSQL schemas."""

from datetime import date, timedelta

import pytest
from factories import DAYS, NOW, bar, batch, state
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError


@pytest.fixture
def client(repository):
    from dragon_radar.api import create_app

    with TestClient(create_app(repository=repository)) as client:
        yield client


@pytest.fixture
def snapshot(repository):
    return repository.publish(repository.import_batch(batch(), key="api").id)


def test_health_live_no_config_or_connection_and_docs_disabled(monkeypatch):
    from dragon_radar.api import create_app

    monkeypatch.delenv("DRAGON_DATABASE_URL", raising=False)
    with TestClient(create_app()) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").status_code == 503
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/docs").status_code == 404


def test_health_schema_readiness(database_url):
    from dragon_radar.api import create_app
    from dragon_radar.storage.database import Database

    with TestClient(create_app(database_url)) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        db = Database(database_url)
        try:
            db.migrate()
        finally:
            db.close()
        assert client.get("/health/ready").status_code == 200


def test_snapshot_envelope_safe_metadata(client, snapshot):
    response = client.get(f"/api/v1/snapshots/{snapshot.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["snapshot_id"] == str(snapshot.id)
    assert data["trade_date"] == DAYS[-1].isoformat()
    assert data["as_of"] == NOW.isoformat().replace("+00:00", "Z")
    assert data["source_time"] is None
    assert data["source"] == {"id": "synthetic", "version": "s1"}
    assert data["coverage"]["valid"] == 1
    assert data["coverage_scope"] == "snapshot_current"
    assert data["units"] == {
        "currency": ["CNY"],
        "price_unit": ["currency/share"],
        "volume_unit": ["share"],
        "amount_unit": ["currency"],
    }
    assert data["quality_flags"] == []
    assert data["manifest"]["domain_version"] == "market-batch-v1"
    assert data["rule_version"] == "analytics-v1-decimal34-half-even"
    for forbidden in (
        "grant",
        "reference",
        "canonical_json",
        "security_states",
        "bars",
    ):
        assert forbidden not in response.text
    assert "synthetic-test-only" not in response.text


def test_list_only_published_requested_date_and_bounded_pagination(
    client, repository, snapshot
):
    repository.import_batch(batch(complete_pages=False), key="draft")
    assert client.get("/api/v1/snapshots?trade_date=2020-01-01").json() == []
    data = client.get("/api/v1/snapshots?limit=1").json()
    assert len(data) == 1
    assert data[0]["snapshot_id"] == str(snapshot.id)
    assert client.get("/api/v1/snapshots?offset=1").json() == []
    for query in ("limit=101", "limit=0", "offset=-1", "offset=10001"):
        assert client.get("/api/v1/snapshots?" + query).status_code == 422


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_api_has_no_mutations_even_with_forwarded_owner_headers(
    client, snapshot, method
):
    response = getattr(client, method)(
        f"/api/v1/snapshots/{snapshot.id}",
        headers={"Host": "localhost", "Forwarded": "for=127.0.0.1;host=localhost"},
    )
    assert response.status_code == 405


def test_overview_and_interval_use_domain_analytics(client, snapshot):
    base = f"/api/v1/snapshots/{snapshot.id}"
    overview = client.get(base + "/overview").json()
    assert overview["basis"] == "raw_daily_return"
    assert overview["result"]["unchanged"] == 1
    interval = client.get(base + "/interval?n=3&ids=A").json()
    assert interval["basis"] == "adjusted_close"
    assert interval["result"]["sessions"] == [day.isoformat() for day in DAYS]
    assert interval["result"]["items"][0]["interval_return"] == "0"
    assert interval["analysis_coverage"] == {
        "requested": 1,
        "eligible": 1,
        "excluded": 0,
    }
    custom = client.get(base + "/interval?start=2026-09-03&end=2026-09-08").json()
    assert custom["result"] == interval["result"]
    assert client.get(f"/api/v1/overview?snapshot_id={snapshot.id}").json() == overview
    assert client.get("/api/v1/overview?trade_date=2020-01-01").status_code == 404
    assert client.get("/api/v1/interval?n=3").json() == interval


def test_interval_default_twenty(repository, client):
    days = tuple(date(2026, 8, 1) + timedelta(days=i) for i in range(21))
    payload = batch(
        trade_date=days[-1],
        calendar={"exchange": "TEST", "version": "c1", "sessions": days},
        security_states=(state(effective_from=days[0]),),
        bars=tuple(bar(trade_date=day) for day in days),
    )
    snapshot = repository.publish(repository.import_batch(payload, key="20").id)
    response = client.get(f"/api/v1/snapshots/{snapshot.id}/interval")
    assert response.status_code == 200
    assert len(response.json()["result"]["sessions"]) == 21


@pytest.mark.parametrize(
    "query",
    [
        "n=true",
        "n=0",
        "n=251",
        "n=1.5",
        "n=2&start=2026-09-03&end=2026-09-08",
        "start=2026-09-03",
        "end=2026-09-08",
        "start=2026-09-08&end=2026-09-03",
        "start=2025-01-01&end=2026-09-08",
        "n=3&ids=A,B,C,D,E,F",
        "n=3&ids=A,A",
        "n=3&ids=",
        "n=2&n=3",
        "unknown=secret",
        "n=20",
    ],
)
def test_invalid_intervals_are_sanitized(client, snapshot, query):
    response = client.get(f"/api/v1/snapshots/{snapshot.id}/interval?{query}")
    assert response.status_code in (400, 422)
    assert "secret" not in response.text
    assert "traceback" not in response.text.lower()


def test_oversize_custom_interval_rejected(repository, client):
    days = tuple(date(2025, 1, 1) + timedelta(days=i) for i in range(252))
    snapshot = repository.publish(
        repository.import_batch(
            batch(
                trade_date=days[-1],
                calendar={"exchange": "TEST", "version": "c1", "sessions": days},
                security_states=(state(effective_from=days[0]),),
                bars=tuple(bar(trade_date=day) for day in days),
            ),
            key="long",
        ).id
    )
    response = client.get(
        f"/api/v1/snapshots/{snapshot.id}/interval?start={days[0]}&end={days[-1]}"
    )
    assert response.status_code == 400


def test_revocation_blocks_fixed_and_latest_reads(client, repository, snapshot):
    repository.revoke_source("synthetic")
    for suffix in ("", "/overview", "/interval?n=3"):
        assert client.get(f"/api/v1/snapshots/{snapshot.id}{suffix}").status_code in (
            403,
            404,
        )
    assert client.get("/api/v1/snapshots").json() == []
    assert client.get("/api/v1/overview").status_code == 404


def test_database_exception_never_leaks_credentials(client, repository, monkeypatch):
    def fail(**kwargs):
        raise OperationalError("password=super-secret", {}, Exception("private SQL"))

    monkeypatch.setattr(repository, "list_snapshots", fail)
    response = client.get("/api/v1/snapshots")
    assert response.status_code == 503
    assert response.json() == {"error": "Storage unavailable"}
    assert "secret" not in response.text


def test_invalid_ids_and_input_not_echoed(client):
    response = client.get("/api/v1/snapshots/private-secret")
    assert response.status_code == 422
    assert "private-secret" not in response.text


def test_custom_end_rolls_weekend_but_rejects_future_session(repository, client):
    payload = batch(
        trade_date=DAYS[1],
        bars=tuple(bar(trade_date=day) for day in DAYS[:2]),
    )
    snapshot = repository.publish(repository.import_batch(payload, key="friday").id)
    base = f"/api/v1/snapshots/{snapshot.id}/interval?start=2026-09-03&end="
    response = client.get(base + "2026-09-06")
    assert response.status_code == 200
    assert response.json()["result"]["sessions"] == ["2026-09-03", "2026-09-04"]
    assert client.get(base + "2026-09-07").status_code == 400


def test_interval_coverage_does_not_claim_current_coverage(repository, client):
    payload = batch(bars=(bar(),))
    snapshot = repository.publish(repository.import_batch(payload, key="gaps").id)
    response = client.get(f"/api/v1/snapshots/{snapshot.id}/interval?n=3")
    assert response.status_code == 200
    data = response.json()
    assert data["coverage"]["valid"] == 1
    assert data["coverage_scope"] == "snapshot_current"
    assert data["analysis_coverage"] == {"requested": 1, "eligible": 0, "excluded": 1}


@pytest.mark.parametrize("value", ["0", "1788825600", "2026-09-08T00:00:00"])
def test_date_query_requires_iso_date_not_timestamp(client, value):
    response = client.get("/api/v1/snapshots", params={"trade_date": value})
    assert response.status_code in (400, 422)
