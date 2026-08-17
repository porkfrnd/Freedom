"""Giveaway cog (§6.3).

* ``publish`` — post the interactive embed for a new/retried giveaway and
  register its persistent view (called from the web layer via
  ``BotRuntime.submit``).
* ``finalize`` — draw winners at end time, edit the embed to an ended
  state, post the results message. Handles fewer entrants than winners.
* ``reroll`` — manual re-draw from the dashboard: draws from remaining
  entrants, appends to ``winners`` (history preserved), posts a follow-up.
* ``register_persistent_views`` — re-registers one view per ACTIVE
  giveaway on boot so entry buttons survive restarts.

``draw_winners`` is the pure, unit-tested draw function (random.sample with
the fewer-entrants-than-winners case handled explicitly).
"""

from __future__ import annotations

import random

import discord
from discord.ext import commands

from extensions import db
from models import Giveaway
from utils.logging import get_logger

log = get_logger("bot.cogs.giveaways")


def draw_winners(
    entrants: list[int], num_winners: int, exclude: list[int] | None = None
) -> tuple[list[int], int]:
    """Draw ``num_winners`` from ``entrants``, excluding already-drawn ids.

    Returns ``(winners, shortfall)``. When there are fewer entrants than
    winners, ALL entrants win and the shortfall is returned as an int so
    callers can log a note instead of erroring (§6.3 edge case).
    """
    pool = [e for e in (entrants or []) if e not in (exclude or [])]
    if not pool:
        return [], num_winners
    count = min(num_winners, len(pool))
    winners = random.sample(pool, count)
    return winners, num_winners - count


def build_giveaway_embed(giveaway: Giveaway, ended: bool = False) -> discord.Embed:
    from bot.views.giveaways import _build_embed

    return _build_embed(
        giveaway.prize,
        giveaway.end_time,
        len(giveaway.entrants or []),
        giveaway.num_winners,
        ended=ended,
    )


class GiveawayCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Publishing (called from the web layer) ─────────────────────────────

    async def publish(self, giveaway_id: int) -> dict:
        """Post the giveaway embed + view to its channel; record message_id.

        Returns a small status dict for the web layer.
        """
        app = self.bot.ffd_app
        with app.app_context():
            gw = db.session.get(Giveaway, giveaway_id)
            if gw is None:
                return {"ok": False, "error": "not_found"}
            if gw.status != "ACTIVE":
                return {"ok": False, "error": "not_active"}
            channel_id = gw.channel_id
            prize = gw.prize
            end_time = gw.end_time
            num_winners = gw.num_winners

        try:
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                channel = await self.bot.fetch_channel(channel_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            log.warning("giveaway_publish_channel_failed", error=str(exc))
            return {"ok": False, "error": "channel_unreachable"}

        from bot.views.giveaways import GiveawayView

        view = GiveawayView(giveaway_id, prize, end_time, num_winners)
        embed = build_giveaway_embed(gw)
        try:
            message = await channel.send(embed=embed, view=view)
        except discord.HTTPException as exc:
            log.warning("giveaway_publish_send_failed", error=str(exc))
            return {"ok": False, "error": "send_failed"}

        self.bot.add_view(view, message_id=message.id)
        with app.app_context():
            gw = db.session.get(Giveaway, giveaway_id)
            if gw is not None:
                gw.message_id = message.id
                db.session.commit()
        log.info("giveaway_published", giveaway_id=giveaway_id, channel_id=channel_id)
        return {"ok": True, "message_id": message.id}

    # ── Finalize / reroll ───────────────────────────────────────────────────

    async def finalize(self, giveaway_id: int) -> None:
        """Post results + flip the embed after the DB sweep drew winners."""
        app = self.bot.ffd_app
        with app.app_context():
            gw = db.session.get(Giveaway, giveaway_id)
            if gw is None:
                return
            snapshot = {
                "channel_id": gw.channel_id,
                "message_id": gw.message_id,
                "prize": gw.prize,
                "winners": list(gw.winners or []),
                "num_winners": gw.num_winners,
                "end_time": gw.end_time,
                "entrant_count": len(gw.entrants or []),
            }

        channel = await self._get_channel(snapshot["channel_id"])
        if channel is None:
            return

        winners = snapshot["winners"]
        prize = snapshot["prize"]
        entrants = snapshot["entrant_count"]

        if not winners:
            text = f"No winners for **{prize}** — nobody entered the draw."
        elif len(winners) < snapshot["num_winners"]:
            # Fewer entrants than winners: everyone who entered wins (§6.3).
            mentions = " ".join(f"<@{w}>" for w in winners)
            text = (
                f"🎉 **{prize}** — only {entrants} entrant(s), so "
                f"{mentions} all win! Congrats! 🎉"
            )
        else:
            mentions = " ".join(f"<@{w}>" for w in winners)
            text = f"🎉 **{prize}** winners: {mentions} — congrats! 🎉"

        try:
            await channel.send(text)
        except discord.HTTPException as exc:
            log.warning("giveaway_results_failed", giveaway_id=giveaway_id, error=str(exc))

        if snapshot["message_id"]:
            try:
                message = await channel.fetch_message(snapshot["message_id"])
                # Rebuild the ended embed without hitting the ORM row.
                from bot.views.giveaways import _build_embed

                embed = _build_embed(
                    prize,
                    snapshot["end_time"],
                    entrants,
                    snapshot["num_winners"],
                    ended=True,
                )
                await message.edit(embed=embed, view=None)
            except discord.HTTPException as exc:
                log.warning("giveaway_embed_end_failed", giveaway_id=giveaway_id, error=str(exc))

    async def reroll(self, giveaway_id: int) -> dict:
        """Manual re-draw from the dashboard. Appends to winners history."""
        app = self.bot.ffd_app
        with app.app_context():
            gw = db.session.get(Giveaway, giveaway_id)
            if gw is None:
                return {"ok": False, "error": "not_found"}
            if gw.status == "ACTIVE":
                return {"ok": False, "error": "still_active"}

            winners, shortfall = draw_winners(
                entrants=gw.entrants or [],
                num_winners=gw.num_winners,
                exclude=gw.winners or [],
            )
            if not winners:
                return {"ok": False, "error": "no_remaining_entrants"}

            gw.winners = (gw.winners or []) + winners
            db.session.commit()
            new_winners = list(winners)
            channel_id = gw.channel_id
            prize = gw.prize

        channel = await self._get_channel(channel_id)
        if channel is not None:
            mentions = " ".join(f"<@{w}>" for w in new_winners)
            try:
                await channel.send(
                    f"🎉 Re-roll for **{prize}**: {mentions} — congrats! 🎉"
                )
            except discord.HTTPException as exc:
                log.warning("giveaway_reroll_post_failed", error=str(exc))

        log.info("giveaway_rerolled", giveaway_id=giveaway_id, winners=new_winners)
        return {"ok": True, "winners": new_winners}

    # ── Persistent view re-registration ─────────────────────────────────────

    @classmethod
    async def register_persistent_views(cls, bot) -> int:
        """Re-register entry views for all ACTIVE giveaways with a message."""
        from bot.views.giveaways import GiveawayView

        app = bot.ffd_app
        with app.app_context():
            rows = (
                Giveaway.query.filter(
                    Giveaway.status == "ACTIVE", Giveaway.message_id.isnot(None)
                )
                .all()
            )
            pairs = [
                (gw.id, gw.prize, gw.end_time, gw.num_winners, gw.message_id)
                for gw in rows
            ]
        registered = 0
        for giveaway_id, prize, end_time, num_winners, message_id in pairs:
            view = GiveawayView(giveaway_id, prize, end_time, num_winners)
            bot.add_view(view, message_id=message_id)
            registered += 1
        if registered:
            log.info("giveaway_views_registered", count=registered)
        return registered

    async def _get_channel(self, channel_id: int) -> discord.TextChannel | None:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return None
        return channel  # type: ignore[return-value]
