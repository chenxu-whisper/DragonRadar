"""Snapshot-bound regulation status and formal-event HTTP contracts."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from factories import DAYS, NOW, batch
from httpx import ASGITransport, AsyncClient

from dragon_radar.publication.models import (
    PublicationCommand,
    PublicationResult,
    PublishedSnapshot,
)
from dragon_radar.publication.repository import PublicationRepository
from dragon_radar.publication.service import PublicationService
from dragon_radar.regulation.models import (
    RegulationEvent,
    RegulationObservation,
    RegulationReviewRecord,
    RegulationRule,
    RegulationTestCase,
)
from dragon_radar.regulation.repository import RegulationRepository

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _review_record():
    return RegulationReviewRecord(
        prepared_by="rule-author",
        reviewed_by="independent-reviewer",
        published_by="release-owner",
        reviewed_at=NOW - timedelta(hours=2),
        published_at=NOW - timedelta(hours=1),
    )


def _rule(rule_id: str, *, pending: bool = False):
    return RegulationRule(
        rule_id=rule_id,
        rule_version="synthetic-v1",
        applicable_exchange="TEST",
        applicable_board="synthetic_test",
        security_status="regular",
        effective_from=DAYS[0],
        effective_to=None,
        window_days=10,
        direction="upper",
        threshold=Decimal("0.70"),
        warning_threshold=Decimal("0.65"),
        benchmark_index="TEST-BENCHMARK",
        normal_anomaly_count_mode="rolling_window",
        recompute_condition="window_reset",
        calculation_method="relative_return",
        official_source_url=f"https://exchange.example.test/rules/{rule_id}",
        review_switch="pending" if pending else "approved",
        review_record=None if pending else _review_record(),
        test_cases=()
        if pending
        else (
            RegulationTestCase(
                name="approaching-boundary",
                computed_value=Decimal("0.65"),
                expected_state="approaching_alert",
            ),
        ),
        regression_passed=not pending,
        active=not pending,
    )


def _observation(rule_id: str, value: str):
    return RegulationObservation(
        rule_id=rule_id,
        rule_version="synthetic-v1",
        security_id="A",
        trade_date=DAYS[-1],
        exchange="TEST",
        board="synthetic_test",
        security_status="regular",
        computed_value=Decimal(value),
        input_state="complete",
    )


@pytest.fixture
def regulation_publication(database, repository):
    market = repository.publish(
        repository.import_batch(batch(), key="task16-market").id
    )
    regulations = RegulationRepository(database, repository, clock=lambda: NOW)
    for rule_id, pending in (
        ("synthetic-idle", False),
        ("synthetic-approaching", False),
        ("synthetic-triggered", False),
        ("synthetic-pending", True),
    ):
        regulations.register_rule(
            _rule(rule_id, pending=pending),
            key=f"task16-rule-{rule_id}",
        )
    regulations.append_event(
        RegulationEvent(
            event_id="formal-monitoring-001",
            rule_id="synthetic-approaching",
            rule_version="synthetic-v1",
            security_id="A",
            event_type="monitoring",
            announcement_id="notice-001",
            announcement_url="https://exchange.example.test/notices/001",
            effective_from=DAYS[-1],
            effective_to=None,
        ),
        key="task16-event",
    )
    regulation_snapshot = regulations.materialize(
        market_snapshot_id=market.id,
        observations=(
            _observation("synthetic-idle", "0.40"),
            _observation("synthetic-approaching", "0.68"),
            _observation("synthetic-triggered", "0.80"),
            _observation("synthetic-pending", "0.40"),
        ),
        key="task16-regulation-snapshot",
    )
    publication = PublicationService(
        database,
        repository,
        clock=lambda: NOW,
    ).publish(
        PublicationCommand(
            trade_date=DAYS[-1],
            market_snapshot_id=market.id,
            regulation_input_id=regulation_snapshot.id,
            disabled_domains=("topic", "news", "role"),
        ),
        key="task16-publication",
    )
    return publication, PublicationRepository(database, repository, clock=lambda: NOW)


async def _client(repository):
    from dragon_radar.http.app import create_workbench_app

    return AsyncClient(
        transport=ASGITransport(app=create_workbench_app(repository=repository)),
        base_url="http://testserver",
    )


async def test_status_keeps_completeness_calculations_and_formal_events_separate(
    regulation_publication,
):
    publication, publications = regulation_publication
    params = {
        "date": str(publication.trade_date),
        "snapshot_id": str(publication.id),
    }

    async with await _client(publications) as client:
        response = await client.get("/api/regulation/status", params=params)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["snapshot_id"] == str(publication.id)
    assert body["data"]["region1_completeness"] == {
        "status": "complete",
        "coverage_ratio": 1,
        "missing_count": 0,
        "source_check": "pass",
        "expected": 1,
        "valid": 1,
        "excluded": 0,
        "missing": 0,
    }
    calculations = {
        item["rule_id"]: item for item in body["data"]["region2_calc_state"]
    }
    assert {
        rule_id: item["state"] for rule_id, item in calculations.items()
    } == {
        "synthetic-approaching": "approaching_alert",
        "synthetic-idle": "idle",
        "synthetic-pending": "pending",
        "synthetic-triggered": "triggered",
    }
    pending = calculations["synthetic-pending"]
    assert pending["computed_value"] == 0.4
    assert pending["computed_value"] < pending["threshold"]
    assert pending["state"] == "pending"
    assert pending["review_switch"] == "pending"
    assert pending["threshold"] == 0.7
    assert pending["warning_threshold"] == 0.65
    assert pending["official_source_url"].endswith("/synthetic-pending")
    assert pending["effective_from"] == DAYS[0].isoformat()
    assert pending["effective_to"] is None
    assert pending["production_enabled"] is False
    assert "rule_not_approved" in pending["production_gate_reasons"]
    assert body["data"]["region3_events"] == [
        {
            "event_id": "formal-monitoring-001",
            "rule_id": "synthetic-approaching",
            "rule_version": "synthetic-v1",
            "security_id": "A",
            "event_type": "monitoring",
            "display_state": "monitoring",
            "announcement_id": "notice-001",
            "announcement_url": "https://exchange.example.test/notices/001",
            "effective_from": DAYS[-1].isoformat(),
            "effective_to": None,
        }
    ]
    assert len(body["data"]["region3_events"]) == 1
    assert calculations["synthetic-triggered"]["state"] == "triggered"


async def test_events_known_empty_is_zero_and_filter_does_not_infer_from_calculation(
    regulation_publication,
):
    publication, publications = regulation_publication
    common = {
        "date": str(publication.trade_date),
        "snapshot_id": str(publication.id),
    }

    async with await _client(publications) as client:
        known_empty = await client.get(
            "/api/regulation/events",
            params={**common, "security_id": "NO-FORMAL-EVENT"},
        )
        formal = await client.get(
            "/api/regulation/events",
            params={**common, "security_id": "A"},
        )

    assert known_empty.status_code == 200, known_empty.text
    assert known_empty.json()["data"] == {
        "events": [],
        "total": 0,
        "source_check": "pass",
    }
    assert formal.json()["data"]["total"] == 1
    assert formal.json()["data"]["events"][0]["event_type"] == "monitoring"
    assert all(
        event["rule_id"] != "synthetic-triggered"
        for event in formal.json()["data"]["events"]
    )


class _SnapshotRepository:
    database = object()

    def __init__(self, snapshot):
        self.snapshot = snapshot

    def get_snapshot(self, snapshot_id):
        return self.snapshot if snapshot_id == self.snapshot.id else None

    def list_snapshots(self, *, trade_date=None, limit=1, **_kwargs):
        return (
            [self.snapshot]
            if trade_date == self.snapshot.trade_date and limit
            else []
        )


async def test_missing_event_source_is_unknown_not_zero(regulation_publication):
    publication, publications = regulation_publication
    snapshot = publications.get_snapshot(publication.id)
    missing = PublicationResult(
        publication_id=snapshot.id,
        domain="regulation",
        input_id=None,
        status="missing",
        payload={},
        coverage={},
        quality_flags=("regulation_source_missing",),
    )
    unknown_snapshot = PublishedSnapshot(
        snapshot.publication.model_copy(
            update={
                "regulation_input_id": None,
                "domains": snapshot.publication.domains | {"regulation": "missing"},
            }
        ),
        snapshot.market_snapshot,
        tuple(
            missing if result.domain == "regulation" else result
            for result in snapshot.results
        ),
    )

    async with await _client(_SnapshotRepository(unknown_snapshot)) as client:
        response = await client.get(
            "/api/regulation/events",
            params={
                "date": str(publication.trade_date),
                "snapshot_id": str(publication.id),
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "events": None,
        "total": None,
        "source_check": "unknown",
    }
    assert response.json()["coverage"] is None
    assert "regulation_missing" in response.json()["quality_flags"]


async def test_stock_detail_reports_formal_count_and_event_source_completeness(
    regulation_publication,
):
    publication, publications = regulation_publication

    async with await _client(publications) as client:
        response = await client.get(
            "/api/stock/A",
            params={
                "date": str(publication.trade_date),
                "snapshot_id": str(publication.id),
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["regulation"] == {
        "formal_event_count": 1,
        "event_source_completeness": "complete",
        "reason": None,
    }


@pytest.mark.parametrize(
    "path",
    (
        "/api/regulation/status?snapshot_id=00000000-0000-4000-8000-000000000001",
        "/api/regulation/events?date=2026-09-08&security_id=A&security_id=B",
        "/api/regulation/events?date=2026-09-08&extra=1",
    ),
)
async def test_regulation_parameters_fail_before_storage_access(path):
    class NoStorage:
        database = object()
        calls = 0

        def get_snapshot(self, _snapshot_id):
            self.calls += 1

        def list_snapshots(self, **_kwargs):
            self.calls += 1
            return []

    repository = NoStorage()
    async with await _client(repository) as client:
        response = await client.get(path)

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_PARAM"
    assert repository.calls == 0


async def test_unknown_snapshot_returns_the_standard_empty_envelope():
    repository = _SnapshotRepository(None)
    repository.snapshot = type("Never", (), {"id": uuid4()})()

    async with await _client(repository) as client:
        response = await client.get(
            "/api/regulation/status",
            params={
                "date": "2026-09-08",
                "snapshot_id": str(uuid4()),
            },
        )

    assert response.status_code == 200
    assert response.json()["data"] is None
    assert response.json()["quality_flags"] == ["no_data"]
