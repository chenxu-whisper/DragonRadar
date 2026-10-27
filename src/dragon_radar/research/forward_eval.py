"""Point-in-time forward evaluation over a strictly non-overlapping grid."""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import cast

from dragon_radar.analytics.metrics import _decimal_context
from dragon_radar.research import statistics as stats
from dragon_radar.research.models import (
    EVALUATION_KIND,
    GATE_INSUFFICIENT_FOLDS,
    GATE_INSUFFICIENT_OBSERVATIONS,
    GATE_POOLED_IC_UNAVAILABLE,
    GATE_QUINTILE_TOO_SMALL,
    REASON_EMPTY_BUCKET,
    REASON_INSUFFICIENT_FOLDS,
    REASON_INSUFFICIENT_SAMPLE,
    REASON_MISSING_CLOSE,
    REASON_OUT_OF_WINDOW,
    REASON_POOLED_IC_UNAVAILABLE,
    REASON_SLOT_WITHOUT_SIGNAL,
    REASON_ZERO_VARIANCE,
    ValidationConfig,
    validate_config,
)
from dragon_radar.research.samples import ValidatedPack
from dragon_radar.research.statistics import RankCorrelation

_ZERO = Decimal(0)
_ONE = Decimal(1)
_FOUR = Decimal(4)


def evaluate_all(
    validated: ValidatedPack, config: ValidationConfig
) -> dict[str, dict[str, object]]:
    cfg = validate_config(config)
    return {
        str(horizon): evaluate_horizon(validated, cfg, horizon)
        for horizon in cfg.horizons
    }


def _entry_exit_return(closes, calendar, entry_index: int, exit_index: int):
    # ``closes`` is keyed by calendar date, so map grid indices through the
    # calendar; labels_ready() already guaranteed both dates are present.
    with localcontext(_decimal_context()):
        return closes[calendar[exit_index]] / closes[calendar[entry_index]] - _ONE


def _fold_partition(n: int, min_size: int) -> list[tuple[int, int]]:
    if n < min_size:
        return []
    fold_count = n // min_size
    base = n // fold_count
    extra = n % fold_count
    sizes = [base + 1] * extra + [base] * (fold_count - extra)
    bounds: list[tuple[int, int]] = []
    start = 0
    for size in sizes:
        bounds.append((start, start + size))
        start += size
    return bounds


def _fold_records(observations, min_size: int):
    folds = []
    for index, (start, end) in enumerate(_fold_partition(len(observations), min_size)):
        members = observations[start:end]
        if not members:
            continue
        scores = [cast(Decimal, item["score"]) for item in members]
        excess = [cast(Decimal, item["r_excess"]) for item in members]
        correlation = (
            stats.spearman(scores, excess)
            if len(members) >= 2
            else RankCorrelation(None, REASON_INSUFFICIENT_SAMPLE)
        )
        folds.append(
            {
                "fold_index": index,
                "start_date": members[0]["trade_date"],
                "end_date": members[-1]["trade_date"],
                "n": len(members),
                "rank_ic": correlation.value,
                "reason": correlation.reason,
            }
        )
    return folds


def _fold_summary(folds) -> dict[str, object]:
    values = [
        cast(Decimal, fold["rank_ic"]) for fold in folds if fold["rank_ic"] is not None
    ]
    if not values:
        mean = None
        std = None
        icir = None
        icir_reason = REASON_INSUFFICIENT_FOLDS
        positive_share = None
    else:
        mean = stats.decimal_mean(values)
        if len(values) < 2:
            std = None
            icir = None
            icir_reason = REASON_INSUFFICIENT_FOLDS
        else:
            std = cast(Decimal, stats.decimal_sample_std(values))
            if std == 0:
                icir = None
                icir_reason = REASON_ZERO_VARIANCE
            else:
                with localcontext(_decimal_context()):
                    icir = mean / std
                icir_reason = None
        with localcontext(_decimal_context()):
            positive_share = Decimal(sum(1 for value in values if value > 0)) / Decimal(
                len(values)
            )
    return {
        "folds_total": len(folds),
        "nonnull_ic": len(values),
        "ic_mean": mean,
        "ic_std": std,
        "icir": icir,
        "icir_reason": icir_reason,
        "positive_share": positive_share,
    }


