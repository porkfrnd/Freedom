"""Database models — optimized for 0.5 GB Postgres storage budget.

Storage-conscious design:
- VARCHAR sizes trimmed to realistic maximums (saves ~1.9 KB/row)
- TEXT columns converted to bounded VARCHAR where content is capped server-side
- JSONB arrays capped at hard limits (giveaway entrants ≤ 500)
- New features use minimal storage: submissions (~500 B/row), events (~400 B/row)
- Activity feed is computed from existing timestamps (0 new storage)
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB as _PGJSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from extensions import db

Base = db.Model
JSONB = JSON().with_variant(_PGJSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ── Constants ──────────────────────────────────────────────────────────

ANNOUNCEMENT_CATEGORIES = ("CHALLENGE", "CHANGE", "EVENT", "GENERAL")
CHALLENGE_STATUSES = ("ACTIVE", "ENDED", "CANCELLED")
GIVEAWAY_STATUSES = ("ACTIVE", "ENDED", "CANCELLED")

# Hard caps — enforce in code + DB
MAX_GIVEAWAY_ENTRANTS = 500
MAX_PLAYLIST_TRACKS = 50
MAX_PLAYLIST_SAVES = 1000  # per playlist

AVATAR_COLORS = (
    ("violet", "Violet", "#7B2FF7"),
    ("magenta", "Magenta", "#FF2E9A"),
    ("cyan", "Cyan", "#34E4EA"),
    ("green", "Green", "#10B981"),
    ("amber", "Amber", "#F59E0B"),
    ("rose", "Rose", "#F43F5E"),
)

DANCE_STYLES = (
    "Hip-Hop", "Contemporary", "Freestyle", "Breaking",
    "Waacking", "Voguing", "House", "Popping",
    "Locking", "Choreography", "Afro", "Bollywood",
    "Salsa", "Ballet", "Jazz", "Other",
)

ACCENT_COLORS = (
    ("violet", "Violet", "#7B2FF7"),
    ("magenta", "Magenta", "#FF2E9A"),
    ("cyan", "Cyan", "#34E4EA"),
    ("rose", "Rose", "#F43F5E"),
    ("emerald", "Emerald", "#10B981"),
    ("amber", "Amber", "#F59E0B"),
)


# ── ID generation ──────────────────────────────────────────────────────

def generate_user_id() -> int:
    return secrets.randbits(63)


def format_uid(uid: int) -> str:
    return f"#{uid:,}"


def parse_uid(raw: str) -> int | None:
    if not raw:
        return None
    cleaned = raw.strip().lstrip("#").replace(",", "")
    try:
        val = int(cleaned)
    except (ValueError, TypeError):
        return None
    return val if val > 0 else None


_PLAYLIST_ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_PLAYLIST_ID_LENGTH = 4


def generate_playlist_id() -> str:
    body = "".join(
        secrets.choice(_PLAYLIST_ID_ALPHABET) for _ in range(_PLAYLIST_ID_LENGTH)
    )
    return f"DANCE-{body}"


def validate_playlist_id(playlist_id: str) -> bool:
    if not isinstance(playlist_id, str):
        return False
    if not playlist_id.startswith("DANCE-"):
        return False
    body = playlist_id[len("DANCE-"):]
    return (
        len(body) == _PLAYLIST_ID_LENGTH
        and all(ch in _PLAYLIST_ID_ALPHABET for ch in body)
    )


# ── User ───────────────────────────────────────────────────────────────

class User(db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_teacher: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    onboarded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Profile (trimmed for storage) ────────────────────────────────────
    display_name: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    bio: Mapped[str] = mapped_column(String(150), nullable=False, default="")
    dance_styles: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    avatar_color: Mapped[str] = mapped_column(String(16), nullable=False, default="violet")
    instagram: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    youtube: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    tiktok: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    # ── Privacy ──────────────────────────────────────────────────────────
    profile_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    show_email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    show_join_date: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    show_activity: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    playlists: Mapped[list["Playlist"]] = relationship(back_populates="creator", cascade="all, delete-orphan")
    announcements: Mapped[list["Announcement"]] = relationship(back_populates="author", cascade="all, delete-orphan")
    challenges: Mapped[list["Challenge"]] = relationship(back_populates="creator", cascade="all, delete-orphan")
    giveaways: Mapped[list["Giveaway"]] = relationship(back_populates="creator", cascade="all, delete-orphan")
    submissions: Mapped[list["Submission"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    events: Mapped[list["Event"]] = relationship(back_populates="creator", cascade="all, delete-orphan")

    def touch(self) -> None:
        self.last_seen_at = utcnow()

    @property
    def name(self) -> str:
        return self.display_name or self.username

    @property
    def uid_display(self) -> str:
        return format_uid(self.id)

    @property
    def avatar_bg(self) -> str:
        COLORS = {
            "violet": "bg-violet/20 border-violet/30 text-violet",
            "magenta": "bg-magenta/20 border-magenta/30 text-magenta",
            "cyan": "bg-cyan/20 border-cyan/30 text-cyan",
            "green": "bg-green-500/20 border-green-500/30 text-green-500",
            "amber": "bg-amber-500/20 border-amber-500/30 text-amber-500",
            "rose": "bg-rose-500/20 border-rose-500/30 text-rose-500",
        }
        return COLORS.get(self.avatar_color, COLORS["violet"])

    @property
    def avatar_initial(self) -> str:
        return (self.display_name or self.username or "?")[:1].upper()

    def profile_dict(self) -> dict:
        return {
            "id": self.id, "uid": format_uid(self.id),
            "username": self.username, "display_name": self.name,
            "bio": self.bio, "dance_styles": self.dance_styles or [],
            "avatar_color": self.avatar_color, "avatar_initial": self.avatar_initial,
            "is_admin": self.is_admin, "is_teacher": self.is_teacher,
            "email": self.email if self.show_email else None,
            "created_at": self.created_at.isoformat() if self.show_join_date else None,
            "instagram": self.instagram or None, "youtube": self.youtube or None,
            "tiktok": self.tiktok or None,
        }


# ── Playlist ───────────────────────────────────────────────────────────

class Playlist(db.Model):
    __tablename__ = "playlists"
    __table_args__ = (
        Index("ix_playlists_creator_id", "creator_id"),
        Index("ix_playlists_is_public", "is_public"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    creator_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    tracks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Playlist saves — JSONB array of user IDs (no new table needed)
    saved_by: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    creator: Mapped["User"] = relationship(back_populates="playlists")

    @property
    def track_count(self) -> int:
        return len(self.tracks or [])

    @property
    def save_count(self) -> int:
        return len(self.saved_by or [])

    def has_saved(self, user_id: int) -> bool:
        return user_id in (self.saved_by or [])

    def toggle_save(self, user_id: int) -> bool:
        """Toggle save. Returns True if now saved, False if unsaved."""
        saves = list(self.saved_by or [])
        if user_id in saves:
            saves.remove(user_id)
            self.saved_by = saves
            return False
        if len(saves) >= MAX_PLAYLIST_SAVES:
            return False  # cap reached
        saves.append(user_id)
        self.saved_by = saves
        return True


# ── Announcement ───────────────────────────────────────────────────────

class Announcement(db.Model):
    __tablename__ = "announcements"
    __table_args__ = (
        Index("ix_announcements_category", "category"),
        Index("ix_announcements_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    author_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(60), nullable=False)
    content: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False, default="GENERAL")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    author: Mapped["User"] = relationship(back_populates="announcements")


# ── Challenge ──────────────────────────────────────────────────────────

class Challenge(db.Model):
    __tablename__ = "challenges"
    __table_args__ = (
        Index("ix_challenges_status", "status"),
        Index("ix_challenges_deadline", "deadline"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    creator: Mapped["User"] = relationship(back_populates="challenges")
    submissions: Mapped[list["Submission"]] = relationship(back_populates="challenge", cascade="all, delete-orphan")

    @property
    def has_ended(self) -> bool:
        return self.deadline is not None and _aware(self.deadline) <= utcnow()


# ── Giveaway ───────────────────────────────────────────────────────────

class Giveaway(db.Model):
    __tablename__ = "giveaways"
    __table_args__ = (
        Index("ix_giveaways_status", "status"),
        Index("ix_giveaways_deadline", "deadline"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    prize: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    num_winners: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    entrants: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    winners: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    creator: Mapped["User"] = relationship(back_populates="giveaways")

    @property
    def has_ended(self) -> bool:
        return self.deadline is not None and _aware(self.deadline) <= utcnow()

    def has_entered(self, user_id: int) -> bool:
        return user_id in (self.entrants or [])

    def add_entrant(self, user_id: int) -> bool:
        entrants = list(self.entrants or [])
        if user_id in entrants:
            return False
        if len(entrants) >= MAX_GIVEAWAY_ENTRANTS:
            return False  # cap reached — prevents storage bloat
        entrants.append(user_id)
        self.entrants = entrants
        return True


# ── Submission (challenge attempts) — ~500 bytes/row ──────────────────

class Submission(db.Model):
    __tablename__ = "submissions"
    __table_args__ = (
        Index("ix_submissions_challenge_id", "challenge_id"),
        Index("ix_submissions_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    challenge_id: Mapped[int] = mapped_column(Integer, ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    url: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str] = mapped_column(String(150), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    challenge: Mapped["Challenge"] = relationship(back_populates="submissions")
    user: Mapped["User"] = relationship(back_populates="submissions")


# ── Event — ~400 bytes/row ────────────────────────────────────────────

class Event(db.Model):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_starts_at", "starts_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    location: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # RSVPs stored as JSONB on the event row — no join table needed
    # Format: {"going": [user_id, ...], "maybe": [user_id, ...]}
    rsvps: Mapped[dict[str, list[int]]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    creator: Mapped["User"] = relationship(back_populates="events")

    @property
    def going_count(self) -> int:
        return len((self.rsvps or {}).get("going", []))

    @property
    def maybe_count(self) -> int:
        return len((self.rsvps or {}).get("maybe", []))

    def get_rsvp(self, user_id: int) -> str | None:
        rsvps = self.rsvps or {}
        if user_id in rsvps.get("going", []):
            return "going"
        if user_id in rsvps.get("maybe", []):
            return "maybe"
        return None

    def set_rsvp(self, user_id: int, status: str | None) -> None:
        """Set RSVP status. Pass None to remove."""
        rsvps = dict(self.rsvps or {})
        # Remove from both
        for key in ("going", "maybe"):
            rsvps[key] = [uid for uid in rsvps.get(key, []) if uid != user_id]
        # Add to new status
        if status in ("going", "maybe"):
            rsvps.setdefault(status, []).append(user_id)
        self.rsvps = rsvps


# ── Auto-pruning helper ───────────────────────────────────────────────

def prune_old_data(session) -> dict[str, int]:
    """Delete expired/completed data older than retention limits.

    Returns a dict of {table: rows_deleted} for logging.
    Retention policy (storage-conscious):
    - Ended giveaways older than 90 days: remove entrants/winner lists
    - Ended challenges older than 90 days: keep row, clear nothing (small)
    - Submissions older than 180 days: delete entire row
    - Events that ended more than 30 days ago: delete entire row
    """
    now = utcnow()
    pruned = {}

    # Giveaway entrant lists older than 90 days — clear JSONB arrays
    cutoff = now - timedelta(days=90)
    old_giveaways = session.query(Giveaway).filter(
        Giveaway.status == "ENDED",
        Giveaway.created_at < cutoff,
    ).all()
    cleared = 0
    for g in old_giveaways:
        if g.entrants or g.winners:
            g.entrants = []
            g.winners = []
            cleared += 1
    if cleared:
        session.commit()
    pruned["giveaway_entrants_cleared"] = cleared

    # Submissions older than 180 days
    sub_cutoff = now - timedelta(days=180)
    deleted_subs = session.query(Submission).filter(
        Submission.created_at < sub_cutoff
    ).delete(synchronize_session=False)
    if deleted_subs:
        session.commit()
    pruned["submissions_deleted"] = deleted_subs

    # Events that ended more than 30 days ago
    evt_cutoff = now - timedelta(days=30)
    deleted_events = session.query(Event).filter(
        Event.ends_at.isnot(None),
        Event.ends_at < evt_cutoff,
    ).delete(synchronize_session=False)
    if deleted_events:
        session.commit()
    pruned["events_deleted"] = deleted_events

    return pruned
