# Workbench API Contract

Frozen 2026-09-20 for Task01. Normative companion to [PRD](../DragonRadar_开发需求PRD.md) and the historical DEC01-12 decisions (available in Git history). This document specifies behavior, not implementation or test results.

## Application And Selection

`src/dragon_radar/http/app.py` exports `create_workbench_app(repo) -> FastAPI`. The repository is injected explicitly; importing the module must not open a database, start ingestion, load credentials, or mutate legacy state. Tests inject a deterministic repository. Keep `src/dragon_radar/api.py` and its existing application/envelopes for the current frontend until its migration. Never silently change the legacy API's JSON types.

New HTTP routes share one selection adapter and envelope:
`trade_date, as_of, snapshot_id, source, coverage, quality_flags, rule_version, basis, data`.
The repository boundary exposes published selection, authorized domain reads and a trading calendar; route code must not recompute publication analytics.

| Input | Contract |
|---|---|
| date + optional snapshot_id | Explicit ID pins an immutable published manifest; mismatch with its trade_date is 400 INVALID_PARAM. Never fall back to latest for a bad ID |
| date without ID | Resolve latest authorized published manifest once; response ID must be carried by subsequent navigation |
| missing date publication / unknown publication ID | 200, data:null, quality_flags includes no_data; snapshot_id null when unresolved, requested date retained; unknown coverage uses null counts, not invented zero observations |
| known but unlicensed/revoked publication | 403, no protected payload; check permission again on cache hit |
| original endpoints12-14 | Optional date and snapshot_id; selection date is end adjusted by repository trading calendar. Explicit date must equal adjusted end and selected manifest date; start rolls forward, end backward, no earlier price substitution |
| endpoint17 | Snapshot discovery list, not an implicit latest-version data read |
| endpoint18 | q-only basic security lookup retained; date/snapshot_id required together with historical evidence lookup context |
| invalid params | 400 including more than five comparison symbols, malformed dates, reversed window |
| entity absent within selected publication | 404; differs from missing publication |

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

The original PRD 29 numbers and examples remain intact. All entries below are separate contracts, not replacement counts. GET extensions return the shared envelope and accept date/snapshot_id unless task-scoped. Public access requires an already-published, sharing-licensed result; all POST endpoints are owner-only with CSRF, bounded input, idempotency and principal isolation.

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

Realtime outputs additionally bind quote_batch_id/minute revision because a daily snapshot alone cannot pin intraday state. Exact batch selection never substitutes a newer batch; as_of forbids later available_at. Stale data retains source timestamps and is labeled delayed, never presented as live. Background ingestion is separately authorized; public GET never initiates subscription or recomputation.

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
group with no match has total zero. Paging applies to the combined result set
in stable security/topic/message order. Size defaults to 50 and accepts
20/50/100.
Missing archives differ from zero matches. Search does not use a loaded command
index as a complete source. Clients bind query, page, size, publication and date
before enabling target navigation.

Applicable member, role candidate/change, topic, news, ranking, global search
and expanded stock-history lists use 20/50/100 paging, default 50. Actual
identity/name and numerical fields support bidirectional sorting with missing
values last; security ID and archived code are distinct fields. News preserves
its publication-time ordering. Small metric summaries, fixed six-role evidence,
finite date/board matrices and small association previews remain explicit
exceptions. Page exports identify their visible page and filter scope.

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
unresolved publication returns the existing `no_data` envelope. A valid archive
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
