from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
import asyncio
import json
import time
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import outbound
from database import get_db

router = APIRouter()

# Dashboard tiles refresh every 10s. Share one JPEG across screens for a bit less than that.
_SNAP_TTL = 8.0
_snap_cache: dict[str, tuple[float, str, bytes]] = {}
_snap_inflight: dict[str, asyncio.Task] = {}
_snap_lock = asyncio.Lock()


def get_ha_config():
    conn = get_db()
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key IN ('ha_url','ha_token')"
    ).fetchall()
    conn.close()
    s = {r["key"]: r["value"] for r in rows}
    return s.get("ha_url", "").rstrip("/"), s.get("ha_token", "")


@router.get("/camera/{entity_id}/stream")
async def proxy_camera_stream(entity_id: str):
    """Proxy HA MJPEG camera stream so the browser never needs the HA token."""
    ha_url, ha_token = get_ha_config()
    if not ha_url or not ha_token:
        raise HTTPException(status_code=503, detail="Home Assistant not configured")

    stream_url = f"{ha_url}/api/camera_proxy_stream/{entity_id}"

    async def generate():
        async with outbound.client(timeout=None) as client:
            async with client.stream(
                "GET",
                stream_url,
                headers={"Authorization": f"Bearer {ha_token}"},
            ) as resp:
                async for chunk in resp.aiter_bytes(4096):
                    yield chunk

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=--frameboundary",
    )


def _snapshot_response(media: str, body: bytes) -> Response:
    return Response(
        content=body,
        media_type=media,
        headers={"Cache-Control": "private, max-age=8"},
    )


async def _load_snapshot(entity_id: str):
    ha_url, ha_token = get_ha_config()
    if not ha_url or not ha_token:
        raise HTTPException(status_code=503, detail="Home Assistant not configured")
    async with outbound.client(timeout=10.0) as client:
        resp = await client.get(
            f"{ha_url}/api/camera_proxy/{entity_id}",
            headers={"Authorization": f"Bearer {ha_token}"},
        )
    if resp.status_code != 200 or not resp.content:
        raise HTTPException(status_code=502, detail="Camera snapshot failed")
    media = resp.headers.get("content-type", "image/jpeg").split(";")[0]
    return media, resp.content


@router.get("/camera/{entity_id}/snapshot")
async def proxy_camera_snapshot(entity_id: str):
    """Return a single JPEG snapshot from HA, reused for a few seconds."""
    now = time.monotonic()
    hit = _snap_cache.get(entity_id)
    if hit and now - hit[0] < _SNAP_TTL:
        return _snapshot_response(hit[1], hit[2])

    async with _snap_lock:
        hit = _snap_cache.get(entity_id)
        if hit and time.monotonic() - hit[0] < _SNAP_TTL:
            return _snapshot_response(hit[1], hit[2])
        task = _snap_inflight.get(entity_id)
        if task is None:
            task = asyncio.create_task(_load_snapshot(entity_id))
            _snap_inflight[entity_id] = task

    try:
        media, body = await task
    except HTTPException:
        raise
    except Exception as e:
        print(f"Camera snapshot error for {entity_id}: {e}")
        raise HTTPException(status_code=502, detail="Camera snapshot failed")
    else:
        _snap_cache[entity_id] = (time.monotonic(), media, body)
        return _snapshot_response(media, body)
    finally:
        if _snap_inflight.get(entity_id) is task:
            _snap_inflight.pop(entity_id, None)


@router.get("/cameras")
async def get_cameras():
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key='cameras'").fetchone()
    conn.close()
    cameras = json.loads(row["value"]) if row and row["value"] else []
    return {"cameras": cameras}


@router.post("/cameras")
async def save_cameras(data: dict):
    cameras = data.get("cameras", [])
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('cameras', ?)",
        (json.dumps(cameras),),
    )
    conn.commit()
    conn.close()
    return {"ok": True}
