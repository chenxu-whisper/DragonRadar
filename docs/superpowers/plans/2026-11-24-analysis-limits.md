# B6 Analysis Limits Implementation Plan

> **For agentic workers:** Use subagent-driven-development for isolated UI work when capacity is available; the parent executes backend tasks in this approved worktree.

**Goal:** Bound custom analysis admission, computation and output without changing read-only ranking identity or the exact private HTTP input.

**Architecture:** Keep commands/cache identity unchanged; freeze a separate positive `max_wall_clock_ms` on job rows. Validate actual calendar/pool capacity before admission and worker execution. Pass a monotonic cooperative checkpoint through the actual series/window loops, reject oversized serialized results before any cache write, and use the existing owner-only queue protocol in the UI.

**Tech Stack:** Python 3.12, pytest, Pydantic, SQLAlchemy/PostgreSQL 17, React/TypeScript, Vitest, Playwright.

No staging, commits or migration execution against production databases are authorized.

### Task 1: Capacity and monotonic budget

Files: create `src/dragon_radar/analysis_jobs/limits.py`, `tests/test_analysis_limits.py`; modify `src/dragon_radar/analysis_jobs/worker.py`, `src/dragon_radar/readmodels/analysis.py`.

- [ ] Write parameterized boundary tests for 250/251 sessions, 5000/5001 IDs, 1250000/1250001 observations, and 16 MiB output. Assert `AnalysisCapacityExceeded.code == "analysis_capacity_exceeded"` and reject rather than truncate.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/test_analysis_limits.py -q` with `PYTHONPATH=src`, recording the missing-feature RED log.
- [ ] Implement exact constants `MAX_SESSIONS=250`, `MAX_SECURITIES=5000`, `MAX_OBSERVATIONS=1250000`, `MAX_RESULT_BYTES=16*1024*1024`, `MAX_NONTERMINAL_JOBS=20`, `MAX_WALL_CLOCK_MS=300000`. Validate integer nonnegative dimensions; serialize with `json.dumps(default=str, allow_nan=False, ensure_ascii=True, separators=(",",":"))` and enforce encoded bytes.
- [ ] Implement `AnalysisBudget(max_wall_clock_ms, clock=time.monotonic)`; its checkpoint raises `AnalysisBudgetExceeded` at `elapsed_ms >= max_wall_clock_ms`. The constructor captures a monotonic start once; no thread termination or invented I/O preemption.
- [ ] Add optional keyword-only `checkpoint: Callable[[], None] | None` to `_window`, `_series`, `_display_curves` and call it inside security/session/price loops and before completion. Existing materialized reads pass no checkpoint and retain all values.
- [ ] Validate actual session/pool dimensions before `_window` and before final success; run fixed-clock tests proving breach mid-window and zero partial result/cache.
- [ ] Run focused GREEN plus existing pure analysis materialization tests.

### Task 2: Frozen storage and admission

Files: modify `analysis_jobs/models.py`, `analysis_jobs/repository.py`, `analysis_jobs/routes.py`; create migration `0017_analysis_limits.py` after `0016_clock_lineage`, append database-backed cases to `tests/test_analysis_limits.py`.

- [ ] Write real PostgreSQL tests proving one owner can admit exactly 20 nonterminal jobs, changed-key 21st rejects, same-key replay succeeds, and a cache hit creates an isolated succeeded record without consuming a slot.
- [ ] Add `max_wall_clock_ms` to `AnalysisJobRecord` and the SQL table with default 300000 and positive bounded check; do not add it to command, command hashing or `_safe_record.input`.
- [ ] Preserve authorization-first and same-key conflict behavior. Under the existing owner/key transaction, acquire a per-owner advisory lock before counting nonterminal rows:

```python
self._lock(connection, f"analysis-admission:{owner_id}")
pending = connection.scalar(
    select(func.count()).select_from(analysis_jobs).where(
        analysis_jobs.c.owner_id == owner_id,
        analysis_jobs.c.status.in_(("queued", "running")),
    )
)
if cached is None and pending >= MAX_NONTERMINAL_JOBS:
    raise AnalysisCapacityExceeded("owner_nonterminal_jobs")
```

- [ ] Validate input before admission, validate cache/result bytes before returning/cache insertion, and freeze the budget in the inserted job. Map capacity errors to HTTP 400 with the structured code; budget errors become terminal worker failures with no cache.
- [ ] Add only a new reversible migration; coordinate exact schema/release metadata after all new migrations exist.
- [ ] Run focused PostgreSQL admission/worker/lease/cache regression on `dragon_test` with disposable schemas, retaining full raw logs.

### Task 3: Private submit and polling UI

Files: create `web/src/features/analysis-jobs/{client.ts,types.ts,OwnerAnalysisJobPanel.tsx}` and dedicated unit/E2E tests; modify `web/src/features/analysis/ResetAnalysis.tsx` and `web/src/App.tsx` only at explicit dynamic private extension hooks.

- [ ] Write fake-clock tests for 4999/5000/5001 ms ordinary-read handoff; public initiates no private request, owner without compute consent sees an explicit action.
- [ ] Build same-origin no-store owner POST with CSRF and exact `snapshot_id,start,end,benchmark,adj,symbols,idempotency_key`. Freeze the selection including display context separately; retain the same key for uncertain explicit retry and prevent duplicate submit.
- [ ] Immediately GET the accepted UUID job, poll once per 1000 ms with no overlap and a 15000 ms request timeout. Denial/principal/navigation/PUB changes invalidate generations and clear protected references; in-memory session state only.
- [ ] Deep-validate terminal input/result identity, partitions and curve dates. Re-read the exact cache-backed rank/compare URL on success rather than manufacturing a daily envelope. qfq/hfq remain unsupported.
- [ ] Run focused Vitest and Playwright, then verify public chunks exclude the private client in the final five-build gate.

### Task 4: Integration evidence

- [ ] Preserve B1 sorting and preexisting dirty files; inspect diffs before shared wiring.
- [ ] Run Ruff, compileall, TypeScript, focused Python/Vitest tests and independent specification then quality review.
- [ ] Execute the final combined source-bound acceptance only once all modules are integrated; P95 remains <=2 seconds, and external real-data/remote-CI gates remain separately stated.
