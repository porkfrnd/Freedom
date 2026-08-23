"""Dashboard — the community hub.

Routes behind ``require_login``:
- Home — feed: latest announcements + active challenges + giveaways.
- Challenges — browse active/ended challenges (admin can create).
- Giveaways — enter giveaways, see winners (admin can create + draw).
- Announcements — read the news (admin can post).
- Playlists — shared music sets (creator/admin can edit).
"""

from __future__ import annotations

import random

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
    ANNOUNCEMENT_CATEGORIES,
    ANNOUNCEMENT_CONTENT_MAX,
    ANNOUNCEMENT_TITLE_MAX,
    CHALLENGE_DESCRIPTION_MAX,
    CHALLENGE_TITLE_MAX,
    GIVEAWAY_DESCRIPTION_MAX,
    GIVEAWAY_PRIZE_MAX,
    MAX_GIVEAWAY_ENTRANTS,
    PLAYLIST_NAME_MAX,
    Announcement,
    Challenge,
    Event,
    Giveaway,
    Playlist,
    Submission,
    User,
    format_uid,
    generate_playlist_id,
    parse_uid,
    utcnow,
)
from sqlalchemy.orm import joinedload
from utils.decorators import require_admin, require_login, require_teacher
from utils.logging import get_logger

log = get_logger("blueprints.dashboard")

bp = Blueprint("dashboard", __name__)


def _time_ago(dt, now):
    """Human-readable time difference: '2h ago', '3d ago', etc."""
    from models import _aware
    diff = now - _aware(dt)
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    days = seconds // 86400
    return f"{days}d ago"


# ── Shell ────────────────────────────────────────────────────────────────

@bp.get("/dashboard")
@require_login
def shell():
    return redirect(url_for("dashboard.home"))


# ── Home feed ────────────────────────────────────────────────────────────

@bp.get("/home")
@require_login
def home():
    from datetime import timedelta

    announcements = (
        Announcement.query.options(joinedload(Announcement.author))
        .order_by(Announcement.created_at.desc()).limit(8).all()
    )
    challenges = (
        Challenge.query.filter(Challenge.status == "ACTIVE")
        .order_by(Challenge.created_at.desc())
        .limit(6)
        .all()
    )
    giveaways = (
        Giveaway.query.filter(Giveaway.status == "ACTIVE")
        .order_by(Giveaway.created_at.desc())
        .limit(6)
        .all()
    )

    # ── Computed stats (0 storage cost) ─────────────────────────────────
    stats = {
        "members": User.query.count(),
        "active_challenges": Challenge.query.filter(Challenge.status == "ACTIVE").count(),
        "open_giveaways": Giveaway.query.filter(Giveaway.status == "ACTIVE").count(),
        "playlists": Playlist.query.filter(Playlist.is_public.is_(True)).count(),
    }

    # ── Computed activity feed (derived from existing timestamps) ────────
    activity = []
    now = utcnow()
    week_ago = now - timedelta(days=7)

    # New users this week
    new_users = User.query.filter(User.created_at >= week_ago).order_by(User.created_at.desc()).limit(5).all()
    for u in new_users:
        activity.append({
            "user_name": u.name, "avatar_bg": u.avatar_bg, "avatar_initial": u.avatar_initial,
            "action": "joined the community", "target": None, "url": None,
            "time_ago": _time_ago(u.created_at, now), "ts": u.created_at,
        })

    # Recent playlists
    recent_playlists = (
        Playlist.query.options(joinedload(Playlist.creator))
        .filter(Playlist.is_public.is_(True), Playlist.created_at >= week_ago)
        .order_by(Playlist.created_at.desc()).limit(5).all()
    )
    for p in recent_playlists:
        creator = p.creator
        activity.append({
            "user_name": creator.name if creator else "Someone",
            "avatar_bg": creator.avatar_bg if creator else "bg-violet/20 border-violet/30 text-violet",
            "avatar_initial": creator.avatar_initial if creator else "?",
            "action": "created a playlist", "target": p.name,
            "url": url_for("dashboard.playlist_view", playlist_id=p.id),
            "time_ago": _time_ago(p.created_at, now), "ts": p.created_at,
        })

    # Recent challenges
    recent_challenges = (
        Challenge.query.options(joinedload(Challenge.creator))
        .filter(Challenge.created_at >= week_ago)
        .order_by(Challenge.created_at.desc()).limit(5).all()
    )
    for c in recent_challenges:
        creator = c.creator
        activity.append({
            "user_name": creator.name if creator else "Someone",
            "avatar_bg": creator.avatar_bg if creator else "bg-violet/20 border-violet/30 text-violet",
            "avatar_initial": creator.avatar_initial if creator else "?",
            "action": "posted a challenge", "target": c.title,
            "url": url_for("settings.challenge_detail", challenge_id=c.id),
            "time_ago": _time_ago(c.created_at, now), "ts": c.created_at,
        })

    # Recent submissions
    recent_subs = (
        Submission.query.options(joinedload(Submission.user), joinedload(Submission.challenge))
        .filter(Submission.created_at >= week_ago)
        .order_by(Submission.created_at.desc()).limit(5).all()
    )
    for s in recent_subs:
        user = s.user
        challenge = s.challenge
        activity.append({
            "user_name": user.name if user else "Someone",
            "avatar_bg": user.avatar_bg if user else "bg-violet/20 border-violet/30 text-violet",
            "avatar_initial": user.avatar_initial if user else "?",
            "action": "submitted to", "target": challenge.title if challenge else "a challenge",
            "url": url_for("settings.challenge_detail", challenge_id=s.challenge_id),
            "time_ago": _time_ago(s.created_at, now), "ts": s.created_at,
        })

    # Sort by timestamp, take top 10
    activity.sort(key=lambda x: x["ts"], reverse=True)
    activity = activity[:10]

    # ── Upcoming events ─────────────────────────────────────────────────
    upcoming_events = (
        Event.query.filter(Event.starts_at >= now - timedelta(hours=2))
        .order_by(Event.starts_at.asc()).limit(3).all()
    )

    return render_template(
        "dashboard/home.html",
        announcements=announcements,
        challenges=challenges,
        giveaways=giveaways,
        my_user_id=g.user.id,
        stats=stats,
        activity=activity,
        upcoming_events=upcoming_events,
    )


