# Reset Base Acceptance

Generated on 2026-10-08 from a clean detached checkout of implementation
commit `402a41b74d1c0b64a5b55d1d49d2c109a3f187ef`. This documentation
commit follows the tested implementation commit; no future commit SHA is
claimed as tested.

## Gate Status

| Gate | Status | Evidence |
| --- | --- | --- |
| Task20 automated acceptance | PASS | All 21 required verifier records passed with exit 0, zero failures and zero skips |
| Overall acceptance | BLOCKED | External gates below remain unsatisfied |
| Real input | BLOCKED | No licensed real market, news or regulation input supplied |
| Official rules | BLOCKED | Synthetic rule fixtures are not approved official rules |
| Real model evaluation | BLOCKED | No real model/provider evaluation or approval supplied |
| Public sharing permit | BLOCKED | No real-data public-sharing permit supplied |
| At least 20 real trading-day shadow run | BLOCKED | No external dated run records supplied or inferred |

An automated pass does not satisfy any external gate and is not described as
development, production or synthetic-regression completion.

## Environment And Evidence

Windows 11, Python 3.12.9, Node 24.14.0, PostgreSQL 17.11, Chromium
Playwright, Docker, and isolated schemas in the disposable `dragon_test`
database were used. The ten live private-gateway checks used the public local
Caddy root certificate exported to `.temp/tls/private-root.crt`; no private key
or credential was copied.

The full command was:

```powershell
$env:DRAGON_TEST_DATABASE_URL = "postgresql+psycopg://dragon_test:dragon-test-local-only@127.0.0.1:55432/dragon_test"
$env:DRAGON_TEST_PRIVATE_CA = (Resolve-Path ".temp/tls/private-root.crt").Path
$env:PLAYWRIGHT_CHANNEL = "chromium"
uv run python scripts/verify_reset.py --mode full --output .temp/acceptance/reset-base-final.json
```

| Artifact | SHA-256 |
| --- | --- |
| `.temp/acceptance/reset-base-final.json` | `74a4a137081cb9c6dff1d5e7500ed55accc52a690c595518f5a7b9536cb62b64` |
| `.temp/acceptance/task20-command-evidence.json` | `c5c370351504bf3ac195954f397d677f89269f193a36ff6feeab6584fcf4f9ae` |
| `.temp/acceptance/artifacts/reset-performance-ee8911935738ddbf1512bf708509b6faf1a23688db575874214f487607847058.json` | `ee8911935738ddbf1512bf708509b6faf1a23688db575874214f487607847058` |

The performance artifact is copied to a content-addressed immutable path
before later gates execute. Rerunning the performance test inside the full
backend suite therefore cannot invalidate the evidence hash.

## Observed Results

| Check | Result |
| --- | --- |
| CSV unit contract | 14 passed |
| Reset acceptance E2E | 21 passed: seven views at 375 and 1440 with 200% page scale, plus export, keyboard, Esc/focus, conflict, reduced-motion and explicit JSON backup flows |
| Recovery drills | Backend 8 passed; local backup/restore E2E 5 passed; AI-off public E2E 1 passed |
| Full clean backend | 2,511 passed, 0 failed, 0 skipped in 1,450.93s |
| Ruff / format / compileall | Exit 0 for each command |
| Frontend unit and script suites | 330 Vitest and 8 Node tests passed |
| Frontend builds and bundle | Default build, 315,419-byte entry bundle check, then private build all passed |
| Frontend E2E | Legacy 80; Reset 72; private 69; AI public 1; AI private 4 |
| Deployment E2E | Production isolation 1 passed; TLS contract runtime 1 passed |
| Verifier contract | 6 focused tests passed |

The target worktree contained unrelated uncommitted formatting and frontend
changes before Task20 review. A focused verifier run there correctly failed
whole-tree Ruff. The clean committed `402a41b` run above passed Ruff and format;
the unrelated changes were neither reverted nor included in Task20 commits.

## Performance And No-Write Evidence

The fixture is explicitly synthetic: one archived security, four trading
sessions, one publication, one owner session and one completed analysis job.
The legacy archive is 3,352 bytes and publication payloads total 2,338 bytes.
The first run is honestly labelled
`cold_process_first_concurrent_run_db_cache_uncontrolled`; it does not claim an
OS-cold PostgreSQL cache. The second is
`warm_process_and_warmed_database`.

| Run | HTTP | P50 | P95 exact | Max | Process CPU | RSS |
| --- | --- | --- | --- | --- | --- | --- |
| Cold-labelled | 20/20, error rate 0 | 0.35037300013937056s | 0.628135799895972s | 0.6538726999424398s | 0.578125s | 115,638,272 bytes |
| Warm | 20/20, error rate 0 | 0.29673709999769926s | 0.6307210000231862s | 0.6565725998952985s | 0.625s | 116,793,344 bytes |

The measured public workload executed 480 SQL statements. Instrumentation
recorded every statement by request path and found zero
`INSERT/UPDATE/DELETE/DDL` statements. Row counts and row-content fingerprints
for all 72 application tables were identical before and after the workload.
The public app exposed no analysis-job route.

`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` was rerun with SQL and parameters
captured from each measured path:

- `/api/snapshots/list`: publication-sequence maximum using
  `publications_publication_sequence_key`, shared buffers recorded.
- `/api/snapshots/{id}`: publication lookup using `publications_pkey`, shared
  buffers recorded.

The controlled slow-work drill is labelled synthetic. Real owner submission
returned `queued` in 0.04368220013566315s. A separate real
`AnalysisJobWorker` then ran the delayed computation for
5.159800000023097s and persisted `succeeded`. Public requests neither enqueued
nor processed jobs.

## Recovery Drills

| Drill | Executed evidence | Observed disposition |
| --- | --- | --- |
| Snapshot revision and pinned rollback | Publication revision/idempotency and fixed old-ID tests | New revision appends; old published revision remains explicitly readable |
| Withdrawal and revocation | Display withdrawal plus owner source-revocation tests | Affected reads fail closed; stored immutable evidence remains |
| Source failure | Materialization failure rollback and revoked-source HTTP tests | Transaction writes roll back; source read returns 403 without stale substitution |
| Local backup restore | Five `reset-recovery.spec.ts` cases | Complete JSON history restores; damaged, stale and oversized files do not write |
| Owner session expiry/revoke | Expired, revoked and wrong-origin parameter cases | Owner APIs return 401 until pairing; public reads remain independent |
| AI off | Public AI-disabled E2E | Review/AI deep links remain unavailable and IndexedDB is not opened |

No migration was added. Application rollback remains an image rollback subject
to compatibility review; database downgrade is not part of Task20.

## Centralized Review Dispositions

1. The three clean-base backend failures reproduced at both `3bdabc1` and
   `98e1142`. The route, frontend type, frontend validator and regulation API
   tests all use `formal_event_count` plus `event_source_completeness`; only two
   older analysis assertions still expected `active_events`. Those stale
   assertions were corrected. No backend route contract was reverted.
2. The first clean full run found a CRLF-sensitive CI self-test and verifier
   build ordering that checked the public bundle after a private build. Both
   received focused RED/GREEN fixes.
3. Evidence review found that the full backend rerun could overwrite the
   canonical performance JSON. The verifier now snapshots it by content hash;
   a focused immutability test fails if later overwrites change consumed
   evidence.

CI now installs Python, Node and Chromium, runs the default full verifier
against PostgreSQL 17, and uploads `.temp/acceptance/` with `if: always()`.
Missing, skipped, failed or timed-out required items cannot produce an
automated pass.
