"""Public playlist music cog (§6.1).

``/play-playlist <DANCE-XXXX>``:
1. Validates the ID format and looks the playlist up in Neon.
2. Confirms the user is in a voice channel — clear ephemeral error if not.
3. Confirms a Lavalink node is reachable — product-voice ephemeral error if
   not, while a reconnect-with-backoff loop runs in the background.
4. Resolves each track via ``wavelink.Playable.search`` and queues the
   playlist on the user's voice channel player.

Also tracks the currently-playing playlist per guild for the web app's
"now playing" equalizer indicator.
"""

from __future__ import annotations

import asyncio

import discord
import wavelink
from discord import app_commands
from discord.ext import commands

from extensions import db
from models import Playlist, validate_playlist_id
from services import lavalink
from utils.logging import get_logger

log = get_logger("bot.cogs.music")

_EPHEMERAL = True

_INVALID_ID_MSG = (
    "That playlist ID doesn't look right. It should look like **DANCE-89A2**. "
    "Double-check the code and try again."
)
_NOT_FOUND_MSG = (
    "That playlist doesn't exist or isn't public. "
    "Double-check the code and try again."
)
_NO_VOICE_MSG = "Hop into a voice channel first, then run the command again."
_LAVALINK_DOWN_MSG = (
    "Playback's down right now — try again in a bit. "
    "We're already working on bringing the stage lights back."
)


class MusicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._reconnect_task: asyncio.Task | None = None
        # guild_id -> playlist id currently queued (for the web indicator).
        self.playing_playlist: dict[int, str] = {}

    # ── Lavalink lifecycle ─────────────────────────────────────────────────

    async def cog_load(self) -> None:
        cfg = self.bot.ffd_app.config
        if not cfg.get("LAVALINK_PASSWORD"):
            log.warning("lavalink_disabled_no_password")
            return
        try:
            await lavalink.connect_nodes(
                self.bot, cfg["LAVALINK_URI"], cfg["LAVALINK_PASSWORD"]
            )
        except Exception as exc:  # noqa: BLE001 - initial connect failure is retried
            log.error("lavalink_initial_connect_failed", error=str(exc))
            self._start_reconnect()

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        log.info("lavalink_node_ready", node=payload.node.identifier)

    @commands.Cog.listener()
    async def on_wavelink_node_disconnected(
        self, payload: wavelink.NodeDisconnectedEventPayload
    ) -> None:
        log.warning("lavalink_node_disconnected", node=payload.node.identifier)
        self._start_reconnect()

    def _start_reconnect(self) -> None:
        """Start a single reconnect-with-backoff loop (idempotent)."""
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        cfg = self.bot.ffd_app.config
        self._reconnect_task = asyncio.create_task(
            lavalink.reconnect_with_backoff(
                cfg["LAVALINK_URI"], cfg["LAVALINK_PASSWORD"]
            )
        )

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        # When the queue runs dry, the "now playing" indicator clears.
        player = payload.player
        if player is not None and player.queue.is_empty():
            self.playing_playlist.pop(player.guild.id, None)

    @commands.Cog.listener()
    async def on_wavelink_track_exception(
        self, payload: wavelink.TrackExceptionEventPayload
    ) -> None:
        log.warning(
            "lavalink_track_exception",
            guild_id=payload.player.guild.id if payload.player else None,
            error=str(payload.exception),
        )

    # ── Commands ───────────────────────────────────────────────────────────

    @app_commands.command(
        name="play-playlist",
        description="Play a Freedom for Dance playlist in your voice channel",
    )
    async def play_playlist(
        self, interaction: discord.Interaction, playlist_id: str
    ) -> None:
        await interaction.response.defer(ephemeral=_EPHEMERAL)

        if not validate_playlist_id(playlist_id):
            await interaction.followup.send(_INVALID_ID_MSG, ephemeral=_EPHEMERAL)
            return

        playlist = self._fetch_playlist(playlist_id.upper())
        if playlist is None:
            await interaction.followup.send(_NOT_FOUND_MSG, ephemeral=_EPHEMERAL)
            return

        voice = interaction.user.voice
        if voice is None or voice.channel is None:
            await interaction.followup.send(_NO_VOICE_MSG, ephemeral=_EPHEMERAL)
            return

        try:
            node = wavelink.Pool.get_node()
            if node.status is not wavelink.NodeStatus.CONNECTED:
                raise RuntimeError("no connected lavalink node")
        except Exception as exc:  # noqa: BLE001 - node unreachable (NodeNotFound, not connected)
            log.warning("lavalink_node_unreachable", error=str(exc))
            await interaction.followup.send(_LAVALINK_DOWN_MSG, ephemeral=_EPHEMERAL)
            self._start_reconnect()
            return

        try:
            await self._queue_playlist(interaction, playlist, voice.channel)
        except Exception as exc:  # noqa: BLE001 - never leave the user hanging
            log.error("play_playlist_failed", error=str(exc))
            await interaction.followup.send(
                "Something went wrong loading that playlist. Try again in a bit.",
                ephemeral=_EPHEMERAL,
            )

    # ── Helpers ────────────────────────────────────────────────────────────

    def _fetch_playlist(self, playlist_id: str) -> Playlist | None:
        with self.bot.ffd_app.app_context():
            return db.session.get(Playlist, playlist_id)

    async def _queue_playlist(
        self, interaction: discord.Interaction, playlist: Playlist, channel
    ) -> None:
        tracks: list[wavelink.Playable] = []
        for track_dict in playlist.tracks or []:
            url = (track_dict or {}).get("url")
            if not url:
                continue
            try:
                result = await wavelink.Playable.search(url)
            except Exception as exc:  # noqa: BLE001 - skip unloadable tracks
                log.warning("track_search_failed", url=url, error=str(exc))
                continue
            if result:
                tracks.append(result[0])

        if not tracks:
            await interaction.followup.send(
                "None of the tracks in that playlist could be loaded right now. "
                "Try again in a bit.",
                ephemeral=_EPHEMERAL,
            )
            return

        player = channel.guild.voice_client
        if player is None or not getattr(player, "connected", False):
            player = await channel.connect(cls=wavelink.Player)

        # Partial autoplay: advance through the queued tracks, then stop —
        # no YouTube recommendations for a curated playlist.
        player.autoplay = wavelink.AutoPlayMode.partial
        for track in tracks[1:]:
            await player.queue.put_wait(track)
        await player.play(tracks[0])

        self.playing_playlist[channel.guild.id] = playlist.id
        log.info(
            "playlist_started",
            guild_id=channel.guild.id,
            playlist_id=playlist.id,
            track_count=len(tracks),
            requested_by=interaction.user.id,
        )
        await interaction.followup.send(
            f"Now playing **{playlist.name}** ({len(tracks)} tracks) in "
            f"{channel.mention}.",
            ephemeral=_EPHEMERAL,
        )

    def now_playing(self, guild_id: int) -> str | None:
        """Playlist id currently queued for ``guild_id`` (web indicator)."""
        return self.playing_playlist.get(guild_id)