# ── Announcements ────────────────────────────────────────────────────────

@bp.get("/announcements")
@require_login
def announcements():
    category = request.args.get("category", "").strip().upper()
    query = Announcement.query.options(joinedload(Announcement.author))
    if category in ANNOUNCEMENT_CATEGORIES:
        query = query.filter(Announcement.category == category)
    rows = query.order_by(Announcement.created_at.desc()).limit(50).all()
    return render_template(
        "dashboard/announcements.html",
        announcements=rows,
        categories=ANNOUNCEMENT_CATEGORIES,
        active_category=category,
        can_post=g.claims.get("is_admin"),
    )


@bp.post("/announcements")
@require_admin
def create_announcement():
    title = (request.form.get("title") or "").strip()
    content = (request.form.get("content") or "").strip()
    category = (request.form.get("category") or "GENERAL").strip().upper()
    if category not in ANNOUNCEMENT_CATEGORIES:
        category = "GENERAL"

    if not title:
        flash("Give the announcement a title.", "error")
        return redirect(url_for("dashboard.announcements"))
    if len(title) > ANNOUNCEMENT_TITLE_MAX:
        flash(f"Title is too long ({ANNOUNCEMENT_TITLE_MAX} characters max).", "error")
        return redirect(url_for("dashboard.announcements"))
    if not content:
        flash("Write something worth announcing.", "error")
        return redirect(url_for("dashboard.announcements"))
    if len(content) > ANNOUNCEMENT_CONTENT_MAX:
        flash(f"Announcement is too long ({ANNOUNCEMENT_CONTENT_MAX} characters max).", "error")
        return redirect(url_for("dashboard.announcements"))

    announcement = Announcement(
        author_id=g.user.id,
        title=title,
        content=content,
        category=category,
    )
    db.session.add(announcement)
    db.session.commit()
    log.info("announcement_created", announcement_id=announcement.id, category=category)
    flash("Announcement posted.", "success")
    return redirect(url_for("dashboard.announcements"))


