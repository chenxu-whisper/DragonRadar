# Ranking Direction Acceptance, 2026-11-24

Status: original and final narrow independent spec/quality reviews PASS; final corrected-source five builds PASS.
Evidence is local synthetic engineering verification, not remote CI,
production data, C2/C5 recertification, deployment, or full REQ-V03 closure.

## Scope

- Approved design A: optional Reset URL `rank_order`, optional ranking API `order`.
- Worktree: `E:/ByteDance/Projects/Code/.temp/DragonRadar-AllRequirements-20261124`.
- Branch: `feature/all-requirements-20261124`; base `d90a02e`.
- No staging, commits, pushes, other-worktree edits, DB, Docker, or daily-service changes.
- Core implementation/test edits frozen after the final verification below.
- Existing `test_reset_analysis.py` publication/404 fix is preserved.

Production files: `src/dragon_radar/http/routes/analysis.py`,
`web/src/state.ts`, and `web/src/features/analysis/{query.ts,validation.ts,ResetAnalysis.tsx}`.
Tests/fixtures: `tests/test_reset_analysis.py`,
`web/tests/unit/{reset-state,state,reset-analysis}.test.ts`,
`web/tests/fixtures/{ranking-direction,contract-c2-scenes}.ts`,
and `web/tests/e2e/reset-analysis.spec.ts`.
Documentation: this receipt and `docs/contracts/workbench-api.md`.
Parent-owned broader requirement documents are not B1 implementation changes.

## Behavior And Defect Analysis

The prior implementation rejected API direction, ignored explicit ranking URL
direction, selected only metric defaults on header clicks, and validated only
default response order. The implementation adds the approved direction path
without altering calculation/cache/archive identity.

Both directions retain security-ID ASC ties, null-last/null-rank rows, and
competition ranks. Global URL `order` remains ignored by ranking. Invalid and
duplicate ranking direction normalizes with the existing notice. New metric
selection clears the explicit direction; active-header clicks toggle it.
Request, validation, header, and current-page CSV use the same effective
descriptor. Existing full-path resource identity protects late responses and
prevents exporting old rows under new metadata; no hook changes were needed.

## Generated Cases

- Backend four metrics by two directions, fixed golden ID/rank expectations,
  negative/zero values, ties, nulls, all-null values, and row-copy immutability.
- Precomputed ASGI routes preserve omitted-direction defaults and archive,
  coverage and exclusion facts; invalid/uppercase/empty/duplicate/unknown keys
  and comparison `order` reject before storage.
- Custom-cache ASGI requests assert exact `AnalysisJobCommand` equality and
  identical `cache_key` for both directions; cached JSON remains unchanged.
- State tests cover all metric defaults in old URLs with absent/`asc`/`desc` global
  order, explicit direction serialization, invalid/duplicate normalization,
  non-Reset isolation, and standalone details-return context.
- Descriptor/validation tests cover all four metrics by two directions,
  rejection of opposite-order responses, tie identity, null/all-null rows,
  and invalid requested direction.
- Browser tests exercise all metric toggles in both directions, decoded CSV
  IDs/ranks/metadata, refresh, browser history, actual pager controls, details
  return, switching to a new metric's default, pending export suppression,
  and a held route completed after returning to the latest direction.

The direction fixture independently projects synthetic rows; golden assertions
do not use production sorting to construct expectations. C2 scene rows now
follow requested direction and competition ranks. Extended C2 was not run.

## Executed Verification

All paths below are relative to ROOT unless a `web/` working directory is noted.
Raw logs live in `ROOT/.temp/ranking-direction/`.
Python used `ROOT/.venv/Scripts/python.exe`; `PYTHONPATH=ROOT/src`.
Python bytecode, pytest and Ruff caches were redirected below the evidence root.

