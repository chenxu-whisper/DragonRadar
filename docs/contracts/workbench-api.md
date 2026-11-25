# Workbench API Contract

Frozen 2026-09-20 for Task01. Normative companion to [PRD](../DragonRadar_开发需求PRD.md) and the historical DEC01-12 decisions (available in Git history). This document specifies behavior, not implementation or test results.

## Application And Selection

`src/dragon_radar/http/app.py` exports `create_workbench_app(repo) -> FastAPI`. The repository is injected explicitly; importing the module must not open a database, start ingestion, load credentials, or mutate legacy state. Tests inject a deterministic repository. Keep `src/dragon_radar/api.py` and its existing application/envelopes for the current frontend until its migration. Never silently change the legacy API's JSON types.

Daily publication HTTP routes share one selection adapter and envelope:
`trade_date, as_of, snapshot_id, source, coverage, quality_flags, rule_version, basis, data`.
The repository boundary exposes published selection, authorized domain reads and a trading calendar; route code must not recompute publication analytics.

| Input | Contract |
|---|---|
| date + optional snapshot_id | Explicit ID pins an immutable published manifest; mismatch with an authorized manifest's trade_date is 400 INVALID_PARAM. Never fall back to latest for a bad ID |
| date without ID | Resolve latest authorized published manifest once; response ID must be carried by subsequent navigation |
| missing date publication without ID | 200, data:null, quality_flags includes no_data; snapshot_id null when unresolved, requested date retained; unknown coverage uses null counts, not invented zero observations |
| explicit unknown publication ID, with or without date | 404 NOT_FOUND, message "Resource not found"; neither no_data nor latest-publication fallback, even when an authorized publication exists for the requested date |
| known but unlicensed/revoked publication | Explicit ID returns 403, no protected payload; repository authorization errors propagate before date comparison, and permission is checked again on cache hit |
| original endpoints12-14 | Optional date and snapshot_id; selection date is end adjusted by repository trading calendar. Explicit date must equal adjusted end and selected manifest date; start rolls forward, end backward, no earlier price substitution |
| endpoint17 | Snapshot discovery list, not an implicit latest-version data read |
| endpoint18 | q-only basic security lookup retained; date/snapshot_id required together with historical evidence lookup context |
| invalid params | 400 including more than five comparison symbols, malformed dates, reversed window; invalid selectors are rejected before storage access |
| entity absent within selected publication | 404; differs from a date-only missing publication's 200 no_data |

Explicit unknown-ID responses change from the previous 200/no_data behavior to
404 `NOT_FOUND`. Selection performs the exact ID read only; it does not retry
by date, substitute a latest publication, or recompute analytics. Date-only
selection and implicit `through_date` window selection retain their existing
semantics.

Client URL date normalization after reading an authorized publication summary
is distinct from HTTP selection: a request supplying an authorized ID and a
mismatched date still returns 400; URL normalization cannot turn an unknown ID
into date-only fallback.

Errors use `{"code":"INVALID_PARAM","message":"..."}`, with 403/FORBIDDEN_GUEST or FORBIDDEN, 404/NOT_FOUND, 409/VERSION_CONFLICT, 429/RATE_LIMITED, 502/UPSTREAM_ERROR as appropriate. No tokens, raw upstream bodies or stack traces. AI/analysis/research task IDs are authorized against the initiating server-side principal, never local_profile_id. Public app contains no private task handlers.

## Coverage And Decimal

Map existing domain `legal_excluded` to wire `excluded` only in the new adapter. For known counts require nonnegative integers (not bool), `expected = valid + excluded + missing`; unknown state belongs to missing, never reduces expected. Unknown entire pool is explicitly null and flagged, not treated as complete. Different metrics may have different pools; attach their own coverage rather than reusing the market denominator.

Keep Decimal end-to-end through quantization and response encoding. `simplejson.dumps(payload, use_decimal=True, allow_nan=False)` in a custom Response is the planned encoding boundary; do not run FastAPI's default float conversion first. Numeric JSON tokens are mandatory for existing numeric fields. A regression must assert the raw token `9007199254740993.01` and parse it with `json.loads(..., parse_float=Decimal)`.

`basis` declares `price_type, currency, unit`, applicable benchmark, source/adjustment version and field precision/rounding (ROUND_HALF_UP unless the source contract explicitly requires another policy). Ratios are fractions, not percentages; seal/promote rates use four decimal places. Source price/amount precision is authoritative, not silently truncated to a universal two decimals. Reject NaN/Infinity as unavailable data. Optional evidence `{decimal_value: "9007199254740993.01", scale: 2}` retains exact audit values beyond JS precision; browser formatting is not an authoritative calculation.

