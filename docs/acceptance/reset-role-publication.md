# Reset Role Publication Acceptance

Recorded: 2026-09-22

## Delivered

- Immutable role runs bound to market snapshot, topic snapshot, rule version,
  benchmark, source versions, and the previous comparable published run.
- Complete per-topic persistence for six role statuses, candidates, factors,
  limit ladders, change summaries and entries, pending relay validations,
  validation revisions, and their real input references.
- Database-enforced materialization completeness, child ownership, relay
  pending identity, correction-chain continuity, and five-input completeness
  for conclusive relay validations.
- A `role-validate-pending` workflow that scans mature observations, calls
  `validate_relay`, records unavailable windows without synthetic run IDs, and
  appends corrections instead of mutating historical results.
- Current-time authorization for materialization, validation, replay,
  publication, and publication reads, including every validation input
  archive.
- Globally ordered source locking and immutable publication dependencies for
  all role and validation inputs.
- An additive `0007a` and `0007b` migration path that upgrades databases
  already stamped at the original `0007_role_readmodels` revision.

## Verification

| Check | Result |
|---|---|
| PostgreSQL Task 11, history, publication, storage, migration, and CLI regression | 431 passed |
| Remaining role engine and factor regression | 314 passed |
| Final specification review | passed |
| Final targeted code-quality review | passed |
| Ruff lint on committed HEAD | passed |
| Ruff format check on committed HEAD | 148 files formatted |
| Python compileall | passed |
| PRD contract checker | REQ=57, endpoints=29, examples=29, JSON=30 |
| Alembic heads | `0007b_role_validation_inputs` |
| Git diff checks | passed |

## Commits

- `b71d63d` `feat: persist role and ladder evidence`
- `629c652` `fix: complete role evidence persistence contracts`
- `9143c47` `fix: preserve role migration upgrade path`
- `bc7574c` `fix: reauthorize role validation replays`
- `b655ea0` `fix: harden role validation authorization`
- `98675ee` `fix: enforce complete role validation inputs`

## Workspace Note

The shared worktree contains 11 pre-existing, unstaged formatting differences.
They were neither reverted nor included in Task 11 commits. Full static checks
were therefore repeated against committed HEAD in
`.temp/task11-verify`.
