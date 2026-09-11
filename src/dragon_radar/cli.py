"""Local operator commands. Credentials come only from DRAGON_DATABASE_URL."""

import argparse
import json
import sys
from pathlib import Path

from alembic.util.exc import CommandError
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from dragon_radar.domain.models import MarketBatch, SourceGrant
from dragon_radar.owner.cli import COMMANDS, add_commands, execute
from dragon_radar.owner.security import OWNER_ERRORS
from dragon_radar.storage.database import Database
from dragon_radar.storage.repository import (
    Conflict,
    Forbidden,
    NotFound,
    QualityRejected,
    Repository,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dragon-radar")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Apply packaged PostgreSQL migrations")
    register = commands.add_parser(
        "source-register", help="Register a local source grant"
    )
    register.add_argument("path", type=Path)
    revoke = commands.add_parser(
        "source-revoke", help="Revoke a locally registered source"
    )
    revoke.add_argument("source_id")
    importer = commands.add_parser("import", help="Archive a local batch JSON file")
    importer.add_argument("path", type=Path)
    importer.add_argument("--key", required=True)
    publish = commands.add_parser("publish", help="Publish a quality-gated batch")
    publish.add_argument("batch_id")
    serve = commands.add_parser("serve", help="Run the development read API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    add_commands(commands)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    database = None
    try:
        database = Database()
        repository = Repository(database)
        if args.command in COMMANDS:
            result = execute(args, repository)
            if result is None:
                return 0
        elif args.command == "migrate":
            database.migrate()
            result = {"status": "migrated"}
        elif args.command == "source-register":
            repository.register_source(
                SourceGrant.model_validate_json(args.path.read_text(encoding="utf-8"))
            )
            result = {"status": "registered"}
        elif args.command == "source-revoke":
            repository.revoke_source(args.source_id)
            result = {"status": "revoked"}
        elif args.command == "import":
            record = repository.import_batch(
                MarketBatch.model_validate_json(args.path.read_text(encoding="utf-8")),
                key=args.key,
            )
            result = {
                "batch_id": str(record.id),
                "payload_hash": record.payload_hash,
                "quality_report": record.quality_report,
            }
        elif args.command == "publish":
            snapshot = repository.publish(args.batch_id)
            result = {
                "snapshot_id": str(snapshot.id),
                "revision": snapshot.revision,
                "supersedes": str(snapshot.supersedes) if snapshot.supersedes else None,
            }
        else:
            import uvicorn

            from dragon_radar.api import create_app

            if not 1 <= args.port <= 65535:
                raise ValueError("Invalid port")
            uvicorn.run(
                create_app(repository=repository),
                host=args.host,
                port=args.port,
                proxy_headers=False,
                access_log=False,
            )
            return 0
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except ValidationError:
        message = "Invalid JSON or payload structure"
    except (OSError, UnicodeError):
        message = "Cannot read local input or start service"
    except CommandError:
        message = "Migration failed"
    except SQLAlchemyError:
        message = "Storage unavailable"
    except OWNER_ERRORS:
        message = "Owner operation unavailable"
    except Conflict:
        message = "Conflict: idempotency key belongs to another payload"
    except Forbidden:
        message = "Source access denied"
    except NotFound:
        message = "Record not found"
    except QualityRejected:
        message = "Batch failed the quality gate"
    except ValueError as exc:
        message = (
            "DRAGON_DATABASE_URL is required"
            if exc.args == ("DRAGON_DATABASE_URL is required",)
            else "Invalid configuration or input"
        )
    finally:
        if database is not None:
            database.close()
    print(message, file=sys.stderr)
    return 1
