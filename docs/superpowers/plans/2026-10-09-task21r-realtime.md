# Task 21R Realtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add authorized point-in-time quote batches, minute revisions, transparent anomaly events, source-defined intraday sector flow, EX04-07 read APIs, and an Overview realtime panel without presenting synthetic data as production realtime input.

**Architecture:** A new `dragon_radar.realtime` package owns strict models, immutable PostgreSQL storage, internal ingestion, point-in-time reads, and deterministic anomaly rules. Public FastAPI routes are read-only and injected with a dedicated repository; the frontend validates every response and keeps intraday batch identity separate from daily snapshot identity. Migration `0011_realtime` extends the frozen chain and regenerates the release manifest and PostgreSQL 17 schema contract without editing existing migrations.

**Tech Stack:** Python 3.12, Pydantic, SQLAlchemy Core, PostgreSQL 17, Alembic, FastAPI, React, TypeScript, Vitest, Playwright, pytest, Ruff, uv

---

## File Map

- Create `src/dragon_radar/realtime/__init__.py`: package exports only.
- Create `src/dragon_radar/realtime/models.py`: strict input/read models and anomaly rule types.
- Create `src/dragon_radar/realtime/tables.py`: SQLAlchemy table metadata matching migration `0011`.
- Create `src/dragon_radar/realtime/anomalies.py`: deterministic pure anomaly calculations.
- Create `src/dragon_radar/realtime/repository.py`: immutable ingestion and point-in-time reads.
- Create `src/dragon_radar/realtime/ingestion.py`: batch validation and repository orchestration.
- Create `src/dragon_radar/http/routes/realtime.py`: EX04-07 read-only routes.
- Create `src/dragon_radar/migrations/versions/0011_realtime.py`: realtime schema only.
- Create `tests/test_reset_realtime.py`: RT01-12, repository, route, and migration tests.
- Create `web/src/features/realtime/types.ts`: wire contracts.
- Create `web/src/features/realtime/client.ts`: path builders and runtime validators.
- Create `web/src/features/realtime/RealtimePanel.tsx`: Overview panel.
- Create `web/tests/unit/reset-realtime.test.ts`: client and rendering-unit contracts.
- Create `web/tests/e2e/reset-realtime.spec.ts`: responsive and stale/disabled flows.
- Create `docs/acceptance/reset-realtime.md`: executed engineering evidence and production blockers.
- Modify `src/dragon_radar/http/app.py`: inject/mount the realtime repository/router.
- Modify `src/dragon_radar/storage/database.py`: set head to `0011_realtime`.
- Modify `src/dragon_radar/migrations/release_manifest.json`: append `0011` and update head/support.
- Modify `src/dragon_radar/storage/schema_contract.json`: regenerate PostgreSQL 17 contract.
- Modify `scripts/verify_release_migrations.py`: add the `0011` upgrade sentinel.
- Modify `tests/test_release_migrations.py`: assert the new chain, wheel inventory, and schema contract.
- Modify `web/src/pages/Overview.tsx`: lazy-load the realtime panel.
- Modify `.github/workflows/ci.yml`: run focused realtime backend/frontend/E2E checks.

## Task 1: Define Strict Realtime Models And Pure Rules

**Files:**
- Create: `src/dragon_radar/realtime/__init__.py`
- Create: `src/dragon_radar/realtime/models.py`
- Create: `src/dragon_radar/realtime/anomalies.py`
- Create: `tests/test_reset_realtime.py`

- [ ] **Step 1: Write failing RT01-04 and RT10 model/rule tests**

Add model tests for authorization, canonical UTC ordering, stable IDs, trading
sessions, finite Decimal values, and the hand-calculated return:

