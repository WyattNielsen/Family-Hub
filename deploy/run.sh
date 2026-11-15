#!/bin/sh
# Wrapper so PORT and WEB_WORKERS from .env are used by uvicorn (systemd does not expand vars in ExecStart).
UVICORN="/opt/familyhub/venv/bin/uvicorn"
if [ ! -x "$UVICORN" ]; then
    echo "Error: $UVICORN not found or not executable. Re-run deploy/install-lxc.sh." >&2
    exit 1
fi
port="${PORT:-3000}"
workers="${WEB_WORKERS:-2}"
exec "$UVICORN" main:app --host 0.0.0.0 --port "$port" --workers "$workers" --timeout-keep-alive 5
