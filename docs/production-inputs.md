# P1 Production Input Contract

This document retains the approved P1 production input boundary. It implements
the local input/compute seam, not production approval or release permission.
Real `data/suppliers.json` and `docs/proofs/` are still external prerequisites.
Nothing in this implementation creates a supplier, grant, signature or proof.

## Wire Format

All objects below have exactly the listed fields. Unknown fields are rejected.
JSON uses `production.trust.canonical_json`: UTF-8, sorted keys, no duplicate
keys, no nonfinite numbers, bounded size. SHA256 values are lowercase 64-hex.
Dates are ISO dates; times are aware UTC ISO strings. Prices are exact decimal
strings, never floats. Model fields follow the existing strict kernel models.

### Suppliers

Top level: `schema`, `suppliers`.
Schema: `dragon-radar-production-suppliers/v1`.

Each supplier: `supplier_id`, `source_id`, `source_version`, `domains`, `grant`,
`provenance`, `methods`.

- `domains`: unique members of `market`, `state`, `calendar`, `topic`, `benchmark`.
- `grant`: existing `SourceGrant`, with source identity, explicit approval and
  reference, `starts_at`, `expires_at`, and explicit local storage/display rights.
  Export, AI and sharing rights are independent; no right is inferred.
- `provenance`: exactly `real`. Fixture, synthetic, test, demo and mock source
  identities cannot become production suppliers.
- `methods`: factor identity to externally approved method version. Entries
  must agree with the candidate's separate approved method map.

Duplicate supplier identities and source/version/domain bindings are rejected.
Embedded batch grants must exactly match the signed configured supplier grant.
Grants must be live now, at receipt, and at the claimed actual run start/finish.

### Frozen Bundle

Schema: `dragon-radar-production-input/v1`.

Exact fields: `schema`, `candidate`, `methods`, `snapshot_id`,
`publication_sequence`, `config`, `anchor`, `frames`, `publications`, `policy`,
`receipts`, `benchmark_closes`, `factor_archive`.

- `candidate`: exactly `code`, `dataset`, `rules`, `model`, `prompt`, `retrieval`.
  Code is `source-sha256:<digest>`, matching the current local source inventory.
- `methods`: separate approved method map; never a version override.
- `config`: existing `BacktestConfig`; `anchor`: `PitAnchor`; `frames`: ascending
  `PitFrame` series, one per approved session in the inclusive configured period.
- `snapshot_id` equals config snapshot and final publication identity.
  `publication_sequence` equals anchor and final frame/publication sequence.
- `policy`: explicit `UniversePolicy`. Required topic data cannot silently
  degrade into an unscoped market pool.
- `publications`: one per frame, with exactly `snapshot_id`,
  `market_snapshot_id`, `topic_snapshot_id` (nullable), `publication_sequence`,
  `published_at`, `frame_sha256`. The last domain IDs equal config manifests.
  Frame hash covers canonical JSON of the corresponding complete frame.
- `benchmark_closes`: mapping ISO session to exact raw close decimal string,
  or `{}` when an already materialized supplier factor needs no local benchmark.

Market inputs require raw OHLC, volume/amount and units, approved calendar,
historical code/type/listing, ST, suspended and delisting state. Shared domain
quality validation rejects incomplete/invalid batches. Source/pool/adjustment/
calendar versions must match config and supplier bindings. Retrospective batch
provenance and late records cannot authorize historical PIT decisions.

### Reception Evidence

Each receipt has exactly `domain`, `source_id`, `source_version`,
`record_sha256`, `source_ref`, `published_at`, `first_received_at`, `received_at`,
`provenance`.

`source_ref` is an absolute HTTPS immutable source reference, with no credentials.
`provenance` is exactly `real`. Hashes cover canonical JSON of individual raw
`DailyBar`, `SecurityState`, `ExchangeCalendar`, `TopicDefinition`,
`TopicMembership`, `TopicCoverageCell`, or `PageManifest` records. Benchmark
receipts hash `{"trade_date": "<ISO date>", "close": "<exact decimal string>"}`.

Publication <= actual first receipt <= receipt <= cutoff/availability is
required. The signed original collection facts must supply these times;
the adapter never generates or retrospectively fills first-received history.
Legitimate records received earlier may appear in later frames. All raw
dependencies need receipts; duplicate or unbound receipts are rejected.

### Factors

Schema: `dragon-radar-production-factors/v1`.
Exact fields: `schema`, `method_version`, `frames`, `lineage`.

`frames` are existing `FactorFrame` objects. Each matching lineage has exactly
`trade_date`, `available_at`, `market_snapshot_id`, `topic_snapshot_id`,
`publication_sequence`, `source_refs`. Source refs are receipt digests covering
all required frame dependencies; unknown and future dependencies are rejected.
Method versions must match candidate and supplier approval.

P1 supports only FAC-R01 `relative_strength_20`, `relative_strength_60`,
`relative_strength_120`, windows `20d`/`60d`/`120d`, unit `ratio`.
Required visible securities need finite numeric observations. Supplier
materialized frames are accepted only through the authenticated bundle contract.
Every referenced raw record's own `available_at` and coverage `complete_as_of`
must also be no later than factor availability; an earlier receipt cannot
override either knowledge bound.

