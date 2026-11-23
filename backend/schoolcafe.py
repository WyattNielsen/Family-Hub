"""SchoolCafe public menu client.

Weekly menus are a dict keyed by M/D/YYYY, then category, then item list.
Serving lines come from GetServiceLine (the schools list leaves them blank).

Fetched weeks are stored in school_menus. Meal pages read that table.
SchoolCafe itself runs only from the school-menus background job and from
the settings screens that search districts, schools, and serving lines.
"""
import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from database import get_db, get_setting_value, invalidate_settings_cache
import outbound

BASE = "https://webapis.schoolcafe.com/api"
HEADERS = {"Accept": "application/json", "User-Agent": "FamilyHub/1.0"}

# Weeks a reader asked for that the current job has not picked up yet.
_plan_lock = threading.Lock()
_pending_weeks: set[str] = set()
_again = False


class SchoolCafeError(Exception):
    pass


def _client(timeout: float = 6.0):
    # Shares the process-wide client (IPv4, no env proxy). Headers stay on
    # this call so other APIs are not forced to SchoolCafe's Accept.
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


def school_menu_date(dinner_iso: str, hour: int) -> str:
    """School menu to show for a dinner date.

    After 17:00 the next calendar day is used. Saturday and Sunday have no
    published menu, so those roll forward to Monday.
    """
    day = datetime.strptime(dinner_iso, "%Y-%m-%d").date()
    if hour >= 17:
        day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.isoformat()


def setting(key, default=""):
    return get_setting_value(key, default)


def _lunch_hidden() -> bool:
    return (setting("lunch_hidden", "false") or "").strip().lower() == "true"


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
    try:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('schoolcafe_lines', ?)",
            (json.dumps(data),),
        )
        conn.commit()
        invalidate_settings_cache()
        return current
    finally:
        conn.close()


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


def _shift(iso_date: str, days: int) -> str:
    day = datetime.strptime(iso_date, "%Y-%m-%d").date() + timedelta(days=days)
    return day.isoformat()


def _default_weeks() -> set[str]:
    today, _hour = app_today()
    weeks = {monday_of(today)}
    day = datetime.strptime(today, "%Y-%m-%d").date()
    if day.weekday() == 4:
        weeks.add(monday_of(_shift(today, 7)))
    return weeks


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
        districts.append({
            "id": row["ISDId"],
            "name": row.get("ISDName") or "",
            "short_name": row.get("ShortName") or "",
            "published": bool(row.get("IsMenuPuclished")),
        })
    districts.sort(key=lambda d: (not d["published"], d["name"].lower()))
    return districts


def menu_page_url(short_name: str) -> str:
    """Public SchoolCafe menus page for a district. ShortName is the path segment."""
    short = (short_name or "").strip().strip("/")
    if not short:
        return ""
    return f"https://www.schoolcafe.com/{quote(short, safe='')}/menus"


def _remember_short_name(district_id: str, short_name: str):
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('schoolcafe_district_short', ?)",
            (short_name,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('schoolcafe_district_short_for', ?)",
            (district_id,),
        )
        conn.commit()
        invalidate_settings_cache()
    finally:
        conn.close()


def _district_search_query(name: str) -> str:
    name = (name or "").strip()
    # Stored names look like "VA - CHESTERFIELD COUNTY PUBLIC SCHOOLS".
    # The search API does not match that full string.
    if len(name) > 5 and name[2:5] == " - " and name[:2].isalpha():
        name = name[5:].strip()
    return name


async def district_short_name() -> str:
    """Path segment for the district's public menus page."""
    district_id = (setting("schoolcafe_district_id") or "").strip()
    if not district_id:
        return ""
    cached = (setting("schoolcafe_district_short") or "").strip()
    cached_for = (setting("schoolcafe_district_short_for") or "").strip()
    if cached and cached_for == district_id:
        return cached
    query = _district_search_query(setting("schoolcafe_district_name") or "")
    if len(query) < 2:
        return ""
    try:
        rows = await search_districts(query)
    except Exception:
        return ""
    match = next((row for row in rows if str(row.get("id")) == district_id), None)
    short = ((match or {}).get("short_name") or "").strip()
    if short:
        await asyncio.to_thread(_remember_short_name, district_id, short)
    return short


async def list_schools(district_id: str):
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
        schools.append({
            "id": sid,
            "name": name,
            "type": row.get("SiteTypeDescription") or "",
        })
    schools.sort(key=lambda s: s["name"].lower())
    return schools


async def serving_lines(school_id: str, meal_type: str, iso_date: str):
    monday = monday_of(iso_date)
    sunday = (datetime.strptime(monday, "%Y-%m-%d").date() + timedelta(days=6)).isoformat()
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
    return lines


async def weekly_menu(school_id: str, meal_type: str, serving_line: str, iso_date: str):
    monday = monday_of(iso_date)
    params = {
        "SchoolId": school_id,
        "ServingDate": _mdy(monday),
        "ServingLine": serving_line,
        "MealType": meal_type,
        "enabledWeekendMenus": "true",
    }
    async with _client() as client:
        data = await _get_json(client, "CalendarView/GetWeeklyMenuitems", params)
    if not isinstance(data, dict) or not data:
        return {}
    return data


async def menu_week(school_id: str, meal_type: str, iso_date: str, preferred_line: str = ""):
    """Whole SchoolCafe week for one meal, keyed the way the API returns it."""
    lines = await serving_lines(school_id, meal_type, iso_date)
    if not lines:
        return {}, ""
    line = preferred_line if preferred_line in lines else lines[0]
    payload = await weekly_menu(school_id, meal_type, line, iso_date)
    return payload if isinstance(payload, dict) else {}, line


