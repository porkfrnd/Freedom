"""Email/password authentication.

Routes:
- ``GET  /auth/register``  — registration form
- ``POST /auth/register``  — create account
- ``GET  /auth/login``     — login form
- ``POST /auth/login``     — authenticate, set session cookie
- ``GET  /auth/logout``    — clear session
"""

from __future__ import annotations

import re

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from extensions import db
from models import User, USERNAME_MAX, generate_user_id
from services.auth import (
    clear_session_cookie,
    create_session_token,
    set_session_cookie,
)
from utils.logging import get_logger

log = get_logger("blueprints.auth")

bp = Blueprint("auth", __name__)

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def _hash_password(password: str) -> str:
    """Hash a password with werkzeug's pbkdf2_sha256 (always available in Flask)."""
    from werkzeug.security import generate_password_hash
    return generate_password_hash(password, method="pbkdf2:sha256", salt_length=16)


def _check_password(password: str, password_hash: str) -> bool:
    """Verify a password against its hash."""
    from werkzeug.security import check_password_hash
    return check_password_hash(password_hash, password)


# ── Registration ─────────────────────────────────────────────────────────

@bp.get("/auth/register")
def register():
    return render_template("auth/register.html")


@bp.post("/auth/register")
def register_post():
    username = (request.form.get("username") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    confirm = request.form.get("confirm_password") or ""

    # ── Validate
    errors = []
    if not username or len(username) < 2:
        errors.append("Pick a name people will recognise you by (at least 2 characters).")
    if len(username) > USERNAME_MAX:
        errors.append(f"That name is too long — {USERNAME_MAX} characters max.")
    if not email or not _EMAIL_RE.match(email):
        errors.append("That email address doesn't look right.")
    if len(password) < 8:
        errors.append("Your password needs at least 8 characters.")
    if password != confirm:
        errors.append("The two passwords don't match.")

    if not errors:
        existing = User.query.filter((User.email == email) | (User.username == username)).first()
        if existing:
            if existing.email == email:
                errors.append("An account with that email already exists.")
            else:
                errors.append("That username is taken — try another.")

    if errors:
        for msg in errors:
            flash(msg, "error")
        return render_template("auth/register.html", username=username, email=email)

    # ── Create
    user = User(
        id=generate_user_id(),
        email=email,
        username=username,
        password_hash=_hash_password(password),
    )
    db.session.add(user)
    db.session.commit()

    log.info("user_registered", user_id=user.id, email=email)
    token = create_session_token(user.id, user.username, user.email, user.is_admin, user.is_teacher)
    resp = redirect(url_for("settings.onboarding") if not user.onboarded else url_for("dashboard.home"))
    set_session_cookie(resp, token)
    flash("Welcome to the floor — you're in.", "success")
    return resp


# ── Login ────────────────────────────────────────────────────────────────

@bp.get("/auth/login")
def login():
    return render_template("auth/login.html")


@bp.post("/auth/login")
def login_post():
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    if not email or not password:
        flash("Enter your email and password.", "error")
        return render_template("auth/login.html", email=email)

    limiter = current_app.extensions["ffd_login_limiter"]
    if not limiter.allow(f"{request.remote_addr}:{email}"):
        log.info("login_rate_limited", email=email)
        flash("Too many sign-in attempts — wait a few minutes and try again.", "error")
        return render_template("auth/login.html", email=email)

    user = User.query.filter_by(email=email).first()
    if user is None or not _check_password(password, user.password_hash):
        log.info("login_failed", email=email)
        flash("Wrong email or password — try again.", "error")
        return render_template("auth/login.html", email=email)

    user.touch()
    db.session.commit()

    token = create_session_token(user.id, user.username, user.email, user.is_admin, user.is_teacher)
    dest = url_for("settings.onboarding") if not user.onboarded else url_for("dashboard.home")
    resp = redirect(dest)
    set_session_cookie(resp, token)
    log.info("user_logged_in", user_id=user.id)
    flash(f"Welcome back, {user.name}.", "success")
    return resp


# ── Logout ───────────────────────────────────────────────────────────────

@bp.get("/auth/logout")
def logout():
    resp = redirect(url_for("main.index"))
    clear_session_cookie(resp)
    flash("Signed out. See you on the floor.", "info")
    return resp
