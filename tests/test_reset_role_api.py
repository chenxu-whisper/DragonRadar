"""Publication-bound ladder and role evidence HTTP contracts."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from factories import NOW, bar, batch, state
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from topic_factories import topic_batch

from dragon_radar.domain.models import LimitUpEvent
from dragon_radar.publication.models import PublicationCommand
from dragon_radar.publication.repository import PublicationRepository
from dragon_radar.publication.service import PublicationService
from dragon_radar.publication.tables import publication_results
from dragon_radar.roles.materialize import RoleMaterializer
from dragon_radar.topics.repository import TopicRepository

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _event(security_id, trade_date, boards):
    return LimitUpEvent(
        security_id=security_id,
        trade_date=trade_date,
        available_at=datetime(2026, 9, 8, 16, tzinfo=UTC),
        source_id="synthetic",
        source_version="s1",
        is_limit_up=True,
        touched_limit=True,
        continuous_boards=boards,
    )


def _role_payload(run_id, trade_date):
    status = {
        "role": "trend",
        "capability": "ready",
        "status": "ready",
        "expected": 2,
        "valid": 2,
        "legal_excluded": 0,
        "missing": 0,
        "reasons": [],
    }
    other_statuses = [
        {
            "role": role,
            "capability": capability,
            "status": "insufficient",
            "expected": 2,
            "valid": 0,
            "legal_excluded": 0,
            "missing": 2,
            "reasons": ["required_inputs_missing"],
        }
        for role, capability in (
            ("core", "ready"),
            ("emotion", "simplified"),
            ("catchup", "simplified"),
            ("follower", "watch_only"),
            ("relay", "pending_validation"),
        )
    ]
    results = [
        {
            "id": str(uuid4()),
            "security_id": security_id,
            "role": "trend",
            "rule_version": "role-v1",
            "internal_status": "candidate",
            "tie_rank": 1,
            "factors": ["0.2", "0.1"],
            "reasons": [f"persisted_support_{security_id.lower()}"],
        }
        for security_id in ("A", "B")
    ]
    factor = {
        "security_id": "A",
        "factor_id": "FAC-R01",
        "metric": "relative_strength_20",
        "window": "T-20..T",
        "value": "0.2000",
        "unit": "ratio",
        "coverage": {
            "expected": 21,
            "valid": 21,
            "excluded": 0,
            "missing": 0,
        },
        "reasons": [],
        "metadata": {"knowledge_cutoff": NOW.isoformat()},
    }
    topic_a = {
        "topic_id": "topic-a",
        "member_ids": ["A", "B"],
        "statuses": [status, *other_statuses],
        "results": results,
        "factors": [factor],
        "ladder": [
            {
                "security_id": "A",
                "trade_date": trade_date.isoformat(),
                "closing_limit_streak": 1,
                "limit_hits_10": 1,
                "broken_board": False,
                "touched_not_sealed": False,
                "reasons": [],
            },
            {
                "security_id": "B",
                "trade_date": trade_date.isoformat(),
                "closing_limit_streak": 2,
                "limit_hits_10": 2,
                "broken_board": False,
                "touched_not_sealed": False,
                "reasons": [],
            },
        ],
        "change_summaries": [],
        "role_changes": [],
        "pending_validations": [],
        "role_validations": [],
        "role_validation_inputs": [],
    }
    topic_b = {
        **topic_a,
        "topic_id": "topic-b",
        "member_ids": ["A"],
        "statuses": [
            {**item, "expected": 1, "valid": 1, "missing": 0} for item in [status]
        ]
        + [{**item, "expected": 1, "missing": 1} for item in other_statuses],
        "results": [results[0]],
        "factors": [factor],
        "ladder": [topic_a["ladder"][0]],
    }
    return {
        "role_run_id": str(run_id),
        "trade_date": trade_date.isoformat(),
        "rule_version": "role-v1",
        "benchmark_id": "CSI300",
        "topics": [topic_a, topic_b],
    }


@pytest.fixture
def role_publication(database, repository):
    topics = topic_batch()
    market = batch(
        trade_date=topics.trade_date,
        calendar=topics.calendar,
        calendar_version=topics.calendar_version,
        expected_security_ids=("A", "B"),
        security_states=(
            state("A", effective_from=topics.calendar.sessions[0]),
            state("B", effective_from=topics.calendar.sessions[0]),
        ),
        bars=tuple(
            bar(security_id, trade_date=day)
            for day in topics.calendar.sessions
            for security_id in ("A", "B")
        ),
        limit_up_events=(
            _event("A", topics.calendar.sessions[-2], 2),
            _event("A", topics.trade_date, 1),
            _event("B", topics.trade_date, 2),
        ),
    )
    market_snapshot = repository.publish(
        repository.import_batch(market, key="task12-market").id
    )
    topic_repository = TopicRepository(database, clock=lambda: NOW)
    topic_snapshot = topic_repository.publish(
        topic_repository.import_batch(topics, key="task12-topic").id
    )
    run = RoleMaterializer(database, repository, clock=lambda: NOW).materialize(
        market_snapshot_id=market_snapshot.id,
        topic_snapshot_id=topic_snapshot.id,
        rule_version="role-test-v1",
        benchmark_id="CSI300",
        key="task12-role",
    )
    publication = PublicationService(database, repository, clock=lambda: NOW).publish(
        PublicationCommand(
            trade_date=market.trade_date,
            market_snapshot_id=market_snapshot.id,
            topic_snapshot_id=topic_snapshot.id,
            role_input_id=run.id,
            disabled_domains=("news", "regulation"),
        ),
        key="task12-publication",
    )
    persisted = PublicationRepository(database, repository)
    market_result = next(
        item
        for item in persisted.list_results(publication.id)
        if item.domain == "market"
    )
    market_payload = market_result.model_dump(mode="json")["payload"]
    market_payload["overview"]["sentiment"]["limit_up_count"] = 2
    market_payload["overview"]["sentiment"]["promote_rate"] = {
        "yesterday_ge2": 2,
        "numerator": 1,
        "denominator": 2,
        "value": "0.5000",
        "excluded": 0,
        "missing": 0,
        "reason": None,
    }
    market_payload["overview"]["sentiment"]["yesterday_limit_up_median_return"] = (
        "0.0120"
    )
    market_payload["heatmap"] = {
        "columns": [1, 2],
        "cells": [
            {
                "trade_date": str(market.trade_date),
                "boards": 1,
                "count": 1,
            },
            {
                "trade_date": str(market.trade_date),
                "boards": 2,
                "count": 1,
            },
        ],
    }
    market_payload["search"]["securities"] = [
        {
            "security_id": "A",
            "code": "000001",
            "name": "Synthetic Alpha",
            "exchange": "TEST",
        },
        {
            "security_id": "B",
            "code": "000002",
            "name": "Synthetic Beta",
            "exchange": "TEST",
        },
    ]
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE publication_results DISABLE TRIGGER USER"
        )
        connection.execute(
            update(publication_results)
            .where(
                publication_results.c.publication_id == publication.id,
                publication_results.c.domain == "market",
            )
            .values(payload=market_payload)
        )
        connection.execute(
            update(publication_results)
            .where(
                publication_results.c.publication_id == publication.id,
                publication_results.c.domain == "role",
            )
            .values(
                payload=_role_payload(run.id, market.trade_date),
                coverage={
                    "expected": 18,
                    "valid": 3,
                    "legal_excluded": 0,
                    "missing": 15,
                },
            )
        )
        connection.exec_driver_sql(
            "ALTER TABLE publication_results ENABLE TRIGGER USER"
        )
    return publication, persisted, repository


async def _client(repository):
    from dragon_radar.http.app import create_workbench_app

    app = create_workbench_app(repository=repository)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    )


async def test_four_routes_bind_one_publication_and_never_recompute(
    role_publication, monkeypatch
):
    publication, persisted, _repository = role_publication

    def forbidden(*_args, **_kwargs):
        raise AssertionError("role algorithm was called by GET")

    for name in (
        "evaluate_trend",
        "evaluate_core",
        "evaluate_emotion",
        "evaluate_catchup",
        "evaluate_follower",
        "evaluate_relay",
    ):
        monkeypatch.setattr(f"dragon_radar.roles.engine.{name}", forbidden)

    async with await _client(persisted) as client:
        responses = [
            await client.get(
                path,
                params={
                    **params,
                    "date": str(publication.trade_date),
                    "snapshot_id": str(publication.id),
                },
            )
            for path, params in (
                ("/api/limit-up/ladder", {}),
                ("/api/limit-up/continuity", {}),
                ("/api/role/candidates", {"role": "trend_leader"}),
                (
                    "/api/role/evidence",
                    {"security_id": "A", "sector": "topic-a"},
                ),
            )
        ]

    assert [response.status_code for response in responses] == [200] * 4
    assert {response.json()["snapshot_id"] for response in responses} == {
        str(publication.id)
    }


@pytest.mark.parametrize(
    ("path", "query"),
    [
        ("/api/limit-up/ladder", ""),
        ("/api/limit-up/continuity", "date=2026-09-08&extra=1"),
        ("/api/role/candidates", "date=2026-09-08&role=Trend_leader"),
        (
            "/api/role/evidence",
            "date=2026-09-08&security_id=A&sector=topic-a&sector=topic-b",
        ),
    ],
)
async def test_parameters_fail_before_storage_access(path, query):
    class NoStorage:
        database = type("Database", (), {"ready": lambda self: True})()
        calls = []

        def get_snapshot(self, value):
            self.calls.append(value)

        def list_snapshots(self, **values):
            self.calls.append(values)
            return []

    repository = NoStorage()
    async with await _client(repository) as client:
        response = await client.get(f"{path}?{query}")

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_PARAM"
    assert repository.calls == []


async def test_unknown_publication_and_date_mismatch_contract(role_publication):
    publication, persisted, _repository = role_publication
    async with await _client(persisted) as client:
        unknown = await client.get(
            "/api/limit-up/ladder",
            params={"date": str(publication.trade_date), "snapshot_id": str(uuid4())},
        )
        missing = await client.get(
            "/api/role/candidates",
            params={"date": "2025-01-01", "role": "trend_leader"},
        )
        mismatch = await client.get(
            "/api/limit-up/continuity",
            params={"date": "2025-01-01", "snapshot_id": str(publication.id)},
        )

    for response in (unknown, missing):
        assert response.status_code == 200
        assert response.json()["data"] is None
        assert response.json()["quality_flags"] == ["no_data"]
    assert mismatch.status_code == 400


@pytest.mark.parametrize("status", ["missing", "disabled"])
async def test_role_domain_unavailable_is_explicit(role_publication, database, status):
    publication, _persisted, repository = role_publication
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE publication_results DISABLE TRIGGER USER"
        )
        connection.execute(
            update(publication_results)
            .where(
                publication_results.c.publication_id == publication.id,
                publication_results.c.domain == "role",
            )
            .values(status=status, payload={}, coverage={})
        )
        connection.exec_driver_sql(
            "ALTER TABLE publication_results ENABLE TRIGGER USER"
        )
    persisted = PublicationRepository(database, repository)

    async with await _client(persisted) as client:
        response = await client.get(
            "/api/role/candidates",
            params={
                "date": str(publication.trade_date),
                "role": "trend_leader",
                "snapshot_id": str(publication.id),
            },
        )

    assert response.status_code == 200
    assert response.json()["snapshot_id"] == str(publication.id)
    assert response.json()["data"] is None
    assert f"role_{status}" in response.json()["quality_flags"]


async def test_current_source_withdrawal_is_forbidden(role_publication):
    publication, persisted, repository = role_publication
    repository.revoke_source("synthetic")

    async with await _client(persisted) as client:
        response = await client.get(
            "/api/limit-up/ladder",
            params={
                "date": str(publication.trade_date),
                "snapshot_id": str(publication.id),
            },
        )

    assert response.status_code == 403


async def test_ladder_deduplicates_multisector_stocks_and_matches_heatmap(
    role_publication, monkeypatch
):
    publication, persisted, _repository = role_publication
    monkeypatch.setattr(
        "dragon_radar.http.routes.roles.RESET_APPROVED_RULE_VERSIONS",
        frozenset({"role-v1"}),
    )
    params = {
        "date": str(publication.trade_date),
        "snapshot_id": str(publication.id),
    }
    async with await _client(persisted) as client:
        ladder = await client.get("/api/limit-up/ladder", params=params)
        heatmap = await client.get("/api/market/heatmap", params={**params, "days": 1})
        overview = await client.get("/api/market/overview", params=params)
        sector = await client.get(
            "/api/limit-up/ladder", params={**params, "sector": "topic-a"}
        )
        empty = await client.get(
            "/api/limit-up/ladder", params={**params, "sector": "topic-empty"}
        )

    assert ladder.status_code == 200, ladder.text
    data = ladder.json()["data"]
    assert data["columns"] == [1, 2]
    assert data["total"] == 2
    assert sum(cell["count"] for cell in data["cells"]) == 2
    assert [cell["count"] for cell in data["cells"]] == [
        cell["count"] for cell in heatmap.json()["data"]["cells"]
    ]
    assert data["total"] == overview.json()["data"]["sentiment"]["limit_up_count"]
    assert [
        stock["security_id"] for cell in data["cells"] for stock in cell["stocks"]
    ] == ["A", "B"]
    assert data["cells"][0]["stocks"][0]["roles"] == ["trend_leader"]
    assert sector.json()["data"]["total"] == 2
    assert empty.json()["data"]["cells"] == []
    assert empty.json()["data"]["total"] == 0


async def test_continuity_reuses_decimal_materialization(role_publication):
    publication, persisted, _repository = role_publication
    async with await _client(persisted) as client:
        response = await client.get(
            "/api/limit-up/continuity",
            params={
                "date": str(publication.trade_date),
                "snapshot_id": str(publication.id),
            },
        )

    body = json.loads(response.content, parse_float=Decimal)
    assert body["data"] == {
        "promote_rate": {
            "yesterday_ge2": 2,
            "today_sealed": 1,
            "tradable": 2,
            "rate": Decimal("0.5000"),
        },
        "yesterday_limit_up_median_return": Decimal("0.0120"),
        "tradable_count": 2,
    }
    assert (
        body["data"]["promote_rate"]["rate"].as_tuple() == Decimal("0.5000").as_tuple()
    )


async def test_candidates_preserve_ties_filter_coverage_and_fail_closed(
    role_publication, monkeypatch
):
    publication, persisted, _repository = role_publication
    params = {
        "date": str(publication.trade_date),
        "snapshot_id": str(publication.id),
        "role": "trend_leader",
        "sector": "topic-a",
    }
    async with await _client(persisted) as client:
        closed = await client.get("/api/role/candidates", params=params)
        monkeypatch.setattr(
            "dragon_radar.http.routes.roles.RESET_APPROVED_RULE_VERSIONS",
            frozenset({"role-v1"}),
        )
        approved = await client.get("/api/role/candidates", params=params)
        no_candidate = await client.get(
            "/api/role/candidates", params={**params, "role": "emotion_leader"}
        )

    assert closed.json()["data"]["candidates"] == []
    assert "rule_version_not_approved" in closed.json()["quality_flags"]
    candidates = approved.json()["data"]["candidates"]
    assert [(item["security_id"], item["rank"]) for item in candidates] == [
        ("A", 1),
        ("B", 1),
    ]
    assert approved.json()["coverage"] == {
        "expected": 2,
        "valid": 2,
        "excluded": 0,
        "missing": 0,
    }
    assert no_candidate.json()["data"]["candidates"] == []
    assert no_candidate.json()["coverage"]["expected"] == sum(
        no_candidate.json()["coverage"][key] for key in ("valid", "excluded", "missing")
    )


async def test_evidence_is_traceable_and_missing_entity_is_404(
    role_publication, monkeypatch
):
    publication, persisted, _repository = role_publication
    monkeypatch.setattr(
        "dragon_radar.http.routes.roles.RESET_APPROVED_RULE_VERSIONS",
        frozenset({"role-v1"}),
    )
    params = {
        "date": str(publication.trade_date),
        "snapshot_id": str(publication.id),
        "security_id": "A",
        "sector": "topic-a",
    }
    async with await _client(persisted) as client:
        response = await client.get("/api/role/evidence", params=params)
        missing = await client.get(
            "/api/role/evidence", params={**params, "security_id": "UNKNOWN"}
        )

    assert response.status_code == 200
    role = next(
        item
        for item in response.json()["data"]["roles"]
        if item["role_type"] == "trend_leader"
    )
    assert role["evidence_status"] == "candidate"
    assert role["supporting_evidence"] == ["persisted_support_a"]
    factor = role["factors"][0]
    assert {
        "factor_id",
        "value",
        "status",
        "unit",
        "window",
        "coverage",
        "reasons",
        "source_time",
    } <= factor.keys()
    assert factor["source_time"] == NOW.isoformat()
    assert "available_at" not in factor
    assert missing.status_code == 404
