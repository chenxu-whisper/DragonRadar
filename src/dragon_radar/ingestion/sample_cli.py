"""Bounded offline sample CLI; stdout contains diagnostic metadata only."""

import argparse
import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from dragon_radar.ingestion.sample_acceptance import (
    SampleRequest,
    evaluate_sample,
    safe_failure,
)
from dragon_radar.owner.files import secure_output

MAX_INPUT_BYTES = 16 * 1024 * 1024


class SampleError(ValueError):
    """Fixed internal diagnostic code, never a provider exception."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise SampleError("arguments") from None


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise SampleError("invalid_json")
        result[key] = value
    return result


def _constant(value):
    raise SampleError("invalid_json")


def read_sample_json(path: Path):
    """Reject links, nonregular files, duplicate keys and unbounded content."""
    path = Path(path)
    if ".." in path.parts:
        raise SampleError("input_unavailable")
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or getattr(before, "st_file_attributes", 0) & 0x400
            or before.st_nlink != 1
            or before.st_size > MAX_INPUT_BYTES
        ):
            raise SampleError("input_unavailable")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > MAX_INPUT_BYTES
                or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)
            ):
                raise SampleError("input_unavailable")
            raw = stream.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise SampleError("input_unavailable")
    except (OSError, ValueError):
        raise SampleError("input_unavailable") from None
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_float=Decimal,
            parse_constant=_constant,
        )
        return value, hashlib.sha256(raw).hexdigest()
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise SampleError("invalid_json") from None


def _mapping(request: SampleRequest, args):
    from dragon_radar.ingestion.supplier_mapping import (
        MappingProfile,
        normalize_records,
    )

    profile_data, profile_hash = read_sample_json(args.mapping)
    profile = MappingProfile.model_validate(profile_data)
    if (profile.source_id, profile.source_version) != (
        request.batch.source_id,
        request.batch.source_version,
    ):
        raise SampleError("mapping_source_mismatch")
    bars, bar_hash = read_sample_json(args.bar_rows)
    states, state_hash = read_sample_json(args.state_rows)
    if any(
        not isinstance(rows, list)
        or len(rows) > 100000
        or any(not isinstance(row, dict) for row in rows)
        for rows in (bars, states)
    ):
        raise SampleError("invalid_rows")
    result = normalize_records(profile, bar_rows=bars, state_rows=states)
    if result.issues:
        report = safe_failure("normalization_failed")
        report.update(
            kind=request.kind,
            test_only=request.kind == "test",
            profile_sha256=profile_hash,
            normalization_issues=[
                issue.model_dump(mode="json") for issue in result.issues
            ],
        )
        return None, report, {}
    payload = request.model_dump(mode="python", by_alias=True)
    payload["batch"]["bars"] = [bar.model_dump(mode="python") for bar in result.bars]
    payload["batch"]["security_states"] = [
        state.model_dump(mode="python") for state in result.states
    ]
    return (
        SampleRequest.model_validate(payload),
        None,
        {
            "profile_sha256": profile_hash,
            "bar_rows_sha256": bar_hash,
            "state_rows_sha256": state_hash,
        },
    )


def main(argv=None) -> int:
    try:
        parser = _Parser(description="Offline supplier sample engineering diagnostics")
        parser.add_argument("path", type=Path)
        parser.add_argument("--now")
        parser.add_argument("--output", type=Path)
        parser.add_argument("--mapping", type=Path)
        parser.add_argument("--bar-rows", type=Path)
        parser.add_argument("--state-rows", type=Path)
        args = parser.parse_args(argv)
        mapping_paths = (args.mapping, args.bar_rows, args.state_rows)
        if any(mapping_paths) and not all(mapping_paths):
            raise SampleError("mapping_arguments")
        if args.output is not None and any(
            args.output.resolve() == path.resolve()
            for path in (args.path, *mapping_paths)
            if path is not None
        ):
            raise SampleError("output_conflict")
        try:
            now = datetime.fromisoformat(args.now) if args.now else datetime.now(UTC)
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError
        except ValueError:
            raise SampleError("operation_time") from None
        value, sample_hash = read_sample_json(args.path)
        request = SampleRequest.model_validate(value)
        report = None
        digests = {}
        if args.mapping:
            request, report, digests = _mapping(request, args)
        if report is None:
            report = evaluate_sample(request, now=now)
            report.update(digests)
        report["sample_file_sha256"] = sample_hash
        if args.output is not None:
            try:
                with secure_output(args.output) as stream:
                    stream.write(
                        json.dumps(report, sort_keys=True, allow_nan=False).encode(
                            "utf-8"
                        )
                    )
            except (OSError, ValueError):
                raise SampleError("output_unavailable") from None
    except SampleError as error:
        report = safe_failure(str(error))
    except (ValidationError, TypeError, ValueError, RecursionError, OverflowError):
        report = safe_failure("invalid_sample")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return {"pass": 0, "blocked": 2, "fail": 1}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
