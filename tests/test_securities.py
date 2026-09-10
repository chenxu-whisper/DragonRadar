"""Securities read contracts against isolated, authorized PostgreSQL snapshots."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from factories import DAYS, bar, batch, state
from httpx import ASGITransport, AsyncClient

from dragon_radar.analytics.metrics import market_breadth

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(repository):
    from dragon_radar.api import create_app

    app = create_app(repository=repository)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client


def publish(repository, payload=None, key="securities"):
    return repository.publish(
        repository.import_batch(payload if payload is not None else batch(), key=key).id
    )


def url(snapshot, suffix=""):
    return f"/api/v1/snapshots/{snapshot.id}/securities{suffix}"


@pytest.fixture
def large_snapshot(repository):
    ids = tuple(f"S{i:03d}" for i in reversed(range(100)))
    prices = ("0.9", "2", "10", "10.000000000000000001", "2")
    return publish(
        repository,
        batch(
            expected_security_ids=ids,
            security_states=tuple(
                state(
                    sid,
                    name=f"Group {int(sid[1:]) % 4}",
                    suspended=int(sid[1:]) % 6 == 5,
                )
                for sid in ids
            ),
            bars=tuple(
                bar(
                    sid,
                    close=prices[int(sid[1:]) % 6],
                    previous_close="10",
                    amount=None if int(sid[1:]) % 6 == 0 else prices[int(sid[1:]) % 6],
                )
                for sid in ids
                if int(sid[1:]) % 6 != 5
            ),
        ),
    )


async def test_list_schema_and_existing_envelope(client, repository):
    snapshot = publish(repository)
    response = await client.get(url(snapshot))
    assert response.status_code == 200
    data = response.json()
    result = data["result"]
    assert result["total"] == 1
    assert (result["page"], result["page_size"]) == (1, 50)
    assert result["basis"] == data["basis"] == "raw_close"
    assert data["analysis_coverage"] is None
    assert result["items"] == [
        {
            "security_id": "A",
            "name": "Synthetic A",
            "exchange": "TEST",
            "board": "synthetic-board",
            "close": "10",
            "change": "0",
            "amount": "1000",
            "suspended": False,
            "missing_reasons": [],
            "currency": "CNY",
            "price_unit": "currency/share",
            "volume_unit": "share",
            "amount_unit": "currency",
            "change_unit": "fraction",
        }
    ]
    summary = (await client.get(f"/api/v1/snapshots/{snapshot.id}")).json()
    for key in (
        "snapshot_id",
        "trade_date",
        "as_of",
        "source_time",
        "source",
        "coverage",
        "coverage_scope",
        "units",
        "quality_flags",
        "rule_version",
        "manifest",
        "provenance",
    ):
        assert data[key] == summary[key]
    for forbidden in (
        "grant",
        "reference",
        "canonical_json",
        "security_states",
        '"bars"',
        "synthetic-test-only",
        "password",
    ):
        assert forbidden not in response.text


@pytest.mark.parametrize("sort", ["security_id", "name", "close", "change", "amount"])
@pytest.mark.parametrize("order", ["asc", "desc"])
async def test_exact_sort_nulls_last_ties_id_ascending(
    client, large_snapshot, sort, order
):
    response = await client.get(
        url(large_snapshot),
        params={
            "sort": sort,
            "order": order,
            "page_size": 100,
        },
    )
    assert response.status_code == 200
    rows = response.json()["result"]["items"]
    assert len(rows) == 100
    records = {record.security_id: record for record in large_snapshot.batch.bars}
    returns = {
        item.security_id: item.value
        for item in market_breadth(large_snapshot.batch).returns
    }
    for row in rows:
        sid = row["security_id"]
        assert row["suspended"] is (sid not in records)
        for field in ("close", "amount", "change"):
            value = (
                returns.get(sid)
                if field == "change"
                else (getattr(records[sid], field) if sid in records else None)
            )
            assert row[field] == (str(value) if value is not None else None)
    numeric = sort in {"close", "change", "amount"}
    by_id = sorted(rows, key=lambda row: row["security_id"])
    present = [row for row in by_id if row[sort] is not None]
    absent = [row for row in by_id if row[sort] is None]
    expected = (
        sorted(
            present,
            key=lambda row: Decimal(row[sort]) if numeric else row[sort],
            reverse=order == "desc",
        )
        + absent
    )
    assert rows == expected
    if sort == "close" and order == "asc":
        assert next(
            i for i, row in enumerate(rows) if row["security_id"] == "S002"
        ) < next(i for i, row in enumerate(rows) if row["security_id"] == "S003")


@pytest.mark.parametrize("size", [20, 50, 100])
async def test_page_sizes_and_out_of_range_pages(client, large_snapshot, size):
    pages = []
    for page in range(1, 100 // size + 1):
        response = await client.get(
            url(large_snapshot), params={"page": page, "page_size": size}
        )
        assert response.status_code == 200
        result = response.json()["result"]
        assert (result["total"], result["page"], result["page_size"]) == (
            100,
            page,
            size,
        )
        pages.extend(row["security_id"] for row in result["items"])
    assert pages == sorted(large_snapshot.batch.expected_security_ids)
    result = (await client.get(url(large_snapshot), params={"page": 100000})).json()[
        "result"
    ]
    assert result["items"] == [] and result["total"] == 100
    default = (await client.get(url(large_snapshot))).json()["result"]
    assert len(default["items"]) == 50


@pytest.mark.parametrize(
    "q,expected",
    [
        ("", 100),
        ("   ", 100),
        (" s001 ", 1),
        ("gRoUp 0", 25),
        ("unknown", 0),
    ],
)
async def test_normalized_search_and_empty_results(client, large_snapshot, q, expected):
    response = await client.get(url(large_snapshot), params={"q": q})
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["total"] == expected
    assert len(result["items"]) == min(expected, 50)


@pytest.mark.parametrize(
    "query",
    [
        "page=0",
        "page=-1",
        "page=100001",
        "page=true",
        "page=1.5",
        "page_size=0",
        "page_size=21",
        "page_size=101",
        "sort=volume",
        "order=ASC",
        "unknown=secret",
        "q=" + "x" * 101,
        "q=a&q=b",
        "sort=name&sort=close",
        "order=asc&order=desc",
        "page=1&page=2",
        "page_size=20&page_size=50",
    ],
)
async def test_invalid_list_parameters_are_sanitized(client, repository, query):
    snapshot = publish(repository)
    response = await client.get(url(snapshot) + "?" + query)
    assert response.status_code in (400, 422)
    assert response.json() == {"error": "Invalid request"}


async def test_summary_calendar_is_safe_and_clipped_to_selected_snapshot(
    client, repository
):
    snapshot = publish(
        repository,
        batch(
            trade_date=DAYS[1],
            bars=tuple(bar(trade_date=day) for day in DAYS[:2]),
        ),
    )
    for path in (f"/api/v1/snapshots/{snapshot.id}", "/api/v1/snapshots"):
        response = await client.get(path)
        assert response.status_code == 200
        data = response.json()
        result = (data[0] if isinstance(data, list) else data)["result"]
        assert result["calendar_sessions"] == [day.isoformat() for day in DAYS[:2]]
        assert result["earliest_date"] == DAYS[0].isoformat()
        for forbidden in ('"bars"', "grant", "reference", "canonical_json"):
            assert forbidden not in response.text


@pytest.mark.parametrize("mode", ["close_only", "full", "mixed"])
async def test_history_chart_selection_never_fabricates_ohlc(client, repository, mode):
    records = tuple(
        bar(
            trade_date=day,
            adjustment_factor="2",
            **(
                {"open": "9", "high": "11", "low": "8"}
                if mode == "full" or (mode == "mixed" and day == DAYS[-1])
                else {}
            ),
        )
        for day in DAYS
    )
    snapshot = publish(repository, batch(bars=records))
    response = await client.get(url(snapshot, "/A"))
    assert response.status_code == 200
    data = response.json()
    result = data["result"]
    assert result["chart_type"] == ("candlestick" if mode == "full" else "line")
    assert result["price_basis"] == result["basis"] == data["basis"] == "raw_close"
    assert data["analysis_coverage"] is None
    listing = (await client.get(url(snapshot))).json()["result"]["items"][0]
    assert result["security"] == listing
    assert [point["trade_date"] for point in result["history"]] == [
        d.isoformat() for d in DAYS
    ]
    for record, point in zip(records, result["history"], strict=True):
        for field in ("open", "high", "low", "close", "volume", "amount"):
            value = getattr(record, field)
            assert point[field] == (str(value) if value is not None else None)
        assert point["suspended"] is False
        assert point["currency"] == "CNY"
        assert point["volume_unit"] == "share"
        assert point["missing_reasons"] == ([] if record.open else ["missing_ohlc"])
    for forbidden in (
        "grant",
        "reference",
        "canonical_json",
        "security_states",
        '"bars"',
        "synthetic-test-only",
        "adjustment_factor",
    ):
        assert forbidden not in response.text


async def test_history_preserves_unknown_missing_and_suspended_sessions(
    client, repository
):
    future = DAYS[-1] + timedelta(days=1)
    snapshot = publish(
        repository,
        batch(
            calendar={"exchange": "TEST", "version": "c1", "sessions": (*DAYS, future)},
            security_states=(
                state(effective_from=DAYS[1], effective_to=DAYS[2], name="Past"),
                state(effective_from=DAYS[2], effective_to=DAYS[3], suspended=True),
                state(effective_from=DAYS[3], effective_to=future, name="Current"),
                state(effective_from=future, name="Future"),
            ),
            bars=(bar(open="9", high="11", low="8"),),
        ),
    )
    response = await client.get(url(snapshot, "/A"))
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["security"]["name"] == "Current"
    assert result["chart_type"] == "candlestick"
    history = result["history"]
    assert len(history) == 4
    for point, suspended, reason in zip(
        history[:3],
        (None, False, True),
        ("effective_state", "missing_price", "suspension"),
        strict=True,
    ):
        assert point["suspended"] is suspended
        assert point["missing_reasons"] == [reason]
        for field in ("open", "high", "low", "close", "volume", "amount", "currency"):
            assert point[field] is None
    assert history[-1]["close"] == "10"
    assert "Future" not in response.text and "Past" not in response.text


async def test_all_suspended_has_null_values_and_line_chart(client, repository):
    snapshot = publish(
        repository, batch(security_states=(state(suspended=True),), bars=())
    )
    response = await client.get(url(snapshot, "/A"))
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["chart_type"] == "line"
    row = result["security"]
    assert row["name"] == "Synthetic A" and row["suspended"] is True
    assert row["missing_reasons"] == ["suspension"]
    for field in ("close", "change", "amount", "currency"):
        assert row[field] is None
    assert all(
        point["close"] is None and point["suspended"] is True
        for point in result["history"]
    )


async def test_names_follow_selected_snapshot_not_latest_or_future(client, repository):
    future = DAYS[-1] + timedelta(days=1)
    states = (
        state(name="Old", effective_to=DAYS[1]),
        state(name="Stra\u00dfe Current", effective_from=DAYS[1], effective_to=future),
        state(name="Future", effective_from=future),
    )
    old = publish(
        repository,
        batch(
            trade_date=DAYS[0],
            security_states=states,
            bars=(bar(trade_date=DAYS[0], close="7"),),
        ),
        key="old",
    )
    current = publish(repository, batch(security_states=states), key="current")
    for snapshot, name, price, query in (
        (old, "Old", "7", "old"),
        (current, "Stra\u00dfe Current", "10", "STRASSE"),
    ):
        response = await client.get(url(snapshot, "/A"))
        assert response.status_code == 200
        row = response.json()["result"]["security"]
        assert (row["name"], row["close"]) == (name, price)
        listing = (await client.get(url(snapshot), params={"q": query})).json()[
            "result"
        ]
        assert listing["items"] == [row]
    for snapshot, query in ((old, "current"), (current, "old"), (current, "future")):
        assert (await client.get(url(snapshot), params={"q": query})).json()["result"][
            "total"
        ] == 0


@pytest.mark.parametrize("reference", ["explicit", "prior", "missing"])
async def test_change_matches_breadth_and_optional_values_remain_null(
    client, repository, reference
):
    records = (
        bar(
            close="12",
            previous_close="8" if reference == "explicit" else None,
            amount=None,
            volume=None,
        ),
    )
    if reference != "missing":
        records = (bar(trade_date=DAYS[-2], close="10"), *records)
    snapshot = publish(repository, batch(bars=records))
    response = await client.get(url(snapshot, "/A"))
    assert response.status_code == 200
    result = response.json()["result"]
    row = result["security"]
    breadth = market_breadth(snapshot.batch)
    assert row["change"] == (str(breadth.returns[0].value) if breadth.returns else None)
    assert row["close"] == "12" and row["amount"] is None
    assert "missing_amount" in row["missing_reasons"]
    assert result["history"][-1]["volume"] is None
    assert "missing_volume" in result["history"][-1]["missing_reasons"]
    if reference == "missing":
        assert set(breadth.exclusions[0].reasons) <= set(row["missing_reasons"])


async def test_pool_boundary_authorization_and_no_writes(client, repository):
    snapshot = publish(
        repository,
        batch(
            security_states=(state(), state("FOREIGN")),
            bars=(*batch().bars, bar("FOREIGN")),
        ),
    )
    listing = (await client.get(url(snapshot))).json()["result"]
    assert [row["security_id"] for row in listing["items"]] == ["A"]
    assert (await client.get(url(snapshot, "/FOREIGN"))).status_code == 404
    assert (await client.get(url(snapshot, "/unknown-secret"))).json() == {
        "error": "Snapshot not found"
    }
    assert (await client.get(url(snapshot, "/A?unknown=secret"))).status_code == 400
    assert (await client.get(url(snapshot, "/A?q=a&q=b"))).status_code == 400
    for suffix in ("", "/A"):
        path = url(snapshot, suffix)
        response = await client.get(path, headers={"Origin": "https://example.com"})
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers
        for method in ("post", "put", "patch", "delete"):
            assert (await client.request(method, path)).status_code == 405
    repository.revoke_source("synthetic")
    for suffix in ("", "/A", "/FOREIGN", "?unknown=secret", "/A?unknown=secret"):
        response = await client.get(url(snapshot, suffix))
        assert response.status_code == 403
        assert response.json() == {"error": "Source access denied"}


async def test_unknown_snapshot_is_not_replaced_with_latest(client, repository):
    publish(repository)
    for suffix in ("", "/A"):
        response = await client.get(f"/api/v1/snapshots/{uuid4()}/securities{suffix}")
        assert response.status_code == 404
        assert response.json() == {"error": "Snapshot not found"}
