"""Shared pytest fixtures.

Tests never touch real Discord/Groq/Lavalink: the Discord API helpers and
OAuth endpoints are mocked, and the bot thread is disabled (START_BOT=False).
The database is an in-memory SQLite with a StaticPool so every connection
shares the same schema.
"""

from __future__ import annotations

import os

os.environ.setdefault("FFD_SKIP_APP_CREATION", "1")

import pytest  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import create_app  # noqa: E402
from config import Config  # noqa: E402
from extensions import db  # noqa: E402
from models import User  # noqa: E402
from services.auth import create_session_token  # noqa: E402


class TestConfig(Config):
    TESTING = True
    START_BOT = False
    SECRET_KEY = "test-secret-key-that-is-long-enough"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    DISCORD_CLIENT_ID = "test-client-id"
    DISCORD_CLIENT_SECRET = "test-client-secret"
    DISCORD_GUILD_ID = "123456789012345678"
    DISCORD_BOT_TOKEN = ""
    # Keep rate-limit tests fast and deterministic.
    PLAYLIST_WRITE_LIMIT = 30


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def make_token(app, discord_id, username="dancer", is_admin=False, guild_member=True, session_version=1):
    with app.app_context():
        return create_session_token(
            discord_id=discord_id,
            username=username,
            avatar_hash=None,
            is_admin=is_admin,
            guild_member=guild_member,
            session_version=session_version,
        )


@pytest.fixture()
def member_token(app):
    """A JWT for a guild member (non-admin)."""
    with app.app_context():
        db.session.add(User(discord_id=111, username="alice", is_admin=False))
        db.session.commit()
    return make_token(app, 111, "alice", is_admin=False, guild_member=True)


@pytest.fixture()
def member_client(app, member_token):
    client = app.test_client()
    client.set_cookie("ffd_session", member_token)
    return client


@pytest.fixture()
def admin_token(app):
    """A JWT whose COOKIE claims admin (the live check is mocked per test)."""
    with app.app_context():
        db.session.add(User(discord_id=222, username="bob", is_admin=True))
        db.session.commit()
    return make_token(app, 222, "bob", is_admin=True, guild_member=True)


@pytest.fixture()
def admin_client(app, admin_token):
    client = app.test_client()
    client.set_cookie("ffd_session", admin_token)
    return client


def csrf_of(client):
    """Read the CSRF token from the client's cookie jar."""
    return client.get_cookie("ffd_csrf").value


def mock_live_admin(monkeypatch, is_member=True, is_admin=True):
    """Point the admin guard's live Discord check at a canned answer."""
    monkeypatch.setattr(
        "utils.decorators.discord_api.verify_membership_and_admin",
        lambda guild_id, user_id, config: (is_member, is_admin),
    )
