"""Discord REST API helpers (§7, §3.2).

Two credential paths:
- OAuth user access tokens (``/users/@me``, ``/users/@me/guilds``) — used
  during login to compute initial claims.
- The bot token (``/guilds/...`` endpoints) — used for live re-verification
  of membership and the ADMINISTRATOR bit, and for channel lists.

Every call is wrapped in try/except with structured logging (no bare
``except``); failures return ``None`` so callers decide how to fail.

The live membership/admin checks are cached for at most
``ADMIN_CHECK_CACHE_SECONDS`` (default 300s) per §3.2.
"""

from __future__ import annotations

import threading
import time

import requests

from utils.logging import get_logger

log = get_logger("services.discord_api")

# Discord permission bit for ADMINISTRATOR.
PERMISSION_ADMINISTRATOR = 1 << 3

_cache_lock = threading.Lock()
_cache: dict[tuple, tuple[float, object]] = {}


class DiscordAPIError(Exception):
    """Raised when a Discord API call fails after retries."""


def _bot_headers(config) -> dict:
    token = config["DISCORD_BOT_TOKEN"]
    return {"Authorization": f"Bot {token}", "Content-Type": "application/json"}


def _user_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def _cache_get(key: tuple):
    with _cache_lock:
        entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.monotonic() - ts > 300:
        return None
    return value


def _cache_put(key: tuple, value: object) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)


def _request(method: str, url: str, headers: dict, timeout: float = 5.0, **kwargs):
    """Perform an API call with explicit error handling. Returns a response
    object or raises :class:`DiscordAPIError`."""
    try:
        resp = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
    except requests.RequestException as exc:
        log.warning("discord_api_request_failed", url=url, error=str(exc))
        raise DiscordAPIError(f"Discord API unreachable: {exc}") from exc

    if resp.status_code in (429, 500, 502, 503, 504):
        log.warning(
            "discord_api_retryable_status",
            url=url,
            status=resp.status_code,
            body=resp.text[:200],
        )
        raise DiscordAPIError(f"Discord API status {resp.status_code}")

    if resp.status_code >= 400:
        log.warning(
            "discord_api_error_status",
            url=url,
            status=resp.status_code,
            body=resp.text[:200],
        )
        raise DiscordAPIError(f"Discord API status {resp.status_code}")
    return resp


# ── OAuth-token calls (login flow) ──────────────────────────────────────────

def fetch_current_user(access_token: str, config) -> dict | None:
    """GET /users/@me — the user's profile with an access token."""
    try:
        resp = _request(
            "GET",
            f"{config['DISCORD_API_BASE']}/users/@me",
            _user_headers(access_token),
        )
        return resp.json()
    except DiscordAPIError as exc:
        log.warning("fetch_current_user_failed", error=str(exc))
        return None


def fetch_user_guilds(access_token: str, config) -> list[dict] | None:
    """GET /users/@me/guilds — guild list with per-guild permission bits."""
    try:
        resp = _request(
            "GET",
            f"{config['DISCORD_API_BASE']}/users/@me/guilds",
            _user_headers(access_token),
        )
        return resp.json()
    except DiscordAPIError as exc:
        log.warning("fetch_user_guilds_failed", error=str(exc))
        return None


def membership_from_guilds(guilds: list[dict] | None, guild_id: str) -> tuple[bool, bool]:
    """Derive (is_member, is_admin) from the /users/@me/guilds payload."""
    if not guilds or not guild_id:
        return False, False
    for guild in guilds:
        if str(guild.get("id")) == str(guild_id):
            perms = int(guild.get("permissions", 0) or 0)
            return True, bool(perms & PERMISSION_ADMINISTRATOR)
    return False, False


# ── Bot-token calls (live verification, channel lists) ─────────────────────