async def menu_items(school_id: str, meal_type: str, iso_date: str, preferred_line: str = ""):
    payload, line = await menu_week(school_id, meal_type, iso_date, preferred_line)
    return items_for_date(payload, iso_date), line


def _member_schools() -> list[str]:
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT DISTINCT school_id FROM members
               WHERE school_id IS NOT NULL AND school_id != ''"""
        ).fetchall()
        return [r["school_id"] for r in rows]
    finally:
        conn.close()


def _upsert_menu(school_id: str, meal_type: str, serving_line: str, week_start: str, days: dict):
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO school_menus
                (school_id, meal_type, serving_line, week_start, menu_json, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(school_id, meal_type, serving_line, week_start) DO UPDATE SET
                menu_json = excluded.menu_json,
                fetched_at = excluded.fetched_at
            """,
            (school_id, meal_type, serving_line, week_start, json.dumps(days), fetched_at),
        )
        conn.commit()
    finally:
        conn.close()


def load_week_maps(school_ids: list, week_starts: list) -> dict:
    """Stored item lists keyed by (school_id, meal, serving_line, week_start).

    Each value is (fetched_at, {iso_date: [item, ...]}). SQLite only.
    """
    ids = [s for s in school_ids if s]
    weeks = [w for w in week_starts if w]
    if not ids or not weeks:
        return {}
    conn = get_db()
    try:
        query = f"""
            SELECT school_id, meal_type, serving_line, week_start, menu_json, fetched_at
            FROM school_menus
            WHERE school_id IN ({",".join("?" * len(ids))})
              AND week_start IN ({",".join("?" * len(weeks))})
        """
        rows = conn.execute(query, [*ids, *weeks]).fetchall()
    finally:
        conn.close()
    out = {}
    for row in rows:
        try:
            days = json.loads(row["menu_json"])
        except Exception:
            continue
        if not isinstance(days, dict):
            continue
        out[(row["school_id"], row["meal_type"], row["serving_line"], row["week_start"])] = (
            row["fetched_at"] or "",
            days,
        )
    return out


def pick_days(maps: dict, school_id: str, meal: str, week: str, preferred: str):
    """Return (days or None, needs_refresh).

    days is None when this school has nothing stored for the week. A preferred
    line that is not the stored line still returns the stored copy and asks
    for a refresh, so the page can paint while SchoolCafe runs in the background.
    """
    exact = None
    if preferred:
        hit = maps.get((school_id, meal, preferred, week))
        if hit:
            exact = hit[1]
    best = None
    best_at = ""
    for (sid, meal_type, _line, wk), (fetched, days) in maps.items():
        if sid == school_id and meal_type == meal and wk == week and fetched >= best_at:
            best_at = fetched
            best = days
    if preferred:
        if exact is not None:
            return exact, False
        if best is not None:
            return best, True
        return None, True
    if best is not None:
        return best, False
    return None, True


def _take_plan():
    """Work list for one refresh pass, or None when school menus are hidden.

    Opens its own connections and returns before any HTTP call.
    """
    global _again
    if _lunch_hidden():
        return None
    with _plan_lock:
        extra = set(_pending_weeks)
        _pending_weeks.clear()
        _again = False
    weeks = set(extra)
    weeks.update(_default_weeks())
    schools = _member_schools()
    overrides = line_overrides()
    work = []
    for school_id in schools:
        prefs = overrides.get(school_id) or {}
        if not isinstance(prefs, dict):
            prefs = {}
        for week in sorted(weeks):
            for meal in ("Breakfast", "Lunch"):
                preferred = prefs.get(meal) or ""
                if not isinstance(preferred, str):
                    preferred = str(preferred)
                work.append((school_id, meal, week, preferred.strip()))
    return work


async def _store_week(school_id: str, meal: str, week: str, preferred: str):
    payload, line = await menu_week(school_id, meal, week, preferred)
    if not line:
        line = preferred or ""
    days = {}
    for offset in range(7):
        iso = _shift(week, offset)
        days[iso] = items_for_date(payload, iso)
    await asyncio.to_thread(_upsert_menu, school_id, meal, line, week, days)
    if preferred and preferred != line:
        # The settings line is not one SchoolCafe published. Keep a row under
        # the name the read path looks up so the next request is a hit.
        await asyncio.to_thread(_upsert_menu, school_id, meal, preferred, week, days)


async def refresh_school_menus():
    """Fetch current-week menus (and next week on Friday) into SQLite.

    Does nothing when lunch_hidden is true. A kick that arrives mid-run
    causes one more pass so a line change is not dropped.
    """
    while True:
        plan = await asyncio.to_thread(_take_plan)
        if plan is None:
            return
        for school_id, meal, week, preferred in plan:
            try:
                await _store_week(school_id, meal, week, preferred)
            except Exception as e:
                print(f"School menu refresh failed for {school_id} {meal} {week}: {e}")
        with _plan_lock:
            if not _again and not _pending_weeks:
                return


async def kick_school_menu_refresh(weeks=None):
    """Start the shared school-menus job. Returns as soon as it is scheduled.

    A kick that lands as the running job is deciding to exit is started
    again, so a serving-line change is not dropped on the floor.
    """
    global _again
    with _plan_lock:
        if weeks:
            _pending_weeks.update(w for w in weeks if w)
        _again = True
    import jobs
    started = await jobs.run_background("school-menus", lambda: refresh_school_menus())
    if started.get("started"):
        return started
    await asyncio.sleep(0)
    with _plan_lock:
        pending = _again or bool(_pending_weeks)
    if not pending:
        return started
    return await jobs.run_background("school-menus", lambda: refresh_school_menus())
