# Reset Leaders Evidence Acceptance

Recorded: 2026-09-22

## Delivered

- Publication-bound read APIs for the limit-up ladder, continuity feedback,
  role candidates, and per-security role evidence.
- Strict external role names, filtered coverage partitions, explicit
  unavailable states, Decimal continuity values, and current authorization
  checks without GET-time role computation.
- A dense date-by-board leaders workspace with date, topic, and six-role
  filters; tied candidates; continuity metrics; role changes; and comparison
  reasons.
- A single global security drawer that preserves the selected publication and
  topic evidence context without changing the page filter.
- Six-role evidence with factor values, units, windows, coverage, true source
  time, knowledge cutoff, and separate supporting, opposing, and missing
  evidence.
- Chinese presentation labels with raw codes retained in expandable audit
  evidence, conservative all-topic status aggregation, and validated reset
  stock responses.
- The complete local workflow from a ladder security to watchlist, a
  security-bound revisioned note, and return to the original leaders filters.
- Responsive candidate and role-change records that remain readable at a
  375-pixel viewport while preserving semantic desktop tables.

## Verification

| Check | Result |
|---|---|
| PostgreSQL role API and publication regression | 236 passed |
| Frontend unit tests | 168 passed |
| Reset Playwright suite | 32 passed |
| Frontend production build | passed |
| Default entry bundle | 314,201 bytes, within 315,000-byte gate |
| Final specification review | passed |
| Final frontend quality review | passed |
| Ruff lint on committed HEAD | passed |
| Ruff format check on committed HEAD | 151 files formatted |
| Python compileall | passed |
| PRD contract checker | REQ=57, endpoints=29, examples=29, JSON=30 |
| Alembic heads | `0007b_role_validation_inputs` |
| Browser smoke check | one main landmark, no page overflow, no runtime errors |

## Commits

- `a961a04` `feat: add role and ladder read APIs`
- `47caa9a` `fix: align role API evidence contracts`
- `a2fd9c5` `fix: preserve role evidence source time`
- `ebd51b2` `fix: complete role API quality signals`
- `6020e6e` `feat: deliver leaders evidence workflow`
- `283d09f` `fix: preserve leaders evidence context`
- `968a1a8` `fix: harden leaders evidence presentation`
- `18781ae` `fix: adapt leaders tables for mobile`

## Local Preview

- Web: `http://127.0.0.1:5175/?view=leaders`
- API: `http://127.0.0.1:8025`

The disposable PostgreSQL test database currently has no published snapshot,
so the live preview intentionally renders the no-data state. The accepted
desktop and mobile populated-state captures are stored under
`web/.temp/reset-leaders/`.

## Workspace Note

The shared worktree still contains the same 11 pre-existing, unstaged
formatting differences. They were neither reverted nor included in Task 12
commits. Full Python static checks were repeated against committed HEAD in
`.temp/task12-verify`.

---

## Addendum — EX11 Sector Rotation Timeline (Task 24)

Recorded: 2026-10-21

### Delivered

- `build_rotation(published_runs)` as a deterministic pure projection
  (`src/dragon_radar/roles/rotation.py`): events globally ordered by
  `(at, sector_id)` ascending with unique pairs; five change tokens
  (`segment_start`, `unchanged`, `expanded`, `contracted`, `reorganized`);
  comparability breaks across rule-version changes or non-comparable
  membership pools; a frozen reason allowlist
  (`first_observation`, `rule_version_changed`, `no_core_candidate`);
  point-in-time visibility by `available_at`; future publications never flow
  back and historical membership is never backfilled from current
  constituents.
- Publication-time materialization (reads never recompute) of the rotation
  block and `GET /api/topics/rotation`, with required `date` and
  `snapshot_id`, `days` bounded to 1-250 (default 30), optional `sector`,
  fail-closed archived-block validation, a `503 DATA_UNAVAILABLE` mapping
  for damaged blocks, and a null block meaning nothing is publishable for
  the snapshot.
- Each wire event carries the `snapshot_id` of the publication that served
  that day: historical nodes open six-role evidence pinned to their serving
  publication while the anchor stays pinned to the current publication.
- Reset workbench `RotationTimeline` lazy section on the leaders page: a
  fixed 10/30/60/120 trading-day window selector (default 30),
  deterministic lexicographic sector lanes, comparable/break badges, Chinese
  reason labels, core-security buttons exposing
  `data-snapshot`/`aria-pressed`/`aria-expanded`/`aria-controls`, a nested
  lazy evidence panel, selected-node invalidation on any
  days/sector/date/snapshot change, and `SectionBoundary` isolation for lazy
  chunk failures with no private-build leakage.
