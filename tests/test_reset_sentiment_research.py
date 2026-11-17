"""Task 28 sentiment research score contract (method sentiment-research-v1).

These are pure, database-free tests for the deterministic 0-100 research
score and its closed-set point/block shapes. Publication and HTTP wiring are
covered separately once the read model appends the research block.
"""

import json
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from uuid import uuid4

import pytest
import simplejson
from httpx import ASGITransport, AsyncClient
from test_reset_market import _market_batch

from dragon_radar.publication.models import PublicationCommand
from dragon_radar.publication.repository import PublicationRepository
from dragon_radar.publication.service import PublicationService
from dragon_radar.readmodels.sentiment_research import (
    COMPONENT_KEYS,
    METHOD_VERSION,
    PHASES,
    REASON_NO_LIMIT_EVENTS,
    REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET,
    REASON_NO_VALID_DAILY_RETURNS,
    RESEARCH_ONLY,
    WEIGHT,
    ResearchBlockError,
    build_component,
    build_research_point,
    materialize_sentiment_research,
    parse_archived_research_block,
    phase_for,
    score_components,
)

DAY = date(2026, 9, 8)


def test_anchor_ratios_score_70_00_repair():
    score, phase, reasons = score_components(
        Decimal("0.6"), Decimal("1"), Decimal("0.5")
    )

    assert score == Decimal("70.00")
    assert phase == "repair"
    assert reasons == ()


def test_score_rounds_half_up_not_banker_s():
    score, _phase, _reasons = score_components(
        Decimal("0.12"), Decimal("0.12"), Decimal("0.12015")
    )

    # 100 * 0.36015 / 3 = 12.005 -> half-up must yield 12.01, not 12.00.
    assert score == Decimal("12.01")
    assert score.as_tuple().exponent == -2


@pytest.mark.parametrize(
    ("raw_score", "phase"),
    [
        (Decimal("0"), "ice"),
        (Decimal("19.99"), "ice"),
        (Decimal("20"), "ebb"),
        (Decimal("39.99"), "ebb"),
        (Decimal("40"), "divergent"),
        (Decimal("59.99"), "divergent"),
        (Decimal("60"), "repair"),
        (Decimal("79.99"), "repair"),
        (Decimal("80"), "climax"),
        (Decimal("100"), "climax"),
    ],
)
def test_phase_boundaries_are_contiguous_half_open_buckets(raw_score, phase):
    assert phase_for(raw_score) == phase
    assert PHASES == ("ice", "ebb", "divergent", "repair", "climax")


@pytest.mark.parametrize("raw_score", [Decimal("-0.01"), Decimal("100.01")])
def test_phase_rejects_scores_outside_the_0_100_contract(raw_score):
    with pytest.raises(ValueError):
        phase_for(raw_score)


@pytest.mark.parametrize("raw_score", [Decimal("0.001"), Decimal("70.005")])
def test_phase_rejects_scores_below_the_two_decimal_contract(raw_score):
    with pytest.raises(ResearchBlockError):
        phase_for(raw_score)


