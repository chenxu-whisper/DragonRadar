# B4b Q01 Six Clocks Implementation Plan

> **For agentic workers:** Execute inline with the executing-plans skill. User-approved unified design sections 4 and 12 supersede its stale status. No commits, staging, migrations against daily DB, shared wiring, or edits outside ROOT.

**Goal:** Archive honest immutable clock metadata and serve/display six clock types across exactly five daily domains and explicitly selected RT/LHB resources.

**Architecture:** A strict pure clock contract owns aggregation and causal delays. Publication-local storage owns append-only verified input evidence and per-domain archives; the service samples completion immediately after each materializer, then validates the actual PUB timestamp. Dedicated read-only routes reuse current and archived authorization without latest fallback. New isolated UI modules validate exact responses and guard browser acceptance time by selection/generation.

**Tech Stack:** Python 3.12+, Pydantic 2, SQLAlchemy/PostgreSQL, FastAPI, React/TypeScript, pytest, Vitest.

## Ownership And Interfaces

- Create `src/dragon_radar/domain/clocks.py`: `ClockCell`, `InputClockReference`, `InputClockEvidence`, `DomainClockArchive`, `aggregate_clock`, `scalar_clock`, `clock_delays`, UTC validation. Six names only; five daily domains only. All models frozen/extra-forbid.
- Create `src/dragon_radar/publication/clocks.py`: `verify_input_evidence(evidence, object_bytes, evidence_bytes, verifier)` binds independently trusted private evidence to exact object/source/revision/hash/timestamps. `InputClockRepository.append/get` is append-only and exact-key. `archive_domain` resolves actual dependencies/cutoffs and validates completion. `publication_lineage` reads only archived blocks, supports honest legacy unknown and proven news revision clocks, sanitizes public output.
- Modify `publication/models.py`: nullable typed `clock_lineage` on both materialization/result, without changing daily payload contracts.
- Modify `publication/tables.py`: new nullable `publication_results.clock_lineage`; local `input_clock_evidence` declaration. No changes to source dependency contracts.
- Modify `publication/service.py`: completion capture after each actual ready materializer; context-specific cutoff; resolve every existing dependency, including role historical dependencies; validate nondecreasing completion/PUB times; insert clock block atomically. Missing/disabled compute remains null.
- Modify `publication/repository.py`: decode clock archive in results; transactional `get_lineage(publication_id, trade_date)` reuses `_authorized_records` without unvalidated cache/latest/legacy snapshot fallback.
- Create `migrations/versions/0016_clock_lineage.py`: actual predecessor `0015_daily_ai`, append-only evidence triggers using existing immutable function, nullable archive column; never edit earlier migration. Parent owns env/schema/release wiring.
- Create `http/routes/lineage.py`: strict required date+PUB query, fixed 400/403/404/503 semantics; export daily router.
- Minimal additions to `realtime/repository.py`, `lhb/repository.py` and their existing routes: `get_lineage` exact required identity, all parent/child PIT cutoffs and current+archived permissions, source clocks only, no provider/compute invention or latest fallback.
- Create `web/src/features/lineage/{types.ts,validation.ts,api.ts,lifecycle.ts,DomainClocks.tsx}`: exact validation, closed GET path construction, generation-scoped local acceptance state, full Beijing-zone labels, ranges and unknown reasons, pure exact nonnegative millisecond delays. No protected persistence. Parent wires shared headers/App/state.
- Create dedicated `tests/test_b4_clock_contracts.py`, `tests/test_b4_clock_publication.py`, `tests/test_b4_clock_routes.py`, `tests/test_b4_clock_specialized.py`, `web/tests/unit/b4-lineage.test.ts`. Integration tests use only disposable `dragon_test` schema; custom fixture avoids shared stale schema head until parent wiring.
- Create `docs/acceptance/b4b-six-clocks-2026-11-24.md`: evidence and exact parent integration hooks.

## Execution Checklist

### Task 1: Preparation And Test Scope
- [x] Read approved spec sections 4/12 and owned source/testing patterns.
- [x] Load brainstorming (already-approved design), writing-plans, TDD, bits-unit-test-gen and executing-plans instructions.
- [x] Run bits `prepare_test.sh` with `TMP_ROOT=ROOT/.temp/b4-clocks`; retain OS limitation, no invented successful utree bootstrap.
- [x] Load bits language knowledge, scope, defect analysis and generation-loop/report protocols.
- [x] Record TARGETS and BUG_MAP before generating tests. No unrelated diff review.

### Task 2: Pure Contract RED/GREEN
- [x] Generate tests asserting UTC normalization/naive rejection, strict immutability, exact keys/types, input observation requirement, duplicate/sorted/budget archive rejection, known/mixed/unknown aggregation, no empty-set freshness, absent compute/page not-applicable, exact delay and backward causal failures.
- [x] Run `.venv/Scripts/python.exe -m pytest tests/test_b4_clock_contracts.py -q -o cache_dir=.temp/b4-clocks/pytest`; retain RED evidence for missing feature.
- [x] Implement named pure contracts; run the identical command for GREEN.