# ── Challenges ───────────────────────────────────────────────────────────

@bp.get("/challenges")
@require_login
def challenges():
    from models import Submission
    rows = Challenge.query.options(joinedload(Challenge.creator)).order_by(Challenge.created_at.desc()).limit(50).all()
    can_post = g.claims.get("is_admin") or g.claims.get("is_teacher")
    # Prefetch submission counts
    sub_counts = {}
    if rows:
        from sqlalchemy import func
        counts = (
            db.session.query(Submission.challenge_id, func.count(Submission.id))
            .filter(Submission.challenge_id.in_([c.id for c in rows]))
            .group_by(Submission.challenge_id).all()
        )
        sub_counts = dict(counts)
    return render_template(
        "dashboard/challenges.html",
        challenges=rows,
        can_post=can_post,
        sub_counts=sub_counts,
    )


@bp.post("/challenges")
@require_teacher
def create_challenge():
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    deadline_raw = (request.form.get("deadline") or "").strip()

    if not title:
        flash("Give the challenge a title.", "error")
        return redirect(url_for("dashboard.challenges"))
    if len(title) > CHALLENGE_TITLE_MAX:
        flash(f"Title is too long ({CHALLENGE_TITLE_MAX} characters max).", "error")
        return redirect(url_for("dashboard.challenges"))
    if not description:
        flash("Describe the challenge.", "error")
        return redirect(url_for("dashboard.challenges"))
    if len(description) > CHALLENGE_DESCRIPTION_MAX:
        flash(f"Description is too long ({CHALLENGE_DESCRIPTION_MAX} characters max).", "error")
        return redirect(url_for("dashboard.challenges"))

    deadline = None
    if deadline_raw:
        try:
            from datetime import datetime, timezone
            deadline = datetime.fromisoformat(deadline_raw)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
        except ValueError:
            flash("That deadline doesn't parse. Use the date picker.", "error")
            return redirect(url_for("dashboard.challenges"))

    challenge = Challenge(
        creator_id=g.user.id,
        title=title,
        description=description,
        deadline=deadline,
        status="ACTIVE",
    )
    db.session.add(challenge)
    db.session.commit()
    log.info("challenge_created", challenge_id=challenge.id)
    flash("Challenge posted.", "success")
    return redirect(url_for("dashboard.challenges"))


@bp.post("/challenges/<int:challenge_id>/close")
@require_admin
def close_challenge(challenge_id):
    challenge = db.session.get(Challenge, challenge_id)
    if challenge is None:
        flash("That challenge doesn't exist.", "error")
        return redirect(url_for("dashboard.challenges"))
    challenge.status = "ENDED"
    db.session.commit()
    flash("Challenge closed.", "success")
    return redirect(url_for("dashboard.challenges"))


# ── Giveaways ────────────────────────────────────────────────────────────

@bp.get("/giveaways")
@require_login
def giveaways():
    rows = Giveaway.query.order_by(Giveaway.created_at.desc()).limit(50).all()
    # Winner ids → display names (for the "Winners:" list).
    winner_ids = {w for g in rows for w in (g.winners or [])}
    names = {}
    if winner_ids:
        for uid, uname in User.query.with_entities(User.id, User.display_name).filter(User.id.in_(winner_ids)).all():
            names[uid] = uname or None
    return render_template(
        "dashboard/giveaways.html",
        giveaways=rows,
        winner_names=names,
        my_user_id=g.user.id,
        can_post=g.claims.get("is_admin"),
    )


