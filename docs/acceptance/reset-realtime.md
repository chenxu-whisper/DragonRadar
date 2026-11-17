# Reset Realtime Acceptance

Recorded: 2026-10-10

## Scope And Decision

Task 21R.6 verifies the automated release contracts for the synthetic-only
Task 21R realtime implementation. The implementation head is
`a2887b4a1cc83d6635e50892f1d278b386aa6f72`; the independently committed
validation correction is
`b2b204520f7d558dd43a11e6ce34bcbfc4a0ab9a`.

| Decision | Result | Basis |
| --- | --- | --- |
| Automated engineering acceptance | **GO** | Focused/full backend, static, frontend, browser, migration, schema, manifest, and installed-wheel gates passed |
| Production enablement | **NO_GO** | No licensed realtime minute, order-book, or sector-flow input was supplied or exercised |
| Branch cut | **NO_GO** | This task does not establish approved upstream freshness, independent release approval, or a clean release candidate |
| Release | **NO_GO** | External production gates remain blocked |
| Deployment | **NO_GO** | No production authorization, real-input evidence, or operations approval exists |

This is an automated engineering GO only. It is not evidence that the system
has processed real production data, that any provider has licensed the inputs,
or that production use is authorized.

## CI Contract

The CI contract test followed TDD: it first failed once against the workflow
without focused realtime commands, then passed once after the workflow change.
The PostgreSQL 17 `verify` job now runs the focused backend realtime suite, the
web quality job runs the focused realtime unit suite, and the Reset E2E job
runs the focused realtime browser suite.

The existing gates were retained: backend full pytest with warnings as errors
and coverage, Web full unit tests, default build, bundle check, Reset full E2E,
installed-wheel migrations, Ruff, format, and compileall. The CI semantic test
parses command arguments and asserts both the focused commands and these
original gates; it does not replace full gates with substring checks.

## Automated Evidence

| Check | Result |
| --- | --- |
| CI semantic TDD | red: 1 failed; green: 1 passed |
| Backend focused | 425 passed, 4 skipped in 280.84s |
| Backend full | 3034 passed, 15 skipped in 1630.80s |
| Ruff | passed |
| Scoped Ruff format | 190 files already formatted |
| Python compileall | passed |
| Web full unit | 435 Vitest and 10 Node tests passed |
| Original realtime E2E | 11 passed |
| Reset full E2E | 97 passed |
| Default build | passed |
| Reset build | passed |
| Default entry bundle | 314852 / 315500 bytes |

The first full backend run exposed one stale regulation test assertion that
still expected migration head `0010_ai_tasks`. Commit `b2b2045` changed only
that assertion to the current `0011_realtime` head. The subsequent full run is
the 3034-pass green result above.

The focused realtime suite covers RT01-12: authorization, source time and
delay, stable security identity, trading-session validation, duplicate
ingestion, out-of-order data, immutable revision history, no missing-minute
zero fill, stream-gap degradation, price/volume window formulas, anomaly
duplicate/cooldown behavior, and deterministic replay with public no-write
behavior.

## Migration And Wheel Evidence

- Current migration head: `0011_realtime`.
- Wheel: `dragon_radar-0.1.0-py3-none-any.whl`.
- Wheel SHA-256:
  `fb43429a40ae5f923b7519f550c7dfa66615eed594773197030f48764dadca03`.
- Audited commit:
  `b2b204520f7d558dd43a11e6ce34bcbfc4a0ab9a`.
- The verifier imported `dragon_radar` from the wheel environment's installed
  `site-packages`, outside the source checkout.
- A fresh database migrated to `0011_realtime` and reported ready.
- The release manifest and schema contract validations passed.

All 11 supported starting revisions upgraded to `0011_realtime` with
`sentinel_preserved=true` and `ready=true`:

| Starting revision | Sentinel |
| --- | --- |
| `0001_foundation` | `foundation_archive` |
| `0003_private_review` | `private_audit` |
| `0004a_topic_attempt_ranges` | `topic_attempt_batch` |
| `0006_market_readmodels` | `security_market_attempt` |
| `0007_role_readmodels` | `role_run_root` |
| `0007c_analysis_jobs` | `analysis_job` |
| `0008_news` | `news_article_revision` |
| `0009_regulation` | `legacy_regulation_graph` |
| `0009d_reg_event_complete_set` | `regulation_event_provenance` |
| `0010_ai_tasks` | `ai_task` |
| `0011_realtime` | `realtime_evidence` |

