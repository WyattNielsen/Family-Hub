import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi import APIRouter, HTTPException
from database import get_db

router = APIRouter()

# Secrets the client may replace, but must never read back.
WRITE_ONLY_KEYS = frozenset({"ha_token", "ha_alarm_code"})
# Older clients echoed this mask. Never store it, and never treat it as a new secret.
MASK_PLACEHOLDER = "••••••••"


def _is_set(value) -> bool:
    text = "" if value is None else str(value).strip()
    return bool(text) and text != MASK_PLACEHOLDER


def _write_only_flags(stored: dict) -> dict:
    return {f"{key}_set": _is_set(stored.get(key)) for key in WRITE_ONLY_KEYS}


@router.get("/")
def get_settings():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    stored = {r["key"]: r["value"] for r in rows}
    out = {key: value for key, value in stored.items() if key not in WRITE_ONLY_KEYS}
    out.update(_write_only_flags(stored))
    return out


@router.post("/")
def update_settings(payload: dict):
    conn = get_db()
    for key, value in payload.items():
        if key in WRITE_ONLY_KEYS:
            # null clears. Blank or the old mask leaves the stored secret alone,
            # so saving the rest of the form cannot wipe it.
            if value is None:
                conn.execute("DELETE FROM settings WHERE key=?", (key,))
                continue
            text = str(value).strip()
            if not text or text == MASK_PLACEHOLDER:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, text),
            )
            continue
        # Flags are derived. Don't persist them if a client echoes the GET body.
        if key.endswith("_set") and key[: -len("_set")] in WRITE_ONLY_KEYS:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
    conn.commit()
    conn.close()
    return {"ok": True}


@router.get("/{key}")
def get_setting(key: str):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if key in WRITE_ONLY_KEYS:
        value = row["value"] if row else None
        return {"key": key, "set": _is_set(value)}
    if not row:
        raise HTTPException(status_code=404, detail="Setting not found")
    return {"key": key, "value": row["value"]}
