import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from database import get_db
import apple_sync
import httpx
import os as _os
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

router = APIRouter()

def get_app_tz(conn=None) -> str:
    close = False
    if conn is None:
        conn = get_db(); close = True
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = 'timezone'").fetchone()
        return (row["value"] if row and row["value"] else None) or "America/New_York"
    finally:
        if close:
            conn.close()

GOOGLE_CLIENT_ID = _os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = _os.environ.get("GOOGLE_CLIENT_SECRET", "")

class EventCreate(BaseModel):
    title: str
    description: str = ""
    location: str = ""
    start_datetime: str
    end_datetime: str
    all_day: bool = False
    member_id: Optional[int] = None
    is_family: bool = False
    color: Optional[str] = None
    recurrence: Optional[str] = None

class EventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    start_datetime: Optional[str] = None
    end_datetime: Optional[str] = None
    all_day: Optional[bool] = None
    member_id: Optional[int] = None
    is_family: Optional[bool] = None
    color: Optional[str] = None
    location: Optional[str] = None

class IcloudAccountCreate(BaseModel):
    apple_id: str
    app_password: str
    label: Optional[str] = None

class CalendarSourceCreate(BaseModel):
    account_id: Optional[int] = None
    kind: str
    external_id: Optional[str] = None
    url: Optional[str] = None  # alias for ICS
    name: str
    color: Optional[str] = None
    member_id: Optional[int] = None

class CalendarSourceUpdate(BaseModel):
    member_id: Optional[int] = None
    is_family: Optional[bool] = None
    name: Optional[str] = None
    color: Optional[str] = None
    enabled: Optional[bool] = None

@router.get("/events")
def get_events(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    member_id: Optional[int] = Query(None)
):
    conn = get_db()
    query = """
        SELECT e.*, m.name as member_name, m.color as member_color,
               m.avatar as member_avatar, m.avatar_photo as member_avatar_photo
        FROM events e
        LEFT JOIN members m ON e.member_id = m.id
        WHERE 1=1
    """
    params = []
    if start:
        query += " AND e.end_datetime >= ?"
        params.append(start)
    if end:
        query += " AND e.start_datetime <= ?"
        params.append(end)
    if member_id:
        # Person view: that member's events plus shared family calendars
        query += " AND (e.member_id = ? OR e.is_family = 1)"
        params.append(member_id)
    query += " ORDER BY e.start_datetime"
    events = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(e) for e in events]

def _pad_datetime(dt: str) -> str:
    """Ensure datetime is full RFC3339 (needs seconds for Google API)."""
    if dt and len(dt) == 16:  # YYYY-MM-DDTHH:MM
        dt = dt + ":00"
    return dt

def _build_google_body(event_row: dict, tz: str = "America/New_York") -> dict:
    body = {
        "summary": event_row["title"],
        "description": event_row.get("description") or "",
        "location": event_row.get("location") or "",
    }
    if event_row.get("all_day"):
        body["start"] = {"date": str(event_row["start_datetime"])[:10]}
        body["end"]   = {"date": str(event_row["end_datetime"])[:10]}
    else:
        body["start"] = {"dateTime": _pad_datetime(event_row["start_datetime"]), "timeZone": tz}
        body["end"]   = {"dateTime": _pad_datetime(event_row["end_datetime"]),   "timeZone": tz}
    return body


def _is_inbound_only(event_row: dict) -> bool:
    """Inbound sync rows are not pushed back to the provider."""
    if event_row.get("google_event_id") and not event_row.get("google_calendar_id"):
        return True
    if event_row.get("external_event_id") and not event_row.get("google_calendar_id"):
        return True
    return False


def _account_public(row) -> dict:
    d = dict(row)
    return {
        "id": d["id"],
        "provider": d["provider"],
        "email": d.get("email"),
        "label": d.get("label"),
        "apple_id": d.get("apple_id"),
        "has_app_password": bool(d.get("app_password")),
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
    }


def _setting(conn, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _set_setting(conn, key: str, value: Optional[str]):
    if value is None:
        conn.execute("DELETE FROM settings WHERE key=?", (key,))
    else:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value)),
        )


