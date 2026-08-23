"""Playlist API tests — CRUD, validation, ownership, rate limiting."""

from __future__ import annotations

from extensions import db
from models import Playlist, User, generate_playlist_id, validate_playlist_id

from conftest import csrf_of


def _authed_client(app, user_id, username="alice", is_admin=False):
    client = app.test_client()
    with app.app_context():
        if db.session.get(User, user_id) is None:
            from werkzeug.security import generate_password_hash
            db.session.add(User(
                id=user_id, username=username,
                email=f"{username}@test.com",
                password_hash=generate_password_hash("password123"),
                is_admin=is_admin,
            ))
            db.session.commit()
        from services.auth import create_session_token
        token = create_session_token(
            user_id=user_id, username=username,
            email=f"{username}@test.com", is_admin=is_admin,
        )
    client.set_cookie("ffd_session", token)
    client.get("/playlists")  # ensure CSRF cookie
    return client


def _csrf(client):
    return csrf_of(client)


def _make_playlist(app, user_id, name="Set"):
    with app.app_context():
        playlist = Playlist(
            id=generate_playlist_id(),
            creator_id=user_id,
            name=name,
            tracks=[{"title": "A", "url": "https://youtu.be/abc", "duration_seconds": 10}],
            is_public=True,
        )
        db.session.add(playlist)
        db.session.commit()
        return playlist.id


def test_playlist_id_format():
    assert validate_playlist_id("DANCE-89A2")
    assert not validate_playlist_id("dance-89A2")
    assert not validate_playlist_id("DANCE-89A")
    assert not validate_playlist_id("DANCE-OI01")


def test_generated_id_matches_format():
    for _ in range(50):
        assert validate_playlist_id(generate_playlist_id())


def test_create_playlist(app, client):
    client = _authed_client(app, 111)
    resp = client.post(
        "/api/playlists",
        json={"name": "Sweat Session", "tracks": [{"title": "A", "url": "https://youtu.be/abc", "duration_seconds": 120}]},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resp.status_code == 201
    assert resp.get_json()["playlist"]["id"].startswith("DANCE-")


def test_create_playlist_rejects_bad_url(app, client):
    client = _authed_client(app, 111)
    resp = client.post(
        "/api/playlists",
        json={"name": "Bad", "tracks": [{"title": "x", "url": "not-a-url"}]},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resp.status_code == 400


def test_create_playlist_rejects_empty_name(app, client):
    client = _authed_client(app, 111)
    resp = client.post(
        "/api/playlists",
        json={"name": "  ", "tracks": []},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resp.status_code == 400


def test_create_playlist_rejects_overlong_name(app, client):
    client = _authed_client(app, 111)
    resp = client.post(
        "/api/playlists",
        json={"name": "N" * 61, "tracks": []},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resp.status_code == 400
    assert "60 characters max" in resp.get_json()["error"]["message"]


def test_update_playlist_preserves_track_metadata(app, client):
    client = _authed_client(app, 111)
    playlist_id = _make_playlist(app, 111)

    resp = client.put(
        f"/api/playlists/{playlist_id}",
        json={"tracks": [
            {
                "title": "A", "url": "https://youtu.be/abc",
                "duration_seconds": 10, "added_at": "2026-01-01T00:00:00",
            },
            {"title": "B", "url": "https://youtu.be/xyz"},
        ]},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resp.status_code == 200
    with app.app_context():
        tracks = db.session.get(Playlist, playlist_id).tracks
        assert len(tracks) == 2
        assert tracks[0]["duration_seconds"] == 10
        assert tracks[0]["added_at"] == "2026-01-01T00:00:00"
        assert tracks[1]["added_at"]  # fresh timestamp assigned


def test_save_reports_limit_reached_without_toggling(app, client):
    from models import MAX_PLAYLIST_SAVES
    playlist_id = _make_playlist(app, 222, name="Crowded")
    client = _authed_client(app, 111)

    with app.app_context():
        p = db.session.get(Playlist, playlist_id)
        p.saved_by = list(range(1000, 1000 + MAX_PLAYLIST_SAVES))
        db.session.commit()

    resp = client.post(
        f"/api/playlists/{playlist_id}/save",
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["saved"] is False
    assert body["limit_reached"] is True

    with app.app_context():
        assert 111 not in db.session.get(Playlist, playlist_id).saved_by


def test_update_playlist_owner_only(app, client):
    owner = _authed_client(app, 111)
    other = _authed_client(app, 222, username="other")
    playlist_id = _make_playlist(app, 111)

    resp = other.put(
        f"/api/playlists/{playlist_id}",
        json={"name": "Hijacked"},
        headers={"X-CSRF-Token": _csrf(other)},
    )
    assert resp.status_code == 403

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


def test_playlist_writes_are_rate_limited(app, client):
    client = _authed_client(app, 111)
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
