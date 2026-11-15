import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi import APIRouter
import asyncio
import time
import httpx
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
_INLINE_REFRESH_LOCK = asyncio.Lock()
_UA = {"User-Agent": "FamilyHub/1.0 (self-hosted; weather@hub.local)", "Accept": "application/json"}

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


def _icon_from_text(text: str) -> str:
    t = (text or "").lower()
    if "thunder" in t or "storm" in t:
        return "⛈️"
    if "snow" in t or "blizzard" in t:
        return "❄️"
    if "freezing" in t or "sleet" in t or "ice" in t:
        return "🌨️"
    if "rain" in t or "shower" in t or "drizzle" in t:
        return "🌧️"
    if "fog" in t or "mist" in t:
        return "🌫️"
    if "overcast" in t or "cloudy" in t:
        return "☁️"
    if "partly" in t:
        return "⛅"
    if "clear" in t or "sunny" in t or "fair" in t:
        return "☀️"
    return "🌡️"


_FORECAST_PARAMS = {
    "current": "temperature_2m,weather_code,wind_speed_10m",
    "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max,wind_speed_10m_max",
    "temperature_unit": "fahrenheit",
    "wind_speed_unit": "mph",
    "timezone": "auto",
    "forecast_days": 7,
}


async def _http_get_json(url: str, *, params=None, headers=None, timeout: float = 8.0) -> tuple[int, dict | None, str | None]:
    """GET JSON via shared outbound client, then a plain IPv4 client if that fails."""
    hdrs = dict(_UA)
    if headers:
        hdrs.update(headers)
    last_err = None
    try:
        async with outbound.client(timeout=timeout) as client:
            resp = await client.get(url, params=params, headers=hdrs)
            if resp.status_code == 200:
                return resp.status_code, resp.json(), None
            return resp.status_code, None, f"http_{resp.status_code}"
    except Exception as e:
        last_err = e
        print(f"[weather] shared client error for {url}: {e}")
        try:
            await outbound.aclose()
        except Exception:
            pass

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            trust_env=False,
            transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
        ) as client:
            resp = await client.get(url, params=params, headers=hdrs)
            if resp.status_code == 200:
                return resp.status_code, resp.json(), None
            return resp.status_code, None, f"http_{resp.status_code}"
    except Exception as e:
        print(f"[weather] fallback client error for {url}: {e}")
        return 0, None, str(last_err or e)


async def _fetch_open_meteo(lat: str, lon: str, city: str) -> dict | None:
    params = dict(_FORECAST_PARAMS)
    params["latitude"] = lat
    params["longitude"] = lon
    status, data, err = await _http_get_json(
        "https://api.open-meteo.com/v1/forecast",
        params=params,
        timeout=8.0,
    )
    if status != 200 or not data:
        print(f"[weather] open-meteo failed status={status} err={err}")
        return None
    return _forecast_payload(data, city)


