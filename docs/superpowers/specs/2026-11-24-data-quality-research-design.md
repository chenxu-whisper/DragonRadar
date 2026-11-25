# Data Quality And Research Design

Date: 2026-11-24

Status: unified design explicitly approved by the user on 2026-11-24; implementation in progress, acceptance not complete.

ROOT: `E:/ByteDance/Projects/Code/.temp/DragonRadar-AllRequirements-20261124`.
All relative file references below are under ROOT. Initial orientation also read the main workspace; the detailed contracts below were checked in ROOT. The target worktree is actively shared: its analysis route already contains the parent's additive `order` work, unlike the older audit baseline. This document does not freeze or supersede that work.

## 1. Scope And Review Gate

This document decomposes audit batches B4-B7 into five independently reviewable designs:

1. N01: news list receipt time and permission-safe first-100-character AI preview.
2. Q01: six distinct clocks, per-domain lineage, and honest legacy/unknown semantics.
3. Q03: one local, offline, independent two-supplier frozen-sample comparison.
4. EX01/EX02: analysis async applicability and owner submit/poll lifecycle.
5. EX15/B01/B02: deep result validation and health disclosure, followed by separately bounded factor, phase, and AI adapters.

Read-only investigation, design self-review and explicit unified design approval are complete. The user confirmed “确认剩余统一设计，继续全部剩余工程开发”. Module planning, implementation and execution evidence now proceed without another design approval gate.

No code, tests, source documentation, index, commits, staging, pushes, database operations, Docker operations, or service operations were performed for this investigation. This Markdown file is the only output written. In particular, the parent owns `http/routes/analysis.py`, analysis state/query/`ResetAnalysis`, analysis tests, and `docs/contracts/workbench-api.md`; they are not edited here. Proposed integration in those files requires the parent to perform it after approval.

B2 index archival and B3 public reviewed-daily AI are separate products. News summaries do not expose private tasks or silently reuse a daily-review archive. Existing RT/LHB identities, PUBLIC no-write rules, immutable publications, retention, and LOCAL notes/portfolios remain authoritative. No broker execution is added.

Sibling proposed designs are [IH1](2026-11-24-index-history-design.md) and [AI1](2026-11-24-public-daily-ai-design.md); parent accounting remains [current status](../../all-requirements-status-2026-11-24.md) and [73-row matrix](../../all-requirements-matrix-2026-11-24.md). They are separate drafts, not approvals. IH1 retains independent index point/series basis; this comparison v1 neither treats index points as stock prices nor reconciles new index-series semantics. AI1 remains a five-domain-external sidecar: its publication sequence/time cannot be substituted for the base PUB or added as a sixth domain by this design.

### Original Clause Gaps Versus Selected Design

| Category | Source-backed obligation/gap | What this document selects, not what PRD already specifies |
| --- | --- | --- |
| Confirmed N01 original clause gap | List lacks required receipt/AI preview; detail cannot substitute for list | Code-point count, artifact fields, eligibility protocol and archive v3 are proposed implementation choices |
| Confirmed Q01 original clause gap | Daily archive/wire/UI do not preserve all six distinct clocks or per-domain unknowns | Evidence sidecar, clock-cell shapes, lineage endpoints and storage migration are proposed engineering choices |
| Confirmed Q03 original clause gap | Single-input quality/integrity is not two independent same-basis sampled comparison | Daily sample-only v1, deterministic first-ID sample, zero tolerance and report schema are selected bounds, not universal supplier requirements |
| B6 applicability plus confirmed UI integration gap | Jobs exist but browser submit/poll is absent; no GET computation and no measured/capped proof of <=5s for every path | Five-second consent-aware UI transition, admission/session/pool/result caps and analysis cooperative budget need design approval; not existing guarantees |
| Confirmed EX15 deep validation gap | Client accepts object-or-null nested result without complete run/config/manifest/result checks | Strict mirrored schemas and authoritative config read are selected engineering contracts |
| B01 health/fold disclosure gap with existing kernels | Daily coverage is not full health; shallow fold display is not exact PIT/fold validation | Full health/method/lineage presentation is bounded selected disclosure; PRD does not specify every new field or statistical display |
| Original B01/B02 extension obligation, enabling blocked | PRD requires phase-conditioned research and AI comparison; production adapter currently rejects these inputs | No additional FAC formula, new fold layout, score formula or method/model approval is selected as fact. Section 9 specifies only gated adapter boundaries and exact required evidence |

Deliverable accounting: B7a/b can close their engineering subclauses only after implementation and new acceptance. B7c extensibility/disabled-state engineering alone does NOT complete the original genuine AI comparison leg, additional approved computations, or any external enabling gate.

## 2. Evidence And Current Behavior

Sources are inspected code/contracts, not execution evidence:

| Subject | Exact inspected contracts/functions | Existing behavior versus gap |
| --- | --- | --- |
| Requirements | External `E:/ByteDance/Projects/Code/.temp/all-requirements-audit-20261124/req-audit.md` and `req-gaps.json`; `docs/DragonRadar_开发需求PRD.md` sections 4.2, 10.1-10.2, 11.6, 12.2, appendix A | N01 explicitly requires publish/receipt times and AI first 100 characters; Q01 names six clocks; Q03 requires same time/unit/adjustment/pool; ordinary-query P95 <=2s at 20 concurrent, >5s async. Audit is historical source-bound evidence, not proof of this proposal. |
| Identity classes | `docs/contracts/workbench-api.md`, AM03 and EX01/02/14/15 | PUB, intraday, disclosure, and owner job/run are different envelopes. EX02 outer keys are `job_id,status,result,failure,input`; EX15 outer keys are `run_id,status,config_hash,research_only,result,failure`. |
| News | `news/models.py::NewsArticleRevision`; `news/repository.py::create_snapshot`; `news/service.py::NewsService.materialize_context`; `http/routes/news.py::_article,_list_article,_detail_article,list_news`; `web/src/features/news/{types.ts,api.ts}`; `web/src/pages/News.tsx` | Revision has `published_at,received_at,available_at`; available must not precede receipt. Archive schema is `dragon-radar.news`, version 2, and materializer explicitly assigns `ai_summary:None`. List omits receipt and AI summary; detail has both. Rows display type/source/title/available time. List sort is available DESC then article ID ASC, independent of published time. UI default 50; bare HTTP default 20/range 1..100. |
| Daily clocks | `domain/models.py::MarketBatch`; `topics/models.py::TopicBatch`; `storage/models.py::Snapshot`; `publication/models.py::{DomainMaterialization,PublicationResult,Publication,PublishedSnapshot}`; `publication/service.py::publish`; `http/contracts.py::snapshot_envelope,snapshot_summary`; `web/src/App.tsx::legacySnapshot`; `web/src/components/SnapshotHeader.tsx` | Market/topic batches have `as_of,source_time`; no provider publication/first receipt/completion fields. Publication has `published_at`; service samples one `now` after materialization and uses it for creation/publication. No distinct domain completion clock. Reset adapter and version-list conversion hard-code source time null. `source_time` is not a proven provider publication or event timestamp. |
| Other clocks | `realtime/models.py::_TimestampedBatch,MinuteBarInput`; `lhb/models.py::DisclosureRevisionInput,LHBImportBatch`; `news/models.py`; `ingestion/supplier_collection.py::CollectedChunk`; `ingestion/sample_archive.py::{SampleChunk,SampleLineage,SampleArchive}` | RT and LHB have actual source event/received/available timestamps. RT enforces event <= receipt <= available; news/LHB enforce receipt <= available. Collection chunk stores first receipt. Availability is a visibility clock, not automatically first receipt, compute completion, or publication. |
| Supplier diagnostics | `ingestion/sample_acceptance.py::{SampleRequest,evaluate_sample}`; `sample_cli.py::{read_sample_json,main}`; `sample_archive.py::{verify_sample_archive,read_sample_archive}`; `supplier_archive_mapping.py` | Single-sample engineering quality and integrity already exist. `SampleRequest` has batch, benchmark closes, decision sessions, and exact 20-session window. Archive binds rows' availability to chunk receipts and is explicitly retrospective/PIT unverified. This is not independent paired reconciliation. |
| Analysis jobs | `analysis_jobs/models.py::AnalysisJobCommand`; `routes.py::{_command,_safe_record,install_analysis_job_routes}`; `repository.py::{_cache_identity,submit,get,get_cache,claim}`; `worker.py::{materialize_custom_analysis,AnalysisJobWorker}` | Command has snapshot/start/end/benchmark/adjustment/symbols <=5; empty symbols means full eligible ranking, not zero securities. Adjustment enum includes raw/adjusted/qfq/hfq; worker withholds qfq/hfq with a reason. Command hash plus actual adjustment version forms cache identity. Auth precedes replay/cache; owners have separate jobs. Lease default 30s, max attempts default 3, allowed 1..10; lease renewal every lease/3, idle poll 1s. No analysis compute wall-clock timeout, session cap, queue/admission cap, or real progress percentage is implemented. |
| Analysis GET applicability | `readmodels/analysis.py::materialize_analysis` and `http/routes/analysis.py::_window,_cached_result,rank_range,compare_curves` | Publication materializes 20/60/120 N-offset windows using N+1 sessions. GET validates/slices an archive or exact custom cache; cache miss returns `analysis_not_precomputed`. It never computes or submits a job. Custom cache exists and is independently validated. Thus a >5s worker is not a >5s synchronous GET handoff. |
| Research configuration/results | `research/models.py::{BacktestConfig,InputManifests,WalkForwardSpec}`; `engine.py::{BacktestResult,RunVersions,run_backtest}`; `ledger.py::{Metrics,EquityPoint}`; `execution.py::{Fill,Rejection}`; `repository.py::{submit,get,succeed}`; `http/routes/research.py`; `web/src/features/research/backtest-client.ts::validateRunRecord`; `BacktestPanel.tsx::BacktestResultView` | Config frozen/canonical SHA256; input manifests domains market/topic/role, mandatory market, adjustment/calendar/pool versions. Metrics, folds, walk-forward, and ablation have weak `object` fields in outer Python result model. Browser checks exact outer keys but only object-or-null nested values; renderer casts/falls back to empty arrays. Daily factor coverage is labeled health but is not the full pure health report. |
| Research kernels and production | `research/health.py::assess_factor_health`; `walk_forward.py::{PhasePublication,fit_regime_map,run_walk_forward}`; `ablation.py::build_ablation_report`; `worker.py::ResearchBacktestWorker`; `production.py::_inputs` and factor validation; `docs/{production-inputs.md,production-candidate-scope.md,acceptance/task27-research.md}` | Pure health and train-only anchored folds exist. Production adapter rejects walk-forward, AI legs, role domain, and non-FAC-R01 factor archive. Approved-input architecture is not actual input approval. Default worker is fail-closed unless compute supplied. Research has a 300000ms cooperative wall-clock budget, unlike analysis; config caps 250 sessions, 400 calendar-day span, Top-N 100, rebalance 1..60, folds 2..10. |

