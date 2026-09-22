"""Workbench HTTP contracts, with offline checks and real source authorization."""

import json
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import uuid4

import pytest
from factories import DAYS, NOW, bar, batch, grant, state
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError

from dragon_radar.storage.models import Snapshot
from dragon_radar.storage.repository import Conflict, Forbidden, NotFound

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeDatabase:
    def __init__(self):
        self.closed = False

    def ready(self):
        return True

    def close(self):
        self.closed = True


class EmptyRepository:
    """No DB needed to exercise request parsing, empty states and lifecycle."""

    def __init__(self):
        self.database = FakeDatabase()
        self.calls = []

    def list_snapshots(self, **kwargs):
        self.calls.append(kwargs)
        return []

    def get_snapshot(self, snapshot_id):
        self.calls.append(snapshot_id)
        raise NotFound


@asynccontextmanager
async def workbench(repository=None, **kwargs):
    from dragon_radar.http.app import create_workbench_app

    app = create_workbench_app(repository=repository, **kwargs)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        yield client


@pytest.fixture
async def empty_client():
    async with workbench(EmptyRepository()) as client:
        yield client


@pytest.fixture
async def client(repository):
    async with workbench(repository) as client:
        yield client


@pytest.fixture
def snapshot(repository):
    return repository.publish(repository.import_batch(batch(), key="reset-api").id)


def assert_error(response, status, code, message):
    assert response.status_code == status
    assert response.json() == {"code": code, "message": message}


def assert_metadata(payload, snapshot):
    assert payload["snapshot_id"] == str(snapshot.id)
    assert payload["trade_date"] == snapshot.trade_date.isoformat()
    assert payload["as_of"].replace("Z", "+00:00") == NOW.isoformat()
    assert payload["source"] == snapshot.batch.source_id
    assert payload["source_version"] == snapshot.batch.source_version
    assert payload["rule_version"] == snapshot.manifest["rule_version"]
    assert {"unit", "currency", "price_type", "benchmark"} <= payload["basis"].keys()
    assert "result" not in payload
    assert "legal_excluded" not in json.dumps(payload)
    assert "grant" not in json.dumps(payload)
    assert "synthetic-test-only" not in json.dumps(payload)


@pytest.mark.parametrize("path", ["/api/market/overview", "/api/stock/A"])
async def test_absent_date_is_no_data_without_fabricated_metadata(empty_client, path):
    response = await empty_client.get(path, params={"date": "2020-01-01"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] is None
    assert payload["trade_date"] == "2020-01-01"
    assert payload["quality_flags"] == ["no_data"]
    for name in (
        "snapshot_id",
        "as_of",
        "source",
        "source_version",
        "rule_version",
        "coverage",
    ):
        assert payload[name] is None
    assert {"unit", "currency", "price_type", "benchmark"} <= payload["basis"].keys()


async def test_empty_list_is_discovery_with_collection_metadata(empty_client):
    response = await empty_client.get("/api/snapshots/list")
    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] == {"snapshots": []}
    assert payload["snapshot_id"] is None
    assert payload["coverage"] is None
    assert payload["source"] == "snapshot_store"
    assert payload["quality_flags"] == ["no_data"]
    assert {
        "trade_date",
        "as_of",
        "source_version",
        "rule_version",
        "basis",
    } <= payload.keys()


async def test_snapshot_summary_is_read_directly_by_publication_id():
    selected_batch = batch()
    selected_batch = selected_batch.model_copy(
        update={
            "calendar": selected_batch.calendar.model_copy(
                update={"sessions": (*DAYS, NOW.date())}
            )
        }
    )
    selected = Snapshot(
        id=uuid4(),
        batch_id=uuid4(),
        revision=7,
        supersedes=None,
        published_at=NOW,
        manifest={"rule_version": "summary-v1"},
        batch=selected_batch,
        quality_report={
            "issues": [],
            "coverage": {
                "expected": 1,
                "valid": 1,
                "legal_excluded": 0,
                "missing": 0,
            },
        },
    )

    class SummaryRepository(EmptyRepository):
        def get_snapshot(self, snapshot_id):
            self.calls.append(snapshot_id)
            return selected if snapshot_id == selected.id else None

    repository = SummaryRepository()
    async with workbench(repository) as client:
        response = await client.get(f"/api/snapshots/{selected.id}")

    assert response.status_code == 200
    assert response.json()["snapshot_id"] == str(selected.id)
    assert response.json()["data"] == {
        "revision": 7,
        "supersedes": None,
        "published_at": NOW.isoformat(),
        "security_count": 1,
        "calendar_sessions": [day.isoformat() for day in DAYS],
        "earliest_date": DAYS[0].isoformat(),
    }
    assert repository.calls == [selected.id]


