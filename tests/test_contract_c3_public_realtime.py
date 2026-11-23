"""C3 real public entry must mount only the four existing realtime reads."""

from decimal import Decimal

import pytest
from factories import NOW, grant
from httpx import ASGITransport, AsyncByteStream, AsyncClient

from dragon_radar.http.public_app import create_public_app
from dragon_radar.realtime.models import PermissionSnapshot, QuoteBatchInput, QuoteInput
from dragon_radar.realtime.repository import RealtimeRepository
from dragon_radar.storage.repository import Repository


class EmptyRepository:
    database = None


class EmptyRequestStream(AsyncByteStream):
    async def __aiter__(self):
        if False:
            yield b""


@pytest.mark.anyio
async def test_public_realtime_entry_reaches_storage_handler_without_private_routes():
    app = create_public_app(repository=EmptyRepository())
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://public.example",
    ) as client:
        for path in (
            "/api/realtime/quotes?security_ids=SYNTH-C1-1",
            "/api/realtime/minutes?security_id=SYNTH-C1-1&date=2026-09-08",
            "/api/realtime/anomalies?date=2026-09-08",
            "/api/market/sector-flow/intraday?sector=SYNTH-SECTOR-1&date=2026-09-08",
        ):
            request = client.build_request("GET", path, content=EmptyRequestStream())
            request.headers.pop("transfer-encoding", None)
            response = await client.send(request)
            assert response.status_code == 503
            assert response.json()["code"] == "STORAGE_UNAVAILABLE"
            assert (await client.post(path)).status_code == 403
        for path in (
            "/api/realtime/import",
            "/api/realtime/quotes/extra",
            "/api/owner/v1/session",
        ):
            request = client.build_request("GET", path, content=EmptyRequestStream())
            request.headers.pop("transfer-encoding", None)
            assert (await client.send(request)).status_code == 404


@pytest.mark.anyio
async def test_public_exact_quote_denies_nonsharing_source_in_actual_database(database):
    market = Repository(database, clock=lambda: NOW)
    source = grant(source_id="c3-private-feed", sharing=False)
    market.register_source(source)
    batch = QuoteBatchInput(
        source_id=source.source_id,
        batch_id="c3-private-quote",
        source_version="c3-v1",
        source_event_at=NOW,
        received_at=NOW,
        available_at=NOW,
        permission=PermissionSnapshot(
            permission_id="c3-private-permission",
            state="authorized",
            captured_at=NOW,
        ),
        quotes=(
            QuoteInput(
                security_id="SYNTH-C1-1",
                last=Decimal("10"),
                order_book_status="disabled",
                order_book_unavailable_reason="order_book_not_authorized",
            ),
        ),
    )
    RealtimeRepository(database, clock=lambda: NOW).ingest_quote_batch(
        batch, key="c3-private-quote"
    )
    app = create_public_app(repository=market)
    path = (
        "/api/realtime/quotes?security_ids=SYNTH-C1-1"
        "&source_id=c3-private-feed&quote_batch_id=c3-private-quote"
        "&as_of=2026-09-09T12:00:00Z"
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://public.example"
    ) as client:
        request = client.build_request("GET", path, content=EmptyRequestStream())
        request.headers.pop("transfer-encoding", None)
        response = await client.send(request)
    print(
        {
            "method": "GET",
            "path": path,
            "source": source.model_dump(mode="json"),
            "batch": batch.model_dump(mode="json"),
            "status": response.status_code,
            "body": response.json(),
        }
    )
    assert response.status_code == 403, (
        f"Public EX04 leaked nonsharing exact source/batch: {response.status_code} "
        f"{response.text}"
    )
