import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import asyncio
import hashlib
import json
import psycopg
import time
from database import (
    DATABASE_URL,
    get_db,
    get_setting_value,
    get_settings_map_async,
    invalidate_settings_cache,
    notify_worker,
    notify_worker_async,
    upsert_setting_sync,
)
from migrate_sqlite import compute_is_multiday
import apple_sync
import outbound
import os as _os
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

router = APIRouter()
_sync_mutex = asyncio.Lock()


def get_app_tz(conn=None) -> str:
    del conn
    return get_setting_value("timezone", "America/New_York") or "America/New_York"


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


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "America/New_York")
    except Exception:
        return ZoneInfo("America/New_York")


def _clamp_day(year: int, month: int, day: int) -> datetime:
    for candidate in range(day, 0, -1):
        try:
            return datetime(year, month, candidate)
        except ValueError:
            continue
    return datetime(year, month, 1)


def _real_date_prefix(value: str) -> str:
    year, month, day = (int(p) for p in value[:10].split("-"))
    return _clamp_day(year, month, day).date().isoformat()


def _parse_bound(value: Optional[str], tz: ZoneInfo) -> Optional[datetime]:
    if not value:
        return None
    normalized = _real_date_prefix(value) + value[10:]
    raw = normalized.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        dt = datetime.fromisoformat(normalized[:10])
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _event_interval(row: dict, tz: ZoneInfo):
    start_raw = row.get("start_datetime") or ""
    end_raw = row.get("end_datetime") or start_raw
    if row.get("all_day") or "T" not in start_raw:
        start_day = datetime.fromisoformat(start_raw[:10]).date()
        end_day = datetime.fromisoformat(end_raw[:10]).date()
        if end_day <= start_day:
            end_day = start_day + timedelta(days=1)
        start = datetime(start_day.year, start_day.month, start_day.day, tzinfo=tz)
        end = datetime(end_day.year, end_day.month, end_day.day, tzinfo=tz)
        return start, end
    start = _parse_bound(start_raw, tz)
    end = _parse_bound(end_raw, tz)
    if end is None or end <= start:
        end = start + timedelta(minutes=1)
    return start, end


def _event_in_range(row: dict, range_start: Optional[datetime], range_end: Optional[datetime], tz: ZoneInfo) -> bool:
    try:
        ev_start, ev_end = _event_interval(row, tz)
    except Exception:
        return True
    if range_end is not None and ev_start > range_end:
        return False
    if range_start is not None and ev_end <= range_start:
        return False
    return True


def _date_shift(value: str, days: int) -> str:
    day = datetime.fromisoformat(_real_date_prefix(value))
    return (day + timedelta(days=days)).date().isoformat()


def query_events_in_window(
    conn,
    start: Optional[str] = None,
    end: Optional[str] = None,
    member_id: Optional[int] = None,
) -> list[dict]:
    """Bounded interval query using (is_multiday, start_datetime) so single-day events
    never scan historical rows from months/years ago."""
    tz = _zone(get_app_tz())

    if start and end:
        lo = _date_shift(start, -2)
        hi = _date_shift(end, 3)
        member_clause = ""
        branch_params: list = []
        if member_id is not None:
            member_clause = " AND (e.member_id = %s OR e.is_family = 1)"
            branch_params = [member_id]

        sql = f"""
            SELECT * FROM (
                SELECT e.*, m.name as member_name, m.color as member_color,
                       m.avatar as member_avatar, m.avatar_photo as member_avatar_photo
                FROM events e
                LEFT JOIN members m ON e.member_id = m.id
                WHERE e.is_multiday = 0
                  AND e.start_datetime >= %s AND e.start_datetime < %s
                  {member_clause}
                UNION ALL
                SELECT e.*, m.name as member_name, m.color as member_color,
                       m.avatar as member_avatar, m.avatar_photo as member_avatar_photo
                FROM events e
                LEFT JOIN members m ON e.member_id = m.id
                WHERE e.is_multiday = 1
                  AND e.end_datetime >= %s AND e.start_datetime < %s
                  {member_clause}
            ) sub
            ORDER BY start_datetime
        """
        params = [lo, hi, *branch_params, lo, hi, *branch_params]
        events = conn.execute(sql, params).fetchall()
    else:
        query = """
            SELECT e.*, m.name as member_name, m.color as member_color,
                   m.avatar as member_avatar, m.avatar_photo as member_avatar_photo
            FROM events e
            LEFT JOIN members m ON e.member_id = m.id
            WHERE 1=1
        """
        params = []
        if start:
            query += " AND e.end_datetime >= %s"
            params.append(_date_shift(start, -2))
        if end:
            query += " AND e.start_datetime < %s"
            params.append(_date_shift(end, 3))
        if member_id is not None:
            query += " AND (e.member_id = %s OR e.is_family = 1)"
            params.append(member_id)
        query += " ORDER BY e.start_datetime"
        events = conn.execute(query, params).fetchall()

    range_start = _parse_bound(start, tz)
    range_end = _parse_bound(end, tz)
    rows = []
    for e in events:
        row = dict(e)
        if not _event_in_range(row, range_start, range_end, tz):
            continue
        if row.get("member_color") and not row.get("is_family"):
            row["color"] = row["member_color"]
        rows.append(row)
    return rows


