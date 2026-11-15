"""Dedicated background worker process for Family Hub.

Runs in its own container / systemd service with a separate Python GIL and event loop:
- Listens on PostgreSQL `LISTEN fh_jobs` for instant on-demand wakeups
- Polls Home Assistant, Weather, Stocks, SchoolCafe, Nutrislice, and Camera snapshots/go2rtc
- Runs incremental ETag/diff calendar sync and recurring chore catch-up
- Fires scheduled TTS chore reminders and expires 7-day-old family messages
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import json
import signal
import time

import psycopg

from database import (
    DATABASE_URL,
    close_pools,
    get_setting_value,
    init_db,
    listen_cache_events_loop,
)
import go2rtc_sync
import outbound
from routers import calendar, chores, homeassistant, lunch, messages, security, stocks, weather
import schoolcafe


_job_queue: asyncio.Queue[str] = asyncio.Queue()


def _enqueue(job: str):
    if job:
        _job_queue.put_nowait(job)


async def _listen_jobs_loop():
    """Listen on PostgreSQL NOTIFY channel `fh_jobs` for instant wakeups from web workers."""
    while True:
        try:
            aconn = await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True)
            async with aconn:
                await aconn.execute("LISTEN fh_jobs")
                async for notify in aconn.notifies():
                    _enqueue(notify.payload or "")
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"[worker] fh_jobs listener reconnect in 3s: {exc}")
            await asyncio.sleep(3)


async def _job_consumer_loop():
    """Coalesce and execute immediate jobs requested by web requests."""
    while True:
        try:
            first = await _job_queue.get()
            # Drain any duplicate jobs queued within 50ms so bursts coalesce cleanly
            await asyncio.sleep(0.05)
            batch = {first}
            while not _job_queue.empty():
                batch.add(_job_queue.get_nowait())

            for job in batch:
                try:
                    if job == "sync_all":
                        await calendar.run_full_sync()
                    elif job == "refresh_ha":
                        await homeassistant.refresh_ha_snapshot()
                    elif job == "refresh_weather":
                        await weather.refresh_weather_snapshot()
                    elif job.startswith("refresh_stocks"):
                        custom = job.split(":", 1)[1] if ":" in job else None
                        await stocks.refresh_stocks_snapshot(custom)
                    elif job == "refresh_meals":
                        await schoolcafe.refresh_schoolcafe_snapshots()
                        await lunch.refresh_lunch_snapshot()
                    elif job.startswith("refresh_meals_week:"):
                        spec = job.split(":", 1)[1]
                        await schoolcafe.refresh_schoolcafe_snapshots(extra_week_spec=spec)
                    elif job == "sync_cameras":
                        await go2rtc_sync.sync_go2rtc_streams()
                        await security.refresh_camera_snapshots()
                except Exception as exc:
                    print(f"[worker] job '{job}' error: {exc}")
        except asyncio.CancelledError:
            break


async def _fast_poller_loop():
    """Poll Home Assistant states (every 15s) and warm camera snapshots."""
    while True:
        try:
            await homeassistant.refresh_ha_snapshot()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"[worker] HA poll error: {exc}")
        await asyncio.sleep(15)


async def _medium_poller_loop():
    """Poll Stocks (every 3m), Weather (every 15m), and refresh HA rotating camera tokens in go2rtc (every 4m)."""
    last_weather = 0.0
    last_cam_sync = 0.0
    while True:
        try:
            now = time.monotonic()
            await stocks.refresh_stocks_snapshot()
            if now - last_cam_sync >= 240:
                await go2rtc_sync.sync_go2rtc_streams()
                await security.refresh_camera_snapshots()
                last_cam_sync = now
            if now - last_weather >= 900:
                await weather.refresh_weather_snapshot()
                last_weather = now
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"[worker] medium poller error: {exc}")
        await asyncio.sleep(180)


async def _slow_poller_loop():
    """Poll SchoolCafe & Nutrislice menus (every 4h) and expire old messages."""
    while True:
        try:
            await schoolcafe.refresh_schoolcafe_snapshots()
            await lunch.refresh_lunch_snapshot()
            await asyncio.to_thread(messages.expire_old_messages)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"[worker] slow poller error: {exc}")
        await asyncio.sleep(4 * 3600)


async def _chore_advance_loop():
    while True:
        try:
            await asyncio.to_thread(chores.advance_recurring_chores)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"[worker] chore advance error: {exc}")
        await asyncio.sleep(60)


async def _calendar_sync_loop():
    await asyncio.sleep(10)
    while True:
        try:
            raw = get_setting_value("google_sync_interval", "")
            interval = int(raw) if raw else 0
            if interval > 0:
                await calendar.run_full_sync()
                await asyncio.sleep(interval * 60)
            else:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"[worker] auto sync error: {exc}")
            await asyncio.sleep(300)


async def _reminder_loop():
    fired_today: set[str] = set()
    last_date: str | None = None
    while True:
        try:
            await asyncio.sleep(30)
            now = datetime.now()
            today = now.date().isoformat()
            if last_date != today:
                fired_today.clear()
                last_date = today
            hhmm = now.strftime("%H:%M")
            raw = get_setting_value("chore_reminders", "")
            if not raw:
                continue
            reminders = json.loads(raw)
            for r in reminders:
                if not r.get("enabled"):
                    continue
                t = r.get("time", "")
                if t == hhmm and t not in fired_today:
                    fired_today.add(t)
                    await homeassistant.fire_tts(
                        r.get("message", ""),
                        r.get("entity_id", ""),
                        r.get("tts_engine", "tts.google_translate_en_com"),
                    )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"[worker] reminder loop error: {exc}")


async def main():
    print("[worker] Initializing Family Hub worker...")
    init_db()

    # Warm all snapshots right away at boot so web requests hit hot data
    await go2rtc_sync.sync_go2rtc_streams()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    tasks = [
        asyncio.create_task(listen_cache_events_loop()),
        asyncio.create_task(_listen_jobs_loop()),
        asyncio.create_task(_job_consumer_loop()),
        asyncio.create_task(_fast_poller_loop()),
        asyncio.create_task(_medium_poller_loop()),
        asyncio.create_task(_slow_poller_loop()),
        asyncio.create_task(_chore_advance_loop()),
        asyncio.create_task(_calendar_sync_loop()),
        asyncio.create_task(_reminder_loop()),
    ]

    print("[worker] Background loops active.")
    await stop_event.wait()
    print("[worker] Shutting down...")

    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass
    await outbound.aclose()
    await close_pools()


if __name__ == "__main__":
    asyncio.run(main())