Topic inputs use the existing `validate_topic_batch` quality gate, including
its 61-session coverage requirement, declared definition/member intervals,
complete cell counts and contiguous self-consistent pagination. Empty topic
definitions or coverage cannot become a production-ready topic domain.

`materialize_relative_strength(frames, *, metric, benchmark_closes)` is a pure
arithmetic helper for already authorized data. It uses existing `adjusted_price`,
`period_return`, `relative_return`, requiring N+1 approved sessions and all
security/benchmark history from each same PIT frame. No interpolation,
invented score, inferred session or later-frame history is used.

### Actual Run

Schema: `dragon-radar-production-run/v1`.
Exact fields: `schema`, `candidate`, `snapshot_id`, `publication_sequence`,
`config_sha256`, `input_sha256`, `result_sha256`, `started_at`, `finished_at`,
`result`.

The digests cover canonical config, entire bundle, and actual `BacktestResult`
JSON respectively. No claimed completion flag is recognized.

`validate_input_run(bundle: dict, run: dict, suppliers: dict, candidate: Mapping,
now: aware datetime)` validates semantics and re-executes the existing kernel
locally to compare the complete actual deterministic result. Signature
verification must happen first at the calling evidence boundary. This function
is not called by API GET.

The safe summary contains `input_sha256`, `config_sha256`, `result_sha256`,
`snapshot_id`, `publication_sequence`, `started_at`, `finished_at`, `sessions`,
`factor_id`, `method_version`. It contains no raw prices, states or license text.
Parent semantic metadata may contain exactly `methods`, `regulation_rules`,
`rule_scope`, `official_source_hosts`; none may override signed versions.

## Archive And Worker

`freeze_input_bundle` validates and detaches inputs into an immutable byte bundle.
`write_input_archive` uses exclusive `secure_output` and private `read_asset`,
returning `{"path": "<digest>.json", "sha256": "<digest>"}`. Existing files are
never overwritten; Windows may return generic `OSError` on a collision.
`read_input_archive` rejects unsafe paths, noncanonical content and digest drift.
Protected roots are provisioned by the operator, never chmodded by the adapter.

`ProductionResearchCompute` requires explicit asset/supplier/output roots,
record path, trust policy or live policy loader, candidate, scope context,
current code callback, and actual publication-sequence resolver for computation.
Each call verifies the independent signature and current authorization, exact
config, actual `PublishedSnapshot` market batch content, publication time/date/
status, domain IDs, and actual local sequences of all archived publications.
Missing sequence resolution fails closed. It calls `run_backtest`, forwards
worker checkpoint/cancellation/budget exceptions, checks grants at checkpoints,
and re-verifies proof and input identity before archiving the result.
Checkpoints also bracket final reauthentication, archive issuance and return;
validation and output overhead cannot silently exceed the worker wall budget.

Kernel result shape is unchanged. Separately archived digest-named run artifacts
bind actual start/finish times, input/config/result digests and candidate.
The worker's existing leases, result cache and default refusal remain unchanged.

## Explicit Owner CLI

Use `analysis-worker --research-production-request <protected-request-path>`.
No option means the existing `fail_closed_research_compute`.

The private request has exactly `schema`, `asset_root`, `evidence_path`,
`suppliers_path`, `output_root`, `trust_policy_path`, `trust_policy_sha256`,
`openssl`, `candidate`, `methods`, `context`.
Schema: `dragon-radar-production-research-request/v1`.

Paths are absolute except root-relative `evidence_path`; OpenSSL is absolute
or null (null does not enable a verifier fallback). Candidate has exactly six
versions. Methods remain separate. Context has exactly `subject`, `purpose`,
`target`, `audience`. This schema is independent of parent `candidate.json`.
The trust-policy digest must be approved through external operator configuration,
not discovered from proof content or installed by this command.

The loader calls `production.cli.load_policy` with the approved digest and
`trust.verify_evidence`, authenticates supplier bytes and frozen input semantics
before injection, and loads the pinned policy again per run. It computes the
current source identity itself. The owner CLI supplies the existing local
publication database solely for read-only sequence resolution.
Current-source verification requires the loaded code to match the source
checkout's `src/` layout. Installed-only layouts without that binding are refused.

A signed bootstrap `real_input` payload may contain only `bundle`, `supplier`;
it can authorize the first computation, not pass a production input gate.
A gate proof additionally needs `run`. Merely hashing or signing a claimed
result never replaces deterministic result validation or original timing proof.

## Deliberate Limits

No FAC-R02/R03/C01/C02 adapter, AI factor adapter, role domain adapter, limit-event
receipt adapter or walk-forward phase-series adapter is approved in P1. Those
inputs fail closed rather than producing invented scoring or phase frames.
No dependencies, database migrations, containers, Web writes, commits or release
permissions are added. Real evidence availability and genuine daily execution
attestation remain external requirements.
