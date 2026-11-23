from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
import asyncio
import httpx
import json
import time
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import outbound
from database import get_db, get_settings_map_async, invalidate_settings_cache

router = APIRouter()

# Dashboard tiles refresh every 10s. Share one JPEG across screens and serve
# slightly stale frames immediately while refreshing in the background.
_SNAP_TTL = 8.0
_SNAP_STALE_TTL = 60.0
_snap_cache: dict[str, tuple[float, str, bytes]] = {}
_snap_inflight: dict[str, asyncio.Task] = {}
_snap_lock = asyncio.Lock()
_STREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)


async def get_ha_config():
    s = await get_settings_map_async()
    return s.get("ha_url", "").rstrip("/"), s.get("ha_token", "")


@router.get("/camera/{entity_id}/stream")
async def proxy_camera_stream(entity_id: str, request: Request):
    """Proxy HA MJPEG camera stream so the browser never needs the HA token."""
    ha_url, ha_token = await get_ha_config()
    if not ha_url or not ha_token:
        raise HTTPException(status_code=503, detail="Home Assistant not configured")

    stream_url = f"{ha_url}/api/camera_proxy_stream/{entity_id}"

    async def generate():
        try:
            async with outbound.client(timeout=_STREAM_TIMEOUT) as client:
                async with client.stream(
                    "GET",
                    stream_url,
                    headers={"Authorization": f"Bearer {ha_token}"},
                ) as resp:
                    async for chunk in resp.aiter_bytes(8192):
                        if await request.is_disconnected():
                            break
                        yield chunk
        except (asyncio.CancelledError, httpx.HTTPError):
            return

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=--frameboundary",
        headers={"Cache-Control": "no-store"},
    )


def _snapshot_response(media: str, body: bytes) -> Response:
    return Response(
        content=body,
        media_type=media,
        headers={"Cache-Control": "private, max-age=10"},
    )


async def _load_snapshot(entity_id: str):
    ha_url, ha_token = await get_ha_config()
    if not ha_url or not ha_token:
        raise HTTPException(status_code=503, detail="Home Assistant not configured")
    async with outbound.client(timeout=5.0) as client:
        resp = await client.get(
            f"{ha_url}/api/camera_proxy/{entity_id}",
            headers={"Authorization": f"Bearer {ha_token}"},
        )
    if resp.status_code != 200 or not resp.content:
        raise HTTPException(status_code=502, detail="Camera snapshot failed")
    media = resp.headers.get("content-type", "image/jpeg").split(";")[0]
    _snap_cache[entity_id] = (time.monotonic(), media, resp.content)
    return media, resp.content


async def _refresh_snapshot_bg(entity_id: str):
    try:
        await _load_snapshot(entity_id)
    except Exception:
        pass
    finally:
        _snap_inflight.pop(entity_id, None)


@router.get("/camera/{entity_id}/snapshot")
async def proxy_camera_snapshot(entity_id: str):
    """Return a single JPEG snapshot from HA, reused for a few seconds."""
    now = time.monotonic()
    hit = _snap_cache.get(entity_id)
    if hit:
        age = now - hit[0]
        if age < _SNAP_TTL:
            return _snapshot_response(hit[1], hit[2])
        if age < _SNAP_STALE_TTL:
            if entity_id not in _snap_inflight or _snap_inflight[entity_id].done():
                _snap_inflight[entity_id] = asyncio.create_task(_refresh_snapshot_bg(entity_id))
            return _snapshot_response(hit[1], hit[2])

    async with _snap_lock:
        hit = _snap_cache.get(entity_id)
        if hit and time.monotonic() - hit[0] < _SNAP_TTL:
            return _snapshot_response(hit[1], hit[2])
        task = _snap_inflight.get(entity_id)
        if task is None or task.done():
            task = asyncio.create_task(_load_snapshot(entity_id))
            _snap_inflight[entity_id] = task

    try:
        media, body = await task
        return _snapshot_response(media, body)
    except HTTPException:
        raise
    except Exception as e:
        print(f"Camera snapshot error for {entity_id}: {e}")
        raise HTTPException(status_code=502, detail="Camera snapshot failed")
    finally:
        if _snap_inflight.get(entity_id) is task:
            _snap_inflight.pop(entity_id, None)


@router.get("/cameras")
async def get_cameras():
    raw = (await get_settings_map_async()).get("cameras", "")
    cameras = json.loads(raw) if raw else []
    return {"cameras": cameras}


@router.post("/cameras")
def save_cameras(data: dict):
    cameras = data.get("cameras", [])
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('cameras', ?)",
            (json.dumps(cameras),),
        )
        conn.commit()
        invalidate_settings_cache()
        return {"ok": True}
    finally:
        conn.close()
