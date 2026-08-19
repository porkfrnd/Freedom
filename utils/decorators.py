"""Route guards — simplified for email/password auth.

* ``require_login`` — a valid, non-expired JWT session.
* ``require_admin`` — a valid session where ``is_admin`` is True.
"""

from __future__ import annotations

import functools

from flask import flash, g, jsonify, redirect, render_template, request, url_for

from extensions import db
from models import User
from services.auth import claims_from_request, clear_session_cookie
from utils.logging import get_logger

log = get_logger("utils.decorators")


def _load_user(claims: dict) -> User | None:
    """Fetch the user row (cached on ``g``), or None if it doesn't exist."""
    if "user" not in g:
        g.user = db.session.get(User, claims["user_id"])
    return g.user


def require_login(view):
    """Require a valid JWT; redirect to login otherwise."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        claims = claims_from_request()
        if claims is None:
            # JSON endpoints return 401; form endpoints redirect.
            if request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json":
                return jsonify({"error": {"code": "unauthenticated", "message": "Sign in to do that."}}), 401
            flash("Sign in to keep going.", "info")
            return redirect(url_for("auth.login"))
        user = _load_user(claims)
        if user is None:
            # Session references a user that no longer exists.
            resp = redirect(url_for("auth.login"))
            clear_session_cookie(resp)
            return resp
        g.claims = claims
        g.user = user
        return view(*args, **kwargs)

    return wrapper


def require_admin(view):
    """Require a valid session AND is_admin."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        claims = claims_from_request()
        if claims is None:
            flash("Sign in to keep going.", "info")
            return redirect(url_for("auth.login"))
        if not claims.get("is_admin"):
            return render_template("errors/403.html"), 403
        user = _load_user(claims)
        if user is None:
            resp = redirect(url_for("auth.login"))
            clear_session_cookie(resp)
            return resp
        g.claims = claims
        g.user = user
        return view(*args, **kwargs)

    return wrapper


def require_teacher(view):
    """Require a valid session AND (is_admin OR is_teacher)."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        claims = claims_from_request()
        if claims is None:
            flash("Sign in to keep going.", "info")
            return redirect(url_for("auth.login"))
        if not claims.get("is_admin") and not claims.get("is_teacher"):
            return render_template("errors/403.html"), 403
        user = _load_user(claims)
        if user is None:
            resp = redirect(url_for("auth.login"))
            clear_session_cookie(resp)
            return resp
        g.claims = claims
        g.user = user
        return view(*args, **kwargs)

    return wrapper
