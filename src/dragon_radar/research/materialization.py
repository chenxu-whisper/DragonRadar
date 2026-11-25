"""Worker-only health and authorized historical frame materialization."""

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from hashlib import sha256
from typing import Literal
from uuid import UUID

from dragon_radar.domain.models import Identifier
from dragon_radar.research.health import FactorHealthReport, assess_factor_health
from dragon_radar.research.models import BacktestPeriod, StrictFrozenModel
from dragon_radar.research.pit import assemble_pit_window
from dragon_radar.research.result_contracts import (
    RESEARCH_RESULT_SCHEMA_VERSION,
    RESULT_V1,
    ValidatedBacktestResult,
    _wire_shape,
    canonical,
    decimal_string,
    json_value,
    manifests_equal,
    require,
    validate_result,
)

HEALTH_METHOD = {
    "method_version": "factor-health-decimal34-v1",
    "min_sample_size": 5,
    "quantile_points": ["0", "0.25", "0.5", "0.75", "1"],
    "rate_quantum": "0.000001",
    "correlation_method": "equal-session-cross-sectional-spearman-v1",
}


class HealthState(StrictFrozenModel):
    status: Literal["ready", "unavailable"]
    reason: Literal["factor_health_inputs_unavailable"] | None
    report: FactorHealthReport | None


class BenchmarkIdentity(StrictFrozenModel):
    status: Literal["ready", "unavailable"]
    reason: Literal["benchmark_inputs_unavailable"] | None
    definition_sha256: str | None
    source_id: Identifier | None
    source_version: Identifier | None
    input_sha256: str | None


class FrameLineage(StrictFrozenModel):
    trade_date: date
    publication_id: UUID
    publication_sequence: int
    market_snapshot_id: UUID
    topic_snapshot_id: UUID | None
    role_snapshot_id: UUID | None
    frame_sha256: str
    visible_security_ids: tuple[Identifier, ...]
    as_of: datetime


class HealthMethod(StrictFrozenModel):
    method_version: str
    min_sample_size: int
    quantile_points: tuple[Decimal, ...]
    rate_quantum: Decimal
    correlation_method: str


class ResultLineage(StrictFrozenModel):
    anchor_sequence: int
    requested_period: BacktestPeriod
    actual_period: BacktestPeriod
    sessions: tuple[date, ...]
    frames: tuple[FrameLineage, ...]
    benchmark: BenchmarkIdentity
    health_method: HealthMethod
    computed_at: datetime


class MaterializedResult(ValidatedBacktestResult):
    schema_version: Literal["dragon-radar.research-backtest-result/v2"]
    health: HealthState
    lineage: ResultLineage


def digest(value):
    require(
        type(value) is str
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value),
        "SHA256 required",
    )
    return value


def _utc(value):
    require(
        type(value) is str and value.endswith(("Z", "+00:00")),
        "UTC timestamp required",
    )
    time = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(time.tzinfo is not None and time.utcoffset().total_seconds() == 0)
    return time


