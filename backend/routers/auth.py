from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database import get_db
import httpx
import os
import urllib.parse
from datetime import datetime, timedelta, timezone

router = APIRouter()

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:3000")

SCOPES = "https://www.googleapis.com/auth/calendar openid email profile"
PHOTOS_SCOPES = "https://www.googleapis.com/auth/photospicker.mediaitems.readonly openid email profile"

def build_oauth_url(redirect_uri: str, state: str) -> str:
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)

@router.get("/google/connect/family")
def connect_family_calendar():
    """Start OAuth flow for the shared family calendar."""
    redirect_uri = f"{APP_BASE_URL}/api/auth/google/callback/family"
    url = build_oauth_url(redirect_uri, state="family")
    return RedirectResponse(url)

@router.get("/google/connect/member/{member_id}")
def connect_member_calendar(member_id: int):
    """Start OAuth flow for an individual family member's calendar."""
    conn = get_db()
    member = conn.execute("SELECT id FROM members WHERE id=?", (member_id,)).fetchone()
    conn.close()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    redirect_uri = f"{APP_BASE_URL}/api/auth/google/callback/member"
    url = build_oauth_url(redirect_uri, state=str(member_id))
    return RedirectResponse(url)

@router.get("/google/callback/family")
async def google_callback_family(code: str = Query(...), state: str = Query(...)):
    """Handle OAuth callback for the family calendar."""
    redirect_uri = f"{APP_BASE_URL}/api/auth/google/callback/family"
    tokens = await _exchange_code(code, redirect_uri)
    email = await _get_google_email(tokens["access_token"])
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=tokens.get("expires_in", 3600))).isoformat()

    conn = get_db()
    existing = conn.execute("SELECT id FROM family_google_calendar LIMIT 1").fetchone()
    if existing:
        conn.execute("""UPDATE family_google_calendar
                       SET google_access_token=?, google_refresh_token=?, google_token_expiry=?, google_email=?, updated_at=datetime('now')""",
                     (tokens["access_token"], tokens.get("refresh_token"), expiry, email))
    else:
        conn.execute("""INSERT INTO family_google_calendar (google_access_token, google_refresh_token, google_token_expiry, google_email)
                       VALUES (?, ?, ?, ?)""",
                     (tokens["access_token"], tokens.get("refresh_token"), expiry, email))
    conn.commit()
    conn.close()
    return RedirectResponse(f"{APP_BASE_URL}/?connected=family")

@router.get("/google/callback/member")
async def google_callback_member(code: str = Query(...), state: str = Query(...)):
    """Handle OAuth callback for a member's personal calendar."""
    member_id = int(state)
    redirect_uri = f"{APP_BASE_URL}/api/auth/google/callback/member"
    tokens = await _exchange_code(code, redirect_uri)
    email = await _get_google_email(tokens["access_token"])
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=tokens.get("expires_in", 3600))).isoformat()

    conn = get_db()
    conn.execute("""UPDATE members
                   SET google_access_token=?, google_refresh_token=?, google_token_expiry=?, google_email=?
                   WHERE id=?""",
                 (tokens["access_token"], tokens.get("refresh_token"), expiry, email, member_id))
    conn.commit()
    conn.close()
    return RedirectResponse(f"{APP_BASE_URL}/settings.html?connected=member&id={member_id}")

@router.get("/google/connect/photos")
def connect_photos():
    """Start OAuth flow for Google Photos Picker."""
    redirect_uri = f"{APP_BASE_URL}/api/auth/google/callback/photos"
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": PHOTOS_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": "photos",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return RedirectResponse(url)

@router.get("/google/callback/photos")
async def google_callback_photos(code: str = Query(...), state: str = Query(...)):
    """Handle OAuth callback for Google Photos."""
    redirect_uri = f"{APP_BASE_URL}/api/auth/google/callback/photos"
    tokens = await _exchange_code(code, redirect_uri)
    email = await _get_google_email(tokens["access_token"])
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=tokens.get("expires_in", 3600))).isoformat()
    conn = get_db()
    existing = conn.execute("SELECT id FROM photos_auth LIMIT 1").fetchone()
    if existing:
        conn.execute("""UPDATE photos_auth
                       SET google_access_token=?, google_refresh_token=?, google_token_expiry=?, google_email=?, updated_at=datetime('now')""",
                     (tokens["access_token"], tokens.get("refresh_token"), expiry, email))
    else:
        conn.execute("""INSERT INTO photos_auth (google_access_token, google_refresh_token, google_token_expiry, google_email)
                       VALUES (?, ?, ?, ?)""",
                     (tokens["access_token"], tokens.get("refresh_token"), expiry, email))
    conn.commit()
    conn.close()
    return RedirectResponse(f"{APP_BASE_URL}/settings.html?connected=photos")

@router.delete("/google/disconnect/photos")
def disconnect_photos():
    conn = get_db()
    conn.execute("DELETE FROM photos_auth")
    conn.execute("DELETE FROM photos_cache")
    conn.commit()
    conn.close()
    return {"ok": True}

@router.get("/google/photos/status")
def get_photos_status():
    conn = get_db()
    auth = conn.execute("SELECT google_email FROM photos_auth LIMIT 1").fetchone()
    count = conn.execute("SELECT COUNT(*) as c FROM photos_cache").fetchone()
    conn.close()
    return {
        "connected": bool(auth),
        "email": auth["google_email"] if auth else None,
        "cached_photos": count["c"] if count else 0
    }

@router.delete("/google/disconnect/family")
def disconnect_family():
    conn = get_db()
    conn.execute("DELETE FROM family_google_calendar")
    conn.commit()
    conn.close()
    return {"ok": True}

@router.delete("/google/disconnect/member/{member_id}")
def disconnect_member(member_id: int):
    conn = get_db()
    conn.execute("""UPDATE members
                   SET google_access_token=NULL, google_refresh_token=NULL,
                       google_token_expiry=NULL, google_email=NULL
                   WHERE id=?""", (member_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

@router.get("/google/status")
def get_google_status():
    """Return connection status for family + all members."""
    conn = get_db()
    family = conn.execute("SELECT google_email FROM family_google_calendar LIMIT 1").fetchone()
    members = conn.execute("SELECT id, name, google_email FROM members").fetchall()
    conn.close()
    return {
        "family": {"connected": bool(family), "email": family["google_email"] if family else None},
        "members": [{"id": m["id"], "name": m["name"], "connected": bool(m["google_email"]), "email": m["google_email"]} for m in members]
    }

async def _exchange_code(code: str, redirect_uri: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post("https://oauth2.googleapis.com/token", data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Token exchange failed: {resp.text}")
        return resp.json()

async def _get_google_email(access_token: str) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://www.googleapis.com/oauth2/v2/userinfo",
                                headers={"Authorization": f"Bearer {access_token}"})
        if resp.status_code == 200:
            return resp.json().get("email", "")
        return ""
