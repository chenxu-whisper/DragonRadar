# Role and Topic Evidence Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver immutable topic snapshots, deterministic materialized role evidence, public read APIs, owner-only review, and the “龙头梯队” interface defined by the approved fourth-batch specification.

**Architecture:** Topic data is imported and published independently from market data. A role materialization transaction binds one market snapshot, one topic snapshot, one frozen rule definition, and one predecessor activation event, then persists immutable factors, topic-role statuses, role results, limit-ladder rows, and change rows. Verification and activation are separate append-only operations; public reads are gated by activation and both source grants.

**Tech Stack:** Python 3.12, Pydantic 2, SQLAlchemy 2, PostgreSQL 17, Alembic, FastAPI, pytest, React 19, TypeScript 5.9, Vite 8, Vitest, Playwright.

**Authoritative spec:** `docs/superpowers/specs/2026-09-15-role-topic-evidence-design.md`

---

## Execution Preconditions

- Create `.worktrees/role-topic-evidence` on branch `feature/role-topic-evidence` from the approved specification commit.
- Do not copy the dirty backend or test changes currently present in `.worktrees/private-review`.
- Run every red test before implementation and record the expected failure.
- Keep each task in its own commit. Do not push, merge, install certificates, or modify existing data volumes without explicit approval.

### Task 1: Topic Domain Contracts and Test Fixtures

**Files:**
- Create: `src/dragon_radar/topics/__init__.py`
- Create: `src/dragon_radar/topics/models.py`
- Create: `src/dragon_radar/topics/quality.py`
- Create: `tests/topic_factories.py`
- Create: `tests/test_topic_models.py`
- Modify: `src/dragon_radar/domain/models.py`
- Modify: `tests/factories.py`

- [ ] **Step 1: Write failing topic contract tests**

Cover frozen models, strict unknown-field rejection, mandatory aware `available_at`,
non-overlapping effective intervals, calendar coverage, exact `coverage_cells`
with `complete_as_of`, and continuous page manifests.

```python
def test_topic_batch_requires_complete_coverage_at_a_known_cutoff():
    payload = topic_batch_dict()
    payload["coverage_cells"][0]["complete_as_of"] = None
    with pytest.raises(ValidationError):
        TopicBatch.model_validate(payload)


def test_topic_batch_rejects_overlapping_membership_intervals():
    payload = topic_batch_dict()
    payload["memberships"].append(dict(payload["memberships"][0]))
    with pytest.raises(ValidationError):
        TopicBatch.model_validate(payload)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
uv run pytest tests/test_topic_models.py -q
```

Expected: collection/import failure because `dragon_radar.topics.models` does not exist.

- [ ] **Step 3: Implement the immutable models**

Define these exact public types:

```python
class TopicCoverageCell(FrozenModel):
    topic_id: Identifier
    trade_date: date
    expected_member_count: Annotated[int, Field(ge=0)]
    received_member_count: Annotated[int, Field(ge=0)]
    complete_as_of: UTCDateTime
    status: Literal["complete", "unavailable"]
    reason: str | None = None


class TopicBatch(FrozenModel):
    trade_date: date
    as_of: UTCDateTime
    source_time: UTCDateTime | None = None
    provenance: Provenance
    source_id: Identifier
    source_version: Identifier
    grant: SourceGrant
    exchange: Identifier
    calendar: ExchangeCalendar
    calendar_version: Identifier
    taxonomy_version: Identifier
    membership_version: Identifier
    coverage_from: date
    coverage_to: date
    coverage_cells: tuple[TopicCoverageCell, ...]
    topics: tuple[TopicDefinition, ...]
    memberships: tuple[TopicMembership, ...]
    page_manifest: PageManifest


class TopicSnapshotPage(FrozenModel):
    items: tuple[TopicSnapshot, ...]
    page_size: int
    next_cursor: str | None
```

Extend `SecurityState` with nullable `security_type`, `board_category`, and
`is_delisting`. Preserve old archive parsing by defaulting all three to `None`.
Also define `PageManifest`, `TopicDefinition`, `TopicMembership`,
`TopicSnapshot`, `TopicImportRecord`, and `TopicAttempt` in the same module so
later repository signatures use concrete types.

