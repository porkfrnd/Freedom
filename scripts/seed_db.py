"""Seed demo data for local development — including a temporary demo account.

Usage:  FLASK_ENV=development ./bin/python scripts/seed_db.py

Creates:
  - A demo user  (demo@freedom.dance / demo1234)  — an ADMIN so you can
    post challenges, giveaways, and announcements right away.
  - A few regular members, one public playlist, an active challenge,
    an open giveaway with entrants, and a couple of announcements.

HARD-GATED: refuses to run unless ``FLASK_ENV == \"development\"`` — it can
never be pointed at a production database by accident.
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if os.environ.get("FLASK_ENV") != "development":
    print("Refusing to seed: FLASK_ENV must be 'development' (this protects prod).")
    sys.exit(1)

from app import create_app  # noqa: E402  (import after the gate)
from extensions import db  # noqa: E402
from models import (  # noqa: E402
    Announcement,
    Challenge,
    Giveaway,
    Playlist,
    User,
    format_uid,
    generate_playlist_id,
    generate_user_id,
    utcnow,
)

app = create_app()

DEMO_ACCOUNT = {
    "email": "demo@freedom.dance",
    "password": "demo1234",
    "username": "Demo Dancer",
    "is_admin": True,
}

MEMBER_ACCOUNTS = [
    ("mira@freedom.dance", "Mira"),
    ("dante@freedom.dance", "Dante"),
    ("noor@freedom.dance", "Noor"),
]

TEACHER_ACCOUNT = {"email": "teacher@freedom.dance", "password": "teacher123", "username": "Ms. Groove"}

SAMPLE_TRACKS = [
    {"title": "Warm-up Groove (extended)", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "duration_seconds": 212, "added_at": None},
    {"title": "Floorwork Study", "url": "https://www.youtube.com/watch?v=9bZkp7q19f0", "duration_seconds": 195, "added_at": None},
    {"title": "Showcase Finale", "url": "https://www.youtube.com/watch?v=opj1Y9sWn3w", "duration_seconds": 248, "added_at": None},
]


def main() -> None:
    with app.app_context():
        if db.session.query(User).count() > 0:
            print("Database already has users — nothing to seed.")
            print("(If you need the demo account, delete dev.db first, then re-run.)")
            return

        from werkzeug.security import generate_password_hash

        now = utcnow()

        # ── Demo admin + members ───────────────────────────────────────────
        demo = User(
            id=generate_user_id(),
            email=DEMO_ACCOUNT["email"],
            username=DEMO_ACCOUNT["username"],
            password_hash=generate_password_hash(DEMO_ACCOUNT["password"]),
            is_admin=True,
            created_at=now - timedelta(days=40),
            last_seen_at=now - timedelta(hours=2),
        )
        db.session.add(demo)

        members = {}
        for email, name in MEMBER_ACCOUNTS:
            m = User(
                id=generate_user_id(),
                email=email,
                username=name,
                password_hash=generate_password_hash("password123"),
                is_admin=False,
                created_at=now - timedelta(days=20),
                last_seen_at=now - timedelta(hours=5),
            )
            db.session.add(m)
            members[name] = m
        db.session.flush()

        # Teacher account
        teacher = User(
            id=generate_user_id(),
            email=TEACHER_ACCOUNT["email"],
            username=TEACHER_ACCOUNT["username"],
            password_hash=generate_password_hash(TEACHER_ACCOUNT["password"]),
            is_admin=False,
            is_teacher=True,
            created_at=now - timedelta(days=15),
            last_seen_at=now - timedelta(hours=3),
        )
        db.session.add(teacher)
        db.session.flush()

        # ── Playlist ───────────────────────────────────────────────────────
        playlist = Playlist(
            id=generate_playlist_id(),
            creator_id=members["Dante"].id,
            name="Sweat & Spin — Week 1",
            tracks=[{**t, "added_at": now.isoformat()} for t in SAMPLE_TRACKS],
            is_public=True,
        )
        db.session.add(playlist)

        # ── Active challenge ───────────────────────────────────────────────
        challenge = Challenge(
            creator_id=demo.id,
            title="Freestyle Friday — 30 seconds, song picked at random",
            description=(
                "This week: we drop one track and you get 30 seconds of freestyle. "
                "No choreography, no prep — just move. Record it, share it, and "
                "show up Friday to see what everyone brought."
            ),
            deadline=now + timedelta(days=3),
            status="ACTIVE",
        )
        db.session.add(challenge)

        # ── Open giveaway ──────────────────────────────────────────────────
        giveaway = Giveaway(
            creator_id=demo.id,
            prize="Front-row spot at the spring showcase",
            description="One lucky dancer gets a reserved front-row spot — plus a shoutout during the opening.",
            deadline=now + timedelta(days=3),
            num_winners=2,
            status="ACTIVE",
            entrants=[members["Noor"].id, members["Mira"].id],
        )
        db.session.add(giveaway)

        # ── Announcements ──────────────────────────────────────────────────
        db.session.add(Announcement(
            author_id=demo.id,
            title="New challenge is live",
            content="Freestyle Friday is on. Check the Challenges tab — 30 seconds, one random track, zero excuses.",
            category="CHALLENGE",
            created_at=now - timedelta(hours=1),
        ))
        db.session.add(Announcement(
            author_id=demo.id,
            title="Session time moving to 6pm",
            content="Starting next week, Wednesday open sessions start at 6pm instead of 5:30. The floor needs a proper warm-up.",
            category="CHANGE",
            created_at=now - timedelta(days=1),
        ))

        db.session.commit()

        print("Seeded the database:")
        print(f"  - Demo admin:  {DEMO_ACCOUNT['email']} / {DEMO_ACCOUNT['password']}  UID: {format_uid(demo.id)}")
        print(f"  - Teacher:     {TEACHER_ACCOUNT['email']} / {TEACHER_ACCOUNT['password']}  UID: {format_uid(teacher.id)}")
        print(f"  - Members:     {', '.join(e for e, _ in MEMBER_ACCOUNTS)} / password123")
        print(f"  - Playlist:    {playlist.id} — {playlist.name} ({playlist.track_count} tracks)")
        print(f"  - Challenge:   {challenge.title}")
        print(f"  - Giveaway:    {giveaway.prize}")
        print("Log in with the demo account to manage users and post challenges.")


if __name__ == "__main__":
    main()
