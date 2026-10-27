"""Versioned closed-shape research validation reports."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any

import dragon_radar.readmodels.sentiment_research as method
from dragon_radar.analytics.metrics import _decimal_context
from dragon_radar.research.factor_health import build_factor_health
from dragon_radar.research.forward_eval import evaluate_all
from dragon_radar.research.models import (
    DEFAULT_CONFIG,
    EVIDENCE_INSUFFICIENT_REAL_SAMPLE,
    EVIDENCE_REAL_SAMPLE_DESCRIPTIVE,
    EVIDENCE_STATUSES,
    EVIDENCE_SYNTHETIC_ONLY,
    GATE_CODES,
    GATE_COVERAGE_BELOW_THRESHOLD,
    GATE_SCORE_ZERO_VARIANCE,
    REPORT_SCOPES,
    REPORT_TYPE,
    VALIDATOR_VERSION,
    ResearchValidationError,
    SamplePack,
    ValidationConfig,
    validate_config,
)
from dragon_radar.research.samples import canonical_payload_hash, validate_pack

METRIC_SCALE = Decimal("0.000001")

_TOP_KEYS = frozenset(
    {
        "report_type",
        "method_version",
        "validator_version",
        "research_only",
        "config",
        "pack",
        "input_manifests",
        "health",
        "evaluation",
        "evidence_status",
        "evidence_reasons",
        "report_id",
        "generated_at",
    }
)
_DATE_KEYS = frozenset(
    {
        "as_of",
        "first",
        "last",
        "trade_date",
        "entry_date",
        "exit_date",
        "start_date",
        "end_date",
        "grid_origin_date",
        "calendar_holes",
    }
)
_DECIMAL_KEYS = frozenset(
    {
        "min_coverage",
        "coverage_ratio",
        "score",
        "r_target",
        "r_benchmark",
        "r_excess",
        "rank_ic",
        "ic_mean",
        "ic_std",
        "icir",
        "positive_share",
        "mean_excess",
        "mean_target",
        "spread",
        "adjacent_agreement",
        "direction_hit_rate",
        "value",
        "min",
        "max",
        "mean",
        "median",
        "p25",
        "p75",
        "top_tie_share",
    }
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ResearchValidationError(message)


def _require_keys(obj: object, expected: frozenset[str], label: str) -> None:
    _require(isinstance(obj, Mapping), f"{label} must be an object")
    _require(set(obj) == expected, f"{label} has unexpected keys")  # type: ignore[arg-type]


def _config_block(config: ValidationConfig) -> dict[str, object]:
    return {
        "horizons": list(config.horizons),
        "min_coverage": config.min_coverage,
        "min_signals_per_fold": config.min_signals_per_fold,
        "min_folds": config.min_folds,
        "min_quintile_size": config.min_quintile_size,
    }


def _pack_block(validated) -> dict[str, object]:
    pack = validated.pack
    calendar = validated.calendar
    return {
        "pack_id": pack.pack_id,
        "synthetic": pack.synthetic,
        "generator": pack.generator,
        "seed": pack.seed,
        "as_of": pack.as_of,
        "target_name": pack.target_name,
        "benchmark_name": pack.benchmark_name,
        "component_rows": len(pack.components),
        "target_rows": len(validated.target_closes),
        "benchmark_rows": len(validated.benchmark_closes),
        "sample_window": {"first": calendar[0], "last": calendar[-1]},
    }


def _manifest_block(manifest) -> dict[str, object]:
    return {
        "name": manifest.name,
        "source_version": manifest.source_version,
        "frozen_at": manifest.frozen_at,
        "as_of": manifest.as_of,
        "row_count": manifest.row_count,
        "adjustment": manifest.adjustment,
        "sha256": manifest.sha256,
    }


def _quantize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _quantize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_quantize(item) for item in value]
    if isinstance(value, Decimal):
        if not value.is_finite():
            # "NaN" survives Decimal() construction and quantize() (quiet
            # NaN propagates without signalling), so reject it explicitly
            # rather than letting a non-finite value through the shape.
            raise ResearchValidationError("archived decimal is not finite")
        # A six-decimal result (e.g. 100.000000) needs more significant
        # digits than a polluted ambient context (prec=2) allows; pin the
        # fixed 34-digit context so output is caller-context independent.
        with localcontext(_decimal_context()):
            try:
                return value.quantize(METRIC_SCALE, rounding=ROUND_HALF_UP)
            except InvalidOperation:
                # "Infinity"/sNaN signal InvalidOperation under the pinned
                # trapping context: translate the builtin into RVE.
                raise ResearchValidationError(
                    "archived decimal is not finite"
                ) from None
    return value


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if type(value) is date:
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ResearchValidationError("report contains a non-serializable value")


def serialize_report(report: Mapping[str, object]) -> dict[str, object]:
    _validate_report_shape(report)
    return _jsonable(report)


def assemble_report(
    pack: SamplePack,
    config: ValidationConfig = DEFAULT_CONFIG,
    *,
    generated_at: str | None = None,
) -> dict[str, object]:
    _require(
        generated_at is None or (type(generated_at) is str and generated_at),
        "generated_at must be a non-empty string or None",
    )
    validated = validate_pack(pack)
    cfg = validate_config(config)
    health = build_factor_health(validated)
    evaluation = evaluate_all(validated, cfg)

    reasons: list[dict[str, str]] = []
    if health["coverage_ratio"] < cfg.min_coverage:
        reasons.append({"scope": "global", "code": GATE_COVERAGE_BELOW_THRESHOLD})
    distinct_scores = len(
        {point["score"] for point in validated.points if point["score"] is not None}
    )
    if distinct_scores < 2:
        reasons.append({"scope": "global", "code": GATE_SCORE_ZERO_VARIANCE})
    for horizon in cfg.horizons:
        for code in evaluation[str(horizon)]["gate_failures"]:
            reasons.append({"scope": f"h{horizon}", "code": code})

    if pack.synthetic:
        status = EVIDENCE_SYNTHETIC_ONLY
    elif reasons:
        status = EVIDENCE_INSUFFICIENT_REAL_SAMPLE
    else:
        status = EVIDENCE_REAL_SAMPLE_DESCRIPTIVE

    report: dict[str, object] = {
        "report_type": REPORT_TYPE,
        "method_version": method.METHOD_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "research_only": True,
        "config": _config_block(cfg),
        "pack": _pack_block(validated),
        "input_manifests": [
            _manifest_block(manifest)
            for manifest in sorted(pack.manifests, key=lambda item: item.name)
        ],
        "health": health,
        "evaluation": evaluation,
        "evidence_status": status,
        "evidence_reasons": reasons,
        "report_id": None,
        "generated_at": generated_at,
    }
    report = _quantize(report)
    identity = {
        key: value
        for key, value in report.items()
        if key not in ("report_id", "generated_at")
    }
    report["report_id"] = canonical_payload_hash(_jsonable(identity))
    _validate_report_shape(report)
    return report


def _restore(value: Any, key: str | None) -> Any:
    if isinstance(value, Mapping):
        return {item_key: _restore(item, item_key) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_restore(item, key) for item in value]
    if key in _DATE_KEYS and isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            # Archive boundary: never leak datetime's builtin ValueError.
            raise ResearchValidationError(
                f"archived date token is not a valid ISO date: {key}"
            ) from None
    if key in _DECIMAL_KEYS and isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation:
            # Decimal("abc") raises decimal.InvalidOperation/ConversionSyntax.
            raise ResearchValidationError(
                f"archived decimal token is not a valid decimal: {key}"
            ) from None
    return value


def parse_archived_report(archived: Mapping[str, object]) -> dict[str, object]:
    _require(isinstance(archived, Mapping), "archived report must be an object")
    restored = _restore(archived, None)
    restored = _quantize(restored)
    _validate_report_shape(restored)
    identity = {
        key: value
        for key, value in restored.items()
        if key not in ("report_id", "generated_at")
    }
    expected = canonical_payload_hash(_jsonable(identity))
    _require(restored["report_id"] == expected, "report_id mismatch")
    return restored


# ---------------------------------------------------------------------------
# Closed-shape validation
# ---------------------------------------------------------------------------

_DISTRIBUTION_KEYS = frozenset({"n", "min", "max", "mean", "median", "p25", "p75"})
_SCORE_DISTRIBUTION_KEYS = _DISTRIBUTION_KEYS | {
    "zero_count",
    "hundred_count",
    "top_tie_share",
}
_NULL_REASON_KEYS = frozenset(
    {
        method.REASON_NO_VALID_DAILY_RETURNS,
        method.REASON_NO_LIMIT_EVENTS,
        method.REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET,
    }
)


def _is_date_or_none(value: object) -> bool:
    return value is None or type(value) is date


def _is_decimal_or_none(value: object) -> bool:
    return value is None or isinstance(value, Decimal)


def _validate_report_shape(report: Mapping[str, object]) -> None:
    _require_keys(report, _TOP_KEYS, "report")
    _require(report["report_type"] == REPORT_TYPE, "report_type mismatch")
    _require(
        report["method_version"] == method.METHOD_VERSION,
        "method_version mismatch",
    )
    _require(
        report["validator_version"] == VALIDATOR_VERSION,
        "validator_version mismatch",
    )
    _require(report["research_only"] is True, "research_only must be true")
    _require(
        report["evidence_status"] in EVIDENCE_STATUSES,
        "unknown evidence_status",
    )
    _require(isinstance(report["report_id"], str), "report_id must be a string")
    _require(
        report["generated_at"] is None or type(report["generated_at"]) is str,
        "generated_at must be string or None",
    )
    _validate_config_block(report["config"])
    _validate_pack_block(report["pack"])
    _validate_manifests_block(report["input_manifests"])
    _validate_health(report["health"])
    _validate_evaluation(report["evaluation"], report["config"])
    _validate_reasons(report["evidence_reasons"])


def _validate_config_block(config: object) -> None:
    _require_keys(
        config,
        frozenset(
            {
                "horizons",
                "min_coverage",
                "min_signals_per_fold",
                "min_folds",
                "min_quintile_size",
            }
        ),
        "config",
    )
    horizons = config["horizons"]  # type: ignore[index]
    _require(isinstance(horizons, list) and horizons, "config horizons")
    _require(all(type(h) is int and h in {1, 5, 20} for h in horizons), "bad horizon")
    _require(isinstance(config["min_coverage"], Decimal), "min_coverage Decimal")  # type: ignore[index]
    for key in ("min_signals_per_fold", "min_folds", "min_quintile_size"):
        _require(type(config[key]) is int and config[key] > 0, f"{key} int")  # type: ignore[index]


def _validate_pack_block(pack: object) -> None:
    _require_keys(
        pack,
        frozenset(
            {
                "pack_id",
                "synthetic",
                "generator",
                "seed",
                "as_of",
                "target_name",
                "benchmark_name",
                "component_rows",
                "target_rows",
                "benchmark_rows",
                "sample_window",
            }
        ),
        "pack",
    )
    _require(type(pack["synthetic"]) is bool, "pack synthetic bool")  # type: ignore[index]
    _require(type(pack["as_of"]) is date, "pack as_of date")  # type: ignore[index]
    _require_keys(pack["sample_window"], frozenset({"first", "last"}), "sample_window")  # type: ignore[index]
    _require(_is_date_or_none(pack["sample_window"]["first"]), "sample_window first")  # type: ignore[index]
    _require(type(pack["sample_window"]["last"]) is date, "sample_window last")  # type: ignore[index]


def _validate_manifests_block(manifests: object) -> None:
    _require(isinstance(manifests, list) and len(manifests) == 3, "three manifests")
    names = []
    for manifest in manifests:
        _require_keys(
            manifest,
            frozenset(
                {
                    "name",
                    "source_version",
                    "frozen_at",
                    "as_of",
                    "row_count",
                    "adjustment",
                    "sha256",
                }
            ),
            "manifest",
        )
        names.append(manifest["name"])  # type: ignore[index]
        _require(type(manifest["as_of"]) is date, "manifest as_of")  # type: ignore[index]
    _require(sorted(names) == names and len(set(names)) == 3, "manifest names")


def _validate_health(health: object) -> None:
    _require_keys(
        health,
        frozenset(
            {
                "calendar_days",
                "present_days",
                "valid_days",
                "absent_days",
                "null_days",
                "coverage_ratio",
                "null_reasons",
                "calendar_holes",
                "score_distribution",
                "components",
                "rank_correlations",
                "sample_window",
            }
        ),
        "health",
    )
    _require(isinstance(health["coverage_ratio"], Decimal), "coverage Decimal")  # type: ignore[index]
    _require_keys(health["null_reasons"], _NULL_REASON_KEYS, "null_reasons")  # type: ignore[index]
    _require_keys(
        health["score_distribution"],  # type: ignore[index]
        _SCORE_DISTRIBUTION_KEYS,
        "score_distribution",
    )
    _require_keys(
        health["components"],  # type: ignore[index]
        frozenset(method.COMPONENT_KEYS),
        "components",
    )
    for block in health["components"].values():  # type: ignore[union-attr]
        _require_keys(
            block,
            frozenset({"available_days", "zero_denominator_days", "distribution"}),
            "component block",
        )
        _require_keys(
            block["distribution"],
            _DISTRIBUTION_KEYS,
            "component distribution",  # type: ignore[index]
        )
    expected_correlations = {
        "breadth_limit_balance",
        "breadth_promotion",
        "limit_balance_promotion",
        "breadth_score",
        "limit_balance_score",
        "promotion_score",
    }
    _require_keys(
        health["rank_correlations"],  # type: ignore[index]
        expected_correlations,
        "rank_correlations",
    )
    for pair in health["rank_correlations"].values():  # type: ignore[union-attr]
        _require_keys(pair, frozenset({"value", "reason"}), "correlation")
        _require(_is_decimal_or_none(pair["value"]), "correlation value")
    _require_keys(
        health["sample_window"],  # type: ignore[index]
        frozenset({"first", "last"}),
        "health window",
    )
    _require(_is_date_or_none(health["sample_window"]["first"]), "health window first")  # type: ignore[index]
    _require(_is_date_or_none(health["sample_window"]["last"]), "health window last")  # type: ignore[index]
    _require(isinstance(health["calendar_holes"], list), "calendar holes list")
    _require(all(type(day) is date for day in health["calendar_holes"]), "holes dates")


def _validate_evaluation(evaluation: object, config: object) -> None:
    _require(isinstance(evaluation, Mapping), "evaluation mapping")
    horizons = config["horizons"]  # type: ignore[index]
    _require(set(evaluation) == {str(h) for h in horizons}, "evaluation horizons")  # type: ignore[arg-type]
    for horizon in horizons:
        block = evaluation[str(horizon)]  # type: ignore[index]
        _require_keys(
            block,
            frozenset(
                {
                    "horizon",
                    "evaluation_kind",
                    "grid_origin_date",
                    "as_of",
                    "grid",
                    "observations",
                    "folds",
                    "fold_summary",
                    "pooled",
                    "quintiles",
                    "direction_hit_rate",
                    "gate_failures",
                }
            ),
            "evaluation block",
        )
        _require(type(block["horizon"]) is int, "horizon int")
        _require(_is_date_or_none(block["grid_origin_date"]), "grid origin")
        _require(type(block["as_of"]) is date, "block as_of")
        _validate_grid(block["grid"])
        observations = block["observations"]  # type: ignore[index]
        _require(isinstance(observations, list), "observations must be a list")
        for observation in observations:
            _require_keys(
                observation,
                frozenset(
                    {
                        "trade_date",
                        "entry_date",
                        "exit_date",
                        "score",
                        "r_target",
                        "r_benchmark",
                        "r_excess",
                    }
                ),
                "observation",
            )
            _require(type(observation["trade_date"]) is date, "obs date")
        folds = block["folds"]  # type: ignore[index]
        _require(isinstance(folds, list), "folds must be a list")
        for fold in folds:
            _require_keys(
                fold,
                frozenset(
                    {
                        "fold_index",
                        "start_date",
                        "end_date",
                        "n",
                        "rank_ic",
                        "reason",
                    }
                ),
                "fold",
            )
        _require_keys(
            block["fold_summary"],
            frozenset(
                {
                    "folds_total",
                    "nonnull_ic",
                    "ic_mean",
                    "ic_std",
                    "icir",
                    "icir_reason",
                    "positive_share",
                }
            ),
            "fold summary",
        )
        _require_keys(block["pooled"], frozenset({"n", "rank_ic", "reason"}), "pooled")
        _require_keys(
            block["quintiles"],
            frozenset(
                {
                    "buckets",
                    "spread",
                    "monotonic",
                    "monotonic_reason",
                    "adjacent_agreement",
                }
            ),
            "quintiles",
        )
        buckets = block["quintiles"]["buckets"]  # type: ignore[index]
        _require(isinstance(buckets, list), "quintile buckets must be a list")
        for bucket in buckets:
            _require_keys(
                bucket,
                frozenset({"index", "n", "mean_excess", "mean_target"}),
                "quintile bucket",
            )
        _require(
            isinstance(block["gate_failures"], list)
            and all(code in GATE_CODES for code in block["gate_failures"]),
            "gate codes",
        )
        _require(
            _is_decimal_or_none(block["direction_hit_rate"]),
            "hit rate",
        )


def _validate_grid(grid: object) -> None:
    _require_keys(
        grid,
        frozenset(
            {
                "step_trading_days",
                "slots_total",
                "slots_accepted",
                "slots_skipped",
                "drops",
                "signals_total",
                "off_grid_signals",
            }
        ),
        "grid",
    )
    _require_keys(
        grid["slots_skipped"],
        frozenset({"slot_without_signal"}),
        "skips",  # type: ignore[index]
    )
    _require_keys(
        grid["drops"],  # type: ignore[index]
        frozenset({"missing_close", "out_of_window"}),
        "drops",
    )


def _validate_reasons(reasons: object) -> None:
    _require(isinstance(reasons, list), "evidence reasons list")
    for reason in reasons:
        _require(isinstance(reason, Mapping), "evidence reason")
        _require(
            set(reason) in ({"scope", "code"}, {"scope", "code", "detail"}),  # type: ignore[arg-type]
            "reason keys",
        )
        _require(reason["scope"] in REPORT_SCOPES, "reason scope")  # type: ignore[index]
        _require(reason["code"] in GATE_CODES, "reason code")  # type: ignore[index]
