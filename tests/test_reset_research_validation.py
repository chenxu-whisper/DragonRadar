"""Task 27 Slice A tests: research-method validation (synthetic only)."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal, getcontext
from pathlib import Path

import pytest

from dragon_radar.readmodels import sentiment_research as method
from dragon_radar.research import statistics as stats
from dragon_radar.research.models import (
    DEFAULT_CONFIG,
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
