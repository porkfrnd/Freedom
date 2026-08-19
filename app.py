"""Freedom for Dance — Flask application factory.

Run locally:      flask run
Run in prod:      gunicorn -w 1 --threads 4 -k gthread app:app
"""

from __future__ import annotations

import os
import uuid

from flask import Flask, g, jsonify, render_template, request

from config import Config
from extensions import db
from utils import security
from utils.logging import configure_logging, get_logger

log = get_logger("app")


def create_app(config_object=None) -> Flask:
    configure_logging()

    app = Flask(__name__)
    app.config.from_object(config_object or Config)

    db.init_app(app)

    # ── Blueprints ─────────────────────────────────────────────────────────
    # Importing the blueprints registers all models with the metadata, so
    # this MUST happen before the SQLite auto-create below.
    from blueprints.api import bp as api_bp
    from blueprints.auth import bp as auth_bp
    from blueprints.dashboard import bp as dashboard_bp
    from blueprints.main import bp as main_bp
    from blueprints.settings import bp as settings_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(settings_bp)

    # Local dev uses SQLite; create tables at boot so `flask run` just works.
    # Production (Postgres) is migrated via Alembic — never auto-created.
    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        with app.app_context():
            db.create_all()

    # ── CSRF (signed double-submit) ────────────────────────────────────────
    @app.before_request
    def csrf_guard():
        if security.is_safe_method(request.method):
            return None
        cookie = request.cookies.get(security.CSRF_COOKIE_NAME)
        submitted = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        if security.verify_csrf_token(app.config["SECRET_KEY"], cookie, submitted):
            return None
        if request.path.startswith("/api/"):
            return (
                jsonify(
                    {"error": {"code": "csrf_missing", "message": "That request was missing a valid CSRF token. Refresh and try again."}}
                ),
                400,
            )
        return (
            render_template("errors/400.html", message="That form expired. Refresh the page and try again."),
            400,
        )

    @app.before_request
    def ensure_csrf_cookie():
        existing = request.cookies.get(security.CSRF_COOKIE_NAME)
        if existing:
            g.csrf_token = existing
            g.csrf_was_missing = False
        else:
            g.csrf_token = security.generate_csrf_token(app.config["SECRET_KEY"])
            g.csrf_was_missing = True

    @app.after_request
    def attach_csrf_cookie(response):
        if getattr(g, "csrf_was_missing", False) and g.get("csrf_token"):
            response.set_cookie(
                security.CSRF_COOKIE_NAME,
                g.csrf_token,
                httponly=False,
                secure=app.config["SESSION_COOKIE_SECURE"],
                samesite="Lax",
                path="/",
            )
        return response

    # ── Correlation IDs ────────────────────────────────────────────────────
    @app.before_request
    def bind_request_id():
        import structlog
        structlog.contextvars.clear_contextvars()
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
        structlog.contextvars.bind_contextvars(request_id=request_id)
        g.request_id = request_id

    # ── Session refresh (silent re-issue when near expiry) ─────────────────
    @app.after_request
    def refresh_session_cookie(response):
        from services import auth

        token = request.cookies.get(auth.SESSION_COOKIE_NAME)
        if not token:
            return response
        claims = auth.decode_session_token(token)
        if claims is None:
            carries_fresh = any(
                h.startswith(auth.SESSION_COOKIE_NAME + "=")
                for h in response.headers.getlist("Set-Cookie")
            )
            if not carries_fresh:
                auth.clear_session_cookie(response)
            return response
        return response

    # ── Template helpers ───────────────────────────────────────────────────
    app.jinja_env.globals["csrf_token"] = lambda: g.get("csrf_token", "")
    from models import format_uid as _format_uid
    app.jinja_env.globals["format_uid"] = _format_uid

    @app.context_processor
    def inject_claims():
        from services.auth import claims_from_request
        claims = claims_from_request()
        ctx = {"current_claims": claims}
        if claims:
            from extensions import db as _db
            from models import User
            user = _db.session.get(User, claims["user_id"])
            if user:
                ctx["current_user"] = user
        return ctx

    # ── Error pages ────────────────────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(_e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(_e):
        log.error("unhandled_exception", exc_info=True)
        return render_template("errors/500.html"), 500

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("errors/403.html"), 403

    # ── Health check ───────────────────────────────────────────────────────
    @app.get("/healthz")
    def healthz():
        checks = {"database": "ok"}
        status = "ok"
        try:
            from sqlalchemy import text
            db.session.execute(text("SELECT 1"))
        except Exception as exc:
            log.error("healthz_db_failed", error=str(exc))
            checks["database"] = "error"
            status = "error"
        return jsonify({"status": status, "checks": checks}), 200 if status == "ok" else 503

    # ── Rate limiters ──────────────────────────────────────────────────────
    from utils.ratelimit import RateLimiter
    app.extensions["ffd_playlist_limiter"] = RateLimiter(
        limit=app.config["PLAYLIST_WRITE_LIMIT"],
        window_seconds=app.config["PLAYLIST_WRITE_WINDOW"],
    )

    return app


# Module-level instance for `flask run` / gunicorn `app:app`.
app = create_app() if not os.environ.get("FFD_SKIP_APP_CREATION") else None


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
