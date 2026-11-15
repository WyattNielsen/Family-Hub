"""Apple Calendar sync helpers (ICS/webcal feeds + iCloud CalDAV) with ETag/hash & row diffing."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
from typing import Optional
from urllib.parse import urlparse

import asyncio

from icalendar import Calendar

from migrate_sqlite import compute_is_multiday
import outbound

try:
    import recurring_ical_events
except ImportError:  # pragma: no cover
    recurring_ical_events = None

ICLOUD_CALDAV_URL = "https://caldav.icloud.com/"


def normalize_ics_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://") :]
    elif url.startswith("webcals://"):
        url = "https://" + url[len("webcals://") :]
    return url


def sync_window(years_back: int = 1, years_forward: int = 2) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=365 * years_back)
    end = now + timedelta(days=365 * years_forward)
    return start, end


def _as_utc_naive_iso(value) -> tuple[str, int]:
    """Return (iso datetime or date string, all_day flag)."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat(timespec="seconds"), 0
    if isinstance(value, date):
        return value.isoformat(), 1
    text = str(value)
    if "T" in text:
        return text.replace("Z", ""), 0
    return text[:10], 1


def _event_fields_from_component(component, fallback_color: Optional[str] = None) -> Optional[dict]:
    if component.name != "VEVENT":
        return None
    uid = str(component.get("UID") or "").strip()
    if not uid:
        return None
    summary = str(component.get("SUMMARY") or "(No title)")
    description = str(component.get("DESCRIPTION") or "")
    location = str(component.get("LOCATION") or "")
    dtstart = component.get("DTSTART")
    dtend = component.get("DTEND")
    if dtstart is None:
        return None
    start_raw = dtstart.dt
    start, all_day = _as_utc_naive_iso(start_raw)
    if dtend is not None:
        end, end_all_day = _as_utc_naive_iso(dtend.dt)
        all_day = 1 if all_day or end_all_day else 0
    else:
        end = None

    if all_day:
        try:
            start_d = date.fromisoformat(str(start)[:10])
            if end:
                end_d = date.fromisoformat(str(end)[:10])
            else:
                end_d = start_d
            if end_d <= start_d:
                end_d = start_d + timedelta(days=1)
            end = end_d.isoformat()
            start = start_d.isoformat()
        except Exception:
            end = end or start
    elif end is None:
        try:
            end = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat(timespec="seconds")
        except Exception:
            end = start

    return {
        "uid": uid,
        "title": summary,
        "description": description,
        "location": location,
        "start_datetime": start,
        "end_datetime": end,
        "all_day": all_day,
        "color": fallback_color,
    }


def _events_from_ics(text: str, color: Optional[str] = None) -> list[dict]:
    cal = Calendar.from_ical(text)
    start, end = sync_window()
    events: list[dict] = []

    if recurring_ical_events is not None:
        for component in recurring_ical_events.of(cal).between(start, end):
            fields = _event_fields_from_component(component, color)
            if fields:
                fields["uid"] = f"{fields['uid']}@{fields['start_datetime']}"
                events.append(fields)
        return events

    for component in cal.walk():
        fields = _event_fields_from_component(component, color)
        if fields:
            events.append(fields)
    return events


async def fetch_ics_events(
    url: str,
    color: Optional[str] = None,
    etag: Optional[str] = None,
    content_hash: Optional[str] = None,
) -> tuple[bool, list[dict], Optional[str], Optional[str]]:
    """Fetch ICS feed with ETag/SHA-256 skip.

    Returns (changed, events, new_etag, new_hash).
    When changed is False, callers skip parsing and DB writes completely.
    """
    url = normalize_ics_url(url)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("ICS URL must be http(s) or webcal")

    req_headers = {"User-Agent": "FamilyHub/1.0"}
    if etag:
        req_headers["If-None-Match"] = etag

    async with outbound.client(timeout=45.0, headers=req_headers) as client:
        resp = await client.get(url)
        if resp.status_code == 304:
            return False, [], etag, content_hash
        if resp.status_code != 200:
            raise ValueError(f"ICS fetch failed ({resp.status_code})")
        raw_bytes = resp.content
        new_etag = resp.headers.get("etag")
        text = resp.text

    # Hash includes the window day + color so daily rolling windows still advance once/day
    day_bucket = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    digest = hashlib.sha256(raw_bytes + f"|{day_bucket}|{color or ''}".encode("utf-8")).hexdigest()
    if content_hash and digest == content_hash:
        return False, [], new_etag or etag, digest

    events = await asyncio.to_thread(_events_from_ics, text, color)
    return True, events, new_etag, digest


