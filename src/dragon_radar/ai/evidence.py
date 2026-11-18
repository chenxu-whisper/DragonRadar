"""Read-only citation resolution against freshly authorized publication facts."""

import re
from uuid import UUID

from sqlalchemy import select

from dragon_radar.ai.models import AISubmission
from dragon_radar.ai.tables import ai_task_drafts
from dragon_radar.ai.verification import _equal_json, _pointer, build_manifest_evidence
from dragon_radar.http.errors import DataUnavailable
from dragon_radar.storage.repository import Conflict, Forbidden, NotFound


def read_evidence(service, token, task_id, revision, snapshot_id, evidence_id):
    tasks = service.tasks
    with tasks.database.engine.begin() as connection:
        owner = tasks._authorize(connection, token)
        row = tasks._task_row(connection, task_id, owner_id=owner.owner_id)
        if row["snapshot_id"] != snapshot_id:
            raise Conflict("Citation publication mismatch")
        draft = (
            connection.execute(
                select(ai_task_drafts).where(
                    ai_task_drafts.c.task_id == row["id"],
                    ai_task_drafts.c.revision == revision,
                )
            )
            .mappings()
            .first()
        )
        if draft is None:
            raise NotFound("Draft not found")
        if row["human_review_status"] == "rejected":
            raise Forbidden("Draft was rejected")
        citations = draft["content"].get("citations", [])
        cited = [
            item
            for item in citations
            if isinstance(item, dict) and item.get("evidence_id") == evidence_id
        ]
        if len(cited) != 1:
            raise NotFound("Citation not found")
        manifest = row["input_manifest"]
        entries = [
            item
            for item in manifest.get("evidence", [])
            if isinstance(item, dict) and item.get("evidence_id") == evidence_id
        ]
        if len(entries) != 1 or not _equal_json(entries[0], cited[0]):
            raise DataUnavailable
        entry = entries[0]
        # Empty AI domains intentionally require display, not processing permission.
        snapshot = service.publications.get_ai_snapshot(
            connection, snapshot_id, ai_domains=()
        )
        if snapshot is None:
            raise NotFound("Publication not found")
        publication = service._publication_manifest(snapshot)
        if (
            row["as_of"] != snapshot.published_at
            or row["version"] != publication["version"]
            or not _equal_json(manifest.get("publication"), publication)
            or manifest.get("task_type") != row["task_type"]
        ):
            raise DataUnavailable
        response = {
            "owner_id": str(owner.owner_id),
            "task_id": str(row["id"]),
            "revision": revision,
            **{key: publication[key] for key in ("snapshot_id", "as_of", "version")},
            **entry,
            "status": "ready",
            "reason": None,
            "locator": None,
        }
        selected = manifest.get("input", {})
        task_type = row["task_type"]
        articles = (
            [selected.get("article")]
            if task_type == "summarize"
            else selected.get("articles", [])
            if task_type == "topic_suggest"
            else []
        )
        if any(
            not isinstance(article, dict)
            or not article.get("article_id")
            or not article.get("revision_id")
            for article in articles
        ):
            return {
                **response,
                "status": "unavailable",
                "reason": "legacy_locator_unavailable",
                "value": None,
            }
        if task_type == "summarize":
            task_input = {"article_id": selected["article"]["article_id"]}
        elif task_type == "topic_suggest":
            task_input = {"date": selected.get("date")}
        elif task_type == "explain_stock":
            task_input = {
                "date": selected.get("date"),
                "security_id": selected.get("stock", {}).get("security_id"),
            }
        else:
            task_input = {
                "date": selected.get("date"),
                "notes_context": selected.get("notes_context"),
            }
        try:
            expected = service._task_manifest(
                snapshot,
                AISubmission(
                    task_type=task_type,
                    snapshot_id=snapshot_id,
                    as_of=row["as_of"],
                    version=row["version"],
                    task_input=task_input,
                ),
            )
        except (KeyError, TypeError, ValueError, NotFound, Conflict):
            raise DataUnavailable from None
        if not _equal_json(selected, expected["input"]):
            raise DataUnavailable
        actual_entries = build_manifest_evidence(
            task_type, publication, expected["input"]
        )
        actual = [item for item in actual_entries if item["evidence_id"] == evidence_id]
        if len(actual) != 1 or not _equal_json(actual[0], entry):
            raise DataUnavailable
        position = entry["evidence_position"]
        match = re.fullmatch(r"/input/(article|articles/([0-9]+))/(.+)", position)
        if match:
            article = (
                selected["article"]
                if match[1] == "article"
                else selected["articles"][int(match[2])]
            )
            field = match[3]
            found, value = _pointer(article, "/" + field)
            if not found or not _equal_json(value, entry["value"]):
                raise DataUnavailable
            # Identity comes from validated article data, never from source prose.
            UUID(article["article_id"])
            UUID(article["revision_id"])
            parts = field.split("/")
            response["locator"] = {
                "kind": "news_article_field",
                "article_id": article["article_id"],
                "article_revision_id": article["revision_id"],
                "article_revision": article["revision"],
                "field": "/".join(parts[:-1]) if parts[-1].isdigit() else field,
                "index": int(parts[-1]) if parts[-1].isdigit() else None,
            }
        else:
            response["locator"] = {
                "kind": "publication_field",
                "field": position,
                "index": None,
            }
        return response