- [ ] **Step 4: Implement quality validation**

Add `validate_topic_batch(batch, now)` returning the existing `QualityReport`
shape. Reject page-chain gaps, overlapping memberships, missing coverage cells,
count mismatches, future availability, calendar mismatch, and invalid references.

- [ ] **Step 5: Run focused and domain tests**

```powershell
uv run pytest tests/test_topic_models.py tests/test_domain.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
git add src/dragon_radar/topics src/dragon_radar/domain/models.py tests/topic_factories.py tests/test_topic_models.py tests/factories.py
git commit -m "feat: define immutable topic batch contracts"
```

### Task 2: Topic Persistence and Migration

**Files:**
- Create: `src/dragon_radar/topics/tables.py`
- Create: `src/dragon_radar/topics/repository.py`
- Create: `src/dragon_radar/migrations/versions/0004_topic_snapshots.py`
- Create: `tests/test_topic_repository.py`
- Modify: `src/dragon_radar/storage/database.py`

- [ ] **Step 1: Write failing repository tests**

Test same-key replay, same-key/different-payload conflict, quality rejection,
revision chains, source revocation races, immutable UPDATE/DELETE/TRUNCATE, and
failed attempt persistence.

```python
def test_topic_publish_is_immutable_and_revisioned(topic_repository, topic_batch):
    first = topic_repository.publish(
        topic_repository.import_batch(topic_batch, key="topic-v1").id
    )
    revised = topic_batch.model_copy(update={"membership_version": "members-v2"})
    second = topic_repository.publish(
        topic_repository.import_batch(revised, key="topic-v2").id
    )
    assert second.revision == 2
    assert second.supersedes == first.id
```

- [ ] **Step 2: Run the repository test and verify RED**

```powershell
uv run pytest tests/test_topic_repository.py -q
```

- [ ] **Step 3: Add migration `0004_topic_snapshots`**

Create `topic_import_attempts`, `topic_import_batches`, and `topic_snapshots`.
Use UUID primary keys, `(source_id, trade_date, revision)` uniqueness, restrictive
foreign keys, immutable statement-level triggers, and indexes matching the
specification’s list order.

- [ ] **Step 4: Implement `TopicRepository`**

Expose `TopicRepository.import_batch`, `publish`, `get_snapshot`,
`list_snapshots`, `list_attempts`, and `get_attempt`. `list_snapshots` returns
the `TopicSnapshotPage` contract defined in Task 1 and accepts only
`trade_date`, `source_id`, `page_size`, and `cursor`.

Reuse the current source lock and current-plus-embedded grant checks.

- [ ] **Step 5: Verify migration and repository**

```powershell
uv run pytest tests/test_topic_repository.py tests/test_storage.py -q
```

- [ ] **Step 6: Commit**

```powershell
git add src/dragon_radar/topics src/dragon_radar/migrations/versions/0004_topic_snapshots.py tests/test_topic_repository.py
git commit -m "feat: persist immutable topic snapshots"
```

### Task 3: Topic Operator CLI

**Files:**
- Modify: `src/dragon_radar/cli.py`
- Create: `tests/test_topic_cli.py`
- Create: `examples/synthetic-topic-batch.json`

- [ ] **Step 1: Write failing CLI tests**

Cover `topic-import`, `topic-publish`, `topic-attempts`, `topic-attempt-show`, and
`topic-retry`. Assert that output contains IDs and status only, never source
credentials or full evidence text.

- [ ] **Step 2: Run and verify RED**

```powershell
uv run pytest tests/test_topic_cli.py -q
```

- [ ] **Step 3: Add CLI commands**

Register exact commands:

```text
topic-import PATH --key KEY
topic-publish BATCH_ID
topic-attempts [--status STATUS]
topic-attempt-show ATTEMPT_ID
topic-retry ATTEMPT_ID --path PATH --key KEY
```

