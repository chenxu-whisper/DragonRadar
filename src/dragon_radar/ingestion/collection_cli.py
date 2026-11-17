"""Private configuration preflight and explicit bounded supplier collection."""

import argparse
import hashlib
import json
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from dragon_radar.domain.models import SourceGrant
from dragon_radar.ingestion.supplier_collection import CollectionRequest, collect
from dragon_radar.ingestion.supplier_mapping import MappingProfile
from dragon_radar.ingestion.supplier_transport import (
    IfindTransport,
    SupplierError,
    authorize,
    utc_now,
)
from dragon_radar.owner.files import secure_output
from dragon_radar.production.trust import (
    EvidenceError,
    MissingEvidence,
    read_asset,
    strict_json,
)

_MAX_INPUT = 4 * 1024 * 1024
_BLOCKED = {
    "storage_denied",
    "missing_credentials",
    "authentication_failed",
    "rate_limited",
    "server_error",
    "timeout",
    "network_error",
}


class CollectionCliError(ValueError):
    """Internal fixed code; parser and filesystem diagnostics never escape."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CollectionCliError("arguments") from None


def _report(mode, code="ok"):
    return {
        "schema": "dragon-radar-supplier-collection-report/v1",
        "mode": mode,
        "status": "pass" if code == "ok" else "blocked" if code in _BLOCKED else "fail",
        "code": code,
        "production_approved": False,
        "historical_pit_status": "unverified",
        "checkpoint_status": "not_checked",
    }


def _read(path):
    try:
        raw = read_asset(path.absolute().parent, path.name, _MAX_INPUT)
    except (EvidenceError, OSError, ValueError):
        raise CollectionCliError("input_unavailable") from None
    try:
        strict_json(raw)
    except EvidenceError:
        raise CollectionCliError("invalid_configuration") from None
    return raw


def _configuration(args):
    request_raw, grant_raw, mapping_raw = (
        _read(args.request),
        _read(args.grant),
        _read(args.mapping),
    )
    request = CollectionRequest.model_validate_json(request_raw)
    grant = SourceGrant.model_validate_json(grant_raw)
    profile = MappingProfile.model_validate(strict_json(mapping_raw))
    mapping_hash = hashlib.sha256(mapping_raw).hexdigest()
    if (
        request.profile_id,
        request.source_id,
        request.source_version,
        request.profile_sha256,
    ) != (
        profile.profile_id,
        profile.source_id,
        profile.source_version,
        mapping_hash,
    ):
        raise CollectionCliError("mapping_binding_mismatch")
    authorize(grant, request.source_id, utc_now())
    try:
        if not args.archive_root.is_dir():
            raise CollectionCliError("archive_error")
        read_asset(args.archive_root.absolute(), ".probe-" + uuid4().hex, 1)
    except MissingEvidence:
        pass
    except (EvidenceError, OSError, ValueError):
        raise CollectionCliError("archive_error") from None
    client = IfindTransport(
        request.endpoint, token_env=args.token_env, timeout=args.timeout
    )
    if (
        not 1 <= args.max_attempts <= 5
        or not 0 <= args.retry_delay <= 60
        or not 0 <= args.request_interval <= 60
    ):
        raise SupplierError("invalid_limits")
    return (
        request,
        grant,
        client,
        {
            "request_sha256": hashlib.sha256(request_raw).hexdigest(),
            "mapping_sha256": mapping_hash,
            "securities": len(request.securities),
            "sessions": len(request.sessions),
            "planned_chunks": (
                (len(request.securities) + request.max_codes - 1)
                // request.max_codes
                * (
                    (len(request.sessions) + request.max_sessions - 1)
                    // request.max_sessions
                )
            ),
        },
    )


def _run(args, mode):
    try:
        request, grant, client, metadata = _configuration(args)
        report = _report(mode) | metadata
        if args.execute:
            # post() checks credential validity before every actual send, including
            # retries. Resume itself is delegated to the collector's full preflight.
            result = collect(
                request,
                grant=grant,
                archive_root=args.archive_root.absolute(),
                transport=client,
                clock=utc_now,
                max_attempts=args.max_attempts,
                retry_delay=args.retry_delay,
                request_interval=args.request_interval,
            )
            report.update(
                checkpoint_status="verified",
                chunks=len(result.chunks),
                collection_fingerprint=result.fingerprint,
            )
        return report
    except CollectionCliError as error:
        return _report(mode, str(error))
    except SupplierError as error:
        return _report(mode, error.code)
    except (ValidationError, EvidenceError, TypeError, ValueError, OverflowError):
        return _report(mode, "invalid_configuration")


def _output(args):
    if args.output is None:
        return
    target = args.output.resolve()
    if target.is_relative_to(args.archive_root.resolve()) or any(
        target == path.resolve() for path in (args.request, args.grant, args.mapping)
    ):
        raise CollectionCliError("output_conflict")


def main(argv=None) -> int:
    mode = "preflight"
    try:
        parser = _Parser(
            description="Private supplier configuration preflight; --execute collects",
            allow_abbrev=False,
        )
        for name in ("request", "grant", "mapping", "archive-root"):
            parser.add_argument("--" + name, required=True, type=Path)
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--output", type=Path)
        parser.add_argument("--timeout", type=float, default=10.0)
        parser.add_argument("--token-env", default="IFIND_ACCESS_TOKEN")
        parser.add_argument("--max-attempts", type=int, default=3)
        parser.add_argument("--retry-delay", type=float, default=1.0)
        parser.add_argument("--request-interval", type=float, default=1.0)
        args = parser.parse_args(argv)
        mode = "collect" if args.execute else "preflight"
        _output(args)
        with ExitStack() as stack:
            stream = None
            if args.output is not None:
                try:
                    stream = stack.enter_context(secure_output(args.output))
                except (OSError, ValueError):
                    raise CollectionCliError("output_unavailable") from None
            report = _run(args, mode)
            if stream is not None:
                stream.write(json.dumps(report, sort_keys=True).encode("utf-8"))
    except CollectionCliError as error:
        report = _report(mode, str(error))
    except (OSError, ValueError):
        report = _report(mode, "output_unavailable")
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return {"pass": 0, "blocked": 2, "fail": 1}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
