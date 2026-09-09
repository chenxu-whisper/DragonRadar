# Data Foundation Verification

Date: 2026-09-09. Scope: the approved data-foundation and deterministic-calculation
increment only. This is not P0 product or public-deployment acceptance.

## Automated Checks

Environment: Windows, CPython 3.12.9, uv 0.8.2; PostgreSQL 17.11 in Docker.
The local test database is separate from the normal Compose database. Each
database test uses an isolated disposable schema.

| Check | Result |
| --- | --- |
| `uv run pytest -q -W error --cov=dragon_radar --cov-report=term-missing` | 417 passed, no warnings |
| Domain and analytics statement coverage | 100% |
| Whole-process statement coverage | 94%; CLI subprocess execution not included |
| `uv run ruff check .` | Passed |
| `uv run ruff format --check .` | Passed |
| `uv run python -m compileall -q src tests` | Passed |
| `uv build --out-dir .temp/dist` | Wheel and source distribution built |
| Installed-wheel migration and CLI workflow | Passed during Task 2 verification |
| Independent final review | Approved; independently reran 417 tests with `-W error` |

The suite exercises missing dates, cutoff availability, strict Decimal and
timestamp inputs, independently calculated returns, gaps/suspensions, source and
factor versions, limit semantics, raw-vs-adjusted prices, current coverage versus
interval eligibility, permission revocation/expiry, import key conflicts,
concurrent publication, revisions, immutable rows, transactional rollback,
metadata redaction and safe CLI/API failures.

Review-found defects were fixed with regressions: future source timestamp
acceptance, implicit numeric timestamp/float result coercion, and unsanitized
Alembic command errors. API tests use async HTTPX ASGI transport and application
lifespans; no warning filters or dependency downgrades hide upstream warnings.

## Container Checks

- Image builds with the locked Python environment and a non-root runtime user.
- `scripts/start-local.ps1 -Port 8010` ran successfully, including source rebuild,
  repeat migration, database health and API readiness.
- Host `GET http://127.0.0.1:8010/health/ready` returns `{"status":"ok"}`.
- Host `GET /api/v1/snapshots` returns HTTP 200 with `[]`; normal instance is empty.
- Docker port inspection confirms only `127.0.0.1:8010` maps API port 8000.
  Database port 5432 is not mapped to the host.
- Docker Desktop required a non-internal network for API host port forwarding.
  This is only in `compose.dev.yaml`; the base database network remains internal.
  The dev API has outbound network capability, not provider/model integrations.

A separate `dragon_smoke` database was used for container CLI and HTTP smoke
checks. Source registration, import and publication of the clearly synthetic
example succeeded. The read API returned exact interval return `"0.1"`, drawdown
`"0"` and normalized closes `["100","110"]`; source time remained null.

Both API-container and database-container restarts preserved the snapshot ID and
numeric result. Revoking the source changed fixed-snapshot reads to HTTP 403 and
the list to `[]`, while SQL counts confirmed the archive and snapshot were retained
(one each) with four audit events. This tests availability and rights behavior;
it is not a backup restoration/RPO/RTO exercise.

## Operational State

- Development branch: `feature/data-foundation`.
- Worktree: repository-root `.worktrees/data-foundation`.
- Root `.temp/` and `.worktrees/` are ignored; the tracked `.temp/.gitkeep`
  placeholder is removed from the index without deleting the local file.
- Normal Compose stack remains available on port 8010 with no market data.
- Temporary smoke API and integration database containers are stopped after
  verification; their data is retained. Start the test database container again
  before running PostgreSQL tests.
- Generated local Compose credentials are in ignored `.temp/local-compose.env`
  with owner/SYSTEM ACLs. No pre-existing user `.env` was read.

## Not Yet Verified or Implemented

No real data vendor/license activation, frontend, role-scoring thresholds,
regulatory rules, AI, owner browser context, public sharing, source retention
deletion workflow, backup restoration, 20-concurrent-user SLA, or 20-trading-day
shadow operation. The snapshot archive is JSON and listing currently loads
authorized archived payloads; production-scale optimization requires measurement.
CI configuration is present and its commands passed locally, but no remote CI run
or vulnerability-scan success is claimed.
