"""C3 fixture only: real worker HTTP and repository-fed realtime evidence."""

import argparse
import json
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from contract_c1_fixture import encoded, fixture_database


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SyntheticHTTPProvider:
    name = "synthetic-c3"
    model_version = "synthetic-c3-v1"
    prompt_version = "synthetic-c3-v1"

    def __init__(self, target, approved, credential):
        self.target = target
        self.approved = approved
        self.credential = credential

    def generate(self, task_type, manifest, limits):
        parts = urlsplit(self.target)
        if (
            self.target != self.approved
            or parts.scheme != "http"
            or parts.hostname != "127.0.0.1"
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or not parts.path.startswith("/synthetic/c3-")
            or not parts.path.endswith("/exact")
        ):
            raise ValueError("Unapproved synthetic target")
        opener = build_opener(ProxyHandler({}), NoRedirect())
        request = Request(
            self.target,
            data=encoded({"task_type": task_type, "manifest": manifest}),
            headers={"Authorization": f"Bearer {self.credential}"},
            method="POST",
        )
        with opener.open(request, timeout=limits.timeout_seconds) as response:
            if response.status != 200:
                raise ValueError("Synthetic transport failed")
            return json.loads(response.read(128_001))


def assert_redirect_denied(receipts, approved_path):
    assert receipts == [{"method": "POST", "path": approved_path}], (
        "Redirect case must issue one approved POST and no follow-up request"
    )


