"""Portable export of the old local owner's user-authored review data."""

from datetime import UTC, datetime

from sqlalchemy import select

from dragon_radar.owner.models import owner_principals
from dragon_radar.review.tables import (
    review_note_revisions,
    review_notes,
    review_verifications,
    watch_entries,
)

FORMAT_VERSION = 1


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Export timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _identifier(value) -> str | None:
    return str(value) if value is not None else None


def export_local_review(
    database,
    *,
    local_profile_id: str,
    exported_at: datetime | None = None,
) -> dict[str, object]:
    """Return an allowlisted backup for the installation's explicit local owner."""
    if not isinstance(local_profile_id, str) or not local_profile_id.strip():
        raise ValueError("Local profile ID is required")
    when = exported_at or datetime.now(UTC)
    with database.engine.connect() as connection:
        owner_id = connection.scalar(
            select(owner_principals.c.id).where(owner_principals.c.local_key == "local")
        )
        if owner_id is None:
            raise ValueError("Local owner is not initialized")
        notes = (
            connection.execute(
                select(review_notes)
                .where(review_notes.c.owner_id == owner_id)
                .order_by(review_notes.c.created_at, review_notes.c.id)
            )
            .mappings()
            .all()
        )
        revisions = (
            connection.execute(
                select(review_note_revisions)
                .where(review_note_revisions.c.owner_id == owner_id)
                .order_by(
                    review_note_revisions.c.note_id,
                    review_note_revisions.c.version,
                    review_note_revisions.c.id,
                )
            )
            .mappings()
            .all()
        )
        verifications = (
            connection.execute(
                select(review_verifications)
                .where(review_verifications.c.owner_id == owner_id)
                .order_by(
                    review_verifications.c.note_id,
                    review_verifications.c.created_at,
                    review_verifications.c.id,
                )
            )
            .mappings()
            .all()
        )
        watchlist = (
            connection.execute(
                select(watch_entries)
                .where(watch_entries.c.owner_id == owner_id)
                .order_by(watch_entries.c.added_at, watch_entries.c.id)
            )
            .mappings()
            .all()
        )

    note_rows = [
        {
            "note_id": str(row["id"]),
            "snapshot_id": str(row["snapshot_id"]),
            "security_id": row["security_id"],
            "current_revision": row["version"],
            "created_at": _timestamp(row["created_at"]),
            "updated_at": _timestamp(row["updated_at"]),
        }
        for row in notes
    ]
    revision_rows = []
    for row in revisions:
        content = row["content"]
        revision_rows.append(
            {
                "revision_id": str(row["id"]),
                "note_id": str(row["note_id"]),
                "revision": row["version"],
                "state": row["state"],
                "content": {
                    "title": content["title"],
                    "content": "",
                    "judgment": content.get("judgment", ""),
                    "evidence": content.get("evidence", ""),
                    "invalid_condition": content.get("invalidation", ""),
                    "verify_date": content.get("verification_date"),
                },
                "saved_at": _timestamp(row["saved_at"]),
            }
        )
    verification_rows = [
        {
            "verification_id": str(row["id"]),
            "note_id": str(row["note_id"]),
            "revision_id": str(row["revision_id"]),
            "snapshot_id": _identifier(row["snapshot_id"]),
            "outcome": row["outcome"],
            "evidence": row["evidence"],
            "supersedes": _identifier(row["supersedes"]),
            "created_at": _timestamp(row["created_at"]),
        }
        for row in verifications
    ]
    watch_rows = [
        {
            "watch_id": str(row["id"]),
            "security_id": row["security_id"],
            "snapshot_id": str(row["snapshot_id"]),
            "active": row["active"],
            "revision": row["version"],
            "added_at": _timestamp(row["added_at"]),
            "updated_at": _timestamp(row["updated_at"]),
        }
        for row in watchlist
    ]
    return {
        "format_version": FORMAT_VERSION,
        "exported_at": _timestamp(when),
        "profile_mapping": {
            "owner_id": str(owner_id),
            "local_profile_id": local_profile_id,
        },
        "counts": {
            "notes": len(note_rows),
            "revisions": len(revision_rows),
            "verifications": len(verification_rows),
            "watchlist": len(watch_rows),
        },
        "notes": note_rows,
        "revisions": revision_rows,
        "verifications": verification_rows,
        "watchlist": watch_rows,
    }
