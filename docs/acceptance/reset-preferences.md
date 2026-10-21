# Reset Preferences & Command Palette Evidence Acceptance

Recorded: 2026-10-21

Task 25: dark theme, density preference, and the command palette for the
reset workbench. REQ-V09/V10/V11; acceptance AC-V09/V10/V11 and AC-V06
(contrast in both themes, keyboard accessibility). Backend, database, and
Python code are untouched; no new dependency was added.

Two design decisions were explicitly approved before implementation:

1. **Light theme zero regression** — light tokens map to the current
   production values (`--accent: #1759ad`, page background `#fff`, …).
   The PRD light refresh (`#2563EB` family) was deliberately not adopted
   in this task; only the dark theme uses PRD dark values, calibrated
   within the same PRD color band where contrast required it.
2. **Loaded-data-only palette index** — stock/sector candidates come only
   from data already loaded in the current session through existing
   authorized read models. The palette issues no HTTP requests; empty
   results are reported honestly and never guessed.

## Delivered

- Preference pure model (`features/preferences/settings.ts`): theme/density
  parsing with corrupt-value fallback, storage read/write, application to
  the root element, system-theme detection, and Ctrl/Cmd+K shortcut
  classification including IME composition guards.
- Command pure model (`features/preferences/commands.ts`): whitelist
  construction, filtering (group order, lexicographic labels, 50-item
  cap), execution rebuilt from live context on every invocation, dynamic
  id validation (non-empty, ≤200 chars, no `.`/`..`), and a
  pre-bind FIFO report queue (cap 16) for the lazy search-index chunk.
- Semantic token system in `styles.css` (`:root` light mapping plus
  `[data-theme="dark"]` and `[data-density]`), applied across the existing
  hard-coded colors; comfortable 44px / compact 36px table row heights that
  never shrink clickable control hit areas; chart theme linkage with the
  reduced-motion behavior preserved.
- First-paint synchronous preference bootstrap in `main.tsx` (no flash),
  same-tab `dr:prefs-changed`, cross-tab `storage`, and system
  `prefers-color-scheme` follow only when no explicit `dr:theme` exists.
- Lazy `CommandPalette` (Ctrl/Cmd+K and header trigger): combobox/listbox
  ARIA, cyclic keyboard navigation, Home/End, IME composition pass-through
  on both open and execute paths, Esc focus return (including after Tab
  moves focus out), no focus trap, dirty-draft inline block, native open
  `<dialog>` precedence, and an error boundary so a chunk failure cannot
  blank the workbench.
- In-session memory index (`features/preferences/search-index.ts`,
  separate lazy chunk): payload sanitization, kind+id deduplicated merge
  with a deterministic 500-item cap; reporting wired only on already
  loaded data — Overview breadth members and sector flow, Leaders ladder
  cells/candidates/status sectors, and ResetAnalysis ranking securities.
- Build isolation: the command palette and its search index are
  dead-code-eliminated from both the default and private builds — no
  chunks and no palette UI strings; only the internal `dr:index-items`
  event-name constant remains, by design. The preference controls still
  emit a 2,296-byte lazy chunk (`PreferenceControls`) in both builds
  because the header declares `lazy(() => import(...))` unconditionally at
  module scope; it is merely render-gated by `{reset && <Suspense>}`, so
  it is unreachable at runtime outside the reset workbench, and its
  storage helper is inlined into that chunk. Reset-only navigation copy
  never enters the default entry.

## Acceptance Criteria

- **AC-V09 (dark theme):** dark tokens on `html[data-theme="dark"]`,
  `meta[name="color-scheme"]` sync, persistence in `dr:theme`, reload
  retention, explicit choice over system theme, corrupt JSON / invalid
  values fall back at first paint, and snapshot/URL/business values are
  unchanged by a theme switch.
- **AC-V10 (density / touch):** 44px comfortable / 36px compact table row
  heights (±1px on single-line header cells), density changes do not
  touch query/sort/compare state, and at a 375px compact viewport all key
  header controls and mobile navigation anchors retain ≥44px hit areas
  within the viewport.
- **AC-V11 (command palette):** two open paths, six command groups,
  full keyboard path, current-value marks, empty state, IME guard,
  navigation preserving snapshot/date, sector/security navigation,
  denied-session narrowing, dirty-draft guard, zero new `/api/` requests,
  and a localStorage whitelist of exactly `dr:theme`/`dr:density`.
- **AC-V06 (contrast / keyboard):** WCAG 2.x contrast computed in-browser
  from computed styles with ancestor alpha compositing, in both themes;
  focus ring sampled from the real `:focus-visible` outline; palette
  operable entirely by keyboard with focus returned to the trigger.

## Verification

