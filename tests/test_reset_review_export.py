"""Old private review data exports as a minimal, portable local backup."""

import json
from datetime import UTC, date, datetime
from uuid import uuid4

from factories import batch
from sqlalchemy import insert, select
from test_cli import run_cli

from dragon_radar.owner import OwnerRepository
from dragon_radar.owner.models import owner_principals
from dragon_radar.review.export import export_local_review
from dragon_radar.review.models import NoteContent
from dragon_radar.review.repository import ReviewRepository
from dragon_radar.review.tables import review_note_revisions, review_notes

NOW = datetime(2026, 9, 21, 8, 30, tzinfo=UTC)
ORIGIN = "https://localhost:8030"
PROFILE = "browser-profile-1"


def populated_review(repository, database):
    archived = repository.import_batch(batch(), key="export-fixture")
    snapshot = repository.publish(archived.id)
    owners = OwnerRepository(database, clock=lambda: NOW)
    owner_id = owners.initialize()
    session = owners.exchange_pairing(owners.issue_pairing(ORIGIN), ORIGIN)
    review = ReviewRepository(database, owners, repository, clock=lambda: NOW)
    first = review.create_note(
        session.token,
        session.csrf,
        key="create",
        snapshot_id=snapshot.id,
        security_id="A",
        content=NoteContent(
            title="Original",
            judgment="First judgment",
            evidence="First evidence",
            invalidation="Old invalidation",
            verification_date=date(2026, 9, 11),
        ),
    )
    second = review.save_note(
        session.token,
        session.csrf,
        first["id"],
        key="save",
        expected=1,
        content=NoteContent(
            title="Updated",
            judgment="Second judgment",
            evidence="Second evidence",
            invalidation="New invalidation",
            verification_date=date(2026, 9, 12),
        ),
    )
    confirmed = review.confirm_note(
        session.token,
        session.csrf,
        first["id"],
        key="confirm",
        expected=2,
    )
    verification = review.add_verification(
        session.token,
        session.csrf,
        first["id"],
        key="verify",
        version=3,
        outcome="refuted",
        evidence="User verification",
        snapshot_id=snapshot.id,
    )
    watch = review.add_watch(
        session.token,
        session.csrf,
        key="watch",
        snapshot_id=snapshot.id,
        security_id="A",
    )
    return owner_id, first, second, confirmed, verification, watch


def test_export_maps_legacy_fields_and_preserves_complete_history(repository, database):
    owner_id, first, _, confirmed, verification, watch = populated_review(
        repository, database
    )

    payload = export_local_review(database, local_profile_id=PROFILE, exported_at=NOW)

    assert payload["format_version"] == 1
    assert payload["exported_at"] == NOW.isoformat()
    assert payload["profile_mapping"] == {
        "owner_id": str(owner_id),
        "local_profile_id": PROFILE,
    }
    assert payload["counts"] == {
        "notes": 1,
        "revisions": 3,
        "verifications": 1,
        "watchlist": 1,
    }
    assert payload["notes"] == [
        {
            "note_id": str(first["id"]),
            "snapshot_id": first["snapshot_id"],
            "security_id": "A",
            "current_revision": 3,
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
        }
    ]
    assert [row["revision"] for row in payload["revisions"]] == [1, 2, 3]
    assert payload["revisions"][0]["content"] == {
        "title": "Original",
        "content": "",
        "judgment": "First judgment",
        "evidence": "First evidence",
        "invalid_condition": "Old invalidation",
        "verify_date": "2026-09-11",
    }
    assert payload["revisions"][-1]["revision_id"] == confirmed["revision_id"]
    assert payload["verifications"] == [
        {
            "verification_id": verification["id"],
            "note_id": str(first["id"]),
            "revision_id": confirmed["revision_id"],
            "snapshot_id": verification["snapshot_id"],
            "outcome": "refuted",
            "evidence": "User verification",
            "supersedes": None,
            "created_at": NOW.isoformat(),
        }
    ]
    assert payload["watchlist"] == [
        {
            "watch_id": watch["id"],
            "security_id": "A",
            "snapshot_id": watch["snapshot_id"],
            "active": True,
            "revision": 1,
            "added_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
        }
    ]


def test_export_is_local_owner_scoped_and_contains_no_server_secrets_or_market_facts(
    repository, database
):
    owner_id, *_ = populated_review(repository, database)
    other_owner = uuid4()
    note_id = uuid4()
    revision_id = uuid4()
    with database.engine.begin() as connection:
        snapshot_id = connection.scalar(
            select(review_notes.c.snapshot_id).where(
                review_notes.c.owner_id == owner_id
            )
        )
        connection.execute(
            insert(owner_principals).values(
                id=other_owner, local_key=None, created_at=NOW
            )
        )
        connection.execute(
            insert(review_notes).values(
                id=note_id,
                owner_id=other_owner,
                snapshot_id=snapshot_id,
                trade_date=date(2026, 9, 8),
                security_id="SECRET",
                kind="note",
                version=1,
                state="draft",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            insert(review_note_revisions).values(
                id=revision_id,
                owner_id=other_owner,
                note_id=note_id,
                version=1,
                state="draft",
                content={
                    "title": "other-owner-secret",
                    "judgment": "",
                    "evidence": "",
                    "invalidation": "",
                    "verification_date": None,
                },
                saved_at=NOW,
            )
        )

    payload = export_local_review(database, local_profile_id=PROFILE, exported_at=NOW)
    encoded = json.dumps(payload, sort_keys=True)
    assert str(owner_id) in encoded
    assert str(other_owner) not in encoded
    assert "other-owner-secret" not in encoded
    for forbidden in (
        "facts",
        "bars",
        "article",
        "cookie",
        "session",
        "token",
        "csrf",
        "pairing",
        "credential",
        "owner_commands",
        "private_audit_events",
    ):
        assert forbidden not in encoded.lower()


def test_owner_export_cli_writes_secure_new_json_file(database, repository, tmp_path):
    populated_review(repository, database)
    output = tmp_path / "review-backup.json"
    database_url = database.engine.url.render_as_string(hide_password=False)

    result = run_cli(
        [
            "owner-export",
            "--local-profile-id",
            PROFILE,
            "--output",
            str(output),
        ],
        database_url,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["path"] == str(output)
    assert summary["counts"] == json.loads(output.read_text(encoding="utf-8"))["counts"]
    existing = output.read_bytes()
    second = run_cli(
        [
            "owner-export",
            "--local-profile-id",
            PROFILE,
            "--output",
            str(output),
        ],
        database_url,
    )
    assert second.returncode == 1
    assert output.read_bytes() == existing
    assert "Traceback" not in second.stderr
