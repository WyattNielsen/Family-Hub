"""Meals API: family dinner plan + local-first SchoolCafe breakfast & lunch."""
from datetime import datetime, timedelta
from typing import Optional
import asyncio
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from database import async_db, get_db
import schoolcafe

router = APIRouter()


class DinnerIn(BaseModel):
    meal_date: str
    title: str
    notes: Optional[str] = ""


class RequestIn(BaseModel):
    member_id: int
    title: str


def _valid_date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD")
    return value


def _add_days(iso_date: str, days: int) -> str:
    day = datetime.strptime(iso_date, "%Y-%m-%d").date() + timedelta(days=days)
    return day.isoformat()


async def _members_async(member_id: Optional[int]) -> list[dict]:
    query = """
        SELECT id, name, color, avatar, avatar_photo, school_id, school_name
        FROM members
        WHERE school_id IS NOT NULL AND school_id != ''
    """
    params: list = []
    if member_id is not None:
        query += " AND id = %s"
        params.append(member_id)
    query += " ORDER BY sort_order, name, id"
    async with async_db() as conn:
        cur = await conn.execute(query, params)
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _school_block(member: dict, school_date: str):
    overrides = schoolcafe.line_overrides().get(member["school_id"]) or {}
    block = {
        "member_id": member["id"],
        "member_name": member["name"],
        "member_color": member["color"],
        "avatar": member["avatar"],
        "avatar_photo": member["avatar_photo"],
        "school_id": member["school_id"],
        "school_name": member["school_name"] or "",
        "breakfast": [],
        "lunch": [],
        "warming": False,
        "error": None,
    }
    try:
        (breakfast, _b_line, b_warm), (lunch, _l_line, l_warm) = await asyncio.gather(
            schoolcafe.menu_items(member["school_id"], "Breakfast", school_date, overrides.get("Breakfast") or ""),
            schoolcafe.menu_items(member["school_id"], "Lunch", school_date, overrides.get("Lunch") or ""),
        )
        block["breakfast"] = breakfast
        block["lunch"] = lunch
        block["warming"] = bool(b_warm or l_warm)
    except Exception as e:
        print(f"SchoolCafe snapshot read error for {member['name']}: {e}")
        block["error"] = "Menu unavailable"
    return block


async def _get_dinner_async(dinner_date: str) -> dict:
    async with async_db() as conn:
        cur = await conn.execute(
            "SELECT title, notes FROM family_meals WHERE meal_date = %s", (dinner_date,)
        )
        row = await cur.fetchone()
    return {"title": row["title"] if row else "", "notes": row["notes"] if row else ""}


@router.get("/day")
async def day_menu(
    date: Optional[str] = None,
    school_date: Optional[str] = None,
    member_id: Optional[int] = Query(None),
    include_school: bool = Query(True),
):
    today, _hour = schoolcafe.app_today()
    dinner_date = _valid_date(date) if date else today
    menu_date = _valid_date(school_date) if school_date else dinner_date
    menu_date = schoolcafe.next_school_day(menu_date)
    hidden = schoolcafe.setting("lunch_hidden", "false") == "true"
    district_id = (schoolcafe.setting("schoolcafe_district_id") or "").strip()

    dinner = await _get_dinner_async(dinner_date)

    schools = []
    warming = False
    if include_school and district_id and not hidden:
        members = await _members_async(member_id)
        if members:
            schools = list(await asyncio.gather(*[_school_block(m, menu_date) for m in members]))
            warming = any(s.get("warming") for s in schools)

    return {
        "date": dinner_date,
        "school_date": menu_date,
        "dinner": dinner,
        "configured": bool(district_id),
        "school_menus_hidden": hidden,
        "warming": warming,
        "schools": schools,
    }


async def _school_week(member: dict, dates: list):
    overrides = schoolcafe.line_overrides().get(member["school_id"]) or {}
    block = {
        "member_id": member["id"],
        "member_name": member["name"],
        "member_color": member["color"],
        "color": member["color"],
        "avatar": member["avatar"],
        "avatar_photo": member["avatar_photo"],
        "school_id": member["school_id"],
        "school_name": member["school_name"] or "",
        "days": {},
        "warming": False,
        "error": None,
    }
    try:
        (breakfast, _b_line, b_warm), (lunch, _l_line, l_warm) = await asyncio.gather(
            schoolcafe.menu_week(member["school_id"], "Breakfast", dates[0], overrides.get("Breakfast") or ""),
            schoolcafe.menu_week(member["school_id"], "Lunch", dates[0], overrides.get("Lunch") or ""),
        )
        for iso in dates:
            block["days"][iso] = {
                "breakfast": schoolcafe.items_for_date(breakfast, iso),
                "lunch": schoolcafe.items_for_date(lunch, iso),
            }
        block["warming"] = bool(b_warm or l_warm)
    except Exception as e:
        print(f"SchoolCafe week snapshot error for {member['name']}: {e}")
        block["error"] = "Menu unavailable"
    return block