```python
def test_three_minute_return_triggers_at_two_percent():
    rule = AnomalyRule(
        rule_version="return-v1",
        metric="window_return",
        window=3,
        threshold=Decimal("0.02"),
        cooldown_seconds=300,
    )
    result = detect_anomalies(
        bars=minute_bars(["100", "101", "103"]),
        rule=rule,
    )
    assert result.value == Decimal("0.03")
    assert result.triggered is True
    assert result.reason is None


def test_insufficient_window_returns_reason_without_event():
    result = detect_anomalies(
        bars=minute_bars(["100", "103"]),
        rule=return_rule(window=3),
    )
    assert result.triggered is False
    assert result.value is None
    assert result.reason == "insufficient_window"
```

Add parameterized failures for naive datetimes, `available_at < received_at`,
NaN/Infinity, negative volume, empty source/security IDs, invalid revisions,
bars outside the declared trading sessions, and order-book rules without
order-book input.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```powershell
uv run pytest tests/test_reset_realtime.py -q -k "model or return or window or authorization or session"
```

Expected: collection fails because `dragon_radar.realtime` does not exist.

- [ ] **Step 3: Implement complete strict models**

Define these public types with `ConfigDict(extra="forbid", frozen=True)`:

```python
class PermissionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    permission_id: str
    state: Literal["authorized", "revoked", "unavailable"]
    captured_at: datetime


class MinuteBarInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    security_id: str
    event_at: datetime
    revision: int = Field(ge=1)
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None
    amount: Decimal | None
    final: bool


class AnomalyRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    rule_version: str
    metric: Literal["window_return", "relative_volume"]
    window: int = Field(ge=2, le=120)
    threshold: Decimal
    cooldown_seconds: int = Field(ge=0, le=86400)
```

Also define quote batch, minute batch, quote row, anomaly result/event,
sector-flow point, completeness, and import-result models. Validators must
canonicalize UTC and reject non-finite Decimal values without coercing missing
values to zero.

- [ ] **Step 4: Implement deterministic anomaly functions**

Expose:

```python
def detect_anomalies(
    bars: Sequence[MinuteBarInput],
    rule: AnomalyRule,
) -> AnomalyResult:
    ordered = tuple(sorted(bars, key=lambda bar: (bar.event_at, bar.revision)))
    if len(ordered) < rule.window:
        return AnomalyResult(
            triggered=False,
            metric=rule.metric,
            window=rule.window,
            value=None,
            threshold=rule.threshold,
            reason="insufficient_window",
        )
    window = ordered[-rule.window :]
    if rule.metric == "window_return":
        first = window[0].close
        if first == 0:
            return AnomalyResult(
                triggered=False,
                metric=rule.metric,
                window=rule.window,
                value=None,
                threshold=rule.threshold,
                reason="zero_denominator",
            )
        value = window[-1].close / first - Decimal(1)
    else:
        reference = window[0].volume
        current = window[-1].volume
        if reference in (None, Decimal(0)) or current is None:
            return AnomalyResult(
                triggered=False,
                metric=rule.metric,
                window=rule.window,
                value=None,
                threshold=rule.threshold,
                reason="volume_unavailable",
            )
        value = current / reference
    return AnomalyResult(
        triggered=value >= rule.threshold,
        metric=rule.metric,
        window=rule.window,
        value=value,
        threshold=rule.threshold,
        reason=None,
    )
```

The production implementation may extract small private helpers, but must retain
the exact result semantics.

- [ ] **Step 5: Verify Task 1**

Run:

```powershell
uv run pytest tests/test_reset_realtime.py -q -k "model or return or window or authorization or session"
uv run ruff check src/dragon_radar/realtime tests/test_reset_realtime.py
uv run ruff format --check src/dragon_radar/realtime tests/test_reset_realtime.py
```

Expected: all selected tests pass with zero Ruff findings.

- [ ] **Step 6: Commit Task 1**

```powershell
git add src/dragon_radar/realtime tests/test_reset_realtime.py
git commit -m "feat: define realtime evidence models"
```

## Task 2: Add Migration And Immutable Realtime Storage

