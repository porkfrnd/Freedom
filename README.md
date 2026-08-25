# Freedom for Dance

A community platform for dancers — challenges, giveaways, playlists, and the people who make the floor come alive.

> ⚠️ **Development build** — this is shared for iteration, not production software.

---

## What it does

- **Challenges** — admins and teachers post dance challenges with deadlines; members submit YouTube/Instagram links
- **Giveaways** — members enter with one click, winners drawn fairly and shown in the open (deadline-based or open-ended, admin draws whenever)
- **Announcements** — categorized news (Challenge / Change / Event / General) with filter pills
- **Playlists** — shared music sets with an embedded YouTube player — click a track, it plays in-page. Save your favorites with the heart button
- **Events** — session calendar with RSVP (going / maybe)
- **Members** — searchable directory with dance style filters
- **Leaderboard** — community stats ranked by challenges, playlists, and giveaways
- **User profiles** — avatar, bio, dance styles, social links, activity stats, privacy controls
- **Settings** — change password, email, username, avatar color, bio, social links
- **Teacher role** — teachers can post challenges; admins manage roles via UID search
- **Admin panel** — user management, promote/demote teachers, toggle admins
- **Onboarding** — first-time flow to pick dance styles and avatar color

## Design system

The UI is a **Pure Matte** dark theme — flat surfaces, 1px borders, no gradients, no glassmorphism:

- Base `#111111`, cards `#1A1A1A`, borders `#262626`
- Muted accent palette: lavender `#8B7EC8`, blue, teal, sage, amber, rose
- Editorial serif accents (Instrument Serif italic) for personality moments
- Lucide icons via CDN, Inter for UI type
- Micro-interactions: card lifts, pulsing live badges, marquee ticker, rotating hero badge, film grain overlay
- Honest zero-states: founding-member block replaces fake stats on an empty community; "House rules" instead of fabricated testimonials

## Tech stack

- **Backend:** Flask + SQLAlchemy + Jinja2
- **Auth:** Email/password with PBKDF2 (werkzeug) + JWT session cookies, CSRF double-submit with rotation, login rate limiting
- **Database:** SQLite (dev) / PostgreSQL via Neon (production on Render)
- **Frontend:** Tailwind CSS (CDN) + vanilla JS — no framework, no build step
- **Fonts:** Inter (body/UI) + Instrument Serif (editorial accents)
- **Deploy:** Render single service (`gunicorn`), Alembic migrations on deploy

---

## Quick start

```bash
# Clone
git clone https://github.com/porkfrnd/Freedom.git
cd Freedom

# Create virtualenv
python3 -m venv .
source bin/activate

# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with your values (SECRET_KEY at minimum)

# Seed the database with demo data
FLASK_ENV=development python scripts/seed_db.py

# Run
flask run
```

Opens at `http://localhost:5000`.

---

## Demo accounts

| Account | Email | Password | Role |
|---|---|---|---|
| Admin | `demo@freedom.dance` | `demo1234` | Can post everything, manage users |
| Teacher | `teacher@freedom.dance` | `teacher123` | Can post challenges |
| Members | `mira@`, `dante@`, `noor@freedom.dance` | `password123` | Standard members |

---

## Project structure

```
├── app.py                  # Flask app factory (CSRF, session refresh, rate limits)
├── config.py               # Configuration (env vars)
├── models.py               # SQLAlchemy models + palette/style constants
├── extensions.py           # db instance
├── blueprints/
│   ├── main.py             # Landing page
│   ├── auth.py             # Register / login / logout
│   ├── dashboard.py        # Community hub (challenges, giveaways, etc.)
│   ├── settings.py         # User settings + profile pages
│   └── api.py              # Playlist REST API
├── services/
│   └── auth.py             # JWT token creation/verification (cached)
├── utils/
│   ├── decorators.py       # require_login, require_admin, require_teacher
│   ├── logging.py          # Structured logging
│   ├── ratelimit.py        # Simple rate limiter
│   ├── validate.py         # Shared validation helpers
│   └── security.py         # CSRF protection (cached)
├── templates/              # Jinja2 templates (Pure Matte theme)
├── static/
│   ├── css/tokens.css      # Design tokens (colors, spacing, motion)
│   ├── css/app.css         # Component layer
│   └── js/app.js           # Toasts + shared behaviors
├── tests/                  # pytest suite (64 tests)
├── scripts/
│   └── seed_db.py          # Demo data seeder
├── migrations/             # Alembic (Postgres migrations)
└── requirements.txt
```

---

## Running tests

```bash
FFD_SKIP_APP_CREATION=1 FLASK_ENV=development python -m pytest tests/ -q
```

---

## Maintenance

```bash
# Delete expired giveaways/submissions/events past their retention window
flask prune-old-data
```

---

## Environment variables

See `.env.example` for the full list. Required:

- `SECRET_KEY` — JWT signing key (any long random string)
- `DATABASE_URL` — Postgres URL for production (SQLite used automatically in dev)

Optional:

- `FLASK_ENV` — `development` or `production`
- `JWT_TTL_HOURS` — Session lifetime (default: 24)
- `JWT_REFRESH_AFTER_HOURS` — Silent cookie re-issue threshold (default: 6)
- `LOGIN_RATE_LIMIT_*` — Login throttling knobs

---

## License

Not yet licensed. All rights reserved by the Freedom for Dance team.
