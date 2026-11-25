"""Strict archived research wire contracts; no recomputation or current-PUB IO."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from pydantic import BaseModel

from dragon_radar.research.ablation import AblationReport
from dragon_radar.research.engine import (
    COST_METHOD,
    EXECUTION_METHOD,
    LEDGER_METHOD,
    SIZING_METHOD,
    VALUATION_METHOD,
    BacktestResult,
)
from dragon_radar.research.ledger import Metrics
from dragon_radar.research.models import (
    COST_SENSITIVITY_MULTIPLIERS,
    BacktestConfig,
    InputManifests,
)
from dragon_radar.research.walk_forward import FoldResult, WalkForwardReport

RATIO = Decimal("0.000001")
MONEY = Decimal("0.01")
RESULT_V1 = "dragon-radar.research-backtest/v1"
RESEARCH_RESULT_SCHEMA_VERSION = "dragon-radar.research-backtest-result/v2"


class ResearchDataUnavailable(RuntimeError):
    """Authorized stored material is malformed, not a caller parameter error."""


class ValidatedBacktestResult(BacktestResult):
    metrics: Metrics
    folds: tuple[FoldResult, ...] | None = None
    walk_forward: WalkForwardReport | None = None
    ablation: AblationReport | None = None


def json_value(value):
    if isinstance(value, Mapping):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    return value


def canonical(value) -> bytes:
    return json.dumps(
        json_value(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def require(condition, message="research result contract mismatch"):
    if not condition:
        raise ValueError(message)


def decimal_string(value, places=None):
    pattern = r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
    if places is not None:
        pattern = rf"-?\d+\.\d{{{places}}}"
    require(
        type(value) is str and re.fullmatch(pattern, value) is not None,
        "exact finite decimal string required",
    )
    number = Decimal(value)
    require(number.is_finite(), "finite decimal required")
    return number


def _wire_shape(raw, typed):
    """Defaults on kernel models never excuse missing fields on archived wire."""
    if isinstance(typed, BaseModel):
        fields = {
            field.alias or name: name
            for name, field in type(typed).model_fields.items()
        }
        require(
            isinstance(raw, Mapping) and set(raw) == set(fields),
            "missing or extra research fields",
        )
        for key, name in fields.items():
            _wire_shape(raw[key], getattr(typed, name))
    elif isinstance(typed, Mapping):
        require(
            isinstance(raw, Mapping) and set(raw) == set(typed),
            "mapping contract mismatch",
        )
        for key, value in typed.items():
            _wire_shape(raw[key], value)
    elif isinstance(typed, (tuple, list)):
        require(
            isinstance(raw, (tuple, list)) and len(raw) == len(typed),
            "array contract mismatch",
        )
        for item, value in zip(raw, typed, strict=True):
            _wire_shape(item, value)
    elif isinstance(typed, Decimal):
        decimal_string(raw)
    elif isinstance(typed, datetime):
        require(
            type(raw) is str
            and datetime.fromisoformat(raw.replace("Z", "+00:00")) == typed
        )
    elif isinstance(typed, date):
        require(type(raw) is str and typed.isoformat() == raw, "ISO date required")
    elif type(typed) is bool:
        require(type(raw) is bool and raw == typed, "strict boolean required")
    elif type(typed) is int:
        require(type(raw) is int, "strict integer required")


def manifests_equal(left: InputManifests, right: InputManifests) -> bool:
    return left.versions == right.versions and {
        (x.domain, x.snapshot_id) for x in left.domains
    } == {(x.domain, x.snapshot_id) for x in right.domains}


def _metrics(raw, metrics: Metrics):
    for key, value in raw.items():
        places = (
            2
            if key
            in {
                "initial_equity",
                "final_equity",
                "total_commission",
                "total_stamp_tax",
                "total_cost",
            }
            else 6
        )
        decimal_string(value, places)
    require(metrics.initial_equity > 0 and metrics.final_equity >= 0)
    for key in ("max_drawdown", "unfillable_count_ratio", "unfillable_amount_ratio"):
        require(0 <= getattr(metrics, key) <= 1)
    for key in ("turnover_rate", "total_commission", "total_stamp_tax", "total_cost"):
        require(getattr(metrics, key) >= 0)
    require(metrics.total_return >= -1 and metrics.benchmark_return >= -1)
    with localcontext() as ctx:
        ctx.prec = 50
        computed = (metrics.final_equity / metrics.initial_equity - 1).quantize(
            RATIO, rounding=ROUND_HALF_EVEN
        )
    require(computed == metrics.total_return, "equity return mismatch")
    require(
        abs(metrics.total_return - metrics.benchmark_return - metrics.excess_return)
        <= RATIO,
        "excess return mismatch",
    )
    require(
        metrics.total_cost == metrics.total_commission + metrics.total_stamp_tax,
        "cost accounting mismatch",
    )


def _artifacts(raw, metrics, equity, trades, rejections, start, end):
    _metrics(raw["metrics"], metrics)
    require(bool(equity), "equity cannot be empty")
    days = tuple(point.trade_date for point in equity)
    require(
        days == tuple(sorted(set(days))) and start <= days[0] <= days[-1] <= end,
        "equity dates outside archived period",
    )
    require(
        equity[0].equity == metrics.initial_equity
        and equity[-1].equity == metrics.final_equity,
        "equity endpoints mismatch",
    )
    peak = Decimal("0")
    drawdown = Decimal("0")
    for point, source in zip(equity, raw["equity"], strict=True):
        decimal_string(source["equity"], 2)
        peak = max(peak, point.equity)
        if peak:
            drawdown = max(drawdown, (peak - point.equity) / peak)
    require(
        drawdown.quantize(RATIO, rounding=ROUND_HALF_EVEN) == metrics.max_drawdown,
        "drawdown mismatch",
    )
    for fill, source in zip(trades, raw["trades"], strict=True):
        require(
            start <= fill.signal_date < fill.trade_date <= end, "fill dates mismatch"
        )
        require(fill.raw_close > 0 and fill.fill_price > 0)
        decimal_string(source["fill_price"], 4)
        for key in ("gross_amount", "commission", "stamp_tax", "net_cash_delta"):
            decimal_string(source[key], 2)
        require(
            fill.gross_amount
            == (fill.fill_price * fill.quantity).quantize(
                MONEY, rounding=ROUND_HALF_EVEN
            ),
            "gross amount mismatch",
        )
        require(fill.commission >= 0 and fill.stamp_tax >= 0)
        cash = (
            -fill.gross_amount - fill.commission - fill.stamp_tax
            if fill.side == "buy"
            else fill.gross_amount - fill.commission - fill.stamp_tax
        )
        require(fill.net_cash_delta == cash, "fill cash identity mismatch")
        if fill.side == "buy":
            require(fill.stamp_tax == 0 and fill.quantity % 100 == 0)
    require(
        sum((fill.commission for fill in trades), Decimal(0))
        == metrics.total_commission,
        "commission sum mismatch",
    )
    require(
        sum((fill.stamp_tax for fill in trades), Decimal(0)) == metrics.total_stamp_tax,
        "stamp tax sum mismatch",
    )
    for rejection in rejections:
        require(
            start <= rejection.signal_date < rejection.trade_date <= end,
            "rejection dates mismatch",
        )


def _walk_forward(raw, result, config, sessions):
    report = result.walk_forward
    spec = config.walk_forward
    require(
        (report is None) == (spec is None) and (result.folds is None) == (spec is None),
        "fold presence mismatch",
    )
    if report is None:
        return
    require(result.folds == report.folds and len(report.folds) == spec.folds)
    require(
        report.layout == "walk-forward-anchored-v1"
        and report.folds_count == spec.folds
        and report.train_sessions == spec.train_sessions
        and report.test_sessions == spec.test_sessions
    )
    calendar = tuple(sessions)
    required = spec.train_sessions + spec.folds * spec.test_sessions
    require(len(calendar) >= required, "archived fold calendar incomplete")
    regime = report.regime_map
    require(
        regime.layout == report.layout
        and regime.fit_method == "equal-pool-mean-next-session-return-v1"
        and regime.train_start == calendar[0]
        and regime.train_end == calendar[spec.train_sessions - 1]
        and regime.train_sessions == spec.train_sessions
        and regime.rebalance_period == config.parameters.rebalance_period
    )
    require(
        all(
            regime.train_start <= day < regime.train_end
            and calendar.index(day) % config.parameters.rebalance_period == 0
            for day in regime.missing_phase_decision_sessions
        )
    )
    require(
        sum(weight.observations for weight in regime.weights)
        <= len(range(0, spec.train_sessions - 1, config.parameters.rebalance_period))
    )
    for weight in regime.weights:
        if weight.status == "fitted":
            require(
                weight.weight
                == min(
                    Decimal(1), max(Decimal("0.5"), 1 + weight.mean_next_session_return)
                ),
                "regime fitted weight mismatch",
            )
    for index, fold in enumerate(report.folds):
        offset = spec.train_sessions + index * spec.test_sessions
        block = calendar[offset : offset + spec.test_sessions]
        require(
            fold.fold_index == index
            and fold.train_start == regime.train_start
            and fold.train_end == regime.train_end
            and fold.test_start == block[0]
            and fold.test_end == block[-1]
            and fold.train_end < fold.test_start,
            "anchored fold layout mismatch",
        )
        require(
            tuple(x.trade_date for x in fold.equity) == block,
            "fold must tile actual sessions",
        )
        _artifacts(
            raw["walk_forward"]["folds"][index],
            fold.metrics,
            fold.equity,
            fold.trades,
            fold.rejections,
            fold.test_start,
            fold.test_end,
        )
        require(
            all(
                fold.test_start <= event.trade_date <= fold.test_end
                for event in fold.quality
            ),
            "fold quality date mismatch",
        )
    aggregate_raw = {
        "metrics": raw["walk_forward"]["aggregate_metrics"],
        "equity": raw["walk_forward"]["aggregate_equity"],
        # Aggregate fills are not part of v1; do not infer them from reset folds.
    }
    _metrics(aggregate_raw["metrics"], report.aggregate_metrics)
    points = report.aggregate_equity
    require(
        tuple(x.trade_date for x in points) == calendar[spec.train_sessions : required],
        "OOS continuous dates mismatch",
    )
    for point in aggregate_raw["equity"]:
        decimal_string(point["equity"], 2)
    require(
        points[0].equity == report.aggregate_metrics.initial_equity
        and points[-1].equity == report.aggregate_metrics.final_equity,
        "aggregate ledger endpoints mismatch",
    )


def validate_result(
    payload, config: BacktestConfig, *, sessions: Sequence[date] | None = None
):
    raw = json_value(payload)
    require(isinstance(raw, dict), "result object required")
    if raw.get("schema_version") == RESEARCH_RESULT_SCHEMA_VERSION:
        from dragon_radar.research.materialization import validate_v2

        return validate_v2(raw, config, sessions=sessions)
    require(raw.get("schema_version") == RESULT_V1, "unsupported result schema")
    result = ValidatedBacktestResult.model_validate_json(canonical(raw))
    _wire_shape(raw, result)
    require(re.fullmatch("[a-f0-9]{64}", result.config_hash) is not None)
    require(
        result.config_hash == config.config_hash
        and result.snapshot_id == config.snapshot_id,
        "frozen config mismatch",
    )
    require(
        manifests_equal(result.manifests, config.input_manifests), "manifest mismatch"
    )
    versions = result.versions
    require(
        versions.strategy_id == config.strategy_id
        and versions.strategy_method == config.strategy_id
        and versions.factor_id == config.parameters.factor_id
    )
    for field, expected in {
        "sizing_method": SIZING_METHOD,
        "execution_method": EXECUTION_METHOD,
        "ledger_method": LEDGER_METHOD,
        "valuation_method": VALUATION_METHOD,
        "cost_method": COST_METHOD,
    }.items():
        require(getattr(versions, field) == expected, "unsupported method version")
    require(versions.cost_sensitivity_multipliers == COST_SENSITIVITY_MULTIPLIERS)
    require(
        all(
            getattr(versions, name) == getattr(config.input_manifests.versions, name)
            for name in ("adjustment", "calendar", "pool")
        )
    )
    require(
        versions.factor_unit == "ratio"
        and versions.factor_window in ("20d", "60d", "120d")
        and versions.factor_metric
        == f"relative_strength_{versions.factor_window[:-1]}",
        "unsupported factor method metadata",
    )
    start, end = config.period.start, config.period.end
    _artifacts(
        raw, result.metrics, result.equity, result.trades, result.rejections, start, end
    )
    require(result.metrics.initial_equity == Decimal("100000.00"))
    calendar = tuple(point.trade_date for point in result.equity)
    if sessions is not None:
        require(calendar == tuple(sessions), "actual session mismatch")
    require(tuple(day.trade_date for day in result.quality) == calendar)
    for day in result.quality:
        require(
            all(event.trade_date == day.trade_date for event in day.events),
            "quality event date mismatch",
        )
    require(result.baseline.metrics == result.metrics)
    require(
        tuple(tier.tier for tier in result.baseline.cost_sensitivity)
        == ("low", "base", "high")
    )
    for index, tier in enumerate(result.baseline.cost_sensitivity):
        require(tier.cost_multiplier == COST_SENSITIVITY_MULTIPLIERS[tier.tier])
        _metrics(raw["baseline"]["cost_sensitivity"][index]["metrics"], tier.metrics)
        if tier.tier == "base":
            require(tier.metrics == result.metrics)
    _walk_forward(raw, result, config, calendar)
    require((result.ablation is None) == ("ai_variant" not in config.experiment.legs))
    if result.ablation is not None:
        expected = {
            "snapshot_id": str(config.snapshot_id),
            "input_manifests": config.input_manifests.model_dump(mode="json"),
            "period": config.period.model_dump(mode="json"),
            "cost_model": config.cost_model.model_dump(mode="json"),
            "random_seed": config.experiment.random_seed,
        }
        for leg in (result.ablation.baseline, result.ablation.ai_variant):
            actual = leg.inputs.model_dump(mode="json")
            require(manifests_equal(leg.inputs.input_manifests, config.input_manifests))
            actual["input_manifests"] = expected["input_manifests"]
            require(actual == expected, "ablation control mismatch")
        require(result.ablation.baseline.metrics == result.metrics)
        _metrics(
            raw["ablation"]["baseline"]["metrics"], result.ablation.baseline.metrics
        )
    return result


def validate_envelope(payload, config, *, run_id):
    raw = json_value(payload)
    require(
        isinstance(raw, dict)
        and set(raw)
        == {
            "run_id",
            "status",
            "config_hash",
            "research_only",
            "result",
            "failure",
        },
        "EX15 six-key contract mismatch",
    )
    require(
        raw["run_id"] == str(run_id)
        and raw["config_hash"] == config.config_hash
        and raw["research_only"] is True,
        "run context mismatch",
    )
    status, result, failure = raw["status"], raw["result"], raw["failure"]
    require(status in ("queued", "running", "succeeded", "failed", "cancelled"))
    if status == "succeeded":
        require(result is not None and failure is None)
        validate_result(result, config)
    else:
        require(result is None)
        if status == "failed":
            require(
                isinstance(failure, dict)
                and type(failure.get("code")) is str
                and bool(failure["code"])
            )
        else:
            require(
                failure is None
                or (
                    status == "cancelled"
                    and isinstance(failure, dict)
                    and failure.get("code") == "cancelled"
                )
            )
    return raw
