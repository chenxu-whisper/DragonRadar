# Reset Market Ingestion Acceptance

Recorded: 2026-09-22

## Delivered

- Stable security identities separated from current display codes, with
  deterministic current mappings and immutable state history.
- Distinct stock and index daily read models plus versioned limit-up records.
- Exact nullable market-size, turnover, and seal fields with source version,
  availability time, and archive lineage.
- Explicitly licensed local JSON ingestion with page manifests, durable tasks,
  append-only attempts, envelope-bound idempotency, and bounded retries.
- Atomic archive, workflow, and read-model writes with deterministic lock
  ordering and explicit immutable-row conflict detection.
- Independent storage and display authorization. Export, AI, and sharing rights
  remain fail-closed and are never inferred from local display permission.
- Quarantine and publication blocking for incomplete pages, invalid units,
  negative values, unsupported security types, invalid identities, and invalid
  limit-up event state.

## Verification

| Check | Result |
|---|---|
| Task 07 and directly affected regression | 281 passed |
| Full backend after core Task 07 fixes | 1788 passed, 10 environment-dependent live-TLS tests skipped |
| Final specification review | passed |
| Final targeted code-quality review | passed |
| Ruff lint | passed |
| Ruff format check | 133 files formatted |
| Python compileall | passed |
| PRD contract checker | REQ=57, endpoints=29, examples=29, JSON=30 |
| Git diff check | passed |

The final independent regression used the dedicated `dragon_test` PostgreSQL
database. Migration `0006_market_readmodels` is the unreleased schema head;
development databases previously stamped with an intermediate form of `0006`
must be recreated.

## Commits

- `55a09d6` `feat: extend market source contracts and ingestion`
- `21427fd` `fix: harden licensed market ingestion`
- `22e9387` `fix: preserve market read model revisions`
- `eb6d476` `fix: make market revision conflicts explicit`
- `a52cde1` `fix: harden market ingestion retries`
- `1f68b69` `style: format task 07 ingestion changes`

## Production Gate

No supplier API specification, credentials, sample payload, or license evidence
was provided. Real-provider ingestion and historical-data validation remain
blocked. The delivered adapter accepts only operator-supplied licensed local
JSON and does not claim automatic collection or production data coverage.