def test_score_uses_unrounded_ratios_but_sample_survives_display_quantization():
    with localcontext() as context:
        context.prec = 28
        raw = (
            Decimal(1) / Decimal(3),
            Decimal(2) / Decimal(3),
            Decimal(5) / Decimal(6),
        )

    score, _phase, _reasons = score_components(*raw)
    assert score == Decimal("61.11")

    display_scale = Decimal("0.0001")
    display = tuple(
        value.quantize(display_scale, rounding=ROUND_HALF_UP) for value in raw
    )
    score_from_display = (
        Decimal(100) * sum(display, Decimal(0)) / Decimal(3)
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    assert score_from_display == score


@pytest.mark.parametrize(
    ("missing_index", "reason"),
    [
        (0, REASON_NO_VALID_DAILY_RETURNS),
        (1, REASON_NO_LIMIT_EVENTS),
        (2, REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET),
    ],
)
def test_missing_component_nulls_score_without_redistributing_weight(
    missing_index, reason
):
    ratios = [Decimal("0.6"), Decimal("1"), Decimal("0.5")]
    ratios[missing_index] = None

    score, phase, reasons = score_components(*ratios)

    assert score is None
    assert phase is None
    assert reasons == (reason,)


def test_multiple_missing_components_list_every_reason_in_fixed_order():
    score, phase, reasons = score_components(None, None, None)

    assert score is None
    assert phase is None
    assert reasons == (
        REASON_NO_VALID_DAILY_RETURNS,
        REASON_NO_LIMIT_EVENTS,
        REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET,
    )


@pytest.mark.parametrize("bad_value", [0.5, True, False, 1, "0.5"])
def test_score_rejects_non_decimal_component_ratios(bad_value):
    with pytest.raises(ValueError):
        score_components(bad_value, Decimal("1"), Decimal("0.5"))


@pytest.mark.parametrize("bad_value", [Decimal("-0.0001"), Decimal("1.0001")])
def test_score_rejects_out_of_range_ratios(bad_value):
    with pytest.raises(ValueError):
        score_components(bad_value, Decimal("1"), Decimal("0.5"))


@pytest.mark.parametrize("bad_value", ["NaN", "Infinity", "-Infinity"])
def test_score_rejects_non_finite_decimals(bad_value):
    with pytest.raises(ValueError):
        score_components(Decimal(bad_value), Decimal("1"), Decimal("0.5"))


def test_build_component_freezes_available_shape_with_equal_weight():
    component = build_component(1, 2, REASON_NO_LIMIT_EVENTS)

    assert component == {
        "value": Decimal("0.5000"),
        "numerator": 1,
        "denominator": 2,
        "weight": {"numerator": 1, "denominator": 3},
        "reason": None,
    }
    assert component["weight"] == WEIGHT
    assert WEIGHT == {"numerator": 1, "denominator": 3}


def test_build_component_zero_denominator_is_unavailable_without_zero_fill():
    coverage = {"expected": 3, "valid": 0, "excluded": 1, "missing": 2}
    component = build_component(
        0, 0, REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET, coverage=coverage
    )

    assert component["value"] is None
    assert component["numerator"] == 0
    assert component["denominator"] == 0
    assert component["reason"] == (REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET)
    assert component["coverage"] == coverage


def test_build_component_omits_coverage_when_not_supplied():
    component = build_component(3, 3, REASON_NO_LIMIT_EVENTS)
    assert set(component) == {"value", "numerator", "denominator", "weight", "reason"}


@pytest.mark.parametrize(
    ("numerator", "denominator"),
    [(-1, 2), (3, 2), (1, -1), (True, 2)],
)
def test_build_component_rejects_invalid_counts(numerator, denominator):
    with pytest.raises(ValueError):
        build_component(numerator, denominator, REASON_NO_LIMIT_EVENTS)


def _anchor_components():
    return {
        "breadth": build_component(6, 10, REASON_NO_VALID_DAILY_RETURNS),
        "limit_balance": build_component(3, 3, REASON_NO_LIMIT_EVENTS),
        "promotion": build_component(
            1, 2, REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET
        ),
    }


def test_research_point_assembles_closed_set_from_counts():
    point = build_research_point(DAY, _anchor_components())

    assert point == {
        "trade_date": DAY,
        "score": Decimal("70.00"),
        "phase": "repair",
        "components": _anchor_components(),
        "reasons": [],
    }
    assert list(point["components"]) == list(COMPONENT_KEYS)
    assert set(point) == {"trade_date", "score", "phase", "components", "reasons"}


def test_research_point_keeps_component_reasons_when_score_unavailable():
    components = _anchor_components()
    components["limit_balance"] = build_component(0, 0, REASON_NO_LIMIT_EVENTS)

    point = build_research_point(DAY, components)

    assert point["score"] is None
    assert point["phase"] is None
    assert point["reasons"] == [REASON_NO_LIMIT_EVENTS]
    assert point["components"]["limit_balance"]["value"] is None


def test_research_point_requires_exactly_three_components():
    components = _anchor_components()

    with pytest.raises(ValueError):
        build_research_point(DAY, {key: components[key] for key in ("breadth",)})
    extra = {**components, "momentum": object()}
    with pytest.raises(ValueError):
        build_research_point(DAY, extra)


def _frozen_point():
    return build_research_point(DAY, _anchor_components())


def test_stored_point_rejects_sub_cent_score_scale():
    point = _frozen_point()
    point["score"] = Decimal("70.005")

    with pytest.raises(ResearchBlockError):
        materialize_sentiment_research([point])


def test_stored_point_rejects_component_value_below_four_decimal_scale():
    point = _frozen_point()
    point["components"]["breadth"]["value"] = Decimal("0.60005")

    with pytest.raises(ResearchBlockError):
        materialize_sentiment_research([point])


def test_stored_point_rejects_bool_inside_the_equal_weight():
    point = _frozen_point()
    point["components"]["breadth"]["weight"] = {
        "numerator": True,
        "denominator": 3,
    }

    with pytest.raises(ResearchBlockError):
        materialize_sentiment_research([point])


def test_stored_point_rejects_non_integer_coverage_counts():
    components = _anchor_components()
    components["promotion"] = build_component(
        1,
        2,
        REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET,
        coverage={"expected": 2, "valid": 1, "excluded": 0, "missing": 1},
    )
    point = build_research_point(DAY, components)
    point["components"]["promotion"]["coverage"]["missing"] = "1"

    with pytest.raises(ResearchBlockError):
        materialize_sentiment_research([point])


def _archived_wire_point():
    point = _frozen_point()
    point["trade_date"] = DAY.isoformat()
    point["score"] = "70.00"
    return point


def test_parse_archived_block_accepts_decimal_string_wire_shapes():
    parsed = parse_archived_research_block(
        {
            "method_version": METHOD_VERSION,
            "research_only": True,
            "series": [_archived_wire_point()],
        }
    )

    assert parsed["series"][0]["trade_date"] == DAY
    assert parsed["series"][0]["score"] == Decimal("70.00")


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda point: point.update({"score": "70.005"}),
        lambda point: point["components"]["breadth"].update(
            {"value": Decimal("0.60005")}
        ),
        lambda point: point["components"]["breadth"]["weight"].update(
            {"numerator": True}
        ),
        lambda point: point["components"]["promotion"].update(
            {
                "coverage": {
                    "expected": 2,
                    "valid": "1",
                    "excluded": 0,
                    "missing": 1,
                }
            }
        ),
    ],
)
def test_parse_archived_block_fail_closes_stored_precision_tampering(corrupt):
    point = _archived_wire_point()
    # promotion carries no coverage in the anchor fixture; give it the same
    # integer coverage the publication builder would have stored.
    point["components"]["promotion"]["coverage"] = {
        "expected": 2,
        "valid": 1,
        "excluded": 0,
        "missing": 1,
    }
    corrupt(point)

    with pytest.raises(ResearchBlockError):
        parse_archived_research_block(
            {
                "method_version": METHOD_VERSION,
                "research_only": True,
                "series": [point],
            }
        )


