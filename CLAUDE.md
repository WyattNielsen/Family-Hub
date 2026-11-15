# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development

**Every change requires a Docker rebuild to take effect** — frontend files are copied into the image at build time, not volume-mounted.

```bash
# Rebuild and restart (run from repo root)
docker compose up --build -d

# View logs
docker logs family-hub -f
docker logs family-hub-worker -f

# Check DB directly
docker exec -it family-hub-postgres psql -U familyhub -d familyhub
```

There are no tests, linter configs, or local dev server — the only way to run the app is via Docker (or the Proxmox LXC installer).

## Architecture

Four services share one Compose stack (and three systemd units on LXC):

| Service | Role |
|---|---|
| `familyhub` (web) | FastAPI + static frontend on `:3000` (`uvicorn --workers 2`) |
| `worker` | Background calendar sync, chore catch-up, integration pollers |
| `postgres` | PostgreSQL 16 (named volume `pgdata`) |
| `go2rtc` | WebRTC camera streaming (`:8555` TCP/UDP) |

```
backend/
  main.py           # FastAPI app, lifespan, cache listener, router registration
  worker.py         # Dedicated background process (LISTEN fh_jobs + scheduled pollers)
  database.py       # psycopg pools, settings cache, snapshots, init_db()
  migrate_sqlite.py # One-time import from /data/familyhub.db on first boot
  go2rtc_sync.py    # Push camera sources to go2rtc
  routers/          # one file per domain area (+ bootstrap.py aggregates)
frontend/
  js/api.js         # Shared: API helper, AbortController, bootstrap, theme, PWA
  js/<page>.js      # Per-page logic
  <page>.html       # Each page is a full HTML file with sidebar
  css/style.css     # Single stylesheet
```

**Background work** lives in `worker.py` (not the web process):
- Calendar sync (ETag/hash + row diff) on the configured interval or via `NOTIFY fh_jobs`
- Recurring chore catch-up
- TTS chore reminders
- Home Assistant / Weather / Stocks / SchoolCafe / Nutrislice snapshot refresh into `integration_snapshots`
- go2rtc stream provisioning + camera snapshot warm-up

## Backend Patterns

**Database access** — use the pooled `DBConn` context manager (PostgreSQL via psycopg3):
```python
with get_db() as conn:
    rows = conn.execute("SELECT * FROM chores WHERE id=%s", (id,)).fetchall()
    return [dict(r) for r in rows]
# `?` placeholders are auto-translated to `%s` by DBConn
```

Async routes that only need settings/snapshots should prefer:
```python
from database import get_settings_map_async, get_snapshot_async, notify_worker_async
```

**Settings API** — key/value table, read and written as plain dicts:
```python
# Read
get_setting_value("timezone", "America/New_York")

# Write (POST /api/settings/)
{ "timezone": "America/New_York", "dark_mode_start": "21" }  # NOT { key: "...", value: "..." }
```

**Local-first integrations** — SchoolCafe, Nutrislice, Weather, Stocks, and HA states are served from `integration_snapshots` (Postgres JSONB + RAM). User GETs must not await external HTTP; wake the worker with `notify_worker("refresh_meals")` / `notify_worker_async(...)` instead.

**Aggregate endpoints** — prefer `/api/bootstrap` (shell) and `/api/dashboard` (dashboard widgets) over fan-out of many GETs.

**Chores recurrence model** — there is no separate schedule table. Each occurrence is its own row in `chores`. When a recurring chore is completed, `_create_next_recurrence()` inserts the next row. The worker runs `advance_recurring_chores()` on a schedule.

**Chore points** — there is no points table. Points are computed as `SUM(points) WHERE completed=1` in the leaderboard query.

**Chore season reset** — `POST /api/chores/reset?mode=points` deletes all completed rows (zeroes everyone's score). `mode=all` deletes every chore row for a full fresh start. Button in Settings → Family → Chore Season Reset.

**Schema** — created in `init_db()` (`SCHEMA_SQL` in `database.py`). Additive columns use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. Legacy SQLite DBs at `/data/familyhub.db` are auto-imported once on first Postgres boot.

## Frontend Patterns

**Cache busting** — JS and CSS are versioned with `?v=N` on every `<script>` and `<link>` tag. After any frontend change, bump the version in all HTML files:
```bash
# Current version is ?v=94 — change both numbers when bumping
sed -i 's/?v=94/?v=95/g' frontend/*.html
```

**Page initialization** — every page's `DOMContentLoaded` must call `await initAppTimezone()` first before rendering anything date-related. Shell data is loaded once via `/api/bootstrap` in `api.js`.

**Globals from api.js** available on every page:
- `API.get/post/put/delete(path, body?)` — fetch wrapper; page GETs abort on navigation
- `showToast(msg, type)` — pill toast; type: `'success'|'error'|'info'|''`
- `TZ` — app timezone string (e.g. `"America/New_York"`), populated after `initAppTimezone()`
- `toETDateStr(date)` — returns `YYYY-MM-DD` in the configured timezone
- `connectCameraWebRTC(entityId, videoEl)` — WebRTC via go2rtc signaling proxy

**All-day date handling** — `new Date("2026-03-06")` parses as UTC midnight and gives the wrong local date. Always compare all-day dates as strings: `event.start_datetime.slice(0, 10)` vs `toETDateStr(new Date())`. When constructing Date objects for display use the local constructor: `new Date(year, month - 1, day)`.

## Environment Variables

Set in `.env` (gitignored), passed into containers via `env_file` in `docker-compose.yaml`:

| Variable | Purpose |
|---|---|
| `APP_BASE_URL` | Public URL of the app — Google OAuth redirects + WebRTC ICE host hint |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID (Calendar sync) |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret |
| `DATABASE_URL` | Postgres DSN (Compose default: `postgresql://familyhub:familyhub@postgres:5432/familyhub`) |
| `GO2RTC_URL` | go2rtc API (Compose default: `http://go2rtc:1984`) |
| `DB_PATH` | Legacy SQLite path used only for one-time migration (default `/data/familyhub.db`) |

## Key Integrations

- **Google / Apple Calendar** — OAuth/iCloud credentials in `calendar_accounts`; calendars in `calendar_sources` assigned to a member or marked `is_family` (shared). Person write via `members.write_calendar_source_id`; family write via settings `family_write_calendar_source_id`. Diff sync/write in `routers/calendar.py` (worker-driven).
- **Home Assistant** — URL + long-lived token stored in settings. States are snapshotted by the worker; optimistic updates on cover/alarm actions. TTS uses `tts.speak`.
- **Cameras** — Live WebRTC via go2rtc (`POST /api/security/camera/{id}/webrtc`). Optional direct `stream_url` / RTSP per camera. JPEG snapshots still served at `/api/security/camera/{id}/snapshot`.
- **School meals** — SchoolCafe (+ legacy Nutrislice lunch) snapshotted by the worker into `integration_snapshots`.
- **Weather** — zip code based, stored in settings (`weather_zip`), snapshotted by the worker.