## Heatmap

Endpoint04 remains date x board-count with each cell `{trade_date, boards, count}` and columns as board levels, not concepts. Heatmap X=date/Y=boards and ladder row=date/column=boards are transposes of the same counts. For equal manifest/date/pool/filter, each count equals the corresponding ladder stock-list length; cell sum equals ladder total and overview limit_up_count. No-data dates are absent, not synthetic zeros. Concept return/rotation is EX11, not a replacement for endpoint04.

## Task04 Local Repository

Original 26-29 remain `listNotes`, `putNote`, `listWatchlist`, `putWatchlist`, called locally, never HTTP. No `/api/notes` or `/api/watchlist` upload is added to the new app. Legacy routes remain only for explicit migration compatibility until Task06 retirement; the new frontend must not call them.

`getOrCreateLocalProfile()` stores a stable random UUID in IndexedDB profiles in an atomic transaction. Concurrent first-open tabs return the same default profile. Browser reopen, owner logout and owner_session renewal must not rotate it. A local profile is neither authentication nor authorization. Public and private deployments use distinct origins; public builds never open the private profile.

```typescript
type NotePayload = {
  title: string; content: string; judgment: string | null;
  evidence: string | null; invalid_condition: string | null;
  verify_date: string | null; snapshot_id: string;
};
type NoteRecord = NotePayload & {
  local_profile_id: string; note_id: string; revision: number;
  content_hash: string; idempotency_key: string; updated_at: string;
};
type PutNoteCommand = {
  note_id: string; revision: number; // expected revision; 0 creates revision 1
  idempotency_key: string; payload: NotePayload;
};
```

Title length1-100, content<=20000, nullable judgment/evidence/invalid_condition preserved (not mapped to missing fields); verify_date is null or valid YYYY-MM-DD. Snapshot is required. Repository instance scopes `local_profile_id` implicitly. Keys: notes `[local_profile_id,note_id]`; revisions `[local_profile_id,note_id,revision]`; watchlist `[local_profile_id,security_id]`; commands `[local_profile_id,idempotency_key]`. Removal keeps a revisioned tombstone for conflict detection. `listNotes({snapshot_id?})`, `putNote(command)`, `listWatchlist()`, `putWatchlist({security_id, action, idempotency_key, revision?, context?})` return promises.

Canonical SHA-256 payload hashing covers all NotePayload fields, operation, target and expected revision for command identity; content_hash covers complete NotePayload. Compute before opening IDB transaction. In one readwrite transaction read the current revision and command, validate, write record/revision/command result; attach complete/abort listeners before requests. Never await network or crypto inside the live transaction.

Same key/same digest returns the original committed result without another revision; same key/different digest raises local VERSION_CONFLICT (S07, not an HTTP response). Stale revision fails without overwriting. After complete, a separate readonly transaction verifies key/revision/content_hash; only matching readback yields `commit:"committed", readback_verified:true` and S06. Failed commit/readback retains editor input. If readback already sees a higher revision, show conflict and current version; do not describe the original result as latest. Quota/abort/closed DB must not create partial revision or command records.

BroadcastChannel `dr-sync` carries `{store,key,revision}` only, never note text. Receiving tabs reload from IDB. Backup/restore is explicit local JSON, no remote synchronization. AI HTTP requests may carry only separately confirmed selected text; adopting AI drafts requires a user action through this same local repository.

## Engineering Extensions

The original PRD 29 numbers and examples remain intact. All entries below are separate contracts, not replacement counts. GET extensions follow their domain's frozen identity and envelope; the common daily envelope does not override specialization. PUB reads require selected immutable daily publications and sharing rights for public access; intraday and disclosure reads require their own current and archived display/permission gates. Owner job/run reads remain private. All POST endpoints are owner-only with CSRF, bounded input, idempotency and principal isolation.