def test_materialized_block_is_self_describing_and_byte_deterministic():
    first = materialize_sentiment_research(
        [build_research_point(DAY, _anchor_components())]
    )
    second = materialize_sentiment_research(
        [build_research_point(DAY, _anchor_components())]
    )

    assert set(first) == {"method_version", "research_only", "series"}
    assert first["method_version"] == METHOD_VERSION == "sentiment-research-v1"
    assert first["research_only"] is RESEARCH_ONLY
    assert RESEARCH_ONLY is True
    assert first == second

    def encode(block):
        return simplejson.dumps(
            block,
            use_decimal=True,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=lambda value: value.isoformat(),
        ).encode("utf-8")

    assert encode(first) == encode(second)


# ---------------------------------------------------------------------------
# Task B: publication-time materialization appends the block (no schema bump)
# ---------------------------------------------------------------------------


def test_market_payload_appends_research_block_without_container_schema_bump():
    from dragon_radar.readmodels.market import (
        MARKET_READ_MODEL_SCHEMA,
        MARKET_READ_MODEL_SCHEMA_VERSION,
        materialize_market,
    )

    result = materialize_market(_market_batch(), None)

    assert result["schema"] == MARKET_READ_MODEL_SCHEMA
    assert result["schema_version"] == MARKET_READ_MODEL_SCHEMA_VERSION == 1
    assert {
        "schema",
        "schema_version",
        "overview",
        "sector_flow",
        "sentiment",
        "heatmap",
        "search",
    } <= set(result)

    block = result["sentiment_research"]
    assert set(block) == {"method_version", "research_only", "series"}
    assert block["method_version"] == METHOD_VERSION
    assert block["research_only"] is True


