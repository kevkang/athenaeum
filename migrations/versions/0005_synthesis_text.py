"""add synthesis_text to syntheses

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("syntheses", sa.Column("synthesis_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("syntheses", "synthesis_text")
