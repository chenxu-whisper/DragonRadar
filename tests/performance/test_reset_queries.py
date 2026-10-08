"""Deterministic PostgreSQL performance and public no-write acceptance evidence."""

import asyncio
import hashlib
import json
import math
import os
import platform
import re
import time
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from factories import NOW, bar, batch, grant, state
from httpx import ASGITransport, AsyncByteStream, AsyncClient
from sqlalchemy import event, inspect, text

from dragon_radar.analysis_jobs.models import AnalysisJobCommand
from dragon_radar.analysis_jobs.repository import AnalysisJobRepository
from dragon_radar.analysis_jobs.worker import (
    AnalysisJobWorker,
    materialize_custom_analysis,
)
from dragon_radar.domain.models import ExchangeCalendar
from dragon_radar.http.access import public_publication_repository
from dragon_radar.http.public_app import create_public_app
from dragon_radar.owner.repository import OwnerRepository
from dragon_radar.publication.models import PublicationCommand
from dragon_radar.publication.repository import PublicationRepository
from dragon_radar.publication.service import PublicationService

pytestmark = pytest.mark.anyio

CONCURRENCY = 20
MEASURED_ROUNDS = 3
SECURITY_COUNT = 100
SESSION_COUNT = 61
P95_LIMIT_SECONDS = 2.0
FIXTURE_COUNTS = {
    "securities": SECURITY_COUNT,
    "security_read_model_rows": 0,
    "trading_sessions": SESSION_COUNT,
    "publications": 1,
    "owner_sessions": 1,
    "analysis_jobs": 1,
    "completed_analysis_jobs": 1,
}
MEASURED_PATHS = (
    "/api/snapshots/{id}",
    "/api/market/overview",
    "/api/rank/range",
    "/api/stock/{id}",
)
CURRENT_PUBLIC_PATH: ContextVar[str] = ContextVar(
    "task20_current_public_path", default="unattributed"
)
WRITE_VERBS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "CREATE",
    "ALTER",
    "DROP",
    "TRUNCATE",
}


class _EmptyRequestStream(AsyncByteStream):
    async def __aiter__(self):
        if False:
            yield b""


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _shared_grant():
    return grant(sharing=True, reference="task20-synthetic-public-fixture")


def _sessions(count: int = SESSION_COUNT) -> tuple[date, ...]:
    values = []
    current = date(2026, 9, 8)
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current)
        current -= timedelta(days=1)
    return tuple(reversed(values))


def _performance_batch():
    sessions = _sessions()
    security_ids = tuple(f"S{index:03d}" for index in range(SECURITY_COUNT))
    identities = tuple(
        state(
            security_id,
            effective_from=sessions[0],
            available_at=datetime(2026, 5, 1, tzinfo=UTC),
        )
        for security_id in security_ids
    ) + (
        state(
            "B000300",
            name="Synthetic Benchmark",
            security_type="index",
            effective_from=sessions[0],
            available_at=datetime(2026, 5, 1, tzinfo=UTC),
        ),
    )
    bars = []
    for session_index, session in enumerate(sessions):
        for security_index, security_id in enumerate((*security_ids, "B000300")):
            close = (
                Decimal("100")
                + Decimal(session_index) / Decimal("10")
                + Decimal(security_index) / Decimal("100")
            )
            bars.append(
                bar(
                    security_id,
                    trade_date=session,
                    close=close,
                    amount=Decimal("1000000") + Decimal(session_index * 1000),
                )
            )
    return batch(
        trade_date=sessions[-1],
        as_of=NOW,
        calendar=ExchangeCalendar(
            exchange="TEST",
            version="c1",
            sessions=sessions,
        ),
        expected_security_ids=security_ids,
        security_states=identities,
        bars=tuple(bars),
    )


def _application_state(database):
    with database.engine.connect() as connection:
        quote = connection.dialect.identifier_preparer.quote
        state = {}
        for table in sorted(inspect(connection).get_table_names()):
            quoted = quote(table)
            row = (
                connection.execute(
                    text(
                        "SELECT count(*) AS rows, "
                        "md5(coalesce(string_agg(md5(row_to_json(t)::text), '' "
                        "ORDER BY md5(row_to_json(t)::text)), '')) AS fingerprint "
                        f"FROM {quoted} AS t"
                    )
                )
                .mappings()
                .one()
            )
            state[table] = {
                "rows": row["rows"],
                "fingerprint": row["fingerprint"],
            }
        return state


