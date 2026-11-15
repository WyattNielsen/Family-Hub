"""PostgreSQL connection pools, schema management, settings cache, snapshots, and LISTEN/NOTIFY."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import lru_cache
import json
import os
import threading
import time
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool, ConnectionPool

from migrate_sqlite import compute_is_multiday, migrate_from_sqlite_if_needed

_data_dir = os.environ.get("DATA_DIR")
DB_PATH = (
    os.environ.get("DB_PATH")
    or (os.path.join(_data_dir, "familyhub.db") if _data_dir else None)
    or "/data/familyhub.db"
)

DATABASE_URL = (
    os.environ.get("DATABASE_URL")
    or "postgresql://familyhub:familyhub@postgres:5432/familyhub"
)

_pool_lock = threading.Lock()
_sync_pool: ConnectionPool | None = None
_sync_pool_pid: int | None = None

_async_pool: AsyncConnectionPool | None = None
_async_pool_pid: int | None = None

_settings_lock = threading.Lock()
_settings_cache: tuple[float, dict[str, str]] | None = None
_SETTINGS_TTL = 5.0

_snap_mem_lock = threading.Lock()
_snap_mem_cache: dict[str, tuple[float, tuple[Any, float, Optional[str]]]] = {}
_SNAP_MEM_TTL = 3.0


@lru_cache(maxsize=1024)
def _translate_sql(sql: str) -> str:
    """Convert SQLite-style `?` positional parameters outside string literals to `%s`."""
    if "?" not in sql:
        return sql
    out: list[str] = []
    in_single = False
    in_double = False
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'" and not in_double:
            if in_single and i + 1 < n and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_single = not in_single
            out.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
        elif ch == "?" and not in_single and not in_double:
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def get_sync_pool() -> ConnectionPool:
    global _sync_pool, _sync_pool_pid
    pid = os.getpid()
    if _sync_pool is not None and _sync_pool_pid == pid:
        return _sync_pool
    with _pool_lock:
        if _sync_pool is not None and _sync_pool_pid == pid:
            return _sync_pool
        _sync_pool = ConnectionPool(
            conninfo=DATABASE_URL,
            min_size=1,
            max_size=12,
            timeout=10.0,
            kwargs={"row_factory": dict_row, "autocommit": False},
            open=True,
        )
        _sync_pool_pid = pid
        return _sync_pool


async def get_async_pool() -> AsyncConnectionPool:
    global _async_pool, _async_pool_pid
    pid = os.getpid()
    if _async_pool is not None and _async_pool_pid == pid:
        return _async_pool
    pool = AsyncConnectionPool(
        conninfo=DATABASE_URL,
        min_size=1,
        max_size=12,
        timeout=10.0,
        kwargs={"row_factory": dict_row, "autocommit": True},
        open=False,
    )
    await pool.open()
    _async_pool = pool
    _async_pool_pid = pid
    return pool


async def close_pools():
    global _sync_pool, _async_pool
    if _async_pool is not None:
        await _async_pool.close()
        _async_pool = None
    if _sync_pool is not None:
        _sync_pool.close()
        _sync_pool = None


class DBConn:
    """Pooled PostgreSQL connection wrapper preserving the familiar get_db() API."""

    def __init__(self, pool: ConnectionPool):
        self._pool = pool
        self._conn: psycopg.Connection | None = pool.getconn()

    def execute(self, sql: str, params=None):
        assert self._conn is not None, "Connection already closed"
        q = _translate_sql(sql)
        if params is None:
            return self._conn.execute(q)
        return self._conn.execute(q, params)

    def executemany(self, sql: str, seq_of_params):
        assert self._conn is not None, "Connection already closed"
        q = _translate_sql(sql)
        cur = self._conn.cursor()
        cur.executemany(q, seq_of_params)
        return cur

    def cursor(self):
        assert self._conn is not None, "Connection already closed"
        return self._conn.cursor()

    def commit(self):
        if self._conn is not None:
            self._conn.commit()

    def rollback(self):
        if self._conn is not None:
            self._conn.rollback()

    def close(self):
        if self._conn is not None:
            try:
                # Clear any uncommitted read transaction before returning to pool
                if self._conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                    self._conn.rollback()
            except Exception:
                pass
            self._pool.putconn(self._conn)
            self._conn = None

    def __enter__(self) -> "DBConn":
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.rollback()
        self.close()
        return False


def get_db() -> DBConn:
    return DBConn(get_sync_pool())


@asynccontextmanager
async def async_db():
    pool = await get_async_pool()
    async with pool.connection() as conn:
        yield conn


# ── Settings Cache & Cross-Process Invalidation ──────────────────────────────

def _as_setting_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def _load_settings_sync() -> dict[str, str]:
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {_as_setting_str(r["key"]): _as_setting_str(r["value"]) for r in rows}


async def refresh_settings_cache_async() -> dict[str, str]:
    global _settings_cache
    async with async_db() as conn:
        cur = await conn.execute("SELECT key, value FROM settings")
        rows = await cur.fetchall()
        data = {_as_setting_str(r["key"]): _as_setting_str(r["value"]) for r in rows}
    with _settings_lock:
        _settings_cache = (time.monotonic(), data)
    return data


def get_settings_map() -> dict[str, str]:
    """Return all settings from RAM cache; refreshes cleanly if expired."""
    global _settings_cache
    now = time.monotonic()
    hit = _settings_cache
    if hit is not None and now - hit[0] < _SETTINGS_TTL:
        return hit[1]
    # If stale data exists, serve it if we are on an asyncio loop and kick off async refresh
    if hit is not None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(refresh_settings_cache_async())
            return hit[1]
        except RuntimeError:
            pass
    with _settings_lock:
        hit = _settings_cache
        if hit is not None and time.monotonic() - hit[0] < _SETTINGS_TTL:
            return hit[1]
        data = _load_settings_sync()
        _settings_cache = (time.monotonic(), data)
        return data


async def get_settings_map_async() -> dict[str, str]:
    hit = _settings_cache
    if hit is not None and time.monotonic() - hit[0] < _SETTINGS_TTL:
        return hit[1]
    return await refresh_settings_cache_async()


def get_setting_value(key: str, default: str = "") -> str:
    val = get_settings_map().get(key)
    return default if val is None else val


def upsert_setting_sync(conn: DBConn, key: str, value: Optional[str]):
    if value is None:
        conn.execute("DELETE FROM settings WHERE key = %s", (key,))
        return
    # Prefer UPDATE-then-INSERT so a missing PRIMARY KEY / ON CONFLICT
    # constraint cannot 500 every settings save.
    cur = conn.execute(
        "UPDATE settings SET value = %s WHERE key = %s",
        (str(value), key),
    )
    if cur.rowcount:
        return
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (%s, %s)",
            (key, str(value)),
        )
    except Exception:
        # Concurrent insert won the race — take their row over.
        conn.execute(
            "UPDATE settings SET value = %s WHERE key = %s",
            (str(value), key),
        )


def reload_settings_map() -> dict[str, str]:
    """Force a synchronous re-read from Postgres into the process cache."""
    global _settings_cache
    data = _load_settings_sync()
    with _settings_lock:
        _settings_cache = (time.monotonic(), data)
    return data


def invalidate_settings_cache(broadcast: bool = True):
    global _settings_cache
    try:
        data = reload_settings_map()
    except Exception:
        with _settings_lock:
            _settings_cache = None
        return None
    if broadcast:
        try:
            with get_db() as conn:
                # Keep channel/payload as SQL literals — avoids client encoding
                # edge cases with bind params on NOTIFY.
                conn.execute("SELECT pg_notify('fh_events', 'settings')")
                conn.commit()
        except Exception as e:
            print(f"settings notify failed: {e}")
    return data


# ── Worker Job Wakeups (LISTEN / NOTIFY) ─────────────────────────────────────

def notify_worker(job: str):
    """Wake the background worker immediately via PostgreSQL NOTIFY."""
    try:
        with get_db() as conn:
            conn.execute("SELECT pg_notify('fh_jobs', %s)", (job,))
            conn.commit()
    except Exception as e:
        print(f"notify_worker({job}) failed: {e}")


async def notify_worker_async(job: str):
    try:
        async with async_db() as conn:
            await conn.execute("SELECT pg_notify('fh_jobs', %s)", (job,))
    except Exception as e:
        print(f"notify_worker_async({job}) failed: {e}")


# ── Integration Snapshot Store (Local-First Read Path) ───────────────────────

def invalidate_snapshot_local(key: Optional[str] = None):
    with _snap_mem_lock:
        if key is None:
            _snap_mem_cache.clear()
        else:
            _snap_mem_cache.pop(key, None)


def get_snapshot(key: str) -> Optional[tuple[Any, float, Optional[str]]]:
    """Return (payload, updated_at_epoch, error) or None."""
    now_m = time.monotonic()
    with _snap_mem_lock:
        hit = _snap_mem_cache.get(key)
        if hit and now_m - hit[0] < _SNAP_MEM_TTL:
            return hit[1]
    with get_db() as conn:
        row = conn.execute(
            "SELECT payload, updated_at, error FROM integration_snapshots WHERE key = %s",
            (key,),
        ).fetchone()
    if not row:
        return None
    val = (row["payload"], float(row["updated_at"]), row["error"])
    with _snap_mem_lock:
        _snap_mem_cache[key] = (now_m, val)
    return val


async def get_snapshot_async(key: str) -> Optional[tuple[Any, float, Optional[str]]]:
    """Non-blocking async read of (payload, updated_at_epoch, error) or None."""
    now_m = time.monotonic()
    with _snap_mem_lock:
        hit = _snap_mem_cache.get(key)
        if hit and now_m - hit[0] < _SNAP_MEM_TTL:
            return hit[1]
    async with async_db() as conn:
        cur = await conn.execute(
            "SELECT payload, updated_at, error FROM integration_snapshots WHERE key = %s",
            (key,),
        )
        row = await cur.fetchone()
    if not row:
        return None
    val = (row["payload"], float(row["updated_at"]), row["error"])
    with _snap_mem_lock:
        _snap_mem_cache[key] = (now_m, val)
    return val


def set_snapshot(key: str, payload: Any, error: Optional[str] = None):
    ts = time.time()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO integration_snapshots (key, payload, updated_at, error)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (key) DO UPDATE SET
                   payload = EXCLUDED.payload,
                   updated_at = EXCLUDED.updated_at,
                   error = EXCLUDED.error""",
            (key, Jsonb(payload), ts, error),
        )
        conn.execute("SELECT pg_notify('fh_events', %s)", (f"snap:{key}",))
        conn.commit()
    with _snap_mem_lock:
        _snap_mem_cache[key] = (time.monotonic(), (payload, ts, error))


