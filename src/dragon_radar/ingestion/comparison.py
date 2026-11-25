"""Offline independent comparison. No reconciliation, provider choice or DB."""

from collections.abc import Callable
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Context, DecimalException, localcontext
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from dragon_radar.domain.quality import state_at
from dragon_radar.ingestion.comparison_contracts import (
    ALGORITHM,
    MAX_CELLS,
    MAX_METADATA_BYTES,
    MAX_ROWS,
    Cell,
    ComparisonEvidence,
    ComparisonPlan,
    ComparisonReport,
    Counts,
    IdentityMap,
    InputMetadata,
    Inputs,
    Issue,
    Selection,
    Tolerance,
)
from dragon_radar.ingestion.sample_acceptance import evaluate_sample
from dragon_radar.ingestion.sample_archive import SampleArchive
from dragon_radar.ingestion.supplier_transport import (
    SupplierError,
    authorize,
    operation_time,
    utc_now,
)
from dragon_radar.production.trust import (
    EvidenceError,
    MissingEvidence,
    canonical_json,
    read_asset,
    strict_json,
)

# The deployment supplies authentication and approval semantics, not evidence JSON.
EvidenceApprover = Callable[[str, bytes, str, datetime], bool]
DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


class ComparisonError(ValueError):
    """Only closed internal diagnostic codes may reach reports."""

    def __init__(self, code, status="fail"):
        self.code = code
        self.status = status
        super().__init__(code)


def digest(value):
    return sha256(canonical_json(value)).hexdigest()


def failure(code, status="fail", *, plan=None):
    return ComparisonReport(
        status=status,
        test_only=plan is None or plan.kind == "test",
        plan_sha256=digest(plan.model_dump(mode="json", by_alias=True))
        if plan
        else None,
        identity_mapping_sha256=plan.identity_mapping_sha256 if plan else None,
        issues=(Issue(code=code, status=status),),
    )


def _metadata(archive, digest, upstream):
    b, lineage = archive.sample.batch, archive.lineage
    return InputMetadata(
        archive_sha256=digest,
        source_id=b.source_id,
        source_version=b.source_version,
        upstream_id=upstream,
        pool_version=b.pool_version,
        calendar_version=b.calendar_version,
        adjustment_version=b.adjustment_version,
        collection_fingerprint=lineage.collection_fingerprint,
        request_sha256=lineage.request_sha256,
        extraction_sha256=lineage.extraction_sha256,
        sample_file_sha256=lineage.sample_file_sha256,
        raw_sha256=tuple(c.raw_sha256 for c in lineage.chunks),
    )


def _required(sample):
    sessions = sample.batch.calendar.sessions
    result = set()
    for decision in sample.decision_sessions:
        if decision not in sessions:
            raise ComparisonError("session_mismatch", "incompatible")
        index = sessions.index(decision)
        if index < 20:
            raise ComparisonError("sample_unavailable", "blocked")
        result.update(sessions[index - 20 : index + 1])
    if not 1 <= len(result) <= 250:
        raise ComparisonError("budget")
    return tuple(sorted(result))


