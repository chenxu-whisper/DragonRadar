"""Private research records and immutable revisions."""

from alembic import op

revision = "0003_private_review"
down_revision = "0002_owner"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE watch_entries (
            id UUID PRIMARY KEY, owner_id UUID NOT NULL REFERENCES owner_principals(id),
            security_id TEXT NOT NULL,
            snapshot_id UUID NOT NULL REFERENCES snapshots(id),
            active BOOLEAN NOT NULL,
            version INTEGER NOT NULL
                CONSTRAINT watch_version_positive CHECK (version > 0),
            added_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
            UNIQUE (owner_id, id), UNIQUE (owner_id, security_id)
        );
        CREATE TABLE review_notes (
            id UUID PRIMARY KEY, owner_id UUID NOT NULL REFERENCES owner_principals(id),
            snapshot_id UUID NOT NULL REFERENCES snapshots(id),
            trade_date DATE NOT NULL, security_id TEXT,
            kind VARCHAR(20) NOT NULL
                CONSTRAINT notes_kind_valid CHECK (kind IN ('note', 'daily')),
            version INTEGER NOT NULL
                CONSTRAINT notes_version_positive CHECK (version > 0),
            state VARCHAR(20) NOT NULL
                CONSTRAINT notes_state_valid CHECK (state IN ('draft', 'confirmed')),
            derived_from_revision_id UUID, template_version VARCHAR(80),
            created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
            UNIQUE (owner_id, id)
        );
        CREATE TABLE review_note_revisions (
            id UUID PRIMARY KEY, owner_id UUID NOT NULL REFERENCES owner_principals(id),
            note_id UUID NOT NULL,
            version INTEGER NOT NULL
                CONSTRAINT revisions_version_positive CHECK (version > 0),
            state VARCHAR(20) NOT NULL
                CONSTRAINT revisions_state_valid
                CHECK (state IN ('draft', 'confirmed')),
            content JSONB NOT NULL, saved_at TIMESTAMPTZ NOT NULL,
            UNIQUE (owner_id, id), UNIQUE (owner_id, note_id, version),
            UNIQUE (owner_id, note_id, id),
            FOREIGN KEY (owner_id, note_id) REFERENCES review_notes(owner_id, id)
        );
        ALTER TABLE review_notes ADD CONSTRAINT notes_derived_owner_fk
            FOREIGN KEY (owner_id, derived_from_revision_id)
            REFERENCES review_note_revisions(owner_id, id);
        CREATE TABLE review_verifications (
            id UUID PRIMARY KEY, owner_id UUID NOT NULL REFERENCES owner_principals(id),
            note_id UUID NOT NULL, revision_id UUID NOT NULL,
            snapshot_id UUID REFERENCES snapshots(id),
            outcome VARCHAR(20) NOT NULL CONSTRAINT verification_outcome_valid CHECK
                (outcome IN ('supported', 'weakened', 'refuted', 'not_due')),
            evidence TEXT NOT NULL, supersedes UUID, created_at TIMESTAMPTZ NOT NULL,
            UNIQUE (owner_id, id),
            UNIQUE (owner_id, note_id, revision_id, id),
            FOREIGN KEY (owner_id, note_id) REFERENCES review_notes(owner_id, id),
            FOREIGN KEY (owner_id, note_id, revision_id)
                REFERENCES review_note_revisions(owner_id, note_id, id),
            FOREIGN KEY (owner_id, note_id, revision_id, supersedes)
                REFERENCES review_verifications(owner_id, note_id, revision_id, id)
        );
        CREATE TABLE owner_commands (
            owner_id UUID NOT NULL REFERENCES owner_principals(id),
            key VARCHAR(200) NOT NULL, digest VARCHAR(64) NOT NULL,
            result JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (owner_id, key)
        );
        CREATE TABLE private_audit_events (
            id UUID PRIMARY KEY, owner_id UUID NOT NULL REFERENCES owner_principals(id),
            action VARCHAR(80) NOT NULL, object_id UUID NOT NULL,
            version INTEGER, occurred_at TIMESTAMPTZ NOT NULL,
            UNIQUE (owner_id, id)
        );
        CREATE INDEX watch_owner_updated ON watch_entries(owner_id, updated_at, id);
        CREATE INDEX notes_owner_updated ON review_notes(owner_id, updated_at, id);
        CREATE INDEX verifications_owner_note
            ON review_verifications(owner_id, note_id);
    """)
    for table in (
        "review_note_revisions",
        "review_verifications",
        "private_audit_events",
        "owner_commands",
    ):
        op.execute(f"""
            CREATE TRIGGER {table}_immutable
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
            CREATE TRIGGER {table}_no_truncate
            BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION reject_immutable_mutation();
        """)


def downgrade():
    raise RuntimeError("Private research history cannot be destructively downgraded")