async def set_snapshot_async(key: str, payload: Any, error: Optional[str] = None):
    ts = time.time()
    async with async_db() as conn:
        await conn.execute(
            """INSERT INTO integration_snapshots (key, payload, updated_at, error)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (key) DO UPDATE SET
                   payload = EXCLUDED.payload,
                   updated_at = EXCLUDED.updated_at,
                   error = EXCLUDED.error""",
            (key, Jsonb(payload), ts, error),
        )
        await conn.execute("SELECT pg_notify('fh_events', %s)", (f"snap:{key}",))
    with _snap_mem_lock:
        _snap_mem_cache[key] = (time.monotonic(), (payload, ts, error))


async def listen_cache_events_loop():
    """Background listener in web workers so settings & snapshots update across processes."""
    while True:
        try:
            aconn = await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True)
            async with aconn:
                await aconn.execute("LISTEN fh_events")
                async for notify in aconn.notifies():
                    raw = notify.payload or ""
                    if isinstance(raw, (bytes, bytearray, memoryview)):
                        msg = bytes(raw).decode("utf-8", errors="replace")
                    else:
                        msg = str(raw)
                    if msg == "settings":
                        await refresh_settings_cache_async()
                    elif msg.startswith("snap:"):
                        invalidate_snapshot_local(msg[5:])
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"Event listener reconnect in 3s: {exc}")
            await asyncio.sleep(3)