- A fail-closed frontend validator mirroring the wire contract: exact key
  sets, canonical UUID shape, `(at, sector)` ordering and uniqueness,
  comparable/reason mutual-exclusion rules, an anchor-snapshot guard, and
  envelope echo `409` checks for evidence responses.
- Browser-level E2E (11 tests) with a dedicated fixture: the deterministic
  two-sector timeline, window re-pagination, sector filtering and restore,
  historical-vs-anchor evidence snapshot pinning, four-path selected-node
  clearing (close, window, sector, in-app date change, plus full-page anchor
  navigation), keyboard open/close with managed entry focus, null/empty/
  malformed states, lazy chunk abort isolation, and panel-plus-page axe
  cleanliness with left/right containment for every node at 375px.

### Verification

| Check | Result |
|---|---|
| PostgreSQL rotation suite (`tests/test_reset_rotation.py`) | 120 passed, 0 skipped |
| Targeted Ruff lint / format check | passed; 4 files already formatted |
| Rotation frontend unit tests | 87 passed |
| Full frontend unit suite | 30 files, 697 passed |
| Reset rotation E2E | 11 passed; 33/33 under `--repeat-each 3` |
| Full reset Playwright suite | 124 passed |
| Frontend type check (`tsc --noEmit`) | passed |
| Frontend production build | passed |
| Default entry bundle | 314,869 bytes, within the 315,500-byte gate |
| Independent per-slice specification and quality reviews | approved across four slices; minor findings closed or backlogged |

The E2E fixture windows are inclusive UTC calendar days
(`start = end - (days - 1)`), while the backend derives windows from trading
calendar sessions. The frontend treats the window as an opaque echoed string,
so the difference cannot alter rendered behavior; a trading-calendar fixture
model is owed if the UI ever consumes window semantics directly.

### Commits

- `6b602e3`: deterministic sector-rotation timeline pure function.
- `ba1bbba`: materialize EX11 at publication and serve the timeline route.
- `78d7c0b`: harden reason allowlist, version and window guards.
- `d36d269`: expose the serving snapshot id on EX11 wire events.
- `73025bb`: render the EX11 rotation timeline in the reset workbench.
- `30713dd`: contain rotation lazy failures and tighten timeline a11y coverage.
- `bfedcfc`: browser-level EX11 E2E coverage and dedicated fixture.
- `8cb794b`: restore reset E2E gates for cross-section alerts and LHB badge wrap.
- `a4eac9c`: tighten EX11 E2E per slice-4 review findings.

### Backlog

The two mandatory Playwright assertions carried over from the slice-3
quality review (Important #2: evidence requests pinned to the serving
snapshot; evidence region cleared on days/sector/date changes) are delivered
in `bfedcfc` and strengthened in `a4eac9c`. Non-blocking items still open,
using the original review numbering:

- Slice-3 new Minor 1: `aria-controls` on non-pressed core buttons points to
  an unmounted region id (a unit test currently pins the present behavior);
  tighten when the component is next touched.
- Slice-3 new Minor 2: `rotationEvidenceId` concatenates tuple segments
  without `encodeURIComponent`; identifiers permit non-space characters, so
  the impact is a11y-only and low likelihood.
- Slice-3 new Minor 3: closing evidence does not return focus to the
  triggering core button; entry focus to the close button is managed.
- Slice-3 new Minor 4: the region `aria-live` plus the inner Suspense
  `role="status"` may double-announce loading; needs real screen-reader
  verification rather than a code change.
- Slice-3 original Minor 6: the read validator accepts the unreachable
  non-comparable plus `no_core_candidate` shape; the materializer never
  emits it, and tightening both read sides together is deferred to a future
  rotation version.
- Slice-3 original Minor 7 (timeline node dot protrusion) was closed in CSS
  and the 375px containment is now machine-verified by the dual-edge E2E
  assertions.

Slice-4 review items fixed in `a4eac9c`: exact per-change label counts, the
120-day option behavior, `sector` pinned on evidence requests, in-app date
change clearing without a reload, and left/right node containment. Slice-4
items deliberately deferred: the E2E fixture re-declares `OLD` and the
six-role evidence payload from `market.ts` (tech debt; that fixture is
CRLF-sensitive), the LHB badge-wrap CSS selector also matches the in-dialog
permission badge with no observed side effect, the natural-day fixture
window documented above, and a few style-class locators that sit alongside
semantic roles. The `waitForTimeout(750)` in `reset-navigation.spec.ts`
predates Task 24 and is queued for separate cleanup. No findings labeled
"M3", "N3", or a mixed-calendar item exist in the recorded slice reviews.
