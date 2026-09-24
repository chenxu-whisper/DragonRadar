# Reset Range Analysis Acceptance

Recorded: 2026-09-24

## Delivered

- Decimal range-analysis primitives and immutable publication projections for
  interval return, benchmark excess return, maximum drawdown, amount change,
  normalized curves, drawdown curves, and raw stock history.
- Publication-bound public endpoints 11-14 with exact snapshot selection,
  trading-calendar window adjustment, explicit coverage partitions, strict
  payload validation, and unavailable states that never fall back to another
  snapshot or price basis.
- Raw, qfq, and hfq contracts share one versioned identity. Sources without
  provable adjustment semantics return unavailable instead of presenting raw
  prices under another label.
- Custom-range cache identity includes snapshot, dates, benchmark, adjustment,
  symbols, algorithm version, and adjustment version. Public GET requests only
  read validated cache entries and never enqueue work or write storage.
- Owner-only idempotent analysis jobs with session and CSRF enforcement,
  principal isolation, authorization rechecks, durable leases, retries,
  heartbeat renewal, Decimal JSON responses, and a dedicated private Compose
  worker.
- Reset analysis UI with exact N+1 trading-session windows, benchmark and
  adjustment controls, sortable ranking evidence, up to five comparison
  symbols, normalized/drawdown views, null-gap line breaks, and adjusted-window
  disclosure.
- Snapshot-bound stock detail and raw K-line views. Candlesticks render only
  for complete valid OHLC points; close-only data uses a discontinuous line.
  Roles, seal evidence, regulation unavailability, and local-review actions
  retain the same snapshot context.
- Fail-closed frontend validation for ranking order, competition ranks,
  benchmark arithmetic, coverage, curve baselines, point value domains,
  symbol partitions, OHLC invariants, and response identity.

## Verification

| Check | Result |
|---|---|
| Real PostgreSQL analysis-route stage | 120 passed |
| Real PostgreSQL owner/worker stage | 152 passed |
| Real PostgreSQL lease-heartbeat stage | 66 passed |
| Clean-HEAD targeted backend rerun without PostgreSQL | 222 passed, 107 skipped |
| Clean-HEAD full backend rerun without PostgreSQL | 1401 passed, 727 skipped |
| Clean-HEAD frontend unit tests | 240 passed |
| Reset analysis, market, and leaders E2E | 23 passed |
| Frontend production build | passed |
| Default entry bundle | 313,964 bytes, within 315,000-byte gate |
| Ruff lint | passed |
| Ruff format check | 167 files formatted |
| Python compileall | passed |
| PRD contract checker | REQ=57, endpoints=29, examples=29, JSON=30 |
| Final specification review | passed |
| Final code-quality review | passed |
| Desktop and mobile visual inspection | 1280px and 375px passed |

The final clean-HEAD rerun used a detached worktree at `.temp/task13-verify`.
Docker Desktop and the local PostgreSQL test port were unavailable during that
last rerun, so database tests were reported as skipped rather than relabeled as
passing. The real PostgreSQL results above were recorded during the corresponding
implementation stages.

The accepted populated-state captures are stored under
`web/.temp/reset-analysis/`. They are local evidence and are not committed.

## Commits

- `8aaaeda` through `d29dd37`: Decimal analysis read models, publication-bound
  APIs, response hardening, stock projection, and versioned job persistence.
- `241ee60` through `18e12c3`: owner routes, cache validation, coverage
  partitions, worker runtime, response contracts, and lease heartbeat.
- `4af9924`: snapshot trading calendar for exact frontend windows.
- `8b9c9af`: reset analysis, comparison, and stock-history UI.
- `76d8bcd`, `7c10d38`, and `aca047d`: fail-closed frontend response and
  ordering validation.

## Remaining Risk

`PublicationRepository` still reads the complete archived market JSONB before
projecting analysis and stock data. Task 13 intentionally does not introduce
new projection tables. Task 20 must measure realistic payload size, latency,
memory, cache state, and 20-concurrent-query behavior before deciding whether
to split this storage path.

Synthetic fixtures establish deterministic behavior, not production data
coverage, licensed adjustment semantics, public deployment approval, or
investment validity.

## Workspace Note

The shared worktree retains unrelated unstaged changes that predate the final
Task 13 acceptance pass. They were not reverted or included in Task 13 commits.