def _memory_bytes() -> tuple[int, int, str]:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        success = psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(),
            ctypes.byref(counters),
            counters.cb,
        )
        if not success:
            raise ctypes.WinError(ctypes.get_last_error())
        return (
            counters.PeakWorkingSetSize,
            counters.WorkingSetSize,
            "windows_working_set",
        )
    import resource

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    multiplier = 1 if platform.system() == "Darwin" else 1024
    peak = value * multiplier
    if Path("/proc/self/statm").is_file():
        pages = int(Path("/proc/self/statm").read_text().split()[1])
        return peak, pages * os.sysconf("SC_PAGE_SIZE"), "proc_statm_and_ru_maxrss"
    return peak, peak, "ru_maxrss"


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _request_paths(publication_id, sessions, security_id):
    return (
        f"/api/snapshots/{publication_id}",
        f"/api/market/overview?snapshot_id={publication_id}",
        (
            f"/api/rank/range?snapshot_id={publication_id}"
            f"&start={sessions[0]}&end={sessions[-1]}"
            "&benchmark=B000300&adj=raw&sort=return"
        ),
        f"/api/stock/{security_id}?snapshot_id={publication_id}",
    )


def _path_label(path: str) -> str:
    if path.startswith("/api/snapshots/"):
        return "/api/snapshots/{id}"
    if path.startswith("/api/market/overview"):
        return "/api/market/overview"
    if path.startswith("/api/rank/range"):
        return "/api/rank/range"
    if path.startswith("/api/stock/"):
        return "/api/stock/{id}"
    raise AssertionError(f"Unexpected measured path: {path}")


async def _request(client: AsyncClient, path: str):
    started = time.perf_counter()
    label = _path_label(path)
    token = CURRENT_PUBLIC_PATH.set(label)
    try:
        outgoing = client.build_request("GET", path, content=_EmptyRequestStream())
        outgoing.headers.pop("transfer-encoding", None)
        response = await client.send(outgoing)
        return label, response.status_code, time.perf_counter() - started
    finally:
        CURRENT_PUBLIC_PATH.reset(token)


async def _run(
    client: AsyncClient,
    paths: tuple[str, ...],
    statements: list[dict],
    round_number: int,
):
    requests = tuple(paths[index % len(paths)] for index in range(CONCURRENCY))

    before_queries = len(statements)
    cpu_started = time.process_time()
    started = time.perf_counter()
    responses = await asyncio.gather(*(_request(client, path) for path in requests))
    elapsed = time.perf_counter() - started
    cpu = time.process_time() - cpu_started
    latencies = [latency for _path, _status, latency in responses]
    statuses = [status for _path, status, _latency in responses]
    path_metrics = {}
    for path in MEASURED_PATHS:
        path_responses = [
            (status, latency)
            for response_path, status, latency in responses
            if response_path == path
        ]
        path_latencies = [latency for _status, latency in path_responses]
        path_metrics[path] = {
            "request_count": len(path_responses),
            "successful": sum(status == 200 for status, _latency in path_responses),
            "p50_seconds": _percentile(path_latencies, 0.50),
            "p95_seconds": _percentile(path_latencies, 0.95),
            "max_seconds": max(path_latencies),
        }
    peak, rss, memory_label = _memory_bytes()
    return {
        "round": round_number,
        "cache_condition": "post_warmup_caches_uncontrolled",
        "request_count": len(responses),
        "concurrency": CONCURRENCY,
        "successful": sum(status == 200 for status in statuses),
        "error_rate": sum(status != 200 for status in statuses) / len(statuses),
        "p50_seconds": _percentile(latencies, 0.50),
        "p95_seconds": _percentile(latencies, 0.95),
        "max_seconds": max(latencies),
        "wall_seconds": elapsed,
        "process_cpu_seconds": cpu,
        "peak_memory_bytes": peak,
        "rss_bytes": rss,
        "memory_measurement": memory_label,
        "sql_statement_count": len(statements) - before_queries,
        "path_metrics": path_metrics,
    }, latencies


def _statement_verb(statement: str) -> str:
    normalized = statement.lstrip().upper()
    first = normalized.split(maxsplit=1)[0] if normalized else "UNKNOWN"
    if first == "WITH":
        for verb in WRITE_VERBS:
            if re.search(rf"\b{verb}\b", normalized):
                return verb
        return "SELECT"
    return first


