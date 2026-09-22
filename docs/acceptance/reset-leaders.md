# Reset Leaders Evidence Acceptance

Recorded: 2026-09-22

## Delivered

- Publication-bound read APIs for the limit-up ladder, continuity feedback,
  role candidates, and per-security role evidence.
- Strict external role names, filtered coverage partitions, explicit
  unavailable states, Decimal continuity values, and current authorization
  checks without GET-time role computation.
- A dense date-by-board leaders workspace with date, topic, and six-role
  filters; tied candidates; continuity metrics; role changes; and comparison
  reasons.
- A single global security drawer that preserves the selected publication and
  topic evidence context without changing the page filter.
- Six-role evidence with factor values, units, windows, coverage, true source
  time, knowledge cutoff, and separate supporting, opposing, and missing
  evidence.
- Chinese presentation labels with raw codes retained in expandable audit
  evidence, conservative all-topic status aggregation, and validated reset
  stock responses.
- The complete local workflow from a ladder security to watchlist, a
  security-bound revisioned note, and return to the original leaders filters.
- Responsive candidate and role-change records that remain readable at a
  375-pixel viewport while preserving semantic desktop tables.

## Verification

| Check | Result |
|---|---|
| PostgreSQL role API and publication regression | 236 passed |
| Frontend unit tests | 168 passed |
| Reset Playwright suite | 32 passed |
| Frontend production build | passed |
| Default entry bundle | 314,201 bytes, within 315,000-byte gate |
| Final specification review | passed |
| Final frontend quality review | passed |
| Ruff lint on committed HEAD | passed |
| Ruff format check on committed HEAD | 151 files formatted |
| Python compileall | passed |
| PRD contract checker | REQ=57, endpoints=29, examples=29, JSON=30 |
| Alembic heads | `0007b_role_validation_inputs` |
| Browser smoke check | one main landmark, no page overflow, no runtime errors |

## Commits

- `a961a04` `feat: add role and ladder read APIs`
- `47caa9a` `fix: align role API evidence contracts`
- `a2fd9c5` `fix: preserve role evidence source time`
- `ebd51b2` `fix: complete role API quality signals`
- `6020e6e` `feat: deliver leaders evidence workflow`
- `283d09f` `fix: preserve leaders evidence context`
- `968a1a8` `fix: harden leaders evidence presentation`
- `18781ae` `fix: adapt leaders tables for mobile`

## Local Preview

- Web: `http://127.0.0.1:5175/?view=leaders`
- API: `http://127.0.0.1:8025`

The disposable PostgreSQL test database currently has no published snapshot,
so the live preview intentionally renders the no-data state. The accepted
desktop and mobile populated-state captures are stored under
`web/.temp/reset-leaders/`.

## Workspace Note

The shared worktree still contains the same 11 pre-existing, unstaged
formatting differences. They were neither reverted nor included in Task 12
commits. Full Python static checks were repeated against committed HEAD in
`.temp/task12-verify`.
