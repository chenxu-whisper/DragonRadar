# All Requirements: Scoped B8 Residual Matrix

Date: 2026-11-24. Documentation-only child handoff; no application tests executed.

## Scope And Evidence Rules

ROOT is `E:/ByteDance/Projects/Code/.temp/DragonRadar-AllRequirements-20261124`.
Base and observed HEAD: `d90a02e98abfd517a462c077a9f08bcbed32a347`.
Concurrent parent/B1 edits are not frozen or certified by this document. Source
anchors below are read-time locators; resolve them again after integration.

Supporting inputs:

- `E:/ByteDance/Projects/Code/.temp/all-requirements-audit-20261124/req-audit.md`
- `E:/ByteDance/Projects/Code/.temp/all-requirements-audit-20261124/req-gaps.json`
- Historical command manifest M: `E:/ByteDance/Projects/Code/.temp/dragon-c5-full-3fbee707122f3776/evidence/acceptance/c5/task20-command-evidence.json`
- Historical final report F: `E:/ByteDance/Projects/Code/DragonRadar/.temp/c5-deliverables/contract-c5-2026-11-23.md`

This is supporting evidence for the 57 REQ / 16 EX register, not another
73-item closure report. Only decoded exports, the eight frozen lists,
Q04 affected-module denial with LOCAL unchanged, D04 operator rollback with
notes/history selection unchanged, and LHB source/mapping/revision UI are owned.
B1 ranking implementation, B2-B7 designs, research-result semantics, broader
LOCAL fault permutations and production-input gates are outside this child.

| State | Meaning |
| --- | --- |
| P: prior-run covered | Exact historical outcome is established by a finite verifier or explicitly selected assertion plus actual command receipt. Covers only the stated subclause on historical d90. |
| U: assertion exists, execution unknown | Exact assertion inspected, but available command-level counts do not establish its individual execution/result. This is not FAIL or PASS. |
| M: missing assertion | No assertion for the stated combined/scoped outcome found in the bounded paths inspected. Product failure is not implied. |
| N/A | Frozen scope has no such control/field; not a feature request or inferred PASS. |

Every new source/build requires fresh source-bound proof. Unchanged test bytes
alone do not certify changed production code. Aggregate test-file success, old
C5 gate status, registry `implemented`, and headings never close an unasserted
subclause.

## Historical Command Evidence

M has `tested_commit=d90...a347`, Linux/WSL2, Python 3.12.3, Node v22.23.5,
and clean tracked source before/after. All times below are +08:00 on 2026-11-23.
These are read receipts, not commands executed by this child.

| Key | Actual command; cwd / environment | Window; exit; counts | What it can establish here |
| --- | --- | --- | --- |
| C2 | `npm run test:contract-c2`; `web`, default; internally public/local/owner builds | 20:42:31.139349-20:55:10.296066; 0; outer 0/0/0, native observations 201/0/0 | Exact `assertRunEvidence`/`assertMatrix` outcomes, not every UI property |
| CSV | `npm exec -- vitest run tests/unit/reset-export.test.ts`; `web`, default | 19:59:59.180653-20:00:00.182911; 0; 15/0/0 | Shared serializer command receipt only; no browser-download substitution |
| RESET | `npm run test:e2e:reset`; `web`, default | 20:28:35.809804-20:34:26.091082; 0; 248/0/0 | Relevant ordinary Reset cases have assertion anchors below; no individual outcome inferred |
| BACKEND | `uv run pytest -q -rs --deselect tests/performance/test_reset_queries.py --deselect tests/test_private_https.py`; `.`, default | 20:02:26.890108-20:26:10.025325; 0; 4222/0/0 | Domain test assertions exist; aggregate count is not individual proof |
| FRONTEND | `npm test`; `web`, default | 20:26:37.683908-20:26:53.240504; 0; 1426/0/0 recorded | LHB client assertion execution remains U without case result |
| RECOVERY-1 | `uv run pytest -q -rs` with six explicit node IDs in M `records[gate_id=recovery_drills].commands[0].argv` | 20:01:50.186968-20:02:01.744077; 0; 8/0/0 | Includes explicit `test_display_withdrawal_blocks_reads_clears_cache_and_preserves_storage` and publication replay/pinning; not combined D04 |
| RECOVERY-2 | `npm exec -- playwright test tests/e2e/reset-recovery.spec.ts --config playwright.reset.config.ts`; `web`, default | 20:02:01.750707-20:02:12.292088; 0; 5/0/0 | File receipt, not operator rollback or per-case proof |

