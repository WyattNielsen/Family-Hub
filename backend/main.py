from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from contextlib import asynccontextmanager
from datetime import date
import asyncio
import os

from database import init_db, get_setting_value_async
from routers import calendar, chores, members, auth, settings, photos, lunch, weather, homeassistant, stocks, security, messages, meals, schoolcafe, shopping
import json
import jobs
import outbound

async def _reminder_loop():
    _fired_today = set()  # track (time_str) already fired today so we don't double-fire
    _last_date = None
    while True:
        try:
            await asyncio.sleep(30)
            from datetime import datetime as _dt
            now = _dt.now()
            today = now.date().isoformat()
            if _last_date != today:
                _fired_today.clear()
                _last_date = today
            hhmm = now.strftime("%H:%M")
            raw = await get_setting_value_async("chore_reminders", "")
            if not raw:
                continue
            reminders = json.loads(raw)
            for r in reminders:
                if not r.get("enabled"):
                    continue
                t = r.get("time", "")
                if t == hhmm and t not in _fired_today:
                    _fired_today.add(t)
                    await homeassistant.fire_tts(r.get("message", ""), r.get("entity_id", ""), r.get("tts_engine", "tts.google_translate_en_com"))
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Reminder loop error: {e}")

_chores_advanced_on = None

async def _advance_chores_if_new_day():
    """New occurrences only appear when the date changes, so run once per day.
    A failed run leaves the date unset and is retried on the next check."""
    global _chores_advanced_on
    today = date.today()
    if _chores_advanced_on == today:
        return
    await asyncio.to_thread(chores.advance_recurring_chores)
    _chores_advanced_on = today

async def _chore_advance_loop():
    """Catch up recurring chores away from request handlers."""
    while True:
        try:
            await asyncio.sleep(60)
            await _advance_chores_if_new_day()
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Chore advance error: {e}")

async def _auto_sync_loop():
    await asyncio.sleep(30)  # let the app fully start first
    while True:
        try:
            raw = await get_setting_value_async("google_sync_interval", "")
            interval = int(raw) if raw else 0
            if interval > 0:
                # Same job name as the Sync button, so the two cannot overlap.
                await jobs.run_background("calendar-sync", calendar.run_full_sync)
                await asyncio.sleep(interval * 60)
            else:
                await asyncio.sleep(60)  # check again in 1 min
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(300)  # back off 5 min on error

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        await _advance_chores_if_new_day()
    except Exception as e:
        print(f"Chore advance error: {e}")
    sync_task = asyncio.create_task(_auto_sync_loop())
    reminder_task = asyncio.create_task(_reminder_loop())
    advance_task = asyncio.create_task(_chore_advance_loop())
    yield
    sync_task.cancel()
    reminder_task.cancel()
    advance_task.cancel()
    for t in (sync_task, reminder_task, advance_task):
        try:
            await t
        except asyncio.CancelledError:
            pass
    await outbound.aclose()

app = FastAPI(title="Family Hub", lifespan=lifespan)

class _GzipExceptMedia:
    """Gzip text responses, add immutable Cache-Control on versioned static
    assets (crucial on LAN HTTP where Service Workers do not run), and leave
    camera streams, photos, and avatars uncompressed."""

    def __init__(self, app, minimum_size: int = 500):
        self.app = app
        self._gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path") or ""
            if (
                path.startswith("/api/security/camera/")
                or path.startswith("/api/photos/file/")
                or path.startswith("/api/members/avatar/")
            ):
                await self.app(scope, receive, send)
                return

            qs = scope.get("query_string") or b""
            cache_hdr = None
            if (path.startswith("/js/") or path.startswith("/css/")) and b"v=" in qs:
                cache_hdr = b"public, max-age=31536000, immutable"
            elif path.startswith("/icons/") or path == "/favicon.svg":
                cache_hdr = b"public, max-age=86400"

            if cache_hdr is not None:
                async def send_with_cache(message):
                    if message["type"] == "http.response.start" and message.get("status") == 200:
                        headers = [
                            (k, v)
                            for k, v in message.get("headers", [])
                            if k.lower() != b"cache-control"
                        ]
                        headers.append((b"cache-control", cache_hdr))
                        message = {**message, "headers": headers}
                    await send(message)

                await self._gzip(scope, receive, send_with_cache)
                return

        await self._gzip(scope, receive, send)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(_GzipExceptMedia)

app.include_router(calendar.router, prefix="/api/calendar", tags=["calendar"])
app.include_router(chores.router, prefix="/api/chores", tags=["chores"])
app.include_router(members.router, prefix="/api/members", tags=["members"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(photos.router, prefix="/api/photos", tags=["photos"])
app.include_router(lunch.router, prefix="/api/lunch", tags=["lunch"])
app.include_router(weather.router, prefix="/api/weather", tags=["weather"])
app.include_router(homeassistant.router, prefix="/api/homeassistant", tags=["homeassistant"])
app.include_router(stocks.router, prefix="/api/stocks", tags=["stocks"])
app.include_router(security.router, prefix="/api/security", tags=["security"])
app.include_router(messages.router, prefix="/api/messages", tags=["messages"])
app.include_router(meals.router, prefix="/api/meals", tags=["meals"])
app.include_router(schoolcafe.router, prefix="/api/schoolcafe", tags=["schoolcafe"])
app.include_router(shopping.router, prefix="/api/shopping", tags=["shopping"])

# Serve frontend static files (configurable for LXC/native installs)
FRONTEND_DIR = os.environ.get("FRONTEND_DIR", "/app/frontend")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
