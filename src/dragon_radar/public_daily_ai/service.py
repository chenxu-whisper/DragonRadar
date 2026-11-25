"""Public envelopes and PUB-bound, unified-dependency-watermarked discovery."""

import base64
import json
from datetime import date
from uuid import UUID

from dragon_radar.http.contracts import snapshot_envelope
from dragon_radar.public_daily_ai.models import Closed, Nonnegative, Positive


class Cursor(Closed):
    snapshot_id: UUID
    date: date
    watermark: Nonnegative
    last_version: Positive
    last_id: UUID


def encode_cursor(snapshot_id, trade_date, watermark, version, archive_id):
    value = Cursor(
        snapshot_id=snapshot_id,
        date=trade_date,
        watermark=watermark,
        last_version=version,
        last_id=archive_id,
    )
    return (
        base64.urlsafe_b64encode(value.model_dump_json().encode()).decode().rstrip("=")
    )


def decode_cursor(value, snapshot_id, trade_date):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise ValueError("Invalid cursor")
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
        cursor = Cursor.model_validate(json.loads(decoded))
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid cursor") from exc
    if cursor.snapshot_id != UUID(str(snapshot_id)) or cursor.date != trade_date:
        raise ValueError("Cursor binding mismatch")
    if (
        encode_cursor(
            cursor.snapshot_id,
            cursor.date,
            cursor.watermark,
            cursor.last_version,
            cursor.last_id,
        )
        != value
    ):
        raise ValueError("Noncanonical cursor")
    return cursor.model_dump()


def archive_metadata(row):
    return {
        "archive_id": str(row["id"]),
        "archive_version": row["archive_version"],
        "archive_published_at": row["published_at"].isoformat(),
        "publication_sequence": row["publication_sequence"],
        "content_hash": row["content_hash"],
        "attestation_reference": row["attestation_reference"],
    }


class PublicDailyAIService:
    def __init__(self, repository):
        self.repository = repository

    def get(self, snapshot_id, trade_date, *, archive_id=None):
        snapshot, row = self.repository.get(
            snapshot_id, trade_date, archive_id=archive_id
        )
        data = (
            None
            if row is None
            else {
                **archive_metadata(row),
                "schema": "dragon-radar.public-daily-ai/v1",
                "payload": row["safe_payload"],
            }
        )
        response = snapshot_envelope(snapshot, data)
        response["base_published_at"] = snapshot.published_at
        response["archive_published_at"] = row["published_at"] if row else None
        response["archive_version"] = row["archive_version"] if row else None
        if row is None:
            response["quality_flags"] = sorted(
                set(response["quality_flags"]) | {"reviewed_daily_ai_unavailable"}
            )
        return response

    def discover(self, snapshot_id, trade_date, *, limit=50, cursor=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Discovery limit invalid")
        selection = (
            decode_cursor(cursor, snapshot_id, trade_date)
            if cursor is not None
            else None
        )
        snapshot, watermark, rows = self.repository.discover(
            snapshot_id,
            trade_date,
            watermark=selection["watermark"] if selection else None,
        )
        total = len(rows)
        if selection:
            rows = [
                row
                for row in rows
                if (
                    -row["archive_version"],
                    row["id"],
                )
                > (-selection["last_version"], selection["last_id"])
            ]
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            tail = page[-1]
            next_cursor = encode_cursor(
                snapshot_id, trade_date, watermark, tail["archive_version"], tail["id"]
            )
        return {
            "snapshot_id": str(snapshot.id),
            "trade_date": trade_date,
            "base_published_at": snapshot.published_at,
            "publication_sequence_watermark": watermark,
            "items": [archive_metadata(row) for row in page],
            "total": total,
            "next_cursor": next_cursor,
        }