async def _get_week_dinners_async(dates: list[str]) -> dict[str, dict]:
    async with async_db() as conn:
        cur = await conn.execute(
            "SELECT meal_date, title, notes FROM family_meals WHERE meal_date = ANY(%s)",
            (dates,),
        )
        rows = await cur.fetchall()
    return {r["meal_date"]: {"title": r["title"], "notes": r["notes"]} for r in rows}


@router.get("/week")
async def week_menu(
    start: Optional[str] = None,
    member_id: Optional[int] = Query(None),
    include_school: bool = Query(True),
):
    today, _hour = schoolcafe.app_today()
    anchor = _valid_date(start) if start else today
    monday = schoolcafe.monday_of(anchor)
    dates = [_add_days(monday, i) for i in range(7)]
    by_date = await _get_week_dinners_async(dates)
    days = [
        {
            "date": d,
            "title": (by_date.get(d) or {}).get("title", ""),
            "notes": (by_date.get(d) or {}).get("notes", ""),
        }
        for d in dates
    ]

    hidden = schoolcafe.setting("lunch_hidden", "false") == "true"
    district_id = (schoolcafe.setting("schoolcafe_district_id") or "").strip()
    schools = []
    warming = False
    if include_school and district_id and not hidden:
        members = await _members_async(member_id)
        if members:
            schools = list(await asyncio.gather(*[_school_week(m, dates[:5]) for m in members]))
            warming = any(s.get("warming") for s in schools)

    return {
        "start": monday,
        "end": dates[-1],
        "days": days,
        "configured": bool(district_id),
        "school_menus_hidden": hidden,
        "warming": warming,
        "schools": schools,
    }


@router.get("/history")
def meal_history(before: Optional[str] = None, limit: int = 80):
    today, _hour = schoolcafe.app_today()
    cutoff = _valid_date(before) if before else today
    limit = max(1, min(int(limit), 200))
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT meal_date, title, notes
            FROM family_meals
            WHERE meal_date < %s AND TRIM(title) != ''
            ORDER BY meal_date DESC
            LIMIT %s
            """,
            (cutoff, limit),
        ).fetchall()
    return [dict(r) for r in rows]


@router.put("/dinner")
def save_dinner(body: DinnerIn):
    meal_date = _valid_date(body.meal_date)
    title = (body.title or "").strip()
    notes = (body.notes or "").strip()
    with get_db() as conn:
        if not title and not notes:
            conn.execute("DELETE FROM family_meals WHERE meal_date = %s", (meal_date,))
            conn.commit()
            return {"meal_date": meal_date, "title": "", "notes": ""}
        conn.execute(
            """
            INSERT INTO family_meals (meal_date, title, notes, updated_at)
            VALUES (%s, %s, %s, NOW()::text)
            ON CONFLICT(meal_date) DO UPDATE SET
                title = EXCLUDED.title,
                notes = EXCLUDED.notes,
                updated_at = NOW()::text
            """,
            (meal_date, title, notes),
        )
        conn.commit()
    return {"meal_date": meal_date, "title": title, "notes": notes}


@router.get("/requests")
def list_requests(member_id: Optional[int] = Query(None)):
    query = """
        SELECT r.id, r.member_id, r.title, r.created_at,
               m.name, m.color, m.avatar, m.avatar_photo,
               m.name AS member_name, m.color AS member_color,
               m.avatar AS member_avatar, m.avatar_photo AS member_avatar_photo
        FROM meal_requests r
        JOIN members m ON m.id = r.member_id
    """
    params = []
    if member_id is not None:
        query += " WHERE r.member_id = %s"
        params.append(member_id)
    query += " ORDER BY r.created_at DESC, r.id DESC"
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


@router.post("/requests")
def create_request(body: RequestIn):
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Meal name is required")
    with get_db() as conn:
        member = conn.execute("SELECT id FROM members WHERE id=%s", (body.member_id,)).fetchone()
        if not member:
            raise HTTPException(status_code=400, detail="Choose who is asking")
        inserted = conn.execute(
            "INSERT INTO meal_requests (member_id, title) VALUES (%s, %s) RETURNING id",
            (body.member_id, title),
        ).fetchone()
        conn.commit()
        row = conn.execute(
            """
            SELECT r.id, r.member_id, r.title, r.created_at,
                   m.name AS member_name, m.color AS member_color,
                   m.avatar AS member_avatar, m.avatar_photo AS member_avatar_photo
            FROM meal_requests r JOIN members m ON m.id = r.member_id WHERE r.id = %s
            """,
            (inserted["id"],),
        ).fetchone()
    return dict(row)


@router.delete("/requests/{request_id}")
def delete_request(request_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM meal_requests WHERE id=%s", (request_id,))
        conn.commit()
    return {"ok": True}
