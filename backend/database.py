import asyncio
import sqlite3
import os
import threading
import time

_data_dir = os.environ.get("DATA_DIR")
DB_PATH = (
    os.environ.get("DB_PATH")
    or (os.path.join(_data_dir, "familyhub.db") if _data_dir else None)
    or "/data/familyhub.db"
)

_settings_lock = threading.Lock()
_settings_cache: tuple[float, dict[str, str]] | None = None
_settings_gen = 0
# Writes call invalidate_settings_cache(), so this only bounds how long a
# change made outside this process stays invisible. A short TTL made every
# poll reopen SQLite on the event loop.
_SETTINGS_TTL = 60.0


# Idle connections only. Checked-out connections are not in this list, so a
# given connection has one borrower at a time. Cap is the idle pool, not the
# number of connections that may be open at once.
_POOL_CAP = 8
_pool: list[sqlite3.Connection] = []
_pool_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    # check_same_thread=False: the connection is opened on whichever thread
    # first fills the pool and later used on another. Callers do not share
    # one borrowed connection across threads.
    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Once per physical connection, not on checkout. journal_mode is persisted
    # on the database file by init_db(); asking for it again takes a lock.
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-8000")
    return conn


def _close_raw(conn: sqlite3.Connection) -> None:
    try:
        conn.close()
    except sqlite3.Error:
        pass


def _release(conn: sqlite3.Connection) -> None:
    with _pool_lock:
        if len(_pool) < _POOL_CAP:
            _pool.append(conn)
            return
    _close_raw(conn)


