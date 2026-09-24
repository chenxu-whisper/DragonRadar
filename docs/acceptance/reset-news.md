# Reset News Acceptance

Recorded: 2026-09-24

## Delivered

- Immutable article revisions and point-in-time news snapshots selected by
  `available_at`, preventing late-arriving articles or revisions from entering
  earlier research snapshots.
- Deterministic event grouping by source event ID, canonical URL, exact content
  digest, or manual confirmation. Similar titles alone never merge events.
- Revision-level source links, licensed summary/body scope, novelty flags,
  supporting evidence, opposing evidence, and security/topic associations.
- Idempotent ingestion commands bound to the complete canonical request, with
  conflict detection and atomic concurrent event upsert.
- Publication-bound news materialization, source authorization rechecks,
  withdrawal lineage, database guards, and migration `0008_news`.
- Public endpoint 09/10 list and detail responses with type/source/text/security
  filtering, pagination, stable ordering, frozen `news|announcement|research`
  types, and exact snapshot selection.
- Owner-only association review with CSRF, expected revision, idempotency,
  stable audit identity, and transaction-time authorization. Accepted AI topic
  suggestions remain pending until a newer published topic snapshot contains
  the target topic.
- Reset News UI with list/detail layout, filters, pagination, explicit
  metadata-only licensing state, novelty/counter-evidence display, and
  snapshot-preserving navigation between news and the shared security drawer.

## Verification

| Check | Result |
|---|---|
| Final full backend run without PostgreSQL | 1411 passed, 745 skipped |
| Final news backend run without PostgreSQL | 10 passed, 18 skipped |
| Frontend unit tests | 248 passed |
| Reset analysis, market, leaders, and news E2E | 29 passed |
| Frontend production build | passed |
| Default entry bundle | 314,394 bytes, within 315,000-byte gate |
| Targeted Ruff lint | passed |
| Targeted Ruff format check | 17 files formatted |
| Python compileall | passed |
| PRD contract checker | REQ=57, endpoints=29, examples=29, JSON=30 |
| Final specification and code-quality review | approved |

Docker Desktop was unavailable and `DRAGON_TEST_DATABASE_URL` was unset during
the final run. PostgreSQL migration, constraint, concurrency, owner write, and
publication-lineage cases were therefore reported as skipped rather than
passing. Synthetic fixtures establish deterministic behavior only; no licensed
production news feed or body-display permission has been supplied.

## Commits

- `1099d65`: immutable news storage, publication/API integration, owner review,
  and News/SecurityDetail UI.
- `f56da69`: reviewed association activation, frozen news types, and pagination.
- `3a4532a`: stable persisted audit identity in owner review responses.
- `af2b7e5`: endpoint wire-contract and FAC-N01 evidence alignment.
- `2342322`: ingestion idempotency, concurrent event upsert, and lineage index.
- `32c0d6c`: frontend error isolation and lazy-loaded News bundle.
- `ef3ba86`: linear grouping of news lineage by snapshot.

## Remaining Risk

Real PostgreSQL evidence is still required before production acceptance,
especially for migration `0008_news`, immutable triggers, concurrent event
ingestion, owner review conflicts, withdrawal handling, and publication
lineage. The implemented UI uses synthetic metadata and does not claim licensed
full-text coverage or AI analysis.

## Workspace Note

The shared worktree retains unrelated unstaged changes that predate Task14.
They were not reverted or included in Task14 commits.
