"""Playlist API tests (§7.4, §9.1: playlist ID validation, validation rules,
ownership, per-user rate limiting)."""

from __future__ import annotations

from extensions import db
from models import Playlist, User, generate_playlist_id, validate_playlist_id

from conftest import csrf_of


def _authed_client(app, discord_id, username="alice", is_admin=False):
    client = app.test_client()
    with app.app_context():
        if db.session.get(User, discord_id) is None:
            db.session.add(User(discord_id=discord_id, username=username, is_admin=is_admin))
            db.session.commit()
        from services.auth import create_session_token

        token = create_session_token(
            discord_id=discord_id, username=username, avatar_hash=None,
            is_admin=is_admin, guild_member=True,
        )
    client.set_cookie("ffd_session", token)
    client.get("/playlists")  # ensure CSRF cookie
    return client


def _csrf(client):
    return csrf_of(client)


def _make_playlist(app, discord_id, name="Set"):
    with app.app_context():
        playlist = Playlist(
            id=generate_playlist_id(),
            creator_discord_id=discord_id,
            name=name,
            tracks=[{"title": "A", "url": "https://youtu.be/abc", "duration_seconds": 10}],
            is_public=True,
        )
        db.session.add(playlist)
        db.session.commit()
        return playlist.id


# ── ID format (§9.1) ────────────────────────────────────────────────────────

def test_playlist_id_format():
    assert validate_playlist_id("DANCE-89A2")
    assert validate_playlist_id("DANCE-ABCD")
    assert not validate_playlist_id("dance-89A2")
    assert not validate_playlist_id("DANCE-89A")
    assert not validate_playlist_id("DANCE-89A21")
    assert not validate_playlist_id("DANCE-OI01")  # ambiguous chars excluded
    assert not validate_playlist_id("PLAYLIST-X")


def test_generated_id_matches_format():
    for _ in range(50):
        assert validate_playlist_id(generate_playlist_id())


# ── CRUD + validation ───────────────────────────────────────────────────────

def test_create_playlist(app, client):
    client = _authed_client(app, 111)
    resp = client.post(
        "/api/playlists",
        json={"name": "Sweat Session", "tracks": [{"title": "A", "url": "https://youtu.be/abc", "duration_seconds": 120}], "is_public": True},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resp.status_code == 201
    playlist_id = resp.get_json()["playlist"]["id"]
    assert validate_playlist_id(playlist_id)
    assert playlist_id.startswith("DANCE-")


def test_create_playlist_rejects_bad_url(app, client):
    client = _authed_client(app, 111)
    resp = client.post(
        "/api/playlists",
        json={"name": "Bad", "tracks": [{"title": "x", "url": "not-a-url"}]},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "invalid_tracks"


def test_create_playlist_rejects_empty_name(app, client):
    client = _authed_client(app, 111)
    resp = client.post(
        "/api/playlists",
        json={"name": "  ", "tracks": []},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "invalid_name"


def test_create_playlist_rejects_too_many_tracks(app, client):
    client = _authed_client(app, 111)
    tracks = [{"title": f"T{i}", "url": f"https://youtu.be/{i}"} for i in range(51)]
    resp = client.post(
        "/api/playlists",
        json={"name": "Too many", "tracks": tracks},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resp.status_code == 400


def test_update_playlist_owner_only(app, client):
    owner = _authed_client(app, 111)
    other = _authed_client(app, 222, username="other")
    playlist_id = _make_playlist(app, 111)

    # The other user cannot edit.
    resp = other.put(
        f"/api/playlists/{playlist_id}",
        json={"name": "Hijacked"},
        headers={"X-CSRF-Token": _csrf(other)},
    )
    assert resp.status_code == 403

    # The owner can.
    resp = owner.put(
        f"/api/playlists/{playlist_id}",
        json={"name": "Renamed"},
        headers={"X-CSRF-Token": _csrf(owner)},
    )
    assert resp.status_code == 200
    with app.app_context():
        assert db.session.get(Playlist, playlist_id).name == "Renamed"


def test_delete_playlist_owner_only(app, client):
    owner = _authed_client(app, 111)
    other = _authed_client(app, 222, username="other")
    playlist_id = _make_playlist(app, 111)

    assert other.delete(
        f"/api/playlists/{playlist_id}", headers={"X-CSRF-Token": _csrf(other)}
    ).status_code == 403
    assert owner.delete(
        f"/api/playlists/{playlist_id}", headers={"X-CSRF-Token": _csrf(owner)}
    ).status_code == 200
    with app.app_context():
        assert db.session.get(Playlist, playlist_id) is None


def test_admin_can_edit_others_playlist(app, client):
    admin = _authed_client(app, 333, username="admin", is_admin=True)
    playlist_id = _make_playlist(app, 111)
    resp = admin.put(
        f"/api/playlists/{playlist_id}",
        json={"name": "Admin fix"},
        headers={"X-CSRF-Token": _csrf(admin)},
    )
    assert resp.status_code == 200


def test_unknown_playlist_404(app, client):
    client = _authed_client(app, 111)
    resp = client.put(
        "/api/playlists/DANCE-ZZZZ",
        json={"name": "Nope"},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "not_found"


# ── Rate limiting (§8) ──────────────────────────────────────────────────────

def test_playlist_writes_are_rate_limited(app, client):
    client = _authed_client(app, 111)
    # Lower the limiter through the app extension to force the limit.
    limiter = app.extensions["ffd_playlist_limiter"]
    limiter.limit = 3
    limiter.reset()

    for _ in range(3):
        resp = client.post(
            "/api/playlists",
            json={"name": "Rapid", "tracks": []},
            headers={"X-CSRF-Token": _csrf(client)},
        )
        assert resp.status_code == 201

    resp = client.post(
        "/api/playlists",
        json={"name": "Too fast", "tracks": []},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resp.status_code == 429
    assert resp.get_json()["error"]["code"] == "rate_limited"
