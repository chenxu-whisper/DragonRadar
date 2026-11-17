# Reset News Acceptance

Recorded: 2026-09-24

## Delivered

- Immutable article revisions and point-in-time news snapshots selected by
  `available_at`, preventing late-arriving articles or revisions from entering
  earlier research snapshots.
- Deterministic event grouping by source event ID, canonical URL, exact content
  digest, or manual confirmation. Similar titles alone never merge events.
- Revision-level source links, licensed summary/body scope, novelty flags,
  supporting evidence, opposing evidence, and security/topic associations.
- Idempotent ingestion commands bound to the complete canonical request, with
  conflict detection and atomic concurrent event upsert.
- Publication-bound news materialization, source authorization rechecks,
  withdrawal lineage, database guards, and migration `0008_news`.
- Public endpoint 09/10 list and detail responses with type/source/text/security
  filtering, pagination, stable ordering, frozen `news|announcement|research`
  types, and exact snapshot selection.
- Owner-only association review with CSRF, expected revision, idempotency,
  stable audit identity, and transaction-time authorization. Accepted AI topic
  suggestions remain pending until a newer published topic snapshot contains
  the target topic.
- Reset News UI with list/detail layout, filters, pagination, explicit
  metadata-only licensing state, novelty/counter-evidence display, and
  snapshot-preserving navigation between news and the shared security drawer.

## Verification

| Check | Result |
|---|---|
| Final full backend run without PostgreSQL | 1411 passed, 745 skipped |
| Final news backend run without PostgreSQL | 10 passed, 18 skipped |
| Frontend unit tests | 248 passed |
| Reset analysis, market, leaders, and news E2E | 29 passed |
| Frontend production build | passed |
| Default entry bundle | 314,394 bytes, within 315,000-byte gate |
| Targeted Ruff lint | passed |
| Targeted Ruff format check | 17 files formatted |
| Python compileall | passed |
| PRD contract checker | REQ=57, endpoints=29, examples=29, JSON=30 |
| Final specification and code-quality review | approved |

Docker Desktop was unavailable and `DRAGON_TEST_DATABASE_URL` was unset during
the final run. PostgreSQL migration, constraint, concurrency, owner write, and
publication-lineage cases were therefore reported as skipped rather than
passing. Synthetic fixtures establish deterministic behavior only; no licensed
production news feed or body-display permission has been supplied.

## Commits

- `1099d65`: immutable news storage, publication/API integration, owner review,
  and News/SecurityDetail UI.
- `f56da69`: reviewed association activation, frozen news types, and pagination.
- `3a4532a`: stable persisted audit identity in owner review responses.
- `af2b7e5`: endpoint wire-contract and FAC-N01 evidence alignment.
- `2342322`: ingestion idempotency, concurrent event upsert, and lineage index.
- `32c0d6c`: frontend error isolation and lazy-loaded News bundle.
- `ef3ba86`: linear grouping of news lineage by snapshot.

## Remaining Risk

Real PostgreSQL evidence is still required before production acceptance,
especially for migration `0008_news`, immutable triggers, concurrent event
ingestion, owner review conflicts, withdrawal handling, and publication
lineage. The implemented UI uses synthetic metadata and does not claim licensed
full-text coverage or AI analysis.

## Workspace Note

The shared worktree retains unrelated unstaged changes that predate Task14.
They were not reverted or included in Task14 commits.

---

## Addendum — EX10 News Word Cloud (Task 23)

Recorded: 2026-10-20

### Delivered

- Publication-materialized, immutable word cloud embedded in the news payload
  (schema v2) as four groups (`all`, `announcement`, `news`, `research`), each
  built at publication from the same licensed snapshot rows with per-group
  titles/summaries/contents text scopes. Reads never recompute; no new
  migration was required.
- Deterministic tokenizer v1 (`wordcloud-tokenizer-v1`, frozen `STOPWORDS_V1`):
  greedy longest dictionary match, OOV runs degrade to single characters,
  words are ordered by `document_count` desc with Unicode code-point tiebreak,
  and `article_ids` are canonical lowercase UUIDs, deduplicated, ascending,
  with `document_count === len(article_ids)`. `sentiment` is always null
  pending an independent research method.
- Three-tier text authorization (none/summary/full) bounds which fields feed
  tokenization; unlicensed text never reaches the archived block.
- `GET /api/news/wordcloud` with required `date` and `top_n` (1-100), bound
  `snapshot_id`, and `type` in `all|announcement|news|research`, registered
  before `/api/news/{article_id}` so "wordcloud" is never parsed as an
  article UUID. Archived blocks are validated fail-closed; a malformed block
  maps to `503 DATA_UNAVAILABLE`, and a null block signals no publishable
  cloud for the snapshot.
- Reset workbench UI (`WordCloud`): deterministic pure `cloudLayout` with
  linear font mapping, CJK-aware advance widths, and width-bounded wrapping,
  rendered as a decorative `aria-hidden` SVG plus an accessible button
  ledger whose names come from explicit `aria-label="{term}，{count} 篇"`.
  No third-party word-cloud dependency and no DOM measurement.
- Fail-closed frontend validator mirroring the archived contract, including
  `words.length === min(total, top_n)`, exact key sets, and strict ordering.
  Selecting a term drives the existing title/summary `q` filter with
  `aria-pressed` toggling; no-publication and zero-licensed-text are distinct
  empty states. Lazy-loaded behind `VITE_RESET_WORKBENCH`, with zero default
  entry-bundle increment.
- Browser-level E2E covering provenance, keyboard selection and list
  filtering, per-group refetch, both empty states, fail-closed malformed
  rejection, DOM ordering, panel plus full-page axe cleanliness, and no
  horizontal overflow at 375px.

### Verification

| Check | Result |
|---|---|
| EX10 wordcloud backend without PostgreSQL | 58 passed, 14 skipped (72 collected) |
| Targeted Ruff lint (news/, news route, test file) | passed |
| Targeted Ruff format check | 4 files formatted |
| Wordcloud frontend unit tests | 62 passed |
| Full frontend unit suite | 29 files, 610 passed |
| Reset wordcloud E2E | 7 passed |
| Reset news E2E regression (shared fixture) | 6 passed |
| Frontend type check (`tsc --noEmit`) | passed |
| Frontend production build | passed |
| Default entry bundle | 314,852 bytes, within 315,500-byte gate, zero increment |
| Independent specification and code-quality reviews | approved |

The 14 skipped backend cases require PostgreSQL publication/materialization
and HTTP fixture support, the same environment limitation recorded for
Task 14; they skip rather than pass when `DRAGON_TEST_DATABASE_URL` is
unset. The full-page axe guard was mutation-verified: reverting the panel
heading from `h2` to `h3` produces an axe `heading-order` failure, and
restoring it returns the suite to green. A pre-existing, failure-set
drifting narrow-viewport (188 CSS px) flake in `reset-acceptance` is
isolated from this slice by code path and is left for CI observation.

### Commits

- `0dbd019`: deterministic license-scoped word cloud tokenizer and models.
- `bae56b1`: materialize EX10 at publication (news payload schema v2) and
  serve `GET /api/news/wordcloud` fail-closed.
- `77a9a0b`: render the EX10 word cloud in the reset workbench, including
  the lazy chunk, response validator, and accessible ledger.
- `263a042`: browser-level E2E coverage and explicit button accessible names.

### Remaining Risk

PostgreSQL-backed evidence is still owed for the 14 skipped
publication/materialization/route cases, consistent with the Task 14 risk
note. Tokenizer v1 and its stopword set are frozen by design; sentiment
stays null until an independently validated method exists.
