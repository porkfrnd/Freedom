"""Seed dummy data for local development.

Usage:  FLASK_ENV=development python scripts/seed_db.py

Creates a handful of dummy users, one public playlist and one active
giveaway so the dashboard has something to render. HARD-GATED: the script
refuses to run unless ``FLASK_ENV == "development"`` — it can never be
pointed at a production database by accident.

Run ``alembic upgrade head`` (or create the tables) before seeding.
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config  # noqa: E402
from extensions import db  # noqa: E402
from models import Giveaway, Playlist, User, generate_playlist_id, utcnow  # noqa: E402

if os.environ.get("FLASK_ENV") != "development":
    print("Refusing to seed: FLASK_ENV must be 'development' (this protects prod).")
    sys.exit(1)

from app import create_app  # noqa: E402  (import after the gate)

app = create_app(Config)

DUMMY_USERS = [
    (101_000_000_000_001, "Mira", True),
    (101_000_000_000_002, "Dante", False),
    (101_000_000_000_003, "Noor", False),
    (101_000_000_000_004, "Ivo", False),
]

SAMPLE_TRACKS = [
    {"title": "Warm-up Groove (extended)", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "duration_seconds": 212, "added_at": None},
    {"title": "Floorwork Study", "url": "https://www.youtube.com/watch?v=9bZkp7q19f0", "duration_seconds": 195, "added_at": None},
    {"title": "Showcase Finale", "url": "https://www.youtube.com/watch?v=opj1Y9sWn3w", "duration_seconds": 248, "added_at": None},
]


def main() -> None:
    with app.app_context():
        if db.session.query(User).count() > 0 or db.session.query(Playlist).count() > 0:
            print("Database already has data — nothing to seed.")
            return

        now = utcnow()
        users = {}
        for discord_id, username, is_admin in DUMMY_USERS:
            user = User(
                discord_id=discord_id,
                username=username,
                is_admin=is_admin,
                first_seen_at=now - timedelta(days=40),
                last_seen_at=now - timedelta(hours=2),
            )
            db.session.add(user)
            users[username] = user
        db.session.flush()

        playlist = Playlist(
            id=generate_playlist_id(),
            creator_discord_id=users["Dante"].discord_id,
            name="Sweat & Spin — Week 1",
            tracks=[{**t, "added_at": now.isoformat()} for t in SAMPLE_TRACKS],
            is_public=True,
        )
        db.session.add(playlist)

        giveaway = Giveaway(
            prize="Front-row spot at the spring showcase",
            channel_id=101_000_000_000_500,
            created_by=users["Mira"].discord_id,
            end_time=now + timedelta(days=3),
            num_winners=2,
            status="ACTIVE",
            entrants=[users["Noor"].discord_id, users["Ivo"].discord_id],
        )
        db.session.add(giveaway)

        db.session.commit()
        print("Seeded:")
        print(f"  - {len(DUMMY_USERS)} users")
        print(f"  - playlist {playlist.id} — {playlist.name} ({playlist.track_count} tracks)")
        print(f"  - active giveaway: {giveaway.prize}")


if __name__ == "__main__":
    main()