**Files:**
- Create: `src/dragon_radar/realtime/tables.py`
- Create: `src/dragon_radar/migrations/versions/0011_realtime.py`
- Modify: `tests/test_reset_realtime.py`
- Modify: `src/dragon_radar/storage/database.py`
- Modify: `src/dragon_radar/migrations/release_manifest.json`
- Modify: `src/dragon_radar/storage/schema_contract.json`
- Modify: `scripts/verify_release_migrations.py`
- Modify: `tests/test_release_migrations.py`

- [ ] **Step 1: Write failing migration and immutability tests**

Add PostgreSQL tests that migrate from `0010_ai_tasks` to `0011_realtime` and
assert:

```python
EXPECTED_REALTIME_TABLES = {
    "realtime_quote_batches",
    "realtime_quotes",
    "realtime_minute_bars",
    "realtime_anomaly_events",
    "realtime_sector_flow_points",
    "realtime_import_attempts",
}


def test_0011_realtime_extends_0010_without_rewriting_history(database_url):
    at_revision(database_url, "0010_ai_tasks")
    assert current_revision(database_url) == "0010_ai_tasks"
    upgrade(database_url, "0011_realtime")
    assert current_revision(database_url) == "0011_realtime"
    assert EXPECTED_REALTIME_TABLES <= application_tables(database_url)
```

Add checks for exact unique identities, foreign keys, nonnegative values,
timestamp ordering, immutable-row triggers, and `NULL` preservation.

- [ ] **Step 2: Confirm RED**

Run:

```powershell
uv run pytest tests/test_reset_realtime.py -q -k "0011 or realtime_schema or immutable"
```

Expected: failure because revision `0011_realtime` does not exist.

- [ ] **Step 3: Implement `0011_realtime`**

Create a linear Alembic revision:

```python
revision = "0011_realtime"
down_revision = "0010_ai_tasks"
branch_labels = None
depends_on = None
```

Create all six tables from the design, explicit indexes for point-in-time
selection, and immutable triggers using the repository's existing trigger
function pattern. Every `CHECK` must be named. Do not implement downgrade as a
release rollback; `downgrade()` must raise
`RuntimeError("Realtime evidence history cannot be downgraded")`.

- [ ] **Step 4: Mirror migration metadata in `tables.py`**

Define SQLAlchemy `Table` objects with names and column types identical to the
migration. Do not call `metadata.create_all()`.

- [ ] **Step 5: Advance the release manifest and readiness atomically**

Append `0011_realtime.py` with canonical LF byte count, SHA-256, revision, and
parent. Change the manifest head to `0011_realtime`, append it to
`supported_from`, and leave every prior revision entry byte-identical.

Set `SCHEMA_REVISION = "0011_realtime"`. Against a fresh PostgreSQL 17 database
at the new head, regenerate:

```powershell
uv run python scripts/verify_release_migrations.py `
  --database-url $env:DRAGON_TEST_DATABASE_URL `
  --write-schema-contract src/dragon_radar/storage/schema_contract.json
```

Add an `0011_realtime` sentinel containing one batch, minute revision, and
anomaly event. Extend release migration tests to require 19 revisions, the new
wheel member, strict readiness, and drift detection for one realtime column,
constraint, FK, index, and trigger.

- [ ] **Step 6: Verify Task 2**

Run against PostgreSQL 17:

