"""Shared pytest fixtures — email/password auth, no Discord."""

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
    SECRET_KEY = "test-secret-key-that-is-long-enough"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
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


def make_token(app, user_id, username="dancer", email="dancer@test.com", is_admin=False, is_teacher=False):
    with app.app_context():
        return create_session_token(
            user_id=user_id,
            username=username,
            email=email,
            is_admin=is_admin,
            is_teacher=is_teacher,
        )


def _create_user(app, user_id, username, email=None, is_admin=False, is_teacher=False):
    with app.app_context():
        existing = db.session.get(User, user_id)
        if existing is None:
            from werkzeug.security import generate_password_hash
            db.session.add(User(
                id=user_id,
                username=username,
                email=email or f"{username}@test.com",
                password_hash=generate_password_hash("password123"),
                is_admin=is_admin,
                is_teacher=is_teacher,
            ))
            db.session.commit()


@pytest.fixture()
def member_token(app):
    _create_user(app, 111, "alice")
    return make_token(app, 111, "alice")


@pytest.fixture()
def member_client(app, member_token):
    client = app.test_client()
    client.set_cookie("ffd_session", member_token)
    return client


@pytest.fixture()
def admin_token(app):
    _create_user(app, 222, "bob", is_admin=True)
    return make_token(app, 222, "bob", is_admin=True)


@pytest.fixture()
def admin_client(app, admin_token):
    client = app.test_client()
    client.set_cookie("ffd_session", admin_token)
    return client


@pytest.fixture()
def teacher_token(app):
    _create_user(app, 333, "carol", is_teacher=True)
    return make_token(app, 333, "carol", is_teacher=True)


@pytest.fixture()
def teacher_client(app, teacher_token):
    client = app.test_client()
    client.set_cookie("ffd_session", teacher_token)
    return client


def csrf_of(client):
    """Read the CSRF token from the client's cookie jar."""
    return client.get_cookie("ffd_csrf").value
