"""Database models — the single source of truth for the schema.

Every model change ships an Alembic migration (see ``migrations/``); never
edit the schema by hand in production.

Type notes:
- ``JSONB`` renders as Postgres JSONB, and as plain JSON on SQLite so the
  local dev database and the test suite can use the same models.
- ``BigInteger`` PKs keep Discord snowflakes exact on both backends.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB as _PGJSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from extensions import db

# Declarative base shared with Alembic (migrations/env.py imports this).
Base = db.Model

# JSONB that degrades gracefully to JSON on SQLite (dev/tests).
JSONB = JSON().with_variant(_PGJSONB(), "postgresql")


def utcnow() -> datetime:
    """Timezone-aware UTC now, used for all server-side timestamps."""
    return datetime.now(timezone.utc)


# ── Enumerated-ish values (kept as constants, enforced in code + app logic) ─

ANNOUNCEMENT_STATUSES = ("DRAFT", "SENT", "FAILED")
GIVEAWAY_STATUSES = ("ACTIVE", "ENDED", "CANCELLED")
MOD_ACTION_TAKEN = ("NONE", "WARNING", "TIMEOUT_SHORT", "TIMEOUT_LONG")
SEVERITY_TIERS = (1, 2, 3)

# Alphabet for playlist IDs: unambiguous (no 0/O/1/I), uppercase.
_PLAYLIST_ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_PLAYLIST_ID_LENGTH = 4


def generate_playlist_id() -> str:
    """Return a short human-readable playlist ID like ``DANCE-89A2``.

    The body is drawn from an unambiguous alphabet so the ID is easy to
    read aloud and type into Discord. Uniqueness is checked against the
    database by the caller, which retries on collision.
    """
    body = "".join(
        secrets.choice(_PLAYLIST_ID_ALPHABET) for _ in range(_PLAYLIST_ID_LENGTH)
    )
    return f"DANCE-{body}"


def validate_playlist_id(playlist_id: str) -> bool:
    """Return True when ``playlist_id`` matches the ``DANCE-XXXX`` format."""
    if not isinstance(playlist_id, str):
        return False
    if not playlist_id.startswith("DANCE-"):
        return False
    body = playlist_id[len("DANCE-") :]
    return (
        len(body) == _PLAYLIST_ID_LENGTH
        and all(ch in _PLAYLIST_ID_ALPHABET for ch in body)
    )


class User(db.Model):
    __tablename__ = "users"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    avatar_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Recalculated from live Discord permissions — never user-editable.
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Bumped when a background membership check finds the user's Discord
    # state changed; the JWT carries this value so stale sessions die
    # instantly (see services/auth.py + utils/decorators.py).
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    playlists: Mapped[list["Playlist"]] = relationship(
        back_populates="creator", cascade="all, delete-orphan"
    )
    announcements: Mapped[list["Announcement"]] = relationship(
        back_populates="author", cascade="all, delete-orphan"
    )
    moderation_logs: Mapped[list["ModerationLog"]] = relationship(back_populates="user")
    giveaways: Mapped[list["Giveaway"]] = relationship(back_populates="creator")

    def touch(self) -> None:
        self.last_seen_at = utcnow()

    @property
    def avatar_url(self) -> str:
        """Discord CDN URL, or a generated initial-on-violet fallback."""
        if self.avatar_hash:
            return (
                f"https://cdn.discordapp.com/avatars/{self.discord_id}/"
                f"{self.avatar_hash}.png?size=128"
            )
        initial = (self.username[:1] or "?").upper()
        return (
            f"https://ui-avatars.com/api/?name={initial}&background=7B2FF7"
            f"&color=F3F1F8&bold=true&size=128"
        )


class Playlist(db.Model):
    __tablename__ = "playlists"
    __table_args__ = (
        Index("ix_playlists_creator_discord_id", "creator_discord_id"),
        Index("ix_playlists_is_public", "is_public"),
    )

    # Short human-readable ID: DANCE-XXXX (see generate_playlist_id).
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    creator_discord_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.discord_id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # JSONB array of {title, url, duration_seconds, added_at}.
    tracks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    creator: Mapped[User] = relationship(back_populates="playlists")

    @property
    def track_count(self) -> int:
        return len(self.tracks or [])


class Announcement(db.Model):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    author_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.discord_id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Target text channel chosen at publish time (from the Broadcast panel).
    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Null until the bot has dispatched the embed.
    discord_msg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="DRAFT"
    )  # DRAFT | SENT | FAILED
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    author: Mapped[User] = relationship(back_populates="announcements")


class ModerationLog(db.Model):
    __tablename__ = "moderation_logs"
    __table_args__ = (
        # Powers the rolling-window repeat-offense check (§6.2).
        Index("ix_moderation_logs_user_timestamp", "user_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Nullable + SET NULL so audit rows survive a user deletion; the row
    # itself is retained for the audit period even when content is purged.
    user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.discord_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    violation_category: Mapped[str] = mapped_column(String(40), nullable=False)
    severity_tier: Mapped[int] = mapped_column(Integer, nullable=False)  # 1|2|3
    # Raw model reasoning JSONB (only the reasoning, not raw message text).
    groq_analysis: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=True)
    action_taken: Mapped[str] = mapped_column(
        String(20), nullable=False, default="NONE"
    )  # NONE | WARNING | TIMEOUT_SHORT | TIMEOUT_LONG
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    # True once the scheduled purge job has nulled ``content`` (§8).
    content_purged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    user: Mapped[User | None] = relationship(back_populates="moderation_logs")


class Giveaway(db.Model):
    __tablename__ = "giveaways"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prize: Mapped[str] = mapped_column(String(120), nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Null until the bot has posted the embed.
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.discord_id", ondelete="SET NULL"),
        nullable=True,
    )
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    num_winners: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ACTIVE"
    )  # ACTIVE | ENDED | CANCELLED
    entrants: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    winners: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    creator: Mapped[User | None] = relationship(back_populates="giveaways")

    @property
    def has_ended(self) -> bool:
        return self.end_time <= utcnow()

    def has_entered(self, discord_id: int) -> bool:
        return discord_id in (self.entrants or [])

    def add_entrant(self, discord_id: int) -> bool:
        """Add an entrant; returns False when they had already entered."""
        entrants = list(self.entrants or [])
        if discord_id in entrants:
            return False
        entrants.append(discord_id)
        self.entrants = entrants
        return True
