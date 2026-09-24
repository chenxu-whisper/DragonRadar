"""Serialize regulation snapshot publication and child appends."""

import sqlalchemy as sa
from alembic import op

revision = "0010_regulation_publication_lock"
down_revision = "0009_regulation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "publications_regulation_input",
        "publications",
        ["regulation_input_id"],
        postgresql_where=sa.text("regulation_input_id IS NOT NULL"),
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION guard_regulation_snapshot_child_insert()
        RETURNS trigger AS $$
        BEGIN
            PERFORM 1
              FROM regulation_snapshots
             WHERE id = NEW.snapshot_id
               FOR UPDATE;
            IF EXISTS (
                SELECT 1
                  FROM publications
                 WHERE regulation_input_id = NEW.snapshot_id
                   AND status IN ('published', 'revoked')
            ) THEN
                RAISE EXCEPTION 'published regulation snapshot is sealed';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade():
    raise RuntimeError("Regulation publication sealing cannot be weakened")