def list_icloud_calendars(apple_id: str, app_password: str) -> list[dict]:
    from caldav import DAVClient

    client = DAVClient(url=ICLOUD_CALDAV_URL, username=apple_id, password=app_password, timeout=20)
    principal = client.principal()
    calendars = []
    for cal in principal.calendars():
        name = None
        try:
            name = cal.get_display_name()
        except Exception:
            pass
        url = str(cal.url)
        calendars.append(
            {
                "id": url,
                "url": url,
                "name": name or url.rstrip("/").split("/")[-1] or "Calendar",
            }
        )
    return calendars


def fetch_caldav_events(apple_id: str, app_password: str, calendar_url: str, color: Optional[str] = None) -> list[dict]:
    from caldav import DAVClient

    client = DAVClient(url=ICLOUD_CALDAV_URL, username=apple_id, password=app_password, timeout=20)
    cal = client.calendar(url=calendar_url)
    start, end = sync_window()
    events: list[dict] = []
    try:
        results = cal.search(start=start, end=end, event=True, expand=True)
    except TypeError:
        results = cal.date_search(start=start, end=end, expand=True)
    except Exception:
        results = cal.events()

    for item in results:
        try:
            ical_text = item.data
            if hasattr(ical_text, "decode"):
                ical_text = ical_text.decode("utf-8", errors="replace")
            parsed = Calendar.from_ical(ical_text)
            for component in parsed.walk():
                fields = _event_fields_from_component(component, color)
                if not fields:
                    continue
                fields["uid"] = f"{fields['uid']}@{fields['start_datetime']}"
                events.append(fields)
        except Exception as exc:
            print(f"CalDAV event parse error: {exc}")
            continue
    return events


def upsert_apple_events(
    conn,
    member_id: Optional[int],
    source_id: int,
    events: list[dict],
    *,
    is_family: bool = False,
) -> int:
    """Diff-sync Apple/ICS inbound events for a source so unchanged rows stay untouched."""
    prefix = f"apple:{source_id}:"
    family_flag = 1 if is_family else 0

    # Clean up any legacy rows without external_source_id once
    if member_id is not None:
        conn.execute(
            """DELETE FROM events
               WHERE member_id=%s AND external_event_id LIKE %s
                 AND google_calendar_id IS NULL AND external_source_id IS NULL""",
            (member_id, prefix + "%"),
        )

    existing_rows = conn.execute(
        """SELECT id, external_event_id, title, description, location,
                  start_datetime, end_datetime, all_day, member_id, is_family,
                  color, is_multiday
           FROM events
           WHERE external_source_id=%s AND google_calendar_id IS NULL""",
        (source_id,),
    ).fetchall()

    existing_by_ext: dict[str, dict] = {}
    duplicate_ids: list[int] = []
    for row in existing_rows:
        ext_id = row.get("external_event_id")
        if not ext_id or ext_id in existing_by_ext:
            duplicate_ids.append(row["id"])
        else:
            existing_by_ext[ext_id] = row

    seen_ext_ids: set[str] = set()
    to_insert: list[tuple] = []
    to_update: list[tuple] = []

    for ev in events:
        ext_id = f"{prefix}{ev['uid']}"
        if ext_id in seen_ext_ids:
            continue
        seen_ext_ids.add(ext_id)
        title = ev["title"]
        desc = ev.get("description") or ""
        loc = ev.get("location") or ""
        start_dt = ev["start_datetime"]
        end_dt = ev["end_datetime"]
        all_day = int(ev.get("all_day") or 0)
        color = ev.get("color")
        multiday = compute_is_multiday(start_dt, end_dt)

        prev = existing_by_ext.get(ext_id)
        if prev is None:
            to_insert.append(
                (title, desc, loc, start_dt, end_dt, all_day, member_id, family_flag, color, ext_id, source_id, multiday)
            )
        else:
            changed = (
                prev["title"] != title
                or (prev["description"] or "") != desc
                or (prev["location"] or "") != loc
                or prev["start_datetime"] != start_dt
                or prev["end_datetime"] != end_dt
                or int(prev["all_day"] or 0) != all_day
                or prev["member_id"] != member_id
                or int(prev["is_family"] or 0) != family_flag
                or prev["color"] != color
                or int(prev["is_multiday"] or 0) != multiday
            )
            if changed:
                to_update.append(
                    (title, desc, loc, start_dt, end_dt, all_day, member_id, family_flag, color, multiday, prev["id"])
                )

    removed_ids = duplicate_ids + [
        row["id"] for ext_id, row in existing_by_ext.items() if ext_id not in seen_ext_ids
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
                member_id, is_family, color, external_event_id, external_source_id, is_multiday)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            to_insert,
        )
    return len(seen_ext_ids)


