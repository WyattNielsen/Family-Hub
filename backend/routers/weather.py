import json
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi import APIRouter
import asyncio
import time
import outbound
import jobs
from database import get_db, get_setting_value, get_settings_map_async, invalidate_settings_cache

router = APIRouter()

# Open-Meteo changes slowly. A stored copy is served while a refresh runs.
_FORECAST_TTL = 10 * 60

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


def _save_geo(lat: str, lon: str, city: str):
    conn = get_db()
    try:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ("weather_lat", lat))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ("weather_lon", lon))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ("weather_city", city))
        conn.commit()
    finally:
        conn.close()
    invalidate_settings_cache()


def _load_forecast():
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT cache_key, payload, fetched_at FROM weather_forecast WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        payload = json.loads(row["payload"])
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return {
        "cache_key": row["cache_key"],
        "payload": payload,
        "fetched_at": row["fetched_at"],
    }


def _save_forecast(key: str, payload: dict):
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO weather_forecast (id, cache_key, payload, fetched_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                cache_key = excluded.cache_key,
                payload = excluded.payload,
                fetched_at = excluded.fetched_at
            """,
            (key, json.dumps(payload), time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def _save_forecast_if_current(key: str, payload: dict):
    """Drop a refresh that finished after the zip code changed."""
    lat = (get_setting_value("weather_lat") or "").strip()
    lon = (get_setting_value("weather_lon") or "").strip()
    if f"{lat},{lon}" != key:
        return
    _save_forecast(key, payload)


@router.get("/")
async def get_weather():
    s = await get_settings_map_async()

    zip_code = (s.get("weather_zip") or "").strip()
    if not zip_code:
        return {"error": "no_location"}

    lat = (s.get("weather_lat") or "").strip()
    lon = (s.get("weather_lon") or "").strip()
    city = s.get("weather_city") or ""
    stored = await asyncio.to_thread(_load_forecast)

    if lat and lon:
        key = f"{lat},{lon}"
        if stored and stored["cache_key"] == key and not stored["payload"].get("error"):
            age = time.time() - float(stored["fetched_at"] or 0)
            if age >= _FORECAST_TTL:
                await jobs.run_background(
                    "weather-forecast",
                    lambda lat=lat, lon=lon, city=city, key=key: _refresh_forecast(lat, lon, city, key),
                )
            return stored["payload"]

    # Nothing stored for this place. The widget cannot paint an empty payload,
    # so this first request fetches once. Later requests read the stored copy.
    if not lat or not lon:
        geo = await _geocode(zip_code)
        if not geo:
            return {"error": "geocode_failed"}
        lat, lon, city = geo
        await asyncio.to_thread(_save_geo, lat, lon, city)
    key = f"{lat},{lon}"
    payload = await _fetch_open_meteo(lat, lon, city)
    if not payload.get("error"):
        await asyncio.to_thread(_save_forecast, key, payload)
    return payload


async def _geocode(zip_code: str):
    try:
        async with outbound.client(timeout=5.0) as client:
            resp = await client.get(
                f"https://api.zippopotam.us/us/{zip_code}",
                headers={"Accept": "application/json"}
            )
            if resp.status_code != 200:
                return None
            geo = resp.json()
            place = geo["places"][0]
            lat = place["latitude"]
            lon = place["longitude"]
            city = f"{place['place name']}, {place['state abbreviation']}"
            return lat, lon, city
    except Exception as e:
        print(f"Geocode error: {e}")
        return None


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


async def _fetch_open_meteo(lat, lon, city: str) -> dict:
    try:
        async with outbound.client(timeout=5.0) as client:
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
                }
            )
            if resp.status_code != 200:
                return {"error": "weather_fetch_failed"}
            return _forecast_payload(resp.json(), city)
    except Exception as e:
        print(f"Weather fetch error: {e}")
        return {"error": "weather_fetch_failed"}


async def _refresh_forecast(lat, lon, city: str, key: str):
    payload = await _fetch_open_meteo(lat, lon, city)
    if payload.get("error"):
        return
    await asyncio.to_thread(_save_forecast_if_current, key, payload)
