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
    conn.close()
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
        "error": None,
    }
    try:
        (breakfast, _b_line), (lunch, _l_line) = await asyncio.gather(
            schoolcafe.menu_items(member["school_id"], "Breakfast", school_date, overrides.get("Breakfast") or ""),
            schoolcafe.menu_items(member["school_id"], "Lunch", school_date, overrides.get("Lunch") or ""),
        )
        block["breakfast"] = breakfast
        block["lunch"] = lunch
    except Exception as e:
        print(f"SchoolCafe menu error for {member['name']}: {e}")
        block["error"] = "Menu unavailable"
    return block


@router.get("/day")
async def day_menu(
    date: Optional[str] = None,
    school_date: Optional[str] = None,
    member_id: Optional[int] = Query(None),
):
    today, _hour = schoolcafe.app_today()
    dinner_date = _valid_date(date) if date else today
    menu_date = _valid_date(school_date) if school_date else dinner_date
    menu_date = schoolcafe.next_school_day(menu_date)
    hidden = schoolcafe.setting("lunch_hidden", "false") == "true"
    district_id = (schoolcafe.setting("schoolcafe_district_id") or "").strip()

    conn = get_db()
    row = conn.execute(
        "SELECT title, notes FROM family_meals WHERE meal_date = ?", (dinner_date,)
    ).fetchone()
    conn.close()
    dinner = {"title": row["title"] if row else "", "notes": row["notes"] if row else ""}

    schools = []
    if district_id and not hidden:
        members = _members(member_id)
        if members:
            schools = await asyncio.gather(*[_school_block(m, menu_date) for m in members])

    return {
        "date": dinner_date,
        "school_date": menu_date,
        "dinner": dinner,
        "configured": bool(district_id),
        "school_menus_hidden": hidden,
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
        "days": [],
        "error": None,
    }
    try:
        (breakfast, _b_line), (lunch, _l_line) = await asyncio.gather(
            schoolcafe.menu_week(member["school_id"], "Breakfast", dates[0], overrides.get("Breakfast") or ""),
            schoolcafe.menu_week(member["school_id"], "Lunch", dates[0], overrides.get("Lunch") or ""),
        )
        block["days"] = [
            {
                "date": iso,
                "breakfast": schoolcafe.items_for_date(breakfast, iso),
                "lunch": schoolcafe.items_for_date(lunch, iso),
            }
            for iso in dates
        ]
    except Exception as e:
        print(f"SchoolCafe week error for {member['name']}: {e}")
        block["error"] = "Menu unavailable"
    return block


@router.get("/week")
async def week_menu(start: Optional[str] = None, member_id: Optional[int] = Query(None)):
    today, _hour = schoolcafe.app_today()
    anchor = _valid_date(start) if start else today
    start_date = schoolcafe.monday_of(anchor)
    dates = [_add_days(start_date, i) for i in range(7)]
    conn = get_db()
    rows = conn.execute(
        f"SELECT meal_date, title, notes FROM family_meals WHERE meal_date IN ({','.join('?' * len(dates))})",
        dates,
    ).fetchall()
    conn.close()
    by_date = {r["meal_date"]: dict(r) for r in rows}
    days = []
    for iso in dates:
        saved = by_date.get(iso) or {}
        days.append({
            "date": iso,
            "title": saved.get("title") or "",
            "notes": saved.get("notes") or "",
        })

    hidden = schoolcafe.setting("lunch_hidden", "false") == "true"
    district_id = (schoolcafe.setting("schoolcafe_district_id") or "").strip()
    schools = []
    if district_id and not hidden:
        members = _members(member_id)
        if members:
            schools = list(await asyncio.gather(*[_school_week(m, dates) for m in members]))

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
    rows = conn.execute(
        """
        SELECT meal_date, title, notes FROM family_meals
        WHERE meal_date < ? AND trim(title) != ''
        ORDER BY meal_date DESC
        LIMIT ?
        """,
        (cutoff, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.put("/dinner")
def save_dinner(body: DinnerIn):
    meal_date = _valid_date(body.meal_date)
    title = (body.title or "").strip()
    notes = (body.notes or "").strip()
    conn = get_db()
    if not title:
        conn.execute("DELETE FROM family_meals WHERE meal_date = ?", (meal_date,))
        conn.commit()
        conn.close()
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
    conn.close()
    return {"ok": True, "meal_date": meal_date, "title": title, "notes": notes}


@router.get("/requests")
def list_requests(member_id: Optional[int] = Query(None)):
    conn = get_db()
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
    conn.close()
    return [dict(r) for r in rows]


@router.post("/requests")
def create_request(body: RequestIn):
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Say what they want")
    conn = get_db()
    member = conn.execute("SELECT id FROM members WHERE id=?", (body.member_id,)).fetchone()
    if not member:
        conn.close()
        raise HTTPException(status_code=400, detail="Unknown person")
    cur = conn.execute(
        "INSERT INTO meal_requests (member_id, title) VALUES (?, ?)",
        (body.member_id, title),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return {"ok": True, "id": new_id}


@router.delete("/requests/{request_id}")
def delete_request(request_id: int):
    conn = get_db()
    conn.execute("DELETE FROM meal_requests WHERE id=?", (request_id,))
    conn.commit()
    conn.close()
    return {"ok": True}