For `0009_regulation`, the complete before/after sentinel field sets and values
were identical.

## Synthetic-Only Limitation

All Task 21R automated inputs are synthetic fixtures. No licensed realtime
minute feed, order-book feed, or intraday sector-flow feed was available,
ingested, or validated. Synthetic fixtures do not establish provider
permission, production latency, market completeness, source availability,
official field semantics, or real trading-day behavior. No synthetic result is
claimed as real, licensed, or production data.

## External Gates

| External gate | Status | Required evidence |
| --- | --- | --- |
| `licensed_real_input` | **BLOCKED** | Independently trusted licenses and real quote/minute/order-book/sector-flow execution evidence |
| `approved_official_rules` | **BLOCKED** | Approved rule sources, scopes, versions, and release authority |
| `real_model_evaluation` | **BLOCKED** | Real-provider evaluation against approved criteria |
| `public_sharing_permit` | **BLOCKED** | Explicit legal/compliance permission for public sharing |
| `twenty_trading_day_shadow_run` | **BLOCKED** | At least 20 real trading days of reviewed shadow evidence |

Production enablement additionally requires approved source field/unit
semantics, provider availability and latency objectives, operational
monitoring/incident ownership, and release/deployment approval. None is granted
by this task.

## Evidence Sources

The observed command logs and installed-wheel JSON are retained under
`.temp/task21r/`. They are local execution evidence, not separately trusted
attestations and not production input.

| Complete command | Evidence under `.temp/task21r/` | Key result |
| --- | --- | --- |
| `uv run pytest tests/test_reset_realtime.py tests/test_release_migrations.py tests/test_storage.py -q` | `backend-focused.log` | 425 passed, 4 skipped; exit 0 |
| `uv run pytest -q -W error` | `backend-full-green.log` | 3034 passed, 15 skipped; exit 0 |
| `uv run ruff check .` | `static-gates.log` | All checks passed |
| `uv run ruff format --check src tests scripts` | `static-gates.log` | 190 files already formatted |
| `uv run python -m compileall -q src tests scripts` | `static-gates.log` | Passed |
| `npm test -- --run` | `web-gates.log` | The runner forwarded `--run` to Vitest only; 435 Vitest tests passed |
| `npm test` | `web-npm-full.log` | The full runner executed both gates; 435 Vitest and 10 Node tests passed; exit 0 |
| `npm run build` | `web-gates.log` | Default production build passed |
| `npm run check:bundle` | `web-gates.log` | Default entry bundle was 314852 bytes against the 315500-byte limit |
| `VITE_RESET_WORKBENCH=1 npm run build` | `web-gates.log` | Reset production build passed |
| `npx playwright test tests/e2e/reset-realtime.spec.ts` | `web-gates.log` | 11 focused realtime browser tests passed |
| `npm run test:e2e:reset` | `web-gates.log` | 97 full Reset browser tests passed |
| `uv build --out-dir .temp/task21r/dist` | `wheel-build.log` | Built the source distribution and `dragon_radar-0.1.0-py3-none-any.whl` |
| `uv run python scripts/verify_release_migrations.py --database-url "$DRAGON_TEST_DATABASE_URL" --wheel .temp/task21r/dist/dragon_radar-0.1.0-py3-none-any.whl --output .temp/task21r/wheel-evidence.json --audited-commit b2b204520f7d558dd43a11e6ce34bcbfc4a0ab9a` | `wheel-verify.log`, `wheel-evidence.json` | Installed-wheel import passed; fresh and all 11 supported upgrades reached `0011_realtime`, preserved sentinels, and reported ready; audited commit matched `b2b2045`; exit 0 |

The database argument above is an environment-variable placeholder; no secret
is recorded. Failed precondition and earlier-attempt logs are diagnostic only
and are not green evidence. Final green evidence is limited to the logs and
JSON named in the table.

No merge, tag, version bump, image publication, release, or deployment
occurred at the Task 21R.6 gate.

## Task 21R.7 Review Closure (Recorded 2026-10-14)

This addendum records the independent Task 7 reviews without rewriting the
historical Task 21R.6 evidence above. The migration chain stayed at
`0011_realtime`; no migration, schema contract, or release manifest entry
changed during review closure.

