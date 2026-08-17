"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-17

Creates the five core tables: users, playlists, announcements,
moderation_logs, giveaways — with indexes on every FK and on columns used
by dashboard filters (§4).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _jsonb():
    """JSONB on Postgres, plain JSON elsewhere (SQLite dev/test)."""
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _empty_list_default():
    """Server default for JSON list columns, dialect-aware."""
    if op.get_bind().dialect.name == "postgresql":
        return sa.text("'[]'::jsonb")
    return sa.text("'[]'")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("discord_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("avatar_hash", sa.String(length=64), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("discord_id"),
    )

    op.create_table(
        "playlists",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("creator_discord_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("tracks", _jsonb(), nullable=False, server_default=_empty_list_default()),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["creator_discord_id"], ["users.discord_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_playlists_creator_discord_id", "playlists", ["creator_discord_id"]
    )
    op.create_index("ix_playlists_is_public", "playlists", ["is_public"])

    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("discord_msg_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="DRAFT"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["author_id"], ["users.discord_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "moderation_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("violation_category", sa.String(length=40), nullable=False),
        sa.Column("severity_tier", sa.Integer(), nullable=False),
        sa.Column("groq_analysis", _jsonb(), nullable=True),
        sa.Column(
            "action_taken", sa.String(length=20), nullable=False, server_default="NONE"
        ),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("content_purged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["user_id"], ["users.discord_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_moderation_logs_user_id", "moderation_logs", ["user_id"]
    )
    op.create_index(
        "ix_moderation_logs_timestamp", "moderation_logs", ["timestamp"]
    )
    op.create_index(
        "ix_moderation_logs_user_timestamp",
        "moderation_logs",
        ["user_id", "timestamp"],
    )

    op.create_table(
        "giveaways",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("prize", sa.String(length=120), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("num_winners", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("entrants", _jsonb(), nullable=False, server_default=_empty_list_default()),
        sa.Column("winners", _jsonb(), nullable=False, server_default=_empty_list_default()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.discord_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("giveaways")
    op.drop_index("ix_moderation_logs_user_timestamp", table_name="moderation_logs")
    op.drop_index("ix_moderation_logs_user_id", table_name="moderation_logs")
    op.drop_index("ix_moderation_logs_timestamp", table_name="moderation_logs")
    op.drop_table("moderation_logs")
    op.drop_table("announcements")
    op.drop_index("ix_playlists_is_public", table_name="playlists")
    op.drop_index("ix_playlists_creator_discord_id", table_name="playlists")
    op.drop_table("playlists")
    op.drop_table("users")
