"""Freedom for Dance — Flask application factory and process bootstrap.

Boot order:
1. Configure logging (JSON + correlation IDs) and Sentry (clean no-op
   without a DSN).
2. Initialise DB, Discord OAuth, CSRF, rate limiters.
3. Register blueprints and error pages.
4. Start the Discord bot thread + background scheduler — exactly once,
   never twice under Gunicorn preload or Flask's dev reloader.

Run locally:      flask run
Run in prod:      gunicorn -w 1 --threads 4 -k gthread app:app (see Procfile)
"""

from __future__ import annotations

import os
import uuid
from datetime import timezone

import sentry_sdk
from flask import Flask, g, jsonify, render_template, request

from config import Config
from extensions import db, oauth
from utils import security
from utils.logging import configure_logging, get_logger
from utils.ratelimit import RateLimiter

log = get_logger("app")

def create_app(config_object=None) -> Flask:
    configure_logging()

    app = Flask(__name__)
    app.config.from_object(config_object or Config)

    if app.config.get("SENTRY_DSN"):
        try:
            sentry_sdk.init(
                dsn=app.config["SENTRY_DSN"],
                environment=app.config.get("FLASK_ENV", "development"),
                traces_sample_rate=0.1,
            )
        except Exception as exc:  # pragma: no cover - Sentry must never break boot
            log.warning("sentry_init_failed", error=str(exc))

    db.init_app(app)

    # ── Discord OAuth (Authlib) ────────────────────────────────────────────
    oauth.init_app(app)
    oauth.register(
        name="discord",
        client_id=app.config["DISCORD_CLIENT_ID"],
        client_secret=app.config["DISCORD_CLIENT_SECRET"],
        authorize_url="https://discord.com/api/oauth2/authorize",
        authorize_params={"scope": app.config["DISCORD_OAUTH_SCOPES"]},
        access_token_url="https://discord.com/api/oauth2/token",
        access_token_params=None,
        client_kwargs={"token_endpoint_auth_method": "client_secret_post"},
    )

    # ── Blueprints ─────────────────────────────────────────────────────────
    from blueprints.api import bp as api_bp
    from blueprints.auth import bp as auth_bp
    from blueprints.dashboard import bp as dashboard_bp
    from blueprints.main import bp as main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(api_bp)

    # ── CSRF (signed double-submit, §8) ────────────────────────────────────
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
                    {
                        "error": {
                            "code": "csrf_missing",
                            "message": "That request was missing a valid CSRF token. Refresh the page and try again.",
                        }
                    }
                ),
                400,
            )
        return (
            render_template(
                "errors/400.html",
                message="That form expired. Refresh the page and try again.",
            ),
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
                # Session cookie (no Expires/Max-Age), matching the session
                # cookie — browsers that drop persistent cookies still keep
                # these, so CSRF + session behave identically.
                httponly=False,  # JS reads it to echo back as X-CSRF-Token
                secure=app.config["SESSION_COOKIE_SECURE"],
                samesite="Lax",
                path="/",
            )
        return response

    # ── Correlation IDs (§9.2) ─────────────────────────────────────────────
    @app.before_request
    def bind_request_id():
        import structlog

        structlog.contextvars.clear_contextvars()
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
        structlog.contextvars.bind_contextvars(request_id=request_id)
        g.request_id = request_id

    # ── Silent JWT refresh + stale-session self-heal (§3.2) ────────────────
    # Valid tokens near expiry are silently re-issued. A token that fails to
    # decode (old SECRET_KEY, tampering, garbage) is DELETED from the browser
    # on contact — UNLESS the response already sets a fresh session cookie
    # (e.g. the login callback), which must never be clobbered.
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
                log.info("session_cookie_cleared_stale", path=request.path)
            return response
        if auth.should_refresh(claims):
            auth.set_session_cookie(response, auth.build_refresh_token(claims))
        return response

    # ── Request audit (§9.2) — records host + session-cookie state so
    #    login/cookie issues (host mismatch, rejected JWT) are visible in
    #    one log line instead of a mystery redirect back to the landing page.
    @app.after_request
    def audit_request(response):
        from services import auth

        token = request.cookies.get(auth.SESSION_COOKIE_NAME)
        if token:
            session_state = "ok" if auth.decode_session_token(token) else "invalid"
        else:
            session_state = "missing"
        log.info(
            "request_audit",
            method=request.method,
            path=request.path,
            status=response.status_code,
            host=request.host,
            session=session_state,
            # Which cookies the browser actually sent — distinguishes
            # "session cookie not stored" from "all cookies blocked".
            cookies=sorted(request.cookies.keys()),
        )
        return response

    # ── Template helpers ───────────────────────────────────────────────────
    app.jinja_env.globals["csrf_token"] = lambda: g.get("csrf_token", "")

    @app.template_filter("ffd_dt")
    def ffd_dt(value):
        """UTC timestamp for tables; '—' when missing."""
        if value is None:
            return "—"
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%b %d, %Y %H:%M UTC")

    @app.template_filter("timeago")
    def timeago(value):
        from models import utcnow

        if value is None:
            return "—"
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        delta = utcnow() - value
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "just now"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        return f"{days}d ago"

    @app.template_filter("iso8601")
    def iso8601(value):
        if value is None:
            return ""
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    @app.context_processor
    def inject_claims():
        from services.auth import claims_from_request

        return {"current_claims": claims_from_request()}

    # ── Error pages (§5.11) ────────────────────────────────────────────────
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

    # ── Health check (§9.2) ────────────────────────────────────────────────
    @app.get("/healthz")
    def healthz():
        checks = {"database": "ok", "bot_thread": "ok", "bot_ready": "ok"}
        status = "ok"
        try:
            from sqlalchemy import text

            db.session.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            log.error("healthz_db_failed", error=str(exc))
            checks["database"] = "error"
            status = "error"
        runtime = app.extensions.get("ffd_bot_runtime")
        token = app.config.get("DISCORD_BOT_TOKEN")
        if runtime is not None and token:
            # The bot is *supposed* to run — its liveness matters for health.
            if not runtime.is_alive():
                checks["bot_thread"] = "down"
                status = "error"
            elif not runtime.is_ready():
                checks["bot_ready"] = "connecting"
        elif not token:
            # No bot token configured (local web-only dev) — not an error.
            checks["bot_thread"] = "disabled"
        return jsonify({"status": status, "checks": checks}), 200 if status == "ok" else 503

    # ── Rate limiters ──────────────────────────────────────────────────────
    app.extensions["ffd_playlist_limiter"] = RateLimiter(
        limit=app.config["PLAYLIST_WRITE_LIMIT"],
        window_seconds=app.config["PLAYLIST_WRITE_WINDOW"],
    )

    # ── Bot thread + scheduler (§3.1) ──────────────────────────────────────
    _bootstrap_background(app)

    return app


def _bootstrap_background(app: Flask) -> None:
    """Start the bot thread and scheduler, guarding against double-start.

    Under Flask's dev reloader the *parent* process imports this module to
    spawn the child; only the child (WERKZEUG_RUN_MAIN=true) should start
    background threads. Gunicorn (debug=False) always starts them.
    """
    from bot.engine import BotRuntime
    from bot.scheduler import AppScheduler

    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        runtime = BotRuntime(app)
        app.extensions["ffd_bot_runtime"] = runtime
        scheduler = AppScheduler(app, runtime)
        app.extensions["ffd_scheduler"] = scheduler
        runtime.start()
        if app.config["START_BOT"]:
            scheduler.start()
        import atexit

        atexit.register(lambda: scheduler.shutdown())


# Module-level instance for `flask run` / gunicorn `app:app`.
# Tests set FFD_SKIP_APP_CREATION=1 before importing so they can build
# their own app instances against a throwaway database.
app = create_app() if not os.environ.get("FFD_SKIP_APP_CREATION") else None


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
