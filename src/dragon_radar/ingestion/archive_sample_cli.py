"""Offline complete archive -> explicit mapping -> sample quality diagnostics."""

import argparse
import json
from contextlib import nullcontext
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from dragon_radar.ingestion.archive_sample import (
    INPUTS as _INPUTS,
)
from dragon_radar.ingestion.archive_sample import (
    MAX_INPUT as _MAX_INPUT,
)
from dragon_radar.ingestion.archive_sample import (
    ArchiveSampleError,
    evaluate_archive,
)
from dragon_radar.ingestion.archive_sample import (
    failure as _failure,
)
from dragon_radar.ingestion.sample_archive import (
    SampleArchiveError,
    archive_bytes,
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
        raise ArchiveSampleError("arguments") from None


def _read(path):
    try:
        raw = read_asset(path.absolute().parent, path.name, _MAX_INPUT)
    except (EvidenceError, OSError, ValueError):
        raise ArchiveSampleError("input_unavailable") from None
    try:
        strict_json(raw)
    except EvidenceError:
        raise ArchiveSampleError("invalid_configuration") from None
    return raw


def _run(args, clock):
    try:
        raw = {name: _read(getattr(args, name)) for name in _INPUTS}
        return evaluate_archive(
            raw, archive_root=args.archive_root.absolute(), clock=clock
        )
    except ArchiveSampleError as error:
        from dragon_radar.ingestion.archive_sample import ArchiveSampleResult

        return ArchiveSampleResult(_failure(str(error)))


def _output(args):
    targets = [
        path.resolve() for path in (args.output, args.sample_output) if path is not None
    ]
    if len(set(targets)) != len(targets) or any(
        target.is_relative_to(args.archive_root.resolve())
        or any(target == getattr(args, name).resolve() for name in _INPUTS)
        for target in targets
    ):
        raise ArchiveSampleError("output_conflict")


class _NoArchive(Exception):
    """Rollback the reserved sample after safely writing a failed diagnostic."""


def _publish(args, clock):
    report = None
    result = None

    def reauthorize():
        if result is not None and result.archive is not None:
            authorize(
                result.archive.sample.batch.grant,
                result.archive.sample.batch.source_id,
                operation_time(clock),
            )

    try:
        with (
            secure_output(args.sample_output, on_durable=reauthorize)
            if args.sample_output is not None
            else nullcontext()
        ) as sample_stream:
            with (
                secure_output(args.output, on_durable=reauthorize)
                if args.output is not None
                else nullcontext()
            ) as report_stream:
                result = _run(args, clock)
                report = result.report
                content = None
                if sample_stream is not None and result.archive is not None:
                    content = archive_bytes(result.archive, clock=clock)
                    report = report | {
                        "sample_archive_sha256": sha256(content).hexdigest()
                    }
                if report_stream is not None:
                    report_stream.write(
                        json.dumps(report, sort_keys=True, allow_nan=False).encode(
                            "utf-8"
                        )
                    )
            if sample_stream is not None:
                if content is None:
                    raise _NoArchive
                # Revalidate rights after the report becomes durable.
                content = archive_bytes(result.archive, clock=clock)
                if sha256(content).hexdigest() != report["sample_archive_sha256"]:
                    raise SampleArchiveError("sample_archive_integrity")
                sample_stream.write(content)
                authorize(
                    result.archive.sample.batch.grant,
                    result.archive.sample.batch.source_id,
                    operation_time(clock),
                )
        return report
    except _NoArchive:
        return report


def main(argv=None) -> int:
    try:
        parser = _Parser(
            description="Offline archived supplier sample diagnostics",
            allow_abbrev=False,
        )
        for name in (*_INPUTS, "archive-root"):
            parser.add_argument("--" + name, type=Path, required=True)
        parser.add_argument("--output", type=Path)
        parser.add_argument("--sample-output", type=Path)
        parser.add_argument("--now")
        args = parser.parse_args(argv)
        try:
            now = datetime.fromisoformat(args.now) if args.now else utc_now()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError
        except ValueError:
            raise ArchiveSampleError("operation_time") from None
        _output(args)
        report = _publish(args, (lambda: now) if args.now else utc_now)
    except ArchiveSampleError as error:
        report = _failure(str(error))
    except SampleArchiveError as error:
        report = _failure(str(error))
    except SupplierError as error:
        report = _failure(error.code)
    except (OSError, ValueError):
        report = _failure("output_unavailable")
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return {"pass": 0, "blocked": 2, "fail": 1}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
