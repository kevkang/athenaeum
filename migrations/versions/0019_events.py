"""Rename provenance_log → events; add per-project seq, actor, entity ref, pre-image.

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Rename the table (PK, FK, default carry over)
    op.rename_table("provenance_log", "events")
    op.execute("ALTER INDEX provenance_log_project_time_idx RENAME TO events_project_time_idx")

    # 2. Add new columns nullable; backfill before tightening
    op.add_column("events", sa.Column("project_seq", sa.BigInteger(), nullable=True))
    op.add_column("events", sa.Column("actor_id", UUID(as_uuid=True), nullable=True))
    op.add_column("events", sa.Column("entity_kind", sa.String(20), nullable=True))
    op.add_column("events", sa.Column("entity_id", UUID(as_uuid=True), nullable=True))
    op.add_column("events", sa.Column("prev_json", JSONB(), nullable=True))

    # 3. Backfill project_seq from chronological order within each project
    op.execute("""
        WITH ordered AS (
            SELECT id,
                   ROW_NUMBER() OVER (PARTITION BY project_id ORDER BY created_at, id)
                       AS new_seq
            FROM events
        )
        UPDATE events e
        SET project_seq = ordered.new_seq
        FROM ordered
        WHERE e.id = ordered.id
    """)

    # 4. Backfill entity_kind/entity_id from existing brick_* payloads
    op.execute("""
        UPDATE events
        SET entity_kind = 'brick',
            entity_id   = (payload_json->>'brick_id')::uuid
        WHERE kind LIKE 'brick_%'
          AND payload_json ? 'brick_id'
    """)
    op.execute("UPDATE events SET entity_kind = 'unknown' WHERE entity_kind IS NULL")

    # 5. Normalize kinds to dotted namespace
    op.execute("UPDATE events SET kind = 'brick.created'    WHERE kind = 'brick_created'")
    op.execute("UPDATE events SET kind = 'brick.cemented'   WHERE kind = 'brick_cemented'")
    op.execute("UPDATE events SET kind = 'brick.uncemented' WHERE kind = 'brick_uncemented'")
    op.execute("UPDATE events SET kind = 'brick.deleted'    WHERE kind = 'brick_deleted'")

    # 6. Tighten constraints
    op.alter_column("events", "project_seq", nullable=False)
    op.alter_column("events", "entity_kind", nullable=False)
    op.create_foreign_key(
        "events_actor_id_fkey", "events", "users", ["actor_id"], ["id"],
        ondelete="SET NULL",
    )

    # 7. Indexes for history pagination and entity lookup
    op.create_unique_constraint(
        "events_project_seq_uq", "events", ["project_id", "project_seq"]
    )
    op.create_index(
        "events_project_seq_desc_idx", "events",
        ["project_id", sa.text("project_seq DESC")],
    )
    op.create_index("events_entity_idx", "events", ["entity_kind", "entity_id"])

    # 8. Counter table for allocating the next project_seq under concurrent writes
    op.create_table(
        "project_event_seq",
        sa.Column(
            "project_id", UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("next_seq", sa.BigInteger(), nullable=False, server_default="1"),
    )
    op.execute("""
        INSERT INTO project_event_seq (project_id, next_seq)
        SELECT project_id, COALESCE(MAX(project_seq), 0) + 1
        FROM events
        GROUP BY project_id
    """)


def downgrade() -> None:
    op.drop_table("project_event_seq")

    op.drop_index("events_entity_idx", table_name="events")
    op.drop_index("events_project_seq_desc_idx", table_name="events")
    op.drop_constraint("events_project_seq_uq", "events", type_="unique")
    op.drop_constraint("events_actor_id_fkey", "events", type_="foreignkey")

    op.execute("UPDATE events SET kind = 'brick_created'    WHERE kind = 'brick.created'")
    op.execute("UPDATE events SET kind = 'brick_cemented'   WHERE kind = 'brick.cemented'")
    op.execute("UPDATE events SET kind = 'brick_uncemented' WHERE kind = 'brick.uncemented'")
    op.execute("UPDATE events SET kind = 'brick_deleted'    WHERE kind = 'brick.deleted'")

    op.drop_column("events", "prev_json")
    op.drop_column("events", "entity_id")
    op.drop_column("events", "entity_kind")
    op.drop_column("events", "actor_id")
    op.drop_column("events", "project_seq")

    op.execute("ALTER INDEX events_project_time_idx RENAME TO provenance_log_project_time_idx")
    op.rename_table("events", "provenance_log")
