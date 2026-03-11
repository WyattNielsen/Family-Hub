#!/bin/bash
# Install Family Hub in a Proxmox LXC (Debian/Ubuntu).
# Run as root from the host, or copy this + repo into the LXC and run there.
# Usage: sudo ./install-lxc.sh [path-to-repo]
#   If path not given, uses the directory containing this script (../).

set -e
REPO="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
INSTALL_DIR="/opt/familyhub"
APP_USER="familyhub"

echo "=== Family Hub LXC install ==="
echo "Repo: $REPO"
echo "Install to: $INSTALL_DIR"
echo ""

# Validate repo layout so we fail fast with a clear message
for f in "$REPO/backend/main.py" "$REPO/backend/requirements.txt" "$REPO/deploy/run.sh" "$REPO/deploy/familyhub.service"; do
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
    apt-get install -y python3 python3-pip python3-venv ca-certificates
elif command -v dnf &>/dev/null; then
    dnf install -y python3 python3-pip
else
    echo "Unsupported distro (need apt-get or dnf). Install Python 3.10+ and venv manually."
    exit 1
fi

# Create user for running the app
if ! id "$APP_USER" &>/dev/null; then
    useradd -r -s /bin/false -d "$INSTALL_DIR" "$APP_USER"
    echo "Created user $APP_USER"
fi

# Create dirs
mkdir -p "$INSTALL_DIR"/{backend,frontend,data/photos}
cp -r "$REPO/backend"/* "$INSTALL_DIR/backend/"
cp -r "$REPO/frontend"/* "$INSTALL_DIR/frontend/"
# Port-configurable run script (reads PORT from .env)
cp "$REPO/deploy/run.sh" "$INSTALL_DIR/backend/run.sh"
chmod +x "$INSTALL_DIR/backend/run.sh"

# Python venv: create only if missing (so upgrades don't recreate from scratch)
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
chmod -R 755 "$INSTALL_DIR/backend" "$INSTALL_DIR/frontend"
chmod -R 775 "$INSTALL_DIR/data"

# Systemd unit
cp "$REPO/deploy/familyhub.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable familyhub
systemctl restart familyhub

echo ""
echo "Family Hub is installed and running."
echo "  App URL: http://<this-host-ip>:${PORT:-3000}  (set PORT in $INSTALL_DIR/.env to change)"
echo "  Config:  $INSTALL_DIR/.env"
echo "  Data:    $INSTALL_DIR/data (DB + photos)"
echo "  Logs:    journalctl -u familyhub -f"
echo "  Stop:    systemctl stop familyhub"