`topic-retry` must revalidate the complete manifest and must never publish a
partial batch.

- [ ] **Step 4: Run CLI and contract tests**

```powershell
uv run pytest tests/test_topic_cli.py tests/test_cli.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add src/dragon_radar/cli.py tests/test_topic_cli.py examples/synthetic-topic-batch.json
git commit -m "feat: add topic snapshot operator commands"
```

### Task 4: Role Formula Primitives

**Files:**
- Create: `src/dragon_radar/roles/__init__.py`
- Create: `src/dragon_radar/roles/models.py`
- Create: `src/dragon_radar/roles/factors.py`
- Create: `src/dragon_radar/roles/rules.py`
- Create: `tests/test_role_factors.py`

- [ ] **Step 1: Write failing Decimal formula tests**

Cover adjusted returns, relative returns, odd/even medians, maximum drawdown,
daily topic amount share, average share, mean absolute deviation rate, zero
denominators, unit mismatch, and `knowledge_cutoff`.

```python
def test_even_decimal_median_is_exact():
    assert decimal_median((Decimal("1"), Decimal("3"))) == Decimal("2")


def test_topic_share_rejects_zero_denominator():
    with pytest.raises(AnalysisUnavailable):
        topic_amount_share(Decimal("0"), (Decimal("0"), Decimal("0")))
```

- [ ] **Step 2: Run and verify RED**

```powershell
uv run pytest tests/test_role_factors.py -q
```

- [ ] **Step 3: Implement formula primitives**

Implement `adjusted_price`, `period_return`, `decimal_median`,
`relative_return`, `topic_amount_share`, and `mean_absolute_deviation_rate`.
Each function uses the existing Decimal34 half-even context, validates finite
positive divisors, and returns the exact frozen-model value required by the
specification’s public formula table.

- [ ] **Step 4: Freeze rule definitions**

Define `ROLE_RULE_VERSION = "role-v1"` and `ROLE_RULE_SHA256`. Add immutable
definitions for production boards and `role-test-v1`; test that changing the
canonical JSON without changing its version fails startup validation.
Define `RoleContext`, `RoleCandidate`, `RankedCandidate`, `RoleEvaluation`,
`RoleTopicStatus`, and the candidate/status literals in `roles/models.py`.

- [ ] **Step 5: Run tests and commit**

```powershell
uv run pytest tests/test_role_factors.py -q
git add src/dragon_radar/roles tests/test_role_factors.py
git commit -m "feat: add deterministic role factor primitives"
```

### Task 5: Formal Role Evaluators

**Files:**
- Create: `src/dragon_radar/roles/engine.py`
- Create: `tests/test_role_engine_formal.py`
- Modify: `src/dragon_radar/roles/models.py`
- Modify: `src/dragon_radar/roles/rules.py`

- [ ] **Step 1: Write failing trend tests**

Cover 61-session eligibility, market median benchmark, positive RS20/RS60,
topic-median drawdown gate, exact sort tuple, dense ties, missing state, and
cross-exchange rejection.

- [ ] **Step 2: Write failing core tests**

Cover `T-4..T` and `T-19..T`, daily membership denominators, full topic amount
coverage, median gates, zero mean, currency mismatch, and MAD ordering.

- [ ] **Step 3: Run and verify RED**

```powershell
uv run pytest tests/test_role_engine_formal.py -q
```

- [ ] **Step 4: Implement formal evaluators**

Implement `evaluate_trend(context, topic_id)`, `evaluate_core(context, topic_id)`,
and `dense_rank(rows, key)`. Every topic-role call returns `RoleTopicStatus`
even when no result rows exist; ranking compares unrounded Decimal tuples and
uses dense rank.

- [ ] **Step 5: Verify and commit**

```powershell
uv run pytest tests/test_role_engine_formal.py tests/test_role_factors.py -q
git add src/dragon_radar/roles tests/test_role_engine_formal.py
git commit -m "feat: compute formal trend and core roles"
```

### Task 6: Simplified, Observational, and Historical Role Outputs

