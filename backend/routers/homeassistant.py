import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import asyncio
import json
import time
import outbound
from database import (
    get_settings_map_async,
    get_snapshot_async,
    notify_worker_async,
    set_snapshot_async,
)

router = APIRouter()
_HA_TTL = 15.0

ALARM_STATES = {
    "disarmed":    ("🔓", "ha-alarm-disarmed",   "Disarmed"),
    "armed_away":  ("🔒", "ha-alarm-armed",       "Armed Away"),
    "armed_home":  ("🔒", "ha-alarm-armed",       "Armed Home"),
    "armed_night": ("🔒", "ha-alarm-armed",       "Armed Night"),
    "arming":      ("⏳", "ha-alarm-pending",     "Arming…"),
    "pending":     ("⏳", "ha-alarm-pending",     "Pending…"),
    "triggered":   ("🚨", "ha-alarm-triggered",   "TRIGGERED"),
}


def alarm_visuals(state: str) -> tuple[str, str, str]:
    return ALARM_STATES.get(state, ("❓", "ha-state-unknown", state.replace("_", " ").title()))


def normalize_state(entity_id: str, raw_state: str) -> str:
    if entity_id.startswith("binary_sensor."):
        return "open" if raw_state == "on" else "closed"
    return raw_state


def state_visuals(entity_id: str, state: str) -> tuple[str, str]:
    if state in ("open", "opening", "on", "unlocked"):
        if "garage" in entity_id or entity_id.startswith("cover."):
            return ("🚗", "ha-state-open")
        if "lock" in entity_id:
            return ("🔓", "ha-state-open")
        return ("🔴", "ha-state-open")
    if state in ("closed", "closing", "off", "locked"):
        if "garage" in entity_id or entity_id.startswith("cover."):
            return ("🏠", "ha-state-closed")
        if "lock" in entity_id:
            return ("🔒", "ha-state-closed")
        return ("🟢", "ha-state-closed")
    return ("❓", "ha-state-unknown")


def _unavailable(entity_id: str, name: str) -> dict:
    return {
        "entity_id": entity_id,
        "name": name,
        "state": "unavailable",
        "state_label": "Unavailable",
        "icon": "❓",
        "css_class": "ha-state-unknown",
        "entity_type": "alarm" if entity_id.startswith("alarm_control_panel.") else "generic",
    }


def shape_state(entity_id: str, name: str, raw: str) -> dict:
    if entity_id.startswith("alarm_control_panel."):
        icon, css_class, label = alarm_visuals(raw)
        return {
            "entity_id": entity_id,
            "name": name,
            "state": raw,
            "state_label": label,
            "icon": icon,
            "css_class": css_class,
            "entity_type": "alarm",
        }
    state = normalize_state(entity_id, raw)
    icon, css_class = state_visuals(entity_id, state)
    return {
        "entity_id": entity_id,
        "name": name,
        "state": state,
        "state_label": state,
        "icon": icon,
        "css_class": css_class,
        "entity_type": "generic",
    }


def _parse_configured_entities(s: dict) -> tuple[str, str, list[tuple[str, str]], Optional[str]]:
    ha_url = s.get("ha_url", "").strip()
    ha_token = s.get("ha_token", "").strip()
    ha_entities_raw = s.get("ha_entities", "").strip()
    if not ha_url or not ha_token or not ha_entities_raw:
        return ha_url, ha_token, [], "not_configured"
    try:
        entities = json.loads(ha_entities_raw)
    except Exception:
        return ha_url, ha_token, [], "invalid_config"
    if not entities:
        return ha_url, ha_token, [], "not_configured"
    named = [
        (e.get("entity_id", ""), e.get("name") or e.get("entity_id", ""))
        for e in entities
        if e.get("entity_id")
    ]
    if not named:
        return ha_url, ha_token, [], "not_configured"
    return ha_url, ha_token, named, None


