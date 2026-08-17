"""Public landing page (§5.11) + shared helpers.

The landing page is the one page that works with zero auth: mission copy,
the equalizer signature, live stats pulled from the database, and feature
cards. The guild's name/avatar are fetched from Discord when a bot token
is configured (cached), otherwise the brand name is used.
"""

from __future__ import annotations

from flask import Blueprint, current_app, render_template

from extensions import db
from models import Giveaway, Playlist, User
from services import discord_api

bp = Blueprint("main", __name__)

MISSION = "Dancing is a form of expression and we must push it forward."


@bp.get("/")
def index():
    cfg = current_app.config

    member_count = db.session.query(User).count()
    playlist_count = db.session.query(Playlist).filter(Playlist.is_public.is_(True)).count()
    event_count = db.session.query(Giveaway).filter(Giveaway.status == "ACTIVE").count()

    guild = None
    if cfg.get("DISCORD_GUILD_ID") and cfg.get("DISCORD_BOT_TOKEN"):
        guild = discord_api.fetch_guild(cfg["DISCORD_GUILD_ID"], cfg)

    return render_template(
        "landing.html",
        mission=MISSION,
        member_count=member_count,
        playlist_count=playlist_count,
        event_count=event_count,
        guild_name=(guild or {}).get("name") or "Freedom for Dance",
        guild_icon=(guild or {}).get("icon"),
    )


@bp.get("/not-member")
def not_member():
    return render_template("not_member.html"), 403
