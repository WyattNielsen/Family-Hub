import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi import APIRouter
import httpx
from database import get_db

router = APIRouter()

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
            async with httpx.AsyncClient(timeout=10.0) as client:
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

    # Fetch weather from Open-Meteo
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,weather_code",
                    "daily": "temperature_2m_max,temperature_2m_min",
                    "temperature_unit": "fahrenheit",
                    "wind_speed_unit": "mph",
                    "timezone": "auto",
                    "forecast_days": 1,
                }
            )
            if resp.status_code != 200:
                return {"error": "weather_fetch_failed"}
            data = resp.json()
    except Exception as e:
        print(f"Weather fetch error: {e}")
        return {"error": "weather_fetch_failed"}

    current = data.get("current", {})
    daily = data.get("daily", {})
    code = current.get("weather_code", 0)
    condition, icon = get_condition(code)

    return {
        "temp": current.get("temperature_2m"),
        "high": daily.get("temperature_2m_max", [None])[0],
        "low": daily.get("temperature_2m_min", [None])[0],
        "condition": condition,
        "icon": icon,
        "city": city,
    }
