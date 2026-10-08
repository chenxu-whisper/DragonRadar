"""Explicit local-owner commands; pairing plaintext never goes to stdout."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dragon_radar.analysis_jobs.repository import AnalysisJobRepository
from dragon_radar.analysis_jobs.worker import AnalysisJobWorker
from dragon_radar.owner.files import output_path, secure_output
from dragon_radar.owner.repository import (
    DEFAULT_ORIGIN,
    PAIRING_LIFETIME,
    OwnerRepository,
)
from dragon_radar.owner.security import validate_origin
from dragon_radar.publication.repository import PublicationRepository
from dragon_radar.storage.repository import Repository, _identifier

COMMANDS = {
    "analysis-worker",
    "owner-export",
    "owner-init",
    "owner-pair",
    "owner-revoke",
    "serve-owner",
}


def _worker_identifier(value: str) -> str:
    try:
        return _identifier(value)
    except ValueError:
        raise argparse.ArgumentTypeError("invalid worker identifier") from None


def _poll_seconds(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("poll seconds must be a number") from None
    if not 0.1 <= parsed <= 60:
        raise argparse.ArgumentTypeError("poll seconds must be between 0.1 and 60")
    return parsed


def _bounded_integer(value: str, *, minimum: int, maximum: int, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{label} must be an integer") from None
    if not minimum <= parsed <= maximum:
        raise argparse.ArgumentTypeError(
            f"{label} must be between {minimum} and {maximum}"
        )
    return parsed


def add_commands(commands: argparse._SubParsersAction) -> None:
    worker = commands.add_parser(
        "analysis-worker", help="Run queued custom analysis jobs"
    )
    worker.add_argument(
        "--worker-id",
        type=_worker_identifier,
        default="analysis-worker-1",
    )
    worker.add_argument("--poll-seconds", type=_poll_seconds, default=1.0)
    worker.add_argument(
        "--lease-seconds",
        type=lambda value: _bounded_integer(
            value, minimum=1, maximum=3600, label="lease seconds"
        ),
        default=30,
    )
    worker.add_argument(
        "--max-attempts",
        type=lambda value: _bounded_integer(
            value, minimum=1, maximum=10, label="max attempts"
        ),
        default=3,
    )
    commands.add_parser("owner-init", help="Initialize the local owner explicitly")
    commands.add_parser(
        "owner-revoke", help="Revoke all owner sessions and pairing codes"
    )
    pair = commands.add_parser(
        "owner-pair", help="Write a short-lived code to a protected new file"
    )
    pair.add_argument("--origin", default=DEFAULT_ORIGIN)
    pair.add_argument("--output", type=Path, required=True)
    export = commands.add_parser(
        "owner-export", help="Export old local-owner review data to a new JSON file"
    )
    export.add_argument("--local-profile-id", required=True)
    export.add_argument("--output", type=Path, required=True)
    serve = commands.add_parser(
        "serve-owner", help="Serve the explicitly private owner API"
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8001)
    serve.add_argument("--origin", default=DEFAULT_ORIGIN)


def issue_pairing_file(
    owner_repository: OwnerRepository, origin: str, output: Path
) -> dict[str, str]:
    """CLI-owned repository only: capture its original aware issuance clock."""
    validate_origin(origin)
    path = output_path(output)
    original_clock = owner_repository.clock
    issued_at: datetime | None = None
    issued = False

    def clock():
        nonlocal issued_at
        value = original_clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Owner clock must return an aware datetime")
        issued_at = value.astimezone(UTC)
        return value

    try:
        with secure_output(path) as stream:
            owner_repository.clock = clock
            try:
                code = owner_repository.issue_pairing(origin)
                issued = True
            finally:
                owner_repository.clock = original_clock
            stream.write((code + "\n").encode("ascii"))
        return {
            "path": str(path),
            "expires_at": (issued_at + PAIRING_LIFETIME).isoformat(),
        }
    except BaseException:
        if issued:
            # The stable repository API has no single-pairing revoke operation.
            # Fail closed after a post-issuance file failure, including fsync.
            owner_repository.revoke_all()
        raise


def execute(args: argparse.Namespace, repository: Repository) -> dict | None:
    owners = OwnerRepository(repository.database)
    if args.command == "analysis-worker":
        publications = PublicationRepository(repository.database, repository)
        jobs = AnalysisJobRepository(repository.database, owners, publications)
        AnalysisJobWorker(
            jobs,
            worker_id=args.worker_id,
            lease_for=timedelta(seconds=args.lease_seconds),
            max_attempts=args.max_attempts,
        ).run_forever(poll_seconds=args.poll_seconds)
        return None
    if args.command == "owner-init":
        return {"owner_id": str(owners.initialize())}
    if args.command == "owner-export":
        from dragon_radar.review.export import export_local_review

        payload = export_local_review(
            repository.database, local_profile_id=args.local_profile_id
        )
        path = output_path(args.output)
        with secure_output(path) as stream:
            stream.write(
                (
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
            )
        return {"path": str(path), "counts": payload["counts"]}
    if args.command == "owner-revoke":
        owners.revoke_all()
        return {"status": "revoked"}
    if args.command == "owner-pair":
        return issue_pairing_file(owners, args.origin, args.output)
    if args.command != "serve-owner":
        raise ValueError("Invalid owner command")
    validate_origin(args.origin)
    if args.host != "127.0.0.1" or args.port != 8001:
        raise ValueError("Owner service must bind to 127.0.0.1:8001")
    import uvicorn

    from dragon_radar.owner.app import create_owner_app

    uvicorn.run(
        create_owner_app(
            repository=repository, owner_repository=owners, origin=args.origin
        ),
        host=args.host,
        port=args.port,
        proxy_headers=False,
        access_log=False,
    )
    return None