| Command | Result | Raw Log |
| --- | --- | --- |
| `.venv/Scripts/python.exe -m pytest tests/test_reset_analysis.py -k rank_direction -q --tb=short -o cache_dir=.temp/ranking-direction/pytest-cache` | RED: 24 failed, 1 passed; absent sorter argument and HTTP 400 for valid direction | `backend-red-corrected.log` |
| `web/: node node_modules/vitest/vitest.mjs run tests/unit/reset-state.test.ts tests/unit/state.test.ts tests/unit/reset-analysis.test.ts` | RED: 27 failed, 104 passed | `frontend-red.log` |
| Same focused Vitest command | GREEN: 131 passed | `frontend-green.log` |
| `web/: node node_modules/@playwright/test/cli.js test --config ../.temp/ranking-direction/playwright.config.mts --grep "ranking direction"` | RED: six failures before page wiring | `browser-red-all.log` |
| `.venv/Scripts/python.exe -m pytest tests/test_reset_analysis.py -q --tb=short -o cache_dir=.temp/ranking-direction/pytest-cache` | GREEN after formatting: 113 passed | `backend-green-formatted.log` |
| `web/: node scripts/test-runner.mjs` | GREEN: 1,467 Vitest tests in 50 files; 211 Node tests | `frontend-full-final.log` |
| `web/: node node_modules/@playwright/test/cli.js test --config ../.temp/ranking-direction/playwright.config.mts` | GREEN: 13 passed | `browser-green-final.log` |
| `web/: node node_modules/@playwright/test/cli.js test --config ../.temp/ranking-direction/playwright.extended.config.mts` | Parent: acceptance 35 passed; navigation 7 structurally skipped because its test requires port 5174 | `parent-browser-extended.log` |
| `web/: node node_modules/@playwright/test/cli.js test --config ../.temp/ranking-direction/playwright.navigation.config.mts` | Parent: navigation 7 passed, zero skipped, after checking port 5174 free | `parent-browser-navigation.log` |
| `web/: node node_modules/typescript/bin/tsc --noEmit` | Parent fresh verification exit 0 | `parent-typecheck.log` |
| `.venv/Scripts/python.exe -m ruff check src/dragon_radar/http/routes/analysis.py tests/test_reset_analysis.py` | All checks passed | `ruff-check-final.log` |
| `.venv/Scripts/python.exe -m ruff format --check src/dragon_radar/http/routes/analysis.py tests/test_reset_analysis.py` | Two files already formatted | `ruff-format-check-final.log` |
| `.venv/Scripts/python.exe -B -m ruff check src tests scripts --cache-dir .temp/ranking-direction/ruff-cache` | Parent fresh full Python lint: all checks passed | `parent-ruff-check.log` |
| `.venv/Scripts/python.exe -B -m compileall -q src tests scripts` | Parent fresh full Python compilation exit 0 | `parent-compileall.log` |
| `git -c core.whitespace=cr-at-eol diff --check -- <B1 tracked paths>` | Exit 0; Windows line-ending conversion warnings only | `diff-check-crlf-final.log` |

JS test commands used Git Bash outer timeouts: 120 seconds focused, 300 seconds
full suite, and 180 seconds for the focused browser suite. Playwright launched
only its isolated Vite instance at `127.0.0.1:5291`, with installed `msedge`,
using local `.temp` configs. The isolated port was checked before use and had
no listener after the completed run; no preexisting daily services were stopped.
The original and Unicode ranking browser runs emitted no server/config warnings.

## Retained Failures And Limits

- Original backend RED included an immutable `mappingproxy` deep-copy test
  setup error. It was corrected to compare the mutable JSON payload; original
  and corrected logs are both retained.
- Initial isolated Playwright config attempts failed at ESM loading; logs
  `browser-red.log` and `browser-red-corrected.log` remain unchanged.
- Early Vite config bundling emitted missing optional `esbuild` warnings;
  switching the isolated config to native loading removed them.
- First browser GREEN attempt: 12 passed, one new test expected unchanged
  `return_view` despite existing details navigation changing it to `analysis`.
  The test now seeds that navigation field correctly; ranking-condition and
  exact-return-URL assertions remain. `browser-green-attempt1.log` is retained.
- Ruff initially reported six import/line-format issues; fixes and final
  checks have separate logs. No business behavior changed during formatting.
- Plain `git diff --check` reported CRLF line ends as trailing whitespace
  (exit 2). Its raw `diff-check-final.log` is preserved; the CRLF-aware command
  above passes without rewriting the frozen core files.
