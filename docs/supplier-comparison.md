# Q03 Independent Paired Samples

## Scope And Trust

This is an offline private engineering comparison of two independently frozen,
qualified `SampleArchive` bundles. It performs no collection, network operation,
database access, reconciliation, supplier selection, publication or promotion.
V1 inherits the existing normalized sample's integer 20-session window,
SSE/common-stock/sh_main scope. Every required warmup session is compared.
It is not general-market, intraday, qfq/hfq or factor-ranking acceptance.

All reports retain `production_approved:false` and
`historical_pit_status:"unverified"`. Synthetic fixtures use `kind:"test"` and
`test_only:true`. Passing test reports are not real supplier evidence.
Synthetic sources labeled `supplier_sample` still fail the existing validator.
Original contemporaneous receipt/PIT evidence and real source rights remain
external requirements.

## Independent CLI

Run from the approved ROOT using its interpreter and `PYTHONPATH=ROOT/src`:

```powershell
& .venv/Scripts/python.exe -m dragon_radar.ingestion.comparison_cli `
  --left LEFT.json --right RIGHT.json --plan PLAN.json `
  --identity-map MAP.json --left-grant LG.json --right-grant RG.json `
  --output REPORT.json --evidence-root PRIVATE_EVIDENCE_DIRECTORY