| ID | Method / interface | Request and response contract | Task / production gate |
|---|---|---|---|
| EX01 | POST /api/analysis/jobs | snapshot_id,start,end,benchmark,adj,symbols<=5,idempotency_key -> job_id,status; no arbitrary SQL | 13; licensed fixed price history |
| EX02 | GET /api/analysis/jobs/{job_id} | owner principal -> status,result or structured failure; immutable input manifest | 13; same as EX01 |
| EX03 | POST /api/news/{article_id}/associations/review | association_id,decision=accept/reject,expected_revision,idempotency_key -> revision,audit_id; effective only after new topic publication | 14; source association evidence |
| EX04 | GET /api/realtime/quotes | security_ids<=100,as_of? -> quote_batch_id,source_event_at,received_at,delay_ms,status,quotes | 21; realtime display permission and measured latency |
| EX05 | GET /api/realtime/minutes | security_id,date -> minute bars with batch_id,event_at,available_at,OHLC,volume,amount,revision,final; no gap fill | 21; minute input permission |
| EX06 | GET /api/realtime/anomalies | date,cursor?,limit1-100 -> events,next_cursor; event includes security_id,rule_version,batch_id,window,value,threshold,source time,quality | 21; transparent local rules and authorized inputs |
| EX07 | GET /api/market/sector-flow/intraday | sector,date -> source-defined net_inflow points and units; unavailable values null+reason | 21; defined net-flow fields, not turnover proxies |
| EX08 | GET /api/lhb | date,security_id?,page>=1,size1-100 -> disclosure_id,reason,seats,coverage,total | 22; disclosed source rows and licenses |
| EX09 | GET /api/lhb/seats/{seat_id} | date,days1-250 -> seat source identity, disclosures,buy_amount,sell_amount,net_amount; no person attribution | 22; seat mappings/version |
| EX10 | GET /api/news/wordcloud | date,top_n1-100,type? -> words[{term,document_count,article_ids,sentiment:null or research label}],tokenizer_version | 23; licensed text/title corpus |
| EX11 | GET /api/topics/rotation | date,days1-250,sector? -> events[{at,sector_id,core_ids,change,comparable,reason,input_manifest,snapshot_id=publication version id serving the event}],returns; history not current members | 24; historical topic/role versions |
| EX12 | POST /api/query/parse | text1-500,snapshot_id,idempotency_key -> query_id,typed_ast,explanation,unsupported; optional model parser cannot execute | 26; deterministic parser baseline, optional AI permission |
| EX13 | POST /api/query/execute | query_id,confirmed_ast_hash,snapshot_id,idempotency_key -> fields,rows<=100,total,coverage; rejects unapproved/changed AST | 26; authorized frozen read-model fields |
| EX14 | POST /api/research/backtests | snapshot_id,input_manifests,strategy_id,parameters,period,cost_model,execution_model,experiment,idempotency_key -> run_id,status | 27; PIT data and disclosed simulation assumptions |
| EX15 | GET /api/research/backtests/{run_id} | owner -> config_hash,manifests,metrics,trades,rejections,equity,folds,baseline,ablation,quality; research_only=true | 27; no actual broker orders |
| EX16 | GET /api/market/sentiment/research | date,days1-120 -> score0-100 or null,phase or null,components,method_version,coverage,research_only=true | 28; formula and historical input completeness |

### AM03 Domain Identity And Envelope Freeze

| GET class | EX IDs | Frozen identity / envelope |
| --- | --- | --- |
| Daily publication (PUB) | EX10, EX11, EX16 | Selected immutable `trade_date + snapshot_id`, authorized published manifest and daily coverage/basis/rule metadata. Rotation additionally binds each historical event's actual publication and input manifest, not today's members |
| Intraday | EX04, EX05, EX06, EX07 | Source-scoped batch/revision, source version, requested date where applicable and PIT `as_of` availability; not a daily publication pin |
| Disclosed source rows | EX08, EX09 | `date, as_of, source, source_version, quality_flags, basis, coverage, data`; source/disclosure `revision` and source-scoped seat `mapping_version`, not fabricated market snapshot/rule IDs |
| Owner job/run | EX02, EX15 | Initiating authenticated server principal plus immutable job/run and input identity; private status/result/failure records, not public daily envelopes or discovery |

EX04 accepts `security_ids<=100`, optional `as_of`, `source_id` and
`quote_batch_id`. An explicit quote batch requires `source_id`; its identity
is `source_id + quote_batch_id`, not a globally unique batch label. The
intraday envelope includes `source, source_version, as_of`, null
`snapshot_id`/`rule_version`, and EX04 `trade_date:null`. `data` retains
`quote_batch_id, source_event_at, received_at, delay_ms, status, quotes`.
Source event time and receipt time remain separate; measured delay is not
proof of live supplier latency or a substitute for source freshness.

