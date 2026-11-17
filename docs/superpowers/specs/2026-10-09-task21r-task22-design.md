# Task 21R Realtime And Task 22 LHB Design

## Status

Approved on 2026-10-09.

This specification restores the realtime capability that the original reset
plan called Task 21 under the unambiguous identifier **Task 21R**. The migration
and release audit already delivered as Task 21 remains unchanged. Task 22 starts
only after Task 21R passes implementation, specification review, and code
quality review.

## Goals

Task 21R adds authorized point-in-time realtime quote batches, minute revisions,
transparent anomaly events, and source-defined intraday sector flow.

Task 22 adds point-in-time LHB disclosures and stable source-seat aggregation.

Both tasks must:

- preserve Decimal values and source timestamps;
- distinguish zero, unknown, unavailable, delayed, and missing observations;
- keep ingestion separate from public read APIs;
- prevent future or late data from entering historical reads;
- keep production use blocked until independently trusted licenses and real
  source evidence exist;
- extend the frozen migration chain without rewriting any existing revision.

## Non-Goals

- No broker connection, order generation, or automated trading.
- No public GET starts a subscription, import, recomputation, or background job.
- No daily bars are used to manufacture quotes, minute bars, order-book fields,
  or intraday flow.
- No source seat name is attributed to a natural person or fund.
- No same-name seats are merged without a stable source mapping.
- No synthetic fixture is presented as licensed production data.
- No new runtime dependency is introduced.

## Chosen Architecture

Use two independent domain packages with internal ingestion commands and
read-only HTTP routers:

```text
authorized batch
    -> domain validation
    -> transactional immutable storage
    -> point-in-time repository selection
    -> read-only EX04-09 routes
    -> runtime-validated frontend panels
```

Realtime data remains independent from daily publication snapshots. A response
may carry the selected daily `snapshot_id` as research context, but its exact
intraday identity is the source batch, revision, event time, and availability
watermark. Explicit batch or revision selection never falls forward.

LHB disclosures use their own disclosure and revision identities. They can be
linked from a security selected in a daily snapshot, but visibility is governed
by the disclosure's `available_at`, source permission, and revision history.

### Rejected Alternatives

1. A generic event store for both domains was rejected because it would add a
   new abstraction before two concrete read models prove common semantics.
2. Reusing daily market/publication tables was rejected because it would blur
   daily snapshot identity with independently revised intraday and disclosure
   data.

## Migration Chain And Release Contracts

The chain becomes:

```text
0010_ai_tasks -> 0011_realtime -> 0012_lhb
```

The existing migrations through `0010_ai_tasks` are immutable.

Each new migration stage must update:

- `src/dragon_radar/storage/database.py::SCHEMA_REVISION`;
- `src/dragon_radar/migrations/release_manifest.json`;
- `src/dragon_radar/storage/schema_contract.json`;
- migration manifest and schema drift tests;
- installed-wheel migration inventory and upgrade coverage.

The Task 21 release evidence remains a historical audit of the `0010` head. It
must not be silently rewritten to claim the new head was previously audited.
Task 21R and Task 22 commits produce task-specific acceptance evidence; the
project-wide release audit is regenerated at the later full release gate.

## Task 21R Domain

### Tables

`0011_realtime` creates:

- `realtime_quote_batches`
  - source and source version;
  - stable `quote_batch_id`;
  - source event, received, and available timestamps;
  - optional monotonic source sequence;
  - permission snapshot and import identity;
  - immutable uniqueness on source plus batch identity.
- `realtime_quotes`
  - batch identity and stable security ID;
  - nullable last, bid, ask, bid size, ask size, volume, and amount;
  - source status and explicit unavailable reason;
  - no field converts missing values to zero.
- `realtime_minute_bars`
  - source, security, minute event time, revision, batch identity;
  - OHLC, volume, amount, final flag, source/received/available timestamps;
  - uniqueness on `(source, security_id, event_at, revision)`;
  - immutable revisions rather than in-place correction.
