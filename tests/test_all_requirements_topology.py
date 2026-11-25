"""Actual app factories must expose exactly the approved new boundaries."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from dragon_radar.http.app import create_workbench_app
from dragon_radar.http.public_app import create_public_app
from dragon_radar.owner.app import create_owner_app


def paths(app):
    return {
        nested.path
        for route in app.routes
        for nested in getattr(
            getattr(route, "original_router", None), "routes", [route]
        )
        if hasattr(nested, "path")
    }


@pytest.mark.parametrize(
    "factory", [create_workbench_app, create_public_app, create_owner_app]
)
def test_new_daily_reads_are_mounted_in_actual_app(factory, monkeypatch):
    monkeypatch.delenv("DRAGON_DATABASE_URL", raising=False)
    app = factory()
    assert {
        "/api/snapshots/lineage",
        "/api/index/{security_id}/history",
        "/api/market/daily-ai",
        "/api/market/daily-ai/archives",
    } <= paths(app)
    with TestClient(app, base_url="https://localhost:8030") as client:
        # Matching the static route must happen before UUID path validation.
        response = client.get("/api/snapshots/lineage")
        if factory is create_owner_app:
            assert response.status_code == 401
        else:
            assert response.status_code == 400
            assert response.json()["code"] == "INVALID_PARAM"


def test_public_never_mounts_private_or_specialized_lhb_routes(monkeypatch):
    monkeypatch.delenv("DRAGON_DATABASE_URL", raising=False)
    app = create_public_app()
    assert not any(
        path.startswith(("/api/ai/", "/api/research/", "/api/lhb"))
        for path in paths(app)
    )
    with TestClient(app) as client:
        for path in ("/api/market/daily-ai", "/api/snapshots/lineage"):
            assert client.post(path).status_code == 403
        response = client.get(f"/api/ai/task/{uuid4()}/publication-eligibility")
        assert response.status_code == 404


def test_owner_mounts_promotion_and_research_metadata_only_privately(monkeypatch):
    monkeypatch.delenv("DRAGON_DATABASE_URL", raising=False)
    app = create_owner_app()
    expected = {
        "/api/ai/task/{task_id}/publication-eligibility",
        "/api/ai/task/{task_id}/publish-daily",
        "/api/ai/task/{task_id}/publish-summary",
        "/api/ai/task/{task_id}/published-daily/{archive_id}/revoke",
        "/api/ai/task/{task_id}/published-summary/{summary_id}/revoke",
        "/api/research/capabilities",
        "/api/research/backtests/{run_id}/config",
        "/api/realtime/lineage",
        "/api/lhb",
        "/api/lhb/lineage",
        "/api/lhb/seats/{seat_id}",
    }
    assert expected <= paths(app)
    assert not expected & paths(create_public_app())