```python
def test_unknown_supplier_prevents_known_aggregate():
    cell = aggregate_clock((T0, None), reason="input_clock_unavailable")
    assert cell.state == "unknown"
    assert cell.at is None
    assert cell.first_at == cell.last_at == T0
```

### Task 3: Evidence And Publication RED/GREEN
- [ ] Generate tests for independently verified evidence bytes/object hash/source/revision/timestamp binding, unchanged-key replay and changed-key conflict, complete actual dependency matching, distinct domain cutoffs, completion sampled after materializer, backward publish failure with no partial results, refresh bytes unchanged, corrupt archive fail-closed, legacy unknown.
- [x] Run dedicated publication test file and retain RED.
- [x] Implement storage/model/service/repository changes and additive migration; rerun for GREEN. Shared conftest guards dragon_test and creates disposable schemas; fresh and existing-schema migration upgrades verified. No daily DB migrations.

```python
with pytest.raises(ValueError, match="publication"):
    validate_publication_time((T0,), T0 - timedelta(microseconds=1))
```

### Task 4: Dedicated Daily And Specialized Routes RED/GREEN
- [ ] Generate isolated FastAPI tests using existing fixed error handlers and explicit route registration. Assert required/duplicate/unknown query rejection before storage, explicit 404, mismatch 400, denial 403, corrupt lineage 503, exact sanitized response.
- [ ] Generate RT/LHB repository tests for source/kind/batch/revision exact selection, invisible parent/child, revoked current/archive permissions, multiple revisions, provider/compute unknown, snapshot/page not-applicable.
- [x] Run dedicated route/specialized tests for RED; implement exact methods/minimal route additions; rerun for GREEN.

### Task 5: Isolated UI RED/GREEN
- [ ] Generate Vitest tests for exact daily/source cells, mismatched PUB/identity/cutoff, malformed clocks, timezone/midnight, known range/unknown subset, causal delays, stale responses, selection reset, same-key refresh retention, rejected data no acceptance advancement, denial clearing.
- [x] Run Vitest from ROOT/web with the dedicated `.temp/b4-clocks/vitest.config.ts` cache override; retain missing-module RED evidence.
- [x] Implement isolated typed modules/component, no shared UI changes. Vitest GREEN; full TypeScript originally passed, latest shared fixture errors recorded separately.

### Task 6: Verify And Deliver Hooks
- [x] Run all dedicated suites and relevant existing publication/RT/LHB regressions with raw logs/cache beneath `.temp/b4-clocks`. Shared readiness and concurrent test-database teardown limitations must remain disclosed.
- [x] Run owned-file Ruff and compileall with `PYTHONPYCACHEPREFIX=ROOT/.temp/b4-clocks/pycache`.
- [x] Review owned diff for five-domain/six-clock exactness, fail-closed authorization, no backfill/latest fallback or forbidden file edits.
- [x] Attempt mandatory `utree flush` with same ROOT/temp variables; record missing executable honestly.
- [x] Record RED/GREEN counts, DB/tool limitations and integration hooks in owned acceptance document.

## Parent Hooks (Not Owned Edits)

1. Register daily lineage router before `/api/snapshots/{snapshot_id}` in owner and public apps. The repository must be the same policy-configured `PublicationRepository` instance; public lineage does not grant new authorization.
2. Add exactly intended GET paths to public route allowlist/Caddy. Existing RT/LHB feature switches/public policy remain authoritative.
3. Wire `0016_clock_lineage` after actual `0015_daily_ai` into env/schema/release readiness. No daily DB execution here. Missing predecessor is a coordination limitation, not a fabricated migration.
4. Source collector supplies actual object bytes, private attestation bytes and independently trusted verifier to `verify_input_evidence`; never accept asserted historical receipt sidecars or creation-time substitution. Insert evidence before PUB; do not mutate old PUB blocks.
5. Header/panels request selected lineage, validate response, then accept local `page_updated_at`; selection/reset/denial invalidates generation. Display legacy `source_time` separately.
6. B7 owns private run completion metadata. Public lineage must never serve private run/job records.

## Plan Review

All spec section 4 acceptance requirements map to tasks 2-5; section 12 shared router/migration sequencing maps to task 6 parent hooks. No sixth daily domain, daily envelope modifications, raw public evidence, invented receipt/computation, or DB execution beyond guarded disposable tests. User instruction permits implementation fixes during TDD; bits generation-loop test-only repair rules govern test repairs, not the explicitly requested production implementation.

## Current Ownership Handoff

`publication/service.py` was declared stable-ready and handed to parent on 2026-11-24 for the B3 `summary_selections` hook. B4 will not edit or format it again. Parent owns `SnapshotClocks.tsx`, shared state/header, app/router/public facade, proxy and schema wiring. Specialized lineage GETs remain owner-only because current methods do not implement a public-sharing policy. Unchecked test-generation checklist lines describe exhaustive scenario aspirations; actual executed coverage and residual gates are listed in the acceptance document rather than asserted complete.
