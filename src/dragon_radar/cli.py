"""Local operator commands. Credentials come only from DRAGON_DATABASE_URL."""

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

from alembic.util.exc import CommandError
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from dragon_radar.ai.worker import (
    DEFAULT_AI_LEASE_DURATION,
    validate_ai_worker_timing,
)
from dragon_radar.domain.models import MarketBatch, SourceGrant
from dragon_radar.ingestion.contracts import MarketImportAttempt, MarketImportTask
from dragon_radar.ingestion.local_json import load_licensed_market_json
from dragon_radar.ingestion.runner import MarketIngestionRunner
from dragon_radar.owner.cli import COMMANDS, add_commands, execute
from dragon_radar.owner.security import OWNER_ERRORS
from dragon_radar.publication.models import PublicationCommand
from dragon_radar.publication.service import PublicationService
from dragon_radar.regulation.models import (
    RegulationEvent,
    RegulationObservation,
    RegulationRule,
)
from dragon_radar.regulation.repository import RegulationRepository
from dragon_radar.roles.materialize import RoleMaterializer
from dragon_radar.roles.repository import RoleRepository
from dragon_radar.storage.database import Database
from dragon_radar.storage.repository import (
    Conflict,
    Forbidden,
    NotFound,
    QualityRejected,
    Repository,
    _identifier,
)
from dragon_radar.topics.models import TopicAttempt, TopicBatch
from dragon_radar.topics.repository import TopicRepository


def _topic_attempt_result(attempt: TopicAttempt) -> dict[str, object]:
    return {
        "attempt_id": str(attempt.id),
        "status": attempt.status,
        "stage": attempt.stage,
        "missing_items": list(attempt.missing_items),
        "failed_cursor": attempt.failed_cursor,
        "request_from": (
            attempt.request_from.isoformat()
            if attempt.request_from is not None
            else None
        ),
        "request_to": (
            attempt.request_to.isoformat() if attempt.request_to is not None else None
        ),
        "retry_from": (
            attempt.retry_from.isoformat() if attempt.retry_from is not None else None
        ),
        "retry_to": (
            attempt.retry_to.isoformat() if attempt.retry_to is not None else None
        ),
    }


def _market_attempt_result(attempt: MarketImportAttempt) -> dict[str, object]:
    return {
        "attempt_id": str(attempt.id),
        "task_id": str(attempt.task_id),
        "batch_id": str(attempt.batch_id),
        "status": attempt.status,
        "stage": attempt.stage,
        "missing_items": list(attempt.missing_items),
        "failed_cursor": attempt.failed_cursor,
        "request_from": attempt.request_from.isoformat(),
        "request_to": attempt.request_to.isoformat(),
        "retry_from": (
            attempt.retry_from.isoformat() if attempt.retry_from is not None else None
        ),
        "retry_to": (
            attempt.retry_to.isoformat() if attempt.retry_to is not None else None
        ),
    }


def _market_task_result(task: MarketImportTask) -> dict[str, object]:
    return {
        "task_id": str(task.id),
        "status": task.status,
        "source_id": task.source_id,
        "request_from": task.request_from.isoformat(),
        "request_to": task.request_to.isoformat(),
    }


def _ai_worker_id(value: str) -> str:
    try:
        if len(value) > 100:
            raise ValueError
        return _identifier(value)
    except ValueError:
        raise argparse.ArgumentTypeError("invalid worker identifier") from None


def _ai_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("invalid bounded number") from None
    if not 0.1 <= parsed <= 300:
        raise argparse.ArgumentTypeError("number must be between 0.1 and 300")
    return parsed


