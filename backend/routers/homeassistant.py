import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import json
import outbound
from database import get_db

router = APIRouter()

# ── Alarm control panel ───────────────────────────────────────

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


# ── Generic entity helpers ────────────────────────────────────

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
        "entity_id": entity_id, "name": name,
        "state": "unavailable", "state_label": "Unavailable",
        "icon": "❓", "css_class": "ha-state-unknown",
        "entity_type": "alarm" if entity_id.startswith("alarm_control_panel.") else "generic",
    }


def shape_state(entity_id: str, name: str, raw: str) -> dict:
    if entity_id.startswith("alarm_control_panel."):
        icon, css_class, label = alarm_visuals(raw)
        return {
            "entity_id": entity_id, "name": name,
            "state": raw, "state_label": label,
            "icon": icon, "css_class": css_class,
            "entity_type": "alarm",
        }
    state = normalize_state(entity_id, raw)
    icon, css_class = state_visuals(entity_id, state)
    return {
        "entity_id": entity_id, "name": name,
        "state": state, "state_label": state,
        "icon": icon, "css_class": css_class,
        "entity_type": "generic",
    }


# ── Routes ────────────────────────────────────────────────────

@router.get("/states")
async def get_ha_states():
    conn = get_db()
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key IN ('ha_url', 'ha_token', 'ha_entities')"
    ).fetchall()
    conn.close()
    s = {r["key"]: r["value"] for r in rows}

    ha_url = s.get("ha_url", "").strip()
    ha_token = s.get("ha_token", "").strip()
    ha_entities_raw = s.get("ha_entities", "").strip()

    if not ha_url or not ha_token or not ha_entities_raw:
        return {"error": "not_configured"}

    try:
        entities = json.loads(ha_entities_raw)
    except Exception:
        return {"error": "invalid_config"}

    if not entities:
        return {"error": "not_configured"}

    named = [(e.get("entity_id", ""), e.get("name") or e.get("entity_id", "")) for e in entities]
    states = {}
    try:
        async with outbound.client(timeout=10.0) as client:
            resp = await client.get(
                f"{ha_url.rstrip('/')}/api/states",
                headers={"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"HA returned {resp.status_code}")
        for row in resp.json():
            if isinstance(row, dict) and row.get("entity_id"):
                states[row["entity_id"]] = row.get("state", "unknown")
    except Exception as e:
        print(f"HA states fetch error: {e}")
        return {"entities": [_unavailable(entity_id, name) for entity_id, name in named if entity_id]}

    results = []
    for entity_id, name in named:
        if not entity_id:
            continue
        raw = states.get(entity_id)
        if raw is None:
            results.append(_unavailable(entity_id, name))
        else:
            results.append(shape_state(entity_id, name, raw))
    return {"entities": results}


class HAActionRequest(BaseModel):
    entity_id: str

@router.post("/cover/{action}")
async def cover_action(action: str, body: HAActionRequest):
    valid_actions = {"open_cover", "close_cover", "stop_cover"}
    if action not in valid_actions:
        raise HTTPException(status_code=400, detail="Invalid action")

    conn = get_db()
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key IN ('ha_url', 'ha_token')"
    ).fetchall()
    conn.close()
    s = {r["key"]: r["value"] for r in rows}

    ha_url = s.get("ha_url", "").strip()
    ha_token = s.get("ha_token", "").strip()

    if not ha_url or not ha_token:
        raise HTTPException(status_code=400, detail="HA not configured")

    try:
        async with outbound.client(timeout=10.0) as client:
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

    return {"ok": True}


@router.post("/alarm/{action}")
async def alarm_action(action: str, body: HAActionRequest):
    valid_actions = {"alarm_disarm", "alarm_arm_away", "alarm_arm_home", "alarm_arm_night"}
    if action not in valid_actions:
        raise HTTPException(status_code=400, detail="Invalid action")

    conn = get_db()
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key IN ('ha_url', 'ha_token', 'ha_alarm_code')"
    ).fetchall()
    conn.close()
    s = {r["key"]: r["value"] for r in rows}

    ha_url = s.get("ha_url", "").strip()
    ha_token = s.get("ha_token", "").strip()
    alarm_code = s.get("ha_alarm_code", "").strip()

    if not ha_url or not ha_token:
        raise HTTPException(status_code=400, detail="HA not configured")

    payload = {"entity_id": body.entity_id}
    if alarm_code:
        payload["code"] = alarm_code

    try:
        async with outbound.client(timeout=10.0) as client:
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

    return {"ok": True}


# ── TTS Announcement ──────────────────────────────────────────

class TTSRequest(BaseModel):
    message: str
    entity_id: str  # media player / speaker
    tts_engine: str = "tts.google_translate_en_com"

@router.post("/tts")
async def speak_tts(body: TTSRequest):
    conn = get_db()
    s = {r["key"]: r["value"] for r in conn.execute(
        "SELECT key, value FROM settings WHERE key IN ('ha_url', 'ha_token')"
    ).fetchall()}
    conn.close()
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
        async with outbound.client(timeout=10.0) as client:
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
    """Called by the reminder background loop — no HTTP context."""
    conn = get_db()
    s = {r["key"]: r["value"] for r in conn.execute(
        "SELECT key, value FROM settings WHERE key IN ('ha_url', 'ha_token')"
    ).fetchall()}
    conn.close()
    ha_url = s.get("ha_url", "").strip()
    ha_token = s.get("ha_token", "").strip()
    if not ha_url or not ha_token:
        return
    try:
        async with outbound.client(timeout=10.0) as client:
            await client.post(
                f"{ha_url.rstrip('/')}/api/services/tts/speak",
                headers={"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"},
                json={"entity_id": tts_engine, "media_player_entity_id": entity_id, "message": message},
            )
    except Exception as e:
        print(f"TTS reminder error: {e}")