- `realtime_anomaly_events`
  - deterministic event identity, security, rule version, batch;
  - metric, window, value, threshold, source time, available time, quality;
  - deterministic duplicate and cooldown identity.
- `realtime_sector_flow_points`
  - stable sector, event time, batch, source-defined net inflow and unit;
  - nullable value plus required unavailable reason when absent.

All new tables use explicit primary, foreign, check, uniqueness, and supporting
read indexes. The schema contract captures their columns, constraints, indexes,
and immutability triggers.

### Models And Validation

`dragon_radar.realtime.models` defines strict Pydantic models for:

- quote and minute input batches;
- quote/minute/anomaly/sector-flow read records;
- anomaly rules and detection results;
- permission and source completeness.

Rules:

- datetimes are timezone-aware and canonicalized to UTC;
- security and sector IDs are stable identifiers, not names;
- Decimal values reject NaN and Infinity;
- OHLC values are nonnegative and satisfy source-declared availability;
- volume and amount are nonnegative when present;
- `received_at >= source_event_at` and `available_at >= received_at`;
- revisions are positive integers;
- absent order-book or flow inputs stay unavailable.

### Ingestion

`ingest_minutes(batch)` and quote/flow equivalents are internal commands. They:

1. validate the complete batch before opening a transaction;
2. verify the source permission snapshot;
3. acquire a source/batch advisory lock;
4. insert the immutable batch and rows atomically;
5. return the prior result for the same identity and payload hash;
6. reject the same identity with a different payload;
7. preserve out-of-order batches and later revisions;
8. never fill missing minutes with zero or copied bars.

Import attempts record accepted, duplicate, rejected, and incomplete outcomes
without exposing credentials or raw protected payloads.

### Anomaly Detection

The first version supports:

- window return:
  `current_close / first_close - 1`;
- relative volume:
  `current_volume / reference_volume` only when both inputs are available.

For the acceptance example, closes `100 -> 103` over the configured three-minute
window produce `0.03`; threshold `0.02` triggers.

Insufficient windows produce no event and an explicit reason. Events are
deterministic for the rule version, input batch/revisions, security, metric,
window, and cooldown bucket. Detection never sends orders.

### Point-In-Time Reads

- Quotes select an exact batch or the latest authorized batch with
  `available_at <= as_of`.
- Minute reads return only revisions visible at the read watermark and select
  the latest visible revision for each minute.
- Missing minutes are absent, not synthetic bars.
- Stale/stream-gap states retain original source timestamps and return delayed
  or unavailable status.
- Anomaly cursors are signed, versioned, filter-bound, expiry-bound, and sorted
  by `(available_at, event_id)`.
- Public reads recheck current permission even when data is cached.

### HTTP Contracts

Implement EX04-07:

- `GET /api/realtime/quotes`
- `GET /api/realtime/minutes`
- `GET /api/realtime/anomalies`
- `GET /api/market/sector-flow/intraday`

The routes use the existing Decimal response boundary and structured errors.
Unknown entities within an available batch are `404`; no available authorized
batch is a successful envelope with `data:null` and `no_data` or `unavailable`.
Revoked permissions return `403` without protected payload.

### Frontend

`RealtimePanel` is added to Overview without replacing daily market content.
It includes:

- quote batch/source timestamp and delayed/unavailable status;
- minute series without gap interpolation;
- anomaly rows with rule version, input batch, value, and threshold;
- sector flow only when the source supplies a defined field and unit.

The panel is lazy-loaded under the reset feature flag. It preserves fixed daily
snapshot navigation while displaying independent intraday identity. Loading,
empty, revoked, stale, stream-gap, partial, and retry states are explicit.

## Task 22 Domain

### Tables

`0012_lhb` creates:

- `lhb_import_batches`
  - source/version, batch identity, permission, received and available times,
    payload hash, and immutable import outcome.
- `lhb_disclosures`
  - stable disclosure ID, security ID, disclosure date, reason, source, and
    permission identity.