def _vevent_ical(event_row: dict, uid: str) -> bytes:
    from icalendar import Calendar, Event as VEvent
    from datetime import date as date_cls, datetime as dt_cls

    cal = Calendar()
    cal.add("prodid", "-//Family Hub//EN")
    cal.add("version", "2.0")
    vevent = VEvent()
    vevent.add("uid", uid)
    vevent.add("summary", event_row.get("title") or "(No title)")
    if event_row.get("description"):
        vevent.add("description", event_row["description"])
    if event_row.get("location"):
        vevent.add("location", event_row["location"])

    if event_row.get("all_day"):
        start_d = date_cls.fromisoformat(str(event_row["start_datetime"])[:10])
        end_d = date_cls.fromisoformat(str(event_row["end_datetime"])[:10])
        vevent.add("dtstart", start_d)
        vevent.add("dtend", end_d)
    else:
        start_s = str(event_row["start_datetime"])
        end_s = str(event_row["end_datetime"])
        if len(start_s) == 16:
            start_s += ":00"
        if len(end_s) == 16:
            end_s += ":00"
        vevent.add("dtstart", dt_cls.fromisoformat(start_s.replace("Z", "")))
        vevent.add("dtend", dt_cls.fromisoformat(end_s.replace("Z", "")))

    cal.add_component(vevent)
    return cal.to_ical()


def create_caldav_event(apple_id: str, app_password: str, calendar_url: str, event_row: dict) -> str:
    """Create a CalDAV event; returns the UID used as the remote id."""
    import uuid
    from caldav import DAVClient

    uid = str(uuid.uuid4())
    ical = _vevent_ical(event_row, uid)
    client = DAVClient(url=ICLOUD_CALDAV_URL, username=apple_id, password=app_password, timeout=20)
    cal = client.calendar(url=calendar_url)
    cal.save_event(ical)
    return uid


def update_caldav_event(
    apple_id: str, app_password: str, calendar_url: str, uid: str, event_row: dict
) -> bool:
    """Update an existing CalDAV event by UID. Returns True on success."""
    from caldav import DAVClient

    client = DAVClient(url=ICLOUD_CALDAV_URL, username=apple_id, password=app_password, timeout=20)
    cal = client.calendar(url=calendar_url)
    try:
        existing = cal.event_by_uid(uid)
    except Exception:
        try:
            results = cal.search(event=True, uid=uid)
            existing = results[0] if results else None
        except Exception:
            existing = None
    if not existing:
        return False
    existing.data = _vevent_ical(event_row, uid)
    existing.save()
    return True


def delete_caldav_event(apple_id: str, app_password: str, calendar_url: str, uid: str) -> bool:
    """Delete a CalDAV event by UID. Returns True if deleted."""
    from caldav import DAVClient

    client = DAVClient(url=ICLOUD_CALDAV_URL, username=apple_id, password=app_password, timeout=20)
    cal = client.calendar(url=calendar_url)
    try:
        existing = cal.event_by_uid(uid)
    except Exception:
        try:
            results = cal.search(event=True, uid=uid)
            existing = results[0] if results else None
        except Exception:
            existing = None
    if not existing:
        return False
    existing.delete()
    return True
