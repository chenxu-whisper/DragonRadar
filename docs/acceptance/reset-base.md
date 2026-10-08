# Reset Base Acceptance

Generated on 2026-10-09 from a clean detached checkout of implementation
commit `aa9a678e9f07a631ddd9a2e79bb32ab9f4d29add`. This documentation
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
| `.temp/acceptance/reset-base-final.json` | `7d164f05cc86cd9f5ef3b4c2afaa582c6f2cc9ae354cb89983b4fc9a67f5aff4` |
| `.temp/acceptance/task20-command-evidence.json` | `d36d19871a9ff248a49b106107e47d80225f68812cd4afe9f0280b243b0e2185` |
| `.temp/acceptance/artifacts/reset-performance-6ec3ef96e2e0094a252fb78a86b1b00439882c5be2658c68e67dc16c71f0eb02.json` | `6ec3ef96e2e0094a252fb78a86b1b00439882c5be2658c68e67dc16c71f0eb02` |

The performance artifact is copied to a content-addressed immutable path
before later gates execute. Rerunning the performance test inside the full
backend suite therefore cannot invalidate the evidence hash.

Evidence uses the closed `dragon-radar.task20-evidence` schema version 2.
Validation requires exact gate IDs and allowlisted argv/cwd/environment
classes, recomputes counts from hashed UTF-8 logs, derives every status, and
ties performance/no-write observations to the hashed raw artifact. The full
run and independent validation both recorded `tested_commit` as the current
`HEAD`; the following evidence-only documentation commit is valid only as the
recorded `HEAD^` relation.

## Observed Results

| Check | Result |
| --- | --- |
| CSV unit contract | 14 passed |
| Reset acceptance E2E | 35 passed: seven views at 375 and 1440, plus seven views at halved CSS viewport with DPR 2 for each physical width; every zoom case performs a keyboard-reachable main interaction and checks result visibility, overlap and overflow |
| Recovery drills | Backend 8 passed; local backup/restore E2E 5 passed; AI-off public E2E 1 passed |
| Full clean backend | 2,529 passed, 0 failed, 0 skipped in 1,464.47s |
| Ruff / format / compileall | Exit 0 for each command |
| Frontend unit and script suites | 330 Vitest and 8 Node tests passed |
| Frontend builds and bundle | Default build, 315,419-byte entry bundle check, then private build all passed |
| Frontend E2E | Legacy 80; Reset 86; private 69; AI public 1; AI private 4 |
| Deployment E2E | Production isolation 1 passed; TLS contract runtime 1 passed |
| Verifier contract | 24 focused tests passed, including adversarial schema, mode, commit, argv/cwd/environment, count, artifact, performance and duplicate/missing-gate cases |

The target worktree contained unrelated uncommitted formatting and frontend
changes before Task20 review. The clean committed `aa9a678` run above passed
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
| Cold-labelled | 20/20, error rate 0 | 0.349368599941954s | 0.6452558001037687s | 0.6687199999578297s | 0.578125s | 115,245,056 bytes |
| Warm | 20/20, error rate 0 | 0.3389839001465589s | 0.6769144998397678s | 0.6862912001088262s | 0.5625s | 116,649,984 bytes |

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
returned `queued` in 0.04449120000936091s. A separate real
`AnalysisJobWorker` then ran the delayed computation for
5.165430099936202s and persisted `succeeded`. Public requests neither enqueued
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

1. Evidence validation moved from permissive v1 maps to a closed v2 list
   schema so duplicate gate IDs are representable and rejected. Claimed status,
   arbitrary commands, wrong commits, inconsistent counts and altered
   performance summaries cannot pass.
2. Browser 200% coverage now uses layout-equivalent CDP device metrics rather
   than page painting scale: CSS width and height are halved and DPR is 2.
   Sub-320 CSS layout rules prevent sticky-header overlap and page overflow.
3. The first strict full run honestly failed two gates. Quiet `compileall`
   produced zero output lines despite exit 0 and a derived positive pass; the
   validator now requires positive pass counts, not nonempty output. The stock
   export test now waits for the raw-window state before exporting, eliminating
   a two-worker race. Focused RED/GREEN reruns preceded the final full run.

CI now installs Python, Node and Chromium, runs the default full verifier
against PostgreSQL 17, and uploads `.temp/acceptance/` with `if: always()`.
Missing, skipped, failed or timed-out required items cannot produce an
automated pass.