def validate_health(raw, config, lineage, versions):
    typed = FactorHealthReport.model_validate_json(canonical(raw))
    _wire_shape(raw, typed)
    require(
        typed.anchor_sequence == lineage.anchor_sequence
        and typed.window_start == lineage.actual_period.start
        and typed.window_end == lineage.actual_period.end
        and typed.sessions == len(lineage.sessions)
        and typed.primary_factor_id == config.parameters.factor_id
        and manifests_equal(typed.manifests, config.input_manifests),
        "health context mismatch",
    )
    ids = [f.factor_id for f in typed.factors]
    require(ids == sorted(set(ids)))
    expected = sum(len(frame.visible_security_ids) for frame in lineage.frames)
    for index, factor in enumerate(typed.factors):
        require(
            factor.sessions == typed.sessions and factor.coverage.expected == expected
        )
        if factor.factor_id == config.parameters.factor_id:
            require(
                (factor.metric, factor.window, factor.unit)
                == (
                    versions.factor_metric,
                    versions.factor_window,
                    versions.factor_unit,
                )
            )
        coverage = factor.coverage
        require(factor.sample_size == coverage.valid)
        for name in ("valid", "excluded", "missing"):
            with localcontext() as ctx:
                ctx.prec = 34
                rate = (
                    Decimal(getattr(coverage, name)) / coverage.expected
                    if coverage.expected
                    else Decimal(0)
                ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
            require(decimal_string(raw["factors"][index][name + "_rate"], 6) == rate)
        require((factor.status == "ok") == (factor.sample_size >= 5))
        if factor.quantiles is not None:
            require(
                raw["factors"][index]["quantiles"]["points"]
                == HEALTH_METHOD["quantile_points"]
            )
            values = factor.quantiles.values
            require(tuple(sorted(values)) == values)
            for value in raw["factors"][index]["quantiles"]["values"]:
                decimal_string(value, 6)
    pairs = []
    for index, correlation in enumerate(typed.rank_correlations):
        require(correlation.left in ids and correlation.right in ids)
        require(correlation.left < correlation.right)
        pair = (correlation.left, correlation.right)
        require(pair not in pairs)
        pairs.append(pair)
        require(
            0 <= correlation.paired_sample_size <= expected
            and 0 <= correlation.eligible_sessions <= typed.sessions
        )
        if correlation.status == "ok":
            require(
                correlation.paired_sample_size >= 5 and -1 <= correlation.value <= 1
            )
            decimal_string(raw["rank_correlations"][index]["value"], 6)
    require(pairs == [(a, b) for i, a in enumerate(ids) for b in ids[i + 1 :]])
    return typed


def validate_v2(payload, config, *, sessions=None):
    raw = json_value(payload)
    typed = MaterializedResult.model_validate_json(canonical(raw))
    _wire_shape(raw, typed)
    base = {
        key: value for key, value in raw.items() if key not in ("health", "lineage")
    }
    base["schema_version"] = RESULT_V1
    validate_result(base, config, sessions=sessions)
    lineage = typed.lineage
    actual_sessions = tuple(point.trade_date for point in typed.equity)
    require(
        lineage.requested_period == config.period
        and lineage.sessions == actual_sessions
        and len(lineage.frames) == len(actual_sessions)
        and lineage.actual_period.start == actual_sessions[0]
        and lineage.actual_period.end == actual_sessions[-1]
        and 1 <= lineage.anchor_sequence
        and raw["lineage"]["health_method"] == HEALTH_METHOD
    )
    completed = _utc(raw["lineage"]["computed_at"])
    previous_sequence = 0
    pub_ids = set()
    for frame, session in zip(lineage.frames, actual_sessions, strict=True):
        require(
            frame.trade_date == session
            and previous_sequence
            < frame.publication_sequence
            <= lineage.anchor_sequence
            and frame.publication_id not in pub_ids
            and len(frame.visible_security_ids) <= 5000
            and frame.visible_security_ids
            == tuple(sorted(set(frame.visible_security_ids)))
        )
        digest(frame.frame_sha256)
        _utc(raw["lineage"]["frames"][len(pub_ids)]["as_of"])
        require(frame.as_of <= completed)
        previous_sequence = frame.publication_sequence
        pub_ids.add(frame.publication_id)
    last = lineage.frames[-1]
    domains = {d.domain: d.snapshot_id for d in config.input_manifests.domains}
    require(
        last.publication_id == config.snapshot_id
        and last.publication_sequence == lineage.anchor_sequence
        and last.market_snapshot_id == domains["market"]
        and last.topic_snapshot_id == domains.get("topic")
        and last.role_snapshot_id == domains.get("role"),
        "anchor manifest mismatch",
    )
    by_day = {f.trade_date: set(f.visible_security_ids) for f in lineage.frames}
    for quality in typed.quality:
        if quality.factor_coverage is not None:
            require(quality.factor_coverage.expected == len(by_day[quality.trade_date]))
        for event in quality.events:
            require(
                event.security_id is None
                or event.security_id in by_day[event.trade_date]
            )
    for artifact in (*typed.trades, *typed.rejections):
        require(artifact.security_id in by_day[artifact.signal_date])
    for fold in typed.folds or ():
        for artifact in (*fold.trades, *fold.rejections):
            require(artifact.security_id in by_day[artifact.signal_date])
    if typed.health.status == "ready":
        require(typed.health.reason is None and typed.health.report is not None)
        validate_health(raw["health"]["report"], config, lineage, typed.versions)
    else:
        require(
            typed.health.report is None
            and typed.health.reason == "factor_health_inputs_unavailable"
        )
    benchmark = typed.lineage.benchmark
    facts = ("definition_sha256", "source_id", "source_version", "input_sha256")
    if benchmark.status == "unavailable":
        require(
            benchmark.reason == "benchmark_inputs_unavailable"
            and all(getattr(benchmark, name) is None for name in facts)
        )
    else:
        require(
            benchmark.reason is None and all(getattr(benchmark, name) for name in facts)
        )
        digest(benchmark.definition_sha256)
        digest(benchmark.input_sha256)
    return typed


def authorized_frame_mapping(frames, *, anchor, publications, policy=None):
    """Derive trusted provenance from authorized loader inputs, never a result."""
    window = assemble_pit_window(frames, anchor=anchor, policy=policy)
    require(len(publications) == len(frames) == len(window.days))
    line_frames = []
    for frame, pub, day in zip(frames, publications, window.days, strict=True):
        require(
            digest(pub["frame_sha256"])
            == sha256(canonical(frame.model_dump(mode="json"))).hexdigest()
            and pub["publication_sequence"] == frame.publication_sequence
            and (pub["topic_snapshot_id"] is None) == (frame.topic is None),
            "authorized frame mapping mismatch",
        )
        as_of = (
            min(frame.market.as_of, frame.topic.as_of)
            if frame.topic
            else frame.market.as_of
        )
        require(as_of <= _utc(pub["published_at"]))
        line_frames.append(
            {
                "trade_date": frame.trade_date.isoformat(),
                "publication_id": pub["snapshot_id"],
                "publication_sequence": frame.publication_sequence,
                "market_snapshot_id": pub["market_snapshot_id"],
                "topic_snapshot_id": pub["topic_snapshot_id"],
                "role_snapshot_id": pub.get("role_snapshot_id"),
                "frame_sha256": pub["frame_sha256"],
                "visible_security_ids": sorted(s.security_id for s in day.securities),
                "as_of": as_of.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            }
        )
    return line_frames


def materialize_result(
    result,
    config,
    frames,
    factor_streams,
    *,
    anchor,
    publications,
    computed_at,
    policy=None,
    benchmark_identity=None,
    benchmark_closes=None,
):
    """The caller supplies already-authorized PUB mapping and benchmark descriptor."""
    result = json_value(result)
    validate_result(result, config)
    require(
        computed_at.tzinfo is not None and computed_at.utcoffset().total_seconds() == 0,
        "worker completion must be UTC",
    )
    window = assemble_pit_window(frames, anchor=anchor, policy=policy)
    line_frames = authorized_frame_mapping(
        frames, anchor=anchor, publications=publications, policy=policy
    )
    require(all(_utc(pub["published_at"]) <= computed_at for pub in publications))
    benchmark = {
        "status": "unavailable",
        "reason": "benchmark_inputs_unavailable",
        "definition_sha256": None,
        "source_id": None,
        "source_version": None,
        "input_sha256": None,
    }
    if benchmark_identity is not None:
        require(
            benchmark_closes is not None
            and set(benchmark_closes) == {d.trade_date for d in window.days}
        )
        frozen_prices = {
            day.isoformat(): str(close) for day, close in benchmark_closes.items()
        }
        require(
            benchmark_identity["input_sha256"]
            == sha256(canonical(frozen_prices)).hexdigest(),
            "benchmark stream digest mismatch",
        )
        require(
            all(
                isinstance(close, Decimal) and close.is_finite() and close > 0
                for close in benchmark_closes.values()
            ),
            "benchmark requires positive exact closes",
        )

        def verify_benchmark(metrics, equity):
            with localcontext() as ctx:
                ctx.prec = 34
                ctx.rounding = ROUND_HALF_EVEN
                first = benchmark_closes[date.fromisoformat(equity[0]["trade_date"])]
                last = benchmark_closes[date.fromisoformat(equity[-1]["trade_date"])]
                bench = (last - first) / first
                total = (
                    Decimal(metrics["final_equity"])
                    / Decimal(metrics["initial_equity"])
                ) - 1
                quantum = Decimal("0.000001")
                require(
                    Decimal(metrics["benchmark_return"]) == bench.quantize(quantum)
                    and Decimal(metrics["excess_return"])
                    == (total - bench).quantize(quantum),
                    "benchmark summary does not match frozen stream",
                )

        verify_benchmark(result["metrics"], result["equity"])
        for tier in result["baseline"]["cost_sensitivity"]:
            verify_benchmark(tier["metrics"], result["equity"])
        if result["walk_forward"]:
            report = result["walk_forward"]
            verify_benchmark(report["aggregate_metrics"], report["aggregate_equity"])
            for fold in report["folds"]:
                verify_benchmark(fold["metrics"], fold["equity"])
        benchmark = {"status": "ready", "reason": None, **benchmark_identity}
    report = (
        assess_factor_health(
            config, frames, factor_streams, anchor=anchor, policy=policy
        )
        if factor_streams
        else None
    )
    result.update(
        schema_version=RESEARCH_RESULT_SCHEMA_VERSION,
        health={
            "status": "ready" if report else "unavailable",
            "reason": None if report else "factor_health_inputs_unavailable",
            "report": report.model_dump(mode="json") if report else None,
        },
        lineage={
            "anchor_sequence": anchor.publication_sequence,
            "requested_period": config.period.model_dump(mode="json"),
            "actual_period": {
                "start": window.days[0].trade_date.isoformat(),
                "end": window.days[-1].trade_date.isoformat(),
            },
            "sessions": [day.trade_date.isoformat() for day in window.days],
            "frames": line_frames,
            "benchmark": benchmark,
            "health_method": HEALTH_METHOD,
            "computed_at": computed_at.isoformat().replace("+00:00", "Z"),
        },
    )
    validate_v2(result, config)
    return result
