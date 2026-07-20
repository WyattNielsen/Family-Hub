# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development

**Every change requires a Docker rebuild to take effect** — frontend files are copied into the image at build time, not volume-mounted.

```bash
# Rebuild and restart (run from repo root)
docker compose up --build -d

# View logs
docker logs family-hub -f

# Check DB directly
sqlite3 data/familyhub.db
```

There are no tests, linter configs, or local dev server — the only way to run the app is via Docker.

## Architecture

A single Docker container serves everything: FastAPI (port 3000) handles all API routes and also serves the frontend static files via `StaticFiles`. SQLite lives at `/data/familyhub.db` (volume-mounted, so it persists across rebuilds). Photos are volume-mounted at `./data/photos`.

```
backend/
  main.py           # FastAPI app, lifespan, background loops, router registration
  database.py       # get_db(), init_db(), schema, migrations
  routers/          # one file per domain area
frontend/
  js/api.js         # Shared: API helper, showToast, date utils, theme, PWA — loaded on every page
  js/<page>.js      # Per-page logic
  <page>.html       # Each page is a full HTML file with sidebar
  css/style.css     # Single stylesheet
```

**Background loops** run in `main.py` via `asyncio.create_task` in the lifespan context:
- `_auto_sync_loop()` — Google Calendar auto-sync (interval from settings)
- `_reminder_loop()` — TTS chore reminders (polls every 30s, fires at matching HH:MM, deduplicates per day)

## Backend Patterns

**Database access** — always close the connection when done:
```python
conn = get_db()            # sqlite3.connect with timeout=30, WAL mode, Row factory
rows = conn.execute("SELECT * FROM chores WHERE id=?", (id,)).fetchall()
return [dict(r) for r in rows]
conn.close()
```

**Routers** each begin with this sys.path hack to import from `database.py`:
```python
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database import get_db
```

**Settings API** — key/value table, read and written as plain dicts:
```python
# Read
conn.execute("SELECT value FROM settings WHERE key = ?", ("timezone",)).fetchone()

# Write (POST /api/settings/)
{ "timezone": "America/New_York", "dark_mode_start": "21" }  # NOT { key: "...", value: "..." }
```

**Chores recurrence model** — there is no separate schedule table. Each occurrence is its own row in `chores`. When a recurring chore is completed, `_create_next_recurrence()` inserts the next row. `_advance_recurring_to_today()` is called at the start of `get_chores()` to catch up any missed days.

**Chore points** — there is no points table. Points are computed as `SUM(points) WHERE completed=1` in the leaderboard query.

**Schema migrations** use bare `ALTER TABLE` wrapped in try/except (see `init_db()` in `database.py`). Add new columns the same way.

## Frontend Patterns

**Cache busting** — JS and CSS are versioned with `?v=N` on every `<script>` and `<link>` tag. After any frontend change, bump the version in all 6 HTML files:
```bash
# Current version is ?v=50 — change both numbers when bumping
sed -i 's/?v=50/?v=51/g' frontend/index.html frontend/calendar.html frontend/chores.html frontend/messages.html frontend/security.html frontend/settings.html
```

**Page initialization** — every page's `DOMContentLoaded` must call `await initAppTimezone()` first before rendering anything date-related. `initTheme()` and `loadMessageBadge()` are called automatically by `api.js`.

**Globals from api.js** available on every page:
- `API.get/post/put/delete(path, body?)` — fetch wrapper, throws on non-OK
- `showToast(msg, type)` — pill toast; type: `'success'|'error'|'info'|''`
- `TZ` — app timezone string (e.g. `"America/New_York"`), populated after `initAppTimezone()`
- `toETDateStr(date)` — returns `YYYY-MM-DD` in the configured timezone

**All-day date handling** — `new Date("2026-03-06")` parses as UTC midnight and gives the wrong local date. Always compare all-day dates as strings: `event.start_datetime.slice(0, 10)` vs `toETDateStr(new Date())`. When constructing Date objects for display use the local constructor: `new Date(year, month - 1, day)`.

## Environment Variables

Set in `.env` (gitignored), passed into the container via `env_file` in `docker-compose.yaml`:

| Variable | Purpose |
|---|---|
| `APP_BASE_URL` | Public URL of the app — used for Google OAuth redirect URIs |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID (Calendar sync) |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret |
| `DB_PATH` | SQLite path (default `/data/familyhub.db`) |

## Key Integrations

- **Google Calendar** — OAuth tokens stored in `family_google_calendar` table (shared) and `members` table (per-person). Sync logic in `routers/calendar.py`.
- **Home Assistant** — URL + long-lived token stored in settings table. All HA calls go through `routers/homeassistant.py`. TTS uses `tts.speak` service with `entity_id` = TTS engine, `media_player_entity_id` = speaker.
- **Cameras** — HA MJPEG proxy via FastAPI `StreamingResponse` at `/api/security/camera/{entity_id}/stream`. No go2rtc.
- **Lunch** — Nutrislice API, school domain stored in settings (`lunch_school`).
- **Weather** — zip code based, stored in settings (`weather_zip`).