async def test_snapshot_summary_reports_null_earliest_date_without_prior_session():
    from dragon_radar.http.contracts import snapshot_summary

    source = batch()
    source = source.model_copy(update={"trade_date": DAYS[0].replace(year=2020)})
    selected = Snapshot(
        id=uuid4(),
        batch_id=uuid4(),
        revision=1,
        supersedes=None,
        published_at=NOW,
        manifest={"rule_version": "summary-v1"},
        batch=source,
        quality_report={
            "issues": [],
            "coverage": {
                "expected": 1,
                "valid": 1,
                "legal_excluded": 0,
                "missing": 0,
            },
        },
    )

    data = snapshot_summary(selected)["data"]

    assert data["calendar_sessions"] == []
    assert data["earliest_date"] is None


@pytest.mark.parametrize("path", ["/api/market/overview", "/api/stock/A"])
@pytest.mark.parametrize(
    "query",
    [
        "",
        "?date=",
        "?date=2026-9-8",
        "?date=2026-02-30",
        "?date=1788825600",
        "?date=2026-09-08T00:00:00",
        "?date=2026-09-08&date=2026-09-08",
        "?date=2026-09-08&unknown=private-secret",
        "?snapshot_id=private-secret",
        "?snapshot_id=",
        f"?snapshot_id={uuid4()}&snapshot_id={uuid4()}",
        "?trade_date=2026-09-08",
    ],
)
async def test_bad_selectors_fail_before_storage(path, query):
    repo = EmptyRepository()
    async with workbench(repo) as client:
        response = await client.get(path + query)
    assert_error(response, 400, "INVALID_PARAM", "Invalid request")
    assert repo.calls == []


@pytest.mark.parametrize(
    "query",
    [
        "date=not-a-date",
        "limit=0",
        "limit=101",
        "limit=true",
        "limit=1.5",
        "offset=-1",
        "offset=10001",
        "offset=1&offset=1",
        "unknown=secret",
        f"snapshot_id={uuid4()}",
    ],
)
async def test_list_rejects_invalid_query(empty_client, query):
    response = await empty_client.get("/api/snapshots/list?" + query)
    assert_error(response, 400, "INVALID_PARAM", "Invalid request")


@pytest.mark.parametrize("path", ["/api/market/overview", "/api/stock/A"])
async def test_explicit_nonexistent_snapshot_is_not_no_data(empty_client, path):
    response = await empty_client.get(path, params={"snapshot_id": str(uuid4())})
    assert_error(response, 404, "NOT_FOUND", "Resource not found")


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "path", ["/api/market/overview", "/api/owner/pair", "/missing", "/health/live"]
)
async def test_readonly_rejects_writes_even_with_owner_like_headers(
    empty_client, method, path
):
    response = await empty_client.request(
        method,
        path,
        headers={
            "Host": "localhost",
            "Forwarded": "for=127.0.0.1;host=localhost",
            "Cookie": "owner_session=private-secret",
        },
    )
    assert_error(response, 403, "FORBIDDEN", "Read-only API")


async def test_liveness_unconfigured_readiness_and_no_private_routes(monkeypatch):
    monkeypatch.delenv("DRAGON_DATABASE_URL", raising=False)
    async with workbench() as client:
        assert (await client.get("/health/live")).json() == {"status": "ok"}
        for path in ("/health/ready", "/api/market/overview?date=2026-09-08"):
            assert_error(
                await client.get(path),
                503,
                "STORAGE_UNAVAILABLE",
                "Storage unavailable",
            )
        for path in (
            "/docs",
            "/openapi.json",
            "/api/owner/session",
            "/api/notes",
            "/api/v1/snapshots",
        ):
            assert_error(await client.get(path), 404, "NOT_FOUND", "Resource not found")


async def test_injected_repository_is_not_closed_or_replaced_by_environment(
    monkeypatch,
):
    monkeypatch.setenv("DRAGON_DATABASE_URL", "invalid://private-secret")
    repo = EmptyRepository()
    async with workbench(repo, database_url="also-invalid://secret") as client:
        assert (await client.get("/health/ready")).status_code == 200
    assert not repo.database.closed


async def test_owned_database_is_closed(monkeypatch):
    from dragon_radar.http import app

    database = FakeDatabase()
    urls = []

    def construct(value):
        urls.append(value)
        return database

    monkeypatch.setattr(app, "Database", construct)
    monkeypatch.setenv("DRAGON_DATABASE_URL", "environment-config")
    async with workbench(database_url="explicit-config") as client:
        assert (await client.get("/health/ready")).status_code == 200
    assert urls == ["explicit-config"]
    assert database.closed