def test_research_headline_matches_hand_counts_from_same_day_facts():
    from dragon_radar.readmodels.market import materialize_market

    payload = _market_batch()
    headline = materialize_market(payload, None)["sentiment_research"]["series"][-1]

    assert headline["trade_date"] == payload.trade_date
    assert headline["score"] == Decimal("44.44")
    assert headline["phase"] == "divergent"
    assert headline["reasons"] == []
    assert headline["components"] == {
        "breadth": {
            "value": Decimal("0.3333"),
            "numerator": 1,
            "denominator": 3,
            "weight": WEIGHT,
            "reason": None,
            "coverage": {"expected": 3, "valid": 3, "excluded": 0, "missing": 0},
        },
        "limit_balance": {
            "value": Decimal("0.5000"),
            "numerator": 1,
            "denominator": 2,
            "weight": WEIGHT,
            "reason": None,
        },
        "promotion": {
            "value": Decimal("0.5000"),
            "numerator": 1,
            "denominator": 2,
            "weight": WEIGHT,
            "reason": None,
            "coverage": {"expected": 2, "valid": 2, "excluded": 0, "missing": 0},
        },
    }


def test_research_series_has_one_point_per_session_in_ascending_order():
    from dragon_radar.readmodels.market import materialize_market

    payload = _market_batch()
    series = materialize_market(payload, None)["sentiment_research"]["series"]

    assert [point["trade_date"] for point in series] == list(payload.calendar.sessions)


def test_research_counts_share_the_single_fact_source_with_raw_sentiment():
    from dragon_radar.readmodels.market import materialize_market

    result = materialize_market(_market_batch(), None)
    raw_by_day = {item["trade_date"]: item for item in result["sentiment"]["series"]}

    for point in result["sentiment_research"]["series"]:
        raw = raw_by_day.get(point["trade_date"])
        if raw is None:
            continue
        promotion = point["components"]["promotion"]
        assert promotion["numerator"] == raw["promote_rate"]["numerator"]
        assert promotion["denominator"] == raw["promote_rate"]["denominator"]
        assert promotion["value"] == raw["promote_rate"]["value"]
        balance = point["components"]["limit_balance"]
        assert balance["numerator"] == raw["limit_up_count"]
        assert balance["denominator"] == (
            raw["limit_up_count"] + raw["limit_down_count"]
        )


def test_quiet_sessions_report_unavailable_components_without_zero_fill():
    from dragon_radar.readmodels.market import materialize_market

    series = materialize_market(_market_batch(), None)["sentiment_research"]["series"]

    first = series[0]
    assert first["score"] is None
    assert first["phase"] is None
    assert set(first["reasons"]) == {
        REASON_NO_VALID_DAILY_RETURNS,
        REASON_NO_LIMIT_EVENTS,
        REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET,
    }
    assert first["components"]["breadth"]["value"] is None
    assert first["components"]["breadth"]["coverage"]["valid"] == 0

    quiet = series[10]
    assert quiet["components"]["breadth"]["value"] == Decimal("0.0000")
    assert quiet["components"]["limit_balance"]["value"] is None
    assert quiet["components"]["limit_balance"]["reason"] == REASON_NO_LIMIT_EVENTS
    assert quiet["components"]["promotion"]["value"] is None
    assert quiet["score"] is None
    assert quiet["phase"] is None
    assert quiet["reasons"] == [
        REASON_NO_LIMIT_EVENTS,
        REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET,
    ]


def test_headline_without_limit_up_events_keeps_available_components():
    from dragon_radar.readmodels.market import materialize_market

    payload = _market_batch(include_touch=False).model_copy(
        update={"limit_up_events": ()}
    )

    headline = materialize_market(payload, None)["sentiment_research"]["series"][-1]

    assert headline["components"]["breadth"]["value"] == Decimal("0.3333")
    assert headline["components"]["limit_balance"] == {
        "value": Decimal("0.0000"),
        "numerator": 0,
        "denominator": 1,
        "weight": WEIGHT,
        "reason": None,
    }
    assert headline["components"]["promotion"]["value"] is None
    assert headline["score"] is None
    assert headline["phase"] is None
    assert headline["reasons"] == [REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET]


def _publish_market(database, repository, payload, *, key):
    snapshot = repository.publish(repository.import_batch(payload, key=key).id)
    return PublicationService(database, repository).publish(
        PublicationCommand(
            trade_date=payload.trade_date,
            market_snapshot_id=snapshot.id,
            disabled_domains=("topic", "news", "regulation", "role"),
        ),
        key=f"{key}-publication",
    )


def _market_research(database, repository, publication_id):
    stored = PublicationRepository(database, repository).list_results(publication_id)
    market = next(item for item in stored if item.domain == "market")
    return market.payload["sentiment_research"]


