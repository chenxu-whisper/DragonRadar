# Reset Regulation Rules Acceptance

Recorded: 2026-09-24

## Delivered

- Immutable, versioned regulation rules with exchange, board, security-status,
  effective-window, direction, benchmark, reset-basis, official-source,
  independent-review, and frozen-regression metadata.
- Fail-closed production gate requiring an approved review, official HTTPS
  source, independent release record, supported method, passing regression
  cases, and an active lifecycle binding.
- Independent upper/lower legal and warning thresholds. Missing, conflicting,
  unsupported, inactive, rejected, and inapplicable inputs remain `pending`.
- Rule-version-bound observations, market-wide completeness, formal disclosed
  events, append-only lifecycle controls, and withdrawal that stops only new
  calculations while preserving historical snapshots.
- Publication-bound regulation snapshots with immutable lineage, source
  authorization, payload recomputation, concurrent publication sealing, and
  additive event-source provenance migration `0009b_reg_event_provenance`
  after `0009a_reg_event_source`, followed by
  `0009c_reg_event_provenance_required` to reject new provenance-less events
  without invalidating readable legacy rows.
- Persisted `unknown`/`complete` event-source attestations with source identity
  and version required for `complete`; listed formal events remain visible and
  counted independently when source completeness is unknown.
- Controlled CLI commands for rule registration/activation/withdrawal, event
  ingestion, and snapshot materialization.
- Database guards against fabricated calculations, invalid market identity,
  non-finite numeric values, malformed immutable records, post-publication
  child appends, and destructive mutation.
- Set-based materialization and publication reads. Materialization remains at
  16 SQL statements for both 1x1 and 6x6 rule/security inputs; publication
  reads use 13 statements without regulation and 21 with one or four
  regulation publications.

## Verification

| Check | Result |
|---|---|
| Final PostgreSQL backend suite | 2268 passed, 10 skipped |
| Skipped scope | live private TLS only; `DRAGON_TEST_PRIVATE_CA` unavailable |
| Focused regulation/publication suite | 364 passed |
| Migration head | `0009c_reg_event_provenance_required` |
| Targeted Ruff lint and format | passed |
| Python compileall | passed |
| PRD contract checker | REQ=57, endpoints=29, examples=29, JSON=30 |
| Specification review | approved |
| Final code-quality review | approved; no Critical or Important findings |

The PostgreSQL suite ran against a fresh PostgreSQL 17 container and dedicated
`dragon_test` database. Tests use synthetic rules and observations only.

## Production Enablement

No formal regulation threshold is production-enabled by this task. Production
activation still requires an official rule version, applicable scope and
effective dates, an independent review/release record, and approved frozen
regression examples. Formal events must come from disclosed announcement facts;
calculation state never creates a formal event.

## Commits

- `a418879`: reviewed rule, event, snapshot, publication, CLI, migration, and
  documentation framework.
- `7eb9b83`: market-wide completeness, rule-bound inputs, fail-closed gates,
  and sequence watermarks.
- `f21baf3`: aligned SQL and Python HTTPS validation.
- `544c87d`: hardened persistence and set-based materialization.
- `12cc494`: batched regulation publication reads.
- `a1a96f8`: restored PostgreSQL regression coverage across prior tasks.
- `5153ad3`: sealed snapshot integrity and finite numeric contracts.
- `4a8d083`: serialized publication sealing and indexed regulation bindings.
- `99e09be`: preserved the frozen `0009_regulation` migration head.

Future AI migration `0010_ai_tasks` remains reserved and must use
`0009c_reg_event_provenance_required` as its `down_revision`.
- `507e9d0`: scoped migration trigger checks to isolated schemas.

## Workspace Note

The shared worktree retains unrelated unstaged changes that predate Task15.
They were not reverted or included in Task15 commits.
