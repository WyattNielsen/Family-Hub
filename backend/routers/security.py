from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
import asyncio
import base64
import httpx
import ipaddress
import json
import socket
import time
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database import (
    get_db,
    get_settings_map_async,
    get_snapshot_async,
    invalidate_settings_cache,
    notify_worker,
    notify_worker_async,
    set_snapshot_async,
    upsert_setting_sync,
)
import go2rtc_sync

router = APIRouter()

# Dedicated HTTP client strictly for camera traffic so slow cameras never touch API pools
_cam_client: httpx.AsyncClient | None = None
_STREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
_SNAP_TTL = 10.0
_snap_ram: dict[str, tuple[float, str, bytes]] = {}
_snap_inflight: dict[str, asyncio.Task] = {}


def _camera_http() -> httpx.AsyncClient:
    global _cam_client
    if _cam_client is None or _cam_client.is_closed:
        _cam_client = httpx.AsyncClient(
            timeout=8.0,
            follow_redirects=True,
            trust_env=False,
            limits=httpx.Limits(max_connections=12, max_keepalive_connections=6),
        )
    return _cam_client


async def close_camera_http():
    global _cam_client
    if _cam_client is not None and not _cam_client.is_closed:
        await _cam_client.aclose()
    _cam_client = None


class WebRTCOffer(BaseModel):
    sdp: str
    type: str = "offer"


async def _get_ha_config() -> tuple[str, str]:
    s = await get_settings_map_async()
    return s.get("ha_url", "").rstrip("/"), s.get("ha_token", "")


def _resolve_request_ip(host_header: str) -> str:
    host = (host_header or "").split(":", 1)[0].strip("[] ")
    if not host or host == "localhost":
        return "127.0.0.1"
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        try:
            return socket.gethostbyname(host)
        except Exception:
            return ""


def _inject_lan_candidates(sdp: str, target_ip: str) -> str:
    """Ensure Docker bridge go2rtc SDP answers also advertise the host IP the browser connected to."""
    if not target_ip or target_ip in sdp:
        return sdp
    lines = sdp.splitlines()
    out: list[str] = []
    extra_idx = 900
    for line in lines:
        out.append(line)
        # Match go2rtc host candidates on port 8555:
        # a=candidate:<foundation> <comp> <proto> <prio> <ip> 8555 typ host...
        if line.startswith("a=candidate:") and " 8555 typ host" in line:
            parts = line.split()
            if len(parts) >= 8 and parts[5] == "8555":
                extra_idx += 1
                comp = parts[1]
                proto = parts[2]
                prio = parts[3]
                rest = " ".join(parts[6:])
                out.append(f"a=candidate:{extra_idx} {comp} {proto} {prio} {target_ip} 8555 {rest}")
    return "\r\n".join(out) + "\r\n"


@router.post("/camera/{entity_id}/webrtc")
async def camera_webrtc_offer(entity_id: str, offer: WebRTCOffer, request: Request):
    """Exchange WebRTC SDP offer/answer with go2rtc (~50ms HTTP, then pure UDP/TCP :8555 media)."""
    s = await get_settings_map_async()
    raw = s.get("cameras", "")
    cameras = json.loads(raw) if raw else []
    cam = next(
        (c for c in cameras if c.get("ha_entity_id") == entity_id or go2rtc_sync.stream_key_for_camera(c) == entity_id),
        {"ha_entity_id": entity_id, "name": entity_id},
    )
    stream_key = go2rtc_sync.stream_key_for_camera(cam)
    req_ip = _resolve_request_ip(request.headers.get("host", ""))

    client = _camera_http()
    webrtc_url = f"{go2rtc_sync.GO2RTC_URL}/api/webrtc"

    async def _post_offer() -> httpx.Response:
        return await client.post(
            webrtc_url,
            params={"src": stream_key},
            content=offer.sdp.encode("utf-8"),
            headers={"Content-Type": "application/sdp"},
            timeout=8.0,
        )

    try:
        resp = await _post_offer()
        # If stream wasn't registered yet in go2rtc (e.g. go2rtc container restarted), sync & retry once
        if resp.status_code in (400, 404, 500):
            await go2rtc_sync.sync_go2rtc_streams(extra_host=req_ip)
            resp = await _post_offer()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"go2rtc unreachable: {exc}")

    if resp.status_code not in (200, 201) or not resp.text.strip():
        raise HTTPException(status_code=502, detail=f"WebRTC negotiation failed ({resp.status_code})")

    text = resp.text
    # go2rtc returns raw SDP for application/sdp or JSON for application/json
    if text.lstrip().startswith("{"):
        data = resp.json()
        answer_sdp = data.get("sdp") or ""
    else:
        answer_sdp = text

    if not answer_sdp:
        raise HTTPException(status_code=502, detail="Empty SDP answer from go2rtc")

    answer_sdp = _inject_lan_candidates(answer_sdp, req_ip)
    return {"type": "answer", "sdp": answer_sdp, "stream_key": stream_key}


