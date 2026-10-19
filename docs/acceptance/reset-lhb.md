# Reset LHB Acceptance

Recorded: 2026-10-19

## Scope And Decision

Task 22-5 verifies the automated release contracts for the synthetic-only
Task 22 LHB (Dragon-Tiger list, 龙虎榜) disclosure implementation. The frozen
migration and installed-wheel audit commit is
`efadac79bf117bb81f85d0d2c91b501bc0ab7505`. The code head at recording is
`d84a3b79c84b78c89aa66f562d5d222e58e925e8`, a post-evidence commit that
changes only CI wiring and the web Playwright default config; rebuilding the
wheel at that commit produced the identical wheel SHA-256, so the shipped
Python bytes audited at `efadac7` are unchanged. This document itself is the
docs-only C2 commit.

| Decision | Result | Basis |
| --- | --- | --- |
| Automated engineering acceptance | **GO** | Targeted backend 664 passed / 4 skipped; full backend 3273 passed / 15 skipped in 30m12s; Ruff 0 findings; scoped format green (198 files); compileall passed; web 540 tests / 27 files; production build passed; default entry bundle 314852 / 315500 bytes; focused reset Playwright 20 tests green (reset-lhb 7, reset-realtime 13); installed wheel fresh plus all 12 supported starts upgraded to `0012_lhb` ready; named-container restore drill verified backup `sha256=17f1f8fa53fba43c3048ebd7b01aa54a16413ce43dc0d6e3940023093f4b750d`, elapsed 9.844s, cleanup complete |
| Production enablement | **NO_GO** | No licensed real Dragon-Tiger disclosure feed and no real seat-directory mapping evidence was supplied or exercised |
| Branch cut | **NO_GO** | This task does not establish approved upstream data rights, independent release approval, or a clean release candidate |
| Release | **NO_GO** | External production gates remain blocked; version stays 0.1.0; no merge or tag occurred |
| Deployment | **NO_GO** | No production authorization, real-input evidence, or operations approval exists; nothing was deployed |

This is an automated engineering GO only. It is not evidence that the system
has processed real production disclosures, that any exchange or vendor has
licensed the inputs, or that production use is authorized.

## CI Contract

Three focused LHB gates were added to the Data Foundation workflow, matching
the existing Task 21R realtime pattern:

- PostgreSQL 17 `verify` job: `uv run pytest tests/test_reset_lhb.py -q`.
- `web_quality` job: `npm test -- --run tests/unit/reset-lhb.test.ts`.
- `web_e2e_reset` job: `npx playwright test tests/e2e/reset-lhb.spec.ts`.

The focused browser command deliberately contains no inline
`--config playwright.reset.config.ts`: the web quality gate
(`tests/unit/quality-gates.test.ts`) forbids the string
`npx playwright test --config` in the workflow. The default
`web/playwright.config.ts` detects an explicitly passed focused reset spec
(`reset-realtime.spec.ts` or `reset-lhb.spec.ts`) on the process arguments,
clears the reset ignore pattern, and starts the dev server with
`VITE_RESET_WORKBENCH=1`, exactly as the Task 21R focused realtime gate does.
The explicit reset config remains the environment owner for full reset runs
(`npm run test:e2e:reset`) and was used for the local two-spec drill recorded
below.

The initial C1 line used an inline `--config`, which the full web unit suite
caught (539 passed, 1 failed in `quality-gates.test.ts`). Commit `d84a3b7`
corrected the line and generalized the default-config argument detection; the
suite then passed 540 / 27, and the corrected focused command was run directly
and passed 7 / 7.

No Task 21R gate was removed: backend full pytest with warnings as errors and
coverage, focused realtime suites, web full unit tests, default and private
builds, bundle check, all Reset and legacy E2E jobs, installed-wheel
migrations, Ruff, format, and compileall remain. The installed-wheel gate
audits repository HEAD end to end; at the `0012_lhb` release head its fresh
upgrade and its supported-start matrix (including a start at `0012_lhb`)
automatically cover the `lhb_evidence` sentinel preservation and readiness
contract.

## Automated Evidence

| Check | Result |
| --- | --- |
| Backend targeted (LHB + realtime + release migrations + storage) | 664 passed, 4 skipped in 401.86s |
| Backend full (`pytest -q -W error -p no:randomly`) | 3273 passed, 15 skipped in 1812.72s (30m12s) |
| Ruff check | All checks passed (0 findings) |
| Scoped Ruff format (`src tests scripts`) | 198 files already formatted |
| Python compileall (`src tests scripts`) | passed |
| Web full unit | 540 tests across 27 files passed |
| Default production build | passed (TypeScript check plus 2464 transformed modules) |
| Default entry bundle | 314852 / 315500 bytes |
| Reset two-spec Playwright drill (`--config playwright.reset.config.ts`, reset-lhb plus reset-realtime) | 20 passed in 32.7s (reset-lhb 7, reset-realtime 13) |
| Focused LHB browser gate in CI form (`npx playwright test tests/e2e/reset-lhb.spec.ts`) | 7 passed in 14.4s |