EX05 accepts `security_id, date`, optional `as_of, source_id, batch_id`.
An explicit minute `batch_id` also requires `source_id`. Its envelope
`trade_date` is the requested trading date, with null daily snapshot/rule IDs.
Each bar preserves `batch_id, source_id, source_version, security_id,
event_at, received_at, available_at, open, high, low, close, volume, amount,
revision, final`. PIT selection requires BOTH parent batch and bar
`available_at <= as_of`. Apply the explicit source/batch restriction before
choosing the visible revision per source/security/event time. A later atomic
batch cannot expose an earlier-timestamped bar before the parent is available;
late corrections never change an earlier `as_of` read. Exact missing/unknown
batches return 404 without latest-batch or daily-publication fallback; denied
sources cannot be substituted. Unpinned reads retain authorized domain
selection, not an implicit daily pin. Missing bars remain absent, never
gap-filled; revision and finality remain disclosed.

EX06 retains source/rule/batch/window/value/threshold and availability
provenance with its frozen cursor chain. EX07 retains source-defined net
flow, batch/time/unit and explicit null reasons, never turnover proxies.
Their intraday envelopes do not acquire daily IDs. Stale data retains source
timestamps and is labeled delayed, never presented as live. Background
ingestion is separately authorized; GET never initiates subscription,
collection or recomputation. Full anomaly traversal and RT-01-RT-12 parity
remain acceptance obligations, not closed by this documentation freeze.

EX08/EX09 use the exact specialized top-level key set in the classification
table. EX08 `data` is `{items,total,page,size}`; EX09 `data` is
`{seat,identity_state,items,total}`. Disclosures bind `source_id,
source_version, disclosure_id, disclosure_date, revision` plus
`source_event_at, received_at, available_at`. The wire field is `revision`,
NOT `disclosure_revision`. Seat rows retain `seat_id, source_row_id,
source_seat_name, mapping_version`; history identity additionally retains
`source_id`. Same seat labels or IDs across different sources do not imply
the same identity, and names never imply a person, fund or holdings.
History contributions retain actual disclosure revisions and source rows;
mapping/name changes do not rewrite earlier disclosed evidence.

LHB `basis` has exactly `unit,currency`. Dedicated coverage has exactly
`total_disclosures, visible_disclosures, late_disclosures,
selected_disclosures, seat_count, complete_seats, unknown_seats`; it is not
the market `expected/valid/excluded/missing` pool. Visibility is bounded by
`as_of`, and unknown buy/sell/net amounts are not coerced to zero. EX08
top-level source/version/basis summarize CURRENT PAGE rows only; mixed values
are null rather than guessed uniform provenance. Empty results retain the
domain's no-data/null-basis semantics. Neither daily `snapshot_id` nor
`rule_version` is added to satisfy the common daily envelope.

EX02/EX15 derive the owner from authenticated authorization, never a
request-supplied principal or `local_profile_id`. Submission authorization,
CSRF and owner-scoped idempotency/replay occur in the same transaction;
authorization precedes replay/cache reads. Each GET checks the initiating
principal and currently authorized archived input, including on cache-backed
success. A shared compute cache does not share job/run access between owners.
Immutable identity includes ID, owner, idempotency key, request/cache or
config hash, snapshot, input manifest and creation time; lifecycle updates
do not mutate those inputs.

EX02's safe record is exactly `{job_id,status,result,failure,input}`; `input`
discloses snapshot, actual requested start/end, benchmark, adjustment,
symbols, algorithm and adjustment versions. EX15's safe record is exactly
`{run_id,status,config_hash,research_only,result,failure}`, with
`research_only:true`; manifests/metrics/trades/rejections/equity/folds/
baseline/ablation/quality belong inside the materialized `result`, not
invented top-level daily fields. Persisted research input is
`input_manifest.config` with the frozen config hash. Client EX15 validation
checks exact outer keys and nested result/failure object-or-null only;
it is NOT proof of nested metrics, manifest lineage or full result validity.
Missing browser asynchronous-job handoff, full research result lineage and
approved computation/AI-comparison inputs remain open.

### AM05 Public Template And Owner AI Boundary

Public overview daily content is the explicitly labeled deterministic facts
template drawn from the selected authorized publication, not generated AI or
a human-reviewed daily result. Owner AI requires explicit create, exact
task/publication/date/version reads, and explicit review of a selected draft
revision. Local note adoption is another explicit action, not publication.
Selected authorized text is the only separately confirmed AI input; neither
public reads nor template fallback discover or expose private owner tasks.

