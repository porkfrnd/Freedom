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
