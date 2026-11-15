"""One-time automatic data migration from legacy SQLite (/data/familyhub.db) to PostgreSQL.

Runs inside init_db() under a PostgreSQL advisory lock so concurrent web/worker
processes never race. Leaves the original SQLite file intact on disk as a backup.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date


def compute_is_multiday(start_dt: str | None, end_dt: str | None) -> int:
    """Return 1 if end date is > 1 calendar day after start date, else 0."""
    if not start_dt or not end_dt:
        return 0
    try:
        s = date.fromisoformat(str(start_dt)[:10])
        e = date.fromisoformat(str(end_dt)[:10])
        return 1 if (e - s).days > 1 else 0
    except Exception:
        return 0


def _sqlite_tables(sconn: sqlite3.Connection) -> set[str]:
    rows = sconn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def _sqlite_rows(sconn: sqlite3.Connection, table: str) -> list[dict]:
    cur = sconn.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def migrate_from_sqlite_if_needed(pg_conn, sqlite_path: str) -> bool:
    """Copy data from sqlite_path into PostgreSQL if Postgres is still empty."""
    if not sqlite_path or not os.path.isfile(sqlite_path):
        return False
    if os.path.getsize(sqlite_path) == 0:
        return False

    already = pg_conn.execute(
        "SELECT value FROM settings WHERE key = '_sqlite_migrated'"
    ).fetchone()
    if already:
        return False

    member_count = pg_conn.execute("SELECT COUNT(*) AS c FROM members").fetchone()["c"]
    event_count = pg_conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
    chore_count = pg_conn.execute("SELECT COUNT(*) AS c FROM chores").fetchone()["c"]
    if member_count > 0 or event_count > 0 or chore_count > 0:
        pg_conn.execute(
            """INSERT INTO settings (key, value) VALUES ('_sqlite_migrated', 'existing_pg')
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"""
        )
        pg_conn.commit()
        return False

    print(f"Found legacy SQLite database at {sqlite_path} — migrating into PostgreSQL...")
    sconn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        tables = _sqlite_tables(sconn)

        # 1. settings
        if "settings" in tables:
            for r in _sqlite_rows(sconn, "settings"):
                if r.get("key") is not None and r.get("value") is not None:
                    pg_conn.execute(
                        """INSERT INTO settings (key, value) VALUES (%s, %s)
                           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                        (str(r["key"]), str(r["value"])),
                    )

        # 2. calendar_accounts (needed before calendar_sources)
        valid_account_ids = set()
        if "calendar_accounts" in tables:
            for r in _sqlite_rows(sconn, "calendar_accounts"):
                pg_conn.execute(
                    """INSERT INTO calendar_accounts
                       (id, provider, email, label, access_token, refresh_token,
                        token_expiry, apple_id, app_password, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, NOW()::text), COALESCE(%s, NOW()::text))
                       ON CONFLICT (id) DO NOTHING""",
                    (
                        r["id"], r.get("provider") or "google", r.get("email"), r.get("label"),
                        r.get("access_token"), r.get("refresh_token"), r.get("token_expiry"),
                        r.get("apple_id"), r.get("app_password"),
                        r.get("created_at"), r.get("updated_at"),
                    ),
                )
                valid_account_ids.add(r["id"])

        # 3. members (insert without write_calendar_source_id first to break circular FK)
        valid_member_ids = set()
        member_write_map: list[tuple[int, int]] = []
        if "members" in tables:
            for r in _sqlite_rows(sconn, "members"):
                pg_conn.execute(
                    """INSERT INTO members
                       (id, name, color, avatar, avatar_photo, is_admin, write_calendar_source_id,
                        school_id, school_name, sort_order, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, COALESCE(%s, NOW()::text))
                       ON CONFLICT (id) DO NOTHING""",
                    (
                        r["id"], r["name"], r.get("color") or "#4A90D9",
                        r.get("avatar"), r.get("avatar_photo"),
                        int(r.get("is_admin") or 0),
                        r.get("school_id"), r.get("school_name"),
                        int(r.get("sort_order") or 0), r.get("created_at"),
                    ),
                )
                valid_member_ids.add(r["id"])
                if r.get("write_calendar_source_id"):
                    member_write_map.append((r["id"], int(r["write_calendar_source_id"])))

        # 4. calendar_sources
        valid_source_ids = set()
        if "calendar_sources" in tables:
            for r in _sqlite_rows(sconn, "calendar_sources"):
                acc_id = r.get("account_id") if r.get("account_id") in valid_account_ids else None
                mem_id = r.get("member_id") if r.get("member_id") in valid_member_ids else None
                pg_conn.execute(
                    """INSERT INTO calendar_sources
                       (id, account_id, kind, name, external_id, color, enabled,
                        member_id, is_family, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, NOW()::text))
                       ON CONFLICT (id) DO NOTHING""",
                    (
                        r["id"], acc_id, r["kind"], r["name"], r["external_id"],
                        r.get("color"), int(1 if r.get("enabled") is None else r["enabled"]),
                        mem_id, int(r.get("is_family") or 0), r.get("created_at"),
                    ),
                )
                valid_source_ids.add(r["id"])

        # Restore members.write_calendar_source_id now that sources exist
        for mem_id, src_id in member_write_map:
            if src_id in valid_source_ids:
                pg_conn.execute(
                    "UPDATE members SET write_calendar_source_id = %s WHERE id = %s",
                    (src_id, mem_id),
                )

        # 5. events
        if "events" in tables:
            event_rows = _sqlite_rows(sconn, "events")
            params_batch = []
            for r in event_rows:
                mem_id = r.get("member_id") if r.get("member_id") in valid_member_ids else None
                src_id = r.get("external_source_id") if r.get("external_source_id") in valid_source_ids else None
                start_dt = r["start_datetime"]
                end_dt = r["end_datetime"]
                params_batch.append((
                    r["id"], r["title"], r.get("description") or "", r.get("location") or "",
                    start_dt, end_dt, int(r.get("all_day") or 0), mem_id,
                    int(r.get("is_family") or 0), r.get("google_event_id"),
                    r.get("google_calendar_id"), r.get("external_event_id"),
                    src_id, r.get("color"), r.get("recurrence"),
                    compute_is_multiday(start_dt, end_dt),
                    r.get("created_at"),
                ))
            if params_batch:
                with pg_conn.cursor() as cur:
                    cur.executemany(
                        """INSERT INTO events
                           (id, title, description, location, start_datetime, end_datetime,
                            all_day, member_id, is_family, google_event_id, google_calendar_id,
                            external_event_id, external_source_id, color, recurrence,
                            is_multiday, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, NOW()::text))
                           ON CONFLICT (id) DO NOTHING""",
                        params_batch,
                    )

        # 6. chores
        if "chores" in tables:
            chore_rows = _sqlite_rows(sconn, "chores")
            params_batch = []
            for r in chore_rows:
                mem_id = r.get("assigned_to") if r.get("assigned_to") in valid_member_ids else None
                params_batch.append((
                    r["id"], r["title"], r.get("description") or "", mem_id,
                    r.get("due_date"), int(r.get("completed") or 0), r.get("completed_at"),
                    r.get("recurrence"), r.get("recurrence_days"),
                    int(r.get("recurrence_interval") or 1), int(r.get("points") or 1),
                    r.get("time_of_day"), r.get("created_at"),
                ))
            if params_batch:
                with pg_conn.cursor() as cur:
                    cur.executemany(
                        """INSERT INTO chores
                           (id, title, description, assigned_to, due_date, completed,
                            completed_at, recurrence, recurrence_days, recurrence_interval,
                            points, time_of_day, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, NOW()::text))
                           ON CONFLICT (id) DO NOTHING""",
                        params_batch,
                    )

        # 7. messages
        if "messages" in tables:
            for r in _sqlite_rows(sconn, "messages"):
                author = r.get("author_id") if r.get("author_id") in valid_member_ids else None
                recip = r.get("recipient_id") if r.get("recipient_id") in valid_member_ids else None
                pg_conn.execute(
                    """INSERT INTO messages (id, author_id, recipient_id, body, created_at)
                       VALUES (%s, %s, %s, %s, COALESCE(%s, NOW()::text))
                       ON CONFLICT (id) DO NOTHING""",
                    (r["id"], author, recip, r["body"], r.get("created_at")),
                )

        # 8. family_meals
        if "family_meals" in tables:
            for r in _sqlite_rows(sconn, "family_meals"):
                pg_conn.execute(
                    """INSERT INTO family_meals (id, meal_date, title, notes, updated_at)
                       VALUES (%s, %s, %s, %s, COALESCE(%s, NOW()::text))
                       ON CONFLICT (meal_date) DO NOTHING""",
                    (r["id"], r["meal_date"], r["title"], r.get("notes") or "", r.get("updated_at")),
                )

        # 9. meal_requests
        if "meal_requests" in tables:
            for r in _sqlite_rows(sconn, "meal_requests"):
                if r.get("member_id") in valid_member_ids:
                    pg_conn.execute(
                        """INSERT INTO meal_requests (id, member_id, title, created_at)
                           VALUES (%s, %s, %s, COALESCE(%s, NOW()::text))
                           ON CONFLICT (id) DO NOTHING""",
                        (r["id"], r["member_id"], r["title"], r.get("created_at")),
                    )

        # 10. shopping_items
        if "shopping_items" in tables:
            for r in _sqlite_rows(sconn, "shopping_items"):
                pg_conn.execute(
                    """INSERT INTO shopping_items (id, name, quantity, checked, created_at)
                       VALUES (%s, %s, %s, %s, COALESCE(%s, NOW()::text))
                       ON CONFLICT (id) DO NOTHING""",
                    (r["id"], r["name"], r.get("quantity") or "", int(r.get("checked") or 0), r.get("created_at")),
                )

        # 11. photos_auth & photos_cache
        if "photos_auth" in tables:
            for r in _sqlite_rows(sconn, "photos_auth"):
                pg_conn.execute(
                    """INSERT INTO photos_auth
                       (id, google_access_token, google_refresh_token, google_token_expiry, google_email, updated_at)
                       VALUES (%s, %s, %s, %s, %s, COALESCE(%s, NOW()::text))
                       ON CONFLICT (id) DO NOTHING""",
                    (
                        r["id"], r.get("google_access_token"), r.get("google_refresh_token"),
                        r.get("google_token_expiry"), r.get("google_email"), r.get("updated_at"),
                    ),
                )
        if "photos_cache" in tables:
            for r in _sqlite_rows(sconn, "photos_cache"):
                pg_conn.execute(
                    """INSERT INTO photos_cache
                       (id, media_item_id, base_url, url_expiry, mime_type, created_at)
                       VALUES (%s, %s, %s, %s, %s, COALESCE(%s, NOW()::text))
                       ON CONFLICT (id) DO NOTHING""",
                    (
                        r["id"], r.get("media_item_id"), r["base_url"], r["url_expiry"],
                        r.get("mime_type") or "image/jpeg", r.get("created_at"),
                    ),
                )

        # Advance SERIAL sequences for every id-bearing table
        for tbl in (
            "members", "events", "photos_auth", "photos_cache", "chores",
            "messages", "calendar_accounts", "calendar_sources",
            "family_meals", "meal_requests", "shopping_items",
        ):
            pg_conn.execute(
                f"SELECT setval(pg_get_serial_sequence('{tbl}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {tbl}), 1), "
                f"(SELECT MAX(id) FROM {tbl}) IS NOT NULL)"
            )

        pg_conn.execute(
            """INSERT INTO settings (key, value) VALUES ('_sqlite_migrated', 'done')
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"""
        )
        pg_conn.commit()
        print("SQLite -> PostgreSQL migration completed successfully.")
        return True
    finally:
        sconn.close()