@router.get("/events")
def get_events(
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    member_id: Optional[int] = Query(None),
):
    with get_db() as conn:
        return query_events_in_window(conn, start=start, end=end, member_id=member_id)


def _pad_datetime(dt: str) -> str:
    if dt and len(dt) == 16:
        dt = dt + ":00"
    return dt


def _build_google_body(event_row: dict, tz: str = "America/New_York") -> dict:
    body = {
        "summary": event_row["title"],
        "description": event_row.get("description") or "",
    }
    if event_row.get("location"):
        body["location"] = event_row["location"]
    if event_row["all_day"]:
        body["start"] = {"date": event_row["start_datetime"][:10]}
        body["end"] = {"date": event_row["end_datetime"][:10]}
    else:
        body["start"] = {"dateTime": _pad_datetime(event_row["start_datetime"]), "timeZone": tz}
        body["end"] = {"dateTime": _pad_datetime(event_row["end_datetime"]), "timeZone": tz}
    return body


def _is_inbound_only(event_row: dict) -> bool:
    return bool(event_row.get("external_source_id")) and not event_row.get("google_calendar_id")


def _account_public(row) -> dict:
    d = dict(row)
    d.pop("access_token", None)
    d.pop("refresh_token", None)
    d.pop("app_password", None)
    d["connected"] = bool(row["access_token"] or row["app_password"])
    return d


