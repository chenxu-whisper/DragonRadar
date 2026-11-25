# B2 IH1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish immutable, explicitly typed index history and expose a licensed PUB-only history drawer.

**Architecture:** Add frozen index basis to input bars without relaxing stock validation. Build a separately versioned block inside market JSON using the original archive coordinates. Parse the complete block strictly before projecting a requested calendar window. The drawer uses an independent cancellable read and the selected PUB calendar, never stock endpoints.

**Tech Stack:** Python 3, Pydantic, FastAPI, pytest, React, TypeScript, Vitest, Playwright.

---

Approved design: `docs/superpowers/specs/2026-11-24-index-history-design.md`; its approval status text is stale. Root is `E:/ByteDance/Projects/Code/.temp/DragonRadar-AllRequirements-20261124`. No commits, shared-file edits, live inputs, or database/services. Subagent dispatch is unavailable in this tool environment; execute sequentially with separate spec and quality review checkpoints.

## File Map

- `src/dragon_radar/domain/models.py`: `IndexBasis` and optional `DailyBar.index_basis`.
- `src/dragon_radar/domain/quality.py`: known-index-only basis/raw validation, series consistency.
- `src/dragon_radar/readmodels/index_history.py`: materialization, strict archive contracts, window projection.
- `src/dragon_radar/readmodels/market.py`: optional archive-coordinate keywords and materializer call.
- `src/dragon_radar/http/routes/indexes.py`: mandatory exact PUB selectors and licensed projection.
- `web/src/features/workbench/index-history.ts`: request validation, bounded fetch cancellation, calendar windows.
- `web/src/features/workbench/ResearchDrawer.tsx`: independent history resource and gap-aware chart.
- `tests/test_index_history.py`: synthetic fixed identities, units, materialization, parser, route.
- `web/tests/unit/index-history.test.ts`: calendar, precision, corrupt response, cancellation.
- `web/tests/e2e/reset-index-history.spec.ts`: drawer focus, windows, gap, denial and stale response.
- Dedicated runner configs under `.temp/b2-index-history`: prevent cache/log output outside the assigned location.

## Commands

Run Python from ROOT:

```powershell
$env:PYTHONPATH="$PWD/src"
$env:PYTHONPYCACHEPREFIX="$PWD/.temp/b2-index-history/pycache"
& .venv/Scripts/python.exe -m pytest tests/test_index_history.py -q -o cache_dir=.temp/b2-index-history/pytest 2>&1 | Tee-Object .temp/b2-index-history/python-red.log
```

Each GREEN reruns the identical command with a distinct green log filename. Run web tests from `ROOT/web`:

```powershell
node node_modules/vitest/vitest.mjs run tests/unit/index-history.test.ts --cache=false 2>&1 | Tee-Object ../.temp/b2-index-history/web-green.log
```

Use PowerShell process timeout for web tests (120 seconds focused, 300 seconds broad). Preserve all failing raw logs, never weaken tests. User implementation authorization supersedes the test-generation skill's test-only repair limit for subsequent, separately recorded implementation steps.

## Task 1: Frozen Basis And Quality

Status: implementation and dedicated red/green verification complete (17 passed). Separate spec and quality inspection passed; broader offline regression remains in Task 5.

- [x] Complete bits Steps 1–4 before tests: run preparation, read Python/JS rules, output explicit TARGETS and BUG_MAP. Use existing factories with `source_id=synthetic`, `source_version=s1`, `pool_version=p1`; no real provider claims.
- [x] Write `test_explicit_index_units_preserve_stock_contract` with known `IDX`, point prices and mixed stock batch version `a1`. Assert publishable with index raw version, stock point bars rejected.
- [x] Run RED; expect rejection of the missing index basis field.
- [x] Add this frozen input contract and optional bar field:

```python
class IndexBasis(FrozenModel):
    series_type: Literal["price", "total_return"]
    series_reference: Identifier
    price_unit: Literal["point"]
    amount_currency: Identifier | None
    volume_unit: Literal["share", "lot", "not_applicable"]

# DailyBar
index_basis: IndexBasis | None = None
```