def _ai_integer(value: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("invalid bounded integer") from None
    if not minimum <= parsed <= maximum:
        raise argparse.ArgumentTypeError(
            f"integer must be between {minimum} and {maximum}"
        )
    return parsed


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
    market_import = commands.add_parser(
        "market-import", help="Import an explicitly licensed local market JSON batch"
    )
    market_import.add_argument("path", type=Path)
    market_import.add_argument("--key", required=True)
    commands.add_parser("market-tasks", help="List licensed market import tasks")
    market_task_show = commands.add_parser(
        "market-task-show", help="Show a licensed market import task"
    )
    market_task_show.add_argument("task_id")
    market_attempts = commands.add_parser(
        "market-attempts", help="List licensed market import attempts"
    )
    market_attempts.add_argument("--status")
    market_attempt_show = commands.add_parser(
        "market-attempt-show", help="Show a licensed market import attempt"
    )
    market_attempt_show.add_argument("attempt_id")
    market_retry = commands.add_parser(
        "market-retry", help="Retry a quarantined licensed market import"
    )
    market_retry.add_argument("attempt_id")
    market_retry.add_argument("--path", required=True, type=Path)
    market_retry.add_argument("--key", required=True)
    publish = commands.add_parser("publish", help="Publish a quality-gated batch")
    publish.add_argument("batch_id")
    topic_import = commands.add_parser(
        "topic-import", help="Archive a local topic batch JSON file"
    )
    topic_import.add_argument("path", type=Path)
    topic_import.add_argument("--key", required=True)
    topic_publish = commands.add_parser(
        "topic-publish", help="Publish a quality-gated topic batch"
    )
    topic_publish.add_argument("batch_id")
    topic_attempts = commands.add_parser(
        "topic-attempts", help="List topic import attempts"
    )
    topic_attempts.add_argument("--status")
    topic_attempt_show = commands.add_parser(
        "topic-attempt-show", help="Show a topic import attempt"
    )
    topic_attempt_show.add_argument("attempt_id")
    topic_retry = commands.add_parser(
        "topic-retry", help="Retry a failed topic import range"
    )
    topic_retry.add_argument("attempt_id")
    topic_retry.add_argument("--path", required=True, type=Path)
    topic_retry.add_argument("--key", required=True)
    publication_publish = commands.add_parser(
        "publication-publish", help="Atomically publish a workbench manifest"
    )
    publication_publish.add_argument("path", type=Path)
    publication_publish.add_argument("--key", required=True)
    role_materialize = commands.add_parser(
        "role-materialize", help="Materialize immutable role evidence"
    )
    role_materialize.add_argument("--market-snapshot", required=True)
    role_materialize.add_argument("--topic-snapshot", required=True)
    role_materialize.add_argument("--rule-version", required=True)
    role_materialize.add_argument("--benchmark-id", required=True)
    role_materialize.add_argument("--key", required=True)
    role_validate_pending = commands.add_parser(
        "role-validate-pending",
        help="Validate mature pending relay evidence",
    )
    role_validate_pending.add_argument(
        "--through-market-snapshot",
        required=True,
    )
    role_validate_pending.add_argument("--key", required=True)
    regulation_rule_register = commands.add_parser(
        "regulation-rule-register",
        help="Register an immutable reviewed regulation rule version",
    )
    regulation_rule_register.add_argument("path", type=Path)
    regulation_rule_register.add_argument("--key", required=True)
    regulation_rule_activate = commands.add_parser(
        "regulation-rule-activate",
        help="Append activation for an eligible regulation rule",
    )
    regulation_rule_activate.add_argument("rule_id")
    regulation_rule_activate.add_argument("rule_version")
    regulation_rule_activate.add_argument("--key", required=True)
    regulation_rule_withdraw = commands.add_parser(
        "regulation-rule-withdraw",
        help="Append withdrawal for a regulation rule",
    )
    regulation_rule_withdraw.add_argument("rule_id")
    regulation_rule_withdraw.add_argument("rule_version")
    regulation_rule_withdraw.add_argument("--reason", required=True)
    regulation_rule_withdraw.add_argument("--key", required=True)
    regulation_event_add = commands.add_parser(
        "regulation-event-add",
        help="Append a formally disclosed regulation event",
    )
    regulation_event_add.add_argument("path", type=Path)
    regulation_event_add.add_argument("--key", required=True)
    regulation_materialize = commands.add_parser(
        "regulation-materialize",
        help="Materialize regulation calculations from reviewed local observations",
    )
    regulation_materialize.add_argument("path", type=Path)
    regulation_materialize.add_argument("--market-snapshot", required=True)
    regulation_materialize.add_argument(
        "--event-source-completeness",
        choices=("unknown", "complete"),
        default="unknown",
    )
    regulation_materialize.add_argument("--event-source-id")
    regulation_materialize.add_argument("--event-source-version")
    regulation_materialize.add_argument("--key", required=True)
    ai_worker = commands.add_parser("ai-worker", help="Run bounded queued AI tasks")
    ai_worker.add_argument(
        "--worker-id",
        type=_ai_worker_id,
        default="ai-worker-1",
    )
    ai_worker.add_argument("--poll-seconds", type=_ai_float, default=1.0)
    ai_worker.add_argument(
        "--lease-seconds",
        type=lambda value: _ai_integer(value, 1, 3600),
        default=int(DEFAULT_AI_LEASE_DURATION.total_seconds()),
    )
    ai_worker.add_argument(
        "--max-attempts",
        type=lambda value: _ai_integer(value, 1, 10),
        default=3,
    )
    ai_worker.add_argument(
        "--provider-timeout-seconds",
        type=_ai_float,
        default=None,
    )
    ai_worker.add_argument(
        "--concurrency",
        type=lambda value: _ai_integer(value, 1, 8),
        default=None,
    )
    serve = commands.add_parser("serve", help="Run the development read API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    add_commands(commands)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    database = None
    ai_config = None
    try:
        if args.command == "ai-worker":
            from dragon_radar.ai.provider import load_provider_config

            ai_config = load_provider_config()
            args.provider_timeout_seconds = (
                args.provider_timeout_seconds
                if args.provider_timeout_seconds is not None
                else ai_config.timeout_seconds
            )
            args.concurrency = (
                args.concurrency
                if args.concurrency is not None
                else ai_config.concurrency
            )
            validate_ai_worker_timing(
                lease_for=timedelta(seconds=args.lease_seconds),
                provider_timeout=args.provider_timeout_seconds,
            )
        database = Database()
        repository = Repository(database)
        topic_repository = (
            TopicRepository(database) if args.command.startswith("topic-") else None
        )
        market_runner = (
            MarketIngestionRunner(repository)
            if args.command.startswith("market-")
            else None
        )
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
        elif args.command == "market-import":
            imported = market_runner.import_batch(
                load_licensed_market_json(args.path), key=args.key
            )
            if imported.status == "quarantined":
                raise QualityRejected("Licensed market batch was quarantined")
            result = {
                "task_id": str(imported.task_id),
                "attempt_id": str(imported.attempt_id),
                "batch_id": str(imported.batch_id),
                "status": imported.status,
            }
        elif args.command == "market-tasks":
            result = {
                "tasks": [
                    _market_task_result(task) for task in market_runner.list_tasks()
                ]
            }
        elif args.command == "market-task-show":
            result = _market_task_result(market_runner.get_task(args.task_id))
        elif args.command == "market-attempts":
            result = {
                "attempts": [
                    _market_attempt_result(attempt)
                    for attempt in market_runner.list_attempts(status=args.status)
                ]
            }
        elif args.command == "market-attempt-show":
            result = _market_attempt_result(market_runner.get_attempt(args.attempt_id))
        elif args.command == "market-retry":
            imported = market_runner.retry_attempt(
                args.attempt_id,
                load_licensed_market_json(args.path),
                key=args.key,
            )
            if imported.status == "quarantined":
                raise QualityRejected("Licensed market batch was quarantined")
            result = {
                "task_id": str(imported.task_id),
                "attempt_id": str(imported.attempt_id),
                "batch_id": str(imported.batch_id),
                "status": imported.status,
            }
        elif args.command == "publish":
            snapshot = repository.publish(args.batch_id)
            result = {
                "snapshot_id": str(snapshot.id),
                "revision": snapshot.revision,
                "supersedes": str(snapshot.supersedes) if snapshot.supersedes else None,
            }
        elif args.command == "topic-import":
            record = topic_repository.import_batch(
                TopicBatch.model_validate_json(args.path.read_text(encoding="utf-8")),
                key=args.key,
            )
            result = {"batch_id": str(record.id), "status": "imported"}
        elif args.command == "topic-publish":
            snapshot = topic_repository.publish(args.batch_id)
            result = {
                "snapshot_id": str(snapshot.id),
                "batch_id": str(snapshot.batch_id),
                "status": "published",
                "revision": snapshot.revision,
                "supersedes": (
                    str(snapshot.supersedes) if snapshot.supersedes else None
                ),
            }
        elif args.command == "topic-attempts":
            result = {
                "attempts": [
                    _topic_attempt_result(attempt)
                    for attempt in topic_repository.list_attempts(status=args.status)
                ]
            }
        elif args.command == "topic-attempt-show":
            result = _topic_attempt_result(
                topic_repository.get_attempt(args.attempt_id)
            )
        elif args.command == "topic-retry":
            batch = TopicBatch.model_validate_json(
                args.path.read_text(encoding="utf-8")
            )
            record = topic_repository.retry_attempt(
                args.attempt_id,
                batch,
                key=args.key,
            )
            result = {"batch_id": str(record.id), "status": "imported"}
        elif args.command == "publication-publish":
            command = PublicationCommand.model_validate_json(
                args.path.read_text(encoding="utf-8")
            )
            publication = PublicationService(database, repository).publish(
                command, key=args.key
            )
            result = {
                "snapshot_id": str(publication.id),
                "trade_date": publication.trade_date.isoformat(),
                "version": publication.version,
                "status": publication.status,
                "supersedes": (
                    str(publication.supersedes) if publication.supersedes else None
                ),
                "published_at": publication.published_at.isoformat(),
            }
        elif args.command == "role-materialize":
            role_run = RoleMaterializer(database, repository).materialize(
                market_snapshot_id=args.market_snapshot,
                topic_snapshot_id=args.topic_snapshot,
                rule_version=args.rule_version,
                benchmark_id=args.benchmark_id,
                key=args.key,
            )
            result = {
                "role_run_id": str(role_run.id),
                "status": role_run.status,
                "trade_date": role_run.trade_date.isoformat(),
            }
        elif args.command == "role-validate-pending":
            validated = RoleRepository(database, repository).validate_pending(
                args.through_market_snapshot,
                key=args.key,
            )
            result = {
                "through_market_snapshot_id": str(validated.through_market_snapshot_id),
                "validation_ids": [
                    str(validation_id) for validation_id in validated.validation_ids
                ],
                "mature_count": validated.mature_count,
                "immature_count": validated.immature_count,
            }
        elif args.command == "regulation-rule-register":
            rule = RegulationRepository(
                database,
                repository,
            ).register_rule(
                RegulationRule.model_validate_json(
                    args.path.read_text(encoding="utf-8")
                ),
                key=args.key,
            )
            result = {
                "rule_id": rule.rule_id,
                "rule_version": rule.rule_version,
                "active": rule.active,
            }
        elif args.command == "regulation-rule-activate":
            status = RegulationRepository(database, repository).activate_rule(
                args.rule_id,
                args.rule_version,
                key=args.key,
            )
            result = {
                "status_id": str(status.id),
                "rule_id": status.rule_id,
                "rule_version": status.rule_version,
                "status": status.status,
            }
        elif args.command == "regulation-rule-withdraw":
            status = RegulationRepository(database, repository).withdraw_rule(
                args.rule_id,
                args.rule_version,
                reason=args.reason,
                key=args.key,
            )
            result = {
                "status_id": str(status.id),
                "rule_id": status.rule_id,
                "rule_version": status.rule_version,
                "status": status.status,
            }
        elif args.command == "regulation-event-add":
            event = RegulationRepository(database, repository).append_event(
                RegulationEvent.model_validate_json(
                    args.path.read_text(encoding="utf-8")
                ),
                key=args.key,
            )
            result = {"event_id": event.event_id, "status": "recorded"}
        elif args.command == "regulation-materialize":
            payload = json.loads(args.path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError("Regulation observations must be a JSON array")
            regulation_snapshot = RegulationRepository(
                database,
                repository,
            ).materialize(
                market_snapshot_id=args.market_snapshot,
                observations=tuple(
                    RegulationObservation.model_validate(item) for item in payload
                ),
                event_source_completeness=args.event_source_completeness,
                event_source_id=args.event_source_id,
                event_source_version=args.event_source_version,
                key=args.key,
            )
            result = {
                "regulation_snapshot_id": str(regulation_snapshot.id),
                "market_snapshot_id": str(regulation_snapshot.market_snapshot_id),
                "trade_date": regulation_snapshot.trade_date.isoformat(),
                "event_source_completeness": (
                    regulation_snapshot.event_source_completeness
                ),
                "event_source_id": regulation_snapshot.event_source_id,
                "event_source_version": regulation_snapshot.event_source_version,
            }
        elif args.command == "ai-worker":
            from dragon_radar.ai.provider import provider_from_environment
            from dragon_radar.ai.repository import AITaskRepository
            from dragon_radar.ai.worker import AIWorker
            from dragon_radar.owner.repository import OwnerRepository
            from dragon_radar.publication.repository import PublicationRepository

            assert ai_config is not None
            publications = PublicationRepository(database, repository)
            tasks = AITaskRepository(
                database,
                OwnerRepository(database),
                publications,
            )
            AIWorker(
                tasks,
                provider_from_environment(ai_config),
                worker_id=args.worker_id,
                lease_for=timedelta(seconds=args.lease_seconds),
                provider_timeout=args.provider_timeout_seconds,
                max_attempts=args.max_attempts,
                max_tokens=ai_config.max_tokens,
                max_cost=ai_config.max_cost,
                concurrency=args.concurrency,
            ).run_forever(poll_seconds=args.poll_seconds)
            return 0
        else:
            import uvicorn

            from dragon_radar.http.app import create_workbench_app
            from dragon_radar.publication.repository import PublicationRepository

            if not 1 <= args.port <= 65535:
                raise ValueError("Invalid port")
            uvicorn.run(
                create_workbench_app(
                    repository=PublicationRepository(database, repository)
                ),
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
