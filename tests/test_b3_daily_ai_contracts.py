"""Closed publication commands and independent eligibility checks."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from dragon_radar.ai.verification import build_manifest_evidence
from dragon_radar.storage.repository import Conflict


def candidate(kind="summarize"):
    task_id, owner_id, snapshot_id, review_id = [uuid4() for _ in range(4)]
    publication = {
        "snapshot_id": str(snapshot_id),
        "as_of": "2026-11-24T08:00:00+00:00",
        "version": "v1",
        "trade_date": "2026-11-24",
    }
    selected = (
        {"article": {"title": "Licensed article"}}
        if kind == "summarize"
        else {
            "date": "2026-11-24",
            "notes_context": "",
            "market_overview": {},
        }
    )
    manifest = {
        "task_type": kind,
        "publication": publication,
        "input": selected,
        "evidence": build_manifest_evidence(kind, publication, selected),
    }
    output = {"facts": [], "inferences": [], "missing_evidence": [], "citations": []}
    task = {
        "id": task_id,
        "owner_id": owner_id,
        "task_type": kind,
        "status": "succeeded",
        "verification_status": "passed",
        "human_review_status": "accepted",
        "selected_revision": 1,
        "snapshot_id": snapshot_id,
        "input_manifest": manifest,
    }
    draft = {
        "task_id": task_id,
        "revision": 1,
        "source": "provider",
        "content": output,
        "provider": "test",
        "model_version": "model-v1",
        "prompt_version": "prompt-v1",
    }
    review = {
        "id": review_id,
        "task_id": task_id,
        "owner_id": owner_id,
        "decision": "accept",
        "resulting_revision": 1,
        "verification_status": "passed",
        "human_review_status": "accepted",
    }
    return {"task": task, "draft": draft, "review": review, "ancestry": [draft]}


def test_module_exists():
    import importlib.util

    assert importlib.util.find_spec("dragon_radar.public_daily_ai") is not None


def test_article_only_input_and_extra_context_rejected():
    from dragon_radar.public_daily_ai.models import digest, validate_candidate

    value = candidate()
    assert (
        validate_candidate(**value, expected_hash=digest(value["draft"]["content"]))
        == "ai"
    )
    value["task"]["input_manifest"]["input"]["notes_context"] = ""
    with pytest.raises(Conflict, match="private_input"):
        validate_candidate(**value, expected_hash=digest(value["draft"]["content"]))


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "running"),
        ("verification_status", "failed"),
        ("human_review_status", "edited"),
        ("selected_revision", 2),
    ],
)
def test_independent_task_checks(field, value):
    from dragon_radar.public_daily_ai.models import digest, validate_candidate

    item = candidate()
    item["task"][field] = value
    with pytest.raises(Conflict):
        validate_candidate(**item, expected_hash=digest(item["draft"]["content"]))


def test_independent_verification_and_hash():
    from dragon_radar.public_daily_ai.models import digest, validate_candidate

    item = candidate()
    with pytest.raises(Conflict, match="content_hash"):
        validate_candidate(**item, expected_hash="0" * 64)
    item["draft"]["content"]["owner_id"] = "secret"
    with pytest.raises(Conflict, match="verification"):
        validate_candidate(**item, expected_hash=digest(item["draft"]["content"]))


def test_private_daily_input_never_promotable():
    from dragon_radar.public_daily_ai.models import digest, validate_candidate

    item = candidate("daily_review")
    item["task"]["input_manifest"]["input"]["notes_context"] = "private"
    with pytest.raises(Conflict, match="private_input"):
        validate_candidate(**item, expected_hash=digest(item["draft"]["content"]))


def test_entitlement_is_closed_bounded_and_public():
    from dragon_radar.public_daily_ai.models import AIOutputEntitlement

    now = datetime(2026, 11, 24, tzinfo=UTC)
    data = dict(
        reference="license-v1",
        provider="test",
        model_version="model-v1",
        starts_at=now,
        expires_at=now + timedelta(days=1),
        storage=True,
        display=True,
        sharing=True,
        audience="public",
        policy_reference="contract-v1",
    )
    entitlement = AIOutputEntitlement(**data)
    assert entitlement.allows(now, provider="test", model_version="model-v1")
    assert not entitlement.allows(
        now + timedelta(days=1), provider="test", model_version="model-v1"
    )
    for key, val in [("audience", "local"), ("sharing", False), ("provider", "other")]:
        changed = deepcopy(data)
        changed[key] = val
        assert not AIOutputEntitlement(**changed).allows(
            now, provider="test", model_version="model-v1"
        )
    with pytest.raises(ValidationError):
        AIOutputEntitlement(**data, owner_id="private")


def test_publication_commands_reject_bool_revision_and_non_iso_date():
    from dragon_radar.public_daily_ai.models import PublishDaily

    data = {
        "revision": 1,
        "expected_review_id": str(uuid4()),
        "expected_content_hash": "a" * 64,
        "snapshot_id": str(uuid4()),
        "date": "2026-11-24",
        "expected_archive_version": 0,
        "idempotency_key": "publish",
        "publication_consent_reference": "consent",
        "output_entitlement_reference": "output",
        "attestation_reference": "attestation",
    }
    for key, value in [
        ("revision", True),
        ("expected_archive_version", False),
        ("expected_archive_version", -1),
        ("date", 0),
        ("owner_id", "private"),
    ]:
        with pytest.raises(ValidationError):
            PublishDaily.model_validate({**data, key: value})


def test_public_projection_rejects_private_citation_and_prose():
    from dragon_radar.public_daily_ai.models import safe_payload

    item = candidate()
    output = {
        "facts": [],
        "inferences": [],
        "citations": [],
        "missing_evidence": [{"text": "/api/ai/task/private"}],
    }
    with pytest.raises(ValueError):
        safe_payload(item["task"]["input_manifest"], output)


def test_template_or_broken_provider_ancestry_rejected():
    from dragon_radar.public_daily_ai.models import digest, validate_candidate

    for source in ("deterministic_template", "human_edit"):
        item = candidate()
        item["draft"]["source"] = source
        with pytest.raises(Conflict, match="provider_ancestry"):
            validate_candidate(**item, expected_hash=digest(item["draft"]["content"]))


def test_human_edit_requires_fresh_exact_acceptance_and_provider_lineage():
    from dragon_radar.public_daily_ai.models import digest, validate_candidate

    item = candidate()
    provider = deepcopy(item["draft"])
    item["draft"] = {**item["draft"], "revision": 2, "source": "human_edit"}
    item["task"]["selected_revision"] = 2
    item["ancestry"] = [item["draft"], provider]
    expected_hash = digest(item["draft"]["content"])
    with pytest.raises(Conflict, match="review_binding"):
        validate_candidate(**item, expected_hash=expected_hash)
    item["review"]["resulting_revision"] = 2
    assert validate_candidate(**item, expected_hash=expected_hash) == "ai_human_edited"
    item["ancestry"][-1]["task_id"] = uuid4()
    with pytest.raises(Conflict, match="provider_ancestry"):
        validate_candidate(**item, expected_hash=expected_hash)


@pytest.mark.parametrize("field", ["task_id", "owner_id"])
def test_review_identity_must_match_owner_and_task(field):
    from dragon_radar.public_daily_ai.models import digest, validate_candidate

    item = candidate()
    item["review"][field] = uuid4()
    with pytest.raises(Conflict, match="review_binding"):
        validate_candidate(**item, expected_hash=digest(item["draft"]["content"]))