class _PooledConnection:
    """Delegates to a sqlite3 connection. close() returns it to the pool."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._closed = False
        self._close_lock = threading.Lock()

    def __getattr__(self, name):
        if self._closed or self._conn is None:
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
        return getattr(self._conn, name)

    @property
    def row_factory(self):
        if self._closed or self._conn is None:
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        if self._closed or self._conn is None:
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
        self._conn.row_factory = value

    def close(self):
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            conn = self._conn
            self._conn = None
        # Roll back before reuse so the next borrower does not see an open
        # transaction. After commit(), there is nothing to undo.
        try:
            conn.rollback()
        except sqlite3.Error:
            _close_raw(conn)
            return
        if conn.in_transaction:
            _close_raw(conn)
            return
        _release(conn)


def get_db():
    """Borrow a SQLite connection. close() returns it to the pool."""
    with _pool_lock:
        raw = _pool.pop() if _pool else None
    if raw is None:
        raw = _connect()
    return _PooledConnection(raw)


def get_settings_map() -> dict[str, str]:
    """Return all settings from memory. The SQLite read runs outside the lock.

    Holding the lock across get_db() deadlocks a save: the event loop waits
    inside SQLite for the writer's transaction, and the writer waits on this
    lock in invalidate_settings_cache() before it can commit and close.
    """
    global _settings_cache
    now = time.monotonic()
    hit = _settings_cache
    if hit is not None and now - hit[0] < _SETTINGS_TTL:
        return hit[1]
    with _settings_lock:
        hit = _settings_cache
        if hit is not None and time.monotonic() - hit[0] < _SETTINGS_TTL:
            return hit[1]
        gen = _settings_gen
    conn = get_db()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        data = {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()
    with _settings_lock:
        if _settings_gen == gen:
            _settings_cache = (time.monotonic(), data)
            return data
        hit = _settings_cache
        if hit is not None:
            return hit[1]
        return data


def get_setting_value(key: str, default: str = "") -> str:
    val = get_settings_map().get(key)
    return default if val is None else val


async def get_settings_map_async() -> dict[str, str]:
    """Settings read for async routes and background tasks.

    A cache miss opens SQLite. Doing that on the event loop freezes every
    request, including the save whose response is waiting to be written.
    """
    return await asyncio.to_thread(get_settings_map)


async def get_setting_value_async(key: str, default: str = "") -> str:
    val = (await get_settings_map_async()).get(key)
    return default if val is None else val


def invalidate_settings_cache():
    global _settings_cache, _settings_gen
    with _settings_lock:
        _settings_cache = None
        _settings_gen += 1

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            color TEXT NOT NULL DEFAULT '#4A90D9',
            avatar TEXT DEFAULT NULL,
            is_admin INTEGER DEFAULT 0,
            write_calendar_source_id INTEGER DEFAULT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            location TEXT DEFAULT '',
            start_datetime TEXT NOT NULL,
            end_datetime TEXT NOT NULL,
            all_day INTEGER DEFAULT 0,
            member_id INTEGER DEFAULT NULL,
            is_family INTEGER DEFAULT 0,
            google_event_id TEXT DEFAULT NULL,
            google_calendar_id TEXT DEFAULT NULL,
            external_event_id TEXT DEFAULT NULL,
            external_source_id INTEGER DEFAULT NULL,
            color TEXT DEFAULT NULL,
            recurrence TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS photos_auth (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            google_access_token TEXT,
            google_refresh_token TEXT,
            google_token_expiry TEXT,
            google_email TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS photos_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_item_id TEXT UNIQUE,
            base_url TEXT NOT NULL,
            url_expiry TEXT NOT NULL,
            mime_type TEXT DEFAULT 'image/jpeg',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS chores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            assigned_to INTEGER DEFAULT NULL,
            due_date TEXT DEFAULT NULL,
            completed INTEGER DEFAULT 0,
            completed_at TEXT DEFAULT NULL,
            recurrence TEXT DEFAULT NULL,
            recurrence_days TEXT DEFAULT NULL,
            recurrence_interval INTEGER DEFAULT 1,
            points INTEGER DEFAULT 1,
            time_of_day TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (assigned_to) REFERENCES members(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_id INTEGER DEFAULT NULL,
            body TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (author_id) REFERENCES members(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS calendar_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            email TEXT DEFAULT NULL,
            label TEXT DEFAULT NULL,
            access_token TEXT DEFAULT NULL,
            refresh_token TEXT DEFAULT NULL,
            token_expiry TEXT DEFAULT NULL,
            apple_id TEXT DEFAULT NULL,
            app_password TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS calendar_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER DEFAULT NULL,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            external_id TEXT NOT NULL,
            color TEXT DEFAULT NULL,
            enabled INTEGER DEFAULT 1,
            member_id INTEGER DEFAULT NULL,
            is_family INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (account_id) REFERENCES calendar_accounts(id) ON DELETE CASCADE,
            FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS family_meals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meal_date TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            notes TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS meal_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (member_id) REFERENCES members(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS shopping_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            quantity TEXT DEFAULT '',
            checked INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)

    conn.commit()

    # Drop superseded tables from earlier calendar designs (app not live yet)
    for table in ("family_google_calendar", "apple_calendar_sources"):
        try:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.commit()
        except Exception:
            pass

    # Additive migrations for DBs created before columns were in CREATE TABLE
    for stmt in (
        "ALTER TABLE members ADD COLUMN write_calendar_source_id INTEGER DEFAULT NULL",
        "ALTER TABLE calendar_sources ADD COLUMN is_family INTEGER DEFAULT 0",
        "ALTER TABLE events ADD COLUMN location TEXT DEFAULT ''",
        "ALTER TABLE events ADD COLUMN google_calendar_id TEXT DEFAULT NULL",
        "ALTER TABLE events ADD COLUMN external_event_id TEXT DEFAULT NULL",
        "ALTER TABLE events ADD COLUMN external_source_id INTEGER DEFAULT NULL",
        "ALTER TABLE chores ADD COLUMN recurrence_days TEXT DEFAULT NULL",
        "ALTER TABLE chores ADD COLUMN time_of_day TEXT DEFAULT NULL",
        "ALTER TABLE members ADD COLUMN school_id TEXT DEFAULT NULL",
        "ALTER TABLE members ADD COLUMN school_name TEXT DEFAULT NULL",
        "ALTER TABLE members ADD COLUMN avatar_photo TEXT DEFAULT NULL",
        "ALTER TABLE members ADD COLUMN sort_order INTEGER DEFAULT 0",
        "ALTER TABLE messages ADD COLUMN recipient_id INTEGER DEFAULT NULL",
    ):
        try:
            conn.execute(stmt)
            conn.commit()
        except Exception:
            pass

    # First time the column exists, keep the previous alphabetical order.
    total = conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]
    unordered = conn.execute(
        "SELECT COUNT(*) FROM members WHERE sort_order IS NULL OR sort_order = 0"
    ).fetchone()[0]
    if total and unordered == total:
        rows = conn.execute("SELECT id FROM members ORDER BY name, id").fetchall()
        for i, row in enumerate(rows):
            conn.execute("UPDATE members SET sort_order=? WHERE id=?", (i, row["id"]))
        conn.commit()

    defaults = [
        ('slideshow_timeout', '120'),
        ('slideshow_interval', '5'),
        ('photos_album_url', ''),
        ('photos_session_id', ''),
    ]
    for key, val in defaults:
        try:
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
        except Exception:
            pass
    conn.commit()

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_datetime);
        CREATE INDEX IF NOT EXISTS idx_events_end ON events(end_datetime);
        CREATE INDEX IF NOT EXISTS idx_events_member ON events(member_id, is_family);
        CREATE INDEX IF NOT EXISTS idx_events_ext_source ON events(external_source_id);
        CREATE INDEX IF NOT EXISTS idx_events_google_id ON events(google_event_id);
        CREATE INDEX IF NOT EXISTS idx_chores_assigned ON chores(assigned_to, completed, due_date);
        CREATE INDEX IF NOT EXISTS idx_chores_completed ON chores(completed, assigned_to);
        CREATE INDEX IF NOT EXISTS idx_chores_due ON chores(due_date);
        CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
        CREATE INDEX IF NOT EXISTS idx_shopping_checked ON shopping_items(checked);
    """)
    conn.commit()

    conn.close()
    invalidate_settings_cache()
    print("Database initialized.")
