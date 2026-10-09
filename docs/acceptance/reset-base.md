# Reset Base Acceptance

Generated on 2026-10-09 from a clean detached worktree at implementation
commit `2b7f2801be131fe273f5a8cca3831ea69d7eafec`. The verifier recorded that
same tracked-clean HEAD before and after both focused and full execution.
Only generated, ignored `.temp/` content was present.

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
uv run python scripts/verify_reset.py --mode full --output .temp/acceptance/double-check-full.json
```

Focused mode first executed the selected gates successfully and reported
`blocked_by_design`, because it cannot satisfy omitted full-only gates. Full
mode then passed all 21 automated gates.

The committed evidence is a sanitized derivative of those executions, not a
claim that the later documentation commit was tested. Machine-local reports,
absolute paths, full logs, command graphs, credentials, and private keys are
not committed.

| Committed artifact | Purpose |
| --- | --- |
| [manifest](evidence/task20/manifest.json) | Relative paths, byte counts, SHA-256 values, and execution provenance |
| [acceptance summary](evidence/task20/acceptance-summary.json) | Gate status, native counts, and external blockers |
| [performance raw](evidence/task20/performance-raw.json) | Sanitized synthetic raw metrics, SQL hashes, and normalized plans |
| [performance summary](evidence/task20/performance-summary.json) | Compact performance and endpoint-plan metrics |
| [native output](evidence/task20/native-output.txt) | Concise command-native result lines |

Run `uv run python scripts/verify_task20_bundle.py` to require every manifest
entry to be Git-tracked and to recompute every byte count and SHA-256 value.

## Suite Results

| Check | Result |
| --- | --- |
| CSV contract | 15 passed |
| Responsive acceptance E2E | 35 passed |
| Recovery drills | Backend 8, recovery E2E 5, AI-off E2E 1 passed |
| Full backend | 2,557 passed, 0 failed, 0 skipped in 1,447.68s |
| Ruff, format, compileall | Exit 0 |
| Frontend unit and scripts | 335 Vitest and 8 Node tests passed |
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
| 1 | 20/20 | 0.934725s | 1.427671s | 1.450128s | 1.562500s | 290,877,440 bytes |
| 2 | 20/20 | 1.236827s | 1.417923s | 1.484724s | 1.562500s | 298,672,128 bytes |
| 3 | 20/20 | 0.778479s | 1.301985s | 1.451968s | 1.578125s | 296,574,976 bytes |
| Aggregate | 60/60 | 1.194883s | 1.427671s | 1.484724s | 4.703125s | Per-round above |

Each round includes per-path snapshot, overview, rank, and stock metrics in
the raw artifact. The measured requests executed 300 SQL statements and zero
writes or DDL. Fingerprints for all 72 application tables were unchanged.
The raw artifact compacts those 300 executions into 20 unique path/query
shapes. Four distinct selected SQL hashes bind snapshot, overview, rank, and
stock to appropriate relations. Normalized evidence records relations,
indexes, node types/counts, and buffers; it contains no SQL text or parameters.

The controlled slow-work drill is labelled synthetic. Owner submission
returned `queued` in 0.319341s; the worker ran for 5.416536s and persisted
`succeeded`. Public requests did not expose or create analysis jobs.

## Implementation Notes

- Frontend request and CSV metadata share one rank sort descriptor: return,
  excess return, and amount change descend; drawdown ascends.
- CSV filters use lossless sorted canonical JSON, then formula protection is
  applied to the whole string cell.
- Responsive checks cover root width plus visible left/right overflow and
  exempt only genuinely contained horizontal scroll content.
- Publication payload caches perform paired LRU lookup/refresh and insert/trim
  atomically while SQL remains unlocked. Concurrent eviction becomes a normal
  miss/reload, and stale result rows cannot be paired with a newer signature.
- The source worktree's unrelated tracked modifications were neither reverted
  nor included in the implementation or evidence commits.
