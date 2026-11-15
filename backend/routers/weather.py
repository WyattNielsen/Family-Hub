import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi import APIRouter
import asyncio
import time
import outbound
from database import (
    get_db,
    get_settings_map_async,
    get_snapshot_async,
    invalidate_settings_cache,
    notify_worker_async,
    set_snapshot_async,
    upsert_setting_sync,
)

router = APIRouter()
_FORECAST_TTL = 15 * 60

WMO_CONDITIONS = {
    0:  ("Clear", "☀️"),
    1:  ("Mainly Clear", "🌤️"),
    2:  ("Partly Cloudy", "⛅"),
    3:  ("Overcast", "☁️"),
    45: ("Foggy", "🌫️"),
    48: ("Icy Fog", "🌫️"),
    51: ("Light Drizzle", "🌦️"),
    53: ("Drizzle", "🌦️"),
    55: ("Heavy Drizzle", "🌦️"),
    56: ("Freezing Drizzle", "🌨️"),
    57: ("Heavy Freezing Drizzle", "🌨️"),
    61: ("Light Rain", "🌧️"),
    63: ("Rain", "🌧️"),
    65: ("Heavy Rain", "🌧️"),
    66: ("Freezing Rain", "🌨️"),
    67: ("Heavy Freezing Rain", "🌨️"),
    71: ("Light Snow", "🌨️"),
    73: ("Snow", "❄️"),
    75: ("Heavy Snow", "❄️"),
    77: ("Snow Grains", "❄️"),
    80: ("Light Showers", "🌦️"),
    81: ("Showers", "🌧️"),
    82: ("Heavy Showers", "🌧️"),
    85: ("Snow Showers", "❄️"),
    86: ("Heavy Snow Showers", "❄️"),
    95: ("Thunderstorm", "⛈️"),
    96: ("Thunderstorm w/ Hail", "⛈️"),
    99: ("Thunderstorm w/ Hail", "⛈️"),
}


def get_condition(code):
    return WMO_CONDITIONS.get(code, ("Unknown", "🌡️"))


def _save_geo(zip_code: str, lat: str, lon: str, city: str):
    with get_db() as conn:
        upsert_setting_sync(conn, "weather_lat", lat)
        upsert_setting_sync(conn, "weather_lon", lon)
        upsert_setting_sync(conn, "weather_city", city)
        upsert_setting_sync(conn, "weather_geocoded_zip", zip_code)
        conn.commit()
    invalidate_settings_cache(broadcast=True)


def _at(seq, i):
    if isinstance(seq, list) and 0 <= i < len(seq):
        return seq[i]
    return None


def _forecast_payload(data: dict, city: str) -> dict:
    current = data.get("current", {})
    daily = data.get("daily", {})
    code = current.get("weather_code", 0)
    condition, icon = get_condition(code)

    dates = daily.get("time") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    codes = daily.get("weather_code") or []
    precips = daily.get("precipitation_probability_max") or []
    winds = daily.get("wind_speed_10m_max")
    has_wind = isinstance(winds, list)

    days = []
    for i, date in enumerate(dates):
        d_code = _at(codes, i)
        d_condition, d_icon = get_condition(0 if d_code is None else d_code)
        day = {
            "date": date,
            "icon": d_icon,
            "condition": d_condition,
            "high": _at(highs, i),
            "low": _at(lows, i),
            "precip": _at(precips, i),
        }
        if has_wind and i < len(winds):
            day["wind"] = winds[i]
        days.append(day)

    tmr_code = _at(codes, 1)
    tmr_condition, tmr_icon = get_condition(0 if tmr_code is None else tmr_code)

    return {
        "temp": current.get("temperature_2m"),
        "high": _at(highs, 0),
        "low": _at(lows, 0),
        "condition": condition,
        "icon": icon,
        "city": city,
        "tomorrow": {
            "high": _at(highs, 1),
            "low": _at(lows, 1),
            "condition": tmr_condition,
            "icon": tmr_icon,
            "precip": _at(precips, 1),
        },
        "days": days,
    }


async def refresh_weather_snapshot():
    """Live geocode + Open-Meteo fetch executed inside worker.py."""
    from database import reload_settings_map
    # Always re-read settings from Postgres — worker RAM cache can lag the web process.
    s = await asyncio.to_thread(reload_settings_map)
    zip_code = (s.get("weather_zip") or "").strip()
    if not zip_code:
        print("[weather] refresh skipped: weather_zip not set")
        return

    lat = s.get("weather_lat")
    lon = s.get("weather_lon")
    city = s.get("weather_city", "")
    geocoded_zip = s.get("weather_geocoded_zip", "")

    if not lat or not lon or geocoded_zip != zip_code:
        try:
            async with outbound.client(timeout=6.0) as client:
                resp = await client.get(
                    f"https://api.zippopotam.us/us/{zip_code}",
                    headers={"Accept": "application/json"},
                )
                if resp.status_code != 200:
                    await set_snapshot_async(f"weather:{zip_code}", {"error": "geocode_failed"}, error="geocode_failed")
                    return
                geo = resp.json()
                place = geo["places"][0]
                lat = place["latitude"]
                lon = place["longitude"]
                city = f"{place['place name']}, {place['state abbreviation']}"
        except Exception as e:
            print(f"Geocode error: {e}")
            return
        await asyncio.to_thread(_save_geo, zip_code, lat, lon, city)

    try:
        async with outbound.client(timeout=6.0) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,weather_code,wind_speed_10m",
                    "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max,wind_speed_10m_max",
                    "temperature_unit": "fahrenheit",
                    "wind_speed_unit": "mph",
                    "timezone": "auto",
                    "forecast_days": 7,
                },
            )
            if resp.status_code != 200:
                return
            payload = _forecast_payload(resp.json(), city)
    except Exception as e:
        print(f"Weather fetch error: {e}")
        return

    await set_snapshot_async(f"weather:{zip_code}", payload, error=None)


@router.get("/")
async def get_weather():
    """Zero-external-IO read of weather forecast from PostgreSQL snapshot."""
    s = await get_settings_map_async()
    zip_code = s.get("weather_zip", "").strip()
    if not zip_code:
        return {"error": "no_location"}

    snap = await get_snapshot_async(f"weather:{zip_code}")
    if snap is None:
        await notify_worker_async("refresh_weather")
        return {"warming": True}

    payload, updated_at, _err = snap
    if time.time() - updated_at > _FORECAST_TTL:
        await notify_worker_async("refresh_weather")
    return payload
