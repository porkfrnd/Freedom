"""AI moderation cog (§6.2).

Flow per message:
1. Skip bots, DMs, empty/command content, and runs with no Groq key.
2. Classify via the Groq client (5s timeout) behind the circuit breaker.
3. When the breaker is OPEN: log-only mode — store the message content
   with action ``NONE`` and alert admins once per open period.
4. Otherwise: apply the severity/action matrix, escalate repeat offenses
   via ``resolve_action`` (the single auditable escalation function), then
   log the incident.

All Discord-side actions (DM warning, timeout, mod-channel alert) are
defensive: any failure is caught and logged so the listener never crashes.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import discord
from discord.ext import commands

from extensions import db
from models import ModerationLog, utcnow
from services.groq_moderation import (
    CircuitBreaker,
    CircuitOpenError,
    GroqModerationClient,
    ModerationVerdict,
)
from utils.logging import get_logger

log = get_logger("bot.cogs.moderation")

# Category used for log-only entries written while the breaker is open.
# Excluded from the repeat-offense escalation counts.
CATEGORY_UNRESOLVED = "UNRESOLVED"

# Duration mapping for the action matrix.
TIMEOUT_SHORT = timedelta(minutes=10)
TIMEOUT_LONG = timedelta(hours=24)

# Base action per classified tier (before escalation).
_BASE_ACTION = {1: "WARNING", 2: "TIMEOUT_SHORT", 3: "TIMEOUT_LONG"}

# Product-voice copy for the private DM warning.
_WARNING_DM = (
    "Hey — one of your messages in Freedom for Dance was flagged by our "
    "moderation. Keep it respectful so everyone can dance."
)


def resolve_action(tier: int, recent_counts: dict[int, int]) -> str:
    """The ONE auditable escalation rule (§6.2).

    ``tier`` is the freshly classified severity (1-3). ``recent_counts``
    maps severity_tier -> number of PRIOR incidents in the rolling 30-day
    window for this user (the current incident is not included).

    Rule (escalation is one step per repeat, counted by PRIOR incidents):
    - Tier 3 → ``TIMEOUT_LONG``.
    - Tier 2 → ``TIMEOUT_SHORT``; a prior Tier 2 in the window (this is the
      second or later) → ``TIMEOUT_LONG``.
    - Tier 1 → ``WARNING``; one prior Tier 1 (this is the second) →
      ``TIMEOUT_SHORT`` (tier-2 behavior); two or more prior →
      ``TIMEOUT_LONG``.

    This function is deliberately side-effect free so the rule is testable
    and auditable in exactly one place.
    """
    t1 = recent_counts.get(1, 0)
    t2 = recent_counts.get(2, 0)

    if tier == 3:
        return "TIMEOUT_LONG"
    if tier == 2:
        return "TIMEOUT_LONG" if t2 >= 1 else "TIMEOUT_SHORT"
    # tier == 1
    if t1 >= 2:
        return "TIMEOUT_LONG"
    if t1 >= 1:
        return "TIMEOUT_SHORT"
    return "WARNING"


def count_recent_incidents(app, user_id: int, window_days: int = 30) -> dict[int, int]:
    """Prior incidents for ``user_id`` inside the rolling window (excludes
    UNRESOLVED log-only entries)."""
    cutoff = utcnow() - timedelta(days=window_days)
    rows = (
        db.session.query(ModerationLog.severity_tier, db.func.count())
        .filter(
            ModerationLog.user_id == user_id,
            ModerationLog.timestamp >= cutoff,
            ModerationLog.violation_category != CATEGORY_UNRESOLVED,
        )
        .group_by(ModerationLog.severity_tier)
        .all()
    )
    return {tier: count for tier, count in rows}


class ModerationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        cfg = bot.ffd_app.config
        self.breaker = CircuitBreaker(
            failure_threshold=cfg["GROQ_CIRCUIT_FAILURE_THRESHOLD"],
            cooldown_seconds=cfg["GROQ_CIRCUIT_COOLDOWN_SECONDS"],
        )
        self.groq = GroqModerationClient(
            api_key=cfg["GROQ_API_KEY"],
            model=cfg["GROQ_MODEL"],
            timeout=cfg["GROQ_TIMEOUT_SECONDS"],
            breaker=self.breaker,
        )

    # ── Listener ───────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if message.guild is None:
            return
        content = (message.content or "").strip()
        if not content:
            return
        if content.startswith("ffd::"):  # never analyze our own prefix space
            return
        if not self.groq.enabled:
            return  # no GROQ_API_KEY — moderation listener is idle

        try:
            verdict = await self.groq.classify(content)
        except CircuitOpenError:
            await self._log_only(message, reason="circuit open — log-only mode")
            await self._alert_breaker_once()
            return
        except Exception as exc:  # noqa: BLE001 - a failed call must not crash the listener
            log.warning("moderation_classification_failed", error=str(exc))
            return

        if verdict.severity_tier == 0:
            return  # not a violation

        action = resolve_action(
            verdict.severity_tier,
            count_recent_incidents(self.bot.ffd_app, message.author.id),
        )
        await self._apply_action(message, verdict, action)

    # ── Actions ────────────────────────────────────────────────────────────

    async def _apply_action(
        self, message: discord.Message, verdict: ModerationVerdict, action: str
    ) -> None:
        tier = verdict.severity_tier
        member = message.author
        guild = message.guild

        try:
            await member.send(_WARNING_DM)
            dm_sent = True
        except discord.Forbidden:
            dm_sent = False
            log.info("moderation_dm_blocked", user_id=member.id)
        except discord.HTTPException as exc:
            dm_sent = False
            log.warning("moderation_dm_failed", user_id=member.id, error=str(exc))

        if action in ("TIMEOUT_SHORT", "TIMEOUT_LONG"):
            duration = TIMEOUT_SHORT if action == "TIMEOUT_SHORT" else TIMEOUT_LONG
            try:
                await member.timeout(
                    duration,
                    reason=f"FFD moderation: {verdict.category} (tier {tier})",
                )
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning("moderation_timeout_failed", user_id=member.id, error=str(exc))

        await self._alert_moderators(guild, message, verdict, action, dm_sent)
        await self._write_log(message, verdict, action, dm_sent)

    async def _log_only(self, message: discord.Message, reason: str) -> None:
        """Circuit-breaker log-only mode: store content, take no action."""
        await self._write_log(
            message,
            verdict=ModerationVerdict(
                category=CATEGORY_UNRESOLVED,
                severity_tier=1,
                reasoning=reason,
                suggested_action="none",
            ),
            action="NONE",
            dm_sent=False,
            is_log_only=True,
        )

    async def _write_log(
        self,
        message: discord.Message,
        verdict: ModerationVerdict,
        action: str,
        dm_sent: bool,
        is_log_only: bool = False,
    ) -> None:
        analysis = {
            "category": verdict.category,
            "reasoning": verdict.reasoning,
            "suggested_action": verdict.suggested_action,
        }
        if is_log_only:
            analysis["note"] = "circuit breaker open — content stored, no action taken"
        entry = ModerationLog(
            user_id=message.author.id,
            content=message.content[:1900],
            violation_category=verdict.category,
            severity_tier=verdict.severity_tier,
            groq_analysis=analysis,
            action_taken=action,
        )
        try:
            with self.bot.ffd_app.app_context():
                db.session.add(entry)
                db.session.commit()
        except Exception as exc:  # noqa: BLE001 - DB hiccup must not crash the listener
            log.error("moderation_log_write_failed", error=str(exc))

    async def _alert_moderators(
        self,
        guild: discord.Guild,
        message: discord.Message,
        verdict: ModerationVerdict,
        action: str,
        dm_sent: bool,
    ) -> None:
        cfg = self.bot.ffd_app.config
        try:
            channel_id = cfg.get("DISCORD_MODERATION_CHANNEL_ID")
            if channel_id:
                channel = guild.get_channel(int(channel_id)) or await guild.fetch_channel(
                    int(channel_id)
                )
            else:
                channel = guild.system_channel
            if channel is None:
                return

            embed = discord.Embed(
                title=f"Moderation alert — {verdict.category}",
                color=0xFF4D6D if verdict.severity_tier >= 2 else 0xFFB020,
                description=message.content[:500],
            )
            embed.add_field(name="User", value=f"{message.author.mention} ({message.author})")
            embed.add_field(name="Severity", value=f"Tier {verdict.severity_tier}")
            embed.add_field(name="Action", value=action)
            if verdict.reasoning:
                embed.add_field(name="Why", value=verdict.reasoning, inline=False)
            embed.set_footer(text=f"#{message.channel.name} • DM sent: {dm_sent}")

            content = None
            if verdict.severity_tier == 3 and cfg.get("MODERATOR_ROLE_IDS"):
                content = " ".join(
                    f"<@&{rid}>" for rid in cfg["MODERATOR_ROLE_IDS"]
                )
            await channel.send(content=content, embed=embed)
        except Exception as exc:  # noqa: BLE001 - alerts are best-effort
            log.warning("moderation_alert_failed", error=str(exc))

    async def _alert_breaker_once(self) -> None:
        """Alert admins once per open period that moderation went log-only."""
        if self.breaker.alerted_once:
            return
        self.breaker.alerted_once = True
        cfg = self.bot.ffd_app.config
        guild_id = cfg.get("DISCORD_GUILD_ID")
        if not guild_id:
            return
        try:
            guild = self.bot.get_guild(int(guild_id))
            if guild is None:
                return
            channel_id = cfg.get("DISCORD_MODERATION_CHANNEL_ID")
            channel = None
            if channel_id:
                channel = guild.get_channel(int(channel_id)) or await guild.fetch_channel(
                    int(channel_id)
                )
            channel = channel or guild.system_channel
            if channel is None:
                return
            await channel.send(
                "**Moderation AI is offline.** It'll keep storing flagged "
                "messages but won't act on them until the breaker resets."
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("breaker_alert_failed", error=str(exc))
