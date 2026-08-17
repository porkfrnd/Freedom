"""Application configuration, loaded exclusively from environment variables.

All secrets come from the environment — never hardcode credentials here.
Unknown keys are left unset so callers can branch on their presence
(e.g. an empty bot token means "run web-only for local dev").
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


class Config:
    """Flask configuration object.

    In local development (no ``DATABASE_URL``) the app falls back to a
    file-backed SQLite database so the whole stack runs without a Neon
    account. Production always uses Postgres via ``DATABASE_URL``.
    """

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-secret-change-me")

    FLASK_ENV = os.environ.get("FLASK_ENV", "development")
    DEBUG = _bool("FLASK_DEBUG", FLASK_ENV == "development")
    TESTING = False

    # Public origin used to build the Discord OAuth redirect URI.
    BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")

    # ── Database ────────────────────────────────────────────────────────────
    _db_url = os.environ.get("DATABASE_URL", "")
    if _db_url:
        SQLALCHEMY_DATABASE_URI = _db_url
    else:
        # Local dev fallback only — never reached in production, where
        # DATABASE_URL is always set by the platform.
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "dev.db"
        )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Postgres pool tuning (§3.3): small pool, short-lived connections,
    # pre-ping so a recycled Neon connection is detected before use.
    # SQLite ignores pool kwargs, so only pass them for Postgres.
    if SQLALCHEMY_DATABASE_URI.startswith("postgresql"):
        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_size": 3,
            "max_overflow": 2,
            "pool_recycle": 300,
            "pool_pre_ping": True,
        }
    else:
        # SQLite: the bot thread and web threads share the dev DB file, so
        # disable the same-thread check and give writers a lock timeout.
        SQLALCHEMY_ENGINE_OPTIONS = {
            "connect_args": {"check_same_thread": False, "timeout": 15}
        }

    # ── Discord application ──────────────────────────────────────────────────
    DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
    DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
    DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
    DISCORD_GUILD_ID = os.environ.get("DISCORD_GUILD_ID", "")

    DISCORD_OAUTH_SCOPES = "identify guilds"
    DISCORD_API_BASE = "https://discord.com/api"

    DISCORD_ANNOUNCEMENTS_CHANNEL_ID = os.environ.get(
        "DISCORD_ANNOUNCEMENTS_CHANNEL_ID", ""
    )
    DISCORD_MODERATION_CHANNEL_ID = os.environ.get(
        "DISCORD_MODERATION_CHANNEL_ID", ""
    )
    MODERATOR_ROLE_IDS = [
        rid.strip()
        for rid in os.environ.get("MODERATOR_ROLE_IDS", "").split()
        if rid.strip()
    ]

    # ── Auth / session (§3.2) ───────────────────────────────────────────────
    JWT_TTL_HOURS = _int("JWT_TTL_HOURS", 12)
    JWT_REFRESH_AFTER_HOURS = _int("JWT_REFRESH_AFTER_HOURS", 6)
    # Live admin re-verification cache TTL.
    ADMIN_CHECK_CACHE_SECONDS = _int("ADMIN_CHECK_CACHE_SECONDS", 300)

    SESSION_COOKIE_NAME = "ffd_session"
    # HttpOnly in production; off in local dev because some browsers and
    # privacy extensions REFUSE to store HttpOnly cookies on localhost (we
    # hit this with the login session cookie while ffd_csrf, the identical
    # non-HttpOnly cookie, stored fine). The JWT is signed, the app has no
    # XSS surface (autoescape on, no |safe), and CSRF is separately
    # protected, so the exposure from JS-readable cookies is minimal.
    SESSION_COOKIE_HTTPONLY = _bool("SESSION_COOKIE_HTTPONLY", FLASK_ENV != "development")
    SESSION_COOKIE_SECURE = FLASK_ENV == "production"
    SESSION_COOKIE_SAMESITE = "Lax"

    # ── Groq moderation (§6.2) ──────────────────────────────────────────────
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
    GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
    GROQ_CIRCUIT_FAILURE_THRESHOLD = _int("GROQ_CIRCUIT_FAILURE_THRESHOLD", 3)
    GROQ_CIRCUIT_COOLDOWN_SECONDS = _int("GROQ_CIRCUIT_COOLDOWN_SECONDS", 300)
    GROQ_TIMEOUT_SECONDS = _float("GROQ_TIMEOUT_SECONDS", 5.0)

    # ── Lavalink (§6.1) ─────────────────────────────────────────────────────
    LAVALINK_URI = os.environ.get("LAVALINK_URI", "http://localhost:2333")
    LAVALINK_PASSWORD = os.environ.get("LAVALINK_PASSWORD", "")

    # ── Observability ───────────────────────────────────────────────────────
    SENTRY_DSN = os.environ.get("SENTRY_DSN", "")

    # Whether the Discord bot thread should start at boot. Disabled in tests
    # and when no bot token is configured.
    START_BOT = _bool("START_BOT", True)

    # Moderation log content retention (§8): flagged message text is purged
    # after this many days, keeping the audit row itself.
    MOD_LOG_CONTENT_RETENTION_DAYS = 90
    # Rolling window (days) used by the repeat-offense escalation rule (§6.2).
    MOD_ESCALATION_WINDOW_DAYS = 30

    # Giveaway sweep interval (seconds).
    GIVEAWAY_SWEEP_SECONDS = 30

    # Rate limits.
    PLAYLIST_WRITE_LIMIT = 30  # writes per hour, per user
    PLAYLIST_WRITE_WINDOW = 3600
    GIVEAWAY_ENTRY_LIMIT = 3  # button presses per 30s, per user
    GIVEAWAY_ENTRY_WINDOW = 30