**Files:**
- Modify: `src/dragon_radar/roles/engine.py`
- Create: `tests/test_role_engine_staged.py`

- [ ] **Step 1: Write failing staged-role tests**

Create these concrete cases:

```python
@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("unknown_limit", "insufficient"),
        ("disjoint_catchup_windows", "simplified"),
        ("tied_trend_core", "insufficient"),
        ("matching_predecessor_event", "pending_validation"),
        ("incomparable_predecessor", "insufficient"),
    ],
)
def test_staged_role_statuses(role_scenario, scenario, expected):
    assert role_scenario(scenario).status == expected
```

- [ ] **Step 2: Run and verify RED**

```powershell
uv run pytest tests/test_role_engine_staged.py -q
```

- [ ] **Step 3: Implement four evaluators**

Implement `evaluate_emotion`, `evaluate_catchup`, `evaluate_follower`, and
`evaluate_relay`. Each accepts a `RoleContext` and topic ID; relay additionally
accepts the immutable predecessor run selected by its activation event. Use only
the exact formulas and inequalities in the specification.

- [ ] **Step 4: Verify and commit**

```powershell
uv run pytest tests/test_role_engine_staged.py tests/test_role_engine_formal.py -q
git add src/dragon_radar/roles tests/test_role_engine_staged.py
git commit -m "feat: add staged role evidence outputs"
```

### Task 7: Limit Ladder, Role Changes, and Relay Validation

**Files:**
- Create: `src/dragon_radar/roles/history.py`
- Create: `tests/test_role_history.py`
- Modify: `src/dragon_radar/roles/models.py`

- [ ] **Step 1: Write failing history tests**

Test 10-day hit count, exact closing streak, suspension/no-limit interruption,
unknown-limit propagation, touched-not-sealed, unchanged/add/exit/tie/member
changes, and non-comparable runs.

- [ ] **Step 2: Write failing relay validation tests**

Test T+5 maturity, supported/weakened/refuted/unavailable, duplicate execution,
future inputs not changing old validation, and authorization revocation.

- [ ] **Step 3: Run and verify RED**

```powershell
uv run pytest tests/test_role_history.py -q
```

- [ ] **Step 4: Implement history and validation**

Implement `build_limit_ladder(context, topic_id)`, `compare_runs(current,
previous)`, and `validate_relay(pending, future_runs)`. Return immutable
`LimitRow`, `RoleChange`, and `RoleValidation` tuples defined in
`roles/models.py`.

- [ ] **Step 5: Verify and commit**

```powershell
uv run pytest tests/test_role_history.py -q
git add src/dragon_radar/roles tests/test_role_history.py
git commit -m "feat: materialize role history evidence"
```

### Task 8: Role Persistence, Materialization, and Idempotency

**Files:**
- Create: `src/dragon_radar/roles/tables.py`
- Create: `src/dragon_radar/roles/repository.py`
- Create: `src/dragon_radar/roles/service.py`
- Create: `tests/test_role_repository.py`
- Create: `src/dragon_radar/migrations/versions/0005_role_evidence.py`

- [ ] **Step 1: Write failing transaction tests**

Cover all new tables, restrictive foreign keys, immutable triggers, topic-role
status rows with zero results, same-key replay, same-key conflict, different-key
same-combination convergence, sorted source locks, predecessor activation
identity, relay-validation command uniqueness, rollback on partial insert, and
startup rule-hash mismatch.

- [ ] **Step 2: Run and verify RED**

```powershell
uv run pytest tests/test_role_repository.py -q
```

- [ ] **Step 3: Implement tables and repository**

Implement `RoleRepository.materialize`, `get_run`, `list_topic_statuses`, and
`list_results` with the exact parameters from specification sections 7 and 9.
Persist rule status rows, factors, results, ladder, and changes in one
transaction.

- [ ] **Step 4: Verify and commit**

```powershell
uv run pytest tests/test_role_repository.py tests/test_topic_repository.py -q
git add src/dragon_radar/roles src/dragon_radar/migrations/versions/0005_role_evidence.py tests/test_role_repository.py
git commit -m "feat: persist materialized role runs"
```

