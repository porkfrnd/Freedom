"""add users.session_version

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-17

Adds ``users.session_version`` — bumped by the background membership check
when a user's Discord state changes so their JWT session dies immediately
(see §3.2).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("users", "session_version")
