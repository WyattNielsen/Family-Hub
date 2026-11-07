from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database import get_db

router = APIRouter()

MEMBER_SELECT = (
    "SELECT id, name, color, avatar, is_admin, write_calendar_source_id "
    "FROM members"
)

class MemberCreate(BaseModel):
    name: str
    color: str = "#4A90D9"
    avatar: Optional[str] = None
    is_admin: bool = False

class MemberUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    avatar: Optional[str] = None
    is_admin: Optional[bool] = None
    write_calendar_source_id: Optional[int] = None

def _validate_write_source(conn, member_id: int, source_id: Optional[int]):
    if source_id is None:
        return
    source = conn.execute(
        "SELECT id, member_id, kind FROM calendar_sources WHERE id=?", (source_id,)
    ).fetchone()
    if not source:
        raise HTTPException(status_code=400, detail="Write calendar source not found")
    if source["member_id"] != member_id:
        raise HTTPException(
            status_code=400,
            detail="Write calendar must be assigned to this member",
        )
    if source["kind"] not in ("google", "caldav"):
        raise HTTPException(
            status_code=400,
            detail="Write calendar must be Google or iCloud CalDAV",
        )

@router.get("/")
def get_members():
    conn = get_db()
    members = conn.execute(f"{MEMBER_SELECT} ORDER BY name").fetchall()
    conn.close()
    return [dict(m) for m in members]

@router.post("/")
def create_member(member: MemberCreate):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO members (name, color, avatar, is_admin) VALUES (?, ?, ?, ?)",
        (member.name, member.color, member.avatar, 1 if member.is_admin else 0)
    )
    conn.commit()
    new_id = cur.lastrowid
    row = conn.execute(f"{MEMBER_SELECT} WHERE id=?", (new_id,)).fetchone()
    conn.close()
    return dict(row)

def _apply_member_update(member_id: int, member: MemberUpdate):
    conn = get_db()
    existing = conn.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Member not found")

    data = member.dict(exclude_unset=True)
    updates = {}
    if "name" in data and data["name"] is not None:
        updates["name"] = data["name"]
    if "color" in data and data["color"] is not None:
        updates["color"] = data["color"]
    if "avatar" in data:
        updates["avatar"] = data["avatar"]
    if "is_admin" in data and data["is_admin"] is not None:
        updates["is_admin"] = 1 if data["is_admin"] else 0
    if "write_calendar_source_id" in data:
        _validate_write_source(conn, member_id, data["write_calendar_source_id"])
        updates["write_calendar_source_id"] = data["write_calendar_source_id"]

    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE members SET {set_clause} WHERE id=?", (*updates.values(), member_id))
        conn.commit()
    row = conn.execute(f"{MEMBER_SELECT} WHERE id=?", (member_id,)).fetchone()
    conn.close()
    return dict(row)

@router.put("/{member_id}")
def update_member(member_id: int, member: MemberUpdate):
    return _apply_member_update(member_id, member)

@router.patch("/{member_id}")
def patch_member(member_id: int, member: MemberUpdate):
    return _apply_member_update(member_id, member)

@router.delete("/{member_id}")
def delete_member(member_id: int):
    conn = get_db()
    conn.execute("DELETE FROM members WHERE id=?", (member_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

@router.get("/{member_id}/chore-stats")
def get_chore_stats(member_id: int):
    conn = get_db()
    stats = conn.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(completed) as completed,
            SUM(CASE WHEN completed=1 THEN points ELSE 0 END) as points
        FROM chores WHERE assigned_to=?
    """, (member_id,)).fetchone()
    conn.close()
    return dict(stats)
