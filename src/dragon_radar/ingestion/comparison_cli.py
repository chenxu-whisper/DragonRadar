"""Private, offline Q03 CLI. No collection, database or production approval."""

import argparse
import sys
from contextlib import nullcontext
from pathlib import Path

from pydantic import ValidationError

from dragon_radar.domain.models import SourceGrant
from dragon_radar.ingestion.comparison import (
    ComparisonError,
    EvidenceApprover,
    compare_samples,
    failure,
)
from dragon_radar.ingestion.comparison_contracts import (
    MAX_METADATA_BYTES,
    MAX_REPORT_BYTES,
    MAX_ROWS,
    ComparisonPlan,
    IdentityMap,
)
from dragon_radar.ingestion.sample_archive import (
    MAX_ARCHIVE_BYTES,
    SampleArchiveError,
    read_sample_archive,
)
from dragon_radar.ingestion.supplier_transport import (
    SupplierError,
    authorize,
    operation_time,
    utc_now,
)
from dragon_radar.owner.files import secure_output
from dragon_radar.production.trust import (
    EvidenceError,
    canonical_json,
    read_asset,
    strict_json,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ComparisonError("arguments") from None


def _arguments(argv):
    parser = _Parser(
        description="Offline independent sample comparison", allow_abbrev=False
    )
    for name in ("left", "right", "plan", "identity-map", "left-grant", "right-grant"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    tokens = list(sys.argv[1:] if argv is None else argv)
    options = [t.split("=", 1)[0] for t in tokens if t.startswith("--")]
    if len(options) != len(set(options)):
        raise ComparisonError("arguments")
    return parser.parse_args(tokens)


def _clock():
    try:
        return operation_time(utc_now)
    except (SupplierError, ValueError, TypeError, OverflowError):
        raise ComparisonError("operation_time", "blocked") from None


def _read(path, limit):
    path = path.absolute()
    try:
        raw = read_asset(path.parent, path.name, limit)
    except (EvidenceError, OSError, ValueError):
        raise ComparisonError("input_unavailable") from None
    try:
        value = strict_json(raw)
    except (EvidenceError, ValueError, TypeError, OverflowError, RecursionError):
        raise ComparisonError("invalid_input") from None
    return raw, value


def _archive(path, expected, grant):
    # Check row budgets before the existing single-sample validator's loops.
    raw, value = _read(path, MAX_ARCHIVE_BYTES)
    try:
        batch = value["sample"]["batch"]
        rows = [batch[name] for name in ("bars", "security_states", "limit_up_events")]
        if any(type(items) is not list for items in rows):
            raise ComparisonError("invalid_input")
        if sum(map(len, rows)) > MAX_ROWS:
            raise ComparisonError("budget")
        del raw, value, batch, rows
        return read_sample_archive(
            path, expected_sha256=expected, grant=grant, clock=_clock
        )
    except SampleArchiveError as error:
        code = str(error)
        if code in {"storage_denied", "sample_grant_mismatch"}:
            raise ComparisonError("storage_denied", "blocked") from None
        if code == "sample_archive_integrity":
            raise ComparisonError("archive_integrity") from None
        raise ComparisonError("sample_invalid") from None
    except (KeyError, TypeError):
        raise ComparisonError("invalid_input") from None


def main(argv=None, *, evidence_approver: EvidenceApprover | None = None) -> int:
    """Parent may inject authenticated evidence approval; JSON cannot approve itself."""
    plan = None
    grants = []
    persisted = False
    args = None

    def reauthorize():
        for grant in grants:
            authorize(grant, grant.source_id, _clock())

    try:
        args = _arguments(argv)
        input_paths = {
            getattr(args, name).resolve()
            for name in (
                "left",
                "right",
                "plan",
                "identity_map",
                "left_grant",
                "right_grant",
            )
        }
        if args.output is not None and args.output.resolve() in input_paths:
            raise ComparisonError("output_conflict")
        if args.output is not None and args.output.exists():
            raise ComparisonError("output_conflict")
        with (
            secure_output(args.output, on_durable=reauthorize)
            if args.output is not None
            else nullcontext()
        ) as stream:
            metadata_bytes = 0

            def metadata(path, model):
                nonlocal metadata_bytes
                raw, _ = _read(path, 4 * 1024 * 1024)
                metadata_bytes += len(raw)
                if metadata_bytes > MAX_METADATA_BYTES:
                    raise ComparisonError("budget")
                return model.model_validate_json(raw)

            plan = metadata(args.plan, ComparisonPlan)
            identity_map = metadata(args.identity_map, IdentityMap)
            evidence_root = args.evidence_root or args.plan.absolute().parent
            if args.output is not None and args.output.resolve() in {
                (evidence_root / f"{proof}.json").resolve()
                for proof in (
                    plan.independence_evidence_sha256,
                    plan.adjustment_evidence_sha256,
                    plan.tolerance_evidence_sha256,
                )
                if proof is not None
            }:
                raise ComparisonError("output_conflict")
            for path in (args.left_grant, args.right_grant):
                grants.append(metadata(path, SourceGrant))
            reauthorize()
            left = _archive(args.left, plan.left_archive_sha256, grants[0])
            right = _archive(args.right, plan.right_archive_sha256, grants[1])
            report = compare_samples(
                left,
                right,
                plan,
                identity_map,
                evidence_root=evidence_root,
                evidence_approver=evidence_approver,
                clock=_clock,
                metadata_bytes=metadata_bytes,
            )
            raw_report = canonical_json(report.model_dump(mode="json", by_alias=True))
            if len(raw_report) > MAX_REPORT_BYTES:
                raise ComparisonError("budget")
            reauthorize()
            if stream is not None:
                stream.write(raw_report)
        persisted = args.output is not None
        # Rights may expire after fsync as well as during the durable callback.
        reauthorize()
    except ComparisonError as error:
        report = failure(error.code, error.status, plan=plan)
    except SupplierError:
        report = failure("storage_denied", "blocked", plan=plan)
    except ValidationError:
        report = failure("invalid_input", plan=plan)
    except FileExistsError:
        report = failure("output_conflict", plan=plan)
    except (
        EvidenceError,
        OSError,
        ValueError,
        TypeError,
        OverflowError,
        RecursionError,
    ):
        report = failure("output_unavailable", plan=plan)
    else:
        print(raw_report.decode("utf-8"))
        return {"pass": 0, "fail": 1, "blocked": 2, "incompatible": 2}[report.status]
    if persisted:
        try:
            args.output.unlink()
        except OSError:
            report = failure("output_unavailable", plan=plan)
    print(canonical_json(report.model_dump(mode="json", by_alias=True)).decode("utf-8"))
    return {"pass": 0, "fail": 1, "blocked": 2, "incompatible": 2}[report.status]


if __name__ == "__main__":
    raise SystemExit(main())