No test suites were run. Existing tests are references for later targeted verification, not results credited to this draft. No external web research or real supplier/model collection is needed to specify these engineering contracts.

## 3. B4a: N01 News Preview

### Alternatives

**A, recommended: extend the frozen news projection, with a separately eligible summary artifact.** One list read carries all required metadata and a bounded preview. Permission/provenance checks happen before projection; archive revisions stay stable. Requires a versioned archive addition and an explicit publication step when AI content becomes eligible.

**B: fetch article detail or private summarize jobs for every visible row.** Less archive work but creates N+1 requests, inconsistent list/detail revision selection, and a risk of private task discovery or late-result leakage. Reject; neither GET nor row visibility may initiate AI work.

### Exact Proposed Contract

Retain all existing list fields. Add `received_at:string`, `revision_id:UUID-string`, and `ai_preview:NewsAIPreview`. Retain `summary` as licensed supplier summary, NEVER rename it AI summary. Detail adds the same `ai_preview`; existing `ai_summary` remains a compatibility nullable string and can be nonnull only for an eligible archived summary.

`NewsAIPreview` has exactly:

| Field | Type / rule |
| --- | --- |
| `status` | `ready | unavailable` |
| `text` | string of 1..100 Unicode code points when ready; otherwise null |
| `truncated` | boolean; true iff eligible original text exceeds 100 code points; false when unavailable |
| `reason` | null when ready; otherwise `summary_not_archived | summary_not_eligible | summary_display_denied | summary_sharing_denied` |
| `summary_id` | UUID string when ready; otherwise null |
| `summary_revision` | positive integer when ready; otherwise null |
| `provenance` | `ai | ai_human_edited` when ready; otherwise null |

Count Unicode code points, consistently with Python slicing and `Array.from`, not UTF-16 units, bytes, or words. Preserve text order; do not flatten, rewrite, or replace the leading 100 code points. Text must contain non-whitespace; an ellipsis is UI decoration outside `text`. Define this explicitly as the conservative interpretation of PRD "100 characters".

New archived `NewsSummaryArtifact` has exactly `schema:"dragon-radar.news-summary/v1",summary_id,summary_revision,article_id,article_revision_id,article_revision,source_id,source_version,text,provenance,task_type,task_snapshot_id,draft_revision,verification_status,human_review_status,input_sha256,output_sha256,model_version,prompt_version,retrieval_version,verified_at,reviewed_at,available_at`. UUIDs/positive revisions are strict; digests are lowercase SHA256; text <=32768 UTF-8 bytes; versions use existing SafeVersion syntax. `task_type` is exactly `summarize`. `provenance` has the two values above. `verification_status` must be passed, `human_review_status` accepted; a human edit needs a fresh verified/accepted revision. `task_snapshot_id` binds the exact origin news publication; it is not forced to equal the later publication that archives the artifact.

Publication-time eligibility is checked against actual owner summarize task, selected draft, independently recorded verification/review, manifest-backed exact article revision and source input hashes. `succeeded`, `passed`, and `accepted` are three independent checks. Provider version strings and self-declared passed/accepted fields are not authority. Require the existing exact article-only summarize input shape: it contains no notes_context field, and any added private context rejects eligibility. Do not require a nonexistent empty notes field in valid summarize manifests. Require explicit publication consent and provider ancestry for AI-human-edited text. Public artifact excludes owner ID, private task ID, credentials, notes, and provider request bodies. Internal binding/dependencies remain privately persisted.

Proposed news archive version 3 adds `summary_artifact` (object/null) to each existing archived article; no v2 row is modified. Owner promotion uses POST `/api/ai/task/{task_id}/publish-summary`, with the same live-owner/CSRF/exact-revision verification and output-entitlement checks as AI1. Body is exactly `revision,expected_review_id,expected_content_hash,snapshot_id,date,idempotency_key,publication_consent_reference,output_entitlement_reference,attestation_reference`; immutable receipt binds `summary_id,summary_revision,article_revision_id,content_hash`. No caller-supplied owner is accepted, and same-key replay reauthorizes. The local news publication CLI accepts a selection map of only these already promoted summary IDs/revisions and exact article revisions; it cannot promote or inspect arbitrary private tasks. New publication validates the immutable summary registry and current authority, and archives only eligible artifacts. It does not generate summaries or retroactively alter the old article. New publication records text/evidence source dependencies and summary eligibility dependencies so revocation applies to derived text too. Public builds/gateways never expose the promotion endpoint or private links.

V2 `ai_summary:null` projects unavailable `summary_not_archived`; nonnull legacy strings without independently verifiable summary provenance do NOT become ready. A present corrupt v3 artifact is `DATA_UNAVAILABLE`, not a silently missing summary. Missing/ineligible artifact leaves licensed article metadata usable. Ordinary article/source display denial retains existing 403/clearing behavior; it cannot be downgraded to a harmless unavailable preview to expose protected metadata.

At archiving require source storage and permitted AI processing at original use; at every read require current plus archived local display and, for PUBLIC, sharing rights for generated text and referenced source material. Reuse AI1's proposed `AIOutputEntitlement` contract and output-rights locking/revocation only after sibling approval; local summary publication freezes an entitlement reference privately, not a new assumed SourceGrant permission. SourceGrant's booleans do not prove generated-text rights: production acceptance needs the actual contract/use coverage. Display rights do not grant AI, export, or sharing. No grant is sent to the browser.

### Flow And Files

Existing snapshot selection -> authorized immutable news archive -> strict version-specific article validation -> eligible preview projection -> one list response -> `validateNewsListResponse` -> labeled publish/first receipt/availability and preview. Detail uses the same artifact. No per-row detail calls.

Proposed implementation files: `news/{models.py,service.py,repository.py}`, new `news/summary_publication.py` and local CLI wiring, `http/routes/news.py`, `web/src/features/news/{types.ts,api.ts}`, `web/src/pages/News.tsx`. An additive summary-selection input is a proposed publication CLI contract, not an existing `PublicationCommand` field. Freeze its canonical identity before implementation so changed selection with the same idempotency key conflicts. Parent coordinates changes to publication inputs/common docs.

Use the existing Beijing `timestamp` formatter for all displayed times, including detail, with full date and explicit zone. Retain `available_at` sorting, filtering, paging, selection and totals. Current-page CSV adds `received_at,revision_id`; preview text is excluded until the export authorization path explicitly authorizes this derived field. No widening of export/sharing permission follows from display.

### Acceptance Scenarios

