# Family Hub

A self-hosted family dashboard built for a wall-mounted touchscreen. Combines calendar, chores, messages, security cameras, weather, Home Assistant integration, and more — all in one polished interface your whole family can use.

## Screenshots

| Dashboard | Chores |
|-----------|--------|
| ![Dashboard](screenshots/screenshot-dashboard.png) | ![Chores](screenshots/screenshot-chores.png) |

| Messages | Security |
|----------|----------|
| ![Messages](screenshots/screenshot-messages.png) | ![Security](screenshots/screenshot-security.png) |

---

## Features

### Dashboard
- Greeting with time of day, date, and rotating family quote
- **Up Next** — upcoming calendar events
- **Chores Today** — today's chore assignments; shows streak leaderboard when all done
- **Lunch Menu** — school lunch pulled from Nutrislice (BCPS and others); shows tomorrow after 5 PM
- **Message preview pill** — latest family note displayed above the grid; ✏️ button to post directly without leaving the dashboard
- Live Home Assistant status badges (garage, alarm) in the header
- Weather widget — current conditions + tomorrow's forecast (high/low + precipitation %)
- Stock ticker (slim banner, configurable symbols)
- Auto-refreshes every 5 minutes

### Family Messages Board
- Leave quick notes for the family (e.g. "I'm at practice, home by 6")
- Color-coded per person with avatar
- Unread badge on the nav link — clears when you visit the page
- Quick-compose from the dashboard without navigating away
- Messages auto-expire after 7 days

### Calendar
- Month view with color-coded event dots per family member
- **Tap any event** to see a detail popover — title, time, location (tappable → Google Maps), description, person tag, delete button
- Tap a day to see all events in a side panel
- Shared Google & iCloud accounts — add calendars into a pool, assign each to a person
- ICS / webcal feeds (school, sports, etc.) alongside account calendars
- Location field synced from Google Calendar automatically
- Add events for a selected person — writes to that person's configured write calendar

### Chores
- Daily chore assignments per family member with morning/afternoon/evening grouping
- Points system with Chore Champions leaderboard
- **Day streaks** — consecutive days of completed chores, shown on dashboard
- Recurring chores (daily, weekly, custom days)
- Color-coded cards with each person's color

### Security
- **Camera feeds** — live MJPEG streams via Home Assistant proxy (no go2rtc required)
- Fullscreen camera modal with prev/next arrows, dot indicators, keyboard arrow keys, and swipe gestures
- **Garage doors** — open/close control with live state
- **Alarm panel** — arm home, arm away, disarm with optional PIN

### Night Mode & Auto-Dim (wall-mount friendly)
- **Dark mode** — automatically switches after sunset (default 9 PM–7 AM)
- **Auto-dim** — screen goes nearly black after inactivity (default 5 min); any touch restores it
- Both configurable in Settings → General → Night Mode

### PWA — Install on Tablet
The app ships with a Web App Manifest so you can install it to your tablet's home screen:
- On iPad/iPhone: Safari → Share → Add to Home Screen
- On Android: Chrome → ⋮ → Add to Home Screen / Install App

Launches full-screen in landscape with no browser chrome.

### Settings
Organized into 4 tabs:
- **Family** — members (name, color, avatar) + shared calendar accounts / sources
- **Home** — Home Assistant URL/token/entities + camera feeds
- **Integrations** — Weather zip, lunch school, stock symbols, slideshow
- **General** — Timezone + Night Mode (dark hours + auto-dim timeout)

---

## Quick Start

### 1. Create your `.env` file

```bash
cp .env.example .env
nano .env
```

**.env contents:**

```env
# The public URL where Family Hub is accessible (used for Google OAuth redirects)
APP_BASE_URL=http://192.168.1.100:3000

# Google OAuth credentials (see "Set up Google OAuth" below)
GOOGLE_CLIENT_ID=your_client_id_here
GOOGLE_CLIENT_SECRET=your_client_secret_here
```

> **Important:** The `.env` file is gitignored and will never be committed.

---

### 2. Set up Google OAuth (one-time, optional)

Required only for Google Calendar sync.

