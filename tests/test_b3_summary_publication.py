"""Promoted-only summary selection and Unicode preview."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from dragon_radar.storage.repository import Conflict, Forbidden


def artifact_data():
    return {
        "schema": "dragon-radar.news-summary/v1",
        "summary_id": str(uuid4()),
        "summary_revision": 1,
        "article_id": str(uuid4()),
        "article_revision_id": str(uuid4()),
        "article_revision": 1,
        "source_id": "licensed",
        "source_version": "v1",
        "text": "\U0001f600" * 101,
        "provenance": "ai",
        "task_type": "summarize",
        "task_snapshot_id": str(uuid4()),
        "draft_revision": 1,
        "verification_status": "passed",
        "human_review_status": "accepted",
        "input_sha256": "a" * 64,
        "output_sha256": "b" * 64,
        "model_version": "v1",
        "prompt_version": "v1",
        "retrieval_version": "v1",
        "verified_at": datetime(2026, 11, 24, tzinfo=UTC).isoformat(),
        "reviewed_at": datetime(2026, 11, 24, tzinfo=UTC).isoformat(),
        "available_at": datetime(2026, 11, 24, tzinfo=UTC).isoformat(),
    }


def test_preview_code_points():
    from dragon_radar.news.summary_publication import (
        NewsSummaryArtifact,
        summary_preview,
    )

    artifact = NewsSummaryArtifact.model_validate(artifact_data())
    preview = summary_preview(artifact)
    assert preview["text"] == "\U0001f600" * 100
    assert preview["status"] == "ready"
    assert preview["truncated"] is True


def test_closed_summary_contract():
    from dragon_radar.news.summary_publication import NewsSummaryArtifact

    data = artifact_data()
    for key, value in [
        ("draft_revision", True),
        ("verification_status", "failed"),
        ("text", " "),
        ("owner_id", "secret"),
    ]:
        with pytest.raises(ValidationError):
            NewsSummaryArtifact.model_validate({**data, key: value})


def test_selection_contains_no_private_task_fields():
    from dragon_radar.news.summary_publication import (
        SummarySelection,
        selection_identity,
    )

    data = artifact_data()
    selection = {
        "summary_id": data["summary_id"],
        "summary_revision": 1,
        "article_id": data["article_id"],
        "article_revision_id": data["article_revision_id"],
        "article_revision": 1,
    }
    assert selection_identity([selection]) == selection_identity([selection])
    with pytest.raises(ValidationError):
        SummarySelection.model_validate({**selection, "task_id": str(uuid4())})


def test_legacy_list_projects_unavailable_never_unverified_text():
    from dragon_radar.http.routes.news import _list_article

    article = {
        "article_id": str(uuid4()),
        "revision_id": str(uuid4()),
        "title": "Title",
        "source": {"id": "licensed", "version": "v1"},
        "published_at": "2026-11-24T00:00:00Z",
        "received_at": "2026-11-24T01:00:00Z",
        "available_at": "2026-11-24T01:00:00Z",
        "type": "news",
        "summary": "Supplier text",
        "ai_summary": "Unverified legacy",
        "event_id": str(uuid4()),
        "revision": 1,
    }
    result = _list_article(article)
    assert result["received_at"] == article["received_at"]
    assert result["revision_id"] == article["revision_id"]
    assert result["ai_preview"]["status"] == "unavailable"
    assert result["ai_preview"]["text"] is None
    assert result["summary"] == "Supplier text"


def selected_materializer(*, registry=None):
    from types import SimpleNamespace

    from dragon_radar.news.summary_publication import PromotedSummaryMaterializer
    from dragon_radar.publication.models import DomainMaterialization

    data = artifact_data()
    article = {
        "article_id": data["article_id"],
        "revision_id": data["article_revision_id"],
        "revision": data["article_revision"],
        "source": {"id": data["source_id"], "version": data["source_version"]},
        "summary": "Licensed supplier summary",
    }
    original = DomainMaterialization(
        payload={"schema_version": 2, "articles": [article]},
        coverage={},
        quality_flags=[],
    )
    service = SimpleNamespace(
        materialize_context=lambda *_args, **_kwargs: original,
        dependencies=lambda _context: (),
    )
    registry = registry or SimpleNamespace(summary=lambda *_args: data)
    selection = {
        key: data[key]
        for key in (
            "summary_id",
            "summary_revision",
            "article_id",
            "article_revision_id",
            "article_revision",
        )
    }
    return PromotedSummaryMaterializer(service, registry, [selection]), original, data


def test_v3_materializes_selected_registry_only_preserving_v2():
    materializer, original, data = selected_materializer()
    result = materializer.materialize_context({}, connection=object())
    value = result.model_dump(mode="json")["payload"]
    assert value["schema_version"] == 3
    assert value["articles"][0]["summary_artifact"] == data
    assert original.model_dump(mode="json")["payload"]["schema_version"] == 2
    assert (
        "summary_artifact"
        not in original.model_dump(mode="json")["payload"]["articles"][0]
    )


@pytest.mark.parametrize("case", ["missing", "revision_id", "revision"])
def test_dependency_selection_rejects_absent_or_mismatched_revision(case):
    materializer, _original, data = selected_materializer()
    row = {
        "article_id": data["article_id"],
        "article_revision_id": data["article_revision_id"],
        "revision": data["article_revision"],
        "source_id": data["source_id"],
    }
    if case == "revision_id":
        row["article_revision_id"] = str(uuid4())
    if case == "revision":
        row["revision"] = 2
    with pytest.raises(Conflict):
        materializer.dependencies({"rows": [] if case == "missing" else [row]})


def test_withdrawal_clears_derived_text_not_supplier_metadata():
    from contextlib import nullcontext
    from types import SimpleNamespace

    from dragon_radar.http.routes.news import _authorized_summary

    materializer, original, _data = selected_materializer()
    article = materializer.materialize_context({}, connection=object()).model_dump(
        mode="json"
    )["payload"]["articles"][0]

    def denied(*_args):
        raise Forbidden("withdrawn")

    registry = SimpleNamespace(
        database=SimpleNamespace(
            engine=SimpleNamespace(begin=lambda: nullcontext(object()))
        ),
        summary=denied,
        public=True,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(public_daily_ai_repository=registry))
    )
    preview, text = _authorized_summary(request, article)
    assert preview["reason"] == "summary_sharing_denied"
    assert preview["text"] is None and text is None
    assert (
        original.model_dump(mode="json")["payload"]["articles"][0]["summary"]
        == "Licensed supplier summary"
    )


def test_corrupt_summary_cannot_masquerade_as_unavailable_registry():
    from types import SimpleNamespace

    from dragon_radar.http.errors import DataUnavailable
    from dragon_radar.http.routes.news import _authorized_summary

    materializer, _original, _data = selected_materializer()
    article = materializer.materialize_context({}, connection=object()).model_dump(
        mode="json"
    )["payload"]["articles"][0]
    article["summary_artifact"]["article_revision"] = 2
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    with pytest.raises(DataUnavailable):
        _authorized_summary(request, article)


@pytest.mark.parametrize(
    "failure", [ValueError("corrupt registry"), Conflict("corrupt binding")]
)
def test_corrupt_registry_counterpart_maps_to_data_unavailable(failure):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from dragon_radar.http.errors import DataUnavailable
    from dragon_radar.http.routes.news import _authorized_summary

    materializer, _original, _data = selected_materializer()
    article = materializer.materialize_context({}, connection=object()).model_dump(
        mode="json"
    )["payload"]["articles"][0]

    def corrupt(*_args):
        raise failure

    registry = SimpleNamespace(
        database=SimpleNamespace(
            engine=SimpleNamespace(begin=lambda: nullcontext(object()))
        ),
        summary=corrupt,
        public=True,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(public_daily_ai_repository=registry))
    )
    with pytest.raises(DataUnavailable):
        _authorized_summary(request, article)