async def _fetch_nws(lat: str, lon: str, city: str) -> dict | None:
    """US National Weather Service — useful when open-meteo is blocked on the LAN."""
    status, points, err = await _http_get_json(
        f"https://api.weather.gov/points/{lat},{lon}",
        timeout=8.0,
    )
    if status != 200 or not points:
        print(f"[weather] nws points failed status={status} err={err}")
        return None
    props = points.get("properties") or {}
    forecast_url = props.get("forecast")
    if not forecast_url:
        return None
    status, forecast, err = await _http_get_json(forecast_url, timeout=8.0)
    if status != 200 or not forecast:
        print(f"[weather] nws forecast failed status={status} err={err}")
        return None

    periods = (forecast.get("properties") or {}).get("periods") or []
    if not periods:
        return None

    # Collapse day/night periods into daily rows.
    by_date: dict[str, dict] = {}
    for p in periods:
        start = (p.get("startTime") or "")[:10]
        if not start:
            continue
        day = by_date.setdefault(start, {"date": start, "high": None, "low": None, "condition": None, "icon": None, "precip": None, "wind": None})
        temp = p.get("temperature")
        if p.get("isDaytime"):
            day["high"] = temp
            cond = p.get("shortForecast") or day["condition"]
            day["condition"] = cond
            day["icon"] = _icon_from_text(cond)
            day["precip"] = p.get("probabilityOfPrecipitation", {}).get("value")
            wind = p.get("windSpeed") or ""
            try:
                day["wind"] = float(str(wind).split()[0])
            except Exception:
                pass
        else:
            day["low"] = temp
            if not day["condition"]:
                cond = p.get("shortForecast")
                day["condition"] = cond
                day["icon"] = _icon_from_text(cond)

    days = list(by_date.values())[:7]
    if not days:
        return None

    first = periods[0]
    temp = first.get("temperature")
    condition = first.get("shortForecast") or days[0].get("condition") or "Unknown"
    icon = _icon_from_text(condition)
    tmr = days[1] if len(days) > 1 else {}
    return {
        "temp": temp,
        "high": days[0].get("high"),
        "low": days[0].get("low"),
        "condition": condition,
        "icon": icon,
        "city": city,
        "tomorrow": {
            "high": tmr.get("high"),
            "low": tmr.get("low"),
            "condition": tmr.get("condition"),
            "icon": tmr.get("icon") or _icon_from_text(tmr.get("condition") or ""),
            "precip": tmr.get("precip"),
        },
        "days": [
            {
                "date": d["date"],
                "icon": d.get("icon") or _icon_from_text(d.get("condition") or ""),
                "condition": d.get("condition") or "",
                "high": d.get("high"),
                "low": d.get("low"),
                "precip": d.get("precip"),
                **({"wind": d["wind"]} if d.get("wind") is not None else {}),
            }
            for d in days
        ],
    }