- Ready eligible artifact longer than 100 code points, including astral characters: list/detail have identical first 100; revision binding exact; truncated true. No summary/job request occurs.
- Title-only article or legacy v2: receipt/publish/availability independently visible; preview unavailable and UI shows "AI temporarily unavailable"; licensed supplier summary remains separately labeled, not substituted for AI.
- Succeeded/unverified, verified/unreviewed, accepted-but-edited, mismatched revision/hash, or future artifact: no ready preview. A corrupt archived binding fails closed.
- A late AI result does not change the old PUB; a new explicit publication may include it, with unchanged origin article identity and original receipt.
- Public reads cannot discover private jobs. Display/sharing revocation clears preview, article content, and actions as applicable; late responses cannot restore them.
- Filter/page/return retains PUB/date and selected article; no change to event counts or sorting. UTC crossing midnight displays correct Beijing date.

## 4. B4b: Q01 Six-Clocks Lineage

### Alternatives

**A, recommended: additive input clock evidence plus immutable domain clock archival and dedicated lineage reads.** Preserves existing strict domain payloads/envelopes and distinguishes multiple suppliers. Costs small append-only metadata storage and explicit adapters.

**B: add six scalar fields to every existing envelope and infer missing clocks from `as_of`, `available_at`, or publication time.** Simpler UI but collapses source/visibility/computation time, breaks specialized exact contracts, and invents historical evidence. Reject.

### Clock Semantics

The six names are `source_event_at,provider_published_at,first_received_at,computed_at,snapshot_published_at,page_updated_at`. There are five existing daily publication domains, not "six daily domains": `market,topic,news,regulation,role`. The requirement is SIX CLOCK TYPES per applicable domain.

- Source event: actual provider-defined event timestamp; session/trade date alone is insufficient.
- Provider published: actual supplier release timestamp; news `published_at` maps here, not to source event.
- First received: first local acquisition of this immutable source object/revision. Retries preserve it; a correction has a new source revision and its own receipt, never rewrites the prior revision.
- Computed: local completion of that domain's materialization, captured immediately after its materializer finishes.
- Snapshot published: unified PUB `Publication.published_at` for PUB-derived reads, not upstream topic/market publication or SQL commit inference.
- Page updated: browser receipt/acceptance of validated authorized data for the selected resource, not source freshness, server completion, or snapshot publication. It is UI state only.

`as_of` and `available_at` remain separate PIT cutoffs. None of the six replaces availability/publication-sequence checks. No default timestamp is invented from current wall-clock time.

### Exact Proposed Storage And API

New append-only `input_clock_evidence` metadata, keyed by `(source_id,archive_type,archive_id,revision_key)` with unique immutable content digest. Fields: `source_version,input_sha256,source_event_at,provider_published_at,first_received_at,evidence_sha256`. Timestamps nullable UTC-aware; at least one actual source/receipt observation and a resolvable private evidence hash are required. Changed evidence is a new archive revision, not an UPDATE. No paths/raw supplier records/grants cross a public API.

Collection adapter may supply first receipt from verified `SampleChunk.first_received_at`, bound to actual object/chunk hash; `SampleArchive` does NOT establish contemporaneous historical PIT. Local input adapters accept a sidecar only after source/object/hash and timestamp verification. Caller assertions of old receipt times are insufficient. This metadata avoids adding imaginary mandatory fields to existing `MarketBatch`/`TopicBatch`.

Add nullable `clock_lineage` JSONB to `publication_results`, reflected in `DomainMaterialization`/`PublicationResult`, through an additive migration. No UPDATE/backfill of old results, no migration execution in this task. After each materializer, publication service archives completion and resolved upstream clock evidence; after all domains, publication samples its actual publish time. Completion must precede or equal publication time; a backward clock fails publication. Missing/disabled domains have no fabricated compute clock.

Each archived `DomainClockArchive` has exactly `schema:"dragon-radar.domain-clocks/v1",domain,input_id,as_of,status,inputs,computed_at`. `input_id` is the actual reference from PublicationCommand or null, `status` is existing ready/missing/disabled, `computed_at` nullable UTC timestamp. `inputs` is sorted by `(source_id,archive_type,archive_id,revision_key)`, unique, <=10000 objects; over-budget fails publication instead of truncating. Each input object has exactly the metadata key fields above plus nullable event/provider/receipt timestamps. `as_of` comes from that domain's actual cutoff, never silently from market for news/topic/regulation/role. Missing unknown cutoff is null. A role cutoff is its actual archived input/context cutoff; absent evidence remains unknown.

New daily read:

`GET /api/snapshots/lineage?date=YYYY-MM-DD&snapshot_id=PUB`

Both parameters required; duplicate/unknown params reject 400. Response exactly `schema:"dragon-radar.publication-clocks/v1",snapshot_id,trade_date,domains`. `domains` contains exactly the five actual daily domain names, each with `status,input_id,as_of,clocks`. It reads existing authorized publication and archived clock block only. It requires the same display/sharing/withdrawal gates as the selected PUB. No-data explicit unknown ID is 404, date mismatch 400, denied 403, corrupt present lineage 503 DATA_UNAVAILABLE. Old missing clock blocks return usable legacy lineage, not rebuilt materializations.

`clocks` has exactly the six names. Each `ClockCell` has exactly `state,at,first_at,last_at,reason`, where state is `known | mixed | unknown | not_applicable`. Known means every relevant input has the same proven timestamp (at/first/last equal). Mixed means all inputs known but values differ (at null, exact min/max range). Unknown means one or more relevant inputs unproven (at null, min/max may disclose the proven subset but cannot imply complete freshness). Not-applicable has all times null.

Reasons are null for known/mixed; otherwise `legacy_clock_unavailable | input_clock_unavailable | domain_missing | domain_disabled | browser_clock | domain_clock_not_applicable`. Empty ready input sets are unknown, never fresh. Every timestamp is UTC-normalized with a timezone. Scalar compute and PUB publication cells are known only when actually recorded. `page_updated_at` is always null/not_applicable/browser_clock on the server; UI overlays local state without mutating response/archive.

New specialized metadata reads preserve AM03 existing outer/data contracts:

- `GET /api/realtime/lineage?source_id=S&kind=quotes|minutes|sector_flow|anomalies&batch_id=B&as_of=T`
- `GET /api/lhb/lineage?source_id=S&disclosure_id=D&revision=R&as_of=T`

All parameters required; bound safe identifiers as existing domain types, revision positive, T aware UTC. Unknown/mismatched identity never selects latest. Response exactly `schema:"dragon-radar.source-clocks/v1",identity,as_of,clocks`. RT identity is `source_id,source_version,kind,batch_id`; LHB identity is `source_id,source_version,disclosure_id,revision`. Use their existing current/archived permission and PIT checks. Raw-source provider publication unknown stays null; daily snapshot publication is not applicable. Source import availability remains visible in the existing endpoint, not relabeled compute completion. Anomaly computation completion requires actual new archival evidence; old events remain unknown. No domain fake snapshot/rule IDs are introduced.

Owner job/run clocks use existing private record lifecycle internally; result panels disclose actual recorded completion where a versioned private result contract carries it. They do not call public lineage endpoints or invent daily publication for a private run. For records without emitted completion metadata, show unknown; B7's new lineage explicitly records computation timing from the worker, never from GET time.

### Legacy Mapping And Delay Rules

| Existing field | Permitted known mapping | Forbidden inference |
| --- | --- | --- |
| Market/topic `source_time` | Retain as legacy source-time metadata; event mapping only with verified source contract/evidence | Cannot automatically mean event or provider publication |
| News `published_at,received_at` | provider published and first receipt of archived article REVISION | Cannot become source event or article-global original receipt |
| RT/LHB `source_event_at,received_at` | respective source event and first archived revision/batch receipt where existing ingestion proves first acquisition | Cannot infer provider publication, compute completion, daily PUB |
| `available_at`, snapshot `as_of`, source archive `created_at` | Retain actual availability/cutoff/creation semantics | Not substitute first receipt or computed clock |
| `Publication.published_at` | selected unified PUB publication | Not use current latest or upstream input publication |

Unknown legacy compute stays unknown even though the old service sampled publication time after computing. New archive has real computed timestamp. Old Reset adapters must not continue forcing null when new lineage exists; instead `SnapshotHeader` uses validated lineage directly, and legacy `source_time` remains separately labeled. Parent coordinates any `App.tsx` adapter wiring; do not encode six clocks into the legacy `Snapshot` as if already present.

Display timestamp labels/full Beijing dates with explicit zone. Show domain cutoff and range/unknown state, not a single guessed whole-market clock. Computable delays are exact millisecond differences: provider-minus-event, receipt-minus-provider, completion-minus-latest-known-receipt, publication-minus-completion. Only compute when required endpoints/completeness are proven and nonnegative; otherwise null plus reason. Do not impose event <= provider where actual source semantics do not support it; contradictory attested causal evidence is a quality failure, not clamped zero.

Source freshness compares a local observation time to proven source event/provider time and is labeled as such; page-updated never makes stale input "live". No new universal real-time SLA threshold is invented. Daily target 18:30 Beijing requires actual trading-day publish evidence and remains an external measurement gate.

### Flow, Files, And Acceptance