def _setting(conn, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM settings WHERE key=%s", (key,)).fetchone()
    return row["value"] if row else None


def _set_setting(conn, key: str, value: Optional[str]):
    upsert_setting_sync(conn, key, value)


def _source_with_account(conn, source_id: int):
    source = conn.execute("SELECT * FROM calendar_sources WHERE id=%s", (source_id,)).fetchone()
    if not source:
        return None, None
    account = None
    if source["account_id"]:
        account = conn.execute(
            "SELECT * FROM calendar_accounts WHERE id=%s", (source["account_id"],)
        ).fetchone()
    return dict(source), (dict(account) if account else None)


def _get_member_write_source(conn, member_id: int):
    member = conn.execute(
        "SELECT id, write_calendar_source_id FROM members WHERE id=%s", (member_id,)
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
        invalidate_settings_cache(broadcast=True)


def _resolve_write_target_sync(event_row: dict):
    with get_db() as conn:
        if event_row.get("is_family"):
            return _get_family_write_source(conn)
        member_id = event_row.get("member_id")
        if not member_id:
            return None, None
        return _get_member_write_source(conn, member_id)


async def _push_event_to_write_calendar(event_row: dict) -> tuple:
    """Push a locally created/updated event to the configured write calendar without holding a DB connection."""
    if _is_inbound_only(event_row):
        return None, None, None

    source, account = await asyncio.to_thread(_resolve_write_target_sync, event_row)
    if not source:
        return None, None, None

    kind = source["kind"]
    remote_id = event_row.get("google_event_id")
    calendar_ext = source["external_id"]

    if kind == "google":
        if not account or not account.get("access_token"):
            return None, None, None
        access_token = await refresh_account_token_if_needed(account)
        body = _build_google_body(event_row, tz=get_app_tz())
        encoded = quote(calendar_ext, safe="")
        try:
            async with outbound.client(timeout=10.0) as client:
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
                ok = await asyncio.to_thread(
                    apple_sync.update_caldav_event,
                    account["apple_id"], account["app_password"], calendar_ext, remote_id, event_row,
                )
                if not ok:
                    remote_id = await asyncio.to_thread(
                        apple_sync.create_caldav_event,
                        account["apple_id"], account["app_password"], calendar_ext, event_row,
                    )
            else:
                remote_id = await asyncio.to_thread(
                    apple_sync.create_caldav_event,
                    account["apple_id"], account["app_password"], calendar_ext, event_row,
                )
            return remote_id, calendar_ext, source["id"]
        except Exception as e:
            print(f"CalDAV push error: {e}")
            return None, None, None

    return None, None, None


async def _delete_from_write_calendar(event_row: dict):
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
        source, account = await asyncio.to_thread(
            lambda: _source_with_account_standalone(source_id)
        )

    kind = source["kind"] if source else "google"

    if kind == "google":
        if not account:
            return
        try:
            access_token = await refresh_account_token_if_needed(dict(account))
            encoded = quote(calendar_id, safe="")
            async with outbound.client(timeout=10.0) as client:
                await client.delete(
                    f"https://www.googleapis.com/calendar/v3/calendars/{encoded}/events/{remote_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except Exception as e:
            print(f"Google Calendar delete error: {e}")
        return

    if kind == "caldav" and account:
        try:
            await asyncio.to_thread(
                apple_sync.delete_caldav_event,
                account["apple_id"], account["app_password"], calendar_id, remote_id,
            )
        except Exception as e:
            print(f"CalDAV delete error: {e}")


def _source_with_account_standalone(source_id: int):
    with get_db() as conn:
        return _source_with_account(conn, source_id)


def _create_event_local_sync(event: EventCreate) -> dict:
    is_family = bool(event.is_family)
    with get_db() as conn:
        if is_family:
            source, _account = _get_family_write_source(conn)
            if not source:
                raise HTTPException(status_code=400, detail="No family write calendar configured")
            if source["kind"] not in ("google", "caldav"):
                raise HTTPException(status_code=400, detail="Write calendar must be Google or iCloud CalDAV")
            member_id = None
            color = event.color or source.get("color") or "#F6BF26"
        else:
            if not event.member_id:
                raise HTTPException(status_code=400, detail="Select a person or Family for this event")
            member = conn.execute(
                "SELECT id, color, write_calendar_source_id FROM members WHERE id=%s", (event.member_id,)
            ).fetchone()
            if not member:
                raise HTTPException(status_code=404, detail="Member not found")
            if not member["write_calendar_source_id"]:
                raise HTTPException(status_code=400, detail="No write calendar configured for this member")
            source = conn.execute(
                "SELECT * FROM calendar_sources WHERE id=%s", (member["write_calendar_source_id"],)
            ).fetchone()
            if not source or source["member_id"] != event.member_id:
                raise HTTPException(status_code=400, detail="Write calendar is invalid for this member")
            if source["kind"] not in ("google", "caldav"):
                raise HTTPException(status_code=400, detail="Write calendar must be Google or iCloud CalDAV")
            member_id = event.member_id
            color = member["color"] or event.color

        multiday = compute_is_multiday(event.start_datetime, event.end_datetime)
        inserted = conn.execute(
            """
            INSERT INTO events
                (title, description, location, start_datetime, end_datetime,
                 all_day, member_id, is_family, color, recurrence, is_multiday)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                event.title, event.description, event.location,
                event.start_datetime, event.end_datetime,
                1 if event.all_day else 0, member_id, 1 if is_family else 0,
                color, event.recurrence, multiday,
            ),
        ).fetchone()
        conn.commit()
        return dict(inserted)


def _save_remote_event_ids_sync(event_id: int, remote_id: str, cal_id: str, source_id: int):
    with get_db() as conn:
        conn.execute(
            """UPDATE events
               SET google_event_id=%s, google_calendar_id=%s, external_source_id=%s
               WHERE id=%s""",
            (remote_id, cal_id, source_id, event_id),
        )
        conn.commit()


@router.post("/events")
async def create_event(event: EventCreate):
    row = await asyncio.to_thread(_create_event_local_sync, event)
    new_id = row["id"]

    remote_id, cal_id, source_id = await _push_event_to_write_calendar(row)
    if remote_id:
        await asyncio.to_thread(_save_remote_event_ids_sync, new_id, remote_id, cal_id, source_id)
        row["google_event_id"] = remote_id
        row["google_calendar_id"] = cal_id
        row["external_source_id"] = source_id
    else:
        target = "family" if event.is_family else f"member {row.get('member_id')}"
        print(f"Event {new_id} saved locally but not pushed to write calendar ({target})")

    return row


def _update_event_local_sync(event_id: int, event: EventUpdate) -> dict:
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM events WHERE id=%s", (event_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Event not found")
        updates = {k: v for k, v in event.dict().items() if v is not None}
        if "all_day" in updates:
            updates["all_day"] = 1 if updates["all_day"] else 0
        if "is_family" in updates:
            updates["is_family"] = 1 if updates["is_family"] else 0
        if "start_datetime" in updates or "end_datetime" in updates:
            s_dt = updates.get("start_datetime", existing["start_datetime"])
            e_dt = updates.get("end_datetime", existing["end_datetime"])
            updates["is_multiday"] = compute_is_multiday(s_dt, e_dt)
        if updates:
            set_clause = ", ".join(f"{k}=%s" for k in updates)
            conn.execute(f"UPDATE events SET {set_clause} WHERE id=%s", (*updates.values(), event_id))
            conn.commit()
        row = conn.execute("SELECT * FROM events WHERE id=%s", (event_id,)).fetchone()
        return dict(row)


@router.put("/events/{event_id}")
async def update_event(event_id: int, event: EventUpdate):
    row = await asyncio.to_thread(_update_event_local_sync, event_id, event)
    if not _is_inbound_only(row):
        remote_id, cal_id, source_id = await _push_event_to_write_calendar(row)
        if remote_id:
            await asyncio.to_thread(_save_remote_event_ids_sync, event_id, remote_id, cal_id, source_id)
            row["google_event_id"] = remote_id
            row["google_calendar_id"] = cal_id
            row["external_source_id"] = source_id
    return row


def _fetch_and_delete_local_sync(event_id: int, delete_now: bool = False):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM events WHERE id=%s", (event_id,)).fetchone()
        if delete_now:
            conn.execute("DELETE FROM events WHERE id=%s", (event_id,))
            conn.commit()
        return dict(row) if row else None


@router.delete("/events/{event_id}")
async def delete_event(event_id: int):
    row = await asyncio.to_thread(_fetch_and_delete_local_sync, event_id, False)
    if row:
        await _delete_from_write_calendar(row)
    await asyncio.to_thread(_fetch_and_delete_local_sync, event_id, True)
    return {"ok": True}


# ── Token Refresh ─────────────────────────────────────────────────────────────

async def refresh_account_token_if_needed(account: dict) -> str:
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
        async with outbound.client(timeout=10.0) as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                new_token = data["access_token"]
                new_expiry = (datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600))).isoformat()

                def _save_token():
                    with get_db() as conn:
                        conn.execute(
                            """UPDATE calendar_accounts
                               SET access_token=%s, token_expiry=%s, updated_at=NOW()::text
                               WHERE id=%s""",
                            (new_token, new_expiry, account_id),
                        )
                        conn.commit()

                await asyncio.to_thread(_save_token)
                return new_token
    return access_token


# ── Calendar Accounts ─────────────────────────────────────────────────────────

@router.get("/accounts")
def list_calendar_accounts():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM calendar_accounts ORDER BY provider, email, apple_id, id"
        ).fetchall()
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
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM calendar_accounts WHERE provider='apple_icloud' AND apple_id=%s",
            (apple_id,),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE calendar_accounts
                   SET app_password=%s, label=%s, email=COALESCE(email, %s), updated_at=NOW()::text
                   WHERE id=%s""",
                (app_password, label, apple_id, existing["id"]),
            )
            account_id = existing["id"]
        else:
            inserted = conn.execute(
                """INSERT INTO calendar_accounts
                   (provider, email, label, apple_id, app_password)
                   VALUES ('apple_icloud', %s, %s, %s, %s)
                   RETURNING id""",
                (apple_id, label, apple_id, app_password),
            ).fetchone()
            account_id = inserted["id"]
        conn.commit()
        row = conn.execute("SELECT * FROM calendar_accounts WHERE id=%s", (account_id,)).fetchone()
    return _account_public(row)


@router.delete("/accounts/{account_id}")
def delete_calendar_account(account_id: int):
    with get_db() as conn:
        account = conn.execute("SELECT * FROM calendar_accounts WHERE id=%s", (account_id,)).fetchone()
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")

        sources = conn.execute(
            "SELECT * FROM calendar_sources WHERE account_id=%s", (account_id,)
        ).fetchall()
        for src in sources:
            _delete_synced_events_for_source(conn, dict(src))
            conn.execute(
                "UPDATE members SET write_calendar_source_id=NULL WHERE write_calendar_source_id=%s",
                (src["id"],),
            )
            _clear_family_write_if_source(conn, src["id"])
            conn.execute("DELETE FROM calendar_sources WHERE id=%s", (src["id"],))

        conn.execute("DELETE FROM calendar_accounts WHERE id=%s", (account_id,))
        conn.commit()
    return {"ok": True}


@router.get("/accounts/{account_id}/available")
async def list_available_calendars(account_id: int):
    account = await asyncio.to_thread(_load_account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if account["provider"] == "google":
        if not account.get("access_token"):
            raise HTTPException(status_code=400, detail="Google account has no access token")
        access_token = await refresh_account_token_if_needed(account)
        async with outbound.client(timeout=10.0) as client:
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
            calendars = await asyncio.to_thread(
                apple_sync.list_icloud_calendars, account["apple_id"], account["app_password"]
            )
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
    conn.execute(
        """DELETE FROM events
           WHERE external_source_id=%s
             AND google_calendar_id IS NULL""",
        (source["id"],),
    )


def _source_row(conn, source_id: int) -> Optional[dict]:
    row = conn.execute(
        """SELECT s.*, a.email AS account_email, a.label AS account_label,
                  a.provider AS account_provider, a.apple_id AS account_apple_id
           FROM calendar_sources s
           LEFT JOIN calendar_accounts a ON a.id = s.account_id
           WHERE s.id=%s""",
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

    with get_db() as conn:
        account_id = payload.account_id
        if kind == "ics":
            account_id = None
        else:
            if not account_id:
                raise HTTPException(status_code=400, detail="account_id is required for google/caldav sources")
            account = conn.execute("SELECT * FROM calendar_accounts WHERE id=%s", (account_id,)).fetchone()
            if not account:
                raise HTTPException(status_code=404, detail="Account not found")
            if kind == "google" and account["provider"] != "google":
                raise HTTPException(status_code=400, detail="Google sources require a Google account")
            if kind == "caldav" and account["provider"] != "apple_icloud":
                raise HTTPException(status_code=400, detail="CalDAV sources require an iCloud account")

        if payload.member_id is not None:
            member = conn.execute("SELECT id FROM members WHERE id=%s", (payload.member_id,)).fetchone()
            if not member:
                raise HTTPException(status_code=404, detail="Member not found")

        inserted = conn.execute(
            """INSERT INTO calendar_sources
               (account_id, kind, name, external_id, color, member_id)
               VALUES (%s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (account_id, kind, name, external_id, payload.color, payload.member_id),
        ).fetchone()
        conn.commit()
        row = _source_row(conn, inserted["id"])

    if payload.member_id is not None:
        notify_worker("sync_all")
    return row


@router.get("/sources")
def list_calendar_sources():
    with get_db() as conn:
        rows = conn.execute(
            """SELECT s.*, a.email AS account_email, a.label AS account_label,
                      a.provider AS account_provider, a.apple_id AS account_apple_id
               FROM calendar_sources s
               LEFT JOIN calendar_accounts a ON a.id = s.account_id
               ORDER BY s.name, s.id"""
        ).fetchall()
    return [dict(r) for r in rows]


@router.patch("/sources/{source_id}")
def patch_calendar_source(source_id: int, payload: CalendarSourceUpdate):
    trigger_sync = False
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM calendar_sources WHERE id=%s", (source_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Source not found")

        data = payload.dict(exclude_unset=True)
        updates = {}
        if "name" in data and data["name"] is not None:
            updates["name"] = data["name"].strip()
        if "color" in data:
            updates["color"] = data["color"]
            updates["content_hash"] = None
        if "enabled" in data and data["enabled"] is not None:
            updates["enabled"] = 1 if data["enabled"] else 0

        if "is_family" in data or "member_id" in data:
            want_family = bool(data["is_family"]) if "is_family" in data else bool(existing["is_family"])
            new_member = data["member_id"] if "member_id" in data else existing["member_id"]
            if "is_family" in data and data["is_family"]:
                new_member = None
                want_family = True
            elif "member_id" in data and data["member_id"] is not None:
                want_family = False
            if want_family and new_member is not None:
                raise HTTPException(status_code=400, detail="Family calendars cannot also be assigned to a person")
            if new_member is not None:
                member = conn.execute("SELECT id FROM members WHERE id=%s", (new_member,)).fetchone()
                if not member:
                    raise HTTPException(status_code=404, detail="Member not found")
            if new_member is None or new_member != existing["member_id"]:
                conn.execute(
                    "UPDATE members SET write_calendar_source_id=NULL WHERE write_calendar_source_id=%s",
                    (source_id,),
                )
            if not want_family and existing["is_family"]:
                _clear_family_write_if_source(conn, source_id)
            updates["member_id"] = new_member
            updates["is_family"] = 1 if want_family else 0
            # Clear content_hash if newly assigned so worker syncs immediately
            if (new_member or want_family) and not existing["member_id"] and not existing["is_family"]:
                updates["content_hash"] = None
                updates["sync_etag"] = None
                trigger_sync = True

        if updates:
            set_clause = ", ".join(f"{k}=%s" for k in updates)
            conn.execute(f"UPDATE calendar_sources SET {set_clause} WHERE id=%s", (*updates.values(), source_id))
            if "member_id" in updates or "is_family" in updates:
                conn.execute(
                    """UPDATE events SET member_id=%s, is_family=%s
                       WHERE external_source_id=%s""",
                    (
                        updates.get("member_id", existing["member_id"]),
                        updates.get("is_family", existing["is_family"]),
                        source_id,
                    ),
                )
            if updates.get("color"):
                conn.execute(
                    """UPDATE events SET color=%s
                       WHERE external_source_id=%s
                         AND (is_family=1 OR member_id IS NULL)""",
                    (updates["color"], source_id),
                )
            conn.commit()

        row = _source_row(conn, source_id)

    if trigger_sync:
        notify_worker("sync_all")
    return row


@router.delete("/sources/{source_id}")
def delete_calendar_source(source_id: int):
    with get_db() as conn:
        src = conn.execute("SELECT * FROM calendar_sources WHERE id=%s", (source_id,)).fetchone()
        if not src:
            raise HTTPException(status_code=404, detail="Source not found")
        _delete_synced_events_for_source(conn, dict(src))
        conn.execute(
            "UPDATE members SET write_calendar_source_id=NULL WHERE write_calendar_source_id=%s",
            (source_id,),
        )
        _clear_family_write_if_source(conn, source_id)
        conn.execute("DELETE FROM calendar_sources WHERE id=%s", (source_id,))
        conn.commit()
    return {"ok": True}


@router.get("/family/write-calendar")
def get_family_write_calendar():
    with get_db() as conn:
        source, _account = _get_family_write_source(conn)
        raw = _setting(conn, "family_write_calendar_source_id")
    return {
        "source_id": int(raw) if raw and str(raw).isdigit() else None,
        "source": source,
    }


@router.put("/family/write-calendar")
def set_family_write_calendar(payload: dict):
    source_id = payload.get("source_id", payload.get("write_calendar_source_id"))
    with get_db() as conn:
        if source_id is None or source_id == "":
            _set_setting(conn, "family_write_calendar_source_id", None)
            conn.commit()
            invalidate_settings_cache(broadcast=True)
            return {"source_id": None, "source": None}
        try:
            source_id = int(source_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid source_id")
        source = conn.execute("SELECT * FROM calendar_sources WHERE id=%s", (source_id,)).fetchone()
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")
        if not source["is_family"]:
            raise HTTPException(status_code=400, detail="Write calendar must be assigned to Family")
        if source["kind"] not in ("google", "caldav"):
            raise HTTPException(status_code=400, detail="Write calendar must be Google or iCloud CalDAV")
        _set_setting(conn, "family_write_calendar_source_id", str(source_id))
        conn.commit()
        row = _source_row(conn, source_id)
    invalidate_settings_cache(broadcast=True)
    return {"source_id": source_id, "source": row}


# ── Diff-Based Sync Engine ───────────────────────────────────────────────────

GOOGLE_COLOR_MAP = {
    "1": "#ac725e", "2": "#d06b64", "3": "#f83a22", "4": "#fa573c",
    "5": "#ff7537", "6": "#ffad46", "7": "#42d692", "8": "#16a765",
    "9": "#7bd148", "10": "#b3dc6c", "11": "#fbe983", "12": "#fad165",
    "13": "#92e1c0", "14": "#9fe1e7", "15": "#9fc6e7", "16": "#4986e7",
    "17": "#9a9cff", "18": "#b99aff", "19": "#c2c2c2", "20": "#cabdbf",
    "21": "#cca6ac", "22": "#f691b2", "23": "#cd74e6", "24": "#a47ae2",
}


def _save_source_sync_meta(source_id: int, etag: Optional[str], content_hash: Optional[str]):
    with get_db() as conn:
        conn.execute(
            "UPDATE calendar_sources SET sync_etag=%s, content_hash=%s WHERE id=%s",
            (etag, content_hash, source_id),
        )
        conn.commit()


def _count_source_events(source_id: int) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM events WHERE external_source_id=%s AND google_calendar_id IS NULL",
            (source_id,),
        ).fetchone()
        return int(row["c"]) if row else 0


def _store_google_events(
    source: dict,
    google_events: list,
    cal_color,
    new_etag: Optional[str] = None,
    new_hash: Optional[str] = None,
) -> int:
    """Diff-upsert inbound Google events so unchanged events require zero writes."""
    source_id = source["id"]
    member_id = source["member_id"]
    family_flag = 1 if source.get("is_family") else 0

    prepared: list[tuple] = []
    seen_gids: set[str] = set()
    for ge in google_events:
        if ge.get("status") == "cancelled":
            continue
        gid = ge.get("id")
        if not gid or gid in seen_gids:
            continue
        seen_gids.add(gid)
        title = ge.get("summary", "(No title)")
        desc = ge.get("description", "")
        location = ge.get("location", "")
        start = ge["start"].get("dateTime") or ge["start"].get("date")
        end = ge["end"].get("dateTime") or ge["end"].get("date")
        all_day = 1 if "date" in ge["start"] and "dateTime" not in ge["start"] else 0
        color_id = ge.get("colorId")
        event_color = GOOGLE_COLOR_MAP.get(color_id) if color_id else cal_color
        multiday = compute_is_multiday(start, end)
        prepared.append((gid, title, desc, location, start, end, all_day, member_id, family_flag, event_color, multiday))

    with get_db() as conn:
        written_ids = {
            r["google_event_id"]
            for r in conn.execute(
                "SELECT google_event_id FROM events WHERE google_calendar_id IS NOT NULL AND google_event_id IS NOT NULL"
            ).fetchall()
        }

        existing_rows = conn.execute(
            """SELECT id, google_event_id, title, description, location,
                      start_datetime, end_datetime, all_day, member_id, is_family,
                      color, is_multiday
               FROM events
               WHERE external_source_id=%s AND google_calendar_id IS NULL""",
            (source_id,),
        ).fetchall()

        existing_by_gid: dict[str, dict] = {}
        duplicate_ids: list[int] = []
        for r in existing_rows:
            gid = r.get("google_event_id")
            if not gid or gid in existing_by_gid:
                duplicate_ids.append(r["id"])
            else:
                existing_by_gid[gid] = r

        active_gids: set[str] = set()
        to_insert: list[tuple] = []
        to_update: list[tuple] = []

        for gid, title, desc, location, start, end, all_day, mem_id, fam_flag, ev_color, multiday in prepared:
            if gid in written_ids:
                continue
            active_gids.add(gid)
            prev = existing_by_gid.get(gid)
            if prev is None:
                to_insert.append(
                    (title, desc, location, start, end, all_day, mem_id, fam_flag, gid, ev_color, source_id, multiday)
                )
            else:
                changed = (
                    prev["title"] != title
                    or (prev["description"] or "") != desc
                    or (prev["location"] or "") != location
                    or prev["start_datetime"] != start
                    or prev["end_datetime"] != end
                    or int(prev["all_day"] or 0) != all_day
                    or prev["member_id"] != mem_id
                    or int(prev["is_family"] or 0) != fam_flag
                    or prev["color"] != ev_color
                    or int(prev["is_multiday"] or 0) != multiday
                )
                if changed:
                    to_update.append(
                        (title, desc, location, start, end, all_day, mem_id, fam_flag, ev_color, multiday, prev["id"])
                    )

        removed_ids = duplicate_ids + [
            r["id"] for gid, r in existing_by_gid.items() if gid not in active_gids
        ]

        if removed_ids:
            conn.execute("DELETE FROM events WHERE id = ANY(%s)", (removed_ids,))
        if to_update:
            conn.executemany(
                """UPDATE events
                   SET title=%s, description=%s, location=%s, start_datetime=%s, end_datetime=%s,
                       all_day=%s, member_id=%s, is_family=%s, color=%s, is_multiday=%s
                   WHERE id=%s""",
                to_update,
            )
        if to_insert:
            conn.executemany(
                """INSERT INTO events
                   (title, description, location, start_datetime, end_datetime, all_day,
                    member_id, is_family, google_event_id, color, external_source_id, is_multiday)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                to_insert,
            )
        conn.execute(
            "UPDATE calendar_sources SET sync_etag=%s, content_hash=%s WHERE id=%s",
            (new_etag, new_hash, source_id),
        )
        conn.commit()
        return len(active_gids)


def _store_apple_events(
    member_id,
    source_id: int,
    events: list,
    is_family: bool,
    new_etag: Optional[str] = None,
    new_hash: Optional[str] = None,
) -> int:
    with get_db() as conn:
        synced = apple_sync.upsert_apple_events(
            conn, member_id, source_id, events, is_family=is_family
        )
        conn.execute(
            "UPDATE calendar_sources SET sync_etag=%s, content_hash=%s WHERE id=%s",
            (new_etag, new_hash, source_id),
        )
        conn.commit()
        return synced


async def _sync_google_source(access_token: str, source: dict) -> int:
    cal_id = source["external_id"]
    cal_color = source.get("color")
    encoded_cal_id = quote(cal_id, safe="")
    headers = {"Authorization": f"Bearer {access_token}"}

    async with outbound.client(timeout=20.0) as client:
        if not cal_color:
            cl_resp = await client.get(
                "https://www.googleapis.com/calendar/v3/users/me/calendarList",
                headers=headers,
                params={"maxResults": "250"},
            )
            if cl_resp.status_code == 200:
                for item in cl_resp.json().get("items", []):
                    if item["id"] == cal_id and item.get("backgroundColor"):
                        cal_color = item["backgroundColor"]
                        break

        window_start, window_end = apple_sync.sync_window()
        google_events = []
        page_token = None
        hasher = hashlib.sha256()
        first_etag = None

        for page_idx in range(20):
            params = {
                "timeMin": window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "timeMax": window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": "2500",
                "timeZone": get_app_tz(),
            }
            if page_token:
                params["pageToken"] = page_token
            req_headers = dict(headers)
            if page_idx == 0 and source.get("sync_etag"):
                req_headers["If-None-Match"] = source["sync_etag"]
            resp = await client.get(
                f"https://www.googleapis.com/calendar/v3/calendars/{encoded_cal_id}/events",
                headers=req_headers,
                params=params,
            )
            if resp.status_code == 304 and page_idx == 0:
                return await asyncio.to_thread(_count_source_events, source["id"])
            if resp.status_code != 200:
                print(f"Calendar fetch failed for {cal_id}: {resp.status_code} {resp.text[:200]}")
                return 0
            hasher.update(resp.content)
            body = await asyncio.to_thread(json.loads, resp.content)
            if page_idx == 0 and not body.get("nextPageToken"):
                first_etag = resp.headers.get("etag") or body.get("etag")
            google_events.extend(body.get("items") or [])
            page_token = body.get("nextPageToken")
            if not page_token:
                break

    hasher.update(f"|{cal_color or ''}|{source.get('member_id')}|{source.get('is_family')}".encode("utf-8"))
    new_hash = hasher.hexdigest()
    if source.get("content_hash") and source["content_hash"] == new_hash:
        return await asyncio.to_thread(_count_source_events, source["id"])

    return await asyncio.to_thread(
        _store_google_events, source, google_events, cal_color, first_etag, new_hash
    )


def _load_account(account_id: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM calendar_accounts WHERE id=%s", (account_id,)).fetchone()
        return dict(row) if row else None


async def _sync_source(source: dict, account: Optional[dict] = None) -> int:
    t0 = time.monotonic()
    kind = source["kind"]
    is_family = bool(source.get("is_family"))
    member_id = source["member_id"]
    if not is_family and not member_id:
        return 0
    if is_family:
        member_id = None
    color = source.get("color")
    if source.get("account_id") and not account:
        loaded = await asyncio.to_thread(_load_account, source["account_id"])
        if loaded:
            account = loaded

    if kind == "google":
        if not account or not account.get("access_token"):
            print(f"Skipping Google source {source['id']}: no account tokens")
            return 0
        access_token = await refresh_account_token_if_needed(account)
        count = await _sync_google_source(access_token, source)
        dt = time.monotonic() - t0
        if dt > 1.0:
            print(f"Synced Google source {source['id']} ({source.get('name')}): {count} events in {dt:.2f}s")
        return count

    if kind == "ics":
        try:
            changed, events, new_etag, new_hash = await apple_sync.fetch_ics_events(
                source["external_id"],
                color=color,
                etag=source.get("sync_etag"),
                content_hash=source.get("content_hash"),
            )
            if not changed:
                if new_etag != source.get("sync_etag"):
                    await asyncio.to_thread(_save_source_sync_meta, source["id"], new_etag, new_hash)
                return await asyncio.to_thread(_count_source_events, source["id"])
            count = await asyncio.to_thread(
                _store_apple_events, member_id, source["id"], events, is_family, new_etag, new_hash
            )
            dt = time.monotonic() - t0
            if dt > 1.0:
                print(f"Synced ICS source {source['id']} ({source.get('name')}): {count} events in {dt:.2f}s")
            return count
        except Exception as exc:
            print(f"ICS sync failed for source {source['id']}: {exc}")
            return 0

    if kind == "caldav":
        if not account or not account.get("apple_id") or not account.get("app_password"):
            print(f"Skipping CalDAV source {source['id']}: iCloud credentials missing")
            return 0
        try:
            events = await asyncio.to_thread(
                apple_sync.fetch_caldav_events,
                account["apple_id"], account["app_password"], source["external_id"], color,
            )
            payload_hash = hashlib.sha256(
                json.dumps(events, sort_keys=True).encode("utf-8")
            ).hexdigest()
            if source.get("content_hash") == payload_hash:
                return await asyncio.to_thread(_count_source_events, source["id"])
            count = await asyncio.to_thread(
                _store_apple_events, member_id, source["id"], events, is_family, None, payload_hash
            )
            dt = time.monotonic() - t0
            if dt > 1.0:
                print(f"Synced CalDAV source {source['id']} ({source.get('name')}): {count} events in {dt:.2f}s")
            return count
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
        query += " AND s.member_id=%s"
        params.append(member_id)
    query += " ORDER BY s.id"
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def _account_from_source_row(source: dict) -> Optional[dict]:
    if source.get("kind") == "ics":
        return None
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
    s = await get_settings_map_async()
    last = s.get("google_last_synced")
    interval = s.get("google_sync_interval")
    return {
        "last_synced": last if last else None,
        "interval_minutes": int(interval) if interval else 0,
    }


def _touch_last_synced(total: int):
    with get_db() as conn:
        upsert_setting_sync(conn, "google_last_synced", datetime.utcnow().isoformat())
        conn.execute("SELECT pg_notify('fh_events', %s)", (f"sync_done:{total}",))
        conn.commit()
    invalidate_settings_cache(broadcast=True)


async def run_full_sync() -> int:
    """Serialized full calendar sync. Intended to run in worker.py."""
    async with _sync_mutex:
        def _load_sources():
            with get_db() as c:
                return _assigned_sources(c)

        sources = await asyncio.to_thread(_load_sources)
        total = 0
        for src in sources:
            try:
                total += await _sync_source(src, _account_from_source_row(src))
            except Exception as e:
                print(f"Full sync source {src.get('id')} failed: {e}")
        await asyncio.to_thread(_touch_last_synced, total)
        return total


@router.post("/sync/all")
async def sync_all_calendars():
    """Trigger sync in the worker container so the web GIL stays completely free."""
    try:
        aconn = await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True)
        async with aconn:
            await aconn.execute("LISTEN fh_events")
            await notify_worker_async("sync_all")
            deadline = time.monotonic() + 15.0
            gen = aconn.notifies(timeout=15.0)
            async for notify in gen:
                payload = notify.payload or ""
                if payload.startswith("sync_done:"):
                    total = int(payload.split(":", 1)[1] or "0")
                    return {"synced": total}
                if time.monotonic() >= deadline:
                    break
        return {"synced": 0, "queued": True}
    except Exception as exc:
        print(f"Worker sync delegation fallback: {exc}")
        synced = await run_full_sync()
        return {"synced": synced}


def _load_member_sources_sync(member_id: int):
    with get_db() as conn:
        member = conn.execute("SELECT id FROM members WHERE id=%s", (member_id,)).fetchone()
        if not member:
            return None, []
        return dict(member), _assigned_sources(conn, member_id=member_id)


@router.post("/sync/member/{member_id}")
async def sync_member_calendars(member_id: int):
    member, sources = await asyncio.to_thread(_load_member_sources_sync, member_id)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    if not sources:
        raise HTTPException(status_code=400, detail="No calendars assigned to this member")

    synced = 0
    by_kind = {"google": 0, "ics": 0, "caldav": 0}
    for src in sources:
        count = await _sync_source(src, _account_from_source_row(src))
        synced += count
        k = src["kind"]
        by_kind[k] = by_kind.get(k, 0) + count

    return {"synced": synced, "by_kind": by_kind}