```

The six input options are required. `--output` is optional; stdout always emits
one JSON report. `--evidence-root` defaults to the plan's parent directory.
There is no `--now`, token, provider endpoint or trust-approval switch.
Abbreviations, unknown arguments and repeated options (including `--key=value`)
fail with a safe `arguments` report. `--help` prints ordinary argparse help.

Each current grant is a strict `SourceGrant` JSON document. Both are checked
using actual operation time, must match the embedded frozen grants exactly,
and must authorize their own source. Rights are rechecked before output, inside
the durable fsync callback, and immediately before stdout. Expiry rejects the
report and rolls back a newly reserved output. Diagnostic reports never contain
grant references, names, filesystem paths, raw payloads or exception text.
Numeric reports contain protected market values; stdout must be handled privately.

All asset reads use the existing hardened private, regular, single-link,
non-symlink/non-junction reader. Duplicate JSON keys are rejected. Archives
and evidence must be canonical, digest-matching bytes; plan/map/grants may use
ordinary strict JSON whitespace. Output is reserved with exclusive private
`secure_output` before any input read; existing files and input/evidence aliases
cannot be overwritten. Failure during write/fsync/authorization removes output.
Input parse failures emit stdout diagnostics without leaving a partial file.
Service-produced pass/fail/blocked/incompatible reports may be persisted only
while both current grants remain valid.

## Frozen Plan

`ComparisonPlan` is frozen, strict and rejects unknown keys. Exact JSON keys:

```text
schema,kind,left_archive_sha256,right_archive_sha256,
left_supplier_id,right_supplier_id,left_upstream_id,right_upstream_id,
independence_evidence_sha256,as_of,decision_sessions,window,
pool_sha256,calendar_sha256,adjustment_semantics,adjustment_evidence_sha256,
identity_mapping_sha256,benchmark_definition_sha256,fields,
max_security_count,tolerances,tolerance_evidence_sha256
```

`schema` is `dragon-radar.supplier-comparison/v1`; `kind` is `test` or
`supplier_sample`. Digests are lowercase 64-hex SHA256s. Supplier/upstream IDs
are 1..100-character ASCII tokens matching
`^[A-Za-z0-9][A-Za-z0-9._:/+-]*$`. `as_of` is aware UTC; decision sessions
are nonempty sorted unique explicit ISO dates, at most 250.
`window` is exactly integer 20, not a bool/float.
`adjustment_semantics` is exactly `raw_ohlc_with_multiplier`.

Fields are a nonempty unique subset, normalized to this canonical order:

```text
open,high,low,close,previous_close,volume,amount,adjustment_factor,benchmark_close
```

`max_security_count` is an integer 1..100 (default 100).
`tolerances` has exactly the selected field keys, each exactly
`{"abs":"0","rel":"0"}` by default when the map is omitted. Values must be
finite nonnegative Decimal strings; no floats. Numeric wire strings are bounded
to 128 characters and adjusted exponent magnitude <=1000.
No vendor tick or guessed tolerance is supplied.
`tolerance_evidence_sha256` defaults to null and must be null for all-zero
tolerances. Any nonzero tolerance requires approved precision evidence.
Even a declared multiplier tolerance never relaxes exact factor equality.

## Identity And Evidence

Identity map exact root:
`{"schema":"dragon-radar.supplier-identity-map/v1","entries":[...]}`.
Each entry has exactly:

```text
left_security_id,right_security_id,canonical_security_id,exchange,code,
security_type,board_category,effective_from,effective_to
```

IDs use the bounded token syntax. Entries are sorted by canonical ID; left,
right, canonical IDs and exchange/code pairs are unique. `exchange` is SSE,
`code` six digits, `security_type` common_stock, `board_category` sh_main.
Effective bounds are explicit dates; nullable `effective_to` is exclusive.
Every expected archived security must be mapped; no intersection denominator.

Evidence files are `<sha256>.json` in the private evidence root.
Exact descriptor root keys:

```text
schema,kind,left,right,basis,tolerances
```

`schema` is `dragon-radar.supplier-comparison-evidence/v1`.
`kind` must match the plan. Both source descriptors have exactly:

```text
supplier_id,upstream_id,source_version,pool_version,calendar_version,
adjustment_version,collection_fingerprint,request_sha256,extraction_sha256,
sample_file_sha256,raw_sha256,benchmark_id,currency,price_unit,volume_unit,amount_unit
```

`raw_sha256` is a nonempty list (<=10000) of ordered acquisition chunk digests.
Other digests and tokens follow the same bounded types. `basis` has exactly:

```text
as_of,decision_sessions,pool_sha256,calendar_sha256,adjustment_semantics,
benchmark_definition
```

Benchmark definition exact keys:
`canonical_benchmark_id,currency,price_unit,method_version`.
Currency is CNY and price unit `currency/share`. The source-specific benchmark
IDs map to this separate canonical benchmark identity and frozen definition;
they are not inferred from the stock pool or bare benchmark-close dictionary.
Evidence units must equal the corresponding bars.

All distinct evidence descriptors bind the same two source lineages, units,
benchmark identities and basis. One descriptor may be referenced for both
independence and adjustment; its bytes count once toward the metadata budget.
Its `tolerances` is null unless it includes a precision specification;
precision resolution requires exact equality with the plan's tolerance map.
Independent origin is rejected for equal archive hashes, supplier/upstream IDs,
collection/extraction/sample-file digests, or shared raw/chunk archive/fingerprint
digests. Relabeling/copying is not independent acquisition.

For `test` kind, zero-tolerance synthetic descriptors only prove fixture
consistency. Real `supplier_sample` descriptors require an externally configured
authenticated approver for both independence and adjustment. Nonzero precision
requires that approver even for test kind. A JSON assertion never grants approval.
Missing authority/evidence is blocked with no numeric cells. Approval callback
exceptions and false/non-True results fail closed without exposing their text.

## Digest Encoding

Use `dragon_radar.production.trust.canonical_json`: UTF-8, sorted object keys,
compact separators `(",",":")`, `ensure_ascii=False`, no NaN and no trailing
newline. Dates/UTC timestamps and Decimal values use the typed JSON serialization.
SHA256 hex is lowercase.

Archive hashes bind exact canonical frozen file bytes. Map digest binds its
canonical JSON document including schema. Pool digest binds
`{"security_ids":[sorted canonical expected IDs]}`. Calendar digest binds
`{"exchange":"SSE","sessions":[ordered required ISO dates]}`.
Benchmark digest binds its exact definition. Evidence digest binds its exact
canonical descriptor bytes. Plan digest binds the fully validated normalized
plan JSON, including defaults and canonical field ordering.

Comparison identity is SHA256 of canonical JSON with exact keys:

```text
plan_sha256,left_archive_sha256,right_archive_sha256,
identity_mapping_sha256,algorithm_version
```

Algorithm version is `paired-sample-decimal34-v1`. It contains no wall clock.
Rerunning identical frozen inputs gives the same report and identity.
Permuting archive records preserves selection/cells, but changes file hashes
and therefore identity. The design acceptance phrase requiring identical
identity after changing frozen record bytes conflicts with its external-file
digest binding; no weakening of that binding is implemented.

## Comparability And Values

Both single-sample diagnostics and current grants must pass. Preflight validates
archive integrity/independence; exact cutoff, decisions/window, pool/calendar;
source-bound evidence; whole-pool PIT state and identity intervals; units and
per-session multipliers. It invokes existing `state_at`. Identity, listing,
ST, suspension and delisting contradictions fail; unknown state is blocked.
Different provider version tokens can be comparable only through bound semantic
evidence; equal tokens never substitute for actual pool/calendar equality.

All preflight failures emit no numeric cells. No date rebasing, unit conversion,
nearest-time substitution, pool intersection, averaging or cross-source ratios.
Sampling takes the first `min(max_security_count,pool_size)` sorted canonical
IDs before values. Every required 20-window warmup session is included; the
benchmark compares once per required date. Cells are ordered stock ID/date/field,
then benchmark/date. Issues sort by code/date/ID/field.

Using local Decimal precision 34 and ROUND_HALF_EVEN:

```text
difference = abs(L-R)
limit = max(abs_tol, rel_tol * max(abs(L),abs(R)))
agree iff difference <= limit
```

Left/right/difference/limit remain Decimal strings; no average exists.
Missing optional observations (including previous close) and suspensions are
unavailable, not zero or agreement. Exact multiplier preflight applies even
when adjustment_factor is not among selected fields.

## Closed Report And Limits

Exact report root keys:

```text
schema,comparison_id,status,test_only,production_approved,historical_pit_status,
plan_sha256,identity_mapping_sha256,inputs,selection,counts,issues,cells
```

Schema is `dragon-radar.supplier-comparison-report/v1`. Early failures may have
null comparison/plan/map IDs and null inputs; they are never a pass.
`inputs` is exactly `{left,right}`, each null or exact metadata keys:

```text
archive_sha256,source_id,source_version,upstream_id,pool_version,calendar_version,
adjustment_version,collection_fingerprint,request_sha256,extraction_sha256,
sample_file_sha256,raw_sha256
```

`selection` has `security_ids,sessions,fields,benchmark_id`.
`counts` has nonnegative `expected,agree,conflict,unavailable`, matching the cells
and exactly partitioning expected. An issue has exactly
`code,status,canonical_security_id,trade_date,field`, with nullable coordinates
and closed internal code enum in `comparison_contracts.Code`.
A cell has exactly:

```text
canonical_security_id,trade_date,field,status,left,right,absolute_difference,limit,reason
```

Cell status is agree/conflict/unavailable; unavailable has null difference/limit
and an explicit reason. Duplicate cell keys and contradictory report status
are rejected. Pass requires complete comparable agreement, nonempty cells,
both input metadata blocks and an identity, with no issues.

Precedence and exits: fail (integrity/schema/identity/numeric conflict) -> 1;
incompatible (proven noncomparable basis) -> 2; blocked (unavailable rights,
evidence or observations) -> 2; pass -> 0. Fail outranks incompatible outranks
blocked. Parse and authorization failures expose only constant codes.

Caps: each archive <=16MiB, therefore both <=32MiB; <=100000 total bar/state/
limit-event rows per archive, checked before single-sample validation;
plan/map/grants/distinct evidence combined <=4MiB; <=100 selected IDs;
<=250 required sessions; <=250000 numeric cells and <=250000 whole-pool
security-session validation pairs; report <=16MiB. Exceeding bounds rejects,
never truncates. These are engineering caps, not latency/capacity proof.

## Parent Hooks And Verification

No shared wiring was changed. Optional parent central dispatch:

```python
from dragon_radar.ingestion.comparison_cli import main
return main(argv, evidence_approver=authenticated_approval_service)
```

The keyword is optional; standalone zero-tolerance test comparison works without
it. Real evidence/nonzero precision must be injected by trusted parent code:

```python
EvidenceApprover = Callable[[str, bytes, str, datetime], bool]
# (expected_digest, canonical_descriptor_bytes, purpose, current_utc_time)
# purpose is "independence", "adjustment" or "precision"
```

The authority must independently authenticate the evidence's origin, semantic
equivalence and current approval for that purpose/digest; do not implement it as
`lambda *args: True` in deployment. Pure service hook:
`compare_samples(left,right,plan,identity_map,*,evidence_root,
evidence_approver=None,clock=utc_now,metadata_bytes=0) -> ComparisonReport`.
`metadata_bytes` accounts for metadata already read by the CLI.
No route, schema, migration, source dependency or frontend hook is needed.

Actual full RED/GREEN logs reside under ROOT `.temp/b5-paired-samples/`.
`08-cli-red-clean.log` has 22 expected failures; `11-regression-green.log`
has 175 passing comparison/single-sample/archive tests. Later contract tests
reproduced malformed Decimal/default fields/status defects in
`12-contract-red.log`; final verification evidence is recorded in the execution
plan: `14-final-coverage.log` records 186 passed (74 owned cases), 88% combined
branch-enabled coverage; `15-ruff.log`, `16-compileall.log` and
`17-format-check.log` passed. Actual CLI subprocess success/failure passed.
`13-utree-flush.log` records exit 127 because the executable is absent after the
unsupported Windows/cygwin bootstrap. Independent parent review remains external.
All fixtures are engineering-only. No DB was used, and no production,
historical-PIT, real-supplier or performance acceptance is claimed.
