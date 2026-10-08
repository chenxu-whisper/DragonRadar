# Reset Base Acceptance

Generated on 2026-10-09 from a clean detached checkout of implementation
commit `864ab189c4429c334cd9e83dddf59d2090e5f9aa`. This documentation
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
Caddy root certificate from the already-running isolated private-review
gateway on `127.0.0.1:8030`; no private key or credential was copied.

The full command was:

```powershell
$env:DRAGON_TEST_DATABASE_URL = "postgresql+psycopg://dragon_test:dragon-test-local-only@127.0.0.1:55432/dragon_test"
$env:DRAGON_TEST_PRIVATE_CA = (Resolve-Path "<private-review-public-root.crt>").Path
$env:PLAYWRIGHT_CHANNEL = "chromium"
uv run python scripts/verify_reset.py --mode full --output .temp/acceptance/reset-base-task20-final.json
```

| Artifact | SHA-256 |
| --- | --- |
| `.temp/acceptance/reset-base-task20-final.json` | `ac7d7c81982379bc299aba47101662bd5183a0e734ba21f4c6956e380cc4d75b` |
| `.temp/acceptance/task20-command-evidence.json` | `308f92ddd027fe56cc646b510a17824ced483bb4b522508277adc5887887a5f0` |
| `.temp/acceptance/artifacts/reset-performance-3f3b9bb32d218d7b270d2f08e0500958cf80825ae9a4c1f1712d89982988179a.json` | `3f3b9bb32d218d7b270d2f08e0500958cf80825ae9a4c1f1712d89982988179a` |

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
| Full clean backend | 2,543 passed, 0 failed, 0 skipped in 1,394.32s |
| Ruff / format / compileall | Exit 0 for each command |
| Frontend unit and script suites | 330 Vitest and 8 Node tests passed |
| Frontend builds and bundle | Default build, 315,419-byte entry bundle check, then private build all passed |
| Frontend E2E | Legacy 80; Reset 86; private 69; AI public 1; AI private 4 |
| Deployment E2E | Production isolation 1 passed; TLS contract runtime 1 passed |
| Verifier and CI contracts | 38 tests passed, including fabricated all-pass JSON, self-hashed logs, fake exits, omitted Runner execution, native-output mismatch, immutable argv, atomic stream artifacts, CI TLS ordering/teardown, LF checkout and commit relation |

The target worktree contained unrelated uncommitted backend, test and frontend
changes before Task20 review. The clean detached `864ab18` run above passed
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
| Cold-labelled | 20/20, error rate 0 | 0.3059831000864506s | 0.5774214998818934s | 0.6008079999592155s | 0.5s | 115,503,104 bytes |
| Warm | 20/20, error rate 0 | 0.28546990011818707s | 0.5355324998963624s | 0.5745947998948395s | 0.390625s | 117,084,160 bytes |

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
returned `queued` in 0.03472250001505017s. A separate real
`AnalysisJobWorker` then ran the delayed computation for
5.15983820008114s and persisted `succeeded`. Public requests neither enqueued
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
   recorded `2,543 passed, 0 skipped`. A prior focused run also exposed Windows
   `.cmd` resolution; the Runner now resolves the executable while preserving
   the exact logical allowlisted argv and `shell=False`.

CI now installs Python, Node and Chromium, runs the default full verifier
against PostgreSQL 17, and first uses the checked-in Compose files to build,
migrate, owner-initialize and health-check an isolated private TLS gateway on
`127.0.0.1:8030`. It validates and exports only Caddy's public root through
`GITHUB_ENV`; generated credentials stay out of logs and artifacts. An
`if: always()` teardown removes project containers, volumes and secret state
before `.temp/acceptance/` is uploaded. Missing, skipped, failed or timed-out
required items cannot produce an automated pass.
