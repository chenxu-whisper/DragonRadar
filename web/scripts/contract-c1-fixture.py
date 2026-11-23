"""Fixture-only synthetic input generator. Never imported by the application."""

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str).encode()


def generate(output):
    from factories import NOW, bar, batch, grant, state
    from news_factories import article_revision, association, event_evidence
    from topic_factories import (
        TOPIC_AVAILABLE_AT,
        TOPIC_DAYS,
        coverage_cell,
        topic_batch,
        topic_definition,
        topic_membership,
    )

    from dragon_radar.domain.models import ExchangeCalendar

    # Exclusive directory/file creation prevents accidental reuse of archived inputs.
    output.mkdir()
    source = grant(source_id="synthetic-c1", ai=True, sharing=True)
    calendar = ExchangeCalendar(exchange="TEST", version="c1", sessions=TOPIC_DAYS)
    manifest = {
        "synthetic": True,
        "version": "synthetic-c1-v1",
        "publications": [],
        "files": {},
    }

    def freeze(name, model):
        payload = (
            model.model_dump(mode="json") if hasattr(model, "model_dump") else model
        )
        raw = encoded(payload)
        with (output / name).open("xb") as handle:
            handle.write(raw)
        manifest["files"][name] = hashlib.sha256(raw).hexdigest()

    freeze("grant.json", source)
    for index in (1, 2):
        security, sector = f"SYNTH-C1-{index}", f"SYNTH-SECTOR-{index}"
        version, adjustment, pool = f"s{index}", f"a{index}", f"p{index}"
        cutoff = NOW + timedelta(hours=index - 1)
        market = batch(
            grant=source,
            source_id=source.source_id,
            source_version=version,
            adjustment_version=adjustment,
            pool_version=pool,
            as_of=cutoff,
            calendar=calendar,
            expected_security_ids=(security,),
            security_states=(
                state(
                    security,
                    source_id=source.source_id,
                    source_version=version,
                    pool_version=pool,
                    effective_from=TOPIC_DAYS[0],
                    available_at=TOPIC_AVAILABLE_AT,
                ),
            ),
            bars=tuple(
                bar(
                    security,
                    day,
                    source_id=source.source_id,
                    source_version=version,
                    adjustment_version=adjustment,
                    available_at=bar(trade_date=day).available_at
                    + timedelta(hours=index - 1),
                )
                for day in TOPIC_DAYS
            ),
        )
        topics = topic_batch(
            grant=source,
            source_id=source.source_id,
            source_version=version,
            as_of=cutoff,
            taxonomy_version=f"t{index}",
            membership_version=f"m{index}",
            topics=[topic_definition(sector, name=f"Synthetic C1 sector {index}")],
            memberships=[topic_membership(sector, security)],
            coverage_cells=[coverage_cell(sector, day) for day in TOPIC_DAYS],
        )
        article = article_revision(
            id=uuid4(),
            article_id=uuid4(),
            grant=source,
            source_id=source.source_id,
            source_version=f"news-v{index}",
            source_article_id=f"synthetic-c1-{index}",
            title=f"Synthetic C1 article {index}",
            content=(
                f"Synthetic C1 archived article {index}. "
                "No provider or real market content."
            ),
            published_at=NOW - timedelta(hours=4 - index),
            received_at=NOW - timedelta(hours=2 - index),
            available_at=cutoff,
        )
        link = association(
            id=uuid4(),
            article_revision_id=article.id,
            target_type="topic",
            target_id="SYNTH-SECTOR-2",
            method="ai",
            status="unreviewed",
            created_at=article.available_at,
        )
        evidence = event_evidence(value=f"synthetic-c1:event:{index}")
        freeze(f"market-{index}.json", market)
        freeze(f"topic-{index}.json", topics)
        freeze(f"article-{index}.json", article)
        freeze(f"association-{index}.json", link)
        freeze(f"event-{index}.json", evidence)
        manifest["publications"].append(
            {
                "index": index,
                "sector_id": sector,
                "security_id": security,
                "article_id": str(article.article_id),
                "article_revision_id": str(article.id),
                "association_id": str(link.id),
                "source_version": version,
                "available_at": cutoff.isoformat(),
            }
        )
    with (output / "inputs.json").open("xb") as handle:
        handle.write(encoded(manifest))
    return manifest


