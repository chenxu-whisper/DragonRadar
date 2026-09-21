# Reset API, Navigation, and Local Review Acceptance

Recorded: 2026-09-21

## Delivered

- Independent local read-only `create_workbench_app` with fixed snapshot/date
  selection, normalized coverage, deterministic no-data behavior, sanitized
  errors, and exact Decimal JSON encoding.
- Reset frontend with six primary destinations, per-page state preservation,
  standalone stock routes, stale-response protection, and explicit not-ready
  states for data domains not yet connected.
- IndexedDB-backed notes and watchlist with stable local profile, composite
  keys, atomic revisions and commands, SHA-256 content identity, post-commit
  readback, tombstones, cross-tab notifications, and conflict-preserving
  save-as behavior.
- Stock list and drawer actions can add a security to the local watchlist and
  open a snapshot-bound review.
- Explicit owner export and browser backup/restore preserve every revision,
  verification link, watch entry, source identity, and verified trade date.
  Restore supports deterministic merge or save-as-new, rejects stale previews,
  and writes accepted records in one IndexedDB transaction.

## Verification

| Check | Result |
|---|---|
| API and legacy API focused tests | 121 passed |
| PRD contract tests | 28 passed |
| Frontend unit tests | 145 passed |
| Reset browser tests | 16 passed |
| Python Ruff | passed |
| Python format check | passed |
| Python compileall | passed |
| Default frontend build | passed |
| Default bundle isolation | passed; local-review modules absent from default static assets |
| Reset frontend build | passed |
| Mobile layout | 375px test passed with no horizontal page overflow |
| Full backend | 1670 passed, 10 live-TLS checks skipped because `DRAGON_TEST_PRIVATE_CA` was not configured |

The reset browser was also inspected at `http://127.0.0.1:5184/?view=review`:
all six destinations were present, the local review page rendered without
horizontal overflow, and new-note save was disabled until a snapshot was
selected.

The final local-storage review also covered lossless upgrade from the earlier
schema, replay of an older committed idempotency command after a newer revision,
and clean rollback when a migration write fails synchronously.

## Remaining Scope

Tasks 07-29 remain. The message, ladder, and regulation destinations
currently show explicit not-ready states and do not fabricate records. No
production provider, official regulatory rule, or model release has been
claimed.