Ordinary RESET selects `reset-*.spec.ts` but excludes `reset-contract*.spec.ts`
(`web/playwright.reset.config.ts:63-68`); C2 has its own matching config
(`web/playwright.contract-c2.config.ts:6-16`). Thus C2 proof is not attributed
to RESET's 248 tests.

C2's 477-byte raw result was read: `dragon-c2-9cdd782a-ec8`, empty focused
argv, `fullMatrix=true`, `cells=320`, `outcome=passed`, container removed.
M locator: `records[gate_id=frontend_contract_c2].observations.raw_artifact`,
SHA256 `b065627b1538402c11e613f2a6f68614feff407b27e1e23368ea9efbf026010d`.
The 6,763,973-byte matrix and full logs were not opened or independently
re-audited. The source chain was inspected:
`web/scripts/test-contract-c2.mjs:132-156` runs matching cases then calls the
finite verifier; `web/scripts/contract-c2-support.mjs:69-110` requires
passed cell outcomes, fixed target sets, exact schema/parsed rows/PUB/count/name.
Read-time matrix-test/support SHA256s match M source artifacts respectively:
`2c1203d6d75f16f07e465ad7123f9dc229087a0037b54f018c0d6fa686970863`,
`9e3708e4fa581cccaaa939f15fdbf717d965a9176e6bc135690a943826a89a74`.
This credits the original finite verifier, not a new raw-matrix verification.

## Decoded CSV Matrix

REQ-G04/V03; frozen export scopes are
`docs/contracts/workbench-api.md:349-358`. Exactly six existing CSV entry
points were found via `CsvExportButton`/`downloadCsv` callsites. LOCAL backup
is JSON, not a seventh CSV. No new export feature is requested.

All C2 rows use actual UTF-8 download bytes (`reset-contract-c2-matrix.spec.ts:17-28`),
CSV parsing and metadata/data equality (`:64-78`), plus fixed column/count/name
verification (`contract-c2-support.mjs:22-30,95-110`). Builds are public/local/owner
for these six surfaces, four widths, two themes, native 1x/2x; the separate LOCAL
review surface has no public cell. This is historical P for these exact fixtures.

| Export | Existing source | Exact browser assertions | State / remaining |
| --- | --- | --- | --- |
| Overview current filtered/sorted page | `web/src/export/overview.ts:15`; `web/src/pages/OverviewContent.tsx` | C2 `:84-109`: up/query, code DESC, page 2/size20, exact 12 IDs/name order; explicitly no page/sort CSV metadata | P/C2. Do not demand missing metadata as an unfrozen enhancement |
| News current page | `web/src/pages/News.tsx:206-230` | C2 `:110-129`: announcement filter, page2/size20, exact five articles/all exported fields | P/C2. B4 new fields require fresh column/row proof if exported |
| Role candidates current page | `web/src/features/roles/RoleLists.tsx:115` | C2 `:130-146`: trend/SEC001 page2/size20, five exact candidates, page/date/role metadata; no sort key | P/C2 for trend fixture, not all six role choices |
| Analysis current page | `web/src/features/analysis/ResetAnalysis.tsx:100` | C2 `:147-162`: page2/size20, exact TEST021-25 rows, raw/baseline/filter descriptor | P/C2 for old default fixture only. B1 owns fresh directions/descriptor parity |
| Stock quote plus returned raw window | `web/src/pages/SecurityDetail.tsx:183` | C2 `:163-188`: decoded quote + all 21 points; bytes unchanged by expanded-table page/date-sort, exact fields | P/C2. Not current-table-page export; B2 index history is separate |
| Regulation calculation + events | `web/src/features/regulation/RegulationContent.tsx:90` | C2 `:189-205`: exact calculation/event union, rule versions/values/effective dates and basis | P/C2 for existing eight-row synthetic fixture, not approved real rules |
| Role changes / topic members / global search | Frozen contract `:353-354,357` | C2 `:146` explicitly asserts no changes CSV; bounded callsite search finds no topic/search CSV | N/A: no dedicated CSV. No feature addition |
| Shared quoting/injection/null rules | `web/src/export/csv.ts` | `web/tests/unit/reset-export.test.ts:37-47,49-66,69-94,97-107,116-138`: exact CRLF/quotes, eight hostile prefixes, numeric/null, PUB mismatch rejection, blob/revoke | U/CSV case result; separate from P browser fixtures. Existing hostile-text assertion, not missing |

