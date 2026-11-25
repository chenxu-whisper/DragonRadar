"""IH1 fixed synthetic indices; no provider, database or current-data fallback."""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from factories import DAYS, NOW, bar, batch, state
from pydantic import ValidationError

from dragon_radar.domain.models import DailyBar, ExchangeCalendar
from dragon_radar.domain.quality import bar_issues, validate_batch


def index_bar(day=None, **changes):
    return bar(
        "IDX",
        trade_date=day,
        **{
            "price_unit": "point",
            "adjustment_version": "raw-index-v1",
            "volume_unit": "not_applicable",
            "volume": None,
            "amount": None,
            "index_basis": {
                "series_type": "price",
                "series_reference": "synthetic-index-series-v1",
                "price_unit": "point",
                "amount_currency": None,
                "volume_unit": "not_applicable",
            },
            **changes,
        },
    )


def index_batch(**changes):
    return batch(
        **{
            "security_states": (state(), state("IDX", security_type="index")),
            "bars": (*batch().bars, *(index_bar(day) for day in DAYS)),
            **changes,
        }
    )


def test_explicit_index_units_preserve_stock_contract():
    payload = index_batch()
    assert validate_batch(payload, now=NOW).publishable
    assert "unit_contract" in {
        issue.code for issue in bar_issues(bar(price_unit="point"), payload)
    }
    with pytest.raises(ValidationError):
        payload.bars[-1].index_basis.series_type = "total_return"


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"close": None}, "missing_price"),
        ({"close": Decimal("NaN")}, "nonfinite"),
        ({"close": Decimal("Infinity")}, "nonfinite"),
        ({"close": Decimal("0")}, "invalid_price"),
        ({"amount": Decimal("-1")}, "negative_amount"),
        ({"amount": Decimal("1")}, "index_amount_currency"),
        ({"volume": Decimal("1")}, "index_volume"),
        ({"adjustment_factor": Decimal("2")}, "index_adjustment"),
        ({"adjustment_version": "a1"}, "index_adjustment"),
        ({"available_at": NOW + timedelta(seconds=1)}, "late_bar"),
        (
            {"open": Decimal("11"), "low": Decimal("12"), "high": Decimal("13")},
            "invalid_ohlc",
        ),
    ],
)
def test_index_quarantines_invalid_supplied_bars(changes, code):
    payload = index_batch()
    assert code in {issue.code for issue in bar_issues(index_bar(**changes), payload)}


def test_index_basis_cannot_authorize_stock_or_unknown_identity():
    payload = batch()
    candidate = index_bar()
    assert "index_identity" in {issue.code for issue in bar_issues(candidate, payload)}
    raw = candidate.model_dump()
    raw["security_id"] = "A"
    assert "index_identity" in {
        issue.code for issue in bar_issues(DailyBar.model_validate(raw), payload)
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("series_type", "total_return"),
        ("series_reference", "synthetic-other-series"),
        ("amount_currency", "CNY"),
    ],
)
def test_one_index_cannot_mix_series_or_units(key, value):
    raw = index_bar(DAYS[0]).model_dump()
    raw["index_basis"][key] = value
    payload = index_batch()
    payload = index_batch(
        bars=(
            *payload.bars[:-4],
            DailyBar.model_validate(raw),
            *payload.bars[-3:],
        )
    )
    assert "index_series_mismatch" in {
        issue.code for issue in validate_batch(payload, now=NOW).issues
    }


def test_legacy_index_units_keep_existing_quotes_eligible():
    payload = batch(
        security_states=(state(), state("IDX", security_type="index")),
        bars=(*batch().bars, *(bar("IDX", day) for day in DAYS)),
    )
    assert validate_batch(payload, now=NOW).publishable


@pytest.mark.parametrize(
    "key,value",
    [
        ("series_type", "unknown"),
        ("price_unit", "currency/share"),
        ("volume_unit", "point"),
    ],
)
def test_index_basis_rejects_unsupported_provider_declarations(key, value):
    basis = dict(index_bar().index_basis.model_dump(), **{key: value})
    with pytest.raises(ValidationError):
        index_bar(index_basis=basis)