def ai_evidence(database):
    from factories import NOW, batch, grant

    from dragon_radar.ai.models import AIProviderResult
    from dragon_radar.ai.provider import UnavailableProvider, provider_from_environment
    from dragon_radar.ai.repository import AITaskRepository
    from dragon_radar.ai.service import AIService
    from dragon_radar.ai.worker import AIWorker
    from dragon_radar.owner.repository import OwnerRepository
    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.repository import PublicationRepository
    from dragon_radar.publication.service import PublicationService
    from dragon_radar.storage.repository import Repository

    owners = OwnerRepository(database, clock=lambda: NOW)
    owners.initialize()
    session = owners.exchange_pairing(
        owners.issue_pairing("https://localhost:8443"),
        "https://localhost:8443",
    )
    repository = Repository(database, clock=lambda: NOW)
    repository.register_source(grant(ai=True))
    market = repository.publish(
        repository.import_batch(
            batch(grant=grant(ai=True)),
            key="c3-ai-market",
        ).id
    )
    publication = PublicationService(database, repository, clock=lambda: NOW).publish(
        PublicationCommand(
            trade_date=market.batch.trade_date,
            market_snapshot_id=market.id,
            disabled_domains=("topic", "news", "regulation", "role"),
        ),
        key="c3-ai-publication",
    )
    tasks = AITaskRepository(database, owners, clock=lambda: NOW)
    service = AIService(
        tasks, PublicationRepository(database, repository, clock=lambda: NOW)
    )

    def submit(key):
        return service.submit(
            session.token,
            session.csrf,
            task_type="daily_review",
            snapshot_id=publication.id,
            as_of=publication.published_at,
            version=f"v{publication.version}",
            key=key,
            task_input={
                "date": publication.trade_date.isoformat(),
                "notes_context": "Synthetic C3 only",
            },
        )

    task = submit("c3-clock")
    boundaries = []
    for milliseconds, overdue, draft in (
        (59_999, False, False),
        (60_000, True, False),
        (119_999, True, False),
        (120_000, True, True),
    ):
        tasks.clock = lambda ms=milliseconds: NOW + timedelta(milliseconds=ms)
        record = tasks.get(session.token, task.id)
        assert record.processing_overdue is overdue
        assert (record.content_draft is not None) is draft
        if draft:
            assert not record.content_draft.ai_generated
            assert record.content_draft.source == "deterministic_template"
        boundaries.append(
            {"milliseconds": milliseconds, "overdue": overdue, "draft": draft}
        )
    tasks.cancel(session.token, session.csrf, task.id, key="c3-clock-cancel")
    tasks.clock = lambda: NOW
    unavailable = submit("c3-unavailable")
    unavailable_record = AIWorker(
        tasks,
        provider_from_environment(),
        worker_id="c3-unavailable",
        max_attempts=1,
    ).run_once()
    assert unavailable_record.id == unavailable.id
    assert unavailable_record.failure["code"] == "provider_unavailable"
    tasks.clock = lambda: NOW + timedelta(milliseconds=119_999)
    assert tasks.get(session.token, unavailable.id).content_draft is None
    tasks.clock = lambda: NOW + timedelta(seconds=120)
    assert (
        tasks.get(session.token, unavailable.id).content_draft.source
        == "deterministic_template"
    )
    tasks.clock = lambda: NOW
    assert isinstance(provider_from_environment(), UnavailableProvider)
    late = submit("c3-late-human")
    lease = tasks.claim("c3-controlled-late", lease_for=timedelta(seconds=300))
    assert lease.job.id == late.id
    tasks.clock = lambda: NOW + timedelta(seconds=120)
    template = tasks.get(session.token, late.id).content_draft
    edited = tasks.review(
        session.token,
        session.csrf,
        late.id,
        decision="edit",
        revision=template.revision,
        edited_content={"text": "C3 saved human note"},
        key="c3-human-1",
    )
    newer = tasks.review(
        session.token,
        session.csrf,
        late.id,
        decision="edit",
        revision=edited.revision,
        edited_content={"text": "C3 newer human note"},
        key="c3-human-2",
    )
    completed = tasks.succeed(
        lease,
        AIProviderResult(
            content={"text": "C3 late provider output"}, token_usage=1, cost="0.01"
        ),
        provider="synthetic-clock-only",
        model_version="c3-clock",
        prompt_version="c3-clock",
        latency_ms=120_000,
        max_tokens=10,
        max_cost=Decimal("0.1"),
    )
    fetched = tasks.get(session.token, late.id)
    assert completed.status == "succeeded"
    assert fetched.content_draft.content == {"text": "C3 newer human note"}
    assert fetched.content_draft.revision == newer.revision
    assert fetched.human_review_status == "edited"
    late_readback = {
        "task_id": str(late.id),
        "saved_revision": edited.revision,
        "newer_revision": newer.revision,
        "readback_revision": fetched.content_draft.revision,
        "late_task_status": completed.status,
        "human_review_status": fetched.human_review_status,
        "newer_note_preserved": True,
        "transport": "controlled-clock-repository-not-HTTP",
    }
    tasks.clock = lambda: NOW

    lock = threading.Lock()
    counters = {
        "requests": 0,
        "forbidden": 0,
        "active": 0,
        "peak": 0,
        "receipts": [],
        "rendezvous_timeouts": 0,
    }
    mode = {"value": "exact"}
    credential = "synthetic-c3-credential-no-production-rights"
    exact_barrier = threading.Barrier(2, timeout=4)

    class Listener(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def parse_request(self):
            if not super().parse_request():
                return False
            with lock:
                counters["requests"] += 1
                counters["receipts"].append({"method": self.command, "path": self.path})
                if (
                    self.command != "POST"
                    or self.path != "/synthetic/c3-worker/exact"
                    or self.headers.get("Authorization") != f"Bearer {credential}"
                ):
                    counters["forbidden"] += 1
                assert counters["requests"] <= 8
            return True

        def do_POST(self):
            with lock:
                counters["active"] += 1
                counters["peak"] = max(counters["peak"], counters["active"])
                selected = mode["value"]
            try:
                if (
                    self.path != "/synthetic/c3-worker/exact"
                    or self.headers.get("Authorization") != f"Bearer {credential}"
                ):
                    self.send_error(403)
                    return
                length = int(self.headers["Content-Length"])
                assert 0 < length <= 128_000
                payload = json.loads(self.rfile.read(length))
                if selected == "exact":
                    try:
                        exact_barrier.wait()
                    except threading.BrokenBarrierError:
                        with lock:
                            counters["rendezvous_timeouts"] += 1
                        self.send_error(504, "C3 exact batch rendezvous failed")
                        return
                if selected == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "/forbidden")
                    self.end_headers()
                    return
                if selected == "error":
                    self.send_error(500, f"Bearer {credential} raw-provider-secret")
                    return
                evidence = payload["manifest"]["evidence"]
                content = {
                    "facts": [],
                    "inferences": [],
                    "missing_evidence": [],
                    "citations": evidence[:1],
                }
                raw = encoded(
                    {
                        "content": content,
                        "token_usage": 11 if selected == "token-over" else 10,
                        "cost": "0.100000000000000001"
                        if selected == "cost-over"
                        else "0.1",
                    }
                )
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            finally:
                with lock:
                    counters["active"] -= 1

    listener = ThreadingHTTPServer(("127.0.0.1", 0), Listener)
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    thread.start()
    target = f"http://127.0.0.1:{listener.server_port}/synthetic/c3-worker/exact"
    worker = AIWorker(
        tasks,
        SyntheticHTTPProvider(target, target, credential),
        worker_id="c3-real-http",
        max_attempts=1,
        max_tokens=10,
        max_cost=Decimal("0.1"),
        concurrency=2,
        provider_timeout=10,
    )
    results = []
    exact_receipts = []
    try:
        submit("c3-http-exact-1")
        submit("c3-http-exact-2")
        exact = worker.run_batch()
        assert counters["rendezvous_timeouts"] == 0, (
            "C3 exact batch did not rendezvous within 4 seconds"
        )
        assert len(exact) == 2 and all(item.status == "succeeded" for item in exact)
        for item in exact:
            assert item.token_usage == 10 and item.cost == Decimal("0.1")
            assert item.verification_status == "passed"
            assert item.human_review_status == "unreviewed"
            exact_receipts.append(
                {
                    "task_id": str(item.id),
                    "snapshot_id": str(item.snapshot_id),
                    "version": item.version,
                    "status": item.status,
                    "verification_status": item.verification_status,
                    "human_review_status": item.human_review_status,
                    "token_usage": item.token_usage,
                    "decimal_cost": str(item.cost),
                    "provider": item.provider,
                    "model_version": item.model_version,
                    "prompt_version": item.prompt_version,
                }
            )
        assert counters["peak"] == 2
        for selected in ("token-over", "cost-over", "error", "redirect"):
            mode["value"] = selected
            submit(f"c3-http-{selected}")
            receipt_start = len(counters["receipts"])
            record = worker.run_once()
            case_receipts = counters["receipts"][receipt_start:]
            if selected == "redirect":
                assert_redirect_denied(case_receipts, urlsplit(target).path)
            expected = (
                "result_limit_exceeded"
                if selected.endswith("over")
                else "provider_failed"
            )
            assert record.status == "failed" and record.failure["code"] == expected
            raw = record.model_dump_json()
            assert credential not in raw and "raw-provider-secret" not in raw
            results.append(
                {
                    "case": selected,
                    "status": record.status,
                    "failure": record.failure["code"],
                    "requests": case_receipts,
                }
            )
        before = counters["requests"]
        for forbidden in (
            target + "/extra",
            target.replace("c3-worker", "other"),
            target.replace("127.0.0.1", "user:secret@127.0.0.1"),
        ):
            submit(f"c3-forbidden-{before}-{len(results)}")
            denied = AIWorker(
                tasks,
                SyntheticHTTPProvider(forbidden, target, credential),
                worker_id="c3-forbidden",
                max_attempts=1,
            ).run_once()
            assert denied.status == "failed"
            results.append({"case": "forbidden-target", "status": denied.status})
        assert counters["requests"] == before == 6
        assert counters["forbidden"] == 0
    finally:
        exact_barrier.abort()
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=2)
    return {
        "mock_clock": boundaries,
        "unavailable_template_at_120": True,
        "controlled_late_human_readback": late_readback,
        "actual_http": {
            **counters,
            "approved_target": target,
            "license": "approved-C3-synthetic-only-no-daily-rights",
            "input_version": f"v{publication.version}",
            "publication_id": str(publication.id),
            "exact_results": exact_receipts,
        },
        "results": results,
        "limits": {
            "per_result_tokens": 10,
            "per_result_decimal_cost": "0.1",
            "per_worker_batch_concurrency": 2,
            "harness_request_ceiling": 8,
        },
        "daily_model_rights": "disabled",
        "real_provider_quality": "D-not-evaluated",
        "publication": str(publication.id),
    }