Correction to the input audit: "all decoded CSV downloads unproven" is too
broad. C2 already asserts all six current export entry points on the above
bounded fixtures. No new CSV-only scenario is needed for unchanged scopes.
Do not rerun C2's 320 cells just to restate this fact.

## Eight Lists: Identity, Units, Sticky And Sizing

The eight-list enumeration is `workbench-api.md:351-358`; this is not eight
tables. Overview members and global search are list layouts, news is a row
button list. PRD row sizing is `docs/DragonRadar_开发需求PRD.md:149`;
V03 identity/unit is `:1625`; sticky residual is frozen in
`workbench-api.md:394-395`. Fixed date/board matrices are bounded exceptions.

| List | Exact existing assertion and source | Historical coverage / residual |
| --- | --- | --- |
| Overview members | C2 `:101-109` exact current IDs/names; `reset-ig11.spec.ts:324-344,348-362` paging and ID-vs-code/name sorting. Source `.filter-members` in OverviewContent; separate quote table `.identity` at `:385,396` and value units `:399-401` | P/C2 exported identities; U/RESET other cases. Quote-table sticky CSS is not proof that the member list's identity persists |
| Role candidates | C2 `:141-145` exact page identities; `reset-ig04-05.spec.ts:329-352` table paging/sorting. `RoleLists.tsx:97-108` identity/name/board fields | P/C2 limited identity. M sticky and candidate row dimensions; unit is board/count, not currency |
| Role changes | `reset-ig04-05.spec.ts:357-364` separate change paging; candidate sorting is `:342-356`, not a change-sort assertion; `RoleLists.tsx:142-149` topic/security/name/change/reason | U/RESET. M sticky and actual row sizing. Currency/percent N/A |
| Topic members | `reset-market.spec.ts:227-239` archived topic/member-to-stock flow; `ResearchDrawer.tsx:107-112` member ID/name/code/effective/available/evidence | U/RESET. M row-specific identity persistence/sticky/sizing; currency N/A for members (sector net flow separate) |
| News | C2 `:124-129` exact five title/article identities; `News.tsx:217-230` exported schema; `.news-list-row` CSS | P/C2 exported identity only. M visible row identity/density measurement. Do not invent numeric units or sticky table columns for a non-table |
| Rank | `reset-analysis.spec.ts:183-195` TEST001/name/topic/10%/8%/25%/20%, basis request; C2 `:160-162` exact page IDs | P/C2 identity/default export; U/RESET percentage rendering. M sticky identity/sizing. B1 direction tests independent |
| Global search | `reset-ig11.spec.ts:199-218` actual topic/article targets and PUB; `GlobalSearch.tsx:93-96,111-124` grouped list | U/RESET. M persistent identity across group/page/reflow and row sizing. Numeric units N/A |
| Expanded stock history | C2 `:165-182` page2/date-sort/date rows; `StockHistoryTable.tsx:5-8,35-46` date identity/OHLC/volume/amount with number-only props | P/C2 limited date/paging/export scope. M sticky/date geometry/sizing and explicit price/volume/amount unit display assertion |