The red-then-green quality gate result above was the only failure encountered
while closing this gate; every recorded number was executed and observed in
this worktree. No pytest suite was rerun after the already-accepted backend
runs, and the static, web, wheel, and restore steps were run serially.

### Task 22 Commit Chain

| Commit | Date | Purpose |
| --- | --- | --- |
| `4ec250c` | 2026-10-14 | feat: define LHB disclosure models |
| `1444225` | 2026-10-14 | fix: retain LHB source scope and permission state in aggregation |
| `3c258b2` | 2026-10-14 | fix: source-scope LHB revision selection and harden batch validation |
| `7018e8f` | 2026-10-14 | feat: add immutable 0012_lhb schema and release contracts |
| `ed18572` | 2026-10-15 | fix: drop redundant LHB catalog index and tighten schema tests |
| `86f8dc2` | 2026-10-15 | feat: store LHB disclosure revisions |
| `c2b50d8` | 2026-10-15 | fix: complete LHB repository semantics |
| `85af421` | 2026-10-15 | fix: harden LHB repository review findings |
| `8cd5ee6` | 2026-10-15 | feat: expose LHB read contracts |
| `417fa71` | 2026-10-16 | fix: mark LHB seat identity provenance in history |
| `58c25ed` | 2026-10-16 | feat: add LHB seats panel |
| `1aec04e` | 2026-10-16 | fix: scope LHB coverage and keys by source |
| `efadac7` | 2026-10-16 | chore: verify LHB release contracts (frozen audited commit) |
| `d84a3b7` | 2026-10-19 | fix: route focused LHB e2e gate through reset spec detection (post-evidence CI/web wiring only) |

The focused LHB suite covers disclosure authorization and source scoping,
batch and revision validation, immutable revision history, the authorized
0012 sentinel (one authorized disclosure revision with two seats including an
empty amount, mapping version, and readiness state), read contracts, seat
history provenance, and synthetic front-end rendering including null-versus-zero
amounts and unknown-identity presentation.

## Migration And Wheel Evidence

- Current migration head: `0012_lhb`; release revision count: 20;
  `supported_from` entries: 12.
- `0012_lhb.py` manifest digest:
  `sha256=e62922e105a816669277e0806ec4d5b74db2d060d240d627be26f58ffc765528`,
  1220 bytes (manifest bytes).
- Wheel: `dragon_radar-0.1.0-py3-none-any.whl` (394696 bytes),
  SHA-256 `4cd1344a06f72e93643a220ee66a2dac7b84820068b1f526c9ec6da04a51ba10`.
- Audited commit:
  `efadac79bf117bb81f85d0d2c91b501bc0ab7505`.
- The verifier imported `dragon_radar` from an isolated installed-wheel
  virtual environment outside the source checkout.
- A fresh database migrated to `0012_lhb` and reported ready.
- Rebuilding the wheel at post-evidence `d84a3b7` produced byte-identical
  archive bytes and the same SHA-256; the fix commit does not alter shipped
  Python.
- The release manifest and schema contract validations passed.

All 12 supported starting revisions upgraded to `0012_lhb` with
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
| `0012_lhb` | `lhb_evidence` |

For `0009_regulation`, the complete before/after sentinel field sets and values
were identical, as recorded in `.temp/task22/wheel-evidence.json`. The stale
2026-10-14 wheel artifacts under `.temp/task22/dist` were rebuilt and
overwritten before this run.

## Backup/Restore Drill

A disposable PostgreSQL 17 container mirrored the release workflow:

- Image: `postgres:17-alpine` pinned by digest
  `sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73`.
- Container: `dragon-radar-task22-migration-release`, label
  `dragon-radar.task21-release=true`, ephemeral host port 53941 bound to
  127.0.0.1, admin database `postgres`, a 24-byte random hex password, and
  readiness confirmed with `pg_isready`.
- Backup format: custom. Result line:
  `backup restore verified: sha256=17f1f8fa53fba43c3048ebd7b01aa54a16413ce43dc0d6e3940023093f4b750d elapsed_seconds=9.844000 cleanup=complete`.
- Post-restore verification: revision `0012_lhb`, `restored_ready=true`,
  `sentinel_preserved=true`, `permissions_preserved=true`,
  `app_semantics_verified=true`, 62 canonicalized schema objects.
- Counts were unchanged (audit events, import batches, snapshots, and source
  grants: 1 each), measured data loss was 0 rows with a 0.0s loss window, and
  source/restore fingerprints both matched
  `a7581ac4c4b519824edfe52dfb1b24878b7dd85be8b36b82147388b24c77bf21`.
- The drill enforces a non-superuser application role: the role is created
  `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION`, and verification fails
  if the restored database does not continue using that non-superuser role.
