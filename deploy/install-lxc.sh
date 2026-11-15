#!/bin/bash
# Install Family Hub in a Proxmox LXC (Debian/Ubuntu).
# Run as root from the host, or copy this + repo into the LXC and run there.
# Usage: sudo ./install-lxc.sh [path-to-repo]
#   If path not given, uses the directory containing this script (../).

set -e
REPO="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
INSTALL_DIR="/opt/familyhub"
APP_USER="familyhub"
GO2RTC_VERSION="v1.9.9"

echo "=== Family Hub LXC install ==="
echo "Repo: $REPO"
echo "Install to: $INSTALL_DIR"
echo ""

# Validate repo layout so we fail fast with a clear message
for f in "$REPO/backend/main.py" "$REPO/backend/worker.py" "$REPO/backend/requirements.txt" "$REPO/deploy/run.sh" "$REPO/deploy/familyhub.service" "$REPO/deploy/familyhub-worker.service" "$REPO/deploy/familyhub-go2rtc.service"; do
    if [ ! -f "$f" ]; then
        echo "Error: missing required file: $f"
        exit 1
    fi
done
if [ ! -d "$REPO/frontend" ]; then
    echo "Error: missing directory: $REPO/frontend"
    exit 1
fi

# Install system packages (Debian/Ubuntu)
if command -v apt-get &>/dev/null; then
    apt-get update
    apt-get install -y python3 python3-pip python3-venv ca-certificates curl postgresql postgresql-contrib
elif command -v dnf &>/dev/null; then
    dnf install -y python3 python3-pip curl postgresql-server postgresql-contrib
    postgresql-setup --initdb || true
else
    echo "Unsupported distro (need apt-get or dnf). Install Python 3.10+, PostgreSQL, curl, and venv manually."
    exit 1
fi

systemctl enable postgresql
systemctl start postgresql

# Ensure PostgreSQL role & database exist
su - postgres -c "psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='familyhub'\" | grep -q 1 || psql -c \"CREATE ROLE familyhub WITH LOGIN PASSWORD 'familyhub';\""
su - postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname='familyhub'\" | grep -q 1 || psql -c \"CREATE DATABASE familyhub OWNER familyhub;\""

# Create user for running the app
if ! id "$APP_USER" &>/dev/null; then
    useradd -r -s /bin/false -d "$INSTALL_DIR" "$APP_USER"
    echo "Created user $APP_USER"
fi

# Create dirs
mkdir -p "$INSTALL_DIR"/{backend,frontend,bin,data/photos,data/avatars,data/go2rtc}
cp -r "$REPO/backend"/* "$INSTALL_DIR/backend/"
cp -r "$REPO/frontend"/* "$INSTALL_DIR/frontend/"
cp "$REPO/deploy/run.sh" "$INSTALL_DIR/backend/run.sh"
chmod +x "$INSTALL_DIR/backend/run.sh"

# Install go2rtc binary if missing
if [ ! -x "$INSTALL_DIR/bin/go2rtc" ]; then
    ARCH="$(uname -m)"
    case "$ARCH" in
        x86_64|amd64) GO2RTC_ASSET="go2rtc_linux_amd64" ;;
        aarch64|arm64) GO2RTC_ASSET="go2rtc_linux_arm64" ;;
        armv7l|armhf) GO2RTC_ASSET="go2rtc_linux_arm" ;;
        *) GO2RTC_ASSET="go2rtc_linux_amd64" ;;
    esac
    echo "Downloading go2rtc ($GO2RTC_ASSET)..."
    curl -fsSL "https://github.com/AlexxIT/go2rtc/releases/download/${GO2RTC_VERSION}/${GO2RTC_ASSET}" -o "$INSTALL_DIR/bin/go2rtc"
    chmod +x "$INSTALL_DIR/bin/go2rtc"
fi

if [ ! -f "$INSTALL_DIR/data/go2rtc/go2rtc.yaml" ]; then
    cat > "$INSTALL_DIR/data/go2rtc/go2rtc.yaml" <<'EOF'
api:
  listen: 127.0.0.1:1984
webrtc:
  listen: ":8555"
  candidates:
    - stun:8555
streams: {}
EOF
fi

# Python venv: create only if missing
if [ ! -f "$INSTALL_DIR/venv/bin/python3" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
    "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
fi
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/backend/requirements.txt"

# .env: copy example if no .env exists
if [ ! -f "$INSTALL_DIR/.env" ]; then
    if [ -f "$REPO/.env.example" ]; then
        cp "$REPO/.env.example" "$INSTALL_DIR/.env"
        echo "Created $INSTALL_DIR/.env from .env.example — edit with your Google OAuth and APP_BASE_URL."
    else
        touch "$INSTALL_DIR/.env"
        echo "Created empty $INSTALL_DIR/.env — add APP_BASE_URL, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET."
    fi
fi

# Permissions
chown -R "$APP_USER:$APP_USER" "$INSTALL_DIR"
chmod 700 "$INSTALL_DIR/.env"
chmod -R 755 "$INSTALL_DIR/backend" "$INSTALL_DIR/frontend" "$INSTALL_DIR/bin"
chmod -R 775 "$INSTALL_DIR/data"

# Systemd units
cp "$REPO/deploy/familyhub-go2rtc.service" /etc/systemd/system/
cp "$REPO/deploy/familyhub.service" /etc/systemd/system/
cp "$REPO/deploy/familyhub-worker.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable familyhub-go2rtc familyhub familyhub-worker
systemctl restart familyhub-go2rtc familyhub familyhub-worker

echo ""
echo "Family Hub is installed and running (PostgreSQL + Web + Worker + go2rtc)."
echo "  App URL: http://<this-host-ip>:${PORT:-3000}  (set PORT in $INSTALL_DIR/.env to change)"
echo "  Config:  $INSTALL_DIR/.env"
echo "  Data:    $INSTALL_DIR/data (photos + avatars + go2rtc) & PostgreSQL (familyhub)"
echo "  Logs:    journalctl -u familyhub -u familyhub-worker -u familyhub-go2rtc -f"
echo "  Stop:    systemctl stop familyhub familyhub-worker familyhub-go2rtc"
