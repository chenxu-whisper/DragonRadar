# Reset Publication Acceptance

Recorded: 2026-09-21

## Delivered

- Immutable multi-domain publication manifests with explicit
  `ready`/`missing`/`disabled` states, publication sequence watermarks, and
  append-only revisions.
- Atomic publication of materialized results, lineage dependencies, and the
  final manifest transition; failed materialization leaves no visible partial
  publication and records an immutable failed attempt.
- Idempotent publication commands with database-enforced command/publication
  authority and deterministic conflict handling.
- Workbench snapshot selection backed by unified publications, while preserving
  authorized legacy snapshot IDs for existing local note references.
- Publication-time market overview materialization; unified reads do not
  recompute analytics.
- Current-permission checks under stable source lock ordering, including
  explicit 403 behavior after revocation.
- Source-to-archive-to-derived dependency tracking, approved destructive
  retention actions, immutable binding seals, cache invalidation, module
  disablement without an approved policy, and replay-before-restore gates.
- Keyset pagination with a fixed publication-sequence watermark, preventing
  concurrent publications from changing an in-progress listing.

## Verification

| Check | Result |
|---|---|
| Task 08 focused tests | 80 passed |
| Migration/reset/CLI regression | 181 passed |
| Full backend | 1750 passed, 10 live-TLS checks skipped because `DRAGON_TEST_PRIVATE_CA` was not configured |
| Python Ruff | passed |
| Python format check | 123 files formatted |
| Python compileall | passed |
| Git diff check | passed |
| Final specification review | passed |
| Final targeted quality review | passed |

The PostgreSQL checks used the dedicated `dragon_test` database. Migration
coverage includes a clean upgrade from `0004a_topic_attempt_ranges` to the
final, not-yet-released `0005_publications` revision. Intermediate development
forms of revision `0005` are not a supported deployment state.

## Commits

- `d3f9f83` `feat: publish atomic versioned read models`
- `0839ea0` `fix: close publication consistency gaps`
- `9bf0d79` `fix: enforce publication ledger invariants`
- `ca90c63` `fix: enforce publication database state machine`
- `caeb4c7` `fix: replay complete withdrawal history`
- `2c2aeba` `fix: harden publication persistence and reads`
- `74d14a4` `fix: stabilize publication scans and withdrawal binding`
- `69b0fa8` `fix: freeze publication listing watermark`

## Remaining Scope

Task 07 still needs the unified security and market read tables plus licensed
local-file ingestion. Missing news, regulation, and role inputs remain explicit
publication states; this task does not fabricate or claim production data.
