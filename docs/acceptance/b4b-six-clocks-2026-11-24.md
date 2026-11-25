# B4b Q01 Six Clocks: Owned Implementation And Handoff

Date: 2026-11-24. ROOT: `E:/ByteDance/Projects/Code/.temp/DragonRadar-AllRequirements-20261124`.

## Status And Scope

B4-owned implementation is ready for parent integration. This is not a release, production-source acceptance, historical-PIT certification, or all-requirements signoff.

Exactly five daily domains (`market,topic,news,regulation,role`) expose six distinct clocks. New immutable input evidence, per-domain archival, authorized daily metadata reads, precise RT/LHB metadata reads and isolated UI rendering are implemented. Old daily payloads and specialized data envelopes are unchanged.

Owned production files:

- `src/dragon_radar/domain/clocks.py`
- `src/dragon_radar/publication/{clocks,models,tables,service,repository}.py`
- `src/dragon_radar/migrations/versions/0016_clock_lineage.py`
- `src/dragon_radar/http/routes/lineage.py`
- Dedicated additions only in existing RT/LHB routes and repositories.
- `web/src/features/lineage/{types,validation,api,lifecycle}.ts` and `DomainClocks.tsx`.

Parent added `SnapshotClocks.tsx` in the same UI directory. B4 did not edit it. `publication/service.py` was handed to parent as stable-ready before the requested B3 `summary_selections` hook; B4 will not edit/format it again.

## Verification Evidence

Raw evidence is under `.temp/b4-clocks/`. Tests use ROOT `.venv/Scripts/python.exe`, `PYTHONPATH=ROOT/src`, and only `dragon_test` with disposable per-test schemas.

| Gate | Observed Result | Log |
| --- | --- | --- |
| Initial pure contract RED | 10 failures: missing implementation | `contracts-red.log` |
| Initial publication RED | 3 failures | `publication-red.log` |
| Initial service RED | 1 failure | `service-red.log` |
| Initial route RED | 10 failures | `routes-red.log` |
| Initial specialized RED | 3 failures | `specialized-red.log` |
| UI RED | Missing-module suite failure before implementation | `ui-red.log` |
| Added hardening RED | Missing-result/reference and naive-cutoff failures | `publication-hardening-red.log` |
| Numeric timestamp RED | 2 coercion failures; concurrent DB setup error also recorded | `final-hardening-red.log` |
| Zero-anomaly RED | Visible source batch incorrectly rejected 404 | `anomaly-zero-red.log` |
| Final dedicated backend | **40 passed**, exit 0, no skips/errors | `backend-final.log` |
| Final dedicated UI | **11 passed**, exit 0 | `ui-green.log` |
| Scoped TypeScript including parent lineage wrapper | Exit 0 | `typescript-scoped.log` |
| Owned-file Ruff | All checks passed, exit 0 | `ruff.log` |
| Owned-file compileall | Exit 0; pycache redirected below B4 temp directory | `compileall.log` |
| Existing specialized regression subset | **7 passed**, 437 intentionally deselected | `specialized-regression.log` |
| Existing publication/role/continuity regression | 99 passed, 5 failures, 1 teardown error | `publication-regression.log` |
| Windows CRLF-aware diff check | Exit 0 | `diff-check.log` |
| Mandatory bits `utree flush` attempt | Exit 127: executable absent after unsupported Windows bootstrap | `utree-flush.log` |

The broader publication regression failures concern stale `0014_research` schema-head/readiness expectations versus the new migration chain. Its error is a concurrent disposable-schema teardown deadlock. An intermediate B4 run also had 40 passing test bodies plus two teardown deadlocks; retained separately in `backend-green.log`. The final rerun is clean. Do not relabel these intermediate failures as application failures or silently discard them.

Full-project TypeScript initially passed, then a later run failed only on sibling `tests/fixtures/b8-residual.ts` unknown `body.data` accesses. That file is untouched; `typescript.log` retains the failure. Full shared TypeScript and schema readiness remain parent gates.

## Generated Coverage And Fixes

Dedicated tests: `tests/test_b4_clock_{contracts,publication,routes,specialized}.py` and `web/tests/unit/b4-lineage.test.ts`.

Coverage includes frozen strict-key contracts, explicit timezone inputs, numeric-epoch rejection, complete/mixed/unknown aggregation, no empty-set freshness, sorted unique/bounded dependencies, real observation and evidence hash binding, independently trusted verification, replay/conflict/immutable triggers, exact legacy unknowns, corrupt-present rejection, actual completion sampling, backward PUB rollback without partial results, authorized byte-stable refresh, current public-sharing revocation, exact RT/LHB identity/PIT, parent/bar availability, corrected LHB revision receipt preservation, browser generations/reset/denial/backward clock, closed GET URLs, SSR five-domain rendering, full Beijing timestamps and exact integer-millisecond delays.

Initial analysis concerned an absent feature, not an asserted existing defect list. RED/GREEN hardening corrected newly implemented numeric timestamp coercion, naive legacy cutoff inference and a false 404 for zero emitted anomalies. A missing materializer with a non-null command reference now fails before publishing, preserving the existing publication result contract. Source event is not universally constrained to precede provider publication.