def fetch_guild_member(guild_id: str, user_id: int, config) -> dict | None:
    """GET /guilds/{g}/members/{u} — member payload or None (not a member)."""
    try:
        resp = _request(
            "GET",
            f"{config['DISCORD_API_BASE']}/guilds/{guild_id}/members/{user_id}",
            _bot_headers(config),
        )
        return resp.json()
    except DiscordAPIError as exc:
        if "404" in str(exc):
            return None
        log.warning("fetch_guild_member_failed", user_id=user_id, error=str(exc))
        raise


def fetch_guild_roles(guild_id: str, config) -> list[dict] | None:
    try:
        resp = _request(
            "GET",
            f"{config['DISCORD_API_BASE']}/guilds/{guild_id}/roles",
            _bot_headers(config),
        )
        return resp.json()
    except DiscordAPIError as exc:
        log.warning("fetch_guild_roles_failed", error=str(exc))
        return None


def fetch_guild(guild_id: str, config) -> dict | None:
    """GET /guilds/{g} — used for the landing page's server name/avatar."""
    try:
        resp = _request(
            "GET",
            f"{config['DISCORD_API_BASE']}/guilds/{guild_id}",
            _bot_headers(config),
        )
        return resp.json()
    except DiscordAPIError as exc:
        log.warning("fetch_guild_failed", error=str(exc))
        return None


def fetch_guild_channels(guild_id: str, config) -> list[dict] | None:
    """GET /guilds/{g}/channels — filtered to text channels, cached 5 min."""
    key = ("channels", guild_id)
    cached = _cache_get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]
    try:
        resp = _request(
            "GET",
            f"{config['DISCORD_API_BASE']}/guilds/{guild_id}/channels",
            _bot_headers(config),
        )
        channels = [
            ch
            for ch in resp.json()
            if ch.get("type") == 0  # GUILD_TEXT
            and ch.get("name")
            and ch.get("id")
        ]
        _cache_put(key, channels)
        return channels
    except DiscordAPIError as exc:
        log.warning("fetch_guild_channels_failed", error=str(exc))
        return None


def is_guild_member(guild_id: str, user_id: int, config) -> bool | None:
    """Live membership check via bot token.

    Returns True/False, or None when the check itself failed (caller decides
    how to fail; see ``utils.decorators``).
    """
    key = ("member", guild_id, int(user_id))
    cached = _cache_get(key)
    if cached is not None:
        return bool(cached)
    try:
        result = fetch_guild_member(guild_id, user_id, config) is not None
    except DiscordAPIError:
        return None
    _cache_put(key, result)
    return result


def has_administrator(guild_id: str, user_id: int, config) -> bool | None:
    """Live ADMINISTRATOR-bit check via bot token (roles + guild owner).

    Returns True/False, or None when the check itself failed.
    """
    key = ("admin", guild_id, int(user_id))
    cached = _cache_get(key)
    if cached is not None:
        return bool(cached)

    guild = fetch_guild(guild_id, config)
    if guild is None:
        return None
    if int(guild.get("owner_id", 0)) == int(user_id):
        _cache_put(key, True)
        return True

    roles = fetch_guild_roles(guild_id, config)
    if roles is None:
        return None

    member = fetch_guild_member(guild_id, user_id, config)
    if member is None:
        # Not a member — treat as not-admin (membership gate will catch it).
        _cache_put(key, False)
        return False

    member_role_ids = {str(r) for r in member.get("roles", [])}
    for role in roles:
        if str(role.get("id")) in member_role_ids:
            perms = int(role.get("permissions", 0) or 0)
            if perms & PERMISSION_ADMINISTRATOR:
                _cache_put(key, True)
                return True

    _cache_put(key, False)
    return False


def verify_membership_and_admin(
    guild_id: str, user_id: int, config
) -> tuple[bool | None, bool | None]:
    """Combined live check used by the admin guard.

    Returns (is_member, is_admin) where each may be None when that check
    could not be completed (Discord API failure).
    """
    member = is_guild_member(guild_id, user_id, config)
    if member is False:
        return False, False
    admin = has_administrator(guild_id, user_id, config)
    return member, admin