- [x] Implement `bar_issues` branching only when `state_at(batch, sid, day).security_type == "index"` and basis exists. Require `point`, declared matching volume, currency amount unit, amount currency when amount exists, null N/A volume, factor 1 and `raw-index-v1`. Retain all generic numeric/OHLC/source/availability checks and existing stock checks. Unknown/non-index identity with basis is an error.
- [x] Add parameterized RED tests for nonfinite/negative/inverted/missing close, unknown identity, late availability, wrong raw factor/version, absent currency, N/A volume, mixed price/total-return or reference/unit changes for one security. Add normal validation tests for unsupported enums.
- [x] Implement per-security consistency checks for explicit index basis across supplied history; never default legacy basis.
- [x] Run GREEN and existing offline domain tests, then spec review followed by quality review.

## Task 2: Immutable Block And Strict Parser

Status: implementation and dedicated red/green verification complete (36 passed). Separate spec and quality inspection passed; final verification will broaden corruption cases.

- [x] Write RED tests calling the following API with fixed UUIDs and the original 64-character digest:

```python
block = materialize_index_history(
    batch,
    market_snapshot_id=market_id,
    source_archive_id=archive_id,
    source_archive_digest="a" * 64,
)
parsed = parse_archived_index_history(block)
assert len(parsed.points if hasattr(parsed, "points") else parsed.indexes[0].points) <= 250
```

- [x] Implement frozen archive contracts with `schema=dragon-radar.index-history`, integer `schema_version=1`, mandatory market/archive UUIDs and digest, `trade_date`, `as_of`, source/version/pool/calendar/provenance, full canonical calendar through cutoff and `archive_start/end`. Keep only last 250 sessions in each series, including missing sessions.
- [x] Implement builder selecting unique valid current index states via `state_at`. Preserve basis unavailable as an explicit entry with null coverage and empty points; valid entries retain identity, raw price type, basis, provider version, exact decimals, point provenance, coverage. Use `observation_missing` gaps and nullable missing numeric fields. Reject provided bad bars, duplicate keys and changing series rather than laundering them into gaps.
- [x] Implement parser revalidating JSON exact numbers (strings/Decimal only), strict dates, timestamps, UUIDs, counts, finite positive prices, nonnegative quantity/amount, complete or absent OHLC, bounds, per-point source/version/cutoff, sorted unique security IDs and sessions, exactly the retained calendar slice, and coverage recomputed from valid closes. Wrong schema or identity never degrades to legacy.
- [x] Add RED tests for 260 canonical sessions, missing interior session, candle/line/gap, exact long decimal string, legacy basis, corrupt coverage/schema/date/source/float/duplicate, missing calendar, retrospective labeling, invalid late bars.
- [x] Run GREEN. Verify P1 serialized bytes remain unchanged after creating distinct P2.
- [x] Extend `materialize_market(batch, topic_batch, *, market_snapshot_id=None, source_archive_id=None, source_archive_digest=None)`. Supply all three or none; only add history when all original archive coordinates are available. Do not fabricate IDs. Parent service hook must pass all three for new PUBs.
- [x] Add RED/GREEN market integration test ensuring the new block and old quotes/FAC-M03 coexist. Perform spec then quality review.

## Task 3: HTTP Projection And License

Status: implementation and dedicated red/green verification complete (58 passed). First HTTP red run exposed fixture serialization; corrected fixture and second red run proved missing route. Shared wiring intentionally untouched.

