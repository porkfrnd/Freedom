# Freedom for Dance

> _Dancing is a form of expression and we must push it forward._

> **Status: development build.** This is a work-in-progress codebase —
> developmental and known to be bug-filled. It's shared for iteration and
> learning, not production software. The test suite passes and the stack
> boots, but expect rough edges.

A single-service web app + Discord bot for one dance community's server:
members sign in with Discord, browse and share music playlists, play them
in voice channels via Lavalink, enter giveaways, and let admins broadcast
announcements and run moderation — all from one Flask process.

## Architecture at a glance

| Layer | Choice |
|---|---|
| Runtime | Python 3.11+ (developed on 3.13) |
| Web | Flask + Flask-SQLAlchemy, blueprints per domain |
| Auth | Authlib (Discord OAuth2) → JWT in an httponly/Secure/SameSite=Lax cookie (§3.2) |
| Bot | discord.py 2.x in ONE daemon thread sharing the process (§3.1) |
| Voice | wavelink 3.x over a Lavalink node, reconnect-with-backoff |
| AI moderation | Groq async client + circuit breaker (log-only fallback) |
| DB | Neon Postgres (serverless) via SQLAlchemy; small pool (§3.3) |
| Migrations | Alembic — every schema change ships a migration |
| Frontend | Jinja2 + Tailwind (CDN, layout only) + hand-written `tokens.css` (§5) |
| Deploy | Single Render Web Service, gunicorn workers=1 (§10) |

```
┌────────────────────────────── Flask process ──────────────────────────────┐
│  Gunicorn worker (threads=4)                                              │
│   ├─ Web layer: blueprints (main/auth/dashboard/api) + JWT + CSRF        │
│   ├─ Bot thread: discord.py asyncio loop (cogs: moderation, music,       │
│   │               giveaways, announcements) — BotRuntime.submit() bridge │
│   └─ APScheduler thread: giveaway sweep, mod-log purge, membership check │
│                                │                                         │
│                    ┌───────────┴────────────┐                            │
│                    ▼                        ▼                            │
│              Neon Postgres          Discord API / Lavalink / Groq        │
└───────────────────────────────────────────────────────────────────────────┘
```

## Repository layout

```
app.py                 application factory + process bootstrap
config.py              env-driven configuration
models.py              SQLAlchemy schema (single source of truth)
extensions.py          shared singletons (db, oauth)
blueprints/            main · auth · dashboard · api
bot/                   engine (thread + client) · scheduler · cogs · views
services/              auth (JWT) · discord_api · groq_moderation · lavalink
utils/                 logging · security (CSRF) · ratelimit · decorators
static/                css/tokens.css · css/app.css · js/app.js
templates/             Jinja pages (design system §5)
migrations/            Alembic migrations
scripts/seed_db.py     dev-only seed data (hard-gated to FLASK_ENV=development)
tests/                 pytest suite (§9.1 minimum list)
```

## Local development

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env          # fill in Discord credentials etc.

# 3. Create the schema (SQLite fallback when DATABASE_URL is unset)
alembic upgrade head

# 4. Optional: dummy data (development only)
python scripts/seed_db.py

# 5. Run web-only
flask run