Shared sizing assertions are **not** the eight-list contract:
`reset-preferences.spec.ts:140-158` measures a date/board matrix **header**
at 36/44 +/-1px, not these eight data-row types (U/RESET).
`:161-230` measures narrow-view key controls, not every list.
`web/src/styles.css:11,165,284,288` defines row tokens and `.identity` sticky;
only finding CSS does not establish measured behavior. Responsive record
tables reflow at `:510-557`; role/rank/topic/history identity cells do not
carry `.identity` in inspected JSX. High confidence static observation:
expanded `StockHistoryTable` receives points only and renders unqualified
numeric OHLC/volume/amount headers/cells. Unit context in its enclosing page
must be accepted/measured explicitly before treating this as a product defect.

M means a missing geometry/unit assertion, not that all lists are broken.
Long text/evidence can require taller rows; do not force literal 36/44 on
wrapped rows or turn responsive records into new horizontally scrolling tables.

## Q04 And D04

Q04 acceptance: PRD `:118,1496` last valid time/impact, stop affected new analysis,
missing topic turnover stops zhongjun, missing rules remain pending.

| Subclause | Exact assertion / source | State and limit |
| --- | --- | --- |
| Independent insufficient role | `reset-ig04-05.spec.ts:186-194`: core has no candidate table, trend survives, coverage not summed; `RoleLists.tsx:90-107` | U/RESET; fixture status is not a real missing-turnover calculation proof |
| Wrong identity/403 clear | Same file `:197-209`: wrong core target removed, trend visible, later denied matrix absent; C2 lifecycle `:382-410`: protected association/citation/session denial clears exact active targets | P/C2 for those protected targets only; U/RESET role case; not LOCAL preservation |
| Actual source withdrawal | `tests/test_reset_retention.py:66-93` cache/read/storage assertions, explicitly selected by RECOVERY-1 | P/RECOVERY-1; no browser or LOCAL in this test |
| Missing rule pending | `tests/test_reset_regulation.py:284-300` incomplete release stays pending; `test_reset_regulation_api.py:228-242` pending plus production disabled | U/BACKEND, not missing assertions for kernel/API pending |
| Transient retain versus denial | `web/src/hooks/useResource.tsx:29-64` same-key retain for 0/408/5xx, clear on malformed/403 and global AccessContext | Source only; do not equate retained data with affected analysis disabled |
| Missing-turnover -> affected analysis blocked -> LOCAL unchanged | Inspected role/UI/retention/C2/recovery paths contain no combined before/after LOCAL equality assertion | M, high confidence **acceptance gap**, not confirmed implementation failure |

D04 acceptance: PRD `:1607` batch rerun/rollback does not clear notes. The
operator rollback-to-last-valid plus unchanged historical selection is the
supplied finite scenario, not an invented Web admin feature.

| Subclause | Exact assertion | State and limit |
| --- | --- | --- |
| Server immutable replay/pinning | `tests/test_reset_publication.py:305-339` (`test_replay_is_idempotent_conflicting_key_rejected_and_revision_appends`) and `:500-543` (`test_fixed_publication_and_old_snapshot_ids_remain_pinned`), explicit RECOVERY-1 nodes | P for those assertions, not end-to-end operator rollback |
| Restore/replay controls | `tests/test_reset_retention.py:346-408`: ledger blocked/deidentified/replayed/restored, controls enabled, purged old snapshot still forbidden | U/BACKEND; restoring permission is not selecting last-valid publication |
| LOCAL backup restore | `reset-recovery.spec.ts:91-178`: fresh context, revision order, exact notes/revisions/verifications/watchlist equality and provenance | U/RECOVERY-2; no server operator rollback in this case |
| Same operator rollback preserves LOCAL and chosen history | No combined operation found in these paths; release docs explicitly use backup/restore rather than Alembic downgrade (`docs/acceptance/reset-release.md:92-93`) | M, high confidence acceptance gap. Do not restore a purged/unauthorized snapshot or silently replace an explicit historical selector |

## LHB Source, Mapping And Revision UI

REQ-E02 / EX08 / EX09 use specialized date/as_of/source/disclosure/seat
identities (`workbench-api.md:143-154`), not a daily PUB envelope.