### Task 9: Verification, Activation, Control, and Retention

**Files:**
- Create: `src/dragon_radar/roles/control.py`
- Create: `tests/test_role_control.py`
- Modify: `src/dragon_radar/cli.py`
- Modify: `src/dragon_radar/roles/tables.py`
- Modify: `src/dragon_radar/roles/repository.py`

- [ ] **Step 1: Write failing control tests**

Cover deterministic SHA-256 sample selection, exact verification, activation
preconditions, stale `if-current`, module disable, rollback, role recheck,
attempt inspection, retry, `role-validate-pending` T+5 validation, duplicate
validation commands, and all authorization races.

- [ ] **Step 2: Write failing retention tests**

Verify privileged purge order, source-derived value removal, role/run tombstones,
owner review preservation, command redaction, activation-event preservation, and
restore remaining disabled until authorization/deletion-ledger replay completes.

- [ ] **Step 3: Run and verify RED**

```powershell
uv run pytest tests/test_role_control.py -q
```

- [ ] **Step 4: Implement operator commands**

Implement the exact CLI commands from specification sections 7 and 8.
Activation and recheck use the common sorted source lock helper and current-plus-
embedded authorization checks.

- [ ] **Step 5: Verify and commit**

```powershell
uv run pytest tests/test_role_control.py tests/test_role_repository.py tests/test_cli.py -q
git add src/dragon_radar/roles src/dragon_radar/cli.py tests/test_role_control.py
git commit -m "feat: verify and control role publications"
```

### Task 10: Public Topic and Role APIs

**Files:**
- Create: `src/dragon_radar/roles/routes.py`
- Create: `src/dragon_radar/readmodels/topics.py`
- Create: `src/dragon_radar/readmodels/roles.py`
- Create: `tests/test_role_api.py`
- Modify: `src/dragon_radar/api.py`
- Modify: `src/dragon_radar/owner/app.py`

- [ ] **Step 1: Write failing API contract tests**

Cover each public endpoint, exact envelopes, parent-run activation gates, separate
market/topic times, cursor high-watermarks, all allowed sorts, malformed
identifiers, module disabled metadata, separate source revocation, namespaced URL
pagination, and direct child-ID bypass attempts.

- [ ] **Step 2: Run and verify RED**

```powershell
uv run pytest tests/test_role_api.py -q
```

- [ ] **Step 3: Implement read models and routes**

Install the same explicit `/api/v1/` role routes in read and owner applications.
Return `{code, message, domain, retryable, current}` errors and never expose
private review content through public handlers.

- [ ] **Step 4: Verify and commit**

```powershell
uv run pytest tests/test_role_api.py tests/test_api.py tests/test_owner_api.py -q
git add src/dragon_radar/api.py src/dragon_radar/owner/app.py src/dragon_radar/roles/routes.py src/dragon_radar/readmodels tests/test_role_api.py
git commit -m "feat: expose topic and role evidence APIs"
```

### Task 11: Owner Role Review API

**Files:**
- Create: `src/dragon_radar/roles/reviews.py`
- Create: `tests/test_role_review_api.py`
- Modify: `src/dragon_radar/roles/routes.py`
- Modify: `src/dragon_radar/owner/app.py`

- [ ] **Step 1: Write failing review workflow tests**

Cover `If-Match: "0"`, version increments, idempotent response loss, same-key
conflict, stale version 409, no cross-owner access, source-display revocation
returning only owner review/tombstone fields, and purge retention behavior.

- [ ] **Step 2: Run and verify RED**

```powershell
uv run pytest tests/test_role_review_api.py -q
```

- [ ] **Step 3: Implement review commands and routes**

Define:

```python
class RoleReviewInput(FrozenModel):
    status: Literal["confirmed", "rejected", "needs_evidence"]
    reason: StrictStr
    supersedes: UUID | None = None
```

Reuse the owner gate, CSRF, `Idempotency-Key`, `If-Match`, command ledger, and
private audit patterns from `review/routes.py`.

