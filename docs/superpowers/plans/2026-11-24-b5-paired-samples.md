# B5 Q03 Independent Paired Samples Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Deliver an offline independently frozen paired-sample comparator and private CLI, without central wiring or production/PIT approval claims.

**Architecture:** Reuse the hardened normalized archive reader and current source grants. Validate a frozen plan, one-to-one whole-pool identity map, and digest-addressed source-specific evidence before any numeric cells. Emit a closed typed bounded report using Decimal precision 34 and deterministic sampling.

**Tech Stack:** Python 3.12+, Pydantic 2, pytest, Decimal, existing private file/trust APIs.

---

## Ownership And Execution

ROOT is `E:/ByteDance/Projects/Code/.temp/DragonRadar-AllRequirements-20261124`.
Create only:

- `src/dragon_radar/ingestion/comparison_contracts.py`: exact frozen input/output schemas and invariants.
- `src/dragon_radar/ingestion/comparison.py`: evidence resolution, comparability gate, deterministic comparison.
- `src/dragon_radar/ingestion/comparison_cli.py`: independent module CLI and exclusive durable private output.
- `tests/test_supplier_comparison.py`, `tests/test_supplier_comparison_cli.py`: synthetic offline contract tests.
- `docs/supplier-comparison.md`: exact Q03 contract, evidence boundary and parent hooks.
- `.temp/b5-paired-samples/`: full actual execution logs and pytest scratch.

Do not edit domain models/quality, central CLI, schema, migrations, release, source dependencies, application routes or frontend state. Do not stage, commit, push, use databases, or read old workspaces. Approved design is sections 5 and 12 of `docs/superpowers/specs/2026-11-24-data-quality-research-design.md`; no new design approval pause is needed.

## Task 1: Frozen Contracts And Pure Comparator

- [x] Read approved design, existing archive reader, sample warmup/state logic and required local skills.
- [x] Run bits preparation; record Windows unsupported bootstrap. Determine Python, empty execution source, explicit new module targets, and empty defect map (no preexisting implementation).
- [x] Generate tests for the comparator first. Public API:

```python
def compare_samples(
    left, right, plan, identity_map, *,
    evidence_root, evidence_approver=None, clock=utc_now,
) -> ComparisonReport:
    ...
```

Tests construct real `SampleArchive` objects with synthetic source-specific chunk lineage and complete 21-session bars. Use canonical digest-addressed evidence assets and `secure_output`; never invent real supplier evidence. Assert `status == "pass"` only for `kind == "test"` with genuine fixture bindings; assert no cells for any preflight rejection.

```python
def test_independent_exact_pair_passes(pair):
    report = compare(pair)
    assert report.status == "pass"
    assert report.counts.expected == report.counts.agree
    assert report.test_only is True
    assert report.production_approved is False
    assert report.historical_pit_status == "unverified"
```

- [x] Execute RED with full logs:

```powershell
$env:PYTHONPATH="$PWD/src"
& .venv/Scripts/python.exe -m pytest tests/test_supplier_comparison.py -vv --tb=long --basetemp=.temp/b5-paired-samples/pytest-red 2>&1 | Tee-Object .temp/b5-paired-samples/01-service-red.log
```

Expected failure: missing owned module/API, reached through runtime import assertions rather than collection errors.

- [x] Implement the exact ComparisonPlan from section 5, frozen identity map, evidence descriptor and closed report contracts. Normalize selected fields to canonical order; defaults are exact zero per selected field. Identity maps must be sorted one-to-one, preserve effective intervals, and cover the entire expected pool.
- [x] Implement preflight in precedence order: schema/integrity/budgets; archive and source/upstream/chunk-content independence; current grants/single-sample qualification; exact as_of/session/window; whole pool/map/calendar; source-bound evidence; PIT identity/state; units and multiplier equality. Accumulate comparable-basis failures before any cells; fail outranks incompatible outranks blocked. Evidence files are `<sha256>.json` in an independently provided private root (CLI defaults to plan parent). Bind each descriptor to both acquisition lineages, actual pool/calendar, source/version tokens, benchmark identities/definition and units.
- [x] Supplier evidence requires an externally authenticated evidence approver callback; standalone CLI without such a configured authority is blocked. No assertion inside a supplied evidence file grants real approval. Nonzero tolerances similarly require independently approved precision evidence; synthetic descriptors alone do not approve precision.
- [x] Implement required dates using the existing 20-window warmup rule. Select sorted canonical IDs before cells, at most 100; benchmark once/date under its explicitly mapped canonical identity. Preserve each value, never average:

```python
with localcontext() as context:
    context.prec = 34
    context.rounding = ROUND_HALF_EVEN
    difference = abs(left - right)
    limit = max(abs_tol, rel_tol * max(abs(left), abs(right)))
    agrees = difference <= limit
```

Multiplier and identity equality remain exact even with tolerances. Missing/optional/suspended observations yield unavailable cells. Counts partition all cells. Reports carry constant diagnostic codes, bounded source/version and lineage metadata only, no grant/reference/name/raw path.
- [x] Execute GREEN using the same interpreter/PYTHONPATH; retain full logs. All 33 tests passed in `06-service-green.log`; final focused coverage remains in Task 3.

## Task 2: Independent CLI And Hardened Files

