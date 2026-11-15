"""SchoolCafe public menu client backed by PostgreSQL integration_snapshots.

User-facing menu GETs read strictly from local snapshots in <3ms.
Network polling runs in worker.py via refresh_schoolcafe_snapshots().
"""
import asyncio
import json
import time
from datetime import datetime, timedelta
from urllib.parse import quote

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from database import (
    get_db,
    get_setting_value,
    get_snapshot_async,
    invalidate_settings_cache,
    notify_worker,
    notify_worker_async,
    set_snapshot_async,
    upsert_setting_sync,
)
import outbound

BASE = "https://webapis.schoolcafe.com/api"
HEADERS = {"Accept": "application/json", "User-Agent": "FamilyHub/1.0"}
_SETUP_CACHE: dict[str, tuple[float, object]] = {}
_SETUP_TTL = 6 * 3600
_WEEK_FRESH_SECONDS = 4 * 3600


class SchoolCafeError(Exception):
    pass


def _client(timeout: float = 8.0):
    return outbound.client(timeout=timeout, headers=HEADERS)


def app_today():
    import pytz
    tz_name = get_setting_value("timezone", "America/New_York") or "America/New_York"
    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        tz = pytz.timezone("America/New_York")
    now = datetime.now(tz)
    return now.strftime("%Y-%m-%d"), now.hour


def setting(key, default=""):
    return get_setting_value(key, default)


def line_overrides():
    raw = setting("schoolcafe_lines", "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_line_override(school_id, breakfast, lunch):
    data = line_overrides()
    current = data.get(school_id) or {}
    if breakfast:
        current["Breakfast"] = breakfast
    if lunch:
        current["Lunch"] = lunch
    data[school_id] = current
    with get_db() as conn:
        upsert_setting_sync(conn, "schoolcafe_lines", json.dumps(data))
        conn.commit()
    invalidate_settings_cache(broadcast=True)
    notify_worker("refresh_meals")
    return current


def monday_of(iso_date: str) -> str:
    day = datetime.strptime(iso_date, "%Y-%m-%d").date()
    monday = day - timedelta(days=day.weekday())
    return monday.isoformat()


def next_school_day(iso_date: str) -> str:
    """Saturday and Sunday have no published menu. Use the following Monday."""
    day = datetime.strptime(iso_date, "%Y-%m-%d").date()
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.isoformat()


def _mdy(iso_date: str) -> str:
    y, m, d = iso_date.split("-")
    return f"{int(m):02d}/{int(d):02d}/{y}"


def _date_keys(iso_date: str):
    y, m, d = iso_date.split("-")
    mi, di = int(m), int(d)
    return {iso_date, f"{mi}/{di}/{y}", f"{m}/{d}/{y}"}


def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if " / " in name:
        name = name.split(" / ", 1)[0].strip()
    return " ".join(name.split())


def items_for_date(payload, iso_date: str):
    if not isinstance(payload, dict):
        return []
    wanted = _date_keys(iso_date)
    day = None
    for key, value in payload.items():
        if key in wanted:
            day = value
            break
    if not isinstance(day, dict):
        return []

    def sort_key(category):
        label = (category or "").lower()
        if "entree" in label or "entrée" in label:
            return (0, label)
        return (1, label)

    seen = set()
    items = []
    for category in sorted(day.keys(), key=sort_key):
        rows = day.get(category)
        if not isinstance(rows, list):
            continue
        entree = "entree" in category.lower() or "entrée" in category.lower()
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = _clean_name(row.get("MenuItemDescription") or "")
            if not name or "has not been published" in name.lower():
                continue
            marker = name.lower()
            if marker in seen:
                continue
            seen.add(marker)
            items.append({"name": name, "category": category, "is_entree": entree})
    return items


async def _get_json(client, path: str, params=None):
    url = path if path.startswith("http") else f"{BASE}/{path.lstrip('/')}"
    try:
        resp = await client.get(url, params=params)
    except Exception as exc:
        print(f"SchoolCafe request failed: {url}: {exc}")
        raise SchoolCafeError(str(exc)) from exc
    if resp.status_code != 200:
        print(f"SchoolCafe HTTP {resp.status_code} for {url}")
        raise SchoolCafeError(f"HTTP {resp.status_code}")
    try:
        return resp.json()
    except Exception as exc:
        print(f"SchoolCafe returned invalid JSON for {url}")
        raise SchoolCafeError("invalid JSON") from exc


# ── Interactive Settings Lookup Helpers (used only in Settings UI) ───────────

async def search_districts(query: str):
    q = (query or "").strip()
    if len(q) < 2:
        return []
    async with _client() as client:
        data = await _get_json(client, f"GetISDBySearch/{quote(q, safe='')}/1")
    if not isinstance(data, list):
        return []
    districts = []
    for row in data:
        if not isinstance(row, dict) or not row.get("ISDId"):
            continue
        districts.append(
            {
                "id": row["ISDId"],
                "name": row.get("ISDName") or "",
                "short_name": row.get("ShortName") or "",
                "published": bool(row.get("IsMenuPuclished")),
            }
        )
    districts.sort(key=lambda d: (not d["published"], d["name"].lower()))
    return districts


async def list_schools(district_id: str):
    cache_key = f"schools:{district_id}"
    hit = _SETUP_CACHE.get(cache_key)
    if hit and time.monotonic() - hit[0] < _SETUP_TTL:
        return hit[1]
    async with _client() as client:
        data = await _get_json(client, "GetSchoolsList", {"districtId": district_id})
    if not isinstance(data, list):
        return None
    schools = []
    seen = set()
    for row in data:
        if not isinstance(row, dict):
            continue
        sid = row.get("SchoolId")
        name = (row.get("SchoolName") or "").strip()
        if not sid or sid in seen or not name:
            continue
        seen.add(sid)
        schools.append(
            {
                "id": sid,
                "name": name,
                "type": row.get("SiteTypeDescription") or "",
            }
        )
    schools.sort(key=lambda s: s["name"].lower())
    _SETUP_CACHE[cache_key] = (time.monotonic(), schools)
    return schools


async def serving_lines(school_id: str, meal_type: str, iso_date: str):
    monday = monday_of(iso_date)
    sunday = (datetime.strptime(monday, "%Y-%m-%d").date() + timedelta(days=6)).isoformat()
    cache_key = f"lines:{school_id}:{meal_type}:{monday}"
    hit = _SETUP_CACHE.get(cache_key)
    if hit and time.monotonic() - hit[0] < _SETUP_TTL:
        return hit[1]
    params = {
        "schoolid": school_id,
        "startdate": _mdy(monday),
        "enddate": _mdy(sunday),
        "mealtype": meal_type,
    }
    async with _client() as client:
        data = await _get_json(client, "GetServiceLine", params)
    lines = []
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            name = (row.get("ServingLineDescription") or "").strip()
            if name and name not in lines:
                lines.append(name)
    if lines:
        _SETUP_CACHE[cache_key] = (time.monotonic(), lines)
    return lines


async def fetch_week_live(school_id: str, meal_type: str, iso_date: str, preferred_line: str = "") -> tuple[dict, str]:
    """Perform live upstream SchoolCafe fetch (called by worker.py)."""
    monday = monday_of(iso_date)
    lines = await serving_lines(school_id, meal_type, iso_date)
    if not lines:
        return {}, ""
    line = preferred_line if preferred_line in lines else lines[0]
    params = {
        "SchoolId": school_id,
        "ServingDate": _mdy(monday),
        "ServingLine": line,
        "MealType": meal_type,
        "enabledWeekendMenus": "true",
    }
    async with _client() as client:
        data = await _get_json(client, "CalendarView/GetWeeklyMenuitems", params)
    payload = data if isinstance(data, dict) else {}
    return payload, line


# ── Local-First Snapshot Read Path (called by GET /api/meals/*) ──────────────

def _snap_key(school_id: str, meal_type: str, monday: str, preferred_line: str) -> str:
    return f"schoolcafe:{school_id}:{meal_type}:{monday}:{preferred_line or 'default'}"


async def menu_week(school_id: str, meal_type: str, iso_date: str, preferred_line: str = "") -> tuple[dict, str, bool]:
    """Read cached weekly SchoolCafe menu from PostgreSQL snapshot in <3ms.

    Returns (payload_dict, line_name, warming_bool). Never makes outbound HTTP calls.
    """
    monday = monday_of(iso_date)
    key = _snap_key(school_id, meal_type, monday, preferred_line)
    snap = await get_snapshot_async(key)
    if snap is None:
        await notify_worker_async(f"refresh_meals_week:{school_id}:{monday}")
        return {}, "", True

    data, updated_at, error = snap
    if time.time() - updated_at > _WEEK_FRESH_SECONDS:
        await notify_worker_async(f"refresh_meals_week:{school_id}:{monday}")

    if not isinstance(data, dict):
        return {}, "", bool(error)
    return data.get("payload") or {}, data.get("line") or "", False


async def menu_items(school_id: str, meal_type: str, iso_date: str, preferred_line: str = "") -> tuple[list, str, bool]:
    payload, line, warming = await menu_week(school_id, meal_type, iso_date, preferred_line)
    return items_for_date(payload, iso_date), line, warming


# ── Worker Poller Entry Point ────────────────────────────────────────────────

def _configured_schools_sync() -> list[str]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT school_id FROM members WHERE school_id IS NOT NULL AND school_id != ''"
        ).fetchall()
    return [r["school_id"] for r in rows]


