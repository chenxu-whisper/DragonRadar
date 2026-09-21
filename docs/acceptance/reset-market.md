# Reset Market Overview Acceptance

Recorded: 2026-09-22

## Delivered

- Publication-bound market read models for indexes, breadth, limit-up
  observations, promotion, prior-limit-up feedback, topic diffusion, and the
  date-by-board-count heatmap.
- FAC-M01 market breadth and MA20 breadth, FAC-M02 same-pool amount change
  against the prior 20 sessions, and FAC-M03 independent MA20, MA60, and
  non-annualized 20-return volatility windows.
- Explicit numerator, denominator, coverage, units, source times, and
  unavailable reasons. Unknown touch data and unavailable net inflow remain
  null rather than being inferred.
- Versioned read-only endpoints 01-04 and 18, including bounded query
  validation, publication-pinned search, legacy materialization compatibility,
  and direct snapshot-summary lookup for stable deep links.
- Reset overview UI for raw observations and factor evidence, real
  publication-bound member filtering, topic drill-down context, heatmap date
  navigation, and local-review provenance preservation.

## Verification

| Check | Result |
|---|---|
| Task 09 and directly affected PostgreSQL regression | 226 passed |
| Frontend unit tests | 145 passed |
| Reset market and review E2E | 7 passed |
| Frontend production build | passed |
| Final specification review | passed |
| Final targeted code-quality review | passed |
| Ruff lint | passed |
| Ruff format check | 136 files formatted |
| Python compileall | passed |
| PRD contract checker | REQ=57, endpoints=29, examples=29, JSON=30 |
| Git diff check | passed |

The PostgreSQL verification used the dedicated `dragon_test` database.

## Commits

- `a9405c2` `feat: deliver versioned market overview`
- `1bdd614` `fix: complete Task09 market specifications`
- `9ab8119` `fix: address Task09 quality findings`
- `41e9c2c` `fix: disclose topic source time in market basis`
- `319cc30` `test: align market assertions with persisted contracts`
- `b8970d6` `fix: restore reset overview security entry points`

## Production Gate

No licensed supplier feed or historical topic-membership sample was provided.
The implementation therefore preserves explicit unavailable states for sector
net inflow and historical topic analysis. Synthetic fixtures validate behavior
but do not establish production data coverage.
