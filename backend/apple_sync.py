"""Apple Calendar sync helpers (ICS/webcal feeds + iCloud CalDAV)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import asyncio

from icalendar import Calendar

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
    # icalendar sometimes returns strings
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

    # All-day iCal DTEND is exclusive (same as Google). Normalize so single-day
    # events use start+1 day, matching Google sync + frontend display logic.
    if all_day:
        try:
            start_d = date.fromisoformat(str(start)[:10])
            if end:
                end_d = date.fromisoformat(str(end)[:10])
            else:
                end_d = start_d
            # Missing or inclusive-looking end → exclusive next day
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
                # Recurring expansions share UID — disambiguate with occurrence start
                fields["uid"] = f"{fields['uid']}@{fields['start_datetime']}"
                events.append(fields)
        return events

    for component in cal.walk():
        fields = _event_fields_from_component(component, color)
        if fields:
            events.append(fields)
    return events


async def fetch_ics_events(url: str, color: Optional[str] = None) -> list[dict]:
    url = normalize_ics_url(url)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("ICS URL must be http(s) or webcal")

    async with outbound.client(timeout=60.0, headers={"User-Agent": "FamilyHub/1.0"}) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            raise ValueError(f"ICS fetch failed ({resp.status_code})")
        text = resp.text

    return await asyncio.to_thread(_events_from_ics, text, color)


def list_icloud_calendars(apple_id: str, app_password: str) -> list[dict]:
    from caldav import DAVClient

    client = DAVClient(url=ICLOUD_CALDAV_URL, username=apple_id, password=app_password)
    principal = client.principal()
    calendars = []
    for cal in principal.calendars():
        name = None
        try:
            name = cal.get_display_name()
        except Exception:
            pass
        url = str(cal.url)
        calendars.append({
            "id": url,
            "url": url,
            "name": name or url.rstrip("/").split("/")[-1] or "Calendar",
        })
    return calendars


def fetch_caldav_events(apple_id: str, app_password: str, calendar_url: str, color: Optional[str] = None) -> list[dict]:
    from caldav import DAVClient

    client = DAVClient(url=ICLOUD_CALDAV_URL, username=apple_id, password=app_password, timeout=20)
    # Re-bind to the specific calendar URL
    cal = client.calendar(url=calendar_url)
    start, end = sync_window()
    events: list[dict] = []
    try:
        results = cal.search(start=start, end=end, event=True, expand=True)
    except TypeError:
        # Older caldav API
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
                # Keep occurrence-stable id when expand gives same UID
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
    """Replace synced Apple/ICS inbound events for a source with the fresh set.

    Preserves locally-written events (google_calendar_id set) on the same source.
    """
    prefix = f"apple:{source_id}:"
    conn.execute(
        """DELETE FROM events
           WHERE external_source_id=? AND google_calendar_id IS NULL""",
        (source_id,),
    )
    # Legacy rows keyed only by the apple: prefix
    if member_id is not None:
        conn.execute(
            """DELETE FROM events
               WHERE member_id=? AND external_event_id LIKE ? AND google_calendar_id IS NULL
                 AND (external_source_id IS NULL OR external_source_id=?)""",
            (member_id, prefix + "%", source_id),
        )
    family_flag = 1 if is_family else 0
    synced = 0
    for ev in events:
        ext_id = f"{prefix}{ev['uid']}"
        conn.execute(
            """INSERT INTO events
               (title, description, location, start_datetime, end_datetime, all_day,
                member_id, is_family, color, external_event_id, external_source_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ev["title"],
                ev.get("description") or "",
                ev.get("location") or "",
                ev["start_datetime"],
                ev["end_datetime"],
                ev.get("all_day") or 0,
                member_id,
                family_flag,
                ev.get("color"),
                ext_id,
                source_id,
            ),
        )
        synced += 1
    return synced


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
    client = DAVClient(url=ICLOUD_CALDAV_URL, username=apple_id, password=app_password)
    cal = client.calendar(url=calendar_url)
    cal.save_event(ical)
    return uid


def update_caldav_event(
    apple_id: str, app_password: str, calendar_url: str, uid: str, event_row: dict
) -> bool:
    """Update an existing CalDAV event by UID. Returns True on success."""
    from caldav import DAVClient

    client = DAVClient(url=ICLOUD_CALDAV_URL, username=apple_id, password=app_password)
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

    client = DAVClient(url=ICLOUD_CALDAV_URL, username=apple_id, password=app_password)
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
