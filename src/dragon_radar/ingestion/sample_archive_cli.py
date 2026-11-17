"""Offline verification of an existing private normalized sample."""

import argparse
import json
import re
from contextlib import nullcontext
from pathlib import Path

from pydantic import ValidationError

from dragon_radar.domain.models import SourceGrant
from dragon_radar.ingestion.archive_sample import MAX_INPUT, failure
from dragon_radar.ingestion.sample_archive import (
    SampleArchiveError,
    verify_sample_archive,
)
from dragon_radar.ingestion.supplier_transport import (
    SupplierError,
    authorize,
    operation_time,
    utc_now,
)
from dragon_radar.owner.files import secure_output
from dragon_radar.production.trust import EvidenceError, read_asset, strict_json


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise SampleArchiveError("arguments") from None


def _grant(path):
    try:
        path = path.absolute()
        raw = read_asset(path.parent, path.name, MAX_INPUT)
    except (EvidenceError, OSError, ValueError):
        raise SampleArchiveError("input_unavailable") from None
    try:
        strict_json(raw)
        return SourceGrant.model_validate_json(raw)
    except (EvidenceError, ValidationError, ValueError, RecursionError, OverflowError):
        raise SampleArchiveError("invalid_grant") from None


def _clock():
    try:
        return operation_time(utc_now)
    except (SupplierError, ValueError, TypeError, OverflowError):
        raise SampleArchiveError("operation_time") from None


def _run(args, grant):
    return verify_sample_archive(
        args.sample, expected_sha256=args.sha256, grant=grant, clock=_clock
    )


def main(argv=None) -> int:
    try:
        parser = _Parser(
            description="Offline normalized sample verification", allow_abbrev=False
        )
        parser.add_argument("--sample", type=Path, required=True)
        parser.add_argument("--sha256", required=True)
        parser.add_argument("--grant", type=Path, required=True)
        parser.add_argument("--output", type=Path)
        args = parser.parse_args(argv)
        if re.fullmatch(r"[0-9a-f]{64}", args.sha256) is None:
            raise SampleArchiveError("arguments")
        if args.output is not None and args.output.resolve() in {
            args.sample.resolve(),
            args.grant.resolve(),
        }:
            raise SampleArchiveError("output_conflict")
        grant = None

        def reauthorize():
            if grant is not None:
                authorize(grant, grant.source_id, _clock())

        with (
            secure_output(args.output, on_durable=reauthorize)
            if args.output is not None
            else nullcontext()
        ) as stream:
            try:
                grant = _grant(args.grant)
                result = _run(args, grant)
                report = result.report
            except SampleArchiveError as error:
                report = failure(str(error))
            if stream is not None:
                stream.write(
                    json.dumps(report, sort_keys=True, allow_nan=False).encode()
                )
    except SampleArchiveError as error:
        report = failure(str(error))
    except SupplierError as error:
        report = failure(error.code)
    except (OSError, ValueError):
        report = failure("output_unavailable")
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return {"pass": 0, "blocked": 2, "fail": 1}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