1. Go to [https://console.cloud.google.com](https://console.cloud.google.com)
2. Create a project → enable the **Google Calendar API**
3. Create **OAuth 2.0 Client ID** credentials (Web application type)
4. Copy the Client ID and Secret into your `.env`
5. Set a `TOKEN_IMPORT_SECRET` in `.env` (see `.env.example`)

**Proxmox / LAN note:** Google rejects most `http://192.168.x.x` redirect URIs. Prefer the local helper:

1. In Google Cloud Console, add this **Authorized redirect URI**:
   ```
   http://127.0.0.1:8765/callback
   ```
2. On your PC (with the same Google client ID/secret and the server’s `TOKEN_IMPORT_SECRET`):
   ```bash
   python tools/google-oauth-local/oauth_local.py \
     --hub http://192.168.1.50:3000 \
     --target account
   ```
   Use `--target account` for shared calendar accounts (Settings → Family Calendar), or `--target photos` for Photos.

If you have a public HTTPS `APP_BASE_URL`, you can instead register the in-app callback and use **Connect Google Account** in Settings:
```
https://your-host/api/auth/google/callback/account
```

---

### 3. Build and run

**Option A — Docker (any host):**

```bash
docker compose up -d --build
```

**Option B — Single Proxmox LXC (no Docker):**

See [Running in a Proxmox LXC](#running-in-a-proxmox-lxc) below.

### 4. Open the app

Navigate to `http://your-ip:3000` (local) or your `APP_BASE_URL` (remote).

---

## Running in a Proxmox LXC

You can run Family Hub in a single Proxmox LXC (Debian or Ubuntu) without Docker. The app runs under systemd with a dedicated user and stores all data under `/opt/familyhub/data`.

### 1. Create the LXC

In Proxmox: Create CT → choose a Debian 12 or Ubuntu 22/24 template → set root password and (optionally) static IP. Start the container and open a shell.

### 2. Get the repo into the container

From your workstation (or Proxmox host), either clone into the CT or copy the repo:

```bash
# From inside the LXC (if it has internet and git):
git clone https://github.com/your-repo/Family-Hub.git /tmp/Family-Hub
```

Or copy the repo from your machine:

```bash
# From your machine (adjust CT id and path):
pct push 100 /path/to/Family-Hub /tmp/Family-Hub --recursive
```

### 3. Install and run

Inside the LXC, as root:

```bash
cd /tmp/Family-Hub   # or wherever you put the repo
chmod +x deploy/install-lxc.sh
./deploy/install-lxc.sh
```

The script installs Python, copies the app to `/opt/familyhub`, creates a `familyhub` user, sets up a venv, and enables a systemd service. If `/opt/familyhub/.env` does not exist, it is created from `.env.example` — edit it with your `APP_BASE_URL` and Google OAuth credentials:

```bash
nano /opt/familyhub/.env
```

Then restart the service:

```bash
systemctl restart familyhub
```

### 4. Open the app

From a browser, go to `http://<lxc-ip>:3000` (or whatever port you set). Set **`PORT`** in `/opt/familyhub/.env` to change the port (e.g. `PORT=8080`). Use the same port in **`APP_BASE_URL`** for Google OAuth (e.g. `http://192.168.1.50:3000`).

### LXC paths and commands

| Item   | Path or command |
|--------|------------------|
| App    | `/opt/familyhub/` (backend, frontend, venv) |
| Data   | `/opt/familyhub/data/` (SQLite DB + photos) |
| Config | `/opt/familyhub/.env` |
| Logs   | `journalctl -u familyhub -f` |
| Restart| `systemctl restart familyhub` |

### Updating (LXC)

Pull or copy the new code, then re-run the install script (it overwrites app files but keeps `/opt/familyhub/data` and `.env`):

```bash
cd /tmp/Family-Hub && git pull
./deploy/install-lxc.sh
```

The script uses `systemctl restart familyhub` so the new code is loaded. The venv is reused and only dependencies are updated.

### Troubleshooting (LXC)

- **Service won’t start:** Run `systemctl status familyhub` and `journalctl -u familyhub -n 50`. If you see “uvicorn not found”, re-run `./deploy/install-lxc.sh` so the venv and `run.sh` are correct.
- **Start limit hit:** After 5 restarts in 2 minutes, systemd stops retrying. Fix the cause (e.g. bad `.env`, missing data dir), then run `systemctl reset-failed familyhub` and `systemctl start familyhub`.
- **Permission errors:** Ensure `/opt/familyhub` is owned by `familyhub:familyhub` and that `/opt/familyhub/data` is writable (`chown -R familyhub:familyhub /opt/familyhub`).

---

## First-Time Setup (In-App Settings)

### Family Members
Add each person — name, color, avatar emoji. They appear in the sidebar and can be assigned to chores and calendars.

### Home Assistant
1. **HA URL** — e.g. `http://192.168.1.100:8123`
2. **Long-Lived Access Token** — Home Assistant → Profile → Security → Long-Lived Access Tokens
3. **Entities** — add entity IDs to monitor:
   - `cover.garage_door` — garage door
   - `alarm_control_panel.home` — alarm panel
4. **Cameras** — add HA camera entity IDs (e.g. `camera.driveway`) for live feeds on the Security page

### Night Mode
In **Settings → General → Night Mode**:
- Set dark mode start/end hours (default 9 PM – 7 AM)
- Set auto-dim timeout (default 5 minutes of inactivity)

### Timezone
Select your local timezone — affects all date/time displays and the lunch menu.

### Weather
Enter your US zip code for current conditions and tomorrow's forecast on the dashboard.

### Google & Apple Calendar
In **Settings → Family → Family Calendar**:

1. **Accounts** — Connect Google (`Connect Google Account`, or local OAuth with `--target account`) and/or iCloud ([app-specific password](https://appleid.apple.com))
2. **Calendars** — Browse calendars from each account (or paste an ICS/webcal URL) into the shared pool, then assign each to a person or to **Family** (holidays, birthdays, shared events — visible for everyone)
3. **Write calendars** — Pick the Family write calendar plus each person's write calendar for new events created in Family Hub

Event locations sync from Google when present. Rebuild the Docker image after frontend changes so Settings picks up the new UI.

### School Lunch Menu
Enter your Nutrislice school domain (e.g. `bcps`) in Settings → Integrations → Lunch.

### Photo Slideshow
- Upload photos (JPG, PNG, GIF, WebP)
- Set inactivity timeout and photo interval
- Click the 📷 button (bottom-right) to start manually

---

## Updating

**Docker:** `git pull` then `docker compose up -d --build`.

**LXC:** See [Updating (LXC)](#updating-lxc) in the LXC section.

---

## Data & Backups

- **Docker:** Data is in `./data/familyhub.db` and `./data/photos/`.
- **LXC:** Data is in `/opt/familyhub/data/familyhub.db` and `/opt/familyhub/data/photos/`.

Back up the SQLite file and photos folder regularly.

---

## Ports

The app runs on port **3000** by default. To change it:
- **Docker:** Edit the port mapping in `docker-compose.yaml` (e.g. `"8080:3000"` to expose host 8080).
- **LXC:** Set **`PORT=8080`** (or your choice) in `/opt/familyhub/.env`, then `systemctl restart familyhub`. Keep **`APP_BASE_URL`** in sync (e.g. `http://192.168.1.50:8080`).
