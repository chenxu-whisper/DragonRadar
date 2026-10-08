# Reset Base Acceptance

Observed on 2026-10-08 from the Task20 working tree based on commit
`3bdabc13cdc1958faae028f2c663febfa5f92277`.

## Status Boundaries

| Gate | Status | Evidence |
| --- | --- | --- |
| Development complete | PASS | CSV contract and six view actions, seven-flow Reset acceptance, performance fixture, verifier, CI and recovery documentation implemented |
| Synthetic regression | PASS | Focused automated results below; full-suite results must be read from the Task20 commit/CI report |
| Real input | NOT RUN | No licensed real market/news/regulation input supplied |
| Official rules | NOT RUN | Synthetic rules are test fixtures and are not official rules |
| Real model evaluation | BLOCKED | No real model/provider evaluation or approval supplied |
| Public sharing permit | BLOCKED | The gateway exists, but no real-data public-sharing permit supplied |
| At least 20 trading-day shadow run | BLOCKED / NOT RUN | No external shadow-run records supplied; no dates are inferred or fabricated |

Development completion and synthetic regression do not satisfy any independent
external gate in the table.

## Observed Synthetic Results

Environment: Windows 11, Python 3.12.9, PostgreSQL 17.11, Node 22-compatible
toolchain, Chromium Playwright, dedicated disposable `dragon_test` schemas.

| Check | Result |
| --- | --- |
| CSV export unit contract | 14 passed |
| Task20 seven-flow Reset acceptance | 7 passed |
| Verifier contract | 4 passed |
| PostgreSQL performance/no-write fixture | 1 passed |
| Frontend unit and script suites | 330 Vitest + 8 Node tests passed |
| Frontend environment E2E | Legacy 80; Reset 58; private 69; AI public 1; AI private 4; production isolation passed |
| Frontend builds/bundle | Default and private builds passed; entry 315,419 bytes within 315,500-byte budget |
| Full backend on dirty worktree | 2,496 passed; 10 live-TLS checks skipped; 3 stock-contract failures |
| Clean `3bdabc1` comparison | The same 3 stock-contract tests fail; Ruff and format pass on all 216 files |
| Cold-process first concurrent run | 20/20 HTTP 200; P50 0.467s; P95 0.811s; max 0.844s; error rate 0 |
| Warm process/warmed DB run | 20/20 HTTP 200; P50 0.374s; P95 0.744s; max 0.789s; error rate 0 |
| Resource evidence | CPU 0.828s/0.719s; peak working set 115,380,224/116,043,776 bytes |
| Query evidence | 240 SQL statements per 20-request mixed run; publication primary-key index scan; shared buffers recorded |
| Public no-write proof | Counts and PostgreSQL row-version sums unchanged for publication, analysis job/cache, AI task, review note and audit tables |

The first run is labelled `cold_process_first_concurrent_run_db_cache_uncontrolled`.
The test does not claim an OS-cold PostgreSQL cache. The second run is labelled
`warm_process_and_warmed_database`.

The fixed fixture contains one archived security, four trading sessions, one
publication, one owner session, and one explicitly queued owner analysis job.
The legacy archive has zero rows in the newer `security` read-model table.
Archive volume was 3,352 bytes and publication result payload volume was 2,338
bytes. Owner work projected above five seconds remains queued asynchronously;
the public app has no job route and public GETs create no jobs.

The three full-backend failures are pre-existing at clean commit `3bdabc1`:
`test_published_analysis_gets_only_project_archived_jsonb` and both parameters
of `test_stock_roles_are_always_an_array_when_domain_is_unavailable` expect
`regulation.active_events`, while the committed stock route returns
`formal_event_count` and `event_source_completeness`. Task20 does not alter that
backend contract.

Machine evidence:

- `.temp/acceptance/reset-performance.json`
- `.temp/acceptance/reset-base.json`

## Recovery Drills

| Drill | Expected and observed behavior | Remains usable | Disabled or blocked |
| --- | --- | --- | --- |
| Snapshot revision | Publish a new immutable revision and select it explicitly; old revision remains traceable | Authorized published revisions and local notes bound to their original snapshot | Silent replacement or mixed-snapshot export |
| Application rollback | Roll back application image only after compatibility review; retain DB and evidence | Existing compatible published reads | Database downgrade; no migration was added in Task20 |
| Withdrawal/revocation | Reauthorize every read and fail closed after source withdrawal/revoke | Unaffected authorized publications and local backup files | Affected public reads and new derived work |
| Source failure | Return unavailable/error state without stale cross-snapshot rows | Already loaded local review data and explicit backup/restore | Failed source domain and dependent exports |
| Local backup restore | Export explicit JSON, preview import, choose merge/save-as-new, validate before writes | Local notes, revisions, verification records and watchlist | CSV note export; damaged backup writes |
| Owner session expiry/revoke | Owner API returns unauthorized and requires pairing again | Public authorized GETs and browser-local review | Owner/private API operations until re-paired |
| AI unavailable/off | Keep AI routes/tools absent or report unavailable without fabricated output | Deterministic published facts, CSV export and local review | AI generation/evaluation |

The local JSON backup remains the only note export path. CSV exports contain
only currently rendered/filter/page published data and fail closed on snapshot
mismatch.

## Commands

```powershell
$env:DRAGON_TEST_DATABASE_URL = "postgresql+psycopg://dragon_test:dragon-test-local-only@127.0.0.1:55432/dragon_test"
uv run pytest -q -rs tests/performance/test_reset_queries.py
uv run python scripts/verify_reset.py --performance-evidence .temp/acceptance/reset-performance.json --output .temp/acceptance/reset-base.json

Set-Location web
npx vitest run tests/unit/reset-export.test.ts tests/unit/quality-gates.test.ts
npx playwright test tests/e2e/reset-acceptance.spec.ts --config playwright.reset.config.ts
```

The password above is only for the disposable loopback `dragon_test` container.
Do not use a production or research database.
