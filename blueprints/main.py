"""Public landing page + shared helpers.

The landing page works with zero auth: mission copy, the equalizer
signature, live stats, and feature cards. Real content, not template filler.
"""

from __future__ import annotations

from flask import Blueprint, render_template

from extensions import db
from models import Challenge, Giveaway, Playlist, User

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    member_count = db.session.query(User).count()
    playlist_count = db.session.query(Playlist).filter(Playlist.is_public.is_(True)).count()
    challenge_count = db.session.query(Challenge).filter(Challenge.status == "ACTIVE").count()
    giveaway_count = db.session.query(Giveaway).filter(Giveaway.status == "ACTIVE").count()

    return render_template(
        "landing.html",
        member_count=member_count,
        playlist_count=playlist_count,
        challenge_count=challenge_count,
        giveaway_count=giveaway_count,
    )
