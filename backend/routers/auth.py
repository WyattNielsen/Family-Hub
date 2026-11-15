from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
import asyncio
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database import get_db, notify_worker
import outbound
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

router = APIRouter()

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:3000")
TOKEN_IMPORT_SECRET = os.environ.get("TOKEN_IMPORT_SECRET", "").strip()

SCOPES = "https://www.googleapis.com/auth/calendar openid email profile"
PHOTOS_SCOPES = "https://www.googleapis.com/auth/photospicker.mediaitems.readonly openid email profile"


class GoogleTokenImport(BaseModel):
    target: Literal["account", "photos"]
    access_token: str
    refresh_token: Optional[str] = None
    expires_in: int = Field(default=3600, ge=1)
    email: Optional[str] = None
    label: Optional[str] = None


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


@router.get("/google/connect/account")
def connect_google_account():
    redirect_uri = f"{APP_BASE_URL}/api/auth/google/callback/account"
    url = build_oauth_url(redirect_uri, state="account")
    return RedirectResponse(url)


@router.get("/google/callback/account")
async def google_callback_account(code: str = Query(...), state: str = Query(...)):
    redirect_uri = f"{APP_BASE_URL}/api/auth/google/callback/account"
    tokens = await _exchange_code(code, redirect_uri)
    email = await _get_google_email(tokens["access_token"])
    account_id = await asyncio.to_thread(
        _store_account_tokens,
        tokens["access_token"],
        tokens.get("refresh_token"),
        tokens.get("expires_in", 3600),
        email,
    )
    notify_worker("sync_all")
    return RedirectResponse(f"{APP_BASE_URL}/settings.html?connected=account&id={account_id}")


@router.get("/google/connect/photos")
def connect_photos():
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
    redirect_uri = f"{APP_BASE_URL}/api/auth/google/callback/photos"
    tokens = await _exchange_code(code, redirect_uri)
    email = await _get_google_email(tokens["access_token"])
    await asyncio.to_thread(
        _store_photos_tokens,
        tokens["access_token"],
        tokens.get("refresh_token"),
        tokens.get("expires_in", 3600),
        email,
    )
    return RedirectResponse(f"{APP_BASE_URL}/settings.html?connected=photos")


@router.post("/google/import")
async def import_google_tokens(
    payload: GoogleTokenImport,
    x_familyhub_import_secret: Optional[str] = Header(default=None),
):
    if not TOKEN_IMPORT_SECRET:
        raise HTTPException(
            status_code=403,
            detail="Token import is disabled. Set TOKEN_IMPORT_SECRET on the server.",
        )
    provided = x_familyhub_import_secret or ""
    secret_ok = (
        len(provided) == len(TOKEN_IMPORT_SECRET)
        and secrets.compare_digest(provided, TOKEN_IMPORT_SECRET)
    )
    if not secret_ok:
        raise HTTPException(status_code=401, detail="Invalid import secret")

    email = (payload.email or "").strip()
    if not email:
        email = await _get_google_email(payload.access_token)

    account_id = None
    if payload.target == "account":
        account_id = await asyncio.to_thread(
            _store_account_tokens,
            payload.access_token,
            payload.refresh_token,
            payload.expires_in,
            email,
            payload.label,
        )
        notify_worker("sync_all")
    else:
        await asyncio.to_thread(
            _store_photos_tokens,
            payload.access_token,
            payload.refresh_token,
            payload.expires_in,
            email,
        )

    return {
        "ok": True,
        "target": payload.target,
        "email": email,
        "account_id": account_id,
    }


@router.delete("/google/disconnect/photos")
def disconnect_photos():
    with get_db() as conn:
        conn.execute("DELETE FROM photos_auth")
        conn.execute("DELETE FROM photos_cache")
        conn.commit()
    return {"ok": True}


@router.get("/google/photos/status")
def get_photos_status():
    with get_db() as conn:
        auth = conn.execute("SELECT google_email FROM photos_auth LIMIT 1").fetchone()
        count = conn.execute("SELECT COUNT(*) as c FROM photos_cache").fetchone()
    return {
        "connected": bool(auth),
        "email": auth["google_email"] if auth else None,
        "cached_photos": count["c"] if count else 0,
    }


def _token_expiry(expires_in: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()


def _store_account_tokens(
    access_token: str,
    refresh_token: Optional[str],
    expires_in: int,
    email: str,
    label: Optional[str] = None,
) -> int:
    expiry = _token_expiry(expires_in)
    email = (email or "").strip()
    with get_db() as conn:
        existing = None
        if email:
            existing = conn.execute(
                "SELECT id FROM calendar_accounts WHERE provider='google' AND email=%s",
                (email,),
            ).fetchone()
        if existing:
            conn.execute(
                """UPDATE calendar_accounts
                   SET access_token=%s, refresh_token=COALESCE(%s, refresh_token),
                       token_expiry=%s, email=%s, label=COALESCE(%s, label),
                       updated_at=NOW()::text
                   WHERE id=%s""",
                (access_token, refresh_token, expiry, email, label, existing["id"]),
            )
            account_id = existing["id"]
        else:
            inserted = conn.execute(
                """INSERT INTO calendar_accounts
                   (provider, email, label, access_token, refresh_token, token_expiry)
                   VALUES ('google', %s, %s, %s, %s, %s)
                   RETURNING id""",
                (email or None, label or email or "Google", access_token, refresh_token, expiry),
            ).fetchone()
            account_id = inserted["id"]
        conn.commit()
    return account_id


def _store_photos_tokens(access_token: str, refresh_token: Optional[str], expires_in: int, email: str) -> None:
    expiry = _token_expiry(expires_in)
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM photos_auth LIMIT 1").fetchone()
        if existing:
            conn.execute(
                """UPDATE photos_auth
                   SET google_access_token=%s, google_refresh_token=COALESCE(%s, google_refresh_token),
                       google_token_expiry=%s, google_email=%s, updated_at=NOW()::text""",
                (access_token, refresh_token, expiry, email),
            )
        else:
            conn.execute(
                """INSERT INTO photos_auth
                   (google_access_token, google_refresh_token, google_token_expiry, google_email)
                   VALUES (%s, %s, %s, %s)""",
                (access_token, refresh_token, expiry, email),
            )
        conn.commit()


async def _exchange_code(code: str, redirect_uri: str) -> dict:
    async with outbound.client(timeout=10.0) as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Token exchange failed: {resp.text}")
        return resp.json()


async def _get_google_email(access_token: str) -> str:
    async with outbound.client(timeout=10.0) as client:
        resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code == 200:
            return resp.json().get("email", "")
        return ""