New files: `domain/clocks.py`, `publication/clocks.py`, `http/routes/lineage.py`, `web/src/features/lineage/{types.ts,api.ts,DomainClocks.tsx}`. Integration: supplier collection/archive adapters, publication models/tables/service/repository, additive migration/schema contract, public/owner exact read allowlists, `SnapshotHeader.tsx`, News/RT/LHB/research panels. Common contract documentation remains parent-owned coordination.

- New all-known daily clocks survive refresh byte-for-byte; only validated local page-update state advances.
- One unknown supplier clock makes the aggregate unknown even when another supplier is current. Multiple known source clocks show range, not falsely uniform provenance.
- Legacy publication keeps publish/cutoff and verified revision receipt where available; unknown event/provider/compute remains unknown, never "live".
- Late corrected bar/article/topic membership has new identity/receipt; original PUB/PIT selection and sequence watermark remain unchanged.
- RT minute selection honors both parent and bar availability; explicit unknown batch/LHB revision yields 404, not current-source fallback.
- Switching PUB/reset clears page-update state for mismatched resources. Same-key refresh may retain previously valid data with its prior update time until accepted replacement; rejected/malformed/late reads never advance it.
- Denial clears protected lineage. Clock metadata never enables an affected analysis module or erases independent LOCAL notes; domain missing/disabled stays visible.
- UTC/Beijing midnight, DST-free Beijing conversion, negative causal delay, incomplete input clocks, and backward compute/publication time have deterministic outcomes.

## 5. B5: Q03 Independent Paired Samples

### Alternatives

**A, recommended: two verified normalized archives plus an explicit frozen comparison plan, local CLI only.** Reuses hardened reads, storage rights, exact Decimal and lineage; deterministic selection/report. No ingestion, DB, network, or production approval side effects.

**B: compare live supplier pulls or ingestion-precedence rows.** More convenient operationally but inputs shift while comparing, receipt/adjustment/pool are not frozen, and independent sources may actually share one upstream. Reject as acceptance evidence.

### Scope And Independent Identity

First version compares daily common-stock bars, PIT security identity/state, and benchmark closes already represented by `SampleRequest`; it is not an intraday, topic-ratio, or factor-ranking reconciliation engine. No production strategy or supplier winner is selected.

Reuse `read_sample_archive` on each input, expected external file SHA256 and each separately supplied current grant. Existing single-sample diagnostics must pass. This constrains v1 to the normalized sample's existing 20-session/SSE-common-stock engineering scope; do not claim general-market acceptance. Actual contemporaneous receipt evidence/PIT approval remains external.

`ComparisonPlan` exact fields: `schema:"dragon-radar.supplier-comparison/v1",kind,left_archive_sha256,right_archive_sha256,left_supplier_id,right_supplier_id,left_upstream_id,right_upstream_id,independence_evidence_sha256,as_of,decision_sessions,window,pool_sha256,calendar_sha256,adjustment_semantics,adjustment_evidence_sha256,identity_mapping_sha256,benchmark_definition_sha256,fields,max_security_count,tolerances,tolerance_evidence_sha256`.

`kind` is test/supplier_sample; SHA256s lowercase 64 hex; supplier/upstream IDs bounded nonblank 100-character tokens; `as_of` aware UTC; sessions sorted unique explicit dates; window exactly integer 20. `adjustment_semantics` exactly `raw_ohlc_with_multiplier`; this matches `DailyBar`, not an invented qfq/hfq guarantee. `max_security_count` integer 1..100, default 100 and frozen in the plan. Fields nonempty unique subset of `open,high,low,close,previous_close,volume,amount,adjustment_factor,benchmark_close`; order is the listed canonical order, not caller order.

Reject identical archive/file hashes, same supplier, or same upstream. Independent origin evidence must resolve to source-specific acquisition lineage for both; two filenames/source labels alone do not prove independence. Test kind may use explicit synthetic independence evidence and remains test_only. Real origin evidence missing is blocked, not accepted by assertion. Reports always `production_approved:false,historical_pit_status:"unverified"`.

Frozen private identity map exact entries `left_security_id,right_security_id,canonical_security_id,exchange,code,security_type,board_category,effective_from,effective_to`, sorted/unique one-to-one with a digest. Do not use name equality or code alone as identity. Compare visible state at each required date with existing `state_at`; exchange/code/type/board/listing/ST/suspension/delisting must agree exactly where provided. Unknown state is unavailable; an actual identity/state contradiction is a fail. Map membership/effective bounds must match each archived state; missing mapping never drops a security silently.

### Comparability And Deterministic Tolerance

Before values, require exact normalized UTC `as_of`, decision sessions/window, declared expected canonical security pool, required calendar sessions and definitions, benchmark definition, currency/price/volume/amount units, and documented raw-price/multiplier semantics. Require factor values equal per mapped security/session before comparing adjusted calculations. Supplier-specific `source_version`, `pool_version`, `calendar_version`, and `adjustment_version` are retained on each side; different tokens are not automatically different semantics, nor do equal tokens prove equivalence. Frozen plan evidence binds their semantic equivalence and actual contents.

Pool digest is SHA256 of sorted canonical expected IDs; calendar digest covers exchange and ordered required sessions. Benchmark ID mapping and definition digest are mandatory because `benchmark_closes` itself does not encode benchmark identity. Pool/calendar mismatch or unsupported adjustment/unit semantics is `incompatible` (CLI exit 2), with no numerical agreement claim. No currency/unit conversion, date rebasing, intersection-only denominator, nearest-time match, revision substitution, or averaging in v1.

Stable sampling chooses the first `min(max_security_count, pool_size)` canonical IDs ascending, BEFORE looking at values. Compare every required session derived by the existing 20-window warmup logic. Selected IDs/dates are fixed in the report; benchmark observations compare for every required date once. Whole-pool comparability is checked before sampling. Duplicate mapped keys fail; missing selected row/unknown optional value produces unavailable, not a zero or omission.

Each selected field tolerance is exactly `{abs:string,rel:string}` as finite nonnegative Decimals; the tolerance map has exactly the selected field names. Default both `"0"` for every field; no guessed vendor tick/tolerance. Nonzero tolerance is only permitted when a separately approved precision specification is included in the plan's hashed evidence: `tolerance_evidence_sha256` must resolve to that evidence when any nonzero value is used, otherwise it is null.

Agreement iff `abs(L-R) <= max(abs_tol, rel_tol * max(abs(L),abs(R)))`, under precision 34/ROUND_HALF_EVEN. Report exact left/right/difference/limit as decimal strings. Inclusive boundary is intentional. Adjustment factor and identity always exact equality, irrespective of other field tolerances. Nonfinite input fails; two missing values are unavailable, not agreement. Absence of previous close on one/both sides does not become zero.

No derived ratios are stitched from left numerator/right denominator. If later added, calculate each side wholly within its source and require identical frozen denominator definitions first; that is outside v1.

### CLI, Report, And Files

Proposed command, not executed:

`python -m dragon_radar.ingestion.comparison_cli --left LEFT --right RIGHT --plan PLAN --identity-map MAP --left-grant LG --right-grant RG --output REPORT`

All paths local regular single-link files, no symlink/junction traversal, bounded read with existing hardening and duplicate-key rejection. Each archive <=16MiB, combined archives <=32MiB; plan/map/grants total <=4MiB; <=100000 input rows per archive, <=100 mapped selected IDs, <=250 required sessions, <=250000 comparison cells. Validate bounds before nested comparisons. Output exclusive private secure_output; cannot alias an input. Real operation-time grant checks occur at read and durable output; no caller `--now` may extend real rights.

Report exact root fields: `schema:"dragon-radar.supplier-comparison-report/v1",comparison_id,status,test_only,production_approved,historical_pit_status,plan_sha256,identity_mapping_sha256,inputs,selection,counts,issues,cells`.

`comparison_id` SHA256 of canonical plan, both verified archive digests, identity-map digest, and algorithm version `paired-sample-decimal34-v1`; no wall-clock/run-order value affects identity. `inputs` preserves both source IDs/versions, upstream identities and lineage digests, never secret grants/raw paths. `selection` includes selected canonical IDs and exact required dates/fields. `counts` contains nonnegative `expected,agree,conflict,unavailable` and must partition expected. Issues use constant codes and mapped identity/date/field, sorted by code/date/ID/field. Cells exactly `canonical_security_id,trade_date,field,status,left,right,absolute_difference,limit,reason`; benchmark uses a separate canonical benchmark identity, never a stock substitution.

Status precedence: `fail` for integrity/schema/duplicate/identity/explicit numerical conflict; `incompatible` for proven noncomparable basis; `blocked` for unavailable rights/evidence/observations; `pass` only complete comparable agreement. Parse/authorization failures expose safe constant diagnostics, not private values. Exit 0 pass, 1 fail, 2 blocked/incompatible. No acceptance is written to ingestion/publication/production DB.

New files: `ingestion/{comparison.py,comparison_cli.py}` and future `docs/supplier-comparison.md`; reuse sample archive/parser/SourceGrant, do not repurpose `evaluate_sample` into a two-source function.

