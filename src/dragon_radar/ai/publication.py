"""Owner-only exact-revision promotion, distinct from public archive reads."""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, insert, select

from dragon_radar.ai.models import AISubmission
from dragon_radar.ai.service import AIService
from dragon_radar.ai.tables import ai_task_drafts, ai_task_reviews
from dragon_radar.news.summary_publication import NewsSummaryArtifact
from dragon_radar.news.tables import news_article_revisions
from dragon_radar.public_daily_ai.entitlements import lock
from dragon_radar.public_daily_ai.models import (
    PromoteSummary,
    PublishDaily,
    RevokeArtifact,
    digest,
    safe_payload,
    utc_time,
    validate_candidate,
)
from dragon_radar.public_daily_ai.repository import PublicDailyAIRepository
from dragon_radar.public_daily_ai.tables import (
    ai_artifact_private_links as links,
)
from dragon_radar.public_daily_ai.tables import (
    ai_artifact_revocations as revocations,
)
from dragon_radar.public_daily_ai.tables import (
    ai_artifact_verifications as verifications,
)
from dragon_radar.public_daily_ai.tables import (
    promoted_news_summaries,
    public_daily_ai_archives,
)
from dragon_radar.publication.tables import (
    PUBLICATION_SEQUENCE_LOCK,
    source_dependencies,
)
from dragon_radar.storage.repository import Conflict, Forbidden, NotFound


