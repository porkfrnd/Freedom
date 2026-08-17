"""Guard tests (§9.1 minimum: guild-guard rejection, admin-guard rejection
including the "stale cookie says admin, live check says no" case)."""

from __future__ import annotations

from extensions import db
from models import User
from services.auth import create_session_token

from conftest import make_token, mock_live_admin


# ── Guild guard (§7.2) ──────────────────────────────────────────────────────

def test_dashboard_requires_login(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


def test_dashboard_requires_guild_membership(app, client):
    token = make_token(app, 999, "outsider", guild_member=False)
    client.set_cookie("ffd_session", token)
    resp = client.get("/dashboard")
    assert resp.status_code == 403
    assert b"You'll need to be a member" in resp.data


def test_not_member_page_copy(client):
    resp = client.get("/not-member")
    assert resp.status_code == 403
    assert b"Join the server, then come back." in resp.data


def test_playlists_accessible_to_member(member_client):
    resp = member_client.get("/playlists")
    assert resp.status_code == 200


def test_api_rejects_non_member(app, client):
    token = make_token(app, 999, "outsider", guild_member=False)
    client.set_cookie("ffd_session", token)
    resp = client.get("/api/playlists")
    assert resp.status_code == 403
    assert resp.get_json()["error"]["code"] == "not_a_member"


# ── Session invalidation (§3.2) ─────────────────────────────────────────────

def test_session_invalidated_when_membership_check_bumps_version(app, client):
    with app.app_context():
        user = User(discord_id=555, username="moved-on", is_admin=False, session_version=1)
        db.session.add(user)
        db.session.commit()
    token = make_token(app, 555, "moved-on", session_version=1)
    client.set_cookie("ffd_session", token)

    # The background membership check found the user left the guild.
    with app.app_context():
        user = db.session.get(User, 555)
        user.is_admin = False
        user.session_version += 1
        db.session.commit()

    resp = client.get("/playlists")
    assert resp.status_code == 302  # redirected to landing
    assert resp.headers["Location"].endswith("/")


# ── Admin guard (§7.3, §3.2) ────────────────────────────────────────────────

def test_admin_page_requires_login(client):
    resp = client.get("/giveaways")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


def test_non_admin_member_blocked(app, client, monkeypatch):
    mock_live_admin(monkeypatch, is_member=True, is_admin=False)
    token = make_token(app, 333, "member-only", is_admin=False, guild_member=True)
    client.set_cookie("ffd_session", token)
    resp = client.get("/giveaways")
    assert resp.status_code == 403


def test_stale_admin_cookie_blocked_by_live_check(app, client, monkeypatch):
    """Cookie claims admin; the live check says no → blocked (§9.1)."""
    with app.app_context():
        db.session.add(User(discord_id=444, username="has-been", is_admin=True))
        db.session.commit()
    token = make_token(app, 444, "has-been", is_admin=True, guild_member=True)
    client.set_cookie("ffd_session", token)
    mock_live_admin(monkeypatch, is_member=True, is_admin=False)

    resp = client.get("/giveaways")
    assert resp.status_code == 403
    assert b"That's not your stage" in resp.data

    # The stale claim was corrected in the DB and the cookie invalidated.
    with app.app_context():
        user = db.session.get(User, 444)
        assert user.is_admin is False
        assert user.session_version == 2
    assert "ffd_session" in resp.headers.get("Set-Cookie", "")  # cleared


def test_live_check_passes_admin(app, client, monkeypatch):
    mock_live_admin(monkeypatch, is_member=True, is_admin=True)
    token = make_token(app, 222, "bob", is_admin=True, guild_member=True)
    client.set_cookie("ffd_session", token)
    resp = client.get("/giveaways")
    assert resp.status_code == 200
    assert b"Run a giveaway" in resp.data


def test_live_check_failure_fails_closed(app, client, monkeypatch):
    """Discord API unreachable → fail closed, never trust the cookie."""
    monkeypatch.setattr(
        "utils.decorators.discord_api.verify_membership_and_admin",
        lambda guild_id, user_id, config: (None, None),
    )
    token = make_token(app, 222, "bob", is_admin=True, guild_member=True)
    client.set_cookie("ffd_session", token)
    resp = client.get("/giveaways")
    assert resp.status_code == 503


def test_left_guild_invalidates_session(app, client, monkeypatch):
    mock_live_admin(monkeypatch, is_member=False, is_admin=False)
    token = make_token(app, 222, "bob", is_admin=True, guild_member=True)
    client.set_cookie("ffd_session", token)
    resp = client.get("/giveaways")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")