@pytest.mark.parametrize("series_type", ["price", "total_return"])
@pytest.mark.parametrize("volume_unit", ["share", "lot"])
def test_declared_index_currency_and_quantity_units_are_preserved(
    series_type, volume_unit
):
    basis = dict(
        index_bar().index_basis.model_dump(),
        series_type=series_type,
        volume_unit=volume_unit,
        amount_currency="CNY",
    )
    payload = index_batch(
        bars=(
            *batch().bars,
            index_bar(
                index_basis=basis,
                volume_unit=volume_unit,
                volume=Decimal("10"),
                amount=Decimal("100"),
            ),
        )
    )
    assert validate_batch(payload, now=NOW).publishable
    entry = history_block(payload)["indexes"][0]
    assert entry["index_basis"] == basis
    assert (
        entry["points"][-1]["volume"] == "10" and entry["points"][-1]["amount"] == "100"
    )


def long_index_batch():
    sessions = tuple(date(2025, 1, 1) + timedelta(days=i) for i in range(260))
    cutoff = datetime(2026, 9, 9, 12, tzinfo=UTC)
    return index_batch(
        trade_date=sessions[-1],
        as_of=cutoff,
        calendar=ExchangeCalendar(exchange="TEST", version="c1", sessions=sessions),
        security_states=(
            state(
                "IDX",
                security_type="index",
                effective_from=sessions[0],
                available_at=datetime(2024, 1, 1, tzinfo=UTC),
            ),
        ),
        expected_security_ids=("IDX",),
        bars=tuple(index_bar(day) for day in sessions if day != sessions[12]),
    )


MARKET_ID = UUID("00000000-0000-4000-8000-000000000001")
ARCHIVE_ID = UUID("00000000-0000-4000-8000-000000000002")
PUB_ID = UUID("00000000-0000-4000-8000-000000000003")


def history_block(payload=None):
    from dragon_radar.readmodels.index_history import materialize_index_history

    return materialize_index_history(
        payload or index_batch(),
        market_snapshot_id=MARKET_ID,
        source_archive_id=ARCHIVE_ID,
        source_archive_digest="a" * 64,
    )


def test_archive_retains_250_calendar_sessions_including_gaps():
    from dragon_radar.readmodels.index_history import parse_archived_index_history

    payload = long_index_batch()
    parsed = parse_archived_index_history(history_block(payload))
    entry = parsed.indexes[0]
    assert len(entry.points) == 250
    assert entry.points[0].trade_date == payload.calendar.sessions[10]
    assert entry.points[2].chart_type == "gap"
    assert entry.points[2].close is None
    assert entry.coverage.model_dump() == {
        "expected": 250,
        "valid": 249,
        "excluded": 0,
        "missing": 1,
    }


def test_archive_preserves_candle_line_precision_and_legacy_basis():
    raw = index_bar(
        DAYS[0],
        close=Decimal("10.12345678901234567890123456789"),
        open=Decimal("10"),
        high=Decimal("11"),
        low=Decimal("9"),
    )
    payload = index_batch(bars=(*batch().bars, raw, index_bar(DAYS[-1])))
    block = history_block(payload)
    assert (
        block["indexes"][0]["points"][0]["close"] == "10.12345678901234567890123456789"
    )
    assert [point["chart_type"] for point in block["indexes"][0]["points"]] == [
        "candle",
        "gap",
        "gap",
        "line",
    ]
    legacy = batch(
        security_states=(state("IDX", security_type="index"),),
        expected_security_ids=("IDX",),
        bars=tuple(bar("IDX", day) for day in DAYS),
    )
    entry = history_block(legacy)["indexes"][0]
    assert entry["reason"] == "index_basis_unavailable"
    assert entry["index_basis"] is None and entry["points"] == []


