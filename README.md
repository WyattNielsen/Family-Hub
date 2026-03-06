# 🏠 Family Hub

A self-hosted family calendar and chore manager with Google Calendar sync.

## Features
- 📅 Family calendar with month view
- ✅ Chore tracker with assignments, due dates, recurrence & points
- 🏆 Chore leaderboard for the kids
- 📊 Dashboard with today's events and upcoming chores
- 🔗 Google Calendar sync (shared family + per-member personal calendars)
- 📱 Touch-friendly — works great on iPad, Chromebook, or any browser

## Quick Start

### 1. Configure docker-compose.yaml

Edit `docker-compose.yaml` and fill in:

```yaml
APP_BASE_URL: https://YOUR-SUBDOMAIN.ui.nabu.casa   # Your Nabu Casa URL
GOOGLE_CLIENT_ID: your_client_id_here
GOOGLE_CLIENT_SECRET: your_client_secret_here
```

### 2. Set up Google OAuth (one-time)

1. Go to https://console.cloud.google.com
2. Create a project → APIs & Services → Credentials
3. Create OAuth 2.0 Client ID (Web application)
4. Add these **Authorized redirect URIs**:
   ```
   https://YOUR-SUBDOMAIN.ui.nabu.casa/api/auth/google/callback/family
   https://YOUR-SUBDOMAIN.ui.nabu.casa/api/auth/google/callback/member
   ```
5. Enable the **Google Calendar API** in APIs & Services → Library
6. Copy the Client ID and Secret into docker-compose.yaml

### 3. Build and run

```bash
docker compose up -d --build
```

### 4. Open the app

Navigate to `http://your-ubuntu-ip:3000` (local) or your Nabu Casa URL (remote).

### 5. First-time setup

1. Go to **Settings** → Add your family members
2. Go to **Settings** → Connect Google Calendars
3. Start adding events and chores!

## Updating

```bash
git pull
docker compose up -d --build
```

## Data

All data is stored in `./data/familyhub.db` (SQLite). Back this file up regularly.

## Ports

The app runs on port `3000`. Change the left side of `"3000:3000"` in docker-compose.yaml if needed.
