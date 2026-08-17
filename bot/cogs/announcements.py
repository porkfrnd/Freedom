"""Announcement dispatch cog (§6.4).

Admins write an announcement on the web; this cog posts the rich embed to
the chosen channel and writes back ``discord_msg_id`` + ``status``. Any
dispatch failure (permissions, deleted channel, Discord down) flips the
row to ``FAILED`` so the dashboard surfaces it instead of pretending the
message went out.

Called from the web layer via ``BotRuntime.submit``.
"""

from __future__ import annotations

import discord
from discord.ext import commands

from extensions import db
from models import Announcement
from utils.logging import get_logger

log = get_logger("bot.cogs.announcements")

ANNOUNCE_COLOR = 0x7B2FF7  # curtain-violet


class AnnouncementCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def dispatch(self, announcement_id: int) -> dict:
        app = self.bot.ffd_app
        with app.app_context():
            ann = db.session.get(Announcement, announcement_id)
            if ann is None:
                return {"ok": False, "error": "not_found"}
            if ann.status == "SENT":
                return {"ok": False, "error": "already_sent"}
            channel_id = ann.channel_id
            title = ann.title
            content = ann.content

        channel = await self._get_channel(channel_id)
        if channel is None:
            await self._mark(announcement_id, "FAILED")
            return {"ok": False, "error": "channel_unreachable"}

        embed = discord.Embed(
            title=title,
            description=content[:4000],
            color=ANNOUNCE_COLOR,
        )
        embed.set_footer(text="Freedom for Dance")

        try:
            message = await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.warning("announcement_send_failed", announcement_id=announcement_id, error=str(exc))
            await self._mark(announcement_id, "FAILED")
            return {"ok": False, "error": "send_failed"}

        with app.app_context():
            ann = db.session.get(Announcement, announcement_id)
            if ann is not None:
                ann.discord_msg_id = message.id
                ann.status = "SENT"
                db.session.commit()
        log.info("announcement_dispatched", announcement_id=announcement_id, message_id=message.id)
        return {"ok": True, "message_id": message.id}

    async def _mark(self, announcement_id: int, status: str) -> None:
        app = self.bot.ffd_app
        with app.app_context():
            ann = db.session.get(Announcement, announcement_id)
            if ann is not None:
                ann.status = status
                db.session.commit()

    async def _get_channel(self, channel_id: int | None) -> discord.TextChannel | None:
        if not channel_id:
            return None
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                log.warning("announcement_channel_fetch_failed", channel_id=channel_id, error=str(exc))
                return None
        return channel  # type: ignore[return-value]