### Acceptance Scenarios

- Independently frozen same-basis sample passes exact comparisons; rerun/record-order permutation produces same selection, cells and comparison identity.
- Same archive copied/renamed or different supplier labels sharing upstream cannot pass independence.
- Equal numeric values with mismatched UTC cutoff, units, currency, benchmark, pool or adjustment semantics are incompatible, not agreement.
- One conflict beyond inclusive deterministic tolerance fails with both values retained; no reconciled/averaged value appears.
- Duplicate identity mappings/rows and conflicting source identity fail; null/missing/suspended observations are disclosed, never coerced to zero.
- Different provider version tokens with genuinely attested identical semantics are comparable; equal tokens with unequal actual pools are not.
- Grant expiry during report persistence rejects/rolls back private output. Test-only successful report remains production/PIT unverified.

## 6. B6: EX01/EX02 Analysis Applicability

### Alternatives And Recommendation

**A, recommended: archive GET stays read-only; explicit owner custom analysis always queues, with a five-second UI transition for any eligible stalled ordinary read.** Avoids synchronous recomputation. Use the already implemented job/cache path and expose `analysis_not_precomputed` as owner action. Slow custom calculations are async from the start, not after consuming five seconds of synchronous compute.

**B: every ordinary ranking read automatically creates a job.** Creates writes for pure reads, unnecessary compute for materialized windows, and permission/intent problems on public surfaces. Reject.

Conclusion: fixed 20/60/120 PUB/cache GETs have no computation to move. Their non-compute nature is demonstrable statically, but no finite latency proof was executed here, and no hard existing capacity bound proves they can never exceed five seconds. Custom/missing-cache computation needs UI submit/poll integration. A synthetic >5s worker alone cannot close that UI obligation.

### Exact Proposed Behavior And API

Use existing POST/GET without adding daily fields:

- POST body `snapshot_id,start,end,benchmark,adj,symbols,idempotency_key` -> exactly `job_id,status`.
- GET -> exactly `job_id,status,result,failure,input`; input exactly `snapshot_id,start,end,benchmark,adj,symbols,algorithm_version,adjustment_version`.
- `queued,running`: result/failure null. `succeeded`: result object, failure null. `failed`: result null, structured failure. No cancel/progress field exists.

UI selection fingerprint freezes PUB/date/start/end/benchmark/adj/symbols plus parent sort/query/page/return context. Sort/filter/page are DISPLAY context, not compute command fields. Empty symbols requests full ranking, whereas comparison uses 1..5 unique exact security IDs. Preserve parent ranking direction/defaults and null-last logic; never change command hashing to incorporate sort.

On `analysis_not_precomputed`, owner sees an explicit "Run custom analysis" command, disabled unless owner session and authorized input/validated bounds exist. For a user-initiated ordinary read still unsettled at exactly 5000ms, owner UI transitions to async offer/status, aborts/invalidates the late GET generation, and may submit only if that initiating action explicitly included compute consent and the immutable command is eligible. Otherwise require click. This is an honest UI timeout/handoff, not evidence of elapsed computation. A success at 4999ms remains ordinary GET; an obsolete response after transition cannot overwrite the job state.

Public at 5000ms shows delayed read/retry or archived unavailable; it issues NO POST or private GET, and cannot initiate background collection/computation. Public rank/compare remain authorized immutable archive reads, including any deployment-authorized existing cache policy. No attempt to route public fetch to owner gateway.

POST pending/uncertain outcome retains SAME idempotency key and frozen command for explicit retry; changed input allocates a new command. Disable duplicate submit. Immediately GET accepted job, then one nonoverlapping poll each 1000ms while queued/running; per-request timeout 15000ms. Show real status/elapsed time, never invented percent. Network failure pauses with explicit retry; 401 locks/clears, 403 clears protected data/action, 404 stops as unavailable, 409 keeps input and requires explicit resolution. Old owner/principal/job input responses cannot restore state.

Success deep-validates existing custom-analysis schema/version, input/snapshot/interval/benchmark/adjustment/eligible IDs, row partitions and curve dates/identities before presentation. For whole ranking use exact existing cache-backed rank GET after success so parent sorting/projection/coverage remain authoritative; for comparison re-read compare GET with exact symbols. If cache read is unavailable, show explicit result-unavailable, do not manufacture a daily envelope around private job result. qfq/hfq remain unavailable because worker semantics are unproven; do not reinterpret generic `adjusted` as qfq.

Navigating away stops browser polling, not server computation. Keep command/job reference only in in-memory owner-session state; return with same owner+fingerprint reauthorizes GET. Full reload can lose this reference and must not claim job discovery/history exists. Never persist protected result/token/job identity in UI-only preferences or LOCAL notes. Input/PUB reset invalidates the active response generation and clears result; return context may preserve only the appropriate in-memory prior selection. Denial clears all protected references.

### Capacity And Current Limits

Existing analysis command limits only unique symbols <=5, date ordering, safe IDs and adjustment enum. It has no bounded full-pool/session/output volume and no compute hard deadline. Lease 30s/default 3 attempts only controls ownership/recovery; an executing thread may renew indefinitely. Research's 300000ms checkpoint budget must not be described as analysis's existing timeout.

Proposed bounded first integration: at submit/worker input validation <=250 actual sessions, <=5000 eligible pool IDs, <=1250000 security-session observations, <=5 curves, custom serialized result <=16MiB. Empty full-ranking symbols still require pool limit. Reject exceeding capacity with structured `analysis_capacity_exceeded`; do not truncate. Admission <=20 nonterminal analysis jobs per owner; unchanged-key replay/cache hit does not consume an extra admission slot. These are proposed conservative engineering caps, not claims they guarantee P95 or a new market coverage promise.

Add 300000ms monotonic cooperative analysis budget with checkpoints in its actual expensive window loop and before success. Keep immutable `max_wall_clock_ms` in the job input/storage contract with additive migration; safe EX02 input remains the existing exact eight fields. Budget breach fails `analysis_budget_exceeded` without cache/result. Do not kill Python threads or claim checkpoints preempt blocking I/O; compute uses frozen inputs/no network, but checkpoint density and heartbeat behavior require verification. Lease/retry logic remains distinct.

Parent decides integration in owned analysis/readmodel loops and handles result-null/rank compatibility. This child does not implement them.

### Files And Acceptance

New private UI files `web/src/features/analysis-jobs/{client.ts,types.ts,OwnerAnalysisJobPanel.tsx}`; parent wires them into owned analysis view/state. Backend changes proposed in analysis_jobs models/repository/worker, capacity/checkpoint helpers and additive migration. Client closed path allowlist only `/api/analysis/jobs` and UUID job path; CSRF on POST, same-origin credentials, no-store, generation guards. PUBLIC build excludes private client chunks.

- At 4999/5000/5001ms forced ordinary-read latency, owner transition is deterministic; public has zero writes/private requests.
- Missing custom cache -> explicit owner submit -> queued -> running (>5s injected compute) -> succeeded -> exact cache-backed rank/compare read. Poll result fields never become fake daily fields.
- Fast archive read never queues. Unsupported adjustment, invalid symbols/oversized pool/session/result, or absent input cannot submit useful computation.
- Timeout/unknown POST retry uses same key; cache reuse across owners produces distinct isolated job records; changed command/sort handling preserves identity rules.
- Failure, budget breach, lease takeover and denial publish no partial result. Return/PUB change/late poll cannot overwrite current selection.
- Future performance acceptance must bind actual maximum supported inputs and 20-concurrent ordinary GETs to measured P95 <=2s. No such measurement is claimed here.

## 7. B7a: Deep Existing EX15 v1 Validation

### Alternatives

**A, recommended: strict mirrored typed validators at producer/cache/read/client boundaries, retaining six outer keys.** Closes malformed and selection-mismatch paths without inventing new success semantics. Later health/lineage additions are explicitly versioned.

**B: renderer best-effort parsing of object fields.** More tolerant but can turn corrupt results into empty "no trades"/"no health" displays and accept unrelated config/manifests. Reject for quantitative results.

### Exact Existing Result Contract To Enforce

`BacktestResult` v1 has exactly `schema_version,research_only,config_hash,snapshot_id,manifests,versions,metrics,equity,trades,rejections,quality,folds,walk_forward,baseline,ablation`. Schema value is `dragon-radar.research-backtest/v1`; do not confuse `schema_version` with `schema` or integer version.

Outer EX15 retains exactly six keys. Validate requested UUID equals `run_id`; `config_hash` is lowercase SHA256, equals expected frozen submitted config hash, and equals result hash. Status invariants: queued/running result/failure null; succeeded result nonnull/failure null; failed result null/failure object; cancelled result null and failure null or an authorized structured cancellation reason. `research_only` true at all relevant levels.

