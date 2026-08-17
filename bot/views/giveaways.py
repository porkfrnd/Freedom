"""Interactive giveaway embed view (§6.3).

The view is persistent (``timeout=None``) with a stable ``custom_id`` of
``ffd_giveaway:<giveaway_id>``, so entry buttons survive bot restarts: on
boot the cog re-registers one view per ACTIVE giveaway via ``bot.add_view``.

Entry flow: rate-limit check → duplicate check against ``Giveaway.entrants``
→ append entrant → update the embed's entrant count. A giveaway that is
still ACTIVE in the DB but past its end time is finalized on the spot.
"""

from __future__ import annotations

import discord

from utils.logging import get_logger
from utils.ratelimit import RateLimiter

log = get_logger("bot.views.giveaways")

# Shared per-user entry limiter (3 presses / 30s) — keyed by user id.
_entry_limiter = RateLimiter(limit=3, window_seconds=30)

_SLOW_DOWN_MSG = "Easy on the button — give it a second."
_ALREADY_IN_MSG = "You're already in the draw. Good luck! 🍀"
_ENTERED_MSG = "You're in! 🎉"
_ENDED_MSG = "That giveaway has ended. 🎬"


def _build_embed(prize: str, end_time, entrants_count: int, num_winners: int, ended: bool = False) -> discord.Embed:
    embed = discord.Embed(
        title=f"🎉 Giveaway: {prize}",
        color=0xFF2E9A,  # spotlight-magenta
    )
    if ended:
        embed.description = "This giveaway has ended."
    embed.add_field(name="Ends", value=discord.utils.format_dt(end_time, style="R"))
    embed.add_field(name="Winners", value=str(num_winners))
    embed.add_field(name="Entrants", value=str(entrants_count))
    embed.set_footer(text="Freedom for Dance")
    return embed


class GiveawayButton(discord.ui.Button["GiveawayView"]):
    def __init__(self, giveaway_id: int):
        super().__init__(
            label="Enter Giveaway",
            style=discord.ButtonStyle.success,
            emoji="🎉",
            custom_id=f"ffd_giveaway:{giveaway_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.view.handle_entry(interaction)


class GiveawayView(discord.ui.View):
    """Persistent view for one giveaway's entry button."""

    def __init__(self, giveaway_id: int, prize: str, end_time=None, num_winners: int = 1):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        self.prize = prize
        self.end_time = end_time
        self.num_winners = num_winners
        self.add_item(GiveawayButton(giveaway_id))

    async def handle_entry(self, interaction: discord.Interaction) -> None:
        from extensions import db
        from models import Giveaway

        if not _entry_limiter.allow(f"{interaction.user.id}"):
            await interaction.response.send_message(_SLOW_DOWN_MSG, ephemeral=True)
            return

        app = interaction.client.ffd_app
        with app.app_context():
            giveaway = db.session.get(Giveaway, self.giveaway_id)
            if giveaway is None or giveaway.status == "CANCELLED":
                await interaction.response.send_message(_ENDED_MSG, ephemeral=True)
                return
            if giveaway.status == "ACTIVE" and giveaway.has_ended:
                # Sweep hasn't run yet — finalize right now.
                from bot.cogs.giveaways import draw_winners

                winners, _ = draw_winners(
                    entrants=giveaway.entrants or [],
                    num_winners=giveaway.num_winners,
                    exclude=giveaway.winners or [],
                )
                giveaway.winners = (giveaway.winners or []) + winners
                giveaway.status = "ENDED"
                await interaction.response.send_message(_ENDED_MSG, ephemeral=True)
            elif giveaway.status != "ACTIVE":
                await interaction.response.send_message(_ENDED_MSG, ephemeral=True)
                return
            else:
                added = giveaway.add_entrant(interaction.user.id)
                await interaction.response.send_message(
                    _ENTERED_MSG if added else _ALREADY_IN_MSG, ephemeral=True
                )
                if not added:
                    return
            db.session.commit()
            entrants_count = len(giveaway.entrants or [])
            ended = giveaway.status != "ACTIVE"
            prize = giveaway.prize
            end_time = giveaway.end_time
            num_winners = giveaway.num_winners

        try:
            message = interaction.message
            embed = _build_embed(
                prize, end_time, entrants_count, num_winners, ended=ended
            )
            view = None if ended else self
            await message.edit(embed=embed, view=view)
        except discord.HTTPException as exc:  # noqa: BLE001 - embed refresh is best-effort
            log.warning("giveaway_embed_update_failed", error=str(exc))