def realtime_inputs(database):
    from factories import grant

    from dragon_radar.realtime.models import (
        AnomalyRule,
        MinuteBarInput,
        MinuteBatchInput,
        PermissionSnapshot,
        QuoteBatchInput,
        QuoteInput,
        SectorFlowBatchInput,
        SectorFlowPoint,
    )
    from dragon_radar.realtime.repository import RealtimeRepository
    from dragon_radar.storage.repository import Repository

    start = datetime(2026, 9, 8, 1, 30, tzinfo=UTC)
    now = start + timedelta(hours=1)
    source = "synthetic-c3-rt"
    Repository(database, clock=lambda: now).register_source(
        grant(source_id=source, sharing=True)
    )
    repo = RealtimeRepository(database, clock=lambda: now)
    permission = PermissionSnapshot(
        permission_id="c3-permission",
        state="authorized",
        captured_at=start,
    )
    quote = QuoteBatchInput(
        source_id=source,
        batch_id="c3-quote",
        source_version="c3-v1",
        source_event_at=start,
        received_at=start + timedelta(seconds=1),
        available_at=start + timedelta(seconds=2),
        permission=permission,
        quotes=(
            QuoteInput(
                security_id="SYNTH-C1-1",
                last=Decimal("123.45"),
                order_book_status="disabled",
                order_book_unavailable_reason="not_authorized",
            ),
        ),
    )
    first = repo.ingest_quote_batch(quote, key="c3-quote")
    replay = repo.ingest_quote_batch(quote, key="c3-quote")
    assert first.batch_id == replay.batch_id
    Repository(database, clock=lambda: now).register_source(
        grant(source_id="c3-private-feed")
    )
    repo.ingest_quote_batch(
        quote.model_copy(
            update={
                "source_id": "c3-private-feed",
                "batch_id": "c3-private-quote",
            }
        ),
        key="c3-private-quote",
    )
    bars = tuple(
        MinuteBarInput(
            security_id="SYNTH-C1-1",
            event_at=start + timedelta(minutes=minute),
            received_at=start + timedelta(minutes=minute, seconds=1),
            available_at=start + timedelta(minutes=minute, seconds=2),
            revision=1,
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=Decimal("100"),
            amount=None,
            final=True,
        )
        for minute, close in ((0, "100"), (1, "101"), (3, "103"))
    )
    minute = MinuteBatchInput(
        source_id=source,
        batch_id="c3-minute",
        source_version="c3-v1",
        source_event_at=start,
        received_at=bars[-1].received_at,
        available_at=bars[-1].available_at,
        permission=permission,
        session_start=start,
        session_end=start + timedelta(minutes=4),
        bars=bars,
    )
    repo.ingest_minute_batch(minute, key="c3-minute")
    # Equal availability times exercise signed cursor tie-breaking across 3 pages.
    rules = tuple(
        AnomalyRule(
            rule_version=f"c3-return-{index:03d}" if index < 110 else "c3-volume-110",
            metric="window_return" if index < 110 else "relative_volume",
            window=3,
            threshold=Decimal("0.02") if index < 110 else Decimal("1"),
            cooldown_seconds=300,
        )
        for index in range(111)
    )
    # Events are generated by the repository, never by browser-side formulas.
    anomaly_bars = tuple(
        bar.model_copy(
            update={
                "event_at": start + timedelta(minutes=index),
                "received_at": start + timedelta(minutes=index, seconds=1),
                "available_at": start + timedelta(minutes=index, seconds=2),
                "revision": 2,
            }
        )
        for index, bar in enumerate(bars)
    )
    anomaly_batch = minute.model_copy(
        update={
            "batch_id": "c3-anomaly-minutes",
            "bars": anomaly_bars,
        }
    )
    accepted = repo.ingest_minute_batch(
        anomaly_batch, key="c3-anomaly-minutes", rules=rules
    )
    duplicate = repo.ingest_minute_batch(
        anomaly_batch, key="c3-anomaly-replay", rules=rules
    )
    assert duplicate.status == "duplicate"
    assert duplicate.payload_hash == accepted.payload_hash
    flow = SectorFlowBatchInput(
        source_id=source,
        batch_id="c3-flow",
        source_version="c3-v1",
        source_event_at=start,
        received_at=start + timedelta(seconds=1),
        available_at=start + timedelta(seconds=2),
        permission=permission,
        session_start=start,
        session_end=start + timedelta(minutes=4),
        points=(
            SectorFlowPoint(
                sector_id="SYNTH-SECTOR-1",
                event_at=start,
                net_inflow=None,
                unit="CNY",
                unavailable_reason="synthetic-unavailable",
            ),
        ),
    )
    repo.ingest_sector_flow_batch(flow, key="c3-flow")
    correction = bars[0].model_copy(
        update={
            "revision": 3,
            "open": Decimal("105"),
            "high": Decimal("105"),
            "low": Decimal("105"),
            "close": Decimal("105"),
            "received_at": start + timedelta(minutes=4),
            "available_at": start + timedelta(minutes=4),
        }
    )
    repo.ingest_minute_batch(
        minute.model_copy(
            update={
                "batch_id": "c3-correction",
                "bars": (correction,),
                "received_at": correction.received_at,
                "available_at": correction.available_at,
            }
        ),
        key="c3-correction",
    )
    gates = []
    for kind, parent_minutes, bar_minutes, event_index in (
        ("parent", 15, 10, 0),
        ("bar", 10, 15, 1),
    ):
        gate_bar = bars[event_index].model_copy(
            update={
                "revision": 4,
                "received_at": start + timedelta(minutes=bar_minutes),
                "available_at": start + timedelta(minutes=bar_minutes),
            }
        )
        gate = minute.model_copy(
            update={
                "batch_id": f"c3-{kind}-late",
                "bars": (gate_bar,),
                "received_at": start + timedelta(minutes=parent_minutes),
                "available_at": start + timedelta(minutes=parent_minutes),
            }
        )
        repo.ingest_minute_batch(gate, key=f"c3-{kind}-late")
        boundary = start + timedelta(minutes=max(parent_minutes, bar_minutes))
        gates.append(
            {
                "kind": kind,
                "batch": gate.batch_id,
                "boundary": boundary.isoformat(),
                "before": (boundary - timedelta(microseconds=1)).isoformat(),
                "after": (boundary + timedelta(microseconds=1)).isoformat(),
            }
        )
    from sqlalchemy import select

    from dragon_radar.realtime.tables import realtime_anomaly_events

    with database.engine.connect() as connection:
        event_ids = list(connection.scalars(select(realtime_anomaly_events.c.event_id)))
    assert len(event_ids) == 111
    return {
        "source": source,
        "date": "2026-09-08",
        "security": "SYNTH-C1-1",
        "sector": "SYNTH-SECTOR-1",
        "pit": now.isoformat(),
        "quote": "c3-quote",
        "minute": "c3-minute",
        "flow": "c3-flow",
        "old_pit": minute.available_at.isoformat(),
        "gates": gates,
        "event_ids": event_ids,
        "duplicate_anomaly_replay": {
            "status": duplicate.status,
            "same_payload_hash": True,
            "unique_events_after_replay": len(event_ids),
        },
    }