Current run is tied to `input_manifest.config`, not latest browser form, current publication, or loaded LOCAL portfolio. Add private read `GET /api/research/backtests/{run_id}/config` for authoritative reload/return context. Exact response `run_id,config_hash,config`; same initiating owner and archived-input authorization as EX15, no request-supplied owner. `config` is the existing complete BacktestConfig. Client closed whitelist adds only this UUID/config path. Without an authoritative or in-memory frozen config, numeric rendering is disabled as `config_context_unavailable`, not matched to today's form.

Hash using Python's existing canonical model JSON (defaults included, sorted keys, compact separators, ASCII escaping, finite exact Decimal strings). Browser must reproduce ASCII escaping/default normalization and Decimal-string representation with dedicated canonicalizer before SHA256, not generic unsorted `JSON.stringify` or JS Number rounding. Golden canonical bytes across both languages are an acceptance obligation. Idempotency key is not hashed.

`manifests` exactly matches frozen config domain IDs and adjustment/calendar/pool tokens. Market mandatory, 1..3 unique domains; compare canonical normalized domain sets, not current PUB's members. `versions` has exactly the existing RunVersions fields: strategy_id, strategy_method, factor_id, factor_metric, factor_window, factor_unit, sizing_method, execution_method, ledger_method, valuation_method, cost_method, cost_sensitivity_multipliers, adjustment, calendar, pool. Strategy/factor/version metadata must match config/manifests and supported method contract; unknown method versions are unavailable, not relabeled familiar formulas.

Metrics exactly: `initial_equity,final_equity,total_return,benchmark_return,excess_return,max_drawdown,turnover_rate,total_commission,total_stamp_tax,total_cost,unfillable_count_ratio,unfillable_amount_ratio`. Finite decimal strings at archived precision; no floats/bools/NaN/Infinity. Money two decimal places; ratios six; initial equity >0, final >=0; drawdown and unfillable ratios [0,1], costs/turnover nonnegative, return >=-1. Cross-check total return from equity at six-digit HALF_EVEN. Recompute excess from source-precision returns when available; with only rounded summaries require an error bound of one ratio quantum (`0.000001`), not false exact equality between differently rounded computations. `total_cost = total_commission + total_stamp_tax`, with sums matching fills; slippage is already reflected in fill price, not added twice. Existing `summarize_run` writes benchmark zero when benchmark input is absent: v1 cannot prove that zero was observed and must label benchmark/excess provenance unverified unless independent complete input evidence is available.

Equity points exactly `trade_date,equity`, strictly ascending unique valid ISO dates, nonnegative 2-decimal amounts. First/last values match Metrics initial/final; dates within actual archived period. Equity is monetary account equity, not a normalized price curve. Baseline initial cash is existing 100000.00 assumption, not a config field.

Fill exactly `security_id,side,quantity,signal_date,trade_date,raw_close,fill_price,gross_amount,commission,stamp_tax,net_cash_delta`. Side buy/sell; quantity positive integer, price positive finite (fill precision four decimal places), money two. Signal precedes trade; both within simulation bounds; cash delta sign and gross/fee identity match side. Do not assert every sell quantity is a 100-share multiple merely because buy sizing uses board lots. Rejection exactly `security_id,side,quantity,signal_date,trade_date,reason`, reason from existing closed nine-reason execution enum; no inference of actual broker orders.

Quality exactly `trade_date,factor_coverage,events`; coverage null or exact nonnegative expected/valid/excluded/missing partition. Events exactly `trade_date,code,security_id`, code from actual `QualityCode | DegradationCode`; validate parent/event dates and IDs against authoritative frame context when available. Quality is not a FactorHealthReport. Full historical identity validation needs B7b lineage; v1 missing frame identity evidence is disclosed rather than substituted by current pool. Arrays preserve kernel order; do not invent unique trade IDs or prohibit legitimate repeated fills.

Baseline exactly `status,metrics,cost_sensitivity`, completed; baseline metrics equal root. Sensitivity exactly three tiers low/base/high in that order, multipliers 0.5/1/2 and full Metrics each; base metrics equal root. Do not assume returns monotonically decrease with cost because changed sizing/fills can alter paths.

`folds`/`walk_forward` both null iff config walk_forward null. Otherwise validate existing report exactly: schema_version, research_only, layout, folds_count, train_sessions, test_sessions, regime_map, folds, aggregate_equity, aggregate_metrics. Layout `walk-forward-anchored-v1`, count/length match frozen spec. Fold fields exactly `fold_index,train_start,train_end,test_start,test_end,metrics,equity,trades,rejections,quality`.

Fold indices contiguous 0..F-1; shared train prefix; train precedes each test; test blocks tile ordered calendar sessions without overlaps/gaps. Root folds equal nested report folds. Validate regime map exactly against FrozenRegimeMap: five ordered phases ice/ebb/divergent/repair/climax, fit_method `equal-pool-mean-next-session-return-v1`, frozen train bounds/count/rebalance; observed/nonobserved status/count/mean coherent and weights [0.5,1], unobserved unit weight. Quality enums/phase presence match kernel. Training uses no next-session pair crossing train/test boundary.

Aggregate report simulates the whole OOS region as a continuous ledger. Its metrics are NOT an arithmetic average of fold returns, and its equity is NOT a concatenation of reset fold ledgers. Root baseline metrics are the separate whole-period unweighted baseline, not the OOS aggregate.

Ablation null iff AI variant not requested. V1 nonnull report exactly `schema_version,research_only,baseline,ai_variant`; legs exactly `leg,status,inputs,metrics,reason`. Inputs exactly `snapshot_id,input_manifests,period,cost_model,random_seed` and identical on both legs/config. Baseline completed with matching baseline metrics. AI leg ALWAYS disabled, metrics null, reason `real_evaluation_inputs_unavailable`; reject invented uplift/equity/trades/extra fields.

### Boundary Flow And Acceptance

Replace weak object fields with actual Metrics, FoldResult/WalkForwardReport/AblationReport types (avoid import cycles with a focused contract module). Repository success/cache validation must bind materialized result to persisted config BEFORE cache insertion; validate any authoritative preexisting cache BEFORE reuse. A differing result for the same deterministic key is a conflict/quality failure, not silently trusted first-writer success. No mutation or replacement of existing immutable cache.

GET validates same binding after owner/input authorization; malformed stored result fails closed with deliberate 503 DATA_UNAVAILABLE (requires explicit research route/client code mapping update), not storage error guessing. Client performs matching checks and produces a typed result or `malformed_response/selection_mismatch`. Renderer no longer silently casts bad arrays to empty.

Proposed files: new `research/result_contracts.py`, research engine/repository/routes, `web/src/features/research/{result-types.ts,result-validation.ts,config-canonical.ts,backtest-client.ts,BacktestPanel.tsx}`. Preserve API outer keys and existing worker ownership.

- Wrong run ID, hash, PUB, input manifest, method token, Decimal precision, status/result combination, extra numeric claim or malformed trade fails before numeric rendering/cache success.
- Valid empty trades/rejections is distinguishable from malformed/missing arrays.
- Current form/PUB change cannot rebind old result; authorized return/reload reads its exact frozen config.
- Fold overlaps, future training pair, reordered phases, root/nested fold mismatch, average-as-aggregate metrics and fabricated AI numbers are rejected.
- Same config/cache used by another owner never reveals the other run ID/config; revoked input blocks even cached success.
- All later verification is source/version-bound. Existing `tests/test_reset_backtest.py`, client/panel unit and research E2E are candidate focused suites, not newly executed proof.

## 8. B7b: Actual Health And Deep Frame Lineage

### Alternatives

**A, recommended: add a versioned materialized result extension and render full pure health plus frame manifests.** Strong lineage, no read-time statistical work; explicit legacy unavailable. Requires cache namespace/migration review.

**B: compute health/fold evidence in the browser or GET from quality coverage.** Cannot recover distributions/correlations or historical frame identity from counts, can change as current data moves, and violates materialize-on-publish/run. Reject.

### Exact Proposed v2 Additions

Result v2 uses separate `RESEARCH_RESULT_SCHEMA_VERSION = "dragon-radar.research-backtest-result/v2"`. Existing BacktestConfig request `schema_version` remains v1; do not accidentally change config hashing by reusing the same result/config constant.

V2 retains all v1 result fields and adds exactly `health,lineage`. `health` exactly `status,reason,report`; ready means report is actual existing FactorHealthReport and reason null; unavailable means report null and reason `factor_health_inputs_unavailable`. Worker computes health from same anchored frames and factor streams once; it cannot synthesize distributions from daily coverage.

`lineage` exactly `anchor_sequence,requested_period,actual_period,sessions,frames,benchmark,health_method,computed_at`. Periods exactly start/end; sessions sorted unique <=250. Frames exactly `trade_date,publication_id,publication_sequence,market_snapshot_id,topic_snapshot_id,role_snapshot_id,frame_sha256,visible_security_ids,as_of`. Nullable topic/role IDs remain null, not current versions; security IDs sorted unique <=5000 per frame, tied to frame digest. Sequence <=anchor, actual PUB IDs from repository/approved input bundle, not fictitious properties on PitFrame (which has sequence but NO publication_id). Frame mapping must be supplied by authorized loader with hash correspondence, or v2 cannot claim deep lineage.

