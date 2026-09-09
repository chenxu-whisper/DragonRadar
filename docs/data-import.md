# Local Data Import Contract

This backend accepts an explicit local JSON batch. It does not download market
data, infer provider rights, or infer historical holiday calendars. Operators
must register current local rights separately from importing a batch.

## Payload

`dragon_radar.domain.models.MarketBatch` is the executable schema. Use
`MarketBatch.model_json_schema()` to inspect the exact required fields rather
than maintaining a second handwritten schema. Decimal fields must be JSON
strings or integers, not floating-point JSON numbers.

Every batch supplies:

- `trade_date` and UTC-aware `as_of`; observations cannot be available after cutoff.
- `provenance`: `contemporaneous` or `retrospective`, explicitly selected.
- `source_id`, `source_version`, `adjustment_version`, `calendar_version`,
  `pool_version`.
- `grant`: approved source ID, documentary reference, start/expiry timestamps
  and separate storage/display/export/AI/sharing permissions.
- `calendar`: exchange, version and sorted unique explicit trading sessions.
- `expected_security_ids`: declared current coverage denominator, not inferred
  from whatever rows happened to arrive.
- `security_states`: immutable historical identity/status intervals with
  availability times, listing date and explicit suspension state.
- `bars`: raw daily prices with source and factor version, availability timestamp,
  factor, currency and units. Complete OHLC or close-only data are supported.
- `complete_pages`: explicitly true only when collection completeness is known.

Security effective intervals include `effective_from` and exclude `effective_to`.
Grant validity includes `starts_at` and excludes `expires_at`. Do not use the
system's current date as the source event timestamp.

The adjustment factor multiplies raw close for research returns. It must be
supplied with the same version across the entire batch. Limit checks never use
adjusted prices. `limit_status="limited"` requires the same trade date and
positive, distinct, ordered upper/lower prices. `unknown` is not `no_limit`.

## Failures and Revisions

Incomplete pages, duplicates, invalid prices, inconsistent identities/versions
and anomalous missing coverage prevent publication. They remain reviewable as
quality issues when storage is permitted. Structural schema failures are rejected
before business validation. Denied storage permission prevents saving the market
payload; it is not acceptable to retain unauthorized raw data merely for debugging.

Use a stable idempotency key for retries of the same payload. Reusing a key for
different content is a conflict, not an overwrite. Once published, a snapshot
cannot be edited or deleted through the repository or normal database writes.
Corrections are new batches and new revisions; reads of old snapshots still
check the current source grant.

## Synthetic Test Materials

Tests build clearly named `SYNTH-*` or fixture-only securities without downloading
real prices. These are test inputs, not investment examples or production content.
Never run a test seed/import command against an actual research database.

`examples/synthetic-grant.json` and `examples/synthetic-batch.json` are a matching
local test pair. The batch deliberately uses the `TEST` exchange, one virtual
security and two explicit sessions. Its 100-to-110 closing sequence gives a 0.1
return, zero drawdown and a 100-to-110 normalized curve. Both days explicitly
have no price limits; the 10% return must not be identified as a limit-up event.
Source event time is unknown rather than copied from the collection time.
Use a dedicated test database and `n=1` (the default 20-day window is unavailable).

With `DRAGON_DATABASE_URL` pointing only to your disposable test database:

```powershell
uv run dragon-radar migrate
uv run dragon-radar source-register examples/synthetic-grant.json
$import = uv run dragon-radar import examples/synthetic-batch.json --key synthetic-v1 | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Import failed" }
$published = uv run dragon-radar publish $import.batch_id | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Publication failed" }
uv run dragon-radar serve --host 127.0.0.1 --port 8011
```

The final command occupies that terminal. In another terminal, query the returned
snapshot ID with `GET /api/v1/snapshots/{snapshot_id}/interval?n=1`.
Use `GET /api/v1/snapshots?trade_date=2026-09-08` to rediscover published IDs.
`source-revoke synthetic-example` blocks further reads/publications; it does
not mutate the archived payload. A repeated import with the same key/content
returns the original batch, and a repeated publication returns the original
snapshot. A changed payload needs a new key and creates a new revision.

For deterministic research, retain the source payload, calendar, factor version,
cutoff and software version. A historical backfill acquired today can support
retrospective calculations, but cannot prove what this system knew at the
historical trading date.