| Check | Result |
|---|---|
| Frontend unit tests | 31 files, 813 passed (116 reset-preferences cases) |
| Frontend type check (`tsc --noEmit`) | passed, 0 errors |
| New reset-preferences E2E spec | 16 passed |
| New spec `--repeat-each 3` | 48/48 passed |
| Full reset Playwright suite | 140 passed (124 prior + 16 new) |
| Default production build | passed |
| Default entry bundle | 307,931 bytes, within the 315,500-byte gate |
| Default build forbidden-marker scan | 0 hits across 6 static assets; no palette chunks; one 2,296 B `PreferenceControls` lazy chunk is emitted but render-gated (see Delivered) |
| Reset build palette lazy chunks | CommandPalette 4,608 B, search-index 1,149 B, persist 263 B |
| Private build (`VITE_PRIVATE_REVIEW=1`) | 0 palette chunks or palette UI strings (the internal `dr:index-items` event constant remains by design); PrivateApp 42,942 B |
| Independent reviews | specification and quality reviews approved for all four slices; slice 3 second round verified six closure items; the slice 4 acceptance record was tightened after a documentation-only quality pass (build-isolation wording and `tsc --noEmit`) |

Contrast measured in Microsoft Edge via `getComputedStyle` and the WCAG
relative-luminance formula (evidence artifact:
`web/.temp/slice4-contrast.json`, gitignored):

| Sample | Foreground / background | Light | Dark | Threshold |
|---|---|---|---|---|
| Body text / page | text-primary on bg-page | 14.28 | 17.60 | ≥ 4.5:1 |
| Secondary text / header surface | text-secondary on snapshot-header | 6.32 | 8.41 | ≥ 4.5:1 |
| Solid primary button | accent-contrast on accent-solid | 6.84 | 4.77 | ≥ 4.5:1 |
| Focus ring / surface | focus-ring outline | 6.84 | 4.26 | ≥ 3:1 |

The dark small-text up-color token is calibrated to `#ea6065`
(5.78/5.25/4.74 on the three dark surfaces); thresholds were never
lowered. The dark solid blue uses the dedicated `--accent-solid: #2563eb`
because `#3b82f6` only reaches about 3:1 for white text.

## Commits

- `78f0d5f` `feat(web): add preferences and command palette pure functions (Task 25, slice 1)`
- `945b4eb` `feat(web): apply theme/density preferences with dark mode tokens (Task 25, slice 2)`
- `3176f2d` `feat(web): add command palette with in-session loaded-data index (Task 25, slice 3)`
- `6e4d3d2` `test(web): cover theme/density and command palette end-to-end (Task 25, slice 4)`

## Known Boundaries and Backlog

- **Loaded-only candidates.** The palette indexes only data loaded in the
  current session: Overview breadth members plus hot sectors/sector flow,
  Leaders ladder cells, candidates, and status sectors (status sectors
  carry no name and display by id), and ResetAnalysis ranking securities.
  There is no whole-market search endpoint by design. ResetAnalysis
  `topic_id` is deliberately not reported as a sector command: it belongs
  to the topic-snapshot ID space (same source as news topics), not the
  roles-domain `sector_id` space, so it could never resolve on the leaders
  page.
- **Chart up color is separate from UI text color.** Chart candlestick up
  color in dark mode remains the PRD original `#e5484d` (graphical
  elements meet the 3:1 threshold); only the UI `--up` token used for
  small text/borders is calibrated to `#ea6065`.
- **Corrupt preferences are never rewritten.** Bootstrap and parsing fall
  back read-only; invalid `dr:theme`/`dr:density` values remain in storage
  until the user makes an explicit choice.
- **Lazy chunk rejection is not retried within a session (low).** React 19
  caches a rejected `lazy` payload; if the CommandPalette chunk fails to
  load the error boundary closes the palette and a full page reload is
  needed to retry. The workbench itself is unaffected.
- **Denied session edge behaviors (info).** Ctrl/Cmd+K still opens the
  palette when authorization is denied (the trigger button is hidden); all
  review/stock/sector commands are absent and the index is empty. Closing
  from that state returns focus to `body` rather than the retry button.
- **Defense-in-depth note (info).** The palette navigation mapping performs
  its own goto view check against the module-level view list; the
  authoritative enforcement is `executeCommand` rebuilding the whitelist
  from live context, where denied sessions cannot reach review.
- **Native dialog lazy-load race (low).** In the narrow window while the
  security-drawer chunk is loading there is no `dialog[open]` yet, so a
  Ctrl/Cmd+K can open the palette first; the native top-layer dialog then
  covers it and the first Escape dismisses both. The state self-heals.
- **E2E coverage proxies.** At 375px, "key status columns not clipped" is
  asserted via the breadth up/down button edges plus document-level
  no-horizontal-overflow, not per table cell. The deviceScaleFactor 2 cases
  assert no overflow and no page errors rather than explicit chart canvas
  presence; the reduced-motion case additionally pins canvas rendering on
  the candlestick page. Its stylesheet check that the palette trigger
  computes `transition-duration: 0s` is non-discriminating on its own —
  the trigger declares no base transition, so the value is `0s` without
  reduced motion as well; the discriminating reduced-motion behavior,
  ECharts animation being disabled, is asserted separately in
  `hardening.spec.ts` via `getOption().animation === false`, while the
  global CSS motion-off rule has no dedicated E2E assertion. The
  public-sharing build's review-item removal cannot be exercised in the
  reset E2E environment and is covered by unit tests and the
  build-isolation checks.
- **Production isolation Docker stack** (`test:e2e:production-isolation`)
  was not executed locally; isolation is evidenced locally by the dual
  builds and the forbidden-marker/static-asset gate, with the full Docker
  stack exercised in CI.
