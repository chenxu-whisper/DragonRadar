"""Versioned market materialization and read API contracts."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from factories import bar, batch, state
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from dragon_radar.publication.models import PublicationCommand
from dragon_radar.publication.repository import PublicationRepository
from dragon_radar.publication.service import PublicationService
from dragon_radar.publication.tables import publication_results

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


def _market_batch(
    *, include_touch=True, mismatched_touch=False, session_count: int = 61
):
    from dragon_radar.domain.models import ExchangeCalendar, LimitUpEvent

    sessions = _sessions(session_count)
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
        "value": None,
        "reason": "incomplete_touched_limit_coverage",
        "coverage": {"expected": 3, "valid": 2, "excluded": 0, "missing": 1},
    }
    assert breadth["members"] == {
        "up": [{"security_id": "A", "code": "000001", "name": "Synthetic A"}],
        "down": [{"security_id": "B", "code": "000002", "name": "Synthetic B"}],
        "flat": [{"security_id": "C", "code": "000003", "name": "Synthetic C"}],
    }

    no_events = _market_batch(include_touch=False).model_copy(
        update={"limit_up_events": ()}
    )
    rate = materialize_market(no_events, None)["overview"]["breadth"]["seal_rate"]
    assert rate["value"] is None
    assert rate["denominator"] == 0
    assert rate["reason"] == "incomplete_touched_limit_coverage"
    assert rate["coverage"] == {
        "expected": 3,
        "valid": 0,
        "excluded": 0,
        "missing": 3,
    }
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
    assert rate["value"] is None
    assert rate["reason"] == "incomplete_touched_limit_coverage"
    assert rate["coverage"]["missing"] == 1


def test_limit_rate_unknown_status_disables_value_for_expected_common_stock_pool():
    from dragon_radar.readmodels.market import materialize_market

    payload = _market_batch()
    payload = payload.model_copy(
        update={
            "limit_up_events": tuple(
                event.model_copy(update={"touched_limit": None})
                if event.trade_date == payload.trade_date and event.security_id == "C"
                else event
                for event in payload.limit_up_events
            )
        }
    )

    rate = materialize_market(payload, None)["overview"]["breadth"]["seal_rate"]

    assert rate == {
        "numerator": 1,
        "denominator": 1,
        "value": None,
        "reason": "incomplete_touched_limit_coverage",
        "coverage": {"expected": 3, "valid": 2, "excluded": 0, "missing": 1},
    }


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
    assert latest["yesterday_limit_up_return_coverage"] == {
        "expected": 2,
        "valid": 2,
        "excluded": 0,
        "missing": 0,
    }
    assert latest["yesterday_limit_up_return_reason"] is None


def test_yesterday_limit_up_feedback_discloses_missing_reason():
    from dragon_radar.readmodels.market import materialize_market

    payload = _market_batch()
    payload = payload.model_copy(
        update={
            "bars": tuple(
                item
                for item in payload.bars
                if not (
                    item.trade_date == payload.trade_date
                    and item.security_id in {"A", "B"}
                )
            )
        }
    )

    feedback = materialize_market(payload, None)["overview"]["sentiment"]

    assert feedback["yesterday_limit_up_median_return"] is None
    assert feedback["yesterday_limit_up_return_coverage"] == {
        "expected": 2,
        "valid": 0,
        "excluded": 0,
        "missing": 2,
    }
    assert feedback["yesterday_limit_up_return_reason"] == (
        "no_valid_returns_for_yesterday_limit_up_members"
    )


def test_yesterday_limit_up_feedback_counts_suspension_as_legal_exclusion():
    from dragon_radar.readmodels.market import materialize_market

    payload = _market_batch()
    payload = payload.model_copy(
        update={
            "security_states": tuple(
                item.model_copy(update={"suspended": True})
                if item.security_id == "B"
                else item
                for item in payload.security_states
            )
        }
    )

    coverage = materialize_market(payload, None)["overview"]["sentiment"][
        "yesterday_limit_up_return_coverage"
    ]

    assert coverage == {
        "expected": 2,
        "valid": 1,
        "excluded": 1,
        "missing": 0,
    }
    assert coverage["expected"] == (
        coverage["valid"] + coverage["excluded"] + coverage["missing"]
    )


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


def test_topic_member_coverage_counts_suspension_as_legal_exclusion():
    from topic_factories import topic_batch, topic_membership

    from dragon_radar.readmodels.market import materialize_market

    market = _market_batch()
    market = market.model_copy(
        update={
            "security_states": tuple(
                item.model_copy(update={"suspended": True})
                if item.security_id == "B"
                else item
                for item in market.security_states
            )
        }
    )
    topics = topic_batch(
        memberships=(topic_membership(), topic_membership(security_id="B"))
    )
    topics = topics.model_copy(
        update={
            "coverage_cells": tuple(
                cell.model_copy(
                    update={"expected_member_count": 2, "received_member_count": 2}
                )
                if cell.trade_date == topics.trade_date
                else cell
                for cell in topics.coverage_cells
            )
        }
    )

    sector = materialize_market(market, topics)["overview"]["hot_sectors"][0]

    assert sector["coverage"] == {
        "expected": 2,
        "valid": 1,
        "excluded": 1,
        "missing": 0,
    }
    assert sector["coverage"]["expected"] == sum(
        sector["coverage"][key] for key in ("valid", "excluded", "missing")
    )


def test_topic_limit_up_ratio_uses_same_valid_return_pool_as_denominator():
    from topic_factories import topic_batch

    from dragon_radar.readmodels.market import materialize_market

    payload = _market_batch()
    payload = payload.model_copy(
        update={
            "bars": tuple(
                item
                for item in payload.bars
                if not (
                    item.security_id == "A" and item.trade_date == payload.trade_date
                )
            )
        }
    )

    sector = materialize_market(payload, topic_batch())["overview"]["hot_sectors"][0]

    assert sector["up_ratio"]["denominator"] == 0
    assert sector["limit_up_ratio"] == {
        "numerator": 0,
        "denominator": 0,
        "value": None,
        "reason": "no_valid_member_returns",
    }
    assert sector["limit_up_count"] == 0
    assert sector["coverage"] == {
        "expected": 1,
        "valid": 0,
        "excluded": 0,
        "missing": 1,
    }


def test_topic_coverage_missing_never_becomes_negative():
    from topic_factories import topic_batch, topic_membership

    from dragon_radar.readmodels.market import materialize_market

    topics = topic_batch(
        memberships=(
            topic_membership(),
            topic_membership(security_id="B"),
            topic_membership(security_id="C"),
        )
    )

    sector = materialize_market(_market_batch(), topics)["overview"]["hot_sectors"][0]

    assert sector["coverage"] == {
        "expected": 3,
        "valid": 0,
        "excluded": 0,
        "missing": 3,
    }
    assert sector["reason"] == "topic_coverage_unavailable"


@pytest.mark.parametrize(
    "source_time",
    [datetime(2026, 9, 8, 18, tzinfo=UTC), None],
)
def test_topic_metrics_use_market_as_of_as_the_knowledge_cutoff(source_time):
    from topic_factories import topic_batch, topic_definition, topic_membership

    from dragon_radar.readmodels.market import materialize_market

    market = _market_batch().model_copy(
        update={
            "as_of": datetime(2026, 9, 8, 10, tzinfo=UTC),
        }
    )
    topics = topic_batch(
        as_of=datetime(2026, 9, 8, 18, tzinfo=UTC),
        source_time=source_time,
        topics=(
            topic_definition(),
            topic_definition(
                "topic-late",
                name="Late Topic",
                available_at=datetime(2026, 9, 8, 18, tzinfo=UTC),
            ),
        ),
        memberships=(
            topic_membership(),
            topic_membership(
                security_id="B",
                available_at=datetime(2026, 9, 8, 18, tzinfo=UTC),
            ),
            topic_membership(
                topic_id="topic-late",
                available_at=datetime(2026, 9, 8, 18, tzinfo=UTC),
            ),
        ),
    )

    result = materialize_market(market, topics)

    assert [sector["sector_id"] for sector in result["sector_flow"]["sectors"]] == [
        "topic-a"
    ]
    assert result["sector_flow"]["sectors"][0]["member_count"] == 1
    assert result["sector_flow"]["sectors"][0]["reason"] == (
        "topic_coverage_unavailable"
    )
    assert result["sector_flow"]["topic_basis"] == {
        "source": topics.source_id,
        "source_version": topics.source_version,
        "as_of": topics.as_of,
        "source_time": source_time,
        "cutoff": market.as_of,
    }
    assert result["overview"]["topic_basis"] == result["sector_flow"]["topic_basis"]


def test_historical_topic_analysis_requires_historical_coverage_cell():
    from topic_factories import topic_batch

    from dragon_radar.readmodels.market import materialize_market

    topics = topic_batch()
    topics = topics.model_copy(
        update={
            "coverage_cells": tuple(
                cell
                for cell in topics.coverage_cells
                if cell.trade_date == topics.trade_date
            )
        }
    )

    historical = materialize_market(_market_batch(), topics)["overview"][
        "historical_topic_analysis"
    ]

    assert historical == {
        "enabled": False,
        "reason": "historical_topic_coverage_unavailable",
    }


def test_fac_m01_m02_m03_follow_explicit_decimal_hand_calculations():
    from dragon_radar.readmodels.market import materialize_market

    result = materialize_market(_market_batch(), None)
    factors = result["overview"]["factors"]

    assert factors["FAC-M01"]["advancing"] == {
        "numerator": 1,
        "denominator": 3,
        "value": Decimal("0.3333"),
        "reason": None,
        "coverage": {"expected": 3, "valid": 3, "excluded": 0, "missing": 0},
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
    assert index["coverage"] == {
        "ma20": {"expected": 20, "valid": 20, "excluded": 0, "missing": 0},
        "ma60": {"expected": 60, "valid": 60, "excluded": 0, "missing": 0},
        "return20": {"expected": 20, "valid": 20, "excluded": 0, "missing": 0},
    }
    assert index["close_over_ma20_reason"] is None
    assert index["close_over_ma60_reason"] is None
    assert index["volatility_20_reason"] is None
    strip = result["overview"]["indexes"][0]
    assert strip["last"] == closes[-1]
    assert strip["change_ratio"] == Decimal("0.01")
    assert strip["amount"] == Decimal("1000")


@pytest.mark.parametrize(
    (
        "session_count",
        "ma20_available",
        "ma60_available",
        "volatility_available",
        "return_coverage",
    ),
    [
        (20, True, False, False, 19),
        (21, True, False, True, 20),
        (59, True, False, True, 20),
        (60, True, True, True, 20),
    ],
)
def test_fac_m03_windows_are_independent_at_session_boundaries(
    session_count,
    ma20_available,
    ma60_available,
    volatility_available,
    return_coverage,
):
    from dragon_radar.readmodels.market import materialize_market

    index = materialize_market(_market_batch(session_count=session_count), None)[
        "overview"
    ]["factors"]["FAC-M03"]["indexes"][0]

    assert (index["close_over_ma20"] is not None) is ma20_available
    assert (index["close_over_ma60"] is not None) is ma60_available
    assert (index["volatility_20"] is not None) is volatility_available
    assert index["coverage"]["ma20"] == {
        "expected": 20,
        "valid": 20,
        "excluded": 0,
        "missing": 0,
    }
    assert index["coverage"]["ma60"] == {
        "expected": 60,
        "valid": min(session_count, 60),
        "excluded": 0,
        "missing": max(60 - session_count, 0),
    }
    assert index["coverage"]["return20"] == {
        "expected": 20,
        "valid": return_coverage,
        "excluded": 0,
        "missing": 20 - return_coverage,
    }
    assert index["close_over_ma20_reason"] is None
    assert index["close_over_ma60_reason"] == (
        None if ma60_available else "requires_60_consecutive_index_closes"
    )
    assert index["volatility_20_reason"] == (
        None if volatility_available else "requires_21_consecutive_index_closes"
    )


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
        "schema",
        "schema_version",
        "overview",
        "sector_flow",
        "sentiment",
        "heatmap",
        "search",
    }
    flat_members = market.payload["overview"]["breadth"]["members"]["flat"]
    assert [dict(member) for member in flat_members] == [
        {
            "security_id": "A",
            "code": "000001",
            "name": "Synthetic Alpha",
        }
    ]

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
async def test_legacy_market_jsonb_projects_overview_and_controls_new_views(
    market_publication, database
):
    from dragon_radar.http.app import create_workbench_app

    record, _publication_repository = market_publication
    legacy_payload = {
        "advancing": 1,
        "declining": 1,
        "unchanged": 1,
        "returns": [
            {
                "security_id": "A",
                "value": "0.1000",
                "basis": "explicit_previous_close",
                "warnings": [],
            }
        ],
        "exclusions": [],
    }
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE publication_results "
            "DISABLE TRIGGER publication_results_retention"
        )
        connection.execute(
            update(publication_results)
            .where(
                publication_results.c.publication_id == record.id,
                publication_results.c.domain == "market",
            )
            .values(payload=legacy_payload)
        )
        connection.exec_driver_sql(
            "ALTER TABLE publication_results "
            "ENABLE TRIGGER publication_results_retention"
        )

    app = create_workbench_app(
        repository=PublicationRepository(database, _publication_repository.market)
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        overview = await client.get(
            "/api/market/overview", params={"snapshot_id": str(record.id)}
        )
        unavailable = [
            await client.get(path, params={"snapshot_id": str(record.id)})
            for path in (
                "/api/market/sector-flow",
                "/api/market/sentiment",
                "/api/market/heatmap",
            )
        ]
        unavailable.append(
            await client.get(
                "/api/search",
                params={
                    "q": "A",
                    "date": str(record.trade_date),
                    "snapshot_id": str(record.id),
                },
            )
        )

    assert overview.status_code == 200
    overview_body = json.loads(overview.content, parse_float=Decimal)
    assert (
        overview_body["data"]["returns"][0]["value"].as_tuple()
        == Decimal("0.1000").as_tuple()
    )
    assert overview_body["data"] == {
        **legacy_payload,
        "returns": [
            {
                **legacy_payload["returns"][0],
                "value": Decimal("0.1000"),
            }
        ],
        "coverage": overview_body["coverage"],
    }
    for response in unavailable:
        assert response.status_code == 200
        assert response.json()["data"] is None
        assert "materialization_unavailable" in response.json()["quality_flags"]


@pytest.mark.anyio
async def test_legacy_market_payload_route_contract_without_database():
    from dragon_radar.http.app import create_workbench_app
    from dragon_radar.publication.models import (
        Publication,
        PublicationResult,
        PublishedSnapshot,
    )
    from dragon_radar.storage.models import Snapshot

    publication_id = uuid4()
    market_snapshot_id = uuid4()
    payload = _market_batch()
    market_snapshot = Snapshot(
        id=market_snapshot_id,
        batch_id=uuid4(),
        revision=1,
        supersedes=None,
        published_at=payload.as_of,
        manifest={"rule_version": "legacy-v1"},
        batch=payload,
        quality_report={
            "issues": [],
            "coverage": {
                "expected": 3,
                "valid": 3,
                "legal_excluded": 0,
                "missing": 0,
            },
        },
    )
    legacy_payload = {
        "advancing": 1,
        "declining": 1,
        "unchanged": 1,
        "returns": [],
        "exclusions": [],
    }
    publication = Publication(
        id=publication_id,
        trade_date=payload.trade_date,
        version=1,
        supersedes=None,
        status="published",
        market_snapshot_id=market_snapshot_id,
        topic_snapshot_id=None,
        news_input_id=None,
        regulation_input_id=None,
        role_input_id=None,
        domains={
            "market": "ready",
            "topic": "disabled",
            "news": "disabled",
            "regulation": "disabled",
            "role": "disabled",
        },
        coverage={},
        quality_flags=(),
        command_json="{}",
        published_at=payload.as_of,
    )
    result = PublicationResult(
        publication_id=publication_id,
        domain="market",
        input_id=market_snapshot_id,
        status="ready",
        payload=legacy_payload,
        coverage={
            "expected": 3,
            "valid": 3,
            "legal_excluded": 0,
            "missing": 0,
        },
        quality_flags=(),
    )
    selected = PublishedSnapshot(publication, market_snapshot, (result,))

    class LegacyRepository:
        database = type("Database", (), {"ready": lambda self: True})()

        def get_snapshot(self, snapshot_id):
            return selected if snapshot_id == publication_id else None

        def list_snapshots(self, **_kwargs):
            return [selected]

    app = create_workbench_app(repository=LegacyRepository())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        overview = await client.get(
            "/api/market/overview", params={"snapshot_id": str(publication_id)}
        )
        sector = await client.get(
            "/api/market/sector-flow", params={"snapshot_id": str(publication_id)}
        )

    assert overview.status_code == 200
    assert overview.json()["data"] == {
        **legacy_payload,
        "coverage": overview.json()["coverage"],
    }
    assert sector.status_code == 200
    assert sector.json()["data"] is None
    assert "materialization_unavailable" in sector.json()["quality_flags"]


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
