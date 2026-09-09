# Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A tested local PostgreSQL-backed snapshot and deterministic analytics backend.

**Architecture:** Frozen typed snapshot inputs flow through rights and quality gates
before atomic publication. Pure analytics read only a selected published payload;
administration is local CLI-only.

**Tech Stack:** Python 3.12, uv, Pydantic, FastAPI, SQLAlchemy, psycopg, Alembic,
PostgreSQL, pytest, Ruff, Docker Compose.

## File Ownership

- `src/dragon_radar/domain/{models,quality}.py`: immutable input and quality contracts.
- `src/dragon_radar/analytics/{calendar,metrics}.py`: calendar windows and pure calculations.
- `src/dragon_radar/storage/{database,models,repository}.py`: database and transactions.
- `src/dragon_radar/api.py`, `cli.py`, `__init__.py`, `__main__.py`: read HTTP and local CLI.
- `alembic.ini`, `src/dragon_radar/migrations/`: packaged PostgreSQL migrations and immutability guards.
- `tests/test_domain.py`, `tests/test_analytics.py`: synthetic deterministic unit scenarios.
- `tests/test_storage.py`, `tests/test_api.py`: real PostgreSQL and HTTP integration.
- `Dockerfile`, `compose.yaml`, `compose.dev.yaml`, `.dockerignore`: local containers.
- `.github/workflows/ci.yml`, `README.md`, `docs/dependencies.md`: repeatable verification.

## Task 1: Contracts, Quality and Pure Analytics

- [x] Define failing tests for timestamp/Decimal validation, source rights expiry,
  duplicate/security/calendar/price errors, coverage and cutoff violations.
- [x] Run `.venv/Scripts/python -m pytest tests/test_domain.py -q`; observe the
  missing implementation failure before creating domain code.
- [x] Implement frozen Pydantic objects and typed issue/result structures; no
  provider client, credentials or mutable defaults. Grant expiry is exclusive.
- [x] Run tests, then add failing calendar/return/drawdown/limits tests before
  implementing pure analytics. Include 100 independently calculated return cases.

```python
from decimal import Decimal


def test_drawdown_uses_running_peak():
    from dragon_radar.analytics.metrics import maximum_drawdown

    assert maximum_drawdown(
        [Decimal("100"), Decimal("125"), Decimal("100")]
    ) == Decimal("0.2")
```

- [x] Reject bool/noninteger/out-of-range N; use exact provided session positions,
  not weekday inference. Test weekend endpoint adjustment, same day, reversed range,
  suspended/missing history, zero base, mixed versions, sixth comparison member,
  explicit no-limit sessions and unknown streaks.
- [x] Run `uv run pytest tests/test_domain.py tests/test_analytics.py -q`,
  `uv run ruff check src tests`, and `uv run ruff format --check src tests`.
- [x] Perform spec review, then quality review; fix and rerun before committing.
  Commit only owned files: `git commit -m "feat: add validated market contracts and deterministic analytics"`.

Task 1 evidence: 344 tests passed; Ruff and compileall passed. Spec findings fixed
in `21b78e6`; independent quality review approved after the spec re-check.

## Task 2: PostgreSQL Persistence, CLI and Read API

- [x] Write integration tests first using a dedicated PostgreSQL database/schema.
  Missing `DRAGON_TEST_DATABASE_URL` may skip locally but CI must provide it.
- [x] Run `uv run pytest tests/test_storage.py tests/test_api.py -q` against the
  real database, observe missing implementation failures.
- [x] Implement initial Alembic migration, tables, source grants, canonical
  JSON archive hashing, import idempotency and durable structured quality issues.
- [x] Publish only valid complete batches in one transaction; repeated publication
  returns the original result; corrections create explicit revisions.
- [x] Add database guards rejecting edits/deletes of published payloads and audit
  history. Add concurrent publication/idempotency regression scenarios.
- [x] Implement local CLI `migrate`, `source-register`, `source-revoke`, `import`,
  `publish`, `serve`. No HTTP mutation routes. JSON paths are local CLI paths only.
- [x] Expose `/health/live`, `/health/ready`, `/api/v1/snapshots`,
  `/api/v1/snapshots/{id}`, `/api/v1/snapshots/{id}/overview` and
  `/api/v1/snapshots/{id}/interval` with documented typed parameters and envelopes.
  Read rights are evaluated at request time; unknown IDs/dates never fall back.

```python
def test_unimplemented_http_methods_are_not_owner_access(client):
    response = client.post("/api/v1/snapshots")
    assert response.status_code == 405
```

- [x] Verify API sanitizes database failures and invalid inputs; precision, cutoff
  and version fields survive serialization. Health liveness and readiness differ.
- [x] Run migration to head twice, targeted tests, full suite, Ruff and compileall.
- [x] Perform spec review, then quality review; fix and rerun before committing.
  Commit owned files with `feat: persist immutable snapshots and expose local read API`.

## Task 3: Container/CI Verification and Handoff

- [x] Review selected dependency metadata, lock versions, document license scope
  and no runtime provider/model outbound traffic.
- [x] Add Python image, PostgreSQL healthcheck and internal network, persistent
  volumes, required database password and loopback-only development override.
  Never read the user's existing `.env`; use an isolated test configuration.
- [x] Add CI PostgreSQL service and commands:

```text
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run python -m compileall -q src tests
```

- [x] Document PowerShell setup, migrations, source authorization JSON, synthetic
  import example path, publication, queries, startup/shutdown and current limits.
- [x] Execute container build/start, liveness/readiness and persistence checks;
  keep production storage empty and synthetic fixtures isolated in test storage.
- [x] Review all changes, run the complete suite with PostgreSQL and coverage,
  record actual counts and residual limitations in a verification document.
- [x] Commit verified infrastructure/documentation. Keep the feature branch and
  root-level worktree available for user review; do not push or merge without request.

## Execution Notes

The owner approved starting this increment on 2026-09-09. Worktree placement was
subsequently changed by explicit request to repository-root `.worktrees/`.
`.temp/` is fully ignored; tracked placeholder removal keeps local files.
Main-worktree changes made by the owner are preserved.

Final evidence is in `docs/verification-data-foundation.md`: 417 tests passed with
warnings treated as errors; independent final review approved. Docker build,
local startup, source registration/import/publication, exact HTTP calculations,
API/database restart persistence and source-revocation denial were exercised.
Packaged migrations moved to `src/dragon_radar/migrations` to support installed
wheels. This increment deliberately retains the feature worktree without a
merge or push; main-worktree ignore edits remain visible for the owner.