def test_large_exact_decimal_archive_round_trips_without_exponent():
    from dragon_radar.readmodels.index_history import parse_archived_index_history

    payload = index_batch(bars=(*batch().bars, index_bar(close=Decimal("1E+30"))))
    block = history_block(payload)
    parsed = parse_archived_index_history(block)
    assert parsed.indexes[0].points[-1].close == Decimal("1E+30")
    assert "E" not in block["indexes"][0]["points"][-1]["close"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update(schema_version=True),
        lambda b: b.update(schema="bad"),
        lambda b: b.update(calendar_sessions=[]),
        lambda b: b.update(source_archive_digest="bad"),
        lambda b: b["indexes"][0]["coverage"].update(valid=0),
        lambda b: b["indexes"][0]["coverage"].update(excluded=False),
        lambda b: b["indexes"][0]["coverage"].update(excluded=0.0),
        lambda b: b["indexes"][0]["identity"].update(effective_from="20260903"),
        lambda b: b.update(as_of="20260909T120000Z"),
        lambda b: b["indexes"][0]["points"][0].update(close=10.0),
        lambda b: b["indexes"][0]["points"][0].update(close="NaN"),
        lambda b: b["indexes"][0]["points"][0].update(trade_date="20260903"),
        lambda b: b["indexes"][0]["points"][0].update(source_version="other"),
        lambda b: b["indexes"][0]["points"][0].update(
            available_at="2027-01-01T00:00:00Z"
        ),
        lambda b: b["indexes"].append(deepcopy(b["indexes"][0])),
        lambda b: b["indexes"][0]["points"].append(
            deepcopy(b["indexes"][0]["points"][0])
        ),
        lambda b: b["indexes"][0]["points"][0].update(open="11", high="9", low="8"),
    ],
)
def test_strict_archive_parser_rejects_corrupt_blocks(mutate):
    from dragon_radar.readmodels.index_history import (
        IndexHistoryError,
        parse_archived_index_history,
    )

    block = history_block()
    mutate(block)
    with pytest.raises(IndexHistoryError):
        parse_archived_index_history(block)


def test_supplied_bad_or_duplicate_bars_cannot_become_history_gaps():
    from dragon_radar.readmodels.index_history import IndexHistoryError

    for bars in (
        (*index_batch().bars, index_bar(DAYS[0])),
        (*batch().bars, index_bar(close=None)),
        (*batch().bars, index_bar(available_at=NOW + timedelta(seconds=1))),
    ):
        with pytest.raises(IndexHistoryError):
            history_block(index_batch(bars=bars))


def test_archive_window_uses_calendar_and_keeps_three_distinct_boundaries():
    from dragon_radar.readmodels.index_history import (
        parse_archived_index_history,
        project_index_history,
    )

    block = parse_archived_index_history(history_block())
    data, reason = project_index_history(
        block,
        "IDX",
        date(2026, 9, 5),
        date(2026, 9, 8),
    )
    assert reason is None
    assert data["requested"] == {"start": date(2026, 9, 5), "end": DAYS[-1]}
    assert data["adjusted"] == {"start": DAYS[-2], "end": DAYS[-1]}
    assert data["available"] == {"start": DAYS[-2], "end": DAYS[-1]}
    assert project_index_history(block, "IDX", date(2026, 9, 5), date(2026, 9, 6)) == (
        None,
        "no_sessions",
    )
    long = parse_archived_index_history(history_block(long_index_batch()))
    data, _ = project_index_history(
        long, "IDX", long.calendar_sessions[0], long.calendar_sessions[19]
    )
    assert data["adjusted"]["start"] == long.calendar_sessions[0]
    assert data["available"]["start"] == long.calendar_sessions[10]
    with pytest.raises(ValueError):
        project_index_history(long, "IDX", long.calendar_sessions[0], long.trade_date)