def late_anomaly(database):
    from dragon_radar.realtime.models import (
        AnomalyRule,
        MinuteBarInput,
        MinuteBatchInput,
        PermissionSnapshot,
    )
    from dragon_radar.realtime.repository import RealtimeRepository

    start = datetime(2026, 9, 8, 1, 30, tzinfo=UTC)
    bars = tuple(
        MinuteBarInput(
            security_id="SYNTH-C1-1",
            event_at=start + timedelta(minutes=30 + index),
            received_at=start + timedelta(minutes=30 + index, seconds=1),
            available_at=start + timedelta(minutes=30 + index, seconds=2),
            revision=5,
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=Decimal("100"),
            amount=None,
            final=True,
        )
        for index, close in enumerate(("100", "104"))
    )
    batch = MinuteBatchInput(
        source_id="synthetic-c3-rt",
        batch_id="c3-post-first-commit",
        source_version="c3-v1",
        source_event_at=bars[0].event_at,
        received_at=bars[-1].received_at,
        available_at=bars[-1].available_at,
        permission=PermissionSnapshot(
            permission_id="c3-permission",
            state="authorized",
            captured_at=start,
        ),
        session_start=start,
        session_end=start + timedelta(minutes=60),
        bars=bars,
    )
    result = RealtimeRepository(database).ingest_minute_batch(
        batch,
        key="c3-late",
        rules=(
            AnomalyRule(
                rule_version="c3-late",
                metric="window_return",
                window=2,
                threshold=Decimal("0.02"),
                cooldown_seconds=300,
            ),
        ),
    )
    return result.model_dump(mode="json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("ai", "realtime", "late", "revoke"))
    args = parser.parse_args()
    database = fixture_database()
    try:
        if args.operation == "ai":
            result = ai_evidence(database)
        elif args.operation == "realtime":
            result = realtime_inputs(database)
        elif args.operation == "revoke":
            from dragon_radar.storage.repository import Repository

            Repository(database).revoke_source("synthetic-c3-rt")
            result = {"revoked": True}
        else:
            result = late_anomaly(database)
        print(encoded(result).decode())
    finally:
        database.close()


if __name__ == "__main__":
    main()
