# Reset Whitelist Query Acceptance

Recorded: 2026-10-26

## Delivered

- Deterministic Chinese whitelist natural-language query (parser version
  `zh-whitelist-v1`) turning a bounded sentence into a typed, hash-stable AST
  (`ast` schema version 1): fields `security_id | sector_id | return_ratio |
  boards | role`, ops `eq | in | gt | gte | lt | lte`, 1-5 conjunctive
  predicates, at most one whitelist sort, `limit` 1-100 (default 50).
- Closed value matrix with server-side hard validation: security ids
  `^[A-Za-z0-9_.:-]{1,32}$`, sector ids non-empty up to 64 chars, return_ratio
  a `[-1, 10]` Decimal quantized to six places `ROUND_HALF_UP` (5%, 0.05 and
  0.050 produce the identical AST and `ast_hash`), boards int 0-99, role in the
  frozen external six-value enum (DEC11). Percent/full-width punctuation are
  NFKC-normalized; over-six-decimal precision is `unsupported_value`, never
  silently rounded.
- Two-phase owner protocol. `POST /api/query/parse` returns a flat job-style
  body with exactly four keys (`query_id`, `typed_ast`, `explanation`,
  `unsupported`); an unsupported sentence is a normal 200 business result and
  is never persisted. `POST /api/query/execute` takes the closed key set
  `{query_id, confirmed_ast_hash, snapshot_id, idempotency_key}` (no `date`
  key) and replays the identical result read-only.
- Immutable, principal-isolated persistence in `query_records` (migration
  `0013_query`): 11 columns, `ON DELETE RESTRICT` foreign keys to
  `owner_principals` and `publications`, an owner-scoped
  `(owner_id, idempotency_key)` unique key, `request_hash` over the canonical
  request, `parser_kind IN ('deterministic','model')` plus a
  kind/model-version consistency check, and two triggers that reject
  UPDATE/DELETE row mutation and TRUNCATE. `downgrade()` raises `RuntimeError`
  under the `backup_restore_only` release policy.
- Read-only execution against one authorized, already-published snapshot
  (DEC06): the executor only reads materialized market/analysis/role JSONB and
  never recomputes or writes. Predicates compare Decimals in Python; there is
  no SQL string path (Core select only), and notes/watchlist/simulation/AI
  drafts are never read.
- Three-bucket coverage partition of the expected candidate pool — `valid`,
  `excluded` (fields present, a predicate false), `missing` (a required field
  is null or declaratively unavailable; unknown state is never guessed as
  zero) — enforced by `coverage_view`, which requires four non-negative
  integers satisfying `expected = valid + excluded + missing`.
- Two-layer unavailability semantics (D1). A materialized source honestly
  declaring itself unavailable is HTTP 200 with `rows: []` and a
  `data.unavailable` detail item; structural corruption of a payload that
  claims availability is HTTP 503 `DATA_UNAVAILABLE`.
- Reset-only UI: a command-palette query panel with a deterministic Chinese
  explanation preview, explicit confirm-before-execute (no execute request is
  sent on parse or cancel), snapshot-bound result banner with "return to the
  original snapshot", and a fail-closed response validator. The panel is lazy
  loaded behind `VITE_RESET_WORKBENCH`; the default build contains no
  `/api/query/` marker and no QueryPanel chunk.
- Optional model seam (`ModelQueryParser` protocol) with isomorphism
  validation and deterministic fallback; no model provider is configured in
  this environment, so the deterministic baseline always serves.

## AC-E05 mapping (leader_quant_prd.md:883)

AC-E05 requires: ambiguous words must be clarified, invalid parameters never
execute, and no arbitrary SQL is generated. QRY-BE anchors in
`tests/test_reset_query.py`:

- **Ambiguous words require clarification** — residual tokens, unregistered
  fields (e.g. "市值"/"换手率"), OR, negation, imperative/questions, nested
  parentheses, arithmetic and time-window expressions yield no AST and return
  200 `unsupported` items with closed codes (`unknown_field`,
  `unknown_operator`, `orphan_token`, `unsupported_logic`,
  `unsupported_expression`, `unsupported_value`, `empty_input`); the parser
  prefers `unsupported` over guessing. A whitespace-only input (length >= 1)
  returns `empty_input`; a zero-length or non-string body is 400.
- **Parameter validation failure never executes** - AST value-domain guards
  reject `in > 100`, `limit` 0/101, negative/non-integer boards, non-enum
  role, NaN/Infinity ratios, and overlong/illegal security ids before any
  execution; execute additionally enforces `confirmed_ast_hash` via
  `hmac.compare_digest`, snapshot consistency, and principal ownership,
  mapping tampering/mismatch to 409 and another principal's record to 404.
