"""fix bricks.built_from_synthesis_id FK to ON DELETE SET NULL

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("bricks_built_from_synthesis_id_fkey", "bricks", type_="foreignkey")
    op.create_foreign_key(
        "bricks_built_from_synthesis_id_fkey",
        "bricks",
        "syntheses",
        ["built_from_synthesis_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("bricks_built_from_synthesis_id_fkey", "bricks", type_="foreignkey")
    op.create_foreign_key(
        "bricks_built_from_synthesis_id_fkey",
        "bricks",
        "syntheses",
        ["built_from_synthesis_id"],
        ["id"],
    )