- [ ] **Step 4: Verify and commit**

```powershell
uv run pytest tests/test_role_review_api.py tests/test_review_api.py -q
git add src/dragon_radar/roles/reviews.py src/dragon_radar/roles/routes.py src/dragon_radar/owner/app.py tests/test_role_review_api.py
git commit -m "feat: add owner role evidence review"
```

### Task 12: Frontend State, Validation, and API Boundary

**Files:**
- Create: `web/src/features/leaders/types.ts`
- Create: `web/src/features/leaders/api.ts`
- Create: `web/tests/unit/leaders-state.test.ts`
- Create: `web/tests/unit/leaders-api.test.ts`
- Modify: `web/src/state.ts`
- Modify: `web/src/api.ts`
- Modify: `web/src/hooks/useLocation.ts`

- [ ] **Step 1: Write failing URL tests**

Cover only-`role_run`, only-`snapshot`, exact mismatch, invalid IDs, visibility
default, one-time canonicalization, namespaced cursors, and snapshot-change cleanup.

- [ ] **Step 2: Write failing response-validation tests**

Reject response mismatches across run, snapshot, topic, role, security, provenance,
and activation state. Verify stable domain-specific 403 handling.

- [ ] **Step 3: Run and verify RED**

```powershell
npm test -- --run tests/unit/leaders-state.test.ts tests/unit/leaders-api.test.ts
```

- [ ] **Step 4: Implement state and API contracts**

Extend `ResearchState` with:

```ts
view: 'overview' | 'analysis' | 'stock' | 'leaders' | 'review';
role_run: string;
visibility_mode: 'contemporaneous' | 'retrospective';
topic: string;
role: RoleKind | '';
role_result: string;
topic_cursor: string;
result_cursor: string;
ladder_cursor: string;
change_cursor: string;
review_cursor: string;
```

Implement namespaced size/sort/order fields exactly as specified.

- [ ] **Step 5: Verify and commit**

```powershell
npm test -- --run tests/unit/leaders-state.test.ts tests/unit/leaders-api.test.ts tests/unit/state.test.ts
git add web/src/state.ts web/src/api.ts web/src/hooks/useLocation.ts web/src/features/leaders web/tests/unit
git commit -m "feat: add leader evidence client contracts"
```

### Task 13: Read-Only Leaders Workspace

**Files:**
- Create: `web/src/features/leaders/LeadersPage.tsx`
- Create: `web/src/features/leaders/TopicList.tsx`
- Create: `web/src/features/leaders/RoleMatrix.tsx`
- Create: `web/src/features/leaders/LimitLadder.tsx`
- Create: `web/src/features/leaders/RoleChanges.tsx`
- Create: `web/src/features/leaders/EvidenceDrawer.tsx`
- Create: `web/src/features/leaders/leaders.css`
- Create: `web/tests/e2e/leaders.spec.ts`
- Modify: `web/src/App.tsx`
- Modify: `web/src/pages/SecurityDetail.tsx`

- [ ] **Step 1: Write failing read-only E2E tests**

Test the complete path:

```text
select snapshot → open 龙头梯队 → select topic → select role
→ open evidence → close with Esc → focus returns to trigger
```

Also cover no topic snapshot, no run, no candidate, insufficient, retrospective,
limit ladder, role changes, mismatch discard, separate revocation domains,
375/768/1024/1440 widths, 200% scale, grayscale, keyboard, and axe.

- [ ] **Step 2: Run and verify RED**

```powershell
npx playwright test tests/e2e/leaders.spec.ts
```

- [ ] **Step 3: Implement the workspace**

Use the accepted dense three-region layout. Keep sections unframed, tables inside
their own overflow containers, one global modal drawer, visible source times, and
text labels for every status.

- [ ] **Step 4: Verify and commit**

```powershell
npx playwright test tests/e2e/leaders.spec.ts
npm test
npm run build
npm run check:bundle
git add web/src/App.tsx web/src/pages/SecurityDetail.tsx web/src/features/leaders web/tests/e2e/leaders.spec.ts
git commit -m "feat: add read-only leader evidence workspace"
```