@bp.post("/giveaways")
@require_admin
def create_giveaway():
    prize = (request.form.get("prize") or "").strip()
    description = (request.form.get("description") or "").strip()
    deadline_raw = (request.form.get("deadline") or "").strip()
    num_winners_raw = (request.form.get("num_winners") or "1").strip()

    if not prize:
        flash("Give the prize a name.", "error")
        return redirect(url_for("dashboard.giveaways"))
    if len(prize) > GIVEAWAY_PRIZE_MAX:
        flash(f"Prize name is too long ({GIVEAWAY_PRIZE_MAX} characters max).", "error")
        return redirect(url_for("dashboard.giveaways"))

    try:
        num_winners = int(num_winners_raw)
    except ValueError:
        num_winners = 0
    if num_winners < 1 or num_winners > 20:
        flash("Number of winners must be between 1 and 20.", "error")
        return redirect(url_for("dashboard.giveaways"))

    deadline = None
    if deadline_raw:
        try:
            from datetime import datetime, timezone
            deadline = datetime.fromisoformat(deadline_raw)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
        except ValueError:
            flash("That deadline doesn't parse. Use the date picker.", "error")
            return redirect(url_for("dashboard.giveaways"))

    giveaway = Giveaway(
        creator_id=g.user.id,
        prize=prize,
        description=description[:GIVEAWAY_DESCRIPTION_MAX] or None,
        deadline=deadline,
        num_winners=num_winners,
        status="ACTIVE",
    )
    db.session.add(giveaway)
    db.session.commit()
    log.info("giveaway_created", giveaway_id=giveaway.id)
    flash("Giveaway posted.", "success")
    return redirect(url_for("dashboard.giveaways"))


@bp.post("/giveaways/<int:giveaway_id>/enter")
@require_login
def enter_giveaway(giveaway_id):
    giveaway = db.session.get(Giveaway, giveaway_id)
    if giveaway is None:
        flash("That giveaway doesn't exist.", "error")
        return redirect(url_for("dashboard.giveaways"))
    if giveaway.status != "ACTIVE":
        flash("That giveaway has already ended.", "info")
        return redirect(url_for("dashboard.giveaways"))
    if giveaway.has_ended:
        giveaway.status = "ENDED"
        db.session.commit()
        flash("That giveaway has already ended.", "info")
        return redirect(url_for("dashboard.giveaways"))
    if not giveaway.has_entered(g.user.id) and len(giveaway.entrants or []) >= MAX_GIVEAWAY_ENTRANTS:
        flash("That giveaway is full — all entries are taken.", "warning")
        return redirect(url_for("dashboard.giveaways"))
    if not giveaway.add_entrant(g.user.id):
        flash("You're already in this one — one entry per person.", "info")
        return redirect(url_for("dashboard.giveaways"))
    db.session.commit()
    log.info("giveaway_entry", giveaway_id=giveaway.id, user_id=g.user.id)
    flash("You're in. Good luck!", "success")
    return redirect(url_for("dashboard.giveaways"))


@bp.post("/giveaways/<int:giveaway_id>/draw")
@require_admin
def draw_giveaway(giveaway_id):
    giveaway = db.session.get(Giveaway, giveaway_id)
    if giveaway is None:
        flash("That giveaway doesn't exist.", "error")
        return redirect(url_for("dashboard.giveaways"))
    if giveaway.status == "ACTIVE" and not giveaway.has_ended:
        flash("The giveaway is still running — close it first.", "warning")
        return redirect(url_for("dashboard.giveaways"))
    if giveaway.winners:
        flash("Winners were already drawn for this one.", "info")
        return redirect(url_for("dashboard.giveaways"))

    entrants = list(giveaway.entrants or [])
    if not entrants:
        flash("No one entered — nothing to draw.", "warning")
        return redirect(url_for("dashboard.giveaways"))

    giveaway.status = "ENDED"
    winners = random.sample(entrants, min(giveaway.num_winners, len(entrants)))
    giveaway.winners = winners
    db.session.commit()
    log.info("giveaway_drawn", giveaway_id=giveaway.id, winner_count=len(winners))
    flash(f"Winners drawn — {len(winners)} lucky name(s) pulled.", "success")
    return redirect(url_for("dashboard.giveaways"))


# ── Admin: user management ───────────────────────────────────────────

@bp.get("/admin/users")
@require_admin
def admin_users():
    search_uid = request.args.get("uid", "").strip()
    found_user = None
    if search_uid:
        uid_int = parse_uid(search_uid)
        if uid_int is not None:
            found_user = db.session.get(User, uid_int)
    users = User.query.order_by(User.created_at.desc()).limit(100).all()
    return render_template(
        "dashboard/admin_users.html",
        users=users,
        found_user=found_user,
        search_uid=search_uid,
        my_user_id=g.user.id,
        format_uid=format_uid,
    )