@pytest.mark.parametrize(
    ("exception", "status", "code", "message"),
    [
        (Forbidden("secret"), 403, "FORBIDDEN", "Source access denied"),
        (NotFound("secret"), 404, "NOT_FOUND", "Resource not found"),
        (Conflict("secret"), 409, "VERSION_CONFLICT", "Version conflict"),
        (
            OperationalError("password=secret", {}, Exception("private SQL")),
            503,
            "STORAGE_UNAVAILABLE",
            "Storage unavailable",
        ),
    ],
)
async def test_repository_errors_are_fixed_and_sanitized(
    monkeypatch, exception, status, code, message
):
    repo = EmptyRepository()

    def fail(**kwargs):
        raise exception

    monkeypatch.setattr(repo, "list_snapshots", fail)
    async with workbench(repo) as client:
        assert_error(await client.get("/api/snapshots/list"), status, code, message)


@pytest.mark.parametrize("rate_limited", [False, True])
async def test_upstream_contract_helpers(monkeypatch, rate_limited):
    from dragon_radar.http.errors import UpstreamError

    repo = EmptyRepository()

    def fail(**kwargs):
        raise UpstreamError(rate_limited=rate_limited)

    monkeypatch.setattr(repo, "list_snapshots", fail)
    async with workbench(repo) as client:
        assert_error(
            await client.get("/api/snapshots/list"),
            429 if rate_limited else 502,
            "RATE_LIMITED" if rate_limited else "UPSTREAM_ERROR",
            "Too many requests" if rate_limited else "Upstream unavailable",
        )


@pytest.mark.parametrize(
    "counts",
    [(3, 1, 1, 0), (1, True, 0, 0), (1, 1.0, 0, 0), (-1, 0, 0, -1), (3, 1, 0, None)],
)
async def test_coverage_rejects_invalid_partitions(counts):
    from dragon_radar.http.contracts import coverage_view

    with pytest.raises(ValueError):
        coverage_view(*counts)


async def test_coverage_preserves_missing_and_legal_exclusions():
    from dragon_radar.http.contracts import coverage_view

    assert coverage_view(7, 3, 2, 2) == {
        "expected": 7,
        "valid": 3,
        "excluded": 2,
        "missing": 2,
    }


async def test_decimal_serialization_is_numeric_and_lossless():
    from dragon_radar.http.serialization import DecimalJSONResponse

    value = Decimal("12345678901234567890.12345678901234567890123456789")
    response = DecimalJSONResponse(
        {
            "nested": [value, Decimal("0.0000000000000000000000000000000001")],
            "id": uuid4(),
            "date": DAYS[-1],
            "at": NOW,
        }
    )
    assert response.media_type == "application/json"
    parsed = json.loads(response.body, parse_float=Decimal)
    assert parsed["nested"][0] == value
    assert str(value).encode() in response.body


@pytest.mark.parametrize(
    "value",
    [
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        float("nan"),
        float("inf"),
    ],
)
async def test_nonfinite_numbers_are_never_json_tokens(value):
    from dragon_radar.http.errors import DataUnavailable
    from dragon_radar.http.serialization import DecimalJSONResponse

    with pytest.raises(DataUnavailable):
        DecimalJSONResponse({"nested": [{"value": value}]})


async def test_nonfinite_response_has_sanitized_unavailable_contract(monkeypatch):
    from dragon_radar.http.app import create_workbench_app
    from dragon_radar.http.serialization import DecimalJSONResponse

    app = create_workbench_app(repository=EmptyRepository())

    @app.get("/test/nonfinite")
    def nonfinite():
        return DecimalJSONResponse({"value": Decimal("NaN")})

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        assert_error(
            await client.get("/test/nonfinite"),
            503,
            "DATA_UNAVAILABLE",
            "Data unavailable",
        )


async def test_overview_and_stock_map_existing_projections(client, snapshot):
    for path in ("/api/market/overview", "/api/stock/A"):
        response = await client.get(
            path, params={"date": str(DAYS[-1]), "snapshot_id": str(snapshot.id)}
        )
        assert response.status_code == 200
        payload = response.json()
        assert_metadata(payload, snapshot)
        assert payload["coverage"] == {
            "expected": 1,
            "valid": 1,
            "excluded": 0,
            "missing": 0,
        }
        assert payload["basis"]["currency"] == "CNY"
        assert payload["basis"]["price_type"] == "raw"
        assert payload["basis"]["benchmark"] is None
        if path.endswith("overview"):
            assert payload["data"]["unchanged"] == 1
            assert payload["basis"]["unit"] == "ratio"
        else:
            assert payload["data"]["security"]["close"] == 10
            assert payload["data"]["history"][-1]["trade_date"] == str(DAYS[-1])