- Quiescence was enforced with a 4.875s write freeze; rollback mode is
  backup/restore only; targets RPO 3600s / RTO 14400s with
  `approval_status=suggested_unapproved`.
- Cleanup removed the backup file, the disposable role, and both disposable
  databases; the one-off container was force-removed afterward. The separate
  `dragon-radar-task22-test-db` container was not reused or touched. The local
  credential temp files were deleted; no secret is recorded here.

Full machine-readable detail: `.temp/task22/restore-evidence.json`; console
log: `.temp/task22/06-restore.log`.

## Synthetic-Only Limitation

All Task 22 LHB inputs are synthetic fixtures. No licensed real Dragon-Tiger
disclosure feed, exchange disclosure file, or provider seat directory was ever
connected, ingested, or validated. Synthetic fixtures do not establish provider
permission, publication timing, field semantics, disclosure completeness,
trading-day behavior, or authoritative reason codes. No synthetic result is
claimed as real, licensed, or production data.

## No Natural-Person Attribution

Seats and brokerage branches in the fixtures use synthetic directory
identifiers and demonstration labels (for example the same-name-seat
demonstration). They do not identify, represent, or attribute any real natural
person or institution. The `identity_state` value (`known` or `unknown`)
expresses only whether a synthetic seat identifier is reachable through the
synthetic directory mapping; it is not a statement about a person's or
institution's identity, history, or conduct, and unknown mapping must not be
read as an attribution of wrongdoing.

## External Gates

| External gate | Status | Required evidence |
| --- | --- | --- |
| `licensed_real_input` | **BLOCKED** | Independently trusted licenses for real exchange Dragon-Tiger disclosures and real execution evidence; no real disclosure-data license exists |
| `approved_official_rules` | **BLOCKED** | Approved official disclosure rule sources, scopes, versions, and release authority |
| `real_model_evaluation` | **BLOCKED** | Human-annotated evaluation on real disclosures and seats against approved criteria |
| `public_sharing_permit` | **BLOCKED** | Explicit legal/compliance permission for public sharing, including attribution handling |
| `twenty_trading_day_shadow_run` | **BLOCKED** | At least 20 real trading days of reviewed shadow evidence |

Production enablement additionally requires a real, auditable seat-directory
mapping evidence chain (the current mapping is a synthetic `mapping_version`
fixture only), approved source field/unit semantics, provider availability and
latency objectives, operational monitoring/incident ownership, and
release/deployment approval. None is granted by this task.

## Evidence Sources

The observed command logs and JSON evidence are retained under
`.temp/task22/`. The directory is git-ignored; these are local execution
records, not separately trusted attestations and not production input.

| Command / artifact | Evidence under `.temp/task22/` | Key result |
| --- | --- | --- |
| Targeted backend (LHB, realtime, release migrations, storage) | `01-backend-targeted.log` | 664 passed, 4 skipped in 401.86s |
| Full backend with warnings as errors | `02-backend-full.log` | 3273 passed, 15 skipped in 1812.72s |
| `uv run ruff check .` | `03-static-ruff-check.log` | All checks passed |
| `uv run ruff format --check src tests scripts` | `03-static-format.log` | 198 files already formatted |
| `uv run python -m compileall -q src tests scripts` | `03-static-compileall.log` | passed |
| `npm test -- --run` (web) | `04a-web-vitest.log` | 540 tests / 27 files passed |
| `npm run build` (web) | `04b-web-build.log` | TypeScript plus production build passed |
| `npm run check:bundle` (web) | `04c-web-bundle.log` | entryBytes 314852 against 315500 |
| `npx playwright test --config playwright.reset.config.ts tests/e2e/reset-lhb.spec.ts tests/e2e/reset-realtime.spec.ts` | `04d-web-playwright-reset.log` | 20 passed (7 LHB, 13 realtime) |
| `npx playwright test tests/e2e/reset-lhb.spec.ts` | `04e-web-playwright-lhb-focused.log` | 7 passed; CI-form focused gate verified |
| `uv build --out-dir .temp/task22/dist` | `05-wheel-build.log`, `dist/` | Rebuilt sdist and `dragon_radar-0.1.0-py3-none-any.whl` (SHA-256 `4cd1344a...`) |
| Installed-wheel migration verification with `--audited-commit efadac79bf117bb81f85d0d2c91b501bc0ab7505` | `05-wheel.log`, `wheel-evidence.json` | Isolated import passed; fresh plus all 12 supported starts reached `0012_lhb`, sentinels preserved, ready; exit 0 |
| Named-container backup/restore drill with the same audited commit | `06-restore.log`, `restore-evidence.json` | backup SHA-256 `17f1f8fa...`, 9.844s, cleanup complete |

The database URL used a CI-only random password over an ephemeral local port;
no secret is recorded in the logs or this document. No merge, tag, version
bump, image publication, release, or deployment occurred at the Task 22-5
gate; package version remains 0.1.0 and the branch remains unmerged.
