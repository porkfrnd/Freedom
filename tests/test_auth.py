"""Auth tests — register, login, logout, CSRF."""

from __future__ import annotations

from extensions import db
from models import User

from conftest import csrf_of


def _get_csrf(client):
    """GET any page to seed the CSRF cookie, then return the token."""
    client.get("/auth/login")
    return csrf_of(client)


def test_register_creates_user(app, client):
    csrf = _get_csrf(client)
    resp = client.post("/auth/register", data={
        "username": "newdancer", "email": "new@test.com",
        "password": "password123", "confirm_password": "password123",
        "csrf_token": csrf,
    }, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/onboarding"

    with app.app_context():
        user = User.query.filter_by(email="new@test.com").first()
        assert user is not None
        assert user.username == "newdancer"


def test_register_sets_session_cookie(app, client):
    csrf = _get_csrf(client)
    client.post("/auth/register", data={
        "username": "dancer2", "email": "d2@test.com",
        "password": "password123", "confirm_password": "password123",
        "csrf_token": csrf,
    }, follow_redirects=False)
    # Session cookie was set during the redirect — test client stores it
    resp = client.get("/home")
    assert resp.status_code == 200
    assert b"What's happening" in resp.data


def test_register_rejects_duplicate_email(app, client):
    csrf = _get_csrf(client)
    client.post("/auth/register", data={
        "username": "first", "email": "dupe@test.com",
        "password": "password123", "confirm_password": "password123",
        "csrf_token": csrf,
    })
    csrf = _get_csrf(client)
    resp = client.post("/auth/register", data={
        "username": "second", "email": "dupe@test.com",
        "password": "password123", "confirm_password": "password123",
        "csrf_token": csrf,
    }, follow_redirects=True)
    assert b"already exists" in resp.data


def test_register_rejects_mismatched_passwords(app, client):
    csrf = _get_csrf(client)
    resp = client.post("/auth/register", data={
        "username": "x", "email": "x@test.com",
        "password": "password123", "confirm_password": "different",
        "csrf_token": csrf,
    })
    # Should get 200 (re-rendered form with errors), not 302
    assert resp.status_code == 200
    assert b"don" in resp.data  # flash msg has JSON-encoded apostrophe


def test_register_rejects_overlong_username(app, client):
    csrf = _get_csrf(client)
    resp = client.post("/auth/register", data={
        "username": "u" * 33, "email": "longname@test.com",
        "password": "password123", "confirm_password": "password123",
        "csrf_token": csrf,
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"too long" in resp.data
    with app.app_context():
        assert User.query.filter_by(email="longname@test.com").first() is None


def test_invalid_csrf_cookie_is_rotated(client):
    client.set_cookie("ffd_csrf", "garbage-not-signed")
    resp = client.get("/auth/login")
    assert resp.status_code == 200
    fresh = csrf_of(client).encode()
    assert fresh and fresh != b"garbage-not-signed"


def test_login_rate_limited(app, client, monkeypatch):
    from werkzeug.security import generate_password_hash
    with app.app_context():
        db.session.add(User(
            id=444, username="rl", email="rl@test.com",
            password_hash=generate_password_hash("password123"),
        ))
        db.session.commit()

    client.get("/auth/login")  # seed CSRF cookie
    csrf = csrf_of(client)
    limiter = app.extensions["ffd_login_limiter"]
    limiter.reset()
    limiter.limit = 2

    for _ in range(2):
        resp = client.post("/auth/login", data={
            "email": "rl@test.com", "password": "wrongpass",
            "csrf_token": csrf,
        })
        assert b"Wrong email or password" in resp.data

    resp = client.post("/auth/login", data={
        "email": "rl@test.com", "password": "wrongpass",
        "csrf_token": csrf,
    })
    assert b"Too many sign-in attempts" in resp.data


def test_login_works(app, client):
    csrf = _get_csrf(client)
    _register(client, "alice", "alice@test.com", csrf)
    client.get("/auth/logout")

    csrf = _get_csrf(client)
    resp = client.post("/auth/login", data={
        "email": "alice@test.com", "password": "password123",
        "csrf_token": csrf,
    }, follow_redirects=False)
    assert resp.status_code == 302
    # New users go to onboarding first; seeded users go to /home
    assert resp.headers["Location"] in ("/home", "/onboarding")


def test_login_rejects_wrong_password(app, client):
    csrf = _get_csrf(client)
    _register(client, "bob", "bob@test.com", csrf)
    client.get("/auth/logout")

    csrf = _get_csrf(client)
    resp = client.post("/auth/login", data={
        "email": "bob@test.com", "password": "wrongpassword",
        "csrf_token": csrf,
    }, follow_redirects=True)
    assert b"Wrong email or password" in resp.data


def test_logout_clears_session(app, client):
    csrf = _get_csrf(client)
    _register(client, "carl", "carl@test.com", csrf)
    resp = client.get("/auth/logout", follow_redirects=False)
    assert resp.status_code == 302

    resp = client.get("/playlists", follow_redirects=False)
    assert resp.status_code == 302
    assert "auth/login" in resp.headers["Location"]


def test_dashboard_requires_login(client):
    resp = client.get("/playlists", follow_redirects=False)
    assert resp.status_code == 302


def test_post_without_csrf_rejected(app, member_client):
    resp = member_client.post("/api/playlists", json={"name": "X", "tracks": []})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "csrf_missing"


def test_post_with_csrf_accepted(app, member_client):
    member_client.get("/playlists")  # seed CSRF
    csrf = csrf_of(member_client)
    resp = member_client.post(
        "/api/playlists",
        json={"name": "Valid", "tracks": []},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201


def test_tampered_csrf_rejected(app, member_client):
    member_client.get("/playlists")
    resp = member_client.post(
        "/api/playlists",
        json={"name": "Forged", "tracks": []},
        headers={"X-CSRF-Token": "forged-value"},
    )
    assert resp.status_code == 400


def _register(client, username, email, csrf):
    client.post("/auth/register", data={
        "username": username, "email": email,
        "password": "password123", "confirm_password": "password123",
        "csrf_token": csrf,
    })