def _source_with_account(conn, source_id: int):
    source = conn.execute("SELECT * FROM calendar_sources WHERE id=?", (source_id,)).fetchone()
    if not source:
        return None, None
    account = None
    if source["account_id"]:
        account = conn.execute(
            "SELECT * FROM calendar_accounts WHERE id=?", (source["account_id"],)
        ).fetchone()
    return dict(source), (dict(account) if account else None)


def _get_member_write_source(conn, member_id: int):
    member = conn.execute(
        "SELECT id, write_calendar_source_id FROM members WHERE id=?", (member_id,)
    ).fetchone()
    if not member or not member["write_calendar_source_id"]:
        return None, None
    return _source_with_account(conn, member["write_calendar_source_id"])


def _get_family_write_source(conn):
    raw = _setting(conn, "family_write_calendar_source_id")
    if not raw:
        return None, None
    try:
        source_id = int(raw)
    except (TypeError, ValueError):
        return None, None
    source, account = _source_with_account(conn, source_id)
    if not source or not source.get("is_family"):
        return None, None
    return source, account


def _clear_family_write_if_source(conn, source_id: int):
    raw = _setting(conn, "family_write_calendar_source_id")
    if raw and str(raw) == str(source_id):
        _set_setting(conn, "family_write_calendar_source_id", None)


async def _push_event_to_write_calendar(event_row: dict, conn) -> tuple:
    """Push a locally created/updated event to the configured write calendar.

    Returns (remote_id, calendar_external_id, source_id) or (None, None, None).
    """
    if _is_inbound_only(event_row):
        return None, None, None

    if event_row.get("is_family"):
        source, account = _get_family_write_source(conn)
    else:
        member_id = event_row.get("member_id")
        if not member_id:
            return None, None, None
        source, account = _get_member_write_source(conn, member_id)
    if not source:
        return None, None, None

    kind = source["kind"]
    remote_id = event_row.get("google_event_id")
    calendar_ext = source["external_id"]

    if kind == "google":
        if not account or not account.get("access_token"):
            return None, None, None
        access_token = await refresh_account_token_if_needed(account)
        body = _build_google_body(event_row, tz=get_app_tz(conn))
        encoded = quote(calendar_ext, safe="")
        try:
            async with httpx.AsyncClient() as client:
                if remote_id:
                    resp = await client.put(
                        f"https://www.googleapis.com/calendar/v3/calendars/{encoded}/events/{remote_id}",
                        headers={"Authorization": f"Bearer {access_token}"},
                        json=body,
                    )
                else:
                    resp = await client.post(
                        f"https://www.googleapis.com/calendar/v3/calendars/{encoded}/events",
                        headers={"Authorization": f"Bearer {access_token}"},
                        json=body,
                    )
            if resp.status_code in (200, 201):
                return resp.json()["id"], calendar_ext, source["id"]
            print(f"Google Calendar push failed ({calendar_ext}): {resp.status_code} {resp.text[:300]}")
        except Exception as e:
            print(f"Google Calendar push error: {e}")
        return None, None, None

    if kind == "caldav":
        if not account or not account.get("apple_id") or not account.get("app_password"):
            return None, None, None
        try:
            if remote_id:
                ok = apple_sync.update_caldav_event(
                    account["apple_id"], account["app_password"], calendar_ext, remote_id, event_row
                )
                if not ok:
                    remote_id = apple_sync.create_caldav_event(
                        account["apple_id"], account["app_password"], calendar_ext, event_row
                    )
            else:
                remote_id = apple_sync.create_caldav_event(
                    account["apple_id"], account["app_password"], calendar_ext, event_row
                )
            return remote_id, calendar_ext, source["id"]
        except Exception as e:
            print(f"CalDAV push error: {e}")
            return None, None, None

    # ICS sources are inbound-only
    return None, None, None


