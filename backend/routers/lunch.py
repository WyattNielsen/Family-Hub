from fastapi import APIRouter
from datetime import datetime, timedelta
import pytz
import time
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import outbound
from database import (
    get_settings_map,
    get_snapshot_async,
    notify_worker_async,
    set_snapshot_async,
)

router = APIRouter()
_LUNCH_TTL = 4 * 3600


def get_lunch_config():
    s = get_settings_map()
    district = s.get("lunch_district") or "bcps"
    slug = s.get("lunch_school_slug") or "bcps-weekly-menus"
    menu_type = s.get("lunch_menu_type") or "weekly-menus"
    return district, slug, menu_type


def get_app_tz() -> str:
    return get_settings_map().get("timezone") or "America/New_York"


def get_today_local():
    try:
        tz = pytz.timezone(get_app_tz())
    except Exception:
        tz = pytz.timezone("America/New_York")
    now = datetime.now(tz)
    return now.strftime("%Y/%m/%d"), now.strftime("%Y-%m-%d")


async def fetch_items_for_date_live(date_key: str):
    """Live Nutrislice fetch (called by worker.py)."""
    district, slug, menu_type = get_lunch_config()
    date_path = date_key.replace("-", "/")
    url = f"https://{district}.api.nutrislice.com/menu/api/weeks/school/{slug}/menu-type/{menu_type}/{date_path}/"
    try:
        async with outbound.client(timeout=10.0) as client:
            resp = await client.get(url, headers={"Accept": "application/json"})
            if resp.status_code != 200:
                return None
            data = resp.json()
    except Exception as e:
        print(f"Nutrislice fetch error: {e}")
        return None

    today_day = None
    for day in data.get("days", []):
        if day.get("date", "")[:10] == date_key:
            today_day = day
            break

    if not today_day:
        return {"date": date_key, "breakfast": [], "lunch": [], "sides": []}

    entree_skip = {
        "100% fruit juice", "assorted fresh fruits", "fresh fruits",
        "1% white milk", "white milk", "chocolate milk", "skim milk",
        "fat free chocolate milk",
    }

    def is_beverage(name: str) -> bool:
        n = name.lower()
        return "milk" in n or "juice" in n or "punch" in n or "drink" in n

    breakfast, lunch, sides = [], [], []
    current_section = None

    for mi in today_day.get("menu_items", []):
        image = mi.get("image") or ""
        if mi.get("food") is None:
            if image:
                parts = image.rstrip("/").split("/")
                asset_id = parts[-1] if parts[-1].isdigit() else (parts[-2] if len(parts) > 1 else "")
                if asset_id == "277390":
                    current_section = "breakfast"
                elif asset_id == "277391":
                    current_section = "lunch"
            continue

        food = mi.get("food")
        if not food:
            continue

        name = (food.get("name") or "").strip()
        if not name:
            continue

        featured = mi.get("featured", False)
        if featured:
            if name.lower() in entree_skip:
                continue
            item = {"name": name, "is_entree": True}
            if current_section == "breakfast":
                breakfast.append(item)
            elif current_section == "lunch":
                lunch.append(item)
        elif current_section == "lunch" and not is_beverage(name):
            sides.append({"name": name, "is_entree": False})

    return {"date": date_key, "breakfast": breakfast, "lunch": lunch, "sides": sides}


async def refresh_lunch_snapshot():
    s = get_settings_map()
    if s.get("lunch_hidden", "false") == "true":
        return
    district, slug, menu_type = get_lunch_config()
    _, today_key = get_today_local()
    tomorrow_key = (datetime.strptime(today_key, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
    for dkey in (today_key, tomorrow_key):
        snap_key = f"nutrislice:{district}:{slug}:{menu_type}:{dkey}"
        result = await fetch_items_for_date_live(dkey)
        if result is not None:
            await set_snapshot_async(snap_key, result, error=None)


@router.get("/today")
async def get_todays_menu(date: str = None):
    district, slug, menu_type = get_lunch_config()
    if not district:
        return {"not_configured": True, "breakfast": [], "lunch": []}
    if not date:
        _, date = get_today_local()
    snap_key = f"nutrislice:{district}:{slug}:{menu_type}:{date}"
    snap = await get_snapshot_async(snap_key)
    if snap is None:
        await notify_worker_async("refresh_meals")
        return {
            "date": date,
            "breakfast": [],
            "lunch": [],
            "sides": [],
            "warming": True,
            "district": district,
            "school_slug": slug,
            "menu_type": menu_type,
        }
    payload, updated_at, _err = snap
    if time.time() - updated_at > _LUNCH_TTL:
        await notify_worker_async("refresh_meals")
    result = dict(payload) if isinstance(payload, dict) else {"date": date, "breakfast": [], "lunch": []}
    result["district"] = district
    result["school_slug"] = slug
    result["menu_type"] = menu_type
    return result


@router.get("/debug")
async def debug_menu():
    district, slug, menu_type = get_lunch_config()
    date_path, date_key = get_today_local()
    url = f"https://{district}.api.nutrislice.com/menu/api/weeks/school/{slug}/menu-type/{menu_type}/{date_path}/"
    async with outbound.client(timeout=10.0) as client:
        resp = await client.get(url, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            return {"error": resp.status_code}
        data = resp.json()

    today_day = next((d for d in data.get("days", []) if d.get("date", "")[:10] == date_key), None)
    if not today_day:
        return {"error": "no day", "date": date_key}

    simplified = []
    for i, mi in enumerate(today_day.get("menu_items", [])):
        image = mi.get("image") or ""
        food = mi.get("food")
        simplified.append(
            {
                "pos": i,
                "image": image[-30:] if image else None,
                "food": food.get("name") if food else None,
                "featured": mi.get("featured", False),
            }
        )
    return {"date": date_key, "items": simplified}
