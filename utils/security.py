"""CSRF protection — signed double-submit token (§8).

Because the app stores no server-side session (the auth state lives in a
JWT cookie), we use the classic *signed double-submit* pattern:

1. On first request, issue a ``ffd_csrf`` cookie containing a random token
   signed with the app's ``SECRET_KEY`` (HMAC via itsdangerous). The cookie
   is NOT httponly so client-side JavaScript can read it and echo it back
   as the ``X-CSRF-Token`` header on ``fetch`` calls.
2. Every state-changing request (POST/PUT/PATCH/DELETE) must carry the
   token either as the ``X-CSRF-Token`` header or a ``csrf_token`` form
   field. The value is compared against the cookie AND the signature is
   verified, so an attacker cannot forge a token by setting their own
   cookie value (they cannot sign it).
"""

from __future__ import annotations
from functools import lru_cache

from itsdangerous import BadSignature, URLSafeTimedSerializer

CSRF_COOKIE_NAME = "ffd_csrf"
CSRF_MAX_AGE = 60 * 60 * 12  # 12h, mirrors the session lifetime

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt="ffd-csrf")


def generate_csrf_token(secret_key: str) -> str:
    """Return a fresh signed CSRF token (random payload + HMAC signature)."""
    return _serializer(secret_key).dumps(None)


def verify_csrf_token(secret_key: str, cookie_value: str | None, submitted: str | None) -> bool:
    """True when the submitted token matches the signed cookie token."""
    if not cookie_value or not submitted:
        return False
    # Timing-safe comparison of the raw strings, then signature check.
    import hmac

    if not hmac.compare_digest(cookie_value, submitted):
        return False
    try:
        _serializer(secret_key).loads(submitted, max_age=CSRF_MAX_AGE)
        return True
    except BadSignature:
        return False


@lru_cache(maxsize=128)
def csrf_cookie_valid(secret_key: str, cookie_value: str | None) -> bool:
    """True when the cookie itself still carries a fresh, valid signature.

    Used to decide whether an existing ``ffd_csrf`` cookie should be kept
    or rotated — an expired one must never be left in place, or the user
    gets stuck failing CSRF checks until they clear cookies manually.
    """
    if not cookie_value:
        return False
    try:
        _serializer(secret_key).loads(cookie_value, max_age=CSRF_MAX_AGE)
        return True
    except BadSignature:
        return False


def is_safe_method(method: str) -> bool:
    return method.upper() in _SAFE_METHODS
