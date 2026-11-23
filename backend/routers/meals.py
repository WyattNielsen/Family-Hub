from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import asyncio
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database import get_db
import schoolcafe

router = APIRouter()


class DinnerIn(BaseModel):
    meal_date: str
    title: str = ""
    notes: str = ""


class RequestIn(BaseModel):
    member_id: int
    title: str


def _valid_date(value: str) -> str:
    value = (value or "").strip()
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD")
    try:
        schoolcafe.monday_of(value)
    except Exception:
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD")
    return value


def _add_days(iso_date: str, days: int) -> str:
    from datetime import datetime, timedelta
    day = datetime.strptime(iso_date, "%Y-%m-%d").date() + timedelta(days=days)
    return day.isoformat()


def _members(member_id: Optional[int]):
    conn = get_db()
    try:
        query = (
            "SELECT id, name, color, avatar, avatar_photo, school_id, school_name "
            "FROM members WHERE school_id IS NOT NULL AND school_id != ''"
        )
        params = []
        if member_id:
            query += " AND id = ?"
            params.append(member_id)
        query += " ORDER BY sort_order, name, id"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _member_head(member: dict) -> dict:
    return {
        "member_id": member["id"],
        "member_name": member["name"],
        "member_color": member["color"],
        "avatar": member["avatar"],
        "avatar_photo": member["avatar_photo"],
        "school_id": member["school_id"],
        "school_name": member["school_name"] or "",
    }


def _day_items(days, iso: str) -> list:
    if not isinstance(days, dict):
        return []
    items = days.get(iso)
    return items if isinstance(items, list) else []


def _school_day_blocks(members: list, menu_date: str):
    """School blocks from SQLite. Also the week starts that still need a fetch."""
    if not members:
        return [], []
    overrides = schoolcafe.line_overrides()
    week = schoolcafe.monday_of(menu_date)
    maps = schoolcafe.load_week_maps([m["school_id"] for m in members], [week])
    blocks = []
    missing = []
    for member in members:
        sid = member["school_id"]
        pref = overrides.get(sid) or {}
        if not isinstance(pref, dict):
            pref = {}
        breakfast, b_refresh = schoolcafe.pick_days(maps, sid, "Breakfast", week, pref.get("Breakfast") or "")
        lunch, l_refresh = schoolcafe.pick_days(maps, sid, "Lunch", week, pref.get("Lunch") or "")
        if b_refresh or l_refresh:
            missing.append(week)
        block = _member_head(member)
        block["breakfast"] = _day_items(breakfast, menu_date)
        block["lunch"] = _day_items(lunch, menu_date)
        block["error"] = None
        blocks.append(block)
    return blocks, list(dict.fromkeys(missing))


def _get_dinner(dinner_date: str) -> dict:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT title, notes FROM family_meals WHERE meal_date = ?", (dinner_date,)
        ).fetchone()
        return {"title": row["title"] if row else "", "notes": row["notes"] if row else ""}
    finally:
        conn.close()


def _resolve_menu_dates(dinner_date: Optional[str]):
    today, hour = schoolcafe.app_today()
    dinner = dinner_date or today
    return today, schoolcafe.school_menu_date(dinner, hour), dinner


@router.get("/day")
async def day_menu(
    date: Optional[str] = None,
    member_id: Optional[int] = Query(None),
    include_school: bool = Query(True),
):
    dinner_date = _valid_date(date) if date else None
    today, menu_date, dinner_date = await asyncio.to_thread(_resolve_menu_dates, dinner_date)
    hidden = (await asyncio.to_thread(schoolcafe.setting, "lunch_hidden", "false")) == "true"
    district_id = ((await asyncio.to_thread(schoolcafe.setting, "schoolcafe_district_id")) or "").strip()

    dinner = await asyncio.to_thread(_get_dinner, dinner_date)

    schools = []
    if include_school and district_id and not hidden:
        members = await asyncio.to_thread(_members, member_id)
        schools, missing = await asyncio.to_thread(_school_day_blocks, members, menu_date)
        if missing:
            await schoolcafe.kick_school_menu_refresh(missing)

    return {
        "date": dinner_date,
        "school_date": menu_date,
        "dinner": dinner,
        "configured": bool(district_id),
        "school_menus_hidden": hidden,
        "schools": schools,
    }