def fixture_database():
    from sqlalchemy.engine import make_url

    from dragon_radar.storage.database import Database

    try:
        url = make_url(os.environ.get("DRAGON_DATABASE_URL", ""))
        valid = (
            url.drivername in ("postgresql", "postgresql+psycopg")
            and url.host == "db"
            and url.port == 5432
            and url.database == "dragon_test"
            and url.username == "dragon_radar"
        )
    except Exception:
        valid = False
    if not valid:
        raise ValueError("C1 requires isolated dragon_test at db")
    return Database()


def news_snapshot(database, inputs, index, cutoff):
    from dragon_radar.news.models import (
        NewsArticleRevision,
        NewsAssociation,
        NewsEventEvidence,
    )
    from dragon_radar.news.service import NewsService
    from dragon_radar.storage.repository import Repository

    service = NewsService(database, Repository(database))
    article = NewsArticleRevision.model_validate_json(
        (inputs / f"article-{index}.json").read_bytes()
    )
    link = NewsAssociation.model_validate_json(
        (inputs / f"association-{index}.json").read_bytes()
    )
    event = NewsEventEvidence.model_validate_json(
        (inputs / f"event-{index}.json").read_bytes()
    )
    service.ingest_article(
        article,
        key=f"synthetic-c1-article-{index}",
        event_evidence=event,
        associations=(link,),
    )
    as_of = (
        max(article.available_at, datetime.fromisoformat(cutoff))
        if cutoff
        else article.available_at
    )
    result = service.create_snapshot(as_of=as_of, key=f"synthetic-c1-news-{index}")
    return result.model_dump(mode="json")


def database_proof(database):
    from sqlalchemy import text

    query = "SELECT current_database() AS database, current_schema() AS schema"
    with database.engine.connect() as connection:
        proof = dict(connection.execute(text(query)).mappings().one())
        proof["migration"] = connection.scalar(
            text("SELECT version_num FROM alembic_version")
        )
    if proof["database"] != "dragon_test" or not proof["schema"]:
        raise ValueError("C1 actual database/schema proof mismatch")
    return proof


def archive(database, publication, index):
    from sqlalchemy import select

    from dragon_radar.publication.tables import publication_results, publications

    with database.engine.connect() as connection:
        root = dict(
            connection.execute(
                select(publications).where(publications.c.id == UUID(publication))
            )
            .mappings()
            .one()
        )
        rows = [
            dict(row)
            for row in connection.execute(
                select(publication_results)
                .where(publication_results.c.publication_id == UUID(publication))
                .order_by(publication_results.c.domain)
            ).mappings()
        ]
    return archive_evidence(root, rows, index)


def archive_evidence(root, rows, index):
    domains = {row["domain"]: row for row in rows}
    sector_id, security_id = f"SYNTH-SECTOR-{index}", f"SYNTH-C1-{index}"
    detail = domains["market"]["payload"]["topic_details"]["sectors"][sector_id]
    articles = domains["news"]["payload"]["articles"]
    article = next(
        item for item in articles if item["title"] == f"Synthetic C1 article {index}"
    )
    materialization_hashes = {
        row["domain"]: hashlib.sha256(encoded(row)).hexdigest() for row in rows
    }
    return {
        "snapshot_id": str(root["id"]),
        "version": root["version"],
        "published_at": root["published_at"].isoformat(),
        "trade_date": root["trade_date"].isoformat(),
        "sector_id": sector_id,
        "security_id": security_id,
        "members": [member["security_id"] for member in detail["members"]],
        "article_id": article["article_id"],
        "source_version": detail["topic_basis"]["source_version"],
        "available_at": article["available_at"],
        "raw_sha256": hashlib.sha256(
            encoded({"publication": root, "results": rows})
        ).hexdigest(),
        "materialization_hashes": materialization_hashes,
        "raw_publication": root,
        "results": rows,
    }


