"""SchoolCafe public menu client.

Weekly menus are a dict keyed by M/D/YYYY, then category, then item list.
Serving lines come from GetServiceLine (the schools list leaves them blank).
"""
import json
import time
from datetime import datetime, timedelta
from urllib.parse import quote

import httpx

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from database import get_db

BASE = "https://webapis.schoolcafe.com/api"
HEADERS = {"Accept": "application/json", "User-Agent": "FamilyHub/1.0"}
_CACHE = {}
_TTL = 6 * 3600


def _cache_get(key):
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, value = hit
    if time.monotonic() - ts > _TTL:
        return None
    return value


def _cache_set(key, value):
    _CACHE[key] = (time.monotonic(), value)
    return value


def app_today():
    import pytz
    tz_name = "America/New_York"
    try:
        conn = get_db()
        row = conn.execute("SELECT value FROM settings WHERE key = 'timezone'").fetchone()
        conn.close()
        if row and row["value"]:
            tz_name = row["value"]
    except Exception:
        pass
    now = datetime.now(pytz.timezone(tz_name))
    return now.strftime("%Y-%m-%d"), now.hour


def setting(key, default=""):
    try:
        conn = get_db()
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        conn.close()
        if row and row["value"] is not None:
            return row["value"]
    except Exception:
        pass
    return default


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
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('schoolcafe_lines', ?)",
        (json.dumps(data),),
    )
    conn.commit()
    conn.close()
    return current


def monday_of(iso_date: str) -> str:
    day = datetime.strptime(iso_date, "%Y-%m-%d").date()
    monday = day - timedelta(days=day.weekday())
    return monday.isoformat()


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
            if not name:
                continue
            marker = name.lower()
            if marker in seen:
                continue
            seen.add(marker)
            items.append({"name": name, "category": category, "is_entree": entree})
    return items


async def _get_json(client: httpx.AsyncClient, path: str, params=None):
    url = path if path.startswith("http") else f"{BASE}/{path.lstrip('/')}"
    resp = await client.get(url, params=params, headers=HEADERS)
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except Exception:
        return None


async def search_districts(query: str):
    q = (query or "").strip()
    if len(q) < 2:
        return []
    async with httpx.AsyncClient(timeout=15.0) as client:
        data = await _get_json(client, f"GetISDBySearch/{quote(q, safe='')}/1")
    if not isinstance(data, list):
        return []
    districts = []
    for row in data:
        if not isinstance(row, dict) or not row.get("ISDId"):
            continue
        districts.append({
            "id": row["ISDId"],
            "name": row.get("ISDName") or "",
            "short_name": row.get("ShortName") or "",
            "published": bool(row.get("IsMenuPuclished")),
        })
    districts.sort(key=lambda d: (not d["published"], d["name"].lower()))
    return districts


async def list_schools(district_id: str):
    cache_key = f"schools:{district_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    async with httpx.AsyncClient(timeout=20.0) as client:
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
        schools.append({
            "id": sid,
            "name": name,
            "type": row.get("SiteTypeDescription") or "",
        })
    schools.sort(key=lambda s: s["name"].lower())
    return _cache_set(cache_key, schools)


async def serving_lines(school_id: str, meal_type: str, iso_date: str):
    monday = monday_of(iso_date)
    sunday = (datetime.strptime(monday, "%Y-%m-%d").date() + timedelta(days=6)).isoformat()
    cache_key = f"lines:{school_id}:{meal_type}:{monday}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    params = {
        "schoolid": school_id,
        "startdate": _mdy(monday),
        "enddate": _mdy(sunday),
        "mealtype": meal_type,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        data = await _get_json(client, "GetServiceLine", params)
    lines = []
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            name = (row.get("ServingLineDescription") or "").strip()
            if name and name not in lines:
                lines.append(name)
    return _cache_set(cache_key, lines)


async def weekly_menu(school_id: str, meal_type: str, serving_line: str, iso_date: str):
    monday = monday_of(iso_date)
    cache_key = f"week:{school_id}:{meal_type}:{serving_line}:{monday}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    params = {
        "SchoolId": school_id,
        "ServingDate": _mdy(monday),
        "ServingLine": serving_line,
        "MealType": meal_type,
        "enabledWeekendMenus": "true",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        data = await _get_json(client, "CalendarView/GetWeeklyMenuitems", params)
    if not isinstance(data, dict):
        data = {}
    return _cache_set(cache_key, data)


async def menu_items(school_id: str, meal_type: str, iso_date: str, preferred_line: str = ""):
    lines = await serving_lines(school_id, meal_type, iso_date)
    if not lines:
        return [], ""
    line = preferred_line if preferred_line in lines else lines[0]
    payload = await weekly_menu(school_id, meal_type, line, iso_date)
    return items_for_date(payload, iso_date), line