def _school_week_blocks(members: list, dates: list):
    """One stored week, sliced into the day lists the meals page already renders."""
    if not members or not dates:
        return [], []
    overrides = schoolcafe.line_overrides()
    week = schoolcafe.monday_of(dates[0])
    maps = schoolcafe.load_week_maps([m["school_id"] for m in members], [week])
    blocks = []
    missing = []
    for member in members:
        sid = member["school_id"]
        pref = overrides.get(sid) or {}
        if not isinstance(pref, dict):
            pref = {}
        breakfast, b_refresh = schoolcafe.pick_days(maps, sid, "Breakfast", week, pref.get("Breakfast") or "")
        lunch, l_refresh = schoolcafe.pick_days(maps, sid, "Lunch", week, pref.get("Lunch") or "")
        if b_refresh or l_refresh:
            missing.append(week)
        block = _member_head(member)
        block["color"] = member["color"]
        block["days"] = [
            {
                "date": iso,
                "breakfast": _day_items(breakfast, iso),
                "lunch": _day_items(lunch, iso),
            }
            for iso in dates
        ]
        block["error"] = None
        blocks.append(block)
    return blocks, list(dict.fromkeys(missing))


def _get_week_dinners(dates: list[str]) -> dict[str, dict]:
    conn = get_db()
    try:
        rows = conn.execute(
            f"SELECT meal_date, title, notes FROM family_meals WHERE meal_date IN ({','.join('?' * len(dates))})",
            dates,
        ).fetchall()
        return {r["meal_date"]: dict(r) for r in rows}
    finally:
        conn.close()


@router.get("/week")
async def week_menu(
    start: Optional[str] = None,
    member_id: Optional[int] = Query(None),
    include_school: bool = Query(True),
):
    today, _hour = await asyncio.to_thread(schoolcafe.app_today)
    anchor = _valid_date(start) if start else today
    start_date = schoolcafe.monday_of(anchor)
    dates = [_add_days(start_date, i) for i in range(7)]
    by_date = await asyncio.to_thread(_get_week_dinners, dates)
    days = []
    for iso in dates:
        saved = by_date.get(iso) or {}
        days.append({
            "date": iso,
            "title": saved.get("title") or "",
            "notes": saved.get("notes") or "",
        })

    hidden = (await asyncio.to_thread(schoolcafe.setting, "lunch_hidden", "false")) == "true"
    district_id = ((await asyncio.to_thread(schoolcafe.setting, "schoolcafe_district_id")) or "").strip()
    schools = []
    if include_school and district_id and not hidden:
        members = await asyncio.to_thread(_members, member_id)
        schools, missing = await asyncio.to_thread(_school_week_blocks, members, dates)
        if missing:
            await schoolcafe.kick_school_menu_refresh(missing)
        source_url = schoolcafe.menu_page_url(await schoolcafe.district_short_name())
        for block in schools:
            block["source_url"] = source_url

    return {
        "start": start_date,
        "end": dates[-1],
        "today": today,
        "days": days,
        "configured": bool(district_id),
        "school_menus_hidden": hidden,
        "schools": schools,
    }


@router.get("/history")
def meal_history(before: Optional[str] = None, limit: int = 80):
    today, _hour = schoolcafe.app_today()
    cutoff = _valid_date(before) if before else today
    limit = max(1, min(int(limit or 80), 200))
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT meal_date, title, notes FROM family_meals
            WHERE meal_date < ? AND trim(title) != ''
            ORDER BY meal_date DESC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.put("/dinner")
def save_dinner(body: DinnerIn):
    meal_date = _valid_date(body.meal_date)
    title = (body.title or "").strip()
    notes = (body.notes or "").strip()
    conn = get_db()
    try:
        if not title:
            conn.execute("DELETE FROM family_meals WHERE meal_date = ?", (meal_date,))
            conn.commit()
            return {"ok": True, "deleted": True, "meal_date": meal_date}
        conn.execute(
            """
            INSERT INTO family_meals (meal_date, title, notes, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(meal_date) DO UPDATE SET
                title = excluded.title,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (meal_date, title, notes),
        )
        conn.commit()
        return {"ok": True, "meal_date": meal_date, "title": title, "notes": notes}
    finally:
        conn.close()


@router.get("/requests")
def list_requests(member_id: Optional[int] = Query(None)):
    conn = get_db()
    try:
        query = """
            SELECT r.id, r.title, r.created_at, r.member_id,
                   m.name, m.color, m.avatar, m.avatar_photo
            FROM meal_requests r
            JOIN members m ON m.id = r.member_id
        """
        params = []
        if member_id:
            query += " WHERE r.member_id = ?"
            params.append(member_id)
        query += " ORDER BY r.id"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/requests")
def create_request(body: RequestIn):
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Say what they want")
    conn = get_db()
    try:
        member = conn.execute("SELECT id FROM members WHERE id=?", (body.member_id,)).fetchone()
        if not member:
            raise HTTPException(status_code=400, detail="Unknown person")
        cur = conn.execute(
            "INSERT INTO meal_requests (member_id, title) VALUES (?, ?)",
            (body.member_id, title),
        )
        conn.commit()
        new_id = cur.lastrowid
        return {"ok": True, "id": new_id}
    finally:
        conn.close()


@router.delete("/requests/{request_id}")
def delete_request(request_id: int):
    conn = get_db()
    try:
        conn.execute("DELETE FROM meal_requests WHERE id=?", (request_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
