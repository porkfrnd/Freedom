"""initial schema — matches models.py (email/password auth era)

Revision ID: 0001
Revises:
Create Date: 2026-08-17

Tables: users, playlists, announcements, challenges, submissions, events.
Indexes on every FK plus the columns used by dashboard filters.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[Sequence[str], str, None] = None


def _jsonb():
    """JSONB on Postgres, plain JSON elsewhere (SQLite dev/test)."""
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgres"


def _json_default(text: str) -> sa.text:
    """Server default for JSON columns, dialect-aware ('[]' or '{}')."""
    if _is_pg():
        return sa.text(f"'{text}'::jsonb")
    return sa.text(f"'{text}'")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=120), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.String(length=80), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_teacher", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("onboarded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("display_name", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("bio", sa.String(length=150), nullable=False, server_default=""),
        sa.Column("dance_styles", _jsonb(), nullable=False, server_default=_json_default("[]")),
        sa.Column("avatar_color", sa.String(length=16), nullable=False, server_default="violet"),
        sa.Column("instagram", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("youtube", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("tiktok", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("profile_public", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("show_email", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("show_join_date", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("show_activity", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "playlists",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("creator_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("tracks", _jsonb(), nullable=False, server_default=_json_default("[]")),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("saved_by", _jsonb(), nullable=False, server_default=_json_default("[]")),
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
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_playlists_creator_id", "playlists", ["creator_id"])
    op.create_index("ix_playlists_is_public", "playlists", ["is_public"])

    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=60), nullable=False),
        sa.Column("content", sa.String(length=500), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False, server_default="GENERAL"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_announcements_category", "announcements", ["category"])
    op.create_index("ix_announcements_created_at", "announcements", ["created_at"])

    op.create_table(
        "challenges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("creator_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=60), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_challenges_status", "challenges", ["status"])
    op.create_index("ix_challenges_deadline", "challenges", ["deadline"])

    op.create_table(
        "submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("challenge_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=200), nullable=False),
        sa.Column("note", sa.String(length=150), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["challenge_id"], ["challenges.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_submissions_challenge_id", "submissions", ["challenge_id"])
    op.create_index("ix_submissions_user_id", "submissions", ["user_id"])

    op.create_table(
        "giveaways",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("creator_id", sa.Integer(), nullable=False),
        sa.Column("prize", sa.String(length=60), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("num_winners", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("entrants", _jsonb(), nullable=False, server_default=_json_default("[]")),
        sa.Column("winners", _jsonb(), nullable=False, server_default=_json_default("[]")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_giveaways_status", "giveaways", ["status"])
    op.create_index("ix_giveaways_deadline", "giveaways", ["deadline"])

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("creator_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=60), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("location", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rsvps", _jsonb(), nullable=False, server_default=_json_default("{}")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_events_starts_at", "events", ["starts_at"])


def downgrade() -> None:
    op.drop_index("ix_giveaways_status", table_name="giveaways")
    op.drop_index("ix_giveaways_deadline", table_name="giveaways")
    op.drop_table("giveaways")
    op.drop_index("ix_events_starts_at", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_submissions_user_id", table_name="submissions")
    op.drop_index("ix_submissions_challenge_id", table_name="submissions")
    op.drop_table("submissions")
    op.drop_index("ix_challenges_deadline", table_name="challenges")
    op.drop_index("ix_challenges_status", table_name="challenges")
    op.drop_table("challenges")
    op.drop_index("ix_announcements_created_at", table_name="announcements")
    op.drop_index("ix_announcements_category", table_name="announcements")
    op.drop_table("announcements")
    op.drop_index("ix_playlists_is_public", table_name="playlists")
    op.drop_index("ix_playlists_creator_id", table_name="playlists")
    op.drop_table("playlists")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
