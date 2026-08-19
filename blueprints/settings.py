"""Settings + community features — all storage-optimized."""

from __future__ import annotations

import re
from datetime import timedelta

from flask import (
    Blueprint,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from extensions import db
from models import (
    AVATAR_COLORS, ACCENT_COLORS, DANCE_STYLES,
    Challenge, Event, Giveaway, Playlist, Submission, User,
    format_uid, utcnow,
)
from services.auth import (
    create_session_token, set_session_cookie, clear_session_cookie,
)
from utils.decorators import require_login
from utils.logging import get_logger

log = get_logger("blueprints.settings")

bp = Blueprint("settings", __name__)

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


# ── Onboarding ─────────────────────────────────────────────────────────

@bp.get("/onboarding")
@require_login
def onboarding():
    if g.user.onboarded:
        return redirect(url_for("dashboard.home"))
    return render_template(
        "dashboard/onboarding.html",
        dance_styles=DANCE_STYLES,
        avatar_colors=AVATAR_COLORS,
    )


@bp.post("/onboarding")
@require_login
def complete_onboarding():
    display_name = (request.form.get("display_name") or g.user.username).strip()[:32]
    styles = request.form.getlist("dance_styles")
    avatar_color = (request.form.get("avatar_color") or "violet").strip()
    valid_styles = set(DANCE_STYLES)
    styles = [s for s in styles if s in valid_styles]
    valid_colors = {c[0] for c in AVATAR_COLORS}
    if avatar_color not in valid_colors:
        avatar_color = "violet"

    g.user.display_name = display_name
    g.user.dance_styles = styles
    g.user.avatar_color = avatar_color
    g.user.onboarded = True
    db.session.commit()

    token = create_session_token(g.user.id, g.user.username, g.user.email, g.user.is_admin, g.user.is_teacher)
    resp = redirect(url_for("dashboard.home"))
    set_session_cookie(resp, token)
    flash("Welcome to the floor!", "success")
    return resp


# ── Settings page ───────────────────────────────────────────────────────

@bp.get("/settings")
@require_login
def settings():
    return render_template(
        "dashboard/settings.html",
        user=g.user,
        avatar_colors=AVATAR_COLORS,
        accent_colors=ACCENT_COLORS,
        dance_styles=DANCE_STYLES,
        format_uid=format_uid,
    )


# ── Account changes ────────────────────────────────────────────────────

@bp.post("/settings/password")
@require_login
def change_password():
    from werkzeug.security import check_password_hash, generate_password_hash
    current = request.form.get("current_password") or ""
    new = request.form.get("new_password") or ""
    confirm = request.form.get("confirm_password") or ""
    errors = []
    if not check_password_hash(g.user.password_hash, current):
        errors.append("Your current password is incorrect.")
    if len(new) < 8:
        errors.append("New password needs at least 8 characters.")
    if new != confirm:
        errors.append("The two new passwords don't match.")
    if current == new:
        errors.append("New password must be different from the current one.")
    if errors:
        for msg in errors:
            flash(msg, "error")
        return redirect(url_for("settings.settings"))
    g.user.password_hash = generate_password_hash(new, method="pbkdf2:sha256", salt_length=16)
    db.session.commit()
    token = create_session_token(g.user.id, g.user.username, g.user.email, g.user.is_admin, g.user.is_teacher)
    resp = redirect(url_for("settings.settings"))
    set_session_cookie(resp, token)
    flash("Password changed.", "success")
    return resp


@bp.post("/settings/email")
@require_login
def change_email():
    from werkzeug.security import check_password_hash
    new_email = (request.form.get("new_email") or "").strip().lower()
    password = request.form.get("email_password") or ""
    errors = []
    if not new_email or not _EMAIL_RE.match(new_email):
        errors.append("That email address doesn't look right.")
    if new_email == g.user.email:
        errors.append("New email must be different from the current one.")
    if not check_password_hash(g.user.password_hash, password):
        errors.append("Your password is incorrect.")
    if User.query.filter(User.email == new_email, User.id != g.user.id).first():
        errors.append("That email is already taken.")
    if errors:
        for msg in errors:
            flash(msg, "error")
        return redirect(url_for("settings.settings"))
    g.user.email = new_email
    db.session.commit()
    flash(f"Email changed to {new_email}.", "success")
    return redirect(url_for("settings.settings"))


@bp.post("/settings/username")
@require_login
def change_username():
    from werkzeug.security import check_password_hash
    new_username = (request.form.get("new_username") or "").strip()
    password = request.form.get("username_password") or ""
    errors = []
    if not new_username or len(new_username) < 2:
        errors.append("Username needs at least 2 characters.")
    if len(new_username) > 32:
        errors.append("Username is too long (32 characters max).")
    if new_username == g.user.username:
        errors.append("New username must be different.")
    if not check_password_hash(g.user.password_hash, password):
        errors.append("Your password is incorrect.")
    if User.query.filter(User.username == new_username, User.id != g.user.id).first():
        errors.append("That username is already taken.")
    if errors:
        for msg in errors:
            flash(msg, "error")
        return redirect(url_for("settings.settings"))
    g.user.username = new_username
    db.session.commit()
    token = create_session_token(g.user.id, g.user.username, g.user.email, g.user.is_admin, g.user.is_teacher)
    resp = redirect(url_for("settings.settings"))
    set_session_cookie(resp, token)
    flash(f"Username changed to {new_username}.", "success")
    return resp


# ── Profile ────────────────────────────────────────────────────────────

@bp.post("/settings/profile")
@require_login
def update_profile():
    display_name = (request.form.get("display_name") or "").strip()[:32]
    bio = (request.form.get("bio") or "").strip()[:150]
    styles = [s for s in request.form.getlist("dance_styles") if s in set(DANCE_STYLES)]
    avatar_color = (request.form.get("avatar_color") or "violet").strip()
    if avatar_color not in {c[0] for c in AVATAR_COLORS}:
        avatar_color = "violet"
    g.user.display_name = display_name
    g.user.bio = bio
    g.user.dance_styles = styles
    g.user.avatar_color = avatar_color
    db.session.commit()
    token = create_session_token(g.user.id, g.user.username, g.user.email, g.user.is_admin, g.user.is_teacher)
    resp = redirect(url_for("settings.settings"))
    set_session_cookie(resp, token)
    flash("Profile updated.", "success")
    return resp


@bp.post("/settings/social")
@require_login
def update_social():
    g.user.instagram = (request.form.get("instagram") or "").strip()[:32]
    g.user.youtube = (request.form.get("youtube") or "").strip()[:32]
    g.user.tiktok = (request.form.get("tiktok") or "").strip()[:32]
    db.session.commit()
    flash("Social links updated.", "success")
    return redirect(url_for("settings.settings"))


@bp.post("/settings/privacy")
@require_login
def update_privacy():
    g.user.profile_public = request.form.get("profile_public") == "on"
    g.user.show_email = request.form.get("show_email") == "on"
    g.user.show_join_date = request.form.get("show_join_date") == "on"
    g.user.show_activity = request.form.get("show_activity") == "on"
    db.session.commit()
    flash("Privacy settings updated.", "success")
    return redirect(url_for("settings.settings"))


@bp.post("/settings/delete-account")
@require_login
def delete_account():
    from werkzeug.security import check_password_hash
    username = (request.form.get("delete_username") or "").strip()
    password = request.form.get("delete_password") or ""
    if username != g.user.username:
        flash("Type your username exactly to confirm deletion.", "error")
        return redirect(url_for("settings.settings"))
    if not check_password_hash(g.user.password_hash, password):
        flash("Your password is incorrect.", "error")
        return redirect(url_for("settings.settings"))
    user_id = g.user.id
    log.info("account_deleted", user_id=user_id)
    db.session.delete(g.user)
    db.session.commit()
    resp = redirect(url_for("main.index"))
    clear_session_cookie(resp)
    flash("Your account has been deleted. See you on the floor.", "info")
    return resp


# ── Playlist saves ─────────────────────────────────────────────────────

@bp.post("/playlists/<playlist_id>/save")
@require_login
def toggle_save_playlist(playlist_id):
    playlist = db.session.get(Playlist, playlist_id)
    if playlist is None:
        flash("Playlist not found.", "error")
        return redirect(url_for("dashboard.playlists"))
    saved = playlist.toggle_save(g.user.id)
    db.session.commit()
    if saved:
        flash("Playlist saved.", "success")
    else:
        flash("Playlist unsaved.", "info")
    return redirect(url_for("dashboard.playlist_view", playlist_id=playlist_id))


# ── Challenge submissions ──────────────────────────────────────────────

@bp.get("/challenges/<int:challenge_id>")
@require_login
def challenge_detail(challenge_id):
    challenge = db.session.get(Challenge, challenge_id)
    if challenge is None:
        flash("Challenge not found.", "error")
        return redirect(url_for("dashboard.challenges"))
    submissions = (
        Submission.query.filter_by(challenge_id=challenge_id)
        .order_by(Submission.created_at.desc()).limit(50).all()
    )
    user_sub = Submission.query.filter_by(
        challenge_id=challenge_id, user_id=g.user.id
    ).first()
    # Prefetch usernames
    user_ids = {s.user_id for s in submissions}
    users = {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}
    return render_template(
        "dashboard/challenge_detail.html",
        challenge=challenge,
        submissions=submissions,
        user_submission=user_sub,
        users=users,
    )


@bp.post("/challenges/<int:challenge_id>/submit")
@require_login
def submit_to_challenge(challenge_id):
    challenge = db.session.get(Challenge, challenge_id)
    if challenge is None:
        flash("Challenge not found.", "error")
        return redirect(url_for("dashboard.challenges"))
    if challenge.has_ended or challenge.status != "ACTIVE":
        flash("That challenge has ended.", "info")
        return redirect(url_for("dashboard.challenges"))
    # Check if already submitted
    existing = Submission.query.filter_by(
        challenge_id=challenge_id, user_id=g.user.id
    ).first()
    if existing:
        flash("You already submitted to this challenge.", "info")
        return redirect(url_for("settings.challenge_detail", challenge_id=challenge_id))
    url = (request.form.get("url") or "").strip()
    note = (request.form.get("note") or "").strip()[:150]
    if not url or not url.startswith("http"):
        flash("Give us a link (YouTube, Instagram, etc.).", "error")
        return redirect(url_for("settings.challenge_detail", challenge_id=challenge_id))
    sub = Submission(
        challenge_id=challenge_id,
        user_id=g.user.id,
        url=url[:200],
        note=note,
    )
    db.session.add(sub)
    db.session.commit()
    flash("Submission recorded — good luck!", "success")
    return redirect(url_for("settings.challenge_detail", challenge_id=challenge_id))


# ── Events ─────────────────────────────────────────────────────────────

@bp.get("/events")
@require_login
def events():
    now = utcnow()
    upcoming = (
        Event.query.filter(Event.starts_at >= now - timedelta(hours=2))
        .order_by(Event.starts_at.asc()).limit(30).all()
    )
    past = (
        Event.query.filter(Event.starts_at < now - timedelta(hours=2))
        .order_by(Event.starts_at.desc()).limit(10).all()
    )
    return render_template(
        "dashboard/events.html",
        upcoming=upcoming,
        past=past,
        is_admin=g.claims.get("is_admin"),
        is_teacher=g.claims.get("is_teacher"),
    )


@bp.post("/events")
@require_login
def create_event():
    if not g.claims.get("is_admin") and not g.claims.get("is_teacher"):
        flash("Only admins and teachers can create events.", "error")
        return redirect(url_for("settings.events"))
    title = (request.form.get("title") or "").strip()[:60]
    description = (request.form.get("description") or "").strip()[:300]
    location = (request.form.get("location") or "").strip()[:60]
    starts_raw = (request.form.get("starts_at") or "").strip()
    ends_raw = (request.form.get("ends_at") or "").strip()
    if not title:
        flash("Give the event a title.", "error")
        return redirect(url_for("settings.events"))
    if not starts_raw:
        flash("Set a start time.", "error")
        return redirect(url_for("settings.events"))
    from datetime import datetime, timezone as tz
    try:
        starts_at = datetime.fromisoformat(starts_raw)
        if starts_at.tzinfo is None:
            starts_at = starts_at.replace(tzinfo=tz.utc)
    except ValueError:
        flash("Invalid start time.", "error")
        return redirect(url_for("settings.events"))
    ends_at = None
    if ends_raw:
        try:
            ends_at = datetime.fromisoformat(ends_raw)
            if ends_at.tzinfo is None:
                ends_at = ends_at.replace(tzinfo=tz.utc)
        except ValueError:
            pass
    event = Event(
        creator_id=g.user.id,
        title=title,
        description=description,
        location=location,
        starts_at=starts_at,
        ends_at=ends_at,
    )
    db.session.add(event)
    db.session.commit()
    flash("Event created.", "success")
    return redirect(url_for("settings.events"))


@bp.post("/events/<int:event_id>/rsvp")
@require_login
def rsvp_event(event_id):
    event = db.session.get(Event, event_id)
    if event is None:
        flash("Event not found.", "error")
        return redirect(url_for("settings.events"))
    status = request.form.get("status")
    if status not in ("going", "maybe", "none"):
        status = None
    if status == "none":
        status = None
    event.set_rsvp(g.user.id, status)
    db.session.commit()
    if status:
        flash(f"RSVP'd as {status}.", "success")
    else:
        flash("RSVP removed.", "info")
    return redirect(url_for("settings.events"))


@bp.post("/events/<int:event_id>/delete")
@require_login
def delete_event(event_id):
    event = db.session.get(Event, event_id)
    if event is None:
        flash("Event not found.", "error")
        return redirect(url_for("settings.events"))
    if event.creator_id != g.user.id and not g.claims.get("is_admin"):
        flash("Only the creator or an admin can delete this.", "error")
        return redirect(url_for("settings.events"))
    db.session.delete(event)
    db.session.commit()
    flash("Event deleted.", "success")
    return redirect(url_for("settings.events"))


# ── Members directory ──────────────────────────────────────────────────

@bp.get("/members")
@require_login
def members():
    search = (request.args.get("q") or "").strip()
    style = (request.args.get("style") or "").strip()
    query = User.query
    if search:
        like = f"%{search}%"
        query = query.filter(User.username.ilike(like) | User.display_name.ilike(like))
    if style and style in DANCE_STYLES:
        # JSONB array contains — works on both Postgres and SQLite
        query = query.filter(User.dance_styles.contains(style))
    users = query.order_by(User.created_at.desc()).limit(100).all()
    return render_template(
        "dashboard/members.html",
        users=users,
        search=search,
        selected_style=style,
        dance_styles=DANCE_STYLES,
    )


# ── Leaderboard ────────────────────────────────────────────────────────

@bp.get("/leaderboard")
@require_login
def leaderboard():
    from sqlalchemy import func

    # Challenges created
    challenge_leaders = (
        db.session.query(User, func.count(Challenge.id).label("count"))
        .join(Challenge, Challenge.creator_id == User.id)
        .group_by(User.id).order_by(func.count(Challenge.id).desc()).limit(10).all()
    )
    # Playlists created
    playlist_leaders = (
        db.session.query(User, func.count(Playlist.id).label("count"))
        .join(Playlist, Playlist.creator_id == User.id)
        .group_by(User.id).order_by(func.count(Playlist.id).desc()).limit(10).all()
    )
    # Giveaways entered (across all giveaways)
    # This is expensive on raw data — approximate from giveaway entrant counts
    giveaway_winners = (
        db.session.query(User, func.count(Giveaway.id).label("count"))
        .join(Giveaway, Giveaway.creator_id == User.id)
        .group_by(User.id).order_by(func.count(Giveaway.id).desc()).limit(10).all()
    )
    return render_template(
        "dashboard/leaderboard.html",
        challenge_leaders=challenge_leaders,
        playlist_leaders=playlist_leaders,
        giveaway_leaders=giveaway_winners,
    )


# ── User profile ───────────────────────────────────────────────────────

@bp.get("/users/<int:user_id>")
@require_login
def user_profile(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        flash("That user doesn't exist.", "error")
        return redirect(url_for("dashboard.home"))
    is_own = user.id == g.user.id
    is_admin = g.claims.get("is_admin")
    if not user.profile_public and not is_own and not is_admin:
        flash("That profile is private.", "info")
        return redirect(url_for("dashboard.home"))
    return render_template(
        "dashboard/user_profile.html",
        profile_user=user,
        is_own=is_own,
        is_admin=is_admin,
    )
