"""JWT session tokens (§3.2).

The session is a signed JWT in an httponly/secure/SameSite=Lax cookie —
there is no server-side session store. Payload: ``discord_id``,
``username``, ``avatar_hash``, ``is_admin``, ``guild_member``,
``session_version``, ``iat``/``exp`` (12h by default).

Important: a JWT claim is a *hint*, not proof. Every privileged action
re-verifies membership and the ADMINISTRATOR bit live via
``services.discord_api`` (or a ≤5-minute cache); see ``utils/decorators.py``.
"""

from __future__ import annotations

import time
from datetime import timedelta

import jwt
from flask import current_app, request

from utils.logging import get_logger

log = get_logger("services.auth")

SESSION_COOKIE_NAME = "ffd_session"


def create_session_token(
    discord_id: int,
    username: str,
    avatar_hash: str | None,
    is_admin: bool,
    guild_member: bool,
    session_version: int = 1,
) -> str:
    """Issue a signed JWT carrying the session claims."""
    ttl_hours = current_app.config["JWT_TTL_HOURS"]
    now = int(time.time())
    payload = {
        "discord_id": int(discord_id),
        "username": username,
        "avatar_hash": avatar_hash,
        "is_admin": bool(is_admin),
        "guild_member": bool(guild_member),
        "session_version": int(session_version),
        "iat": now,
        "exp": now + int(timedelta(hours=ttl_hours).total_seconds()),
    }
    return jwt.encode(payload, current_app.config["SECRET_KEY"], algorithm="HS256")


def decode_session_token(token: str | None) -> dict | None:
    """Decode and verify a token; None when invalid or expired."""
    if not token:
        return None
    try:
        claims = jwt.decode(token, current_app.config["SECRET_KEY"], algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    # Hard type guards — a forged cookie must not smuggle weird types in.
    if not isinstance(claims.get("discord_id"), int):
        return None
    if not isinstance(claims.get("username"), str):
        return None
    claims["guild_member"] = bool(claims.get("guild_member", False))
    claims["is_admin"] = bool(claims.get("is_admin", False))
    return claims


def claims_from_request() -> dict | None:
    """Decode the session JWT from the current request's cookie."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    return decode_session_token(token)


def should_refresh(claims: dict) -> bool:
    """True when the token is valid but near expiry (silent refresh)."""
    refresh_after = current_app.config["JWT_REFRESH_AFTER_HOURS"]
    remaining = claims.get("exp", 0) - time.time()
    return remaining < refresh_after * 3600


def build_refresh_token(claims: dict) -> str:
    """Re-issue the session with a fresh expiry, preserving current claims."""
    return create_session_token(
        discord_id=claims["discord_id"],
        username=claims["username"],
        avatar_hash=claims.get("avatar_hash"),
        is_admin=claims.get("is_admin", False),
        guild_member=claims.get("guild_member", False),
        session_version=claims.get("session_version", 1),
    )


def set_session_cookie(response, token: str) -> None:
    """Attach the session JWT cookie to a response.

    The cookie is a SESSION cookie (no ``Expires``/``Max-Age``) so it is
    stored identically to Flask's own session cookie — browsers and privacy
    extensions that drop persistent (Expires-based) cookies keep session
    cookies, and some of them refuse the former entirely. The JWT inside
    still expires after ``JWT_TTL_HOURS`` and is silently refreshed on
    activity, so cookie persistence does not extend session validity.
    """
    response.set_cookie(
        SESSION_COOKIE_NAME,
        value=token,
        httponly=current_app.config["SESSION_COOKIE_HTTPONLY"],
        secure=current_app.config["SESSION_COOKIE_SECURE"],
        samesite=current_app.config["SESSION_COOKIE_SAMESITE"],
        path="/",
    )


def clear_session_cookie(response) -> None:
    """Expire the session cookie (used on logout and session invalidation)."""
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