# ── Schema Initialization & Legacy Migration ─────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS calendar_accounts (
    id SERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    email TEXT DEFAULT NULL,
    label TEXT DEFAULT NULL,
    access_token TEXT DEFAULT NULL,
    refresh_token TEXT DEFAULT NULL,
    token_expiry TEXT DEFAULT NULL,
    apple_id TEXT DEFAULT NULL,
    app_password TEXT DEFAULT NULL,
    created_at TEXT DEFAULT (NOW()::text),
    updated_at TEXT DEFAULT (NOW()::text)
);

CREATE TABLE IF NOT EXISTS members (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT '#4A90D9',
    avatar TEXT DEFAULT NULL,
    avatar_photo TEXT DEFAULT NULL,
    is_admin INTEGER DEFAULT 0,
    write_calendar_source_id INTEGER DEFAULT NULL,
    school_id TEXT DEFAULT NULL,
    school_name TEXT DEFAULT NULL,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (NOW()::text)
);

CREATE TABLE IF NOT EXISTS calendar_sources (
    id SERIAL PRIMARY KEY,
    account_id INTEGER DEFAULT NULL REFERENCES calendar_accounts(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    external_id TEXT NOT NULL,
    color TEXT DEFAULT NULL,
    enabled INTEGER DEFAULT 1,
    member_id INTEGER DEFAULT NULL REFERENCES members(id) ON DELETE SET NULL,
    is_family INTEGER DEFAULT 0,
    sync_etag TEXT DEFAULT NULL,
    content_hash TEXT DEFAULT NULL,
    created_at TEXT DEFAULT (NOW()::text)
);

CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    location TEXT DEFAULT '',
    start_datetime TEXT NOT NULL,
    end_datetime TEXT NOT NULL,
    all_day INTEGER DEFAULT 0,
    member_id INTEGER DEFAULT NULL REFERENCES members(id) ON DELETE SET NULL,
    is_family INTEGER DEFAULT 0,
    google_event_id TEXT DEFAULT NULL,
    google_calendar_id TEXT DEFAULT NULL,
    external_event_id TEXT DEFAULT NULL,
    external_source_id INTEGER DEFAULT NULL,
    color TEXT DEFAULT NULL,
    recurrence TEXT DEFAULT NULL,
    is_multiday INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (NOW()::text)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS photos_auth (
    id SERIAL PRIMARY KEY,
    google_access_token TEXT,
    google_refresh_token TEXT,
    google_token_expiry TEXT,
    google_email TEXT,
    updated_at TEXT DEFAULT (NOW()::text)
);

CREATE TABLE IF NOT EXISTS photos_cache (
    id SERIAL PRIMARY KEY,
    media_item_id TEXT UNIQUE,
    base_url TEXT NOT NULL,
    url_expiry TEXT NOT NULL,
    mime_type TEXT DEFAULT 'image/jpeg',
    created_at TEXT DEFAULT (NOW()::text)
);

CREATE TABLE IF NOT EXISTS chores (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    assigned_to INTEGER DEFAULT NULL REFERENCES members(id) ON DELETE SET NULL,
    due_date TEXT DEFAULT NULL,
    completed INTEGER DEFAULT 0,
    completed_at TEXT DEFAULT NULL,
    recurrence TEXT DEFAULT NULL,
    recurrence_days TEXT DEFAULT NULL,
    recurrence_interval INTEGER DEFAULT 1,
    points INTEGER DEFAULT 1,
    time_of_day TEXT DEFAULT NULL,
    created_at TEXT DEFAULT (NOW()::text)
);

CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    author_id INTEGER DEFAULT NULL REFERENCES members(id) ON DELETE SET NULL,
    recipient_id INTEGER DEFAULT NULL REFERENCES members(id) ON DELETE SET NULL,
    body TEXT NOT NULL,
    created_at TEXT DEFAULT (NOW()::text)
);