def _evidence(plan, root, left, right, approver, now, metadata_bytes):
    references = [
        ("independence", plan.independence_evidence_sha256),
        ("adjustment", plan.adjustment_evidence_sha256),
    ]
    nonzero = any(t.abs != 0 or t.rel != 0 for t in plan.tolerances.values())
    if nonzero:
        if plan.tolerance_evidence_sha256 is None:
            raise ComparisonError("precision_unavailable", "blocked")
        references.append(("precision", plan.tolerance_evidence_sha256))
    elif plan.tolerance_evidence_sha256 is not None:
        raise ComparisonError("precision_mismatch")
    checked = {}
    for purpose, expected in references:
        try:
            if expected not in checked:
                raw = read_asset(
                    Path(root).absolute(), f"{expected}.json", MAX_METADATA_BYTES
                )
                metadata_bytes += len(raw)
                if metadata_bytes > MAX_METADATA_BYTES:
                    raise ComparisonError("budget")
                value = strict_json(raw)
                if sha256(raw).hexdigest() != expected or canonical_json(value) != raw:
                    raise ComparisonError("evidence_invalid")
                descriptor = ComparisonEvidence.model_validate_json(raw)
                checked[expected] = (raw, descriptor)
            raw, descriptor = checked[expected]
        except MissingEvidence:
            raise ComparisonError("evidence_unavailable", "blocked") from None
        except (EvidenceError, ValidationError, OSError, ValueError) as error:
            if isinstance(error, ComparisonError):
                raise
            raise ComparisonError("evidence_invalid") from None
        if descriptor.kind != plan.kind:
            raise ComparisonError("kind_mismatch")
        for archive, source, supplier, upstream in (
            (left, descriptor.left, plan.left_supplier_id, plan.left_upstream_id),
            (right, descriptor.right, plan.right_supplier_id, plan.right_upstream_id),
        ):
            expected_source = _metadata(archive, "0" * 64, upstream).model_dump()
            expected_source.pop("archive_sha256")
            expected_source["supplier_id"] = expected_source.pop("source_id")
            actual = source.model_dump(
                exclude={
                    "benchmark_id",
                    "currency",
                    "price_unit",
                    "volume_unit",
                    "amount_unit",
                }
            )
            if expected_source != actual or source.supplier_id != supplier:
                raise ComparisonError("evidence_binding")
        basis = descriptor.basis
        if (
            basis.as_of != plan.as_of
            or basis.decision_sessions != plan.decision_sessions
            or basis.pool_sha256 != plan.pool_sha256
            or basis.calendar_sha256 != plan.calendar_sha256
            or basis.adjustment_semantics != plan.adjustment_semantics
        ):
            raise ComparisonError("evidence_binding", "incompatible")
        if (
            digest(basis.benchmark_definition.model_dump(mode="json"))
            != plan.benchmark_definition_sha256
        ):
            raise ComparisonError("benchmark_mismatch", "incompatible")
        if basis.benchmark_definition.canonical_benchmark_id in {
            row.security_id
            for row in (*left.sample.batch.bars, *right.sample.batch.bars)
        }:
            raise ComparisonError("benchmark_mismatch", "incompatible")
        if purpose == "precision" and descriptor.tolerances != dict(plan.tolerances):
            raise ComparisonError("precision_mismatch")
        if plan.kind == "supplier_sample" or purpose == "precision":
            if approver is None:
                raise ComparisonError(
                    "precision_unavailable"
                    if purpose == "precision"
                    else "evidence_approval_unavailable",
                    "blocked",
                )
            try:
                approved = approver(expected, raw, purpose, now)
            except Exception:
                raise ComparisonError(
                    "evidence_approval_unavailable", "blocked"
                ) from None
            if approved is not True:
                raise ComparisonError("evidence_approval_unavailable", "blocked")
    # Every descriptor must bind the same source units and benchmark identities.
    basis_proofs = [item[1] for item in checked.values()]
    primary = checked[plan.independence_evidence_sha256][1]
    if any(
        (p.left, p.right, p.basis) != (primary.left, primary.right, primary.basis)
        for p in basis_proofs
    ):
        raise ComparisonError("evidence_binding")
    return primary


def _cell(sid, day, field, left, right, tolerance, reason=None):
    if reason is not None or left is None or right is None:
        return Cell(
            canonical_security_id=sid,
            trade_date=day,
            field=field,
            status="unavailable",
            left=str(left) if left is not None else None,
            right=str(right) if right is not None else None,
            absolute_difference=None,
            limit=None,
            reason=reason or "observation_unavailable",
        )
    if not left.is_finite() or not right.is_finite():
        raise ComparisonError("nonfinite")
    if any(
        len(str(value)) > 128 or abs(value.adjusted()) > 1000 for value in (left, right)
    ):
        raise ComparisonError("numeric_budget")
    with localcontext(DECIMAL_CONTEXT):
        difference = abs(left - right)
        limit = max(tolerance.abs, tolerance.rel * max(abs(left), abs(right)))
    status = "agree" if difference <= limit else "conflict"
    return Cell(
        canonical_security_id=sid,
        trade_date=day,
        field=field,
        status=status,
        left=str(left),
        right=str(right),
        absolute_difference=str(difference),
        limit=str(limit),
        reason=None if status == "agree" else "numeric_conflict",
    )


