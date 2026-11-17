# Task18 Minimal Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the six approved Task18 verification, evaluation, adoption, deployment, and E2E gaps without a migration or unrelated cleanup.

**Architecture:** Keep evidence verification pure and fail closed, with exact Decimal token matching and structured inference metadata. Keep evaluation data external and attested per sample, compute independent split gates, and reuse one pending local note command only while its result is uncertain. Preserve the existing compile-time public/private AI boundary.

**Tech Stack:** Python 3.12, Decimal, pytest, React 19, TypeScript 5.9, IndexedDB, Vitest, Playwright, Vite, Docker.

---

### Task 1: Exact Numeric And Inference Verification

**Files:**
- Modify: `tests/test_reset_ai_verification.py`
- Modify: `src/dragon_radar/ai/verification.py`
- Modify: `web/src/features/ai/client.ts`
- Modify: `web/src/features/ai/TaskPanel.tsx`
- Modify: `web/src/features/ai/tasks.ts`
- Modify: `web/tests/unit/reset-ai.test.ts`
- Modify: `web/tests/e2e/reset-ai.spec.ts`

- [ ] Add tests proving `2` cannot support displayed `12`, precision/unit mismatches fail, and approved recomputations pass.
- [ ] Run the focused pytest tests and confirm failures are caused by substring matching and absent metadata validation.
- [ ] Add tests requiring non-empty cited inference evidence, explanatory confidence metadata, and supported regulatory conclusion metadata while leaving quoted evidence text unscanned.
- [ ] Run the focused pytest tests and confirm the old scalar confidence schema fails.
- [ ] Implement exact numeric-token validation and structured inference validation.
- [ ] Update the TypeScript validator, editor payload, rendering, and fixtures to the same schema.
- [ ] Re-run focused Python and web unit tests until green.

### Task 2: Evaluation Lineage And Independent Split Gates

**Files:**
- Modify: `tests/test_reset_ai_verification.py`
- Modify: `scripts/evaluate_ai.py`
- Modify: `tests/fixtures/ai_eval/manifest.json`
- Modify: `docs/ai-evaluation.md`

- [ ] Add tests requiring immutable input/output lineage hashes and explicit human label provenance plus attestation.
- [ ] Add tests for sample leakage, lineage leakage, and a passing development split with a failing frozen split.
- [ ] Run the evaluator tests and confirm the current aggregate evaluator fails them.
- [ ] Validate per-sample lineage and attestation without synthesizing repository fixture attestations.
- [ ] Compute and report development and frozen-test metrics and gate each split independently.
- [ ] Keep the checked-in empty fixture truthfully blocked with zero human samples and no real model results.
- [ ] Re-run evaluator tests and the CLI until green.

### Task 3: Replay-Safe Local Adoption Commands

**Files:**
- Modify: `web/tests/unit/reset-ai.test.ts`
- Modify: `web/src/features/ai/tasks.ts`
- Modify: `web/src/features/local-review/LocalReview.tsx`
- Modify: `web/src/pages/News.tsx`
- Modify: `web/src/pages/SecurityDetail.tsx`

- [ ] Add unit tests for a lost response followed by an exact command replay with no duplicate note.
- [ ] Add tests proving task revision, strategy, or exact payload changes produce a fresh note ID and key.
- [ ] Run the focused Vitest file and confirm the command lifecycle is absent.
- [ ] Implement a reusable pending adoption-command cache keyed by task revision, strategy, and canonical payload.
- [ ] Route LocalReview, News, and SecurityDetail adoption through the cache and clear only after confirmation.
- [ ] Re-run focused and full web unit tests until green.

### Task 4: Private Build And Public/Private E2E Boundaries

**Files:**
- Modify: `deploy/private.Dockerfile`
- Modify: `web/playwright.ai.config.ts`
- Create: `web/playwright.ai-public.config.ts`
- Modify: `web/tests/e2e/reset-ai.spec.ts`

- [ ] Add a private Dockerfile assertion test or source check that fails until both Reset and private AI flags are set.
- [ ] Split the public zero-owner/AI-request case into a public config with AI disabled and remove all skip/early-return behavior.
- [ ] Keep the private config focused on positive panel presence and private AI workflows.
- [ ] Run public and private AI E2E independently and confirm both pass.
- [ ] Build default and private variants; verify the private bundle contains the AI feature and the public bundle does not.

### Task 5: Full Verification And Commit

**Files:**
- Verify only; no migration files.

- [ ] Run focused Task18 and Task17 PostgreSQL backend suites, then the requested backend regression.
- [ ] Run the evaluation CLI and record the expected blocked external gate.
- [ ] Run all web unit tests, public/private AI E2E, default/private builds, and bundle checks.
- [ ] Run Ruff, format check, compileall, `git diff --check`, and verify the Alembic head remains `0010_ai_tasks`.
- [ ] Inspect working and staged diffs, stage only Task18 files/hunks, commit, and report the SHA and exact results.