CREATE TABLE IF NOT EXISTS family_meals (
    id SERIAL PRIMARY KEY,
    meal_date TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    notes TEXT DEFAULT '',
    updated_at TEXT DEFAULT (NOW()::text)
);

CREATE TABLE IF NOT EXISTS meal_requests (
    id SERIAL PRIMARY KEY,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at TEXT DEFAULT (NOW()::text)
);

CREATE TABLE IF NOT EXISTS shopping_items (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    quantity TEXT DEFAULT '',
    checked INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (NOW()::text)
);

CREATE TABLE IF NOT EXISTS integration_snapshots (
    key TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    error TEXT DEFAULT NULL
);

ALTER TABLE calendar_sources ADD COLUMN IF NOT EXISTS sync_etag TEXT DEFAULT NULL;
ALTER TABLE calendar_sources ADD COLUMN IF NOT EXISTS content_hash TEXT DEFAULT NULL;
ALTER TABLE events ADD COLUMN IF NOT EXISTS is_multiday INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_events_single_start ON events(is_multiday, start_datetime);
CREATE INDEX IF NOT EXISTS idx_events_multiday_span ON events(is_multiday, end_datetime, start_datetime);
CREATE INDEX IF NOT EXISTS idx_events_member ON events(member_id, is_family);
CREATE INDEX IF NOT EXISTS idx_events_ext_source ON events(external_source_id);
CREATE INDEX IF NOT EXISTS idx_events_google_id ON events(google_event_id);
CREATE INDEX IF NOT EXISTS idx_events_ext_event_id ON events(external_event_id);
CREATE INDEX IF NOT EXISTS idx_chores_assigned ON chores(assigned_to, completed, due_date);
CREATE INDEX IF NOT EXISTS idx_chores_completed ON chores(completed, assigned_to);
CREATE INDEX IF NOT EXISTS idx_chores_due ON chores(due_date);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_shopping_checked ON shopping_items(checked);
"""


def init_db():
    """Initialize PostgreSQL schema and auto-migrate legacy SQLite DB if present."""
    # Retry briefly in case Postgres is finishing startup
    last_err = None
    for _ in range(20):
        try:
            conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)
            break
        except Exception as exc:
            last_err = exc
            time.sleep(1)
    else:
        raise RuntimeError(f"Could not connect to PostgreSQL: {last_err}")

    try:
        # Advisory lock serializes schema init & SQLite migration across web/worker processes
        conn.execute("SELECT pg_advisory_lock(84729103)")
        try:
            # psycopg3 execute() allows only one statement at a time
            for stmt in SCHEMA_SQL.split(";"):
                sql = stmt.strip()
                if sql:
                    conn.execute(sql)
            conn.commit()

            migrate_from_sqlite_if_needed(conn, DB_PATH)

            # Ensure upserts work even if an older table lacked a PRIMARY KEY
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS settings_key_uidx ON settings (key)"
                )
                conn.commit()
            except Exception as e:
                print(f"settings unique index note: {e}")
                conn.rollback()
            defaults = [
                ("slideshow_timeout", "120"),
                ("slideshow_interval", "5"),
                ("photos_album_url", ""),
                ("photos_session_id", ""),
            ]
            for key, val in defaults:
                cur = conn.execute(
                    "UPDATE settings SET value = value WHERE key = %s",
                    (key,),
                )
                if not cur.rowcount:
                    conn.execute(
                        "INSERT INTO settings (key, value) VALUES (%s, %s)",
                        (key, val),
                    )
            conn.commit()
        finally:
            conn.execute("SELECT pg_advisory_unlock(84729103)")
            conn.commit()
    finally:
        conn.close()

    # Warm local settings cache immediately so event loop never blocks on first request
    with _settings_lock:
        global _settings_cache
        _settings_cache = (time.monotonic(), _load_settings_sync())
    print("PostgreSQL database initialized.")