async def _fetch_wttr(zip_code: str, city: str) -> dict | None:
    status, data, err = await _http_get_json(
        f"https://wttr.in/{zip_code}",
        params={"format": "j1"},
        timeout=8.0,
    )
    if status != 200 or not data:
        print(f"[weather] wttr failed status={status} err={err}")
        return None
    try:
        cur = (data.get("current_condition") or [{}])[0]
        temp = float(cur.get("temp_F"))
        condition = ((cur.get("weatherDesc") or [{}])[0].get("value")) or "Unknown"
        icon = _icon_from_text(condition)
        days_raw = data.get("weather") or []
        days = []
        for d in days_raw[:7]:
            cond = condition
            hourly = d.get("hourly") or []
            if hourly:
                mid = hourly[len(hourly) // 2]
                cond = ((mid.get("weatherDesc") or [{}])[0].get("value")) or cond
                precip = mid.get("chanceofrain")
                try:
                    precip = int(precip) if precip is not None else None
                except Exception:
                    precip = None
            else:
                precip = None
            days.append({
                "date": d.get("date"),
                "icon": _icon_from_text(cond),
                "condition": cond,
                "high": float(d.get("maxtempF")) if d.get("maxtempF") is not None else None,
                "low": float(d.get("mintempF")) if d.get("mintempF") is not None else None,
                "precip": precip,
            })
        tmr = days[1] if len(days) > 1 else {}
        return {
            "temp": temp,
            "high": days[0].get("high") if days else None,
            "low": days[0].get("low") if days else None,
            "condition": condition,
            "icon": icon,
            "city": city or zip_code,
            "tomorrow": {
                "high": tmr.get("high"),
                "low": tmr.get("low"),
                "condition": tmr.get("condition"),
                "icon": tmr.get("icon"),
                "precip": tmr.get("precip"),
            },
            "days": days,
        }
    except Exception as e:
        print(f"[weather] wttr parse error: {e}")
        return None


async def refresh_weather_snapshot() -> bool:
    """Live geocode + forecast fetch. Returns True when a usable forecast was stored."""
    from database import reload_settings_map
    # Always re-read settings from Postgres — worker RAM cache can lag the web process.
    s = await asyncio.to_thread(reload_settings_map)
    zip_code = (s.get("weather_zip") or "").strip()
    if not zip_code:
        print("[weather] refresh skipped: weather_zip not set")
        return False

    snap_key = f"weather:{zip_code}"
    lat = (s.get("weather_lat") or "").strip()
    lon = (s.get("weather_lon") or "").strip()
    city = s.get("weather_city") or ""
    geocoded_zip = (s.get("weather_geocoded_zip") or "").strip()

    if not lat or not lon or geocoded_zip != zip_code:
        status, geo, err = await _http_get_json(
            f"https://api.zippopotam.us/us/{zip_code}",
            timeout=8.0,
        )
        if status != 200 or not geo:
            print(f"[weather] geocode failed zip={zip_code} status={status} err={err}")
            await set_snapshot_async(snap_key, {"error": "geocode_failed"}, error="geocode_failed")
            return False
        try:
            place = geo["places"][0]
            lat = place["latitude"]
            lon = place["longitude"]
            city = f"{place['place name']}, {place['state abbreviation']}"
        except Exception as e:
            print(f"[weather] geocode parse error: {e}")
            await set_snapshot_async(snap_key, {"error": "geocode_failed"}, error="geocode_failed")
            return False
        await asyncio.to_thread(_save_geo, zip_code, lat, lon, city)

    payload = await _fetch_open_meteo(lat, lon, city)
    if not payload:
        payload = await _fetch_nws(lat, lon, city)
    if not payload:
        payload = await _fetch_wttr(zip_code, city)
    if not payload:
        print(f"[weather] all providers failed zip={zip_code}")
        await set_snapshot_async(snap_key, {"error": "fetch_failed"}, error="fetch_failed")
        return False

    await set_snapshot_async(snap_key, payload, error=None)
    print(f"[weather] refreshed zip={zip_code} temp={payload.get('temp')} city={payload.get('city')}")
    return True


@router.get("/")
async def get_weather():
    """Prefer snapshot; if missing, fetch inline once so the UI is not stuck on warming."""
    s = await get_settings_map_async()
    zip_code = (s.get("weather_zip") or "").strip()
    if not zip_code:
        return {"error": "no_location"}

    snap_key = f"weather:{zip_code}"
    snap = await get_snapshot_async(snap_key)

    def _is_soft_error(row):
        if not row:
            return True
        payload, updated_at, _err = row
        return (
            isinstance(payload, dict)
            and payload.get("error")
            and payload.get("temp") is None
            and time.time() - updated_at > 30
        )

    if snap is None or _is_soft_error(snap):
        await notify_worker_async("refresh_weather")
        # Inline fill so a stuck/wedged worker does not leave the header empty forever.
        if _INLINE_REFRESH_LOCK.locked():
            for _ in range(40):
                await asyncio.sleep(0.25)
                snap = await get_snapshot_async(snap_key)
                if snap and not (
                    isinstance(snap[0], dict) and snap[0].get("error") and snap[0].get("temp") is None
                ):
                    break
        else:
            async with _INLINE_REFRESH_LOCK:
                snap = await get_snapshot_async(snap_key)
                needs = snap is None or (
                    isinstance(snap[0], dict)
                    and snap[0].get("error")
                    and snap[0].get("temp") is None
                )
                if needs:
                    try:
                        await asyncio.wait_for(refresh_weather_snapshot(), timeout=20.0)
                    except Exception as e:
                        print(f"[weather] inline refresh error: {e}")
                    snap = await get_snapshot_async(snap_key)

        if snap is None:
            return {"warming": True}

    payload, updated_at, _err = snap
    if time.time() - updated_at > _FORECAST_TTL:
        await notify_worker_async("refresh_weather")
    if isinstance(payload, dict) and payload.get("error") and payload.get("temp") is None:
        return {"warming": True, "error": payload.get("error")}
    return payload