`status`, `verification_status` and `human_review_status` are independent:
task `succeeded` != numeric/source verification passed != human accepted.
`edited` is not accepted or verified; human edits create a revision and
re-enter verification. Deterministic content, processing/overdue state and
AI/human draft provenance must remain separately visible. Late provider
results cannot replace the selected human revision. A successful task alone
does not authorize display, adoption or published-result eligibility.
60s/120s timing and whole review/revocation workflows still require C evidence;
real provider quality and processing/display rights remain D/PC03 gates.

Two missing products require SEPARATE future product specifications:
index price-history archival (identity, authorized source, actual window,
versions and missing-state behavior), and public reviewed-daily-AI
archive/discovery (publication binding, storage, revision eligibility,
independent verification/human acceptance and sharing/revocation rights).
Neither is implemented by this freeze. Do not synthesize index history from
market/stock data or infer a public daily result from private jobs, known task
IDs, successful status or the deterministic market template.

Research endpoints never read private notes or simulation portfolios from the server. Simulation repositories are local IndexedDB `simulation_accounts`, `simulation_orders`, `simulation_fills`, `simulation_commands`, partitioned by local_profile_id; atomic command/revision semantics match Task04. Scenario rules are explicit research assumptions, not an assertion about current exchange rules.

## IG04–IG11 Interaction Contracts

These additions retain the original 29 interface numbers and EX01–EX16. They
do not imply production acceptance or change the migration head.

### Roles and archived history

Reset URLs accept canonical `roles` multi-selection and retain legacy `role`.
Selections use OR highlighting; each role keeps its own candidates, evidence
status, changes and coverage. Repeated securities are not counted as independent
observations, and role denominators are never added together.

`GET /api/limit-up/ladder` accepts bounded `days` (1–250). Historical cells
contain the actual selected historical publication's securities, date, rule
version and coverage. History resolution respects the original anchor's
publication watermark. Missing or unauthorized history is never substituted
with the current pool or a later historical revision. Anchor heatmap counts
and historical publication lists have separately disclosed provenance.

New market publications archive fixed-yesterday-pool feedback under
`continuity_detail_version: 1`. The continuity read parses this block only;
legacy materialization retains available summary values and explicitly marks
detail unavailable. Invalid version, membership, coverage or direction data
fails closed. Stock cards disclose actual returns, topic/role evidence, seal
times and their unavailable reasons without reading every stock separately.

### Local origin and manual verification

Watch records may preserve `context` with nullable `trade_date`,
`security_name`, `sector_id` and `sector_name`. Duplicate add, remove and undo
retain the original watch identity, publication, added time and context.
Legacy records resolve their own publication; an unresolved origin cannot use
the current publication. New context participates in command identity and
backup validation.

Explicit note confirmation appends a new immutable confirmed revision.
Verification records reference that revision and use manual outcomes
`supported`, `weakened`, `refuted` or `not_due`. Corrections append a record
with `supersedes`; they do not rewrite original judgments or market facts.
New records with an available evaluation require archived `fact_interval`
and `context` containing requested and actual interval bounds, publication
trade date, as-of time, source, source version and rule version. Requested
bounds are the confirmed note's trade date through its verification date,
including the original baseline. `snapshot_id` pins the evaluation publication.
Reopening displays saved provenance and facts. Legacy omitted facts remain
unknown. `not_due` has null snapshot/context/facts; unavailable or unauthorized
facts cannot automatically become a human conclusion. No subjective win rate
is inferred.

Reset-only `return_snapshot`, `return_date` and `return_sector` preserve
origin navigation. An explicitly empty `return_sector` differs from an absent
field. Opening historical evidence does not alter the root selection;
full-page and local-review returns restore the original selection and sector.

### Owner association review and citation location

`GET /api/news/{article_id}/associations/review-state?date=YYYY-MM-DD&snapshot_id=PUB`
requires a live owner session and authorized archived article. It binds the
article revision and each actual association ID, returns the association's
current review revision, and discloses only the caller's audit entries.
The existing EX03 command uses this association revision, independently of
article revision and AI task review. A 409 retains the user's decision until
explicit reread and reconfirmation; an unknown command outcome retries the
original idempotency key. Accepted/rejected audit and pending new topic
publication are distinct. Published members and associations remain immutable.

