# Family Hub

A self-hosted family dashboard with calendar, chores, weather, Home Assistant integration, and photo slideshow.

## Screenshots

| Dashboard | Calendar |
|-----------|----------|
| ![Dashboard](screenshots/screenshot-dashboard.png) | ![Calendar](screenshots/screenshot-calendar.png) |

| Chores | Settings |
|--------|----------|
| ![Chores](screenshots/screenshot-chores.png) | ![Settings](screenshots/screenshot-settings-top.png) |

---

## Features

- Dashboard with today's events, chores due, weather, and home device status
- Family calendar with month view and Google Calendar sync
- Chore tracker with assignments, due dates, recurrence, points, and leaderboard
- Home Assistant integration — display garage doors, locks, sensors, and more
- School lunch menu (BCPS / Nutrislice)
- Photo slideshow screensaver
- Touch-friendly — works great on iPad, Chromebook, or any browser

---

## Quick Start

### 1. Create your `.env` file

Copy the example and fill in your values:

```bash
cp .env.example .env
nano .env
```

**.env contents:**

```env
# The public URL where Family Hub is accessible (used for Google OAuth redirects)
# Use your local IP for local-only access, or your Nabu Casa / ngrok URL for remote access
APP_BASE_URL=http://192.168.1.100:3000

# Google OAuth credentials (see "Set up Google OAuth" below)
GOOGLE_CLIENT_ID=your_client_id_here
GOOGLE_CLIENT_SECRET=your_client_secret_here
```

> **Important:** The `.env` file is gitignored and will never be committed. Keep it safe — it contains your Google OAuth secret.

---

### 2. Set up Google OAuth (one-time)

Required for Google Calendar sync. Skip if you don't need calendar sync.

1. Go to [https://console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (or select an existing one)
3. Go to **APIs & Services → Library** → search for **Google Calendar API** → Enable it
4. Go to **APIs & Services → Credentials** → **Create Credentials → OAuth 2.0 Client ID**
5. Application type: **Web application**
6. Add these **Authorized redirect URIs** (replace with your `APP_BASE_URL`):
   ```
   http://192.168.1.100:3000/api/auth/google/callback/family
   http://192.168.1.100:3000/api/auth/google/callback/member
   ```
7. Copy the **Client ID** and **Client Secret** into your `.env` file

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

After the app is running, go to **Settings** and configure each section:

### Family Members
Add each person in your household. Each member gets a name, color, and avatar emoji. Members appear in the sidebar and can be assigned to chores and calendars.

### Home Assistant
Connect to your local Home Assistant server to display device states on the dashboard (garage doors, locks, sensors, etc.).

1. **Home Assistant URL** — the local address of your HA server, e.g. `http://192.168.1.100:8123`
2. **Long-Lived Access Token** — in Home Assistant: *Profile → Security → Long-Lived Access Tokens → Create Token*
3. **Entities to Monitor** — add entity IDs you want displayed, e.g.:
   - `cover.garage_door` — garage door (open/closed)
   - `binary_sensor.front_door` — door/window sensor
   - `alarm_control_panel.home` — alarm panel
4. **Alarm Code** — optional PIN used to arm/disarm alarm panel entities
5. Click **Save** — devices appear on the dashboard and refresh every 30 seconds

### Timezone
Select your local timezone. This affects all date/time displays throughout the app and the school lunch menu.

### Weather
Enter your US zip code to show current weather conditions on the dashboard.

### Google Calendar
- **Shared Family Calendar** — connect one Google account as the main family calendar. All events from this account appear on the dashboard and calendar page.
- **Write new events to** — choose which Google Calendar new events created in Family Hub get added to.
- After connecting, use the calendar picker to select which of your Google Calendars to sync.

### Photo Slideshow
- Upload photos (JPG, PNG, GIF, WebP) to display as a screensaver
- **Inactivity timeout** — how long before the slideshow starts (default: 2 minutes)
- **Photo interval** — how long each photo is displayed (default: 5 seconds)
- Click the camera button (bottom-right corner of any page) to start the slideshow manually

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
