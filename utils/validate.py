"""Shared request-validation helpers.

Single source of truth for the regexes used by both the playlist API and
the form-based routes, plus a tiny cleaner for bounded string fields.
Length limits themselves live in ``models`` next to the columns they guard.
"""

from __future__ import annotations

import re
from typing import Any

URL_RE = re.compile(r"^https?://\S+$")


def clean_str(value: Any, max_len: int) -> str:
    """Coerce to str, strip whitespace, hard-truncate to ``max_len``."""
    return str(value or "").strip()[:max_len]