```powershell
uv run pytest tests/test_reset_realtime.py -q -k "0011 or realtime_schema or immutable"
uv run pytest tests/test_release_migrations.py tests/test_storage.py -q
uv run ruff check src/dragon_radar/realtime/tables.py src/dragon_radar/migrations/versions/0011_realtime.py tests/test_reset_realtime.py
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit Task 2**

```powershell
git add src/dragon_radar/realtime/tables.py src/dragon_radar/migrations/versions/0011_realtime.py tests/test_reset_realtime.py src/dragon_radar/storage/database.py src/dragon_radar/migrations/release_manifest.json src/dragon_radar/storage/schema_contract.json scripts/verify_release_migrations.py tests/test_release_migrations.py
git commit -m "feat: add realtime storage migration"
```

## Task 3: Implement Idempotent Ingestion And Point-In-Time Reads

**Files:**
- Create: `src/dragon_radar/realtime/repository.py`
- Create: `src/dragon_radar/realtime/ingestion.py`
- Modify: `tests/test_reset_realtime.py`

- [ ] **Step 1: Write failing RT05-09, RT11-12 repository tests**

Cover:

- same identity/same hash returns `duplicate`;
- same identity/different hash raises conflict;
- out-of-order batches remain queryable;
- later revisions do not rewrite earlier rows;
- missing minutes are absent;
- stream gaps return delayed/incomplete state with source timestamps;
- event identity and cooldown suppress duplicates only within the same rule/input
  identity;
- fixed-batch replay is deterministic;
- repository reads execute no insert/update/delete statements.

Use a SQLAlchemy event listener to fail the public read test on any write SQL.

- [ ] **Step 2: Confirm RED**

Run:

```powershell
uv run pytest tests/test_reset_realtime.py -q -k "idempotent or out_of_order or revision or gap or cooldown or replay or no_write"
```

Expected: failure because repository and ingestion modules do not exist.

- [ ] **Step 3: Implement transaction-safe ingestion**

`RealtimeRepository` exposes typed methods `ingest_minute_batch`,
`ingest_quote_batch`, `ingest_sector_flow_batch`, `list_quotes`,
`list_minutes`, `list_anomalies`, and `list_sector_flow`. Each method accepts
the corresponding strict model and returns its declared result model; raw
dictionaries are not part of the interface.

Implementation requirements:

- one transaction per validated batch;
- PostgreSQL advisory transaction lock keyed by source plus batch identity;
- strict canonical payload hash over every persisted field;
- select existing identity before insert;
- immutable append for revisions and events;
- import attempt result recorded in the same transaction;
- current permission rechecked for every read.

`ingestion.py` validates the complete model, calls the repository, and converts
storage conflicts into domain errors without accepting raw dictionaries.

- [ ] **Step 4: Implement point-in-time SQL**

Use window functions or `DISTINCT ON` with deterministic ordering to select the
latest visible minute revision:

```sql
SELECT DISTINCT ON (security_id, event_at) *
FROM realtime_minute_bars
WHERE security_id = :security_id
  AND event_at >= :session_start
  AND event_at < :session_end
  AND available_at <= :as_of
ORDER BY security_id, event_at, revision DESC, id DESC
```

The final query must remain parameterized and must not replace absent minutes.
Anomaly cursor payloads include version, filters, expiry, watermark, and last
`(available_at,event_id)` and reuse the signed cursor pattern from
`topics.repository`.

- [ ] **Step 5: Verify Task 3**

Run:

```powershell
uv run pytest tests/test_reset_realtime.py -q -k "idempotent or out_of_order or revision or gap or cooldown or replay or no_write or point_in_time or cursor"
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit Task 3**

```powershell
git add src/dragon_radar/realtime/repository.py src/dragon_radar/realtime/ingestion.py tests/test_reset_realtime.py
git commit -m "feat: ingest and read realtime evidence"
```

## Task 4: Add EX04-07 Read-Only Routes

**Files:**
- Create: `src/dragon_radar/http/routes/realtime.py`
- Modify: `src/dragon_radar/http/app.py`
- Modify: `tests/test_reset_realtime.py`

- [ ] **Step 1: Write failing HTTP contract tests**

Test exact query allowlists and bounds:

```python
def test_quotes_rejects_more_than_one_hundred_security_ids(client):
    response = client.get(
        "/api/realtime/quotes",
        params=[("security_ids", f"S{i:03d}") for i in range(101)],
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_PARAM"


def test_public_realtime_get_does_not_start_ingestion(client, fake_realtime):
    response = client.get(
        "/api/realtime/minutes",
        params={"security_id": "S1", "date": "2026-09-18"},
    )
    assert response.status_code == 200
    assert fake_realtime.ingestion_calls == 0
```