async def _fetch_single_entity(client, base_url: str, headers: dict, entity_id: str, name: str) -> dict:
    try:
        resp = await client.get(f"{base_url}/api/states/{entity_id}", headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            return shape_state(entity_id, name, data.get("state", "unknown"))
    except Exception as e:
        print(f"HA fetch error for {entity_id}: {e}")
    return _unavailable(entity_id, name)


async def refresh_ha_snapshot():
    """Live Home Assistant state poll executed inside worker.py."""
    s = await get_settings_map_async()
    ha_url, ha_token, named, err = _parse_configured_entities(s)
    if err:
        return
    base_url = ha_url.rstrip("/")
    headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
    async with outbound.client(timeout=4.0) as client:
        results = await asyncio.gather(
            *[_fetch_single_entity(client, base_url, headers, eid, name) for eid, name in named]
        )
    await set_snapshot_async("ha:states", {"entities": list(results)}, error=None)


async def _optimistic_snapshot_update(entity_id: str, new_raw_state: str):
    snap = await get_snapshot_async("ha:states")
    if not snap or not isinstance(snap[0], dict):
        return
    entities = snap[0].get("entities") or []
    updated = []
    for item in entities:
        if item.get("entity_id") == entity_id:
            updated.append(shape_state(entity_id, item.get("name") or entity_id, new_raw_state))
        else:
            updated.append(item)
    await set_snapshot_async("ha:states", {"entities": updated}, error=None)


@router.get("/states")
async def get_ha_states():
    """Zero-external-IO read of Home Assistant entity states from PostgreSQL snapshot."""
    s = await get_settings_map_async()
    _ha_url, _ha_token, named, err = _parse_configured_entities(s)
    if err:
        return {"error": err}

    snap = await get_snapshot_async("ha:states")
    if snap is None:
        await notify_worker_async("refresh_ha")
        return {
            "warming": True,
            "entities": [_unavailable(eid, name) for eid, name in named],
        }

    payload, updated_at, _snap_err = snap
    if time.time() - updated_at > _HA_TTL:
        await notify_worker_async("refresh_ha")
    return payload


class HAActionRequest(BaseModel):
    entity_id: str


@router.post("/cover/{action}")
async def cover_action(action: str, body: HAActionRequest):
    valid_actions = {"open_cover", "close_cover", "stop_cover"}
    if action not in valid_actions:
        raise HTTPException(status_code=400, detail="Invalid action")

    s = await get_settings_map_async()
    ha_url = s.get("ha_url", "").strip()
    ha_token = s.get("ha_token", "").strip()
    if not ha_url or not ha_token:
        raise HTTPException(status_code=400, detail="HA not configured")

    try:
        async with outbound.client(timeout=6.0) as client:
            resp = await client.post(
                f"{ha_url.rstrip('/')}/api/services/cover/{action}",
                headers={"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"},
                json={"entity_id": body.entity_id},
            )
            if resp.status_code not in (200, 201):
                raise HTTPException(status_code=502, detail=f"HA returned {resp.status_code}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"HA cover action error: {e}")
        raise HTTPException(status_code=502, detail="Could not reach Home Assistant")

    if action in ("open_cover", "close_cover"):
        await _optimistic_snapshot_update(body.entity_id, "opening" if action == "open_cover" else "closing")
    await notify_worker_async("refresh_ha")
    return {"ok": True}


@router.post("/alarm/{action}")
async def alarm_action(action: str, body: HAActionRequest):
    valid_actions = {"alarm_disarm", "alarm_arm_away", "alarm_arm_home", "alarm_arm_night"}
    if action not in valid_actions:
        raise HTTPException(status_code=400, detail="Invalid action")

    s = await get_settings_map_async()
    ha_url = s.get("ha_url", "").strip()
    ha_token = s.get("ha_token", "").strip()
    alarm_code = s.get("ha_alarm_code", "").strip()
    if not ha_url or not ha_token:
        raise HTTPException(status_code=400, detail="HA not configured")

    payload = {"entity_id": body.entity_id}
    if alarm_code:
        payload["code"] = alarm_code

    try:
        async with outbound.client(timeout=6.0) as client:
            resp = await client.post(
                f"{ha_url.rstrip('/')}/api/services/alarm_control_panel/{action}",
                headers={"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"},
                json=payload,
            )
            if resp.status_code not in (200, 201):
                raise HTTPException(status_code=502, detail=f"HA returned {resp.status_code}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"HA alarm action error: {e}")
        raise HTTPException(status_code=502, detail="Could not reach Home Assistant")

    expected = {
        "alarm_disarm": "disarmed",
        "alarm_arm_away": "armed_away",
        "alarm_arm_home": "armed_home",
        "alarm_arm_night": "armed_night",
    }.get(action, "arming")
    await _optimistic_snapshot_update(body.entity_id, expected)
    await notify_worker_async("refresh_ha")
    return {"ok": True}


class TTSRequest(BaseModel):
    message: str
    entity_id: str
    tts_engine: str = "tts.google_translate_en_com"


@router.post("/tts")
async def speak_tts(body: TTSRequest):
    s = await get_settings_map_async()
    ha_url = s.get("ha_url", "").strip()
    ha_token = s.get("ha_token", "").strip()
    if not ha_url or not ha_token:
        raise HTTPException(status_code=400, detail="Home Assistant not configured")

    payload = {
        "entity_id": body.tts_engine,
        "media_player_entity_id": body.entity_id,
        "message": body.message,
    }
    try:
        async with outbound.client(timeout=8.0) as client:
            resp = await client.post(
                f"{ha_url.rstrip('/')}/api/services/tts/speak",
                headers={"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"},
                json=payload,
            )
            if resp.status_code not in (200, 201):
                print(f"HA TTS error: status={resp.status_code} body={resp.text[:500]}")
                raise HTTPException(status_code=502, detail=f"HA returned {resp.status_code}: {resp.text[:200]}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"HA TTS exception: {e}")
        raise HTTPException(status_code=502, detail=f"Could not reach Home Assistant: {e}")
    return {"ok": True}


async def fire_tts(message: str, entity_id: str, tts_engine: str = "tts.google_translate_en_com"):
    """Called by worker.py reminder loop."""
    s = await get_settings_map_async()
    ha_url = s.get("ha_url", "").strip()
    ha_token = s.get("ha_token", "").strip()
    if not ha_url or not ha_token:
        return
    try:
        async with outbound.client(timeout=8.0) as client:
            await client.post(
                f"{ha_url.rstrip('/')}/api/services/tts/speak",
                headers={"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"},
                json={"entity_id": tts_engine, "media_player_entity_id": entity_id, "message": message},
            )
    except Exception as e:
        print(f"TTS reminder error: {e}")