def test_research_block_is_immutable_after_revised_republication(database, repository):
    first_payload = _market_batch()
    first_publication = _publish_market(
        database, repository, first_payload, key="t28-market-v1"
    )
    original_block = _market_research(database, repository, first_publication.id)

    revised = first_payload.model_copy(
        update={
            "limit_up_events": tuple(
                event
                for event in first_payload.limit_up_events
                if not (
                    event.trade_date == first_payload.trade_date
                    and event.security_id == "A"
                )
            )
        }
    )
    second_publication = _publish_market(
        database, repository, revised, key="t28-market-v2"
    )
    revised_block = _market_research(database, repository, second_publication.id)

    reread_block = _market_research(database, repository, first_publication.id)
    assert reread_block == original_block
    assert revised_block != original_block
    revised_headline = revised_block["series"][-1]
    assert revised_headline["components"]["limit_balance"]["numerator"] == 0
    assert revised_headline["components"]["promotion"]["numerator"] == 0


# ---------------------------------------------------------------------------
# Task C: EX16 read-only route and two-layer unavailability
# ---------------------------------------------------------------------------


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def research_record_app(database, repository):
    from dragon_radar.http.app import create_workbench_app

    record = _publish_market(
        database, repository, _market_batch(), key="t28-research-market"
    )
    app = create_workbench_app(repository=PublicationRepository(database, repository))
    return record, app


def _body(response):
    return json.loads(response.content, parse_float=Decimal)


@pytest.mark.anyio
async def test_research_route_returns_headline_and_last_days_series(
    research_record_app,
):
    record, app = research_record_app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        default = await client.get(
            "/api/market/sentiment/research",
            params={"snapshot_id": str(record.id)},
        )
        one = await client.get(
            "/api/market/sentiment/research",
            params={"snapshot_id": str(record.id), "days": 1},
        )
        full = await client.get(
            "/api/market/sentiment/research",
            params={"date": str(record.trade_date), "days": 120},
        )
        days_zero = await client.get(
            "/api/market/sentiment/research",
            params={"snapshot_id": str(record.id), "days": 0},
        )
        days_too_large = await client.get(
            "/api/market/sentiment/research",
            params={"snapshot_id": str(record.id), "days": 121},
        )
        no_selection = await client.get("/api/market/sentiment/research")

    assert default.status_code == 200
    body = _body(default)
    assert body["snapshot_id"] == str(record.id)
    data = body["data"]
    assert set(data) == {
        "method_version",
        "research_only",
        "trade_date",
        "score",
        "phase",
        "components",
        "reasons",
        "series",
    }
    assert data["method_version"] == METHOD_VERSION
    assert data["research_only"] is True
    assert data["trade_date"] == record.trade_date.isoformat()
    assert data["score"] == Decimal("44.44")
    assert data["phase"] == "divergent"
    assert data["reasons"] == []
    assert set(data["components"]) == set(COMPONENT_KEYS)
    assert data["components"]["breadth"]["weight"] == WEIGHT

    dates = [point["trade_date"] for point in data["series"]]
    assert len(dates) == 20
    assert dates == sorted(dates)
    assert dates[-1] == record.trade_date.isoformat()
    assert data["series"][-1]["score"] == Decimal("44.44")

    assert one.status_code == 200
    one_data = _body(one)["data"]
    assert len(one_data["series"]) == 1
    assert one_data["series"][0]["trade_date"] == record.trade_date.isoformat()

    assert full.status_code == 200
    assert len(_body(full)["data"]["series"]) == 61

    for rejected in (days_zero, days_too_large, no_selection):
        assert rejected.status_code == 400
        assert rejected.json()["code"] == "INVALID_PARAM"


