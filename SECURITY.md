# Family Hub — Security Evaluation

## External connections

| Service | Purpose | Auth / API keys | Called from |
|--------|---------|------------------|-------------|
| **Google OAuth & Calendar** | OAuth, Calendar API, token refresh | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (`.env`); tokens in DB | Backend only |
| **Open-Meteo** | Weather | None (free, no key) | Backend only |
| **Zippopotam** | Zip to lat/lon | None (free) | Backend only |
| **Nutrislice (BCPS)** | School lunch menu | None (public API) | Backend only |
| **Home Assistant** | Device states, cover/alarm control | Long-lived token in **settings** table | Backend (token now masked in API; see below) |
| **Google Fonts** | CSS/fonts | None | Frontend (browser) |

All server-side API keys (Google client ID/secret) are read from `.env` and **never** sent to the browser. The app does not expose `.env` or environment variables to the client.

---

## API key / secret leak risk

### High risk (fixed): Settings API returned all keys including secrets

- **Endpoint:** `GET /api/settings/`
- **Previous behavior:** Returned every row from the `settings` table, including `ha_token` and `ha_alarm_code`, so the Home Assistant token and alarm PIN were sent to the browser.
- **Fix applied:** The settings API now masks sensitive values. `ha_token` and `ha_alarm_code` are returned as `••••••••` when set, and are never sent in full to the client. On save, if the client sends the mask (e.g. user did not change the field), the backend keeps the existing value. Sensitive keys are listed in `backend/routers/settings.py` as `SENSITIVE_KEYS`; **add any new API keys or secrets stored in settings to that set** so they are never leaked.

### Low risk: Google credentials (`.env`)

- **Stored in:** `.env` (gitignored).
- **Usage:** Only in backend code (`auth.py`, `calendar.py`). Never sent to the frontend.
- **Conclusion:** Safe from client-side leak as long as `.env` is not committed and the server is not compromised.

### Adding new API keys safely

- **In `.env`:** Safe. Used only server-side; do not include them in any API response or HTML.
- **In settings table (UI-configurable):** Sensitive keys are now masked. When adding new API keys or secrets stored in settings, add them to `SENSITIVE_KEYS` in `backend/routers/settings.py`.

---

## Other security notes

1. **No application authentication**  
   The app has no login. Anyone who can reach the server (e.g. same network or exposed via Nabu Casa/ngrok) can call all APIs. Not returning secrets in API responses limits damage.

2. **CORS**  
   `allow_origins=["*"]` with `allow_credentials=True` can allow other sites to send credentialed requests to your API. For production, set `allow_origins` to the exact origin(s) of your Family Hub frontend.

3. **SQLite / data directory**  
   `data/` is gitignored. The DB and files under `data/` are not served by the app.

4. **Google OAuth redirect URI**  
   Redirect URIs are built from `APP_BASE_URL`. Use HTTPS and correct URLs in production.

---

## Summary

| Item | Status |
|------|--------|
| External connections | Documented above; no unexpected outbound calls. |
| `.env` / server-side API keys | Not exposed to the app frontend. |
| Settings API | **Fixed:** sensitive keys (`ha_token`, `ha_alarm_code`) are masked in responses. |
| Adding API keys in `.env` | Safe if used only server-side. |
| Adding API keys in settings | Add new secret keys to `SENSITIVE_KEYS` in `backend/routers/settings.py`. |
