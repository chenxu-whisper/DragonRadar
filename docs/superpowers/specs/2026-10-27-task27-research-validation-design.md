# Task 27 Slice A — Research Score Method Validation Design

## Status

Draft for specification review on 2026-10-27. Branch
`feature/task27-research-validation`, created from `4d08fcf` on
`feature/task21r-task22`. No implementation exists yet; this document is the
single frozen reference before the implementation plan is written.

## Scope Statement

Task 27 in the reset plan ("PIT backtest, factor health check, rolling
validation, and local simulated portfolio") is delivered in slices. This
specification covers **Slice A: validation of the `sentiment-research-v1`
research method only** — a pure-function subdomain that:

1. rebuilds research scores from frozen integer component counts using the
   real `sentiment-research-v1` builders;
2. produces a contemporaneous factor health report (coverage, distribution,
   component rank correlations, sample size);
3. produces forward, point-in-time descriptive evidence (non-overlapping
   grid, per-fold RankIC/ICIR, quintile layering, direction hit rate);
4. emits a versioned, closed-shape, hash-stamped report whose
   `evidence_status` can never claim that the method is effective,
   profitable, or predictive.

Everything else in Task 27 stays blocked/absent in this slice (see
Non-Goals).

## Goals

- Add an isolated, deterministic, side-effect-free Python package
  `dragon_radar.research` with no database, HTTP, clock-of-wall, filesystem,
  or global mutable state.
- Bind validation mechanically to the exact shipped method
  (`METHOD_VERSION = "sentiment-research-v1"`): scores are rebuilt through
  `build_component`/`build_research_point`; a tampered score cannot enter the
  evaluation because no score is accepted as input.
- Enforce point-in-time discipline structurally: label construction can only
  reference prices strictly after the signal date; no interpolation,
  extrapolation, zero-fill, or fall-forward is permitted anywhere.
- Provide exact Decimal statistics implemented with integer/average ranks —
  no `float` in financial math, no numpy/scipy or any new dependency.
- Report evidence honestly with a three-value `evidence_status`; synthetic
  inputs are permanently labeled synthetic, and insufficient real samples
  can never produce an effectiveness conclusion.
- Refine the existing Task 27 artifact guard so the validation subdomain is
  explicitly allowed while the backtest, simulation ledger, migration, and
  HTTP surfaces remain forbidden.

## Non-Goals

Slice A explicitly does NOT deliver and must leave absent:

- the backtest engine (`run_backtest`), fill simulation (`simulate_fill`),
  walk-forward parameter fitting, ablation/AI variant comparison;
- the local simulation ledger (IDB accounts/orders/fills/commands);
- migration `0014_research` (head stays `0013_query`);
- HTTP routes `POST /api/research/backtests` and
  `GET /api/research/backtests/{run_id}` (EX14/EX15 stay unimplemented and
  blocked on real inputs);
- any frontend panel, bundle, repository, or E2E change;
- real market-data ingestion, licensing, corporate-action handling,
  delisting/ST/suspension tradability rules, T+1 handling, fees, and
  slippage — those are backtest-slice concerns blocked on external inputs;
- any update to `docs/acceptance/reset-final.md`, README, CI workflow, or
  `scripts/check_prd_contracts.py` (Slice A does not change the seven-state
  ledger; B01/B02 and EX14/EX15 remain blocked);
- effectiveness, profitability, win-probability, or forecast claims of any
  kind; the output is descriptive research statistics with
  `research_only = true`.

## Relationship To Existing Artifacts

- Method under test: `src/dragon_radar/readmodels/sentiment_research.py`
  (Task 28, pure functions). The validator imports its public builders and
  `METHOD_VERSION`; it never duplicates the score formula.
- Task 27 authoritative executable wording:
  `docs/superpowers/plans/2026-09-20-prd-reset-refactor.md`, Task 27 section
  (factor health outputs missing/distribution/rank-correlation/sample;
  rolling windows strictly non-overlapping; frozen versions; no manufactured
  ground truth).
- The reserved test filename
  `tests/test_reset_research_validation.py` (Task 27 plan file manifest) is
  the test module this slice adds.
- Existing Task 29 guard
  `tests/test_reset_full_scope.py::test_task27_artifacts_remain_absent` is
  refined, not weakened (see Boundary Guards).
- Canonical JSON/hash precedent:
  `src/dragon_radar/analysis_jobs/models.py` (`canonical_json`/`cache_key`)
  and `src/dragon_radar/analysis_jobs/repository.py` (manifest hashing):
  `json.dumps(..., sort_keys=True, separators=(",", ":"),
  ensure_ascii=True, allow_nan=False)` → ASCII → `sha256` hexdigest.
  Slice A copies this byte convention; it does not import `analysis_jobs`.

## Chosen Architecture

A new self-contained domain package performs one pure pipeline:

```text
frozen SamplePack (counts + calendar + target/benchmark closes + manifests)
    -> fail-closed sample validation + manifest hash verification
    -> rebuild sentiment-research-v1 points via the real builders
    -> contemporaneous factor health
    -> per-horizon PIT labels on a strictly non-overlapping grid
    -> per-fold / pooled RankIC, ICIR, quintile layering, hit rate
    -> versioned closed-shape report + report_id hash
```

All functions are deterministic. Wall time enters only through an injected
`generated_at` argument that is excluded from the report identity hash.
There is no I/O at package runtime; synthetic fixtures live exclusively in
the test file.

### Why A Market-Level Score Produces Time-Series IC

`sentiment-research-v1` emits exactly one market-level score per trade date
(equal-weight combination of market breadth, limit balance, promotion), not a
cross-section of per-stock scores. Therefore the "cross-section" for each
evaluation is the set of **non-overlapping grid signal dates inside a time
fold**: scores and forward excess returns are ranked across those dates.
Quintile layering sorts historical grid observations by score rank into five
groups and compares subsequent mean excess returns. This is a time-serial
factor evaluation, not stock cross-sectional IC, and the report must label it
as such (`evaluation_kind = "time_serial_non_overlapping"`).

The forward target is a frozen **evaluation target series** (`target_closes`,
e.g. a tradable market aggregate) compared with a frozen benchmark series
(`benchmark_closes`, real packs conventionally use CSI 300). The validator
is agnostic to the names; both are declared strings on the pack, and naming a
series confers no licensing or production-readiness meaning.

### Rejected Alternatives

1. **Reuse `analysis_jobs` worker/repository for validation.** Rejected: its
   input model is owner-scoped persisted jobs; using it would require HTTP
   routes, a lease worker, and the `0007c`/`0014` persistence surface,
   tripping the route/migration guards for a computation that needs neither.
2. **Materialize validation results at publication time.** Rejected: forward
   labels require prices after the signal date, so publication-time
   materialization is inherently look-ahead and would also violate snapshot
   immutability.
3. **Compute scores inside the validator from raw ratios.** Rejected: any
   independently coded formula can drift from the shipped method. Rebuilding
   via the real builders and consuming only integer counts closes that gap.
4. **Import `roles.factors.decimal_median` / `roles.engine.dense_rank`.**
   Rejected for physical isolation of the read-only build boundary; the
   needed primitives (median, quantile, ranks) are reimplemented in the
   research package using the same decimal34 context convention. The only
   shared runtime symbols are `_decimal_context` precedent usage and the
   research method builders themselves.

## Package Layout (Closed Set)

New package `src/dragon_radar/research/` containing exactly:

| File | Responsibility | Allowed imports |
|---|---|---|
| `__init__.py` | Public API re-exports; package version constants | sibling modules only |
| `models.py` | Frozen input/config dataclasses, stream-name constants, error type | stdlib, `datetime` |
| `samples.py` | Pack shape validation, stream hashing, manifest verification, score rebuilding via `sentiment_research` | stdlib, `readmodels.sentiment_research` |
| `statistics.py` | Decimal guards, average ranks, Spearman, mean/sample-std/median/type-7 quantile, quintile membership | stdlib, `analytics.metrics._decimal_context` |
| `factor_health.py` | Contemporaneous coverage/distribution/rank-correlation block | `research.statistics`, `readmodels.sentiment_research` constants |
| `forward_eval.py` | PIT labels, non-overlapping grid, balanced folds, per-fold/pooled RankIC, ICIR, quintiles, hit rate, gates | `research.statistics`, `research.models` |
| `report.py` | Report assembly, closed-shape validation, canonical hashing, archive serializer/parser, evidence state machine | stdlib, sibling modules |

The package MUST NOT exist beyond this file set in Slice A. Concretely
forbidden sibling modules: `repository.py`, `backtest.py`, `execution.py`,
`walk_forward.py`, `ablation.py`, `routes.py`, `cli.py`, `worker.py`,
`models_db.py`.

Import purity (enforced by an AST scan test): a module in `dragon_radar.research`
may import only stdlib modules, `dragon_radar.analytics.metrics`,
`dragon_radar.readmodels.sentiment_research`, and `dragon_radar.research.*`.
Imports of `fastapi`, `sqlalchemy`, `httpx`, `requests`, or any
`dragon_radar.{http,storage,owner,publication,analysis_jobs,query,realtime,ai,lhb,news,review,regulation,topics,roles,ingestion,cli,api}`
module fail the test.

No new third-party dependency is added to `pyproject.toml`.

## Input Model (`models.py`)

All input types are `@dataclass(frozen=True, slots=True)` with strict
runtime type checks in `samples.py` (`type(x) is int`, `bool` rejected for
numeric fields, exactly as the method's own validators do).

```python
class ComponentCounts:
    numerator: int   # >= 0
    denominator: int # >= numerator; denominator == 0 means unavailable

class DailyComponents:
    trade_date: date
    breadth: ComponentCounts
    limit_balance: ComponentCounts
    promotion: ComponentCounts

class InputManifest:
    name: str            # one of the three STREAM_NAMES, unique
    source_version: str  # non-empty
    frozen_at: str       # ISO 8601 with timezone offset/designator
    as_of: date          # must equal pack.as_of
    row_count: int       # >= 0, must equal payload length
    adjustment: str      # non-empty declared policy ("raw", "qfq", ...); opaque
    sha256: str          # exactly 64 lowercase hex chars

class SamplePack:
    pack_id: str                  # non-empty
    synthetic: bool
    generator: str                # non-empty provenance tag
    seed: int | None
    as_of: date                   # signal knowledge cutoff; must be in calendar
    calendar: tuple[date, ...]    # strictly ascending, unique, non-empty
    components: tuple[DailyComponents, ...]  # ascending, dates subset of calendar
    target_name: str              # non-empty
    benchmark_name: str           # non-empty; real packs conventionally "csi300"
    target_closes: Mapping[date, Decimal]    # keys subset of calendar
    benchmark_closes: Mapping[date, Decimal] # keys subset of calendar
    manifests: tuple[InputManifest, ...]     # exactly the three streams

class ValidationConfig:
    horizons: tuple[int, ...]            # default (1, 5, 20); any non-empty subset
    min_coverage: Decimal                # default Decimal("0.80")
    min_signals_per_fold: int            # default 8
    min_folds: int                       # default 5
    min_quintile_size: int               # default 5
```

`STREAM_NAMES = ("components", "target_closes", "benchmark_closes")`.
In Slice A every horizon must belong to the frozen set `{1, 5, 20}`; any
other value raises `ResearchValidationError` at config validation. The
report keys horizons as their decimal strings (`"1"`, `"5"`, `"20"`), and
gate scopes use the matching `h1`/`h5`/`h20` codes. Defaults are frozen
constants, not heuristic tunables discovered from the data.

Error type: `ResearchValidationError(ValueError)` raised for every
structural violation, hash mismatch, and PIT violation. A violation never
becomes a report status (there is no `invalid` status).

## Stream Hashing And Fail-Closed Pack Validation

Canonical byte convention (identical to `analysis_jobs`):

```text
json.dumps(payload, sort_keys=True, separators=(",", ":"),
          ensure_ascii=True, allow_nan=False).encode("ascii")
    -> hashlib.sha256(...).hexdigest()
```

Payloads:

- `components`: `[[iso_date, b.num, b.den, l.num, l.den, p.num, p.den], ...]`
  in ascending date order;
- each close stream: `[[iso_date, decimal_token], ...]` in ascending date
  order, where `decimal_token = str(value.normalize())`.

Validation rules (each violation raises `ResearchValidationError`):

1. calendar: non-empty tuple of exact `date`, strictly ascending, unique;
   `pack.as_of` is in the calendar.
2. components: tuple ascending by unique date, every date in calendar and
   every date `<= pack.as_of` (future component data is a look-ahead
   violation, code `components_after_as_of`).
3. counts: exact non-negative ints, `numerator <= denominator`.
4. closes: mappings of exact-date keys to finite `Decimal` values strictly
   greater than zero; no float, bool, NaN, or infinity; keys subset of
   calendar.
5. manifests: exactly three entries named by `STREAM_NAMES`, each once;
   `as_of == pack.as_of`; `row_count` equals the payload length; `sha256`
   matches the recomputed hash exactly (a one-character change to any count
   or close raises); `frozen_at` parses as timezone-aware ISO 8601;
   `source_version`/`adjustment` non-empty.
6. identifiers/names: `pack_id`, `generator`, both series names non-empty;
   `synthetic` exact bool; `seed` is int or None.

### Score Rebuilding

For every component day the validator calls the real method:

```python
build_component(num, den, unavailable_reason=<component reason constant>)
build_research_point(trade_date, {"breadth": ..., "limit_balance": ..., "promotion": ...})
```

No `coverage` field is attached. Rebuilt points are the only source of
scores: a valid point yields a quantized 2-decimal `score`; a day with any
zero denominator yields `score is None` with the method's own reason tuple.
The rebuilt block additionally passes through
`materialize_sentiment_research` so method/version/research-only shape is
verified. The validator never calls `phase_for` for evaluation — phases are
contemporaneous buckets with no predictive claim; quintile membership is
derived from score ranks instead.

## Point-In-Time Label Contract

For a signal at calendar index `i` (the score must be rebuilt-valid there)
and horizon `h` trading days:

- entry reference: close of `calendar[i+1]` (the next tradable point — a
  signal formed after the day-t close cannot trade at the day-t close);
- exit reference: close of `calendar[i+1+h]`;
- `r_target    = C_target[i+1+h] / C_target[i+1] - 1`;
- `r_benchmark = C_bench[i+1+h]  / C_bench[i+1]  - 1`;
- `r_excess    = r_target - r_benchmark` (arithmetic excess, matching the
  existing `relative_return` semantics); raw `r_target` is retained.

All arithmetic runs in the decimal34 context (`prec=34`,
`ROUND_HALF_EVEN`, traps on `InvalidOperation`/`DivisionByZero`/`Overflow`).

Rules:

- signal inputs (component counts) never exceed `as_of`; prices after
  `as_of` are permitted solely for constructing labels, which is the
  intended forward use, not a signal-side leak;
- if `i+1+h >= len(calendar)`, the signal is dropped with reason
  `out_of_window` — never extrapolated;
- if either required target or benchmark close is absent/non-positive on
  `i+1` or `i+1+h`, the signal is dropped with reason `missing_close`
  (suspension-style gaps are the backtest slice's problem; here a label is
  simply unavailable);
- no fill price, limit, T+1, fee, or slippage modeling exists in Slice A.

## Non-Overlapping Grid And Folds (`forward_eval.py`)

Per horizon independently:

1. Candidate signal indices are rebuilt-valid component days.
2. The grid origin `s` is the smallest candidate index for which the label
  is available (`i+1+h < len(calendar)`, all four closes present).
3. Grid slots are `s, s+h, s+2h, ...` while the slot index is `<= as_of`
  index and `slot + 1 + h < len(calendar)`.
4. A slot without a valid signal is skipped (`slot_without_signal`); a slot
  with a signal but unavailable labels is dropped (`label_unavailable`);
  every accepted slot becomes one observation. Adjacent accepted windows
  share at most the boundary close; interiors are always disjoint, and
  non-adjacent accepted windows are separated by gaps — this is asserted
  programmatically in tests for every horizon.
5. Observations are ordered chronologically. Let `n` be their count and
   `K = n // min_signals_per_fold`. Folds are balanced contiguous time
   blocks: the first `n % K` folds (chronologically) take one extra
   observation when `K > 0`; every fold therefore has at least
   `min_signals_per_fold` observations and every observation is used.
   Because the method has no fitted parameters, folds are a **temporal
   stability** partition, not train/test windows. Nothing is fitted.

## Decimal Statistics Primitives (`statistics.py`)

- `average_ranks(values) -> tuple[Decimal, ...]`: ascending 1-based ranks;
  tied values share their average position as an exact Decimal
  (e.g. `[3, 1, 2, 3] -> [3.5, 1, 2, 3.5]`).
- `spearman(x, y) -> RankCorrelation`: average-rank both sides, then
  Pearson on the ranks under decimal34. Returns `(None, reason)` when
  `n < 2` (`insufficient_sample`) or either rank side has zero variance
  (`zero_variance`); it never fabricates zero.
- `decimal_mean`, `decimal_sample_std` (unbiased denominator `n-1`,
  `None` when `n < 2`), `decimal_median` (middle pair averaged),
  `decimal_quantile(sorted_values, q)` using deterministic type-7 linear
  interpolation (`position = q*(n-1)` zero-based), so the median equals the
  middle-pair average for even `n`.
- `quintile_buckets(n) -> tuple[int, ...]`: for average rank `r`,
  position `p = (r - 0.5) / n`, bucket `ceil(5*p)` in `1..5`. Because
  average ranks are half-integers and `5(r-0.5)/n` can never be an integer,
  no boundary ambiguity exists; ties always share a bucket.
- Every primitive rejects bool, float, non-Decimal, NaN, and infinities with
  `ResearchValidationError`, and requires equal non-empty sequence lengths
  for paired inputs.

## Factor Health Block (`factor_health.py`)

One contemporaneous block (no horizon dimension):

- coverage: `calendar_days`, `present_days` (component records),
  `valid_days` (non-null rebuilt scores), `absent_days`, `null_days`,
  `coverage_ratio = valid_days / calendar_days`;
  `null_reasons` always carries all three method reason codes
  (`no_valid_daily_returns`, `no_limit_events`,
  `no_tradable_members_from_yesterday_ge2_set`) with integer counts;
  `calendar_holes` lists calendar dates strictly between the first and last
  component day that lack a component record;
- `score_distribution` over valid scores: `n, min, max, mean, median, p25,
  p75, zero_count` (score exactly 0), `hundred_count` (score exactly 100),
  `top_tie_share` (largest multiplicity divided by `n`, reporting score
  concentration);
- per component (`breadth`, `limit_balance`, `promotion`):
  `available_days`, `zero_denominator_days`, and distribution of the
  unrounded `numerator/denominator` ratio (`n, min, max, mean, median, p25,
  p75`) computed on days where that component is available;
- `rank_correlations` on the valid-score day set (identical pairing for all
  six pairs, each `Decimal` or null-with-reason): the three component pairs
  and the three component-vs-score pairs;
- `sample_window`: first/last valid score dates.

The block reports numbers only — no "healthy/normal" verdicts.

## Forward Evaluation Block And Sufficiency Gates

Per horizon `h`, the block contains:

- echo: `horizon`, `evaluation_kind = "time_serial_non_overlapping"`,
  `grid_origin_date`, `step_trading_days = h`, `as_of`;
- grid accounting: `slots_total`, `slots_skipped` (with
  `slot_without_signal` count), and drop counters keyed by reason;
- `observations`: closed tuples
  `{trade_date, entry_date, exit_date, score, r_target, r_benchmark,
  r_excess}` in chronological order;
- `folds`: `{fold_index, start_date, end_date, n, rank_ic}` where
  `rank_ic = spearman(score, r_excess)` ranked only inside that fold;
- `fold_summary`: `folds_total`, `nonnull_ic`, `ic_mean`, `ic_std`,
  `icir = ic_mean / ic_std` (null with `zero_variance` when std is exactly
  0, null with `insufficient_folds` when fewer than two non-null fold ICs
  exist), `positive_share = count(ic > 0) / nonnull_ic`;
- `pooled`: `n`, `rank_ic` (Spearman over all observations);
- `quintiles`: five `{index, n, mean_excess, mean_target}` buckets from
  score ranks, `spread = mean_excess[Q5] - mean_excess[Q1]`,
  `monotonic` (strictly increasing when pooled IC is positive, strictly
  decreasing when negative; null with `empty_bucket`/`pooled_ic_unavailable`
  reason otherwise), `adjacent_agreement` (share of the four adjacent mean
  differences whose sign equals the pooled IC sign);
- `direction_hit_rate`: share of observations with
  `sign(r_excess) == sign(pooled.rank_ic)`; null when pooled IC is null or
  exactly zero;
- `gate_failures`: closed list of gate codes (below).

Sufficiency gates (frozen defaults; evaluated per real/synthetic pack):

| Gate | Code | Rule |
|---|---|---|
| G1 | `coverage_below_threshold` | global: `coverage_ratio >= min_coverage` (0.80) |
| G2 | `score_zero_variance` | global: at least two distinct valid score values |
| G3 | `insufficient_observations` | per horizon: `n >= min_folds * min_signals_per_fold` (40) |
| G4 | `insufficient_folds` | per horizon: `K >= min_folds` (5) |
| G5 | `quintile_too_small` | per horizon: every realized quintile bucket `n >= min_quintile_size` (5) |
| G6 | `pooled_ic_unavailable` | per horizon: pooled RankIC non-null |

Gate failures do not suppress the statistics: all computable numbers remain
present alongside the failure codes. Derived quantities that need more data
(ICIR, monotonicity) degrade to null-plus-reason rather than disappearing.

## Versioned Report (`report.py`)

Closed top-level shape with an exact key set, validated on build and on
archive parse:

```text
report_type            = "research-method-validation"
method_version         = "sentiment-research-v1"
validator_version      = "research-validation-v1"
research_only          = True            # anything else raises
config                 {horizons, min_coverage, min_signals_per_fold,
                        min_folds, min_quintile_size}
pack                   {pack_id, synthetic, generator, seed, as_of,
                        target_name, benchmark_name,
                        component_rows, target_rows, benchmark_rows,
                        sample_window}  # {first, last} = calendar endpoints
input_manifests        [three normalized manifests, name-sorted]
health                 <factor health block>
evaluation             {"1"|<h>: <forward block>, ...}  # config horizons, in order
evidence_status        one of the three values below
evidence_reasons       [{scope, code, detail?}, ...]
report_id              sha256 hexdigest
generated_at           ISO 8601 string supplied by the injected clock, or null
```

Forbidden as keys or string values anywhere in the schema: `effective`,
`validated`, `profitable`, `profit`, `win_probability`, `forecast`,
`prediction`, `recommendation`, `guaranteed` (the test asserts these tokens
never appear). Descriptions are mechanical (`rank_ic`, `mean_excess`, ...).

`report_id` is the sha256 of the canonical JSON of the whole report with
`generated_at` and `report_id` itself excluded. Same pack + same config +
same validator version must always yield the same `report_id`; this is a
test. `serialize_report` converts dates to ISO strings and Decimals to
canonical decimal tokens; `parse_archived_report` restores them and reruns
the full closed-shape validation, including recomputing and comparing
`report_id` and requiring `research_only is True` and exact version strings.

Derived metrics are quantized to `Decimal("0.000001")` with
`ROUND_HALF_UP` at assembly time; intermediate math is unrounded. Counts,
dates, ranks, and the 2-decimal rebuilt score remain exact.

### Evidence State Machine

Exactly three values:

1. `synthetic_only` — `pack.synthetic is True`, regardless of gate results
   (CI's permanent state; proves mechanics, never effectiveness);
2. `insufficient_real_sample` — real pack with at least one failed global or
   per-horizon gate; every failure is enumerated in `evidence_reasons`;
3. `real_sample_descriptive` — real pack passing every gate; still only
   descriptive statistics, `research_only = true`, never an effectiveness
   claim.

Structural/hash/PIT violations raise before a report exists; they are not a
fourth status. `evidence_reasons` entries use closed codes
(`scope ∈ {"global", "h1", "h5", "h20"}`; codes from the gate table plus the
pack-validation codes) and an optional non-interpreted `detail` string.

## Boundary Guards

`tests/test_reset_full_scope.py` is refined as follows (all other Task 29
assertions untouched):

1. `test_task27_artifacts_remain_absent` keeps asserting absence of:
   - `src/dragon_radar/http/routes/research.py`
   - `src/dragon_radar/migrations/versions/0014_research.py`
   - `web/src/repositories/local/simulation.ts`
   - `web/src/features/research/BacktestPanel.tsx`
   - `web/src/features/research/SimulationPanel.tsx`
   - `tests/test_reset_backtest.py`
   - `web/tests/unit/reset-simulation.test.ts`
   - `web/tests/e2e/reset-research.spec.ts`

   It removes `src/dragon_radar/research` and
   `tests/test_reset_research_validation.py` from the absent tuple.
2. A new sibling test asserts the research package exists and its `*.py`
   closed set equals `{__init__.py, models.py, samples.py, statistics.py,
   factor_health.py, forward_eval.py, report.py}`, and that the forbidden
   sibling modules listed above do not exist.
3. The existing web assertion stays: `web/src/features/research/` contains
   only `SentimentPanel.tsx`.
4. Untouched and still green: migration head `0013_query` / no `0014`
   revision; workbench and owner apps do not serve EX14/EX15; public app
   read-only/private-namespace assertions; `BLOCKED_REQS={"B01","B02"}`,
   `BLOCKED_EX={"EX14","EX15"}`; seven-state anchors and forbidden
   production-claim strings; `PRIVATE_PREFIXES` containing `/api/research`.
5. `scripts/check_prd_contracts.py` contains no research gating today and
   is not modified. CI, README, and `reset-final.md` are not modified; the
   blocked conclusions for B01/B02/EX14/EX15 stand.

## Test Plan (TDD, Synthetic Only)

All tests live in `tests/test_reset_research_validation.py`, use
hand-built synthetic packs (no database, no Docker, no network), and are
written RED first, in this order:

1. **Statistics primitives.** Average-rank tie cases; Spearman of a strictly
   monotone pair = 1, reverse pair = -1, a hand-computed tie case; `n < 2`
   and zero-variance nulls with exact reasons; quintile bucket sizes with
   ties never split; bool/float/NaN rejection; unequal-length rejection.
2. **Factor health.** Coverage counts with absent/null/valid days; all
   three reason codes counted; hand-computed quartiles and means; six
   pairwise correlations on a hand-built series; calendar hole listing;
   component distributions.
3. **Sample integrity / PIT negatives.** Tampered close or count →
   manifest hash mismatch raises; wrong `row_count`, non-lowercase/short
   hash, duplicate/non-ascending dates, components after `as_of`, negative
   or zero close, float input each raise; rebuilt score equality with a
   direct `score_components` hand example; a zero-denominator day yields
   null score with the method reason and no weight redistribution.
4. **Labels and grid.** Hand-built dual close series verifying h = 1/5/20
   returns and arithmetic excess; entry at `i+1` pinned by an off-by-one
   reversal case (shifting the signal by one day changes the answer);
   out-of-window and missing-close drops with exact counters; programmatic
   proof that all accepted windows per horizon are pairwise interior-
   disjoint; balanced fold sizes differ by at most one and stay
   chronological; skipped slots leave gaps, not overlaps.
5. **Forward metrics.** Hand-computed per-fold IC, ICIR, positive share,
   pooled IC; a perfectly monotone constructed relationship yields pooled
   IC = 1, strictly monotone quintiles, hit rate = 1; constant score
   degrades IC/monotonicity to null-plus-reason; null propagation never
   raises.
6. **Report.** `research_only=false` and wrong versions raise; exact key
   set; forbidden-token scan; determinism of `report_id` across repeated
   builds and clock changes; archive roundtrip restores dates/Decimals and
   recomputes the id; each of the three `evidence_status` values is
   reachable for the intended reason (small synthetic pack that fails gates
   still reports `synthetic_only`; a small real pack reports
   `insufficient_real_sample` with enumerated reasons; a long constructed
   real pack reports `real_sample_descriptive`).
7. **Boundary.** The two refined reset-full-scope tests; the AST import
   purity scan for every research module; the package closed-set test.

## Verification And Completion Criteria

- Targeted loop during TDD:
  `uv run pytest tests/test_reset_research_validation.py -q` plus the two
  refined tests in `tests/test_reset_full_scope.py`.
- Static gates before completion: `uv run ruff check .`,
  `uv run ruff format --check .`,
  `uv run python -m compileall -q src tests`; zero warnings/errors.
- Full-suite regression (`uv run pytest -q -rs`) is run once at slice
  completion and its real exit code/counts are reported by the
  implementation plan; timing-sensitive gates remain authoritative on CI
  Ubuntu given the local CPU frequency lock documented in Task 29.
- No web build or Playwright run is required (zero frontend files change);
  no database container is required.
- Commits stay on `feature/task27-research-validation`, staged only via
  explicit `git add -- <path>`; no push, merge, tag, version bump, release,
  or deployment at any point; `.trae/` never enters the index.

## Risks And Notes

- The default gates are deliberately conservative (40 non-overlapping
  observations and five folds per horizon imply roughly
  `h*40 + h + 1` trading days of calendar coverage — about 821 trading days
  for h=20 before warm-up). Real samples are expected to report
  `insufficient_real_sample`; that is the honest outcome, not a failure of
  the tool.
- Type-7 quantile interpolation and average-rank conventions are fixed
  here precisely so future slices cannot silently change statistics; any
  change requires a new `validator_version`.
- Synthetic packs verify mechanics only. The design cannot produce, and
  forbids wording that could be read as, evidence that the method predicts
  future returns.
