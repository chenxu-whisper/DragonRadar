# Workbench API Contract

Frozen 2026-09-20 for Task01. Normative companion to [PRD](../DragonRadar_开发需求PRD.md) and [DEC01-12](../superpowers/specs/2026-09-20-prd-reset-decisions.md). This document specifies behavior, not implementation or test results.

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
| EX11 | GET /api/topics/rotation | date,days1-250,sector? -> events[{at,sector_id,core_ids,change,comparable,reason,input_manifest}],returns; history not current members | 24; historical topic/role versions |
| EX12 | POST /api/query/parse | text1-500,snapshot_id,idempotency_key -> query_id,typed_ast,explanation,unsupported; optional model parser cannot execute | 26; deterministic parser baseline, optional AI permission |
| EX13 | POST /api/query/execute | query_id,confirmed_ast_hash,snapshot_id,idempotency_key -> fields,rows<=100,total,coverage; rejects unapproved/changed AST | 26; authorized frozen read-model fields |
| EX14 | POST /api/research/backtests | snapshot_id,input_manifests,strategy_id,parameters,period,cost_model,execution_model,experiment,idempotency_key -> run_id,status | 27; PIT data and disclosed simulation assumptions |
| EX15 | GET /api/research/backtests/{run_id} | owner -> config_hash,manifests,metrics,trades,rejections,equity,folds,baseline,ablation,quality; research_only=true | 27; no actual broker orders |
| EX16 | GET /api/market/sentiment/research | date,days1-120 -> score0-100 or null,phase or null,components,method_version,coverage,research_only=true | 28; formula and historical input completeness |

Realtime outputs additionally bind quote_batch_id/minute revision because a daily snapshot alone cannot pin intraday state. Exact batch selection never substitutes a newer batch; as_of forbids later available_at. Stale data retains source timestamps and is labeled delayed, never presented as live. Background ingestion is separately authorized; public GET never initiates subscription or recomputation.

Research endpoints never read private notes or simulation portfolios from the server. Simulation repositories are local IndexedDB `simulation_accounts`, `simulation_orders`, `simulation_fills`, `simulation_commands`, partitioned by local_profile_id; atomic command/revision semantics match Task04. Scenario rules are explicit research assumptions, not an assertion about current exchange rules.