async def test_date_mismatch_and_unknown_stock(client, snapshot):
    for path in ("/api/market/overview", "/api/stock/A"):
        assert_error(
            await client.get(
                path, params={"date": "2020-01-01", "snapshot_id": str(snapshot.id)}
            ),
            400,
            "INVALID_PARAM",
            "Invalid request",
        )
    assert_error(
        await client.get(
            "/api/stock/unknown", params={"snapshot_id": str(snapshot.id)}
        ),
        404,
        "NOT_FOUND",
        "Resource not found",
    )


async def test_pinned_version_never_drifts_and_discovery_is_paginated(
    repository, client, snapshot
):
    second = repository.publish(
        repository.import_batch(batch(source_time=NOW), key="revision").id
    )
    params = {"snapshot_id": str(snapshot.id)}
    pinned = (await client.get("/api/market/overview", params=params)).json()
    assert_metadata(pinned, snapshot)
    latest = (
        await client.get("/api/market/overview", params={"date": str(DAYS[-1])})
    ).json()
    assert_metadata(latest, second)
    result = (await client.get("/api/snapshots/list?limit=1&offset=1")).json()
    assert len(result["data"]["snapshots"]) == 1
    assert_metadata(result["data"]["snapshots"][0], snapshot)
    assert result["data"]["snapshots"][0]["data"]["calendar_sessions"] == [
        day.isoformat() for day in DAYS
    ]
    assert (
        result["data"]["snapshots"][0]["data"]["earliest_date"] == DAYS[0].isoformat()
    )
    absent = (await client.get("/api/snapshots/list?date=2020-01-01")).json()
    assert absent["data"] == {"snapshots": []}


async def test_real_authorization_revocation_and_expiry_block_reads(
    repository, client, snapshot
):
    # Fixtures only grant local display/storage, not sharing/public permission.
    assert snapshot.batch.grant.sharing is False
    assert (
        await client.get("/api/stock/A", params={"snapshot_id": str(snapshot.id)})
    ).status_code == 200
    for denied in (grant(expires_at=NOW), grant(approved=False)):
        repository.register_source(denied)
        for path in ("/api/market/overview", "/api/stock/A"):
            assert_error(
                await client.get(path, params={"snapshot_id": str(snapshot.id)}),
                403,
                "FORBIDDEN",
                "Source access denied",
            )
            payload = (await client.get(path, params={"date": str(DAYS[-1])})).json()
            assert payload["data"] is None
            assert payload["snapshot_id"] is None
        assert (await client.get("/api/snapshots/list")).json()["data"] == {
            "snapshots": []
        }


async def test_breadth_missing_history_does_not_shrink_expected(repository, client):
    payload = batch(
        expected_security_ids=("A", "B"),
        security_states=(state(), state("B", suspended=True)),
        bars=(bar(),),
    )
    snapshot = repository.publish(
        repository.import_batch(payload, key="missing-history").id
    )
    result = (
        await client.get(
            "/api/market/overview", params={"snapshot_id": str(snapshot.id)}
        )
    ).json()
    assert result["coverage"] == {
        "expected": 2,
        "valid": 0,
        "excluded": 1,
        "missing": 1,
    }
    assert result["data"]["coverage"] == result["coverage"]
    assert result["data"]["exclusions"][0]["reasons"] == ["missing_price"]


async def test_real_stock_decimal_keeps_source_precision(repository, client):
    value = Decimal("12345678901234567890.1234567890123456789")
    payload = batch(bars=tuple(bar(trade_date=day, close=value) for day in DAYS))
    snapshot = repository.publish(repository.import_batch(payload, key="precision").id)
    response = await client.get(
        "/api/stock/A", params={"snapshot_id": str(snapshot.id)}
    )
    assert response.status_code == 200
    assert response.json()["data"]["security"]["close"] != str(value)
    assert (
        json.loads(response.text, parse_float=Decimal)["data"]["security"]["close"]
        == value
    )


async def test_bad_archived_coverage_is_unavailable_not_repaired(monkeypatch):
    repo = EmptyRepository()
    snapshot = Snapshot(
        id=uuid4(),
        batch_id=uuid4(),
        revision=1,
        supersedes=None,
        published_at=NOW,
        manifest={"rule_version": "test"},
        batch=batch(),
        quality_report={
            "issues": [],
            "coverage": {"expected": 3, "valid": 1, "legal_excluded": 0, "missing": 0},
        },
    )
    monkeypatch.setattr(repo, "list_snapshots", lambda **kwargs: [snapshot])
    async with workbench(repo) as client:
        assert_error(
            await client.get("/api/snapshots/list"),
            503,
            "DATA_UNAVAILABLE",
            "Data unavailable",
        )
