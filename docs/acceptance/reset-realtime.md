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
occurred. Task 21R.7 was not started.
