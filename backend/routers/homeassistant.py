import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import httpx
import asyncio
import json
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


async def fetch_entity(client: httpx.AsyncClient, ha_url: str, token: str, entity: dict) -> dict:
    entity_id = entity["entity_id"]
    name = entity.get("name", entity_id)
    is_alarm = entity_id.startswith("alarm_control_panel.")
    try:
        resp = await client.get(
            f"{ha_url.rstrip('/')}/api/states/{entity_id}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            raw = data.get("state", "unknown")
            if is_alarm:
                icon, css_class, label = alarm_visuals(raw)
                return {
                    "entity_id": entity_id, "name": name,
                    "state": raw, "state_label": label,
                    "icon": icon, "css_class": css_class,
                    "entity_type": "alarm",
                }
            else:
                state = normalize_state(entity_id, raw)
                icon, css_class = state_visuals(entity_id, state)
                return {
                    "entity_id": entity_id, "name": name,
                    "state": state, "state_label": state,
                    "icon": icon, "css_class": css_class,
                    "entity_type": "generic",
                }
        return {"entity_id": entity_id, "name": name, "state": "unavailable", "state_label": "Unavailable",
                "icon": "❓", "css_class": "ha-state-unknown", "entity_type": "alarm" if is_alarm else "generic"}
    except Exception as e:
        print(f"HA fetch error for {entity_id}: {e}")
        return {"entity_id": entity_id, "name": name, "state": "unavailable", "state_label": "Unavailable",
                "icon": "❓", "css_class": "ha-state-unknown", "entity_type": "alarm" if is_alarm else "generic"}


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

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[
            fetch_entity(client, ha_url, ha_token, e) for e in entities
        ])

    return {"entities": list(results)}


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
        async with httpx.AsyncClient(timeout=10.0) as client:
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
        async with httpx.AsyncClient(timeout=10.0) as client:
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
