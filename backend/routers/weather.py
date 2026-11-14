import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi import APIRouter
import asyncio
import time
import outbound
from database import get_db, get_settings_map, invalidate_settings_cache

router = APIRouter()

# Open-Meteo changes slowly. A page load on every screen was hitting it each time.
_FORECAST_TTL = 10 * 60
_FORECAST_STALE_TTL = 2 * 3600
_forecast_cache = {"key": None, "at": 0.0, "payload": None}
_forecast_lock = asyncio.Lock()
_forecast_bg: asyncio.Task | None = None

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


@router.get("/")
async def get_weather():
    s = get_settings_map()

    zip_code = s.get("weather_zip", "").strip()
    if not zip_code:
        return {"error": "no_location"}

    lat = s.get("weather_lat")
    lon = s.get("weather_lon")
    city = s.get("weather_city", "")

    # Geocode if lat/lon not cached
    if not lat or not lon:
        try:
            async with outbound.client(timeout=5.0) as client:
                resp = await client.get(
                    f"https://api.zippopotam.us/us/{zip_code}",
                    headers={"Accept": "application/json"}
                )
                if resp.status_code != 200:
                    return {"error": "geocode_failed"}
                geo = resp.json()
                place = geo["places"][0]
                lat = place["latitude"]
                lon = place["longitude"]
                city = f"{place['place name']}, {place['state abbreviation']}"
        except Exception as e:
            print(f"Geocode error: {e}")
            return {"error": "geocode_failed"}

        await asyncio.to_thread(_save_geo, lat, lon, city)

    return await _forecast(lat, lon, city)


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


async def _do_fetch_forecast(lat, lon, city: str, key: tuple) -> dict:
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
            payload = _forecast_payload(resp.json(), city)
    except Exception as e:
        print(f"Weather fetch error: {e}")
        return {"error": "weather_fetch_failed"}

    _forecast_cache["key"] = key
    _forecast_cache["at"] = time.monotonic()
    _forecast_cache["payload"] = payload
    return payload


async def _forecast(lat, lon, city: str) -> dict:
    global _forecast_bg
    key = (str(lat), str(lon))
    now = time.monotonic()
    cached = _forecast_cache
    if cached["payload"] is not None and cached["key"] == key:
        age = now - cached["at"]
        if age < _FORECAST_TTL:
            return cached["payload"]
        if age < _FORECAST_STALE_TTL:
            if _forecast_bg is None or _forecast_bg.done():
                _forecast_bg = asyncio.create_task(_do_fetch_forecast(lat, lon, city, key))
            return cached["payload"]

    async with _forecast_lock:
        cached = _forecast_cache
        if cached["payload"] is not None and cached["key"] == key and time.monotonic() - cached["at"] < _FORECAST_TTL:
            return cached["payload"]
        return await _do_fetch_forecast(lat, lon, city, key)