# 6. Run the bot thread locally
#    The bot starts automatically at boot when DISCORD_BOT_TOKEN is set
#    (guarded against the dev reloader double-start). If you only want the
#    web app, leave DISCORD_BOT_TOKEN empty — everything web still works.
```

The dev reloader spawns a child process; the bot thread is started only in
the child (`WERKZEUG_RUN_MAIN` guard + an idempotent start lock), so it
never double-connects.

### Running a Lavalink node locally (optional, for music)

1. Download a [Lavalink v4](https://github.com/lavalink-devs/Lavalink/releases) jar.
2. Create `application.yml` with a password, then `java -jar Lavalink.jar`.
3. Point `LAVALINK_URI` / `LAVALINK_PASSWORD` at it and restart the app.

## Environment variables

All secrets come from the environment only — never hardcoded, never logged.
See `.env.example` for the full annotated list. The essentials:

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Signs JWT sessions + CSRF tokens |
| `BASE_URL` | Public origin, builds the OAuth redirect URI |
| `DISCORD_CLIENT_ID/SECRET` | OAuth2 app credentials |
| `DISCORD_BOT_TOKEN` | Bot connection + live admin re-verification |
| `DISCORD_GUILD_ID` | The single guild this app serves (§7.2) |
| `DATABASE_URL` | Neon Postgres DSN (unset → local SQLite dev DB) |
| `GROQ_API_KEY` | AI moderation (unset → moderation listener idle) |
| `LAVALINK_URI/PASSWORD` | Music playback node |
| `SENTRY_DSN` | Error tracking (unset → clean no-op) |

## Security model

* **Sessions** are stateless JWTs (12h, silently refreshed) in a
  `SameSite=Lax` cookie (`secure` in production; `httponly` in production,
  but off in local dev because some browsers/privacy extensions refuse
  HttpOnly cookies on `localhost` — the signed JWT and separate CSRF
  protection make JS-readable cookies acceptable there).
  `is_admin`/`guild_member` are hints for read-only views only.
* **Admin actions are always live-verified**: every admin-gated route
  re-checks guild membership and the Discord `ADMINISTRATOR` bit through
  the bot token (cached ≤5 min). A stale cookie that claims admin but
  fails the live check is blocked and its session invalidated.
* A **background job** re-checks membership for recently active users
  every 15 minutes; leaving the guild bumps `session_version`, which
  instantly kills any outstanding JWT.
* **CSRF** is a signed double-submit token (`ffd_csrf` cookie + header or
  form field) on every state-changing request.
* Jinja autoescaping stays on; user content is never marked `|safe`.

## Moderation log data retention (user data)

`ModerationLog.content` stores the text of flagged messages. This is user
data, retained only as long as the repeat-offense escalation window needs
it (§6.2) **plus a fixed 90-day audit period** (`MOD_LOG_CONTENT_RETENTION_DAYS`).
A daily scheduled job nulls out `content` for entries older than that;
the audit row (user, category, tier, action, timestamp, reasoning) is kept
indefinitely. `content_purged` flags rows whose message text has been
dropped.

## Giveaway behavior notes

* Entries are recorded in the DB (`Giveaway.entrants`, JSONB) with
  duplicate protection and a per-user rate limit on the button.
* The winner sweep runs every 30 seconds — no blocking sleeps.
* **Fewer entrants than winners is handled**: everyone who entered wins,
  the shortfall is logged, and a note posts to the channel.
* Re-rolls append to `winners` and post a follow-up message — history is
  never silently overwritten.

## Deployment (Render)

1. Push this repo to GitHub and create a new Blueprint from `render.yaml`.
2. In the dashboard, fill the manual env vars (`sync: false` group):
   Discord credentials, guild ID, channel IDs, Groq key, Lavalink node,
   `BASE_URL` pointing at your Render URL.
3. `render.yaml` wires `DATABASE_URL` to the managed Postgres, runs
   `alembic upgrade head` in `preDeployCommand` before the web process
   starts, and points the health check at `/healthz` (DB + bot-thread
   liveness).

Discord OAuth: in your Discord developer application, add the redirect URI
`https://<your-app>.onrender.com/auth/callback`. Enable the bot with the
**message content** intent (moderation) and add the bot to the guild.

Note: the bot connects and every non-moderation feature works even if the
**Message Content** intent is not enabled — it logs a
`privileged_intents_falling_back_no_message_content` line and the AI
moderation listener stays idle until the intent is switched on in the
developer portal (Bot → Privileged Gateway Intents).

## Testing

```bash
pytest            # 54 tests — guards, OAuth (mocked), moderation matrix,
                  # circuit breaker, giveaway draws, playlist validation,
                  # CSRF. Never touches real Discord/Groq/Lavalink.
```

## Non-goals (§13)

No native mobile app, no payments, single-guild only, dark theme only, no
livestream/video.
