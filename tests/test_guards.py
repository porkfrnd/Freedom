"""Guard tests — login required, admin required."""

from __future__ import annotations

from extensions import db
from models import User
from services.auth import create_session_token

from conftest import make_token


def test_dashboard_requires_login(client):
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 302


def test_home_accessible_to_member(member_client):
    resp = member_client.get("/")
    assert resp.status_code == 200


def test_announcements_accessible_to_member(member_client):
    resp = member_client.get("/announcements")
    assert resp.status_code == 200


def test_challenges_accessible_to_member(member_client):
    resp = member_client.get("/challenges")
    assert resp.status_code == 200


def test_giveaways_accessible_to_member(member_client):
    resp = member_client.get("/giveaways")
    assert resp.status_code == 200


def test_playlists_accessible_to_member(member_client):
    resp = member_client.get("/playlists")
    assert resp.status_code == 200


def test_api_rejects_unauthenticated(client):
    resp = client.get("/api/playlists")
    assert resp.status_code == 401


def _csrf(client):
    from conftest import csrf_of
    return csrf_of(client)


def _make_member(app, user_id=333, username="member-only"):
    from werkzeug.security import generate_password_hash
    with app.app_context():
        if db.session.get(User, user_id) is None:
            db.session.add(User(
                id=user_id, username=username, email=f"{username}@test.com",
                password_hash=generate_password_hash("password123"),
                is_admin=False,
            ))
            db.session.commit()


def test_non_admin_blocked_from_creating_announcement(app, client):
    _make_member(app)
    token = make_token(app, 333, "member-only")
    client.set_cookie("ffd_session", token)
    client.get("/announcements")  # seed CSRF cookie
    csrf = _csrf(client)
    resp = client.post("/announcements", data={"title": "X", "content": "Y", "csrf_token": csrf})
    assert resp.status_code == 403


def test_non_admin_blocked_from_creating_challenge(app, client):
    _make_member(app)
    token = make_token(app, 333, "member-only")
    client.set_cookie("ffd_session", token)
    client.get("/challenges")  # seed CSRF cookie
    csrf = _csrf(client)
    resp = client.post("/challenges", data={"title": "X", "description": "Y", "csrf_token": csrf})
    assert resp.status_code == 403


def test_non_admin_blocked_from_creating_giveaway(app, client):
    _make_member(app)
    token = make_token(app, 333, "member-only")
    client.set_cookie("ffd_session", token)
    client.get("/giveaways")  # seed CSRF cookie
    csrf = _csrf(client)
    resp = client.post("/giveaways", data={"prize": "X", "csrf_token": csrf})
    assert resp.status_code == 403


def test_admin_can_access_announcements(admin_client):
    resp = admin_client.get("/announcements")
    assert resp.status_code == 200


def test_admin_can_access_challenges(admin_client):
    resp = admin_client.get("/challenges")
    assert resp.status_code == 200


def test_admin_can_access_giveaways(admin_client):
    resp = admin_client.get("/giveaways")
    assert resp.status_code == 200
