# Reset Sentiment Research Acceptance

Recorded: 2026-10-26

## Delivered

- Deterministic 0-100 sentiment research score, method version
  `sentiment-research-v1`, flagged `research_only: true`. Three components —
  breadth (`b`), limit-up/down balance (`l`), sealed promotion (`p`) — are
  combined at equal weight from **unrounded** Decimal ratios:
  `score = ROUND_HALF_UP(100 * (b + l + p) / 3)` quantized to two decimals.
  Float never enters the financial math; the 4-decimal component `value`
  fields are a display quantization only and never participate in scoring.
- Contemporaneous phase labels with contiguous half-open buckets:
  `ice < 20`, `ebb [20, 40)`, `divergent [40, 60)`, `repair [60, 80)`,
  `climax [80, 100]`; phases describe the same-day score only, never a
  time-order or forecast claim. Anchors: b=0.6 / l=1 / p=0.5 → 70.00 /
  `repair`; b=1/3 / l=1/2 / p=1/2 → 44.44 / `divergent`; 12.005 half-up
  boundary is 12.01 (not banker's 12.00).
- Honest unavailability. Any component without observations
  (zero-denominator day) emits `value: null` plus the closed reason
  (`no_valid_daily_returns` | `no_limit_events` |
  `no_tradable_members_from_yesterday_ge2_set`); weights are never
  redistributed and no zero is filled. With one or more missing components
  the point's `score`/`phase` are null and `reasons` lists every missing
  component in fixed key order.
- Publication-time materialization only. The market read model's single
  `_daily_sentiment_facts` source feeds both the raw sentiment observations
  and the new top-level immutable key `sentiment_research`
  (`method_version`, `research_only`, ascending `series` with one point per
  calendar session). The container schema id/version are unchanged; there
  is no migration and no `schema_contract.json` / `release_manifest.json`
  diff; no new dependency.
- EX16 read-only endpoint `GET /api/market/sentiment/research`
  (`date` | `snapshot_id`, `days` 1-120, default 20): headline fields plus
  the last-`days` slice of the series. Installed on the market route and
  allow-listed on the owner app and all three Caddyfiles
  (`market/(overview|sector-flow|sentiment/research|sentiment|heatmap)`);
  POST is rejected by the read-only middleware.
- Two-layer unavailability, isomorphic with the existing market endpoints:
  a published snapshot whose payload honestly lacks the block returns
  HTTP 200 `data: null` with `materialization_unavailable` (the route never
  recomputes); structural corruption of a stored block returns HTTP 503
  `DATA_UNAVAILABLE`; unpublished/missing selection returns the empty
  envelope.
- Fail-closed closed-shape validator shared by build and archive paths,
  with the route additionally cross-binding the headline `trade_date` to
  the snapshot's trade date (a block whose series ends on another day is
  503). Stored-score precision above two decimals, component `value`
  precision above four decimals, a bool impersonating the integer 1/3
  weight member, and non-integer coverage counts are all rejected.
- Reset-only UI: an independent "情绪研究" section, lazy-loaded behind
  `VITE_RESET_WORKBENCH`, showing the three components, formula/sample/
  version/limitations, the headline score/phase, and the date-ascending
  series. It is visually and structurally separate from the pre-existing
  raw sentiment observations (which carry no research score). The client
  validator independently recomputes the score (exact BigInt rational
  arithmetic) and the component display quantization before render and
  enters an alert state on any malformed, missing-item, or null-data
  response; no half-value is ever rendered.
- Compliance copy: no win-rate, future-up-probability, certainty, or
  forecast wording; a Playwright banned-word scan covers the section. The
  method's predictive validity is out of scope here and is owned by
  Task 27.

## AC mapping (task28-sentiment-research_spec.md §5)

- **AC1 determinism / unrounded inputs** — `test_anchor_ratios_score_70_00_repair`,
  `test_score_rounds_half_up_not_banker_s`,
  `test_score_uses_unrounded_ratios_but_sample_survives_display_quantization`,
  byte-determinism on `materialize_sentiment_research`.
- **AC2 phase boundaries** — 10-value parametrized
  `test_phase_boundaries_are_contiguous_half_open_buckets`.
- **AC3 missing-component semantics** —
  `test_missing_component_nulls_score_without_redistributing_weight`,
  `test_multiple_missing_components_list_every_reason_in_fixed_order`,
  zero-denominator component/quiet-session tests.
- **AC4 publication materialization, no container bump** —
  `test_market_payload_appends_research_block_without_container_schema_bump`
  and the shared-fact/single-source tests.
- **AC5 EX16 contract** — full HTTP contract (default 20, `days=1`,
  `days=120`, `days=0`/121/no-selection → 400 `INVALID_PARAM`), PIT
  double publication (`test_research_block_is_immutable_after_revised_republication`).
- **AC6 two-layer unavailability** —
  `test_missing_research_block_is_unavailable_without_recomputation`
  (white-box forbid on `materialize_market`) and the 14-case corrupt-block
  503 matrix.
- **AC7 PIT immutability** — revised-republication test compares the old
  snapshot's block field-by-field.
