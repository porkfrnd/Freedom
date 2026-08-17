"""Structured (JSON) logging with a per-request / per-interaction correlation ID.

Usage::

    from utils.logging import get_logger
    log = get_logger("services.discord_api")
    log.info("guild_member_fetched", guild_id=..., user_id=...)

The correlation ID is bound by Flask's ``before_request`` hook (see
``app.py``) via ``structlog.contextvars`` and merged into every record, so
logs from the web request, the bot thread and the scheduler can be traced
back to a single interaction.
"""

from __future__ import annotations

import logging
import os
import sys

import structlog

_configured = False


def configure_logging() -> None:
    """Configure structlog once per process. Idempotent."""
    global _configured
    if _configured:
        return
    _configured = True

    json_output = os.environ.get("LOG_FORMAT", "json").lower() != "console"

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if os.environ.get("FLASK_DEBUG") else logging.INFO
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Keep the stdlib root logger quiet unless something sets a level.
    logging.basicConfig(level=logging.WARNING)

    # Surface discord.py's connection lifecycle ("Logged in as …", gateway
    # status) so bot connectivity problems are visible instead of silent.
    for name in ("discord", "discord.gateway", "discord.http", "discord.client"):
        logging.getLogger(name).setLevel(logging.INFO)

    # wavelink retries a dead Lavalink node forever; its per-attempt
    # warnings flood the console. Real failures (auth/version errors)
    # still surface at ERROR.
    logging.getLogger("wavelink").setLevel(logging.ERROR)


def get_logger(name: str):
    """Return a structlog logger bound to the module name."""
    configure_logging()
    return structlog.get_logger(name)
