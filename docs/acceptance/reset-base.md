# Reset Base Acceptance

Generated on 2026-10-09 from the clean acceptance worktree at implementation
commit `5460a4caee619ed7920efbd5fdf06bc87edcfd8f`. The verifier recorded the
same tracked-clean HEAD before and after execution. Only generated, untracked
`.temp/` content was ignored.

## Status

| Gate | Status |
| --- | --- |
| All 21 automated Task20 gates | PASS |
| Overall production acceptance | BLOCKED |
| Licensed real input | BLOCKED |
| Approved official rules | BLOCKED |
| Real model evaluation | BLOCKED |
| Public-sharing permit | BLOCKED |
| At least 20 real trading-day shadow run | BLOCKED |

Automated acceptance does not satisfy or imply any external production gate.
No migration was added.

## Execution

Environment: Windows 11, Python 3.12.9, Node, PostgreSQL 17.11, Chromium,
Docker, and the disposable `dragon_test` database. The live private gateway
tests used only the current Caddy public root certificate.

```powershell
$env:DRAGON_TEST_DATABASE_URL = "postgresql+psycopg://dragon_test:dragon-test-local-only@127.0.0.1:55432/dragon_test"
$env:DRAGON_TEST_PRIVATE_CA = "<current-caddy-public-root.crt>"
$env:PLAYWRIGHT_CHANNEL = "chromium"
uv run python scripts/verify_reset.py --mode full --output .temp/acceptance/reset-base-5460a4c.json
```

| Artifact | SHA-256 |
| --- | --- |
| `.temp/acceptance/reset-base-5460a4c.json` | `35224c2facbc92deffcb323d6ac337a6d0547c9df1352f1eaa19e324882140f5` |
| `.temp/acceptance/task20-command-evidence.json` | `392bff6d3b963698e32b1d8d4be4f51a243353aede462ebeff55c707d6376e57` |
| `.temp/acceptance/artifacts/reset-performance-2127dee0f01658852f02e32c9b4e08687afe3769f1fe9ecad1a9addcbb54e43d.json` | `2127dee0f01658852f02e32c9b4e08687afe3769f1fe9ecad1a9addcbb54e43d` |

## Suite Results

| Check | Result |
| --- | --- |
| CSV contract | 17 passed |
| Responsive acceptance E2E | 35 passed |
| Recovery drills | Backend 8, recovery E2E 5, AI-off E2E 1 passed |
| Full backend | 2,552 passed, 0 failed, 0 skipped in 1,445.74s |
| Ruff, format, compileall | Exit 0 |
| Frontend unit and scripts | 337 Vitest and 8 Node tests passed |
| Builds and bundle | Default/private builds passed; entry 315,419 bytes |
| Frontend E2E | Legacy 80; reset 86; private 69; AI public 1; AI private 4 |
| Deployment/runtime | Production isolation 1; TLS contract runtime 1 passed |

## Performance

The fixed fixture is explicitly synthetic: 100 securities, 61 trading
sessions, one publication, one owner session, and one completed analysis job.
The market archive is 3,597,209 bytes and publication payload is 509,039
bytes.

A separate 20-concurrent warmup covered snapshot, overview, rank, and stock.
It was followed by three measured 20-concurrent rounds. OS and PostgreSQL
caches were not controlled; the evidence therefore makes no cold-cache claim.

| Round | Success | P50 | P95 | Max | CPU | RSS |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 20/20 | 0.913720s | 1.319977s | 1.323795s | 1.453125s | 270,327,808 bytes |
| 2 | 20/20 | 0.950899s | 1.252013s | 1.256734s | 1.421875s | 270,561,280 bytes |
| 3 | 20/20 | 1.067229s | 1.293446s | 1.294116s | 1.421875s | 284,819,456 bytes |
| Aggregate | 60/60 | 0.992506s | 1.293446s | 1.323795s | 4.296875s | Per-round above |

Each round includes per-path snapshot, overview, rank, and stock metrics in
the raw artifact. The measured requests executed 300 SQL statements and zero
writes or DDL. Fingerprints for all 72 application tables were unchanged.
Captured SQL and `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` plans are present
for all four actual paths.

The controlled slow-work drill is labelled synthetic. Owner submission
returned `queued` in 0.246574s; the worker ran for 5.401920s and persisted
`succeeded`. Public requests did not expose or create analysis jobs.

## Implementation Notes

- Frontend request and CSV metadata share one rank sort descriptor: return,
  excess return, and amount change descend; drawdown ascends.
- CSV formula protection applies independently to filter keys and values.
- Responsive checks cover root width plus visible left/right overflow and
  exempt only genuinely contained horizontal scroll content.
- Publication payload caches use PostgreSQL row-version signatures, refresh
  changed rows, preserve fixed list-query budgets, and recheck current grants
  and withdrawal state on every market-only cache hit.
- The source worktree's unrelated tracked modifications were neither reverted
  nor included in the implementation or evidence commits.