async def refresh_school_week_snapshot(school_id: str, anchor_iso: str):
    monday = monday_of(anchor_iso)
    overrides = line_overrides().get(school_id) or {}
    for meal_type in ("Breakfast", "Lunch"):
        pref = overrides.get(meal_type) or ""
        key = _snap_key(school_id, meal_type, monday, pref)
        try:
            payload, line = await fetch_week_live(school_id, meal_type, monday, pref)
            await set_snapshot_async(key, {"payload": payload, "line": line}, error=None)
        except Exception as exc:
            existing = await get_snapshot_async(key)
            prev_payload = existing[0] if existing else {"payload": {}, "line": ""}
            await set_snapshot_async(key, prev_payload, error=str(exc))


async def refresh_schoolcafe_snapshots(extra_week_spec: str | None = None):
    """Refresh current + next week menus for every member's assigned school."""
    district_id = (setting("schoolcafe_district_id") or "").strip()
    if not district_id or setting("lunch_hidden", "false") == "true":
        return

    if extra_week_spec:
        # Format: "<school_id>:<monday_iso>"
        parts = extra_week_spec.split(":", 1)
        if len(parts) == 2:
            await refresh_school_week_snapshot(parts[0], parts[1])
            return

    schools = await asyncio.to_thread(_configured_schools_sync)
    if not schools:
        return

    today_iso, _ = app_today()
    cur_monday = monday_of(next_school_day(today_iso))
    next_monday = (datetime.strptime(cur_monday, "%Y-%m-%d").date() + timedelta(days=7)).isoformat()

    for sid in schools:
        await refresh_school_week_snapshot(sid, cur_monday)
        await refresh_school_week_snapshot(sid, next_monday)
