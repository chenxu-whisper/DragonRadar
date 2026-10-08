# Reset Base Acceptance

Generated on 2026-10-09 from a clean detached checkout of implementation
commit `0c6db2d0d35f8bec69ca298a3445ee92f8a9de5a`. This documentation
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
Caddy root certificate exported to `.temp/private-root.crt`; no private key
or credential was copied.

The full command was:

```powershell
$env:DRAGON_TEST_DATABASE_URL = "postgresql+psycopg://dragon_test:dragon-test-local-only@127.0.0.1:55432/dragon_test"
$env:DRAGON_TEST_PRIVATE_CA = (Resolve-Path ".temp/private-root.crt").Path
$env:PLAYWRIGHT_CHANNEL = "chromium"
uv run python scripts/verify_reset.py --mode full --output .temp/acceptance/reset-base-final.json
```

| Artifact | SHA-256 |
| --- | --- |
| `.temp/acceptance/reset-base-final.json` | `a6dd2d64ef0ec600848e3bb08894dc3b6cd38cb7b9e5cd099a661400b0045852` |
| `.temp/acceptance/task20-command-evidence.json` | `500fd94a0e44e2a0117d4c158192b0baa6c38459a9539845c9f9c6843df28633` |
| `.temp/acceptance/artifacts/reset-performance-157dda9e32127a8813c339e374d57447314391de27b86735b3378bd781fec6eb.json` | `157dda9e32127a8813c339e374d57447314391de27b86735b3378bd781fec6eb` |

The performance artifact is copied to a content-addressed immutable path
before later gates execute. Rerunning the performance test inside the full
backend suite therefore cannot invalidate the evidence hash.

Evidence uses `dragon-radar.task20-evidence` schema version 2. Automated
acceptance is available only to the in-process execution result created by the
production Runner. It records exact allowlisted argv/cwd/environment classes,
start/end times, exit status, and separately hashed atomic stdout/stderr byte
streams. Counts are derived from command-native summaries and JSON statuses;
performance/no-write observations are tied to the hashed raw artifact.
`--validate-existing` can check those relationships but always reports
`UNVERIFIED/INTEGRITY_ONLY` and exits nonzero. The full run recorded
`tested_commit` as `HEAD`; this evidence-only documentation commit is valid as
the recorded `HEAD^` relation.

## Observed Results

| Check | Result |
| --- | --- |
| CSV unit contract | 14 passed |
| Reset acceptance E2E | 35 passed: seven views at 375 and 1440, plus seven views at halved CSS viewport with DPR 2 for each physical width; every zoom case performs a keyboard-reachable main interaction and checks result visibility, overlap and overflow |
| Recovery drills | Backend 8 passed; local backup/restore E2E 5 passed; AI-off public E2E 1 passed |
| Full clean backend | 2,539 passed, 0 failed, 0 skipped in 1,443.66s |
| Ruff / format / compileall | Exit 0 for each command |
| Frontend unit and script suites | 330 Vitest and 8 Node tests passed |
| Frontend builds and bundle | Default build, 315,419-byte entry bundle check, then private build all passed |
| Frontend E2E | Legacy 80; Reset 86; private 69; AI public 1; AI private 4 |
| Deployment E2E | Production isolation 1 passed; TLS contract runtime 1 passed |
| Verifier contract | 34 tests passed, including fabricated all-pass JSON, self-hashed logs, fake exits, omitted Runner execution, native-output mismatch, immutable argv, atomic stream artifacts and commit relation |

The target worktree contained unrelated uncommitted backend, test and frontend
changes before Task20 review. The clean committed `0c6db2d` run above passed
Ruff and format; the unrelated changes were neither reverted nor included in
Task20 commits.

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
| Cold-labelled | 20/20, error rate 0 | 0.39137539989314973s | 0.7055875000078231s | 0.7426760999951512s | 0.59375s | 131,559,424 bytes |
| Warm | 20/20, error rate 0 | 0.34119349997490644s | 0.6152411999646574s | 0.6968985002022237s | 0.578125s | 132,734,976 bytes |

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
returned `queued` in 0.04740060004405677s. A separate real
`AnalysisJobWorker` then ran the delayed computation for
5.164260699879378s and persisted `succeeded`. Public requests neither enqueued
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

1. Automated acceptance now has an execution-only trust boundary. The
   immutable command graph runs through argv-array subprocesses with
   `shell=False`; caller-authored status/exit/count/log fields cannot enter the
   accepting path. Existing evidence is integrity-only and never exits as an
   acceptance pass.
2. Browser 200% coverage now uses layout-equivalent CDP device metrics rather
   than page painting scale: CSS width and height are halved and DPR is 2.
   Sub-320 CSS layout rules prevent sticky-header overlap and page overflow.
3. The first clean execution honestly rejected `2,529 passed, 10 skipped`
   because live TLS tests lacked `DRAGON_TEST_PRIVATE_CA`. After exporting only
   the local Caddy public root, the entire immutable graph was rerun and
   recorded `2,539 passed, 0 skipped`. A prior focused run also exposed Windows
   `.cmd` resolution; the Runner now resolves the executable while preserving
   the exact logical allowlisted argv and `shell=False`.

CI now installs Python, Node and Chromium, runs the default full verifier
against PostgreSQL 17, and uploads `.temp/acceptance/` with `if: always()`.
Missing, skipped, failed or timed-out required items cannot produce an
automated pass.
