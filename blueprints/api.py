"""Playlist API (§7.4).

REST-ish JSON endpoints under ``/api/playlists``:

* ``GET    /api/playlists``        — list public playlists (+ mine via ?filter=mine)
* ``POST   /api/playlists``        — create (validated, rate-limited)
* ``PUT    /api/playlists/<id>``   — update (creator or admin)
* ``DELETE /api/playlists/<id>``   — delete (creator or admin)

Every error uses the same shape: ``{"error": {"code": ..., "message": ...}}``.
All writes are per-user rate-limited (sliding window).
"""

from __future__ import annotations

import re

from flask import Blueprint, current_app, g, jsonify, request

from extensions import db
from models import Playlist, User, generate_playlist_id, utcnow
from utils.decorators import require_api_user
from utils.logging import get_logger
from utils.ratelimit import RateLimiter

log = get_logger("blueprints.api")

bp = Blueprint("api", __name__)

TRACK_URL_RE = re.compile(r"^https?://[^\s]+$")
NAME_MAX = 80
TRACK_LIMIT = 50
MAX_ID_ATTEMPTS = 20


def _error(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


def _track_errors(tracks) -> list[str]:
    """Validate a track list; returns a list of human-readable problems."""
    if not isinstance(tracks, list):
        return ["tracks must be a list."]
    if len(tracks) > TRACK_LIMIT:
        return [f"A playlist can hold at most {TRACK_LIMIT} tracks."]
    problems = []
    for i, track in enumerate(tracks):
        if not isinstance(track, dict):
            problems.append(f"Track #{i + 1} must be an object.")
            continue
        url = str(track.get("url") or "")
        if not TRACK_URL_RE.match(url):
            problems.append(f"Track #{i + 1} has an invalid URL — it must start with http(s)://")
        title = str(track.get("title") or "").strip()
        if not title:
            problems.append(f"Track #{i + 1} needs a title.")
        elif len(title) > 200:
            problems.append(f"Track #{i + 1} title is too long (200 characters max).")
        duration = track.get("duration_seconds")
        if duration is not None and (not isinstance(duration, int) or duration < 0):
            problems.append(f"Track #{i + 1} has an invalid duration.")
    return problems


def _normalize_tracks(tracks) -> list[dict]:
    out = []
    for track in tracks:
        out.append(
            {
                "title": str(track.get("title") or "").strip(),
                "url": str(track.get("url") or "").strip(),
                "duration_seconds": track.get("duration_seconds"),
                "added_at": (track.get("added_at") or utcnow().isoformat()),
            }
        )
    return out


@bp.get("/api/playlists")
@require_api_user
def list_playlists():
    mine = request.args.get("filter") == "mine"
    query = Playlist.query
    if mine:
        query = query.filter(Playlist.creator_discord_id == g.claims["discord_id"])
    else:
        query = query.filter(Playlist.is_public.is_(True))
    playlists = query.order_by(Playlist.updated_at.desc()).all()
    return jsonify(
        {
            "playlists": [
                {
                    "id": p.id,
                    "name": p.name,
                    "creator_discord_id": p.creator_discord_id,
                    "creator_name": p.creator.username if p.creator else None,
                    "track_count": p.track_count,
                    "is_public": p.is_public,
                    "created_at": p.created_at.isoformat(),
                    "updated_at": p.updated_at.isoformat(),
                }
                for p in playlists
            ]
        }
    )


@bp.post("/api/playlists")
@require_api_user
def create_playlist():
    limiter = current_app.extensions["ffd_playlist_limiter"]
    if not limiter.allow(str(g.claims["discord_id"])):
        return _error("rate_limited", "You're creating playlists a bit fast — slow down and try again.", 429)

    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return _error("invalid_name", "Give the playlist a name.", 400)
    if len(name) > NAME_MAX:
        return _error("invalid_name", f"Playlist name is too long ({NAME_MAX} characters max).", 400)

    tracks = data.get("tracks", [])
    problems = _track_errors(tracks)
    if problems:
        return _error("invalid_tracks", problems[0], 400)

    is_public = bool(data.get("is_public", True))

    playlist_id = None
    for _ in range(MAX_ID_ATTEMPTS):
        candidate = generate_playlist_id()
        if db.session.get(Playlist, candidate) is None:
            playlist_id = candidate
            break
    if playlist_id is None:
        return _error("id_exhausted", "Couldn't mint a playlist ID — try again.", 500)

    playlist = Playlist(
        id=playlist_id,
        creator_discord_id=g.claims["discord_id"],
        name=name,
        tracks=_normalize_tracks(tracks),
        is_public=is_public,
    )
    db.session.add(playlist)
    db.session.commit()
    log.info("playlist_created", playlist_id=playlist_id, by=g.claims["discord_id"])
    return jsonify({"playlist": {"id": playlist.id, "name": playlist.name}}), 201


@bp.put("/api/playlists/<playlist_id>")
@require_api_user
def update_playlist(playlist_id):
    playlist = db.session.get(Playlist, playlist_id)
    if playlist is None:
        return _error("not_found", "That playlist ID doesn't exist. Double-check the code and try again.", 404)

    if not _can_edit(playlist):
        return _error("forbidden", "Only the creator (or an admin) can edit this playlist.", 403)

    limiter = current_app.extensions["ffd_playlist_limiter"]
    if not limiter.allow(str(g.claims["discord_id"])):
        return _error("rate_limited", "You're making changes a bit fast — slow down and try again.", 429)

    data = request.get_json(silent=True) or {}
    if "name" in data:
        name = str(data["name"] or "").strip()
        if not name:
            return _error("invalid_name", "Give the playlist a name.", 400)
        if len(name) > NAME_MAX:
            return _error("invalid_name", f"Playlist name is too long ({NAME_MAX} characters max).", 400)
        playlist.name = name
    if "tracks" in data:
        problems = _track_errors(data["tracks"])
        if problems:
            return _error("invalid_tracks", problems[0], 400)
        playlist.tracks = _normalize_tracks(data["tracks"])
    if "is_public" in data:
        playlist.is_public = bool(data["is_public"])
    playlist.updated_at = utcnow()
    db.session.commit()
    log.info("playlist_updated", playlist_id=playlist_id, by=g.claims["discord_id"])
    return jsonify({"playlist": {"id": playlist.id, "name": playlist.name}})


@bp.delete("/api/playlists/<playlist_id>")
@require_api_user
def delete_playlist(playlist_id):
    playlist = db.session.get(Playlist, playlist_id)
    if playlist is None:
        return _error("not_found", "That playlist ID doesn't exist. Double-check the code and try again.", 404)
    if not _can_edit(playlist):
        return _error("forbidden", "Only the creator (or an admin) can delete this playlist.", 403)

    limiter = current_app.extensions["ffd_playlist_limiter"]
    if not limiter.allow(str(g.claims["discord_id"])):
        return _error("rate_limited", "You're making changes a bit fast — slow down and try again.", 429)

    db.session.delete(playlist)
    db.session.commit()
    log.info("playlist_deleted", playlist_id=playlist_id, by=g.claims["discord_id"])
    return jsonify({"ok": True})


@bp.get("/api/bot/now-playing")
@require_api_user
def now_playing():
    """Current playlist id per guild, for the equalizer 'now playing' badge."""
    from blueprints.dashboard import _now_playing

    return jsonify({"playlist_id": _now_playing()})


def _can_edit(playlist: Playlist) -> bool:
    if playlist.creator_discord_id == g.claims["discord_id"]:
        return True
    user = db.session.get(User, g.claims["discord_id"])
    return bool(user and user.is_admin)
