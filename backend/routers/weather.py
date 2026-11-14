import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi import APIRouter
import asyncio
import time
import outbound
from database import get_db

router = APIRouter()

# Open-Meteo changes slowly. A page load on every screen was hitting it each time.
_FORECAST_TTL = 10 * 60
_forecast_cache = {"key": None, "at": 0.0, "payload": None}
_forecast_lock = asyncio.Lock()

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


@router.get("/")
async def get_weather():
    conn = get_db()
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key IN ('weather_zip','weather_lat','weather_lon','weather_city')"
    ).fetchall()
    conn.close()
    s = {r["key"]: r["value"] for r in rows}

    zip_code = s.get("weather_zip", "").strip()
    if not zip_code:
        return {"error": "no_location"}

    lat = s.get("weather_lat")
    lon = s.get("weather_lon")
    city = s.get("weather_city", "")

    # Geocode if lat/lon not cached
    if not lat or not lon:
        try:
            async with outbound.client(timeout=10.0) as client:
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

        # Cache lat/lon/city
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ("weather_lat", lat))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ("weather_lon", lon))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ("weather_city", city))
        conn.commit()
        conn.close()

    return await _forecast(lat, lon, city)


def _forecast_payload(data: dict, city: str) -> dict:
    current = data.get("current", {})
    daily = data.get("daily", {})
    code = current.get("weather_code", 0)
    condition, icon = get_condition(code)

    tmr_code = (daily.get("weather_code") or [0, 0])[1]
    tmr_condition, tmr_icon = get_condition(tmr_code)
    tmr_precip = (daily.get("precipitation_probability_max") or [None, None])[1]

    return {
        "temp": current.get("temperature_2m"),
        "high": daily.get("temperature_2m_max", [None])[0],
        "low": daily.get("temperature_2m_min", [None])[0],
        "condition": condition,
        "icon": icon,
        "city": city,
        "tomorrow": {
            "high": (daily.get("temperature_2m_max") or [None, None])[1],
            "low": (daily.get("temperature_2m_min") or [None, None])[1],
            "condition": tmr_condition,
            "icon": tmr_icon,
            "precip": tmr_precip,
        }
    }


async def _forecast(lat, lon, city: str) -> dict:
    key = (str(lat), str(lon))
    now = time.monotonic()
    cached = _forecast_cache
    if cached["payload"] is not None and cached["key"] == key and now - cached["at"] < _FORECAST_TTL:
        return cached["payload"]

    async with _forecast_lock:
        cached = _forecast_cache
        if cached["payload"] is not None and cached["key"] == key and time.monotonic() - cached["at"] < _FORECAST_TTL:
            return cached["payload"]
        try:
            async with outbound.client(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "current": "temperature_2m,weather_code",
                        "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max",
                        "temperature_unit": "fahrenheit",
                        "wind_speed_unit": "mph",
                        "timezone": "auto",
                        "forecast_days": 2,
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