def audit(database, association_id):
    from sqlalchemy import select

    from dragon_radar.news.tables import (
        news_association_reviews,
        news_review_audit_events,
    )

    with database.engine.connect() as connection:
        reviews = [
            dict(row)
            for row in connection.execute(
                select(news_association_reviews)
                .where(
                    news_association_reviews.c.association_id == UUID(association_id)
                )
                .order_by(news_association_reviews.c.revision)
            ).mappings()
        ]
        events = [
            dict(row)
            for row in connection.execute(
                select(news_review_audit_events)
                .where(news_review_audit_events.c.object_id == association_id)
                .order_by(news_review_audit_events.c.occurred_at)
            ).mappings()
        ]
    return {
        "reviews": reviews,
        "audit_events": events,
        "captured_at": datetime.now(UTC).isoformat(),
        "sha256": hashlib.sha256(
            encoded({"reviews": reviews, "audit_events": events})
        ).hexdigest(),
    }


def synthetic_provider_content(manifest):
    entries = [
        item
        for item in manifest["evidence"]
        if item["evidence_position"] == "/input/article/content"
    ]
    if len(entries) != 1:
        raise ValueError("C1 requires exact archived content evidence")
    entry = entries[0]
    content = {
        "facts": [
            {
                "text": "Synthetic archived evidence.",
                **{
                    key: entry[key]
                    for key in ("evidence_id", "evidence_position", "value")
                },
            }
        ],
        "inferences": [],
        "missing_evidence": [],
        "citations": [entry],
    }
    return content, entry


def complete_task(database, task_id):
    from dragon_radar.ai.models import AIProviderResult
    from dragon_radar.ai.repository import AITaskRepository
    from dragon_radar.owner.repository import OwnerRepository
    from dragon_radar.publication.repository import PublicationRepository
    from dragon_radar.storage.repository import Repository

    tasks = AITaskRepository(
        database,
        OwnerRepository(database),
        PublicationRepository(database, Repository(database)),
    )
    lease = tasks.claim("synthetic-c1-fixture", lease_for=timedelta(minutes=5))
    if (
        lease is None
        or str(lease.job.id) != task_id
        or lease.job.task_type != "summarize"
    ):
        raise ValueError("C1 actual UI summarize task is not the claimed task")
    manifest = json.loads(encoded(tasks.load_input(lease)))
    content, entry = synthetic_provider_content(manifest)
    result = tasks.succeed(
        lease,
        AIProviderResult(content=content, token_usage=1, cost=Decimal("0")),
        provider="synthetic-fixture",
        model_version="synthetic-c1-v1",
        prompt_version="synthetic-c1-v1",
        latency_ms=1,
        max_tokens=10,
        max_cost=Decimal("1"),
    )
    if (
        result.status != "succeeded"
        or result.verification_status != "passed"
        or result.content_draft is None
    ):
        raise ValueError("C1 provider draft unavailable")
    return {
        "task_id": str(result.id),
        "snapshot_id": str(result.snapshot_id),
        "article_id": manifest["input"]["article"]["article_id"],
        "draft_revision": result.content_draft.revision,
        "status": result.status,
        "verification_status": result.verification_status,
        "citation": entry,
        "manifest": manifest,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation",
        choices=(
            "generate",
            "news",
            "archive",
            "audit",
            "complete-task",
            "database-proof",
        ),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--index", type=int, choices=(1, 2), default=1)
    parser.add_argument("--cutoff")
    parser.add_argument("--publication")
    parser.add_argument("--association")
    parser.add_argument("--task")
    args = parser.parse_args()
    if args.operation == "generate":
        result = generate(args.output)
    else:
        database = fixture_database()
        try:
            if args.operation == "database-proof":
                result = database_proof(database)
            elif args.operation == "news":
                result = news_snapshot(database, args.inputs, args.index, args.cutoff)
            elif args.operation == "archive":
                result = archive(database, args.publication, args.index)
            elif args.operation == "audit":
                result = audit(database, args.association)
            else:
                result = complete_task(database, args.task)
        finally:
            database.close()
    print(encoded(result).decode())


if __name__ == "__main__":
    main()
