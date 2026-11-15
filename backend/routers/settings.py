import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import asyncio
import traceback
from fastapi import APIRouter, HTTPException
from database import (
    get_db,
    get_settings_map_async,
    invalidate_settings_cache,
    notify_worker,
    reload_settings_map,
    upsert_setting_sync,
)

router = APIRouter()

# Secrets the client may replace, but must never read back.
WRITE_ONLY_KEYS = frozenset({"ha_token", "ha_alarm_code"})
# Older clients echoed this mask. Never store it, and never treat it as a new secret.
MASK_PLACEHOLDER = "••••••••"


def _as_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def _is_set(value) -> bool:
    text = _as_str(value).strip()
    return bool(text) and text != MASK_PLACEHOLDER


def _write_only_flags(stored: dict) -> dict:
    return {f"{key}_set": _is_set(stored.get(key)) for key in WRITE_ONLY_KEYS}


def sanitize_settings(stored: dict) -> dict:
    out = {}
    for raw_key, value in (stored or {}).items():
        key = _as_str(raw_key)
        if key in WRITE_ONLY_KEYS or key.startswith("_"):
            continue
        out[key] = _as_str(value) if value is not None else ""
    out.update(_write_only_flags(stored or {}))
    return out


def _http_err(prefix: str, exc: Exception) -> HTTPException:
    tb = traceback.format_exc(limit=6)
    return HTTPException(status_code=500, detail=f"{prefix}: {exc} | {tb}")


@router.get("/")
async def get_settings():
    # Sync reload avoids cross-worker stale RAM and surfaces DB errors clearly.
    try:
        stored = await asyncio.to_thread(reload_settings_map)
        return sanitize_settings(stored)
    except Exception as e:
        raise _http_err("settings read failed", e) from e


@router.get("/page")
async def get_settings_page():
    """One JSON blob for the Settings screen — settings + members + calendars."""
    try:
        from routers import calendar, members

        stored = await asyncio.to_thread(reload_settings_map)
        settings = sanitize_settings(stored)
        member_rows = await asyncio.to_thread(members.get_members)
        accounts = await asyncio.to_thread(calendar.list_calendar_accounts)
        sources = await asyncio.to_thread(calendar.list_calendar_sources)
        family_write = await asyncio.to_thread(calendar.get_family_write_calendar)
        interval = settings.get("google_sync_interval") or "0"
        try:
            interval_minutes = int(interval)
        except (TypeError, ValueError):
            interval_minutes = 0
        return {
            "settings": settings,
            "members": member_rows,
            "calendar_accounts": accounts,
            "calendar_sources": sources,
            "family_write": family_write,
            "sync": {
                "last_synced": settings.get("google_last_synced") or None,
                "interval_minutes": interval_minutes,
            },
        }
    except Exception as e:
        raise _http_err("settings page read failed", e) from e


@router.post("/")
def update_settings(payload: dict):
    try:
        with get_db() as conn:
            for raw_key, value in payload.items():
                key = _as_str(raw_key)
                if key in WRITE_ONLY_KEYS:
                    if value is None:
                        upsert_setting_sync(conn, key, None)
                        continue
                    text = _as_str(value).strip()
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
                    upsert_setting_sync(conn, key, _as_str(value))
            conn.commit()

        stored = invalidate_settings_cache(broadcast=True)
        if stored is None:
            stored = reload_settings_map()

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

        return {"ok": True, "settings": sanitize_settings(stored)}
    except HTTPException:
        raise
    except Exception as e:
        raise _http_err("settings write failed", e) from e


@router.get("/{key}")
async def get_setting(key: str):
    stored = await get_settings_map_async()
    if key in WRITE_ONLY_KEYS:
        return {"key": key, "set": _is_set(stored.get(key))}
    if key not in stored:
        raise HTTPException(status_code=404, detail="Setting not found")
    return {"key": key, "value": stored[key]}