@router.get("/camera/{entity_id}/stream")
async def proxy_camera_stream(entity_id: str, request: Request):
    """Legacy MJPEG fallback on isolated camera HTTP client."""
    ha_url, ha_token = await _get_ha_config()
    if not ha_url or not ha_token:
        raise HTTPException(status_code=503, detail="Home Assistant not configured")

    stream_url = f"{ha_url}/api/camera_proxy_stream/{entity_id}"
    client = _camera_http()

    async def generate():
        try:
            async with client.stream(
                "GET",
                stream_url,
                headers={"Authorization": f"Bearer {ha_token}"},
                timeout=_STREAM_TIMEOUT,
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


async def _fetch_snapshot_live(entity_id: str) -> tuple[str, bytes] | None:
    """Fetch a JPEG frame from go2rtc (fastest, reuses warm stream) or Home Assistant."""
    s = await get_settings_map_async()
    raw = s.get("cameras", "")
    cameras = json.loads(raw) if raw else []
    cam = next((c for c in cameras if c.get("ha_entity_id") == entity_id), None)
    client = _camera_http()

    # 1. If direct RTSP/stream_url is configured, try go2rtc's frame.jpeg endpoint first
    if cam and (cam.get("stream_url") or cam.get("rtsp_url")):
        skey = go2rtc_sync.stream_key_for_camera(cam)
        try:
            r = await client.get(f"{go2rtc_sync.GO2RTC_URL}/api/frame.jpeg", params={"src": skey}, timeout=4.0)
            if r.status_code == 200 and r.content:
                return "image/jpeg", r.content
        except Exception:
            pass

    # 2. Home Assistant camera_proxy snapshot
    ha_url, ha_token = await _get_ha_config()
    if not ha_url or not ha_token:
        return None
    try:
        resp = await client.get(
            f"{ha_url}/api/camera_proxy/{entity_id}",
            headers={"Authorization": f"Bearer {ha_token}"},
            timeout=5.0,
        )
        if resp.status_code == 200 and resp.content:
            media = resp.headers.get("content-type", "image/jpeg").split(";")[0]
            return media, resp.content
    except Exception as exc:
        print(f"Camera snapshot fetch error ({entity_id}): {exc}")
    return None


async def _refresh_single_snapshot(entity_id: str):
    try:
        res = await _fetch_snapshot_live(entity_id)
        if res:
            media, body = res
            _snap_ram[entity_id] = (time.monotonic(), media, body)
            await set_snapshot_async(
                f"cam_snap:{entity_id}",
                {"media": media, "b64": base64.b64encode(body).decode("ascii")},
                error=None,
            )
    finally:
        _snap_inflight.pop(entity_id, None)


async def refresh_camera_snapshots():
    """Pre-warm snapshots for configured cameras in worker.py."""
    s = await get_settings_map_async()
    raw = s.get("cameras", "")
    try:
        cameras = json.loads(raw) if raw else []
    except Exception:
        cameras = []
    for cam in cameras[:6]:
        eid = (cam.get("ha_entity_id") or "").strip()
        if eid:
            await _refresh_single_snapshot(eid)


@router.get("/camera/{entity_id}/snapshot")
async def proxy_camera_snapshot(entity_id: str):
    """Serve cached JPEG snapshot immediately in <3ms; refresh asynchronously when stale."""
    now = time.monotonic()
    hit = _snap_ram.get(entity_id)
    if hit:
        age = now - hit[0]
        if age >= _SNAP_TTL and (entity_id not in _snap_inflight or _snap_inflight[entity_id].done()):
            _snap_inflight[entity_id] = asyncio.create_task(_refresh_single_snapshot(entity_id))
        return _snapshot_response(hit[1], hit[2])

    # Check shared PostgreSQL snapshot written by worker / other uvicorn worker
    db_snap = await get_snapshot_async(f"cam_snap:{entity_id}")
    if db_snap and isinstance(db_snap[0], dict) and db_snap[0].get("b64"):
        media = db_snap[0].get("media") or "image/jpeg"
        body = base64.b64decode(db_snap[0]["b64"])
        _snap_ram[entity_id] = (now, media, body)
        if time.time() - db_snap[1] >= _SNAP_TTL:
            if entity_id not in _snap_inflight or _snap_inflight[entity_id].done():
                _snap_inflight[entity_id] = asyncio.create_task(_refresh_single_snapshot(entity_id))
        return _snapshot_response(media, body)

    # First ever request for this camera: fetch once and cache
    res = await _fetch_snapshot_live(entity_id)
    if not res:
        raise HTTPException(status_code=502, detail="Camera snapshot unavailable")
    media, body = res
    _snap_ram[entity_id] = (time.monotonic(), media, body)
    asyncio.create_task(
        set_snapshot_async(
            f"cam_snap:{entity_id}",
            {"media": media, "b64": base64.b64encode(body).decode("ascii")},
            error=None,
        )
    )
    return _snapshot_response(media, body)


@router.get("/cameras")
async def get_cameras():
    s = await get_settings_map_async()
    raw = s.get("cameras", "")
    cameras = json.loads(raw) if raw else []
    enriched = []
    for c in cameras:
        item = dict(c)
        item["stream_key"] = go2rtc_sync.stream_key_for_camera(c)
        enriched.append(item)
    return {"cameras": enriched}


@router.post("/cameras")
def save_cameras(data: dict):
    cameras = data.get("cameras", [])
    cleaned = []
    for c in cameras:
        name = (c.get("name") or "").strip()
        eid = (c.get("ha_entity_id") or "").strip()
        surl = (c.get("stream_url") or c.get("rtsp_url") or "").strip()
        if name and (eid or surl):
            entry = {"name": name, "ha_entity_id": eid or go2rtc_sync.stream_key_for_camera({"name": name})}
            if surl:
                entry["stream_url"] = surl
            cleaned.append(entry)
    with get_db() as conn:
        upsert_setting_sync(conn, "cameras", json.dumps(cleaned))
        conn.commit()
    invalidate_settings_cache(broadcast=True)
    notify_worker("sync_cameras")
    return {"ok": True}
