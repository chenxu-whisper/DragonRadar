"""Owned service and HTTP boundaries, no live database required."""

import copy
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_b7_result_contracts import actual_result

from dragon_radar.http.routes.research import _safe_record
from dragon_radar.research.repository import ResearchRunRecord


def record(*, status="succeeded", result=None, failure=None):
    cfg, actual = actual_result()
    return ResearchRunRecord(
        id=uuid4(),
        owner_id=uuid4(),
        config=cfg,
        status=status,
        attempt_count=1,
        max_wall_clock_ms=300000,
        result=actual if status == "succeeded" and result is None else result,
        failure=failure,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_safe_record_rejects_corrupt_success():
    cfg, payload = actual_result()
    payload["metrics"]["total_return"] = "9.000000"
    with pytest.raises(RuntimeError, match="materialized"):
        _safe_record(record(result=payload))


@pytest.mark.parametrize("status", ["queued", "running", "failed", "cancelled"])
def test_safe_record_rejects_invalid_lifecycle(status):
    cfg, payload = actual_result()
    with pytest.raises(RuntimeError, match="materialized"):
        _safe_record(record(status=status, result=payload))


def test_safe_record_preserves_exact_six_fields():
    assert set(_safe_record(record())) == {
        "run_id",
        "status",
        "config_hash",
        "research_only",
        "result",
        "failure",
    }


def test_cache_validator_rejects_differing_result():
    from dragon_radar.research import repository

    cfg, payload = actual_result()
    different = copy.deepcopy(payload)
    different["quality"][0]["events"].append(
        {
            "trade_date": different["quality"][0]["trade_date"],
            "code": "factor_section_empty",
            "security_id": None,
        }
    )
    validator = getattr(repository, "validated_cache_result", None)
    assert callable(validator), "repository must validate cache before success"
    with pytest.raises(ValueError):
        validator(
            {
                "result": different,
                "input_manifest": {
                    "config": cfg.model_dump(mode="json"),
                },
            },
            cfg,
            candidate=payload,
        )


def test_worker_invalid_candidate_fails_without_success():
    from types import SimpleNamespace

    from dragon_radar.research.worker import finish_validated_result

    cfg, payload = actual_result()
    payload["metrics"]["total_return"] = True

    class Repository:
        def succeed(self, *args):
            pytest.fail("invalid result must never enter success")

        def fail(self, lease, failure):
            return failure

    assert finish_validated_result(
        Repository(), SimpleNamespace(run=SimpleNamespace(config=cfg)), payload
    ) == {"code": "result_contract_invalid"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_owner_http_config_and_window_boundaries():
    from types import SimpleNamespace

    import httpx
    from fastapi import FastAPI

    from dragon_radar.http.routes.research import install_research_routes
    from dragon_radar.owner.security import OwnerContext
    from dragon_radar.research.capabilities import disabled_capabilities
    from dragon_radar.research.repository import ResearchRunRepository

    run = record()
    calls = []

    class Repository(ResearchRunRepository):
        def __init__(self):
            pass

        def get(self, token, run_id):
            calls.append(("get", token, run_id))
            return run

        def run_config(self, token, run_id):
            calls.append(("config", token, run_id))
            return {
                "run_id": str(run_id),
                "config_hash": run.config.config_hash,
                "config": run.config.model_dump(mode="json"),
            }

        def capabilities(self, token, snapshot_id, period):
            calls.append(("capabilities", token, snapshot_id, period))
            return disabled_capabilities(snapshot_id, period)

    app = FastAPI()
    app.state.research_repository = Repository()

    @app.middleware("http")
    async def trusted_owner_context(request, call_next):
        request.state.owner_context = OwnerContext(
            SimpleNamespace(), "authenticated-owner", None, "https://test"
        )
        return await call_next(request)

    install_research_routes(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        response = await client.get(f"/api/research/backtests/{run.id}/config")
        assert response.status_code == 200
        assert set(response.json()) == {"run_id", "config_hash", "config"}
        assert calls[-1] == ("config", "authenticated-owner", run.id)
        query = f"snapshot_id={run.config.snapshot_id}&start=2025-01-06&end=2025-01-10"
        response = await client.get("/api/research/capabilities?" + query)
        assert response.status_code == 200
        assert response.json()["period"] == {"start": "2025-01-06", "end": "2025-01-10"}
        assert calls[-1][0:2] == ("capabilities", "authenticated-owner")
        count = len(calls)
        for suffix in (
            "&start=2025-01-06",
            "&unknown=1",
            "&snapshot_id=" + str(run.config.snapshot_id),
        ):
            assert (
                await client.get("/api/research/capabilities?" + query + suffix)
            ).status_code == 400
        assert len(calls) == count
        run = record(result={"invented": "metrics"})
        response = await client.get(f"/api/research/backtests/{run.id}")
        assert response.status_code == 503
        assert response.json()["code"] == "DATA_UNAVAILABLE"
        assert "invented" not in response.text


def test_v2_service_checks_every_archived_publication_not_just_anchor():
    from types import SimpleNamespace
    from uuid import UUID

    from test_b7_materialization import materialized

    from dragon_radar.research.repository import ResearchRunRepository
    from dragon_radar.research.result_contracts import ResearchDataUnavailable

    cfg, result = materialized()
    frames = result["lineage"]["frames"]
    rows = [
        {
            "id": UUID(frame["publication_id"]),
            "publication_sequence": frame["publication_sequence"],
            "trade_date": datetime.fromisoformat(frame["trade_date"]).date(),
            "market_snapshot_id": UUID(frame["market_snapshot_id"]),
            "topic_snapshot_id": None,
            "role_input_id": None,
            "published_at": datetime(2026, 1, 1, tzinfo=UTC),
            "status": "published",
        }
        for frame in frames
    ]
    calls = []

    class Connection:
        def execute(self, statement):
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))

    repository = ResearchRunRepository(
        None,
        None,
        SimpleNamespace(
            _authorized_records=lambda connection, actual: calls.append(actual)
        ),
    )
    method = getattr(repository, "_validate_history", None)
    assert callable(method)
    method(Connection(), result, trusted_history=copy.deepcopy(frames))
    assert calls == [rows]
    rows[0]["market_snapshot_id"] = uuid4()
    with pytest.raises(ResearchDataUnavailable):
        method(Connection(), result, trusted_history=copy.deepcopy(frames))