async def _delete_from_write_calendar(event_row: dict, conn):
    """Delete a locally-written event from its remote calendar."""
    if _is_inbound_only(event_row):
        return
    remote_id = event_row.get("google_event_id")
    calendar_id = event_row.get("google_calendar_id")
    source_id = event_row.get("external_source_id")
    if not remote_id or not calendar_id:
        return

    source = None
    account = None
    if source_id:
        source = conn.execute("SELECT * FROM calendar_sources WHERE id=?", (source_id,)).fetchone()
        if source and source["account_id"]:
            account = conn.execute(
                "SELECT * FROM calendar_accounts WHERE id=?", (source["account_id"],)
            ).fetchone()

    kind = source["kind"] if source else "google"

    if kind == "google":
        if not account:
            return
        try:
            access_token = await refresh_account_token_if_needed(dict(account))
            encoded = quote(calendar_id, safe="")
            async with httpx.AsyncClient() as client:
                await client.delete(
                    f"https://www.googleapis.com/calendar/v3/calendars/{encoded}/events/{remote_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except Exception as e:
            print(f"Google Calendar delete error: {e}")
        return

    if kind == "caldav" and account:
        try:
            apple_sync.delete_caldav_event(
                account["apple_id"], account["app_password"], calendar_id, remote_id
            )
        except Exception as e:
            print(f"CalDAV delete error: {e}")


@router.post("/events")
async def create_event(event: EventCreate):
    is_family = bool(event.is_family)
    conn = get_db()

    if is_family:
        source, _account = _get_family_write_source(conn)
        if not source:
            conn.close()
            raise HTTPException(status_code=400, detail="No family write calendar configured")
        if source["kind"] not in ("google", "caldav"):
            conn.close()
            raise HTTPException(status_code=400, detail="Write calendar must be Google or iCloud CalDAV")
        member_id = None
        color = event.color or source.get("color") or "#F6BF26"
    else:
        if not event.member_id:
            conn.close()
            raise HTTPException(status_code=400, detail="Select a person or Family for this event")
        member = conn.execute(
            "SELECT id, write_calendar_source_id FROM members WHERE id=?", (event.member_id,)
        ).fetchone()
        if not member:
            conn.close()
            raise HTTPException(status_code=404, detail="Member not found")
        if not member["write_calendar_source_id"]:
            conn.close()
            raise HTTPException(status_code=400, detail="No write calendar configured for this member")
        source = conn.execute(
            "SELECT * FROM calendar_sources WHERE id=?", (member["write_calendar_source_id"],)
        ).fetchone()
        if not source or source["member_id"] != event.member_id:
            conn.close()
            raise HTTPException(status_code=400, detail="Write calendar is invalid for this member")
        if source["kind"] not in ("google", "caldav"):
            conn.close()
            raise HTTPException(status_code=400, detail="Write calendar must be Google or iCloud CalDAV")
        member_id = event.member_id
        color = event.color

    cur = conn.execute("""
        INSERT INTO events (title, description, location, start_datetime, end_datetime, all_day, member_id, is_family, color, recurrence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (event.title, event.description, event.location, event.start_datetime, event.end_datetime,
          1 if event.all_day else 0, member_id, 1 if is_family else 0, color, event.recurrence))
    conn.commit()
    new_id = cur.lastrowid
    row = dict(conn.execute("SELECT * FROM events WHERE id=?", (new_id,)).fetchone())

    remote_id, cal_id, source_id = await _push_event_to_write_calendar(row, conn)
    if remote_id:
        conn.execute(
            """UPDATE events
               SET google_event_id=?, google_calendar_id=?, external_source_id=?
               WHERE id=?""",
            (remote_id, cal_id, source_id, new_id),
        )
        conn.commit()
        row["google_event_id"] = remote_id
        row["google_calendar_id"] = cal_id
        row["external_source_id"] = source_id
    else:
        target = "family" if is_family else f"member {member_id}"
        print(f"Event {new_id} saved locally but not pushed to write calendar ({target})")

    conn.close()
    return row

@router.put("/events/{event_id}")
async def update_event(event_id: int, event: EventUpdate):
    conn = get_db()
    existing = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Event not found")
    updates = {k: v for k, v in event.dict().items() if v is not None}
    if "all_day" in updates: updates["all_day"] = 1 if updates["all_day"] else 0
    if "is_family" in updates: updates["is_family"] = 1 if updates["is_family"] else 0
    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE events SET {set_clause} WHERE id=?", (*updates.values(), event_id))
        conn.commit()
    row = dict(conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone())

    if not _is_inbound_only(row):
        remote_id, cal_id, source_id = await _push_event_to_write_calendar(row, conn)
        if remote_id:
            conn.execute(
                """UPDATE events
                   SET google_event_id=?, google_calendar_id=?, external_source_id=?
                   WHERE id=?""",
                (remote_id, cal_id, source_id, event_id),
            )
            conn.commit()
            row["google_event_id"] = remote_id
            row["google_calendar_id"] = cal_id
            row["external_source_id"] = source_id

    conn.close()
    return row

@router.delete("/events/{event_id}")
async def delete_event(event_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if row:
        await _delete_from_write_calendar(dict(row), conn)
    conn.execute("DELETE FROM events WHERE id=?", (event_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

# ── Token Refresh ─────────────────────────────────────────────────────────────

async def refresh_account_token_if_needed(account: dict) -> str:
    """Refresh Google OAuth tokens stored on a calendar_accounts row."""
    expiry = account.get("token_expiry")
    access_token = account.get("access_token")
    refresh_token = account.get("refresh_token")
    account_id = account.get("id")
    needs_refresh = True
    if expiry:
        try:
            exp_dt = datetime.fromisoformat(expiry)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            needs_refresh = datetime.now(timezone.utc) >= exp_dt
        except Exception:
            needs_refresh = True
    if needs_refresh and refresh_token:
        async with httpx.AsyncClient() as client:
            resp = await client.post("https://oauth2.googleapis.com/token", data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            })
            if resp.status_code == 200:
                data = resp.json()
                new_token = data["access_token"]
                new_expiry = (datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600))).isoformat()
                conn = get_db()
                conn.execute(
                    """UPDATE calendar_accounts
                       SET access_token=?, token_expiry=?, updated_at=datetime('now')
                       WHERE id=?""",
                    (new_token, new_expiry, account_id),
                )
                conn.commit()
                conn.close()
                return new_token
    return access_token



# ── Calendar Accounts ─────────────────────────────────────────────────────────

@router.get("/accounts")
def list_calendar_accounts():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM calendar_accounts ORDER BY provider, email, apple_id, id"
    ).fetchall()
    conn.close()
    return [_account_public(r) for r in rows]


@router.post("/accounts/icloud")
def create_icloud_account(payload: IcloudAccountCreate):
    apple_id = payload.apple_id.strip()
    app_password = payload.app_password.strip().replace(" ", "")
    if not apple_id or not app_password:
        raise HTTPException(status_code=400, detail="Apple ID and app-specific password are required")
    try:
        apple_sync.list_icloud_calendars(apple_id, app_password)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"iCloud login failed: {exc}") from exc

    label = (payload.label or apple_id).strip()
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM calendar_accounts WHERE provider='apple_icloud' AND apple_id=?",
        (apple_id,),
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE calendar_accounts
               SET app_password=?, label=?, email=COALESCE(email, ?), updated_at=datetime('now')
               WHERE id=?""",
            (app_password, label, apple_id, existing["id"]),
        )
        account_id = existing["id"]
    else:
        cur = conn.execute(
            """INSERT INTO calendar_accounts
               (provider, email, label, apple_id, app_password)
               VALUES ('apple_icloud', ?, ?, ?, ?)""",
            (apple_id, label, apple_id, app_password),
        )
        account_id = cur.lastrowid
    conn.commit()
    row = conn.execute("SELECT * FROM calendar_accounts WHERE id=?", (account_id,)).fetchone()
    conn.close()
    return _account_public(row)


@router.delete("/accounts/{account_id}")
def delete_calendar_account(account_id: int):
    conn = get_db()
    account = conn.execute("SELECT * FROM calendar_accounts WHERE id=?", (account_id,)).fetchone()
    if not account:
        conn.close()
        raise HTTPException(status_code=404, detail="Account not found")

    sources = conn.execute(
        "SELECT * FROM calendar_sources WHERE account_id=?", (account_id,)
    ).fetchall()
    for src in sources:
        _delete_synced_events_for_source(conn, dict(src))
        conn.execute(
            "UPDATE members SET write_calendar_source_id=NULL WHERE write_calendar_source_id=?",
            (src["id"],),
        )
        _clear_family_write_if_source(conn, src["id"])
        conn.execute("DELETE FROM calendar_sources WHERE id=?", (src["id"],))

    conn.execute("DELETE FROM calendar_accounts WHERE id=?", (account_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.get("/accounts/{account_id}/available")
async def list_available_calendars(account_id: int):
    conn = get_db()
    account = conn.execute("SELECT * FROM calendar_accounts WHERE id=?", (account_id,)).fetchone()
    conn.close()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    account = dict(account)

    if account["provider"] == "google":
        if not account.get("access_token"):
            raise HTTPException(status_code=400, detail="Google account has no access token")
        access_token = await refresh_account_token_if_needed(account)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://www.googleapis.com/calendar/v3/users/me/calendarList",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Google API error: {resp.text}")
            items = resp.json().get("items", [])
        return {
            "calendars": [
                {
                    "id": c["id"],
                    "external_id": c["id"],
                    "name": c.get("summary", ""),
                    "primary": c.get("primary", False),
                    "backgroundColor": c.get("backgroundColor", "#4F6EF7"),
                    "kind": "google",
                }
                for c in items
            ]
        }

    if account["provider"] == "apple_icloud":
        if not account.get("apple_id") or not account.get("app_password"):
            raise HTTPException(status_code=400, detail="iCloud credentials missing")
        try:
            calendars = apple_sync.list_icloud_calendars(account["apple_id"], account["app_password"])
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to list iCloud calendars: {exc}") from exc
        return {
            "calendars": [
                {
                    "id": c["url"],
                    "external_id": c["url"],
                    "name": c["name"],
                    "url": c["url"],
                    "kind": "caldav",
                }
                for c in calendars
            ]
        }

    raise HTTPException(status_code=400, detail=f"Unknown provider: {account['provider']}")


# ── Calendar Sources ──────────────────────────────────────────────────────────

def _delete_synced_events_for_source(conn, source: dict):
    """Remove inbound synced events for a source; keep locally-written rows."""
    conn.execute(
        """DELETE FROM events
           WHERE external_source_id=?
             AND google_calendar_id IS NULL""",
        (source["id"],),
    )


def _source_row(conn, source_id: int) -> Optional[dict]:
    row = conn.execute(
        """SELECT s.*, a.email AS account_email, a.label AS account_label,
                  a.provider AS account_provider, a.apple_id AS account_apple_id
           FROM calendar_sources s
           LEFT JOIN calendar_accounts a ON a.id = s.account_id
           WHERE s.id=?""",
        (source_id,),
    ).fetchone()
    return dict(row) if row else None


@router.post("/sources")
def create_calendar_source(payload: CalendarSourceCreate):
    kind = (payload.kind or "").strip().lower()
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    if kind not in ("google", "ics", "caldav"):
        raise HTTPException(status_code=400, detail="kind must be google, ics, or caldav")

    external_id = (payload.external_id or payload.url or "").strip()
    if kind == "ics":
        external_id = apple_sync.normalize_ics_url(external_id)
    if not external_id:
        raise HTTPException(status_code=400, detail="external_id (or url for ICS) is required")

    conn = get_db()
    account_id = payload.account_id
    if kind == "ics":
        account_id = None
    else:
        if not account_id:
            conn.close()
            raise HTTPException(status_code=400, detail="account_id is required for google/caldav sources")
        account = conn.execute("SELECT * FROM calendar_accounts WHERE id=?", (account_id,)).fetchone()
        if not account:
            conn.close()
            raise HTTPException(status_code=404, detail="Account not found")
        if kind == "google" and account["provider"] != "google":
            conn.close()
            raise HTTPException(status_code=400, detail="Google sources require a Google account")
        if kind == "caldav" and account["provider"] != "apple_icloud":
            conn.close()
            raise HTTPException(status_code=400, detail="CalDAV sources require an iCloud account")

    if payload.member_id is not None:
        member = conn.execute("SELECT id FROM members WHERE id=?", (payload.member_id,)).fetchone()
        if not member:
            conn.close()
            raise HTTPException(status_code=404, detail="Member not found")

    cur = conn.execute(
        """INSERT INTO calendar_sources
           (account_id, kind, name, external_id, color, member_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (account_id, kind, name, external_id, payload.color, payload.member_id),
    )
    conn.commit()
    source_id = cur.lastrowid
    row = _source_row(conn, source_id)
    conn.close()
    return row


@router.get("/sources")
def list_calendar_sources():
    conn = get_db()
    rows = conn.execute(
        """SELECT s.*, a.email AS account_email, a.label AS account_label,
                  a.provider AS account_provider, a.apple_id AS account_apple_id
           FROM calendar_sources s
           LEFT JOIN calendar_accounts a ON a.id = s.account_id
           ORDER BY s.name, s.id"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.patch("/sources/{source_id}")
def patch_calendar_source(source_id: int, payload: CalendarSourceUpdate):
    conn = get_db()
    existing = conn.execute("SELECT * FROM calendar_sources WHERE id=?", (source_id,)).fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Source not found")

    data = payload.dict(exclude_unset=True)
    updates = {}
    if "name" in data and data["name"] is not None:
        updates["name"] = data["name"].strip()
    if "color" in data:
        updates["color"] = data["color"]
    if "enabled" in data and data["enabled"] is not None:
        updates["enabled"] = 1 if data["enabled"] else 0

    # Assignment: person XOR family XOR unassigned
    if "is_family" in data or "member_id" in data:
        want_family = bool(data["is_family"]) if "is_family" in data else bool(existing["is_family"])
        new_member = data["member_id"] if "member_id" in data else existing["member_id"]
        if "is_family" in data and data["is_family"]:
            new_member = None
            want_family = True
        elif "member_id" in data and data["member_id"] is not None:
            want_family = False
        if want_family and new_member is not None:
            conn.close()
            raise HTTPException(status_code=400, detail="Family calendars cannot also be assigned to a person")
        if new_member is not None:
            member = conn.execute("SELECT id FROM members WHERE id=?", (new_member,)).fetchone()
            if not member:
                conn.close()
                raise HTTPException(status_code=404, detail="Member not found")
        if new_member is None or new_member != existing["member_id"]:
            conn.execute(
                "UPDATE members SET write_calendar_source_id=NULL WHERE write_calendar_source_id=?",
                (source_id,),
            )
        if not want_family and existing["is_family"]:
            _clear_family_write_if_source(conn, source_id)
        updates["member_id"] = new_member
        updates["is_family"] = 1 if want_family else 0

    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE calendar_sources SET {set_clause} WHERE id=?", (*updates.values(), source_id))
        conn.commit()

    row = _source_row(conn, source_id)
    conn.close()
    return row


@router.delete("/sources/{source_id}")
def delete_calendar_source(source_id: int):
    conn = get_db()
    src = conn.execute("SELECT * FROM calendar_sources WHERE id=?", (source_id,)).fetchone()
    if not src:
        conn.close()
        raise HTTPException(status_code=404, detail="Source not found")
    _delete_synced_events_for_source(conn, dict(src))
    conn.execute(
        "UPDATE members SET write_calendar_source_id=NULL WHERE write_calendar_source_id=?",
        (source_id,),
    )
    _clear_family_write_if_source(conn, source_id)
    conn.execute("DELETE FROM calendar_sources WHERE id=?", (source_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.get("/family/write-calendar")
def get_family_write_calendar():
    conn = get_db()
    source, _account = _get_family_write_source(conn)
    raw = _setting(conn, "family_write_calendar_source_id")
    conn.close()
    return {
        "source_id": int(raw) if raw and str(raw).isdigit() else None,
        "source": source,
    }


@router.put("/family/write-calendar")
def set_family_write_calendar(payload: dict):
    source_id = payload.get("source_id", payload.get("write_calendar_source_id"))
    conn = get_db()
    if source_id is None or source_id == "":
        _set_setting(conn, "family_write_calendar_source_id", None)
        conn.commit()
        conn.close()
        return {"source_id": None, "source": None}
    try:
        source_id = int(source_id)
    except (TypeError, ValueError):
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid source_id")
    source = conn.execute("SELECT * FROM calendar_sources WHERE id=?", (source_id,)).fetchone()
    if not source:
        conn.close()
        raise HTTPException(status_code=404, detail="Source not found")
    if not source["is_family"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Write calendar must be assigned to Family")
    if source["kind"] not in ("google", "caldav"):
        conn.close()
        raise HTTPException(status_code=400, detail="Write calendar must be Google or iCloud CalDAV")
    _set_setting(conn, "family_write_calendar_source_id", str(source_id))
    conn.commit()
    row = _source_row(conn, source_id)
    conn.close()
    return {"source_id": source_id, "source": row}


# ── Sync ──────────────────────────────────────────────────────────────────────

GOOGLE_COLOR_MAP = {
    "1": "#ac725e", "2": "#d06b64", "3": "#f83a22", "4": "#fa573c",
    "5": "#ff7537", "6": "#ffad46", "7": "#42d692", "8": "#16a765",
    "9": "#7bd148", "10": "#b3dc6c", "11": "#fbe983", "12": "#fad165",
    "13": "#92e1c0", "14": "#9fe1e7", "15": "#9fc6e7", "16": "#4986e7",
    "17": "#9a9cff", "18": "#b99aff", "19": "#c2c2c2", "20": "#cabdbf",
    "21": "#cca6ac", "22": "#f691b2", "23": "#cd74e6", "24": "#a47ae2",
}


async def _sync_google_source(access_token: str, source: dict, conn) -> int:
    """Sync inbound events from one Google calendar_sources row."""
    cal_id = source["external_id"]
    member_id = source["member_id"]
    source_id = source["id"]
    cal_color = source.get("color")
    encoded_cal_id = quote(cal_id, safe="")

    # Prefer calendarList backgroundColor when available
    async with httpx.AsyncClient() as client:
        cl_resp = await client.get(
            "https://www.googleapis.com/calendar/v3/users/me/calendarList",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"maxResults": "250"},
        )
        if cl_resp.status_code == 200:
            for item in cl_resp.json().get("items", []):
                if item["id"] == cal_id and item.get("backgroundColor"):
                    cal_color = item["backgroundColor"]
                    break

    window_start, window_end = apple_sync.sync_window()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://www.googleapis.com/calendar/v3/calendars/{encoded_cal_id}/events",
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "timeMin": window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "timeMax": window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": "2500",
                "timeZone": get_app_tz(),
            },
        )
        if resp.status_code != 200:
            print(f"Calendar fetch failed for {cal_id}: {resp.status_code} {resp.text[:200]}")
            return 0
        google_events = resp.json().get("items", [])

    # Clear inbound for this source; preserve locally-written events
    conn.execute(
        """DELETE FROM events
           WHERE external_source_id=? AND google_calendar_id IS NULL""",
        (source_id,),
    )

    synced = 0
    for ge in google_events:
        if ge.get("status") == "cancelled":
            continue
        gid = ge["id"]
        # Skip events we ourselves wrote (already stored with google_calendar_id)
        written = conn.execute(
            """SELECT id FROM events
               WHERE google_event_id=? AND google_calendar_id IS NOT NULL""",
            (gid,),
        ).fetchone()
        if written:
            continue
        title = ge.get("summary", "(No title)")
        desc = ge.get("description", "")
        location = ge.get("location", "")
        start = ge["start"].get("dateTime") or ge["start"].get("date")
        end = ge["end"].get("dateTime") or ge["end"].get("date")
        all_day = 1 if "date" in ge["start"] and "dateTime" not in ge["start"] else 0
        color_id = ge.get("colorId")
        event_color = GOOGLE_COLOR_MAP.get(color_id) if color_id else cal_color
        family_flag = 1 if source.get("is_family") else 0
        conn.execute(
            """INSERT INTO events
               (title, description, location, start_datetime, end_datetime, all_day,
                member_id, is_family, google_event_id, color, external_source_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, desc, location, start, end, all_day, member_id, family_flag, gid, event_color, source_id),
        )
        synced += 1
    return synced


async def _sync_source(source: dict, account: Optional[dict], conn) -> int:
    kind = source["kind"]
    is_family = bool(source.get("is_family"))
    member_id = source["member_id"]
    if not is_family and not member_id:
        return 0
    if is_family:
        member_id = None
    color = source.get("color")

    if kind == "google":
        if not account or not account.get("access_token"):
            print(f"Skipping Google source {source['id']}: no account tokens")
            return 0
        access_token = await refresh_account_token_if_needed(account)
        return await _sync_google_source(access_token, source, conn)

    if kind == "ics":
        try:
            events = await apple_sync.fetch_ics_events(source["external_id"], color=color)
            return apple_sync.upsert_apple_events(
                conn, member_id, source["id"], events, is_family=is_family
            )
        except Exception as exc:
            print(f"ICS sync failed for source {source['id']}: {exc}")
            return 0

    if kind == "caldav":
        if not account or not account.get("apple_id") or not account.get("app_password"):
            print(f"Skipping CalDAV source {source['id']}: iCloud credentials missing")
            return 0
        try:
            events = apple_sync.fetch_caldav_events(
                account["apple_id"], account["app_password"], source["external_id"], color=color
            )
            return apple_sync.upsert_apple_events(
                conn, member_id, source["id"], events, is_family=is_family
            )
        except Exception as exc:
            print(f"CalDAV sync failed for source {source['id']}: {exc}")
            return 0

    return 0


def _assigned_sources(conn, member_id: Optional[int] = None):
    query = """
        SELECT s.*, a.access_token, a.refresh_token, a.token_expiry,
               a.apple_id, a.app_password, a.provider, a.email AS account_email
        FROM calendar_sources s
        LEFT JOIN calendar_accounts a ON a.id = s.account_id
        WHERE s.enabled=1 AND (s.member_id IS NOT NULL OR s.is_family=1)
    """
    params = []
    if member_id is not None:
        # Member sync: that person's calendars only (family synced via sync/all)
        query += " AND s.member_id=?"
        params.append(member_id)
    query += " ORDER BY s.id"
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def _account_from_source_row(source: dict) -> Optional[dict]:
    if not source.get("account_id") and not source.get("access_token") and not source.get("apple_id"):
        # Need account_id from the join — sources without account (ICS) return None
        if source.get("kind") == "ics":
            return None
    if source.get("kind") == "ics":
        return None
    # Rebuild account dict from joined columns
    if not source.get("access_token") and not source.get("apple_id"):
        return None
    return {
        "id": source.get("account_id"),
        "access_token": source.get("access_token"),
        "refresh_token": source.get("refresh_token"),
        "token_expiry": source.get("token_expiry"),
        "apple_id": source.get("apple_id"),
        "app_password": source.get("app_password"),
        "provider": source.get("provider"),
        "email": source.get("account_email"),
    }


@router.get("/sync/status")
async def get_sync_status():
    conn = get_db()
    last = conn.execute("SELECT value FROM settings WHERE key='google_last_synced'").fetchone()
    interval = conn.execute("SELECT value FROM settings WHERE key='google_sync_interval'").fetchone()
    conn.close()
    return {
        "last_synced": last["value"] if last else None,
        "interval_minutes": int(interval["value"]) if interval else 0,
    }

async def run_full_sync():
    """Auto-sync all assigned calendar sources. Called by background loop."""
    conn = get_db()
    sources = _assigned_sources(conn)
    conn.close()
    total = 0
    conn = get_db()
    for src in sources:
        try:
            account = _account_from_source_row(src)
            # Re-load full account row for token refresh updates
            if src.get("account_id"):
                acct_row = conn.execute(
                    "SELECT * FROM calendar_accounts WHERE id=?", (src["account_id"],)
                ).fetchone()
                account = dict(acct_row) if acct_row else account
            total += await _sync_source(src, account, conn)
            conn.commit()
        except Exception as e:
            print(f"Full sync source {src.get('id')} failed: {e}")
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('google_last_synced', ?)",
        (datetime.utcnow().isoformat(),),
    )
    conn.commit()
    conn.close()
    return total

@router.post("/sync/all")
async def sync_all_calendars():
    synced = await run_full_sync()
    return {"synced": synced}



@router.post("/sync/member/{member_id}")
async def sync_member_calendars(member_id: int):
    """Sync all enabled calendar_sources assigned to this member."""
    conn = get_db()
    member = conn.execute("SELECT id FROM members WHERE id=?", (member_id,)).fetchone()
    if not member:
        conn.close()
        raise HTTPException(status_code=404, detail="Member not found")

    sources = _assigned_sources(conn, member_id=member_id)
    if not sources:
        conn.close()
        raise HTTPException(status_code=400, detail="No calendars assigned to this member")

    synced = 0
    by_kind = {"google": 0, "ics": 0, "caldav": 0}
    for src in sources:
        account = None
        if src.get("account_id"):
            acct_row = conn.execute(
                "SELECT * FROM calendar_accounts WHERE id=?", (src["account_id"],)
            ).fetchone()
            account = dict(acct_row) if acct_row else None
        count = await _sync_source(src, account, conn)
        synced += count
        by_kind[src["kind"]] = by_kind.get(src["kind"], 0) + count

    conn.commit()
    conn.close()
    return {
        "synced": synced,
        "google": by_kind.get("google", 0),
        "apple": by_kind.get("ics", 0) + by_kind.get("caldav", 0),
    }
