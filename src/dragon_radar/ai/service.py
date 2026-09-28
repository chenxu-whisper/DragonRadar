"""Published-snapshot retrieval and AI task submission orchestration."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select

from dragon_radar.ai.models import (
    AI_INPUT_DOMAINS,
    AISubmission,
    AITaskRecord,
    canonical_object,
)
from dragon_radar.ai.repository import AITaskRepository
from dragon_radar.publication.models import PublishedSnapshot
from dragon_radar.publication.repository import PublicationRepository
from dragon_radar.publication.tables import publications
from dragon_radar.storage.repository import Conflict, NotFound


def _bounded_identifier(value, label: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 200:
        raise ValueError(f"Invalid {label}")
    return value


def _date(value, label: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"Invalid {label}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"Invalid {label}") from None


class AIService:
    def __init__(
        self,
        tasks: AITaskRepository,
        publications_repository: PublicationRepository,
    ):
        self.tasks = tasks
        self.publications = publications_repository
        self.tasks.publications = publications_repository

    @staticmethod
    def _publication_manifest(snapshot: PublishedSnapshot) -> dict:
        publication = snapshot.publication
        return {
            "snapshot_id": str(publication.id),
            "as_of": publication.published_at.isoformat(),
            "version": f"v{publication.version}",
            "trade_date": publication.trade_date.isoformat(),
        }

    @staticmethod
    def _news(snapshot: PublishedSnapshot) -> dict:
        result = snapshot.result("news")
        payload = result.model_dump(mode="json")["payload"]
        if result.status != "ready" or not isinstance(payload.get("articles"), list):
            raise NotFound("Published news input is unavailable")
        return payload

    @staticmethod
    def _market(snapshot: PublishedSnapshot) -> dict:
        result = snapshot.result("market")
        payload = result.model_dump(mode="json")["payload"]
        if result.status != "ready":
            raise NotFound("Published market input is unavailable")
        return payload

    def _task_manifest(
        self,
        snapshot: PublishedSnapshot,
        submission: AISubmission,
    ) -> dict:
        task_input = dict(submission.task_input)
        if submission.task_type == "summarize":
            if set(task_input) != {"article_id"}:
                raise ValueError("Invalid summarize input")
            article_id = _bounded_identifier(task_input["article_id"], "article ID")
            try:
                UUID(article_id)
            except ValueError:
                raise ValueError("Invalid article ID") from None
            matches = [
                article
                for article in self._news(snapshot)["articles"]
                if article.get("article_id") == article_id
            ]
            if len(matches) != 1:
                raise NotFound("Published article not found")
            selected_input = {"article": matches[0]}
        elif submission.task_type == "topic_suggest":
            if set(task_input) != {"date"}:
                raise ValueError("Invalid topic suggestion input")
            trade_date = _date(task_input["date"], "date")
            if trade_date != snapshot.trade_date:
                raise Conflict("Task date does not match publication")
            news = self._news(snapshot)
            selected_input = {
                "date": trade_date.isoformat(),
                "articles": news["articles"][:100],
            }
        elif submission.task_type == "explain_stock":
            if set(task_input) != {"security_id", "date"}:
                raise ValueError("Invalid stock explanation input")
            security_id = _bounded_identifier(task_input["security_id"], "security ID")
            trade_date = _date(task_input["date"], "date")
            if trade_date != snapshot.trade_date:
                raise Conflict("Task date does not match publication")
            market = self._market(snapshot)
            stocks = market.get("stocks", {}).get("stocks", {})
            if not isinstance(stocks, dict) or security_id not in stocks:
                raise NotFound("Published stock not found")
            selected_input = {
                "date": trade_date.isoformat(),
                "stock": {"security_id": security_id, **stocks[security_id]},
            }
        else:
            if set(task_input) != {"date", "notes_context"}:
                raise ValueError("Invalid daily review input")
            trade_date = _date(task_input["date"], "date")
            notes = task_input["notes_context"]
            if trade_date != snapshot.trade_date:
                raise Conflict("Task date does not match publication")
            if not isinstance(notes, str):
                raise ValueError("Invalid notes context")
            market = self._market(snapshot)
            selected_input = {
                "date": trade_date.isoformat(),
                "notes_context": notes,
                "market_overview": market.get("overview"),
            }
        manifest = {
            "task_type": submission.task_type,
            "publication": self._publication_manifest(snapshot),
            "input": selected_input,
        }
        canonical_object(manifest)
        return manifest

    def submit(
        self,
        token: str,
        csrf: str,
        *,
        task_type: str,
        snapshot_id: UUID | str,
        as_of: datetime | str,
        version: str,
        task_input: dict,
        key: str,
    ) -> AITaskRecord:
        try:
            submission = AISubmission(
                task_type=task_type,
                snapshot_id=UUID(str(snapshot_id)),
                as_of=as_of,
                version=version,
                task_input=task_input,
            )
        except (TypeError, ValueError):
            raise ValueError("Invalid AI submission") from None
        with self.tasks.database.engine.begin() as connection:
            owner = self.tasks._authorize(connection, token, csrf)
            replay = self.tasks.replay_for_owner(
                connection,
                owner.owner_id,
                submission,
                key=key,
            )
            if replay is not None:
                return replay
            row = (
                connection.execute(
                    select(publications).where(
                        publications.c.id == submission.snapshot_id
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFound("Publication not found")
            if row["status"] != "published":
                raise Conflict("Publication is not published")
            snapshot = self.publications._authorized_records(
                connection,
                [row],
                ai_domains=AI_INPUT_DOMAINS[submission.task_type],
            )[0]
            expected_version = f"v{snapshot.publication.version}"
            if (
                submission.as_of != snapshot.publication.published_at
                or submission.version != expected_version
            ):
                raise Conflict("Publication coordinates do not match")
            manifest = self._task_manifest(snapshot, submission)
            return self.tasks.submit_for_owner(
                connection,
                owner.owner_id,
                submission,
                manifest,
                key=key,
            )


__all__ = ["AIService"]