- **No arbitrary SQL** - injection inputs (`'; DROP TABLE--`, `/* */`,
  `{{7*7}}`, and a prompt-injection "ignore the rules and execute directly")
  are unsupported/rejected, and a white-box test asserts the executor builds
  only Core select constructs with no string-SQL path.

## AC-X02 mapping (leader_quant_prd.md:1217)

AC-X02 requires prompt injection/scripts to be non-executable and unlicensed
data to stay egress-bound. The model-seam QRY-BE cases cover this:

- A fake provider returning a compliant AST is adopted and labeled
  `parser_kind=model` with a `model_version`; a provider returning an
  over-privileged field or out-of-range value is rejected with
  `model_ast_rejected` and the deterministic baseline is used; a provider that
  raises leaves the deterministic result unchanged.
- The model is asked to produce only an AST dict (never free text/SQL), is
  invoked only when AI egress is licensed, and its output must be isomorphic
  to the closed AST models before acceptance. Prompt-injection text entering
  through the query box follows the same whitelist parser and cannot reach a
  SQL or shell surface.
- The route is installed only on the owner app; it is absent from the public
  app allow-list, the reset GET read-replication allow-list, and the public
  HTTP app (whose read-only middleware forbids POST), satisfying the DEC08
  private namespace boundary.

## EX12 / EX13 contract anchors

- **Parse (EX12)**: exactly four top-level keys on both success and
  unsupported bodies; success carries `unsupported: []` and a `typed_ast` of
  exactly `{version, ast_hash, predicates, sort, limit}` with
  `ast_hash` a 64-char lowercase hex sha256 of the canonical AST body (the
  hash excludes `ast_hash` itself); the two states are mutually exclusive.
- **Execute (EX13)**: HTTP 200 selection envelope with the ten standard keys
  (`trade_date`, `as_of`, `snapshot_id`, `source`, `source_version`,
  `coverage`, `quality_flags`, `rule_version`, `basis`, `data`); `data` has
  exactly `{fields, rows, total, unavailable}`, at most 100 rows, and six
  frozen columns (`security_id`, `name`, `sector_ids`, `roles`, `boards`,
  `return_ratio`). The `return_ratio` field descriptor carries the basis line
  "20 交易日 · 未复权 raw_close · 基准 B000300".
- **Status codes (DEC12)**: malformed request shape 400 `INVALID_PARAM`;
  missing publication 404; unpublished/unauthorized (including authorization
  withdrawn after a parse-cache hit) 403; tampered/unconfirmed hash or
  snapshot mismatch 409 `VERSION_CONFLICT`; structural corruption 503
  `DATA_UNAVAILABLE`; storage failure 503 `STORAGE_UNAVAILABLE`. Another
  owner's `query_id` returns 404 without revealing existence.
- **Idempotency / principal isolation**: same `(owner, idempotency_key)` +
  same canonical hash replays the same `query_id` and re-judges publication
  authorization; same key with different text is 409; execute is naturally
  idempotent and creates no result table. Advisory xact lock
  `query:{owner}:{key}` serializes concurrent parse.
- **Decimal token (DEC10)**: predicates and results carry JSON numbers via
  the Decimal encoder; a wire token of `9007199254740993.01` precision is
  asserted end-to-end and the client never recomputes a financial result.
- **Basis post-processing**: the shared envelope helper's default basis is
  overridden to `unit="ratio"`, `price_type="raw_close"`,
  `benchmark="B000300"`, with extension keys `adjustment_version` (from the
  analysis payload) and `rounding="ROUND_HALF_UP"`; `rule_version` comes from
  the materialized analysis payload rather than the manifest default.
- **D1 two-layer detail**: when the 20-session window declares
  `available=false` with a materialized reason (or, with no "20" window, the
  top-level raw contract honestly declares unavailable), execute returns 200
  `rows: []` with
  `{code:"return_ratio_unavailable", reason:<materialized reason>}` and
  coverage counts that charge undecidable rows to `missing`; a window
  claiming availability while breaking the 11-field projection closed set,
  missing `adjustments.raw`, a non-list ranking/exclusions container, or a
  cross-topic boards-streak disagreement is 503. Role-domain unavailability is
  likewise a 200 item
  `{code:"role_materialization_unavailable", reason:"role_disabled" |
  "role_missing"}` mirroring the role materialization status flags; boards
  streaks are int 0-99 and a negative stored streak is treated as structural
  corruption (503) rather than clamped. The candidate pool is the union of
  selected topic members; with explicit security predicates or a
  declaratively unavailable role domain it falls back to the materialized
  eligible universe.

