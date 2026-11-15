import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi import APIRouter, HTTPException
from database import (
    get_db,
    get_settings_map,
    get_settings_map_async,
    invalidate_settings_cache,
    notify_worker,
    refresh_settings_cache_async,
    upsert_setting_sync,
)

router = APIRouter()

# Secrets the client may replace, but must never read back.
WRITE_ONLY_KEYS = frozenset({"ha_token", "ha_alarm_code"})
# Older clients echoed this mask. Never store it, and never treat it as a new secret.
MASK_PLACEHOLDER = "••••••••"


def _is_set(value) -> bool:
    text = "" if value is None else str(value).strip()
    return bool(text) and text != MASK_PLACEHOLDER


def _write_only_flags(stored: dict) -> dict:
    return {f"{key}_set": _is_set(stored.get(key)) for key in WRITE_ONLY_KEYS}


def sanitize_settings(stored: dict) -> dict:
    out = {
        key: value
        for key, value in stored.items()
        if key not in WRITE_ONLY_KEYS and not key.startswith("_")
    }
    out.update(_write_only_flags(stored))
    return out


@router.get("/")
async def get_settings():
    # Always re-read from Postgres so a GET after POST never hits a stale
    # in-process cache on a different uvicorn worker.
    stored = await refresh_settings_cache_async()
    return sanitize_settings(stored)


@router.post("/")
def update_settings(payload: dict):
    with get_db() as conn:
        for key, value in payload.items():
            if key in WRITE_ONLY_KEYS:
                if value is None:
                    upsert_setting_sync(conn, key, None)
                    continue
                text = str(value).strip()
                if not text or text == MASK_PLACEHOLDER:
                    continue
                upsert_setting_sync(conn, key, text)
                continue
            if key.endswith("_set") and key[: -len("_set")] in WRITE_ONLY_KEYS:
                continue
            # JSON null means delete (never store the literal string "None")
            if value is None:
                upsert_setting_sync(conn, key, None)
            else:
                upsert_setting_sync(conn, key, str(value))
        conn.commit()

    invalidate_settings_cache(broadcast=True)

    if "stock_symbols" in payload:
        notify_worker("refresh_stocks")
    if any(k in payload for k in ("weather_zip", "weather_lat", "weather_lon")):
        notify_worker("refresh_weather")
    if any(k in payload for k in ("ha_url", "ha_token", "ha_entities")):
        notify_worker("refresh_ha")
        notify_worker("sync_cameras")
    if any(
        k in payload
        for k in (
            "schoolcafe_district_id",
            "schoolcafe_lines",
            "lunch_district",
            "lunch_school_slug",
            "lunch_menu_type",
            "lunch_hidden",
        )
    ):
        notify_worker("refresh_meals")

    return {"ok": True, "settings": sanitize_settings(get_settings_map())}


@router.get("/{key}")
async def get_setting(key: str):
    stored = await get_settings_map_async()
    if key in WRITE_ONLY_KEYS:
        return {"key": key, "set": _is_set(stored.get(key))}
    if key not in stored:
        raise HTTPException(status_code=404, detail="Setting not found")
    return {"key": key, "value": stored[key]}
