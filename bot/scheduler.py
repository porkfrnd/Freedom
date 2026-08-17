"""Background scheduler (§6.3, §6.2, §8).

Runs three jobs on APScheduler's own thread:

* ``giveaway-sweep`` — every 30s, finalize ACTIVE giveaways past their end
  time (draw winners, post results) instead of blocking with ``sleep``.
* ``mod-log-purge`` — daily, null out flagged message content older than
  the retention window (default 90 days); the audit row survives.
* ``membership-refresh`` — every 15 min, re-verify guild membership for
  recently active users and bump ``session_version`` when someone leaves,
  which instantly invalidates their JWT (§3.2).

Every job pushes an app context so it can touch the database safely from a
non-request thread.
"""

from __future__ import annotations

from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from extensions import db
from models import Giveaway, ModerationLog, User, utcnow
from services import discord_api
from utils.logging import get_logger

log = get_logger("bot.scheduler")

# Cap on users checked per membership-refresh run (keeps the Discord API
# call volume bounded).
MEMBERSHIP_REFRESH_BATCH = 200


class AppScheduler:
    def __init__(self, app, bot_runtime):
        self.app = app
        self.bot_runtime = bot_runtime
        self.scheduler = BackgroundScheduler(timezone="UTC", daemon=True)

    def start(self) -> None:
        cfg = self.app.config
        self.scheduler.add_job(
            self._sweep_giveaways,
            "interval",
            seconds=cfg["GIVEAWAY_SWEEP_SECONDS"],
            max_instances=1,
            coalesce=True,
            id="giveaway-sweep",
        )
        self.scheduler.add_job(
            self._purge_mod_log_content,
            CronTrigger(hour=4, minute=17),
            max_instances=1,
            coalesce=True,
            id="mod-log-purge",
        )
        self.scheduler.add_job(
            self._refresh_memberships,
            "interval",
            minutes=15,
            max_instances=1,
            coalesce=True,
            id="membership-refresh",
        )
        self.scheduler.start()
        log.info("scheduler_started")

    def shutdown(self) -> None:
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:  # pragma: no cover - already stopped
            pass

    # ── Giveaway sweep (§6.3) ──────────────────────────────────────────────

    def _sweep_giveaways(self) -> None:
        with self.app.app_context():
            overdue = (
                Giveaway.query.filter(
                    Giveaway.status == "ACTIVE", Giveaway.end_time <= utcnow()
                )
                .order_by(Giveaway.end_time.asc())
                .all()
            )
            if not overdue:
                return
            log.info("giveaway_sweep_found_overdue", count=len(overdue))
            ids = [gw.id for gw in overdue]

        for giveaway_id in ids:
            self._finalize(giveaway_id)

    def _finalize(self, giveaway_id: int) -> None:
        """Finalize one giveaway. DB state always updates; the Discord
        results post happens only when the bot thread is alive."""
        from bot.cogs.giveaways import GiveawayCog

        finalized = self._finalize_db(giveaway_id)
        if not finalized:
            return
        if self.bot_runtime.is_alive():
            try:
                cog = self.bot_runtime.client.get_cog("GiveawayCog")
                self.bot_runtime.submit(cog.finalize(giveaway_id))
            except Exception as exc:  # noqa: BLE001 - sweep must not crash
                log.error("giveaway_finalize_submit_failed", giveaway_id=giveaway_id, error=str(exc))
        else:
            log.info("giveaway_finalized_without_bot", giveaway_id=giveaway_id)

    def _finalize_db(self, giveaway_id: int) -> bool:
        """Draw winners and flip status in the DB. Returns False when the
        giveaway was already finalized or no longer exists."""
        from bot.cogs.giveaways import draw_winners

        with self.app.app_context():
            gw = db.session.get(Giveaway, giveaway_id)
            if gw is None or gw.status != "ACTIVE":
                return False
            winners, shortfall = draw_winners(
                entrants=gw.entrants or [],
                num_winners=gw.num_winners,
                exclude=gw.winners or [],
            )
            gw.winners = (gw.winners or []) + winners
            gw.status = "ENDED"
            db.session.commit()
            if shortfall > 0:
                log.info(
                    "giveaway_fewer_entrants_than_winners",
                    giveaway_id=giveaway_id,
                    entrants=len(gw.entrants or []),
                    winners=len(winners),
                    shortfall=shortfall,
                )
            return True

    # ── Moderation log content purge (§8) ──────────────────────────────────

    def _purge_mod_log_content(self) -> None:
        cfg = self.app.config
        retention_days = cfg["MOD_LOG_CONTENT_RETENTION_DAYS"]
        cutoff = utcnow() - timedelta(days=retention_days)
        with self.app.app_context():
            rows = (
                ModerationLog.query.filter(
                    ModerationLog.content.isnot(None),
                    ModerationLog.timestamp < cutoff,
                )
                .limit(2000)
                .all()
            )
            for row in rows:
                row.content = None
                row.content_purged = True
            db.session.commit()
            if rows:
                log.info(
                    "mod_log_content_purged",
                    count=len(rows),
                    retention_days=retention_days,
                )

    # ── Background membership check (§3.2) ─────────────────────────────────

    def _refresh_memberships(self) -> None:
        cfg = self.app.config
        guild_id = cfg.get("DISCORD_GUILD_ID")
        if not guild_id or not cfg.get("DISCORD_BOT_TOKEN"):
            return
        cutoff = utcnow() - timedelta(hours=24)
        with self.app.app_context():
            users = (
                User.query.filter(User.last_seen_at >= cutoff)
                .order_by(User.last_seen_at.desc())
                .limit(MEMBERSHIP_REFRESH_BATCH)
                .all()
            )
            changed = 0
            for user in users:
                member = discord_api.is_guild_member(guild_id, user.discord_id, cfg)
                if member is False:
                    # Left the guild — invalidate their session immediately.
                    user.is_admin = False
                    user.session_version += 1
                    changed += 1
                    log.info(
                        "membership_refresh_left_guild",
                        discord_id=user.discord_id,
                    )
                elif member is True:
                    admin = discord_api.has_administrator(
                        guild_id, user.discord_id, cfg
                    )
                    if admin is not None and admin != user.is_admin:
                        user.is_admin = admin
                        changed += 1
            if changed:
                db.session.commit()
                log.info("membership_refresh_changed", changed=changed)