| Subclause | Exact existing assertion | State / residual |
| --- | --- | --- |
| Pure source/contribution identity | `tests/test_reset_lhb.py:392-406` exact source/version/map/unit/contribution/revision; `:516-521` reused seat split; `:559-570` revision selection source-scoped | U/BACKEND. These are not missing kernel assertions |
| Mapping lineage retained | Same file `:715-723` latest name/map plus both original contribution mappings; `:825-846` divergence accepted | U/BACKEND. Mapping change does not imply a new person/holdings identity |
| SQL mixed-source and revised history | Same file `:4827-4836` two sources and exact order; `:4333-4344,4422-4424,4447,4465-4476` SQL/pure revision, mapping/header parity | U/BACKEND. Domain proof does not establish rendered UI |
| HTTP pages/basis/row revision | Same file `:6172-6207`: exact page1/2 IDs, total, basis, revision/map/row identity; `:6219-6251` mixed units blank outer basis | U/BACKEND. No missing backend pagination feature inferred |
| Client mixed-source/units | `web/tests/unit/reset-lhb.test.ts:743-804,868-890`: reused seat across feed-a/feed-b, coverage/row IDs, mixed envelope source/basis accepted | U/FRONTEND. Not browser rendering |
| Same-name distinct seats/null/zero | `reset-lhb.spec.ts:312-339`: three buttons, six unknown and two zero cells, 100/60, no authorized marketing claim | U/RESET; fixture is one source/map |
| History keyboard/source/net/permission | Same file `:342-362`: demo-feed, authorized/revoked captured states, net20 CNY/unit, date, focus restoration; `:365-383` unknown/404; `:386-397` early-as_of | U/RESET. Fixture contains rev1/rev2 at `:147-169` but no exact displayed revision/map assertion |
| Mixed source + mapping change + revision UI | No mixed-source browser fixture or exact contribution revision/map-row outcome found in `reset-lhb.spec.ts` | M, high confidence test gap, not proof UI merges sources |

UI already renders mapping/row/revision values:
`SeatsPanel.tsx:92-113,123-143,149-159,223-237`; no new LHB UI feature
is prescribed. Existing history GET selects seat_id/date/days/as_of, and
mixed-source results are source-separated items, not a newly required source
query parameter. Captured `permission_state=revoked` display fixture must not
be mistaken for actual read authorization after a current grant is revoked.

## Finite Follow-up Acceptance

Only the following residual scenarios are proposed, not executed or authorized
for implementation by this child. First recover a case-level receipt for U
assertions when available; do not rerun merely because this mapping lacks one.

| ID | Exact finite scenario / exit assertions | Ownership |
| --- | --- | --- |
| B8-LIST-01 | One parameterized fixture over the eight named lists, 1440 and 375 widths, compact/comfortable. Capture actual archived identity/name/code or date/article/group, before/after one page change and horizontal scroll **only where a table actually scrolls**. Check sticky identity bounding position/visibility on applicable tables; non-table/reflow identity remains visible in each record. Short single-line rows use declared 36/44 +/-1px or document exact applicable sizing exception; wrapped rows remain readable with >=44px actionable targets. No new tables/control features | B8 acceptance; parent approves precise layout exceptions |
| B8-UNIT-01 | Reuse overview/rank/history rows: explicit non-default currency/price/volume/amount units plus null and zero. Verify the rendered number's local header/context, ratio-to-percent, null marker, no unit substitution. Count/board/date-only fields in other lists record N/A. Decode stock CSV to preserve existing raw-window values/basis. Add only uncovered unit assertions; existing C2 default export cases need no duplicate | B8; B4/B1 changed projections rebind separately |
| B8-Q04-01 | Seed saved LOCAL note with two revisions/verification/watch context and choose explicit historical PUB H. On affected role input, missing topic turnover yields no new zhongjun output with impact/reason while independent valid role survives; missing rule remains pending. Then a scoped source denial clears protected targets and blocks affected analysis; late prior response cannot revive it. Read LOCAL business records before/after/reload for exact equality, and H remains selected. Do not require unrelated market resources to survive the existing global source-denial boundary | B8; no product fix until assertion exposes failure |
| B8-D04-01 | One actual isolated operator operation using existing supported replay/backup-restore path, not a mocked browser-only rollback. Freeze last-valid V, failed/new batch F and explicit historical H. Perform rollback to V, record command/action/version identity and re-read V. In the **same browser/profile** compare LOCAL notes/all revisions/verification/watch provenance before/after/reload; H and list/history selection remain H, never silently latest V. Do not resurrect purged/forbidden H; if unavailable, preserve H selector and honest unavailable state. Record exact supported operator command before execution | B8 integration acceptance; operator path selection prerequisite |
| B8-LHB-01 | Extend one browser list/history fixture: two sources reuse seat_id and disclosure_id, multiple pages, source A rev1/map1 then visible rev2/map2 with original contributions retained; source B remains distinct. Assert exact source/version/map/disclosure/revision/source-row tuple and amounts/units per rendered item/contribution, no merged totals/person claim. Switch as_of across correction availability and verify no stale revision/renamed mapping. Include one null-vs-zero row; preserve current unknown/404 and keyboard assertions | B8 UI acceptance; existing kernel/API tests reused |