def _in_memory_selected(payload):
    from dragon_radar.publication.models import (
        Publication,
        PublicationResult,
        PublishedSnapshot,
    )
    from dragon_radar.publication.service import _stored_json
    from dragon_radar.storage.models import Snapshot

    market_payload = _market_batch()
    publication_id = uuid4()
    market_snapshot_id = uuid4()
    snapshot = Snapshot(
        id=market_snapshot_id,
        batch_id=uuid4(),
        revision=1,
        supersedes=None,
        published_at=market_payload.as_of,
        manifest={"rule_version": "market-v1"},
        batch=market_payload,
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
    publication = Publication(
        id=publication_id,
        trade_date=market_payload.trade_date,
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
        published_at=market_payload.as_of,
    )
    result = PublicationResult(
        publication_id=publication_id,
        domain="market",
        input_id=market_snapshot_id,
        status="ready",
        payload=_stored_json(payload),
        coverage={
            "expected": 3,
            "valid": 3,
            "legal_excluded": 0,
            "missing": 0,
        },
        quality_flags=(),
    )
    return PublishedSnapshot(publication, snapshot, (result,))


class _StaticRepository:
    database = type("Database", (), {"ready": lambda self: True})()

    def __init__(self, selected=None):
        self._selected = selected

    def get_snapshot(self, snapshot_id):
        return self._selected

    def list_snapshots(self, **_kwargs):
        return [self._selected] if self._selected is not None else []


@pytest.mark.anyio
async def test_missing_research_block_is_unavailable_without_recomputation(
    monkeypatch,
):
    from dragon_radar.http.app import create_workbench_app
    from dragon_radar.readmodels.market import materialize_market

    payload = materialize_market(_market_batch(), None)
    del payload["sentiment_research"]

    def forbid_recomputation(*_args, **_kwargs):
        raise AssertionError("read route must not recompute the read model")

    monkeypatch.setattr(
        "dragon_radar.readmodels.market.materialize_market",
        forbid_recomputation,
    )
    app = create_workbench_app(
        repository=_StaticRepository(_in_memory_selected(payload))
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/market/sentiment/research",
            params={"date": "2026-09-08"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["data"] is None
    assert "materialization_unavailable" in body["quality_flags"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "corrupt",
    [
        lambda payload: payload["sentiment_research"].update(
            {"method_version": "sentiment-research-v9"}
        ),
        lambda payload: payload["sentiment_research"].update({"research_only": False}),
        lambda payload: payload["sentiment_research"]["series"][-1].update(
            {"phase": "moon"}
        ),
        lambda payload: payload["sentiment_research"]["series"][-1].update(
            {"score": "oops"}
        ),
        lambda payload: payload["sentiment_research"]["series"][-1]["components"].pop(
            "promotion"
        ),
        lambda payload: payload["sentiment_research"]["series"][-1].update(
            {"unexpected": True}
        ),
        lambda payload: payload["sentiment_research"]["series"][-1]["components"][
            "breadth"
        ].update({"weight": {"numerator": 1, "denominator": 2}}),
        lambda payload: payload["sentiment_research"]["series"][-1].update(
            {"score": "70.005"}
        ),
        lambda payload: payload["sentiment_research"]["series"][-1]["components"][
            "breadth"
        ].update({"value": "0.60005"}),
        lambda payload: payload["sentiment_research"]["series"][-1]["components"][
            "breadth"
        ]["weight"].update({"numerator": True}),
        lambda payload: payload["sentiment_research"]["series"][-1]["components"][
            "promotion"
        ]["coverage"].update({"valid": "3"}),
        lambda payload: payload["sentiment_research"]["series"][-1].update(
            {
                "trade_date": payload["sentiment_research"]["series"][-1]["trade_date"]
                + timedelta(days=1)
            }
        ),
        lambda payload: payload["sentiment_research"].update({"series": []}),
        lambda payload: payload.update({"sentiment_research": []}),
    ],
)
async def test_corrupt_research_block_returns_503(corrupt):
    from dragon_radar.http.app import create_workbench_app
    from dragon_radar.readmodels.market import materialize_market

    payload = materialize_market(_market_batch(), None)
    corrupt(payload)
    app = create_workbench_app(
        repository=_StaticRepository(_in_memory_selected(payload))
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/market/sentiment/research",
            params={"date": "2026-09-08"},
        )

    assert response.status_code == 503
    assert response.json()["code"] == "DATA_UNAVAILABLE"


@pytest.mark.anyio
async def test_unpublished_snapshot_is_empty_envelope_and_post_rejected():
    from dragon_radar.http.app import create_workbench_app

    app = create_workbench_app(repository=_StaticRepository())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        missing = await client.get(
            "/api/market/sentiment/research",
            params={"snapshot_id": str(uuid4())},
        )
        posted = await client.post(
            "/api/market/sentiment/research",
            params={"snapshot_id": str(uuid4())},
        )

    assert missing.status_code == 200
    body = missing.json()
    assert body["data"] is None
    assert body["quality_flags"] == ["no_data"]
    # The workbench read-only middleware rejects every write method before
    # routing, so POST is not accepted (TR-C.5: 405/not mounted on writes).
    assert posted.status_code == 403
    assert posted.json()["code"] == "FORBIDDEN"