def compare_samples(
    left: SampleArchive,
    right: SampleArchive,
    plan: ComparisonPlan,
    identity_map: IdentityMap,
    *,
    evidence_root: Path,
    evidence_approver: EvidenceApprover | None = None,
    clock: Callable[[], datetime] = utc_now,
    metadata_bytes: int = 0,
) -> ComparisonReport:
    """Validate complete frozen inputs before generating any numerical cells."""
    try:
        plan = ComparisonPlan.model_validate_json(
            canonical_json(plan.model_dump(mode="json", by_alias=True))
        )
        identity_map = IdentityMap.model_validate_json(
            canonical_json(identity_map.model_dump(mode="json", by_alias=True))
        )
        left = SampleArchive.model_validate_json(
            canonical_json(left.model_dump(mode="json", by_alias=True))
        )
        right = SampleArchive.model_validate_json(
            canonical_json(right.model_dump(mode="json", by_alias=True))
        )
        issues = []
        seen = set()

        def add(code, status="fail", sid=None, day=None, field=None):
            issue = Issue(
                code=code,
                status=status,
                canonical_security_id=sid,
                trade_date=day,
                field=field,
            )
            if issue not in seen:
                if len(issues) >= MAX_CELLS:
                    raise ComparisonError("budget")
                seen.add(issue)
                issues.append(issue)

        def finish(cells=(), selection=None, inputs=None):
            issues.sort(
                key=lambda i: (
                    i.code,
                    i.trade_date or date.min,
                    i.canonical_security_id or "",
                    i.field or "",
                )
            )
            status = next(
                (
                    s
                    for s in ("fail", "incompatible", "blocked")
                    if any(i.status == s for i in issues)
                ),
                "pass",
            )
            plan_digest = digest(plan.model_dump(mode="json", by_alias=True))
            return ComparisonReport(
                comparison_id=digest(
                    {
                        "plan_sha256": plan_digest,
                        "left_archive_sha256": plan.left_archive_sha256,
                        "right_archive_sha256": plan.right_archive_sha256,
                        "identity_mapping_sha256": plan.identity_mapping_sha256,
                        "algorithm_version": ALGORITHM,
                    }
                ),
                status=status,
                test_only=plan.kind == "test",
                plan_sha256=plan_digest,
                identity_mapping_sha256=plan.identity_mapping_sha256,
                inputs=inputs or Inputs(),
                selection=selection or Selection(),
                counts=Counts(
                    expected=len(cells),
                    agree=sum(c.status == "agree" for c in cells),
                    conflict=sum(c.status == "conflict" for c in cells),
                    unavailable=sum(c.status == "unavailable" for c in cells),
                ),
                issues=tuple(issues),
                cells=tuple(cells),
            )

        now = operation_time(clock)
        for archive in (left, right):
            b = archive.sample.batch
            if len(b.bars) + len(b.security_states) + len(b.limit_up_events) > MAX_ROWS:
                add("budget")
            authorize(b.grant, b.source_id, now)
        if issues:
            return finish()
        if plan.left_archive_sha256 == plan.right_archive_sha256:
            add("same_archive")
        if plan.left_supplier_id == plan.right_supplier_id:
            add("same_supplier")
        if plan.left_upstream_id == plan.right_upstream_id:
            add("same_upstream")
        for attr in (
            "collection_fingerprint",
            "sample_file_sha256",
            "extraction_sha256",
        ):
            if getattr(left.lineage, attr) == getattr(right.lineage, attr):
                add("shared_lineage")
        for attr in ("raw_sha256", "archive_sha256", "fingerprint"):
            if {getattr(c, attr) for c in left.lineage.chunks} & {
                getattr(c, attr) for c in right.lineage.chunks
            }:
                add("shared_lineage")
        for archive, expected, supplier in (
            (left, plan.left_archive_sha256, plan.left_supplier_id),
            (right, plan.right_archive_sha256, plan.right_supplier_id),
        ):
            if digest(archive.model_dump(mode="json", by_alias=True)) != expected:
                add("archive_integrity")
            if archive.sample.batch.source_id != supplier:
                add("source_mismatch")
            if archive.sample.kind != plan.kind:
                add("kind_mismatch")
            if archive.sample.batch.as_of != plan.as_of:
                add("cutoff_mismatch", "incompatible")
            if (
                archive.sample.decision_sessions != plan.decision_sessions
                or archive.sample.window != plan.window
            ):
                add("session_mismatch", "incompatible")
            quality = evaluate_sample(archive.sample, now=now)
            if quality["status"] != "pass":
                add(
                    "sample_invalid"
                    if quality["status"] == "fail"
                    else "sample_unavailable",
                    "fail" if quality["status"] == "fail" else "blocked",
                )
        if (
            digest(identity_map.model_dump(mode="json", by_alias=True))
            != plan.identity_mapping_sha256
        ):
            add("identity_mapping")
        required_left, required_right = _required(left.sample), _required(right.sample)
        if required_left != required_right:
            add("calendar_mismatch", "incompatible")
        days = required_left
        if len(identity_map.entries) * len(days) > MAX_CELLS:
            add("budget")
        if left.sample.batch.calendar.exchange != right.sample.batch.calendar.exchange:
            add("calendar_mismatch", "incompatible")
        expected_calendar = digest(
            {
                "exchange": left.sample.batch.calendar.exchange,
                "sessions": [d.isoformat() for d in days],
            }
        )
        if expected_calendar != plan.calendar_sha256:
            add("calendar_mismatch", "incompatible")
        ids = tuple(e.canonical_security_id for e in identity_map.entries)
        if digest({"security_ids": ids}) != plan.pool_sha256:
            add("pool_mismatch", "incompatible")
        for archive, field in (
            (left, "left_security_id"),
            (right, "right_security_id"),
        ):
            if set(archive.sample.batch.expected_security_ids) != {
                getattr(e, field) for e in identity_map.entries
            }:
                add("pool_mismatch", "incompatible")
        selected = identity_map.entries[: plan.max_security_count]
        stocks = tuple(f for f in plan.fields if f != "benchmark_close")
        if (
            len(days)
            * (len(selected) * len(stocks) + ("benchmark_close" in plan.fields))
            > MAX_CELLS
        ):
            add("budget")
        if issues:
            return finish()
        try:
            evidence = _evidence(
                plan,
                evidence_root,
                left,
                right,
                evidence_approver,
                now,
                metadata_bytes,
            )
        except ComparisonError as error:
            add(error.code, error.status)
            evidence = None
        indexed = [
            {(b.security_id, b.trade_date): b for b in archive.sample.batch.bars}
            for archive in (left, right)
        ]
        states = {}
        for entry in identity_map.entries:
            sid = entry.canonical_security_id
            for day in days:
                pair = tuple(
                    state_at(a.sample.batch, source_sid, day)
                    for a, source_sid in (
                        (left, entry.left_security_id),
                        (right, entry.right_security_id),
                    )
                )
                states[sid, day] = pair
                if not entry.effective_from <= day or (
                    entry.effective_to is not None and day >= entry.effective_to
                ):
                    add("identity_conflict", sid=sid, day=day)
                if any(s is None for s in pair):
                    add("state_unavailable", "blocked", sid, day)
                    continue
                for state in pair:
                    if any(
                        getattr(state, field) != getattr(entry, field)
                        for field in (
                            "exchange",
                            "code",
                            "security_type",
                            "board_category",
                        )
                    ) or not (
                        state.effective_from <= entry.effective_from
                        and (
                            state.effective_to is None
                            or (
                                entry.effective_to is not None
                                and entry.effective_to <= state.effective_to
                            )
                        )
                    ):
                        add("identity_conflict", sid=sid, day=day)
                for field in ("listing_date", "is_st", "suspended", "is_delisting"):
                    if any(getattr(s, field) is None for s in pair):
                        add("state_unavailable", "blocked", sid, day)
                    elif getattr(pair[0], field) != getattr(pair[1], field):
                        add("identity_conflict", sid=sid, day=day)
                bars = (
                    indexed[0].get((entry.left_security_id, day)),
                    indexed[1].get((entry.right_security_id, day)),
                )
                if all(b is not None for b in bars):
                    if bars[0].adjustment_factor != bars[1].adjustment_factor:
                        add(
                            "adjustment_mismatch",
                            "incompatible",
                            sid,
                            day,
                            "adjustment_factor",
                        )
                    units = ("currency", "price_unit", "volume_unit", "amount_unit")
                    if any(getattr(bars[0], f) != getattr(bars[1], f) for f in units):
                        add("units_mismatch", "incompatible", sid, day)
                    if evidence:
                        for bar, source in zip(
                            bars, (evidence.left, evidence.right), strict=True
                        ):
                            if any(
                                getattr(bar, f) != getattr(source, f) for f in units
                            ):
                                add("units_mismatch", "incompatible", sid, day)
        if evidence and (
            evidence.left.currency != "CNY"
            or evidence.right.currency != "CNY"
            or evidence.basis.benchmark_definition.canonical_benchmark_id in ids
        ):
            add("benchmark_mismatch", "incompatible")
        if issues:
            return finish()
        cells = []
        for entry in selected:
            for day in days:
                bars = (
                    indexed[0].get((entry.left_security_id, day)),
                    indexed[1].get((entry.right_security_id, day)),
                )
                suspended = any(
                    s.suspended for s in states[entry.canonical_security_id, day]
                )
                for field in stocks:
                    cell = _cell(
                        entry.canonical_security_id,
                        day,
                        field,
                        getattr(bars[0], field) if bars[0] else None,
                        getattr(bars[1], field) if bars[1] else None,
                        Tolerance()
                        if field == "adjustment_factor"
                        else plan.tolerances[field],
                        "suspended" if suspended else None,
                    )
                    cells.append(cell)
        benchmark_id = evidence.basis.benchmark_definition.canonical_benchmark_id
        if "benchmark_close" in plan.fields:
            for day in days:
                cells.append(
                    _cell(
                        benchmark_id,
                        day,
                        "benchmark_close",
                        left.sample.benchmark_closes.get(day),
                        right.sample.benchmark_closes.get(day),
                        plan.tolerances["benchmark_close"],
                    )
                )
        for cell in cells:
            if cell.status != "agree":
                add(
                    cell.reason,
                    "fail" if cell.status == "conflict" else "blocked",
                    cell.canonical_security_id,
                    cell.trade_date,
                    cell.field,
                )
        for archive in (left, right):
            authorize(
                archive.sample.batch.grant,
                archive.sample.batch.source_id,
                operation_time(clock),
            )
        return finish(
            cells,
            Selection(
                security_ids=tuple(e.canonical_security_id for e in selected),
                sessions=days,
                fields=plan.fields,
                benchmark_id=benchmark_id,
            ),
            Inputs(
                left=_metadata(left, plan.left_archive_sha256, plan.left_upstream_id),
                right=_metadata(
                    right, plan.right_archive_sha256, plan.right_upstream_id
                ),
            ),
        )
    except ComparisonError as error:
        return failure(error.code, error.status, plan=plan)
    except SupplierError:
        return failure("storage_denied", "blocked", plan=plan)
    except (
        ValidationError,
        EvidenceError,
        DecimalException,
        ValueError,
        TypeError,
        OverflowError,
        RecursionError,
    ):
        return failure(
            "invalid_input", plan=plan if isinstance(plan, ComparisonPlan) else None
        )