### Task 14: Private Review UI and Navigation Races

**Files:**
- Create: `web/src/features/leaders/RoleReview.tsx`
- Create: `web/tests/private/leaders-review.spec.ts`
- Modify: `web/src/features/review/session.ts`
- Modify: `web/src/features/review/SessionProvider.tsx`
- Modify: `web/src/features/review/useCommand.ts`
- Modify: `web/src/features/leaders/EvidenceDrawer.tsx`

- [ ] **Step 1: Write failing private browser tests**

Cover first review, correction, response loss retry, version conflict, dirty-review
leave cancellation, source revocation during POST, late success after navigation,
session lock/recovery, and purge tombstone review history.

- [ ] **Step 2: Run and verify RED**

```powershell
npx playwright test --config playwright.private.config.ts tests/private/leaders-review.spec.ts
```

- [ ] **Step 3: Implement owner review UI**

Store drafts by `owner + role_result_id`, use the existing `useCommand` context
isolation, and clear only successfully committed drafts. Do not include owner
review code in the default static dependency graph.

- [ ] **Step 4: Verify and commit**

```powershell
npx playwright test --config playwright.private.config.ts tests/private/leaders-review.spec.ts
npm test
VITE_PRIVATE_REVIEW=1 npm run build
git add web/src/features/leaders web/src/features/review web/tests/private/leaders-review.spec.ts
git commit -m "feat: add owner role evidence review UI"
```

### Task 15: Integration, Documentation, and Final Acceptance

**Files:**
- Create: `docs/role-topic-evidence.md`
- Create: `docs/verification-role-topic-evidence.md`
- Modify: `README.md`
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/superpowers/plans/2026-09-16-role-topic-evidence.md`
- Test: `tests/test_role_end_to_end.py`
- Test: `web/tests/e2e/leaders.spec.ts`
- Test: `web/tests/private/leaders-review.spec.ts`

- [ ] **Step 1: Add cross-layer acceptance fixtures**

Use only `SYNTH-*` securities and an isolated database. Include complete,
incomplete, retrospective, revised-topic, same-day-rule-change, cross-exchange,
revoked-source, no-candidate, exact-tie, and T+5 relay-validation scenarios.

- [ ] **Step 2: Run backend quality gates**

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest -q -W error --cov=dragon_radar --cov-report=term-missing --cov-report=xml:.temp/coverage-role-topic.xml
uv run python -m compileall -q src tests
uv build --out-dir .temp/dist-role-topic
```

Expected: zero lint/format errors, all tests pass, coverage report generated,
compileall and package build exit 0.

- [ ] **Step 3: Run frontend quality gates**

```powershell
cd web
npm test
npm run build
npm run check:bundle
$env:VITE_PRIVATE_REVIEW='1'; npm run build
npm run test:e2e
npx playwright test --config playwright.private.config.ts
```

Expected: all unit/E2E tests and both builds pass; default bundle contains no
owner review module.

- [ ] **Step 4: Run container and real-browser acceptance**

Build the isolated fourth-batch image, migrate an isolated database, import the
synthetic market and topic batches, materialize, verify, activate, and test:

```text
select date → open leaders → select topic/role → inspect evidence
→ review result → create conflict → revoke one source → restore
→ restart containers → verify immutable readback
```

Delete the isolated acceptance database and pairing files afterward. Reconfirm
the normal database contains no synthetic topic or role records.

- [ ] **Step 5: Run two-stage final review**

Perform independent specification compliance review, then code-quality review.
Fix every reproducible P0/P1/P2 and rerun the affected and full gates.

- [ ] **Step 6: Update Chinese documentation and commit**

Record exact test counts, image IDs, migration version, limitations, CA state,
and normal-database counts without recording tokens, pairing codes, or review text.

```powershell
git add README.md docs .github/workflows/ci.yml tests/test_role_end_to_end.py web/tests
git commit -m "docs: verify role and topic evidence delivery"
```