def test_archived_all_gap_window_has_partitioned_missing_coverage():
    from dragon_radar.readmodels.index_history import (
        parse_archived_index_history,
        project_index_history,
    )

    payload = long_index_batch()
    payload = type(payload).model_validate(
        dict(payload.model_dump(), bars=[payload.bars[0], payload.bars[-1]])
    )
    assert validate_batch(payload, now=NOW).publishable
    block = parse_archived_index_history(history_block(payload))
    data, reason = project_index_history(
        block, "IDX", payload.calendar.sessions[10], payload.calendar.sessions[20]
    )
    assert reason is None and data["chart_type"] == "gap"
    assert data["coverage"] == {
        "expected": 11,
        "valid": 0,
        "excluded": 0,
        "missing": 11,
    }
    assert all(point["close"] is None for point in data["points"])


def test_p1_bytes_and_provenance_do_not_change_when_p2_is_built():
    import json

    p1 = history_block()
    frozen = json.dumps(p1, sort_keys=True)
    p2 = history_block(index_batch(provenance="retrospective"))
    assert p2["provenance"] == "retrospective"
    assert json.dumps(p1, sort_keys=True) == frozen


def test_market_materializer_accepts_original_archive_coordinates():
    from dragon_radar.readmodels.market import materialize_market

    payload = materialize_market(
        index_batch(),
        None,
        market_snapshot_id=MARKET_ID,
        source_archive_id=ARCHIVE_ID,
        source_archive_digest="a" * 64,
    )
    assert payload["index_history"] == history_block()
    assert payload["overview"]["indexes"][0]["last"] == Decimal("10")
    assert payload["overview"]["factors"]["FAC-M03"]["indexes"][0]["close"] == Decimal(
        "10"
    )


def test_materializer_missing_calendar_and_partial_coordinates_fail_closed():
    from dragon_radar.readmodels.index_history import IndexHistoryError
    from dragon_radar.readmodels.market import materialize_market

    with pytest.raises(IndexHistoryError):
        history_block(index_batch(trade_date=date(2026, 9, 9)))
    with pytest.raises(ValueError):
        materialize_market(index_batch(), None, market_snapshot_id=MARKET_ID)


def published_snapshot(
    payload=None,
    stored=None,
    *,
    pub_id=PUB_ID,
    market_id=MARKET_ID,
    archive_id=ARCHIVE_ID,
):
    import json

    from dragon_radar.publication.models import (
        Publication,
        PublicationResult,
        PublishedSnapshot,
    )
    from dragon_radar.storage.models import Snapshot

    payload = payload or index_batch()
    market = Snapshot(
        id=market_id,
        batch_id=archive_id,
        revision=1,
        supersedes=None,
        published_at=NOW,
        manifest={"rule_version": "test-v1", "payload_hash": "a" * 64},
        batch=payload,
        quality_report={
            "coverage": {"expected": 1, "valid": 1, "legal_excluded": 0, "missing": 0},
            "issues": [],
        },
    )
    publication = Publication(
        id=pub_id,
        trade_date=payload.trade_date,
        version=1,
        supersedes=None,
        status="published",
        market_snapshot_id=market_id,
        topic_snapshot_id=None,
        news_input_id=None,
        regulation_input_id=None,
        role_input_id=None,
        domains={"market": "ready"},
        coverage={},
        quality_flags=(),
        command_json="{}",
        published_at=NOW,
    )
    from dragon_radar.readmodels.market import materialize_market

    archived = (
        stored
        if stored is not None
        else materialize_market(
            payload,
            None,
            market_snapshot_id=market_id,
            source_archive_id=archive_id,
            source_archive_digest="a" * 64,
        )
    )
    result = PublicationResult(
        publication_id=pub_id,
        domain="market",
        input_id=market_id,
        status="ready",
        payload=json.loads(json.dumps(archived, default=str)),
        coverage={"expected": 1, "valid": 1, "legal_excluded": 0, "missing": 0},
        quality_flags=(),
    )
    return PublishedSnapshot(publication, market, (result,))