- **AC8 no read-path recomputation** — the route parses the archived JSONB
  only; monkeypatched recompute raises if hit.
- **AC9 frontend fail-closed** — 42 vitest cases in
  `reset-sentiment-research.test.ts`, including exact BigInt score and
  4-decimal value quantization recomputation.
- **AC10 build isolation** — four-variant build enumeration plus
  `check-default-bundle`; zero research markers in the default entry.
- **AC11 copy compliance** — banned-word Playwright scan plus phase-copy
  assertions.
- **AC12 full gates** — rows below.
- **AC13 accessibility/visual (rubric 2/2)** — keyboard details toggle,
  375px no-overflow, section + full-page axe clean, console zero errors
  across 8 E2E cases (×3 repeat-each).
- **AC14 engineering quality (rubric 2/2)** — single fact source shared
  with the raw sentiment view; Decimal end to end; closed shapes,
  exception mapping and two-layer unavailability identical to existing
  market endpoints. Independent read-only review returned PASS (no
  BLOCKER/MAJOR); its remaining backend defense-in-depth nits were closed
  in this same task with parametrized tests.

## Verification

| Check | Result |
|---|---|
| New backend file (`test_reset_sentiment_research.py`, PostgreSQL 17) | 75 collected; run as part of the three-file group: 146 passed, 0 skipped |
| Full backend suite `pytest -q` (PostgreSQL 17, final code) | 3928 passed, 15 skipped, 0 failed (1:06:35; all PG-gated cases executed) |
| Ruff lint (`ruff check`) | passed |
| Ruff format check | passed for all Task 28 files; the sole flagged repository file remains the pre-existing `docs/superpowers/plans/2026-10-09-task21-release-audit.md` exception, untouched |
| Python compileall | passed |
| Migration / schema_contract / release_manifest | no diff; no new dependency (`uv.lock`/`pyproject.toml` unchanged) |
| Frontend unit suite (`vitest --run --no-fileParallelism`) | 33 files, 1025 passed (research panel: 42; client api allow-list: 36) |
| Frontend type check (`tsc --noEmit`) | passed |
| New reset E2E spec (`reset-sentiment-research.spec.ts`) | 8 passed; `--repeat-each 3` → 24 passed |
| Reset E2E full suite (`playwright.reset.config.ts`) | 157 passed (4.9m) |
| Independent read-only review | PASS — AC13 2/2, AC14 2/2, no BLOCKER/MAJOR; all follow-up nits closed. A post-fix re-review of the hardening in `1004729` confirmed MINOR1/MINOR2/NIT3/NIT4 all PASS with no new findings and empirically verified each new test discriminates (old predicates accept, new checks reject) |

### Build isolation (four production variants, final code)

| Variant | Evidence |
|---|---|
| Default | entry `index-DlbHibfg.js` 307,971 B (gzip 100.17k), under the 315,500 gate; `check:bundle` passed; zero `/api/market/sentiment/research` markers; zero SentimentPanel chunks |
| Public sharing (`VITE_PUBLIC_SHARING=1`) | entry `index-DlbHibfg.js` 307,971 B; `check:bundle` passed; zero research markers (research is reset-only) |
| Reset (`VITE_RESET_WORKBENCH=1`) | entry `index-BVHr6z05.js` 322.14 kB (gzip 104.59k); lazy `SentimentPanel-C_K3vm4g.js` 9,746 B + `SentimentPanel-Drw_ozs3.css` 1,674 B; the research marker appears only inside the lazy chunk, never in the entry (the reset bundle gate intentionally does not scan this variant) |
| Private review (`VITE_PRIVATE_REVIEW=1` + `VITE_PRIVATE_AI=1`, reset base) | entry `index-C9o1gySg.js` 308,465 B (gzip 100.33k); zero research markers; no SentimentPanel chunk leaks outside the reset surface |

## Commits

- `1004729`: `sentiment-research-v1` pure scorer, publication-time
  materialization, EX16 read-only route, allow-list/Caddyfile wiring,
  closed-shape fail-closed validation and the 75-case backend suite.
- `c9e1665`: reset-only lazy research section with BigInt fail-closed
  validator, client allow-list and bundle marker gate, 42 vitest cases
  and 8 reset E2E cases (plus the realtime-suite null-data stub and
  pre-existing dl/dd axe fixes).

## Remaining Risk / Known Boundaries

- **Research-only label**: `sentiment-research-v1` is a contemporaneous
  descriptive score. Predictive validity, calibration, and any forward
  return claim are explicitly out of scope and belong to Task 27; the UI
  states this and no probability/forecast wording is shipped.
- **Public information granularity**: the endpoint is exposed through the
  public Caddyfile as approved by the spec; it exposes only publication-
  bound aggregated components, never member-level inputs.
- **Equal 1/3 weight is frozen**: weight redistribution, configurable
  weights, and alternative component sets require a new method version;
  immutable snapshots are never recalculated.
- **No web admin**: topic/market snapshots continue to be imported and
  published solely via the local CLI; this task adds no management
  surface.