`GET /api/ai/task/{task_id}/evidence/{evidence_id}?revision=DRAFT_REVISION&snapshot_id=PUB`
requires the initiating owner, exact task/draft/publication binding, and a
manifest-backed citation. The server resolves an actual archived article
field, validates its identity and value, and returns only the referenced
authorized fragment and locator. Display permission is independent of AI
processing permission. Every read checks current and archived display grants
and withdrawal; revocation clears displayed fragments and late responses
cannot restore them. Old input with no resolvable article identity returns an
explicit unavailable reason rather than a guessed article or paragraph.
Both GET routes are confined to exact owner gateway paths and private chunks.

### Stock evidence and grouped search

Stock role evidence uses actual `roles[].sector_id` from the selected stock
publication. An unrelated entry sector is disclosed and cannot trigger an
evidence read; absent archived role topics remain unavailable. The selected
sector binds all six evidence groups. Formal event expansion reads the same
publication's regulation archive and filters actual events by security ID;
unknown completeness never implies zero. Stock, topic and search interactions
maintain one native modal and restore opener focus on close.

`GET /api/search?q=QUERY&date=YYYY-MM-DD&snapshot_id=PUB&page=1&size=50`
adds archived `securities`, `topics` and `messages` groups while retaining basic
q-only compatibility. The data shape is
`{query,page,size,total,groups,group_status}`; `groups` contains the three
named result lists, and `group_status` records their independent readiness,
total and unavailable reason. An unavailable group's total is null; a ready
group with no match has total zero. Paging applies once to the combined result
set, not separately to each group: securities by `security_id` ASC, then topics
by `sector_id` ASC, then messages by `article_id` ASC. The combined `total`
counts all available matching rows before slicing; it does not assert complete
coverage of unavailable groups. Size defaults to 50 and accepts 20/50/100.
Missing archives differ from zero matches. Search does not use a loaded command
index as a complete source. Clients bind query, page, size, publication and date
before enabling target navigation.

AM02's minimum is publication-archived stock, topic and news lookup, on the same
authorized PUB/date. Stock matches `security_id`, archived `code` or `name`;
topic matches actual `sector_id` or archived name; news matches `article_id` or
title. Returned `match_field` identifies the matched field. q-only basic lookup
remains compatible; historical lookup requires date and snapshot_id together.
Absent group archives have `status:"unavailable"`, `total:null` and a reason;
only a ready group can claim real zero matches. A combined zero with unknown
groups must not be labeled a complete no-result search. Present corrupt archives
fail the whole read rather than return apparently usable partial groups.

On denial/revocation clients clear protected results and target actions; closed,
superseded, malformed or selection-mismatched responses cannot restore them.
Choosing a security opens its actual stock detail, choosing a topic opens
`/api/topics/{sector_id}` detail, and choosing news navigates to the actual
archived article, always retaining PUB/date. Topic detail and role evidence use
real archived `sector_id`; analysis/news `topic_id`, names, current labels and
historical memberships are not guessed mappings. Missing legacy topic detail
remains explicitly unavailable, never reconstructed from today's members.

### AM04 List Applicability And Rank Compatibility

These Reset UI lists expose 20/50/100, default 50, and actual pre-page totals.
Paging a returned archive is not proof of complete source coverage. The matrix
records existing controls and exports, not a universal sort or export API.

| List | Actual total / paging | Existing fields, direction and missing values | Existing export scope |
| --- | --- | --- | --- |
| Overview members | Unique archived members after market/query filters; client slices current page | `security_id`, archived `code`, `name`, ASC/DESC; null/blank last in either direction; ties use `security_id` ASC | Current filtered, sorted page only, despite `overview-current-filter` scope name; PUB/basis and market/query metadata, visible page/count disclosure. CSV does not currently encode page/sort metadata |
| Role candidates | Each selected role's `candidates.length`; independent client pager | `rank`, `security_id`, `name`, `sector_id`, `boards`, ASC/DESC; null/undefined last; ties retain archive order. Initial order is archive order | Current candidate page, despite `leaders-current-filter` label; PUB/role/sector/date/page/size metadata. No sort metadata is currently added |
| Role changes | Each role's `role_changes.length`; separate client pager | `sector_id`, `security_id`, `name`, `rank`, `change_type`, ASC/DESC; null/undefined last; archive-order ties and initial order | No dedicated change-list CSV control |
| Topic members | API `total` counts all visible archived members; `known` is independent of source completeness; server slices | Fixed `security_id` ASC before paging, no name/code/direction controls; nullable names/codes remain unknown | No topic-member CSV control |
| News | API `total` after type/source/query/security filters; server slices | Fixed publication-availability-time order: archived `available_at` DESC, `article_id` ASC ties. `published_at` is separately displayed, not the current sort key; no bidirectional/null-last control | `news-current-page`, selected article page and PUB/filter/page/size metadata |
| Rank | Client query-filtered `ranking.length`, then current-page slice; not coverage.valid or expected | Four metric sorts below, each ASC/DESC; null last and stable `security_id` ASC ties in both directions. No identity/name/code sort controls | `analysis-current-page`, visible filtered page in actual response order/ranks; actual adjusted interval, query, request sort and effective descriptor direction, page/size/adjustment plus PUB/basis |
| Global search | Combined available-match `total`; one server page across three groups with independent group totals | Fixed group/ID order above; no user-sort controls; nullable stock names/codes retained, not made sortable | No global-search CSV control |
| Expanded stock history | Returned `points.length` in the selected actual window, including null gaps; client slices | `trade_date`, `open`, `high`, `low`, `close`, `volume`, `amount`, ASC/DESC; initial date ASC; null last; ties use date ASC | Existing `stock-current-filter` exports quote plus all returned raw-window points, NOT just the expanded table's page or its sort; PUB/security/adjusted window/adjustment metadata |

