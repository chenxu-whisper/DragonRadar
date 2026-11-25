"""Strict public requests fail before touching any private/storage service."""

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_public_query_is_strict_without_task_dependency():
    from dragon_radar.http.routes.daily_ai import install_daily_ai_routes

    app = FastAPI()
    install_daily_ai_routes(app)
    with TestClient(app) as client:
        for path in (
            "/api/market/daily-ai",
            "/api/market/daily-ai?date=2026-11-24",
            "/api/market/daily-ai?date=2026-11-24&date=2026-11-24&snapshot_id=x",
            "/api/market/daily-ai/archives?date=2026-11-24&snapshot_id=x&limit=0",
            "/api/market/daily-ai?date=2026-11-24&snapshot_id=x&task_id=private",
        ):
            response = client.get(path)
            assert response.status_code == 400
            assert response.headers["cache-control"] == "no-store"
        assert client.post("/api/market/daily-ai").status_code == 405
        assert client.get("/api/ai/task/private").status_code == 404


def test_discovery_cursor_binding_and_bounds():
    from datetime import date
    from uuid import uuid4

    import pytest

    from dragon_radar.public_daily_ai.service import decode_cursor, encode_cursor

    snapshot_id = uuid4()
    cursor = encode_cursor(snapshot_id, date(2026, 11, 24), 20, 2, uuid4())
    assert decode_cursor(cursor, snapshot_id, date(2026, 11, 24))["watermark"] == 20
    for value, selected in [
        (cursor, uuid4()),
        ("bad", snapshot_id),
        (cursor + "!", snapshot_id),
    ]:
        with pytest.raises(ValueError):
            decode_cursor(value, selected, date(2026, 11, 24))
