"""Versioned market materialization and read API contracts."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from factories import bar, batch, state
from httpx import ASGITransport, AsyncClient

from dragon_radar.publication.models import PublicationCommand
from dragon_radar.publication.repository import PublicationRepository
from dragon_radar.publication.service import PublicationService

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _sessions(count: int = 61) -> tuple[date, ...]:
    days = []
    current = date(2026, 9, 8)
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    return tuple(reversed(days))


def _market_batch(*, include_touch=True, mismatched_touch=False):
    from dragon_radar.domain.models import ExchangeCalendar, LimitUpEvent

    sessions = _sessions()
    security_ids = ("A", "B", "C")
    states = tuple(
        state(
            security_id,
            effective_from=sessions[0],
            available_at=datetime(2026, 6, 1, tzinfo=UTC),
            code=f"00000{index}",
        )
        for index, security_id in enumerate(security_ids, 1)
    ) + (
        state(
            "B000300",
            effective_from=sessions[0],
            available_at=datetime(2026, 6, 1, tzinfo=UTC),
            code="000300",
            name="Synthetic Index",
            security_type="index",
        ),
    )
    bars = []
    for offset, day in enumerate(sessions):
        for security_id in security_ids:
            close = Decimal("10")
            amount = Decimal("100")
            if day == sessions[-1]:
                close = {"A": Decimal("11"), "B": Decimal("9"), "C": Decimal("10")}[
                    security_id
                ]
                amount = Decimal("200")
            bars.append(
                bar(
                    security_id,
                    trade_date=day,
                    close=close,
                    previous_close=Decimal("10") if day == sessions[-1] else None,
                    amount=amount,
                    limit_status="limited",
                    limit_date=day,
                    limit_up=Decimal("11"),
                    limit_down=Decimal("9"),
                )
            )
        bars.append(
            bar(
                "B000300",
                trade_date=day,
                close=Decimal("100") * (Decimal("1.01") ** offset),
                amount=Decimal("1000"),
            )
        )

    def event(security_id, day, *, sealed, touched, boards, source_id="synthetic"):
        return LimitUpEvent(
            security_id=security_id,
            trade_date=day,
            available_at=datetime(2026, 9, 8, 16, tzinfo=UTC),
            source_id=source_id,
            source_version="s1",
            is_limit_up=sealed,
            touched_limit=touched,
            continuous_boards=boards,
        )

    events = [
        event("A", sessions[-2], sealed=True, touched=True, boards=2),
        event("B", sessions[-2], sealed=True, touched=True, boards=3),
        event("A", sessions[-1], sealed=True, touched=True, boards=3),
        event("B", sessions[-1], sealed=False, touched=False, boards=0),
    ]
    if include_touch:
        events.append(event("C", sessions[-1], sealed=False, touched=True, boards=0))
    if mismatched_touch:
        events.append(
            event(
                "C",
                sessions[-1],
                sealed=False,
                touched=True,
                boards=0,
                source_id="other-source",
            )
        )
    return batch(
        trade_date=sessions[-1],
        as_of=datetime(2026, 9, 9, 12, tzinfo=UTC),
        calendar=ExchangeCalendar(exchange="TEST", version="c1", sessions=sessions),
        expected_security_ids=security_ids,
        security_states=states,
        bars=tuple(bars),
        limit_up_events=tuple(events),
    )


def test_breadth_partitions_valid_sample_and_zero_rates_are_not_applicable():
    from dragon_radar.readmodels.market import materialize_market

    result = materialize_market(_market_batch(include_touch=False), None)

    breadth = result["overview"]["breadth"]
    assert (
        breadth["up"] + breadth["down"] + breadth["flat"] == breadth["valid_total"] == 3
    )
    assert breadth["coverage"] == {
        "expected": 3,
        "valid": 3,
        "excluded": 0,
        "missing": 0,
    }
    assert breadth["seal_rate"] == {
        "numerator": 1,
        "denominator": 1,
        "value": Decimal("1.0000"),
        "reason": None,
    }

    no_events = _market_batch(include_touch=False).model_copy(
        update={"limit_up_events": ()}
    )
    rate = materialize_market(no_events, None)["overview"]["breadth"]["seal_rate"]
    assert rate["value"] is None
    assert rate["denominator"] == 0
    assert rate["reason"] == "no_known_touched_limit_observations"
    promoted = materialize_market(no_events, None)["overview"]["sentiment"][
        "promote_rate"
    ]
    assert promoted["value"] is None
    assert promoted["reason"] == "no_tradable_members_from_yesterday_ge2_set"


def test_promotion_uses_fixed_yesterday_ge2_set_and_today_tradable_denominator():
    from dragon_radar.readmodels.market import materialize_market

    result = materialize_market(_market_batch(), None)
    promoted = result["overview"]["sentiment"]["promote_rate"]

    assert promoted == {
        "yesterday_ge2": 2,
        "numerator": 1,
        "denominator": 2,
        "value": Decimal("0.5000"),
        "excluded": 0,
        "missing": 0,
        "reason": None,
    }


def test_promotion_exclusions_are_scoped_to_the_fixed_yesterday_set():
    from dragon_radar.readmodels.market import materialize_market

    payload = _market_batch()
    payload = payload.model_copy(
        update={
            "security_states": tuple(
                item.model_copy(update={"suspended": True})
                if item.security_id == "C"
                else item
                for item in payload.security_states
            )
        }
    )

    promoted = materialize_market(payload, None)["overview"]["sentiment"][
        "promote_rate"
    ]
    assert promoted["denominator"] == 2
    assert promoted["excluded"] == 0
    assert promoted["missing"] == 0


def test_limit_rate_never_mixes_sources_between_numerator_and_denominator():
    from dragon_radar.readmodels.market import materialize_market

    payload = _market_batch(include_touch=False, mismatched_touch=True)
    rate = materialize_market(payload, None)["overview"]["breadth"]["seal_rate"]

    assert rate["numerator"] == 1
    assert rate["denominator"] == 1
    assert rate["value"] == Decimal("1.0000")


def test_sentiment_series_and_heatmap_share_materialized_daily_counts():
    from dragon_radar.readmodels.market import materialize_market

    result = materialize_market(_market_batch(), None)
    latest = result["sentiment"]["series"][-1]
    assert {
        "trade_date",
        "limit_up_count",
        "limit_down_count",
        "max_boards",
        "promote_rate",
        "yesterday_limit_up_median_return",
    } <= latest.keys()
    latest_cells = [
        cell
        for cell in result["heatmap"]["cells"]
        if cell["trade_date"] == latest["trade_date"]
    ]
    assert sum(cell["count"] for cell in latest_cells) == latest["limit_up_count"]


def test_topic_metrics_respect_bound_coverage_and_never_substitute_turnover():
    from topic_factories import topic_batch

    from dragon_radar.readmodels.market import materialize_market

    topics = topic_batch()
    unavailable = tuple(
        cell.model_copy(
            update={
                "received_member_count": 0,
                "status": "unavailable",
                "reason": "provider_gap",
            }
        )
        if cell.trade_date == topics.trade_date
        else cell
        for cell in topics.coverage_cells
    )
    topics = topics.model_copy(update={"coverage_cells": unavailable})

    sector = materialize_market(_market_batch(), topics)["overview"]["hot_sectors"][0]
    assert sector["net_inflow"] is None
    assert sector["net_inflow_reason"] == "source_field_unavailable"
    assert sector["median_return"] is None
    assert sector["coverage"]["valid"] == 0
    assert "topic_coverage_unavailable" in sector["quality_flags"]


def test_fac_m01_m02_m03_follow_explicit_decimal_hand_calculations():
    from dragon_radar.readmodels.market import materialize_market

    result = materialize_market(_market_batch(), None)
    factors = result["overview"]["factors"]

    assert factors["FAC-M01"]["advancing"] == {
        "numerator": 1,
        "denominator": 3,
        "value": Decimal("0.3333"),
    }
    assert factors["FAC-M01"]["above_ma20"] == {
        "numerator": 1,
        "denominator": 3,
        "value": Decimal("0.3333"),
        "reason": None,
        "coverage": {"expected": 3, "valid": 3, "excluded": 0, "missing": 0},
    }
    # Today is 600; each of the prior 20 same-pool totals is 300.
    assert factors["FAC-M02"]["value"] == Decimal("1.0000")
    assert factors["FAC-M02"]["baseline_days"] == 20
    assert factors["FAC-M02"]["baseline_excludes_current"] is True

    closes = [
        Decimal("100") * (Decimal("1.01") ** offset)
        for offset in range(len(_sessions()))
    ]
    index = factors["FAC-M03"]["indexes"][0]
    assert index["security_id"] == "B000300"
    assert index["close_over_ma20"] == (
        closes[-1] / (sum(closes[-20:]) / Decimal(20)) - 1
    ).quantize(Decimal("0.0001"))
    assert index["close_over_ma60"] == (
        closes[-1] / (sum(closes[-60:]) / Decimal(60)) - 1
    ).quantize(Decimal("0.0001"))
    assert index["volatility_20"] == Decimal("0.0000")
    assert index["volatility_method"] == "sample_stddev_non_annualized"
    strip = result["overview"]["indexes"][0]
    assert strip["last"] == closes[-1]
    assert strip["change_ratio"] == Decimal("0.01")
    assert strip["amount"] == Decimal("1000")


@pytest.fixture
def market_publication(database, repository):
    payload = batch(
        security_states=(state(code="000001", name="Synthetic Alpha"),),
    )
    market = repository.publish(
        repository.import_batch(payload, key="task09-market").id
    )
    record = PublicationService(database, repository).publish(
        PublicationCommand(
            trade_date=payload.trade_date,
            market_snapshot_id=market.id,
            disabled_domains=("topic", "news", "regulation", "role"),
        ),
        key="task09-publication",
    )
    return record, PublicationRepository(database, repository)


@pytest.mark.anyio
async def test_publication_materializes_all_market_views_and_routes_only_read_them(
    market_publication, monkeypatch
):
    from dragon_radar.http.app import create_workbench_app

    record, publication_repository = market_publication
    stored = publication_repository.list_results(record.id)
    market = next(item for item in stored if item.domain == "market")
    assert set(market.payload) == {
        "overview",
        "sector_flow",
        "sentiment",
        "heatmap",
        "search",
    }

    def recomputation_is_forbidden(*_args, **_kwargs):
        raise AssertionError("market read model was recomputed")

    monkeypatch.setattr(
        "dragon_radar.readmodels.market.materialize_market",
        recomputation_is_forbidden,
    )
    app = create_workbench_app(repository=publication_repository)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        for path in (
            "/api/market/overview",
            "/api/market/sector-flow?top_n=10",
            "/api/market/sentiment?days=20",
            "/api/market/heatmap?days=60",
        ):
            separator = "&" if "?" in path else "?"
            response = await client.get(f"{path}{separator}snapshot_id={record.id}")
            assert response.status_code == 200
            assert response.json()["snapshot_id"] == str(record.id)


@pytest.mark.anyio
async def test_search_uses_publication_bound_identity_and_has_extensible_groups(
    market_publication,
):
    from dragon_radar.http.app import create_workbench_app

    record, publication_repository = market_publication
    app = create_workbench_app(repository=publication_repository)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        q_only = await client.get("/api/search", params={"q": " 000001 "})
        historical = await client.get(
            "/api/search",
            params={
                "q": "Alpha",
                "date": str(record.trade_date),
                "snapshot_id": str(record.id),
            },
        )

    assert q_only.status_code == historical.status_code == 200
    data = q_only.json()["data"]
    assert data["groups"]["securities"] == [
        {
            "security_id": "A",
            "code": "000001",
            "name": "Synthetic Alpha",
            "match_field": "code",
            "exchange": "TEST",
        }
    ]
    assert data["groups"]["topics"] == []
    assert data["groups"]["messages"] == []
    assert historical.json()["data"]["groups"]["securities"][0]["match_field"] == "name"


class _NoStorageRepository:
    def __init__(self):
        self.database = type("Database", (), {"ready": lambda self: True})()
        self.calls = []

    def list_snapshots(self, **kwargs):
        self.calls.append(kwargs)
        return []

    def get_snapshot(self, snapshot_id):
        self.calls.append(snapshot_id)
        return None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "query"),
    [
        ("/api/market/overview", "date=2026-09-08&x=1"),
        ("/api/market/sector-flow", "date=2026-09-08&top_n=0"),
        ("/api/market/sentiment", "date=2026-09-08&days=121"),
        ("/api/market/heatmap", "date=2026-09-08&days=1&days=2"),
        ("/api/search", "q="),
        ("/api/search", f"q=A&snapshot_id={uuid4()}"),
        ("/api/search", f"q={'A' * 51}"),
    ],
)
async def test_market_parameters_fail_before_storage_access(path, query):
    from dragon_radar.http.app import create_workbench_app

    repository = _NoStorageRepository()
    app = create_workbench_app(repository=repository)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(f"{path}?{query}")

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_PARAM"
    assert repository.calls == []
