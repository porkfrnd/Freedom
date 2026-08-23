# Freedom for Dance

A community platform for dancers — challenges, giveaways, playlists, and the people who make the floor come alive.

> ⚠️ **Development build** — this is shared for iteration, not production software.

---

## What it does

- **Challenges** — admins and teachers post dance challenges with deadlines; members submit YouTube/Instagram links
- **Giveaways** — members enter with one click, winners drawn fairly and shown in the open
- **Announcements** — categorized news (Challenge / Change / Event / General)
- **Playlists** — shared music sets with an embedded YouTube player — click a track, it plays in-page. Save your favorites with the heart button
- **Events** — session calendar with RSVP (going / maybe)
- **Members** — searchable directory with dance style filters
- **Leaderboard** — community stats ranked by challenges, playlists, and giveaways
- **User profiles** — avatar, bio, dance styles, social links, activity stats
- **Settings** — change password, email, username, avatar color, accent theme, privacy controls
- **Dark/Light mode** — toggle with 6 accent color options
- **Teacher role** — teachers can post challenges; admins manage roles
- **Admin panel** — user management, role promotion, UID search
- **Onboarding** — first-time flow to pick dance styles and avatar color

## Tech stack

- **Backend:** Flask + SQLAlchemy + Jinja2
- **Auth:** Email/password with PBKDF2 (werkzeug) + JWT session cookies
- **Database:** SQLite (dev) / PostgreSQL (production via Render)
- **Frontend:** Tailwind CSS (CDN) + vanilla JS — no framework
- **Fonts:** Space Grotesk (display) + Inter (body)
- **Deploy:** Render (single-service, `gunicorn`)

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

## Demo accounts

| Account | Email | Password | Role |
|---|---|---|---|
| Admin | `demo@freedom.dance` | `demo1234` | Can post everything, manage users |
| Teacher | `teacher@freedom.dance` | `teacher123` | Can post challenges |
| Members | `mira@`, `dante@`, `noor@freedom.dance` | `password123` | Standard members |

## Project structure

```
├── app.py                  # Flask app factory
├── config.py               # Configuration (env vars)
├── models.py               # SQLAlchemy models
├── extensions.py           # db instance
├── blueprints/
│   ├── main.py             # Landing page
│   ├── auth.py             # Register / login / logout
│   ├── dashboard.py        # Community hub (challenges, giveaways, etc.)
│   ├── settings.py         # User settings + profile pages
│   └── api.py              # Playlist REST API
├── services/
│   └── auth.py             # JWT token creation/verification
├── utils/
│   ├── decorators.py       # require_login, require_admin, require_teacher
│   ├── logging.py          # Structured logging
│   ├── ratelimit.py        # Simple rate limiter
│   └── security.py         # CSRF protection
├── templates/              # Jinja2 templates
├── static/                 # CSS, JS, images
├── tests/                  # pytest test suite
├── scripts/
│   └── seed_db.py          # Demo data seeder
├── migrations/             # Alembic (Postgres migrations)
└── requirements.txt
```

## Running tests

```bash
FFD_SKIP_APP_CREATION=1 FLASK_ENV=development python -m pytest tests/ -q
```

## Maintenance

```bash
# Delete expired giveaways/submissions/events past their retention window
flask prune-old-data
```

## Environment variables

See `.env.example` for the full list. Required:

- `SECRET_KEY` — JWT signing key (any long random string)
- `DATABASE_URL` — Postgres URL for production (SQLite used automatically in dev)

Optional:

- `FLASK_ENV` — `development` or `production`
- `JWT_TTL_HOURS` — Session lifetime (default: 24)

## License

Not yet licensed. All rights reserved by the Freedom for Dance team.