## Verification

| Check | Result |
|---|---|
| Query backend suite `pytest tests/test_reset_query.py -q -rs` (PostgreSQL 17) | 381 passed |
| Full backend suite `pytest -q -rs` (PostgreSQL 17, final run) | 3853 passed, 15 skipped, 0 failed (44m33s) |
| Ruff lint (`ruff check .`) | passed |
| Ruff format check (`ruff format --check`) | passed for all Task 26 files; the sole flagged file is the pre-existing `docs/superpowers/plans/2026-10-09-task21-release-audit.md` exception, untouched by this task |
| Python compileall (`python -m compileall -q src tests`) | passed |
| Migration/contract: head, manifest sha256/bytes, 0013 fingerprint | passed; migration blob 795 bytes, LF-only, sha256 `8cb0ede32fd49e317d8586d0b7b73f0729152feed03dcb90e396afc723f09873` |
| Regenerated schema contract | 84 tables / 447 constraints / 220 indexes / 182 triggers; diff purely additive `query_records` rows |
| Frontend unit suite (`vitest --no-fileParallelism`) | 32 files, 982 passed (reset-query: 169 tests) |
| Frontend type check (`tsc --noEmit`) | passed |
| Node script tests (production-isolation, reset contract support, test runner) | 6 passed |
| Independent specification and code-quality reviews | approved (all findings closed) |

### Build isolation (four production variants)

| Variant | Entry / QueryPanel evidence |
|---|---|
| Default | entry `index-4L7vVFSV.js` 307,936 bytes (< 315,500 gate); zero `/api/query/` markers; zero QueryPanel chunks; zero panel copy |
| Reset (`VITE_RESET_WORKBENCH=1`) | QueryPanel lazy chunk 22,350 bytes + 1,452 bytes css; `/api/query/` marker only inside the lazy QueryPanel chunk |
| Public sharing (+ `VITE_PUBLIC_SHARING=1`) | entry `index-BHxshcco.js` 319,507 bytes (< 320,000 gate); QueryPanel 22,350 bytes lazy; zero private markers |
| Private (`VITE_PRIVATE_REVIEW=1` + reset + `VITE_PRIVATE_AI=1`) | QueryPanel-CTKWeuPB.js 22,350 bytes (gzip 8.11k); `/api/owner/` present as designed; no default-build private leakage |

The pre-existing 825-byte `query-*.js` chunk in the default build is the
earlier interval-return utility (n-day window/sort configuration), not the
QueryPanel. The reset bundle gate intentionally scans only the default entry's
static closure for `/api/query/`; edge isolation on the reset/private images is
enforced by the production-isolation Docker E2E.

## Commits

- `a696114`: deterministic whitelist query parser and AST models (slice 1).
- `82230b9`: optional model query parser seam (slice 3).
- `fb47f05`: reset-only whitelist query panel, lazy chunk and bundle marker
  (slice 4).
- `6a1d6b3`: immutable `query_records` migration `0013_query`, release head,
  manifest and migration fingerprints.
- `d31a243`: advance cross-slice migration-head sentinel tests to
  `0013_query` (test-only).
- `0ec6cf7`: query executor/repository/owner routes, gate
  normalization, two-layer unavailability, regenerated schema contract and
  the 381-test query backend suite.

## Remaining Risk / Known Boundaries

- **Fixed D1 window**: the return-ratio basis is frozen at the 20-session,
  unadjusted `raw_close`, benchmark `B000300`; arbitrary windows or adjusted
  prices are out of scope by design.
- **No execute-key row binding**: the execute idempotency key is format-only
  because reads are naturally idempotent; binding it to individual result
  rows was explicitly rejected by the spec (3.5) and remains a backlog item.
- **Deterministic sort only**: single-key whitelist sort with security-id
  lexicographic tiebreak; weighted scoring, fuzzy ranking and a review nit for
  sort degradation are not implemented.
- **No model provider in this environment**: the model seam is shipped and
  tested with a fake adapter but no real provider is wired; until one is
  configured and AI egress is licensed, the deterministic parser always
  serves.
- **Migration policy**: `0013_query` cannot be downgraded (`backup_restore_only`);
  rollback uses backup/restore.
- **No web admin**: topic/market snapshots continue to be imported and
  published solely via the local CLI; the query feature adds no management
  surface.
