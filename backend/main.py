from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from contextlib import asynccontextmanager
import asyncio
import os
import time

from database import close_pools, init_db, listen_cache_events_loop
from routers import (
    auth,
    bootstrap,
    calendar,
    chores,
    homeassistant,
    lunch,
    meals,
    members,
    messages,
    photos,
    schoolcafe,
    security,
    settings,
    shopping,
    stocks,
    weather,
)
import outbound


async def _loop_lag_watchdog():
    """Log if the web worker's asyncio event loop ever stalls > 300ms."""
    expected = time.monotonic() + 1.0
    while True:
        try:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            lag = now - expected
            if lag > 0.3:
                print(f"[watchdog] Web event-loop lag: {lag * 1000:.0f}ms (pid={os.getpid()})")
            expected = now + 1.0
        except asyncio.CancelledError:
            break


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    cache_listener = asyncio.create_task(listen_cache_events_loop())
    watchdog_task = asyncio.create_task(_loop_lag_watchdog())
    yield
    cache_listener.cancel()
    watchdog_task.cancel()
    for t in (cache_listener, watchdog_task):
        try:
            await t
        except asyncio.CancelledError:
            pass
    await security.close_camera_http()
    await outbound.aclose()
    await close_pools()


app = FastAPI(title="Family Hub", lifespan=lifespan)


class _GzipAndTelemetryMiddleware:
    """Gzip text responses, add immutable Cache-Control on versioned static
    assets (crucial on LAN HTTP where Service Workers do not run), leave
    camera streams/photos/avatars uncompressed, and log slow API calls."""

    def __init__(self, app, minimum_size: int = 500):
        self.app = app
        self._gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""
        t0 = time.monotonic()

        try:
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
        finally:
            elapsed_ms = (time.monotonic() - t0) * 1000
            if elapsed_ms > 500 and not path.endswith("/stream"):
                method = scope.get("method", "GET")
                print(f"[slow] {method} {path} took {elapsed_ms:.0f}ms")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(_GzipAndTelemetryMiddleware)

app.include_router(bootstrap.router, prefix="/api", tags=["bootstrap"])
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

FRONTEND_DIR = os.environ.get("FRONTEND_DIR", "/app/frontend")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