Also test exact batch/as-of pinning, date validation, cursor tampering,
`limit=1..100`, unavailable/null data, permission revocation, Decimal numeric
tokens, and EX07 refusing turnover proxies.

- [ ] **Step 2: Confirm RED**

Run:

```powershell
uv run pytest tests/test_reset_realtime.py -q -k "http or route or public or ex04 or ex05 or ex06 or ex07"
```

Expected: 404 because the router is not mounted.

- [ ] **Step 3: Implement dependency injection and router**

Extend `create_workbench_app` with:

```python
def create_workbench_app(
    database_url: str | None = None,
    *,
    repository: Repository | None = None,
    analysis_repository: AnalysisJobRepository | None = None,
    realtime_repository: RealtimeRepository | None = None,
) -> FastAPI:
```

When a database is configured and no realtime repository is injected, construct
`RealtimeRepository(database)`. Store it in `app.state.realtime_repository`.

The new router:

- accepts GET only;
- parses query values through existing strict helpers;
- calls one repository read method;
- uses `DecimalJSONResponse`;
- never imports ingestion or starts a task;
- returns structured 400/403/404 responses.

- [ ] **Step 4: Verify Task 4**

Run:

```powershell
uv run pytest tests/test_reset_realtime.py -q -k "http or route or public or ex04 or ex05 or ex06 or ex07"
uv run pytest tests/test_reset_public_gateway.py tests/test_api.py -q
```

Expected: all selected tests pass and existing public boundaries remain green.

- [ ] **Step 5: Commit Task 4**

```powershell
git add src/dragon_radar/http/routes/realtime.py src/dragon_radar/http/app.py tests/test_reset_realtime.py
git commit -m "feat: expose realtime read contracts"
```

## Task 5: Build The Overview Realtime Panel

**Files:**
- Create: `web/src/features/realtime/types.ts`
- Create: `web/src/features/realtime/client.ts`
- Create: `web/src/features/realtime/RealtimePanel.tsx`
- Create: `web/tests/unit/reset-realtime.test.ts`
- Create: `web/tests/e2e/reset-realtime.spec.ts`
- Modify: `web/src/pages/Overview.tsx`

- [ ] **Step 1: Write failing TypeScript contract tests**

Test runtime validators rather than type assertions:

```typescript
it('rejects minute responses that substitute a newer batch', () => {
  expect(() => validateMinuteResponse(responseFixture({
    requested_batch_id: 'batch-1',
    quote_batch_id: 'batch-2',
  }), minuteSelection)).toThrow(/batch/i);
});

it('preserves null flow separately from zero', () => {
  const unavailable = validateSectorFlow(flowFixture({ net_inflow: null, unavailable_reason: 'field_not_supplied' }));
  const zero = validateSectorFlow(flowFixture({ net_inflow: 0, unavailable_reason: null }));
  expect(unavailable.points[0].net_inflow).toBeNull();
  expect(zero.points[0].net_inflow).toBe(0);
});
```

Cover exact key sets, Decimal-safe wire values, timestamps, delayed status,
missing minutes, unsupported flow, cursor, and source identity.

- [ ] **Step 2: Confirm RED**

Run:

```powershell
npm test -- --run tests/unit/reset-realtime.test.ts
```

Expected: failure because realtime frontend modules do not exist.

- [ ] **Step 3: Implement path builders and validators**

Expose pure builders:

```typescript
export function minutePath(selection: MinuteSelection): string {
  return `/api/realtime/minutes?${new URLSearchParams({
    security_id: selection.securityId,
    date: selection.date,
    ...(selection.asOf ? { as_of: selection.asOf } : {}),
    ...(selection.batchId ? { quote_batch_id: selection.batchId } : {}),
  })}`;
}
```

Validators reject extra/missing keys, mismatched selection, invalid dates,
non-finite numeric values, and missing reasons for unavailable values.

- [ ] **Step 4: Implement `RealtimePanel`**

The panel:

- uses `useResource` only when a date and selected securities/sector exist;
- displays source event time, received time, and delayed/unavailable status;
- renders absent minutes as gaps;
- shows anomaly rule, value, threshold, batch, and quality;
- omits order-book and flow charts when unavailable;
- supports retry, empty, revoked, partial, and stream-gap states;
- does not call any POST endpoint.

Lazy-load it in Overview under the reset feature flag. Keep sections unframed
and use existing tables/charts rather than nested cards.

- [ ] **Step 5: Add Playwright coverage**

Mock only public GET responses. Verify:

- 375 and 1440 widths;
- equivalent 200% zoom;
- keyboard focus and retry;
- original source time shown when delayed;
- no interpolated row for a missing minute;
- unavailable flow explains the reason;
- no request is made when the panel is disabled.

- [ ] **Step 6: Verify Task 5**

Run:

```powershell
Set-Location web
npm test -- --run tests/unit/reset-realtime.test.ts
npx playwright test tests/e2e/reset-realtime.spec.ts
npm run build
npm run check:bundle
```

Expected: all commands pass.

- [ ] **Step 7: Commit Task 5**

```powershell
git add web/src/features/realtime web/src/pages/Overview.tsx web/tests/unit/reset-realtime.test.ts web/tests/e2e/reset-realtime.spec.ts
git commit -m "feat: add realtime research panel"
```

## Task 6: Verify `0011_realtime` Release Contracts And Evidence

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `docs/acceptance/reset-realtime.md`

- [ ] **Step 1: Extend CI verification**

Update CI focused
commands so the PostgreSQL 17 job runs `tests/test_reset_realtime.py` and the web
job runs the realtime unit/E2E test without removing any existing gate.

- [ ] **Step 2: Generate Task 21R acceptance evidence**

Write `docs/acceptance/reset-realtime.md` with:

- exact implementation commit;
- RT01-12 results;
- migration/wheel/schema results;
- frontend results;
- synthetic fixture declaration;
- missing licensed minute/order-book/flow inputs;
- explicit production `NO_GO`.

- [ ] **Step 3: Run Task 21R full verification**

Run:

```powershell
uv run pytest tests/test_reset_realtime.py tests/test_release_migrations.py tests/test_storage.py -q
uv run pytest -q -W error
uv run ruff check .
uv run ruff format --check src tests scripts
uv run python -m compileall -q src tests scripts
Set-Location web
npm test -- --run
npm run build
npm run check:bundle
npx playwright test tests/e2e/reset-realtime.spec.ts
```

Run the installed-wheel migration verifier against a labeled disposable
PostgreSQL 17 container and retain temporary evidence under `.temp/`.

- [ ] **Step 4: Commit CI integration and evidence**

```powershell
git add .github/workflows/ci.yml docs/acceptance/reset-realtime.md
git commit -m "chore: verify realtime release contracts"
```

## Task 7: Independent Task 21R Reviews

**Files:**
- Modify only files required by confirmed findings.

- [ ] **Step 1: Perform specification review**

Compare the Task 21R commit range against
`docs/superpowers/specs/2026-10-09-task21r-task22-design.md`, RT01-12, and
EX04-07. Verify no production/license claim and no public write side effect.

- [ ] **Step 2: Fix every specification issue**

Use TDD for each confirmed issue, commit fixes, and rerun affected backend,
frontend, migration, and wheel gates.

- [ ] **Step 3: Perform code quality review**

Review Decimal handling, timestamp ordering, idempotency races, cursor binding,
SQL indexes, permission rechecks, route bounds, frontend selection validation,
failure cleanup, and CI coverage.

- [ ] **Step 4: Fix every Critical or Important issue**

Add a regression test before each fix. Repeat review until approved.

- [ ] **Step 5: Record Task 21R completion**

Report implementation/evidence commits, exact test totals, migration head,
unresolved production blockers, and confirmation that Task 22 may now begin.
Do not merge, tag, publish, or deploy.
