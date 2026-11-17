# Reset Role Contract Acceptance

Recorded: 2026-09-22

## Delivered

- Exact, case-sensitive mapping from the six internal role names to
  `emotion_leader`, `trend_leader`, `zhongjun`, `genfeng`, `buZhang`, and
  `jieli`.
- Reset-facing evidence projection that preserves every candidate row,
  capability, internal status, rank, rule version, and reason.
- A separate, fail-closed approval allowlist. Existing `role-v1` remains a
  historical market-pool-median rule and is not relabeled as an approved
  index-based reset rule.
- FAC-R01 explicit-index relative strength for 20/60/120-session windows,
  FAC-R02 moving-average position and slope, FAC-R03 prior-window highs and
  drawdowns, and FAC-C01/C02 capacity, turnover, and directional amount
  observations.
- Independent factor observations with exact Decimal values, units, windows,
  coverage partitions, reasons, source versions, adjustment version, calendar
  version, and knowledge cutoff.
- Fail-closed handling for missing or duplicate bound rows, unavailable index
  inputs, incomplete windows, incompatible currencies, source authorization,
  topic identity and coverage, and Decimal arithmetic failures.

## Verification

| Check | Result |
|---|---|
| Task 10 and role/field regression | 475 passed |
| Final specification review | passed |
| Final targeted code-quality review | passed |
| Ruff lint | passed |
| Ruff format check | 4 Task 10 files formatted |
| Python compileall | passed |
| PRD contract checker | REQ=57, endpoints=29, examples=29, JSON=30 |
| Git diff check | passed |

## Commits

- `0db5f5d` `feat: adapt role evidence to reset contracts`
- `97b29b7` `fix: satisfy task 10 role evidence contracts`
- `5ff61b7` `fix: harden task 10 role factor contracts`

## Production Gate

No new reset role thresholds, approved benchmark feed, or production rule
version was supplied. The reset approval allowlist therefore remains empty.
Research factors are available with explicit missing reasons, while formal
candidate publication remains blocked until a new immutable rule version and
its source inputs are approved.