The UI supplies news size 50 explicitly. The existing bare `/api/news` default
is 20 and accepts size 1..100; topic-detail API also accepts size 1..100
(default 50). These HTTP compatibility ranges are not new universal UI options.
Rank paging/filtering is client-side: `/api/rank/range` does not accept
page/size. It accepts optional `order` as described below. Null data and null numerical observations remain unavailable,
not zero; security ID is distinct from archived code even where an existing
heading says code. Existing basis/units accompany exports; browser formatting
does not replace source precision.

Rank compatibility is fixed by backend `_SORT_FIELDS` and frontend
`analysisSortDescriptor`:

| API sort | Archived metric | Default Direction |
| --- | --- | --- |
| `return` (default) | `return_ratio` | DESC |
| `excess_return` | `excess_return_ratio` | DESC |
| `amount_change` | `amount_change_ratio` | DESC |
| `drawdown` | `max_drawdown_ratio` | ASC |

`/api/rank/range` accepts optional `order` with exact lowercase values `asc`
and `desc`. Omission preserves the metric defaults above. Empty, uppercase,
invalid and duplicate `order`, including identical duplicates, are rejected
with the existing invalid-request response before storage access. Unknown
query keys remain rejected. `/api/compare/curves` does not accept `order`.

Equal metric values retain security-ID ASC order and equal competition rank
(for example `1,1,3`) in both directions; a missing sort value is last with
null rank, not a valid zero metric. Ranking is a read projection on row copies:
archives, custom-calculation cache identity, algorithm versions, exclusions,
coverage and window/basis identity do not change with direction. Frontend
`interval_return`, `excess_return`, `amount_change` (legacy `amount` alias) and
`maximum_drawdown` map through that descriptor; unsupported UI sorts default
to return DESC when no explicit ranking direction is present.

Reset page URLs use optional `rank_order=asc|desc`, independent of the legacy
global `order`. Old URLs without `rank_order`, even with explicit global
`order=asc` or `order=desc`, retain the metric defaults. Invalid or duplicate
`rank_order` is removed with the existing invalid-URL notice. Omitted direction
is not serialized; explicit direction survives refresh, history, pagination,
and stock-details return. Non-Reset pages do not interpret or serialize it.

The active metric header toggles effective direction and resets page 1.
Selecting a new metric clears `rank_order`, uses its default, and resets page 1;
other research conditions are retained. One effective descriptor drives the
API request `order`, response-order validation, header `aria-sort` and next-action
direction title, and CSV metadata. CSV exports only the visible page in actual
response order with its competition ranks, without local renumbering. A changed
request path invalidates displayed old rows and their export; a late superseded
response cannot supply rows under new direction metadata.

The local implementation receipt is
[ranking-direction-2026-11-24.md](../acceptance/ranking-direction-2026-11-24.md).
It proves the bounded ranking-direction behavior with synthetic tests, not a
rerun of C2/C5 or closure of all REQ-V03 clauses.

Bounded exceptions are separate from the paged lists: finite date/board-count
heatmap and ladder matrices; the fixed six role-evidence groups; small metric
summaries; and small related stock/topic/news association previews. They are
not full-list coverage, a way to truncate the applicable lists above, or a
promise of generic sort/export controls. C retains whole-list identity/unit,
sticky-column, decoded-export and interaction acceptance checks; D retains
actual archive and independent display/export rights.

