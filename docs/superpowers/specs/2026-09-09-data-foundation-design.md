# Data Foundation and Deterministic Calculations

Status: technical direction approved by the owner on 2026-09-09.
This document records that approval and the first implementation boundary.

## Scope

Deliver a local backend increment, not the full P0 release: typed data contracts,
source rights, quality isolation, explicit trading calendars, deterministic
calculations, PostgreSQL persistence/migrations, immutable snapshots, local CLI
imports and read-only HTTP queries. No frontend, real provider connection, AI,
public sharing, formal role scoring, or regulatory determination in this increment.

## Architecture

Python 3.12 modular monolith: `domain` contains validated contracts; `analytics`
contains pure calculations; `storage` owns PostgreSQL transactions; `api` exposes
read models; `cli` performs explicit local administration. FastAPI/Pydantic,
SQLAlchemy/psycopg and Alembic implement the boundary. Decimal values retain exact
input precision; UTC timestamps retain availability separate from event time.
Dependencies are locked with uv; test/build artifacts live in `.temp/`.

One import is a complete, self-contained snapshot input: source grant, selected
exchange calendar, versioned security states, expected pool, daily bars, date,
cutoff, price-adjustment version and basis. No automatic holiday generation,
weekend inference, provider scraping or cross-source value fusion.

## Data and Quality

All timestamp inputs must have time zones. IDs are nonempty stable identifiers;
names may change but identity does not. Monetary amounts, volume and prices have
explicit units. Ratios are decimals, not display percentages.

Source grants distinguish local storage, local display, export, AI and sharing,
with approved status, expiry and a documentary reference. No grant is implied
by a provider name or possession of a key. Revocation/expiry must be checked
again on reads, not just when importing.

The batch validator checks unique keys, known identities, effective states,
calendar membership, cutoff availability, OHLC relationships, positive prices
and adjustment factors, nonnegative amounts/volumes, source and adjustment
versions, pagination completeness, and declared pool coverage. Suspended records
are not fabricated as zero-price bars. Invalid or incomplete batches are retained
with structured issues and cannot publish. A legitimately suspended security can
be accounted for separately only with an explicit known state.

Historical backfills are retrospective research; they cannot be labeled as
contemporaneously observed. Selecting a historical cutoff excludes data first
available later. Input payloads contain no supplier credentials.

## Calculations

- N-day windows use 1..250 integer trading-day offsets, need N+1 calendar
  positions, and roll the requested end back to a supplied session.
- Custom starts roll forward and ends backward; reversed/empty windows fail;
  same-session returns are zero only for otherwise eligible observations.
- Returns use adjusted closes with one explicit factor version:
  `adjusted_close(end) / adjusted_close(start) - 1`. Raw close multiplied by the
  supplied factor gives adjusted close; factors from different versions cannot mix.
- Missing, suspended, not-yet-known, nonpositive, or mixed-source inputs exclude
  a security with a reason, never shorten its window.
- Close-based maximum drawdown is the maximum `1 - price / running_peak`.
  Normalized curves use `100 * price / first_price`. Comparison is restricted to
  at most five unique eligible securities and one common complete window.
- Market breadth discloses expected, valid, legally excluded and missing counts;
  missing inputs cannot silently shrink quality denominators.
- Closing limit hits use raw close and explicitly supplied effective limit price.
  No-limit sessions are not ordinary limit hits. Suspension breaks a consecutive
  run; gaps make the affected run unknown, not zero. No intraday sequence is inferred.

Role thresholds remain unapproved. The foundation does not invent a ranking model
or infer formal regulation status from these calculations.

## Storage and HTTP

PostgreSQL is the source of truth, including an immutable JSON input archive,
batch issues, source grants and snapshot manifests. Normalized domain objects
are validated before persistence; snapshot payload and domain versions are
frozen together. Archive access is internal, never an arbitrary filesystem path
from a request.

An idempotency key identifies import content: same key and canonical content
returns the same batch; same key with different content conflicts. Source grant
changes are explicit administration, not implicit imports overwriting authority.
Publishing is a transaction with a per-date revision constraint, an immutable
payload, and an append-only audit record. Re-publishing the same batch is
idempotent. A correction creates a new snapshot referencing the predecessor;
old snapshots remain readable subject to current license. Database protections
must reject mutation/deletion of published payloads.

Read API: health/liveness, database readiness, snapshot list/detail, overview
and interval results. Every analytical response carries trade date, source time,
snapshot ID, source, coverage, quality flags, rule version and basis. Missing
dates never select today's data. Validation and infrastructure errors are
sanitized; credentials and raw database exceptions cannot reach HTTP responses.
No HTTP administration, arbitrary SQL, external URL fetching or public endpoint
is supplied. Local development is loopback-only; this is not a completed owner
session or anonymous sharing implementation.

## Deployment and Verification

Use a project-specific Compose project with PostgreSQL and API only for this
backend increment. Database is internal in the base configuration; API exposure
is a loopback-only development override. No Caddy/Tunnel/share listener until
the frontend and approved owner access controls are ready.

Tests run pure domain/analytics fixtures plus real PostgreSQL migration,
idempotency, atomic publication, immutable payload, revision, revocation and
restart-persistence scenarios. API tests exercise missing dates and invalid
parameters without provider network calls. CI runs Ruff, compileall, pytest and
PostgreSQL integration tests. Test material is labeled synthetic and is never
automatically inserted in production.

## Acceptance Mapping

| PRD | This increment | Still outside acceptance |
| --- | --- | --- |
| G01 / D03 | Date-bound snapshots, revisions, immutable reads | Seven-page context |
| D01 / X02 / X03 | Grant validation, revoked/expired local reads blocked | Provider contracts, public sharing, retention policy |
| D02 / Q02 / Q04 | Idempotent file import, archived issues, publication gate | Provider pagination retry workers, operations UI |
| P01 / P02 | Common windows, returns, drawdown, max-five comparison | Charts, interactive sorting UI |
| M01 / L02 | Coverage, breadth, closing limits and streak semantics | Full index/theme pages, intraday observations |
| TCR-02/03, TCF-01/03 | Reproducibility, cutoff checks, missing-input behavior | Role experiments, statistical effectiveness |

Passing this increment does not satisfy P0 release criteria, independent
regulatory review, real-data license review, 20-day shadow operation, accessibility,
backup recovery RPO/RTO or internet deployment acceptance.