def _quintile_block(
    observations, pooled_ic, min_quintile_size: int
) -> dict[str, object]:
    buckets = [
        {"index": index, "n": 0, "sum_excess": _ZERO, "sum_target": _ZERO}
        for index in range(1, 6)
    ]
    if observations:
        scores = [cast(Decimal, item["score"]) for item in observations]
        memberships = stats.quintile_buckets(stats.average_ranks(scores))
        with localcontext(_decimal_context()):
            for item, bucket_index in zip(observations, memberships, strict=True):
                bucket = buckets[bucket_index - 1]
                bucket["n"] += 1
                bucket["sum_excess"] += cast(Decimal, item["r_excess"])
                bucket["sum_target"] += cast(Decimal, item["r_target"])
    finalized = []
    with localcontext(_decimal_context()):
        for bucket in buckets:
            n = cast(int, bucket["n"])
            finalized.append(
                {
                    "index": cast(int, bucket["index"]),
                    "n": n,
                    "mean_excess": (bucket["sum_excess"] / Decimal(n) if n else None),
                    "mean_target": (bucket["sum_target"] / Decimal(n) if n else None),
                }
            )
    means = [cast(Decimal | None, bucket["mean_excess"]) for bucket in finalized]
    with localcontext(_decimal_context()):
        if any(mean is None for mean in means):
            spread = None
            monotonic = None
            monotonic_reason = REASON_EMPTY_BUCKET
            agreement = None
        elif pooled_ic is None or pooled_ic == 0:
            spread = cast(Decimal, means[4]) - cast(Decimal, means[0])
            monotonic = None
            monotonic_reason = REASON_POOLED_IC_UNAVAILABLE
            agreement = None
        else:
            means_decimal = cast(list[Decimal], means)
            spread = means_decimal[4] - means_decimal[0]
            differences = [means_decimal[i + 1] - means_decimal[i] for i in range(4)]
            positive = pooled_ic > 0
            monotonic = all(
                difference > 0 if positive else difference < 0
                for difference in differences
            )
            agreement = (
                Decimal(
                    sum(1 for difference in differences if (difference > 0) == positive)
                )
                / _FOUR
            )
            monotonic_reason = None
    return {
        "buckets": finalized,
        "spread": spread,
        "monotonic": monotonic,
        "monotonic_reason": monotonic_reason,
        "adjacent_agreement": agreement,
    }