- RED traces/screenshots are retained in `browser-red-evidence/`.
- The required test-preparation script ran, but `utree` bootstrap reports
  unsupported Windows/Cygwin. Required flush was attempted and failed with
  exit 127 (binary unavailable). See `prepare.log` and `utree-flush.log`.
- C2 browser suites, full backend suite,
  remote CI and C5 were not run in B1. This receipt does
  not invent evidence for those gates.

Independent spec review passed with unchanged code hashes; see
`ROOT/.temp/ranking-direction/spec-review.md`. Independent quality review
is still pending.

Parent extended browser runs cover 35 acceptance and 7 navigation cases,
in addition to the 13 ranking cases: 55 distinct successful cases.
The original seven skips remain in the retained first-run log; they were
resolved by a separate run on the test's required explicit port, without
changing test assertions. These older fixtures have unstubbed optional
rotation/research/LHB/news requests and emitted proxy ECONNREFUSED warnings
against the absent local API on 8020. Outcomes are bounded UI/CSV/navigation
verification, not console-clean or real-backend integration evidence.

Parent evidence correction: the original receipt named `typecheck-final.log`
and `compileall-final.log`, which were absent. Those entries do not establish
retained evidence. The parent ran the TypeScript check and full Python
lint/compilation again on the unchanged B1 source; the three `parent-*` logs
above exist and the actual shell commands returned exit 0.

## Final Unicode Correction

The parent found one case missed by the original independent reviews:
Python sorts accepted security IDs by Unicode code points, while the newly
added JavaScript `<` tie validator used UTF-16 code units. A valid tied
response with U+E000 before U+10000 was rejected by the browser.
The final validator compares code points; the independent fixture uses its
own point-array comparator. Allowed IDs, metric order, rank, Decimal,
archive and cache semantics are unchanged.

Twenty-six fixed golden cases exercised the defect before correction
(`unicode-red.log`: 26 failed, 70 passed), then all 96 focused cases passed
(`unicode-green.log`). The updated full frontend result is
**1493 Vitest + 211 Node passed**, zero failures/skips (`unicode-frontend.log`).
The 13 ranking browser cases passed again (`unicode-browser.log`, 34.6s).
The 35 acceptance and 7 navigation cases predate this three-file correction;
they remain evidence for unchanged surrounding behavior, not a fresh rerun
of those suites on the corrected validator.

Original five-build verification passed all default/sharing/reset/private/owner
build and bundle checks, but it also predates the Unicode correction:
`ROOT/.temp/five-builds/run-2026-11-24T03-38-43-748Z-ZPRn11/run.json`.
Its SHA256 is `a45167d3f7c66736acca18a21a0d36467a56f72f58abb7a0b5ae6db4fe998816`.
Final corrected-source builds and narrow reviews are recorded separately;
old successful receipts are not rebound to the changed code.

## Final Disposition

Final narrow specification and quality reviews passed:
`ROOT/.temp/ranking-direction/spec-review-final.md` and
`ROOT/.temp/ranking-direction/quality-review-final.md`.
The original reviews remain historical; the Unicode correction and its
fresh reviews supersede their three affected file hashes.

All five corrected-source production builds and all ten build/bundle
commands passed in
`ROOT/.temp/five-builds/run-2026-11-24T03-45-48-523Z-aV9S4D/run.json`
(finished `2026-11-24T03:46:38.235Z`).
Receipt SHA256:
`ce22c675efce1fc6b696bdbb66f093458ac3a113fccc208d1c410e56868bacf4`.
`parent-five-builds-final.log` records the actual runner result.

The parent delivery check bound all 12 B1 production/test files to that
final build's source inventory and confirmed no subsequent code drift,
unchanged d90 base commit, and no staged files:
`ROOT/.temp/ranking-direction/delivery-receipt-20261124.json`.
This is verified uncommitted source, not a new tested commit.
Documentation was subsequently updated to describe these observed results;
those documentation edits are not described as part of the earlier build inventory.

B1's direction subclause is implemented and has the bounded evidence above.
Remaining REQ-P01/REQ-V03 responsibilities, C2/C5 full recertification,
remote CI and real-input gates are not closed by this report.