class AuthorizedRepository:
    """Same two-grant policy contract as the actual publication repository."""

    def __init__(self, snapshot, *, current=None, public=False):
        from factories import grant

        from dragon_radar.publication.access import (
            LocalPublicationAccessPolicy,
            PublicSharingAccessPolicy,
        )

        self.snapshot = snapshot
        self.calls = []
        self.current = current or grant()
        self.access_policy = (
            PublicSharingAccessPolicy if public else LocalPublicationAccessPolicy
        )(lambda: NOW)

    def get_snapshot(self, snapshot_id):
        from dragon_radar.storage.repository import Forbidden

        self.calls.append(snapshot_id)
        if snapshot_id != self.snapshot.id:
            return None
        if not self.access_policy.allows(
            "market",
            source_id=self.snapshot.batch.source_id,
            current=self.current,
            embedded=self.snapshot.batch.grant,
        ):
            raise Forbidden("denied")
        return self.snapshot


async def history_request(repository, query="", sid="IDX"):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from dragon_radar.http.errors import register_error_handlers
    from dragon_radar.http.routes.indexes import router

    app = FastAPI()
    app.state.repository = repository
    register_error_handlers(app)
    app.include_router(router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(f"/api/index/{sid}/history?{query}")


QUERY = f"date=2026-09-08&snapshot_id={PUB_ID}&start=2026-09-03&end=2026-09-08"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "query",
    [
        "",
        f"date=2026-09-08&snapshot_id={PUB_ID}",
        QUERY + "&extra=1",
        QUERY + "&adj=qfq",
        QUERY + "&date=2026-09-08",
        QUERY.replace(str(PUB_ID), "invalid"),
        QUERY.replace("start=2026-09-03", "start=2026-09-31"),
        QUERY.replace("start=2026-09-03", "start=2026-09-09"),
        QUERY.replace("end=2026-09-08", "end=2026-09-09"),
    ],
)
async def test_route_invalid_selectors_rejected_before_storage(query):
    repo = AuthorizedRepository(published_snapshot())
    response = await history_request(repo, query)
    assert response.status_code == 400
    assert repo.calls == []


@pytest.mark.anyio
async def test_route_exact_pub_projection_and_unknown_coordinates():
    repo = AuthorizedRepository(published_snapshot())
    response = await history_request(repo, QUERY)
    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_id"] == str(PUB_ID) and body["as_of"] == NOW.isoformat()
    assert body["data"]["points"][0]["close"] == "10"
    assert body["basis"]["unit"] == "point"
    assert (await history_request(repo, QUERY, "UNKNOWN")).status_code == 404
    assert (
        await history_request(repo, QUERY.replace(str(PUB_ID), str(MARKET_ID)))
    ).status_code == 404
    assert (
        await history_request(repo, QUERY.replace("date=2026-09-08", "date=2026-09-07"))
    ).status_code == 400


@pytest.mark.anyio
async def test_route_p2_cannot_replace_or_mutate_p1_history():
    p1 = published_snapshot()
    p2_id = UUID("00000000-0000-4000-8000-000000000004")
    p2 = published_snapshot(
        index_batch(
            bars=(*batch().bars, *(index_bar(day, close="20") for day in DAYS))
        ),
        pub_id=p2_id,
        market_id=UUID("00000000-0000-4000-8000-000000000005"),
        archive_id=UUID("00000000-0000-4000-8000-000000000006"),
    )
    repositories = {PUB_ID: AuthorizedRepository(p1), p2_id: AuthorizedRepository(p2)}

    class MultiplePublications:
        def get_snapshot(self, snapshot_id):
            return repositories[snapshot_id].get_snapshot(snapshot_id)

    repo = MultiplePublications()
    before = await history_request(repo, QUERY)
    newer = await history_request(repo, QUERY.replace(str(PUB_ID), str(p2_id)))
    after = await history_request(repo, QUERY)
    assert before.status_code == newer.status_code == after.status_code == 200
    assert before.content == after.content
    assert before.json()["data"]["points"][-1]["close"] == "10"
    assert newer.json()["data"]["points"][-1]["close"] == "20"
    assert before.json()["data"]["source"]["market_snapshot_id"] == str(MARKET_ID)
    assert newer.json()["data"]["source"]["market_snapshot_id"] != str(MARKET_ID)


