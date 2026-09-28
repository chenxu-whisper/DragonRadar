# Task17 Quality Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the five approved Task17 quality gaps without a migration or unrelated cleanup.

**Architecture:** Keep lease-loss handling in `AIWorker`, response shaping in the AI route, review wording in the repository, and CLI length validation at argument parsing. Validate the configured provider with Windows' spawn serializer during worker construction so an invalid provider cannot claim work.

**Tech Stack:** Python 3.12, multiprocessing spawn, FastAPI, SQLAlchemy/PostgreSQL, pytest, Ruff.

---

### Task 1: Worker lease-loss boundaries

**Files:**
- Modify: `tests/test_reset_ai_tasks.py`
- Modify: `src/dragon_radar/ai/worker.py`

- [ ] Add tests that make `load_input`, `renew`, expected terminal writes, retry writes, and success writes raise `Conflict`; assert `run_once()` returns `None` and performs no fallback failure write.
- [ ] Run the focused tests and confirm they fail because `Conflict` escapes or is converted to a provider failure.
- [ ] Catch only `Conflict` at those repository boundaries, narrow provider failure handling to `RuntimeError`, and preserve `_ProviderCleanupError` and arbitrary exceptions.
- [ ] Add and run tests proving one lease loss does not stop `run_batch()` or a later `run_forever()` iteration.

### Task 2: Poll response draft metadata

**Files:**
- Modify: `tests/test_reset_ai_tasks.py`
- Modify: `src/dragon_radar/http/routes/ai.py`

- [ ] Extend the poll response test to require direct `content_draft` plus nullable `revision`, `content_draft_source`, `content_draft_created_at`, and `ai_generated`.
- [ ] Confirm the focused route test fails on the missing top-level fields.
- [ ] Populate only the selected draft's non-sensitive metadata and rerun the route test.

### Task 3: Windows spawn-safe provider contract

**Files:**
- Modify: `tests/test_reset_ai_tasks.py`
- Modify: `src/dragon_radar/ai/worker.py`
- Modify: `src/dragon_radar/ai/provider.py`

- [ ] Change the unpicklable-provider regression to require a fixed sanitized constructor `ValueError` before `claim()`.
- [ ] Confirm the test fails under the current deferred process-start behavior.
- [ ] Validate with `multiprocessing.reduction.ForkingPickler` in `AIWorker.__init__` and raise `ValueError("AI provider must support Windows spawn serialization")` without chaining.
- [ ] Document that provider objects and captured state must be spawn-serializable.

### Task 4: CLI worker identifier limit

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/dragon_radar/cli.py`

- [ ] Add parser assertions that 100 characters are accepted and 101 are rejected.
- [ ] Confirm the 101-character case fails.
- [ ] Enforce the exact `VARCHAR(100)` maximum in `_ai_worker_id` and rerun the CLI tests.

### Task 5: Decision-specific review receipts

**Files:**
- Modify: `tests/test_reset_ai_tasks.py`
- Modify: `src/dragon_radar/ai/repository.py`

- [ ] Add accept, edit, and reject assertions with idempotent replay for each note.
- [ ] Confirm accept/reject fail because they currently mention editing and re-verification.
- [ ] Derive the note from the persisted decision and resulting revision, keeping re-verification language only for edit.

### Task 6: Verification and commit

**Files:**
- Verify only; no migration files.

- [ ] Run the isolated PostgreSQL `tests/test_reset_ai_tasks.py` suite and relevant CLI/migration tests.
- [ ] Run `uv run ruff check .`, `uv run ruff format --check .`, and `uv run python -m compileall -q src tests`.
- [ ] Inspect `git diff --check`, the complete diff, and the staged diff; stage only Task17 files or hunks.
- [ ] Commit once and report the commit SHA and exact verification results.
