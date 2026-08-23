"""Email/password authentication — JWT session tokens.

The session is a signed JWT in a cookie — no server-side session store.
Payload: ``user_id``, ``username``, ``email``, ``is_admin``, ``iat``/``exp``.
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
    user_id: int,
    username: str,
    email: str,
    is_admin: bool,
    is_teacher: bool = False,
) -> str:
    """Issue a signed JWT carrying the session claims."""
    ttl_hours = current_app.config["JWT_TTL_HOURS"]
    now = int(time.time())
    payload = {
        "user_id": int(user_id),
        "username": username,
        "email": email,
        "is_admin": bool(is_admin),
        "is_teacher": bool(is_teacher),
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
    if not isinstance(claims.get("user_id"), int):
        return None
    if not isinstance(claims.get("username"), str):
        return None
    claims["is_admin"] = bool(claims.get("is_admin", False))
    claims["is_teacher"] = bool(claims.get("is_teacher", False))
    return claims


def claims_from_request() -> dict | None:
    """Decode the session JWT from the current request's cookie."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    return decode_session_token(token)


def renewed_session_token(token: str, refresh_fraction: float = 0.25) -> str | None:
    """Re-issue a token when it has burned most of its lifetime.

    Returns a fresh token once less than ``refresh_fraction`` of the TTL
    remains (silent sliding expiry), or ``None`` when no refresh is due
    or the input token is invalid/expired.
    """
    claims = decode_session_token(token)
    if not claims:
        return None
    now = int(time.time())
    exp = int(claims.get("exp") or 0)
    iat = int(claims.get("iat") or exp)
    total = max(exp - iat, 0)
    if total <= 0:
        return None
    if (exp - now) > total * refresh_fraction:
        return None
    return create_session_token(
        claims["user_id"],
        claims["username"],
        claims["email"],
        claims["is_admin"],
        claims["is_teacher"],
    )


def set_session_cookie(response, token: str) -> None:
    """Attach the session JWT cookie to a response."""
    response.set_cookie(
        SESSION_COOKIE_NAME,
        value=token,
        httponly=current_app.config["SESSION_COOKIE_HTTPONLY"],
        secure=current_app.config["SESSION_COOKIE_SECURE"],
        samesite=current_app.config["SESSION_COOKIE_SAMESITE"],
        path="/",
    )


def clear_session_cookie(response) -> None:
    """Expire the session cookie (used on logout)."""
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