Benchmark exactly `status,reason,definition_sha256,source_id,source_version,input_sha256`; ready requires all identity/hash fields and complete matching sessions; unavailable has null facts and `benchmark_inputs_unavailable`. No stock/market-average substitution. V2 quantitative excess/benchmark claims are withheld in presentation when benchmark unavailable; archived legacy numeric zero is explicitly labeled legacy/unverified, not actual flat benchmark. Future nullable metric representation requires its own result-contract revision; do not silently violate existing Metrics by inserting nulls.

`health_method` exactly `method_version,min_sample_size,quantile_points,rate_quantum,correlation_method`; initial values `factor-health-decimal34-v1`, 5, decimal strings 0/0.25/0.5/0.75/1, `"0.000001"`, `equal-session-cross-sectional-spearman-v1`. The pure report already contains `schema_version,research_only,anchor_sequence,window_start,window_end,sessions,primary_factor_id,manifests,factors,rank_correlations`. Do not invent IC/p-values/forecast accuracy in it.

Each actual FactorHealth has factor_id/metric/window/unit/sessions/coverage/sample_size/valid_rate/excluded_rate/missing_rate/quantiles/status/reasons. Sample equals coverage.valid; rates match counts at six digits. Quantiles points/values align and values monotone; insufficient sample has no estimates and nonempty reason. Expected zero currently yields zero rates in the kernel: label "no observations", not 0% valid market coverage. Correlation report has left/right/paired_sample_size/eligible_sessions/value/status/reasons; ready value [-1,1], insufficient null with actual reasons. Validate factor IDs unique, primary included, no self/duplicate correlation pair, same window/manifest/anchor.

UI splits "Daily factor coverage and quality" from "Factor health": valid/excluded/missing counts/rates, sample size, quantile distribution, actual cross-sectional rank correlation, methods/units and unavailable reasons. Display every actual quality event/rejection, not just coverage. Show run/config/PUB/domain IDs, frame sequence/digests and requested/actual period in expandable provenance. Show full fold metrics/quality/regime evidence alongside baseline/OOS distinction.

`computed_at` is actual worker completion sampled before persisted success, UTC-aware. GET never changes it. Legacy v1 has no new health/frame/completion claims; UI explicitly shows unavailable while still rendering fully validated v1 facts.

### Cache And Files

Current unique config_hash cache cannot safely mix v1/v2 outputs. Proposed additive cache contract key `(config_hash,result_schema_version,compute_contract_sha256)`; compute digest binds actual strategy/method/result/health contract versions and approved input-method descriptor. Update run/cache storage and immutable server manifest metadata through reviewed migration; preserve old rows/read path, do not overwrite or backfill old cache. Request idempotency replay returns its original version, never silently upgrades a completed run. Fresh command with a fresh key may request the deployment's current supported contract.

Version metadata is server-derived and frozen, not client-chosen arbitrary code. It does not waive production's actual code/evidence binding. Parent must approve migration/schema head coordination before implementation.

Files: research contracts/engine/health/worker/authorized frame loader/production adapter/repository/tables, additive migrations/schema contract, typed client/result panel and acceptance docs.

- <5 valid health samples: exact counts/rates remain; quantiles/correlation estimates null with reason.
- Valid full health has matching run/config/anchor/manifests and known method/units; tampered counts/rates/quantiles/correlation fail validation.
- Missing historical frame IDs/hashes cannot be replaced by latest PUB or current constituents; v2 output is unavailable/rejected rather than dishonest lineage.
- Added future revisions/constituents leave anchored earlier run bytes unchanged.
- Legacy v1 remains readable with explicit missing health/lineage; new cache version does not mutate it.
- Missing benchmark yields no claimed observed zero benchmark/excess advantage. Result view does not confuse equity money with normalized prices.

## 9. B7c: Bounded Factor, Phase, And AI Extensions

These are separate approval/execution slices, not implicit enablement by B7a/b. The shared recommendation is source-bound adapters plus approved descriptors. Alternative for each is generic inference from available market/model fields; that is faster to prototype but would invent semantics/permission and is rejected.

### Additional FAC Adapter

Approach A, recommended: one typed stream adapter per approved factor; approach B: compute all five enumerated factors opportunistically from whatever fields exist. A preserves independent formulas/units/PIT/coverage; B risks assuming enumeration equals approval.

Finite new descriptor `FactorAdapterApproval` exactly `factor_id,method_version,metric,window,unit,required_domains,formula_sha256,approval_evidence_sha256,input_schema`. Factor ID restricted to existing FAC-R01/R02/R03/C01/C02, but only an actually approved descriptor can enable it. R01 production path remains intact. Each new adapter outputs existing FactorFrame per exact decision session with archive/digest binding, explicit null/reasons and no substitution. Required domain missing/withdrawn -> `factor_adapter_unavailable`, no scores/returns. Do not specify new factor formulas without the approved method inputs; no new formulas are chosen in this draft.

Files: new `research/factor_adapters.py`, production `_factors` dispatch, authoritative input manifest, capabilities projection and UI controls. Acceptance: unknown/unapproved method blocked; single approved stream matches golden archived formula/units/required domains; late input excluded; no role/topic current-pool fallback; factor health reports only supplied approved streams.

### Actual Phase Adapter

Approach A, recommended: verify actual archived EX16 phase publications and then emit existing PhasePublication kernel inputs; approach B: classify phase from today's scores/calendar or accept unlabeled arbitrary arrays. A preserves sequence visibility; B contaminates training and misrepresents missing states.

Finite `PhaseSeriesArchive` exact fields `schema:"dragon-radar.research-phase-input/v1",method_version,approval_evidence_sha256,anchor_sequence,entries`. Entry exactly `publication_id,publication_sequence,trade_date,available_at,phase,sentiment_method_version,input_sha256`. Phase one of actual five kernel names or null; null excluded from kernel stream but disclosed as unavailable. Verify archived EX16 score/phase/materialization, original PUB sequence/cutoff, method and grants before reducing to `PhasePublication(trade_date,publication_sequence,phase)`; do not add imaginary fields to that existing type.

Enabled only with approved phase-series descriptor and actual input. Preserve existing anchored shared train prefix, train-only fit, five-phase de-risk formula `clamp(1+mean_next_session_return,0.5,1)`. Missing/unobserved phases within an otherwise approved series retain existing unit-weight quality events; wholly missing/unapproved series leaves walk-forward unavailable, not a fake five-state report. Whether a different expanding/refitted rolling layout is desired is a genuine product choice; recommend retain `walk-forward-anchored-v1` here, require a separately approved algorithm version for a different layout.

Files: new `research/phase_adapter.py`, production input validator/authorized loader, capabilities, walk-forward result validation/display. Acceptance: future/late sequence cannot reach earlier fold; terminal train pair never touches test; tests tile real sessions; missing phase/unit fallback disclosed; reject unapproved adapter, no current-phase rebasing.

### Genuine AI Comparison Adapter

Approach A, recommended: separately evaluated, PIT-frozen security scoring overlay with identical controlled experiment inputs; approach B: convert summarize/explain prose or today's AI output directly into historical scores. A is reviewable and measurable; B fabricates a numeric/PIT factor and gain.

V1 remains disabled and structurally cannot contain an AI return. A real adapter requires actual evaluated samples (>=100 unique human-labeled items with development/frozen separation), fact support >=95%, numeric/recompute 100%, zero tolerated hard failures, permitted provider/processing/use, independent release/rollback approval and actual PIT scoring inputs. A signed evaluation alone is not a score formula.

Future `AIScoreAdapterApproval` exact fields `adapter_id,method_version,base_factor_id,score_unit,score_formula_sha256,evaluation_manifest_sha256,approval_evidence_sha256,model_version,prompt_version,retrieval_version`. `AIScoreArchive` exact fields `schema:"dragon-radar.research-ai-scores/v1",adapter_id,method_version,entries`; entry exactly `trade_date,security_id,base_factor_input_sha256,score,unit,available_at,source_evidence_sha256,model_version,prompt_version,retrieval_version,reason`. Score finite exact Decimal/null; numeric score requires null reason, null score requires nonblank approved missing-reason token; availability <= actual decision cutoff, correct identity/session, attested approved scoring formula.

Do not define blend weights or transform prose into a score in this document. Conservative initial supported adapter is an independently approved replacement ordering stream for the existing factor Top-N strategy, with same pool, dates, costs, execution/sizing, seed, benchmark and missing-value policy. Approval must specify exact score ordering/formula and applicability; without it, adapter stays disabled, not implementation-complete. Preserve base and overlay inputs/hashes, no undocumented blend.

