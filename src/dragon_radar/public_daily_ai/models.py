"""Closed commands and independently checked promotion eligibility."""

import hashlib
from datetime import UTC, date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from dragon_radar.ai.models import canonical_object
from dragon_radar.ai.verification import verify_output
from dragon_radar.storage.repository import Conflict

Positive = Annotated[int, Field(strict=True, ge=1)]
Nonnegative = Annotated[int, Field(strict=True, ge=0)]
Reference = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$",
    ),
]
SHA256 = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]


def digest(value: dict) -> str:
    return hashlib.sha256(canonical_object(value).encode("ascii")).hexdigest()


def utc_time(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("AI publication clocks require timezone-aware timestamps")
    return value.astimezone(UTC)


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AIOutputEntitlement(Closed):
    reference: Reference
    provider: Reference
    model_version: Reference
    starts_at: datetime
    expires_at: datetime
    storage: StrictBool
    display: StrictBool
    sharing: StrictBool
    audience: Literal["public", "local"]
    policy_reference: Reference

    @field_validator("starts_at", "expires_at")
    @classmethod
    def normalize_time(cls, value):
        return utc_time(value)

    @model_validator(mode="after")
    def bounded(self):
        if any(
            t.tzinfo is None or t.utcoffset() is None
            for t in (self.starts_at, self.expires_at)
        ):
            raise ValueError("Entitlement timestamps require timezones")
        if self.expires_at <= self.starts_at:
            raise ValueError("Entitlement interval is empty")
        return self

    def allows(self, now, *, provider, model_version, public=True):
        return bool(
            self.starts_at <= now < self.expires_at
            and self.storage
            and self.display
            and (self.sharing if public else True)
            and (self.audience == "public" if public else True)
            and self.provider == provider
            and self.model_version == model_version
        )


class PromoteSummary(Closed):
    revision: Positive
    expected_review_id: UUID
    expected_content_hash: SHA256
    snapshot_id: UUID
    date: date
    idempotency_key: Reference
    publication_consent_reference: Reference
    output_entitlement_reference: Reference
    attestation_reference: Reference

    @field_validator("date", mode="before")
    @classmethod
    def exact_date(cls, value):
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if (
            not isinstance(value, str)
            or len(value) != 10
            or date.fromisoformat(value).isoformat() != value
        ):
            raise ValueError("Date requires ISO YYYY-MM-DD")
        return value


class PublishDaily(PromoteSummary):
    expected_archive_version: Nonnegative


class RevokeArtifact(Closed):
    expected_archive_version: Positive
    idempotency_key: Reference
    reason_code: Literal[
        "withdrawn_by_owner", "rights_invalidated", "verification_invalidated"
    ]


class RevokeSummary(Closed):
    expected_summary_revision: Positive
    idempotency_key: Reference
    reason_code: Literal[
        "withdrawn_by_owner", "rights_invalidated", "verification_invalidated"
    ]


def validate_candidate(*, task, draft, review, ancestry, expected_hash):
    if (task["status"], task["verification_status"], task["human_review_status"]) != (
        "succeeded",
        "passed",
        "accepted",
    ):
        raise Conflict("task_eligibility")
    revision = draft["revision"]
    if task["selected_revision"] != revision or draft["task_id"] != task["id"]:
        raise Conflict("selected_revision")
    if (
        review["task_id"] != task["id"]
        or review["owner_id"] != task["owner_id"]
        or review["resulting_revision"] != revision
        or (
            review["decision"],
            review["human_review_status"],
            review["verification_status"],
        )
        != ("accept", "accepted", "passed")
    ):
        raise Conflict("review_binding")
    manifest = task["input_manifest"]
    selected = manifest.get("input", {})
    if task["task_type"] == "summarize":
        valid = set(selected) == {"article"} and isinstance(selected["article"], dict)
    elif task["task_type"] == "daily_review":
        valid = (
            set(selected) == {"date", "notes_context", "market_overview"}
            and selected["notes_context"] == ""
        )
    else:
        valid = False
    if not valid:
        raise Conflict("private_input")
    if manifest.get("task_type") != task["task_type"] or manifest["publication"][
        "snapshot_id"
    ] != str(task["snapshot_id"]):
        raise Conflict("manifest_binding")
    if digest(draft["content"]) != expected_hash:
        raise Conflict("content_hash")
    if verify_output(manifest, draft["content"]).status != "passed":
        raise Conflict("independent_verification")
    if not ancestry or ancestry[0]["revision"] != revision:
        raise Conflict("provider_ancestry")
    if (
        any(item["source"] != "human_edit" for item in ancestry[:-1])
        or ancestry[-1]["source"] != "provider"
    ):
        raise Conflict("provider_ancestry")
    if any(item["task_id"] != task["id"] for item in ancestry):
        raise Conflict("provider_ancestry")
    if not all(
        ancestry[-1].get(key) for key in ("provider", "model_version", "prompt_version")
    ):
        raise Conflict("provider_metadata")
    return "ai" if draft["source"] == "provider" else "ai_human_edited"


def safe_payload(manifest, output):
    """Project verified scalar evidence to public PUB locators, never private URLs."""
    if verify_output(manifest, output).status != "passed":
        raise Conflict("independent_verification")
    result = {key: output[key] for key in ("facts", "inferences", "missing_evidence")}
    result["citations"] = []
    for citation in output["citations"]:
        position = citation["evidence_position"]
        if not position.startswith(("/input/article/", "/input/market_overview/")):
            raise Conflict("private_citation")
        fragment = citation["value"]
        if not isinstance(fragment, (str, int, float, bool)) or (
            isinstance(fragment, str)
            and any(part in fragment for part in ("/api/ai", "/api/owner"))
        ):
            raise Conflict("private_citation")
        result["citations"].append(
            {
                "evidence_id": citation["evidence_id"],
                "source_identity": citation["source_identity"],
                "locator": {
                    "snapshot_id": manifest["publication"]["snapshot_id"],
                    "position": position,
                },
                "fragment": fragment,
            }
        )
    canonical_object(result)
    validate_safe_payload(result, manifest["publication"]["snapshot_id"])
    return result


def validate_safe_payload(value, snapshot_id):
    """Validate persisted public bytes without ever loading a private manifest."""
    fields = {
        "facts": {
            "text",
            "evidence_id",
            "evidence_position",
            "value",
            "unit",
            "precision",
            "recomputation",
        },
        "inferences": {"text", "claim", "confidence", "evidence_ids"},
        "missing_evidence": {"text", "reason"},
        "citations": {"evidence_id", "source_identity", "locator", "fragment"},
    }
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("Invalid public AI payload")
    for key, allowed in fields.items():
        if not isinstance(value[key], list) or len(value[key]) > 1000:
            raise ValueError("Invalid public AI payload")
        for item in value[key]:
            if not isinstance(item, dict) or set(item) - allowed:
                raise ValueError("Invalid public AI payload")
            if key != "citations" and (
                not isinstance(item.get("text"), str) or not item["text"].strip()
            ):
                raise ValueError("Invalid public AI text")
            if key == "citations":
                locator = item.get("locator")
                if (
                    set(item) != allowed
                    or not isinstance(locator, dict)
                    or set(locator) != {"snapshot_id", "position"}
                ):
                    raise ValueError("Invalid public citation")
                if (
                    locator["snapshot_id"] != str(snapshot_id)
                    or not isinstance(locator["position"], str)
                    or not locator["position"].startswith(
                        ("/input/article/", "/input/market_overview/")
                    )
                ):
                    raise ValueError("Invalid public citation binding")
    serialized = canonical_object(value)
    if any(
        path in serialized.lower()
        for path in ("/api/ai", "/api/owner", "notes_context", "provider_credentials")
    ):
        raise ValueError("Private content cannot be projected publicly")
