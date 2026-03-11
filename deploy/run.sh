#!/bin/sh
# Wrapper so PORT from .env is used by uvicorn (systemd does not expand vars in ExecStart).
UVICORN="/opt/familyhub/venv/bin/uvicorn"
if [ ! -x "$UVICORN" ]; then
    echo "Error: $UVICORN not found or not executable. Re-run deploy/install-lxc.sh." >&2
    exit 1
fi
port="${PORT:-3000}"
exec "$UVICORN" main:app --host 0.0.0.0 --port "$port"