@bp.post("/admin/users/<int:user_id>/promote-teacher")
@require_admin
def promote_teacher(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        flash("That user doesn't exist.", "error")
        return redirect(url_for("dashboard.admin_users"))
    if user.is_admin:
        flash("That user is already an admin.", "info")
        return redirect(url_for("dashboard.admin_users"))
    user.is_teacher = True
    db.session.commit()
    log.info("teacher_promoted", user_id=user.id)
    flash(f"{user.name} is now a teacher.", "success")
    return redirect(url_for("dashboard.admin_users"))


@bp.post("/admin/users/<int:user_id>/demote-teacher")
@require_admin
def demote_teacher(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        flash("That user doesn't exist.", "error")
        return redirect(url_for("dashboard.admin_users"))
    user.is_teacher = False
    db.session.commit()
    log.info("teacher_demoted", user_id=user.id)
    flash(f"{user.name} is no longer a teacher.", "success")
    return redirect(url_for("dashboard.admin_users"))


@bp.post("/admin/users/<int:user_id>/toggle-admin")
@require_admin
def toggle_admin(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        flash("That user doesn't exist.", "error")
        return redirect(url_for("dashboard.admin_users"))
    if user.id == g.user.id:
        flash("You can't change your own admin status.", "error")
        return redirect(url_for("dashboard.admin_users"))
    user.is_admin = not user.is_admin
    db.session.commit()
    log.info("admin_toggled", user_id=user.id, is_admin=user.is_admin)
    flash(f"{user.name} {'is now an admin.' if user.is_admin else 'is no longer an admin.'}", "success")
    return redirect(url_for("dashboard.admin_users"))


# ── Playlists ────────────────────────────────────────────────────────────

@bp.get("/playlists")
@require_login
def playlists():
    mine = request.args.get("mine") == "1"

    query = Playlist.query.options(joinedload(Playlist.creator))
    if mine:
        query = query.filter(Playlist.creator_id == g.user.id)
    else:
        query = query.filter(Playlist.is_public.is_(True))

    rows = query.order_by(Playlist.updated_at.desc()).all()

    return render_template(
        "dashboard/playlists.html",
        playlists=rows,
        mine=mine,
    )


@bp.post("/playlists")
@require_login
def create_playlist():
    """Create a playlist from the form, then jump straight into editing it."""
    name = (request.form.get("name") or "").strip()
    is_public = request.form.get("is_public") == "on"

    if not name:
        flash("Give the playlist a name.", "error")
        return redirect(url_for("dashboard.playlists"))
    if len(name) > PLAYLIST_NAME_MAX:
        flash(f"Playlist name is too long ({PLAYLIST_NAME_MAX} characters max).", "error")
        return redirect(url_for("dashboard.playlists"))

    playlist = Playlist(
        id=generate_playlist_id(),
        creator_id=g.user.id,
        name=name,
        tracks=[],
        is_public=is_public,
    )
    db.session.add(playlist)
    db.session.commit()
    log.info("playlist_created", playlist_id=playlist.id, by=g.user.id)
    flash("Playlist created — add some tracks.", "success")
    return redirect(url_for("dashboard.playlist_view", playlist_id=playlist.id))


@bp.get("/playlists/<playlist_id>")
@require_login
def playlist_view(playlist_id):
    """Show a playlist. Anyone public can view; owner gets edit controls."""
    playlist = db.session.get(Playlist, playlist_id)
    if playlist is None:
        flash("That playlist ID doesn't exist.", "error")
        return redirect(url_for("dashboard.playlists"))

    # Private playlists: only the creator or an admin can see them.
    is_owner = playlist.creator_id == g.user.id
    if not playlist.is_public and not is_owner and not g.claims.get("is_admin"):
        flash("That playlist is private.", "error")
        return redirect(url_for("dashboard.playlists"))

    return render_template("dashboard/playlist_view.html", playlist=playlist, is_owner=is_owner)


@bp.get("/playlists/<playlist_id>/edit")
@require_login
def playlist_editor_redirect(playlist_id):
    """Legacy redirect — old links go straight to the view."""
    return redirect(url_for("dashboard.playlist_view", playlist_id=playlist_id))



