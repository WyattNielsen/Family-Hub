from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from contextlib import asynccontextmanager
import asyncio
import os

from database import init_db, get_db
from routers import calendar, chores, members, auth, settings, photos, lunch, weather, homeassistant, stocks, security, messages, meals, schoolcafe, shopping
import json
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
            conn = get_db()
            row = conn.execute("SELECT value FROM settings WHERE key='chore_reminders'").fetchone()
            conn.close()
            if not row or not row["value"]:
                continue
            reminders = json.loads(row["value"])
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

async def _chore_advance_loop():
    """Catch up recurring chores away from request handlers."""
    while True:
        try:
            await asyncio.sleep(60)
            await asyncio.to_thread(chores.advance_recurring_chores)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Chore advance error: {e}")

async def _auto_sync_loop():
    await asyncio.sleep(30)  # let the app fully start first
    while True:
        try:
            conn = get_db()
            row = conn.execute("SELECT value FROM settings WHERE key='google_sync_interval'").fetchone()
            conn.close()
            interval = int(row["value"]) if row and row["value"] else 0
            if interval > 0:
                await calendar.run_full_sync()
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
        await asyncio.to_thread(chores.advance_recurring_chores)
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
    """Gzip text responses. Leave camera streams and photo files alone."""

    def __init__(self, app, minimum_size: int = 500):
        self.app = app
        self._gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path") or ""
            if path.startswith("/api/security/camera/") or path.startswith("/api/photos/file/"):
                await self.app(scope, receive, send)
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
