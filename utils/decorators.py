"""Route guards (§3.2, §7.2, §7.3).

* ``require_login`` — a valid, non-expired JWT that matches the user's
  current ``session_version`` (so background invalidations take effect).
* ``require_guild_member`` — the cookie claims membership; read-only views
  may trust this hint per §3.2.
* ``require_admin`` — ALWAYS re-verifies guild membership and the
  ADMINISTRATOR bit live via the Discord API (or a ≤5-minute cache) before
  letting the request through. A stale cookie that claims admin but fails
  the live check gets its session invalidated and is blocked.

The guards that render pages return redirects/flashes or error pages; the
API variants return JSON with the same semantics.
"""

from __future__ import annotations

import functools

from flask import current_app, flash, g, jsonify, make_response, redirect, render_template, url_for

from extensions import db
from models import User
from services import discord_api
from services.auth import (
    claims_from_request,
    clear_session_cookie,
    set_session_cookie,
)
from utils.logging import get_logger

log = get_logger("utils.decorators")


def _invalidate(response) -> None:
    clear_session_cookie(response)


def _load_user(claims: dict) -> User | None:
    """Fetch the user row (cached on ``g``), or None if it doesn't exist."""
    if "user" not in g:
        g.user = db.session.get(User, claims["discord_id"])
    return g.user


def require_login(view):
    """Require a valid JWT; redirect to the landing page otherwise."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        claims = claims_from_request()
        if claims is None:
            flash("Sign in with Discord to keep going.", "info")
            return redirect(url_for("main.index"))

        if not _session_matches_version(claims):
            # A background membership check invalidated this session.
            log.info("session_invalidated_version_mismatch", discord_id=claims["discord_id"])
            return _invalidate_and_redirect(
                "Your session ended because your Discord membership changed. "
                "Join the server, then come back."
            )

        g.claims = claims
        return view(*args, **kwargs)

    return wrapper


def _invalidate_and_redirect(message: str):
    """Redirect to the landing page with a flashed message + cookie clear."""
    flash(message, "info")
    resp = redirect(url_for("main.index"))
    _invalidate(resp)
    return resp


def require_guild_member(view):
    """Require a valid JWT whose cookie claims guild membership.

    Read-only views trust the cookie claim per §3.2 (live re-verification
    is reserved for privileged actions via ``require_admin``) — but a
    session whose ``session_version`` no longer matches (i.e. the
    background membership check found the user left) is invalidated here
    too, so stale memberships die on the next page load.
    """

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        claims = claims_from_request()
        if claims is None:
            return redirect(url_for("main.index"))
        if not _session_matches_version(claims):
            return _invalidate_and_redirect(
                "Your session ended because your Discord membership changed. "
                "Join the server, then come back."
            )
        if not claims.get("guild_member"):
            return render_template("not_member.html"), 403
        g.claims = claims
        return view(*args, **kwargs)

    return wrapper


def require_api_user(view):
    """JSON variant of ``require_login`` + ``require_guild_member``."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        claims = claims_from_request()
        if claims is None:
            return (
                jsonify(
                    {"error": {"code": "unauthenticated", "message": "Sign in to do that."}}
                ),
                401,
            )
        if not _session_matches_version(claims):
            return (
                jsonify(
                    {
                        "error": {
                            "code": "session_invalidated",
                            "message": "Your session ended because your Discord membership changed. Sign in again.",
                        }
                    }
                ),
                401,
            )
        if not claims.get("guild_member"):
            return (
                jsonify(
                    {
                        "error": {
                            "code": "not_a_member",
                            "message": "You'll need to be a member of the Freedom for Dance Discord to see this.",
                        }
                    }
                ),
                403,
            )
        g.claims = claims
        return view(*args, **kwargs)

    return wrapper


def _session_matches_version(claims: dict) -> bool:
    """True when the JWT's session_version still matches the user row.

    The background membership check bumps ``session_version`` when a user's
    Discord state changes, which instantly invalidates old JWTs (§3.2).
    """
    user = _load_user(claims)
    if user is None:
        return True  # no row → nothing to compare; other guards decide
    return claims.get("session_version", 1) == user.session_version


def require_admin(view):
    """Require a valid session AND a live-verified ADMINISTRATOR bit.

    This is the §3.2 enforcement point: the cookie claim is never trusted
    on its own. If the live check cannot be completed (Discord API down)
    the request fails closed.
    """

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        claims = claims_from_request()
        if claims is None:
            flash("Sign in with Discord to keep going.", "info")
            return redirect(url_for("main.index"))
        if not claims.get("guild_member"):
            return render_template("not_member.html"), 403

        guild_id = current_app.config["DISCORD_GUILD_ID"]
        if not guild_id:
            log.error("admin_guard_no_guild_id")
            return render_template("errors/503.html"), 503

        is_member, is_admin = discord_api.verify_membership_and_admin(
            guild_id, claims["discord_id"], current_app.config
        )

        if is_member is None or (is_member is True and is_admin is None):
            # Live verification failed — fail closed.
            log.warning(
                "admin_guard_live_check_failed",
                discord_id=claims["discord_id"],
                is_member=is_member,
                is_admin=is_admin,
            )
            return (
                render_template(
                    "errors/503.html",
                    message=(
                        "Couldn't verify your permissions with Discord right now. "
                        "Give it a moment and try again.",
                    ),
                ),
                503,
            )

        if is_member is False:
            # User left the guild — invalidate the session immediately (§3.2).
            log.info("admin_guard_membership_lost", discord_id=claims["discord_id"])
            resp = redirect(url_for("main.index"))
            _invalidate(resp)
            flash(
                "Your session ended because you're no longer a member of the server. "
                "Join the server, then come back.",
                "info",
            )
            return resp

        if not is_admin:
            # Cookie claimed admin but the live check says otherwise.
            log.warning(
                "admin_guard_stale_admin_claim",
                discord_id=claims["discord_id"],
            )
            user = _load_user(claims)
            if user is not None and user.is_admin:
                user.is_admin = False
                user.session_version += 1
                db.session.commit()
            response = make_response(render_template("errors/403.html"))
            response.status_code = 403
            _invalidate(response)
            return response

        # Live check passed — align the DB + cookie claim with reality.
        user = _load_user(claims)
        if user is not None and not user.is_admin:
            user.is_admin = True
            db.session.commit()
        g.claims = claims
        return view(*args, **kwargs)

    return wrapper


def require_api_admin(view):
    """JSON variant of ``require_admin`` for privileged API writes."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        claims = claims_from_request()
        if claims is None:
            return jsonify({"error": {"code": "unauthenticated", "message": "Sign in to do that."}}), 401
        if not claims.get("guild_member"):
            return jsonify({"error": {"code": "not_a_member", "message": "Membership required."}}), 403

        guild_id = current_app.config["DISCORD_GUILD_ID"]
        if not guild_id:
            return jsonify({"error": {"code": "server_not_configured", "message": "Guild ID is not configured."}}), 503

        is_member, is_admin = discord_api.verify_membership_and_admin(
            guild_id, claims["discord_id"], current_app.config
        )
        if is_member is None or (is_member is True and is_admin is None):
            return jsonify({"error": {"code": "verification_unavailable", "message": "Couldn't verify your permissions right now."}}), 503
        if is_member is False or not is_admin:
            resp = jsonify({"error": {"code": "forbidden", "message": "You don't have permission to do that."}}), 403
            clear_session_cookie(resp[0])
            return resp
        g.claims = claims
        return view(*args, **kwargs)

    return wrapper
