"""OAuth flow (§7.1) with a mocked Discord API, plus CSRF enforcement (§8)."""

from __future__ import annotations

from unittest import mock

from extensions import db, oauth
from models import User

from conftest import csrf_of


# ── OAuth callback (§9.1: "including the OAuth callback with a mocked
#    Discord API response") ──────────────────────────────────────────────────

def _mock_oauth(monkeypatch, profile, guilds):
    discord_app = oauth.discord  # construct the registered client once
    monkeypatch.setattr(
        discord_app, "authorize_access_token", lambda: {"access_token": "tok123"}
    )
    monkeypatch.setattr(
        "services.discord_api.fetch_current_user",
        lambda access_token, config: profile,
    )
    monkeypatch.setattr(
        "services.discord_api.fetch_user_guilds",
        lambda access_token, config: guilds,
    )


def test_oauth_callback_member_flow(app, client, monkeypatch):
    """Member login: callback serves the signing-in page with ONE session
    cookie, and the cookie round-trips to the dashboard."""
    _mock_oauth(
        monkeypatch,
        profile={"id": "777", "username": "choreo", "avatar": "abc123"},
        guilds=[{"id": "123456789012345678", "permissions": str(1 << 3)}],  # ADMINISTRATOR
    )
    resp = client.get("/auth/callback")
    # Same-origin 200 page (not a 302 in the cross-site redirect chain).
    assert resp.status_code == 200
    assert b"/playlists" in resp.data  # the page bounces to the dashboard
    session_headers = [
        h for h in resp.headers.getlist("Set-Cookie") if h.startswith("ffd_session=")
    ]
    assert len(session_headers) == 1  # exactly one, never a conflicting delete
    assert "Max-Age=0" not in session_headers[0]

    # The cookie survives into the next request — the full browser flow.
    landed = client.get("/playlists")
    assert landed.status_code == 200

    with app.app_context():
        user = db.session.get(User, 777)
        assert user is not None
        assert user.username == "choreo"
        assert user.avatar_hash == "abc123"
        assert user.is_admin is True  # ADMINISTRATOR bit computed at login


def test_oauth_callback_member_flow_stale_cookie(app, client, monkeypatch):
    """A stale/garbage cookie in the jar must not prevent the fresh login
    cookie from replacing it (§9.1 stale-cookie scenario)."""
    _mock_oauth(
        monkeypatch,
        profile={"id": "777", "username": "choreo", "avatar": "abc123"},
        guilds=[{"id": "123456789012345678", "permissions": str(1 << 3)}],
    )
    client.set_cookie("ffd_session", "garbage.token.that.fails.jwt.decode")
    resp = client.get("/auth/callback")
    assert resp.status_code == 200
    assert b"/playlists" in resp.data
    assert client.get("/playlists").status_code == 200


def test_oauth_callback_non_member_flow(app, client, monkeypatch):
    _mock_oauth(
        monkeypatch,
        profile={"id": "888", "username": "visitor", "avatar": None},
        guilds=[],  # not in the guild
    )
    resp = client.get("/auth/callback")
    assert resp.status_code == 200
    assert b"/not-member" in resp.data

    with app.app_context():
        user = db.session.get(User, 888)
        assert user is not None
        assert user.is_admin is False


def test_oauth_callback_denied(app, client, monkeypatch):
    discord_app = oauth.discord
    monkeypatch.setattr(
        discord_app,
        "authorize_access_token",
        mock.Mock(side_effect=Exception("access_denied")),
    )
    resp = client.get("/auth/callback")
    assert resp.status_code == 302  # bounced to landing, not a 500


# ── CSRF (§8) ───────────────────────────────────────────────────────────────

def test_post_without_csrf_rejected(app, client):
    with app.app_context():
        from services.auth import create_session_token

        token = create_session_token(
            discord_id=111, username="a", avatar_hash=None,
            is_admin=False, guild_member=True,
        )
    client.set_cookie("ffd_session", token)
    resp = client.post("/api/playlists", json={"name": "X", "tracks": []})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "csrf_missing"


def test_post_with_csrf_accepted(app, client):
    with app.app_context():
        db.session.add(User(discord_id=111, username="a"))
        db.session.commit()
        from services.auth import create_session_token

        token = create_session_token(
            discord_id=111, username="a", avatar_hash=None,
            is_admin=False, guild_member=True,
        )
    client.set_cookie("ffd_session", token)
    client.get("/playlists")
    csrf = csrf_of(client)

    resp = client.post(
        "/api/playlists",
        json={"name": "Valid", "tracks": []},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201


def test_tampered_csrf_rejected(app, client):
    with app.app_context():
        db.session.add(User(discord_id=111, username="a"))
        db.session.commit()
        from services.auth import create_session_token

        token = create_session_token(
            discord_id=111, username="a", avatar_hash=None,
            is_admin=False, guild_member=True,
        )
    client.set_cookie("ffd_session", token)
    client.get("/playlists")
    resp = client.post(
        "/api/playlists",
        json={"name": "Forged", "tracks": []},
        headers={"X-CSRF-Token": "forged-value"},
    )
    assert resp.status_code == 400
