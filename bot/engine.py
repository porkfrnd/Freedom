"""Bot runtime (§3.1).

The Discord bot runs its own asyncio loop inside ONE daemon thread started
at app boot. This keeps the Flask process single-process (Gunicorn
workers=1) while letting the bot and the web layer share the same database
and the same app config.

Guards:
- ``BotRuntime.start`` is idempotent (threading lock), so Gunicorn's
  preload / Flask's dev reloader can never double-start the thread.
- The dev reloader guard in ``app.py`` additionally skips starting in the
  reloader *parent* process (``WERKZEUG_RUN_MAIN`` check).
- With no ``DISCORD_BOT_TOKEN`` the bot simply never starts — the web app
  is fully functional on its own (local dev).
"""

from __future__ import annotations

import asyncio
import threading

import discord
from discord.ext import commands

from utils.logging import get_logger

log = get_logger("bot.engine")


class BotNotRunningError(RuntimeError):
    """Raised when a coroutine is submitted while the bot loop is down."""


class FFDClient(commands.Bot):
    """The Freedom for Dance bot client.

    Minimal intents (§3.1): presences/typing off, members off (privileged),
    ``message_content`` on only because the moderation listener needs it.
    """

    def __init__(self, app, *, message_content_intent: bool = True):
        intents = discord.Intents.default()
        intents.message_content = message_content_intent
        intents.presences = False
        intents.typing = False
        intents.members = False

        super().__init__(
            # Slash commands only; this prefix is never a real command
            # trigger, it exists to stop stray messages from being parsed.
            command_prefix="ffd::",
            intents=intents,
            max_messages=100,
            chunk_guilds_at_startup=False,
            help_command=None,
        )
        self.ffd_app = app
        self.ready = asyncio.Event()
        self.message_content_intent = message_content_intent

    async def setup_hook(self) -> None:
        await super().setup_hook()
        from bot.cogs.announcements import AnnouncementCog
        from bot.cogs.giveaways import GiveawayCog
        from bot.cogs.moderation import ModerationCog
        from bot.cogs.music import MusicCog

        await self.add_cog(ModerationCog(self))
        await self.add_cog(MusicCog(self))
        await self.add_cog(GiveawayCog(self))
        await self.add_cog(AnnouncementCog(self))
        # Re-register persistent giveaway views so entry buttons survive
        # bot restarts (§6.3). Defensive: a registration failure (e.g. a
        # locked dev DB) must never prevent the bot from connecting.
        try:
            await GiveawayCog.register_persistent_views(self)
        except Exception as exc:  # noqa: BLE001 - connection must proceed
            log.warning("giveaway_view_registration_failed", error=str(exc))

    async def on_ready(self) -> None:
        self.ready.set()
        log.info(
            "discord_bot_ready",
            user=str(self.user),
            guild_count=len(self.guilds),
        )


class BotRuntime:
    """Owns the bot thread and exposes a cross-thread submit() bridge."""

    def __init__(self, app):
        self.app = app
        self.client: FFDClient | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._start_attempted = False

    def start(self) -> None:
        """Start the bot thread exactly once."""
        with self._lock:
            if self._start_attempted:
                return
            self._start_attempted = True

        cfg = self.app.config
        if not cfg.get("START_BOT"):
            log.info("bot_thread_disabled_by_config")
            return
        if not cfg.get("DISCORD_BOT_TOKEN"):
            log.info("bot_thread_skipped_no_token")
            return

        self._thread = threading.Thread(
            target=self._run, name="ffd-discord-bot", daemon=True
        )
        self._thread.start()
        log.info("bot_thread_started")

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        token = self.app.config["DISCORD_BOT_TOKEN"]
        try:
            # First attempt WITH message_content (spec: needed for AI
            # moderation). Bounded: if READY isn't reached within the
            # watchdog window, the attempt is abandoned rather than letting
            # the gateway reconnect-identify loop run forever (e.g. while
            # Discord throttles identifies).
            ready = self.loop.run_until_complete(
                self._attempt(token, message_content=True, timeout_seconds=20)
            )
            if not ready:
                log.warning("bot_first_attempt_unready_falling_back")
                self.client = FFDClient(self.app, message_content_intent=False)
                # Fallback runs unbounded: discord.py's own reconnect
                # backoff eventually spaces identifies past Discord's
                # throttle window, so this connects as soon as the token
                # is allowed to.
                self.loop.run_until_complete(self.client.start(token))
        except discord.PrivilegedIntentsRequired:
            # Message Content intent is a privileged gateway intent that
            # must be toggled in the Discord developer portal. If it's not
            # enabled, reconnect without it instead of staying dead: the
            # moderation listener simply idles (it can't see message
            # content), and every other feature keeps working. Enable the
            # intent in the portal to turn moderation back on.
            log.warning("privileged_intents_falling_back_no_message_content")
            self.client = FFDClient(self.app, message_content_intent=False)
            try:
                self.loop.run_until_complete(self.client.start(token))
            except Exception as exc:  # noqa: BLE001 - log and die, never crash the web app
                log.error("bot_thread_crashed", error=str(exc), exc_info=True)
        except Exception as exc:  # noqa: BLE001 - log and die, never crash the web app
            log.error("bot_thread_crashed", error=str(exc), exc_info=True)
        finally:
            try:
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            except Exception:  # pragma: no cover - shutdown best-effort
                pass
            self.loop.close()
            self.loop = None

    async def _attempt(
        self, token: str, *, message_content: bool, timeout_seconds: float
    ) -> bool:
        """Run one client until READY, bounded by ``timeout_seconds``.

        Returns True when the client reached READY. On timeout it closes
        the client and returns False. Raises ``PrivilegedIntentsRequired``
        (or the underlying start() error) when the attempt fails fast.
        """
        self.client = FFDClient(self.app, message_content_intent=message_content)
        start_task = asyncio.ensure_future(self.client.start(token))
        done_event = asyncio.Event()
        start_task.add_done_callback(lambda _t: done_event.set())
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    self.client.ready.wait(),
                    done_event.wait(),
                    return_exceptions=True,
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            log.warning(
                "bot_attempt_ready_timeout",
                message_content=message_content,
                seconds=timeout_seconds,
            )
        if start_task.done():
            exc = start_task.exception()
            if exc is not None:
                raise exc
        if self.client.is_ready():
            return True
        # Timed out without READY — close and unwind so the caller can
        # fall back to the intent-less client.
        await self.client.close()
        try:
            await asyncio.wait_for(start_task, timeout=5)
        except asyncio.TimeoutError:  # pragma: no cover - daemon best-effort
            pass
        except discord.PrivilegedIntentsRequired:
            raise
        return False

    def submit(self, coro):
        """Schedule ``coro`` on the bot loop from any thread.

        Returns a ``concurrent.futures.Future``; call ``.result()`` to await
        completion from a sync context.
        """
        if self.loop is None or not self.loop.is_running():
            raise BotNotRunningError("bot loop is not running")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def is_ready(self) -> bool:
        return bool(self.client) and self.client.is_ready()

    def shutdown(self) -> None:
        """Best-effort graceful stop (daemon thread, so mostly informational)."""
        if self.loop is not None and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=3)