## Stage1 Archived Topic Details

`GET /api/topics/{sector_id}?snapshot_id=UNIFIED_PUBLICATION&date=YYYY-MM-DD&page=1&size=50`
uses existing authorized publication selection. A date or publication ID is required;
when both are supplied their dates must match. Page defaults to 1 (range
1..2147483647); size defaults to 50 (range 1..100). Duplicate/unknown query keys
and invalid bounds return 400 `INVALID_PARAM`. `/api/topics/rotation` remains the
separate historical route and is never treated as a sector ID.

The shared HTTP envelope always uses the unified publication `snapshot_id`.
`data` has exactly these fields:

```typescript
type ArchivedTopicDetail = {
  sector: ResetSector; // Same full aggregate archived for sector-flow/overview.
  topic_basis: {
    source: string; source_version: string;
    as_of: string; source_time: string | null; cutoff: string;
    market_snapshot_id: string; topic_snapshot_id: string;
    taxonomy_version: string; membership_version: string;
  };
  definition: {
    effective_from: string; effective_to: string | null;
    available_at: string; source_reference: string;
  };
  membership_coverage: {
    expected: number | null; received: number | null; known: number;
    status: "complete" | "unavailable"; reason: string | null;
    complete_as_of: string | null;
  };
  members: Array<{
    security_id: string; code: string | null; name: string | null;
    effective_from: string; effective_to: string | null;
    available_at: string; evidence_refs: string[];
  }>;
  total: number; page: number; size: number;
};
```

Dates are ISO calendar dates; timestamps are timezone-aware ISO timestamps.
Descriptor snapshot IDs are the actual archived market/topic context-row IDs,
not unified publication IDs. The detail is frozen in the market publication
payload's optional `topic_details` block (`schema: "dragon-radar.topic-details"`,
`schema_version: 1`). No migration or read-time reconstruction is required.

Definitions and memberships must be available at or before
`min(market.as_of, topic.as_of)` and effective on the selected market trade date,
using `[effective_from, effective_to)` intervals. Membership is independent of
return/role readiness. Identities must match the visible archived market identity
list; unavailable, mismatched, or unknown identities remain null, never fall back
to the security ID as a name. Members are ordered by security ID before pagination;
`total` and coverage `known` count the entire visible archived subset, not the page.

Missing coverage has `expected:null`, `received:null`, `complete_as_of:null`,
`status:"unavailable"`, and `reason:"topic_coverage_unavailable"`; known members
remain visible without claiming a complete set. A visible unavailable coverage cell
preserves its reported expected/received counts, completion timestamp, and reason.
`complete` requires `expected = received = known`, with a visible completion
timestamp and null reason. Aggregate return coverage remains distinct from
membership coverage.

A selected publication without the optional block, including old/no-topic
archives, returns 200 with `data:null` and `materialization_unavailable`. An
unresolved date-only publication returns the existing `no_data` envelope; an
explicit unknown publication ID returns 404 `NOT_FOUND`. A valid archive
with an unknown sector returns 404 `NOT_FOUND`; date mismatch returns 400;
revoked/unlicensed reads return 403. A present but corrupt block returns 503
`DATA_UNAVAILABLE`, including schema, fact visibility, ID binding, identity, and
count inconsistencies. GET only validates and slices the archive, never rebuilds
it or loads live topic inputs. Only this GET is added to the public read allowlist;
no topic history, management, ingestion, index, or AI endpoint is added.

The owner Reset read allowlist mounts this same GET behind its existing owner
gate. Taxonomy/membership versions are independently frozen in the topic
publication result and checked against the detail descriptor; the reader does not
trust self-reported detail versions as its validation baseline.

### Research result deployment integration (2026-11-24)

New owner backtest admissions and the authenticated worker loader use the same
server-only `DRAGON_RESEARCH_RESULT_VERSION` setting, default `v2`.
Only `v1` and `v2` are accepted; unknown values fail closed. Explicit `v1`
preserves the legacy deployment option. Direct repository/compute constructors
retain their existing `v1` defaults for explicit legacy execution and tests.
The owner API and worker processes must receive the same setting. Existing
idempotency receipts and cached/run artifacts keep their frozen schema and
compute contract; changing this setting does not upgrade old queued runs.
A mismatched worker cannot complete a run under another frozen result contract.
V2 adds archived health and exact frame lineage; missing approved factors,
phase/AI inputs and benchmark identity remain unavailable or disabled.
