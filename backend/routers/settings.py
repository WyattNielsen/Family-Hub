import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi import APIRouter, HTTPException
from database import get_db

router = APIRouter()

# Keys whose values must never be sent to the client (avoid leaking tokens/PINs).
SENSITIVE_KEYS = frozenset({"ha_token", "ha_alarm_code"})
MASK_PLACEHOLDER = "••••••••"

@router.get("/")
def get_settings():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    out = {}
    for r in rows:
        key, value = r["key"], r["value"]
        if key in SENSITIVE_KEYS and (value or "").strip():
            out[key] = MASK_PLACEHOLDER
        else:
            out[key] = value
    return out

@router.post("/")
def update_settings(payload: dict):
    conn = get_db()
    for key, value in payload.items():
        if key in SENSITIVE_KEYS and str(value).strip() == MASK_PLACEHOLDER:
            # Client sent the mask: keep existing value, do not overwrite.
            continue
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()
    return {"ok": True}

@router.get("/{key}")
def get_setting(key: str):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Setting not found")
    value = row["value"]
    if key in SENSITIVE_KEYS and (value or "").strip():
        value = MASK_PLACEHOLDER
    return {"key": key, "value": value}
