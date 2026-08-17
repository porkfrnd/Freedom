"""Dashboard (§5.11, §7).

Shell routes behind ``require_guild_member``; the four panels live here:

* Playlists — explorer grid + editor (creator/admin only for edits).
* Giveaways — admin-only creator + management (reroll, republish).
* Broadcast — admin-only announcements composer with embed preview.
* Mod Logs — admin-only filterable table with expandable reasoning.

Admin POSTs call into the bot thread via ``BotRuntime.submit``; when the
bot is offline the row is created/queued and the UI surfaces the pending
state rather than failing silently.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from extensions import db
from models import (
    Announcement,
    Giveaway,
    ModerationLog,
    Playlist,
    User,
    generate_playlist_id,
    utcnow,
)
from services import discord_api
from utils.decorators import require_admin, require_guild_member
from utils.logging import get_logger

log = get_logger("blueprints.dashboard")

bp = Blueprint("dashboard", __name__)

TRACK_URL_RE = re.compile(r"^https?://[^\s]+$")
TRACK_LIMIT = 50


def _bot_runtime():
    return current_app.extensions.get("ffd_bot_runtime")


def _submit_bot(coro):
    """Submit a coroutine to the bot loop; None when the bot is offline."""
    runtime = _bot_runtime()
    if runtime is None or not runtime.is_alive():
        return None
    try:
        return runtime.submit(coro)
    except Exception as exc:  # noqa: BLE001
        log.warning("bot_submit_failed", error=str(exc))
        return None


def _guild_channels():
    """Text channels for selects; [] (with a hint) when Discord is unreachable."""
    cfg = current_app.config
    if not cfg.get("DISCORD_GUILD_ID") or not cfg.get("DISCORD_BOT_TOKEN"):
        return []
    return discord_api.fetch_guild_channels(cfg["DISCORD_GUILD_ID"], cfg) or []


# ── Shell ───────────────────────────────────────────────────────────────────

@bp.get("/dashboard")
@require_guild_member
def shell():
    return redirect(url_for("dashboard.playlists"))


# ── Playlists ───────────────────────────────────────────────────────────────

@bp.get("/playlists")
@require_guild_member
def playlists():
    filter_mode = request.args.get("filter", "public")
    mine = request.args.get("mine") == "1"

    query = Playlist.query
    if mine:
        query = query.filter(Playlist.creator_discord_id == g.claims["discord_id"])
    else:
        query = query.filter(Playlist.is_public.is_(True))

    playlists = query.order_by(Playlist.updated_at.desc()).all()
    now_playing_id = _now_playing()

    return render_template(
        "dashboard/playlists.html",
        playlists=playlists,
        mine=mine,
        now_playing_id=now_playing_id,
    )


@bp.get("/playlists/<playlist_id>")
@require_guild_member
def playlist_editor(playlist_id):
    playlist = db.session.get(Playlist, playlist_id)
    if playlist is None:
        flash("That playlist ID doesn't exist. Double-check the code and try again.", "error")
        return redirect(url_for("dashboard.playlists"))

    is_owner = playlist.creator_discord_id == g.claims["discord_id"]
    if not is_owner and not g.claims.get("is_admin"):
        return render_template(
            "errors/403.html",
            message="Only the playlist's creator (or an admin) can edit it.",
        ), 403
    return render_template(
        "dashboard/playlist_editor.html", playlist=playlist, is_owner=is_owner
    )


# ── Giveaways (admin) ───────────────────────────────────────────────────────

@bp.get("/giveaways")
@require_admin
def giveaways():
    channel_list = _guild_channels()
    giveaway_rows = (
        Giveaway.query.order_by(Giveaway.created_at.desc()).limit(50).all()
    )
    return render_template(
        "dashboard/giveaways.html",
        channels=channel_list,
        giveaways=giveaway_rows,
        default_end=utcnow() + timedelta(days=3),
    )


@bp.post("/giveaways")
@require_admin
def create_giveaway():
    prize = (request.form.get("prize") or "").strip()
    channel_id = (request.form.get("channel_id") or "").strip()
    end_raw = (request.form.get("end_time") or "").strip()
    num_winners_raw = (request.form.get("num_winners") or "1").strip()

    errors = []
    if not prize:
        errors.append("Give the prize a name.")
    if len(prize) > 120:
        errors.append("Prize name is too long (120 characters max).")
    if not channel_id:
        errors.append("Pick a channel to post it in.")
    try:
        num_winners = int(num_winners_raw)
    except ValueError:
        num_winners = 0
    if num_winners < 1 or num_winners > 20:
        errors.append("Number of winners must be between 1 and 20.")
    try:
        end_time = datetime.fromisoformat(end_raw)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        if end_time <= utcnow():
            errors.append("End time must be in the future.")
    except ValueError:
        errors.append("That end time doesn't parse. Use the date picker.")

    if errors:
        for msg in errors:
            flash(msg, "error")
        return redirect(url_for("dashboard.giveaways"))

    giveaway = Giveaway(
        prize=prize,
        channel_id=int(channel_id),
        created_by=g.claims["discord_id"],
        end_time=end_time,
        num_winners=num_winners,
        status="ACTIVE",
    )
    db.session.add(giveaway)
    db.session.commit()

    result = _publish_giveaway(giveaway.id)
    if result is None:
        flash(
            "Giveaway created — the bot is offline, so it'll be posted once the bot reconnects.",
            "info",
        )
    elif result.get("ok"):
        flash("Giveaway posted.", "success")
    else:
        flash("Giveaway created, but posting failed — you can retry below.", "warning")

    return redirect(url_for("dashboard.giveaways"))


def _publish_giveaway(giveaway_id: int):
    from bot.cogs.giveaways import GiveawayCog

    runtime = _bot_runtime()
    if runtime is None or not runtime.is_alive() or runtime.client is None:
        return None
    cog = runtime.client.get_cog("GiveawayCog")
    if cog is None:
        return None
    future = _submit_bot(cog.publish(giveaway_id))
    if future is None:
        return None
    try:
        return future.result(timeout=15)
    except Exception as exc:  # noqa: BLE001
        log.warning("giveaway_publish_timeout", giveaway_id=giveaway_id, error=str(exc))
        return {"ok": False, "error": "timeout"}


@bp.post("/giveaways/<int:giveaway_id>/publish")
@require_admin
def retry_publish_giveaway(giveaway_id):
    result = _publish_giveaway(giveaway_id)
    if result is None:
        flash("The bot is offline right now — try again once it's back.", "info")
    elif result.get("ok"):
        flash("Giveaway posted.", "success")
    else:
        flash("Couldn't post that giveaway yet.", "warning")
    return redirect(url_for("dashboard.giveaways"))


@bp.post("/giveaways/<int:giveaway_id>/reroll")
@require_admin
def reroll_giveaway(giveaway_id):
    from bot.cogs.giveaways import GiveawayCog, draw_winners

    runtime = _bot_runtime()
    if runtime is not None and runtime.is_alive() and runtime.client is not None:
        cog = runtime.client.get_cog("GiveawayCog")
        if cog is not None:
            future = _submit_bot(cog.reroll(giveaway_id))
            if future is not None:
                try:
                    result = future.result(timeout=15)
                except Exception as exc:  # noqa: BLE001
                    log.warning("giveaway_reroll_failed", error=str(exc))
                    result = {"ok": False, "error": "exception"}
                if result.get("ok"):
                    flash("Re-rolled — winners posted to Discord.", "success")
                else:
                    flash(_reroll_message(result), "warning")
                return redirect(url_for("dashboard.giveaways"))

    # Bot offline: fall back to a DB-only re-draw (history preserved).
    giveaway = db.session.get(Giveaway, giveaway_id)
    if giveaway is None:
        flash("That giveaway doesn't exist.", "error")
        return redirect(url_for("dashboard.giveaways"))
    if giveaway.status == "ACTIVE":
        flash("The giveaway is still running — re-rolls are for ended giveaways.", "warning")
        return redirect(url_for("dashboard.giveaways"))
    winners, _ = draw_winners(
        entrants=giveaway.entrants or [],
        num_winners=giveaway.num_winners,
        exclude=giveaway.winners or [],
    )
    if not winners:
        flash("No remaining entrants to re-roll.", "warning")
    else:
        giveaway.winners = (giveaway.winners or []) + winners
        db.session.commit()
        flash("Re-rolled — the bot is offline, so winners were logged in the database.", "info")
    return redirect(url_for("dashboard.giveaways"))


def _reroll_message(result: dict) -> str:
    return {
        "not_found": "That giveaway doesn't exist.",
        "still_active": "The giveaway is still running — re-rolls are for ended giveaways.",
        "no_remaining_entrants": "No remaining entrants to re-roll.",
    }.get(result.get("error"), "Couldn't re-roll that giveaway.")


# ── Broadcast (admin) ───────────────────────────────────────────────────────

@bp.get("/broadcast")
@require_admin
def broadcast():
    channel_list = _guild_channels()
    announcements = (
        Announcement.query.order_by(Announcement.created_at.desc()).limit(30).all()
    )
    return render_template(
        "dashboard/broadcast.html",
        channels=channel_list,
        announcements=announcements,
    )


@bp.post("/broadcast")
@require_admin
def create_announcement():
    title = (request.form.get("title") or "").strip()
    content = (request.form.get("content") or "").strip()
    channel_id = (request.form.get("channel_id") or "").strip()

    if not title:
        flash("Give the announcement a title.", "error")
        return redirect(url_for("dashboard.broadcast"))
    if len(title) > 120:
        flash("Title is too long (120 characters max).", "error")
        return redirect(url_for("dashboard.broadcast"))
    if not content:
        flash("Write something worth announcing.", "error")
        return redirect(url_for("dashboard.broadcast"))

    target_channel = int(channel_id) if channel_id else (
        int(current_app.config["DISCORD_ANNOUNCEMENTS_CHANNEL_ID"])
        if current_app.config.get("DISCORD_ANNOUNCEMENTS_CHANNEL_ID")
        else None
    )
    if target_channel is None:
        flash("Pick a channel to broadcast to (or configure DISCORD_ANNOUNCEMENTS_CHANNEL_ID).", "error")
        return redirect(url_for("dashboard.broadcast"))

    announcement = Announcement(
        author_id=g.claims["discord_id"],
        title=title,
        content=content,
        channel_id=target_channel,
        status="DRAFT",
    )
    db.session.add(announcement)
    db.session.commit()

    result = _dispatch_announcement(announcement.id)
    if result is None:
        flash("Saved as a draft — the bot is offline, so it'll go out once the bot reconnects.", "info")
    elif result.get("ok"):
        flash("Published.", "success")
    else:
        flash("Couldn't publish — the row is marked failed. Fix the channel and try again.", "warning")

    return redirect(url_for("dashboard.broadcast"))


def _dispatch_announcement(announcement_id: int):
    from bot.cogs.announcements import AnnouncementCog

    runtime = _bot_runtime()
    if runtime is None or not runtime.is_alive() or runtime.client is None:
        return None
    cog = runtime.client.get_cog("AnnouncementCog")
    if cog is None:
        return None
    future = _submit_bot(cog.dispatch(announcement_id))
    if future is None:
        return None
    try:
        return future.result(timeout=15)
    except Exception as exc:  # noqa: BLE001
        log.warning("announcement_dispatch_timeout", announcement_id=announcement_id, error=str(exc))
        return {"ok": False, "error": "timeout"}


# ── Mod logs (admin) ────────────────────────────────────────────────────────

@bp.get("/mod-logs")
@require_admin
def mod_logs():
    query = ModerationLog.query

    category = request.args.get("category", "").strip()
    severity = request.args.get("severity", "").strip()
    action = request.args.get("action", "").strip()
    if category:
        query = query.filter(ModerationLog.violation_category == category)
    if severity:
        try:
            query = query.filter(ModerationLog.severity_tier == int(severity))
        except ValueError:
            pass
    if action:
        query = query.filter(ModerationLog.action_taken == action)

    logs = query.order_by(ModerationLog.timestamp.desc()).limit(200).all()
    categories = sorted(
        row[0]
        for row in db.session.query(ModerationLog.violation_category).distinct().all()
    )
    return render_template(
        "dashboard/mod_logs.html",
        logs=logs,
        categories=categories,
        filters={"category": category, "severity": severity, "action": action},
    )


# ── Shared helpers ──────────────────────────────────────────────────────────

def _now_playing():
    """Playlist id currently playing, read from the music cog via the bot loop."""
    runtime = _bot_runtime()
    if runtime is None or not runtime.is_alive() or runtime.client is None:
        return None
    cog = runtime.client.get_cog("MusicCog")
    if cog is None:
        return None
    guild_id = current_app.config.get("DISCORD_GUILD_ID")
    if not guild_id:
        return None
    try:
        return cog.now_playing(int(guild_id))
    except Exception as exc:  # noqa: BLE001
        log.debug("now_playing_lookup_failed", error=str(exc))
        return None