A completed AI leg needs a NEW ablation schema `dragon-radar.research-ablation/v2`; existing v1 validator mandates disabled. V2 report exactly `schema_version,research_only,baseline,ai_variant,controls,uplift`. Each leg includes `leg,status,inputs,metrics,reason,equity,trades,rejections,quality,input_sha256`; disabled leg all numeric/artifact fields null with structured reason. Controls exactly `snapshot_id,input_manifests,period,cost_model,execution_model,random_seed,benchmark_definition_sha256,pool_sha256`. Controls shared exactly; AI input digest/version is separate, not pretending both factor streams identical. Uplift exactly `total_return_difference,excess_return_difference,max_drawdown_difference`, computed at source precision only when both completed. This is descriptive simulation comparison, no statistical significance/profitability claim.

Files: new `research/ai_score_adapter.py`, versioned ablation contracts/engine, production gates/capabilities and typed comparison panel. No model call during historical run or GET; no LOCAL note/portfolio access. Missing approved score/evaluation/PIT inputs returns disabled with reason and no gain numbers.

Acceptance: frozen same-control baseline/AI streams deterministically reproduce own artifacts/differences; any pool/cost/seed/benchmark/time mismatch rejects comparison; unsupported prose score fails; null overlay stays missing without filling from future outputs; missing approval remains disabled; old v1 cannot accept completed AI legs.

### Capabilities And Honest Disabled State

New owner-only `GET /api/research/capabilities?snapshot_id=PUB&start=YYYY-MM-DD&end=YYYY-MM-DD`, all three parameters required, unknown/duplicate rejected. Validate period with existing BacktestPeriod bounds and actual anchored sessions. Exact response `snapshot_id,period,factors,walk_forward,ai_variant`, period exactly start/end matching request. Each capability exactly `id,status,reason,method_version,approval_evidence_sha256`, status ready/disabled. Ready requires null reason and known approved version/evidence; disabled has an explicit reason and nullable metadata. Current-plus-archived auth precedes read. Ready requires actual approved descriptor AND required frozen inputs for this PUB/window; capability is explanatory, not permission to skip submission/worker checks. No supplied inputs -> all affected extensions disabled, current narrow FAC-R01 adapter reported only to its actual applicable scope.

The window-specific worker remains authoritative; capabilities cannot promise readiness for every requested period. Reason set: `approved_method_unavailable,required_domain_unavailable,phase_series_unavailable,real_evaluation_inputs_unavailable,ai_score_inputs_unavailable`. Never advertise existing enumerated factor IDs or pure-kernel support as genuine production availability.

### Exact External Enabling Packages

| Slice | Required actual package before enabling | State without package |
| --- | --- | --- |
| Additional FAC | Each factor's independently approved formula/method descriptor with rounding, input fields/units/window/missing policy; applicable source-use grants; canonical actual factor streams and golden expected outputs; historical identity/calendar/adjustment versions; original per-decision availability and source hashes; independent approval evidence | `approved_method_unavailable` or `required_domain_unavailable`; adapter boundary can be engineered, computation is not approved/completed |
| Phase | Approved actual EX16 method and phase-series adapter; real archived PUB ID/sequence/date/cutoff/availability per entry; complete underlying phase derivation inputs and hashes; explicit missing-phase policy; source grants; independent approval of existing five-phase de-risk fit for selected scope | `phase_series_unavailable`/`approved_method_unavailable`; do not enable by generating five labels or an empty approved-looking series |
| AI gain | Actual provider/output/source AI-use rights; frozen model/prompt/retrieval; >=100 unique genuine labeled evaluation inputs/outputs and split/approval/rollback evidence; approved explicit numeric scoring/ordering formula including null/tie policy; original PIT per-security/session score availability; immutable baseline/AI streams; exact equal control pool/window/benchmark/cost/execution/seed manifests | `real_evaluation_inputs_unavailable` or `ai_score_inputs_unavailable`; AI leg stays disabled/null, no genuine gain completion |

These are enabling inputs, not instructions to manufacture approval files. Hashes/signatures must resolve under the existing trust policy to actual materials and independently approved applicable methods. A synthetically populated descriptor exercises the boundary only. Real packages still require their own specific method review before a computation adapter can be finalized; this document does not pretend the missing formula is a generic future coding task.

## 10. Decisions, Genuine Inputs, And Sequencing

Recommended decisions for approval are concrete:

1. Code-point first-100 preview; separately eligible summarize artifact; licensed summary never masquerades as AI.
2. Append-only clock evidence/domain archive and dedicated metadata GETs; preserve specialized envelopes and legacy unknowns.
3. Offline two-archive daily sample v1; whole-pool comparability; first-100-ID deterministic selection; default ZERO numeric tolerance. Nonzero supplier tolerances require actual approved precision evidence.
4. Keep fixed GETs read-only; explicit owner queued custom compute; five-second eligible UI transition with compute consent; public no writes. Adopt proposed finite analysis caps/budget only after parent integration review.
5. Strict v1 result validation first; separate v2 full health/frame lineage with versioned cache; additional FAC/phase/AI each individually gated. Retain existing anchored fold layout.

Genuine unanswered choices are not generic placeholders:

- Actual supplier/upstream independence, units/adjustment/benchmark precision contracts, generated-text rights and archived receipt proof have not been supplied. Recommend fail-closed unknown/blocked; do not choose invented provider facts.
- Additional FAC formulas and the AI scoring/ordering method have no approved descriptor in inspected inputs. Recommend keep unavailable until independent method approval. This spec defines adapter contracts but cannot truthfully select an unprovided formula or claim that gain leg is done.
- A different walk-forward layout would be a product/method change. Recommend no change to existing anchored shared-prefix layout.
- Proposed clock/cache/budget migrations and admission caps need parent approval/coordination with shared worktree/schema ownership. Recommend additive migration/new cache namespace, never rewriting old results.

Implementation order after review: B4 list/legacy safety -> B4 clock archive/read/display -> B5 CLI -> B6 parent-coordinated handoff/capacity -> B7a deep v1 -> B7b v2 health/lineage -> individually approved B7c adapters. Independent slices may be planned separately, but this child creates no subagents.

Future acceptance scales to each slice: archive/wire/model validation, private/public permission boundaries, deterministic CLI/kernel invariants, client generation/lifecycle, and targeted browser assertions. Run the named existing suites only for applicable assertions and add missing acceptance cases under the authorized implementation workflow. One final source-bound integration gate is a later parent task; old audit/C5 counts are not rebound to new code.

Completion accounting must keep engineering implemented/verified distinct from actual supplier/official-rule/model/sharing/shadow-run gates. No design, synthetic sample, disabled branch, or proposed adapter closes those real-input gates.

## 11. Spec Self-Review

Read-only self-review checked source names/shapes, existing versus proposed fields, no sixth daily domain, no daily owner envelopes, Unicode preview semantics, clock immutability/unknowns, deterministic sample identity/tolerance, async consent/public isolation, cache/config versioning, actual fee formula, fold aggregate semantics, and explicit externally blocked method/model inputs. Schema lists above are final for the proposed boundaries; there are no undecided method formulas concealed as implementation instructions.

This is design review evidence only. It does not claim executable schema validation, tests, implementation, permission approval, or sibling-design acceptance. Parent review keeps B7c external enabling blocked until the listed actual packages are approved.

## 12. Parent Integration Review

The parent corrected summarize input eligibility to the existing article-only shape: absence of notes_context is valid, private added context is forbidden. Promotion authority is now explicit authenticated owner POST, shared with AI1; local CLI selection never substitutes for owner authentication or reads arbitrary tasks. Summary owner withdrawal mirrors AI1 at POST `/api/ai/task/{task_id}/published-summary/{summary_id}/revoke`, body exactly `expected_summary_revision,idempotency_key,reason_code`, with current owner/CSRF/exact private linkage and an append-only audit. News reads clear the derived preview when its summary is revoked while independently permitted article metadata remains usable.

Migration ownership is sequential and additive: `0015_daily_ai` owns AI1 and the shared output entitlement/promoted-summary registries; `0016_clock_lineage` owns input evidence and publication clock metadata; `0017_analysis_limits` owns frozen analysis budgets; `0018_research_contract` owns versioned research run/cache metadata. Each predecessor is the actual preceding migration, each synchronizes release manifest/schema contract, and no earlier migration is edited. IH1's optional JSON block itself has no migration. These are proposed names/order, not executed database changes.

Register `/api/snapshots/lineage` before the existing dynamic `/api/snapshots/{snapshot_id}` matcher and add only the intended GET to the public allowlist/gateway. Keep RT/LHB clock endpoints on their existing feature and authorization boundaries; public availability never follows merely from a new route declaration. Owner summary/run-config/capability clients remain private dynamically loaded modules.

The proposed analysis caps are selected design values requiring approval, not observed capacity measurements: 250 sessions, 5000 eligible securities, 1250000 observations, 16 MiB result, 20 nonterminal jobs per owner, 300000 ms cooperative compute budget. Existing P95 <=2.0s remains unchanged and must be measured after implementation.