- [x] Generate CLI tests only after Task 1 verification. Public entry:

```python
def main(argv=None, *, evidence_approver=None) -> int:
    ...
```

```python
def test_cli_private_report_matches_stdout(pair_files, capsys, monkeypatch):
    monkeypatch.setattr(cli, "utc_now", lambda: pair_files.now)
    code = cli.main(pair_files.args)
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report == json.loads(pair_files.output.read_bytes())
```

- [x] Run `tests/test_supplier_comparison_cli.py` RED with full logs; `08-cli-red-clean.log`: 22 expected failures, no fixture/collection errors.
- [x] Add required `--left --right --plan --identity-map --left-grant --right-grant`, optional `--output --evidence-root`; reject abbreviated/unknown/duplicate options and `--now`.
- [x] Read all assets with existing private, regular single-link non-reparse hardening and strict duplicate-key JSON. Enforce archives <=16 MiB each, <=100000 rows each, plan/map/grants/evidence combined <=4 MiB, <=250 required sessions, <=250000 cells, bounded serialized output. Read each archive with its externally retained plan digest and supplied current grant.
- [x] Reserve output exclusively before reads. Reject aliases of every input and evidence descriptor. Reauthorize both sources immediately before stdout and at durable output callback. Expiry/fsync failure rolls back the file and emits only constant safe typed diagnostics.
- [x] Run CLI GREEN, then comparator + CLI + existing single-sample/archive tests. `11-regression-green.log`: 175 passed. Exit codes: pass 0, fail 1, blocked/incompatible 2.

## Task 3: Documentation And Verification

- [x] Write exact Q03 documentation: `docs/supplier-comparison.md` includes schemas, evidence descriptor serialization/digests, test-only versus real authority, deterministic Decimal formula, scope/caps, CLI usage, report/status/exit contract, actual logs and external gates.
- [x] Report parent hooks: optional central dispatch to `comparison_cli.main(argv, evidence_approver=...)`; authenticated callback injection required for real evidence/nonzero tolerance; no route/schema/migration hooks. Full callable contract in the Q03 document.
- [x] Run Ruff check/format verification and compileall restricted to owned modules/tests. `14-final-coverage.log`: 186 passed, including 74 owned cases and 112 existing sample/archive regression cases, 88% branch-enabled combined coverage. `15-ruff.log`, `16-compileall.log`, `17-format-check.log`: successful. Actual CLI subprocess success/failure both passed without DB.
- [x] Complete bits Step6: actual `utree flush` attempted in `13-utree-flush.log`; exit 127, executable absent after unsupported cygwin bootstrap. No telemetry/report upload success is claimed. Scope, defect analysis, cases and results summarized below.
- [x] Review owned source and report invariants; all manual edits and formatting targeted only the seven listed owned new artifacts. Other agents' dirty files were left untouched. No staging, commit, push or other git write. Independent parent review remains external because no reviewer-dispatch tool is available.

## Self-Review

All section-5 acceptance scenarios map to Tasks 1 and 2. Section 12 introduces no additional Q03 migration/wiring. Whole-pool preflight is distinct from <=100-ID selection. Evidence absence is never numeric agreement or production/PIT approval. Test generation follows bits steps in order; the user's explicit owned TDD implementation authorization overrides bits' test-only repair restriction during the implementation phase, not during test generation.

One specification conflict is explicitly documented: changing frozen archive record
order changes the externally verified file digests. Selection and cells remain
identical after permutation, while comparison identity correctly changes with its
file inputs. Identical frozen bytes retain identical comparison identity.

## Bits Execution Report

**Scope:** Non-diff, file-level targets `comparison_contracts.py`, `comparison.py`,
`comparison_cli.py`; two dedicated test files. Step1 prepare executed; Step2
Python/pytest context loaded, execution source empty; Step3 targets explicit;
Step4 no preexisting implementation defects; Step5 actual generation/verification/
repair cycles; Step6 flush attempted with observed environment limitation.

**Defect analysis:** Implementation-phase RED tests reproduced uncaught service
grant denial, Windows existing-output diagnostic mismatch, malformed Decimal
conversion, invalid-field default tolerance iteration, and inconsistent typed
report status. All were fixed with green verification. No unrelated defect
repair or domain-model change was made.

**Generated cases:** 74 owned cases cover independent frozen identities/content,
source-bound units/lineage/basis, full pool and sorted <=100-ID sampling, exact and
relative/inclusive Decimal comparisons, unavailable previous close, PIT/factor
contradictions, authority absence, bounded schemas/counts/status, budgets,
duplicate arguments/JSON, hardened file reads, output aliases, durable expiry/
fsync rollback and actual standalone module execution. Authority-resolution
fixtures never manufacture successful real-supplier evidence.

**Verification:** 186/186 focused tests passed; no skips or DB fixtures invoked.
88% combined branch-enabled coverage is observed, not 100% coverage. Ruff,
format check and compileall passed. Complete stdout/stderr RED/GREEN logs retained
under `.temp/b5-paired-samples`; earlier failed fixture/setup attempts remain
preserved rather than rewritten as successful evidence.

**Residual gates:** independent parent review, optional central dispatch wiring,
authenticated real-origin/semantic/precision authority, actual supplier/PIT proof,
and measured performance acceptance remain external. No release or deployment.