def evaluate_horizon(
    validated: ValidatedPack, config: ValidationConfig, horizon: int
) -> dict[str, object]:
    cfg = validate_config(config)
    calendar = validated.calendar
    as_of_index = validated.as_of_index
    points_by_date = validated.points_by_date
    target = validated.target_closes
    benchmark = validated.benchmark_closes
    total_days = len(calendar)

    signal_indices = [
        index
        for index in range(as_of_index + 1)
        if (point := points_by_date.get(calendar[index])) is not None
        and point["score"] is not None
    ]

    def labels_ready(index: int) -> bool:
        entry = index + 1
        exit_index = index + 1 + horizon
        return (
            exit_index < total_days
            and calendar[entry] in target
            and calendar[exit_index] in target
            and calendar[entry] in benchmark
            and calendar[exit_index] in benchmark
        )

    origin = next(
        (
            index
            for index in signal_indices
            if index + 1 + horizon < total_days and labels_ready(index)
        ),
        None,
    )

    slot_indices: list[int] = []
    if origin is not None:
        slot = origin
        while slot <= as_of_index and slot + 1 + horizon < total_days:
            slot_indices.append(slot)
            slot += horizon

    out_of_window = sum(
        1 for index in signal_indices if index + 1 + horizon >= total_days
    )

    observations: list[dict[str, object]] = []
    slot_without_signal = 0
    missing_close = 0
    signaled_slots = 0
    for index in slot_indices:
        point = points_by_date.get(calendar[index])
        if point is None or point["score"] is None:
            slot_without_signal += 1
            continue
        signaled_slots += 1
        if not labels_ready(index):
            missing_close += 1
            continue
        entry_index = index + 1
        exit_index = index + 1 + horizon
        r_target = _entry_exit_return(target, calendar, entry_index, exit_index)
        r_benchmark = _entry_exit_return(benchmark, calendar, entry_index, exit_index)
        with localcontext(_decimal_context()):
            r_excess = r_target - r_benchmark
        observations.append(
            {
                "trade_date": calendar[index],
                "entry_date": calendar[entry_index],
                "exit_date": calendar[exit_index],
                "score": point["score"],
                "r_target": r_target,
                "r_benchmark": r_benchmark,
                "r_excess": r_excess,
            }
        )

    n = len(observations)
    off_grid = len(signal_indices) - signaled_slots - out_of_window
    grid = {
        "step_trading_days": horizon,
        "slots_total": len(slot_indices),
        "slots_accepted": n,
        "slots_skipped": {REASON_SLOT_WITHOUT_SIGNAL: slot_without_signal},
        "drops": {
            REASON_MISSING_CLOSE: missing_close,
            REASON_OUT_OF_WINDOW: out_of_window,
        },
        "signals_total": len(signal_indices),
        "off_grid_signals": off_grid,
    }

    folds = _fold_records(observations, cfg.min_signals_per_fold)
    if n >= 2:
        pooled_correlation = stats.spearman(
            [cast(Decimal, item["score"]) for item in observations],
            [cast(Decimal, item["r_excess"]) for item in observations],
        )
    else:
        pooled_correlation = RankCorrelation(None, REASON_INSUFFICIENT_SAMPLE)
    pooled = {
        "n": n,
        "rank_ic": pooled_correlation.value,
        "reason": pooled_correlation.reason,
    }
    quintiles = _quintile_block(
        observations, pooled_correlation.value, cfg.min_quintile_size
    )

    if pooled_correlation.value is None or pooled_correlation.value == 0:
        direction_hit_rate = None
    else:
        positive = pooled_correlation.value > 0
        with localcontext(_decimal_context()):
            direction_hit_rate = (
                Decimal(
                    sum(
                        1
                        for item in observations
                        if (cast(Decimal, item["r_excess"]) > 0) == positive
                    )
                )
                / Decimal(n)
                if n
                else None
            )

    gate_failures = []
    if n < cfg.min_folds * cfg.min_signals_per_fold:
        gate_failures.append(GATE_INSUFFICIENT_OBSERVATIONS)
    if len(folds) < cfg.min_folds:
        gate_failures.append(GATE_INSUFFICIENT_FOLDS)
    if any(
        cast(int, bucket["n"]) < cfg.min_quintile_size
        for bucket in cast(list, quintiles["buckets"])
    ):
        gate_failures.append(GATE_QUINTILE_TOO_SMALL)
    if pooled["rank_ic"] is None:
        gate_failures.append(GATE_POOLED_IC_UNAVAILABLE)

    return {
        "horizon": horizon,
        "evaluation_kind": EVALUATION_KIND,
        "grid_origin_date": calendar[origin] if origin is not None else None,
        "as_of": validated.pack.as_of,
        "grid": grid,
        "observations": observations,
        "folds": folds,
        "fold_summary": _fold_summary(folds),
        "pooled": pooled,
        "quintiles": quintiles,
        "direction_hit_rate": direction_hit_rate,
        "gate_failures": gate_failures,
    }
