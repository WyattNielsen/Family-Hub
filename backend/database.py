import sqlite3
import os

_data_dir = os.environ.get("DATA_DIR")
DB_PATH = (
    os.environ.get("DB_PATH")
    or (os.path.join(_data_dir, "familyhub.db") if _data_dir else None)
    or "/data/familyhub.db"
)

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

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
        "ALTER TABLE messages ADD COLUMN recipient_id INTEGER DEFAULT NULL",
    ):
        try:
            conn.execute(stmt)
            conn.commit()
        except Exception:
            pass

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

    conn.close()
    print("Database initialized.")
