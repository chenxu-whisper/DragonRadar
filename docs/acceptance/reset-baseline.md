# PRD Reset Baseline

Recorded: 2026-09-21

## Source

- Branch: `feature/prd-reset`
- Starting commit: `1d1dd7605534ea774a031c70c33c270c00a6f6c0`
- Worktree: `.worktrees/prd-reset`
- Baseline format-only commit: `7e5a052`
- Requirements: `docs/DragonRadar_开发需求PRD.md`
- Implementation plan: `docs/superpowers/plans/2026-09-20-prd-reset-refactor.md`

The existing worktrees form one ancestry chain:

`data-foundation -> research-workbench -> private-review -> role-topic-evidence`

The reset branch starts from the last committed cumulative branch. It does not copy
uncommitted changes from the other worktrees.

## Existing Worktree Changes

- `research-workbench`: local changes in `README.md` and `docs/dependencies.md`.
- `private-review`: local changes in owner/review source and tests, plus
  `web/src/hooks/useLocation.ts` and `web/src/pages/Analysis.tsx`.
- `data-foundation` and `role-topic-evidence`: clean when the reset worktree was
  created.

Those local changes remain in their original worktrees and are not treated as reset
implementation.

## Baseline Verification

| Check | Evidence |
|---|---|
| Python lint | `uv run ruff check .` passed |
| Python format | Three pre-existing files were normalized in `7e5a052`; changed-file check passed |
| Python bytecode | `uv run python -m compileall -q src tests` passed |
| Frontend unit | 97 tests passed before reset implementation |
| Frontend build | Default production build passed |
| Default bundle | Bundle boundary check passed |
| PostgreSQL | Existing dedicated `dragon_test` container available at loopback port 55432 |
| Full backend | `1666 passed, 10 skipped` in 244.76s; skips are live TLS checks requiring `DRAGON_TEST_PRIVATE_CA` |

## Production Enablement Dependencies

- No production market, index, topic, minute, LHB, news, regulatory, or model
  provider and license has been approved in this task.
- Development may use synthetic fixtures with explicit labels.
- Missing external inputs block only their production activation. They do not
  authorize fabricated values, rules, latency claims, or model evaluation results.
- Full release also requires the separately recorded shadow-run, recovery,
  performance, public-gateway, and accessibility evidence.
