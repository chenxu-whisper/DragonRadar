"""Task 27 Slice A tests: research-method validation (synthetic only)."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal, getcontext, localcontext
from pathlib import Path

import pytest

from dragon_radar.analytics.metrics import _decimal_context
from dragon_radar.readmodels import sentiment_research as method
from dragon_radar.research import statistics as stats
from dragon_radar.research.factor_health import build_factor_health
from dragon_radar.research.forward_eval import evaluate_all, evaluate_horizon
from dragon_radar.research.models import (
    DEFAULT_CONFIG,
    REASON_EMPTY_BUCKET,
    REASON_INSUFFICIENT_SAMPLE,
    REASON_ZERO_VARIANCE,
    ComponentCounts,
    DailyComponents,
    InputManifest,
    ResearchValidationError,
    SamplePack,
    ValidationConfig,
    validate_config,
)
from dragon_radar.research.samples import (
    ValidatedPack,
    canonical_payload_hash,
    validate_pack,
)

D = Decimal

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "dragon_radar" / "research"
FORBIDDEN_IMPORTS = ("fastapi", "sqlalchemy", "httpx", "requests")
FORBIDDEN_DRAGON_SUBPACKAGES = (
    "http",
    "storage",
    "owner",
    "publication",
    "analysis_jobs",
    "query",
    "realtime",
    "ai",
    "lhb",
    "news",
    "review",
    "regulation",
    "topics",
    "roles",
    "ingestion",
)
ALLOWED_DRAGON_IMPORTS = (
    "dragon_radar.analytics.metrics",
    "dragon_radar.readmodels.sentiment_research",
)


def _imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    return names


def test_research_package_imports_stay_pure():
    for path in sorted(PACKAGE_ROOT.glob("*.py")):
        for name in _imported_modules(path):
            assert not name.startswith(FORBIDDEN_IMPORTS), (path.name, name)
            if name.startswith("dragon_radar."):
                assert (
                    name == "dragon_radar"
                    or name.startswith("dragon_radar.research")
                    or name.startswith(ALLOWED_DRAGON_IMPORTS)
                ), (path.name, name)
                root = ".".join(name.split(".")[:2])
                assert root not in {
                    f"dragon_radar.{sub}" for sub in FORBIDDEN_DRAGON_SUBPACKAGES
                }, (path.name, name)


def test_default_config_is_valid_and_frozen():
    assert validate_config(DEFAULT_CONFIG) is DEFAULT_CONFIG
    assert DEFAULT_CONFIG.horizons == (1, 5, 20)
    assert DEFAULT_CONFIG.min_coverage == Decimal("0.80")
    with pytest.raises(AttributeError):
        DEFAULT_CONFIG.min_folds = 9  # type: ignore[misc]


def test_validate_config_rejects_bad_horizons_and_thresholds():
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(horizons=(3,)))
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(horizons=(1, 1)))
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(horizons=()))  # type: ignore[arg-type]
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(min_coverage=Decimal("0")))
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(min_signals_per_fold=0))


def test_component_counts_are_frozen_value_holders():
    counts = ComponentCounts(3, 5)
    assert (counts.numerator, counts.denominator) == (3, 5)
    with pytest.raises(AttributeError):
        counts.numerator = 4  # type: ignore[misc]


def test_validate_config_is_fail_closed_for_bool_nan_infinity_and_boundary():
    # bool must never sneak through the exact-type int/Decimal checks.
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(horizons=(True,)))  # type: ignore[arg-type]
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(min_signals_per_fold=True))  # type: ignore[arg-type]
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(min_coverage=True))  # type: ignore[arg-type]
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(min_coverage=Decimal("NaN")))
    with pytest.raises(ResearchValidationError):
        validate_config(ValidationConfig(min_coverage=Decimal("Infinity")))
    # Upper boundary is inclusive: coverage of exactly one is accepted.
    accepted = validate_config(ValidationConfig(min_coverage=Decimal("1")))
    assert accepted.min_coverage == Decimal("1")


def test_research_package_rejects_relative_imports_beyond_package_root():
    # Level 1 stays inside dragon_radar.research; level >= 2 escapes the
    # package boundary (e.g. ``from ..storage import x``) and is forbidden.
    for path in sorted(PACKAGE_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level >= 2
        ]
        assert offenders == [], (path.name, [node.level for node in offenders])


def test_research_package_has_no_nested_modules():
    assert {
        path.relative_to(PACKAGE_ROOT).as_posix() for path in PACKAGE_ROOT.rglob("*.py")
    } == {
        "__init__.py",
        "models.py",
        "samples.py",
        "statistics.py",
        "factor_health.py",
        "forward_eval.py",
        "report.py",
    }


def test_average_ranks_handles_ties_exactly():
    assert stats.average_ranks([D("3"), D("1"), D("2"), D("3")]) == (
        D("3.5"),
        D("1"),
        D("2"),
        D("3.5"),
    )
    assert stats.average_ranks([D("5"), D("5"), D("5")]) == (
        D("2"),
        D("2"),
        D("2"),
    )


def test_spearman_extremes_and_hand_computed_tie_case():
    assert stats.spearman(
        [D("1"), D("2"), D("3")], [D("4"), D("5"), D("6")]
    ).value == D("1")
    assert stats.spearman(
        [D("1"), D("2"), D("3")], [D("6"), D("5"), D("4")]
    ).value == D("-1")
    # ranks x = 1,2,3 ; ranks y = 2,1,3 -> covariance form yields 0.5 exactly.
    result = stats.spearman([D("1"), D("2"), D("3")], [D("2"), D("1"), D("3")])
    assert result.value == D("0.5")
    # 34-digit sqrt(3)/2 pinned verbatim: 34-context rank Pearson lands
    # 2.9e-34 below the 80-digit reference 0.8660...2936183... (plan note 3).
    tied = stats.spearman([D("1"), D("1"), D("2")], [D("1"), D("2"), D("3")])
    assert tied.value == D("0.8660254037844386467637231707529359")


def test_spearman_degenerate_inputs_report_closed_reasons():
    assert stats.spearman([D("1")], [D("1")]).reason == REASON_INSUFFICIENT_SAMPLE
    assert stats.spearman([D("1"), D("1")], [D("1"), D("2")]).reason == (
        REASON_ZERO_VARIANCE
    )
    assert stats.spearman([D("1"), D("2")], [D("3"), D("3")]).reason == (
        REASON_ZERO_VARIANCE
    )


def test_statistics_reject_bad_types_and_lengths():
    for bad in ([1.0], [True], [float("nan")], [D("NaN")]):
        with pytest.raises(ResearchValidationError):
            stats.average_ranks(bad)
    with pytest.raises(ResearchValidationError):
        stats.spearman([D("1")], [D("1"), D("2")])
    with pytest.raises(ResearchValidationError):
        stats.average_ranks([])


def test_mean_std_median_quantile_distribution():
    assert stats.decimal_mean([D("1"), D("2"), D("3"), D("4")]) == D("2.5")
    assert stats.decimal_median([D("4"), D("1"), D("3"), D("2")]) == D("2.5")
    assert stats.decimal_median([D("3"), D("1"), D("2")]) == D("2")
    assert stats.decimal_sample_std([D("1")]) is None
    # Sample (n-1) std: mean 2, squared deviations 4+0+4=8, /2=4, sqrt=2.
    assert stats.decimal_sample_std([D("0"), D("2"), D("4")]) == D("2")
    assert stats.decimal_quantile([D("1"), D("2"), D("3"), D("4")], D("0")) == D("1")
    assert stats.decimal_quantile([D("1"), D("2"), D("3"), D("4")], D("1")) == D("4")
    assert stats.decimal_quantile([D("1"), D("2"), D("3"), D("4")], D("0.5")) == D(
        "2.5"
    )
    distribution = stats.decimal_distribution([D("1"), D("3")])
    assert distribution["n"] == 2
    assert distribution["p25"] == D("1.5")
    assert distribution["p75"] == D("2.5")


def test_quintile_buckets_never_split_ties():
    # 10 distinct ranks -> two observations per bucket.
    ranks = tuple(D(i) for i in range(1, 11))
    assert stats.quintile_buckets(ranks) == (1, 1, 2, 2, 3, 3, 4, 4, 5, 5)
    # Two equal ranks share one bucket.
    tied = stats.quintile_buckets([D("1.5"), D("1.5"), D("3"), D("4"), D("5")])
    assert tied[0] == tied[1]
    assert 1 <= tied[0] <= 5


def test_statistics_are_independent_of_caller_decimal_context():
    values = [D("1"), D("2"), D("3"), D("4")]
    others = [D("4"), D("2"), D("1"), D("3")]
    previous_prec = getcontext().prec
    try:
        getcontext().prec = 2
        low_precision = {
            "mean": stats.decimal_mean(values),
            "std": stats.decimal_sample_std(values),
            "median": stats.decimal_median(values),
            "quantiles": tuple(
                stats.decimal_quantile(values, q)
                for q in (D("0.25"), D("0.34"), D("0.75"))
            ),
            "rho": stats.spearman(values, others).value,
        }
        getcontext().prec = 34
        high_precision = {
            "mean": stats.decimal_mean(values),
            "std": stats.decimal_sample_std(values),
            "median": stats.decimal_median(values),
            "quantiles": tuple(
                stats.decimal_quantile(values, q)
                for q in (D("0.25"), D("0.34"), D("0.75"))
            ),
            "rho": stats.spearman(values, others).value,
        }
    finally:
        getcontext().prec = previous_prec
    assert low_precision == high_precision
    # Type-7 anchors: 0.25 -> 1.75 and 0.75 -> 3.25 are exact at any
    # precision; q=0.34 -> position 1.02 -> 2.02 diverges at caller prec=2
    # unless arithmetic runs inside the fixed 34-digit context.
    assert high_precision["quantiles"] == (D("1.75"), D("2.02"), D("3.25"))


def test_statistics_fail_closed_branches_value_shapes_and_closed_distribution():
    for bad_q in (True, D("NaN"), D("-0.1"), D("1.1"), 0.5):
        with pytest.raises(ResearchValidationError):
            stats.decimal_quantile([D("1"), D("2")], bad_q)
    # Ranks outside 1..n cannot map into buckets 1..5.
    with pytest.raises(ResearchValidationError):
        stats.quintile_buckets([D("0")])
    with pytest.raises(ResearchValidationError):
        stats.quintile_buckets([D("2")])
    # Two points give exact extremes; success shape carries no reason.
    perfect = stats.spearman([D("1"), D("2")], [D("1"), D("2")])
    assert perfect.value == D("1")
    assert perfect.reason is None
    inverse = stats.spearman([D("1"), D("2")], [D("2"), D("1")])
    assert inverse.value == D("-1")
    # Degenerate shape carries a reason and no value.
    degenerate = stats.spearman([D("1"), D("1")], [D("1"), D("2")])
    assert degenerate.value is None
    assert degenerate.reason == REASON_ZERO_VARIANCE
    assert stats.average_ranks([D("7")]) == (D("1"),)
    distribution = stats.decimal_distribution([D("1"), D("3")])
    assert set(distribution) == {
        "n",
        "min",
        "max",
        "mean",
        "median",
        "p25",
        "p75",
    }
    assert distribution == {
        "n": 2,
        "min": D("1"),
        "max": D("3"),
        "mean": D("2"),
        "median": D("2"),
        "p25": D("1.5"),
        "p75": D("2.5"),
    }
    # Generators are not Sequences and strings are not numeric vectors.
    with pytest.raises(ResearchValidationError):
        stats.average_ranks(iter([D("1"), D("2")]))
    with pytest.raises(ResearchValidationError):
        stats.average_ranks("12")


def calendar_days(start: date, n: int) -> tuple[date, ...]:
    return tuple(start + timedelta(days=i) for i in range(n))


def daily(
    day: date,
    breadth: tuple[int, int],
    limit_balance: tuple[int, int],
    promotion: tuple[int, int],
) -> DailyComponents:
    return DailyComponents(
        day,
        ComponentCounts(*breadth),
        ComponentCounts(*limit_balance),
        ComponentCounts(*promotion),
    )


def _canonical_hash(payload) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _components_payload(components):
    return [
        [
            item.trade_date.isoformat(),
            item.breadth.numerator,
            item.breadth.denominator,
            item.limit_balance.numerator,
            item.limit_balance.denominator,
            item.promotion.numerator,
            item.promotion.denominator,
        ]
        for item in components
    ]


def _closes_payload(closes):
    return [
        [day.isoformat(), str(price.normalize())]
        for day, price in sorted(closes.items())
    ]


def _manifest(
    name, rows, as_of, *, adjustment="raw", frozen_at="2026-10-27T00:00:00+00:00"
):
    return InputManifest(
        name=name,
        source_version="fixture-v1",
        frozen_at=frozen_at,
        as_of=as_of,
        row_count=len(rows),
        adjustment=adjustment,
        sha256=_canonical_hash(rows),
    )


def make_manifests(components, target_closes, benchmark_closes, as_of):
    return (
        _manifest("components", _components_payload(components), as_of),
        _manifest("target_closes", _closes_payload(target_closes), as_of),
        _manifest("benchmark_closes", _closes_payload(benchmark_closes), as_of),
    )


def make_pack(
    *,
    calendar,
    components,
    target_closes,
    benchmark_closes,
    as_of=None,
    synthetic=True,
    seed=None,
    pack_id="fixture-pack",
    generator="test-fixture:hand_built",
    target_name="fixture-target",
    benchmark_name="csi300",
):
    if as_of is None:
        as_of = calendar[-1]
    return SamplePack(
        pack_id=pack_id,
        synthetic=synthetic,
        generator=generator,
        seed=seed,
        as_of=as_of,
        calendar=calendar,
        components=tuple(components),
        target_name=target_name,
        benchmark_name=benchmark_name,
        target_closes=dict(target_closes),
        benchmark_closes=dict(benchmark_closes),
        manifests=make_manifests(
            tuple(components), target_closes, benchmark_closes, as_of
        ),
    )


def const_closes(calendar, value="100.00"):
    return {day: Decimal(value) for day in calendar}


def test_validate_pack_rebuilds_method_scores():
    start = date(2026, 1, 5)
    calendar = calendar_days(start, 4)
    components = [
        daily(calendar[0], (60, 100), (100, 100), (50, 100)),
        # Zero DENOMINATOR on every component -> unavailable -> null score.
        # (Numerator 0 with a positive denominator would be a valid ratio 0.)
        daily(calendar[1], (0, 0), (0, 0), (0, 0)),
        daily(calendar[2], (20, 40), (2, 8), (3, 6)),
        daily(calendar[3], (100, 100), (100, 100), (100, 100)),
    ]
    pack = make_pack(
        calendar=calendar,
        components=components,
        target_closes=const_closes(calendar),
        benchmark_closes=const_closes(calendar),
    )
    validated = validate_pack(pack)
    assert isinstance(validated, ValidatedPack)
    assert validated.valid_dates == tuple(
        point["trade_date"] for point in validated.points if point["score"] is not None
    )
    first = validated.points_by_date[calendar[0]]
    # (0.6 + 1.0 + 0.5) / 3 * 100 = 70.00 -> repair phase from the real method.
    assert first["score"] == Decimal("70.00")
    assert first["phase"] == method.PHASE_REPAIR
    missing = validated.points_by_date[calendar[1]]
    assert missing["score"] is None
    assert set(missing["reasons"]) == {
        method.REASON_NO_VALID_DAILY_RETURNS,
        method.REASON_NO_LIMIT_EVENTS,
        method.REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET,
    }
    assert validated.points_by_date[calendar[3]]["score"] == Decimal("100.00")
    assert validated.as_of_index == len(calendar) - 1


def test_manifest_tampering_and_row_count_mismatch_raise():
    start = date(2026, 1, 5)
    calendar = calendar_days(start, 3)
    components = [daily(day, (1, 2), (1, 2), (1, 2)) for day in calendar]
    closes = const_closes(calendar)
    pack = make_pack(
        calendar=calendar,
        components=components,
        target_closes=closes,
        benchmark_closes=closes,
    )
    tampered = InputManifest(
        name="components",
        source_version="fixture-v1",
        frozen_at="2026-10-27T00:00:00+00:00",
        as_of=pack.as_of,
        row_count=3,
        adjustment="raw",
        sha256="a" * 64,
    )
    # Slotted frozen dataclasses expose no __dict__: copy with replace().
    with pytest.raises(ResearchValidationError, match="sha256 mismatch"):
        validate_pack(replace(pack, manifests=(tampered, *pack.manifests[1:])))
    wrong_rows = replace(pack.manifests[0], row_count=9)
    with pytest.raises(ResearchValidationError, match="row_count mismatch"):
        validate_pack(replace(pack, manifests=(wrong_rows, *pack.manifests[1:])))


def test_pack_structure_and_pit_violations_raise():
    start = date(2026, 1, 5)
    calendar = calendar_days(start, 3)
    good = [daily(day, (1, 2), (1, 2), (1, 2)) for day in calendar]
    closes = const_closes(calendar)

    # Non-ascending calendar.
    with pytest.raises(ResearchValidationError):
        validate_pack(
            make_pack(
                calendar=(calendar[1], calendar[0]),
                components=good[:2],
                target_closes=closes,
                benchmark_closes=closes,
                as_of=calendar[1],
            )
        )
    # Components after as_of are a look-ahead violation.
    with pytest.raises(ResearchValidationError, match="components_after_as_of"):
        validate_pack(
            make_pack(
                calendar=calendar,
                components=good,
                target_closes=closes,
                benchmark_closes=closes,
                as_of=calendar[1],
            )
        )
    # Duplicate component dates.
    dup = [good[0], good[0]]
    with pytest.raises(ResearchValidationError):
        validate_pack(
            make_pack(
                calendar=calendar,
                components=dup,
                target_closes=closes,
                benchmark_closes=closes,
            )
        )
    # Zero or negative close rejected.
    bad_closes = {day: Decimal("0") for day in calendar}
    with pytest.raises(ResearchValidationError):
        validate_pack(
            make_pack(
                calendar=calendar,
                components=good,
                target_closes=bad_closes,
                benchmark_closes=closes,
            )
        )
    # Counts violating 0 <= numerator <= denominator.
    bad_counts = [daily(calendar[0], (3, 2), (1, 2), (1, 2))]
    with pytest.raises(ResearchValidationError):
        validate_pack(
            make_pack(
                calendar=calendar,
                components=bad_counts,
                target_closes=closes,
                benchmark_closes=closes,
            )
        )
    # Naive frozen_at rejected.
    base = make_pack(
        calendar=calendar,
        components=good,
        target_closes=closes,
        benchmark_closes=closes,
    )
    manifests = tuple(
        replace(m, frozen_at="2026-10-27T00:00:00") if m.name == "components" else m
        for m in base.manifests
    )
    with pytest.raises(ResearchValidationError, match="timezone"):
        validate_pack(replace(base, manifests=manifests))


def test_canonical_payload_hash_matches_analysis_jobs_convention():
    assert canonical_payload_hash({"b": 2, "a": 1}) == _canonical_hash({"b": 2, "a": 1})
    assert len(canonical_payload_hash([])) == 64


def test_malformed_frozen_at_raises_research_validation_error():
    start = date(2026, 1, 5)
    calendar = calendar_days(start, 3)
    components = [daily(day, (1, 2), (1, 2), (1, 2)) for day in calendar]
    closes = const_closes(calendar)
    base = make_pack(
        calendar=calendar,
        components=components,
        target_closes=closes,
        benchmark_closes=closes,
    )
    for bad_value in ("not-a-date", "2026-10-27T25:00:00+00:00", "   "):
        manifests = tuple(
            replace(m, frozen_at=bad_value) if m.name == "components" else m
            for m in base.manifests
        )
        with pytest.raises(ResearchValidationError, match="frozen_at"):
            validate_pack(replace(base, manifests=manifests))


def test_datetime_subclass_keys_are_rejected_everywhere():
    start = date(2026, 1, 5)
    calendar = calendar_days(start, 3)
    components = [daily(day, (1, 2), (1, 2), (1, 2)) for day in calendar]
    closes = const_closes(calendar)
    base = make_pack(
        calendar=calendar,
        components=components,
        target_closes=closes,
        benchmark_closes=closes,
    )
    # datetime is a date subclass: exact-type checks must reject it.
    with pytest.raises(ResearchValidationError, match="as_of must be a date"):
        validate_pack(replace(base, as_of=datetime(2026, 1, 5, 9, 30)))
    tampered_closes = dict(closes)
    tampered_closes[datetime(2026, 1, 5, 9, 30)] = Decimal("100.00")
    with pytest.raises(ResearchValidationError, match="target_closes keys"):
        validate_pack(replace(base, target_closes=tampered_closes))


def test_close_stream_tampering_and_manifest_ordering_are_enforced():
    start = date(2026, 1, 5)
    calendar = calendar_days(start, 3)
    components = [daily(day, (1, 2), (1, 2), (1, 2)) for day in calendar]
    closes = const_closes(calendar)
    base = make_pack(
        calendar=calendar,
        components=components,
        target_closes=closes,
        benchmark_closes=closes,
    )
    # Changing one close without refreshing manifests breaks the hash chain.
    tampered_prices = dict(closes)
    tampered_prices[calendar[0]] = Decimal("999.99")
    with pytest.raises(ResearchValidationError, match="target_closes sha256 mismatch"):
        validate_pack(replace(base, target_closes=tampered_prices))
    # Duplicate manifest names are rejected.
    duplicate_manifests = (
        base.manifests[0],
        base.manifests[0],
        base.manifests[2],
    )
    with pytest.raises(ResearchValidationError, match="unique"):
        validate_pack(replace(base, manifests=duplicate_manifests))
    # Manifests must arrive in the canonical stream order.
    shuffled = (base.manifests[1], base.manifests[0], base.manifests[2])
    with pytest.raises(ResearchValidationError, match="ordered"):
        validate_pack(replace(base, manifests=shuffled))


def test_validate_pack_rejects_whitespace_only_text_fields():
    # _require_text must reject whitespace-only strings, not merely "": the
    # .strip() guard previously had no regression test. The matrix covers all
    # six text fields reachable through validate_pack -- the four pack-level
    # fields plus source_version/adjustment on each nested InputManifest -- and
    # is kept in one case so the suite count moves by exactly one test.
    start = date(2026, 1, 5)
    calendar = calendar_days(start, 3)
    components = [daily(day, (1, 2), (1, 2), (1, 2)) for day in calendar]
    closes = const_closes(calendar)
    base = make_pack(
        calendar=calendar,
        components=components,
        target_closes=closes,
        benchmark_closes=closes,
    )
    manifest_fields = ("source_version", "adjustment")
    for field in (
        "pack_id",
        "generator",
        "target_name",
        "benchmark_name",
        *manifest_fields,
    ):
        for whitespace in ("   ", "\t"):
            if field in manifest_fields:
                bad_pack = replace(
                    base,
                    manifests=tuple(
                        replace(manifest, **{field: whitespace})
                        for manifest in base.manifests
                    ),
                )
            else:
                bad_pack = replace(base, **{field: whitespace})
            with pytest.raises(
                ResearchValidationError,
                match=rf"{field} must be a non-empty string",
            ):
                validate_pack(bad_pack)


def test_factor_health_counts_coverage_reasons_and_holes():
    start = date(2026, 2, 2)
    calendar = calendar_days(start, 6)
    components = [
        daily(calendar[0], (50, 100), (50, 100), (50, 100)),
        # calendar[1] absent entirely -> present gap
        daily(calendar[2], (50, 100), (0, 0), (50, 100)),  # null score day
        daily(calendar[3], (50, 100), (50, 100), (50, 100)),
        daily(calendar[4], (50, 100), (50, 100), (50, 100)),
        # calendar[5] absent tail (not a hole between present days)
    ]
    closes = const_closes(calendar)
    pack = make_pack(
        calendar=calendar,
        components=components,
        target_closes=closes,
        benchmark_closes=closes,
    )
    health = build_factor_health(validate_pack(pack))
    assert health["calendar_days"] == 6
    assert health["present_days"] == 4
    assert health["valid_days"] == 3
    assert health["absent_days"] == 2
    assert health["null_days"] == 1
    assert health["coverage_ratio"] == Decimal("3") / Decimal("6")
    assert health["null_reasons"] == {
        method.REASON_NO_VALID_DAILY_RETURNS: 0,
        method.REASON_NO_LIMIT_EVENTS: 1,
        method.REASON_NO_TRADABLE_MEMBERS_FROM_YESTERDAY_GE2_SET: 0,
    }
    assert health["calendar_holes"] == [calendar[1]]
    assert health["sample_window"] == {"first": calendar[0], "last": calendar[4]}


def test_factor_health_distribution_and_perfect_rank_correlations():
    start = date(2026, 2, 2)
    calendar = calendar_days(start, 8)
    ratios = (
        (0, 100),
        (1, 10),
        (2, 10),
        (3, 10),
        (4, 10),
        (5, 10),
        (5, 10),
        (100, 100),
    )
    components = [
        daily(day, ratios[i], ratios[i], ratios[i]) for i, day in enumerate(calendar)
    ]
    closes = const_closes(calendar)
    pack = make_pack(
        calendar=calendar,
        components=components,
        target_closes=closes,
        benchmark_closes=closes,
    )
    health = build_factor_health(validate_pack(pack))
    distribution = health["score_distribution"]
    assert distribution["n"] == 8
    assert distribution["zero_count"] == 1
    assert distribution["hundred_count"] == 1
    # scores are 0,10,20,30,40,50,50,100
    assert distribution["min"] == Decimal("0.00")
    assert distribution["max"] == Decimal("100.00")
    assert distribution["median"] == Decimal("35.00")
    assert distribution["top_tie_share"] == Decimal("2") / Decimal("8")
    for key in method.COMPONENT_KEYS:
        block = health["components"][key]
        assert block["available_days"] == 8
        assert block["zero_denominator_days"] == 0
        assert block["distribution"]["n"] == 8
        assert block["distribution"]["min"] == Decimal("0")
        assert block["distribution"]["max"] == Decimal("1")
    correlations = health["rank_correlations"]
    for name in (
        "breadth_limit_balance",
        "breadth_promotion",
        "limit_balance_promotion",
        "breadth_score",
        "limit_balance_score",
        "promotion_score",
    ):
        assert correlations[name] == {"value": Decimal("1"), "reason": None}


def test_factor_health_empty_sample_degrades_without_raising():
    start = date(2026, 2, 2)
    calendar = calendar_days(start, 3)
    closes = const_closes(calendar)
    pack = make_pack(
        calendar=calendar,
        components=[],
        target_closes=closes,
        benchmark_closes=closes,
    )
    health = build_factor_health(validate_pack(pack))
    assert health["valid_days"] == 0
    assert health["coverage_ratio"] == Decimal("0")
    assert health["score_distribution"]["n"] == 0
    assert health["sample_window"] == {"first": None, "last": None}
    correlations = health["rank_correlations"]
    assert set(correlations) == {
        "breadth_limit_balance",
        "breadth_promotion",
        "limit_balance_promotion",
        "breadth_score",
        "limit_balance_score",
        "promotion_score",
    }
    assert all(
        item == {"value": None, "reason": REASON_INSUFFICIENT_SAMPLE}
        for item in correlations.values()
    )


def test_factor_health_is_independent_of_caller_decimal_context():
    start = date(2026, 2, 2)
    # Two of the three days tie at score 0: top_tie_share is the infinite
    # decimal 2/3, which a 2-digit ambient context would round to 0.67.
    calendar = calendar_days(start, 3)
    ratios = ((0, 100), (0, 100), (2, 10))
    components = [
        daily(day, ratios[i], ratios[i], ratios[i]) for i, day in enumerate(calendar)
    ]
    closes = const_closes(calendar)
    pack = make_pack(
        calendar=calendar,
        components=components,
        target_closes=closes,
        benchmark_closes=closes,
    )
    # Validate once under the default context: point rebuilding runs through
    # the real method module, which is outside this block's context contract.
    # The unit under test is build_factor_health, invoked twice below.
    validated = validate_pack(pack)

    def build():
        return build_factor_health(validated)

    default = build()
    previous_prec = getcontext().prec
    try:
        getcontext().prec = 2
        low_precision = build()
    finally:
        getcontext().prec = previous_prec
    assert (
        low_precision["score_distribution"]["top_tie_share"]
        == default["score_distribution"]["top_tie_share"]
    )
    assert low_precision["coverage_ratio"] == default["coverage_ratio"]
    # Asymmetric zero/hundred counts keep the two predicates distinguishable:
    # swapping them flips the pair (2, 0) into (0, 2).
    assert default["score_distribution"]["zero_count"] == 2
    assert default["score_distribution"]["hundred_count"] == 0


def test_factor_health_unknown_null_reason_fails_closed():
    start = date(2026, 2, 2)
    calendar = calendar_days(start, 3)
    closes = const_closes(calendar)
    pack = make_pack(
        calendar=calendar,
        components=[],
        target_closes=closes,
        benchmark_closes=closes,
    )
    base = validate_pack(pack)
    # ValidatedPack is directly constructible; a null point whose reason is
    # outside the method's closed three-code vocabulary must fail loudly
    # rather than be silently dropped from the null_reasons totals.
    tampered_point = {
        "trade_date": calendar[0],
        "score": None,
        "phase": None,
        "components": {
            key: {"numerator": 0, "denominator": 0} for key in method.COMPONENT_KEYS
        },
        "reasons": ["not_a_method_reason_code"],
    }
    tampered = replace(base, points=(tampered_point,))
    with pytest.raises(KeyError):
        build_factor_health(tampered)


def _closes_from_values(calendar, values):
    assert len(values) == len(calendar)
    return {
        day: Decimal(str(value)) for day, value in zip(calendar, values, strict=True)
    }


def _monotone_forward_pack(n_days=45, *, horizon=1):
    """Closes engineered so every on-grid h-step forward return strictly ascends.

    PIT discipline: component records stop at ``as_of``; the calendar (and
    closes) extend past it solely to provide forward labels.
    """
    assert n_days > horizon + 2
    start = date(2026, 3, 2)
    calendar = calendar_days(start, n_days)
    as_of_index = n_days - horizon - 2
    as_of = calendar[as_of_index]
    components = [
        daily(day, (i + 1, n_days), (i + 1, n_days), (i + 1, n_days))
        for i, day in enumerate(calendar[: as_of_index + 1])
    ]
    benchmark = const_closes(calendar, "100.00")
    # Every close exists (no None -> Decimal tokens). Only on-grid entry/exit
    # closes matter; flat 100 elsewhere keeps the mapping complete.
    values = [100.0] * n_days
    # On the h-spaced grid: slot i enters at C[i+1], exits at C[i+1+h], and
    # r = C[i+1+h]/C[i+1] - 1 = 0.0005 * (i+1), strictly ascending in slot i.
    for i in range(0, n_days - horizon - 1, horizon):
        entry = values[i + 1]
        values[i + 1 + horizon] = round(entry * (1 + 0.0005 * (i + 1)), 12)
    target = _closes_from_values(calendar, values)
    pack = make_pack(
        calendar=calendar,
        components=components,
        target_closes=target,
        benchmark_closes=benchmark,
        as_of=as_of,
    )
    return pack, calendar


def test_monotone_construction_yields_unit_rank_ic_and_strict_quintiles():
    pack, _calendar = _monotone_forward_pack()
    validated = validate_pack(pack)
    block = evaluate_horizon(validated, DEFAULT_CONFIG, 1)
    assert block["horizon"] == 1
    assert block["evaluation_kind"] == "time_serial_non_overlapping"
    assert block["pooled"]["rank_ic"] == Decimal("1")
    assert block["direction_hit_rate"] == Decimal("1")
    buckets = block["quintiles"]["buckets"]
    means = [bucket["mean_excess"] for bucket in buckets]
    assert all(bucket["n"] > 0 for bucket in buckets)
    assert all(means[i + 1] > means[i] for i in range(4))
    assert block["quintiles"]["monotonic"] is True
    assert block["quintiles"]["adjacent_agreement"] == Decimal("1")
    with localcontext(_decimal_context()):
        assert block["quintiles"]["spread"] == means[4] - means[0]
    for fold in block["folds"]:
        assert fold["rank_ic"] == Decimal("1")
    assert block["fold_summary"]["positive_share"] == Decimal("1")
    assert block["fold_summary"]["icir"] is None
    assert block["fold_summary"]["icir_reason"] == "zero_variance"


def test_hand_computed_returns_and_entry_at_next_tradable_point():
    start = date(2026, 3, 2)
    calendar = calendar_days(start, 5)
    # Strictly increasing scores (40, 41, 42) so the pooled rank IC is defined.
    components = [
        daily(day, (40 + i, 100), (40 + i, 100), (40 + i, 100))
        for i, day in enumerate(calendar[:3])
    ]
    # Signal calendar[0] enters at C1=100, exits at C2=101   -> r = +0.01
    # Signal calendar[1] enters at C2=101, exits at C3=98.98 -> r = -0.02
    # Signal calendar[2] enters at C3=98.98, exits at C4=100.9596 -> r = +0.02
    target = _closes_from_values(calendar, [100, 100, 101, 98.98, 100.9596])
    benchmark = const_closes(calendar, "100.00")
    pack = make_pack(
        calendar=calendar,
        components=components,
        target_closes=target,
        benchmark_closes=benchmark,
        as_of=calendar[2],
    )
    validated = validate_pack(pack)
    block = evaluate_horizon(validated, DEFAULT_CONFIG, 1)
    first = block["observations"][0]
    assert first["trade_date"] == calendar[0]
    assert first["entry_date"] == calendar[1]
    assert first["exit_date"] == calendar[2]
    assert first["r_target"] == Decimal("101") / Decimal("100") - 1
    assert first["r_benchmark"] == 0
    assert first["r_excess"] == first["r_target"]
    # Off-by-one reversal: shifting the signal one day changes the label.
    second = block["observations"][1]
    assert second["r_target"] == Decimal("98.98") / Decimal("101") - 1
    assert second["r_target"] != first["r_target"]
    # Score ranks 1,2,3 vs excess ranks [0.01,-0.02,0.02] -> [2,1,3] -> 0.5.
    assert block["pooled"]["rank_ic"] == Decimal("0.5")


def test_grid_windows_are_interior_disjoint_for_all_horizons():
    pack, calendar = _monotone_forward_pack(n_days=120)
    validated = validate_pack(pack)
    for horizon in (1, 5, 20):
        block = evaluate_horizon(validated, DEFAULT_CONFIG, horizon)
        intervals = [
            (
                calendar.index(obs["entry_date"]),
                calendar.index(obs["exit_date"]),
            )
            for obs in block["observations"]
        ]
        for earlier, later in zip(intervals, intervals[1:], strict=False):
            assert later[0] >= earlier[1], (horizon, earlier, later)


def test_drops_accounting_out_of_window_missing_close_and_skips():
    start = date(2026, 3, 2)
    calendar = calendar_days(start, 8)
    # Signals exist every day except index 2 (slot gap when h=2-ish via h=1 grid);
    # h=1 grid origin is index 0; index 2 slot has no signal.
    components = [
        daily(calendar[i], (50, 100), (50, 100), (50, 100)) for i in (0, 1, 3, 4, 5)
    ]
    target = dict(const_closes(calendar, "100"))
    benchmark = dict(const_closes(calendar, "100"))
    # Remove the exit close needed for the signal at index 5 (exit index 7? h=1
    # needs index 6): drop target close at index 6 to force missing_close.
    del target[calendar[6]]
    # as_of at the final day: signals at 5..7 that would need future closes are
    # out-of-window; give components at 6 and 7 too.
    components += [
        daily(calendar[6], (50, 100), (50, 100), (50, 100)),
        daily(calendar[7], (50, 100), (50, 100), (50, 100)),
    ]
    pack = make_pack(
        calendar=calendar,
        components=sorted(components, key=lambda d: d.trade_date),
        target_closes=target,
        benchmark_closes=benchmark,
        as_of=calendar[7],
    )
    validated = validate_pack(pack)
    block = evaluate_horizon(validated, DEFAULT_CONFIG, 1)
    assert block["grid"]["slots_skipped"]["slot_without_signal"] == 1
    assert block["grid"]["drops"]["missing_close"] >= 1
    assert block["grid"]["drops"]["out_of_window"] >= 1
    assert block["grid"]["signals_total"] == 7
    assert (
        block["grid"]["slots_total"]
        == block["grid"]["slots_accepted"]
        + block["grid"]["slots_skipped"]["slot_without_signal"]
        + block["grid"]["drops"]["missing_close"]
    )
    # This fixture has every signal either on the h=1 grid or out of window.
    assert block["grid"]["off_grid_signals"] == 0
    # Full signal accounting partitions the seven signals:
    # 7 == 3 accepted + 2 missing_close + 2 out_of_window + 0 off_grid.
    # slot_without_signal is a *slot* disposition (index 2 is an empty grid
    # position, not a signal) and belongs to the slot identity above rather
    # than this signal partition.
    assert block["grid"]["signals_total"] == (
        block["grid"]["slots_accepted"]
        + block["grid"]["drops"]["missing_close"]
        + block["grid"]["drops"]["out_of_window"]
        + block["grid"]["off_grid_signals"]
    )


def test_balanced_fold_sizes_and_insufficiency_gates():
    pack, _ = _monotone_forward_pack(n_days=45)
    validated = validate_pack(pack)
    block = evaluate_horizon(validated, DEFAULT_CONFIG, 1)
    sizes = [fold["n"] for fold in block["folds"]]
    assert sum(sizes) == len(block["observations"])
    assert max(sizes) - min(sizes) <= 1
    # n_days=45 -> as_of index 42 -> 43 accepted observations; 43 >= 40 and
    # K = 43 // 8 = 5 folds, so no observation/fold gate fires.
    assert block["gate_failures"] == []
    short = evaluate_horizon(
        validated, ValidationConfig(horizons=(1,), min_signals_per_fold=8), 1
    )
    assert short["fold_summary"]["folds_total"] == 5
    tiny_pack, _ = _monotone_forward_pack(n_days=10)
    tiny = evaluate_horizon(validate_pack(tiny_pack), DEFAULT_CONFIG, 1)
    assert "insufficient_observations" in tiny["gate_failures"]
    assert "insufficient_folds" in tiny["gate_failures"]
    assert "quintile_too_small" in tiny["gate_failures"]
    # Gate failures never suppress computable statistics (spec): the pooled
    # RankIC stays exactly 1; G6 fires only when the pooled IC itself is null.
    assert tiny["pooled"]["rank_ic"] == Decimal("1")
    # h=5 over the 10-day pack leaves a single slot (origin 0; the next slot
    # 5 is out of window): n<2 must degrade the pooled IC with the
    # insufficient_sample reason rather than zero_variance.
    h5 = evaluate_horizon(
        validate_pack(_monotone_forward_pack(n_days=10)[0]), DEFAULT_CONFIG, 5
    )
    assert len(h5["observations"]) == 1
    assert h5["pooled"]["rank_ic"] is None
    assert h5["pooled"]["reason"] == REASON_INSUFFICIENT_SAMPLE


def test_evaluate_all_respects_horizon_subset_and_constant_score_degrades():
    pack, _ = _monotone_forward_pack(n_days=60)
    validated = validate_pack(pack)
    subset = ValidationConfig(horizons=(1, 5))
    evaluation = evaluate_all(validated, subset)
    assert set(evaluation) == {"1", "5"}
    constant = [
        daily(day, (50, 100), (50, 100), (50, 100))
        for day in pack.calendar[: pack.calendar.index(pack.as_of) + 1]
    ]
    flat_pack = make_pack(
        calendar=pack.calendar,
        components=constant,
        target_closes=pack.target_closes,
        benchmark_closes=pack.benchmark_closes,
        as_of=pack.as_of,
    )
    block = evaluate_horizon(validate_pack(flat_pack), DEFAULT_CONFIG, 1)
    assert block["pooled"]["rank_ic"] is None
    assert block["pooled"]["reason"] == "zero_variance"
    assert block["quintiles"]["monotonic"] is None
    # Tied scores collapse into one non-empty bucket, so the empty-bucket
    # branch takes priority over the pooled-IC-unavailable branch.
    assert block["quintiles"]["monotonic_reason"] == REASON_EMPTY_BUCKET
    assert block["direction_hit_rate"] is None
    assert "pooled_ic_unavailable" in block["gate_failures"]


def test_forward_eval_is_independent_of_caller_decimal_context():
    pack, _calendar = _monotone_forward_pack(n_days=45)
    # Validate once under the default context: point rebuilding runs through
    # the real method module, whose quantize is fragile under prec=2. The
    # unit under test is evaluate_horizon, invoked twice below.
    validated = validate_pack(pack)

    def evaluate():
        return evaluate_horizon(validated, DEFAULT_CONFIG, 1)

    default_block = evaluate()
    previous_prec = getcontext().prec
    try:
        getcontext().prec = 2
        low_precision_block = evaluate()
    finally:
        getcontext().prec = previous_prec

    def fingerprint(block):
        return (
            tuple(
                (bucket["mean_excess"], bucket["mean_target"])
                for bucket in block["quintiles"]["buckets"]
            ),
            block["quintiles"]["spread"],
            block["quintiles"]["adjacent_agreement"],
            block["direction_hit_rate"],
            block["fold_summary"]["positive_share"],
        )

    assert fingerprint(low_precision_block) == fingerprint(default_block)


def test_fold_rank_ic_is_computed_within_fold_not_pooled():
    # Sixteen signals with strictly increasing scores and a return series
    # that rises within each block of 8 but whose second block (0.001..0.008)
    # sits entirely below the first (0.009..0.016). Score ranks are 1..16;
    # pooled return ranks are 9..16,1..8, so every rank difference d is +-8:
    # sum(d^2) = 16 * 64 = 1024 and the pooled Spearman equals
    # 1 - 6*1024/(16*(16^2-1)) = -43/85 (the fixed 34-digit sqrt path lands
    # on ...6469 rather than the direct-division ...6471; the production
    # output is pinned below), whereas each 8-member fold is co-monotone and
    # must report fold IC exactly 1. Computing a fold IC over the full sample
    # would surface the pooled value here and is killed by this test.
    start = date(2026, 3, 2)
    calendar = calendar_days(start, 18)
    components = [
        daily(calendar[i], (i + 1, 18), (i + 1, 18), (i + 1, 18)) for i in range(16)
    ]
    benchmark = const_closes(calendar, "100.00")
    values = [100.0] * 18
    values[1] = 100.0
    for i in range(16):
        r_i = 0.008 + (i + 1) / 1000 if i < 8 else (i - 7) / 1000
        values[i + 2] = round(values[i + 1] * (1 + r_i), 12)
    target = _closes_from_values(calendar, values)
    pack = make_pack(
        calendar=calendar,
        components=components,
        target_closes=target,
        benchmark_closes=benchmark,
        as_of=calendar[15],
    )
    block = evaluate_horizon(validate_pack(pack), DEFAULT_CONFIG, 1)
    assert len(block["observations"]) == 16
    assert len(block["folds"]) == 2
    assert [fold["n"] for fold in block["folds"]] == [8, 8]
    for fold in block["folds"]:
        assert fold["rank_ic"] == Decimal("1")
    assert block["pooled"]["rank_ic"] < 0
    assert block["pooled"]["rank_ic"] == Decimal(
        "-0.5058823529411764705882352941176469"
    )


def test_evaluate_horizon_rejects_horizon_outside_config():
    validated = validate_pack(_monotone_forward_pack()[0])
    with pytest.raises(ResearchValidationError, match="config.horizons"):
        evaluate_horizon(validated, DEFAULT_CONFIG, 7)
    with pytest.raises(ResearchValidationError, match="config.horizons"):
        # bool must be rejected by the exact-type int check, not truthiness.
        evaluate_horizon(validated, DEFAULT_CONFIG, True)