- [x] Write dedicated in-memory `PublishedSnapshot` fixtures and a recording repository stub; mount only the new router with existing error handlers. No PostgreSQL needed.
- [x] Add RED route tests requiring date/PUB/start/end, rejecting unknown/duplicate/malformed selectors and nonraw before repository calls; explicit unknown PUB/index 404, coordinate mismatch/end beyond PUB 400.
- [x] Implement `GET /api/index/{security_id}/history` using `query_values`, `query_date`, UUID parsing, `select_snapshot`, `_published_payload`, `snapshot_envelope` and `DecimalJSONResponse`. Require `PublishedSnapshot`; never read bars to reconstruct history. Use bound batch identity only to identify legacy known indices.
- [x] Repository selection authorizes embedded/current rights through the existing `access_policy`; public context requires sharing. Never return grant payload. Parent must use the existing `PublicSharingAccessPolicy` publicly.
- [x] Implement calendar projection: requested boundaries unchanged; adjusted boundaries snap forward/backward on full archived calendar; maximum 250 adjusted sessions; intersect actual points with retained archive. Return separate requested/adjusted/available bounds, basis, coverage, source and chart type. All gaps yields gap, all candles candle, otherwise line; never bridge gaps.
- [x] Add RED/GREEN tests for weekend-like canonical gaps, truncated archive windows, no_sessions, 200/null legacy/materialization unavailable, known unavailable history, 503 corrupt block, local/public embedded and current denial, PUB as_of unchanged, P1/P2 selectors, no fallback.
- [x] Perform spec review then quality review. Leave app/public/Caddy wiring untouched.

## Task 4: Drawer History

- [x] Output JS TARGETS for `index-history.ts` (file-level new API) and `ResearchDrawer` history behavior. Existing unavailable notice is intentional, not a defect. Run dependency preflight before test creation.
- [x] Write RED Vitest tests for `indexHistoryWindow(sessions, pubDate, 60)` using only explicit ordered canonical sessions, 20/60/120/250 options, short archives, invalid calendar; validate response coordinates, exact strings, partition counts, gaps and timestamps. Write cancellation test with a pending mocked fetch.
- [x] Implement independent same-origin `fetchIndexHistory(selection, sessions, signal)` in the owned module, explicit GET/no-store/same-origin, 15-second timeout, abort race including body parsing, sanitized ApiError, complete response validation and exact decimal strings. This avoids changes to shared `web/src/api.ts` allowlist. Use existing `useAccess`/session protection in the drawer hook.
- [x] Add history state keyed by index + PUB + date + selected window + access/session epoch + refresh generation. On changed key hide old state synchronously; abort old effect; ignore late responses; clear on denial/lock. Use selected PUB summary request `/api/snapshots/{PUB}` for calendar, not today's date or natural-day arithmetic.
- [x] Render controls labeled `指数历史窗口`, default 60, preserve quote/FAC-M03 and existing Esc/focus trap. Add accessible gap-separated SVG candles/line and exact-decimal table, units/type/range/source/coverage/missing reasons, no export. No chart while loading/error/denied.
- [x] Run GREEN unit tests, then TypeScript.
- [x] Write dedicated E2E tests using existing reset fixtures/mocks: short window, all four controls, gap segments, strict response rejection, 403 clearing, pending late response after close/window switch, Escape and opener focus, single drawer, mobile width, no stock reads.
- [x] Run RED E2E before drawer production edits, GREEN after. Dedicated Playwright config uses port 5186, Vite cache and all artifacts under ROOT `.temp/b2-index-history`; no database or daily services.
- [x] Perform spec review then quality review.

## Task 5: Verification And Handoff

- [x] Run dedicated Python suite and applicable offline domain/market regressions with cache under assigned temp root.
- [x] Run focused web unit/E2E tests, scoped TypeScript and dedicated build with output/cache under assigned temp root. Whole-workspace TSC is separately recorded; concurrent B3/B4 test files caused transient unrelated failures.
- [x] Run Ruff check/format check on owned Python files, compileall with redirected bytecode cache.
- [x] Execute bits `utree flush`; record unavailable telemetry honestly if preparation could not install utree on Windows.
- [x] Update this checklist incrementally. Write `.temp/b2-index-history/report.md` with exact files, commands/results, red/green log paths, scope, defect analysis, generated cases, integration hooks and limits. Real provider identity/rights/history completeness/PIT remain unverified.

## Coverage Review

Tasks 1–4 cover all approved design sections. Shared parent hooks: publication service archive metadata; local/public router assembly; public allowlist; GET Caddy patterns. The owned fetch requires no shared web allowlist modification. No schema migration or normalized index table write is necessary: basis persists in original immutable input JSON.
