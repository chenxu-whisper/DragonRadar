"""Owner-only HTTP routes for queued analysis jobs."""

from datetime import date
from uuid import UUID

from fastapi import FastAPI, Request

from dragon_radar.analysis_jobs.models import AnalysisJobCommand, AnalysisJobRecord
from dragon_radar.analysis_jobs.repository import AnalysisJobRepository
from dragon_radar.owner.security import (
    OwnerRead,
    OwnerWrite,
    invalid_request,
    json_object,
    storage_unavailable,
)


def _repository(request: Request) -> AnalysisJobRepository:
    repository = request.app.state.analysis_repository
    if not isinstance(repository, AnalysisJobRepository):
        raise storage_unavailable()
    return repository


def _no_query(request: Request) -> None:
    if request.query_params:
        raise invalid_request()


def _date(value) -> date:
    if not isinstance(value, str):
        raise invalid_request()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise invalid_request() from None


def _command(body: dict) -> AnalysisJobCommand:
    symbols = body.get("symbols", [])
    if not isinstance(symbols, list):
        raise invalid_request()
    try:
        return AnalysisJobCommand(
            snapshot_id=UUID(body["snapshot_id"]),
            start=_date(body["start"]),
            end=_date(body["end"]),
            benchmark=body.get("benchmark", "B000300"),
            adjustment=body.get("adj", "raw"),
            symbols=tuple(symbols),
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise invalid_request() from None


def _safe_record(record: AnalysisJobRecord) -> dict:
    command = record.command
    return {
        "job_id": str(record.id),
        "status": record.status,
        "result": record.result,
        "failure": record.failure,
        "input": {
            "snapshot_id": str(command.snapshot_id),
            "start": command.start.isoformat(),
            "end": command.end.isoformat(),
            "benchmark": command.benchmark,
            "adj": command.adjustment,
            "symbols": list(command.symbols),
        },
    }


def install_analysis_job_routes(app: FastAPI) -> None:
    @app.post("/api/analysis/jobs")
    def submit_job(request: Request, owner: OwnerWrite):
        _no_query(request)
        body = json_object(
            request,
            {
                "snapshot_id",
                "start",
                "end",
                "benchmark",
                "adj",
                "symbols",
                "idempotency_key",
            },
        )
        if not all(key in body for key in ("snapshot_id", "start", "end")):
            raise invalid_request()
        key = body.get("idempotency_key")
        if not isinstance(key, str):
            raise invalid_request()
        command = _command(body)
        assert owner.csrf is not None
        record = _repository(request).submit(
            owner.token,
            owner.csrf,
            command,
            key=key,
        )
        return {"job_id": str(record.id), "status": record.status}

    @app.get("/api/analysis/jobs/{job_id}")
    def get_job(job_id: UUID, request: Request, owner: OwnerRead):
        _no_query(request)
        record = _repository(request).get(owner.token, job_id)
        return _safe_record(record)


__all__ = ["install_analysis_job_routes"]
