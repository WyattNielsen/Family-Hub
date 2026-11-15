"""Synchronize Family Hub camera definitions and WebRTC candidates with go2rtc."""
from __future__ import annotations

import json
import os
import re
from urllib.parse import urlparse

from database import get_settings_map_async
import outbound

GO2RTC_URL = (os.environ.get("GO2RTC_URL") or "http://go2rtc:1984").rstrip("/")
GO2RTC_CONFIG_PATH = os.environ.get("GO2RTC_CONFIG_PATH") or "/data/go2rtc/go2rtc.yaml"


def stream_key_for_camera(cam: dict) -> str:
    raw = (cam.get("ha_entity_id") or cam.get("name") or "cam").strip().lower()
    return re.sub(r"[^a-z0-9_.-]+", "_", raw)


def _default_lan_host() -> str:
    base = (os.environ.get("APP_BASE_URL") or "").strip()
    if base:
        try:
            host = urlparse(base).hostname
            if host and host not in ("localhost", "127.0.0.1", "0.0.0.0"):
                return host
        except Exception:
            pass
    return ""


def _write_go2rtc_yaml(streams: dict[str, list[str]], extra_host: str = ""):
    cfg_dir = os.path.dirname(GO2RTC_CONFIG_PATH)
    if cfg_dir:
        os.makedirs(cfg_dir, exist_ok=True)

    candidates: list[str] = []
    for h in (_default_lan_host(), extra_host):
        if h and h not in ("localhost", "127.0.0.1"):
            cand = f"{h}:8555"
            if cand not in candidates:
                candidates.append(cand)
    candidates.append("stun:8555")

    lines = [
        "api:",
        '  listen: ":1984"',
        "webrtc:",
        '  listen: ":8555"',
        "  candidates:",
    ]
    for c in candidates:
        lines.append(f'    - "{c}"')

    if not streams:
        lines.append("streams: {}")
    else:
        lines.append("streams:")
        for key, sources in streams.items():
            lines.append(f"  {json.dumps(key)}:")
            for src in sources:
                lines.append(f"    - {json.dumps(src)}")

    content = "\n".join(lines) + "\n"
    try:
        existing = ""
        if os.path.isfile(GO2RTC_CONFIG_PATH):
            with open(GO2RTC_CONFIG_PATH, "r", encoding="utf-8") as f:
                existing = f.read()
        if existing != content:
            with open(GO2RTC_CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write(content)
    except Exception as exc:
        print(f"Could not write {GO2RTC_CONFIG_PATH}: {exc}")


async def _resolve_ha_stream_sources(ha_url: str, ha_token: str, entity_id: str) -> list[str]:
    """Build go2rtc source URLs for a Home Assistant camera entity."""
    if not ha_url or not ha_token or not entity_id:
        return []
    base = ha_url.rstrip("/")
    headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
    sources: list[str] = []

    try:
        async with outbound.client(timeout=5.0) as client:
            resp = await client.get(f"{base}/api/states/{entity_id}", headers=headers)
            if resp.status_code == 200:
                state_obj = resp.json()
                attrs = state_obj.get("attributes") or {}
                cam_token = attrs.get("access_token")
                if cam_token:
                    mjpeg_url = f"{base}/api/camera_proxy_stream/{entity_id}?token={cam_token}"
                    # Prefer direct MJPEG ingest + optional H.264 transcode fallback if ffmpeg exists in go2rtc
                    sources.append(mjpeg_url)
    except Exception as exc:
        print(f"go2rtc HA camera inspect failed ({entity_id}): {exc}")

    return sources


async def sync_go2rtc_streams(extra_host: str = "") -> dict[str, list[str]]:
    """Push all configured cameras to go2rtc via its dynamic REST API and config file."""
    s = await get_settings_map_async()
    raw_cams = s.get("cameras", "")
    try:
        cameras = json.loads(raw_cams) if raw_cams else []
    except Exception:
        cameras = []

    ha_url = s.get("ha_url", "").strip()
    ha_token = s.get("ha_token", "").strip()

    streams: dict[str, list[str]] = {}
    for cam in cameras:
        key = stream_key_for_camera(cam)
        direct_url = (cam.get("stream_url") or cam.get("rtsp_url") or "").strip()
        cam_sources: list[str] = []
        if direct_url:
            cam_sources.append(direct_url)
        entity_id = (cam.get("ha_entity_id") or "").strip()
        if entity_id and ha_url and ha_token:
            ha_sources = await _resolve_ha_stream_sources(ha_url, ha_token, entity_id)
            for hs in ha_sources:
                if hs not in cam_sources:
                    cam_sources.append(hs)
        if cam_sources:
            streams[key] = cam_sources

    _write_go2rtc_yaml(streams, extra_host=extra_host)

    # Register/update each stream dynamically in go2rtc without restarting it
    try:
        async with outbound.client(timeout=4.0) as client:
            for key, src_list in streams.items():
                await client.put(
                    f"{GO2RTC_URL}/api/streams",
                    params=[("name", key), *[("src", s_url) for s_url in src_list]],
                )
    except Exception as exc:
        print(f"go2rtc API sync skipped/failed ({GO2RTC_URL}): {exc}")

    return streams