### Post-Evidence Commits (in order)

| Commit | Date | Purpose |
| --- | --- | --- |
| `47d179a` | 2026-10-11 | Task 21R.6 CI integration and release contract verification |
| `53cf0e2` | 2026-10-11 | Make realtime evidence traceable |
| `a7fa0dc` | 2026-10-11 | Close realtime backend review findings |
| `50a9590` | 2026-10-11 | Close realtime frontend review findings |
| `64c4a4d` | 2026-10-11 | Filter revoked realtime source reads |
| `ba171a1` | 2026-10-11 | Preserve realtime source distinctions |
| `f94c74e` | 2026-10-14 | Bound authorization source probes (constant statement/row budget) |
| `51cb20d` | 2026-10-14 | Keep authorization probes bounded on filter misses (final head) |

### Review Findings Closed On 2026-10-14

The final independent specification compliance review of `f94c74e` passed
(five scenario groups equivalent to the pre-refactor authorization semantics,
including revoked snapshot, revoked grant, future `captured_at`, pinned
`batch_id`, and multi-source filtering).

The independent code quality review initially returned "with fixes":

- **Important I1 (closed by `f94c74e` + `51cb20d`):** the first refactor still
  scanned the whole batch/child history in some plan shapes. The final
  authorization statement uses a non-correlated child-window `visible_horizon`
  existence check (promoted to an InitPlan/One-Time Filter) plus per-source
  `JOIN LATERAL (...) LIMIT 1` probes whose `(source_id, batch_id)` correlation
  enters the child index condition. Independent `EXPLAIN (ANALYZE, BUFFERS)`
  measurements against zero-statistics schemas: full filter misses touch 0
  rows (minutes) or one linear pass over the child table (unscoped anomalies:
  4/128/400 rows for 2/64/200 batches per source), versus about 80,000 child
  rows for two sources with 200 batches under the previous shape; homogeneous
  hits stay at 2 rows per history node, inside the <=8 budget.
- **Important I2 (closed by `51cb20d`):** the previous plan-budget test only
  covered homogeneous hits. `test_minutes_and_anomalies_authorization_reads_stay_linear_on_filter_misses`
  now asserts the two-scale miss budget, including a slope check that fails for
  an O(sources x batches^2) plan while accepting the linear plan, with explicit
  allowance comments and failure diagnostics.

Re-review verdict: **approved (Yes)**, no Critical or Important issues open.
Two Minor observations remain explicitly accepted: the unscoped anomalies
horizon is an intentional one-pass linear scan (documented in code; a future
covering index may be evaluated outside Task 21R), and a redundant `LIMIT 1`
inside an `EXISTS` scalar is harmless.

### Re-Verified Evidence At Head `51cb20d`

| Check | Result |
| --- | --- |
| `tests/test_reset_realtime.py` | 224 passed, 0 skipped |
| Focused closure suite (realtime + release migrations + storage) | 442 passed, 4 skipped; skips are the pre-existing platform-conditional cases |
| Full backend `pytest -q -W error` | Not re-run for this review-only change under the scoped-rerun rule; the 3034 passed/15 skipped full run remains the Task 21R.6 result at `b2b2045`, and the next full run is scheduled at the Task 22 release gate |
| Ruff check / scoped format / compileall / `git diff --check` | passed |
| Wheel | `dragon_radar-0.1.0-py3-none-any.whl`, SHA-256 `20afb8f3cef09fad009efa59446222416cdfaf3cc81ab10a24d8bd47c4f4046c` |
| Installed-wheel verification | fresh install plus all 11 supported starting revisions upgraded to `0011_realtime` with sentinels preserved and `ready=true`; imported from an isolated installed environment; audited commit `51cb20d` |

The wheel and migration evidence above supersedes the Task 21R.6 wheel hash for
the current branch head; the Task 21R.6 row remains the historical audit of
commit `b2b2045`.

### Gate Decision And Handoff

Automated engineering acceptance remains **GO** for Task 21R. Production
enablement, branch cut, release, and deployment remain **NO_GO** for the same
external reasons listed above (all five external gates stay BLOCKED; inputs
remain synthetic fixtures only).

The Task 22 entry gate is met: migration head `0011_realtime`, backend and
installed-wheel verification green, independent specification review approved,
independent code quality review approved, and a clean committed head. No
merge, tag, version bump, image publication, release, or deployment occurred.
