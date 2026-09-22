"""Lease-based execution of custom analysis against published inputs."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from dragon_radar.analysis_jobs.models import AnalysisJobCommand, AnalysisJobRecord
from dragon_radar.analysis_jobs.repository import AnalysisJobRepository
from dragon_radar.publication.models import PublishedSnapshot
from dragon_radar.readmodels.analysis import _window
from dragon_radar.readmodels.projection import MarketProjection
from dragon_radar.storage.repository import Forbidden, NotFound


def materialize_custom_analysis(
    snapshot: PublishedSnapshot,
    command: AnalysisJobCommand,
) -> dict[str, Any]:
    """Compute one request from the immutable market batch bound to a publication."""
    batch = snapshot.batch
    sessions = tuple(
        day for day in batch.calendar.sessions if command.start <= day <= command.end
    )
    if not sessions:
        raise ValueError("Analysis window has no trading sessions")
    known = set(batch.expected_security_ids)
    projection = MarketProjection(batch)
    if any(
        symbol not in known
        or (identity := projection.state(symbol, batch.trade_date)) is None
        or identity.security_type != "common_stock"
        for symbol in command.symbols
    ):
        raise NotFound("Stock not found")

    if command.adjustment in {"qfq", "hfq"}:
        return {
            "schema": "dragon-radar.custom-analysis",
            "schema_version": 1,
            "algorithm_version": command.algorithm_version,
            "snapshot_id": str(snapshot.id),
            "requested_start": command.start.isoformat(),
            "requested_end": command.end.isoformat(),
            "adjusted_start": sessions[0].isoformat(),
            "adjusted_end": sessions[-1].isoformat(),
            "benchmark": command.benchmark,
            "adjustment": command.adjustment,
            "available": False,
            "reason": (
                f"{command.adjustment}_semantics_unproven_from_generic_"
                "adjustment_factor"
            ),
            "ranking": [],
            "exclusions": [],
            "curves": [],
        }

    window = _window(
        projection,
        None,
        sessions,
        command.benchmark,
    )
    projected = window["adjustments"][command.adjustment]
    stored_curves = projected["curves"]
    requested = set(command.symbols)
    ranking = [
        dict(row) for row in projected["ranking"] if row["security_id"] in requested
    ]
    previous_return = None
    current_rank = 0
    for position, row in enumerate(ranking, start=1):
        if previous_return is None or row["return_ratio"] != previous_return:
            current_rank = position
            previous_return = row["return_ratio"]
        row["rank"] = current_rank
    curves = [
        {
            "security_id": symbol,
            "points": [
                {
                    "trade_date": day.isoformat(),
                    "normalized": stored_curves[symbol]["normalized"][index],
                    "drawdown": stored_curves[symbol]["drawdown"][index],
                }
                for index, day in enumerate(sessions)
            ],
            "reasons": stored_curves[symbol]["reasons"],
        }
        for symbol in command.symbols
    ]
    return {
        "schema": "dragon-radar.custom-analysis",
        "schema_version": 1,
        "algorithm_version": command.algorithm_version,
        "snapshot_id": str(snapshot.id),
        "requested_start": command.start.isoformat(),
        "requested_end": command.end.isoformat(),
        "adjusted_start": sessions[0].isoformat(),
        "adjusted_end": sessions[-1].isoformat(),
        "benchmark": command.benchmark,
        "adjustment": command.adjustment,
        "available": True,
        "reason": None,
        "ranking": ranking,
        "exclusions": [
            row for row in projected["exclusions"] if row["security_id"] in requested
        ],
        "benchmark_return_ratio": projected["benchmark_return_ratio"],
        "benchmark_reasons": projected["benchmark_reasons"],
        "curves": curves,
    }


class AnalysisJobWorker:
    def __init__(
        self,
        repository: AnalysisJobRepository,
        *,
        worker_id: str,
        lease_for: timedelta = timedelta(seconds=30),
        compute: Callable[
            [PublishedSnapshot, AnalysisJobCommand], dict[str, Any]
        ] = materialize_custom_analysis,
    ):
        self.repository = repository
        self.worker_id = worker_id
        self.lease_for = lease_for
        self.compute = compute

    def run_once(self) -> AnalysisJobRecord | None:
        lease = self.repository.claim(self.worker_id, lease_for=self.lease_for)
        if lease is None:
            return None
        try:
            snapshot = self.repository.load_input(lease)
            result = self.compute(snapshot, lease.job.command)
        except Forbidden:
            return self.repository.fail(lease, {"code": "input_forbidden"})
        except NotFound:
            return self.repository.fail(lease, {"code": "input_unavailable"})
        except Exception as error:
            return self.repository.fail(
                lease,
                {
                    "code": "analysis_failed",
                    "error_type": type(error).__name__,
                },
            )
        return self.repository.succeed(lease, result)


__all__ = ["AnalysisJobWorker", "materialize_custom_analysis"]