def _measured_path_explains(database, captured):
    explains = {}
    with database.engine.connect() as connection:
        for path in MEASURED_PATHS:
            candidates = [
                item
                for item in captured
                if item["path"] == path
                and item["verb"] == "SELECT"
                and " FROM " in f" {' '.join(item['statement'].split()).upper()} "
            ]
            preferred = [
                item
                for item in candidates
                if "PUBLICATION" in item["statement"].upper()
            ]
            selected = (preferred or candidates)[0]
            plan = connection.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + selected["statement"],
                selected["parameters"],
            ).scalar()
            explains[path] = {
                "sql": " ".join(selected["statement"].split()),
                "sql_sha256": hashlib.sha256(
                    selected["statement"].encode("utf-8")
                ).hexdigest(),
                "plan": plan,
            }
    return explains


async def test_reset_public_queries_meet_budget_and_do_not_write(database, repository):
    embedded = _shared_grant()
    fixture = _performance_batch().model_copy(update={"grant": embedded})
    sessions = fixture.calendar.sessions
    security_id = fixture.expected_security_ids[0]
    market = repository.publish(
        repository.import_batch(fixture, key="task20-performance-market").id
    )
    publication = PublicationService(database, repository, clock=lambda: NOW).publish(
        PublicationCommand(
            trade_date=sessions[-1],
            market_snapshot_id=market.id,
            disabled_domains=("topic", "news", "regulation", "role"),
        ),
        key="task20-performance-publication",
    )
    repository.register_source(_shared_grant())

    owners = OwnerRepository(database, clock=lambda: NOW)
    owners.initialize()
    session = owners.exchange_pairing(
        owners.issue_pairing("https://localhost:8030"),
        "https://localhost:8030",
    )
    jobs = AnalysisJobRepository(
        database,
        owners,
        PublicationRepository(database, repository, clock=lambda: NOW),
        clock=lambda: NOW,
    )
    submission_started = time.perf_counter()
    queued = jobs.submit(
        session.token,
        session.csrf,
        AnalysisJobCommand(
            snapshot_id=publication.id,
            start=sessions[0],
            end=sessions[-1],
            benchmark="B000300",
            adjustment="raw",
            symbols=(security_id,),
        ),
        key="task20-actual-over-five-seconds",
    )
    submission_seconds = time.perf_counter() - submission_started
    assert queued.status == "queued"

    def delayed_compute(snapshot, command):
        time.sleep(5.1)
        return materialize_custom_analysis(snapshot, command)

    worker_started = time.perf_counter()
    completed = AnalysisJobWorker(
        jobs,
        worker_id="task20-synthetic-delay-worker",
        compute=delayed_compute,
    ).run_once()
    worker_elapsed_seconds = time.perf_counter() - worker_started
    assert completed is not None

    with database.engine.connect() as connection:
        counts = {
            "securities": len(market.batch.expected_security_ids),
            "security_read_model_rows": connection.scalar(
                text("SELECT count(*) FROM security")
            ),
            "trading_sessions": len(sessions),
            "publications": connection.scalar(
                text("SELECT count(*) FROM publications")
            ),
            "owner_sessions": connection.scalar(
                text("SELECT count(*) FROM owner_sessions")
            ),
            "analysis_jobs": connection.scalar(
                text("SELECT count(*) FROM analysis_jobs")
            ),
            "completed_analysis_jobs": connection.scalar(
                text("SELECT count(*) FROM analysis_jobs WHERE status = 'succeeded'")
            ),
        }
        volume = {
            "market_archive_bytes": connection.scalar(
                text(
                    "SELECT octet_length(canonical_json) FROM import_batches "
                    "WHERE id = :id"
                ),
                {"id": market.batch_id},
            ),
            "publication_payload_bytes": connection.scalar(
                text(
                    "SELECT sum(pg_column_size(payload)) FROM publication_results "
                    "WHERE publication_id = :id"
                ),
                {"id": publication.id},
            ),
        }
        db_version = connection.scalar(text("SHOW server_version"))
    assert counts == FIXTURE_COUNTS
    assert all(value and value > 0 for value in volume.values())

    paths = _request_paths(publication.id, sessions, security_id)
    assert tuple(_path_label(path) for path in paths) == MEASURED_PATHS
    captured = []

    def capture_query(_conn, _cursor, statement, parameters, _context, _executemany):
        captured.append(
            {
                "path": CURRENT_PUBLIC_PATH.get(),
                "verb": _statement_verb(statement),
                "statement": statement,
                "parameters": parameters,
            }
        )

    public = public_publication_repository(database, repository, clock=lambda: NOW)
    app = create_public_app(repository=public)
    assert not any(
        getattr(route, "path", "").startswith("/api/analysis/jobs")
        for route in app.router.routes
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://public.example",
    ) as client:
        warmup_requests = tuple(
            paths[index % len(paths)] for index in range(CONCURRENCY)
        )
        warmup_responses = await asyncio.gather(
            *(_request(client, path) for path in warmup_requests)
        )
        warmup = {
            "paths": list(MEASURED_PATHS),
            "request_count": len(warmup_responses),
            "concurrency": CONCURRENCY,
            "successful": sum(
                status == 200 for _path, status, _latency in warmup_responses
            ),
        }
        assert warmup["successful"] == CONCURRENCY
        state_before = _application_state(database)
        event.listen(database.engine, "before_cursor_execute", capture_query)
        try:
            measured = [
                await _run(client, paths, captured, round_number)
                for round_number in range(1, MEASURED_ROUNDS + 1)
            ]
            runs = [item[0] for item in measured]
            all_latencies = [
                latency for _run_metrics, latencies in measured for latency in latencies
            ]
        finally:
            event.remove(database.engine, "before_cursor_execute", capture_query)
    measured_path_explains = _measured_path_explains(database, captured)
    state_after = _application_state(database)

    for run in runs:
        assert run["request_count"] == CONCURRENCY
        assert run["successful"] == CONCURRENCY
        assert run["error_rate"] == 0
        assert run["p95_seconds"] <= P95_LIMIT_SECONDS, run
        assert run["peak_memory_bytes"] > 0
        assert run["process_cpu_seconds"] >= 0
    assert state_after == state_before
    aggregate = {
        "round_count": len(runs),
        "request_count": sum(run["request_count"] for run in runs),
        "successful": sum(run["successful"] for run in runs),
        "error_rate": (
            sum(run["request_count"] - run["successful"] for run in runs)
            / sum(run["request_count"] for run in runs)
        ),
        "p50_seconds": _percentile(all_latencies, 0.50),
        "p95_seconds": _percentile(all_latencies, 0.95),
        "max_seconds": max(all_latencies),
        "wall_seconds": sum(run["wall_seconds"] for run in runs),
        "process_cpu_seconds": sum(run["process_cpu_seconds"] for run in runs),
    }
    assert aggregate["request_count"] == MEASURED_ROUNDS * CONCURRENCY
    assert aggregate["successful"] == aggregate["request_count"]
    assert aggregate["p95_seconds"] <= P95_LIMIT_SECONDS

    evidence = {
        "fixture": {
            "kind": "synthetic",
            "counts": counts,
            "data_volume": volume,
            "controlled_worker_delay_seconds": 5.1,
            "owner_execution": "queued_async",
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "postgresql": db_version,
            "os_cold_cache_controlled": False,
            "cache_claim": "warmup_then_measured_caches_uncontrolled",
        },
        "warmup": warmup,
        "runs": runs,
        "aggregate": aggregate,
        "async_work": {
            "synthetic": True,
            "label": "controlled_worker_delay",
            "submission_seconds": submission_seconds,
            "submitted_status": queued.status,
            "worker_elapsed_seconds": worker_elapsed_seconds,
            "status": completed.status,
        },
        "public_sql_statements": [
            {
                "path": item["path"],
                "verb": item["verb"],
                "sql": " ".join(item["statement"].split()),
                "sql_sha256": hashlib.sha256(
                    item["statement"].encode("utf-8")
                ).hexdigest(),
            }
            for item in captured
        ],
        "application_state_before": state_before,
        "application_state_after": state_after,
        "measured_path_explains": measured_path_explains,
    }
    assert evidence["async_work"]["synthetic"] is True
    assert evidence["async_work"]["submission_seconds"] < 2
    assert evidence["async_work"]["worker_elapsed_seconds"] > 5
    assert evidence["async_work"]["status"] == "succeeded"
    assert evidence["public_sql_statements"]
    assert not {
        "INSERT",
        "UPDATE",
        "DELETE",
        "CREATE",
        "ALTER",
        "DROP",
        "TRUNCATE",
    } & {statement["verb"] for statement in evidence["public_sql_statements"]}
    assert evidence["application_state_before"] == evidence["application_state_after"]
    assert len(evidence["application_state_before"]) > 8
    assert set(evidence["measured_path_explains"]) == set(MEASURED_PATHS)
    assert {item["path"] for item in evidence["public_sql_statements"]} == set(
        MEASURED_PATHS
    )
    assert all(run["rss_bytes"] > 0 for run in evidence["runs"])
    artifact = Path(".temp/acceptance/reset-performance.json")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(evidence, ensure_ascii=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=True, default=str))