@pytest.mark.anyio
async def test_route_never_reconstructs_a_non_pub_market_snapshot():
    market = published_snapshot().market_snapshot

    class LegacyOnly:
        def get_snapshot(self, snapshot_id):
            assert snapshot_id == market.id
            return market

    response = await history_request(
        LegacyOnly(), QUERY.replace(str(PUB_ID), str(market.id))
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_route_legacy_missing_history_and_no_sessions_are_honest():
    from dragon_radar.readmodels.market import materialize_market

    legacy = published_snapshot(stored=materialize_market(index_batch(), None))
    body = (await history_request(AuthorizedRepository(legacy), QUERY)).json()
    assert (
        body["data"] is None and "materialization_unavailable" in body["quality_flags"]
    )
    assert body["reason"] == "materialization_unavailable"
    assert body["coverage"] is None
    payload = batch(
        security_states=(state("IDX", security_type="index"),),
        expected_security_ids=("IDX",),
        bars=tuple(bar("IDX", day) for day in DAYS),
    )
    body = (
        await history_request(AuthorizedRepository(published_snapshot(payload)), QUERY)
    ).json()
    assert body["data"] is None and "index_history_unavailable" in body["quality_flags"]
    assert body["reason"] == "index_history_unavailable"
    query = QUERY.replace("start=2026-09-03", "start=2026-09-05").replace(
        "end=2026-09-08", "end=2026-09-06"
    )
    body = (
        await history_request(AuthorizedRepository(published_snapshot()), query)
    ).json()
    assert body["data"] is None and "no_sessions" in body["quality_flags"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b["index_history"].update(market_snapshot_id=str(ARCHIVE_ID)),
        lambda b: b["index_history"].update(source_archive_digest="b" * 64),
        lambda b: b["index_history"]["indexes"][0]["identity"].update(
            name="Wrong identity"
        ),
        lambda b: b["index_history"].update(schema_version=2),
        lambda b: b.update(index_history=None),
        lambda b: b["index_history"]["indexes"][0]["points"][0].update(close="1e1"),
        lambda b: b["index_history"]["indexes"][0]["points"][0].update(close="+10"),
    ],
)
async def test_route_corrupt_present_history_is_503_not_fallback(mutate):
    from dragon_radar.readmodels.market import materialize_market

    stored = materialize_market(
        index_batch(),
        None,
        market_snapshot_id=MARKET_ID,
        source_archive_id=ARCHIVE_ID,
        source_archive_digest="a" * 64,
    )
    mutate(stored)
    response = await history_request(
        AuthorizedRepository(published_snapshot(stored=stored)), QUERY
    )
    assert response.status_code == 503
    assert response.json() == {
        "code": "DATA_UNAVAILABLE",
        "message": "Data unavailable",
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "public,embedded,current,expected",
    [
        (False, True, True, 200),
        (False, False, True, 403),
        (False, True, False, 403),
        (True, True, True, 200),
        (True, False, True, 403),
        (True, True, False, 403),
    ],
)
async def test_route_rechecks_embedded_and_current_rights(
    public, embedded, current, expected
):
    from factories import grant

    rights = {"sharing": embedded} if public else {"local_display": embedded}
    current_rights = {"sharing": current} if public else {"local_display": current}
    snapshot = published_snapshot(index_batch(grant=grant(**rights)))
    repo = AuthorizedRepository(
        snapshot, current=grant(**current_rights), public=public
    )
    response = await history_request(repo, QUERY)
    assert response.status_code == expected
    if expected == 403:
        assert "points" not in response.text and "10" not in response.text
