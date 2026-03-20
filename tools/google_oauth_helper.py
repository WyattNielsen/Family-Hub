#!/usr/bin/env python3
"""
Local OAuth helper for Family Hub (localhost PKCE flow).

Run this on the admin machine:
  python tools/google_oauth_helper.py

Then open:
  http://127.0.0.1:8765
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse


INDEX_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Family Hub OAuth Helper</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 760px; margin: 32px auto; padding: 0 16px; }
    .card { border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
    label { display: block; margin-top: 10px; font-weight: 600; }
    input, select, button { font-size: 14px; padding: 8px; margin-top: 6px; }
    input, select { width: 100%; box-sizing: border-box; }
    button { cursor: pointer; }
    .row { display: flex; gap: 12px; }
    .row > div { flex: 1; }
    .muted { color: #666; font-size: 13px; }
    .ok { color: #0a7f2e; }
    .err { color: #a00; white-space: pre-wrap; }
    code { background: #f5f5f5; padding: 2px 4px; border-radius: 3px; }
  </style>
</head>
<body>
  <h2>Family Hub Google OAuth Helper</h2>
  <p class="muted">This helper runs on localhost and sends OAuth credentials back to your Family Hub server.</p>

  <div class="card">
    <label for="hubUrl">Family Hub URL</label>
    <input id="hubUrl" placeholder="http://192.168.1.100:3000">

    <div class="row">
      <div>
        <label for="target">Connect Target</label>
        <select id="target">
          <option value="family">Family Calendar</option>
          <option value="member">Member Calendar</option>
        </select>
      </div>
      <div>
        <label for="memberId">Member ID (if member target)</label>
        <input id="memberId" placeholder="e.g. 2">
      </div>
    </div>

    <button id="startBtn">Start Google Sign-In</button>
    <p class="muted">Callback URL used: <code>http://127.0.0.1:8765/callback</code></p>
    <div id="status" class="muted"></div>
  </div>

  <script>
    const REDIRECT_URI = 'http://127.0.0.1:8765/callback';

    const hubInput = document.getElementById('hubUrl');
    const targetInput = document.getElementById('target');
    const memberInput = document.getElementById('memberId');
    const statusEl = document.getElementById('status');
    const startBtn = document.getElementById('startBtn');

    hubInput.value = localStorage.getItem('fh_hub_url') || '';
    targetInput.value = localStorage.getItem('fh_target') || 'family';
    memberInput.value = localStorage.getItem('fh_member_id') || '';

    function setStatus(text, cls='muted') {
      statusEl.className = cls;
      statusEl.textContent = text;
    }

    startBtn.onclick = async () => {
      const hubUrl = hubInput.value.trim().replace(/\\/$/, '');
      const target = targetInput.value;
      const memberIdRaw = memberInput.value.trim();
      const memberId = memberIdRaw ? Number(memberIdRaw) : null;
      if (!hubUrl) {
        setStatus('Family Hub URL is required.', 'err');
        return;
      }
      if (target === 'member' && (!memberId || Number.isNaN(memberId))) {
        setStatus('Member target requires a valid numeric Member ID.', 'err');
        return;
      }

      localStorage.setItem('fh_hub_url', hubUrl);
      localStorage.setItem('fh_target', target);
      localStorage.setItem('fh_member_id', memberIdRaw);

      setStatus('Starting OAuth flow...', 'muted');
      startBtn.disabled = true;
      try {
        const resp = await fetch(hubUrl + '/api/auth/google/pkce/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            target,
            member_id: target === 'member' ? memberId : null,
            redirect_uri: REDIRECT_URI
          })
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || JSON.stringify(data));
        localStorage.setItem('fh_oauth_state', data.state);
        window.location.href = data.auth_url;
      } catch (err) {
        setStatus('Failed to start OAuth: ' + err.message, 'err');
        startBtn.disabled = false;
      }
    };
  </script>
</body>
</html>
"""


CALLBACK_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Family Hub OAuth Callback</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 720px; margin: 32px auto; padding: 0 16px; }
    .ok { color: #0a7f2e; }
    .err { color: #a00; white-space: pre-wrap; }
    .muted { color: #666; }
  </style>
</head>
<body>
  <h2>Completing Google OAuth...</h2>
  <p id="msg" class="muted">Please wait.</p>
  <p><a href="/">Back to helper</a></p>

  <script>
    (async function () {
      const msg = document.getElementById('msg');
      try {
        const params = new URLSearchParams(window.location.search);
        const code = params.get('code');
        const state = params.get('state');
        const error = params.get('error');
        if (error) throw new Error('Google returned error: ' + error);
        if (!code || !state) throw new Error('Missing code/state in callback URL.');

        const hubUrl = (localStorage.getItem('fh_hub_url') || '').trim().replace(/\\/$/, '');
        if (!hubUrl) throw new Error('No Family Hub URL saved. Go back and start again.');

        const resp = await fetch(hubUrl + '/api/auth/google/pkce/exchange', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code, state })
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || JSON.stringify(data));

        msg.className = 'ok';
        msg.textContent = 'Connected successfully: ' + (data.email || '(no email returned)');
      } catch (err) {
        msg.className = 'err';
        msg.textContent = 'OAuth exchange failed: ' + err.message;
      }
    })();
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(INDEX_HTML)
            return
        if parsed.path == "/callback":
            self._send_html(CALLBACK_HTML)
            return
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"Not found")

    def log_message(self, format, *args):
        return

    def _send_html(self, content: str):
        payload = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main():
    addr = ("127.0.0.1", 8765)
    server = HTTPServer(addr, Handler)
    print("Family Hub OAuth helper running at http://127.0.0.1:8765")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