Fresh proof should name scenario/case, exact assertion, tested commit and
source bytes, build flags/origin, command, exit, case result and artifact
hash. Fixture HTTP and actual operator operations must be labeled separately.
No whole-25 rerun is requested. New B1 CSV descriptor proof belongs to B1;
changed B2-B7 surfaces need their own finite proof rather than reuse d90 C2.

## Limits And Checklist

- [x] Read audit and historical command JSON/final report; no full-log read.
- [x] Map all six current CSV scopes and all eight frozen lists.
- [x] Separate existing assertions, historical outcomes and missing combinations.
- [x] Provide finite follow-ups only for uncovered outcomes.
- [x] Write only this document and ROOT `.temp` supporting JSON.
- [ ] Parent freezes integrated source and approves/executes only needed scenarios.

No code/test edits, services, Docker, DB access, tests, staging, commits or
other git writes were performed. Read-only git HEAD/diff inspection observed
concurrent B1 test changes; they were neither reviewed nor modified. P is
historical engineering evidence only; remote CI and real input/rules/model/
sharing/shadow-run approval remain outside this matrix.

## Parent Integration Update

The preceding matrix records the initial documentation-only audit. The user
subsequently approved all remaining designs and the five finite scenes.
All five now have observed passing receipts, zero skips, across two bounded
invocations. Exact report:
`ROOT/.temp/b8-residual/b8-report.md`.

- LIST-01, UNIT-01, Q04-01 and LHB-01 passed in
  `run-2026-11-24T08-33-32-966Z`. D04 was held before SQL in that invocation.
- D04-01 passed separately in `run-2026-11-24T08-36-29-309Z`.
  Its supported operation was actual isolated `publication-publish` replay
  returning exact V after F, followed by explicit pinned selection recovery.
  Global latest remained F; historical H and LOCAL records remained unchanged.
  This is not a database rollback, and reload required reselecting table page2.
- Related unit regressions: 111 passed, zero skips.
- Unit metadata and null/zero rendering were corrected in stock history;
  candidate/rank actionable targets retain at least44px in mobile reflow.
  Exact LHB disclosure/source/revision clock mounts and contribution disclosure
  identity are now visible.

Measured multiline/reflow row heights are documented exceptions, not literal
36/44px claims. No fixture table overflowed horizontally; sticky scrolling was
N/A in these scenes. The Q04 kernel probe used real pure evaluators with synthetic
inputs; browser projections were fault-injected. D04 used actual isolated PG17
identities with synthetic browser projections. Neither establishes provider
rights, database readiness, final source freeze or whole-repository acceptance.
The operator schema was removed and DB ownership handed back.

Frontend production hashes match between these invocations, while the complete
repository source changed. Do not represent their union as one frozen full run.
Parent consolidated review, final source-bound gates and release readiness
remain required.
