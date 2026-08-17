"""Discord OAuth2 (§7.1).

Authorization-code flow via Authlib:
1. ``/auth/login`` redirects to Discord's authorize endpoint.
2. ``/auth/callback`` exchanges the code, fetches ``/users/@me`` and
   ``/users/@me/guilds``, confirms membership in the target guild,
   computes ``is_admin`` from the ADMINISTRATOR permission bit, upserts
   the User row and issues the JWT session cookie (§3.2).

The JWT claims are hints for read-only views; ``require_admin`` re-verifies
live before any privileged action.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    current_app,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)

from extensions import db, oauth
from models import User, utcnow
from services import discord_api
from services.auth import create_session_token, set_session_cookie
from utils.logging import get_logger

log = get_logger("blueprints.auth")

bp = Blueprint("auth", __name__)


@bp.get("/auth/login")
def login():
    from services.auth import clear_session_cookie

    cfg = current_app.config
    if not cfg.get("DISCORD_CLIENT_ID") or not cfg.get("DISCORD_CLIENT_SECRET"):
        flash("Discord login isn't configured yet — the app owner needs to set the OAuth credentials.", "error")
        return redirect(url_for("main.index"))
    # Build the callback URI from BASE_URL (not the request host) so login
    # works whether the app is reached via localhost, 127.0.0.1, or the
    # Render domain — matching the URI registered in the Discord app.
    redirect_uri = current_app.config["BASE_URL"] + url_for("auth.callback")
    try:
        resp = oauth.discord.authorize_redirect(redirect_uri)
    except Exception as exc:  # noqa: BLE001 - surface a clear error, never a 500
        log.error("oauth_authorize_failed", error=str(exc))
        flash("Couldn't reach Discord to start sign-in. Try again in a moment.", "error")
        return redirect(url_for("main.index"))
    # Enter the OAuth round-trip with a clean jar: drop any stale session
    # cookie from earlier builds so it can't shadow the fresh one.
    clear_session_cookie(resp)
    return resp


@bp.get("/auth/callback")
def callback():
    cfg = current_app.config
    try:
        token = oauth.discord.authorize_access_token()
    except Exception as exc:  # noqa: BLE001 - e.g. user denied consent
        log.warning("oauth_callback_token_failed", error=str(exc))
        flash("Sign-in was cancelled or didn't complete. Try again.", "info")
        return redirect(url_for("main.index"))

    access_token = token.get("access_token")
    if not access_token:
        flash("Sign-in didn't return a session token. Try again.", "error")
        return redirect(url_for("main.index"))

    profile = discord_api.fetch_current_user(access_token, cfg)
    if profile is None:
        flash("Discord didn't confirm your identity. Try again in a moment.", "error")
        return redirect(url_for("main.index"))

    guilds = discord_api.fetch_user_guilds(access_token, cfg)
    is_member, is_admin = discord_api.membership_from_guilds(
        guilds, cfg.get("DISCORD_GUILD_ID", "")
    )

    discord_id = int(profile["id"])
    username = profile.get("username") or "Unknown Dancer"
    avatar_hash = profile.get("avatar")

    user = db.session.get(User, discord_id)
    if user is None:
        user = User(
            discord_id=discord_id,
            username=username,
            avatar_hash=avatar_hash,
            is_admin=is_admin,
            first_seen_at=utcnow(),
            last_seen_at=utcnow(),
        )
        db.session.add(user)
    else:
        user.username = username
        user.avatar_hash = avatar_hash
        user.is_admin = is_admin
        user.touch()
    db.session.commit()

    session_version = user.session_version
    token_jwt = create_session_token(
        discord_id=discord_id,
        username=username,
        avatar_hash=avatar_hash,
        is_admin=is_admin,
        guild_member=is_member,
        session_version=session_version,
    )

    if is_member:
        log.info("user_logged_in", discord_id=discord_id, is_admin=is_admin)
        next_url = url_for("dashboard.playlists")
    else:
        log.info("user_logged_in_not_member", discord_id=discord_id)
        next_url = url_for("main.not_member")

    # Serve a same-origin 200 "signing you in…" page instead of a bare 302.
    # The callback arrives via a CROSS-SITE navigation (Discord → us), and
    # some browsers (strict tracking protection, redirect-chain cookie
    # handling) drop Set-Cookie on a 302 inside that chain. A 200 response
    # to our own origin stores the session as a first-party cookie; the
    # page then bounces to the dashboard (templates/auth/redirecting.html).
    resp = make_response(
        render_template("auth/redirecting.html", next_url=next_url)
    )
    set_session_cookie(resp, token_jwt)
    log.info(
        "callback_cookies_set",
        next_url=next_url,
        set_cookies=[h for h in resp.headers.getlist("Set-Cookie")],
    )
    return resp


@bp.get("/auth/cookie-echo")
def cookie_echo():
    """Diagnostic: reports exactly which cookies the browser sent on this
    request. Used by the signing-in page to detect a cookie that didn't
    stick (blocked cookies, privacy extensions) and show explicit guidance
    instead of a silent bounce back to the landing page."""
    from services.auth import SESSION_COOKIE_NAME

    return {
        "cookies": sorted(request.cookies.keys()),
        "session_present": SESSION_COOKIE_NAME in request.cookies,
        "user_agent": request.headers.get("User-Agent", "")[:120],
    }


@bp.get("/auth/logout")
def logout():
    from services.auth import clear_session_cookie

    resp = redirect(url_for("main.index"))
    clear_session_cookie(resp)
    flash("Signed out. See you on the floor.", "info")
    return resp
