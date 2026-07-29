"""Add ON DELETE CASCADE to project_members.project_id — the one table
referencing projects.id that was missing it (every other FK to projects.id
already cascades). Needed before a project-delete endpoint can exist at
all: every project has at least one ProjectMember (its owner) by
construction, so without this, deleting a project would fail outright on
this FK constraint the moment such an endpoint was added.

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-29

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("project_members_project_id_fkey", "project_members", type_="foreignkey")
    op.create_foreign_key(
        "project_members_project_id_fkey",
        "project_members",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("project_members_project_id_fkey", "project_members", type_="foreignkey")
    op.create_foreign_key(
        "project_members_project_id_fkey",
        "project_members",
        "projects",
        ["project_id"],
        ["id"],
    )