- `lhb_disclosure_revisions`
  - disclosure ID, positive revision, source/received/available timestamps;
  - completeness state and immutable payload hash.
- `lhb_seats`
  - stable source-scoped seat ID, source seat name, mapping version, provenance;
  - no natural-person attribution.
- `lhb_disclosure_seats`
  - disclosure revision, side/rank when supplied, seat ID;
  - nullable buy and sell amounts with declared currency/unit;
  - source row identity and completeness.

The same source name can map to different stable seat IDs. A mapping revision
does not rewrite historical disclosure rows.

### Ingestion And Revision Rules

The internal import service:

- requires an authorized source and stable disclosure identity;
- is idempotent on source/batch/disclosure/revision identities and payload hash;
- preserves duplicate source rows as rejected or linked duplicates rather than
  double-counting;
- appends corrections as revisions;
- records late disclosures with their real `available_at`;
- preserves null amounts and distinguishes them from numeric zero.

### Aggregation

`aggregate_seats(disclosures, as_of)`:

- excludes revisions with `available_at > as_of`;
- selects the latest visible revision for each disclosure;
- groups by stable `seat_id`, never normalized display name;
- computes `net_amount = buy_amount - sell_amount` only when both operands are
  known;
- returns null net amount and an explicit completeness reason otherwise;
- retains disclosure IDs, security IDs, reasons, source seat names, source,
  permission state, mapping version, currency, and unit.

The acceptance example `buy=100`, `sell=30` returns `net=70`.

### HTTP Contracts

Implement EX08-09:

- `GET /api/lhb`
  - exact `date`, optional security, `page>=1`, `size=1..100`;
  - returns disclosures, seats, coverage, total, source, units, and as-of.
- `GET /api/lhb/seats/{seat_id}`
  - exact date and `days=1..250`;
  - returns stable source identity, disclosure history, buy/sell/net amounts;
  - never returns person attribution.

Historical reads cannot see late announcements. Missing authorized disclosure
data returns `no_data`; revoked permission returns `403`.

### Frontend

`SeatsPanel` is lazy-loaded in SecurityDetail and linked from the security
drawer. It shows:

- disclosure date, reason, revision, and availability time;
- source seat name and stable seat ID;
- buy, sell, and net amounts with unit;
- explicit unknown and incomplete states;
- separate synthetic fixture and licensed-source labels.

No UI copy calls a seat a real person, fund, or current holding.

## Error And Security Boundaries

- Internal imports are not mounted on the public workbench app.
- Public routers accept only bounded, typed query parameters.
- SQL is parameterized and repositories own selection logic.
- No response includes credentials, raw upstream bodies, or internal paths.
- Source permissions are checked at import and read time.
- Caches include source permission/version and point-in-time selection keys.
- Public GET remains read-only and produces no audit/task/import side effect.

## Testing And Delivery

Both tasks use TDD and isolated commits.

Task 21R tests cover RT01-12:

1. authorization;
2. source time and delay;
3. stable security identity;
4. trading session validation;
5. duplicate ingestion;
6. out-of-order data;
7. immutable revision history;
8. no missing-minute zero fill;
9. stream-gap degradation;
10. price/volume window formulas;
11. event duplicate/cooldown behavior;
12. deterministic replay and public no-write behavior.

Task 22 tests cover duplicate disclosures, revisions, same-name distinct seats,
late announcements, nullable amounts, `100-30=70`, authorization, pagination,
historical visibility, and synthetic/licensed labeling.

Each task runs:

- focused backend pytest;
- PostgreSQL 17 migration and drift tests;
- Ruff, format, and compileall;
- frontend unit/build/bundle checks;
- focused Playwright desktop/mobile/accessibility flows;
- specification review, then code quality review;
- task-specific acceptance evidence.

Task 22 cannot start until Task 21R has passed both reviews. Missing production
licenses and real inputs remain explicit release blockers after engineering
tests pass.
