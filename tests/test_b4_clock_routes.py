"""Isolated route integration; parent owns app and public allowlist wiring."""

import importlib
import importlib.util
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from dragon_radar.http.errors import DataUnavailable, register_error_handlers
from dragon_radar.storage.repository import Forbidden, NotFound

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def router():
    name = "dragon_radar.http.routes.lineage"
    assert importlib.util.find_spec(name) is not None, "Daily lineage router missing"
    return importlib.import_module(name).router


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?date=2026-11-24",
        "?snapshot_id=" + str(uuid4()),
        "?date=2026-11-24&snapshot_id=x",
        "?date=2026-11-24&date=2026-11-24&snapshot_id=x",
        "?date=2026-11-24&snapshot_id=x&latest=true",
    ],
)
async def test_route_rejects_invalid_selection_before_storage(query):
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/snapshots/lineage" + query)
    assert response.status_code == 400


@pytest.mark.parametrize(
    "error,status,code",
    [
        (Forbidden(), 403, "FORBIDDEN"),
        (NotFound(), 404, "NOT_FOUND"),
        (ValueError(), 400, "INVALID_PARAM"),
        (DataUnavailable(), 503, "DATA_UNAVAILABLE"),
    ],
)
async def test_route_preserves_sanitized_failure(error, status, code):
    class Reader:
        def get_lineage(self, snapshot_id, date):
            raise error

    app = FastAPI()
    register_error_handlers(app)
    app.state.publication_repository = Reader()
    app.include_router(router())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/api/snapshots/lineage?date=2026-11-24&snapshot_id={uuid4()}"
        )
    assert response.status_code == status
    assert response.json()["code"] == code
