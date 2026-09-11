"""Explicit local-owner commands; pairing plaintext never goes to stdout."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

from dragon_radar.owner.files import output_path, secure_output
from dragon_radar.owner.repository import (
    DEFAULT_ORIGIN,
    PAIRING_LIFETIME,
    OwnerRepository,
)
from dragon_radar.owner.security import validate_origin
from dragon_radar.storage.repository import Repository

COMMANDS = {"owner-init", "owner-pair", "owner-revoke", "serve-owner"}


def add_commands(commands: argparse._SubParsersAction) -> None:
    commands.add_parser("owner-init", help="Initialize the local owner explicitly")
    commands.add_parser(
        "owner-revoke", help="Revoke all owner sessions and pairing codes"
    )
    pair = commands.add_parser(
        "owner-pair", help="Write a short-lived code to a protected new file"
    )
    pair.add_argument("--origin", default=DEFAULT_ORIGIN)
    pair.add_argument("--output", type=Path, required=True)
    serve = commands.add_parser(
        "serve-owner", help="Serve the explicitly private owner API"
    )
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
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
    if args.command == "owner-init":
        return {"owner_id": str(owners.initialize())}
    if args.command == "owner-revoke":
        owners.revoke_all()
        return {"status": "revoked"}
    if args.command == "owner-pair":
        return issue_pairing_file(owners, args.origin, args.output)
    if args.command != "serve-owner":
        raise ValueError("Invalid owner command")
    validate_origin(args.origin)
    if not 1 <= args.port <= 65535:
        raise ValueError("Invalid port")
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
