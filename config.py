"""Application configuration, loaded exclusively from environment variables.

All secrets come from the environment — never hardcode credentials here.
Unknown keys are left unset so callers can branch on their presence.
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


class Config:
    """Flask configuration object.

    In local development (no ``DATABASE_URL``) the app falls back to a
    file-backed SQLite database so the whole stack runs without Postgres.
    Production always uses Postgres via ``DATABASE_URL``.
    """

    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-secret-change-me")

    FLASK_ENV = os.environ.get("FLASK_ENV", "development")
    DEBUG = _bool("FLASK_DEBUG", FLASK_ENV == "development")
    TESTING = False

    # Public origin used to build links in emails, redirects, etc.
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

    # Postgres pool tuning: small pool, short-lived connections,
    # pre-ping so a recycled connection is detected before use.
    # SQLite ignores pool kwargs, so only pass them for Postgres.
    if SQLALCHEMY_DATABASE_URI.startswith("postgresql"):
        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_size": 3,
            "max_overflow": 2,
            "pool_recycle": 300,
            "pool_pre_ping": True,
        }
    else:
        # SQLite: disable the same-thread check and give writers a lock timeout.
        SQLALCHEMY_ENGINE_OPTIONS = {
            "connect_args": {"check_same_thread": False, "timeout": 15}
        }

    # ── Session / auth ──────────────────────────────────────────────────────
    # Flask's built-in session cookie (used for flash messages) — renamed
    # to avoid colliding with our JWT cookie, which also uses "ffd_session".
    SESSION_COOKIE_NAME = "_flask_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = FLASK_ENV == "production"
    SESSION_COOKIE_SAMESITE = "Lax"
    JWT_TTL_HOURS = _int("JWT_TTL_HOURS", 24)

    # Rate limits.
    PLAYLIST_WRITE_LIMIT = 30  # writes per hour, per user
    PLAYLIST_WRITE_WINDOW = 3600
    LOGIN_RATE_LIMIT = _int("LOGIN_RATE_LIMIT", 10)  # attempts per window, per IP+email
    LOGIN_RATE_WINDOW = _int("LOGIN_RATE_WINDOW", 300)
