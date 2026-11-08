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
    query += " ORDER BY name"
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


@router.get("/week")
def week_menu(start: Optional[str] = None):
    today, _hour = schoolcafe.app_today()
    start_date = _valid_date(start) if start else today
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
    return {"start": start_date, "days": days}


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