class AIPublicationService:
    def __init__(self, tasks, publications, *, clock=None):
        self.tasks = tasks
        self.publications = publications
        self.database = tasks.database
        raw_clock = clock or (lambda: datetime.now(UTC))
        self.clock = lambda: utc_time(raw_clock())
        self.public = PublicDailyAIRepository(
            self.database, publications, clock=self.clock
        )

    @staticmethod
    def _ancestry(connection, task, draft):
        chain = [draft]
        seen = {draft["revision"]}
        while chain[-1]["source"] == "human_edit":
            edits = (
                connection.execute(
                    select(ai_task_reviews).where(
                        ai_task_reviews.c.task_id == task["id"],
                        ai_task_reviews.c.owner_id == task["owner_id"],
                        ai_task_reviews.c.decision == "edit",
                        ai_task_reviews.c.resulting_revision == chain[-1]["revision"],
                        ai_task_reviews.c.draft_id == chain[-1]["id"],
                    )
                )
                .mappings()
                .all()
            )
            if len(edits) != 1 or edits[0]["expected_revision"] in seen:
                raise Conflict("provider_ancestry")
            prior = (
                connection.execute(
                    select(ai_task_drafts).where(
                        ai_task_drafts.c.task_id == task["id"],
                        ai_task_drafts.c.revision == edits[0]["expected_revision"],
                    )
                )
                .mappings()
                .one()
            )
            seen.add(prior["revision"])
            chain.append(prior)
        return chain

    @staticmethod
    def _receipt(row, kind):
        if kind == "daily_ai":
            return {
                "archive_id": str(row["id"]),
                "archive_version": row["archive_version"],
                "content_hash": row["content_hash"],
                "published_at": row["published_at"].isoformat(),
            }
        return {
            "summary_id": str(row["id"]),
            "summary_revision": row["archive_version"],
            "article_revision_id": str(row["article_revision_id"]),
            "content_hash": row["content_hash"],
        }

    def publish_daily(self, token, csrf, task_id, command):
        return self._promote(
            token, csrf, task_id, PublishDaily.model_validate(command), "daily_ai"
        )

    def publish_summary(self, token, csrf, task_id, command):
        return self._promote(
            token, csrf, task_id, PromoteSummary.model_validate(command), "summary"
        )

    def eligibility(self, token, task_id, output_reference):
        """Private, advisory check; publication repeats every check transactionally."""
        with self.database.engine.begin() as connection:
            owner = self.tasks._authorize(connection, token)
            task = self.tasks._task_row(
                connection, task_id, owner_id=owner.owner_id, lock=True
            )
            kind = "daily_ai" if task["task_type"] == "daily_review" else "summary"
            draft = (
                connection.execute(
                    select(ai_task_drafts).where(
                        ai_task_drafts.c.task_id == task["id"],
                        ai_task_drafts.c.revision == task["selected_revision"],
                    )
                )
                .mappings()
                .first()
            )
            review = (
                connection.execute(
                    select(ai_task_reviews)
                    .where(
                        ai_task_reviews.c.task_id == task["id"],
                        ai_task_reviews.c.owner_id == owner.owner_id,
                        ai_task_reviews.c.resulting_revision
                        == task["selected_revision"],
                        ai_task_reviews.c.decision == "accept",
                    )
                    .order_by(
                        ai_task_reviews.c.created_at.desc(), ai_task_reviews.c.id.desc()
                    )
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if draft is None or review is None:
                return {"eligible": False, "reason": "exact_accepted_revision_required"}
            try:
                chain = self._ancestry(connection, task, draft)
                validate_candidate(
                    task=task,
                    draft=draft,
                    review=review,
                    ancestry=chain,
                    expected_hash=digest(draft["content"]),
                )
                snapshot = self.public.snapshot(
                    connection,
                    task["snapshot_id"],
                    date.fromisoformat(
                        task["input_manifest"]["publication"]["trade_date"]
                    ),
                    kind=kind,
                )
                self.public.entitlements.authorize(
                    connection,
                    output_reference,
                    provider=chain[-1]["provider"],
                    model_version=chain[-1]["model_version"],
                )
                from dragon_radar.publication.access import grant_allows_public_sharing

                current = self.publications.market._source(
                    connection, snapshot.batch.source_id
                )
                if kind == "daily_ai" and not all(
                    grant_allows_public_sharing(
                        grant,
                        source_id=snapshot.batch.source_id,
                        now=self.clock(),
                    )
                    for grant in (current, snapshot.batch.grant)
                ):
                    raise Forbidden("Source sharing denied")
                if kind == "summary":
                    article = task["input_manifest"]["input"]["article"]
                    embedded = connection.scalar(
                        select(news_article_revisions.c.embedded_grant).where(
                            news_article_revisions.c.id == UUID(article["revision_id"]),
                        )
                    )
                    from dragon_radar.domain.models import SourceGrant

                    current = self.publications.market._source(
                        connection, article["source"]["id"]
                    )
                    if embedded is None or not all(
                        grant_allows_public_sharing(
                            grant,
                            source_id=article["source"]["id"],
                            now=self.clock(),
                        )
                        for grant in (current, SourceGrant.model_validate(embedded))
                    ):
                        raise Forbidden("Source sharing denied")
            except (Conflict, Forbidden, ValueError):
                return {"eligible": False, "reason": "publication_eligibility_denied"}
            return {
                "eligible": True,
                "reason": None,
                "revision": draft["revision"],
                "expected_review_id": str(review["id"]),
                "expected_content_hash": digest(draft["content"]),
                "snapshot_id": str(task["snapshot_id"]),
                "date": task["input_manifest"]["publication"]["trade_date"],
                "kind": kind,
            }

    def _promote(self, token, csrf, task_id, command, kind):
        task_id = UUID(str(task_id))
        table = (
            public_daily_ai_archives if kind == "daily_ai" else promoted_news_summaries
        )
        request_hash = digest(
            {"task_id": str(task_id), **command.model_dump(mode="json")}
        )
        with self.database.engine.begin() as connection:
            owner = self.tasks._authorize(connection, token, csrf)
            lock(
                connection,
                f"ai-artifact-key:{owner.owner_id}:{kind}:{command.idempotency_key}",
            )
            lock(connection, f"ai-artifact-version:{kind}:{command.snapshot_id}")
            lock(connection, PUBLICATION_SEQUENCE_LOCK)
            old_link = (
                connection.execute(
                    select(links).where(
                        links.c.owner_id == owner.owner_id,
                        links.c.kind == kind,
                        links.c.idempotency_key == command.idempotency_key,
                    )
                )
                .mappings()
                .first()
            )
            if old_link:
                if old_link["request_hash"] != request_hash:
                    raise Conflict("Publication key conflict")
                self.public.snapshot(
                    connection, command.snapshot_id, command.date, kind=kind
                )
                row = (
                    connection.execute(
                        select(table).where(table.c.id == old_link["artifact_id"])
                    )
                    .mappings()
                    .one()
                )
                self.public.authorize(connection, row)
                return self._receipt(row, kind)
            task = self.tasks._task_row(
                connection, task_id, owner_id=owner.owner_id, lock=True
            )
            snapshot = self.public.snapshot(
                connection, command.snapshot_id, command.date, kind=kind
            )
            if task["snapshot_id"] != snapshot.id or task["task_type"] != (
                "daily_review" if kind == "daily_ai" else "summarize"
            ):
                raise Conflict("Task publication binding")
            drafts = (
                connection.execute(
                    select(ai_task_drafts).where(
                        ai_task_drafts.c.task_id == task_id,
                        ai_task_drafts.c.revision == command.revision,
                    )
                )
                .mappings()
                .all()
            )
            review = (
                connection.execute(
                    select(ai_task_reviews).where(
                        ai_task_reviews.c.id == command.expected_review_id,
                    )
                )
                .mappings()
                .first()
            )
            if len(drafts) != 1 or review is None:
                raise Conflict("Exact review/revision unavailable")
            draft = drafts[0]
            ancestry = self._ancestry(connection, task, draft)
            provenance = validate_candidate(
                task=task,
                draft=draft,
                review=review,
                ancestry=ancestry,
                expected_hash=command.expected_content_hash,
            )
            manifest = task["input_manifest"]
            selected = manifest["input"]
            task_input = (
                {"article_id": selected["article"]["article_id"]}
                if kind == "summary"
                else {
                    "date": selected["date"],
                    "notes_context": "",
                }
            )
            submission = AISubmission(
                task_type=task["task_type"],
                snapshot_id=snapshot.id,
                as_of=snapshot.published_at,
                version=f"v{snapshot.revision}",
                task_input=task_input,
            )
            rebuilt = AIService(self.tasks, self.publications)._task_manifest(
                snapshot, submission
            )
            if digest(rebuilt) != digest(manifest):
                raise Conflict("Immutable input mismatch")
            provider = ancestry[-1]
            entitlement = self.public.entitlements.authorize(
                connection,
                command.output_entitlement_reference,
                provider=provider["provider"],
                model_version=provider["model_version"],
            )
            # Lock the unified sequence before source dependency insertion, as PUB does.
            dependencies = (
                connection.execute(
                    select(source_dependencies).where(
                        source_dependencies.c.publication_id == snapshot.id,
                        source_dependencies.c.derived_type
                        == ("market" if kind == "daily_ai" else "news"),
                    )
                )
                .mappings()
                .all()
            )
            if kind == "summary":
                dependencies = [
                    item
                    for item in dependencies
                    if item["archive_id"] == UUID(selected["article"]["revision_id"])
                    and item["archive_type"] == "news_article_revision"
                ]
            source_ids = sorted({item["source_id"] for item in dependencies})
            if not source_ids:
                raise Forbidden("Input source dependencies absent")
            rights = {
                "sources": [
                    {
                        "source_id": source,
                        "grant": snapshot.batch.grant.model_dump(mode="json")
                        if kind == "daily_ai"
                        else connection.scalar(
                            select(news_article_revisions.c.embedded_grant).where(
                                news_article_revisions.c.id
                                == UUID(selected["article"]["revision_id"]),
                            )
                        ),
                    }
                    for source in source_ids
                ],
                "output": entitlement,
                "provider": provider["provider"],
                "model_version": provider["model_version"],
            }
            artifact_id = uuid4()
            version_query = select(
                func.coalesce(func.max(table.c.archive_version), 0)
            ).where(
                table.c.base_publication_id == snapshot.id,
            )
            article = selected.get("article")
            if article is not None:
                version_query = version_query.where(
                    table.c.article_revision_id == UUID(article["revision_id"])
                )
            previous = connection.scalar(version_query)
            if kind == "daily_ai" and command.expected_archive_version != previous:
                raise Conflict("Archive version changed")
            version = previous + 1
            now = self.clock()
            projected = safe_payload(manifest, draft["content"])
            if kind == "summary":
                text = "\n".join(
                    item["text"]
                    for key in ("facts", "inferences", "missing_evidence")
                    for item in projected[key]
                )
                projected = NewsSummaryArtifact.model_validate(
                    {
                        "schema": "dragon-radar.news-summary/v1",
                        "summary_id": artifact_id,
                        "summary_revision": version,
                        "article_id": article["article_id"],
                        "article_revision_id": article["revision_id"],
                        "article_revision": article["revision"],
                        "source_id": article["source"]["id"],
                        "source_version": article["source"]["version"],
                        "text": text,
                        "provenance": provenance,
                        "task_type": "summarize",
                        "task_snapshot_id": snapshot.id,
                        "draft_revision": command.revision,
                        "verification_status": "passed",
                        "human_review_status": "accepted",
                        "input_sha256": digest(selected),
                        "output_sha256": command.expected_content_hash,
                        "model_version": provider["model_version"],
                        "prompt_version": provider["prompt_version"],
                        "retrieval_version": task["retrieval_version"],
                        "verified_at": now,
                        "reviewed_at": review["created_at"],
                        "available_at": now,
                    }
                ).model_dump(mode="json", by_alias=True)
            sequences = []
            for source in source_ids:
                sequences.append(
                    connection.scalar(
                        insert(source_dependencies)
                        .values(
                            id=uuid4(),
                            source_id=source,
                            archive_type="public_daily_ai"
                            if kind == "daily_ai"
                            else "promoted_news_summary",
                            archive_id=artifact_id,
                            derived_type="daily_ai"
                            if kind == "daily_ai"
                            else "news_summary",
                            derived_id=artifact_id,
                            publication_id=snapshot.id,
                            created_at=now,
                        )
                        .returning(source_dependencies.c.dependency_sequence)
                    )
                )
            prior = (
                connection.scalar(
                    select(table.c.id)
                    .where(
                        table.c.base_publication_id == snapshot.id,
                        table.c.archive_version == previous,
                        table.c.article_revision_id
                        == (UUID(article["revision_id"]) if article else None),
                    )
                    .limit(1)
                )
                if previous
                else None
            )
            row = dict(
                id=artifact_id,
                base_publication_id=snapshot.id,
                trade_date=command.date,
                archive_version=version,
                publication_sequence=max(sequences),
                supersedes=prior,
                published_at=now,
                content_hash=digest(projected),
                safe_payload=projected,
                rights_manifest=rights,
                attestation_reference=command.attestation_reference,
                article_revision_id=UUID(article["revision_id"]) if article else None,
            )
            self.public.authorize(connection, row)
            connection.execute(insert(table).values(**row))
            connection.execute(
                insert(links).values(
                    artifact_id=artifact_id,
                    kind=kind,
                    owner_id=owner.owner_id,
                    task_id=task_id,
                    draft_revision=command.revision,
                    review_id=command.expected_review_id,
                    idempotency_key=command.idempotency_key,
                    request_hash=request_hash,
                    consent_reference=command.publication_consent_reference,
                    created_at=now,
                )
            )
            connection.execute(
                insert(verifications).values(
                    artifact_id=artifact_id,
                    input_hash=digest(manifest),
                    output_hash=command.expected_content_hash,
                    verification_version="ai-verification-v1",
                    model_version=provider["model_version"],
                    prompt_version=provider["prompt_version"],
                    verified_at=now,
                )
            )
            return self._receipt(row, kind)

    def revoke(self, token, csrf, task_id, artifact_id, command, *, kind="daily_ai"):
        command = RevokeArtifact.model_validate(command)
        task_id, artifact_id = UUID(str(task_id)), UUID(str(artifact_id))
        table = (
            public_daily_ai_archives if kind == "daily_ai" else promoted_news_summaries
        )
        request_hash = digest(
            {
                "task_id": str(task_id),
                "artifact_id": str(artifact_id),
                "kind": kind,
                **command.model_dump(mode="json"),
            }
        )
        with self.database.engine.begin() as connection:
            owner = self.tasks._authorize(connection, token, csrf)
            lock(
                connection,
                f"ai-artifact-revoke:{owner.owner_id}:{command.idempotency_key}",
            )
            link = (
                connection.execute(
                    select(links).where(
                        links.c.artifact_id == artifact_id,
                        links.c.owner_id == owner.owner_id,
                        links.c.task_id == task_id,
                        links.c.kind == kind,
                    )
                )
                .mappings()
                .first()
            )
            if link is None:
                raise NotFound("Owner artifact not found")
            row = (
                connection.execute(select(table).where(table.c.id == artifact_id))
                .mappings()
                .one()
            )
            if row["archive_version"] != command.expected_archive_version:
                raise Conflict("Archive version changed")
            old = (
                connection.execute(
                    select(revocations).where(
                        revocations.c.owner_id == owner.owner_id,
                        revocations.c.idempotency_key == command.idempotency_key,
                    )
                )
                .mappings()
                .first()
            )
            if old and old["request_hash"] != request_hash:
                raise Conflict("Revocation key conflict")
            if not old:
                connection.execute(
                    insert(revocations).values(
                        id=uuid4(),
                        artifact_id=artifact_id,
                        owner_id=owner.owner_id,
                        idempotency_key=command.idempotency_key,
                        request_hash=request_hash,
                        reason_code=command.reason_code,
                        revoked_at=self.clock(),
                    )
                )
            return {"status": "unavailable"}
