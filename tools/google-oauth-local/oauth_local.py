#!/usr/bin/env python3
"""
Local Google OAuth helper for Family Hub on Proxmox / LAN.

Google will not accept http://192.168.x.x redirect URIs. Run this on your PC,
authenticate against http://127.0.0.1, then POST the tokens to Family Hub.

Setup (Google Cloud Console → OAuth client → Authorized redirect URIs):
  http://127.0.0.1:8765/callback

On the Family Hub server (.env):
  TOKEN_IMPORT_SECRET=some-long-random-string
  GOOGLE_CLIENT_ID=...
  GOOGLE_CLIENT_SECRET=...

Example:
  python tools/google-oauth-local/oauth_local.py \\
    --hub http://192.168.1.50:3000 \\
    --secret "$(grep TOKEN_IMPORT_SECRET .env | cut -d= -f2)" \\
    --client-id "$GOOGLE_CLIENT_ID" \\
    --client-secret "$GOOGLE_CLIENT_SECRET" \\
    --target family
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

CALENDAR_SCOPES = "https://www.googleapis.com/auth/calendar openid email profile"
PHOTOS_SCOPES = (
    "https://www.googleapis.com/auth/photospicker.mediaitems.readonly openid email profile"
)
DEFAULT_PORT = 8765
CALLBACK_PATH = "/callback"


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def post_json(url: str, payload: dict, secret: str) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-FamilyHub-Import-Secret": secret,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Import failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Could not reach Family Hub at {url}: {exc}") from exc


def exchange_code(code: str, redirect_uri: str, client_id: str, client_secret: str) -> dict:
    data = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Token exchange failed ({exc.code}): {detail}") from exc


def fetch_email(access_token: str) -> str:
    req = urllib.request.Request(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")).get("email", "")
    except urllib.error.HTTPError:
        return ""


class OAuthHandler(http.server.BaseHTTPRequestHandler):
    server_version = "FamilyHubOAuth/1.0"

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_error(404)
            return

        params = urllib.parse.parse_qs(parsed.query)
        if params.get("error"):
            self.server.oauth_error = params["error"][0]
            self._html(400, f"<h1>Google error</h1><p>{self.server.oauth_error}</p>")
            self.server.done.set()
            return

        code = (params.get("code") or [None])[0]
        if not code:
            self.send_error(400, "Missing code")
            return

        self.server.auth_code = code
        self._html(
            200,
            "<h1>Google sign-in complete</h1>"
            "<p>You can close this tab. Tokens are being sent to Family Hub…</p>",
        )
        self.server.done.set()

    def _html(self, status: int, body: str) -> None:
        content = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Family Hub OAuth</title></head><body>"
            f"{body}</body></html>"
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def run_oauth(port: int, auth_url: str) -> str:
    server = http.server.HTTPServer(("127.0.0.1", port), OAuthHandler)
    server.auth_code = None
    server.oauth_error = None
    server.done = threading.Event()

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print(f"Listening for Google callback on http://127.0.0.1:{port}{CALLBACK_PATH}")
    print("Opening browser…")
    if not webbrowser.open(auth_url):
        print("Open this URL manually:\n", auth_url)

    if not server.done.wait(timeout=300):
        server.server_close()
        raise SystemExit("Timed out waiting for Google sign-in (5 minutes).")

    server.server_close()
    if server.oauth_error:
        raise SystemExit(f"Google OAuth error: {server.oauth_error}")
    if not server.auth_code:
        raise SystemExit("No authorization code received.")
    return server.auth_code


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[2]
    load_dotenv(repo_root / ".env")

    parser = argparse.ArgumentParser(
        description="Authenticate with Google locally and push tokens to Family Hub."
    )
    parser.add_argument(
        "--hub",
        default=os.environ.get("FAMILY_HUB_URL") or os.environ.get("APP_BASE_URL"),
        help="Family Hub base URL (e.g. http://192.168.1.50:3000)",
    )
    parser.add_argument(
        "--secret",
        default=os.environ.get("TOKEN_IMPORT_SECRET"),
        help="Must match TOKEN_IMPORT_SECRET on the Family Hub server",
    )
    parser.add_argument(
        "--client-id",
        default=os.environ.get("GOOGLE_CLIENT_ID"),
        help="Google OAuth client ID",
    )
    parser.add_argument(
        "--client-secret",
        default=os.environ.get("GOOGLE_CLIENT_SECRET"),
        help="Google OAuth client secret",
    )
    parser.add_argument(
        "--target",
        choices=("family", "member", "photos"),
        default="family",
        help="Where to store the tokens on Family Hub",
    )
    parser.add_argument(
        "--member-id",
        type=int,
        default=None,
        help="Required when --target member",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Local callback port (default {DEFAULT_PORT})",
    )
    args = parser.parse_args()

    missing = [
        name
        for name, val in (
            ("--hub / FAMILY_HUB_URL", args.hub),
            ("--secret / TOKEN_IMPORT_SECRET", args.secret),
            ("--client-id / GOOGLE_CLIENT_ID", args.client_id),
            ("--client-secret / GOOGLE_CLIENT_SECRET", args.client_secret),
        )
        if not val
    ]
    if missing:
        parser.error("Missing required values: " + ", ".join(missing))
    if args.target == "member" and args.member_id is None:
        parser.error("--member-id is required when --target member")
    return args


def main() -> None:
    args = parse_args()
    hub = args.hub.rstrip("/")
    redirect_uri = f"http://127.0.0.1:{args.port}{CALLBACK_PATH}"
    scopes = PHOTOS_SCOPES if args.target == "photos" else CALENDAR_SCOPES

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        {
            "client_id": args.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scopes,
            "access_type": "offline",
            "prompt": "consent",
            "state": args.target,
        }
    )

    print(f"Target: {args.target}" + (f" (member_id={args.member_id})" if args.member_id else ""))
    print(f"Hub:    {hub}")
    print(f"Add this Authorized redirect URI in Google Cloud Console if needed:")
    print(f"  {redirect_uri}")
    print()

    code = run_oauth(args.port, auth_url)
    tokens = exchange_code(code, redirect_uri, args.client_id, args.client_secret)
    access_token = tokens.get("access_token")
    if not access_token:
        raise SystemExit(f"No access_token in Google response: {tokens}")

    email = fetch_email(access_token)
    payload = {
        "target": args.target,
        "access_token": access_token,
        "refresh_token": tokens.get("refresh_token"),
        "expires_in": int(tokens.get("expires_in", 3600)),
        "email": email,
        "member_id": args.member_id,
    }
    result = post_json(f"{hub}/api/auth/google/import", payload, args.secret)
    print("Success:", json.dumps(result, indent=2))
    print("Open Family Hub → Settings to confirm the Google account is connected.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