## Backend Integration Hooks

1. Register `dragon_radar.http.routes.lineage.router` before `/api/snapshots/{snapshot_id}`. It reads `app.state.publication_repository`; `get_lineage(publication_id, trade_date)` must exist on that same policy-configured repository/facade. Parent reported adding facade forwarding and state wiring.
2. Public daily metadata must delegate to the `PublicSharingAccessPolicy`-configured inner repository. It reauthorizes current and embedded grants and withdrawals on every read; never replace it with a local-policy repository or legacy snapshot fallback.
3. Add only GET `/api/snapshots/lineage` to intended public allowlist/proxy rules. **RT/LHB lineage stays owner-only.** Their existing repository constructors/state are unchanged (`realtime_repository`, `lhb_repository`); they check current local display rights and captured authorization, not a new public-sharing policy. Do not add specialized public mounts merely because RT data already has other public routes.
4. Migration `0016_clock_lineage` has actual predecessor `0015_daily_ai`. Fresh schemas, absent-column existing schemas and repeat upgrade were verified. It adds nullable `publication_results.clock_lineage` and append-only `input_clock_evidence` with UPDATE/DELETE/TRUNCATE rejection. Parent owns migration env/schema/release readiness and the later migration head. No earlier migration was edited and no daily database migration was executed.
5. Collector integration calls `verify_input_evidence(evidence, object_bytes, evidence_bytes, verifier=trusted_verifier)` or `InputClockRepository.append(connection, evidence, object_bytes=..., evidence_bytes=..., verifier=...)`. The verifier must independently authenticate acquisition/source evidence. A hash-only sidecar assertion is insufficient. No permissive default verifier is provided.
6. Evidence key is `(source_id,archive_type,archive_id,revision_key)`, with exact source version and object digest binding. Market/topic object bytes are stored UTF-8 canonical batch JSON; revision key is `"1"`. News uses its archived revision identity/canonical revision model; regulation uses the exact snapshot and source-specific archived events. Corrections need a new immutable identity/revision, not an update.
7. Domain materializers resolve their actual cutoffs and dependencies, including role historical dependencies. The service samples completion immediately after each ready materializer and rejects backward completion/PUB time. Parent's B3 news hook must finish before that completion sample and add its authorized dependencies without creating a sixth daily domain.

B3 hook caveat: `PromotedSummaryMaterializer.dependencies()` adds archive type `promoted_news_summary`. The current B4 supplier-clock resolver intentionally supports only `market_import_batch,topic_import_batch,news_article_revision,regulation_event_snapshot`; a new derived-summary dependency must receive explicit integration handling instead of being blindly passed through or assigned fabricated supplier receipt/compute clocks. This is a parent integration item, not a tested B4+B3 completion claim.

## UI Integration Hooks

There is no barrel export. Available exports:

- `DomainClocks` from `features/lineage/DomainClocks`, props `{value: AcceptedLineage | null}`.
- `fetchLineage`, `lineageUrl`, `LineageHttpError` from `features/lineage/api`.
- `LineageLifecycle`, `LineageTicket` from `features/lineage/lifecycle`.
- `CLOCK_NAMES`, `DAILY_DOMAINS`, `LineageSelection`, `AcceptedLineage` and response types from `types`.
- `parseLineage`, `selectionKey`, `clockDelay`, `formatClock`, `formatTimestamp`, `timestampMicros` from `validation`.

Daily selection: `{type:"daily",snapshot_id: selectedPUB,trade_date: selectedDate}`. Specialized selections additionally require exact source version, kind/batch or disclosure/revision and explicit `as_of`; they have no latest fallback.

Use a component-local `LineageLifecycle`: `begin(selection)` creates a request ticket; `fetchLineage(selection, signal)` validates its exact response; `accept(ticket, data, new Date())` records browser acceptance only if the generation still matches. Render `current`. On Reset/unmount/resource change call `reset()`; on authorization denial call `deny(ticket)`. Same-key refresh can retain prior accepted data; malformed/stale/backward responses cannot advance its time. Data is frozen/local only, with no persistence. Server page clock remains not-applicable; the UI overlay never mutates the archived response.

Parent owns SnapshotHeader details mounting and its new lazy wrapper. Keep legacy `source_time` separately labeled. Metadata cannot enable missing/disabled analysis, overwrite LOCAL notes or make stale source data live. B7 owns private result completion; public lineage must not expose private job/run clocks.

## Remaining Acceptance Boundaries

Parent owns shared app/proxy/header/schema/release integration tests and the B3 service hook. Exhaustive all-five-ready synthetic cutoff testing, specialized archived-denial permutations and end-to-end shared UI/proxy acceptance are not claimed by the dedicated suite.

Actual supplier acquisition attestations, production/historical PIT approval, trading-day 18:30 evidence and the unchanged P95 <=2.0s measurement remain external gates. Synthetic evidence tests prove contract behavior, not real source authority.

No staging, commits, push, release/version changes, deployment, MAIN/old-worktree edits or daily DB migrations were performed.
