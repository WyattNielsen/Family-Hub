"""Aggregate SPA endpoints (/api/bootstrap and /api/dashboard).

Collapses 8-12 separate HTTP/1.1 requests per screen load into a single sub-10ms
local PostgreSQL read so LAN browsers never saturate their 6-socket pool.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi import APIRouter, Query
from database import get_settings_map_async
from routers import (
    calendar,
    chores,
    homeassistant,
    meals,
    members,
    messages,
    security,
    settings as settings_router,
    shopping,
    stocks,
    weather,
)
import schoolcafe

router = APIRouter()


@router.get("/bootstrap")
async def get_bootstrap():
    """Return everything the persistent app shell needs on initial boot in 1 call."""
    stored_task = asyncio.create_task(get_settings_map_async())
    members_task = asyncio.to_thread(members.get_members)
    msgs_task = asyncio.to_thread(messages.get_messages)
    weather_task = asyncio.create_task(weather.get_weather())
    ha_task = asyncio.create_task(homeassistant.get_ha_states())
    cams_task = asyncio.create_task(security.get_cameras())

    stored, member_rows, msg_rows, wx, ha, cams = await asyncio.gather(
        stored_task, members_task, msgs_task, weather_task, ha_task, cams_task
    )
    return {
        "settings": settings_router.sanitize_settings(stored),
        "members": member_rows,
        "messages": msg_rows,
        "weather": wx,
        "ha_states": ha,
        "cameras": cams,
    }


@router.get("/dashboard")
async def get_dashboard_bundle(
    date: Optional[str] = Query(None),
    school_date: Optional[str] = Query(None),
    events_start: Optional[str] = Query(None),
    events_end: Optional[str] = Query(None),
    member_id: Optional[int] = Query(None),
):
    """Return all Dashboard widgets in 1 sub-10ms local read."""
    today, _hour = schoolcafe.app_today()
    dinner_date = date or today
    ev_start = events_start or f"{dinner_date}T00:00:00"
    if events_end:
        ev_end = events_end
    else:
        end_d = (datetime.strptime(dinner_date[:10], "%Y-%m-%d").date() + timedelta(days=7)).isoformat()
        ev_end = f"{end_d}T23:59:59"

    def _sync_bundle():
        from database import get_db
        with get_db() as conn:
            ev_rows = calendar.query_events_in_window(
                conn, start=ev_start, end=ev_end, member_id=member_id
            )
        chore_rows = chores.get_chores(
            assigned_to=member_id,
            completed=False,
            hide_future=True,
            since_days=0,
        )
        lb_rows = chores.get_leaderboard()
        mem_rows = members.get_members()
        req_rows = meals.list_requests(member_id=member_id)
        shop_rows = shopping.list_items()
        msg_rows = messages.get_messages()
        return ev_rows, chore_rows, lb_rows, mem_rows, req_rows, shop_rows, msg_rows

    sync_task = asyncio.to_thread(_sync_bundle)
    meals_task = asyncio.create_task(
        meals.day_menu(
            date=dinner_date,
            school_date=school_date,
            member_id=member_id,
            include_school=True,
        )
    )
    stocks_task = asyncio.create_task(stocks.get_stocks())

    (ev_rows, chore_rows, lb_rows, mem_rows, req_rows, shop_rows, msg_rows), meal_data, stock_rows = await asyncio.gather(
        sync_task, meals_task, stocks_task
    )

    return {
        "events": ev_rows,
        "chores": chore_rows,
        "leaderboard": lb_rows,
        "members": mem_rows,
        "meals": meal_data,
        "meal_requests": req_rows,
        "shopping": shop_rows,
        "messages": msg_rows,
        "stocks": stock_rows,
    }


@router.get("/page/calendar")
async def page_calendar(
    start: str = Query(...),
    end: str = Query(...),
):
    """Calendar screen — events + members + sync status in one call."""

    def _load():
        from database import get_db

        with get_db() as conn:
            ev_rows = calendar.query_events_in_window(conn, start=start, end=end, member_id=None)
        return ev_rows, members.get_members()

    ev_rows, mem_rows = await asyncio.to_thread(_load)
    sync = await calendar.get_sync_status()
    return {"events": ev_rows, "members": mem_rows, "sync": sync}


@router.get("/page/chores")
async def page_chores(
    hide_future: bool = Query(False),
    since_days: int = Query(0),
):
    """Chores screen — chores + leaderboard + members in one call."""

    def _load():
        return (
            chores.get_chores(
                assigned_to=None,
                completed=None,
                hide_future=hide_future,
                since_days=since_days,
            ),
            chores.get_leaderboard(),
            members.get_members(),
        )

    chore_rows, lb_rows, mem_rows = await asyncio.to_thread(_load)
    return {"chores": chore_rows, "leaderboard": lb_rows, "members": mem_rows}


@router.get("/page/meals")
async def page_meals(start: Optional[str] = Query(None)):
    """Meals screen — week plan + requests + history + members in one call."""
    today, _hour = schoolcafe.app_today()
    week = await meals.week_menu(start=start, member_id=None, include_school=True)
    history, req_rows, mem_rows = await asyncio.to_thread(
        lambda: (
            meals.meal_history(before=today, limit=80),
            meals.list_requests(member_id=None),
            members.get_members(),
        )
    )
    stored = await get_settings_map_async()
    return {
        "week": week,
        "history": history,
        "requests": req_rows,
        "members": mem_rows,
        "school_meal_types": (stored or {}).get("school_meal_types") or "both",
    }


@router.get("/page/messages")
async def page_messages():
    """Messages screen — messages + members in one call."""

    def _load():
        return messages.get_messages(), members.get_members()

    msg_rows, mem_rows = await asyncio.to_thread(_load)
    return {"messages": msg_rows, "members": mem_rows}


@router.get("/page/security")
async def page_security():
    """Security screen — HA device states + cameras in one call."""
    ha, cams = await asyncio.gather(
        homeassistant.get_ha_states(),
        security.get_cameras(),
    )
    return {"ha_states": ha, "cameras": cams}


@router.get("/page/shopping")
async def page_shopping():
    """Shopping screen — one list payload."""
    items = await asyncio.to_thread(shopping.list_items)
    return {"items": items}
